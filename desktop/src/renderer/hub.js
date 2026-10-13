/* Bea Hub: everything Bea does, in one place.
 * Activity (every run, step by step), approvals, goals, schedules, memory, documents and the apps
 * Bea is connected to. All data comes from the Bea server through pom.api (main process), which
 * only allows /v1/agent, /v1/notifications and /v1/connectors paths.
 */
(() => {
  'use strict';
  const pom = window.pom;
  const $ = (id) => document.getElementById(id);
  const view = $('view');
  const TABS = { activity: 'Activity', approvals: 'Approvals', goals: 'Goals', schedules: 'Schedules', memory: 'Memory', documents: 'Documents', connections: 'Connections' };
  let tab = 'activity', timer = 0;

  // ------------------------------------------------------------------ helpers
  function h(tag, props = {}, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === 'class') el.className = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else if (k === 'html') el.innerHTML = v;
      else if (v !== undefined && v !== null && v !== false) el.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) el.append(kid instanceof Node ? kid : String(kid));
    return el;
  }
  const btn = (text, onclick, cls = '') => h('button', { type: 'button', class: 'btn ' + cls, onclick }, text);
  const pill = (text, cls) => h('span', { class: 'pill ' + (cls || text) }, text.replace('_', ' '));
  const when = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '');
  const empty = (text) => h('p', { class: 'empty' }, text);
  async function api(method, path, body) {
    try { return await pom.api(method, path, body); }
    catch (e) { flash(e.message, true); throw e; }
  }
  function flash(text, bad) {
    const m = h('p', { class: 'msg ' + (bad ? 'bad' : 'ok') }, text);
    view.prepend(m); setTimeout(() => m.remove(), 5000);
  }
  function actions(...els) { $('actions').replaceChildren(...els); }

  // Small, safe Markdown renderer for Bea's documents and answers (escapes first, https links only).
  function esc(s) { return s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  function inline(s) {
    return s.replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
      .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<i>$2</i>')
      .replace(/\[([^\]]+)\]\((https:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  }
  function md(text) {
    const parts = esc(text || '').split(/```(?:[\w-]*)\n?([\s\S]*?)(?:```|$)/);
    let out = '';
    parts.forEach((p, i) => {
      if (i % 2) { out += `<pre><code>${p}</code></pre>`; return; }
      for (const block of p.split(/\n{2,}/)) {
        if (!block.trim()) continue;
        const lines = block.split('\n');
        const head = block.match(/^(#{1,3})\s+(.*)$/);
        if (head && lines.length === 1) out += `<h${head[1].length}>${inline(head[2])}</h${head[1].length}>`;
        else if (lines.every((l) => /^\s*\|.*\|\s*$/.test(l))) {
          const rows = lines.filter((l) => !/^\s*\|[\s:|-]+\|\s*$/.test(l));
          out += '<table>' + rows.map((r, ri) => '<tr>' + r.trim().slice(1, -1).split('|').map((c) => (ri ? '<td>' : '<th>') + inline(c.trim())).join('') + '</tr>').join('') + '</table>';
        } else if (lines.every((l) => /^\s*([-*•]|\d+\.)\s+/.test(l) || !l.trim())) {
          out += '<ul>' + lines.filter((l) => l.trim()).map((l) => '<li>' + inline(l.replace(/^\s*([-*•]|\d+\.)\s+/, '')) + '</li>').join('') + '</ul>';
        } else out += '<p>' + lines.map((l) => { const m = l.match(/^(#{1,3})\s+(.*)$/); return m ? `<b>${inline(m[2])}</b>` : inline(l); }).join('<br>') + '</p>';
      }
    });
    return out;
  }

  // ------------------------------------------------------------------ navigation
  function go(name) {
    const [t, arg] = String(name || 'activity').replace(/^#/, '').split('/');
    tab = TABS[t] ? t : 'activity';
    clearInterval(timer); timer = 0;
    document.querySelectorAll('.side button').forEach((b) => b.setAttribute('aria-current', b.dataset.tab === tab ? 'page' : 'false'));
    $('title').textContent = TABS[tab];
    actions();
    view.replaceChildren(h('p', { class: 'muted' }, 'Loading…'));
    RENDER[tab](arg).catch((e) => view.replaceChildren(h('p', { class: 'msg bad' }, e.message)));
    $('main').focus();
  }
  document.querySelectorAll('.side button').forEach((b) => b.addEventListener('click', () => { history.replaceState(null, '', '#' + b.dataset.tab); go(b.dataset.tab); }));
  pom.onHubTab((t) => go(t));

  async function badge() {
    try {
      const n = (await pom.api('GET', '/v1/agent/approvals')).length;
      $('apBadge').hidden = !n; $('apBadge').textContent = n;
    } catch {}
  }

  // ------------------------------------------------------------------ views
  const RENDER = {
    async activity(runId) {
      if (runId) return runDetail(runId);
      const runs = await api('GET', '/v1/agent/runs?limit=60');
      actions(btn('Refresh', () => go('activity')));
      if (!runs.length) { view.replaceChildren(empty('Nothing yet. Give Bea a task in the chat panel (agent mode on).')); return; }
      view.replaceChildren(h('div', { class: 'list' }, runs.map((r) => h('div', { class: 'card click', onclick: () => go('activity/' + r.id) },
        h('div', { class: 'row' }, pill(r.status), r.trigger === 'schedule' ? pill('background', '') : null,
          h('b', { class: 'grow' }, r.goal.length > 140 ? r.goal.slice(0, 137) + '…' : r.goal), h('span', { class: 'muted' }, when(r.created_at))),
        r.result ? h('p', { class: 'muted', style: 'margin:6px 0 0' }, r.result.length > 220 ? r.result.slice(0, 217) + '…' : r.result) : null))));
      if (runs.some((r) => r.status === 'running' || r.status === 'waiting_approval')) timer = setInterval(() => tab === 'activity' && go('activity'), 6000);
    },
    async approvals() {
      const [pending, all] = await Promise.all([api('GET', '/v1/agent/approvals'), api('GET', '/v1/agent/approvals?status=all')]);
      actions(btn('Refresh', () => go('approvals')));
      const past = all.filter((a) => a.status !== 'pending').slice(0, 40);
      view.replaceChildren(
        h('h2', {}, 'Waiting for you'),
        pending.length ? h('div', {}, pending.map(approvalCard)) : empty('Nothing is waiting for your approval.'),
        h('h2', {}, 'History'),
        past.length ? h('div', {}, past.map((a) => h('div', { class: 'card row' }, pill(a.status), h('span', { class: 'grow' }, a.summary),
          a.note ? h('span', { class: 'muted' }, `“${a.note}”`) : null, h('span', { class: 'muted' }, when(a.decided_at || a.created_at))))) : empty('No decisions yet.'));
      badge();
    },
    async goals() {
      const goals = await api('GET', '/v1/agent/goals');
      if (!goals.length) { view.replaceChildren(empty('No goals yet. Ask Bea: "Help me reach … by …, and track it as a goal."')); return; }
      view.replaceChildren(...goals.map((g) => {
        const steps = h('ul', { class: 'steps' }, g.steps.map((st, i) => h('li', { class: st.done ? 'done' : '' },
          h('input', { type: 'checkbox', checked: st.done, 'aria-label': st.text, onchange: async (e) => {
            g.steps[i].done = e.target.checked; await api('PATCH', `/v1/agent/goals/${g.id}`, { steps: g.steps }); go('goals');
          } }), h('span', {}, st.text))));
        const done = g.steps.filter((s) => s.done).length;
        return h('div', { class: 'card' },
          h('div', { class: 'row' }, pill(g.status, g.status === 'active' ? 'running' : g.status === 'done' ? 'done' : 'cancelled'),
            h('b', { class: 'grow' }, g.title), h('span', { class: 'muted' }, `${done}/${g.steps.length} steps${g.due ? ' · due ' + g.due : ''}`)),
          g.details ? h('p', { class: 'muted' }, g.details) : null, steps,
          h('div', { class: 'row', style: 'margin-top:8px' },
            g.status !== 'done' ? btn('Mark done', async () => { await api('PATCH', `/v1/agent/goals/${g.id}`, { status: 'done' }); go('goals'); }, 'small') : null,
            g.status !== 'active' ? btn('Reactivate', async () => { await api('PATCH', `/v1/agent/goals/${g.id}`, { status: 'active' }); go('goals'); }, 'small') : null,
            g.status === 'active' ? btn('Drop', async () => { await api('PATCH', `/v1/agent/goals/${g.id}`, { status: 'dropped' }); go('goals'); }, 'small') : null,
            confirmBtn('Delete', async () => { await api('DELETE', `/v1/agent/goals/${g.id}`); go('goals'); })));
      }));
    },
    async schedules() {
      const items = await api('GET', '/v1/agent/schedules');
      if (!items.length) { view.replaceChildren(empty('Nothing scheduled. Ask Bea: "Every weekday at 9, check my inbox and tell me what needs a reply."')); return; }
      view.replaceChildren(...items.map((j) => h('div', { class: 'card' },
        h('div', { class: 'row' }, pill(j.active ? 'on' : 'off', j.active ? 'on' : ''), pill(j.kind, ''), h('b', { class: 'grow' }, j.title),
          h('span', { class: 'muted' }, j.when + ' · ' + j.timezone)),
        h('p', { class: 'muted', style: 'margin:6px 0' }, j.prompt),
        h('div', { class: 'row' },
          btn(j.active ? 'Pause' : 'Resume', async () => { await api('PATCH', `/v1/agent/schedules/${j.id}`, { active: !j.active }); go('schedules'); }, 'small'),
          btn('Run now', async () => { const r = await api('POST', `/v1/agent/schedules/${j.id}/run-now`); flash(r.run_id ? 'Started. See Activity.' : 'Reminder sent.'); }, 'small'),
          j.last_run_id ? btn('Last run', () => go('activity/' + j.last_run_id), 'small') : null,
          confirmBtn('Delete', async () => { await api('DELETE', `/v1/agent/schedules/${j.id}`); go('schedules'); })))));
    },
    async memory() {
      const items = await api('GET', '/v1/agent/memories');
      const text = h('input', { type: 'text', maxlength: 1000, placeholder: 'e.g. My manager is Priya; weekly review is Friday 4pm' });
      const kind = h('select', {}, ['fact', 'preference', 'person', 'project'].map((k) => h('option', { value: k }, k)));
      const add = async () => { if (!text.value.trim()) return; await api('POST', '/v1/agent/memories', { text: text.value.trim(), kind: kind.value }); go('memory'); };
      text.addEventListener('keydown', (e) => e.key === 'Enter' && add());
      actions(items.length ? confirmBtn('Forget everything', async () => { await api('DELETE', '/v1/agent/memories'); go('memory'); }) : '');
      view.replaceChildren(
        h('p', { class: 'muted' }, 'What Bea remembers about you. She uses it to personalise her work; you can add or remove anything.'),
        h('div', { class: 'card row' }, h('div', { class: 'grow' }, text), h('div', { style: 'width:130px' }, kind), btn('Add', add, 'primary')),
        items.length ? h('div', {}, items.map((m) => h('div', { class: 'card row' }, pill(m.kind, ''), h('span', { class: 'grow' }, m.text),
          h('span', { class: 'muted' }, when(m.created_at)), btn('Forget', async () => { await api('DELETE', `/v1/agent/memories/${m.id}`); go('memory'); }, 'small danger')))) : empty('Bea hasn\'t saved anything yet.'));
    },
    async documents(docId) {
      if (docId) return docDetail(docId);
      const docs = await api('GET', '/v1/agent/documents');
      if (!docs.length) { view.replaceChildren(empty('No documents yet. Ask Bea for a report, brief, agenda or plan.')); return; }
      view.replaceChildren(...docs.map((d) => h('div', { class: 'card click row', onclick: () => go('documents/' + d.id) },
        h('b', { class: 'grow' }, '📄 ' + d.title), h('span', { class: 'muted' }, `${Math.ceil(d.size / 1000)}k chars · ${when(d.updated_at)}`))));
    },
    connections,
  };

  function confirmBtn(text, fn) {
    let armed = 0;
    const b = btn(text, async () => {
      if (!armed) { b.textContent = 'Click again to confirm'; armed = setTimeout(() => { armed = 0; b.textContent = text; }, 3500); return; }
      clearTimeout(armed); armed = 0; b.disabled = true; await fn().catch(() => (b.disabled = false));
    }, 'small danger');
    return b;
  }

  function approvalCard(a) {
    const note = h('input', { type: 'text', maxlength: 300, placeholder: 'Note for Bea (optional), e.g. "send it tomorrow"' });
    const decide = async (approve) => {
      card.querySelectorAll('button').forEach((b) => (b.disabled = true));
      try { const r = await api('POST', `/v1/agent/approvals/${a.id}`, { approve, note: note.value.trim() || null }); flash(r.status === 'expired' ? 'That request had already expired.' : approve ? 'Approved.' : 'Denied.'); }
      finally { go('approvals'); }
    };
    const card = h('div', { class: 'card', style: 'border-color:var(--warn)' },
      h('div', { class: 'row' }, pill('pending'), h('b', { class: 'grow' }, a.summary), h('span', { class: 'muted' }, when(a.created_at))),
      h('details', {}, h('summary', {}, 'Details'), h('pre', {}, JSON.stringify(a.details, null, 2))),
      h('div', { class: 'row', style: 'margin-top:8px' }, btn('Approve', () => decide(true), 'primary'), btn('Deny', () => decide(false)), h('div', { class: 'grow' }, note),
        btn('Open task', () => go('activity/' + a.run_id), 'small')));
    return card;
  }

  async function runDetail(id) {
    const r = await api('GET', `/v1/agent/runs/${id}`);
    actions(btn('← All activity', () => go('activity')), ['running', 'waiting_approval'].includes(r.status)
      ? btn('Stop', async () => { await api('POST', `/v1/agent/runs/${id}/cancel`); go('activity/' + id); }, 'danger') : '');
    const LABEL = { run_started: 'Started', plan: 'Plan', tool: 'Step', tool_result: 'Result', thinking: 'Thinking', approval: 'Approval',
      approval_decided: 'Decision', document: 'Document', client_action: 'On your desktop', message: 'Answer', error: 'Error', done: 'Finished' };
    const shown = r.events.filter((e) => !((e.type === 'tool' || e.type === 'tool_result') && e.data.name === 'update_plan')); // the plan has its own entry
    const items = shown.map((e) => {
      const d = e.data; let body;
      switch (e.type) {
        case 'run_started': body = `${d.trigger === 'schedule' ? 'Background task' : 'Your request'} · ${d.tools.length} tools available`; break;
        case 'plan': body = h('div', {}, d.steps.map((s) => h('div', {}, (s.status === 'done' ? '✓ ' : s.status === 'in_progress' ? '▸ ' : '○ ') + s.text))); break;
        case 'tool': body = h('span', {}, h('b', {}, d.label || d.name), ' ', d.summary || ''); break;
        case 'tool_result': body = h('span', { class: d.ok ? '' : 'msg bad' }, (d.ok ? '✓ ' : '✗ ') + (d.preview || '')); break;
        case 'thinking': body = h('details', {}, h('summary', {}, 'Reasoning summary'), h('pre', {}, d.text)); break;
        case 'approval': body = h('span', {}, '✋ ', d.summary); break;
        case 'approval_decided': body = h('span', {}, d.status + (d.note ? ` · “${d.note}”` : '')); break;
        case 'document': body = h('a', { href: '#documents/' + d.document_id, onclick: (ev) => { ev.preventDefault(); go('documents/' + d.document_id); } }, '📄 ' + d.title); break;
        case 'client_action': body = d.action === 'open_url' ? 'Opened ' + d.url : 'Copied text to your clipboard'; break;
        case 'message': body = h('div', { class: 'doc', html: md(d.text) }); break;
        case 'done': body = `${d.status} · ${d.usage ? d.usage.input_tokens + ' in / ' + d.usage.output_tokens + ' out tokens' : ''}`; break;
        default: body = d.message || JSON.stringify(d);
      }
      const cls = e.type === 'tool' ? 'tool' : e.type === 'error' || (e.type === 'tool_result' && !d.ok) ? 'bad' : e.type.startsWith('approval') ? 'approval' : '';
      return h('li', { class: cls }, h('span', { class: 'k' }, LABEL[e.type] || e.type), h('div', {}, body));
    });
    view.replaceChildren(
      h('div', { class: 'card' }, h('div', { class: 'row' }, pill(r.status), h('b', { class: 'grow' }, r.goal)),
        h('p', { class: 'muted', style: 'margin:6px 0 0' }, `${when(r.created_at)}${r.finished_at ? ' → ' + when(r.finished_at) : ''} · ${r.input_tokens + r.output_tokens} tokens`)),
      h('h2', {}, 'Every step'), h('ul', { class: 'timeline' }, items));
    if (['running', 'waiting_approval'].includes(r.status)) timer = setInterval(() => tab === 'activity' && go('activity/' + id), 4000);
  }

  async function docDetail(id) {
    const d = await api('GET', `/v1/agent/documents/${id}`);
    actions(btn('← All documents', () => go('documents')), btn('Copy', async () => { await pom.copy(d.content); flash('Copied.'); }),
      btn('Save as…', async () => { if (await pom.saveFile(d.title, d.content)) flash('Saved.'); }, 'primary'),
      confirmBtn('Delete', async () => { await api('DELETE', `/v1/agent/documents/${id}`); go('documents'); }));
    view.replaceChildren(h('p', { class: 'muted' }, 'Updated ' + when(d.updated_at)), h('article', { class: 'doc', html: md(d.content) }));
  }

  // ------------------------------------------------------------------ connections
  async function connections() {
    const c = await api('GET', '/v1/connectors');
    const accounts = (p) => c.accounts.filter((a) => a.provider === p);
    actions(btn('Refresh', () => go('connections')));
    const accountRow = (a) => h('div', { class: 'row', style: 'margin-top:6px' }, pill(a.enabled ? 'connected' : 'off', a.enabled ? 'connected' : ''), h('b', { class: 'grow' }, a.name),
      btn('Test', async () => { const r = await api('POST', `/v1/connectors/${a.id}/test`); flash(r.message, !r.ok); }, 'small'),
      btn(a.enabled ? 'Turn off' : 'Turn on', async () => { await api('PATCH', `/v1/connectors/${a.id}`, { enabled: !a.enabled }); go('connections'); }, 'small'),
      confirmBtn('Remove', async () => { await api('DELETE', `/v1/connectors/${a.id}`); go('connections'); }));

    const ms = c.providers.microsoft, gg = c.providers.google;
    const msBox = h('div', {});
    const ggBox = h('div', {});
    view.replaceChildren(
      h('p', { class: 'muted' }, 'Bea can read and draft in these apps on her own. Sending email, creating invites and changing data always waits for your approval.'),
      h('div', { class: 'card' }, h('div', { class: 'row' }, h('b', { class: 'grow' }, 'Microsoft 365 (Outlook mail and calendar)'),
        ms.configured ? btn('Connect an account', () => connectMicrosoft(msBox), 'primary') : pill('not set up', '')),
        ...accounts('microsoft').map(accountRow), msBox, !ms.configured ? h('div', { class: 'help' }, ms.setup_help) : null),
      h('div', { class: 'card' }, h('div', { class: 'row' }, h('b', { class: 'grow' }, 'Google (Gmail and Calendar)'),
        gg.configured ? btn('Connect an account', () => connectGoogle(ggBox), 'primary') : pill('not set up', '')),
        ...accounts('google').map(accountRow), ggBox, !gg.configured ? h('div', { class: 'help' }, gg.setup_help) : null),
      h('div', { class: 'card' }, h('div', { class: 'row' }, h('b', { class: 'grow' }, 'Company and custom APIs'), btn('Add an API', () => apiForm(null), 'primary')),
        h('p', { class: 'muted' }, 'For your project platform or any internal REST API. Describe its endpoints so Bea knows how to use it.'),
        ...accounts('custom_api').map((a) => h('div', {}, accountRow(a),
          h('div', { class: 'row muted' }, a.meta.base_url, a.meta.allow_write ? pill('can write (with approval)', 'pending') : pill('read-only', ''), btn('Edit', () => apiForm(a), 'small'))))),
      h('div', { id: 'apiForm' }));
  }

  async function poll(path, box, label) {
    for (let i = 0; i < 200; i++) {
      await new Promise((r) => setTimeout(r, 3000));
      if (!box.isConnected) return;
      let st; try { st = await pom.api('GET', path); } catch (e) { box.replaceChildren(h('p', { class: 'msg bad' }, e.message)); return; }
      if (st.status === 'connected') { flash(`${label} connected: ${st.account ? st.account.name : ''}`); go('connections'); return; }
      if (st.status === 'failed' || st.status === 'expired') { box.replaceChildren(h('p', { class: 'msg bad' }, st.error || 'Sign-in ' + st.status + '. Try again.')); return; }
    }
  }
  async function connectMicrosoft(box) {
    const f = await api('POST', '/v1/connectors/microsoft/start');
    box.replaceChildren(h('div', { class: 'card', style: 'margin-top:10px' },
      h('p', {}, 'Open the Microsoft sign-in page and enter this code:'), h('div', { class: 'row' }, h('span', { class: 'code' }, f.user_code),
        btn('Copy code', () => pom.copy(f.user_code), 'small'), btn('Open sign-in page', () => pom.openExternal(f.verification_uri), 'primary')),
      h('p', { class: 'muted' }, 'Waiting for you to finish signing in…')));
    pom.openExternal(f.verification_uri).catch(() => {});
    poll(`/v1/connectors/microsoft/status/${f.flow_id}`, box, 'Microsoft 365');
  }
  async function connectGoogle(box) {
    const f = await api('POST', '/v1/connectors/google/start');
    box.replaceChildren(h('p', { class: 'muted' }, 'Finish signing in in your browser. This page updates when you are done.'),
      btn('Open the sign-in page again', () => pom.openExternal(f.auth_url), 'small'));
    pom.openExternal(f.auth_url).catch(() => {});
    poll(`/v1/connectors/google/status/${f.flow_id}`, box, 'Google');
  }

  function apiForm(a) {
    const m = (a && a.meta) || {};
    const f = {
      name: h('input', { type: 'text', value: a ? a.name : '', placeholder: 'projects', disabled: !!a }),
      base_url: h('input', { type: 'url', value: m.base_url || '', placeholder: 'https://projects.betteranalytics.in/api' }),
      auth_header: h('input', { type: 'text', value: m.auth_header || 'Authorization' }),
      auth_scheme: h('input', { type: 'text', value: m.auth_scheme ?? 'Bearer' }),
      token: h('input', { type: 'password', autocomplete: 'off', placeholder: a ? 'Saved. Paste a new token to replace it.' : 'API token (stored encrypted on the server)' }),
      description: h('textarea', { placeholder: 'What the API is for and its main endpoints, e.g.\nGET /projects?status=active — list projects\nGET /projects/{id}/reports — daily reports\nPATCH /tasks/{id} {"status"} — update a task' }, m.description || ''),
      openapi_url: h('input', { type: 'url', value: m.openapi_url || '', placeholder: 'Optional: https://…/openapi.json' }),
      health_path: h('input', { type: 'text', value: m.health_path || '', placeholder: 'Optional: /health (used by Test)' }),
      allow_write: h('input', { type: 'checkbox', checked: !!m.allow_write }),
    };
    const save = async () => {
      const body = Object.fromEntries(Object.entries(f).map(([k, el]) => [k, el.type === 'checkbox' ? el.checked : el.value.trim()]));
      if (!body.token) delete body.token;
      if (!body.openapi_url) body.openapi_url = null;
      if (!body.health_path) body.health_path = null;
      if (a) { delete body.name; await api('PUT', `/v1/connectors/custom-apis/${a.id}`, { ...body, name: a.name }); } else await api('POST', '/v1/connectors/custom-apis', body);
      flash('Saved.'); go('connections');
    };
    const L = (text, el) => [h('label', {}, text), el];
    $('apiForm').replaceChildren(h('div', { class: 'card form' }, h('h2', { style: 'margin-top:0' }, a ? `Edit ${a.name}` : 'Add an API'),
      ...L('Short name Bea uses', f.name), ...L('Base URL', f.base_url), ...L('Auth header', f.auth_header), ...L('Auth scheme (e.g. Bearer; empty for a raw key)', f.auth_scheme),
      ...L('Token or API key', f.token), ...L('Description for Bea', f.description), ...L('OpenAPI spec URL', f.openapi_url), ...L('Health check path', f.health_path),
      h('label', { class: 'check' }, f.allow_write, 'Allow Bea to change data (POST/PUT/PATCH/DELETE), always with your approval'),
      h('div', { class: 'row', style: 'margin-top:12px' }, btn('Save', save, 'primary'), btn('Cancel', () => $('apiForm').replaceChildren()))));
    $('apiForm').scrollIntoView({ behavior: 'smooth' });
  }

  // ------------------------------------------------------------------ start
  $('foot').textContent = 'Bea keeps a full history of what she did and why.';
  badge(); setInterval(badge, 15000);
  go(location.hash || 'activity');
})();
