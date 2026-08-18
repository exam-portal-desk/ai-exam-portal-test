const toast=(m,t='success')=>{const r=document.getElementById('notesToastRegion'),e=document.createElement('div');e.className=`notes-toast ${t}`;e.textContent=m;r.append(e);setTimeout(()=>e.remove(),4000)};
// Narrowed to like/bookmark specifically (was a bare [data-action] selector) so it doesn't also
// pick up the export-pdf/export-json buttons added below, which need their own click handling.
document.querySelectorAll('.library-actions').forEach(group=>group.querySelectorAll('button[data-action="like"],button[data-action="bookmark"]').forEach(button=>button.addEventListener('click',async()=>{
  const a=button.dataset.action,id=group.dataset.id;button.disabled=true;
  try{
    const r=await fetch(`/api/v01/library/${id}/${a}`,{method:'POST',credentials:'same-origin'}),d=await r.json();
    if(!r.ok||!d.success)throw Error(d.message||'Unable to complete action.');
    button.querySelector('span').textContent=d[a==='like'?'likes':'bookmarks'];
    button.classList.toggle('active',d.active);
    button.querySelector('i').className=`${d.active?'fas':'far'} fa-${a==='like'?'heart':'bookmark'}`;
    toast(d.active?'Saved.':'Removed.');
  }catch(e){toast(e.message,'error')}
  finally{button.disabled=false}
})));

/* PDF/JSON export for a Public Notes Library card — same technique, same progress driver, and
   same server endpoints as the private notebook editor's Export PDF/Export JSON (see
   exportNotebookPdf/exportNotebookJson/createExportProgress in editor.js): render each page's
   objects to a PNG on an off-screen Fabric canvas, POST them to the existing /export-pdf route
   (already accepts a currently-public notebook, not just an owned one — see that route's own
   comment), and stream the JSON export for real byte progress before handing the download off
   to the browser via Content-Disposition. The library page has no live canvas/page cache to
   reuse directly (a card is just metadata, not an open notebook), so this re-fetches each page's
   objects from the public read API instead of editor.js's in-memory pageCache — everything
   downstream (rendering, endpoints, progress behavior) is the same. */
