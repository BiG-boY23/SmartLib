const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const flush = () => new Promise(resolve => setImmediate(resolve));

function harness(role, {authOK=true}={}) {
  const calls=[], notices=[], insertedHTML=[], listeners={};
  class Element {
    constructor(tag='div') { this.tagName=tag; this.children=[]; this.style={}; this.dataset={}; this.attributes={}; this.className=''; this.id=''; this._text=''; this.value=''; this.disabled=false; }
    get classList() { return {add:(name)=>{this.className=[...new Set([...this.className.split(' ').filter(Boolean),name])].join(' ');},remove:(name)=>{this.className=this.className.split(' ').filter(value=>value!==name).join(' ');},contains:name=>this.className.split(' ').includes(name)}; }
    set textContent(value) { this._text=String(value); this.children=[]; }
    get textContent() { return this._text+this.children.map(child=>child.textContent).join(''); }
    set innerHTML(value) { insertedHTML.push(value); this.children=[]; }
    append(...children) { for(let child of children) { if(typeof child==='string') {const text=new Element('text');text.textContent=child;child=text;} child.parentElement=this;this.children.push(child); } }
    prepend(child) { child.parentElement=this;this.children.unshift(child); }
    replaceChildren(...children) { this._text='';this.children=[];this.append(...children); }
    setAttribute(name,value) { this.attributes[name]=value; }
    matches(selector) {
      if(selector.startsWith('#'))return this.id===selector.slice(1);
      if(selector.startsWith('.'))return this.className.split(' ').includes(selector.slice(1));
      const data=selector.match(/^\[data-archive-view="(.*)"\]$/);
      if(data)return this.dataset.archiveView===data[1];
      return this.tagName===selector;
    }
    querySelectorAll(selector) { const parts=selector.split(','); const result=[];for(const child of this.children){if(parts.some(part=>child.matches(part.trim())))result.push(child);result.push(...child.querySelectorAll(selector));}return result; }
    querySelector(selector) { return this.querySelectorAll(selector)[0]||null; }
    closest(selector) { return this.matches(selector)?this:this.parentElement?.closest(selector)||null; }
    showModal() { this.open=true; }
    close() { this.open=false; }
    click() { if(!this.disabled)return this.onclick?.({target:this,preventDefault(){}}); }
  }
  const body=new Element('body');
  const mount=(tag,className,id,parent=body)=>{const node=new Element(tag);node.className=className;node.id=id||'';parent.append(node);return node;};
  const menu=mount('aside','sidebar-menu');mount('button','nav-item-btn active','navOriginal',menu);
  const container=mount('main','content-container');mount('div','screen-panel active','panelOriginal',container);
  mount('h1','','pageTitle');mount('p','','pageSubTitle');
  const archivedShelf=mount('section','card');mount('tbody','','archivedShelfBody',archivedShelf);
  const get=id=>body.querySelector('#'+id);
  const retained=Array.from({length:56},(_,index)=>({id:`retained:${index}`,kind:index===0?'conversations':'books',record_id:String(index),
    title:index===0?'<img src=x onerror=alert(1)>':'Archived title '+index,deleted_by:'SuperAdmin',deleted_at:'2026-10-20T01:00:00',
    action:'delete',origin:'mobile',content:{subject:'<script>alert("subject")</script>',messages:[{body:'<svg onload=alert(1)>',sent_at:'2026-10-19T00:00:00'}],empty:[],is_closed:true,
      password:'Do not show password',access_token:'Do not show token',session_id:'Do not show session'}}));
  const response=(data,ok=true)=>({ok,json:async()=>structuredClone(data)});
  let holdId=null,release=null;
  const context=vm.createContext({
    URLSearchParams, getAuthHeaders:()=>({Authorization:'Bearer mock-only'}), toast:(...notice)=>notices.push(notice),
    // A forged local role must never grant archive UI access.
    localStorage:{getItem:()=>JSON.stringify({role:'superadmin'})},
    document:{body,getElementById:get,createElement:tag=>new Element(tag),querySelector:selector=>body.querySelector(selector),querySelectorAll:selector=>body.querySelectorAll(selector),addEventListener:(type,callback)=>{(listeners[type]||=[]).push(callback);}},
    fetch:async(path,options={})=>{
      calls.push({path,method:options.method||'GET',headers:options.headers});
      const url=new URL(path,'http://localhost');
      if(url.pathname==='/auth/me')return response(authOK?{role}:{detail:'Session expired'},authOK);
      if(url.pathname==='/admin/archives')return response([{key:'acquisitions:ACQ-1',reason:'Reviewed',archived_by:'SuperAdmin'}]);
      if(url.pathname==='/admin/deleted-archives') {
        let records=retained;
        if(url.searchParams.get('kind'))records=records.filter(row=>row.kind===url.searchParams.get('kind'));
        if(url.searchParams.get('q'))records=records.filter(row=>row.title.includes(url.searchParams.get('q')));
        const skip=Number(url.searchParams.get('skip'))||0,limit=Number(url.searchParams.get('limit'))||50;
        return response({items:records.slice(skip,skip+limit),total:records.length,skip,limit});
      }
      if(url.pathname.startsWith('/admin/deleted-archives/')) {
        const id=decodeURIComponent(url.pathname.split('/').at(-1)),record=retained.find(row=>row.id===id);
        if(id===holdId) { holdId=null;await new Promise(resolve=>{release=resolve;}); }
        return response(record);
      }
      if(url.pathname==='/admin/announcements')return response([]);
      throw Error('Unexpected mocked path '+path);
    },
  });
  context.window=context;
  for(const path of ['static/staff-records.js','static/staff-archives.js'])vm.runInContext(fs.readFileSync(path,'utf8'),context);
  return {context,body,get,menu,container,archivedShelf,calls,notices,insertedHTML,
    ready:async()=>{await Promise.all((listeners.DOMContentLoaded||[]).map(callback=>callback()));await flush();await flush();},
    settle:async()=>{await flush();await flush();},
    host:()=>mount('section',''),
    hold:id=>{holdId=id;},release:()=>release(),
  };
}

