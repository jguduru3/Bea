'use strict';
// The built-in Bea server for all-in-one installs.
//
// The installers ship a standalone backend (backend/build_server.py) in resources/bea-server.
// When it is present and the person hasn't chosen a separate server, Bea starts it on this
// computer, local only (127.0.0.1), with its database, secrets and logs in the app-data folder,
// and stops it on quit. Extra settings (e.g. POM_MICROSOFT_CLIENT_ID) can go in server.env there.
const { app } = require('electron');
const { spawn, execFile } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const net = require('net');
const path = require('path');

const PORT = 47821; // fixed, so OAuth redirect URIs (Google) stay valid; the next free one is used if taken
let child = null, url = null, starting = null, stopping = false, restarts = [];

function executable() {
  const name = process.platform === 'win32' ? 'bea-server.exe' : 'bea-server';
  const p = process.env.BEA_SERVER_EXE || path.join(process.resourcesPath || '', 'bea-server', name);
  return (app.isPackaged || process.env.BEA_SERVER_EXE) && fs.existsSync(p) ? p : null;
}

const available = () => !!executable();
const dataDir = () => path.join(app.getPath('userData'), 'server');

function freePort(port) {
  return new Promise((resolve) => {
    const srv = net.createServer().once('error', () => resolve(false)).once('listening', () => srv.close(() => resolve(true)));
    srv.listen(port, '127.0.0.1');
  });
}

/** KEY=VALUE lines from server.env (optional, for settings like Microsoft/Google client IDs). */
function extraEnv(dir) {
  const out = {};
  try {
    for (const line of fs.readFileSync(path.join(dir, 'server.env'), 'utf8').split(/\r?\n/)) {
      const m = line.match(/^\s*(POM_[A-Z0-9_]+)\s*=\s*(.*)\s*$/);
      if (m) out[m[1]] = m[2];
    }
  } catch { /* no extras */ }
  return out;
}

async function waitUntilUp(base, ms) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    if (!child) throw new Error('The built-in server stopped while starting. See ' + path.join(dataDir(), 'logs', 'server.log'));
    try { if ((await fetch(base + '/healthz')).ok) return; } catch { /* not yet */ }
    await new Promise((r) => setTimeout(r, 400));
  }
  throw new Error('The built-in server did not start in time. See ' + path.join(dataDir(), 'logs', 'server.log'));
}

/**
 * Start the server if it isn't running. store: the settings store (for the device token).
 * Returns the server's base URL.
 */
function start(store, onCrash) {
  if (child && url) return Promise.resolve(url);
  if (!starting) starting = launch(store, onCrash).finally(() => { starting = null; });
  return starting;
}

async function launch(store, onCrash) {
  const exe = executable();
  if (!exe) throw new Error('This installation has no built-in server.');
  const dir = dataDir();
  fs.mkdirSync(path.join(dir, 'logs'), { recursive: true });
  let token = store.getToken();
  if (!token || !token.startsWith('pom_')) { token = 'pom_' + crypto.randomBytes(32).toString('base64url'); store.setToken(token); }

  let port = PORT;
  while (!(await freePort(port)) && port < PORT + 20) port++;
  const base = `http://127.0.0.1:${port}`;
  const posix = (p) => p.split(path.sep).join('/');
  const env = {
    ...process.env,
    POM_ENV: 'production',
    POM_DATABASE_URL: `sqlite+aiosqlite:///${posix(path.join(dir, 'bea.db'))}`,
    POM_DATA_DIR: path.join(dir, 'data'),
    POM_SECRET_FILE: path.join(dir, '.secret'),
    POM_BOOTSTRAP_DEVICE_TOKEN: token,
    POM_ADMIN_API_KEY: crypto.randomBytes(24).toString('base64url'), // admin API unused by the app; random each run
    POM_ALLOW_DEVICE_KEY_MANAGEMENT: 'true', // keys are added in Settings > API keys
    POM_LLM_PROVIDER: 'anthropic',
    POM_LLM_FALLBACK_PROVIDER: 'openai',
    POM_PUBLIC_BASE_URL: base,
    ...extraEnv(dir),
  };
  const log = fs.openSync(path.join(dir, 'logs', 'server.log'), 'a');
  stopping = false;
  child = spawn(exe, ['--host', '127.0.0.1', '--port', String(port)], { cwd: dir, env, windowsHide: true, stdio: ['ignore', log, log] });
  const me = child;
  child.on('exit', (code) => {
    fs.closeSync(log);
    if (child === me) { child = null; url = null; }
    if (stopping) return;
    restarts = restarts.filter((t) => Date.now() - t < 5 * 60000);
    if (restarts.length < 3) { restarts.push(Date.now()); start(store, onCrash).catch((e) => onCrash && onCrash(e)); }
    else if (onCrash) onCrash(new Error(`The built-in server keeps stopping (exit ${code}). See ${path.join(dir, 'logs', 'server.log')}`));
  });
  await waitUntilUp(base, 90000);
  url = base; // only "running" once it answers
  return url;
}

/** Stop the server and everything it started (its browser). */
function stop() {
  stopping = true;
  const c = child; child = null; url = null;
  if (!c || c.exitCode !== null) return Promise.resolve();
  return new Promise((resolve) => {
    c.once('exit', () => resolve());
    if (process.platform === 'win32') execFile('taskkill', ['/pid', String(c.pid), '/T', '/F'], () => {});
    else c.kill('SIGTERM'); // the server closes its browser on shutdown
    setTimeout(() => { try { c.kill('SIGKILL'); } catch {} resolve(); }, 5000);
  });
}

module.exports = { available, start, stop, dataDir, url: () => url, running: () => !!child };