function createExportProgress(btn, label) {
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
const LIBRARY_PAGE_WIDTH = 2400, LIBRARY_PAGE_HEIGHT = 1600;
function constrainObjectToLibraryPage(object) {
  if (!object) return;
  object.setCoords();
  const box = object.getBoundingRect(true, true);
  let dx = 0, dy = 0;
  if (box.left < 0) dx = -box.left;
  else if (box.left + box.width > LIBRARY_PAGE_WIDTH) dx = LIBRARY_PAGE_WIDTH - (box.left + box.width);
  if (box.top < 0) dy = -box.top;
  else if (box.top + box.height > LIBRARY_PAGE_HEIGHT) dy = LIBRARY_PAGE_HEIGHT - (box.top + box.height);
  if (dx || dy) { object.set({ left: (object.left || 0) + dx, top: (object.top || 0) + dy }); object.setCoords(); }
}
// Same same-origin rewrite as editor.js's withExportSafeImageSrc, for the same reason: the
// export canvas's toDataURL() would otherwise taint on a cross-origin signed image URL.
function withExportSafeImageSrc(raw) {
  return raw.map(entry => (entry.type === 'image' && entry.assetId) ? { ...entry, src: `/api/v01/assets/${entry.assetId}/file` } : entry);
}
async function getLibraryPageObjects(notebookId, pageId) {
  const res = await fetch(`/api/v01/library/${notebookId}/pages/${pageId}/objects`, { credentials: 'same-origin' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.success) throw new Error(data.message || 'Unable to load this notebook.');
  return withExportSafeImageSrc((data.objects || []).map(row => ({ ...(row.payload?.fabric || {}), objectId: row.id, objectType: row.object_type })));
}
async function exportLibraryNotebookPdf(notebookId, btn) {
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
    const pagesRes = await fetch(`/api/v01/library/${notebookId}/pages`, { credentials: 'same-origin' });
    const pagesData = await pagesRes.json().catch(() => ({}));
    if (!pagesRes.ok || !pagesData.success) throw new Error(pagesData.message || 'Unable to load this notebook.');
    const pages = pagesData.pages || [];
    if (!pages.length) throw new Error('No pages to export.');
    const rendered = [];
    const totalPages = pages.length;
    for (let i = 0; i < totalPages; i++) {
      const page = pages[i];
      const raw = await getLibraryPageObjects(notebookId, page.id);
      const objects = await new Promise((resolve, reject) => {
        const result = fabric.util.enlivenObjects(raw, resolve);
        if (result && typeof result.then === 'function') result.then(resolve).catch(reject);
      });
      offCanvas.clear();
      offCanvas.setDimensions({ width: LIBRARY_PAGE_WIDTH, height: LIBRARY_PAGE_HEIGHT });
      objects.forEach(o => { constrainObjectToLibraryPage(o); offCanvas.add(o); });
      offCanvas.renderAll();
      const image = offCanvas.toDataURL({ format: 'png', multiplier: 2 });
      rendered.push({ title: page.title, image });
      progress.advance(((i + 1) / totalPages) * 90);
    }
    let frame = document.getElementById('notesPdfExportFrame');
    if (!frame) { frame = document.createElement('iframe'); frame.name = frame.id = 'notesPdfExportFrame'; frame.style.display = 'none'; document.body.appendChild(frame); }
    const form = document.createElement('form');
    form.method = 'POST'; form.action = `/api/v01/notebooks/${notebookId}/export-pdf`; form.target = 'notesPdfExportFrame'; form.style.display = 'none';
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
    toast('Notebook exported as PDF.');
  } catch (error) {
    toast(error.message || 'Unable to export this notebook.', 'error');
  } finally {
    offCanvas.dispose();
    progress.stop();
    btn.disabled = false;
    btn.classList.remove('exporting');
    btn.innerHTML = originalHTML;
  }
}
async function exportLibraryNotebookJson(notebookId, btn) {
  if (!btn || btn.disabled) return;
  const originalHTML = btn.innerHTML;
  btn.disabled = true;
  btn.classList.add('exporting');
  const progress = createExportProgress(btn, 'Exporting JSON');
  progress.start();
  try {
    const exportUrl = `/api/v01/library/${notebookId}/export`;
    const res = await fetch(exportUrl, { credentials: 'same-origin' });
    if (!res.ok) {
      let message; try { message = (await res.json()).message; } catch (e) {}
      throw new Error(message || 'Unable to export this notebook.');
    }
    // Same reasoning as editor.js's exportNotebookJson: the body is only read here to drive
    // real byte progress off Content-Length. The actual download is triggered below through
    // the same endpoint's Content-Disposition header, not a client-side Blob — a blob: URL has
    // no Content-Disposition of its own, so it opens/displays the JSON instead of downloading.
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
    jsonFrame.src = `${exportUrl}${exportUrl.includes('?') ? '&' : '?'}_=${Date.now()}`;
    toast('Notebook exported as JSON.');
  } catch (error) {
    toast(error.message || 'Unable to export this notebook.', 'error');
  } finally {
    progress.stop();
    btn.disabled = false;
    btn.classList.remove('exporting');
    btn.innerHTML = originalHTML;
  }
}
document.querySelectorAll('.library-actions').forEach(group => {
  const id = group.dataset.id;
  const pdfBtn = group.querySelector('[data-action="export-pdf"]');
  const jsonBtn = group.querySelector('[data-action="export-json"]');
  pdfBtn?.addEventListener('click', () => exportLibraryNotebookPdf(id, pdfBtn));
  jsonBtn?.addEventListener('click', () => exportLibraryNotebookJson(id, jsonBtn));
});