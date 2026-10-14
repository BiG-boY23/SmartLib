(() => {
  const root = document.getElementById('app');
  const toast = document.getElementById('toast');
  const state = { token: localStorage.getItem('smartlib_token') || sessionStorage.getItem('smartlib_token'), user: null, tab: localStorage.getItem('smartlib_active_tab') || 'home', authMode: 'login', signupOptions: { departments: [], courses: [] }, books: [], borrowings: [], digitalId: null, search: '', category: 'All', installPrompt: null, editingProfile: false };
  let pullStart = null;
  let pulling = false;
  let pullRefreshing = false;
  root.addEventListener('touchstart', event => {
    if (!state.token || window.scrollY > 0 || event.touches.length !== 1 || event.target.closest('input,textarea,select,button')) return;
    pullStart = {x:event.touches[0].clientX,y:event.touches[0].clientY};
    pulling = false;
  }, {passive:true});
  root.addEventListener('touchmove', event => {
    if (!pullStart) return;
    const distance = event.touches[0].clientY - pullStart.y;
    if (Math.abs(event.touches[0].clientX - pullStart.x) > 40) { pullStart = null; return; }
    pulling = distance > 80;
    if (distance > 10) event.preventDefault();
    if (pulling) notify('Release to refresh');
  }, {passive:false});
  root.addEventListener('touchend', async () => {
    const refresh = pulling; pullStart = null; pulling = false;
    if (!refresh || pullRefreshing) return;
    pullRefreshing = true; notify('Refreshing...');
    try { await renderTab(state.tab); notify('Updated.'); }
    finally { pullRefreshing = false; }
  });
  root.addEventListener('touchcancel', () => { pullStart = null; pulling = false; });
  let toastTimer;
  let idleTimer;
  let lastActivityAt = Date.now();
  const escapeHtml = (value = '') => String(value).replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const initials = (name = '') => name.trim().split(/\s+/).slice(0, 2).map((part) => part[0] || '').join('').toUpperCase() || 'S';

  async function api(path, options = {}) {
    const headers = { ...(typeof options.body === 'string' ? { 'Content-Type': 'application/json' } : {}), ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}), ...options.headers };
    const response = await fetch(path, { ...options, headers });
    const data = response.status === 204 ? null : await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 401) { state.token = null; state.user = null; localStorage.removeItem('smartlib_token'); sessionStorage.removeItem('smartlib_token'); renderAuth(); }
      const detail = Array.isArray(data?.detail) ? data.detail.map((item) => item.msg).filter(Boolean).join('; ') : data?.detail;
      throw new Error(detail || 'Something went wrong. Please try again.');
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
    const verify = state.authMode === 'register-otp';
    const departments = state.signupOptions.departments || [];
    const courses = state.signupOptions.courses || [];
    const nameFields = `<div class="field"><label for="first-name">First name</label><input id="first-name" name="first_name" autocomplete="given-name" required maxlength="80"></div><div class="field"><label for="middle-name">Middle name (optional)</label><input id="middle-name" name="middle_name" autocomplete="additional-name" maxlength="80"></div><div class="field"><label for="last-name">Last name</label><input id="last-name" name="last_name" autocomplete="family-name" required maxlength="80"></div>`;
    const registrationFields = `${nameFields}<div class="field"><label for="reg-username">Student or staff ID</label><input id="reg-username" name="username" autocomplete="username" required></div><div class="field"><label for="email">EVSU email</label><input id="email" name="email" type="email" autocomplete="email" required></div><div class="field"><label for="account-type">Account type</label><select id="account-type" name="account_type"><option value="student">Student</option><option value="faculty">Faculty</option></select></div><div class="field"><label for="department">Department</label><select id="department" name="department_id" required><option value="">Choose a department</option>${departments.map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join('')}</select></div><div class="field"><label for="course">Course</label><select id="course" name="course_id" required disabled><option value="">Select a department first</option></select></div><div class="field"><label for="reg-password">Password</label><div class="password-input-wrap"><input id="reg-password" name="password" type="password" autocomplete="new-password" minlength="8" maxlength="12" required><button class="password-toggle" type="button" data-action="toggle-password" data-password-target="reg-password" aria-label="Show password"><b>Aa</b><span>Show</span></button></div></div><div class="field"><label for="confirm-password">Confirm password</label><div class="password-input-wrap"><input id="confirm-password" name="confirm_password" type="password" autocomplete="new-password" minlength="8" maxlength="12" required><button class="password-toggle" type="button" data-action="toggle-password" data-password-target="confirm-password" aria-label="Show password"><b>Aa</b><span>Show</span></button></div></div>`;
    const loginFields = `<div class="field"><label for="username">Institutional ID or email</label><input id="username" name="username" autocomplete="username" required></div><div class="field"><label for="password">Password</label><div class="password-input-wrap"><input id="password" name="password" type="password" autocomplete="current-password" required><button class="password-toggle" type="button" data-action="toggle-password" data-password-target="password" aria-label="Show password"><b>Aa</b><span>Show</span></button></div></div><label class="remember-option"><input type="checkbox" name="remember_me"> Remember me</label><button class="text-button" type="button" data-action="forgot-password">Forgot password?</button>`;
    root.innerHTML = `<section class="auth-page fade-in"><div class="auth-art"><div class="brand"><img class="brand-mark-img small" src="/mobile-assets/evsu-smartlib-logo.jpg" alt="EVSU-OC SmartLib logo"><span>EVSU-OC SmartLib</span></div><div><h1>Your campus library, wherever you are.</h1><p>Find your next read, request a copy, and keep your library card close at hand.</p></div><div class="art-bottom">Eastern Visayas State University &middot; Ormoc City Campus</div></div><form class="auth-card" id="auth-form"><button class="back-link" type="button" data-action="public-home">&larr; SmartLib home</button><h2>${verify ? 'Verify your email' : register ? 'Create your account' : 'Welcome back'}</h2><p class="subheading">${verify ? `Enter the 6-digit code sent to ${escapeHtml(state.pendingRegistration?.email || 'your email')}.` : register ? 'Join your EVSU library community.' : 'Sign in with your institutional account.'}</p><div id="auth-error" class="auth-error" hidden></div>${verify ? '<div class="field"><label for="otp">Email code</label><input id="otp" name="otp" inputmode="numeric" pattern="[0-9]{6}" maxlength="6" required></div>' : register ? registrationFields : loginFields}<button class="primary wide" type="submit">${verify ? 'Verify and create account' : register ? 'Send email code' : 'Sign in'}</button><p class="switch-auth"><button type="button" data-action="toggle-auth">${verify || register ? 'Back to sign in' : 'Create account'}</button></p></form></section>`;
  }

  function shell(content, active = state.tab) {
    const showInstall = Boolean(state.installPrompt);
    const tabs = [['home', '\u2302', 'Home'], ['catalog', '\u2315', 'Explore'], ['borrowings', '\u25a4', 'My books'], ['digital-id', '\u25a3', 'Library ID']];
    const activeLabel = tabs.find(([id]) => id === active)?.[2] || 'Profile';
    const avatar = state.user?.profile_picture_url ? `<img src="${escapeHtml(state.user.profile_picture_url)}" alt="" class="top-avatar-photo">` : escapeHtml(initials(state.user?.full_name));
    root.innerHTML = `<header class="topbar"><div class="top-brand"><img class="brand-mark-img" src="/mobile-assets/evsu-smartlib-logo.jpg" alt="EVSU-OC SmartLib logo"><span>EVSU-OC SmartLib</span></div><nav class="top-links" aria-label="Page navigation">${tabs.map(([id,,label]) => `<button class="top-link ${active === id ? 'active' : ''}" data-tab="${id}" ${active === id ? 'aria-current="page"' : ''}>${label}</button>`).join('')}</nav><div class="top-actions"><span class="top-current" aria-live="polite">${activeLabel}</span>${showInstall ? '<button class="install-button" data-action="install">&#43; <span class="install-label">Install</span></button>' : ''}<button class="avatar ${active === 'profile' ? 'active' : ''}" aria-label="Open profile" ${active === 'profile' ? 'aria-current="page"' : ''} data-tab="profile">${avatar}</button></div></header><main class="page fade-in">${content}</main><nav class="bottom-nav" aria-label="Main navigation"><div class="nav-inner">${tabs.map(([id,icon,label]) => `<button class="nav-item ${active === id ? 'active' : ''}" data-tab="${id}" ${active === id ? 'aria-current="page"' : ''}><span class="nav-icon">${icon}</span><span>${label}</span></button>`).join('')}</div></nav>`;
  }

  function bookCard(book, index = 0) {
    const available = Number(book.available_copies) > 0;
    return `<article class="book-card"><div class="book-cover tone-${index % 4}"><span class="book-cover-fallback">${escapeHtml((book.title || 'B').trim().slice(0, 1).toUpperCase())}</span>${book.cover_url ? `<img class="book-cover-image" src="${escapeHtml(book.cover_url)}" alt="Cover of ${escapeHtml(book.title)}" loading="lazy" onerror="this.remove()">` : ''}</div><div class="book-info"><h3>${escapeHtml(book.title)}</h3><p>${escapeHtml(book.author)}</p><span class="book-meta">${escapeHtml(book.category)}${book.year_published ? ` · ${escapeHtml(book.year_published)}` : ''}</span><span class="status ${available ? '' : 'unavailable'}">${available ? `${book.available_copies} available` : 'Currently unavailable'}</span>${available ? `<button class="secondary" data-borrow="${escapeHtml(book.accession_no)}" data-title="${escapeHtml(book.title)}">Request a copy</button>` : ''}</div></article>`;
  }

  function homeView() {
    const active = state.borrowings.filter((item) => ['CHECKED_OUT', 'OVERDUE'].includes(item.status)).length;
    const available = state.books.filter((book) => book.available_copies > 0).length;
    const pending = state.borrowings.filter((item) => item.status === 'PENDING').length;
    return `<section class="greeting"><div><span class="eyebrow">EVSU · Ormoc City Campus</span><h1>Library overview</h1><p>Your reading activity, at a glance.</p></div><span class="pill">${escapeHtml(state.user?.role || 'member')}</span></section><section class="overview-grid" aria-label="Library activity">${[[available, 'Available titles', 'catalog'], [active, 'Active loans', 'borrowings'], [pending, 'Pending requests', 'borrowings']].map(([value, label, target]) => `<button class="overview-card" data-tab="${target}"><strong>${value}</strong><span>${label}</span></button>`).join('')}</section><section class="hero-card"><div class="hero-copy"><span class="eyebrow">A world of knowledge</span><h2>Your next chapter starts here.</h2><p>${available} books ready to discover in the catalog</p></div><button class="primary" data-tab="catalog">Explore books <span aria-hidden="true">→</span></button></section><div class="section-head"><h2>Quick access</h2></div><section class="quick-grid"><button class="quick-card" data-tab="catalog"><span class="quick-icon">⌕</span><b>Find a book</b><span>Search the catalog</span></button><button class="quick-card" data-tab="borrowings"><span class="quick-icon">▤</span><b>My requests</b><span>${pending ? `${pending} awaiting review` : 'Check your loans'}</span></button><button class="quick-card" data-tab="digital-id"><span class="quick-icon">▣</span><b>Library ID</b><span>Your digital membership</span></button></section><div class="section-head"><h2>Recently added</h2><button class="text-button" data-tab="catalog">View catalog →</button></div><section class="book-grid">${state.books.length ? state.books.slice(0, 4).map(bookCard).join('') : `<div class="empty" style="grid-column:1/-1"><span class="empty-icon">⌕</span><b>Catalog is waiting for you</b>Search to discover the collection.</div>`}</section>`;
  }

  function catalogView() {
    const categories = ['All', ...new Set(state.books.map((book) => book.category).filter(Boolean))];
    const filtered = state.books.filter((book) => (state.category === 'All' || book.category === state.category) && (!state.search || `${book.title} ${book.author} ${book.category} ${book.accession_no}`.toLowerCase().includes(state.search.toLowerCase())));
    return `<section class="greeting"><div><span class="eyebrow">EVSU library collection</span><h1>Explore books</h1><p>Search titles, authors, and subjects.</p></div></section><div class="search-row"><span class="search-icon">⌕</span><input class="searchbox with-icon" id="book-search" type="search" placeholder="What would you like to read?" value="${escapeHtml(state.search)}" aria-label="Search books"></div><div class="chip-row">${categories.map((category) => `<button class="chip ${state.category === category ? 'active' : ''}" data-category="${escapeHtml(category)}">${escapeHtml(category)}</button>`).join('')}</div><div class="section-head"><h2>${state.search ? 'Search results' : 'The collection'}</h2><span class="helper">${filtered.length} ${filtered.length === 1 ? 'book' : 'books'}</span></div><section class="book-grid">${filtered.length ? filtered.map(bookCard).join('') : `<div class="empty" style="grid-column:1/-1"><span class="empty-icon">⌕</span><b>No books found</b>Try another title, author, or subject.</div>`}</section>`;
  }

  function borrowingView() {
    const entries = [...state.borrowings].sort((a, b) => new Date(b.request_date) - new Date(a.request_date));
    return `<section class="greeting"><div><span class="eyebrow">Your library activity</span><h1>My books</h1><p>Borrow requests and current loans in one place.</p></div></section><div class="section-head"><h2>Borrowing history</h2></div><section class="list-stack">${entries.length ? entries.map((entry) => { const status = (entry.status || 'PENDING').toLowerCase(); return `<article class="list-card"><span class="quick-icon" style="margin:0;flex:none">▤</span><div class="grow"><h3>${escapeHtml(entry.book_title || `Book ${entry.book_id}`)}</h3><p>${escapeHtml(entry.req_id)} · Pickup ${escapeHtml(entry.pickup_date || 'To be arranged')}</p></div><span class="status ${status}">${escapeHtml(entry.status || 'PENDING').replaceAll('_', ' ')}</span></article>`; }).join('') : `<div class="empty"><span class="empty-icon">▤</span><b>No requests yet</b>Your requested books will appear here.<br><button class="text-button" data-tab="catalog">Browse the catalog →</button></div>`}</section>`;
  }

  function idView() {
    const data = state.digitalId;
    if (!data) return `<div class="empty"><span class="empty-icon">▣</span><b>Loading your library ID</b>Please wait a moment.</div>`;
    return `<section class="greeting"><div><span class="eyebrow">Always with you</span><h1>Library ID</h1><p>Show your membership details at the library desk.</p></div></section><article class="digital-card"><div class="digital-head"><div class="brand"><img class="brand-mark-img small" src="/mobile-assets/evsu-smartlib-logo.jpg" alt="EVSU-OC SmartLib logo"><span>EVSU-OC SmartLib</span></div><small>MEMBER CARD</small></div><div class="digital-name"><small>Card holder</small><h2>${escapeHtml(data.full_name)}</h2></div><div class="digital-grid"><div><small>Institutional ID</small><b>${escapeHtml(data.patron_id)}</b></div><div><small>Membership</small><b>${escapeHtml(data.role)}</b></div><div><small>Department</small><b>${escapeHtml(data.department || 'EVSU Ormoc City Campus')}</b></div><div><small>Active loans</small><b>${data.active_borrows_count}</b></div></div><div class="qr-display" id="qr-display" aria-label="Digital library ID QR code"></div><div class="id-payload"><span>${escapeHtml(data.qr_payload)}</span><button data-action="copy-id">Copy ID</button></div></article><p class="helper" style="text-align:center">${data.is_active ? 'Your membership is active.' : 'Please contact the library desk about your account.'} Card updated ${escapeHtml(new Date(data.issued_at.replace(' ', 'T') + 'Z').toLocaleDateString())}.</p>`;
  }

  function profileView() {
    const user = state.user || {};
    const pieces = (user.full_name || '').trim().split(/\s+/).filter(Boolean);
    const first = user.first_name || pieces.slice(0, -1).join(' ') || pieces[0] || '';
    const last = user.last_name || (pieces.length > 1 ? pieces.at(-1) : '');
    const avatar = user.profile_picture_url ? `<img class="profile-avatar" src="${escapeHtml(user.profile_picture_url)}" alt="Profile picture">` : `<span class="profile-avatar">${escapeHtml(initials(user.full_name))}</span>`;
    const editor = state.editingProfile ? `<form id="profile-form" class="profile-card"><h2>Edit profile</h2><p class="helper">Your institutional ID and email are managed by the library.</p><div class="profile-photo-field">${avatar}<div class="field"><label for="profile-photo">Profile picture (JPG or PNG, up to 5 MB)</label><input id="profile-photo" name="photo" type="file" accept="image/jpeg,image/png"></div></div><div class="field"><label for="profile-first">First name</label><input id="profile-first" name="first_name" value="${escapeHtml(first)}" required maxlength="80"></div><div class="field"><label for="profile-middle">Middle name (optional)</label><input id="profile-middle" name="middle_name" value="${escapeHtml(user.middle_name || '')}" maxlength="80"></div><div class="field"><label for="profile-last">Last name</label><input id="profile-last" name="last_name" value="${escapeHtml(last)}" required maxlength="80"></div><div class="field"><label for="profile-contact">Contact number (optional)</label><input id="profile-contact" name="contact_no" type="tel" value="${escapeHtml(user.contact_no || '')}" maxlength="30"></div><div class="profile-readonly"><span>Institutional ID: <b>${escapeHtml(user.username)}</b></span><span>Email: <b>${escapeHtml(user.email)}</b></span></div><div class="profile-actions"><button class="primary" type="submit">Save changes</button><button class="secondary" type="button" data-action="cancel-profile">Cancel</button></div></form>` : '';
    return `<section class="greeting"><div><span class="eyebrow">Your account</span><h1>Profile</h1><p>Library membership and account details.</p></div></section><article class="profile-card"><div class="profile-row">${avatar}<div><h2>${escapeHtml(user.full_name)}</h2><p>${escapeHtml(user.email)}</p></div></div><button class="secondary edit-profile-button" data-action="edit-profile">Edit profile</button><div class="section-head"><span class="helper">Institutional ID</span><b>${escapeHtml(user.username)}</b></div><div class="section-head"><span class="helper">Contact number</span><b>${escapeHtml(user.contact_no || 'Not provided')}</b></div><div class="section-head"><span class="helper">Account type</span><span class="pill">${escapeHtml(user.role)}</span></div><div class="section-head"><span class="helper">Department</span><b>${escapeHtml(user.department || 'EVSU Ormoc City Campus')}</b></div></article>${editor}<div class="section-head"><h2>Suggest a book</h2></div><form id="suggest-form" class="profile-card"><div class="field"><label for="suggest-title">Book title</label><input id="suggest-title" name="title" required></div><div class="field"><label for="suggest-author">Author</label><input id="suggest-author" name="author" required></div><div class="field"><label for="suggest-isbn">Publisher or ISBN <span class="helper">(optional)</span></label><input id="suggest-isbn" name="publisher_isbn"></div><div class="field"><label for="suggest-why">Why should the library add this book?</label><textarea id="suggest-why" name="justification" required></textarea></div><button class="primary wide">Send suggestion</button></form><button class="secondary wide" data-action="logout" style="margin-top:6px">Sign out</button>`;
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
    if (tab !== 'profile') state.editingProfile = false;
    localStorage.setItem('smartlib_active_tab', tab);
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
    if (action === 'toggle-auth') {
      state.authMode = state.authMode === 'login' ? 'register' : 'login';
      if (state.authMode === 'register') {
        try { state.signupOptions = await api('/auth/signup-options'); }
        catch (error) { notify(`Could not load departments and courses: ${error.message}`); }
      }
      renderAuth();
    }
    if (action === 'edit-profile') { state.editingProfile = true; shell(profileView(), 'profile'); document.getElementById('profile-first')?.focus(); }
    if (action === 'cancel-profile') { state.editingProfile = false; shell(profileView(), 'profile'); }
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
        if (state.authMode === 'register-otp') {
          const result = await api('/auth/register/verify-otp', { method: 'POST', body: JSON.stringify({ email: state.pendingRegistration.email, otp: formData.otp }) });
          state.pendingRegistration = null; state.authMode = 'login'; renderAuth(); notify(result.message);
        } else if (state.authMode === 'register') {
          if (formData.password !== formData.confirm_password) throw new Error('Passwords do not match.');
          const payload = { ...formData, middle_name: formData.middle_name || null, department_id: Number(formData.department_id), course_id: Number(formData.course_id) };
          await api('/auth/register/request-otp', { method: 'POST', body: JSON.stringify(payload) });
          state.pendingRegistration = payload; state.authMode = 'register-otp'; renderAuth(); notify('Check your email for the 6-digit code.');
        } else {
          const data = await api('/auth/login', { method: 'POST', body: JSON.stringify({ username: formData.username, password: formData.password, remember_me: formData.remember_me === 'on' }) });
          state.token = data.access_token;
          localStorage.removeItem('smartlib_token'); sessionStorage.removeItem('smartlib_token');
          (formData.remember_me === 'on' ? localStorage : sessionStorage).setItem('smartlib_token', data.access_token);
          state.tab = 'home'; localStorage.setItem('smartlib_active_tab', 'home'); await startApp();
        }
      } catch (error) { authError(error.message); button.disabled = false; }
    }
    if (form.id === 'profile-form') {
      const button = form.querySelector('button[type="submit"]'); button.disabled = true;
      const values = new FormData(form);
      try {
        const photo = values.get('photo');
        if (photo?.size && (photo.size > 5 * 1024 * 1024 || !['image/jpeg', 'image/png'].includes(photo.type))) throw new Error('Choose a JPG or PNG image under 5 MB.');
        state.user = await api('/auth/profile', { method: 'PUT', body: JSON.stringify({ first_name: values.get('first_name'), middle_name: values.get('middle_name') || null, last_name: values.get('last_name'), contact_no: values.get('contact_no') || null }) });
        if (photo?.size) {
          const upload = new FormData(); upload.append('photo', photo);
          state.user = await api('/auth/me/profile-photo', { method: 'POST', body: upload });
        }
        state.editingProfile = false; shell(profileView(), 'profile'); notify('Profile updated.');
      } catch (error) { notify(error.message); button.disabled = false; }
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

  root.addEventListener('change', (event) => {
    if (event.target.id !== 'department') return;
    const course = document.getElementById('course');
    const options = state.signupOptions.courses.filter((item) => item.department_id === Number(event.target.value));
    course.innerHTML = `<option value="">Choose a course</option>${options.map((item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join('')}`;
    course.disabled = !options.length;
  });

  window.addEventListener('beforeinstallprompt', (event) => { event.preventDefault(); state.installPrompt = event; if (state.user) renderTab(state.tab); });
  ['pointerdown','keydown','touchstart'].forEach((eventName) => document.addEventListener(eventName, resetIdleTimer, { passive: true }));
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/service-worker.js').catch(() => {});
  startApp();
})();