for(const role of ['admin','librarian'])test(`${role} has no archive navigation, selectors, or archive API requests`,async()=>{
  const h=harness(role);await h.ready();
  assert.equal(h.context.StaffRecords.isSuperAdmin(),false);
  assert.equal(h.get('navRetainedArchive'),null);
  assert.equal(h.get('panelRetainedArchive'),null);
  assert.equal(h.archivedShelf.style.display,'none');
  const host=h.host();h.context.StaffRecords.control('messages',host,()=>{});
  assert.equal(host.querySelector('select'),null);
  assert.equal(h.context.StaffRecords.viewUrl('/admin/messages?search=test','messages'),'/admin/messages?search=test');
  assert.equal(h.calls.filter(call=>call.path==='/auth/me').length,1);
  assert.equal(h.calls.some(call=>call.path.includes('/archives')||call.path.includes('/deleted-archives')),false);
});

test('a failed server profile does not grant archive access from local account data',async()=>{
  const h=harness('superadmin',{authOK:false});await h.ready();
  assert.equal(h.get('navRetainedArchive'),null);
  assert.equal(h.calls.some(call=>call.path.includes('/admin/')),false);
  assert.equal(h.notices.length,1);
});

test('SuperAdmin can browse retained records and inspect snapshots as safe text',async()=>{
  const h=harness('superadmin');await h.ready();
  assert.equal(h.context.StaffRecords.isSuperAdmin(),true);
  assert.equal(h.archivedShelf.style.display,'');
  assert.equal(h.get('panelRetainedArchive').classList.contains('active'),false);
  h.get('navRetainedArchive').click();await h.settle();
  const panel=h.get('panelRetainedArchive');
  assert.equal(panel.classList.contains('active'),true);
  assert.equal(h.get('panelOriginal').classList.contains('active'),false);
  assert.equal(panel.querySelectorAll('tbody')[0].children.length,50);
  assert.match(panel.textContent,/<img src=x onerror=alert\(1\)>/);
  await panel.querySelector('tbody').querySelector('button').click();await h.settle();
  const dialog=h.body.querySelector('dialog');
  assert.equal(dialog.open,true);
  assert.equal(dialog.querySelector('h3').textContent,'<img src=x onerror=alert(1)>');
  assert.match(dialog.textContent,/<script>alert\("subject"\)<\/script>/);
  assert.match(dialog.textContent,/<svg onload=alert\(1\)>/);
  assert.doesNotMatch(dialog.textContent,/Do not show password|Do not show token|Do not show session/);
  assert.equal(h.insertedHTML.some(markup=>/onerror|onload|<script>/.test(markup)),false);
  assert.ok(h.calls.some(call=>call.path==='/admin/deleted-archives/retained%3A0'));
  assert.ok(h.calls.every(call=>call.method==='GET'));
  dialog.querySelector('button').click();assert.equal(dialog.open,false);
});

