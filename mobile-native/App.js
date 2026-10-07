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
import * as Brightness from 'expo-brightness';
import { isRunningInExpoGo } from 'expo';
import DateTimePicker, { DateTimePickerAndroid } from '@react-native-community/datetimepicker';
import QRCode from 'react-native-qrcode-svg';

let notificationsModule;
async function getNotificationsModule() {
  // SDK 57 Expo Go throws while importing expo-notifications on Android because
  // remote push-token registration is unavailable there. Local reminder scheduling
  // remains enabled in development builds and standalone apps.
  if (Platform.OS === 'android' && isRunningInExpoGo()) return null;
  if (!notificationsModule) {
    notificationsModule = await import('expo-notifications');
    notificationsModule.setNotificationHandler({
      handleNotification: async () => ({ shouldShowAlert: true, shouldPlaySound: false, shouldSetBadge: false }),
    });
  }
  return notificationsModule;
}

const COLORS = {
  wine: '#730d24', wineDark: '#510919', gold: '#e0b867', ink: '#252129', muted: '#817c82',
  paper: '#ffffff', canvas: '#f7f5f2', line: '#eee9e4', soft: '#f5e9eb', green: '#277955',
  greenBg: '#eaf4ee', amber: '#876015', amberBg: '#fbf4df', red: '#a52c35', redBg: '#fff0f0',
};
const TOKEN_KEY = 'smartlib_access_token';
const API_KEY = 'smartlib_api_base';
const IDLE_TIMEOUT_MS = 15 * 60 * 1000;
const DEFAULT_API = process.env.EXPO_PUBLIC_API_BASE_URL || (Platform.OS === 'android' ? 'http://10.0.2.2:8000' : 'http://127.0.0.1:8000');
const TABS = [
  { id: 'home', icon: '⌂', label: 'Home' },
  { id: 'catalog', icon: '⌕', label: 'Explore' },
  { id: 'borrowings', icon: '▤', label: 'My books' },
  { id: 'updates', icon: '♧', label: 'Updates' },
  { id: 'digital-id', icon: '▣', label: 'Library ID' },
];

