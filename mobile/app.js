(() => {
  const root = document.getElementById('app');
  const toast = document.getElementById('toast');
  const state = { token: localStorage.getItem('smartlib_token') || sessionStorage.getItem('smartlib_token'), user: null, tab: 'home', authMode: 'login', books: [], borrowings: [], digitalId: null, search: '', category: 'All', installPrompt: null };
  let toastTimer;
  let idleTimer;
  let lastActivityAt = Date.now();
  const escapeHtml = (value = '') => String(value).replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const initials = (name = '') => name.trim().split(/\s+/).slice(0, 2).map((part) => part[0] || '').join('').toUpperCase() || 'S';

  async function api(path, options = {}) {
    const headers = { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}), ...options.headers };
    const response = await fetch(path, { ...options, headers });
    const data = response.status === 204 ? null : await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 401) { state.token = null; state.user = null; localStorage.removeItem('smartlib_token'); sessionStorage.removeItem('smartlib_token'); renderAuth(); }
      throw new Error(data?.detail || 'Something went wrong. Please try again.');
    }
    return data;
  }
  function notify(message) { toast.textContent = message; toast.classList.add('show'); clearTimeout(toastTimer); toastTimer = setTimeout(() => toast.classList.remove('show'), 2800); }
  function resetIdleTimer() {
    if (!state.token) return;
    lastActivityAt = Date.now(); clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      api('/auth/logout', { method: 'POST' }).catch(() => {});
      state.token = null; state.user = null; state.digitalId = null;
      localStorage.removeItem('smartlib_token'); sessionStorage.removeItem('smartlib_token'); renderAuth();
    }, 15 * 60 * 1000);
  }
  function authError(message) { const node = document.getElementById('auth-error'); if (node) { node.textContent = message; node.hidden = false; } }

  function renderAuth() {
    const register = state.authMode === 'register';
    root.innerHTML = `<section class="auth-page fade-in"><div class="auth-art"><div class="brand"><img class="brand-mark-img small" src="/mobile-assets/evsu-smartlib-logo.jpg" alt="EVSU-OC SmartLib logo"><span>EVSU-OC SmartLib</span></div><div><h1>Your campus library, wherever you are.</h1><p>Find your next read, request a copy, and keep your library card close at hand.</p></div><div class="art-bottom">Eastern Visayas State University · Ormoc City Campus</div></div><form class="auth-card" id="auth-form"><button class="back-link" type="button" data-action="public-home">← SmartLib home</button><h2>${register ? 'Create your account' : 'Welcome back'}</h2><p class="subheading">${register ? 'Join your EVSU library community.' : 'Sign in with your institutional account.'}</p><div id="auth-error" class="auth-error" hidden></div>${register ? `<div class="field-grid"><div class="field"><label for="first-name">First name</label><input id="first-name" name="first_name" autocomplete="given-name" required></div><div class="field"><label for="last-name">Last name</label><input id="last-name" name="last_name" autocomplete="family-name" required></div></div><div class="field"><label for="reg-username">Student or staff ID</label><input id="reg-username" name="username" placeholder="Your institutional ID" autocomplete="username" required></div><div class="field"><label for="email">EVSU email</label><input id="email" name="email" type="email" autocomplete="email" required></div><div class="field-grid"><div class="field"><label for="account-type">Account type</label><select id="account-type" name="account_type"><option value="student">Student</option><option value="faculty">Faculty</option></select></div><div class="field"><label for="department">Department</label><input id="department" name="department" placeholder="Optional"></div></div><div class="field"><label for="reg-password">Password</label><div class="password-input-wrap"><input id="reg-password" name="password" type="password" autocomplete="new-password" minlength="8" required><button class="password-toggle" type="button" data-action="toggle-password" data-password-target="reg-password" aria-label="Show password"><b>Aa</b><span>Show</span></button></div></div><div class="field"><label for="confirm-password">Confirm password</label><div class="password-input-wrap"><input id="confirm-password" name="confirm_password" type="password" autocomplete="new-password" minlength="8" required><button class="password-toggle" type="button" data-action="toggle-password" data-password-target="confirm-password" aria-label="Show password"><b>Aa</b><span>Show</span></button></div></div>` : `<div class="field"><label for="username">Institutional ID or email</label><input id="username" name="username" placeholder="Enter your ID or email" autocomplete="username" required></div><div class="field"><label for="password">Password</label><div class="password-input-wrap"><input id="password" name="password" type="password" autocomplete="current-password" required><button class="password-toggle" type="button" data-action="toggle-password" data-password-target="password" aria-label="Show password"><b>Aa</b><span>Show</span></button></div></div><label class="remember-option"><input type="checkbox" name="remember_me"> Remember me</label><button class="text-button" type="button" data-action="forgot-password">Forgot password?</button>`}<button class="primary wide" type="submit">${register ? 'Create account' : 'Sign in'}</button><p class="switch-auth">${register ? 'Already have an account?' : 'New to SmartLib?'} <button type="button" data-action="toggle-auth">${register ? 'Sign in' : 'Create account'}</button></p></form></section>`;
  }

  function shell(content, active = state.tab) {
    const showInstall = Boolean(state.installPrompt);
    root.innerHTML = `<header class="topbar"><div class="top-brand"><img class="brand-mark-img" src="/mobile-assets/evsu-smartlib-logo.jpg" alt="EVSU-OC SmartLib logo"><span>EVSU-OC SmartLib</span></div><div class="top-actions">${showInstall ? '<button class="install-button" data-action="install">＋ Install</button>' : ''}<button class="avatar" aria-label="Open profile" data-tab="profile">${escapeHtml(initials(state.user?.full_name))}</button></div></header><main class="page fade-in">${content}</main><nav class="bottom-nav" aria-label="Main navigation"><div class="nav-inner">${[['home','⌂','Home'],['catalog','⌕','Explore'],['borrowings','▤','My books'],['digital-id','▣','Library ID']].map(([id,icon,label]) => `<button class="nav-item ${active === id ? 'active' : ''}" data-tab="${id}"><span class="nav-icon">${icon}</span><span>${label}</span></button>`).join('')}</div></nav>`;
  }

  function bookCard(book, index = 0) {
    const available = Number(book.available_copies) > 0;
    return `<article class="book-card"><div class="book-cover tone-${index % 4}">${escapeHtml((book.title || 'B').trim().slice(0, 1).toUpperCase())}</div><div class="book-info"><h3>${escapeHtml(book.title)}</h3><p>${escapeHtml(book.author)}</p><span class="book-meta">${escapeHtml(book.category)}${book.year_published ? ` · ${escapeHtml(book.year_published)}` : ''}</span><span class="status ${available ? '' : 'unavailable'}">${available ? `${book.available_copies} available` : 'Currently unavailable'}</span>${available ? `<button class="secondary" data-borrow="${escapeHtml(book.accession_no)}" data-title="${escapeHtml(book.title)}">Request a copy</button>` : ''}</div></article>`;
  }

  function homeView() {
    const firstName = (state.user?.full_name || 'reader').trim().split(/\s+/)[0];
    const available = state.books.filter((book) => book.available_copies > 0).length;
    const pending = state.borrowings.filter((item) => item.status === 'PENDING').length;
    return `<section class="greeting"><div><span class="eyebrow">EVSU library · main campus</span><h1>Hello, ${escapeHtml(firstName)} 👋</h1><p>Ready to find something worth reading?</p></div><span class="pill">${escapeHtml(state.user?.role || 'member')}</span></section><section class="hero-card"><div class="hero-copy"><span class="eyebrow">A world of knowledge</span><h2>Your next chapter starts here.</h2><p>${available} books ready to discover in the catalog</p></div><button class="primary" data-tab="catalog">Explore books <span aria-hidden="true">→</span></button></section><div class="section-head"><h2>Quick access</h2></div><section class="quick-grid"><button class="quick-card" data-tab="catalog"><span class="quick-icon">⌕</span><b>Find a book</b><span>Search the catalog</span></button><button class="quick-card" data-tab="borrowings"><span class="quick-icon">▤</span><b>My requests</b><span>${pending ? `${pending} awaiting review` : 'Check your loans'}</span></button><button class="quick-card" data-tab="digital-id"><span class="quick-icon">▣</span><b>Library ID</b><span>Your digital membership</span></button></section><div class="section-head"><h2>Recently added</h2><button class="text-button" data-tab="catalog">View catalog →</button></div><section class="book-grid">${state.books.length ? state.books.slice(0, 4).map(bookCard).join('') : `<div class="empty" style="grid-column:1/-1"><span class="empty-icon">⌕</span><b>Catalog is waiting for you</b>Search to discover the collection.</div>`}</section>`;
  }

  function catalogView() {
    const categories = ['All', ...new Set(state.books.map((book) => book.category).filter(Boolean))];
    const filtered = state.books.filter((book) => (state.category === 'All' || book.category === state.category) && (!state.search || `${book.title} ${book.author} ${book.category} ${book.accession_no}`.toLowerCase().includes(state.search.toLowerCase())));
    return `<section class="greeting"><div><span class="eyebrow">EVSU library collection</span><h1>Explore books</h1><p>Search titles, authors, and subjects.</p></div></section><div class="search-row"><span class="search-icon">⌕</span><input class="searchbox with-icon" id="book-search" type="search" placeholder="What would you like to read?" value="${escapeHtml(state.search)}" aria-label="Search books"></div><div class="chip-row">${categories.map((category) => `<button class="chip ${state.category === category ? 'active' : ''}" data-category="${escapeHtml(category)}">${escapeHtml(category)}</button>`).join('')}</div><div class="section-head"><h2>${state.search ? 'Search results' : 'The collection'}</h2><span class="helper">${filtered.length} ${filtered.length === 1 ? 'book' : 'books'}</span></div><section class="book-grid">${filtered.length ? filtered.map(bookCard).join('') : `<div class="empty" style="grid-column:1/-1"><span class="empty-icon">⌕</span><b>No books found</b>Try another title, author, or subject.</div>`}</section>`;
  }

  function borrowingView() {
    const entries = [...state.borrowings].sort((a, b) => new Date(b.request_date) - new Date(a.request_date));
    return `<section class="greeting"><div><span class="eyebrow">Your library activity</span><h1>My books</h1><p>Borrow requests and current loans in one place.</p></div></section><div class="section-head"><h2>Borrowing history</h2><button class="text-button" data-action="refresh-borrowings">Refresh</button></div><section class="list-stack">${entries.length ? entries.map((entry) => { const status = (entry.status || 'PENDING').toLowerCase(); return `<article class="list-card"><span class="quick-icon" style="margin:0;flex:none">▤</span><div class="grow"><h3>${escapeHtml(entry.book_title || `Book ${entry.book_id}`)}</h3><p>${escapeHtml(entry.req_id)} · Pickup ${escapeHtml(entry.pickup_date || 'To be arranged')}</p></div><span class="status ${status}">${escapeHtml(entry.status || 'PENDING').replaceAll('_', ' ')}</span></article>`; }).join('') : `<div class="empty"><span class="empty-icon">▤</span><b>No requests yet</b>Your requested books will appear here.<br><button class="text-button" data-tab="catalog">Browse the catalog →</button></div>`}</section>`;
  }

  function idView() {
    const data = state.digitalId;
    if (!data) return `<div class="empty"><span class="empty-icon">▣</span><b>Loading your library ID</b>Please wait a moment.</div>`;
    return `<section class="greeting"><div><span class="eyebrow">Always with you</span><h1>Library ID</h1><p>Show your membership details at the library desk.</p></div></section><article class="digital-card"><div class="digital-head"><div class="brand"><img class="brand-mark-img small" src="/mobile-assets/evsu-smartlib-logo.jpg" alt="EVSU-OC SmartLib logo"><span>EVSU-OC SmartLib</span></div><small>MEMBER CARD</small></div><div class="digital-name"><small>Card holder</small><h2>${escapeHtml(data.full_name)}</h2></div><div class="digital-grid"><div><small>Institutional ID</small><b>${escapeHtml(data.patron_id)}</b></div><div><small>Membership</small><b>${escapeHtml(data.role)}</b></div><div><small>Department</small><b>${escapeHtml(data.department || 'EVSU Ormoc City Campus')}</b></div><div><small>Active loans</small><b>${data.active_borrows_count}</b></div></div><div class="qr-display" id="qr-display" aria-label="Digital library ID QR code"></div><div class="id-payload"><span>${escapeHtml(data.qr_payload)}</span><button data-action="copy-id">Copy ID</button></div></article><p class="helper" style="text-align:center">${data.is_active ? 'Your membership is active.' : 'Please contact the library desk about your account.'} Card updated ${escapeHtml(new Date(data.issued_at.replace(' ', 'T') + 'Z').toLocaleDateString())}.</p><div style="text-align:center;margin-top:18px"><button class="secondary" data-action="refresh-id">Refresh card</button></div>`;
  }

  function profileView() {
    return `<section class="greeting"><div><span class="eyebrow">Your account</span><h1>Profile</h1><p>Library membership and account details.</p></div></section><article class="profile-card"><div class="profile-row"><span class="profile-avatar">${escapeHtml(initials(state.user?.full_name))}</span><div><h2>${escapeHtml(state.user?.full_name)}</h2><p>${escapeHtml(state.user?.email)}</p></div></div><div class="section-head"><span class="helper">Institutional ID</span><b>${escapeHtml(state.user?.username)}</b></div><div class="section-head"><span class="helper">Account type</span><span class="pill">${escapeHtml(state.user?.role)}</span></div><div class="section-head"><span class="helper">Department</span><b>${escapeHtml(state.user?.department || 'EVSU Ormoc City Campus')}</b></div></article><div class="section-head"><h2>Suggest a book</h2></div><form id="suggest-form" class="profile-card"><div class="field"><label for="suggest-title">Book title</label><input id="suggest-title" name="title" required></div><div class="field"><label for="suggest-author">Author</label><input id="suggest-author" name="author" required></div><div class="field"><label for="suggest-isbn">Publisher or ISBN <span class="helper">(optional)</span></label><input id="suggest-isbn" name="publisher_isbn"></div><div class="field"><label for="suggest-why">Why should the library add this book?</label><textarea id="suggest-why" name="justification" required></textarea></div><button class="primary wide">Send suggestion</button></form><button class="secondary wide" data-action="logout" style="margin-top:6px">Sign out</button>`;
  }

  async function loadBooks(search = '') {
    const params = new URLSearchParams({ limit: '100' });
    if (search) params.set('search', search);
    state.books = await api(`/mobile/books?${params}`);
  }
  async function loadBorrowings() { state.borrowings = await api('/mobile/my-borrowings'); }
  async function loadDigitalId() { state.digitalId = await api('/mobile/profile/digital-id'); }

  async function renderTab(tab = 'home') {
    state.tab = tab;
    if (tab === 'profile') { shell(profileView(), tab); return; }
    try {
      if (tab === 'home' || tab === 'catalog') await loadBooks(state.search);
      if (tab === 'home') await loadBorrowings();
      if (tab === 'borrowings') await loadBorrowings();
      if (tab === 'digital-id') await loadDigitalId();
    } catch (error) { notify(error.message); }
    const view = { home: homeView, catalog: catalogView, borrowings: borrowingView, 'digital-id': idView }[tab] || homeView;
    shell(view(), tab);
    if (tab === 'digital-id' && state.digitalId?.qr_payload) {
      const qrTarget = document.getElementById('qr-display');
      if (window.QRCode && qrTarget) new QRCode(qrTarget, { text: state.digitalId.qr_payload, width: 128, height: 128, colorDark: '#241d20', colorLight: '#ffffff', correctLevel: QRCode.CorrectLevel.M });
      else if (qrTarget) qrTarget.innerHTML = '<span class="qr-unavailable">QR needs an internet connection. You can copy the ID below.</span>';
    }
    if (tab === 'catalog') document.getElementById('book-search')?.focus({ preventScroll: true });
  }

  async function startApp() {
    try { state.user = await api('/auth/me'); }
    catch { state.token = null; state.user = null; localStorage.removeItem('smartlib_token'); }
    if (!state.user) { renderAuth(); return; }
    resetIdleTimer();
    await renderTab(state.tab);
  }

  root.addEventListener('click', async (event) => {
    resetIdleTimer();
    const tabButton = event.target.closest('[data-tab]');
    if (tabButton) { event.preventDefault(); await renderTab(tabButton.dataset.tab); return; }
    const categoryButton = event.target.closest('[data-category]');
    if (categoryButton) { state.category = categoryButton.dataset.category; shell(catalogView(), 'catalog'); return; }
    const borrowButton = event.target.closest('[data-borrow]');
    if (borrowButton) {
      const pickup = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
      if (!confirm(`Request “${borrowButton.dataset.title}” for pickup on ${pickup}?`)) return;
      try { await api('/mobile/borrow', { method: 'POST', body: JSON.stringify({ accession_no: borrowButton.dataset.borrow, pickup_date: pickup }) }); notify('Borrow request sent to the library.'); await renderTab('borrowings'); }
      catch (error) { notify(error.message); }
      return;
    }
    const action = event.target.closest('[data-action]')?.dataset.action;
    if (action === 'toggle-auth') { state.authMode = state.authMode === 'login' ? 'register' : 'login'; renderAuth(); }
    if (action === 'toggle-password') {
      const toggle = event.target.closest('[data-action="toggle-password"]');
      const input = document.getElementById(toggle?.dataset.passwordTarget);
      if (input && toggle) {
        input.type = input.type === 'password' ? 'text' : 'password';
        const visible = input.type === 'text';
        toggle.setAttribute('aria-label', visible ? 'Hide password' : 'Show password');
        toggle.querySelector('span').textContent = visible ? 'Hide' : 'Show';
      }
    }
    if (action === 'forgot-password') {
      const email = prompt('Enter the email address on your SmartLib account:');
      if (email?.trim()) {
        try { const result = await api('/auth/forgot-password', { method: 'POST', body: JSON.stringify({ email: email.trim() }) }); notify(result.message); }
        catch (error) { notify(error.message); }
      }
    }
    if (action === 'public-home') { window.location.assign('/'); }
    if (action === 'logout') { api('/auth/logout', { method: 'POST' }).catch(() => {}); state.token = null; state.user = null; state.digitalId = null; localStorage.removeItem('smartlib_token'); sessionStorage.removeItem('smartlib_token'); state.authMode = 'login'; renderAuth(); }
    if (action === 'refresh-borrowings') await renderTab('borrowings');
    if (action === 'refresh-id') { await renderTab('digital-id'); notify('Library ID refreshed.'); }
    if (action === 'copy-id' && state.digitalId?.qr_payload) { try { await navigator.clipboard.writeText(state.digitalId.qr_payload); notify('Digital ID copied.'); } catch { notify(state.digitalId.qr_payload); } }
    if (action === 'install' && state.installPrompt) { state.installPrompt.prompt(); await state.installPrompt.userChoice; state.installPrompt = null; await renderTab(state.tab); }
  });

  root.addEventListener('submit', async (event) => {
    resetIdleTimer();
    event.preventDefault();
    const form = event.target;
    if (form.id === 'auth-form') {
      const formData = Object.fromEntries(new FormData(form).entries());
      const button = form.querySelector('button[type="submit"]'); button.disabled = true;
      try {
        if (state.authMode === 'register') {
          if (formData.password !== formData.confirm_password) throw new Error('Passwords do not match.');
          await api('/auth/register', { method: 'POST', body: JSON.stringify(formData) });
          state.authMode = 'login'; renderAuth();
          document.getElementById('username').value = formData.username;
          notify('Check your email for a verification link before signing in.');
        } else {
          const data = await api('/auth/login', { method: 'POST', body: JSON.stringify({ username: formData.username, password: formData.password, remember_me: formData.remember_me === 'on' }) });
          state.token = data.access_token;
          localStorage.removeItem('smartlib_token'); sessionStorage.removeItem('smartlib_token');
          (formData.remember_me === 'on' ? localStorage : sessionStorage).setItem('smartlib_token', data.access_token);
          state.tab = 'home'; await startApp();
        }
      } catch (error) { authError(error.message); button.disabled = false; }
    }
    if (form.id === 'suggest-form') {
      const button = form.querySelector('button[type="submit"]'); button.disabled = true;
      try { await api('/mobile/acquisitions', { method: 'POST', body: JSON.stringify(Object.fromEntries(new FormData(form).entries())) }); notify('Thanks! Your book suggestion was sent.'); form.reset(); }
      catch (error) { notify(error.message); }
      finally { button.disabled = false; }
    }
  });

  let searchTimer;
  root.addEventListener('input', (event) => {
    if (event.target.id !== 'book-search') return;
    state.search = event.target.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
      try { await loadBooks(state.search); const view = document.querySelector('.page'); if (view && state.tab === 'catalog') { const scroll = window.scrollY; shell(catalogView(), 'catalog'); window.scrollTo(0, scroll); const input = document.getElementById('book-search'); input?.focus(); input?.setSelectionRange(state.search.length, state.search.length); } }
      catch (error) { notify(error.message); }
    }, 220);
  });

  window.addEventListener('beforeinstallprompt', (event) => { event.preventDefault(); state.installPrompt = event; if (state.user) renderTab(state.tab); });
  ['pointerdown','keydown','touchstart'].forEach((eventName) => document.addEventListener(eventName, resetIdleTimer, { passive: true }));
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/service-worker.js').catch(() => {});
  startApp();
})();
