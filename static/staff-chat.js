(function () {
  let active = false;
  let timer = null;
  let threadId = null;
  let headers = null;
  let notify = null;
  let ready = false;
  let busy = false;
  let deleting = false;
  let threadLoadVersion = 0;
  let listLoadVersion = 0;
  let threadCreatedAt = null;
  let threadUpdatedAt = null;

  const el = id => document.getElementById(id);
  const dateLabel = value => value ? new Date(/(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : value + 'Z').toLocaleString() : '';

  async function request(path, options = {}) {
    const res = await fetch(path, { ...options, headers: headers() });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      const error = new Error(data.detail || 'The message could not be loaded.');
      error.status = res.status;
      throw error;
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
          <div class="staff-chat-header staff-chat-conversation-header"><div class="staff-chat-heading"><h3 id="staffChatTitle">Select a conversation</h3><small id="staffChatSubtitle">Read the history and reply here.</small></div><div class="staff-chat-manage" id="staffChatManage" style="display:none" role="group" aria-label="Conversation actions"><div class="staff-chat-actions"><button type="button" class="btn btn-outline btn-sm" id="staffChatClear">Clear messages</button><button type="button" class="btn btn-outline btn-sm staff-chat-delete-conversation" id="staffChatDelete">Delete conversation</button></div><small>Removed content is retained in the SuperAdmin-only archive.</small></div></div>
          <div class="staff-chat-messages" id="staffChatMessages"><div class="staff-chat-empty">Choose a conversation or start a new one.</div></div>
          <form class="staff-chat-composer" id="staffChatReplyForm" hidden><textarea id="staffChatReply" rows="3" maxlength="2000" required placeholder="Write a reply…"></textarea><div class="staff-chat-actions"><button type="submit" class="btn btn-maroon btn-sm">Send reply</button></div></form>
          <form class="staff-chat-compose" id="staffChatComposeForm" hidden><label for="staffChatRecipient">Student or faculty ID</label><input id="staffChatRecipient" maxlength="100" required placeholder="Institutional ID"><label for="staffChatSubject">Subject</label><input id="staffChatSubject" maxlength="160" minlength="3" required placeholder="Conversation subject"><label for="staffChatFirstMessage">Message</label><textarea id="staffChatFirstMessage" rows="4" maxlength="2000" required placeholder="Write your message…"></textarea><div class="staff-chat-actions"><button type="button" class="btn btn-outline btn-sm" id="staffChatCancel">Cancel</button><button type="submit" class="btn btn-maroon btn-sm">Send message</button></div></form>
        </section>
      </div>`;
    el('staffChatNew').addEventListener('click', showCompose);
    el('staffChatClear').addEventListener('click', () => removeMessages('clear'));
    el('staffChatDelete').addEventListener('click', () => removeMessages('conversation'));
    el('staffChatCancel').addEventListener('click', () => {
      el('staffChatComposeForm').hidden = true;
      if (threadId) openThread(threadId);
      else resetConversation();
    });
    el('staffChatComposeForm').addEventListener('submit', createThread);
    el('staffChatReplyForm').addEventListener('submit', sendReply);
    ready = true;
  }

  async function loadThreads() {
    if (!active || deleting) return;
    const version = ++listLoadVersion;
    try {
      await StaffRecords.refresh();
      if (!active || deleting || version !== listLoadVersion) return;
      StaffRecords.control('messages',el('staffChatList').parentElement,()=>{resetConversation();loadThreads();});
      const threads = (await request(StaffRecords.viewUrl('/admin/messages','messages'))).filter(t=>StaffRecords.visible('messages',t.id));
      if (!active || deleting || version !== listLoadVersion) return;
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
        list.append(StaffRecords.button('messages',thread.id));
      }
    } catch (error) { notify(error.message, 'danger'); }
  }

  async function openThread(id) {
    if (!active || deleting) return;
    const version = ++threadLoadVersion;
    if (threadId !== id) {
      threadCreatedAt = null;
      threadUpdatedAt = null;
      el('staffChatManage').style.display = 'none';
      el('staffChatReplyForm').hidden = true;
      el('staffChatMessages').textContent = 'Loading conversation…';
    }
    threadId = id;
    el('staffChatComposeForm').hidden = true;
    try {
      const data = await request(StaffRecords.viewUrl(`/admin/messages/${id}`,'messages'));
      if (!active || deleting || threadId !== id || version !== threadLoadVersion) return;
      threadCreatedAt = data.thread.created_at;
      threadUpdatedAt = data.thread.updated_at;
      const archived = StaffRecords.isArchivedView('messages');
      el('staffChatTitle').textContent = data.thread.subject;
      el('staffChatSubtitle').textContent = `${data.thread.patron_name} · ${data.thread.patron_email || 'Patron'}${archived ? ' · Archived. Restore to continue.' : data.thread.is_closed ? ' · Closed' : ''}`;
      el('staffChatReplyForm').hidden = data.thread.is_closed || archived;
      el('staffChatManage').style.display = archived ? 'none' : 'flex';
      const box = el('staffChatMessages');
      box.setAttribute('aria-busy', 'false');
      const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
      box.replaceChildren();
      for (const message of data.messages) {
        const bubble = document.createElement('div');
        bubble.className = 'staff-chat-bubble' + (message.is_staff ? ' staff' : '');
        const sender = document.createElement('strong');
        sender.textContent = message.sender_name || (message.is_staff ? 'Library staff' : data.thread.patron_name);
        const body = document.createElement('div');
        body.className = 'staff-chat-message-body';
        body.textContent = message.message;
        const footer = document.createElement('div');
        footer.className = 'staff-chat-message-footer';
        const date = document.createElement('small');
        date.textContent = dateLabel(message.created_at);
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'staff-chat-delete-message';
        remove.textContent = 'Delete message';
        remove.title = 'Remove this message for staff and the patron; retain a copy in the SuperAdmin archive';
        remove.setAttribute('aria-label', `Delete message from ${sender.textContent} at ${date.textContent}`);
        remove.dataset.messageDelete = message.id;
        remove.disabled = busy;
        remove.addEventListener('click', () => removeMessages('message', message.id, message.created_at));
        footer.append(date);
        if (!archived) footer.append(remove);
        bubble.append(sender, body, footer);
        box.append(bubble);
      }
      if (!data.messages.length) {
        const empty = document.createElement('div');
        empty.className = 'staff-chat-empty';
        empty.textContent = data.thread.is_closed ? 'No messages in this conversation.' : 'No messages in this conversation. Send a reply to continue.';
        box.append(empty);
      }
      if (nearBottom) box.scrollTop = box.scrollHeight;
      loadThreads();
    } catch (error) {
      if (threadId !== id || version !== threadLoadVersion) return;
      if (error.status === 404) { resetConversation(); loadThreads(); }
      notify(error.message, 'danger');
    }
  }

  function resetConversation() {
    threadId = null;
    threadCreatedAt = null;
    threadUpdatedAt = null;
    threadLoadVersion++;
    el('staffChatTitle').textContent = 'Select a conversation';
    el('staffChatSubtitle').textContent = 'Read the history and reply here.';
    el('staffChatMessages').textContent = 'Choose a conversation or start a new one.';
    el('staffChatReplyForm').hidden = true;
    el('staffChatComposeForm').hidden = true;
    el('staffChatManage').style.display = 'none';
  }

  function disableMessageControls(value) {
    el('staffChatNew').disabled = value;
    el('staffChatClear').disabled = value;
    el('staffChatDelete').disabled = value;
    el('staffChatReplyForm').querySelector('[type="submit"]').disabled = value;
    for (const button of el('staffChatMessages').querySelectorAll('[data-message-delete]')) button.disabled = value;
  }

  async function removeMessages(kind, messageId, messageCreatedAt) {
    if (busy || !threadId) return;
    if (!threadCreatedAt || !threadUpdatedAt || (kind === 'message' && !messageCreatedAt)) {
      notify('Refresh this conversation before deleting messages.', 'danger');
      return;
    }
    const prompts = {
      message: 'Remove this message for staff and the patron? A retained copy will be kept in the SuperAdmin-only archive.',
      clear: 'Remove every message for staff and the patron, keeping the conversation? A retained copy will be kept in the SuperAdmin-only archive.',
      conversation: 'Remove this conversation and all its messages for staff and the patron? A retained copy will be kept in the SuperAdmin-only archive.',
    };
    if (!window.confirm(prompts[kind])) return;
    const id = threadId;
    busy = true;
    deleting = true;
    threadLoadVersion++;
    listLoadVersion++;
    disableMessageControls(true);
    el('staffChatMessages').setAttribute('aria-busy', 'true');
    let removed = false;
    let changed = false;
    try {
      const suffix = kind === 'message' ? `/messages/${messageId}` : kind === 'clear' ? '/messages' : '';
      const snapshot = new URLSearchParams({expected_created_at: kind === 'message' ? messageCreatedAt : threadCreatedAt});
      if (kind === 'message') snapshot.set('expected_thread_created_at', threadCreatedAt);
      else snapshot.set('expected_updated_at', threadUpdatedAt);
      const result = await request(`/admin/messages/${id}${suffix}?${snapshot}`, { method: 'DELETE' });
      notify(result.message, 'success');
      removed = true;
      if (kind === 'conversation') resetConversation();
    } catch (error) {
      if (error.status === 404) resetConversation();
      changed = error.status === 409;
      notify(error.message, 'danger');
    } finally {
      busy = false;
      deleting = false;
      disableMessageControls(false);
      el('staffChatMessages').setAttribute('aria-busy', 'false');
    }
    if (((removed && kind !== 'conversation') || changed) && threadId === id) await openThread(id);
    else await loadThreads();
  }

  function showCompose() {
    if (busy) return;
    threadId = null;
    threadCreatedAt = null;
    threadUpdatedAt = null;
    threadLoadVersion++;
    el('staffChatTitle').textContent = 'New message';
    el('staffChatSubtitle').textContent = 'Send a message to an active student or faculty account.';
    el('staffChatMessages').replaceChildren();
    el('staffChatReplyForm').hidden = true;
    el('staffChatComposeForm').hidden = false;
    el('staffChatManage').style.display = 'none';
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
    finally { busy = false; button.disabled = false; disableMessageControls(false); }
  }

  async function sendReply(event) {
    event.preventDefault();
    if (busy || !threadId) return;
    busy = true;
    const id = threadId;
    const input = el('staffChatReply');
    const button = event.currentTarget.querySelector('[type="submit"]');
    disableMessageControls(true);
    try {
      await request(`/admin/messages/${id}/reply`, { method: 'POST', body: JSON.stringify({ message: input.value.trim() }) });
      if (threadId === id) {
        input.value = '';
        await openThread(id);
      } else await loadThreads();
    } catch (error) { notify(error.message, 'danger'); }
    finally { busy = false; button.disabled = false; disableMessageControls(false); }
  }

  window.SmartLibChat = {
    reload() { resetConversation(); return loadThreads(); },
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