function formatLocalDate(value) {
  const date = new Date(value);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function formatLocalTime(value) {
  const date = new Date(value);
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

function formatPickupLabel(value) {
  if (!value) return 'To be arranged';
  return value.includes('T') ? new Date(value).toLocaleString() : value;
}

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
  const [announcements, setAnnouncements] = useState([]);
  const [notifications, setNotifications] = useState([]);
  const [activeThread, setActiveThread] = useState(null);
  const [threadMessages, setThreadMessages] = useState([]);
  const [messageThreads, setMessageThreads] = useState([]);
  const [messageDraft, setMessageDraft] = useState('');
  const [composeMessage, setComposeMessage] = useState({ subject: '', message: '' });
  const [showComposeMessage, setShowComposeMessage] = useState(false);
  const [showAnnouncementEditor, setShowAnnouncementEditor] = useState(false);
  const [announcementDraft, setAnnouncementDraft] = useState({ title: '', body: '' });
  const [assistantVisible, setAssistantVisible] = useState(false);
  const [assistantDraft, setAssistantDraft] = useState('');
  const [assistantMessages, setAssistantMessages] = useState([]);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('All');
  const [availabilityFilter, setAvailabilityFilter] = useState('All');
  const [selectedBook, setSelectedBook] = useState(null);
  const [pickupAt, setPickupAt] = useState(() => { const date = new Date(); date.setDate(date.getDate() + 1); date.setHours(9, 0, 0, 0); return date; });
  const [iosPickerMode, setIosPickerMode] = useState(null);
  const [pickupDateChosen, setPickupDateChosen] = useState(false);
  const [pickupTimeChosen, setPickupTimeChosen] = useState(false);
  const [selectedBorrowing, setSelectedBorrowing] = useState(null);
  const [showSuggestion, setShowSuggestion] = useState(false);
  const [suggestion, setSuggestion] = useState({ title: '', author: '', publisher_isbn: '', justification: '' });
  const [showChangePassword, setShowChangePassword] = useState(false);
  const [changePasswordForm, setChangePasswordForm] = useState({ current_password: '', new_password: '', confirm_password: '' });
  const idleTimer = useRef(null);
  const lastActiveAt = useRef(Date.now());
  const savedBrightness = useRef(null);
  const notificationIdsSeen = useRef(null);
  const dueAlertsShown = useRef(new Set());

  const request = useCallback(async (path, options = {}, authToken = token, base = apiBase) => {
    const headers = { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}), ...options.headers };
    let response;
    try {
      response = await fetch(`${base.replace(/\/+$/, '')}${path}`, { ...options, headers });
    } catch {
      throw new Error('Can’t connect to EVSU SmartLib right now. Check your connection and try again.');
    }
    const data = response.status === 204 ? null : await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 401 && authToken) {
        await SecureStore.deleteItemAsync(TOKEN_KEY);
        setToken(null); setUser(null); setScreen('auth');
      }
      const detail = Array.isArray(data?.detail) ? data.detail.map((item) => item.msg).filter(Boolean).join('\n') : data?.detail;
      throw new Error(detail || (response.status >= 500
        ? `The library server returned an error (${response.status}). Please try again in a moment.`
        : `The request was not accepted (${response.status}). Please check the details and try again.`));
    }
    return data;
  }, [apiBase, token]);

  useEffect(() => {
    let alive = true;
    (async () => {
      const [savedBase, savedToken] = await Promise.all([__DEV__ ? SecureStore.getItemAsync(API_KEY) : Promise.resolve(null), SecureStore.getItemAsync(TOKEN_KEY)]);
      if (!alive) return;
      if (__DEV__ && savedBase) { setApiBase(savedBase); setApiDraft(savedBase); }
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
  const loadBorrowings = useCallback(async () => {
    const rows = await request('/mobile/my-borrowings');
    setBorrowings(rows);
    const today = formatLocalDate(new Date());
    for (const entry of rows) {
      if (!['APPROVED', 'CHECKED_OUT', 'OVERDUE'].includes(entry.status) || !entry.due_date) continue;
      const dueDate = formatLocalDate(entry.due_date);
      const alertKey = `${entry.id}:${dueDate}`;
      if (dueDate <= today && !dueAlertsShown.current.has(alertKey)) {
        dueAlertsShown.current.add(alertKey);
        Alert.alert(dueDate < today ? 'Library book overdue' : 'Book due today', `${entry.book_title || 'Your borrowed book'} ${dueDate < today ? 'is past its due date. Please contact the library about returning it.' : 'is due today. Please return it to the library.'}`);
      }
    }
    try { await syncDueReminders(rows); } catch { /* Keep the library screen usable if notifications are unavailable. */ }
  }, [request]);
  const loadDigitalId = useCallback(async () => setDigitalId(await request('/mobile/profile/digital-id')), [request]);
  const loadLibraryUpdates = useCallback(async () => {
    const [posts, inbox] = await Promise.all([request('/mobile/announcements'), request('/mobile/notifications')]);
    const items = inbox.items || [];
    if (notificationIdsSeen.current === null) notificationIdsSeen.current = new Set(items.map((item) => item.id));
    else {
      const freshApprovals = items.filter((item) => !notificationIdsSeen.current.has(item.id) && item.kind === 'borrowing' && /approved|approval/i.test(`${item.title} ${item.body}`));
      freshApprovals.forEach((item) => notificationIdsSeen.current.add(item.id));
      if (freshApprovals.length) Alert.alert('Library request updated', freshApprovals.map((item) => item.body || item.title).join('\n'));
      items.forEach((item) => notificationIdsSeen.current.add(item.id));
    }
    setAnnouncements(posts); setNotifications(items);
  }, [request]);
  const isStaff = ['admin', 'superadmin', 'librarian'].includes(user?.role);
  const loadMessageThreads = useCallback(async () => setMessageThreads(await request(isStaff ? '/admin/messages' : '/mobile/messages')), [request, isStaff]);
  const loadMessageThread = useCallback(async (threadId) => {
    const data = await request(`${isStaff ? '/admin' : '/mobile'}/messages/${threadId}`);
    setActiveThread(data.thread); setThreadMessages(data.messages || []);
  }, [request, isStaff]);

  useEffect(() => {
    if (screen !== 'app' || !token) return;
    let alive = true;
    const timer = setTimeout(async () => {
      setBusy(true);
      try {
        if (tab === 'home' || tab === 'catalog') await loadBooks(tab === 'catalog' ? query : '');
        if (tab === 'home' || tab === 'borrowings') await loadBorrowings();
        if (tab === 'digital-id') await loadDigitalId();
        if (tab === 'home' || tab === 'updates') await loadLibraryUpdates();
      } catch (error) { if (alive) Alert.alert('Could not refresh', error.message); }
      finally { if (alive) setBusy(false); }
    }, tab === 'catalog' ? 220 : 0);
    return () => { alive = false; clearTimeout(timer); };
  }, [screen, tab, token, query, loadBooks, loadBorrowings, loadDigitalId, loadLibraryUpdates]);

  useEffect(() => {
    if (screen !== 'app' || tab !== 'messages') return;
    const refresh = () => (activeThread ? loadMessageThread(activeThread.id) : loadMessageThreads()).catch((error) => Alert.alert('Messages unavailable', error.message));
    refresh();
    const timer = setInterval(refresh, 10000);
    return () => clearInterval(timer);
  }, [screen, tab, activeThread?.id, loadMessageThread, loadMessageThreads]);

  useEffect(() => {
    if (screen !== 'app') return;
    const timer = setInterval(() => loadLibraryUpdates().catch(() => {}), 30000);
    return () => clearInterval(timer);
  }, [screen, loadLibraryUpdates]);

  const categories = useMemo(() => ['All', ...new Set(books.map((book) => book.category).filter(Boolean))], [books]);
  const visibleBooks = useMemo(() => books.filter((book) => (category === 'All' || book.category === category) && (availabilityFilter === 'All' || (availabilityFilter === 'Available' ? Number(book.available_copies) > 0 : Number(book.available_copies) <= 0))), [books, category, availabilityFilter]);

  async function saveApiBase() {
    const clean = apiDraft.trim().replace(/\/+$/, '');
    if (!/^https?:\/\/[^\s]+/.test(clean)) { setAuthError('Enter a valid library server address.'); return null; }
    setApiBase(clean); setApiDraft(clean); setAuthError('');
    if (__DEV__) await SecureStore.setItemAsync(API_KEY, clean);
    return clean;
  }

  async function syncDueReminders(rows) {
    const Notifications = await getNotificationsModule();
    if (!Notifications) return;
    const scheduled = await Notifications.getAllScheduledNotificationsAsync();
    for (const item of scheduled) {
      if (item.content.data?.smartlibDueReminder) await Notifications.cancelScheduledNotificationAsync(item.identifier);
    }
    const loans = rows.filter((entry) => ['APPROVED', 'CHECKED_OUT'].includes(entry.status) && entry.due_date);
    if (!loans.length) return;
    if (Platform.OS === 'android') await Notifications.setNotificationChannelAsync('library-returns', { name: 'Library return reminders', importance: Notifications.AndroidImportance.DEFAULT });
    let permission = await Notifications.getPermissionsAsync();
    if (permission.status !== 'granted' && permission.canAskAgain) permission = await Notifications.requestPermissionsAsync();
    if (permission.status !== 'granted') return;
    const now = Date.now();
    for (const entry of loans) {
      const due = new Date(entry.due_date);
      const reminders = [
        { date: new Date(due.getTime() - 24 * 60 * 60 * 1000), title: 'Your library book is due tomorrow', body: `${entry.book_title || 'A borrowed book'} is due soon. Plan your return to the library.` },
        { date: due, title: 'Library book due today', body: `${entry.book_title || 'A borrowed book'} is due today. Return it on time to avoid late fines.` },
        { date: new Date(due.getTime() + 24 * 60 * 60 * 1000), title: 'Library book return is overdue', body: `${entry.book_title || 'A borrowed book'} is past its due date. Contact the library about returning it and any late fine.` },
      ];
      for (const reminder of reminders) {
        if (reminder.date.getTime() <= now) continue;
        await Notifications.scheduleNotificationAsync({ content: { title: reminder.title, body: reminder.body, data: { smartlibDueReminder: true, borrowingId: entry.id }, ...(Platform.OS === 'android' ? { channelId: 'library-returns' } : {}) }, trigger: { type: Notifications.SchedulableTriggerInputTypes.DATE, date: reminder.date } });
      }
    }
  }

  useEffect(() => {
    let active = true;
    if (screen === 'app' && tab === 'digital-id') {
      (async () => {
        try {
          const current = await Brightness.getBrightnessAsync();
          if (!active) return;
          savedBrightness.current = current;
          await Brightness.setBrightnessAsync(1);
        } catch { /* Device or OS may not allow app-level brightness control. */ }
      })();
    }
    return () => {
      active = false;
      if (savedBrightness.current !== null) {
        const previous = savedBrightness.current;
        savedBrightness.current = null;
        Brightness.setBrightnessAsync(previous).catch(() => {});
      }
    };
  }, [screen, tab]);

  async function submitAuth(form) {
    setBusy(true); setAuthError('');
    try {
      const base = __DEV__ ? await saveApiBase() : apiBase;
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
    if (!pickupDateChosen || !pickupTimeChosen) { Alert.alert('Choose a reservation date and time', 'Select both a pickup date and a pickup time before sending your request.'); return; }
    if (pickupAt.getTime() <= Date.now()) { Alert.alert('Choose a future pickup time', 'Select a date and time after now.'); return; }
    setBusy(true);
    try {
      await request('/mobile/borrow', { method: 'POST', body: JSON.stringify({ accession_no: selectedBook.accession_no, pickup_date: formatLocalDate(pickupAt), pickup_time: formatLocalTime(pickupAt) }) });
      setSelectedBook(null); await loadBorrowings(); setTab('borrowings');
      Alert.alert('Request sent', 'The library has received your borrow request.');
    } catch (error) { Alert.alert('Request failed', error.message); }
    finally { setBusy(false); }
  }

  function choosePickup(mode) {
    if (Platform.OS === 'android') {
      DateTimePickerAndroid.open({ value: pickupAt, mode, minimumDate: mode === 'date' ? new Date() : undefined, onChange: (event, value) => {
        if (event.type !== 'set' || !value) return;
        setPickupAt(value);
        if (mode === 'date') {
          setPickupDateChosen(true);
          setTimeout(() => DateTimePickerAndroid.open({ value, mode: 'time', onChange: (timeEvent, timeValue) => { if (timeEvent.type === 'set' && timeValue) { setPickupAt(timeValue); setPickupTimeChosen(true); } } }), 250);
        } else setPickupTimeChosen(true);
      } });
    } else setIosPickerMode(mode);
  }

  function openBorrowRequest(book) {
    const nextDay = new Date(); nextDay.setDate(nextDay.getDate() + 1); nextDay.setHours(9, 0, 0, 0);
    setPickupAt(nextDay); setPickupDateChosen(false); setPickupTimeChosen(false); setSelectedBook(book);
    if (Platform.OS === 'ios') setIosPickerMode('datetime');
    else setTimeout(() => DateTimePickerAndroid.open({ value: nextDay, mode: 'date', minimumDate: new Date(), onChange: (event, dateValue) => {
      if (event.type !== 'set' || !dateValue) return;
      setPickupAt(dateValue); setPickupDateChosen(true);
      setTimeout(() => DateTimePickerAndroid.open({ value: dateValue, mode: 'time', onChange: (timeEvent, timeValue) => { if (timeEvent.type === 'set' && timeValue) { setPickupAt(timeValue); setPickupTimeChosen(true); } } }), 250);
    } }), 350);
  }

  async function cancelBorrowRequest(entry) {
    try {
      await request(`/mobile/borrow/${entry.id}/cancel`, { method: 'POST' });
      setSelectedBorrowing(null); await loadBorrowings();
      Alert.alert('Request cancelled', 'Your pending borrow request has been cancelled.');
    } catch (error) { Alert.alert('Could not cancel request', error.message); }
  }

  async function changePassword() {
    const { current_password, new_password, confirm_password } = changePasswordForm;
    if (new_password !== confirm_password) { Alert.alert('Passwords do not match', 'Enter the same new password in both fields.'); return; }
    if (new_password.length < 8 || new_password.length > 12 || !/[a-z]/.test(new_password) || !/[A-Z]/.test(new_password) || !/\d/.test(new_password) || !/[^A-Za-z0-9]/.test(new_password)) {
      Alert.alert('Password does not meet the policy', 'Use 8–12 characters with an uppercase letter, lowercase letter, number, and symbol.'); return;
    }
    setBusy(true);
    try {
      const result = await request('/auth/change-password', { method: 'POST', body: JSON.stringify({ current_password, new_password }) });
      setShowChangePassword(false); setChangePasswordForm({ current_password: '', new_password: '', confirm_password: '' });
      await SecureStore.deleteItemAsync(TOKEN_KEY); setToken(null); setUser(null); setScreen('auth'); setAuthMode('login');
      Alert.alert('Password changed', result.message || 'Sign in again with your new password.');
    } catch (error) { Alert.alert('Could not change password', error.message); }
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

  async function startConversation() {
    if (!composeMessage.subject.trim() || !composeMessage.message.trim()) { Alert.alert('Add a subject and message', 'Tell the library team what you need help with.'); return; }
    setBusy(true);
    try {
      const created = await request('/mobile/messages', { method: 'POST', body: JSON.stringify({ subject: composeMessage.subject.trim(), message: composeMessage.message.trim() }) });
      setComposeMessage({ subject: '', message: '' }); setShowComposeMessage(false); setActiveThread(created); setThreadMessages([]); setTab('messages');
    } catch (error) { Alert.alert('Could not send message', error.message); }
    finally { setBusy(false); }
  }

  async function sendThreadReply() {
    const body = messageDraft.trim();
    if (!body || !activeThread) return;
    setMessageDraft('');
    try {
      await request(`${isStaff ? '/admin' : '/mobile'}/messages/${activeThread.id}/reply`, { method: 'POST', body: JSON.stringify({ message: body }) });
      await loadMessageThread(activeThread.id);
    } catch (error) { setMessageDraft(body); Alert.alert('Message not sent', error.message); }
  }

  async function askAssistant() {
    const question = assistantDraft.trim();
    if (!question) return;
    setAssistantDraft('');
    setAssistantMessages((items) => [...items, { from: 'you', text: question }]);
    try {
      const data = await request('/mobile/assistant', { method: 'POST', body: JSON.stringify({ message: question }) });
      setAssistantMessages((items) => [...items, { from: 'assistant', text: data.reply, books: data.books || [] }]);
    } catch (error) { setAssistantMessages((items) => [...items, { from: 'assistant', text: error.message }]); }
  }

  async function markNotificationRead(item) {
    if (!item.read) {
      try {
        await request(`/mobile/notifications/${item.id}/read`, { method: 'POST' });
        setNotifications((items) => items.map((row) => row.id === item.id ? { ...row, read: true } : row));
      } catch (error) { Alert.alert('Could not update notification', error.message); return; }
    }
    if (item.kind === 'message') { setActiveThread(null); changeTab('messages'); }
    if (item.kind === 'borrowing') changeTab('borrowings');
  }

  async function publishAnnouncement() {
    if (!announcementDraft.title.trim() || !announcementDraft.body.trim()) { Alert.alert('Add announcement details', 'Enter a title and message before publishing.'); return; }
    setBusy(true);
    try {
      await request('/admin/announcements', { method: 'POST', body: JSON.stringify({ title: announcementDraft.title.trim(), body: announcementDraft.body.trim() }) });
      setAnnouncementDraft({ title: '', body: '' }); setShowAnnouncementEditor(false); await loadLibraryUpdates();
      Alert.alert('Published', 'The announcement and reader notifications have been sent.');
    } catch (error) { Alert.alert('Could not publish', error.message); }
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

  if (screen === 'loading') return <View style={styles.loading}><StatusBar barStyle="dark-content" /><Image source={require('./assets/evsu-smartlib-logo.jpg')} style={styles.splashLogo} accessibilityLabel="EVSU OC SmartLib logo" /><ActivityIndicator color={COLORS.wine} size="large" /><Text style={styles.helperText}>Opening your library…</Text></View>;
  if (screen === 'auth') return <AuthScreen apiBase={apiDraft} onApiChange={setApiDraft} onSubmit={submitAuth} mode={authMode} setMode={(mode) => { setAuthMode(mode); setAuthError(''); }} error={authError} busy={busy} />;

  const firstName = (user?.full_name || 'reader').trim().split(/\s+/)[0];
  const unreadNotifications = notifications.filter((item) => !item.read).length;
  return <View style={styles.app} onTouchStart={resetIdleTimer}><StatusBar barStyle="dark-content" /><View style={styles.topbar}><View style={styles.brandLine}><Image source={require('./assets/evsu-smartlib-logo.jpg')} style={styles.brandLogoImage} accessibilityLabel="EVSU OC SmartLib logo" /><Text style={styles.brandText}>SmartLib</Text></View><View style={styles.topbarActions}><Pressable style={styles.headerAction} accessibilityLabel="Open messages" onPress={() => { setActiveThread(null); changeTab('messages'); }}><Text style={styles.headerActionGlyph}>✉</Text></Pressable><Pressable style={styles.headerAction} accessibilityLabel="Ask the library assistant" onPress={() => setAssistantVisible(true)}><Text style={styles.headerActionGlyph}>✦</Text></Pressable><Pressable style={styles.headerAction} accessibilityLabel="Open notifications" onPress={() => changeTab('updates')}><Text style={styles.headerActionGlyph}>♧</Text>{unreadNotifications > 0 && <View style={styles.unreadDot} />}</Pressable><Pressable style={styles.avatar} onPress={() => setTab('profile')}><Text style={styles.avatarText}>{(user?.full_name || 'S').split(/\s+/).slice(0, 2).map((part) => part[0]).join('').toUpperCase()}</Text></Pressable></View></View>
    <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={styles.scrollContent}>
      {tab === 'home' && <HomeScreen firstName={firstName} user={user} books={books} borrowings={borrowings} announcements={announcements} busy={busy} onExplore={() => changeTab('catalog')} onTab={changeTab} onBorrow={openBorrowRequest} onAssistant={() => setAssistantVisible(true)} onMessages={() => { setActiveThread(null); changeTab('messages'); }} />}
      {tab === 'catalog' && <CatalogScreen books={visibleBooks} categories={categories} category={category} onCategory={setCategory} query={query} onQuery={setQuery} availabilityFilter={availabilityFilter} setAvailabilityFilter={setAvailabilityFilter} busy={busy} onBorrow={openBorrowRequest} />}
      {tab === 'borrowings' && <BorrowingsScreen borrowings={borrowings} busy={busy} onRefresh={loadBorrowings} onExplore={() => changeTab('catalog')} onSelect={setSelectedBorrowing} />}
      {tab === 'digital-id' && <DigitalIdScreen digitalId={digitalId} busy={busy} onRefresh={loadDigitalId} />}
      {tab === 'updates' && <UpdatesScreen announcements={announcements} notifications={notifications} isStaff={isStaff} onPublish={() => setShowAnnouncementEditor(true)} onRead={markNotificationRead} onRefresh={loadLibraryUpdates} />}
      {tab === 'messages' && <MessagesScreen user={user} isStaff={isStaff} threads={messageThreads} activeThread={activeThread} messages={threadMessages} draft={messageDraft} setDraft={setMessageDraft} onOpen={loadMessageThread} onBack={() => { setActiveThread(null); loadMessageThreads(); }} onSend={sendThreadReply} onCompose={() => setShowComposeMessage(true)} />}
      {tab === 'profile' && <ProfileScreen user={user} onSuggest={() => setShowSuggestion(true)} onMessages={() => { setActiveThread(null); changeTab('messages'); }} onUpdates={() => changeTab('updates')} onDigitalId={() => changeTab('digital-id')} onSignOut={signOut} onChangePassword={() => setShowChangePassword(true)} onChangeApi={() => { setApiDraft(apiBase); setScreen('server-settings'); }} />}
    </ScrollView>
    <View style={styles.tabBar}>{TABS.map((item) => <Pressable key={item.id} onPress={() => changeTab(item.id)} style={styles.tabItem}><Text style={[styles.tabIcon, tab === item.id && styles.tabActive]}>{item.icon}</Text><Text style={[styles.tabLabel, tab === item.id && styles.tabActive]}>{item.label}</Text></Pressable>)}</View>
    <BorrowModal book={selectedBook} pickupAt={pickupAt} dateChosen={pickupDateChosen} timeChosen={pickupTimeChosen} onChoosePickup={choosePickup} iosPickerMode={iosPickerMode} setIosPickerMode={setIosPickerMode} onPickupChange={(value, mode) => { setPickupAt(value); if (mode === 'datetime' || mode === 'date') setPickupDateChosen(true); if (mode === 'datetime' || mode === 'time') setPickupTimeChosen(true); }} onClose={() => { setSelectedBook(null); setIosPickerMode(null); }} onSubmit={requestBook} busy={busy} />
    <BorrowingDetailsModal entry={selectedBorrowing} onClose={() => setSelectedBorrowing(null)} onCancel={() => selectedBorrowing && Alert.alert('Cancel borrow request?', 'This will withdraw your pending request.', [{ text: 'Keep request', style: 'cancel' }, { text: 'Cancel request', style: 'destructive', onPress: () => cancelBorrowRequest(selectedBorrowing) }])} />
    <ChangePasswordModal visible={showChangePassword} value={changePasswordForm} onChange={(key, value) => setChangePasswordForm((current) => ({ ...current, [key]: value }))} onClose={() => setShowChangePassword(false)} onSubmit={changePassword} busy={busy} />
    <SuggestionModal visible={showSuggestion} suggestion={suggestion} setSuggestion={setSuggestion} onClose={() => setShowSuggestion(false)} onSubmit={sendSuggestion} busy={busy} />
    <ComposeMessageModal visible={showComposeMessage} value={composeMessage} onChange={setComposeMessage} onClose={() => setShowComposeMessage(false)} onSubmit={startConversation} busy={busy} />
    <AnnouncementModal visible={showAnnouncementEditor} value={announcementDraft} onChange={setAnnouncementDraft} onClose={() => setShowAnnouncementEditor(false)} onSubmit={publishAnnouncement} busy={busy} />
    <AssistantModal visible={assistantVisible} onClose={() => setAssistantVisible(false)} messages={assistantMessages} draft={assistantDraft} setDraft={setAssistantDraft} onSend={askAssistant} onExplore={() => { setAssistantVisible(false); changeTab('catalog'); }} onMessage={() => { setAssistantVisible(false); setActiveThread(null); changeTab('messages'); }} />
    {screen === 'server-settings' && <ServerModal value={apiDraft} onChange={setApiDraft} onClose={() => setScreen('app')} onSave={async () => { if (await saveApiBase()) { setScreen('app'); Alert.alert('Saved', 'The development server address has been updated.'); } }} />}
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
  return <KeyboardAvoidingView style={styles.authPage} behavior={Platform.OS === 'ios' ? 'padding' : undefined}><StatusBar barStyle="light-content" /><ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={styles.authScroll}><View style={styles.authHero}><View style={styles.brandLine}><Image source={require('./assets/evsu-smartlib-logo.jpg')} style={styles.brandLogoImage} accessibilityLabel="EVSU OC SmartLib logo" /><Text style={styles.brandTextLight}>EVSU SmartLib</Text></View><Text style={styles.authHeroTitle}>Your campus library, wherever you are.</Text><Text style={styles.authHeroBody}>Find your next read, request a copy, and keep your library card close at hand.</Text></View><View style={styles.authCard}>
    <Text style={styles.authTitle}>{title}</Text><Text style={styles.authSubtitle}>{subtitle}</Text>{error ? <Text style={[styles.errorBox, successful && styles.successBox]}>{error}</Text> : null}
    {register ? <><View style={styles.row}><Field label="First name" value={form.first_name} onChangeText={(value) => update('first_name', value)} style={styles.flex} autoCapitalize="words" /><Field label="Last name" value={form.last_name} onChangeText={(value) => update('last_name', value)} style={styles.flex} autoCapitalize="words" /></View><Field label="Student or staff ID" value={form.username} onChangeText={(value) => update('username', value)} placeholder="Your institutional ID" autoCapitalize="characters" /><Field label="EVSU email" value={form.email} onChangeText={(value) => update('email', value)} placeholder="name@evsu.edu.ph" keyboardType="email-address" /><Text style={styles.label}>Account type</Text><View style={styles.choiceRow}>{['student', 'faculty'].map((role) => <Pressable key={role} onPress={() => update('account_type', role)} style={[styles.choice, form.account_type === role && styles.choiceSelected]}><Text style={[styles.choiceText, form.account_type === role && styles.choiceTextSelected]}>{role === 'student' ? 'Student' : 'Faculty'}</Text></Pressable>)}</View><Field label="Department" value={form.department} onChangeText={(value) => update('department', value)} placeholder="Optional" autoCapitalize="words" /><Field label="Password" value={form.password} onChangeText={(value) => update('password', value)} secureTextEntry placeholder="8–12 characters" /><Text style={styles.serverHint}>Use 8–12 characters with an uppercase letter, lowercase letter, number, and symbol.</Text><Field label="Confirm password" value={form.confirm_password} onChangeText={(value) => update('confirm_password', value)} secureTextEntry placeholder="Enter the password again" /></> : registerOtp || forgotOtp ? <><Field label="6-digit email code" value={form.otp} onChangeText={(value) => update('otp', value.replace(/\D/g, '').slice(0, 6))} placeholder="000000" keyboardType="number-pad" autoCapitalize="none" /><Pressable style={styles.authSwitch} onPress={() => onSubmit({ ...form, resend_otp: true })}><Text style={styles.authSwitchAction}>Send a new code</Text></Pressable></> : forgot ? <Field label="EVSU email" value={form.email} onChangeText={(value) => update('email', value)} placeholder="name@evsu.edu.ph" keyboardType="email-address" /> : newPassword ? <><Field label="New password" value={form.new_password} onChangeText={(value) => update('new_password', value)} secureTextEntry placeholder="8–12 characters" /><Field label="Confirm new password" value={form.confirm_new_password} onChangeText={(value) => update('confirm_new_password', value)} secureTextEntry placeholder="Enter it again" /><Text style={styles.serverHint}>Use 8–12 characters with an uppercase letter, lowercase letter, number, and symbol.</Text></> : <><Field label="Institutional ID or email" value={form.username} onChangeText={(value) => update('username', value)} placeholder="Enter your ID or email" autoCapitalize="none" /><Field label="Password" value={form.password} onChangeText={(value) => update('password', value)} secureTextEntry placeholder="Enter your password" /><Pressable style={styles.authSwitch} onPress={() => setMode('forgot')}><Text style={styles.authSwitchAction}>Forgot password?</Text></Pressable><Pressable style={styles.rememberRow} onPress={() => update('remember_me', !form.remember_me)}><View style={[styles.checkBox, form.remember_me && styles.checkBoxChecked]}>{form.remember_me ? <Text style={styles.checkMark}>✓</Text> : null}</View><Text style={styles.authSwitchText}>Remember me</Text></Pressable></>}
    {__DEV__ && <><Field label="Library server address" value={apiBase} onChangeText={onApiChange} placeholder="http://192.168.1.20:8000" autoCapitalize="none" keyboardType="url" /><Text style={styles.serverHint}>Development only: enter your computer’s Wi-Fi IP and port 8000. Keep the phone and computer on the same network.</Text></>}
    <Button title={busy ? 'Please wait…' : register ? 'Continue to email code' : registerOtp ? 'Verify code & create account' : forgot ? 'Send verification code' : forgotOtp ? 'Verify code' : newPassword ? 'Save new password' : 'Sign in'} disabled={busy} onPress={() => onSubmit(form)} />{authStep && <Pressable onPress={() => setMode('login')} style={styles.authSwitch}><Text style={styles.authSwitchText}>Back to <Text style={styles.authSwitchAction}>sign in</Text></Text></Pressable>}{mode === 'login' && <Pressable onPress={() => setMode('register')} style={styles.authSwitch}><Text style={styles.authSwitchText}>New to SmartLib? <Text style={styles.authSwitchAction}>Create account</Text></Text></Pressable>}</View></ScrollView></KeyboardAvoidingView>;
}

function Greeting({ eyebrow, title, subtitle, right }) {
  return <View style={styles.greeting}><View style={styles.flex}><Text style={styles.eyebrow}>{eyebrow}</Text><Text style={styles.pageTitle}>{title}</Text>{subtitle ? <Text style={styles.pageSubtitle}>{subtitle}</Text> : null}</View>{right}</View>;
}

function BookCard({ book, index, onBorrow }) {
  const available = Number(book.available_copies) > 0;
  const [coverFailed, setCoverFailed] = useState(false);
  const coverColors = ['#c58e79', '#78876c', '#667797', '#aa8749'];
  const coverUri = book.cover_url || (book.isbn ? `https://covers.openlibrary.org/b/isbn/${encodeURIComponent(book.isbn)}-M.jpg?default=false` : null);
  return <View style={styles.bookCard}><View style={[styles.bookCover, { backgroundColor: coverColors[index % coverColors.length] }]}>{coverUri && !coverFailed ? <Image source={{ uri: coverUri }} style={styles.bookCoverImage} resizeMode="cover" onError={() => setCoverFailed(true)} /> : <View style={styles.bookCoverFallback}><Text style={styles.bookCoverGlyph}>▤</Text><View style={styles.bookCoverRule} /></View>}</View><View style={styles.bookInfo}><Text style={styles.bookTitle}>{book.title}</Text><Text style={styles.bookAuthor}>{book.author}</Text><Text style={styles.bookMeta}>{book.category}{book.year_published ? ` · ${book.year_published}` : ''}</Text><Badge tone={available ? 'green' : 'red'}>{available ? `${book.available_copies} available` : 'Borrowed'}</Badge>{available ? <Button title="Request a copy" compact secondary onPress={() => onBorrow(book)} style={styles.bookButton} /> : null}</View></View>;
}

function HomeScreen({ firstName, user, books, borrowings, announcements, busy, onExplore, onTab, onBorrow, onAssistant, onMessages }) {
  const available = books.filter((book) => book.available_copies > 0).length;
  const pending = borrowings.filter((entry) => entry.status === 'PENDING').length;
  return <><Greeting eyebrow="EVSU library · main campus" title={`Hello, ${firstName} 👋`} subtitle="Ready to find something worth reading?" right={<View style={styles.rolePill}><Text style={styles.roleText}>{user?.role}</Text></View>} /><View style={styles.hero}><Text style={styles.heroEyebrow}>A world of knowledge</Text><Text style={styles.heroTitle}>Your next chapter starts here.</Text><Text style={styles.heroSubtitle}>{busy ? 'Updating the collection…' : `${available} books ready to discover`}</Text><Button title="Explore books  →" onPress={onExplore} style={styles.heroButton} textStyle={styles.heroButtonText} /></View><SectionTitle title="Quick access" /><View style={styles.quickGrid}>{[['⌕', 'Find a book', 'Search the catalog', 'catalog'], ['▤', 'My requests', pending ? `${pending} awaiting review` : 'Check your loans', 'borrowings'], ['▣', 'Library ID', 'Digital membership', 'digital-id']].map(([icon, label, sub, target]) => <Pressable key={target} style={styles.quickCard} onPress={() => onTab(target)}><Text style={styles.quickIcon}>{icon}</Text><Text style={styles.quickTitle}>{label}</Text><Text style={styles.quickSub}>{sub}</Text></Pressable>)}</View><View style={styles.serviceRow}><Pressable style={styles.serviceCard} onPress={onAssistant}><Text style={styles.serviceGlyph}>✦</Text><View style={styles.flex}><Text style={styles.quickTitle}>AI Library Assistant</Text><Text style={styles.quickSub}>Ask about books and library services</Text></View><Text style={styles.actionText}>Open</Text></Pressable><Pressable style={styles.serviceCard} onPress={onMessages}><Text style={styles.serviceGlyph}>✉</Text><View style={styles.flex}><Text style={styles.quickTitle}>Message the library</Text><Text style={styles.quickSub}>Talk to an admin or librarian</Text></View><Text style={styles.actionText}>Chat</Text></Pressable></View><SectionTitle title="News & announcements" action="See all →" onAction={() => onTab('updates')} />{announcements.length ? announcements.slice(0, 2).map((post) => <Pressable key={post.id} style={styles.newsCard} onPress={() => onTab('updates')}><Text style={styles.newsEyebrow}>EVSU SMARTLIB · {new Date(post.created_at).toLocaleDateString()}</Text><Text style={styles.newsTitle}>{post.title}</Text><Text style={styles.newsBody} numberOfLines={3}>{post.body}</Text></Pressable>) : <EmptyState icon="▤" title="Library updates will appear here" message="Check back for collection news, events, and service announcements." />}<SectionTitle title="Recently added" action="View all →" onAction={onExplore} />{books.length ? <View style={styles.bookGrid}>{books.slice(0, 4).map((book, index) => <BookCard key={book.id} book={book} index={index} onBorrow={onBorrow} />)}</View> : <EmptyState icon="⌕" title="Catalog is waiting for you" message="The collection will appear here when it loads." />}</>;
}

function CatalogScreen({ books, categories, category, onCategory, query, onQuery, availabilityFilter, setAvailabilityFilter, busy, onBorrow }) {
  const availability = ['All', 'Available', 'Borrowed'];
  return <><Greeting eyebrow="EVSU library collection" title="Explore books" subtitle="Search titles, authors, and subjects." /><TextInput value={query} onChangeText={(text) => { onQuery(text); onCategory('All'); }} placeholder="⌕  What would you like to read?" placeholderTextColor="#918b90" style={styles.searchInput} returnKeyType="search" /><Text style={styles.filterLabel}>Availability</Text><ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chipRow}>{availability.map((item) => <Pressable key={item} onPress={() => setAvailabilityFilter(item)} style={[styles.chip, availabilityFilter === item && styles.chipActive]}><Text style={[styles.chipText, availabilityFilter === item && styles.chipTextActive]}>{item}</Text></Pressable>)}</ScrollView><Text style={styles.filterLabel}>Subject</Text><ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chipRow}>{categories.map((item) => <Pressable key={item} onPress={() => onCategory(item)} style={[styles.chip, category === item && styles.chipActive]}><Text style={[styles.chipText, category === item && styles.chipTextActive]}>{item}</Text></Pressable>)}</ScrollView><SectionTitle title={busy ? 'Updating collection…' : 'The collection'} action={`${books.length} ${books.length === 1 ? 'book' : 'books'}`} />{books.length ? <View style={styles.bookGrid}>{books.map((book, index) => <BookCard key={book.id} book={book} index={index} onBorrow={onBorrow} />)}</View> : <EmptyState icon="⌕" title="No books found" message="Try another availability option, title, author, or subject." />}</>;
}

function BorrowingsScreen({ borrowings, busy, onRefresh, onExplore, onSelect }) {
  const entries = [...borrowings].sort((a, b) => new Date(b.request_date) - new Date(a.request_date));
  return <><Greeting eyebrow="Your library activity" title="My books" subtitle="Borrow requests, due dates, and return reminders." right={<Pressable onPress={onRefresh}><Text style={styles.actionText}>Refresh</Text></Pressable>} /><View style={styles.reminderNote}><Text style={styles.reminderGlyph}>♧</Text><Text style={styles.reminderCopy}>Return reminders are scheduled for the day before and the due date when notifications are enabled.</Text></View><SectionTitle title={busy ? 'Updating…' : 'Borrowing history'} />{entries.length ? <View style={styles.listStack}>{entries.map((entry) => { const status = entry.status || 'PENDING'; const tone = ['PENDING', 'APPROVED'].includes(status) ? 'amber' : ['REJECTED', 'OVERDUE', 'CANCELLED'].includes(status) ? 'red' : 'green'; const due = entry.due_date ? new Date(entry.due_date) : null; const dayStart = new Date(); dayStart.setHours(0, 0, 0, 0); const dueDay = due ? new Date(due.getFullYear(), due.getMonth(), due.getDate()) : null; const daysLeft = dueDay ? Math.ceil((dueDay - dayStart) / 86400000) : null; const dueLabel = !due ? 'Due date not set' : daysLeft < 0 ? `Overdue by ${Math.abs(daysLeft)} ${Math.abs(daysLeft) === 1 ? 'day' : 'days'}` : daysLeft === 0 ? 'Due today' : `Due in ${daysLeft} ${daysLeft === 1 ? 'day' : 'days'}`; return <Pressable accessibilityRole="button" accessibilityLabel={`Details for ${entry.book_title || 'borrow request'}; ${status}`} key={entry.id} style={({ pressed }) => [styles.listCard, pressed && styles.listCardPressed]} onPress={() => onSelect(entry)}><Text style={styles.listIcon}>▤</Text><View style={styles.listContent}><Text style={styles.listTitle}>{entry.book_title || `Book ${entry.book_id}`}</Text><Text style={styles.listSub}>{entry.req_id} · Pickup {formatPickupLabel(entry.pickup_date)}</Text>{['APPROVED', 'CHECKED_OUT', 'OVERDUE'].includes(status) && <Text style={[styles.dueDateText, (daysLeft !== null && daysLeft <= 0) && styles.dueDateLate]}>{dueLabel}{due ? ` · ${due.toLocaleDateString()}` : ''}</Text>}</View><Badge tone={tone}>{status.replaceAll('_', ' ')}</Badge><Text style={styles.settingsChevron}>›</Text></Pressable>; })}</View> : <EmptyState icon="▤" title="No requests yet" message="Requested books will appear here."><Button title="Browse the catalog" secondary onPress={onExplore} /></EmptyState>}</>;
}

function DigitalIdScreen({ digitalId, busy, onRefresh }) {
  if (!digitalId) return <><Greeting eyebrow="Always with you" title="Library ID" subtitle="Your digital EVSU library membership." /><View style={styles.emptyBox}><ActivityIndicator color={COLORS.wine} /><Text style={styles.emptyTitle}>{busy ? 'Loading your library ID…' : 'Could not load your ID'}</Text><Button title="Try again" secondary onPress={onRefresh} /></View></>;
  return <><Greeting eyebrow="Always with you" title="Library ID" subtitle="Show your membership at the library desk." /><View style={styles.digitalCard}><View style={styles.digitalHead}><View style={styles.brandLine}><Image source={require('./assets/evsu-smartlib-logo.jpg')} style={styles.brandLogoImage} accessibilityLabel="EVSU OC SmartLib logo" /><Text style={styles.brandTextLight}>EVSU SmartLib</Text></View><Text style={styles.cardLabel}>MEMBER CARD</Text></View><View style={styles.cardName}><Text style={styles.cardLabel}>CARD HOLDER</Text><Text style={styles.cardHolder}>{digitalId.full_name}</Text></View><View style={styles.idGrid}><IdFact label="Institutional ID" value={digitalId.patron_id} /><IdFact label="Membership" value={digitalId.role} /><IdFact label="Department" value={digitalId.department || 'EVSU Main Campus'} /><IdFact label="Active loans" value={String(digitalId.active_borrows_count)} /></View><View style={styles.qrBox}><QRCode value={digitalId.qr_payload} size={150} color="#241d20" backgroundColor="#ffffff" quietZone={5} /></View><Text style={styles.qrPayload}>{digitalId.qr_payload}</Text><Text style={styles.cardFoot}>{digitalId.is_active ? 'Membership active · brightness raised for scanning' : 'Contact the library desk about your account'}</Text></View><Button title={busy ? 'Refreshing…' : 'Refresh card'} secondary onPress={onRefresh} style={styles.refreshButton} /></>;
}

function IdFact({ label, value }) { return <View style={styles.idFact}><Text style={styles.cardLabel}>{label.toUpperCase()}</Text><Text style={styles.idValue}>{value}</Text></View>; }

function ProfileScreen({ user, onSuggest, onMessages, onUpdates, onDigitalId, onSignOut, onChangePassword, onChangeApi }) {
  return <><Greeting eyebrow="Your account" title="Account" subtitle="Library membership and account settings." /><View style={styles.profileCard}><View style={styles.profileHeader}><View style={styles.profileAvatar}><Text style={styles.avatarText}>{(user?.full_name || 'S').split(/\s+/).slice(0, 2).map((part) => part[0]).join('').toUpperCase()}</Text></View><View style={styles.flex}><Text style={styles.profileName}>{user?.full_name}</Text><Text style={styles.profileEmail}>{user?.email}</Text></View><View style={styles.rolePill}><Text style={styles.roleText}>VERIFIED</Text></View></View><ProfileFact label="Institutional ID" value={user?.username} /><ProfileFact label="Account type" value={user?.role} /><ProfileFact label="Department" value={user?.department || 'EVSU Main Campus'} /></View><SectionTitle title="Library services" /><View style={styles.settingsGroup}><SettingsRow icon="✉" title="Message a librarian" onPress={onMessages} /><SettingsRow icon="♧" title="Notifications & library news" onPress={onUpdates} /><SettingsRow icon="▣" title="Digital library ID" onPress={onDigitalId} /><SettingsRow icon="＋" title="Suggest a book" onPress={onSuggest} /></View><SectionTitle title="About" /><View style={styles.aboutCard}><Text style={styles.aboutTitle}>EVSU SmartLib</Text><Text style={styles.aboutBody}>Your campus library collection, borrowing requests, updates, and librarian support in one place.</Text></View><SectionTitle title="Account management" /><View style={styles.settingsGroup}><SettingsRow icon="⌑" title="Change password" onPress={onChangePassword} />{__DEV__ && <SettingsRow icon="⚙" title="Development server address" onPress={onChangeApi} />}<SettingsRow icon="⇥" title="Sign out" onPress={onSignOut} /></View></>;
}

function ServerModal({ value, onChange, onClose, onSave }) {
  return <Modal visible transparent animationType="fade" onRequestClose={onClose}><View style={styles.modalShade}><View style={styles.modalCard}><Text style={styles.modalTitle}>Development server</Text><Text style={styles.modalSub}>Use the computer’s Wi-Fi IP address and port 8000, such as http://192.168.1.20:8000. The phone and computer must be on the same network.</Text><Field label="Server address" value={value} onChangeText={onChange} keyboardType="url" autoCapitalize="none" /><Button title="Save address" onPress={onSave} /><Button title="Cancel" secondary onPress={onClose} style={styles.modalCancel} /></View></View></Modal>;
}

function SettingsRow({ icon, title, onPress }) { return <Pressable style={styles.settingsRow} onPress={onPress}><Text style={styles.settingsIcon}>{icon}</Text><Text style={styles.settingsTitle}>{title}</Text><Text style={styles.settingsChevron}>›</Text></Pressable>; }

function UpdatesScreen({ announcements, notifications, isStaff, onPublish, onRead, onRefresh }) {
  return <><Greeting eyebrow="Stay in the loop" title="News & notifications" subtitle="Library announcements and updates on your requests." right={<View style={styles.topbarActions}>{isStaff && <Button title="Post news" compact onPress={onPublish} />}<Pressable onPress={onRefresh}><Text style={styles.actionText}>Refresh</Text></Pressable></View>} /><SectionTitle title="Notifications" />{notifications.length ? notifications.map((item) => <Pressable key={item.id} style={[styles.notificationCard, !item.read && styles.notificationUnread]} onPress={() => onRead(item)}><View style={styles.notificationIcon}><Text>{item.kind === 'message' ? '✉' : item.kind === 'borrowing' ? '▤' : '♧'}</Text></View><View style={styles.flex}><Text style={styles.newsTitle}>{item.title}</Text><Text style={styles.newsBody}>{item.body}</Text><Text style={styles.newsEyebrow}>{new Date(item.created_at).toLocaleString()}</Text></View>{!item.read && <View style={styles.unreadDot} />}</Pressable>) : <EmptyState icon="♧" title="You're all caught up" message="Updates about your library requests and messages will show here." />}<SectionTitle title="News & announcements" />{announcements.length ? announcements.map((post) => <View key={post.id} style={styles.newsCard}><Text style={styles.newsEyebrow}>EVSU SMARTLIB · {new Date(post.created_at).toLocaleDateString()}</Text><Text style={styles.newsTitle}>{post.title}</Text><Text style={styles.newsBody}>{post.body}</Text></View>) : <EmptyState icon="▤" title="No announcements yet" message="New library events and service news will appear here." />}</>;
}

function MessagesScreen({ user, isStaff, threads, activeThread, messages, draft, setDraft, onOpen, onBack, onSend, onCompose }) {
  if (activeThread) return <><Greeting eyebrow={isStaff ? `${activeThread.patron_name || 'Library patron'} · ${activeThread.patron_email || ''}` : 'EVSU library support'} title={activeThread.subject} subtitle={activeThread.is_closed ? 'This conversation is closed.' : 'Messages are checked regularly during library hours.'} right={<Pressable onPress={onBack}><Text style={styles.actionText}>Back</Text></Pressable>} /><View style={styles.chatList}>{messages.map((item) => <View key={item.id} style={[styles.chatBubble, item.is_staff ? styles.chatBubbleStaff : styles.chatBubbleUser]}><Text style={styles.chatSender}>{item.is_staff ? 'Library team' : isStaff ? user?.full_name : 'You'}</Text><Text style={styles.chatText}>{item.message}</Text><Text style={styles.chatTime}>{new Date(item.created_at).toLocaleString()}</Text></View>)}</View>{!activeThread.is_closed && <View style={styles.composerRow}><TextInput value={draft} onChangeText={setDraft} placeholder="Write a message…" placeholderTextColor={COLORS.muted} style={[styles.input, styles.composerInput]} multiline /><Button title="Send" onPress={onSend} compact /></View>}</>;
  return <><Greeting eyebrow={isStaff ? 'Library support inbox' : 'Talk to our team'} title={isStaff ? 'Messages' : 'Message the library'} subtitle={isStaff ? 'Reply to student and faculty inquiries.' : 'Ask an admin or librarian without visiting the desk.'} right={!isStaff ? <Button title="New message" compact onPress={onCompose} /> : null} />{threads.length ? <View style={styles.listStack}>{threads.map((item) => <Pressable key={item.id} style={styles.threadCard} onPress={() => onOpen(item.id)}><View style={styles.threadAvatar}><Text style={styles.threadAvatarText}>{isStaff ? (item.patron_name || 'P')[0].toUpperCase() : '✉'}</Text></View><View style={styles.flex}><Text style={styles.newsTitle}>{isStaff ? item.patron_name : item.subject}</Text><Text style={styles.listSub}>{isStaff ? item.subject : item.last_message}</Text><Text style={styles.newsBody} numberOfLines={1}>{item.last_message}</Text></View>{item.unread_count > 0 && <View style={styles.unreadCount}><Text style={styles.unreadCountText}>{item.unread_count}</Text></View>}</Pressable>)}</View> : <EmptyState icon="✉" title={isStaff ? 'No conversations yet' : 'Need help from the library?'} message={isStaff ? 'New patron inquiries will appear in this inbox.' : 'Start a message to ask about loans, services, or library resources.'}>{!isStaff && <Button title="Message a librarian" onPress={onCompose} />}</EmptyState>}</>;
}

function ComposeMessageModal({ visible, value, onChange, onClose, onSubmit, busy }) {
  const set = (key, text) => onChange((current) => ({ ...current, [key]: text }));
  return <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}><View style={styles.modalShade}><KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.modalWrap}><ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={styles.modalCard}><View style={styles.modalHandle} /><Text style={styles.modalTitle}>Message the library</Text><Text style={styles.modalSub}>An admin or librarian can follow up here in the app.</Text><Field label="Subject" value={value.subject} onChangeText={(text) => set('subject', text)} placeholder="What do you need help with?" /><Field label="Your message" value={value.message} onChangeText={(text) => set('message', text)} placeholder="Type your question…" multiline /><Button title={busy ? 'Sending…' : 'Send message'} onPress={onSubmit} disabled={busy} /><Button title="Cancel" secondary onPress={onClose} style={styles.modalCancel} /></ScrollView></KeyboardAvoidingView></View></Modal>;
}

function AnnouncementModal({ visible, value, onChange, onClose, onSubmit, busy }) {
  const set = (key, text) => onChange((current) => ({ ...current, [key]: text }));
  return <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}><View style={styles.modalShade}><KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.modalWrap}><ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={styles.modalCard}><View style={styles.modalHandle} /><Text style={styles.modalTitle}>Publish library news</Text><Text style={styles.modalSub}>Readers will see this update and receive an in-app notification.</Text><Field label="Announcement title" value={value.title} onChangeText={(text) => set('title', text)} /><Field label="Details" value={value.body} onChangeText={(text) => set('body', text)} multiline placeholder="Share library news, events, or service changes…" /><Button title={busy ? 'Publishing…' : 'Publish announcement'} onPress={onSubmit} disabled={busy} /><Button title="Cancel" secondary onPress={onClose} style={styles.modalCancel} /></ScrollView></KeyboardAvoidingView></View></Modal>;
}

