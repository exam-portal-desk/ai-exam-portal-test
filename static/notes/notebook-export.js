/* Shared notebook export logic (progress driver + PDF/JSON export + download delivery), used by
   the Public Notebooks library grid (library.js), My Notebooks (notebooks.js) AND the open
   notebook editor (editor.js) — a notebook the current user owns, an open-for-editing notebook,
   and a currently-public notebook export identically once you have page/object data for it; the
   only difference between callers is WHERE that data comes from (an HTTP fetch for the first
   two, already-in-memory pageCache for the editor) and, for PDF, which theme colors to read the
   dot-grid background from (the editor reads its own live canvas shell; the other two read the
   document root). Extracted here so none of the three callers keeps its own copy.

   PDF export is entirely client-side (jsPDF — see exportNotebookAsPdf below): every page is
   still rasterized locally via an off-screen Fabric canvas (unchanged), but the PDF itself is now
   also assembled in the browser and saved directly, with NO request to the server at all. This
   replaced an earlier design that POSTed every rendered page's PNG to a /export-pdf route for
   ReportLab to assemble server-side — on a large notebook, that meant holding the entire
   multi-page image payload (request body + decoded images + the finished PDF) in the memory of
   the single Gunicorn worker this app runs on Render, which could OOM-crash the whole app for
   every user, not just the one exporting. Moving assembly client-side also drops what that old
   design had to do to force a reliable download: POST the same full payload a SECOND time into a
   hidden iframe (a blob: URL from a single fetch has no Content-Disposition of its own, so
   Chrome's built-in PDF viewer could intercept it and show it inline instead of saving it) —
   jsPDF's own save() sidesteps that entirely by driving a real `<a download>` click itself, so
   there's exactly one PNG-rasterize pass and no network round-trip at all now.

   createExportProgress's bar tracks a `target` that ONLY ever moves via advance()/complete()
   calls fed by real signals from the caller (pages actually rendered, bytes actually read off
   Content-Length, or "the full response has actually arrived") — never a blind timer counting up
   on its own. The internal 15ms tick just interpolates the visible bar smoothly toward whatever
   the last REAL target was; the number/label always reflect real progress, nothing is invented.

   Lifecycle both exportNotebookAsPdf and exportNotebookAsJson drive the button label through:
   Preparing -> Generating -> Finalizing -> Ready -> Downloading... . For JSON, "Ready" only
   happens once the export's complete bytes have actually arrived in the browser — not when the
   server responds, not on a timer, but once fetchToCompletion's read loop has actually finished
   (real byte progress off Content-Length along the way, and a genuine error — never a false
   "success" — the instant the request fails); the browser save is then triggered the same tick
   "Ready" is reached, via a REAL request to the export URL (see deliverViaIframeGet() below) —
   never a blob: URL, for the Content-Disposition reason above. The success
   toast only fires after that request has been handed to the browser. */

export function createExportProgress(btn, label) {
  let pct = 0, target = 0, timer = null, doneResolvers = [];
  const render = () => { btn.innerHTML = `<i class="fas fa-spinner fa-spin"></i> ${label} ${pct}%`; };
  function tick() {
    if (pct >= target) return;
    pct += 1; render();
    if (pct >= 100) { doneResolvers.forEach(resolve => resolve()); doneResolvers = []; }
  }
  return {
    start() { pct = 0; target = 0; render(); timer = setInterval(tick, 15); },
    setLabel(next) { label = next; render(); },
    advance(value) { target = Math.max(target, Math.min(99, Math.round(value))); },
    complete() { target = 100; return pct >= 100 ? Promise.resolve() : new Promise(resolve => doneResolvers.push(resolve)); },
    stop() { if (timer) clearInterval(timer); timer = null; },
  };
}

// Same fixed page geometry as editor.js's PAGE_WIDTH/PAGE_HEIGHT (and pdf_service.py's
// _CANVAS_PX_W/H) — must match so a page renders identically regardless of which page it's
// exported from.
export const EXPORT_PAGE_WIDTH = 2400, EXPORT_PAGE_HEIGHT = 1600;

