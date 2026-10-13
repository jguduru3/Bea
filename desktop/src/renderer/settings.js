(async () => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const pom = window.pom;
  const fields = ['backendUrl', 'userName', 'language', 'petSize', 'speakReplies', 'launchAtLogin', 'agentMode', 'wakeWord', 'useBuiltInServer'];

  function fill(s) {
    for (const f of fields) {
      const el = $(f);
      if (el.type === 'checkbox') el.checked = !!s[f];
      else el.value = s[f] ?? '';
    }
    $('sizeOut').textContent = s.petSize + ' px';
    // All-in-one installs: the built-in server replaces the address and token fields.
    $('builtInRow').hidden = $('builtInHint').hidden = !s.builtInAvailable;
    const builtIn = s.builtInAvailable && s.useBuiltInServer !== false;
    $('remoteFields').hidden = builtIn;
    $('backendUrl').required = !builtIn;
    $('token').placeholder = s.hasToken ? 'Saved. Paste a new token to replace it.' : 'pom_…';
  }
  function collect() {
    const out = {};
    for (const f of fields) {
      const el = $(f);
      out[f] = el.type === 'checkbox' ? el.checked : el.type === 'range' ? Number(el.value) : el.value;
    }
    out.muted = !out.speakReplies;
    const t = $('token').value.trim();
    if (t) out.token = t;
    return out;
  }

  fill(await pom.getSettings());
  $('useBuiltInServer').addEventListener('change', async () => {
    $('testResult').textContent = $('useBuiltInServer').checked ? 'Starting the built-in server…' : '';
    fill(await pom.saveSettings({ useBuiltInServer: $('useBuiltInServer').checked }));
    $('testResult').textContent = '';
    loadKeys();
  });
  pom.onSettings(async () => { fill(await pom.getSettings()); loadKeys(); });
  $('petSize').addEventListener('input', () => { $('sizeOut').textContent = $('petSize').value + ' px'; });

  $('form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const s = await pom.saveSettings(collect());
    $('token').value = '';
    fill(s);
    $('saved').textContent = 'Saved'; $('saved').className = 'ok';
    loadKeys();

  // ------------------------------------------------------------------ hub and wake word
  $('openHub').addEventListener('click', (e) => { e.preventDefault(); pom.openHub(); });
  const WAKE = { off: '', loading: 'Loading…', listening: 'Listening for "Bea".', paused: 'Listening for "Bea".', error: '' };
  function showWake(st) {
    const hint = $('wakeHint');
    if (!st || st.state === 'off') { hint.textContent = 'Listening happens on this computer; nothing is sent until Bea hears her name.'; hint.className = 'hint'; return; }
    hint.textContent = st.state === 'error' ? 'Wake word problem: ' + st.message : WAKE[st.state] || st.message;
    hint.className = 'hint ' + (st.state === 'error' ? 'bad' : st.state === 'listening' || st.state === 'paused' ? 'ok' : '');
  }
  pom.onWakeStatus(showWake);
  pom.wakeStatus().then(showWake).catch(() => {});
    setTimeout(() => { $('saved').textContent = ''; }, 2500);
  });

  // ------------------------------------------------------------------ API keys
  const GET_KEY = {
    anthropic: 'https://console.anthropic.com/settings/keys',
    openai: 'https://platform.openai.com/api-keys',
    sarvam: 'https://dashboard.sarvam.ai',
  };
  const el = (tag, props = {}, ...kids) => {
    const n = Object.assign(document.createElement(tag), props);
    for (const k of kids) n.append(k);
    return n;
  };
  const when = (iso) => new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });

  function keyCard(k) {
    const msg = el('span', { className: 'key-msg', role: 'status' });
    const show = (text, ok) => { msg.textContent = text; msg.className = 'key-msg ' + (ok ? 'ok' : 'bad'); };
    const input = el('input', {
      type: 'password', autocomplete: 'off', spellcheck: false,
      placeholder: k.configured ? 'Paste a new key to replace it' : 'Paste your key',
      ariaLabel: `${k.label} API key`,
    });
    const save = el('button', { type: 'button', className: 'primary', textContent: 'Save' });
    const test = el('button', { type: 'button', className: 'ghost', textContent: 'Test', disabled: !k.configured });
    const del = el('button', { type: 'button', className: 'danger', textContent: 'Delete', disabled: k.source !== 'app' });
    if (k.source === 'server') del.title = "This key is set in the server's .env file";

    const busy = (on, label) => {
      for (const b of [save, test, del]) b.disabled = on;
      if (on) show(label, true); else { test.disabled = !k.configured; del.disabled = k.source !== 'app'; }
    };

    async function doSave() {
      const key = input.value.trim();
      if (!key) { show('Paste a key first.', false); input.focus(); return; }
      busy(true, `Checking with ${k.label}…`);
      try {
        await pom.keySave(k.provider, key);
        input.value = '';
        await loadKeys(k.provider, `Saved. ${k.label} accepted the key.`);
      } catch (err) { busy(false); show(err.message, false); }
    }
    save.addEventListener('click', doSave);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); doSave(); } });

    test.addEventListener('click', async () => {
      busy(true, 'Testing…');
      try { const r = await pom.keyTest(k.provider); busy(false); show(r.message, r.ok); }
      catch (err) { busy(false); show(err.message, false); }
    });

    let armed = null;
    del.addEventListener('click', async () => {
      if (!armed) {
        del.textContent = 'Confirm delete'; del.classList.add('armed');
        armed = setTimeout(() => { armed = null; del.textContent = 'Delete'; del.classList.remove('armed'); }, 4000);
        return;
      }
      clearTimeout(armed); armed = null;
      busy(true, 'Deleting…');
      try { await pom.keyDelete(k.provider); await loadKeys(k.provider, 'Key deleted.'); }
      catch (err) { busy(false); show(err.message, false); }
    });

    const state = k.source === 'app' ? `Saved ${k.hint}` : k.source === 'server' ? `From server .env ${k.hint}` : 'No key';
    const meta = el('div', { className: 'key-meta' },
      el('span', { textContent: `Model: ${k.model}` }));
    if (k.updated_at) meta.append(el('span', { textContent: `Updated ${when(k.updated_at)}` }));
    meta.append(el('a', { href: GET_KEY[k.provider], target: '_blank', rel: 'noopener', textContent: 'Get a key' }));

    const card = el('div', { className: 'key' },
      el('div', { className: 'key-head' },
        el('b', { textContent: k.label }),
        el('span', { className: 'chip' + (k.role === 'Main brain' ? ' main' : ''), textContent: k.role }),
        el('span', { className: 'key-state' + (k.configured ? ' ok' : ''), textContent: (k.configured ? '● ' : '○ ') + state })),
      meta,
      el('div', { className: 'key-row' }, input, save),
      el('div', { className: 'key-actions' }, test, del, msg));
    card.dataset.provider = k.provider;
    card.show = show;
    return card;
  }

  async function loadKeys(focusProvider, message) {
    const list = $('keyList'), notice = $('keysNotice');
    notice.hidden = true;
    try {
      const keys = await pom.keysList();
      list.replaceChildren(...keys.map(keyCard));
      if (focusProvider && message) list.querySelector(`[data-provider="${focusProvider}"]`)?.show(message, true);
    } catch (err) {
      list.replaceChildren();
      notice.textContent = err.status === 403 ? err.message
        : `Connect to the Bea server above to manage keys. (${err.message})`;
      notice.hidden = false;
    }
  }
  loadKeys();

  $('test').addEventListener('click', async () => {
    const out = $('testResult');
    out.textContent = 'Checking…'; out.className = '';
    try {
      await pom.saveSettings(collect());
      $('token').value = '';
      const c = await pom.serverConfig();
      const parts = [`Connected as “${c.device_name}”.`, c.chat ? 'Chat ready.' : 'Chat model not configured.', c.voice ? 'Voice ready.' : 'Voice not configured.'];
      out.textContent = parts.join(' '); out.className = c.chat ? 'ok' : 'bad';
      fill(await pom.getSettings());
      loadKeys();
    } catch (err) {
      out.textContent = err.message; out.className = 'bad';
    }
  });
})();