function AssistantModal({ visible, onClose, messages, draft, setDraft, onSend, onExplore, onMessage }) {
  return <Modal visible={visible} animationType="slide" onRequestClose={onClose}><View style={styles.assistantPage}><View style={styles.assistantHeader}><View><Text style={styles.assistantTitle}>✦ SmartLib AI Assistant</Text><Text style={styles.assistantSubtitle}>Ask about catalog books and library services</Text></View><Pressable onPress={onClose}><Text style={styles.assistantClose}>Close</Text></Pressable></View><ScrollView contentContainerStyle={styles.assistantConversation} keyboardShouldPersistTaps="handled">{messages.length ? messages.map((item, index) => <View key={`${index}-${item.from}`} style={[styles.assistantBubble, item.from === 'you' ? styles.chatBubbleUser : styles.chatBubbleStaff]}><Text style={styles.chatText}>{item.text}</Text>{item.books?.map((book) => <Pressable key={book.id} style={styles.assistantBook} onPress={onExplore}><Text style={styles.quickTitle}>{book.title}</Text><Text style={styles.quickSub}>{book.author} · {book.available_copies} available</Text></Pressable>)}</View>) : <EmptyState icon="✦" title="Ask your library assistant" message="Try “Find books about biology,” “What is my request status?” or “How do I contact a librarian?”" />}<Button title="Message a librarian" secondary onPress={onMessage} style={styles.profileButton} /></ScrollView><View style={styles.assistantComposer}><TextInput value={draft} onChangeText={setDraft} placeholder="Ask a library question…" placeholderTextColor={COLORS.muted} style={[styles.input, styles.composerInput]} multiline /><Button title="Ask" onPress={onSend} compact /></View><Text style={styles.assistantDisclosure}>Answers use the library catalog and service guidance. Contact a librarian for official decisions.</Text></View></Modal>;
}