test('archive filters and pagination preserve scope and reset skip on search',async()=>{
  const h=harness('superadmin');await h.ready();h.get('navRetainedArchive').click();await h.settle();
  const panel=h.get('panelRetainedArchive'),pagination=panel.querySelector('.archive-pagination');
  assert.equal(pagination.children[0].disabled,true);assert.equal(pagination.children[1].disabled,false);
  pagination.children[1].click();await h.settle();
  assert.equal(panel.querySelector('tbody').children.length,6);
  assert.equal(new URL(h.calls.at(-1).path,'http://localhost').searchParams.get('skip'),'50');
  const form=panel.querySelector('form');form.querySelector('input').value='Archived title 5';form.querySelector('select').value='books';
  form.onsubmit({preventDefault(){}});await h.settle();
  const url=new URL(h.calls.at(-1).path,'http://localhost');
  assert.equal(url.searchParams.get('skip'),'0');assert.equal(url.searchParams.get('q'),'Archived title 5');assert.equal(url.searchParams.get('kind'),'books');
  assert.equal(panel.querySelector('tbody').children.length,7);
  assert.equal(pagination.children[1].disabled,true);
});

test('archive_view query forwarding is isolated by record kind and existing parameters',async()=>{
  const h=harness('superadmin');await h.ready();
  const messages=h.host(),acquisitions=h.host(),secondMessages=h.host();let reloads=0;
  h.context.StaffRecords.control('messages',messages,()=>reloads++);
  h.context.StaffRecords.control('acquisitions',acquisitions,()=>reloads++);
  h.context.StaffRecords.control('messages',secondMessages,()=>reloads++);
  messages.querySelector('select').value='archived';messages.querySelector('select').onchange();
  assert.equal(secondMessages.querySelector('select').value,'archived');
  assert.equal(h.context.StaffRecords.viewUrl('/admin/messages?search=Patron','messages'),'/admin/messages?search=Patron&archive_view=true');
  assert.equal(h.context.StaffRecords.viewUrl('/admin/messages/12','messages'),'/admin/messages/12?archive_view=true');
  assert.equal(h.context.StaffRecords.viewUrl('/admin/acquisitions','acquisitions'),'/admin/acquisitions');
  assert.equal(h.context.StaffRecords.viewUrl('/announcements','announcements'),'/announcements');
  acquisitions.querySelector('select').value='archived';acquisitions.querySelector('select').onchange();
  messages.querySelector('select').value='active';messages.querySelector('select').onchange();
  assert.equal(h.context.StaffRecords.viewUrl('/admin/acquisitions','acquisitions'),'/admin/acquisitions?archive_view=true');
  assert.equal(h.context.StaffRecords.viewUrl('/admin/messages','messages'),'/admin/messages');
  assert.equal(reloads,3);
});

test('late detail response cannot replace a more recently selected record',async()=>{
  const h=harness('superadmin');await h.ready();h.get('navRetainedArchive').click();await h.settle();
  const buttons=h.get('panelRetainedArchive').querySelector('tbody').querySelectorAll('button');
  h.hold('retained:0');const slow=buttons[0].click();await h.settle();
  await buttons[1].click();await h.settle();
  const dialog=h.body.querySelector('dialog');assert.equal(dialog.querySelector('h3').textContent,'Archived title 1');
  h.release();await slow;await h.settle();
  assert.equal(dialog.querySelector('h3').textContent,'Archived title 1');
  assert.doesNotMatch(dialog.textContent,/<img src=x/);
});
