import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AppState,
  ActivityIndicator,
  Alert,
  Image,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import * as SecureStore from 'expo-secure-store';
import QRCode from 'react-native-qrcode-svg';

const COLORS = {
  wine: '#730d24', wineDark: '#510919', gold: '#e0b867', ink: '#252129', muted: '#817c82',
  paper: '#ffffff', canvas: '#f7f5f2', line: '#eee9e4', soft: '#f5e9eb', green: '#277955',
  greenBg: '#eaf4ee', amber: '#876015', amberBg: '#fbf4df', red: '#a52c35', redBg: '#fff0f0',
};
const TOKEN_KEY = 'smartlib_access_token';
const API_KEY = 'smartlib_api_base';
const IDLE_TIMEOUT_MS = 15 * 60 * 1000;
const DEFAULT_API = Platform.OS === 'android' ? 'http://10.0.2.2:8000' : 'http://127.0.0.1:8000';
const TABS = [
  { id: 'home', icon: '⌂', label: 'Home' },
  { id: 'catalog', icon: '⌕', label: 'Explore' },
  { id: 'borrowings', icon: '▤', label: 'My books' },
  { id: 'digital-id', icon: '▣', label: 'Library ID' },
];

function Button({ title, onPress, secondary = false, disabled = false, compact = false, style, textStyle }) {
  return <Pressable accessibilityRole="button" onPress={onPress} disabled={disabled} style={({ pressed }) => [styles.button, secondary && styles.buttonSecondary, compact && styles.buttonCompact, disabled && styles.buttonDisabled, pressed && !disabled && styles.buttonPressed, style]}><Text style={[styles.buttonText, secondary && styles.buttonTextSecondary, compact && styles.buttonTextCompact, textStyle]}>{title}</Text></Pressable>;
}

function Field({ label, value, onChangeText, placeholder, secureTextEntry, keyboardType, autoCapitalize, multiline, style, inputStyle, ...props }) {
  const [passwordVisible, setPasswordVisible] = useState(false);
  const input = <TextInput value={value} onChangeText={onChangeText} placeholder={placeholder} placeholderTextColor="#a8a2a7" secureTextEntry={secureTextEntry && !passwordVisible} keyboardType={keyboardType} autoCapitalize={autoCapitalize || 'none'} multiline={multiline} style={[styles.input, multiline && styles.textarea, secureTextEntry && styles.passwordInput, inputStyle]} {...props} />;
  return <View style={[styles.field, style]}><Text style={styles.label}>{label}</Text>{secureTextEntry ? <View style={styles.passwordFieldWrap}>{input}<Pressable accessibilityRole="button" accessibilityLabel={passwordVisible ? 'Hide password' : 'Show password'} onPress={() => setPasswordVisible((visible) => !visible)} style={styles.passwordToggle}><Text style={styles.passwordToggleGlyph}>{passwordVisible ? '🙈' : '🐵'}</Text></Pressable></View> : input}</View>;
}

function Badge({ children, tone = 'green' }) {
  const toneStyle = tone === 'amber' ? [styles.badgeAmber, styles.badgeTextAmber] : tone === 'red' ? [styles.badgeRed, styles.badgeTextRed] : [styles.badgeGreen, styles.badgeTextGreen];
  return <View style={[styles.badge, toneStyle[0]]}><Text style={[styles.badgeText, toneStyle[1]]}>{children}</Text></View>;
}

function SectionTitle({ title, action, onAction }) {
  return <View style={styles.sectionTitle}><Text style={styles.sectionTitleText}>{title}</Text>{action ? <Pressable onPress={onAction}><Text style={styles.actionText}>{action}</Text></Pressable> : null}</View>;
}

