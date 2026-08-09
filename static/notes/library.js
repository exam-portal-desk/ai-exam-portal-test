const toast=(m,t='success')=>{const r=document.getElementById('notesToastRegion'),e=document.createElement('div');e.className=`notes-toast ${t}`;e.textContent=m;r.append(e);setTimeout(()=>e.remove(),4000)};
document.querySelectorAll('.library-actions').forEach(group=>group.querySelectorAll('button[data-action]').forEach(button=>button.addEventListener('click',async()=>{
  const a=button.dataset.action,id=group.dataset.id;button.disabled=true;
  try{
    const r=await fetch(`/api/notes/library/${id}/${a}`,{method:'POST',credentials:'same-origin'}),d=await r.json();
    if(!r.ok||!d.success)throw Error(d.message||'Unable to complete action.');
    button.querySelector('span').textContent=d[a==='like'?'likes':'bookmarks'];
    button.classList.toggle('active',d.active);
    button.querySelector('i').className=`${d.active?'fas':'far'} fa-${a==='like'?'heart':'bookmark'}`;
    toast(d.active?'Saved.':'Removed.');
  }catch(e){toast(e.message,'error')}
  finally{button.disabled=false}
})));