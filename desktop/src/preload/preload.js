'use strict';
const { contextBridge, ipcRenderer } = require('electron');

const unwrap = async (p) => {
  const r = await p;
  if (!r.ok) { const e = new Error(r.error); e.status = r.status; throw e; }
  return r.data;
};
const on = (channel) => (cb) => {
  const fn = (_e, payload) => cb(payload);
  ipcRenderer.on(channel, fn);
  return () => ipcRenderer.removeListener(channel, fn);
};

contextBridge.exposeInMainWorld('pom', {
  platform: process.platform,
  getSettings: () => unwrap(ipcRenderer.invoke('settings:get')),
  saveSettings: (patch) => unwrap(ipcRenderer.invoke('settings:save', patch)),
  openSettings: () => unwrap(ipcRenderer.invoke('settings:open')),
  serverConfig: () => unwrap(ipcRenderer.invoke('server:config')),
  keysList: () => unwrap(ipcRenderer.invoke('keys:list')),
  keySave: (provider, key) => unwrap(ipcRenderer.invoke('keys:save', provider, key)),
  keyTest: (provider) => unwrap(ipcRenderer.invoke('keys:test', provider)),
  keyDelete: (provider) => unwrap(ipcRenderer.invoke('keys:delete', provider)),
  // agent
  agentStart: (payload) => unwrap(ipcRenderer.invoke('agent:start', payload)),
  agentWatch: (runId, after) => ipcRenderer.send('agent:watch', runId, after || 0),
  onAgentEvent: on('agent:event'),
  api: (method, path, body) => unwrap(ipcRenderer.invoke('api:call', method, path, body)),
  openHub: (tab) => unwrap(ipcRenderer.invoke('hub:open', tab)),
  onHubTab: on('hub:tab'),
  openExternal: (url) => unwrap(ipcRenderer.invoke('shell:open', url)),
  copy: (text) => unwrap(ipcRenderer.invoke('clipboard:write', text)),
  saveFile: (name, content) => unwrap(ipcRenderer.invoke('file:save', name, content)),
  checkNotifications: () => unwrap(ipcRenderer.invoke('notifications:check')),
  onNotify: on('notify'),
  // wake word
  wakeStatus: () => unwrap(ipcRenderer.invoke('wake:status')),
  onWakeStatus: on('wake:status'),
  wakeState: (state, message) => ipcRenderer.send('wake:state', state, message),
  wakeDetected: () => ipcRenderer.send('wake:detected'),
  wakePause: (paused) => ipcRenderer.send('wake:pause', !!paused),
  onWakeControl: on('wake:control'),
  tts: (text, language) => unwrap(ipcRenderer.invoke('voice:tts', text, language)),
  stt: (bytes, mime, language) => unwrap(ipcRenderer.invoke('voice:stt', bytes, mime, language)),
  ensureMic: () => unwrap(ipcRenderer.invoke('mic:ensure')),
  chatStart: (id, payload) => ipcRenderer.send('chat:start', id, payload),
  chatCancel: (id) => ipcRenderer.send('chat:cancel', id),
  chatReset: () => ipcRenderer.send('chat:reset'),
  setInteractive: (on) => ipcRenderer.send('pet:interactive', !!on),
  focus: () => ipcRenderer.send('pet:focus'),
  quit: () => ipcRenderer.send('app:quit'),
  onChatEvent: on('chat:event'),
  onCursor: on('cursor'),
  onCommand: on('command'),
  onSettings: on('settings'),
  onLayout: on('layout'),
});
