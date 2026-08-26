import { exportNotebookAsPdf, exportNotebookAsJson } from './notebook-export.js';

const api = '/api/v01/notebooks';
let selectedNotebookId = null;

function toast(message, type = 'success') {
  const region = document.getElementById('notesToastRegion');
  if (!region) return;
  const item = document.createElement('div');
  item.className = `notes-toast ${type}`;
  item.innerHTML = `<i class="fas fa-${type === 'success' ? 'check-circle' : 'exclamation-circle'}"></i><span></span>`;
  item.querySelector('span').textContent = message;
  region.appendChild(item);
  window.setTimeout(() => item.remove(), 4500);
}

function openModal(id) { document.getElementById(id)?.classList.add('open'); }
function closeModal(id) { document.getElementById(id)?.classList.remove('open'); }
function message(id, text = '', success = false) { const el = document.getElementById(id); if (el) { el.textContent = text; el.classList.toggle('success', success); } }

async function request(url, options = {}) {
  const response = await fetch(url, { credentials: 'same-origin', headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }, ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.success) throw new Error(data.message || 'Something went wrong.');
  return data;
}

document.querySelectorAll('[data-open-modal]').forEach(button => button.addEventListener('click', () => openModal(button.dataset.openModal)));

/* Mirrors the Add Page modal's existing pageModalSaving guard: a modal whose
   action is currently in flight cannot be dismissed via its Cancel/X button
   or a backdrop click until that action finishes (success or failure). */
function isModalSaving(modalId) {
  return (modalId === 'createNotebookModal' && createNotebookSaving)
    || (modalId === 'editNotebookModal' && editNotebookSaving)
    || (modalId === 'importNotebookModal' && importNotebookSaving)
    || (modalId === 'visibilityNotebookModal' && visibilityNotebookSaving)
    || (modalId === 'deleteNotebookModal' && deleteNotebookSaving)
    || (modalId === 'shareNotebookModal' && shareSubmitSaving);
}
document.querySelectorAll('[data-close-modal]').forEach(button => button.addEventListener('click', () => { if (!isModalSaving(button.dataset.closeModal)) closeModal(button.dataset.closeModal); }));
document.querySelectorAll('.modal-bd').forEach(modal => modal.addEventListener('click', event => { if (event.target === modal && !isModalSaving(modal.id)) closeModal(modal.id); }));

let createNotebookSaving = false;
function setCreateNotebookSaving(isSaving) {
  const button = document.querySelector('#createNotebookForm button[type="submit"]');
  if (!button) return;
  button.disabled = isSaving;
  button.innerHTML = isSaving ? '<i class="fas fa-spinner fa-spin" aria-hidden="true"></i> Creating...' : '<i class="fas fa-plus"></i> Create notebook';
}
document.getElementById('createNotebookForm')?.addEventListener('submit', async event => {
  event.preventDefault(); if (createNotebookSaving) return;
  const form = event.currentTarget;
  message('createNotebookMessage'); createNotebookSaving = true; setCreateNotebookSaving(true);
  try {
    const data = await request(api, { method: 'POST', body: JSON.stringify(Object.fromEntries(new FormData(form))) });
    toast(`“${data.notebook.title}” was created.`); window.location.href = '/notes?section=created';
  } catch (error) { message('createNotebookMessage', error.message); }
  finally { createNotebookSaving = false; setCreateNotebookSaving(false); }
});

/* PDF/JSON export for a My Notebooks card — same shared implementation Public Notebooks uses
   (static/notes/notebook-export.js), just pointed at the owner read/export API instead of the
   public one; see that module for the real (non-fake) progress driver and download behavior. */