export function constrainObjectToExportPage(object) {
  if (!object) return;
  object.setCoords();
  const box = object.getBoundingRect(true, true);
  let dx = 0, dy = 0;
  if (box.left < 0) dx = -box.left;
  else if (box.left + box.width > EXPORT_PAGE_WIDTH) dx = EXPORT_PAGE_WIDTH - (box.left + box.width);
  if (box.top < 0) dy = -box.top;
  else if (box.top + box.height > EXPORT_PAGE_HEIGHT) dy = EXPORT_PAGE_HEIGHT - (box.top + box.height);
  if (dx || dy) { object.set({ left: (object.left || 0) + dx, top: (object.top || 0) + dy }); object.setCoords(); }
}

// Same same-origin rewrite as editor.js's withExportSafeImageSrc, for the same reason: the
// export canvas's toDataURL() would otherwise taint on a cross-origin signed image URL.
export function withExportSafeImageSrc(raw) {
  return raw.map(entry => (entry.type === 'image' && entry.assetId) ? { ...entry, src: `/api/v01/assets/${entry.assetId}/file` } : entry);
}

// ── The one real completion signal, shared by both formats ───────────────────────────────────
// Fetches `url`, reads the WHOLE response body via its stream reader (the bytes themselves are
// discarded — this call's only job is to know FOR CERTAIN, from the real network exchange, that
// generation finished without error and every byte the server sent actually arrived), reporting
// real progress off Content-Length as each chunk comes in. onProgress gets a 0..1 fraction; pass
// null to skip it. Throws a real Error (the server's own message where available) on any HTTP or
// network failure — never silently "succeeds". The actual file delivery is a separate, real
// request — see deliverViaIframeGet/deliverViaIframeForm below — only ever issued once this one
// has confirmed there is a good file waiting, so a failed export never reaches the browser as a
// download attempt.
async function fetchToCompletion(url, init, onProgress) {
  let res;
  try {
    res = await fetch(url, init);
  } catch (e) {
    throw new Error('Network error — please check your connection and try again.');
  }
  if (!res.ok) {
    let message; try { message = (await res.json()).message; } catch (e) { /* not JSON */ }
    throw new Error(message || 'Unable to export this notebook.');
  }
  const total = Number(res.headers.get('Content-Length')) || 0;
  const reader = res.body?.getReader();
  if (reader) {
    let received = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      received += value.length;
      if (total > 0 && onProgress) onProgress(received / total);
    }
  } else {
    await res.arrayBuffer();   // no streaming reader available (very old browser) — still correct, just no incremental progress
  }
}

// The single shared hidden iframe every real download is handed off through, exactly as this
// notebook download mechanism has always delivered a file: a real browser-native request to a
// real URL, so the server's `Content-Disposition: attachment` is what decides "download, not
// display" — never a client-synthesized blob: URL, which carries no such header and is left to
// the browser's own guess (Chrome's built-in PDF viewer in particular can intercept one).
function downloadFrame() {
  let frame = document.getElementById('notesDownloadFrame');
  // A form's target="..." (and window.open's) resolves against a browsing context's NAME, not
  // its id — an iframe with only an id set (no matching name) is not a valid target, and a form
  // submitted at it falls back to navigating the CURRENT tab instead, which is exactly the "PDF
  // opens in this tab" symptom the id-only version of this produced. Both must be set.
  if (!frame) { frame = document.createElement('iframe'); frame.name = frame.id = 'notesDownloadFrame'; frame.style.display = 'none'; document.body.appendChild(frame); }
  return frame;
}

// JSON: a plain GET, cache-busted so re-downloading the same URL right after still reassigns a
// distinct src (an iframe does not reload a src it already has).
function deliverViaIframeGet(url) {
  const frame = downloadFrame();
  frame.src = `${url}${url.includes('?') ? '&' : '?'}_=${Date.now()}`;
}

