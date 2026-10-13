'use strict';
// Settings live in a JSON file in the user's app-data folder.
// The device token is encrypted with the OS keychain (safeStorage) and never reaches the renderer.
const { app, safeStorage } = require('electron');
const fs = require('fs');
const path = require('path');

const DEFAULTS = {
  backendUrl: 'http://localhost:8080',
  language: 'en-IN',
  speakReplies: true,
  petSize: 170,
  launchAtLogin: false,
  userName: '',
  conversationId: null,
  displayId: null,
  muted: false,
  agentMode: true,     // send messages to Bea's agent (plans, tools, apps) instead of plain chat
  agentThread: null,   // the last agent run, so a follow-up continues the same conversation
  wakeWord: false,     // listen for "Bea" / "Hey Bea" on this computer
  useBuiltInServer: true, // all-in-one installs: run Bea's own server on this computer
  welcomed: false,     // the first-run Settings window was shown
};

const dir = () => app.getPath('userData');
const file = () => path.join(dir(), 'settings.json');
const tokenFile = () => path.join(dir(), 'device-token.bin');

let cache = null;

function load() {
  if (cache) return cache;
  try { cache = { ...DEFAULTS, ...JSON.parse(fs.readFileSync(file(), 'utf8')) }; }
  catch { cache = { ...DEFAULTS }; }
  return cache;
}

function save(patch) {
  const allowed = Object.keys(DEFAULTS);
  const next = { ...load() };
  for (const k of Object.keys(patch || {})) if (allowed.includes(k)) next[k] = patch[k];
  if (typeof next.backendUrl === 'string') next.backendUrl = next.backendUrl.trim().replace(/\/+$/, '');
  next.petSize = Math.max(110, Math.min(320, Number(next.petSize) || DEFAULTS.petSize));
  cache = next;
  fs.mkdirSync(dir(), { recursive: true });
  fs.writeFileSync(file(), JSON.stringify(next, null, 2));
  return next;
}

function getToken() {
  try {
    const buf = fs.readFileSync(tokenFile());
    if (safeStorage.isEncryptionAvailable()) return safeStorage.decryptString(buf);
    return buf.toString('utf8');
  } catch { return ''; }
}

function setToken(token) {
  fs.mkdirSync(dir(), { recursive: true });
  if (!token) { try { fs.unlinkSync(tokenFile()); } catch {} return; }
  const data = safeStorage.isEncryptionAvailable() ? safeStorage.encryptString(token) : Buffer.from(token, 'utf8');
  fs.writeFileSync(tokenFile(), data, { mode: 0o600 });
}

/**
 * One-time provisioning for installers and IT (MDM): if a provision.json file sits in the
 * app-data folder, import its server address and token, encrypt the token, then delete the file.
 *   { "backendUrl": "https://pom.company.com", "token": "pom_…", "userName": "Asha" }
 */
function importProvisioning() {
  const f = path.join(dir(), 'provision.json');
  let data;
  try { data = JSON.parse(fs.readFileSync(f, 'utf8')); } catch { return false; }
  try {
    if (data.token) setToken(String(data.token).trim());
    const patch = {};
    for (const k of ['backendUrl', 'userName', 'language', 'petSize', 'launchAtLogin', 'speakReplies', 'agentMode', 'wakeWord', 'useBuiltInServer']) if (k in data) patch[k] = data[k];
    save(patch);
  } finally {
    try { fs.unlinkSync(f); } catch {}
  }
  return true;
}

module.exports = { load, save, getToken, setToken, importProvisioning, DEFAULTS };
