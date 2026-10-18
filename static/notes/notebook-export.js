/* Shared notebook export logic (progress driver + PDF/JSON export + download delivery), used by
   the Public Notebooks library grid (library.js), My Notebooks (notebooks.js) AND the open
   notebook editor (editor.js) — a notebook the current user owns, an open-for-editing notebook,
   and a currently-public notebook export identically once you have page/object data for it; the
   only difference between callers is WHERE that data comes from (an HTTP fetch for the first
   two, already-in-memory pageCache for the editor) and, for PDF, which theme colors to read the
   dot-grid background from (the editor reads its own live canvas shell; the other two read the
   document root). Extracted here so none of the three callers keeps its own copy.

   PDF export is entirely client-side and no longer flattens a page into one bitmap. Each page is built by pdf-writer.js (a streaming
   PDF writer: finished pages and images go straight into Blob parts, so peak memory follows ONE page, not the document) and
   pdf-render.js (strokes, lines, shapes and the grid become real vector PDF paths; photographs are embedded as image assets, JPEG
   bytes untouched where safe; only what cannot be drawn exactly as vector — text boxes, shadows, clip paths, gradients — is
   rasterised, one object at a time, at an adaptive scale under a pixel budget). There is NO request to the server for the PDF itself.
   Practical limits are documented at buildNotebookPdf() below.

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
import { PAPERS, chooseExportPaper, resolveForExport, parseColor } from './ink-theme.js';
import { PdfWriter, isWinAnsi } from './pdf-writer.js';
import { ObjectPainter } from './pdf-render.js';

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
// request — see deliverViaIframeGet below — only ever issued once this one
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
  const c = parseColor(hex);
  return c ? [c.r, c.g, c.b] : [0, 0, 0];
}

// Same fixed page layout the PDF always had: the notebook page at 0.75 pt per canvas px, 36 pt margins, a 26 pt header strip.
const CANVAS_PX_TO_PT = 0.75, PDF_MARGIN = 36, PDF_HEADER_H = 26;
const GRID_SPACING_PT = 20 * CANVAS_PX_TO_PT;       // the editor's 20px dot grid
const GRID_DOT_PT = 2 * 0.75;                       // 1px dot radius at 0.75pt/px -> 1.5pt square, as before

function enlivenBatch(rawObjects) {
  return new Promise((resolve, reject) => {
    const result = fabric.util.enlivenObjects(rawObjects, resolve);
    if (result && typeof result.then === 'function') result.then(resolve).catch(reject);
  });
}

/**
 * Fabric objects for one page. Fabric leaves a hole for an object it could not build (an image whose file would not load), and one
 * corrupt entry can make the whole batch throw — so a failed batch is retried object by object, and the caller counts every hole.
 */
async function enlivenPage(rawObjects) {
  try { return await enlivenBatch(rawObjects); }
  catch (error) {
    const rebuilt = [];
    for (const entry of rawObjects) {
      try { rebuilt.push((await enlivenBatch([entry]))[0] || null); } catch (e) { rebuilt.push(null); }
    }
    return rebuilt;
  }
}

/**
 * Builds the whole notebook PDF and returns { blob, stats } — no UI, no download (exportNotebookAsPdf wraps this).
 *
 * Memory model: ONE page's Fabric objects are alive at a time; each finished page, and each image, is appended to the writer's Blob
 * parts and released. Raster fallback uses one reusable scratch canvas capped at rasterBudgetPx (default 6 MP, well under the ~16 MP
 * area limit of iOS Safari). The raw JSON of the notebook is dropped page by page as it is rendered.
 * Practical limits (not guarantees): the final document is still assembled as one Blob in the browser, so total size is bounded by
 * what the browser can hold for a Blob; the page JSON arrives up front (callers' loadPages contract), which is bounded by the
 * app's own per-page object cap; very image-heavy notebooks are limited by the images' own (already bounded) embedded size.
 *
 * `onProgress({ page, total, phase })` is called per page; `signal` cancels between objects and pages. Objects that cannot be
 * exported are never dropped silently: they are listed in stats.failed / stats.lostOnLoad for the caller to report.
 */