function exportMyNotebookPdf(notebookId, btn) {
  return exportNotebookAsPdf({
    notebookId, btn, toast,
    pagesUrl: id => `${api}/${id}/pages`,
    objectsUrl: (id, pageId) => `${api}/${id}/pages/${pageId}/objects`,
    exportPdfUrl: id => `${api}/${id}/export-pdf`,
  });
}
function exportMyNotebookJson(notebookId, btn) {
  return exportNotebookAsJson({
    notebookId, btn, toast,
    exportUrl: id => `${api}/${id}/export`,
  });
}

/* ── Shared notebook action menu ─────────────────────────────────────────
   ONE menu panel (in the DOM once, right after <main>) is reused by every
   card in every section/view instead of each card owning its own nested
   dropdown. A dropdown positioned via CSS inside a card only escapes that
   card's own box — it does NOT escape the normal stacking order of the
   card's LATER siblings, which (being equal/auto z-index, later in DOM)
   paint on top of it. That's the root cause of menus rendering "behind"
   the next row/card. Positioning this single menu with position:fixed and
   JS-computed viewport coordinates sidesteps sibling stacking entirely. */
const notesApp = document.getElementById('notesApp');
const actionMenu = document.getElementById('notebookActionMenu');
let openMenuContext = null;

function closeActionMenu() {
  if (!actionMenu || actionMenu.hidden) return;
  actionMenu.hidden = true;
  openMenuContext?.toggleBtn?.setAttribute('aria-expanded', 'false');
  openMenuContext = null;
}

function positionActionMenu(toggleBtn) {
  const margin = 8;
  actionMenu.style.visibility = 'hidden';
  actionMenu.hidden = false;
  actionMenu.style.top = '0px';
  actionMenu.style.left = '0px';
  const menuRect = actionMenu.getBoundingClientRect();
  const btnRect = toggleBtn.getBoundingClientRect();

  // Prefer opening below+right-aligned to the button; flip to above if
  // there isn't room below, flip to left-aligned if there isn't room right.
  let top = btnRect.bottom + 4;
  if (top + menuRect.height > window.innerHeight - margin) {
    const above = btnRect.top - menuRect.height - 4;
    top = above >= margin ? above : Math.max(margin, window.innerHeight - menuRect.height - margin);
  }
  let left = btnRect.right - menuRect.width;
  if (left < margin) left = btnRect.left;
  if (left + menuRect.width > window.innerWidth - margin) left = window.innerWidth - menuRect.width - margin;
  left = Math.max(margin, left);

  actionMenu.style.top = `${top}px`;
  actionMenu.style.left = `${left}px`;
  actionMenu.style.visibility = 'visible';
}

notesApp?.addEventListener('click', event => {
  const toggle = event.target.closest('[data-menu-toggle]');
  if (!toggle) return;
  event.stopPropagation();
  const wasOpenForThisToggle = openMenuContext?.toggleBtn === toggle && !actionMenu.hidden;
  closeActionMenu();
  if (wasOpenForThisToggle) return;
  const card = toggle.closest('.notebook-card');
  if (!card) return;
  const pill = card.querySelector('.visibility-pill');
  const shared = card.dataset.shared === 'true';
  openMenuContext = {
    id: card.dataset.notebookId,
    title: card.querySelector('h2')?.textContent || '',
    description: card.dataset.description || '',
    visibility: pill?.textContent.trim().toLowerCase() || 'private',
    shared,
    toggleBtn: toggle,
  };
  actionMenu.querySelectorAll('[data-owner-only]').forEach(btn => { btn.hidden = shared; });
  actionMenu.querySelectorAll('[data-shared-only]').forEach(btn => { btn.hidden = !shared; });
  toggle.setAttribute('aria-expanded', 'true');
  positionActionMenu(toggle);
});