function ProfileFact({ label, value }) { return <View style={styles.profileFact}><Text style={styles.profileFactLabel}>{label}</Text><Text style={styles.profileFactValue}>{value}</Text></View>; }

function EmptyState({ icon, title, message, children }) { return <View style={styles.emptyBox}><Text style={styles.emptyIcon}>{icon}</Text><Text style={styles.emptyTitle}>{title}</Text><Text style={styles.emptyMessage}>{message}</Text>{children}</View>; }

function BorrowModal({ book, pickupAt, dateChosen, timeChosen, onChoosePickup, iosPickerMode, setIosPickerMode, onPickupChange, onClose, onSubmit, busy }) {
  const dateLabel = pickupAt.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
  const timeLabel = pickupAt.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  return <Modal visible={Boolean(book)} transparent animationType="slide" onRequestClose={onClose}><View style={styles.modalShade}><KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.modalWrap}><View style={styles.modalCard}><View style={styles.modalHandle} /><Text style={styles.modalTitle}>Reserve a pickup time</Text><Text style={styles.modalSub}>{book?.title}</Text><Text style={styles.modalSub}>{book?.author}</Text><Text style={styles.label}>Choose when you plan to collect this book</Text><View style={styles.row}><Button title={`${dateChosen ? '✓ ' : 'Choose date · '}${dateLabel}`} secondary onPress={() => onChoosePickup('date')} style={styles.flex} textStyle={styles.pickerButtonText} /><Button title={`${timeChosen ? '✓ ' : 'Choose time · '}${timeLabel}`} secondary onPress={() => onChoosePickup('time')} style={styles.flex} textStyle={styles.pickerButtonText} /></View>{Platform.OS === 'ios' && iosPickerMode && <View style={styles.iosPickerWrap}><View style={styles.choiceRow}><Button title="Date & time" compact secondary onPress={() => setIosPickerMode('datetime')} /><Button title="Done" compact onPress={() => setIosPickerMode(null)} /></View><DateTimePicker value={pickupAt} mode={iosPickerMode} display="spinner" minimumDate={new Date()} onChange={(_, value) => { if (value) onPickupChange(value, iosPickerMode); }} /></View>}<Text style={styles.serverHint}>Choose both a date and time to continue. The library will confirm your request.</Text><Button title={busy ? 'Sending request…' : 'Send borrow request'} onPress={onSubmit} disabled={busy || !dateChosen || !timeChosen} /><Button title="Cancel" secondary onPress={onClose} style={styles.modalCancel} /></View></KeyboardAvoidingView></View></Modal>;
}

