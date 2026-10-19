function toast(message, type = 'success') { window.Notify.show({ type, message }); }
/* Restore asks first, like Delete permanently does: the button only opens the dialog, the dialog's Restore button makes the request. */
let restoreId = null, restoreSaving = false;
const restoreModal = document.getElementById('restoreModal');
function setRestoreSaving(isSaving) {
  const button = document.getElementById('confirmRestore');
  if (!button) return;
  button.disabled = isSaving;
  button.innerHTML = isSaving ? '<i class="fas fa-spinner fa-spin" aria-hidden="true"></i> Restoring...' : '<i class="fas fa-undo"></i> Restore';
}
function closeRestore() { if (!restoreSaving) restoreModal?.classList.remove('open'); }
document.querySelectorAll('[data-restore-notebook]').forEach(button => button.addEventListener('click', () => {
  const card = button.closest('[data-notebook-id]');
  restoreId = card.dataset.notebookId;
  document.getElementById('restoreNotebookName').textContent = '\u201c' + (card.querySelector('h2')?.textContent || 'this notebook').trim() + '\u201d';
  document.getElementById('restoreMessage').textContent = '';
  restoreModal.classList.add('open');
  document.getElementById('confirmRestore').focus();
}));
document.querySelectorAll('[data-close-restore]').forEach(b => b.addEventListener('click', closeRestore));
restoreModal?.addEventListener('click', event => { if (event.target === restoreModal) closeRestore(); });
document.addEventListener('keydown', event => { if (event.key === 'Escape' && restoreModal?.classList.contains('open')) closeRestore(); });
document.getElementById('confirmRestore')?.addEventListener('click', async () => {
  if (restoreSaving || !restoreId) return;
  const id = restoreId;
  document.getElementById('restoreMessage').textContent = ''; restoreSaving = true; setRestoreSaving(true);
  try {
    const response = await fetch(`/api/v01/notebooks/${id}/restore`, { method: 'POST', credentials: 'same-origin' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.success) throw new Error(data.message || 'Unable to restore notebook.');
    restoreSaving = false; restoreModal.classList.remove('open');
    toast('Notebook restored.');
    document.querySelector(`[data-notebook-id="${id}"]`)?.remove();
    if (!document.querySelector('[data-notebook-id]')) window.location.reload();
  } catch (error) {
    document.getElementById('restoreMessage').textContent = error.message;
  } finally { restoreSaving = false; setRestoreSaving(false); }
});
let selectedId=null;document.querySelectorAll('[data-permanent-notebook]').forEach(button=>button.addEventListener('click',()=>{selectedId=button.closest('[data-notebook-id]').dataset.notebookId;document.getElementById('permanentDeleteMessage').textContent='';document.getElementById('permanentDeleteModal').classList.add('open')}));let permanentDeleteSaving=false;function setPermanentDeleteSaving(isSaving){const button=document.getElementById('confirmPermanentDelete');if(!button)return;button.disabled=isSaving;button.innerHTML=isSaving?'<i class="fas fa-spinner fa-spin" aria-hidden="true"></i> Deleting...':'<i class="fas fa-trash-alt"></i> Delete permanently';}
document.querySelectorAll('[data-close-permanent]').forEach(b=>b.addEventListener('click',()=>{if(!permanentDeleteSaving)document.getElementById('permanentDeleteModal').classList.remove('open')}));document.getElementById('confirmPermanentDelete')?.addEventListener('click',async()=>{if(permanentDeleteSaving)return;document.getElementById('permanentDeleteMessage').textContent='';permanentDeleteSaving=true;setPermanentDeleteSaving(true);try{const r=await fetch(`/api/v01/notebooks/${selectedId}/permanent`,{method:'DELETE',credentials:'same-origin'}),d=await r.json();if(!r.ok||!d.success)throw Error(d.message||'Unable to permanently delete notebook.');document.querySelector(`[data-notebook-id="${selectedId}"]`)?.remove();document.getElementById('permanentDeleteModal').classList.remove('open');toast('Notebook permanently deleted.');if(!document.querySelector('[data-notebook-id]'))location.reload()}catch(err){document.getElementById('permanentDeleteMessage').textContent=err.message}finally{permanentDeleteSaving=false;setPermanentDeleteSaving(false)}});