/**
 * Default `loadPages` for exportNotebookAsPdf: one request for every page's objects (see
 * notebook_export_data_api on the server — the same one route for an owned or a currently-public
 * notebook), instead of one request per page. Kept here, not inlined, so a caller that already
 * has the data in memory (the open editor) can pass its own loadPages instead — see editor.js.
 */
export function loadPagesFromExportDataUrl(exportDataUrl) {
  return async () => {
    const res = await fetch(exportDataUrl, { credentials: 'same-origin' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.success) throw new Error(data.message || 'Unable to load this notebook.');
    return (data.pages || []).map(page => ({
      id: page.id, title: page.title,
      objects: withExportSafeImageSrc((page.objects || []).map(row => ({ ...(row.payload?.fabric || {}), objectId: row.id, objectType: row.object_type }))),
    }));
  };
}

// Same sanitization the server used to apply to a PDF/JSON download's filename (still applies
// to JSON — see _safe_export_filename in app/routes/api/v01/notebooks.py) — kept in sync by hand
// since PDF filenames are no longer built server-side at all.
function safeExportFilename(title) {
  return Array.from(title || 'notebook').filter(ch => /[a-zA-Z0-9 _-]/.test(ch)).join('').trim() || 'notebook';
}

function hexToRgb(hex) {
  const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex || '');
  return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : [0, 0, 0];
}

// Same fixed geometry pdf_service.py's build_notebook_pdf used to draw the dot-grid page
// background as a reusable PDF Form XObject (one shared resource stamped on every page instead
// of a per-page cost) — reproduced here as a small canvas raster instead, for the same reason:
// jsPDF's addImage(..., alias) embeds an image ONCE and reuses it by reference wherever the same
// alias is passed again, so this canvas is built once per export and stamped behind every page
// exactly like the old Form XObject was, not re-rasterized or re-embedded per page. Unlike
// pdf_service.py's _safe_color/_flatten_over (a workaround for a ReportLab Form XObject dropping
// alpha), a canvas fillStyle understands an rgba(...)/hex string natively and composites it
// correctly on its own — no manual alpha flattening needed here.
const GRID_SPACING_PT = 20 * 0.75; // matches editor.css's 20px dot-grid at 100% zoom (see _CANVAS_PX_TO_PT below)
const GRID_DOT_RADIUS_PT = 0.75;   // 1px dot at 0.75pt/px
const GRID_RASTER_SCALE = 2;       // drawn at 2x so the dots stay crisp once embedded, same idea as this module's own page raster (toDataURL multiplier: 2)
function buildGridDataUrl(widthPt, heightPt, bg, dot) {
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(widthPt * GRID_RASTER_SCALE);
  canvas.height = Math.round(heightPt * GRID_RASTER_SCALE);
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = bg || '#f4f5f7';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = dot || '#e1e4e8';
  const spacing = GRID_SPACING_PT * GRID_RASTER_SCALE;
  const radius = GRID_DOT_RADIUS_PT * GRID_RASTER_SCALE;
  for (let y = spacing / 2; y < canvas.height; y += spacing) {
    for (let x = spacing / 2; x < canvas.width; x += spacing) {
      ctx.fillRect(x - radius, y - radius, radius * 2, radius * 2);
    }
  }
  return canvas.toDataURL('image/png');
}

/**
 * Renders every page to a PNG on an off-screen Fabric canvas (unchanged), then assembles the
 * whole multi-page PDF itself, in the browser, via jsPDF (loaded globally from the page's own
 * <script> tag, same way fabric.js already is) — no request to the server at all. `loadPages()`
 * supplies [{id, title, objects}], already withExportSafeImageSrc'd; pass
 * loadPagesFromExportDataUrl(url) for the normal (fetch-based) case, or a function reading
 * already-loaded data for a caller (the open editor) that has it in memory already.
 * `notebookTitle` only names the downloaded file (sanitized the same way the server used to).
 * `toast(message, type)` is the caller's own toast function — called with the success message on
 * completion, or the error message (type 'error') on failure; either way this function itself
 * never throws, so the caller doesn't need its own try/catch.
 */
