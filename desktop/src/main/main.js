'use strict';
const { app, BrowserWindow, Tray, Menu, ipcMain, screen, globalShortcut, nativeImage, session, systemPreferences, shell,
  protocol, clipboard, dialog, Notification } = require('electron');
const fs = require('fs');
const path = require('path');
const store = require('./store');
const api = require('./api');
const server = require('./server');

const DEV = process.argv.includes('--dev');
const RENDERER = path.join(__dirname, '..', 'renderer');
const PRELOAD = path.join(__dirname, '..', 'preload', 'preload.js');

let pet = null;        // transparent overlay window that Bea lives in
let settingsWin = null;
let hubWin = null;     // Bea Hub: activity, approvals, goals, schedules, memory, documents, connections
let wakeWin = null;    // hidden window that listens for the wake word
let wakeState = { state: 'off', message: '' };
let tray = null;
let cursorTimer = null;
const chats = new Map(); // requestId -> AbortController

// For testing and support: run with a separate settings/data folder (BEA_USER_DATA=<folder>).
if (process.env.BEA_USER_DATA) app.setPath('userData', process.env.BEA_USER_DATA);
if (!app.requestSingleInstanceLock()) { app.quit(); }

// bea:// serves the wake-word engine and its speech model to the hidden wake window. The engine runs
// WebAssembly in a worker, so it gets its own origin and a looser policy than Bea's main window.
protocol.registerSchemesAsPrivileged([{ scheme: 'bea', privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true } }]);
const BEA_FILES = {
  'wake.html': () => path.join(RENDERER, 'wake.html'),
  'wake.js': () => path.join(RENDERER, 'wake.js'),
  'wake-match.js': () => path.join(RENDERER, 'wake-match.js'),
  'vosk.js': () => require.resolve('vosk-browser/dist/vosk.js'),
  'wake-model.tar.gz': () => (app.isPackaged ? path.join(process.resourcesPath, 'wake-model.tar.gz')
    : path.join(__dirname, '..', '..', 'resources', 'wake-model.tar.gz')),
};
function wakeModelPresent() { try { return fs.statSync(BEA_FILES['wake-model.tar.gz']()).size > 1e6; } catch { return false; } }
app.on('second-instance', () => send('command', 'chat'));
if (process.platform === 'darwin' && app.dock) app.dock.hide();

// ------------------------------------------------------------------ windows
function targetDisplay() {
  const id = store.load().displayId;
  return screen.getAllDisplays().find((d) => d.id === id) || screen.getPrimaryDisplay();
}

function createPet() {
  const d = targetDisplay();
  pet = new BrowserWindow({
    ...d.workArea,
    transparent: true,
    frame: false,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    hasShadow: false,
    alwaysOnTop: true,
    backgroundColor: '#00000000',
    show: false,
    webPreferences: { preload: PRELOAD, contextIsolation: true, nodeIntegration: false, sandbox: true, backgroundThrottling: false },
  });
  pet.setAlwaysOnTop(true, 'floating');
  if (process.platform === 'darwin') pet.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  pet.setIgnoreMouseEvents(true, { forward: true });
  pet.loadFile(path.join(RENDERER, 'index.html'));
  pet.once('ready-to-show', () => { pet.showInactive(); startCursorFeed(); });
  pet.on('closed', () => { pet = null; stopCursorFeed(); });
  harden(pet);
  if (DEV) pet.webContents.openDevTools({ mode: 'detach' });
}