export default function App() {
  const [apiBase, setApiBase] = useState(DEFAULT_API);
  const [apiDraft, setApiDraft] = useState(DEFAULT_API);
  const [token, setToken] = useState(null);
  const [user, setUser] = useState(null);
  const [screen, setScreen] = useState('loading');
  const [authMode, setAuthMode] = useState('login');
  const [resetToken, setResetToken] = useState('');
  const [tab, setTab] = useState('home');
  const [busy, setBusy] = useState(false);
  const [authError, setAuthError] = useState('');
  const [books, setBooks] = useState([]);
  const [borrowings, setBorrowings] = useState([]);
  const [digitalId, setDigitalId] = useState(null);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('All');
  const [selectedBook, setSelectedBook] = useState(null);
  const [pickupDate, setPickupDate] = useState(new Date(Date.now() + 86400000).toISOString().slice(0, 10));
  const [showSuggestion, setShowSuggestion] = useState(false);
  const [suggestion, setSuggestion] = useState({ title: '', author: '', publisher_isbn: '', justification: '' });
  const idleTimer = useRef(null);
  const lastActiveAt = useRef(Date.now());

  const request = useCallback(async (path, options = {}, authToken = token, base = apiBase) => {
    const headers = { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}), ...options.headers };
    let response;
    try {
      response = await fetch(`${base.replace(/\/+$/, '')}${path}`, { ...options, headers });
    } catch {
      throw new Error(`Can’t reach ${base}. Check the server address and make sure your phone and computer are on the same network.`);
    }
    const data = response.status === 204 ? null : await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 401 && authToken) {
        await SecureStore.deleteItemAsync(TOKEN_KEY);
        setToken(null); setUser(null); setScreen('auth');
      }
      throw new Error(data?.detail || 'Something went wrong. Please try again.');
    }
    return data;
  }, [apiBase, token]);

  useEffect(() => {
    let alive = true;
    (async () => {
      const [savedBase, savedToken] = await Promise.all([SecureStore.getItemAsync(API_KEY), SecureStore.getItemAsync(TOKEN_KEY)]);
      if (!alive) return;
      if (savedBase) { setApiBase(savedBase); setApiDraft(savedBase); }
      if (savedToken) {
        try {
          const me = await request('/auth/me', {}, savedToken, savedBase || DEFAULT_API);
          if (!alive) return;
          setToken(savedToken); setUser(me); setScreen('app');
        } catch {
          await SecureStore.deleteItemAsync(TOKEN_KEY);
          if (alive) setScreen('auth');
        }
      } else setScreen('auth');
    })();
    return () => { alive = false; };
  }, []);

  const loadBooks = useCallback(async (search = '') => {
    const params = new URLSearchParams({ limit: '100' });
    if (search.trim()) params.set('search', search.trim());
    setBooks(await request(`/mobile/books?${params.toString()}`));
  }, [request]);
  const loadBorrowings = useCallback(async () => setBorrowings(await request('/mobile/my-borrowings')), [request]);
  const loadDigitalId = useCallback(async () => setDigitalId(await request('/mobile/profile/digital-id')), [request]);

  useEffect(() => {
    if (screen !== 'app' || !token) return;
    let alive = true;
    const timer = setTimeout(async () => {
      setBusy(true);
      try {
        if (tab === 'home' || tab === 'catalog') await loadBooks(tab === 'catalog' ? query : '');
        if (tab === 'home' || tab === 'borrowings') await loadBorrowings();
        if (tab === 'digital-id') await loadDigitalId();
      } catch (error) { if (alive) Alert.alert('Could not refresh', error.message); }
      finally { if (alive) setBusy(false); }
    }, tab === 'catalog' ? 220 : 0);
    return () => { alive = false; clearTimeout(timer); };
  }, [screen, tab, token, query, loadBooks, loadBorrowings, loadDigitalId]);

  const categories = useMemo(() => ['All', ...new Set(books.map((book) => book.category).filter(Boolean))], [books]);
  const visibleBooks = useMemo(() => books.filter((book) => category === 'All' || book.category === category), [books, category]);

  async function saveApiBase() {
    const clean = apiDraft.trim().replace(/\/+$/, '');
    if (!/^https?:\/\/[^\s]+/.test(clean)) { setAuthError('Enter the server address in the format http://192.168.1.20:8000'); return null; }
    setApiBase(clean); setApiDraft(clean); setAuthError('');
    await SecureStore.setItemAsync(API_KEY, clean);
    return clean;
  }

  async function submitAuth(form) {
    setBusy(true); setAuthError('');
    try {
      const base = await saveApiBase();
      if (!base) return;
      if (authMode === 'register' || authMode === 'forgot-new-password') {
        const password = authMode === 'register' ? form.password : form.new_password;
        const confirmation = authMode === 'register' ? form.confirm_password : form.confirm_new_password;
        if (password !== confirmation) { setAuthError('The password and confirmation do not match.'); return; }
        if (password.length < 8 || password.length > 12 || !/[a-z]/.test(password) || !/[A-Z]/.test(password) || !/\d/.test(password) || !/[^A-Za-z0-9]/.test(password)) {
          setAuthError('Use 8–12 characters with an uppercase letter, lowercase letter, number, and symbol.'); return;
        }
      }
      if (authMode === 'register') {
        const body = {
          first_name: form.first_name.trim(), last_name: form.last_name.trim(), username: form.username.trim(),
          email: form.email.trim(), password: form.password, confirm_password: form.confirm_password,
          account_type: form.account_type, department: form.department.trim() || null,
        };
        await request('/auth/register/request-otp', { method: 'POST', body: JSON.stringify(body) }, null, base);
        setAuthMode('register-otp'); setAuthError('We sent a 6-digit code to your email. Enter it to create your account.');
        return;
      }
      if (form.resend_otp && (authMode === 'register-otp' || authMode === 'forgot-otp')) {
        const path = authMode === 'register-otp' ? '/auth/register/request-otp' : '/auth/forgot-password/request-otp';
        const body = authMode === 'register-otp' ? {
          first_name: form.first_name.trim(), last_name: form.last_name.trim(), username: form.username.trim(),
          email: form.email.trim(), password: form.password, confirm_password: form.confirm_password,
          account_type: form.account_type, department: form.department.trim() || null,
        } : { email: form.email.trim() };
        const data = await request(path, { method: 'POST', body: JSON.stringify(body) }, null, base);
        setAuthError(data.message || 'A new code has been sent.');
        return;
      }
      if (authMode === 'register-otp') {
        const data = await request('/auth/register/verify-otp', { method: 'POST', body: JSON.stringify({ email: form.email.trim(), otp: form.otp.trim() }) }, null, base);
        setAuthMode('login'); setAuthError(data.message || 'Your account is ready. Sign in to continue.');
        return;
      }
      if (authMode === 'forgot') {
        const data = await request('/auth/forgot-password/request-otp', { method: 'POST', body: JSON.stringify({ email: form.email.trim() }) }, null, base);
        setAuthMode('forgot-otp'); setAuthError(data.message || 'If an active account uses that email, a verification code will be sent.');
        return;
      }
      if (authMode === 'forgot-otp') {
        const data = await request('/auth/forgot-password/verify-otp', { method: 'POST', body: JSON.stringify({ email: form.email.trim(), otp: form.otp.trim() }) }, null, base);
        setResetToken(data.reset_token); setAuthMode('forgot-new-password'); setAuthError(data.message || 'Email verified. Choose a new password.');
        return;
      }
      if (authMode === 'forgot-new-password') {
        const data = await request('/auth/forgot-password/complete', { method: 'POST', body: JSON.stringify({ token: resetToken, new_password: form.new_password, confirm_new_password: form.confirm_new_password }) }, null, base);
        setResetToken(''); setAuthMode('login'); setAuthError(data.message || 'Password changed. Sign in using your new password.');
        return;
      }
      const data = await request('/auth/login', { method: 'POST', body: JSON.stringify({ username: form.username.trim(), password: form.password, remember_me: form.remember_me }) }, null, base);
      const profile = await request('/auth/me', {}, data.access_token, base);
      if (form.remember_me) await SecureStore.setItemAsync(TOKEN_KEY, data.access_token);
      else await SecureStore.deleteItemAsync(TOKEN_KEY);
      setToken(data.access_token); setUser(profile); setTab('home'); setScreen('app');
    } catch (error) { setAuthError(error.message); }
    finally { setBusy(false); }
  }

  async function signOut() {
    if (token) request('/auth/logout', { method: 'POST' }).catch(() => {});
    if (idleTimer.current) clearTimeout(idleTimer.current);
    await SecureStore.deleteItemAsync(TOKEN_KEY);
    setToken(null); setUser(null); setDigitalId(null); setScreen('auth'); setAuthMode('login'); setTab('home');
  }

  async function requestBook() {
    if (!selectedBook) return;
    setBusy(true);
    try {
      await request('/mobile/borrow', { method: 'POST', body: JSON.stringify({ accession_no: selectedBook.accession_no, pickup_date: pickupDate }) });
      setSelectedBook(null); await loadBorrowings(); setTab('borrowings');
      Alert.alert('Request sent', 'The library has received your borrow request.');
    } catch (error) { Alert.alert('Request failed', error.message); }
    finally { setBusy(false); }
  }

  async function sendSuggestion() {
    if (!suggestion.title.trim() || !suggestion.author.trim() || !suggestion.justification.trim()) {
      Alert.alert('Details needed', 'Add the book title, author, and a short reason for your suggestion.'); return;
    }
    setBusy(true);
    try {
      await request('/mobile/acquisitions', { method: 'POST', body: JSON.stringify({ ...suggestion, title: suggestion.title.trim(), author: suggestion.author.trim(), publisher_isbn: suggestion.publisher_isbn.trim() || null, justification: suggestion.justification.trim() }) });
      setSuggestion({ title: '', author: '', publisher_isbn: '', justification: '' }); setShowSuggestion(false);
      Alert.alert('Suggestion sent', 'Thanks for helping improve the library collection.');
    } catch (error) { Alert.alert('Could not send suggestion', error.message); }
    finally { setBusy(false); }
  }

  function changeTab(nextTab) {
    setCategory('All'); setTab(nextTab);
    if (nextTab === 'catalog') { setQuery(''); }
  }

  const resetIdleTimer = useCallback(() => {
    lastActiveAt.current = Date.now();
    if (idleTimer.current) clearTimeout(idleTimer.current);
    if (screen === 'app' && token) idleTimer.current = setTimeout(signOut, IDLE_TIMEOUT_MS);
  }, [screen, token]);
  useEffect(() => {
    if (screen !== 'app' || !token) return;
    resetIdleTimer();
    const subscription = AppState.addEventListener('change', (state) => {
      if (state === 'active') {
        if (Date.now() - lastActiveAt.current >= IDLE_TIMEOUT_MS) signOut();
        else resetIdleTimer();
      }
    });
    return () => { subscription.remove(); if (idleTimer.current) clearTimeout(idleTimer.current); };
  }, [screen, token, resetIdleTimer]);

  if (screen === 'loading') return <View style={styles.loading}><StatusBar barStyle="dark-content" backgroundColor={COLORS.canvas} /><Image source={require('./assets/evsu-smartlib-logo.jpg')} style={styles.splashLogo} accessibilityLabel="EVSU OC SmartLib logo" /><ActivityIndicator color={COLORS.wine} size="large" /><Text style={styles.helperText}>Opening your library…</Text></View>;
  if (screen === 'auth') return <AuthScreen apiBase={apiDraft} onApiChange={setApiDraft} onSubmit={submitAuth} mode={authMode} setMode={(mode) => { setAuthMode(mode); setAuthError(''); }} error={authError} busy={busy} />;

  const firstName = (user?.full_name || 'reader').trim().split(/\s+/)[0];
  return <View style={styles.app} onTouchStart={resetIdleTimer}><StatusBar barStyle="dark-content" backgroundColor={COLORS.paper} /><View style={styles.topbar}><View style={styles.brandLine}><Image source={require('./assets/evsu-smartlib-logo.jpg')} style={styles.brandLogoImage} accessibilityLabel="EVSU OC SmartLib logo" /><Text style={styles.brandText}>SmartLib</Text></View><Pressable style={styles.avatar} onPress={() => setTab('profile')}><Text style={styles.avatarText}>{(user?.full_name || 'S').split(/\s+/).slice(0, 2).map((part) => part[0]).join('').toUpperCase()}</Text></Pressable></View>
    <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={styles.scrollContent}>
      {tab === 'home' && <HomeScreen firstName={firstName} user={user} books={books} borrowings={borrowings} busy={busy} onExplore={() => changeTab('catalog')} onTab={changeTab} onBorrow={setSelectedBook} />}
      {tab === 'catalog' && <CatalogScreen books={visibleBooks} categories={categories} category={category} onCategory={setCategory} query={query} onQuery={setQuery} busy={busy} onBorrow={setSelectedBook} />}
      {tab === 'borrowings' && <BorrowingsScreen borrowings={borrowings} busy={busy} onRefresh={loadBorrowings} onExplore={() => changeTab('catalog')} />}
      {tab === 'digital-id' && <DigitalIdScreen digitalId={digitalId} busy={busy} onRefresh={loadDigitalId} />}
      {tab === 'profile' && <ProfileScreen user={user} onSuggest={() => setShowSuggestion(true)} onSignOut={signOut} apiBase={apiBase} onChangeApi={() => { setApiDraft(apiBase); setScreen('server-settings'); }} />}
    </ScrollView>
    <View style={styles.tabBar}>{TABS.map((item) => <Pressable key={item.id} onPress={() => changeTab(item.id)} style={styles.tabItem}><Text style={[styles.tabIcon, tab === item.id && styles.tabActive]}>{item.icon}</Text><Text style={[styles.tabLabel, tab === item.id && styles.tabActive]}>{item.label}</Text></Pressable>)}</View>
    <BorrowModal book={selectedBook} pickupDate={pickupDate} setPickupDate={setPickupDate} onClose={() => setSelectedBook(null)} onSubmit={requestBook} busy={busy} />
    <SuggestionModal visible={showSuggestion} suggestion={suggestion} setSuggestion={setSuggestion} onClose={() => setShowSuggestion(false)} onSubmit={sendSuggestion} busy={busy} />
    {screen === 'server-settings' && <ServerModal value={apiDraft} onChange={setApiDraft} onClose={() => setScreen('app')} onSave={async () => { if (await saveApiBase()) { setScreen('app'); Alert.alert('Saved', 'The server address has been updated.'); } }} />}
  </View>;
}

