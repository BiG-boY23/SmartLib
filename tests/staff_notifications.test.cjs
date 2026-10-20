const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const flush = () => new Promise(resolve => setImmediate(resolve));

function harness() {
  const insertedHTML = [], calls = [], documentListeners = {};
  class Element {
    constructor(tag = 'div') { this.tagName = tag; this.children = []; this.attributes = {}; this.hidden = false; this.disabled = false; this._text = ''; this.className = ''; }
    set innerHTML(value) {
      insertedHTML.push(value);
      for (const match of value.matchAll(/<(\w+)[^>]*class="([^"]+)"[^>]*>/g)) {
        const child = new Element(match[1]); child.className = match[2];
        child.hidden = /\shidden(?:\s|>)/.test(match[0]); this.append(child);
      }
    }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = []; this._text = ''; this.append(...children); }
    insertBefore(child, reference) { this.children.splice(this.children.indexOf(reference), 0, child); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    querySelectorAll(selector) {
      const result = [];
      for (const child of this.children) {
        if (selector.startsWith('.') ? child.className.split(' ').includes(selector.slice(1)) : child.tagName === selector) result.push(child);
        result.push(...child.querySelectorAll(selector));
      }
      return result;
    }
    contains(target) { return this === target || this.children.some(child => child.contains(target)); }
    setAttribute(key, value) { this.attributes[key] = value; }
    focus() { this.focused = true; }
    click() { if (!this.disabled) return this.onclick?.({target:this}); }
  }
  const topbar = new Element('header'); const account = new Element(); account.className = 'account-btn-wrap'; topbar.append(account);
  const rows = [{id:1,title:'Book borrowed',body:'<img src=x onerror=alert(1)>',kind:'borrowing',created_at:'2026-10-20T01:00:00',read:false},
                {id:2,title:'Book returned',body:'Student returned a book.',kind:'borrowing',created_at:'2026-10-19T01:00:00',read:false}];
  let denied = false, holdNext = false, release = null, cleared = false;
  const context = vm.createContext({
    window:{}, document:{readyState:'complete',hidden:false,querySelector:s=>s==='.app-topbar'?topbar:null,createElement:t=>new Element(t),addEventListener:(type, fn)=>{documentListeners[type]=fn;}},
    getAuthHeaders:()=>({Authorization:'Bearer test'}), setInterval:()=>1, clearInterval:()=>{cleared=true;},
    fetch:async(path,options={})=>{
      calls.push({path,method:options.method || 'GET',headers:options.headers});
      if (denied) return {ok:false,status:401};
      if (path.endsWith('/read-all')) for (const row of rows) row.read = true;
      else if (/\/\d+\/read$/.test(path)) rows.find(row=>row.id === Number(path.split('/').at(-2))).read = true;
      const snapshot = {unread_count:rows.filter(row=>!row.read).length, items:structuredClone(rows)};
      if (holdNext) { holdNext=false; await new Promise(resolve=>{release=resolve;}); }
      return {ok:true,json:async()=>snapshot};
    },
  });
  vm.runInContext(fs.readFileSync('static/staff-notifications.js','utf8'), context);
  const get = selector=>topbar.querySelector(selector);
  return {context, get, calls, rows, insertedHTML, documentListeners, deny:()=>{denied=true;}, hold:()=>{holdNext=true;}, release:()=>release(), wasCleared:()=>cleared};
}

test('mounts one accessible inbox and displays server content as text', async()=>{
  const h = harness(); await flush();
  assert.equal(h.get('.staff-notifications-badge').textContent,'2');
  assert.match(h.get('.staff-notifications-trigger').attributes['aria-label'],/2 unread/);
  assert.equal(h.get('.staff-notifications-list').querySelectorAll('article').length,2);
  assert.match(h.get('.staff-notifications-list').textContent,/<img src=x onerror=alert\(1\)>/);
  assert.equal(h.insertedHTML.some(value=>value.includes('onerror')),false);
  h.context.window.SmartLibNotifications.mount();
  assert.equal(h.calls.length,1);
});

test('opening does not mark messages read; individual and all-read controls use POST', async()=>{
  const h = harness(); await flush();
  h.get('.staff-notifications-trigger').click(); await flush();
  assert.equal(h.get('.staff-notifications-panel').hidden,false);
  assert.equal(h.calls.filter(call=>call.method==='POST').length,0);
  await h.get('.staff-notifications-list').querySelector('button').click();
  assert.equal(h.get('.staff-notifications-badge').textContent,'1');
  assert.equal(h.calls.find(call=>call.method==='POST').path,'/admin/notifications/1/read');
  await h.get('.staff-notifications-read-all').click();
  assert.equal(h.get('.staff-notifications-badge').hidden,true);
  assert.equal(h.get('.staff-notifications-read-all').disabled,true);
  assert.equal(h.calls.at(-2).path,'/admin/notifications/read-all');
  assert.equal(h.calls.at(-2).method,'POST');
});

test('dismisses with Escape and ignores a stale refresh after marking read', async()=>{
  const h = harness(); await flush();
  h.get('.staff-notifications-trigger').click(); await flush();
  h.documentListeners.keydown({key:'Escape'});
  assert.equal(h.get('.staff-notifications-panel').hidden,true);
  assert.equal(h.get('.staff-notifications-trigger').focused,true);
  h.hold(); const stale = h.context.window.SmartLibNotifications.refresh();
  await h.get('.staff-notifications-read-all').click();
  h.release(); await stale;
  assert.equal(h.get('.staff-notifications-badge').hidden,true);
});

test('expired staff session clears inbox and stops polling', async()=>{
  const h = harness(); await flush(); h.deny();
  await h.context.window.SmartLibNotifications.refresh();
  assert.equal(h.get('.staff-notifications').hidden,true);
  assert.equal(h.wasCleared(),true);
  assert.doesNotMatch(h.get('.staff-notifications-list').textContent,/Student|onerror/);
});
