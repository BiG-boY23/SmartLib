/* Reversible staff archive and queue downloads. */
(() => {
  let keys = new Map();
  const views = {};
  const reloaders = {};
  let exportBarSequence=0;
  let superAdmin=false;
  let profileRequest=null;
  async function api(path, options={}) {
    const r = await fetch('/admin' + path, {...options, headers:getAuthHeaders()});
    if (!r.ok) { const d=await r.json().catch(()=>({})); throw Error(typeof d.detail === 'string' ? d.detail : 'Request failed'); }
    return r;
  }
  async function refresh() {
    if(!profileRequest) profileRequest=(async()=>{
      const response=await fetch('/auth/me',{headers:getAuthHeaders()});
      if(!response.ok)throw Error('Please sign in again to load library records.');
      const user=await response.json();superAdmin=String(user.role).toLowerCase()==='superadmin';
      const shelf=document.getElementById('archivedShelfBody')?.closest('.card');
      if(shelf)shelf.style.display=superAdmin?'':'none';
    })().catch(error=>{profileRequest=null;throw error;});
    await profileRequest;
    keys=superAdmin?new Map((await (await api('/archives')).json()).map(a=>[a.key,a])):new Map();
    if(!superAdmin)Object.keys(views).forEach(kind=>views[kind]='active');
  }
  function viewUrl(path,kind) { return superAdmin && views[kind]==='archived' ? path+(path.includes('?')?'&':'?')+'archive_view=true' : path; }
  async function toggle(kind,id,reload) {
    const archived=keys.has(`${kind}:${id}`);
    const reason=archived ? '' : prompt('Reason for archiving (retained in the staff audit history):');
    if (!archived && reason === null) return;
    if (!archived && (reason.trim().length < 3 || reason.trim().length > 500)) {
      toast('Enter an archive reason between 3 and 500 characters.','warning');return;
    }
    try {
      await api(`/archives/${kind}/${encodeURIComponent(id)}?`+new URLSearchParams({archived:!archived,reason} ),{method:'PUT'});
      await refresh();
      if(reload) await reload();
      else if(kind==='acquisitions' && typeof loadAcquisitions==='function') await loadAcquisitions();
      else if(reloaders[kind]) await reloaders[kind]();
      else if(kind==='announcements') await SmartLibUpdates.load();
      else if(kind==='messages') await SmartLibChat.reload();
    } catch(e) { toast(e.message,'danger'); }
  }
  function control(kind,parent,reload) {
    reloaders[kind]=reload;
    if(!superAdmin)return;
    if(parent.querySelector(`[data-archive-view="${kind}"]`)) return;
    const label=document.createElement('label');label.style.cssText='display:block;padding:12px';label.textContent='Records: ';
    const select=document.createElement('select');
    if(!document.getElementById('archiveView-'+kind))select.id='archiveView-'+kind;
    select.dataset.archiveView=kind;select.className='form-select';select.style.cssText='display:inline-block;width:auto;margin-left:6px';
    select.innerHTML='<option value="active">Active</option><option value="archived">Archived</option>';
    select.value=views[kind]||'active';
    select.onchange=()=>{views[kind]=select.value;document.querySelectorAll(`[data-archive-view="${kind}"]`).forEach(s=>s.value=select.value);reload();};label.append(select);parent.prepend(label);
  }
  function visible(kind,id) { return keys.has(`${kind}:${id}`) === (views[kind]==='archived'); }
  function button(kind,id,reload) {
    const b=document.createElement('button');b.type='button';b.className='btn btn-outline btn-sm';
    const entry=keys.get(`${kind}:${id}`);b.textContent=entry?'Restore':'Archive';b.title=entry?entry.reason:'Archive with a reason';
    b.onclick=async()=>{b.disabled=true;try{await toggle(kind,id,reload);}finally{b.disabled=false;}};return b;
  }
  function exports(kind,parent) {
    if(parent.querySelector(`[data-queue-exports="${kind}"]`))return;
    const bar=document.createElement('div');bar.style.cssText='display:flex;gap:8px;padding:12px;flex-wrap:wrap';
    bar.dataset.queueExports=kind;
    const paperLabel=document.createElement('label');paperLabel.textContent='PDF / Word paper: ';paperLabel.style.cssText='display:flex;align-items:center;gap:8px;margin:0';
    const paper=document.createElement('select');paper.id=`queueExportPaper-${kind}-${++exportBarSequence}`;paper.dataset.exportPaper=kind;paper.className='form-select';paper.style.width='auto';
    paper.innerHTML='<option value="long">Long bond (8.5 × 13 in)</option><option value="a4">A4</option>';paper.value='long';
    paperLabel.htmlFor=paper.id;paperLabel.append(paper);bar.append(paperLabel);
    for(const format of ['xlsx','csv','pdf','docx']) {
      const b=document.createElement('button');b.type='button';b.className='btn btn-outline btn-sm';b.textContent='Export '+({xlsx:'Excel (.xlsx)',csv:'CSV',pdf:'PDF',docx:'Word'}[format]);
      b.onclick=async()=>{b.disabled=true;try {
        const p=new URLSearchParams({kind,format,archived:views[kind]==='archived'});
        if(format==='pdf'||format==='docx')p.set('paper',paper.value);
        if(kind==='borrow-requests') {p.set('status',document.getElementById('queueStatus')?.value||'');p.set('search',document.getElementById('queueSearch')?.value||'');}
        const r=await api('/reports/queue?'+p);const url=URL.createObjectURL(await r.blob());
        const a=document.createElement('a');a.href=url;a.download=`SmartLib-${kind}.${format}`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      } catch(e){toast(e.message,'danger');}finally{b.disabled=false;}};bar.append(b);
    }
    const note=document.createElement('p');note.className='report-note';note.style.cssText='flex-basis:100%;margin:0;font-size:12px;color:var(--text-muted)';
    note.textContent='A4 scales the full letterhead to fit; long bond preserves original size. For wrapped text and fitted rows, choose Excel. CSV has no cell formatting.';bar.append(note);
    parent.prepend(bar);
  }
  async function mountAcquisitions(parent,books,reload) {
    await refresh();
    parent.replaceChildren();
    const wrapper=document.createElement('div');wrapper.className='table-responsive';
    const table=document.createElement('table');
    table.innerHTML='<thead><tr><th>Reference</th><th>Requested by</th><th>Title / author</th><th>Justification</th><th>Date</th><th>Status</th><th>Actions</th></tr></thead>';
    const body=document.createElement('tbody');
    const records=books.filter(b=>visible('acquisitions',b.acq_id));
    for(const book of records) {
      const row=document.createElement('tr');
      for(const value of [book.acq_id,book.requester_name||'Patron',`${book.title}\n${book.author||''}`,book.justification,String(book.created_at||'').slice(0,10),book.status]) {
        const cell=document.createElement('td');cell.textContent=value||'';cell.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere;max-width:350px';row.append(cell);
      }
      const actions=document.createElement('td');
      if(book.status==='Under Review' && views.acquisitions!=='archived') {
        for(const [label,status] of [['Approve','Approved for Purchase'],['Decline','Declined']]) {
          const review=document.createElement('button');review.type='button';review.className='btn btn-outline btn-sm';review.textContent=label;
          review.onclick=async()=>{
            const reason=status==='Declined'?prompt('Reason for declining this suggestion:'):'';
            if(status==='Declined' && reason===null)return;
            if(status==='Declined' && (reason.trim().length<3 || reason.trim().length>500)) {toast('Enter a decline reason between 3 and 500 characters.','warning');return;}
            actions.querySelectorAll('button').forEach(b=>b.disabled=true);
            try {await api(`/acquisitions/${encodeURIComponent(book.acq_id)}/status`,{method:'PUT',body:JSON.stringify({status,reason})});toast('Suggestion reviewed. Reader notifications queued.','success');await reload();}
            catch(error){toast(error.message,'danger');actions.querySelectorAll('button').forEach(b=>b.disabled=false);}
          };actions.append(review,' ');
        }
      }
      actions.append(button('acquisitions',book.acq_id,reload));row.append(actions);body.append(row);
    }
    if(!records.length){const row=body.insertRow();const cell=row.insertCell();cell.colSpan=7;cell.textContent='No suggestions in this view.';}
    table.append(body);wrapper.append(table);parent.append(wrapper);
    control('acquisitions',parent,reload);exports('acquisitions',parent);
  }
  window.StaffRecords={refresh,control,visible,button,mountAcquisitions,viewUrl,isSuperAdmin:()=>superAdmin,isArchivedView:kind=>superAdmin&&views[kind]==='archived'};
  document.addEventListener('DOMContentLoaded',async()=>{
    try{await refresh();}catch(error){toast(error.message,'danger');return;}
    const table=document.getElementById('acquisitionsTable');
    if(table){control('acquisitions',table.parentElement,()=>loadAcquisitions());exports('acquisitions',table.parentElement);}
    const queue=document.getElementById('queueStatus');if(queue)exports('borrow-requests',queue.closest('.card')||queue.parentElement.parentElement);
  });
})();