function openSettings() {
  if (settingsWin) { settingsWin.show(); settingsWin.focus(); return; }
  settingsWin = new BrowserWindow({
    width: 540, height: 820, resizable: true, minWidth: 460, minHeight: 500, title: 'Bea settings', show: false,
    backgroundColor: '#f2f6fc', autoHideMenuBar: true,
    webPreferences: { preload: PRELOAD, contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  settingsWin.loadFile(path.join(RENDERER, 'settings.html'));
  settingsWin.once('ready-to-show', () => settingsWin.show());
  settingsWin.on('closed', () => { settingsWin = null; });
  harden(settingsWin);
}

function openHub(tab) {
  if (hubWin) { hubWin.show(); hubWin.focus(); if (tab) hubWin.webContents.send('hub:tab', tab); return; }
  hubWin = new BrowserWindow({
    width: 980, height: 720, minWidth: 720, minHeight: 520, title: 'Bea Hub', show: false,
    backgroundColor: '#f2f6fc', autoHideMenuBar: true,
    webPreferences: { preload: PRELOAD, contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  hubWin.loadFile(path.join(RENDERER, 'hub.html'), tab ? { hash: tab } : undefined);
  hubWin.once('ready-to-show', () => hubWin.show());
  hubWin.on('closed', () => { hubWin = null; });
  harden(hubWin);
}

function setWake(state, message = '') {
  wakeState = { state, message };
  for (const w of [pet, settingsWin]) if (w && !w.isDestroyed()) w.webContents.send('wake:status', wakeState);
}

function syncWake() {
  const want = !!store.load().wakeWord;
  if (want && !wakeWin) {
    if (!wakeModelPresent()) { setWake('error', 'The wake word model is missing. Run "npm run fetch-wake-model" in the desktop folder.'); return; }
    wakeWin = new BrowserWindow({
      show: false, width: 200, height: 200, skipTaskbar: true,
      webPreferences: { preload: PRELOAD, contextIsolation: true, nodeIntegration: false, sandbox: true, backgroundThrottling: false },
    });
    wakeWin.loadURL('bea://app/wake.html');
    wakeWin.on('closed', () => { wakeWin = null; if (store.load().wakeWord) setWake('off', 'Stopped'); });
    harden(wakeWin);
    setWake('loading', 'Loading the wake word model…');
  } else if (!want && wakeWin) {
    wakeWin.destroy(); wakeWin = null; setWake('off');
  }
}

function harden(win) {
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https:\/\//.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (e) => e.preventDefault());
}

function send(channel, payload) { if (pet && !pet.isDestroyed()) pet.webContents.send(channel, payload); }

function moveToDisplay(display) {
  if (!pet) return;
  store.save({ displayId: display.id });
  pet.setBounds(display.workArea);
  send('layout', display.workArea);
}

// Global cursor position so Bea's eyes follow the mouse anywhere on screen.
function startCursorFeed() {
  let last = '';
  cursorTimer = setInterval(() => {
    if (!pet) return;
    const p = screen.getCursorScreenPoint();
    const b = pet.getBounds();
    const key = p.x + ',' + p.y;
    if (key === last) return;
    last = key;
    send('cursor', { x: p.x - b.x, y: p.y - b.y });
  }, 33);
}
function stopCursorFeed() { clearInterval(cursorTimer); cursorTimer = null; }

// ------------------------------------------------------------------ tray
function buildTray() {
  // macOS menu bar wants a black "template" image that the system tints for light and dark menu bars.
  const icon = process.platform === 'darwin'
    ? nativeImage.createFromPath(path.join(RENDERER, 'assets', 'trayTemplate.png'))
    : nativeImage.createFromPath(path.join(RENDERER, 'assets', 'tray.png')).resize({ width: 18, height: 18 });
  if (process.platform === 'darwin') icon.setTemplateImage(true);
  tray = new Tray(icon);
  tray.setToolTip('Bea');
  const s = store.load();
  const displays = screen.getAllDisplays();
  const menu = Menu.buildFromTemplate([
    { label: 'Chat with Bea', accelerator: 'CommandOrControl+Shift+Space', click: () => send('command', 'chat') },
    { label: 'Talk to Bea', click: () => send('command', 'talk') },
    { label: 'Bea Hub…', click: () => openHub() },
    { label: 'Listen for "Hey Bea"', type: 'checkbox', checked: !!s.wakeWord, click: (i) => { store.save({ wakeWord: i.checked }); syncWake(); send('settings', store.load()); } },
    { type: 'separator' },
    { label: 'Focus timer (25 min)', click: () => send('command', 'focus') },
    { label: 'Moves', submenu: [
      ['Dance', 'dance'], ['Jump', 'jump'], ['Run around', 'run'], ['Spin', 'spin'], ['Wave', 'wave'],
      ['Hide', 'hide'], ['Party', 'celebrate'], ['Stretch', 'stretch'],
    ].map(([label, m]) => ({ label, click: () => send('command', 'move:' + m) })) },
    { label: 'Nap time', click: () => send('command', 'sleep') },
    { label: 'Emotion show', click: () => send('command', 'showcase') },
    { type: 'separator' },
    { label: 'Speak replies', type: 'checkbox', checked: s.speakReplies && !s.muted, click: (i) => { store.save({ speakReplies: i.checked, muted: !i.checked }); send('settings', store.load()); } },
    ...(displays.length > 1 ? [{ label: 'Move to next screen', click: () => {
      const all = screen.getAllDisplays(); const cur = targetDisplay();
      moveToDisplay(all[(all.findIndex((d) => d.id === cur.id) + 1) % all.length]);
    } }] : []),
    { label: 'Show / hide Bea', click: () => { if (!pet) return; pet.isVisible() ? pet.hide() : pet.showInactive(); } },
    { label: 'Settings…', click: openSettings },
    { type: 'separator' },
    { label: 'Quit Bea', role: 'quit' },
  ]);
  tray.setContextMenu(menu);
  tray.on('click', () => send('command', 'chat'));
}

// ------------------------------------------------------------------ IPC
function handle(channel, fn) {
  ipcMain.handle(channel, async (event, ...args) => {
    try { return { ok: true, data: await fn(event, ...args) }; }
    catch (e) { return { ok: false, error: e.message || String(e), status: e.status || 0 }; }
  });
}

function wireIpc() {
  ipcMain.on('pet:interactive', (_e, on) => {
    if (!pet) return;
    if (on) { pet.setIgnoreMouseEvents(false); }
    else pet.setIgnoreMouseEvents(true, { forward: true });
  });
  ipcMain.on('pet:focus', () => { if (pet) pet.focus(); });

  handle('settings:get', () => ({ ...store.load(), hasToken: !!store.getToken(), builtInAvailable: server.available(), builtInRunning: !!server.url() }));
  handle('settings:save', async (_e, patch) => {
    if (patch && typeof patch.token === 'string' && patch.token.trim()) store.setToken(patch.token.trim());
    if (patch && patch.clearToken) store.setToken('');
    const before = store.load().useBuiltInServer;
    const next = store.save(patch || {});
    if (server.available() && 'useBuiltInServer' in (patch || {}) && patch.useBuiltInServer !== before) {
      if (patch.useBuiltInServer) await startBuiltIn(); else await server.stop();
    }
    app.setLoginItemSettings({ openAtLogin: !!next.launchAtLogin });
    send('settings', next);
    syncWake();
    if (tray) buildTray();
    return { ...store.load(), hasToken: !!store.getToken(), builtInAvailable: server.available(), builtInRunning: !!server.url() };
  });
  handle('settings:open', () => openSettings());
  handle('server:config', () => api.config());
  // After a key changes, the character re-checks the server so chat and voice turn on or off.
  const keysChanged = (r) => { send('settings', store.load()); return r; };
  handle('keys:list', () => api.keysList());
  handle('keys:save', async (_e, provider, key) => keysChanged(await api.keySave(String(provider), String(key || '').trim())));
  handle('keys:test', (_e, provider) => api.keyTest(String(provider)));
  handle('keys:delete', async (_e, provider) => keysChanged(await api.keyDelete(String(provider))));
  handle('voice:tts', (_e, text, language) => api.tts(String(text).slice(0, 2500), language));
  handle('voice:stt', (_e, bytes, mime, language) => api.stt(bytes, mime, language));
  handle('mic:ensure', async () => {
    if (process.platform !== 'darwin') return true;
    const status = systemPreferences.getMediaAccessStatus('microphone');
    if (status === 'granted') return true;
    return systemPreferences.askForMediaAccess('microphone');
  });

  ipcMain.on('chat:start', async (event, id, payload) => {
    const ctrl = new AbortController();
    chats.set(id, ctrl);
    const emit = (type, data) => { if (!event.sender.isDestroyed()) event.sender.send('chat:event', { id, type, data }); };
    try {
      await api.chat(payload, (type, data) => {
        if (type === 'meta' && data.conversation_id) store.save({ conversationId: data.conversation_id });
        emit(type, data);
      }, ctrl.signal);
      emit('end', {});
    } catch (e) {
      if (ctrl.signal.aborted) emit('end', { cancelled: true });
      else emit('error', { message: e.message, status: e.status || 0 });
    } finally { chats.delete(id); }
  });
  ipcMain.on('chat:cancel', (_e, id) => { const c = chats.get(id); if (c) c.abort(); });
  ipcMain.on('chat:reset', () => store.save({ conversationId: null }));
  ipcMain.on('app:quit', () => app.quit());

  // ---- agent
  handle('agent:start', async (event, payload) => {
    const { run_id: runId } = await api.call('POST', '/v1/agent/runs', payload);
    watchRun(event.sender, runId, 0);
    return runId;
  });
  ipcMain.on('agent:watch', (event, runId, after) => watchRun(event.sender, String(runId), after));
  handle('api:call', (_e, method, p, body) => api.call(method, p, body));
  handle('hub:open', (_e, tab) => openHub(tab));
  handle('shell:open', (_e, url) => {
    if (!/^https:\/\/\S+$/.test(String(url))) throw new Error('Only https links can be opened.');
    return shell.openExternal(String(url));
  });
  handle('clipboard:write', (_e, text) => { clipboard.writeText(String(text).slice(0, 100000)); return true; });
  handle('file:save', async (event, name, content) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    const safe = String(name).replace(/[^\w .,()-]+/g, ' ').trim().slice(0, 80) || 'Bea document';
    const r = await dialog.showSaveDialog(win, { defaultPath: safe + '.md', filters: [{ name: 'Markdown', extensions: ['md'] }, { name: 'Text', extensions: ['txt'] }] });
    if (r.canceled || !r.filePath) return false;
    fs.writeFileSync(r.filePath, String(content), 'utf8');
    return true;
  });
  handle('notifications:check', () => pollNotifications());

  // ---- wake word
  handle('wake:status', () => wakeState);
  ipcMain.on('wake:state', (_e, state, message) => setWake(String(state), String(message || '')));
  ipcMain.on('wake:detected', () => send('command', 'wake'));
  ipcMain.on('wake:pause', (_e, paused) => { if (wakeWin) wakeWin.webContents.send('wake:control', { paused: !!paused }); });
}

// ------------------------------------------------------------------ built-in server (all-in-one installs)
const useBuiltIn = () => server.available() && store.load().useBuiltInServer !== false;

async function startBuiltIn() {
  if (!useBuiltIn()) return false;
  try {
    const url = await server.start(store, serverTrouble);
    store.save({ backendUrl: url });
    send('settings', store.load());
    if (settingsWin && !settingsWin.isDestroyed()) settingsWin.webContents.send('settings', store.load());
    return true;
  } catch (e) { serverTrouble(e); return false; }
}

function serverTrouble(e) {
  if (Notification.isSupported()) new Notification({ title: "Bea's server didn't start", body: String(e.message || e).slice(0, 240) }).show();
}

// ------------------------------------------------------------------ agent runs
const watchers = new Map(); // runId -> AbortController, one live stream per run

function watchRun(target, runId, after) {
  if (watchers.has(runId)) return;
  const ctrl = new AbortController();
  watchers.set(runId, ctrl);
  const emit = (type, data) => { if (!target.isDestroyed()) target.send('agent:event', { runId, type, data }); };
  api.agentEvents(runId, after, (type, data) => { if (type !== 'end') emit(type, data); }, ctrl.signal)
    .catch((e) => { if (!ctrl.signal.aborted) emit('stream_error', { message: e.message }); })
    .finally(() => { watchers.delete(runId); emit('stream_end', {}); });
}

// ------------------------------------------------------------------ notifications
// Background tasks, reminders and approvals reach Bea through notifications. They pop up on her
// bubble and, for things that need attention, as a system notification.
const seen = new Set();
let polling = false;

async function pollNotifications() {
  if (polling || !store.getToken()) return [];
  polling = true;
  try {
    const items = await api.call('GET', '/v1/notifications?unread=true&limit=20');
    const fresh = items.filter((n) => !seen.has(n.id)).reverse();
    for (const n of fresh) {
      seen.add(n.id);
      send('notify', n);
      if (Notification.isSupported() && ['approval', 'task_done', 'reminder'].includes(n.kind)) {
        const note = new Notification({ title: n.title, body: (n.body || '').slice(0, 240), silent: n.kind !== 'reminder' });
        note.on('click', () => (n.kind === 'approval' ? openHub('approvals') : send('command', 'chat')));
        note.show();
      }
    }
    const done = fresh.filter((n) => n.kind !== 'approval').map((n) => n.id); // approvals stay until decided
    if (done.length) await api.call('POST', '/v1/notifications/read', { ids: done });
    return fresh;
  } catch { return []; } finally { polling = false; }
}

// ------------------------------------------------------------------ lifecycle
app.whenReady().then(() => {
  session.defaultSession.setPermissionRequestHandler((_wc, permission, cb) => cb(permission === 'media'));
  session.defaultSession.setPermissionCheckHandler((_wc, permission) => permission === 'media');
  // Read with fs (not a file:// fetch) so it also works from inside the packaged app.asar.
  const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.gz': 'application/gzip' };
  protocol.handle('bea', async (req) => {
    const u = new URL(req.url);
    const file = u.host === 'app' && BEA_FILES[decodeURIComponent(u.pathname.slice(1))];
    if (!file) return new Response('Not found', { status: 404 });
    try {
      const f = file();
      return new Response(await fs.promises.readFile(f), { headers: { 'content-type': TYPES[path.extname(f)] || 'application/octet-stream' } });
    } catch { return new Response('Not found', { status: 404 }); }
  });
  wireIpc();
  createPet();
  syncWake();
  setInterval(pollNotifications, 15000);
  setTimeout(pollNotifications, 4000);
  buildTray();
  globalShortcut.register('CommandOrControl+Shift+Space', () => send('command', 'chat'));
  screen.on('display-metrics-changed', () => { if (pet) { const d = targetDisplay(); pet.setBounds(d.workArea); send('layout', d.workArea); } });
  screen.on('display-removed', () => moveToDisplay(screen.getPrimaryDisplay()));
  store.importProvisioning();
  startBuiltIn().then((builtIn) => {
    // First run: open Settings so the person can add their Claude (and Sarvam) keys, or connect a server.
    if (!store.getToken() || (builtIn && !store.load().welcomed)) { store.save({ welcomed: true }); setTimeout(openSettings, 800); }
  });
});

// Stop the built-in server (and its browser) before quitting.
let quitting = false;
app.on('before-quit', (e) => {
  if (quitting || !server.running()) return;
  e.preventDefault();
  quitting = true;
  server.stop().finally(() => app.quit());
});

app.on('will-quit', () => globalShortcut.unregisterAll());
// Bea lives in the tray; closing the settings window must not quit the app.
app.on('window-all-closed', () => {});
