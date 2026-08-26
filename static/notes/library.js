import { exportNotebookAsPdf, exportNotebookAsJson } from './notebook-export.js';

const toast=(m,t='success')=>{const r=document.getElementById('notesToastRegion'),e=document.createElement('div');e.className=`notes-toast ${t}`;e.textContent=m;r.append(e);setTimeout(()=>e.remove(),4000)};
const libraryGrid=document.getElementById('libraryGrid');
async function handleLikeOrBookmark(button,group){
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
}

/* PDF/JSON export for a Public Notes Library card — same technique, same progress driver, and
   same server endpoints as My Notebooks' export (see static/notes/notebook-export.js, shared by
   both so there's one implementation, not two): render each page's objects to a PNG on an
   off-screen Fabric canvas, POST them to the existing /export-pdf route (already accepts a
   currently-public notebook, not just an owned one — see that route's own comment), and stream
   the JSON export for real byte progress before handing the download off to the browser via
   Content-Disposition. The library page has no live canvas/page cache to reuse directly (a card
   is just metadata, not an open notebook), so this re-fetches each page's objects from the
   public read API — only the URLs passed below differ from My Notebooks' wrapper. */
function exportLibraryNotebookPdf(notebookId, btn) {
  return exportNotebookAsPdf({
    notebookId, btn, toast,
    pagesUrl: id => `/api/v01/library/${id}/pages`,
    objectsUrl: (id, pageId) => `/api/v01/library/${id}/pages/${pageId}/objects`,
    exportPdfUrl: id => `/api/v01/notebooks/${id}/export-pdf`,
  });
}
function exportLibraryNotebookJson(notebookId, btn) {
  return exportNotebookAsJson({
    notebookId, btn, toast,
    exportUrl: id => `/api/v01/library/${id}/export`,
  });
}
/* Event delegation (single listener on the grid) instead of per-card
   binding, so cards appended later by Load More work without needing
   their listeners re-registered. */
libraryGrid?.addEventListener('click', event => {
  const button = event.target.closest('.library-actions [data-action]');
  if (!button) return;
  const group = button.closest('.library-actions');
  if (!group) return;
  const action = button.dataset.action, id = group.dataset.id;
  if (action === 'like' || action === 'bookmark') { handleLikeOrBookmark(button, group); return; }
  if (action === 'export-pdf') { exportLibraryNotebookPdf(id, button); return; }
  if (action === 'export-json') { exportLibraryNotebookJson(id, button); return; }
});

/* Grid/List view toggle — shares the same persisted preference
   (users.notes_view_mode) as My Notebooks, saved via the same endpoint. */
const viewToggle = document.getElementById('viewToggle');
function applyViewMode(view) {
  libraryGrid?.classList.toggle('list-view', view === 'list');
  viewToggle?.querySelectorAll('button[data-view]').forEach(btn => {
    const active = btn.dataset.view === view;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
}
viewToggle?.addEventListener('click', async event => {
  const btn = event.target.closest('button[data-view]');
  if (!btn || btn.classList.contains('active')) return;
  const view = btn.dataset.view;
  const previous = viewToggle.querySelector('button.active')?.dataset.view || 'grid';
  applyViewMode(view);
  try {
    const res = await fetch('/api/v01/notebooks/view-mode', {
      method: 'PATCH', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ view_mode: view }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.success) throw new Error(data.message || 'Unable to save your view preference.');
  } catch (error) {
    applyViewMode(previous);
    toast(error.message || 'Unable to save your view preference.', 'error');
  }
});

let libraryLoadMoreBusy = false;
document.getElementById('libraryLoadMore')?.addEventListener('click', async () => {
  if (libraryLoadMoreBusy || !libraryGrid) return;
  libraryLoadMoreBusy = true;
  const btn = document.getElementById('libraryLoadMore');
  const originalHTML = btn.innerHTML;
  btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Loading...';
  try {
    const offset = Number(libraryGrid.dataset.offset || 0);
    const q = encodeURIComponent(libraryGrid.dataset.q || '');
    const res = await fetch(`/notes/library/load-more?offset=${offset}&q=${q}`, { credentials: 'same-origin' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.success) throw new Error(data.message || 'Unable to load more notebooks.');
    libraryGrid.insertAdjacentHTML('beforeend', data.html);
    libraryGrid.dataset.offset = String(offset + data.count);
    libraryGrid.dataset.hasMore = data.has_more ? 'true' : 'false';
    if (!data.has_more) {
      btn.hidden = true;
      document.getElementById('libraryLoadMoreEnd').hidden = false;
    }
  } catch (error) {
    toast(error.message || 'Unable to load more notebooks.', 'error');
  } finally {
    libraryLoadMoreBusy = false;
    btn.disabled = false; btn.innerHTML = originalHTML;
  }
});