function AuthScreen({ apiBase, onApiChange, onSubmit, mode, setMode, error, busy }) {
  const [form, setForm] = useState({ username: '', password: '', confirm_password: '', new_password: '', confirm_new_password: '', otp: '', first_name: '', last_name: '', email: '', account_type: 'student', department: '', remember_me: false });
  const update = (key, value) => setForm((current) => ({ ...current, [key]: value }));
  const register = mode === 'register';
  const registerOtp = mode === 'register-otp';
  const forgot = mode === 'forgot';
  const forgotOtp = mode === 'forgot-otp';
  const newPassword = mode === 'forgot-new-password';
  const authStep = register || registerOtp || forgot || forgotOtp || newPassword;
  const title = register ? 'Create your account' : registerOtp ? 'Verify your email' : forgot ? 'Reset your password' : forgotOtp ? 'Enter your code' : newPassword ? 'Choose a new password' : 'Welcome back';
  const subtitle = register ? 'Join your EVSU library community.' : registerOtp ? `Enter the 6-digit code sent to ${form.email}. Your account is created after verification.` : forgot ? 'We’ll email a 6-digit code to continue.' : forgotOtp ? `Enter the 6-digit code sent to ${form.email}.` : newPassword ? 'Set a new password for your account.' : 'Sign in with your institutional account.';
  const successful = error && !/couldn’t|could not|can’t|invalid|expired|must |already registered|do not match|not configured/i.test(error);
  return <KeyboardAvoidingView style={styles.authPage} behavior={Platform.OS === 'ios' ? 'padding' : undefined}><StatusBar barStyle="light-content" backgroundColor={COLORS.wineDark} /><ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={styles.authScroll}><View style={styles.authHero}><View style={styles.brandLine}><Image source={require('./assets/evsu-smartlib-logo.jpg')} style={styles.brandLogoImage} accessibilityLabel="EVSU OC SmartLib logo" /><Text style={styles.brandTextLight}>EVSU SmartLib</Text></View><Text style={styles.authHeroTitle}>Your campus library, wherever you are.</Text><Text style={styles.authHeroBody}>Find your next read, request a copy, and keep your library card close at hand.</Text></View><View style={styles.authCard}>
    <Text style={styles.authTitle}>{title}</Text><Text style={styles.authSubtitle}>{subtitle}</Text>{error ? <Text style={[styles.errorBox, successful && styles.successBox]}>{error}</Text> : null}
    {register ? <><View style={styles.row}><Field label="First name" value={form.first_name} onChangeText={(value) => update('first_name', value)} style={styles.flex} autoCapitalize="words" /><Field label="Last name" value={form.last_name} onChangeText={(value) => update('last_name', value)} style={styles.flex} autoCapitalize="words" /></View><Field label="Student or staff ID" value={form.username} onChangeText={(value) => update('username', value)} placeholder="Your institutional ID" autoCapitalize="characters" /><Field label="EVSU email" value={form.email} onChangeText={(value) => update('email', value)} placeholder="name@evsu.edu.ph" keyboardType="email-address" /><Text style={styles.label}>Account type</Text><View style={styles.choiceRow}>{['student', 'faculty'].map((role) => <Pressable key={role} onPress={() => update('account_type', role)} style={[styles.choice, form.account_type === role && styles.choiceSelected]}><Text style={[styles.choiceText, form.account_type === role && styles.choiceTextSelected]}>{role === 'student' ? 'Student' : 'Faculty'}</Text></Pressable>)}</View><Field label="Department" value={form.department} onChangeText={(value) => update('department', value)} placeholder="Optional" autoCapitalize="words" /><Field label="Password" value={form.password} onChangeText={(value) => update('password', value)} secureTextEntry placeholder="8–12 characters" /><Text style={styles.serverHint}>Use 8–12 characters with an uppercase letter, lowercase letter, number, and symbol.</Text><Field label="Confirm password" value={form.confirm_password} onChangeText={(value) => update('confirm_password', value)} secureTextEntry placeholder="Enter the password again" /></> : registerOtp || forgotOtp ? <><Field label="6-digit email code" value={form.otp} onChangeText={(value) => update('otp', value.replace(/\D/g, '').slice(0, 6))} placeholder="000000" keyboardType="number-pad" autoCapitalize="none" /><Pressable style={styles.authSwitch} onPress={() => onSubmit({ ...form, resend_otp: true })}><Text style={styles.authSwitchAction}>Send a new code</Text></Pressable></> : forgot ? <Field label="EVSU email" value={form.email} onChangeText={(value) => update('email', value)} placeholder="name@evsu.edu.ph" keyboardType="email-address" /> : newPassword ? <><Field label="New password" value={form.new_password} onChangeText={(value) => update('new_password', value)} secureTextEntry placeholder="8–12 characters" /><Field label="Confirm new password" value={form.confirm_new_password} onChangeText={(value) => update('confirm_new_password', value)} secureTextEntry placeholder="Enter it again" /><Text style={styles.serverHint}>Use 8–12 characters with an uppercase letter, lowercase letter, number, and symbol.</Text></> : <><Field label="Institutional ID or email" value={form.username} onChangeText={(value) => update('username', value)} placeholder="Enter your ID or email" autoCapitalize="none" /><Field label="Password" value={form.password} onChangeText={(value) => update('password', value)} secureTextEntry placeholder="Enter your password" /><Pressable style={styles.authSwitch} onPress={() => setMode('forgot')}><Text style={styles.authSwitchAction}>Forgot password?</Text></Pressable><Pressable style={styles.rememberRow} onPress={() => update('remember_me', !form.remember_me)}><View style={[styles.checkBox, form.remember_me && styles.checkBoxChecked]}>{form.remember_me ? <Text style={styles.checkMark}>✓</Text> : null}</View><Text style={styles.authSwitchText}>Remember me</Text></Pressable></>}
    {authStep && <><Field label="Library server address" value={apiBase} onChangeText={onApiChange} placeholder="http://192.168.1.20:8000" autoCapitalize="none" keyboardType="url" /><Text style={styles.serverHint}>On a phone, use your computer’s local network address. The server must be running on the same Wi-Fi network.</Text></>}
    <Button title={busy ? 'Please wait…' : register ? 'Continue to email code' : registerOtp ? 'Verify code & create account' : forgot ? 'Send verification code' : forgotOtp ? 'Verify code' : newPassword ? 'Save new password' : 'Sign in'} disabled={busy} onPress={() => onSubmit(form)} />{authStep && <Pressable onPress={() => setMode('login')} style={styles.authSwitch}><Text style={styles.authSwitchText}>Back to <Text style={styles.authSwitchAction}>sign in</Text></Text></Pressable>}{mode === 'login' && <Pressable onPress={() => setMode('register')} style={styles.authSwitch}><Text style={styles.authSwitchText}>New to SmartLib? <Text style={styles.authSwitchAction}>Create account</Text></Text></Pressable>}</View></ScrollView></KeyboardAvoidingView>;
}