actionMenu?.addEventListener('click', event => {
  const actionBtn = event.target.closest('[data-action]');
  if (!actionBtn || !openMenuContext) return;
  const { id, title, description, visibility } = openMenuContext;
  selectedNotebookId = id;
  // Export runs in place on the menu button itself (spinner/percent text) —
  // keep the menu open so that's visible, matching the previous per-card
  // dropdown's behavior. Edit/Visibility/Delete open a modal, so close it.
  if (actionBtn.dataset.action === 'export-json') { exportMyNotebookJson(id, actionBtn); return; }
  if (actionBtn.dataset.action === 'export-pdf') { exportMyNotebookPdf(id, actionBtn); return; }
  if (actionBtn.dataset.action === 'edit') {
    document.getElementById('editNotebookTitle').value = title;
    document.getElementById('editNotebookDescription').value = description;
    message('editNotebookMessage'); openModal('editNotebookModal');
  }
  if (actionBtn.dataset.action === 'visibility') {
    document.getElementById('visibilityNotebookValue').value = visibility;
    message('visibilityNotebookMessage'); openModal('visibilityNotebookModal');
  }
  if (actionBtn.dataset.action === 'delete') { message('deleteNotebookMessage'); openModal('deleteNotebookModal'); }
  if (actionBtn.dataset.action === 'share') { openShareModal(id, title); }
  if (actionBtn.dataset.action === 'leave') { leaveNotebook(id); }
  closeActionMenu();
});

document.addEventListener('click', event => {
  if (actionMenu && !actionMenu.contains(event.target)) closeActionMenu();
});
document.addEventListener('keydown', event => { if (event.key === 'Escape') closeActionMenu(); });
window.addEventListener('scroll', () => closeActionMenu(), { capture: true, passive: true });
window.addEventListener('resize', () => closeActionMenu());

let editNotebookSaving = false;
function setEditNotebookSaving(isSaving) {
  const button = document.querySelector('#editNotebookForm button[type="submit"]');
  if (!button) return;
  button.disabled = isSaving;
  button.innerHTML = isSaving ? '<i class="fas fa-spinner fa-spin" aria-hidden="true"></i> Saving...' : 'Save changes';
}
document.getElementById('editNotebookForm')?.addEventListener('submit', async event => {
  event.preventDefault(); if (editNotebookSaving) return;
  const form = event.currentTarget;
  message('editNotebookMessage'); editNotebookSaving = true; setEditNotebookSaving(true);
  const data = new FormData(form);
  try {
    await request(`${api}/${selectedNotebookId}`, { method: 'PATCH', body: JSON.stringify({ title: data.get('title'), description: data.get('description') }) });
    toast('Notebook updated.'); window.location.reload();
  } catch (error) { message('editNotebookMessage', error.message); }
  finally { editNotebookSaving = false; setEditNotebookSaving(false); }
});

/* Notebook Import: real progress, not a fake timer.
   POST /notebooks/import returns a job_id immediately (the actual import runs in a background
   thread). We then poll GET /notebooks/import/status/<job_id> — the poll INTERVAL only controls
   how often we ask the server for its current real state; every percent/status value rendered
   below is set directly from that response, never incremented or interpolated on the client. */
let importNotebookSaving = false;
let importPollTimer = null;

function setImportNotebookSaving(isSaving) {
  const button = document.querySelector('#importNotebookForm button[type="submit"]');
  if (!button) return;
  button.disabled = isSaving;
  button.innerHTML = isSaving ? '<i class="fas fa-spinner fa-spin" aria-hidden="true"></i> Importing...' : '<i class="fas fa-file-import"></i> Import';
}

function stopImportPolling() {
  if (importPollTimer) { clearInterval(importPollTimer); importPollTimer = null; }
}

function resetImportProgressView() {
  stopImportPolling();
  const form = document.getElementById('importNotebookForm');
  const view = document.getElementById('importProgressView');
  if (form) form.hidden = false;
  if (view) view.hidden = true;
  const fill = document.getElementById('importProgressFill');
  if (fill) { fill.style.width = '0%'; fill.classList.remove('done', 'failed'); }
  const status = document.getElementById('importProgressStatus');
  if (status) status.textContent = 'Preparing notebook...';
  const pct = document.getElementById('importProgressPct');
  if (pct) pct.textContent = '0%';
  const err = document.getElementById('importProgressError');
  if (err) { err.hidden = true; err.textContent = ''; }
  const footer = document.getElementById('importProgressFooter');
  if (footer) footer.innerHTML = '';
}

