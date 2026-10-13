'use strict';
// Talks to the Pom backend from the main process, so the device token stays out of the UI.
const store = require('./store');

class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

function base() {
  const url = store.load().backendUrl;
  if (!/^https?:\/\//i.test(url || '')) throw new ApiError(0, 'Set the server address in Settings first.');
  return url;
}

function headers(extra = {}) {
  const token = store.getToken();
  if (!token) throw new ApiError(401, 'Add your device token in Settings first.');
  return { Authorization: `Bearer ${token}`, ...extra };
}

async function errorFrom(res) {
  let msg = `Server returned ${res.status}`;
  try { const j = await res.json(); if (j.detail) msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail); } catch {}
  if (res.status === 401) msg = 'The device token was rejected. Check it in Settings.';
  return new ApiError(res.status, msg);
}

async function request(path, opts = {}, timeoutMs = 30000) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(base() + path, { ...opts, signal: opts.signal || ctrl.signal });
    if (!res.ok) throw await errorFrom(res);
    return res;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if (e.name === 'AbortError') throw new ApiError(0, 'The server took too long to answer.');
    throw new ApiError(0, `Can't reach the server at ${store.load().backendUrl}.`);
  } finally { clearTimeout(t); }
}

async function config() {
  const res = await request('/v1/config', { headers: headers() }, 8000);
  return res.json();
}

async function tts(text, language) {
  const res = await request('/v1/tts', {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ text, language_code: language }),
  }, 45000);
  return { mime: res.headers.get('content-type') || 'audio/mpeg', data: new Uint8Array(await res.arrayBuffer()) };
}

async function stt(bytes, mime, language) {
  const form = new FormData();
  form.append('file', new Blob([bytes], { type: mime }), mime.includes('webm') ? 'speech.webm' : 'speech.ogg');
  if (language) form.append('language_code', language);
  const res = await request('/v1/stt', { method: 'POST', headers: headers(), body: form }, 45000);
  return res.json();
}

/** Stream a chat reply. onEvent(type, data) receives meta, emotion, delta, done and error events. */
async function chat(payload, onEvent, signal) {
  const res = await request('/v1/chat', {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json', Accept: 'text/event-stream' }),
    body: JSON.stringify(payload),
    signal,
  }, 120000);
  await readSSE(res, onEvent);
}

/** Follow an agent run live. Replays steps after `after`, then streams until the run ends. */
async function agentEvents(runId, after, onEvent, signal) {
  const res = await request(`/v1/agent/runs/${encodeURIComponent(runId)}/events?after=${Number(after) || 0}`, {
    headers: headers({ Accept: 'text/event-stream' }), signal,
  }, 6 * 60 * 60 * 1000); // runs can wait a long time for an approval
  await readSSE(res, onEvent);
}

// Only these server paths may be called through the generic bridge used by Bea Hub and the chat panel.
const CALLABLE = /^\/v1\/(agent|notifications|connectors)(\/[\w\-./]*)?(\?[\w=&%.\-]*)?$/;
const METHODS = new Set(['GET', 'POST', 'PUT', 'PATCH', 'DELETE']);

async function call(method, path, body, timeoutMs = 30000) {
  method = String(method || 'GET').toUpperCase();
  if (!METHODS.has(method) || !CALLABLE.test(String(path)) || path.includes('..')) throw new ApiError(400, 'That request is not allowed.');
  if (path.startsWith('/v1/connectors') && method !== 'GET') assertSafeTransport(); // may carry API tokens
  const opts = { method, headers: headers(body !== undefined ? { 'Content-Type': 'application/json' } : {}) };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await request(path, opts, timeoutMs);
  if (res.status === 204) return null;
  const type = res.headers.get('content-type') || '';
  return type.includes('json') ? res.json() : res.text();
}

async function readSSE(res, onEvent) {
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf('\n\n')) !== -1) {
      const block = buf.slice(0, idx); buf = buf.slice(idx + 2);
      let ev = 'message', data = '';
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) ev = line.slice(6).trim();
        else if (line.startsWith('data:')) data += line.slice(5).trim();
      }
      if (data) { try { onEvent(ev, JSON.parse(data)); } catch {} }
    }
  }
}

// ------------------------------------------------------------------ AI and voice keys
// Keys go straight to the server and are never stored on this computer.
const PROVIDERS = new Set(['anthropic', 'openai', 'sarvam']);

function keyPath(provider, suffix = '') {
  if (!PROVIDERS.has(provider)) throw new ApiError(400, 'Unknown provider.');
  return `/v1/keys/${provider}${suffix}`;
}

function assertSafeTransport() {
  const u = new URL(base());
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(u.hostname);
  if (u.protocol !== 'https:' && !local) throw new ApiError(0, 'API keys are only sent over https (or to a server on this computer).');
}

async function keysList() {
  const res = await request('/v1/keys', { headers: headers() }, 10000);
  return res.json();
}

async function keySave(provider, key) {
  assertSafeTransport();
  const res = await request(keyPath(provider), {
    method: 'PUT', headers: headers({ 'Content-Type': 'application/json' }), body: JSON.stringify({ key }),
  }, 30000);
  return res.json();
}

async function keyTest(provider) {
  const res = await request(keyPath(provider, '/test'), { method: 'POST', headers: headers() }, 30000);
  return res.json();
}

async function keyDelete(provider) {
  const res = await request(keyPath(provider), { method: 'DELETE', headers: headers() }, 10000);
  return res.json();
}

module.exports = { config, tts, stt, chat, agentEvents, call, keysList, keySave, keyTest, keyDelete, ApiError };