function BorrowingDetailsModal({ entry, onClose, onCancel }) {
  return <Modal visible={Boolean(entry)} transparent animationType="slide" onRequestClose={onClose}><View style={styles.modalShade}><View style={styles.modalCard}><View style={styles.modalHandle} /><Text style={styles.modalTitle}>Borrow request</Text><Text style={styles.modalSub}>{entry?.book_title || 'Library book'}</Text><View style={styles.detailRows}><ProfileFact label="Request ID" value={entry?.req_id} /><ProfileFact label="Status" value={entry?.status?.replaceAll('_', ' ')} /><ProfileFact label="Pickup" value={formatPickupLabel(entry?.pickup_date)} />{entry?.due_date && <ProfileFact label="Due date" value={new Date(entry.due_date).toLocaleDateString()} />}</View>{entry?.status === 'PENDING' ? <Button title="Cancel borrow request" secondary onPress={onCancel} textStyle={styles.cancelButtonText} /> : <Text style={styles.serverHint}>Only pending requests can be cancelled. For an approved request, please contact the library.</Text>}<Button title="Close" secondary onPress={onClose} style={styles.modalCancel} /></View></View></Modal>;
}

function ChangePasswordModal({ visible, value, onChange, onClose, onSubmit, busy }) {
  return <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}><View style={styles.modalShade}><KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.modalWrap}><View style={styles.modalCard}><View style={styles.modalHandle} /><Text style={styles.modalTitle}>Change password</Text><Text style={styles.modalSub}>Choose a secure password you have not used before.</Text><Field label="Current password" value={value.current_password} onChangeText={(text) => onChange('current_password', text)} secureTextEntry placeholder="Enter your current password" /><Field label="New password" value={value.new_password} onChangeText={(text) => onChange('new_password', text)} secureTextEntry placeholder="8–12 characters" /><Field label="Confirm new password" value={value.confirm_password} onChangeText={(text) => onChange('confirm_password', text)} secureTextEntry placeholder="Enter it again" /><Text style={styles.serverHint}>Use 8–12 characters with an uppercase letter, lowercase letter, number, and symbol. You’ll be signed out after changing it.</Text><Button title={busy ? 'Updating…' : 'Update password'} onPress={onSubmit} disabled={busy} /><Button title="Cancel" secondary onPress={onClose} style={styles.modalCancel} /></View></KeyboardAvoidingView></View></Modal>;
}