function renderImportProgress(job) {
  const percent = Math.max(0, Math.min(100, Math.round(Number(job.percent) || 0)));
  const fill = document.getElementById('importProgressFill');
  if (fill) fill.style.width = percent + '%';
  const pct = document.getElementById('importProgressPct');
  if (pct) pct.textContent = percent + '%';
  const status = document.getElementById('importProgressStatus');
  if (status) status.textContent = job.message || 'Importing...';
}

function importProgressFooterButton(label, cls, onClick) {
  const footer = document.getElementById('importProgressFooter');
  if (!footer) return;
  footer.innerHTML = '';
  const btn = document.createElement('button');
  btn.type = 'button'; btn.className = `sbtn ${cls}`; btn.innerHTML = label;
  btn.addEventListener('click', onClick);
  footer.appendChild(btn);
}

/* Polls until the job reaches a terminal state; resolves with the finished job, or rejects with
   an Error carrying whatever real percent had been reached when it failed. */
function pollImportJob(jobId) {
  return new Promise((resolve, reject) => {
    importPollTimer = setInterval(async () => {
      try {
        const res = await fetch(`${api}/import/status/${jobId}`, { credentials: 'same-origin' });
        const job = await res.json().catch(() => ({}));
        if (!res.ok || !job.success) { stopImportPolling(); reject(new Error(job.message || 'Unable to check import progress.')); return; }
        renderImportProgress(job);
        if (job.status === 'done') { stopImportPolling(); resolve(job); }
        else if (job.status === 'failed') { stopImportPolling(); reject(Object.assign(new Error(job.error || job.message || 'Import failed.'), { percent: job.percent })); }
      } catch (pollError) {
        stopImportPolling(); reject(pollError);
      }
    }, 500);
  });
}

document.querySelector('[data-open-modal="importNotebookModal"]')?.addEventListener('click', resetImportProgressView);

document.getElementById('importNotebookForm')?.addEventListener('submit', async event => {
  event.preventDefault(); if (importNotebookSaving) return;
  const fileInput = document.getElementById('importNotebookFile');
  const file = fileInput.files[0];
  message('importNotebookMessage');
  if (!file) { message('importNotebookMessage', 'Choose a Notebook JSON file to import.'); return; }

  let parsed;
  try {
    const text = await file.text();
    try { parsed = JSON.parse(text); }
    catch { throw new Error('Invalid Notebook file. Please select a Notebook exported from Smart AI Exam Portal.'); }
  } catch (error) {
    message('importNotebookMessage', error.message);
    return;
  }

  importNotebookSaving = true; setImportNotebookSaving(true);
  resetImportProgressView();
  document.getElementById('importNotebookForm').hidden = true;
  document.getElementById('importProgressView').hidden = false;

  try {
    const start = await request(`${api}/import`, { method: 'POST', body: JSON.stringify(parsed) });
    const job = await pollImportJob(start.job_id);
    const notebook = job.notebook;
    document.getElementById('importProgressFill')?.classList.add('done');
    document.getElementById('importProgressStatus').textContent = '✓ Notebook imported successfully';
    if (notebook) {
      importProgressFooterButton('<i class="fas fa-arrow-right"></i> Open Notebook', 'sbtn-primary', () => {
        window.location.href = `/notes/notebook/${notebook.id}`;
      });
    }
    toast(`“${notebook?.title || 'Notebook'}” was imported.`);
  } catch (error) {
    document.getElementById('importProgressFill')?.classList.add('failed');
    document.getElementById('importProgressStatus').textContent = 'Import failed';
    const errEl = document.getElementById('importProgressError');
    if (errEl) { errEl.hidden = false; errEl.textContent = error.message || 'Import failed.'; }
    importProgressFooterButton('Close', 'sbtn-ghost', () => closeModal('importNotebookModal'));
  } finally {
    importNotebookSaving = false; setImportNotebookSaving(false);
    fileInput.value = '';
  }
});

