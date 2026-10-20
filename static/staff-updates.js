(function () {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function request(path, options = {}) {
    const response = await fetch('/admin' + path, { ...options, headers: getAuthHeaders() });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'The update could not be saved.');
    return data;
  }
  function date(value) { return new Date(value.endsWith('Z') ? value : value + 'Z').toLocaleString(); }
  window.SmartLibUpdates = {
    async load() {
      const list = document.getElementById('announcementList');
      try {
        await StaffRecords.refresh();
        StaffRecords.control('announcements',list.parentElement,()=>SmartLibUpdates.load());
        const posts = (await request(StaffRecords.viewUrl('/announcements','announcements'))).filter(p=>StaffRecords.visible('announcements',p.id));
        list.innerHTML = posts.length ? posts.map(post => `<article class="card" style="margin-top:16px"><div class="card-body" style="padding:20px"><small>${escape(date(post.created_at))}</small><h3 style="margin:8px 0">${escape(post.title)}</h3><p style="white-space:pre-wrap">${escape(post.body)}</p></div></article>`).join('') : '<p>No announcements published yet.</p>';
        list.querySelectorAll('article').forEach((article,i)=>article.firstElementChild.append(StaffRecords.button('announcements',posts[i].id)));
      } catch (error) { toast(error.message, 'danger'); }
    },
    async extend(reqId) {
      try {
        const loan = await request(`/borrow-requests/${encodeURIComponent(reqId)}`);
        const dialog = document.getElementById('extensionDialog');
        dialog.dataset.reqId = reqId;
        document.getElementById('extensionSummary').textContent = `${loan.book_title} · ${reqId} · Current due: ${date(loan.due_date)}`;
        const next = new Date(Math.max(new Date(loan.due_date + 'Z').getTime(), Date.now()) + 86400000);
        next.setMinutes(next.getMinutes() - next.getTimezoneOffset());
        document.getElementById('extensionDate').value = next.toISOString().slice(0,16);
        document.getElementById('extensionReason').value = '';
        dialog.showModal();
      } catch (error) { toast(error.message, 'danger'); }
    }
  };
  document.addEventListener('DOMContentLoaded', () => {
    const dialog = document.createElement('dialog');
    dialog.id = 'extensionDialog';
    dialog.style.cssText = 'margin:auto;padding:24px;border:1px solid var(--border);border-radius:16px;background:var(--bg-card,#fff);color:var(--text-main,#222);max-width:500px;width:90%';
    dialog.innerHTML = `<form id="extensionForm"><h3>Extend borrowing period</h3><p id="extensionSummary" style="margin:14px 0"></p><label for="extensionDate">New due date (your local time)</label><input type="datetime-local" id="extensionDate" required style="display:block;width:100%;margin:8px 0 16px"><label for="extensionReason">Reason for extension</label><textarea id="extensionReason" required minlength="3" maxlength="500" style="display:block;width:100%;margin:8px 0 16px"></textarea><p style="margin-bottom:16px">The reader will receive an app notification and email confirmation.</p><button class="btn btn-maroon" type="submit">Confirm extension</button> <button class="btn btn-outline" type="button" id="extensionCancel">Cancel</button></form>`;
    document.body.appendChild(dialog);
    document.getElementById('extensionCancel').onclick = () => dialog.close();
    document.getElementById('extensionForm').onsubmit = async event => {
      event.preventDefault();
      const button = event.submitter;
      button.disabled = true;
      try {
        await request(`/borrow-requests/${encodeURIComponent(dialog.dataset.reqId)}/extend`, {method:'POST', body:JSON.stringify({due_date:new Date(document.getElementById('extensionDate').value).toISOString(), reason:document.getElementById('extensionReason').value.trim()})});
        dialog.close(); toast('Loan extended. Reader notifications queued.', 'success');
        if (typeof loadBorrowQueue === 'function') await loadBorrowQueue();
      } catch (error) { toast(error.message, 'danger'); }
      finally { button.disabled = false; }
    };
    document.getElementById('announcementForm').onsubmit = async event => {
      event.preventDefault();
      const button = event.submitter; button.disabled = true;
      try {
        await request('/announcements', {method:'POST', body:JSON.stringify({title:document.getElementById('announcementTitle').value.trim(), body:document.getElementById('announcementBody').value.trim()})});
        event.target.reset(); toast('Announcement published. Reader emails queued.', 'success');
        await window.SmartLibUpdates.load();
      } catch (error) { toast(error.message, 'danger'); }
      finally { button.disabled = false; }
    };
  });
})();
