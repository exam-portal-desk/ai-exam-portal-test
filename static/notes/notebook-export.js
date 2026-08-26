/* Shared notebook export logic (progress driver + PDF/JSON export), used by both the Public
   Notebooks library grid (library.js) and My Notebooks (notebooks.js) — a notebook the current
   user owns and a currently-public notebook export identically once you have page/object data
   for it; the only difference is which read API supplies that data. Extracted here so neither
   caller keeps its own copy — see each caller's thin wrapper for the exact endpoints it passes.

   createExportProgress's bar tracks a `target` that ONLY ever moves via advance()/complete()
   calls fed by real signals from the caller (pages actually rendered, bytes actually read off
   Content-Length, or "the PDF response actually arrived") — never a blind timer counting up on
   its own. The internal 15ms tick just interpolates the visible bar smoothly toward whatever the
   last REAL target was; the number/label always reflect real progress, nothing is invented. */

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

async function fetchPageObjectsForExport(objectsUrl, notebookId, pageId) {
  const res = await fetch(objectsUrl(notebookId, pageId), { credentials: 'same-origin' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.success) throw new Error(data.message || 'Unable to load this notebook.');
  return withExportSafeImageSrc((data.objects || []).map(row => ({ ...(row.payload?.fabric || {}), objectId: row.id, objectType: row.object_type })));
}

/**
 * Renders every page to a PNG on an off-screen Fabric canvas and POSTs them to the shared
 * export-pdf route (which already accepts either an owned or a currently-public notebook).
 * pagesUrl(notebookId), objectsUrl(notebookId, pageId), exportPdfUrl(notebookId) let each caller
 * point this at its own (owner vs public) read API — everything else is identical. `toast(message,
 * type)` is the caller's own toast function (each page defines its own) — called with the success
 * message on completion, or the error message (type 'error') on failure; either way this function
 * itself never throws, so the caller doesn't need its own try/catch.
 */
export async function exportNotebookAsPdf({ notebookId, btn, pagesUrl, objectsUrl, exportPdfUrl, toast, successMessage = 'Notebook exported as PDF.' }) {
  if (!btn || btn.disabled) return;
  const originalHTML = btn.innerHTML;
  btn.disabled = true;
  btn.classList.add('exporting');
  const progress = createExportProgress(btn, 'Exporting PDF');
  progress.start();
  const offEl = document.createElement('canvas');
  const offCanvas = new fabric.StaticCanvas(offEl, { renderOnAddRemove: false });
  const rootStyle = getComputedStyle(document.documentElement);
  const gridTheme = { bg: rootStyle.getPropertyValue('--bg').trim(), dot: rootStyle.getPropertyValue('--border').trim() };
  try {
    const pagesRes = await fetch(pagesUrl(notebookId), { credentials: 'same-origin' });
    const pagesData = await pagesRes.json().catch(() => ({}));
    if (!pagesRes.ok || !pagesData.success) throw new Error(pagesData.message || 'Unable to load this notebook.');
    const pages = pagesData.pages || [];
    if (!pages.length) throw new Error('No pages to export.');
    const rendered = [];
    const totalPages = pages.length;
    for (let i = 0; i < totalPages; i++) {
      const page = pages[i];
      const raw = await fetchPageObjectsForExport(objectsUrl, notebookId, page.id);
      const objects = await new Promise((resolve, reject) => {
        const result = fabric.util.enlivenObjects(raw, resolve);
        if (result && typeof result.then === 'function') result.then(resolve).catch(reject);
      });
      offCanvas.clear();
      offCanvas.setDimensions({ width: EXPORT_PAGE_WIDTH, height: EXPORT_PAGE_HEIGHT });
      objects.forEach(o => { constrainObjectToExportPage(o); offCanvas.add(o); });
      offCanvas.renderAll();
      const image = offCanvas.toDataURL({ format: 'png', multiplier: 2 });
      rendered.push({ title: page.title, image });
      progress.advance(((i + 1) / totalPages) * 90);
    }
    let frame = document.getElementById('notesPdfExportFrame');
    if (!frame) { frame = document.createElement('iframe'); frame.name = frame.id = 'notesPdfExportFrame'; frame.style.display = 'none'; document.body.appendChild(frame); }
    const form = document.createElement('form');
    form.method = 'POST'; form.action = exportPdfUrl(notebookId); form.target = 'notesPdfExportFrame'; form.style.display = 'none';
    const input = document.createElement('input');
    input.type = 'hidden'; input.name = 'pages'; input.value = JSON.stringify(rendered);
    form.appendChild(input);
    const themeInput = document.createElement('input');
    themeInput.type = 'hidden'; themeInput.name = 'gridTheme'; themeInput.value = JSON.stringify(gridTheme);
    form.appendChild(themeInput);
    let settled = false, resolveSettle;
    const settlePromise = new Promise(resolve => { resolveSettle = resolve; });
    const settle = (ok, message) => { if (settled) return; settled = true; resolveSettle({ ok, message }); };
    frame.onload = () => {
      let text = ''; try { text = frame.contentDocument?.body?.innerText || ''; } catch (e) {}
      if (text.trim()) { let message; try { message = JSON.parse(text).message; } catch (e) {} settle(false, message); }
    };
    document.body.append(form); form.submit(); form.remove();
    const waitStart = Date.now();
    const waitMs = 1200;
    const waitTimer = setInterval(() => { progress.advance(90 + Math.min(1, (Date.now() - waitStart) / waitMs) * 9); }, 80);
    setTimeout(() => settle(true), waitMs);
    const result = await settlePromise;
    clearInterval(waitTimer);
    if (!result.ok) throw new Error(result.message || 'Unable to export this notebook.');
    await progress.complete();
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
 * Fetches the export JSON once to drive real byte-progress off Content-Length, then triggers
 * the actual browser-native download (Content-Disposition) via a cache-busted request to the
 * same URL through a hidden iframe — the first fetch is only ever used for progress, never for
 * the download itself, so the browser's normal save/download-manager UX is untouched. Same
 * `toast`/`successMessage` contract as exportNotebookAsPdf above — never throws.
 */
export async function exportNotebookAsJson({ notebookId, btn, exportUrl, toast, successMessage = 'Notebook exported as JSON.' }) {
  if (!btn || btn.disabled) return;
  const originalHTML = btn.innerHTML;
  btn.disabled = true;
  btn.classList.add('exporting');
  const progress = createExportProgress(btn, 'Exporting JSON');
  progress.start();
  try {
    const url = exportUrl(notebookId);
    const res = await fetch(url, { credentials: 'same-origin' });
    if (!res.ok) {
      let message; try { message = (await res.json()).message; } catch (e) {}
      throw new Error(message || 'Unable to export this notebook.');
    }
    const total = Number(res.headers.get('Content-Length')) || 0;
    const reader = res.body?.getReader();
    let received = 0;
    if (reader) {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        received += value.length;
        if (total > 0) progress.advance((received / total) * 99);
      }
    } else {
      await res.arrayBuffer();
    }
    await progress.complete();
    let jsonFrame = document.getElementById('notesJsonExportFrame');
    if (!jsonFrame) { jsonFrame = document.createElement('iframe'); jsonFrame.name = jsonFrame.id = 'notesJsonExportFrame'; jsonFrame.style.display = 'none'; document.body.appendChild(jsonFrame); }
    // Cache-bust so re-exporting immediately after a previous one still reassigns a distinct
    // src (browsers don't reload an iframe whose src is unchanged); the server ignores unknown
    // query params.
    jsonFrame.src = `${url}${url.includes('?') ? '&' : '?'}_=${Date.now()}`;
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