let visibilityNotebookSaving = false;
function setVisibilityNotebookSaving(isSaving) {
  const button = document.querySelector('#visibilityNotebookForm button[type="submit"]');
  if (!button) return;
  button.disabled = isSaving;
  button.innerHTML = isSaving ? '<i class="fas fa-spinner fa-spin" aria-hidden="true"></i> Updating...' : 'Update visibility';
}
document.getElementById('visibilityNotebookForm')?.addEventListener('submit', async event => {
  event.preventDefault(); if (visibilityNotebookSaving) return;
  const form = event.currentTarget;
  message('visibilityNotebookMessage'); visibilityNotebookSaving = true; setVisibilityNotebookSaving(true);
  try { await request(`${api}/${selectedNotebookId}`, { method: 'PATCH', body: JSON.stringify({ visibility: new FormData(form).get('visibility') }) }); toast('Notebook visibility updated.'); window.location.reload(); }
  catch (error) { message('visibilityNotebookMessage', error.message); }
  finally { visibilityNotebookSaving = false; setVisibilityNotebookSaving(false); }
});

let deleteNotebookSaving = false;
function setDeleteNotebookSaving(isSaving) {
  const button = document.getElementById('confirmDeleteNotebook');
  if (!button) return;
  button.disabled = isSaving;
  button.innerHTML = isSaving ? '<i class="fas fa-spinner fa-spin" aria-hidden="true"></i> Moving...' : '<i class="fas fa-trash-alt"></i> Move to Trash';
}
document.getElementById('confirmDeleteNotebook')?.addEventListener('click', async () => {
  if (deleteNotebookSaving) return;
  message('deleteNotebookMessage'); deleteNotebookSaving = true; setDeleteNotebookSaving(true);
  try {
    await request(`${api}/${selectedNotebookId}`, { method: 'DELETE' });
    toast('Notebook moved to Trash.');
    document.querySelector(`.notebook-card[data-notebook-id="${selectedNotebookId}"]`)?.remove();
    closeModal('deleteNotebookModal');
    if (!notebookGrid.children.length) loadSection(activeSection, { search: searchInput?.value.trim() || '' });
  }
  catch (error) { message('deleteNotebookMessage', error.message); }
  finally { deleteNotebookSaving = false; setDeleteNotebookSaving(false); }
});

/* ── Notebook library: tab-based sections, each lazily loaded ────────────
   Only the active tab's notebooks are ever fetched. Switching tabs, typing
   a search term, or clicking Load More all call the SAME /notes/section
   endpoint (offset=0 for a fresh load, offset=N to append) — no section is
   fetched until the user actually asks for it. */
const SECTION_ICONS = { created: 'book', imported: 'file-import', public: 'globe', shared_with_me: 'inbox', shared_by_me: 'share-alt' };
const SECTION_EMPTY = {
  created: ['No notebooks yet', 'Create your first notebook to start organizing notes, pages, drawings, and study material.'],
  imported: ['Nothing imported yet', 'Notebooks you import from a Notebook file will show up here.'],
  public: ['No public notebooks yet', 'Notebooks you set to Public will show up here.'],
  shared_with_me: ['Nothing shared with you yet', 'Notebooks other people share with you will show up here.'],
  shared_by_me: ['You haven’t shared any notebooks', 'Share a notebook with specific people from its "Share" menu action — it will show up here.'],
};