function Greeting({ eyebrow, title, subtitle, right }) {
  return <View style={styles.greeting}><View style={styles.flex}><Text style={styles.eyebrow}>{eyebrow}</Text><Text style={styles.pageTitle}>{title}</Text>{subtitle ? <Text style={styles.pageSubtitle}>{subtitle}</Text> : null}</View>{right}</View>;
}

function BookCard({ book, index, onBorrow }) {
  const available = Number(book.available_copies) > 0;
  const coverColors = ['#c58e79', '#78876c', '#667797', '#aa8749'];
  return <View style={styles.bookCard}><View style={[styles.bookCover, { backgroundColor: coverColors[index % coverColors.length] }]}><Text style={styles.bookCoverLetter}>{(book.title || 'B').slice(0, 1).toUpperCase()}</Text></View><View style={styles.bookInfo}><Text style={styles.bookTitle}>{book.title}</Text><Text style={styles.bookAuthor}>{book.author}</Text><Text style={styles.bookMeta}>{book.category}{book.year_published ? ` · ${book.year_published}` : ''}</Text><Badge tone={available ? 'green' : 'red'}>{available ? `${book.available_copies} available` : 'Unavailable'}</Badge>{available ? <Button title="Request a copy" compact secondary onPress={() => onBorrow(book)} style={styles.bookButton} /> : null}</View></View>;
}