export async function buildNotebookPdf({ pages, title = '', signal, onProgress, limits, deps = {} }) {
  const fabricNs = deps.fabric || window.fabric;
  const createCanvas = deps.createCanvas || (() => document.createElement('canvas'));
  if (!pages || !pages.length) throw new Error('No pages to export.');
  const exportPaper = chooseExportPaper(pages.map(p => p.objects));
  const paperTheme = PAPERS[exportPaper];
  const contentW = EXPORT_PAGE_WIDTH * CANVAS_PX_TO_PT, contentH = EXPORT_PAGE_HEIGHT * CANVAS_PX_TO_PT;
  const pageW = contentW + PDF_MARGIN * 2, pageH = contentH + PDF_MARGIN * 2 + PDF_HEADER_H;
  const contentBottom = pageH - PDF_MARGIN - PDF_HEADER_H - contentH;     // PDF space is y-up
  const writer = new PdfWriter({ title: isWinAnsi(title) ? title : '', deflate: deps.deflate });
  const painter = new ObjectPainter({ writer, fabric: fabricNs, createCanvas, fetchBytes: deps.fetchBytes, signal, limits });
  const stats = painter.stats;
  stats.lostOnLoad = 0; stats.pages = pages.length; stats.paper = exportPaper;
  try {
    const dot = parseColor(paperTheme.dot), paper = parseColor(paperTheme.paper);
    await writer.definePattern('Grid', { tile: GRID_SPACING_PT, dot: GRID_DOT_PT, origin: [PDF_MARGIN, contentBottom], color: [dot.r, dot.g, dot.b], alpha: dot.a });
    let lastYield = performance.now();
    for (let i = 0; i < pages.length; i++) {
      painter.throwIfAborted();
      const page = pages[i], raw = resolveForExport(page.objects, exportPaper);
      page.objects = null;                                                  // the raw JSON is no longer needed once resolved
      const objects = await enlivenPage(raw);
      stats.lostOnLoad += raw.length - objects.filter(Boolean).length;      // e.g. an image whose file could not be loaded: reported, never silent
      const pg = writer.beginPage(pageW, pageH);
      const label = String(page.title || `Page ${i + 1}`);
      if (isWinAnsi(label)) pg.text(label, PDF_MARGIN, pageH - (PDF_MARGIN - 6), { font: 'F2', size: 11, color: hexToRgb('#2c3e50') });
      else await painter.drawTextBitmap(pg, label, { x: PDF_MARGIN, y: pageH - (PDF_MARGIN - 6), size: 11 });
      pg.save(); pg.strokeRGB(...hexToRgb('#dddddd')); pg.lineWidth(1); pg.moveTo(PDF_MARGIN, pageH - PDF_MARGIN); pg.lineTo(pageW - PDF_MARGIN, pageH - PDF_MARGIN); pg.paint('S'); pg.restore();
      pg.save(); pg.fillRGB(paper.r, paper.g, paper.b); pg.rect(PDF_MARGIN, contentBottom, contentW, contentH); pg.paint('f');
      pg.fillPattern('Grid'); pg.rect(PDF_MARGIN, contentBottom, contentW, contentH); pg.paint('f'); pg.restore();
      pg.text(`Page ${i + 1} of ${pages.length}`, pageW - PDF_MARGIN, PDF_MARGIN / 2, { size: 8, color: hexToRgb('#999999'), align: 'right' });
      // From here on the page is in canvas pixels, y down — the notebook's own coordinate system — clipped to the page like the old canvas was.
      pg.save(); pg.cm(CANVAS_PX_TO_PT, 0, 0, -CANVAS_PX_TO_PT, PDF_MARGIN, pageH - PDF_MARGIN - PDF_HEADER_H);
      pg.rect(0, 0, EXPORT_PAGE_WIDTH, EXPORT_PAGE_HEIGHT); pg.raw('W n');
      for (const object of objects) {
        constrainObjectToExportPage(object);
        await painter.drawObject(pg, object);
        const now = performance.now();
        if (now - lastYield > 60) { await new Promise(resolve => setTimeout(resolve, 0)); lastYield = performance.now(); }
      }
      pg.restore();
      await writer.endPage(pg);
      objects.length = 0;
      onProgress?.({ page: i + 1, total: pages.length, phase: 'render', stats });
      const now = performance.now();
      if (now - lastYield > 30 || i === pages.length - 1) { await new Promise(resolve => setTimeout(resolve, 0)); lastYield = performance.now(); }
    }
    painter.throwIfAborted();
    const blob = writer.finish();
    stats.bytes = blob.size; stats.images = writer.stats.images; stats.imageBytes = writer.stats.imageBytes;
    return { blob, stats };
  } catch (error) {
    writer.discard();
    throw error;
  } finally {
    painter.dispose();
  }
}