const notebookGrid = document.getElementById('notebookGrid');
const loadMoreWrap = document.getElementById('notebookLoadMoreWrap');
const loadMoreBtn = document.getElementById('notebookLoadMore');
const loadingEl = document.getElementById('notebookLoading');
const emptyEl = document.getElementById('notebookSectionEmpty');
const emptyIcon = document.getElementById('notebookSectionEmptyIcon');
const emptyTitle = document.getElementById('notebookSectionEmptyTitle');
const emptyText = document.getElementById('notebookSectionEmptyText');
const emptyAction = document.getElementById('notebookSectionEmptyAction');
const searchInput = document.getElementById('notebookSearch');
const viewToggle = document.getElementById('viewToggle');
let activeSection = notebookGrid?.dataset.section || 'created';
let sectionLoadToken = 0; // discards a response if the user switched tabs/searched again before it arrived

function currentViewMode() {
  return viewToggle?.querySelector('button.active')?.dataset.view === 'list' ? 'list' : 'grid';
}

async function loadSection(section, { offset = 0, append = false, search = '' } = {}) {
  if (!notebookGrid) return;
  const token = ++sectionLoadToken;
  if (!append) {
    loadingEl.hidden = false;
    notebookGrid.hidden = true;
    loadMoreWrap.hidden = true;
    emptyEl.hidden = true;
  } else {
    loadMoreBtn.disabled = true;
    loadMoreBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Loading...';
  }
  try {
    const data = await request(`/notes/section?section=${encodeURIComponent(section)}&offset=${offset}&q=${encodeURIComponent(search)}`, { method: 'GET' });
    if (token !== sectionLoadToken) return;
    if (append) notebookGrid.insertAdjacentHTML('beforeend', data.html);
    else { notebookGrid.innerHTML = data.html; notebookGrid.dataset.section = section; }
    const newOffset = offset + data.count;
    notebookGrid.dataset.offset = String(newOffset);
    notebookGrid.dataset.hasMore = data.has_more ? 'true' : 'false';
    notebookGrid.classList.toggle('list-view', currentViewMode() === 'list');

    const isEmpty = notebookGrid.children.length === 0;
    notebookGrid.hidden = isEmpty;
    loadMoreWrap.hidden = !data.has_more;
    emptyEl.hidden = !isEmpty;
    if (isEmpty) {
      const [title, text] = SECTION_EMPTY[section] || ['Nothing here yet', ''];
      emptyIcon.className = `fas fa-${SECTION_ICONS[section] || 'book'}`;
      emptyTitle.textContent = search ? 'No matches' : title;
      emptyText.textContent = search ? `No notebooks match “${search}” in this section.` : text;
      emptyAction.hidden = section !== 'created' || !!search;
    }
  } catch (error) {
    toast(error.message || 'Unable to load notebooks.', 'error');
  } finally {
    if (token === sectionLoadToken) {
      loadingEl.hidden = true;
      loadMoreBtn.disabled = false; loadMoreBtn.innerHTML = '<i class="fas fa-chevron-down"></i> Load more';
    }
  }
}

