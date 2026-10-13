/* Browser preview bridge.
 * Inside the desktop app, window.pom comes from the preload script and this file does nothing.
 * Opened directly in a browser, it supplies a small offline stand-in so Pom can be tried
 * without a server: canned replies with emotions, no voice.
 */
(function () {
  'use strict';
  if (window.pom) return;
  const listeners = { chat: [], cursor: [], command: [], settings: [], layout: [] };
  let settings = { backendUrl: '(preview)', language: 'en-IN', speakReplies: false, petSize: 170, userName: '', conversationId: null, hasToken: true, muted: true };
  const replies = [
    ['[emotion:excited] [action:jump] Hi! I\'m Bea. In the desktop app I answer with a real AI model and can talk back with Sarvam voices.', 'excited'],
    ['[emotion:thinking] Hmm, good question. [emotion:happy] This is the offline preview, so I can only show you my faces. Try rubbing my head!', 'happy'],
    ['[emotion:laughing] [action:dance] Why did the robot go on holiday? It needed to recharge its batteries!', 'laughing'],
    ['[emotion:love] You\'re doing great today. Remember to drink some water!', 'love'],
  ];
  let n = 0;
  window.pom = {
    platform: 'preview',
    preview: true,
    getSettings: async () => settings,
    saveSettings: async (p) => (settings = { ...settings, ...p }),
    openSettings: async () => {},
    serverConfig: async () => ({ assistant_name: 'Bea', chat: true, voice: false, default_language: 'en-IN',
      languages: [{ code: 'en-IN', name: 'English' }, { code: 'hi-IN', name: 'Hindi' }, { code: 'te-IN', name: 'Telugu' }] }),
    tts: async () => { throw new Error('Voice is off in preview'); },
    stt: async () => { throw new Error('Voice is off in preview'); },
    ensureMic: async () => true,
    chatStart(id) {
      const [text] = replies[n++ % replies.length];
      const parts = text.match(/\S+\s*/g);
      let i = 0;
      const emit = (type, data) => listeners.chat.forEach((cb) => cb({ id, type, data }));
      emit('meta', { conversation_id: 'preview' });
      setTimeout(function tick() {
        if (i >= parts.length) {
          emit('done', { text: text.replace(/\[(emotion|action):[a-z_]+\]\s*/g, ''), emotion: 'happy' }); emit('end', {}); return;
        }
        const p = parts[i++];
        const m = p.match(/\[(emotion|action):([a-z_]+)\]/);
        if (m) emit(m[1], m[1] === 'emotion' ? { emotion: m[2] } : { action: m[2] }); else emit('delta', { text: p });
        setTimeout(tick, 70);
      }, 900);
    },
    chatCancel() {}, chatReset() {},
    setInteractive() {}, focus() {}, quit() {},
    onChatEvent: (cb) => listeners.chat.push(cb),
    onCursor: (cb) => { listeners.cursor.push(cb); addEventListener('pointermove', (e) => cb({ x: e.clientX, y: e.clientY })); },
    onCommand: (cb) => listeners.command.push(cb),
    onSettings: (cb) => listeners.settings.push(cb),
    onLayout: (cb) => listeners.layout.push(cb),
    // agent, hub and wake word are desktop-only; the preview uses plain chat
    api: async () => ({ available: false, reason: 'Agent mode works in the desktop app.' }),
    agentStart: async () => { throw new Error('Agent mode works in the desktop app.'); },
    agentWatch() {}, onAgentEvent() {}, onNotify() {}, onWakeStatus() {}, wakePause() {},
    openHub: async () => {}, openExternal: async () => {}, copy: async () => {},
  };
  document.documentElement.style.background = 'linear-gradient(160deg,#cfe3ff,#f6dbe6)';
})();
