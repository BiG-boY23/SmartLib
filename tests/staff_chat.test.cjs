const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

const flush = () => new Promise(resolve => setImmediate(resolve));

function chatHarness({archived=false}={}) {
  const elements = new Map();
  class Element {
    constructor(tag = 'div') {
      this.tagName = tag;
      this.children = [];
      this.listeners = {};
      this.style = {};
      this.dataset = {};
      this.attributes = {};
      this.hidden = false;
      this.disabled = false;
      this.value = '';
      this._text = '';
      this.scrollHeight = 0;
      this.scrollTop = 0;
      this.clientHeight = 0;
    }
    set innerHTML(value) {
      for (const match of value.matchAll(/id="([^"]+)"/g)) elements.set(match[1], new Element());
    }
    set textContent(value) { this._text = value; this.children = []; }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
    append(...children) { for (const child of children) { child.parentElement = this; this.children.push(child); } }
    replaceChildren(...children) { this.children = []; this._text = ''; this.append(...children); }
    addEventListener(type, listener) { this.listeners[type] = listener; }
    setAttribute(key, value) { this.attributes[key] = value; }
    querySelector(selector) {
      if (selector === '[type="submit"]') return this.submitButton ||= new Element('button');
      return this.querySelectorAll(selector)[0] || null;
    }
    querySelectorAll(selector) {
      const matches = [];
      for (const child of this.children) {
        if (selector === '[data-message-delete]' && 'messageDelete' in child.dataset) matches.push(child);
        matches.push(...child.querySelectorAll(selector));
      }
      return matches;
    }
    focus() {}
    reset() {}
    click() { if (!this.disabled) return this.listeners.click?.({currentTarget: this}); }
  }
  elements.set('staffChatRoot', new Element());
  const get = id => elements.get(id);
  const histories = new Map([
    [1, [{id:11, message:'Keep this', sender_name:'Student', is_staff:false, created_at:'2026-10-19T00:00:11'}, {id:12, message:'Remove this', sender_name:'Staff', is_staff:true, created_at:'2026-10-19T00:00:12'}]],
    [2, [{id:21, message:'Other conversation', sender_name:'Student', is_staff:false, created_at:'2026-10-19T00:00:21'}]],
  ]);
  const versions = new Map([1,2].map(id=>[id,{created_at:`2026-10-19T00:00:0${id}`, updated_at:`2026-10-19T00:01:0${id}`} ]));
  const calls = [], notices = [], prompts = [], delayedReads = [];
  let confirmation = true, holdNextRead = false, holdDelete = false, releaseDelete;
  const inbox = () => [...histories].map(([id, messages]) => ({id, subject:`Subject ${id}`, patron_name:`Patron ${id}`, last_message:messages.at(-1)?.message || '', unread_count:0}));
  const response = data => ({ok:true, json:async()=>structuredClone(data)});
  const context = vm.createContext({
    window: {confirm:message=>{prompts.push(message);return confirmation;}}, URLSearchParams,
    document: {getElementById:get, createElement:tag=>new Element(tag), visibilityState:'visible'},
    setInterval:()=>1, clearInterval(){},
    StaffRecords:{refresh:async()=>{}, control(){}, visible:()=>true, viewUrl:path=>path, isArchivedView:()=>archived, button:()=>new Element('button')},
    fetch:async (path, options={})=>{
      calls.push({path, method:options.method || 'GET'});
      const url = new URL(path, 'http://localhost');
      const route = url.pathname;
      const match = route.match(/^\/admin\/messages\/(\d+)(?:\/messages(?:\/(\d+))?)?$/);
      if (!match) return response(inbox());
      const id = Number(match[1]), messageId = Number(match[2]);
      if (options.method === 'DELETE') {
        if (holdDelete) await new Promise(resolve=>{releaseDelete=resolve;});
        const expectedCreated = url.searchParams.get('expected_created_at');
        const expectedThread = url.searchParams.get('expected_thread_created_at');
        const expectedUpdated = url.searchParams.get('expected_updated_at');
        const actualCreated = messageId ? histories.get(id).find(message=>message.id===messageId)?.created_at : versions.get(id).created_at;
        if (expectedCreated !== actualCreated || (expectedThread && expectedThread !== versions.get(id).created_at) ||
            (expectedUpdated && expectedUpdated !== versions.get(id).updated_at)) {
          return {ok:false, status:409, json:async()=>({detail:'This conversation has changed. Refresh it before deleting messages.'})};
        }
        if (messageId) histories.set(id, histories.get(id).filter(message=>message.id!==messageId));
        else if (route.endsWith('/messages')) histories.set(id, []);
        else histories.delete(id);
        if (histories.has(id)) versions.get(id).updated_at += '1';
        return response({message:'Deleted for staff and patron.'});
      }
      const snapshot = {thread:{id, subject:`Subject ${id}`, patron_name:`Patron ${id}`, is_closed:false, ...versions.get(id)}, messages:structuredClone(histories.get(id))};
      if (holdNextRead) {
        holdNextRead = false;
        return new Promise(resolve=>delayedReads.push(()=>resolve(response(snapshot))));
      }
      return response(snapshot);
    },
  });
  vm.runInContext(fs.readFileSync('static/staff-chat.js', 'utf8'), context);
  context.window.SmartLibChat.setActive(true, ()=>({Authorization:'test'}), (...args)=>notices.push(args));
  return {get, calls, notices, prompts, histories, versions, delayedReads,
    settle:async()=>{await flush();await flush();},
    confirm:value=>{confirmation=value;},
    holdNextRead:()=>{holdNextRead=true;},
    holdDelete:()=>{holdDelete=true;},
    releaseDelete:()=>releaseDelete(),
    threads:()=>get('staffChatList').children.filter(child=>child.className?.includes('staff-chat-thread')),
    removeButtons:()=>get('staffChatMessages').querySelectorAll('[data-message-delete]'),
  };
}