function HomeScreen({ firstName, user, books, borrowings, busy, onExplore, onTab, onBorrow }) {
  const available = books.filter((book) => book.available_copies > 0).length;
  const pending = borrowings.filter((entry) => entry.status === 'PENDING').length;
  return <><Greeting eyebrow="EVSU library · main campus" title={`Hello, ${firstName} 👋`} subtitle="Ready to find something worth reading?" right={<View style={styles.rolePill}><Text style={styles.roleText}>{user?.role}</Text></View>} /><View style={styles.hero}><Text style={styles.heroEyebrow}>A world of knowledge</Text><Text style={styles.heroTitle}>Your next chapter starts here.</Text><Text style={styles.heroSubtitle}>{busy ? 'Updating the collection…' : `${available} books ready to discover`}</Text><Button title="Explore books  →" onPress={onExplore} style={styles.heroButton} textStyle={styles.heroButtonText} /></View><SectionTitle title="Quick access" /><View style={styles.quickGrid}>{[['⌕', 'Find a book', 'Search the catalog', 'catalog'], ['▤', 'My requests', pending ? `${pending} awaiting review` : 'Check your loans', 'borrowings'], ['▣', 'Library ID', 'Digital membership', 'digital-id']].map(([icon, label, sub, target]) => <Pressable key={target} style={styles.quickCard} onPress={() => onTab(target)}><Text style={styles.quickIcon}>{icon}</Text><Text style={styles.quickTitle}>{label}</Text><Text style={styles.quickSub}>{sub}</Text></Pressable>)}</View><SectionTitle title="Recently added" action="View all →" onAction={onExplore} />{books.length ? <View style={styles.bookGrid}>{books.slice(0, 4).map((book, index) => <BookCard key={book.id} book={book} index={index} onBorrow={onBorrow} />)}</View> : <EmptyState icon="⌕" title="Catalog is waiting for you" message="The collection will appear here when it loads." />}</>;
}

function CatalogScreen({ books, categories, category, onCategory, query, onQuery, busy, onBorrow }) {
  return <><Greeting eyebrow="EVSU library collection" title="Explore books" subtitle="Search titles, authors, and subjects." /><TextInput value={query} onChangeText={(text) => { onQuery(text); onCategory('All'); }} placeholder="⌕  What would you like to read?" placeholderTextColor="#918b90" style={styles.searchInput} returnKeyType="search" /><ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chipRow}>{categories.map((item) => <Pressable key={item} onPress={() => onCategory(item)} style={[styles.chip, category === item && styles.chipActive]}><Text style={[styles.chipText, category === item && styles.chipTextActive]}>{item}</Text></Pressable>)}</ScrollView><SectionTitle title={busy ? 'Updating collection…' : 'The collection'} action={`${books.length} ${books.length === 1 ? 'book' : 'books'}`} />{books.length ? <View style={styles.bookGrid}>{books.map((book, index) => <BookCard key={book.id} book={book} index={index} onBorrow={onBorrow} />)}</View> : <EmptyState icon="⌕" title="No books found" message="Try a different title, author, or subject." />}</>;
}

function BorrowingsScreen({ borrowings, busy, onRefresh, onExplore }) {
  const entries = [...borrowings].sort((a, b) => new Date(b.request_date) - new Date(a.request_date));
  return <><Greeting eyebrow="Your library activity" title="My books" subtitle="Borrow requests and current loans." right={<Pressable onPress={onRefresh}><Text style={styles.actionText}>Refresh</Text></Pressable>} /><SectionTitle title={busy ? 'Updating…' : 'Borrowing history'} />{entries.length ? <View style={styles.listStack}>{entries.map((entry) => { const status = entry.status || 'PENDING'; const tone = ['PENDING', 'APPROVED'].includes(status) ? 'amber' : ['REJECTED', 'OVERDUE'].includes(status) ? 'red' : 'green'; return <View key={entry.id} style={styles.listCard}><Text style={styles.listIcon}>▤</Text><View style={styles.listContent}><Text style={styles.listTitle}>{entry.book_title || `Book ${entry.book_id}`}</Text><Text style={styles.listSub}>{entry.req_id} · Pickup {entry.pickup_date || 'To be arranged'}</Text></View><Badge tone={tone}>{status.replaceAll('_', ' ')}</Badge></View>; })}</View> : <EmptyState icon="▤" title="No requests yet" message="Requested books will appear here."><Button title="Browse the catalog" secondary onPress={onExplore} /></EmptyState>}</>;
}

function DigitalIdScreen({ digitalId, busy, onRefresh }) {
  if (!digitalId) return <><Greeting eyebrow="Always with you" title="Library ID" subtitle="Your digital EVSU library membership." /><View style={styles.emptyBox}><ActivityIndicator color={COLORS.wine} /><Text style={styles.emptyTitle}>{busy ? 'Loading your library ID…' : 'Could not load your ID'}</Text><Button title="Try again" secondary onPress={onRefresh} /></View></>;
  return <><Greeting eyebrow="Always with you" title="Library ID" subtitle="Show your membership at the library desk." /><View style={styles.digitalCard}><View style={styles.digitalHead}><View style={styles.brandLine}><Image source={require('./assets/evsu-smartlib-logo.jpg')} style={styles.brandLogoImage} accessibilityLabel="EVSU OC SmartLib logo" /><Text style={styles.brandTextLight}>EVSU SmartLib</Text></View><Text style={styles.cardLabel}>MEMBER CARD</Text></View><View style={styles.cardName}><Text style={styles.cardLabel}>CARD HOLDER</Text><Text style={styles.cardHolder}>{digitalId.full_name}</Text></View><View style={styles.idGrid}><IdFact label="Institutional ID" value={digitalId.patron_id} /><IdFact label="Membership" value={digitalId.role} /><IdFact label="Department" value={digitalId.department || 'EVSU Main Campus'} /><IdFact label="Active loans" value={String(digitalId.active_borrows_count)} /></View><View style={styles.qrBox}><QRCode value={digitalId.qr_payload} size={150} color="#241d20" backgroundColor="#ffffff" quietZone={5} /></View><Text style={styles.qrPayload}>{digitalId.qr_payload}</Text><Text style={styles.cardFoot}>{digitalId.is_active ? 'Membership active' : 'Contact the library desk about your account'}</Text></View><Button title={busy ? 'Refreshing…' : 'Refresh card'} secondary onPress={onRefresh} style={styles.refreshButton} /></>;
}