export async function exportNotebookAsPdf({ notebookId, notebookTitle, btn, loadPages, toast, gridTheme, successMessage = 'PDF download started.' }) {
  if (!btn || btn.disabled) return;
  const originalHTML = btn.innerHTML;
  btn.disabled = true;
  btn.classList.add('exporting');
  const progress = createExportProgress(btn, 'Preparing');
  progress.start();
  const offEl = document.createElement('canvas');
  const offCanvas = new fabric.StaticCanvas(offEl, { renderOnAddRemove: false });
  const theme = gridTheme || (() => {
    const rootStyle = getComputedStyle(document.documentElement);
    return { bg: rootStyle.getPropertyValue('--bg').trim(), dot: rootStyle.getPropertyValue('--border').trim() };
  })();
  try {
    const jsPDFCtor = window.jspdf?.jsPDF;
    if (!jsPDFCtor) throw new Error('PDF export isn’t ready yet — please reload the page and try again.');
    const pages = await loadPages();
    if (!pages.length) throw new Error('No pages to export.');
    progress.setLabel('Generating');

    // Same fixed page geometry pdf_service.py's build_notebook_pdf used (_CANVAS_PX_TO_PT = 0.75
    // is the standard CSS-px-to-PDF-pt conversion) — EXPORT_PAGE_WIDTH/HEIGHT above already
    // mirror its _CANVAS_PX_W/H, so reusing them here keeps every PDF page exactly the notebook's
    // real, fixed page size, same as before.
    const CANVAS_PX_TO_PT = 0.75;
    const margin = 36, headerH = 26;
    const contentW = EXPORT_PAGE_WIDTH * CANVAS_PX_TO_PT, contentH = EXPORT_PAGE_HEIGHT * CANVAS_PX_TO_PT;
    const pageW = contentW + margin * 2, pageH = contentH + margin * 2 + headerH;
    const totalPages = pages.length;

    // jsPDF's `format` array alone is NOT taken as literal [width, height] — it defaults to
    // portrait orientation and silently SWAPS a landscape-shaped array to enforce that, which
    // left every exported page 1298x1872 instead of the intended 1872x1298: the page-content
    // image was then still drawn at its real (unswapped) 1800pt width onto a page only 1298pt
    // wide, so roughly the right third of every page's content fell off the page entirely.
    // Passing the real orientation makes jsPDF keep the dimensions exactly as given.
    const doc = new jsPDFCtor({ unit: 'pt', format: [pageW, pageH], orientation: pageW >= pageH ? 'landscape' : 'portrait', compress: true });
    const gridDataUrl = buildGridDataUrl(contentW, contentH, theme.bg, theme.dot);

    // The page raster used to render at 2x pixel density (4800x3200 per page) purely for print
    // sharpness — that's also the single most expensive step per page (canvas paint, PNG encode,
    // then jsPDF's own PNG decode/recompress while embedding it), and it happens once per page.
    // 1x matches the canvas's own native resolution — the exact same pixels the notebook already
    // renders with on screen at 100% zoom — so it's not a real quality loss for how these pages
    // are actually viewed, while cutting the dominant per-page cost to a quarter.
    const RASTER_MULTIPLIER = 1;
    let lastYield = performance.now();
    for (let i = 0; i < totalPages; i++) {
      const page = pages[i];
      const objects = await new Promise((resolve, reject) => {
        const result = fabric.util.enlivenObjects(page.objects, resolve);
        if (result && typeof result.then === 'function') result.then(resolve).catch(reject);
      });
      offCanvas.clear();
      offCanvas.setDimensions({ width: EXPORT_PAGE_WIDTH, height: EXPORT_PAGE_HEIGHT });
      objects.forEach(o => { constrainObjectToExportPage(o); offCanvas.add(o); });
      offCanvas.renderAll();
      const image = offCanvas.toDataURL({ format: 'png', multiplier: RASTER_MULTIPLIER });

      if (i > 0) doc.addPage([pageW, pageH]);
      doc.setFont('helvetica', 'bold'); doc.setFontSize(11); doc.setTextColor(...hexToRgb('#2c3e50'));
      doc.text(String(page.title || `Page ${i + 1}`), margin, margin - 6);
      doc.setDrawColor(...hexToRgb('#dddddd')); doc.setLineWidth(1);
      doc.line(margin, margin, pageW - margin, margin);
      // Grid stamped first (shared alias -> embedded once, reused every page — see
      // buildGridDataUrl's own comment), then this page's own transparent content raster on top.
      doc.addImage(gridDataUrl, 'PNG', margin, margin + headerH, contentW, contentH, 'notebookGrid');
      doc.addImage(image, 'PNG', margin, margin + headerH, contentW, contentH);
      doc.setFont('helvetica', 'normal'); doc.setFontSize(8); doc.setTextColor(...hexToRgb('#999999'));
      doc.text(`Page ${i + 1} of ${totalPages}`, pageW - margin, pageH - margin / 2, { align: 'right' });

      // Rasterizing + embedding every page is the dominant, genuinely measurable cost of this
      // export, so it drives the bar in direct proportion to pages actually done — not an
      // arbitrary schedule. The last stretch is reserved for jsPDF's own output/save call below.
      progress.advance(((i + 1) / totalPages) * 99);
      // A real yield to the browser (canvas rendering + jsPDF's image embedding are pure
      // synchronous CPU work, with no actual network/timer wait left anywhere in this loop to
      // naturally let the progress bar's setInterval/repaint run) — but only roughly every 80ms
      // of real work, not every single page: each yield itself costs a few ms of real wall-clock
      // time (a browser timer's own minimum resolution), which adds up across many pages if paid
      // on every one. This still keeps the bar visibly live without that compounding cost.
      const now = performance.now();
      if (now - lastYield > 80 || i === totalPages - 1) {
        await new Promise(resolve => setTimeout(resolve, 0));
        lastYield = performance.now();
      }
    }

    progress.setLabel('Ready');
    await progress.complete();
    progress.setLabel('Downloading');
    await new Promise(resolve => setTimeout(resolve, 0));
    doc.save(`${safeExportFilename(notebookTitle)}.pdf`);
    toast?.(successMessage);
    return true;
  } catch (error) {
    toast?.(error.message || 'Unable to export this notebook.', 'error');
    return false;
  } finally {
    offCanvas.dispose();
    progress.stop();
    btn.disabled = false;
    btn.classList.remove('exporting');
    btn.innerHTML = originalHTML;
  }
}