function SuggestionModal({ visible, suggestion, setSuggestion, onClose, onSubmit, busy }) {
  const change = (key, value) => setSuggestion((current) => ({ ...current, [key]: value }));
  return <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}><View style={styles.modalShade}><KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={styles.modalWrap}><ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={styles.modalCard}><View style={styles.modalHandle} /><Text style={styles.modalTitle}>Suggest a book</Text><Text style={styles.modalSub}>Help grow the EVSU library collection.</Text><Field label="Book title" value={suggestion.title} onChangeText={(value) => change('title', value)} /><Field label="Author" value={suggestion.author} onChangeText={(value) => change('author', value)} /><Field label="Publisher or ISBN (optional)" value={suggestion.publisher_isbn} onChangeText={(value) => change('publisher_isbn', value)} /><Field label="Why should the library add this book?" value={suggestion.justification} onChangeText={(value) => change('justification', value)} multiline /><Button title={busy ? 'Sending…' : 'Send suggestion'} onPress={onSubmit} disabled={busy} /><Button title="Cancel" secondary onPress={onClose} style={styles.modalCancel} /></ScrollView></KeyboardAvoidingView></View></Modal>;
}

const styles = StyleSheet.create({
  app: { flex: 1, backgroundColor: COLORS.canvas, paddingTop: Platform.OS === 'ios' ? 44 : 8 },
  loading: { flex: 1, backgroundColor: COLORS.canvas, alignItems: 'center', justifyContent: 'center', gap: 14 },
  helperText: { color: COLORS.muted, fontSize: 13 },
  splashLogo: { width: 148, height: 148, borderRadius: 74, marginBottom: 8 },
  topbar: { height: 62, backgroundColor: COLORS.paper, paddingHorizontal: 19, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderBottomWidth: 1, borderBottomColor: COLORS.line },
  topbarActions: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  headerAction: { width: 34, height: 38, alignItems: 'center', justifyContent: 'center', position: 'relative' },
  headerActionGlyph: { color: COLORS.wine, fontSize: 20, fontWeight: '800' },
  unreadDot: { width: 9, height: 9, borderRadius: 5, backgroundColor: COLORS.red, borderWidth: 1, borderColor: COLORS.paper, position: 'absolute', right: 4, top: 5 },
  brandLine: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  brandLogoImage: { height: 40, width: 40, borderRadius: 12, backgroundColor: COLORS.paper },
  brandText: { color: COLORS.ink, fontWeight: '800', fontSize: 16 },
  brandTextLight: { color: COLORS.paper, fontWeight: '800', fontSize: 15 },
  avatar: { width: 38, height: 38, borderRadius: 19, backgroundColor: COLORS.soft, alignItems: 'center', justifyContent: 'center' },
  avatarText: { color: COLORS.wine, fontWeight: '800', fontSize: 13 },
  scrollContent: { paddingHorizontal: 18, paddingTop: 22, paddingBottom: 190 },
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
  serviceRow: { gap: 8, marginBottom: 18 },
  serviceCard: { flexDirection: 'row', alignItems: 'center', gap: 10, borderRadius: 14, padding: 12, backgroundColor: COLORS.paper, borderWidth: 1, borderColor: COLORS.line },
  serviceGlyph: { width: 34, height: 34, borderRadius: 12, textAlign: 'center', textAlignVertical: 'center', overflow: 'hidden', color: COLORS.wine, backgroundColor: COLORS.soft, fontSize: 18 },
  newsCard: { padding: 15, borderRadius: 16, backgroundColor: COLORS.paper, borderWidth: 1, borderColor: COLORS.line, marginBottom: 10 },
  newsEyebrow: { color: COLORS.wine, fontSize: 9, letterSpacing: 0.7, fontWeight: '800', marginBottom: 6 },
  newsTitle: { color: COLORS.ink, fontSize: 13, fontWeight: '800' },
  newsBody: { color: COLORS.muted, fontSize: 11, lineHeight: 17, marginTop: 5 },
  bookGrid: { gap: 11 },
  bookCard: { flexDirection: 'row', gap: 13, backgroundColor: COLORS.paper, borderColor: COLORS.line, borderWidth: 1, borderRadius: 16, padding: 13 },
  bookCover: { width: 60, height: 82, borderRadius: 8, alignItems: 'center', justifyContent: 'center', overflow: 'hidden' },
  bookCoverImage: { width: '100%', height: '100%' },
  bookCoverFallback: { width: 45, height: 65, borderRadius: 4, borderLeftWidth: 4, borderLeftColor: '#ffffffb0', backgroundColor: '#ffffff22', alignItems: 'center', justifyContent: 'center', gap: 3 },
  bookCoverGlyph: { color: '#ffffffef', fontSize: 23, fontWeight: '600' },
  bookCoverRule: { height: 2, width: 20, borderRadius: 2, backgroundColor: '#ffffffa0' },
  bookInfo: { flex: 1, minWidth: 0, alignItems: 'flex-start', gap: 2 },
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
  filterLabel: { color: COLORS.muted, fontSize: 10, fontWeight: '800', marginBottom: 7, textTransform: 'uppercase', letterSpacing: 0.7 },
  listStack: { gap: 10 },
  listCard: { flexDirection: 'row', alignItems: 'center', gap: 11, padding: 14, borderRadius: 15, backgroundColor: COLORS.paper, borderWidth: 1, borderColor: COLORS.line },
  listCardPressed: { opacity: 0.78, borderColor: '#d5aab4' },
  listIcon: { width: 34, height: 34, borderRadius: 12, textAlign: 'center', textAlignVertical: 'center', overflow: 'hidden', color: COLORS.wine, backgroundColor: COLORS.soft, fontSize: 17 },
  listContent: { flex: 1 },
  listTitle: { color: COLORS.ink, fontSize: 12, fontWeight: '800' },
  listSub: { color: COLORS.muted, fontSize: 10, marginTop: 4 },
  dueDateText: { color: COLORS.green, fontSize: 10, fontWeight: '800', marginTop: 5, lineHeight: 15 },
  dueDateLate: { color: COLORS.red },
  reminderNote: { flexDirection: 'row', gap: 9, alignItems: 'center', padding: 12, borderRadius: 13, backgroundColor: COLORS.soft, marginBottom: 16 },
  reminderGlyph: { color: COLORS.wine, fontSize: 18 },
  reminderCopy: { flex: 1, color: COLORS.wine, fontSize: 10, lineHeight: 15 },
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
  settingsGroup: { borderRadius: 17, backgroundColor: COLORS.paper, borderWidth: 1, borderColor: COLORS.line, overflow: 'hidden', marginBottom: 18 },
  settingsRow: { minHeight: 60, flexDirection: 'row', alignItems: 'center', gap: 13, paddingHorizontal: 15, borderBottomWidth: 1, borderBottomColor: COLORS.line },
  settingsIcon: { color: COLORS.wine, fontSize: 20, width: 28, textAlign: 'center' },
  settingsTitle: { flex: 1, color: COLORS.ink, fontSize: 13, fontWeight: '600' },
  settingsChevron: { color: COLORS.muted, fontSize: 23 },
  aboutCard: { borderRadius: 16, padding: 16, backgroundColor: COLORS.paper, borderWidth: 1, borderColor: COLORS.line, marginBottom: 16 },
  aboutTitle: { color: COLORS.ink, fontSize: 15, fontWeight: '800' },
  aboutBody: { color: COLORS.muted, fontSize: 12, lineHeight: 18, marginTop: 6 },
  notificationCard: { flexDirection: 'row', alignItems: 'flex-start', gap: 11, padding: 14, borderRadius: 15, backgroundColor: COLORS.paper, borderWidth: 1, borderColor: COLORS.line, marginBottom: 9 },
  notificationUnread: { borderColor: '#d6aeb6', backgroundColor: '#fffafb' },
  notificationIcon: { width: 34, height: 34, borderRadius: 12, backgroundColor: COLORS.soft, alignItems: 'center', justifyContent: 'center' },
  threadCard: { flexDirection: 'row', alignItems: 'center', gap: 11, padding: 13, borderRadius: 15, backgroundColor: COLORS.paper, borderWidth: 1, borderColor: COLORS.line },
  threadAvatar: { width: 42, height: 42, borderRadius: 21, backgroundColor: COLORS.soft, alignItems: 'center', justifyContent: 'center' },
  threadAvatarText: { color: COLORS.wine, fontSize: 16, fontWeight: '800' },
  unreadCount: { minWidth: 20, height: 20, borderRadius: 10, backgroundColor: COLORS.wine, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 5 },
  unreadCountText: { color: COLORS.paper, fontSize: 10, fontWeight: '800' },
  chatList: { gap: 10, paddingVertical: 8 },
  chatBubble: { maxWidth: '88%', padding: 12, borderRadius: 15 },
  chatBubbleStaff: { alignSelf: 'flex-start', backgroundColor: COLORS.paper, borderWidth: 1, borderColor: COLORS.line },
  chatBubbleUser: { alignSelf: 'flex-end', backgroundColor: COLORS.soft },
  chatSender: { color: COLORS.wine, fontSize: 9, fontWeight: '800', marginBottom: 4 },
  chatText: { color: COLORS.ink, fontSize: 12, lineHeight: 18 },
  chatTime: { color: COLORS.muted, fontSize: 8, marginTop: 6, textAlign: 'right' },
  composerRow: { flexDirection: 'row', alignItems: 'flex-end', gap: 8, marginTop: 10 },
  composerInput: { flex: 1, minHeight: 44, maxHeight: 100, paddingTop: 11 },
  assistantPage: { flex: 1, backgroundColor: COLORS.canvas, paddingTop: Platform.OS === 'ios' ? 48 : 22 },
  assistantHeader: { minHeight: 66, paddingHorizontal: 18, paddingVertical: 10, backgroundColor: COLORS.paper, borderBottomWidth: 1, borderBottomColor: COLORS.line, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  assistantTitle: { color: COLORS.ink, fontSize: 16, fontWeight: '900' },
  assistantSubtitle: { color: COLORS.muted, fontSize: 10, marginTop: 3 },
  assistantClose: { color: COLORS.wine, fontSize: 12, fontWeight: '800' },
  assistantConversation: { flexGrow: 1, padding: 16, gap: 10 },
  assistantBubble: { maxWidth: '94%', padding: 12, borderRadius: 15 },
  assistantBook: { padding: 9, backgroundColor: COLORS.paper, borderRadius: 10, marginTop: 8 },
  assistantComposer: { flexDirection: 'row', alignItems: 'flex-end', gap: 8, paddingHorizontal: 14, paddingVertical: 10, backgroundColor: COLORS.paper, borderTopWidth: 1, borderTopColor: COLORS.line },
  assistantDisclosure: { color: COLORS.muted, fontSize: 9, textAlign: 'center', paddingHorizontal: 12, paddingBottom: 8, backgroundColor: COLORS.paper },
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
  pickerButtonText: { fontSize: 10 },
  iosPickerWrap: { backgroundColor: COLORS.canvas, borderRadius: 14, padding: 8, marginBottom: 12 },
  detailRows: { marginVertical: 8 },
  cancelButtonText: { color: COLORS.red },
  modalTitle: { color: COLORS.ink, fontSize: 21, fontWeight: '900', marginBottom: 6 },
  modalSub: { color: COLORS.muted, fontSize: 12, lineHeight: 18, marginBottom: 6 },
  modalCancel: { marginTop: 9 },
});