function IdFact({ label, value }) { return <View style={styles.idFact}><Text style={styles.cardLabel}>{label.toUpperCase()}</Text><Text style={styles.idValue}>{value}</Text></View>; }

function ProfileScreen({ user, onSuggest, onSignOut, apiBase, onChangeApi }) {
  return <><Greeting eyebrow="Your account" title="Profile" subtitle="Library membership and account details." /><View style={styles.profileCard}><View style={styles.profileHeader}><View style={styles.profileAvatar}><Text style={styles.avatarText}>{(user?.full_name || 'S').split(/\s+/).slice(0, 2).map((part) => part[0]).join('').toUpperCase()}</Text></View><View style={styles.flex}><Text style={styles.profileName}>{user?.full_name}</Text><Text style={styles.profileEmail}>{user?.email}</Text></View></View><ProfileFact label="Institutional ID" value={user?.username} /><ProfileFact label="Account type" value={user?.role} /><ProfileFact label="Department" value={user?.department || 'EVSU Main Campus'} /></View><SectionTitle title="Library services" /><Button title="Suggest a book" onPress={onSuggest} /><Button title="Server address" secondary onPress={onChangeApi} style={styles.profileButton} /><Text style={styles.serverSummary}>Connected to {apiBase}</Text><Button title="Sign out" secondary onPress={onSignOut} style={styles.profileButton} /></>;
}

function ProfileFact({ label, value }) { return <View style={styles.profileFact}><Text style={styles.profileFactLabel}>{label}</Text><Text style={styles.profileFactValue}>{value}</Text></View>; }

function EmptyState({ icon, title, message, children }) { return <View style={styles.emptyBox}><Text style={styles.emptyIcon}>{icon}</Text><Text style={styles.emptyTitle}>{title}</Text><Text style={styles.emptyMessage}>{message}</Text>{children}</View>; }

function BorrowModal({ book, pickupDate, setPickupDate, onClose, onSubmit, busy }) {
  return <Modal visible={Boolean(book)} transparent animationType="slide" onRequestClose={onClose}><View style={styles.modalShade}><KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.modalWrap}><View style={styles.modalCard}><View style={styles.modalHandle} /><Text style={styles.modalTitle}>Request this book</Text><Text style={styles.modalSub}>{book?.title}</Text><Text style={styles.modalSub}>{book?.author}</Text><Field label="Preferred pickup date (YYYY-MM-DD)" value={pickupDate} onChangeText={setPickupDate} placeholder="2026-10-02" autoCapitalize="none" /><Button title={busy ? 'Sending request…' : 'Send borrow request'} onPress={onSubmit} disabled={busy} /><Button title="Cancel" secondary onPress={onClose} style={styles.modalCancel} /></View></KeyboardAvoidingView></View></Modal>;
}

function SuggestionModal({ visible, suggestion, setSuggestion, onClose, onSubmit, busy }) {
  const change = (key, value) => setSuggestion((current) => ({ ...current, [key]: value }));
  return <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}><View style={styles.modalShade}><KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.modalWrap}><ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={styles.modalCard}><View style={styles.modalHandle} /><Text style={styles.modalTitle}>Suggest a book</Text><Text style={styles.modalSub}>Help grow the EVSU library collection.</Text><Field label="Book title" value={suggestion.title} onChangeText={(value) => change('title', value)} /><Field label="Author" value={suggestion.author} onChangeText={(value) => change('author', value)} /><Field label="Publisher or ISBN (optional)" value={suggestion.publisher_isbn} onChangeText={(value) => change('publisher_isbn', value)} /><Field label="Why should the library add this book?" value={suggestion.justification} onChangeText={(value) => change('justification', value)} multiline /><Button title={busy ? 'Sending…' : 'Send suggestion'} onPress={onSubmit} disabled={busy} /><Button title="Cancel" secondary onPress={onClose} style={styles.modalCancel} /></ScrollView></KeyboardAvoidingView></View></Modal>;
}

function ServerModal({ value, onChange, onClose, onSave }) {
  return <Modal visible transparent animationType="fade" onRequestClose={onClose}><View style={styles.modalShade}><View style={styles.modalCard}><Text style={styles.modalTitle}>Library server</Text><Text style={styles.modalSub}>Enter the computer’s local network address, such as http://192.168.1.20:8000.</Text><Field label="Server address" value={value} onChangeText={onChange} keyboardType="url" autoCapitalize="none" /><Button title="Save address" onPress={onSave} /><Button title="Cancel" secondary onPress={onClose} style={styles.modalCancel} /></View></View></Modal>;
}

