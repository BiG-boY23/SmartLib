/* Personal in-app activity inbox shared by the Admin and Librarian dashboards. */
(() => {
  let root, trigger, panel, list, badge, status, markAll;
  let timer = null;
  let requestVersion = 0;
  let busy = false;
  const dateLabel = value => value ? new Date(/(?:Z|[+-]\d\d:\d\d)$/.test(value) ? value : value + 'Z').toLocaleString() : '';

  async function api(path = '', options = {}) {
    const response = await fetch('/admin/notifications' + path, {...options, headers: getAuthHeaders()});
    if (!response.ok) {
      const error = new Error('Activity notifications could not be loaded.');
      error.status = response.status;
      throw error;
    }
    return response.json();
  }
  function message(text) { list.replaceChildren(); const p = document.createElement('p'); p.className = 'staff-notifications-empty'; p.textContent = text; list.append(p); }
  function render(data) {
    const unread = Number(data.unread_count) || 0;
    badge.textContent = unread > 99 ? '99+' : String(unread);
    badge.hidden = unread === 0;
    trigger.setAttribute('aria-label', `Activity notifications, ${unread} unread`);
    status.textContent = unread ? `${unread} unread` : 'You are up to date';
    markAll.disabled = unread === 0 || busy;
    list.replaceChildren();
    if (!data.items?.length) { message('Borrowing, returns, loan extensions, and book suggestions will appear here.'); return; }
    for (const item of data.items) {
      const row = document.createElement('article'); row.className = 'staff-notification' + (item.read ? '' : ' is-unread');
      const title = document.createElement('h4'); title.textContent = item.title;
      const body = document.createElement('p'); body.textContent = item.body;
      const footer = document.createElement('div'); footer.className = 'staff-notification-footer';
      const time = document.createElement('time'); time.dateTime = item.created_at; time.textContent = dateLabel(item.created_at);
      footer.append(time);
      if (!item.read) {
        const button = document.createElement('button'); button.type = 'button'; button.textContent = 'Mark as read';
        button.setAttribute('aria-label', `Mark ${item.title} as read`);
        button.onclick = async () => {
          button.disabled = true;
          try { await api(`/${item.id}/read`, {method: 'POST'}); await refresh(); }
          catch (_) { status.textContent = 'Could not mark this notification as read. Try again.'; button.disabled = false; }
        };
        footer.append(button);
      }
      row.append(title, body, footer); list.append(row);
    }
  }
  async function refresh() {
    if (!root || document.hidden) return;
    const version = ++requestVersion;
    try {
      const data = await api();
      if (version === requestVersion) render(data);
    } catch (error) {
      if (version !== requestVersion) return;
      if (error.status === 401 || error.status === 403) { stop(); root.hidden = true; message('Sign in to view notifications.'); }
      else { status.textContent = 'Could not refresh. Open again to retry.'; }
    }
  }
  function close() { panel.hidden = true; trigger.setAttribute('aria-expanded', 'false'); }
  function stop() { if (timer) clearInterval(timer); timer = null; requestVersion++; }
  function mount() {
    if (root || typeof getAuthHeaders !== 'function') return;
    const topbar = document.querySelector('.app-topbar');
    if (!topbar) return;
    root = document.createElement('div'); root.className = 'staff-notifications';
    root.innerHTML = `<button type="button" class="staff-notifications-trigger" id="staffNotificationsTrigger" aria-label="Activity notifications" aria-expanded="false" aria-controls="staffNotificationsPanel"><svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9Z"/><path d="M10 21h4"/></svg><span class="staff-notifications-badge" hidden></span></button><section class="staff-notifications-panel" id="staffNotificationsPanel" aria-label="Activity notifications" hidden><div class="staff-notifications-head"><div><h3>Library activity</h3><p class="staff-notifications-status" role="status">Loading activity...</p></div><button type="button" class="staff-notifications-close" aria-label="Close notifications">&times;</button></div><div class="staff-notifications-toolbar"><span>Updates for your staff account</span><button type="button" class="staff-notifications-read-all">Mark all as read</button></div><div class="staff-notifications-list"></div></section>`;
    const account = topbar.querySelector('.account-btn-wrap');
    if (account) topbar.insertBefore(root, account); else topbar.append(root);
    trigger = root.querySelector('.staff-notifications-trigger'); panel = root.querySelector('.staff-notifications-panel');
    list = root.querySelector('.staff-notifications-list'); badge = root.querySelector('.staff-notifications-badge');
    status = root.querySelector('.staff-notifications-status'); markAll = root.querySelector('.staff-notifications-read-all');
    trigger.onclick = () => { panel.hidden = !panel.hidden; trigger.setAttribute('aria-expanded', String(!panel.hidden)); if (!panel.hidden) refresh(); };
    root.querySelector('.staff-notifications-close').onclick = () => { close(); trigger.focus(); };
    markAll.onclick = async () => {
      if (busy) return;
      busy = true; markAll.disabled = true;
      try { await api('/read-all', {method: 'POST'}); await refresh(); }
      catch (_) { status.textContent = 'Could not mark notifications as read. Try again.'; }
      finally { busy = false; markAll.disabled = badge.hidden; }
    };
    document.addEventListener('click', event => { if (!root.contains(event.target)) close(); });
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && !panel.hidden) { close(); trigger.focus(); } });
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
    refresh(); timer = setInterval(refresh, 30000);
  }
  window.SmartLibNotifications = {mount, refresh, stop};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount); else mount();
})();