/**
 * Fetches the export JSON once to confirm it's genuinely ready (real byte progress off
 * Content-Length, a real error the instant anything goes wrong) — then, only once that has
 * succeeded, hands the browser a second, real GET to the same URL via a hidden iframe, which is
 * what actually triggers the download (see deliverViaIframeGet's own comment for why a blob:
 * URL can't reliably do this). Same `toast`/`successMessage` contract as exportNotebookAsPdf
 * above — never throws.
 */
export async function exportNotebookAsJson({ notebookId, btn, exportUrl, toast, successMessage = 'JSON download started.' }) {
  if (!btn || btn.disabled) return;
  const originalHTML = btn.innerHTML;
  btn.disabled = true;
  btn.classList.add('exporting');
  const progress = createExportProgress(btn, 'Generating');
  progress.start();
  try {
    const url = exportUrl(notebookId);
    await fetchToCompletion(url, { credentials: 'same-origin' }, fraction => progress.advance(fraction * 99));
    progress.setLabel('Ready');
    await progress.complete();
    progress.setLabel('Downloading');
    deliverViaIframeGet(url);
    toast?.(successMessage);
    return true;
  } catch (error) {
    toast?.(error.message || 'Unable to export this notebook.', 'error');
    return false;
  } finally {
    progress.stop();
    btn.disabled = false;
    btn.classList.remove('exporting');
    btn.innerHTML = originalHTML;
  }
}