const styles = StyleSheet.create({
  app: { flex: 1, backgroundColor: COLORS.canvas, paddingTop: Platform.OS === 'ios' ? 44 : 8 },
  loading: { flex: 1, backgroundColor: COLORS.canvas, alignItems: 'center', justifyContent: 'center', gap: 14 },
  helperText: { color: COLORS.muted, fontSize: 13 },
  splashLogo: { width: 148, height: 148, borderRadius: 74, marginBottom: 8 },
  topbar: { height: 62, backgroundColor: COLORS.paper, paddingHorizontal: 19, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderBottomWidth: 1, borderBottomColor: COLORS.line },
  brandLine: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  brandLogoImage: { height: 40, width: 40, borderRadius: 12, backgroundColor: COLORS.paper },
  brandText: { color: COLORS.ink, fontWeight: '800', fontSize: 16 },
  brandTextLight: { color: COLORS.paper, fontWeight: '800', fontSize: 15 },
  avatar: { width: 38, height: 38, borderRadius: 19, backgroundColor: COLORS.soft, alignItems: 'center', justifyContent: 'center' },
  avatarText: { color: COLORS.wine, fontWeight: '800', fontSize: 13 },
  scrollContent: { paddingHorizontal: 18, paddingTop: 22, paddingBottom: 132 },
  greeting: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, marginBottom: 20 },
  flex: { flex: 1 },
  eyebrow: { color: COLORS.wine, fontSize: 10, fontWeight: '800', letterSpacing: 1.4, textTransform: 'uppercase' },
  pageTitle: { color: COLORS.ink, fontSize: 29, fontWeight: '800', letterSpacing: -0.7, marginTop: 5 },
  pageSubtitle: { color: COLORS.muted, fontSize: 13, lineHeight: 19, marginTop: 3 },
  rolePill: { backgroundColor: COLORS.soft, borderRadius: 99, paddingHorizontal: 10, paddingVertical: 7, marginTop: 14 },
  roleText: { color: COLORS.wine, fontSize: 10, fontWeight: '700', textTransform: 'capitalize' },
  hero: { backgroundColor: COLORS.wine, borderRadius: 21, padding: 21, overflow: 'hidden', marginBottom: 24 },
  heroEyebrow: { color: COLORS.gold, fontSize: 10, fontWeight: '800', letterSpacing: 1.3, textTransform: 'uppercase' },
  heroTitle: { color: COLORS.paper, fontSize: 22, lineHeight: 28, fontWeight: '800', marginTop: 8, maxWidth: 260 },
  heroSubtitle: { color: '#efdde0', fontSize: 12, marginTop: 5 },
  heroButton: { alignSelf: 'flex-start', backgroundColor: COLORS.paper, marginTop: 17 },
  heroButtonText: { color: COLORS.wine },
  sectionTitle: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 6, marginBottom: 12 },
  sectionTitleText: { color: COLORS.ink, fontSize: 18, fontWeight: '800' },
  actionText: { color: COLORS.wine, fontWeight: '700', fontSize: 12 },
  quickGrid: { flexDirection: 'row', gap: 9, marginBottom: 23 },
  quickCard: { flex: 1, minHeight: 112, borderWidth: 1, borderColor: COLORS.line, borderRadius: 16, backgroundColor: COLORS.paper, padding: 11, justifyContent: 'flex-start' },
  quickIcon: { width: 32, height: 32, overflow: 'hidden', textAlign: 'center', textAlignVertical: 'center', borderRadius: 11, color: COLORS.wine, backgroundColor: COLORS.soft, fontSize: 18, marginBottom: 9 },
  quickTitle: { color: COLORS.ink, fontSize: 11, fontWeight: '800' },
  quickSub: { color: COLORS.muted, fontSize: 9, marginTop: 4 },
  bookGrid: { gap: 11 },
  bookCard: { flexDirection: 'row', gap: 13, backgroundColor: COLORS.paper, borderColor: COLORS.line, borderWidth: 1, borderRadius: 16, padding: 13 },
  bookCover: { width: 60, height: 82, borderRadius: 8, alignItems: 'center', justifyContent: 'center' },
  bookCoverLetter: { color: '#ffffffda', fontSize: 23, fontWeight: '800' },
  bookInfo: { flex: 1, alignItems: 'flex-start' },
  bookTitle: { color: COLORS.ink, fontSize: 13, lineHeight: 18, fontWeight: '800' },
  bookAuthor: { color: COLORS.muted, fontSize: 11, marginTop: 3 },
  bookMeta: { color: COLORS.muted, fontSize: 10, marginTop: 7, marginBottom: 8 },
  bookButton: { marginTop: 8 },
  button: { minHeight: 47, borderRadius: 13, backgroundColor: COLORS.wine, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 17, paddingVertical: 12 },
  buttonSecondary: { backgroundColor: COLORS.soft },
  buttonText: { color: COLORS.paper, fontSize: 13, fontWeight: '800' },
  buttonTextSecondary: { color: COLORS.wine },
  buttonCompact: { minHeight: 34, borderRadius: 10, paddingHorizontal: 11, paddingVertical: 8 },
  buttonTextCompact: { fontSize: 10 },
  buttonDisabled: { opacity: 0.55 },
  buttonPressed: { opacity: 0.83 },
  badge: { borderRadius: 99, paddingVertical: 5, paddingHorizontal: 8, alignSelf: 'flex-start' },
  badgeText: { fontSize: 9, fontWeight: '800' },
  badgeGreen: { backgroundColor: COLORS.greenBg }, badgeTextGreen: { color: COLORS.green },
  badgeAmber: { backgroundColor: COLORS.amberBg }, badgeTextAmber: { color: COLORS.amber },
  badgeRed: { backgroundColor: COLORS.redBg }, badgeTextRed: { color: COLORS.red },
  searchInput: { height: 48, borderRadius: 14, backgroundColor: COLORS.paper, borderWidth: 1, borderColor: COLORS.line, paddingHorizontal: 15, marginBottom: 12, color: COLORS.ink },
  chipRow: { gap: 8, paddingBottom: 12 },
  chip: { borderRadius: 99, paddingHorizontal: 13, paddingVertical: 9, borderWidth: 1, borderColor: COLORS.line, backgroundColor: COLORS.paper },
  chipActive: { backgroundColor: COLORS.wine, borderColor: COLORS.wine },
  chipText: { color: COLORS.muted, fontSize: 11, fontWeight: '600' },
  chipTextActive: { color: COLORS.paper },
  listStack: { gap: 10 },
  listCard: { flexDirection: 'row', alignItems: 'center', gap: 11, padding: 14, borderRadius: 15, backgroundColor: COLORS.paper, borderWidth: 1, borderColor: COLORS.line },
  listIcon: { width: 34, height: 34, borderRadius: 12, textAlign: 'center', textAlignVertical: 'center', overflow: 'hidden', color: COLORS.wine, backgroundColor: COLORS.soft, fontSize: 17 },
  listContent: { flex: 1 },
  listTitle: { color: COLORS.ink, fontSize: 12, fontWeight: '800' },
  listSub: { color: COLORS.muted, fontSize: 10, marginTop: 4 },
  emptyBox: { alignItems: 'center', justifyContent: 'center', paddingVertical: 32, paddingHorizontal: 20, borderRadius: 17, borderWidth: 1, borderStyle: 'dashed', borderColor: '#e6ded8', backgroundColor: COLORS.paper, gap: 9 },
  emptyIcon: { fontSize: 27, color: COLORS.wine },
  emptyTitle: { color: COLORS.ink, fontSize: 15, fontWeight: '800', textAlign: 'center' },
  emptyMessage: { color: COLORS.muted, fontSize: 12, textAlign: 'center', lineHeight: 18, marginBottom: 5 },
  digitalCard: { borderRadius: 22, backgroundColor: COLORS.wine, padding: 21, marginTop: 2 },
  digitalHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  cardLabel: { color: '#e4bec5', fontSize: 9, letterSpacing: 1, fontWeight: '700' },
  cardName: { marginTop: 30, marginBottom: 19 },
  cardHolder: { color: COLORS.paper, fontSize: 22, fontWeight: '800', marginTop: 5 },
  idGrid: { flexDirection: 'row', flexWrap: 'wrap', rowGap: 18 },
  idFact: { width: '50%' },
  idValue: { color: COLORS.paper, fontSize: 12, fontWeight: '700', marginTop: 5 },
  qrBox: { alignSelf: 'center', marginTop: 23, backgroundColor: COLORS.paper, padding: 10, borderRadius: 12 },
  qrPayload: { color: '#f0dfe2', textAlign: 'center', fontSize: 9, marginTop: 12 },
  cardFoot: { color: '#f0dfe2', textAlign: 'center', fontSize: 10, marginTop: 12 },
  refreshButton: { marginTop: 14 },
  profileCard: { borderRadius: 18, padding: 17, backgroundColor: COLORS.paper, borderWidth: 1, borderColor: COLORS.line },
  profileHeader: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingBottom: 15, borderBottomWidth: 1, borderBottomColor: COLORS.line },
  profileAvatar: { width: 48, height: 48, borderRadius: 16, backgroundColor: COLORS.soft, alignItems: 'center', justifyContent: 'center' },
  profileName: { color: COLORS.ink, fontSize: 15, fontWeight: '800' },
  profileEmail: { color: COLORS.muted, fontSize: 11, marginTop: 4 },
  profileFact: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingTop: 13 },
  profileFactLabel: { color: COLORS.muted, fontSize: 11 },
  profileFactValue: { color: COLORS.ink, fontSize: 11, fontWeight: '700', maxWidth: '58%', textAlign: 'right', textTransform: 'capitalize' },
  profileButton: { marginTop: 10 },
  serverSummary: { color: COLORS.muted, fontSize: 10, textAlign: 'center', marginTop: 8 },
  tabBar: { position: 'absolute', bottom: 0, left: 0, right: 0, minHeight: 68, paddingBottom: Platform.OS === 'ios' ? 30 : 8, paddingTop: 7, backgroundColor: '#fffffff2', borderTopWidth: 1, borderTopColor: COLORS.line, flexDirection: 'row', justifyContent: 'space-around' },
  tabItem: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 2 },
  tabIcon: { color: '#88828a', fontSize: 20, lineHeight: 24 },
  tabLabel: { color: '#88828a', fontSize: 9, fontWeight: '600' },
  tabActive: { color: COLORS.wine, fontWeight: '800' },
  profileExtra: { display: 'none' },
  authPage: { flex: 1, backgroundColor: COLORS.canvas },
  authScroll: { flexGrow: 1 },
  authHero: { backgroundColor: COLORS.wineDark, paddingTop: Platform.OS === 'ios' ? 62 : 38, paddingHorizontal: 23, paddingBottom: 29 },
  authHeroTitle: { color: COLORS.paper, fontSize: 30, lineHeight: 34, fontWeight: '900', letterSpacing: -0.8, marginTop: 29 },
  authHeroBody: { color: '#edcdd1', fontSize: 13, lineHeight: 20, marginTop: 8, maxWidth: 340 },
  authCard: { flex: 1, paddingHorizontal: 22, paddingTop: 26, paddingBottom: 40, borderTopLeftRadius: 22, borderTopRightRadius: 22, backgroundColor: COLORS.paper, marginTop: -1 },
  authTitle: { color: COLORS.ink, fontSize: 25, fontWeight: '900' },
  authSubtitle: { color: COLORS.muted, fontSize: 13, marginTop: 6, marginBottom: 22 },
  field: { flex: 1, marginBottom: 14, gap: 7 },
  label: { color: '#48434a', fontSize: 11, fontWeight: '800' },
  input: { minHeight: 46, borderWidth: 1, borderColor: '#e7e1dc', borderRadius: 12, paddingHorizontal: 12, paddingVertical: 11, color: COLORS.ink, fontSize: 13, backgroundColor: COLORS.paper },
  passwordFieldWrap: { position: 'relative', justifyContent: 'center' },
  passwordInput: { paddingRight: 85 },
  passwordToggle: { position: 'absolute', right: 9, top: 0, bottom: 0, flexDirection: 'row', alignItems: 'center', gap: 5, paddingHorizontal: 5 },
  passwordToggleGlyph: { fontWeight: '900', fontSize: 23 },
  passwordToggleLabel: { color: COLORS.wine, fontSize: 10, fontWeight: '800' },
  textarea: { minHeight: 92, textAlignVertical: 'top' },
  row: { flexDirection: 'row', gap: 10 },
  choiceRow: { flexDirection: 'row', gap: 8, marginBottom: 14 },
  choice: { flex: 1, borderRadius: 11, borderWidth: 1, borderColor: COLORS.line, alignItems: 'center', paddingVertical: 11 },
  choiceSelected: { backgroundColor: COLORS.soft, borderColor: '#d5aab4' },
  choiceText: { color: COLORS.muted, fontSize: 12, fontWeight: '700', textTransform: 'capitalize' },
  choiceTextSelected: { color: COLORS.wine },
  serverHint: { color: COLORS.muted, fontSize: 10, lineHeight: 15, marginTop: -6, marginBottom: 16 },
  errorBox: { color: COLORS.red, backgroundColor: COLORS.redBg, borderRadius: 11, padding: 11, fontSize: 11, marginBottom: 15 },
  successBox: { color: COLORS.green, backgroundColor: COLORS.greenBg },
  authSwitch: { alignItems: 'center', paddingVertical: 16 },
  rememberRow: { flexDirection: 'row', alignItems: 'center', gap: 9, alignSelf: 'flex-start', paddingVertical: 6 },
  checkBox: { width: 19, height: 19, borderWidth: 1, borderColor: '#c9c1c4', borderRadius: 5, alignItems: 'center', justifyContent: 'center' },
  checkBoxChecked: { backgroundColor: COLORS.wine, borderColor: COLORS.wine },
  checkMark: { color: COLORS.paper, fontSize: 13, fontWeight: '900' },
  authSwitchText: { color: COLORS.muted, fontSize: 12 },
  authSwitchAction: { color: COLORS.wine, fontWeight: '800' },
  modalShade: { flex: 1, justifyContent: 'flex-end', backgroundColor: '#16111677' },
  modalWrap: { maxHeight: '92%' },
  modalCard: { backgroundColor: COLORS.paper, paddingHorizontal: 21, paddingTop: 14, paddingBottom: Platform.OS === 'ios' ? 34 : 22, borderTopLeftRadius: 22, borderTopRightRadius: 22 },
  modalHandle: { width: 42, height: 4, borderRadius: 2, backgroundColor: '#ded7d2', alignSelf: 'center', marginBottom: 17 },
  modalTitle: { color: COLORS.ink, fontSize: 21, fontWeight: '900', marginBottom: 6 },
  modalSub: { color: COLORS.muted, fontSize: 12, lineHeight: 18, marginBottom: 6 },
  modalCancel: { marginTop: 9 },
});
