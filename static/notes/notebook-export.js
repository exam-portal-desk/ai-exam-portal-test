/* Shared notebook export logic (progress driver + PDF/JSON export + download delivery), used by
   the Public Notebooks library grid (library.js), My Notebooks (notebooks.js) AND the open
   notebook editor (editor.js) — a notebook the current user owns, an open-for-editing notebook,
   and a currently-public notebook export identically once you have page/object data for it; the
   only difference between callers is WHERE that data comes from (an HTTP fetch for the first
   two, already-in-memory pageCache for the editor) and, for PDF, which theme colors to read the
   dot-grid background from (the editor reads its own live canvas shell; the other two read the
   document root). Extracted here so none of the three callers keeps its own copy.

   createExportProgress's bar tracks a `target` that ONLY ever moves via advance()/complete()
   calls fed by real signals from the caller (pages actually rendered, bytes actually read off
   Content-Length, or "the full response has actually arrived") — never a blind timer counting up
   on its own. The internal 15ms tick just interpolates the visible bar smoothly toward whatever
   the last REAL target was; the number/label always reflect real progress, nothing is invented.

   Lifecycle both exportNotebookAsPdf and exportNotebookAsJson drive the button label through:
   Preparing -> Generating -> Finalizing -> Ready -> Downloading... . "Ready" only happens once
   the export's complete bytes have actually arrived in the browser — not when the server
   responds, not on a timer, but once fetchToCompletion's read loop has actually finished (real
   byte progress off Content-Length along the way, and a genuine error — never a false "success"
   — the instant the request fails). The browser save is then triggered the same tick "Ready" is
   reached, via a REAL request to the export URL (see deliverViaIframe*() below) — never a blob:
   URL: a blob has no Content-Disposition of its own, so navigating/clicking one is at the mercy
   of the browser's own guess about how to handle it (Chrome's built-in PDF viewer in particular
   can intercept an application/pdf blob even behind a clicked <a download>, showing it inline
   with a blob: address instead of saving it — that's a real request to a real URL below avoids
   entirely, since the server's Content-Disposition: attachment leaves no ambiguity). The success
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

// PDF: the payload (rendered pages + theme) only travels the network twice — once above, via
// fetchToCompletion, to confirm the export is genuinely ready; once here, as a real form POST
// into the hidden iframe, which is the actual download. Both build the same PDF server-side, a
// deliberate, small, one-time-per-click trade for a delivery mechanism proven to trigger a real
// browser download reliably (see the module comment above).
function deliverViaIframeForm(url, formData) {
  const frame = downloadFrame();
  const form = document.createElement('form');
  form.method = 'POST'; form.action = url; form.target = frame.id; form.style.display = 'none';
  for (const [name, value] of formData.entries()) {
    const input = document.createElement('input');
    input.type = 'hidden'; input.name = name; input.value = value;
    form.appendChild(input);
  }
  document.body.appendChild(form);
  form.submit();
  form.remove();
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

/**
 * Renders every page to a PNG on an off-screen Fabric canvas, then POSTs them all to the shared
 * export-pdf route (which already accepts either an owned or a currently-public notebook) and
 * waits for the COMPLETE PDF to actually arrive before calling this done — no timer ever stands
 * in for that. Only once that's confirmed does it hand the same pages to the browser a second
 * time, as a real form POST into a hidden iframe — the actual download (see deliverViaIframeForm's
 * own comment for why a blob: URL can't reliably trigger one for a PDF). `loadPages()` supplies
 * [{id, title, objects}], already withExportSafeImageSrc'd; pass loadPagesFromExportDataUrl(url)
 * for the normal (fetch-based) case, or a function reading already-loaded data for a caller (the
 * open editor) that has it in memory already. `toast(message, type)` is the caller's own toast
 * function — called with the success message on completion, or the error message (type 'error')
 * on failure; either way this function itself never throws, so the caller doesn't need its own
 * try/catch.
 */
export async function exportNotebookAsPdf({ notebookId, btn, loadPages, exportPdfUrl, toast, gridTheme, successMessage = 'PDF download started.' }) {
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
    const pages = await loadPages();
    if (!pages.length) throw new Error('No pages to export.');
    progress.setLabel('Generating');
    const rendered = [];
    const totalPages = pages.length;
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
      const image = offCanvas.toDataURL({ format: 'png', multiplier: 2 });
      rendered.push({ title: page.title, image });
      // Rasterizing every page's objects is the dominant, genuinely measurable cost of this
      // export, so it drives most of the bar in direct proportion to pages actually rendered —
      // not an arbitrary schedule. The last stretch is reserved for the request that actually
      // builds and delivers the PDF (see below), paced by its own real byte progress.
      progress.advance(((i + 1) / totalPages) * 80);
    }

    progress.setLabel('Finalizing');
    const form = new FormData();
    form.append('pages', JSON.stringify(rendered));
    form.append('gridTheme', JSON.stringify(theme));
    const url = exportPdfUrl(notebookId);
    await fetchToCompletion(url, { method: 'POST', credentials: 'same-origin', body: form }, fraction => progress.advance(80 + fraction * 19));

    progress.setLabel('Ready');
    await progress.complete();
    progress.setLabel('Downloading');
    deliverViaIframeForm(url, form);
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