/**
 * Hands the finished PDF to the browser. `download` makes it a file download; `target=_blank` + `noopener` guarantees that a browser
 * which shows PDFs inline instead opens them in a NEW tab and never replaces the notebook the user is working in. A blob: address is
 * how any file built in the browser is exposed (it cannot be renamed); the viewer tab's title comes from the PDF's own /Title. The
 * object URL stays valid for 5 minutes so that tab can be reloaded, then is released.
 */
function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = filename; link.target = '_blank'; link.rel = 'noopener'; link.style.display = 'none';
  document.body.appendChild(link); link.click();
  setTimeout(() => { link.remove(); URL.revokeObjectURL(url); }, 5 * 60 * 1000);
  return url;
}

/**
 * A browser gives a page no way to ask "was my download or new tab blocked?", so instead of guessing, every finished export also
 * shows this small notice for a minute. Its buttons are real clicks by the user, which no popup blocker refuses: Download saves the
 * file again, and Open in new tab reports it when even that is blocked (window.open returns null), so a blocked export is never a
 * dead end.
 */
function showReadyPill(url, filename, toast) {
  ensureExportPillStyle();
  document.querySelector('.notes-export-pill.ready')?.remove();
  const pill = document.createElement('div'); pill.className = 'notes-export-pill ready'; pill.setAttribute('role', 'status');
  pill.innerHTML = '<span class="lbl">PDF is ready. If nothing downloaded:</span><button type="button" data-act="download">Download</button><button type="button" data-act="open">Open in new tab</button><button type="button" data-act="close" aria-label="Dismiss">✕</button>';
  const remove = () => pill.remove();
  pill.addEventListener('click', event => {
    const act = event.target.closest('button')?.dataset.act;
    if (act === 'download') { const a = document.createElement('a'); a.href = url; a.download = filename; a.style.display = 'none'; document.body.appendChild(a); a.click(); a.remove(); }
    else if (act === 'open') {
      const tab = window.open(url, '_blank');
      if (!tab) toast?.('Your browser blocked the new tab — use Download instead.', 'error');
      else { try { tab.opener = null; } catch (e) { /* cross-origin guard: nothing to clear */ } }
    } else if (act === 'close') remove();
  });
  document.body.appendChild(pill);
  setTimeout(remove, 60000);
}

