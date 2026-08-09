const api = '/api/notes/notebooks';
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
    || (modalId === 'deleteNotebookModal' && deleteNotebookSaving);
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
    toast(`“${data.notebook.title}” was created.`); window.location.reload();
  } catch (error) { message('createNotebookMessage', error.message); }
  finally { createNotebookSaving = false; setCreateNotebookSaving(false); }
});

document.querySelectorAll('[data-menu-toggle]').forEach(button => button.addEventListener('click', event => {
  event.stopPropagation(); document.querySelectorAll('[data-menu]').forEach(menu => { if (menu !== button.nextElementSibling) menu.classList.remove('open'); }); button.nextElementSibling?.classList.toggle('open');
}));
document.addEventListener('click', () => document.querySelectorAll('[data-menu]').forEach(menu => menu.classList.remove('open')));

document.querySelectorAll('.notebook-card').forEach(card => card.querySelectorAll('[data-action]').forEach(button => button.addEventListener('click', () => {
  selectedNotebookId = card.dataset.notebookId; const title = card.querySelector('h2')?.textContent || '';
  if (button.dataset.action === 'export') { window.location.href = `${api}/${selectedNotebookId}/export`; return; }
  if (button.dataset.action === 'edit') {
    document.getElementById('editNotebookTitle').value = title;
    document.getElementById('editNotebookDescription').value = card.dataset.description || '';
    message('editNotebookMessage'); openModal('editNotebookModal');
  }
  if (button.dataset.action === 'visibility') { const pill = card.querySelector('.visibility-pill'); document.getElementById('visibilityNotebookValue').value = pill?.textContent.trim().toLowerCase() || 'private'; message('visibilityNotebookMessage'); openModal('visibilityNotebookModal'); }
  if (button.dataset.action === 'delete') { message('deleteNotebookMessage'); openModal('deleteNotebookModal'); }
})));

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

let importNotebookSaving = false;
function setImportNotebookSaving(isSaving) {
  const button = document.querySelector('#importNotebookForm button[type="submit"]');
  if (!button) return;
  button.disabled = isSaving;
  button.innerHTML = isSaving ? '<i class="fas fa-spinner fa-spin" aria-hidden="true"></i> Importing...' : '<i class="fas fa-file-import"></i> Import';
}
document.getElementById('importNotebookForm')?.addEventListener('submit', async event => {
  event.preventDefault(); if (importNotebookSaving) return;
  const fileInput = document.getElementById('importNotebookFile');
  const file = fileInput.files[0];
  message('importNotebookMessage');
  if (!file) { message('importNotebookMessage', 'Choose a Notebook JSON file to import.'); return; }
  importNotebookSaving = true; setImportNotebookSaving(true);
  try {
    const text = await file.text();
    let parsed;
    try { parsed = JSON.parse(text); }
    catch { throw new Error('Invalid Notebook file. Please select a Notebook exported from Smart AI Exam Portal.'); }
    const data = await request(`${api}/import`, { method: 'POST', body: JSON.stringify(parsed) });
    toast(`“${data.notebook.title}” was imported.`);
    window.location.reload();
  } catch (error) {
    message('importNotebookMessage', error.message);
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
  try { await request(`${api}/${selectedNotebookId}`, { method: 'DELETE' }); toast('Notebook moved to Trash.'); window.location.reload(); }
  catch (error) { message('deleteNotebookMessage', error.message); }
  finally { deleteNotebookSaving = false; setDeleteNotebookSaving(false); }
});

document.getElementById('notebookSearch')?.addEventListener('input', event => {
  const query = event.currentTarget.value.trim().toLowerCase(); let shown = 0;
  document.querySelectorAll('.notebook-card').forEach(card => { const match = !query || card.dataset.search.includes(query); card.hidden = !match; if (match) shown += 1; });
  const count = document.getElementById('notebookCount'); if (count) count.textContent = `${shown} notebook${shown === 1 ? '' : 's'}`;
});