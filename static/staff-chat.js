(function () {
  let active = false;
  let timer = null;
  let threadId = null;
  let headers = null;
  let notify = null;
  let ready = false;
  let busy = false;

  const el = id => document.getElementById(id);
  const dateLabel = value => value ? new Date(/(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : value + 'Z').toLocaleString() : '';

  async function request(path, options = {}) {
    const res = await fetch(path, { ...options, headers: headers() });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || 'The message could not be loaded.');
    }
    return res.json();
  }

  function setup() {
    if (ready) return;
    const root = el('staffChatRoot');
    if (!root) return;
    root.innerHTML = `
      <div class="staff-chat-shell">
        <section class="staff-chat-card">
          <div class="staff-chat-header"><div><h3>Conversations</h3><small>Student and faculty messages</small></div><button type="button" class="btn btn-maroon btn-sm" id="staffChatNew">New</button></div>
          <div class="staff-chat-list" id="staffChatList"><div class="staff-chat-empty">Loading conversations…</div></div>
        </section>
        <section class="staff-chat-card staff-chat-conversation">
          <div class="staff-chat-header"><div><h3 id="staffChatTitle">Select a conversation</h3><small id="staffChatSubtitle">Read the history and reply here.</small></div></div>
          <div class="staff-chat-messages" id="staffChatMessages"><div class="staff-chat-empty">Choose a conversation or start a new one.</div></div>
          <form class="staff-chat-composer" id="staffChatReplyForm" hidden><textarea id="staffChatReply" rows="3" maxlength="2000" required placeholder="Write a reply…"></textarea><div class="staff-chat-actions"><button type="submit" class="btn btn-maroon btn-sm">Send reply</button></div></form>
          <form class="staff-chat-compose" id="staffChatComposeForm" hidden><label for="staffChatRecipient">Student or faculty ID</label><input id="staffChatRecipient" maxlength="100" required placeholder="Institutional ID"><label for="staffChatSubject">Subject</label><input id="staffChatSubject" maxlength="160" minlength="3" required placeholder="Conversation subject"><label for="staffChatFirstMessage">Message</label><textarea id="staffChatFirstMessage" rows="4" maxlength="2000" required placeholder="Write your message…"></textarea><div class="staff-chat-actions"><button type="button" class="btn btn-outline btn-sm" id="staffChatCancel">Cancel</button><button type="submit" class="btn btn-maroon btn-sm">Send message</button></div></form>
        </section>
      </div>`;
    el('staffChatNew').addEventListener('click', showCompose);
    el('staffChatCancel').addEventListener('click', () => {
      el('staffChatComposeForm').hidden = true;
      if (threadId) openThread(threadId);
      else {
        el('staffChatTitle').textContent = 'Select a conversation';
        el('staffChatSubtitle').textContent = 'Read the history and reply here.';
        el('staffChatMessages').textContent = 'Choose a conversation or start a new one.';
      }
    });
    el('staffChatComposeForm').addEventListener('submit', createThread);
    el('staffChatReplyForm').addEventListener('submit', sendReply);
    ready = true;
  }

  async function loadThreads() {
    if (!active) return;
    try {
      const threads = await request('/admin/messages');
      const list = el('staffChatList');
      list.replaceChildren();
      if (!threads.length) {
        const empty = document.createElement('div');
        empty.className = 'staff-chat-empty';
        empty.textContent = 'No conversations yet. Start one with a patron ID.';
        list.append(empty);
      }
      for (const thread of threads) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'staff-chat-thread' + (thread.id === threadId ? ' active' : '');
        const name = document.createElement('strong');
        name.textContent = thread.patron_name || 'Library patron';
        if (thread.unread_count) {
          const unread = document.createElement('span');
          unread.className = 'staff-chat-unread';
          unread.textContent = thread.unread_count;
          name.append(unread);
        }
        const subject = document.createElement('small');
        subject.textContent = thread.subject;
        const preview = document.createElement('div');
        preview.className = 'staff-chat-preview';
        preview.textContent = thread.last_message || '';
        button.append(name, subject, preview);
        button.addEventListener('click', () => openThread(thread.id));
        list.append(button);
      }
    } catch (error) { notify(error.message, 'danger'); }
  }

  async function openThread(id) {
    if (!active) return;
    threadId = id;
    el('staffChatComposeForm').hidden = true;
    try {
      const data = await request(`/admin/messages/${id}`);
      if (threadId !== id) return;
      el('staffChatTitle').textContent = data.thread.subject;
      el('staffChatSubtitle').textContent = `${data.thread.patron_name} · ${data.thread.patron_email || 'Patron'}${data.thread.is_closed ? ' · Closed' : ''}`;
      el('staffChatReplyForm').hidden = data.thread.is_closed;
      const box = el('staffChatMessages');
      const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
      box.replaceChildren();
      for (const message of data.messages) {
        const bubble = document.createElement('div');
        bubble.className = 'staff-chat-bubble' + (message.is_staff ? ' staff' : '');
        const sender = document.createElement('strong');
        sender.textContent = message.sender_name || (message.is_staff ? 'Library staff' : data.thread.patron_name);
        const body = document.createElement('div');
        body.textContent = message.message;
        const date = document.createElement('small');
        date.textContent = dateLabel(message.created_at);
        bubble.append(sender, body, date);
        box.append(bubble);
      }
      if (nearBottom) box.scrollTop = box.scrollHeight;
      loadThreads();
    } catch (error) { notify(error.message, 'danger'); }
  }

  function showCompose() {
    threadId = null;
    el('staffChatTitle').textContent = 'New message';
    el('staffChatSubtitle').textContent = 'Send a message to an active student or faculty account.';
    el('staffChatMessages').replaceChildren();
    el('staffChatReplyForm').hidden = true;
    el('staffChatComposeForm').hidden = false;
    el('staffChatRecipient').focus();
  }

  async function createThread(event) {
    event.preventDefault();
    if (busy) return;
    busy = true;
    const form = event.currentTarget;
    const button = form.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      const thread = await request('/admin/messages', { method: 'POST', body: JSON.stringify({ recipient_username: el('staffChatRecipient').value.trim(), subject: el('staffChatSubject').value.trim(), message: el('staffChatFirstMessage').value.trim() }) });
      form.reset();
      notify('Message sent.', 'success');
      await openThread(thread.id);
    } catch (error) { notify(error.message, 'danger'); }
    finally { busy = false; button.disabled = false; }
  }

  async function sendReply(event) {
    event.preventDefault();
    if (busy || !threadId) return;
    busy = true;
    const input = el('staffChatReply');
    const button = event.currentTarget.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      await request(`/admin/messages/${threadId}/reply`, { method: 'POST', body: JSON.stringify({ message: input.value.trim() }) });
      input.value = '';
      await openThread(threadId);
    } catch (error) { notify(error.message, 'danger'); }
    finally { busy = false; button.disabled = false; }
  }

  window.SmartLibChat = {
    setActive(value, getHeaders, toast) {
      active = value;
      headers = getHeaders;
      notify = toast;
      if (timer) clearInterval(timer);
      timer = null;
      if (!active) return;
      setup();
      loadThreads();
      if (threadId) openThread(threadId);
      timer = setInterval(() => { if (document.visibilityState === 'visible') { loadThreads(); if (threadId) openThread(threadId); } }, 15000);
    },
  };
})();