function ensureExportPillStyle() {
  if (document.getElementById('notesExportPillStyle')) return;
  const style = document.createElement('style'); style.id = 'notesExportPillStyle';
  style.textContent = '.notes-export-pill{position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:3000;display:flex;align-items:center;gap:.6rem;max-width:calc(100vw - 24px);padding:.4rem .5rem .4rem .9rem;background:var(--surface,#fff);color:var(--text-1,#222);border:1px solid var(--border,#ddd);border-radius:999px;box-shadow:var(--shadow-lg,0 8px 24px rgba(0,0,0,.18));font-size:.8rem;font-weight:600}'
    + '.notes-export-pill .bar{width:90px;height:6px;border-radius:3px;background:var(--border,#ddd);overflow:hidden}.notes-export-pill .bar i{display:block;height:100%;width:0;background:var(--accent,#4a86e8);transition:width .15s}'
    + '.notes-export-pill.ready{flex-wrap:wrap;justify-content:center;border-radius:16px}'
    + '.notes-export-pill button{min-height:32px;padding:0 .85rem;border:1px solid var(--border,#ddd);border-radius:999px;background:transparent;color:inherit;font:inherit;cursor:pointer}';
  document.head.appendChild(style);
}

let activeExport = null;     // at most one PDF export at a time, across every export button on the page

/**
 * Exports the notebook as a PDF entirely in the browser (see buildNotebookPdf for the pipeline) and starts the download. `loadPages()`
 * supplies [{id, title, objects}], already withExportSafeImageSrc'd; pass loadPagesFromExportDataUrl(url) for the normal (fetch-based)
 * case, or a function reading already-loaded data for a caller (the open editor) that has it in memory already. `notebookTitle` only
 * names the downloaded file. `toast(message, type)` is the caller's own toast function; this function never throws. While running,
 * a small progress pill shows the page count with a Cancel button; cancelling, failing or finishing always restores the button.
 */
export async function exportNotebookAsPdf({ notebookId, notebookTitle, btn, loadPages, toast, gridTheme, successMessage = 'PDF download started.' }) {
  if (!btn || btn.disabled) return;
  if (activeExport) { toast?.('A PDF export is already running — please wait for it to finish.', 'error'); return; }
  const originalHTML = btn.innerHTML;
  const controller = new AbortController();
  activeExport = controller;
  btn.disabled = true;
  btn.classList.add('exporting');
  const progress = createExportProgress(btn, 'Preparing');
  progress.start();
  ensureExportPillStyle();
  const pill = document.createElement('div'); pill.className = 'notes-export-pill'; pill.setAttribute('role', 'status');
  pill.innerHTML = '<span class="lbl">Preparing PDF…</span><span class="bar"><i></i></span><button type="button">Cancel</button>';
  const pillLabel = pill.querySelector('.lbl'), pillBar = pill.querySelector('.bar i');
  pill.querySelector('button').addEventListener('click', () => { controller.abort(); pillLabel.textContent = 'Cancelling…'; });
  document.body.appendChild(pill);
  try {
    const pages = await loadPages();
    if (!pages.length) throw new Error('No pages to export.');
    progress.setLabel('Generating');
    const { blob, stats } = await buildNotebookPdf({
      pages, title: notebookTitle || '', signal: controller.signal,
      onProgress: ({ page, total }) => {
        progress.setLabel(`Page ${page}/${total}`); progress.advance((page / total) * 99);
        pillLabel.textContent = `Exporting PDF — page ${page} of ${total}`; pillBar.style.width = `${Math.round((page / total) * 100)}%`;
      },
    });
    progress.setLabel('Ready');
    await progress.complete();
    progress.setLabel('Downloading');
    await new Promise(resolve => setTimeout(resolve, 0));
    const fileName = `${safeExportFilename(notebookTitle)}.pdf`;
    const fileUrl = saveBlob(blob, fileName);
    showReadyPill(fileUrl, fileName, toast);
    const problems = stats.failed.length + stats.lostOnLoad;
    toast?.(problems ? `${successMessage} ${problems} object${problems === 1 ? '' : 's'} (for example an image that could not be loaded) could not be included.` : successMessage, problems ? 'error' : undefined);
    return true;
  } catch (error) {
    if (error?.name === 'AbortError') toast?.('PDF export cancelled.');
    else toast?.(error.message || 'Unable to export this notebook.', 'error');
    return false;
  } finally {
    activeExport = null;
    pill.remove();
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