document.getElementById('notebookTabs')?.addEventListener('click', event => {
  const tab = event.target.closest('.notebook-tab');
  if (!tab || tab.classList.contains('active')) return;
  document.querySelectorAll('.notebook-tab').forEach(btn => {
    const active = btn === tab;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  activeSection = tab.dataset.section;
  if (searchInput) searchInput.value = '';
  window.history.replaceState(null, '', `/notes?section=${activeSection}`);
  loadSection(activeSection);
});

loadMoreBtn?.addEventListener('click', () => {
  const offset = Number(notebookGrid.dataset.offset || 0);
  loadSection(activeSection, { offset, append: true, search: searchInput?.value.trim() || '' });
});

let searchDebounce = null;
searchInput?.addEventListener('input', event => {
  clearTimeout(searchDebounce);
  const term = event.currentTarget.value.trim();
  searchDebounce = setTimeout(() => loadSection(activeSection, { search: term }), 300);
});

/* Grid/List view toggle — persisted server-side (users.notes_view_mode) so
   it survives logout/login, not just kept in local/session storage.
   Optimistic: the UI switches immediately, and only reverts if the save
   request actually fails. Applies to whichever section is currently shown. */
function applyViewMode(view) {
  notebookGrid?.classList.toggle('list-view', view === 'list');
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
  const previous = currentViewMode();
  applyViewMode(view);
  try {
    await request('/api/v01/notebooks/view-mode', { method: 'PATCH', body: JSON.stringify({ view_mode: view }) });
  } catch (error) {
    applyViewMode(previous);
    toast(error.message || 'Unable to save your view preference.', 'error');
  }
});

/* ── Leave a shared notebook (Shared with Me cards only) ──────────────── */
async function leaveNotebook(id) {
  try {
    await request(`${api}/${id}/leave`, { method: 'POST' });
    toast('Removed from My Notebooks.');
    document.querySelector(`.notebook-card[data-notebook-id="${id}"]`)?.remove();
    if (!notebookGrid.children.length) loadSection(activeSection, { search: searchInput?.value.trim() || '' });
  } catch (error) {
    toast(error.message || 'Unable to leave this notebook.', 'error');
  }
}

/* ── Sharing dialog ────────────────────────────────────────────────────
   Search box + "Adding" list (submitted together as one POST) + a live
   "Currently has access" list where each row's permission PATCHes and
   removal DELETEs immediately — see /notebooks/<id>/shares* in
   app/routes/api/v01/notebooks.py. */
const shareEls = {
  title: document.getElementById('shareNotebookTitle'),
  search: document.getElementById('shareUserSearch'),
  results: document.getElementById('shareSearchResults'),
  pendingSection: document.getElementById('sharePendingSection'),
  pendingList: document.getElementById('sharePendingList'),
  currentSection: document.getElementById('shareCurrentSection'),
  currentList: document.getElementById('shareCurrentList'),
  submitBtn: document.getElementById('shareSubmitBtn'),
};
let shareNotebookId = null;
let sharePending = new Map(); // user_id -> { id, name, permission }
let shareSubmitSaving = false;

function renderShareRow(list, { id, name, permission }, { onPermissionChange, onRemove }) {
  const row = document.createElement('div');
  row.className = 'share-row';
  row.dataset.userId = id;
  const nameEl = document.createElement('span');
  nameEl.className = 'share-row-name';
  nameEl.textContent = name;
  const select = document.createElement('select');
  select.setAttribute('aria-label', `Permission for ${name}`);
  [['viewer', 'Viewer'], ['editor', 'Editor']].forEach(([value, label]) => {
    const option = document.createElement('option');
    option.value = value; option.textContent = label; option.selected = value === permission;
    select.appendChild(option);
  });
  select.addEventListener('change', () => onPermissionChange(select.value));
  const removeBtn = document.createElement('button');
  removeBtn.type = 'button'; removeBtn.className = 'share-row-remove'; removeBtn.setAttribute('aria-label', `Remove ${name}`);
  removeBtn.innerHTML = '<i class="fas fa-times"></i>';
  removeBtn.addEventListener('click', onRemove);
  row.append(nameEl, select, removeBtn);
  list.appendChild(row);
}

function renderSharePending() {
  shareEls.pendingList.innerHTML = '';
  sharePending.forEach((entry, userId) => {
    renderShareRow(shareEls.pendingList, entry, {
      onPermissionChange: value => { entry.permission = value; },
      onRemove: () => { sharePending.delete(userId); renderSharePending(); },
    });
  });
  shareEls.pendingSection.hidden = sharePending.size === 0;
}

async function loadShareCurrentList() {
  shareEls.currentList.innerHTML = '';
  shareEls.currentSection.hidden = true;
  try {
    const data = await request(`${api}/${shareNotebookId}/shares`, { method: 'GET' });
    shareEls.currentSection.hidden = data.shares.length === 0;
    data.shares.forEach(share => {
      renderShareRow(shareEls.currentList, { id: share.user_id, name: share.full_name || share.username, permission: share.permission }, {
        onPermissionChange: async value => {
          try {
            await request(`${api}/${shareNotebookId}/shares/${share.user_id}`, { method: 'PATCH', body: JSON.stringify({ permission: value }) });
            toast('Permission updated.');
          } catch (error) { toast(error.message || 'Unable to update permission.', 'error'); }
        },
        onRemove: async () => {
          try {
            await request(`${api}/${shareNotebookId}/shares/${share.user_id}`, { method: 'DELETE' });
            shareEls.currentList.querySelector(`.share-row[data-user-id="${share.user_id}"]`)?.remove();
            if (!shareEls.currentList.children.length) shareEls.currentSection.hidden = true;
            toast('Access removed.');
          } catch (error) { toast(error.message || 'Unable to remove access.', 'error'); }
        },
      });
    });
  } catch (error) {
    message('shareNotebookMessage', error.message);
  }
}

function openShareModal(notebookId, title) {
  shareNotebookId = notebookId;
  sharePending = new Map();
  shareEls.title.textContent = title;
  if (shareEls.search) shareEls.search.value = '';
  shareEls.results.hidden = true; shareEls.results.innerHTML = '';
  message('shareNotebookMessage');
  renderSharePending();
  loadShareCurrentList();
  openModal('shareNotebookModal');
}

let shareSearchDebounce = null;
shareEls.search?.addEventListener('input', event => {
  clearTimeout(shareSearchDebounce);
  const term = event.currentTarget.value.trim();
  if (term.length < 2) { shareEls.results.hidden = true; shareEls.results.innerHTML = ''; return; }
  shareSearchDebounce = setTimeout(async () => {
    try {
      const data = await request(`${api}/${shareNotebookId}/share-search?q=${encodeURIComponent(term)}`, { method: 'GET' });
      shareEls.results.innerHTML = '';
      const candidates = data.users.filter(u => !sharePending.has(u.id));
      if (!candidates.length) {
        const empty = document.createElement('div'); empty.className = 'share-search-empty'; empty.textContent = 'No matching users.';
        shareEls.results.appendChild(empty);
      } else {
        candidates.forEach(u => {
          const btn = document.createElement('button');
          btn.type = 'button'; btn.className = 'share-search-result';
          btn.textContent = u.full_name || u.username;
          btn.addEventListener('click', () => {
            sharePending.set(u.id, { id: u.id, name: u.full_name || u.username, permission: 'viewer' });
            renderSharePending();
            shareEls.results.hidden = true;
            if (shareEls.search) shareEls.search.value = '';
          });
          shareEls.results.appendChild(btn);
        });
      }
      shareEls.results.hidden = false;
    } catch (error) {
      shareEls.results.hidden = true;
    }
  }, 300);
});
document.addEventListener('click', event => {
  if (shareEls.results && !shareEls.results.hidden && !shareEls.results.contains(event.target) && event.target !== shareEls.search) {
    shareEls.results.hidden = true;
  }
});

shareEls.submitBtn?.addEventListener('click', async () => {
  if (shareSubmitSaving || !sharePending.size) return;
  shareSubmitSaving = true;
  const original = shareEls.submitBtn.innerHTML;
  shareEls.submitBtn.disabled = true; shareEls.submitBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Sharing...';
  message('shareNotebookMessage');
  try {
    const shares = Array.from(sharePending.values()).map(({ id, permission }) => ({ user_id: id, permission }));
    await request(`${api}/${shareNotebookId}/shares`, { method: 'POST', body: JSON.stringify({ shares }) });
    toast('Notebook shared.');
    sharePending = new Map();
    renderSharePending();
    await loadShareCurrentList();
  } catch (error) {
    message('shareNotebookMessage', error.message);
  } finally {
    shareSubmitSaving = false;
    shareEls.submitBtn.disabled = false; shareEls.submitBtn.innerHTML = original;
  }
});
