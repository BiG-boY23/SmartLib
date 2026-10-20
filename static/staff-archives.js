/* Retained records are served only by the SuperAdmin archive API. */
(() => {
  let panel, list, status, query, kind, previous, next, dialog, detail, skip=0, total=0, version=0, detailVersion=0;
  const limit=50;
  const label=value=>String(value).replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
  const date=value=>value?new Date(/(?:Z|[+-]\d\d:\d\d)$/.test(value)?value:value+'Z').toLocaleString():'';
  const element=(tag,text,className)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;};
  async function request(path) {
    const response=await fetch('/admin/deleted-archives'+path,{headers:getAuthHeaders()});
    const data=await response.json();
    if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'The archive could not be loaded.');
    return data;
  }
  function content(value,key='') {
    if(value===null||value===undefined)return element('span','Not recorded','archive-muted');
    if(Array.isArray(value)) {
      const group=element('div',undefined,'archive-items');
      if(!value.length)group.append(element('p','No records.','archive-muted'));
      value.forEach((item,index)=>{const box=element('section',undefined,'archive-item');box.append(element('h4',`Record ${index+1}`),content(item));group.append(box);});
      return group;
    }
    if(typeof value==='object') {
      const group=element('dl',undefined,'archive-facts');
      Object.entries(value).forEach(([name,item])=>{
        if(/password|token|secret|session|otp/i.test(name))return;
        const term=element('dt',label(name)), description=element('dd');description.append(content(item,name));group.append(term,description);
      });return group;
    }
    const text=typeof value==='boolean'?(value?'Yes':'No'):/(_at|date)$/.test(key)&&/^\d{4}-\d{2}-\d{2}T/.test(String(value))?date(value):String(value);
    return element('span',text,'archive-value');
  }
  async function openRecord(id) {
    const current=++detailVersion;
    detail.replaceChildren(element('p','Loading retained record…'));
    dialog.showModal();
    try {
      const record=await request('/'+encodeURIComponent(id));
      if(current!==detailVersion)return;
      detail.replaceChildren(element('h3',record.title),content({type:record.kind,action:record.action,removed_by:record.deleted_by,removed_at:record.deleted_at,source:record.origin}),element('h4','Retained content'),content(record.content));
    }catch(error){if(current===detailVersion)detail.replaceChildren(element('p',error.message));}
  }
  async function load() {
    const current=++version;
    status.textContent='Loading retained records…';list.replaceChildren();
    try {
      const result=await request('?'+new URLSearchParams({q:query.value.trim(),kind:kind.value,skip,limit}));
      if(current!==version)return;
      total=result.total;
      if(skip>=total&&skip>0){skip=Math.max(0,Math.floor((total-1)/limit)*limit);return load();}
      const table=element('table'),head=element('thead'),heading=element('tr'),body=element('tbody');
      ['Removed on','Type','Record','Removed by','Review'].forEach(text=>heading.append(element('th',text)));head.append(heading);table.append(head,body);
      for(const record of result.items) {
        const row=element('tr'),identity=element('td');identity.append(element('strong',record.title),element('small',`Reference: ${record.record_id}`,'archive-muted'));
        const action=element('td'),button=element('button','View record','btn btn-outline btn-sm');button.type='button';button.onclick=()=>openRecord(record.id);action.append(button);
        row.append(element('td',date(record.deleted_at)),element('td',label(record.kind)),identity,element('td',record.deleted_by),action);body.append(row);
      }
      if(result.items.length)list.append(table);else list.append(element('p','No retained records match these filters.','archive-muted'));
      status.textContent=total?`${skip+1}–${Math.min(skip+limit,total)} of ${total} retained records.`:'No retained records.';
      previous.disabled=skip===0;next.disabled=skip+limit>=total;
    }catch(error){if(current===version){status.textContent=error.message;previous.disabled=true;next.disabled=true;}}
  }
  function open() {
    document.querySelectorAll('.screen-panel,.nav-item-btn').forEach(node=>node.classList.remove('active'));
    panel.classList.add('active');document.getElementById('navRetainedArchive').classList.add('active');
    document.getElementById('pageTitle').textContent='Retained archive';
    document.getElementById('pageSubTitle').textContent='SuperAdmin access · Removed records and conversation history';
    load();
  }
  async function setup() {
    try{await StaffRecords.refresh();if(!StaffRecords.isSuperAdmin())return;}catch{return;}
    const menu=document.querySelector('.sidebar-menu'),container=document.querySelector('.content-container');if(!menu||!container)return;
    const nav=element('button','Retained archive','nav-item-btn');nav.id='navRetainedArchive';nav.type='button';nav.onclick=open;menu.append(nav);
    panel=element('div',undefined,'screen-panel');panel.id='panelRetainedArchive';
    const card=element('section',undefined,'card archive-card');
    const form=element('form',undefined,'archive-filters');
    const searchLabel=element('label','Search records');query=element('input');query.type='search';query.maxLength=200;query.placeholder='Title, reference, or removed by';query.className='form-control';searchLabel.append(query);
    const typeLabel=element('label','Record type');kind=element('select');kind.className='form-select';
    [['','All types'],['messages','Messages'],['conversations','Conversations'],['books','Books'],['acquisitions','Acquisition suggestions'],['announcements','Announcements'],['signup-options','Signup options']].forEach(([value,text])=>{const option=element('option',text);option.value=value;kind.append(option);});typeLabel.append(kind);
    const search=element('button','Search archive','btn btn-gold');search.type='submit';form.append(searchLabel,typeLabel,search);
    form.onsubmit=event=>{event.preventDefault();skip=0;load();};
    status=element('p',undefined,'archive-muted');status.setAttribute('role','status');list=element('div',undefined,'table-responsive archive-records');
    const pagination=element('div',undefined,'archive-pagination');previous=element('button','Previous','btn btn-outline btn-sm');next=element('button','Next','btn btn-outline btn-sm');previous.type=next.type='button';previous.disabled=next.disabled=true;previous.onclick=()=>{skip=Math.max(0,skip-limit);load();};next.onclick=()=>{skip+=limit;load();};pagination.append(previous,next);
    card.append(element('h3','Retained records'),element('p','Removed content is retained here for SuperAdmin review. Other accounts cannot view this archive.','archive-muted'),form,status,list,pagination);panel.append(card);container.append(panel);
    dialog=element('dialog',undefined,'archive-dialog');dialog.setAttribute('aria-label','Retained record');dialog.onclose=()=>{detailVersion++;};const close=element('button','Close','btn btn-outline btn-sm');close.type='button';close.onclick=()=>dialog.close();detail=element('div');dialog.append(close,detail);document.body.append(dialog);
  }
  document.addEventListener('DOMContentLoaded',setup);
})();