test('shared chat confirms deletes, clears history, and deletes the whole conversation', async () => {
  const chat = chatHarness();
  await chat.settle();
  await chat.threads()[0].click();
  await chat.settle();
  assert.equal(chat.get('staffChatManage').style.display, 'flex');
  assert.equal(chat.removeButtons().length, 2);
  for (const button of chat.removeButtons()) {
    assert.equal(button.textContent, 'Delete message');
    assert.equal(button.parentElement.className, 'staff-chat-message-footer');
    assert.ok(button.attributes['aria-label'].startsWith('Delete message from '));
    assert.ok(button.title.includes('staff and the patron'));
  }

  chat.confirm(false);
  await chat.removeButtons()[1].click();
  assert.equal(chat.calls.filter(call=>call.method==='DELETE').length, 0);
  assert.equal(chat.histories.get(1).length, 2);

  chat.confirm(true);
  await chat.removeButtons()[1].click();
  await chat.settle();
  const individualDelete = new URL(chat.calls.find(call=>call.method==='DELETE').path, 'http://localhost');
  assert.equal(individualDelete.pathname, '/admin/messages/1/messages/12');
  assert.equal(individualDelete.searchParams.get('expected_created_at'), '2026-10-19T00:00:12');
  assert.equal(individualDelete.searchParams.get('expected_thread_created_at'), '2026-10-19T00:00:01');
  assert.equal(chat.removeButtons().length, 1);
  assert.ok(chat.get('staffChatMessages').textContent.includes('Keep this'));
  assert.ok(!chat.get('staffChatMessages').textContent.includes('Remove this'));
  assert.ok(chat.prompts.every(prompt=>prompt.includes('staff and the patron') && prompt.includes('SuperAdmin-only archive')));

  await chat.get('staffChatClear').click();
  await chat.settle();
  assert.equal(chat.histories.get(1).length, 0);
  assert.equal(chat.removeButtons().length, 0);
  assert.ok(chat.get('staffChatMessages').textContent.includes('Send a reply to continue'));
  assert.equal(chat.get('staffChatReplyForm').hidden, false);
  assert.equal(chat.threads().length, 2);

  await chat.get('staffChatDelete').click();
  await chat.settle();
  assert.equal(chat.histories.has(1), false);
  assert.equal(chat.histories.get(2).length, 1);
  assert.equal(chat.threads().length, 1);
  assert.equal(chat.get('staffChatManage').style.display, 'none');
  assert.equal(chat.get('staffChatReplyForm').hidden, true);
  assert.equal(chat.notices.filter(notice=>notice[1]==='success').length, 3);
  for (const call of chat.calls.filter(call=>call.method==='DELETE' && !call.path.includes('/messages/12'))) {
    const url = new URL(call.path, 'http://localhost');
    assert.ok(url.searchParams.get('expected_created_at'));
    assert.ok(url.searchParams.get('expected_updated_at'));
  }
});

