const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const mobile = path.resolve('mobile-native');
const parser = require(path.join(mobile, 'node_modules/@babel/parser'));
const source = fs.readFileSync(path.join(mobile, 'App.js'), 'utf8');
const ast = parser.parse(source, { sourceType: 'module', plugins: ['jsx'] });
const declarations = new Map();
function walk(node) {
  if (!node || typeof node !== 'object') return;
  if (node.type === 'VariableDeclarator' && node.id.type === 'Identifier') declarations.set(node.id.name, node);
  if (node.type === 'FunctionDeclaration' && node.id) declarations.set(node.id.name, node);
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) value.forEach(walk);
    else if (value && typeof value === 'object') walk(value);
  }
}
walk(ast);
function extract(name) {
  const node = declarations.get(name);
  assert.ok(node);
  return (node.type === 'VariableDeclarator' ? 'const ' : '') + source.slice(node.start, node.end) + ';';
}

test('native session teardown discards private memory once, clears secure keys and notifications', async () => {
  const deleted = [], events = [];
  const context = vm.createContext({
    useCallback: fn => fn, sessionEnded: { current: false }, idleTimer: { current: null },
    TOKEN_KEY: 'token', TAB_KEY: 'tab', clearTimeout,
    SecureStore: { deleteItemAsync: async key => deleted.push(key) },
    onSessionEnd: () => events.push('remount'), Alert: { alert: () => events.push('storage-failure') },
    getNotificationsModule: async () => ({
      cancelAllScheduledNotificationsAsync: async () => events.push('cancel-reminders'),
      dismissAllNotificationsAsync: async () => events.push('dismiss'),
    }),
  });
  vm.runInContext(extract('clearPrivateSession') + '\nglobalThis.clear = clearPrivateSession;', context);
  await Promise.all([context.clear(), context.clear()]);
  assert.deepEqual(deleted.sort(), ['tab', 'token']);
  assert.deepEqual(events, ['remount', 'cancel-reminders', 'dismiss']);
});

test('native teardown still locks memory if secure storage is unavailable', async () => {
  let remounts = 0;
  const context = vm.createContext({
    useCallback: fn => fn, sessionEnded: { current: false }, idleTimer: { current: null },
    TOKEN_KEY: 'token', TAB_KEY: 'tab', clearTimeout,
    SecureStore: { deleteItemAsync: async () => { throw new Error('locked'); } },
    onSessionEnd: () => remounts++, Alert: { alert() {} }, getNotificationsModule: async () => null,
  });
  vm.runInContext(extract('clearPrivateSession') + '\nglobalThis.clear = clearPrivateSession;', context);
  await context.clear();
  assert.equal(remounts, 1);
});

test('late authenticated response is discarded after native logout', async () => {
  const context = vm.createContext({
    useCallback: fn => fn, apiBase: 'https://library.evsu.edu.ph', token: 'old-session',
    clearPrivateSession() {}, sessionEnded: { current: true },
    fetch: async () => ({ status: 200, ok: true, json: async () => ({ messages: ['private message'] }) }),
  });
  vm.runInContext(extract('request') + '\nglobalThis.load = request;', context);
  await assert.rejects(context.load('/mobile/messages'), /session has ended/);
});

test('a due reminder finishing after logout is canceled without touching another session', async () => {
  let finish;
  const canceled = [];
  const context = vm.createContext({
    sessionEnded: { current: false }, Platform: { OS: 'ios' }, parseServerTimestamp: value => new Date(value),
    getNotificationsModule: async () => ({
      getAllScheduledNotificationsAsync: async () => [], getPermissionsAsync: async () => ({ status: 'granted' }),
      SchedulableTriggerInputTypes: { DATE: 'date' },
      scheduleNotificationAsync: () => new Promise(resolve => { finish = resolve; }),
      cancelScheduledNotificationAsync: async identifier => canceled.push(identifier),
    }),
  });
  vm.runInContext(extract('syncDueReminders') + '\nglobalThis.sync = syncDueReminders;', context);
  const pending = context.sync([{ id: 5, status: 'CHECKED_OUT', book_title: 'Private loan', due_date: new Date(Date.now() + 3 * 86400000).toISOString() }]);
  while (!finish) await new Promise(resolve => setImmediate(resolve));
  context.sessionEnded.current = true;
  finish('old-session-reminder');
  await pending;
  assert.deepEqual(canceled, ['old-session-reminder']);
});

test('standalone release config refuses HTTP, private addresses and placeholder backends', () => {
  const configure = require(path.join(mobile, 'app.config.js'));
  const original = { ...process.env };
  try {
    process.env.SMARTLIB_ENV = 'production';
    for (const api of ['', 'http://library.evsu.edu.ph', 'https://192.168.1.10', 'https://169.254.1.1', 'https://0.0.0.0', 'https://example.com', 'https://library.evsu.edu.ph/api']) {
      process.env.EXPO_PUBLIC_API_BASE_URL = api;
      assert.throws(() => configure({ config: {} }), /public HTTPS backend/);
    }
    process.env.EXPO_PUBLIC_API_BASE_URL = 'https://library.evsu.edu.ph';
    const result = configure({ config: { ios: { infoPlist: { NSAppTransportSecurity: { NSAllowsLocalNetworking: true } } } } });
    assert.equal(result.android.usesCleartextTraffic, false);
    assert.equal(result.ios.infoPlist.NSAppTransportSecurity, undefined);
  } finally {
    for (const key of ['SMARTLIB_ENV', 'EXPO_PUBLIC_API_BASE_URL']) {
      if (original[key] === undefined) delete process.env[key]; else process.env[key] = original[key];
    }
  }
});