test('an old polling response cannot restore deleted content or switch the pending delete target', async () => {
  const chat = chatHarness();
  await chat.settle();
  await chat.threads()[0].click();
  await chat.settle();
  chat.holdNextRead();
  const oldRead = chat.threads()[0].click();
  await chat.settle();
  chat.holdDelete();
  const deletion = chat.removeButtons()[1].click();
  await chat.settle();
  assert.equal(chat.get('staffChatClear').disabled, true);
  assert.equal(chat.get('staffChatDelete').disabled, true);
  assert.ok(chat.removeButtons().every(button=>button.disabled));
  assert.equal(chat.get('staffChatMessages').attributes['aria-busy'], 'true');
  await chat.threads()[1].click();
  chat.releaseDelete();
  await deletion;
  await chat.settle();
  chat.delayedReads[0]();
  await oldRead;
  await chat.settle();
  assert.equal(chat.get('staffChatTitle').textContent, 'Subject 1');
  assert.ok(!chat.get('staffChatMessages').textContent.includes('Remove this'));
  assert.equal(chat.histories.get(2).length, 1);
  assert.equal(chat.get('staffChatClear').disabled, false);
  assert.equal(chat.get('staffChatMessages').attributes['aria-busy'], 'false');
});

test('stale message IDs are refreshed without deleting replacement content', async () => {
  const chat = chatHarness();
  await chat.settle();
  await chat.threads()[0].click();
  await chat.settle();
  chat.histories.get(1)[1] = {...chat.histories.get(1)[1], message:'Replacement message', created_at:'2026-10-19T01:00:12'};
  await chat.removeButtons()[1].click();
  await chat.settle();
  assert.equal(chat.histories.get(1)[1].message, 'Replacement message');
  assert.ok(chat.get('staffChatMessages').textContent.includes('Replacement message'));
  assert.equal(chat.notices.at(-1)[1], 'danger');
});

for (const action of ['staffChatClear', 'staffChatDelete']) {
  test(`${action} protects a replacement conversation with a reused ID`, async () => {
    const chat = chatHarness();
    await chat.settle();
    await chat.threads()[0].click();
    await chat.settle();
    chat.versions.get(1).created_at = '2026-10-19T01:00:01';
    chat.histories.set(1, [{id:11, message:'New conversation content', sender_name:'Student', created_at:'2026-10-19T01:00:11'}]);
    await chat.get(action).click();
    await chat.settle();
    assert.equal(chat.histories.get(1)[0].message, 'New conversation content');
    assert.ok(chat.get('staffChatMessages').textContent.includes('New conversation content'));
    assert.equal(chat.notices.at(-1)[1], 'danger');
  });
}

test('clearing rejects unseen new messages and refreshes the conversation', async () => {
  const chat = chatHarness();
  await chat.settle();
  await chat.threads()[0].click();
  await chat.settle();
  chat.versions.get(1).updated_at = '2026-10-19T01:01:01';
  chat.histories.get(1).push({id:13, message:'Unseen reply', sender_name:'Student', created_at:'2026-10-19T01:00:13'});
  await chat.get('staffChatClear').click();
  await chat.settle();
  assert.equal(chat.histories.get(1).length, 3);
  assert.ok(chat.get('staffChatMessages').textContent.includes('Unseen reply'));
  assert.equal(chat.notices.at(-1)[1], 'danger');
});

test('archived conversations remain read-only until SuperAdmin restores them', async () => {
  const chat=chatHarness({archived:true});
  await chat.settle();
  await chat.threads()[0].click();
  await chat.settle();
  assert.equal(chat.removeButtons().length,0);
  assert.equal(chat.get('staffChatManage').style.display,'none');
  assert.equal(chat.get('staffChatReplyForm').hidden,true);
  assert.ok(chat.get('staffChatSubtitle').textContent.includes('Restore to continue'));
});

test('both staff dashboards load the same versioned message controls', () => {
  const assets = [];
  for (const dashboard of ['admin-dashboard.html', 'librarian-dashboard.html']) {
    const html = fs.readFileSync(dashboard, 'utf8');
    const script = html.match(/<script src="(\/static\/staff-chat\.js\?v=[^"]+)"/);
    const stylesheet = html.match(/href="(\/static\/staff-chat\.css\?v=[^"]+)"/);
    assert.ok(script, `${dashboard} must bypass the previous cached chat script`);
    assert.ok(stylesheet, `${dashboard} must bypass the previous cached chat styles`);
    assert.equal(script[1].split('?')[1], stylesheet[1].split('?')[1]);
    assert.ok(html.includes('id="staffChatRoot"'));
    assert.ok(html.includes('window.SmartLibChat.setActive'));
    assets.push(script[1]);
  }
  assert.equal(assets[0], assets[1]);
});
