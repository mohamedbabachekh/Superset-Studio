'use strict';
/* SharpSight BI Studio — the frame: signing in, the API, navigation, and the small pieces every screen uses.
   Every rule lives in the service. This page asks, draws what it is told, and sends back what a person chose;
   it validates nothing on its own. No value from the service is ever written as HTML: text only. */
const Studio = (() => {
  const S = { session: null, me: null, config: null, route: { view: 'home', args: [] }, token: 0 };

  // ── building the page ──────────────────────────────────────────────────────────────────────────────────
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === 'class') el.className = value;
      else if (key === 'text') el.textContent = value;
      else if (key === 'style') el.style.cssText = value;
      else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
      else if (key === 'value') el.value = value;
      else if (value === true) el.setAttribute(key, '');
      else el.setAttribute(key, value);
    }
    const add = (kid) => {
      if (kid === null || kid === undefined || kid === false) return;
      if (Array.isArray(kid)) kid.forEach(add);
      else el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    };
    kids.forEach(add);
    return el;
  }
  const enc = encodeURIComponent;
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const fmt = (n) => (n === null || n === undefined ? '—' : Number(n).toLocaleString('en-US'));

  // ── the service ────────────────────────────────────────────────────────────────────────────────────────
  async function request(method, path, body, session) {
    const who = session || S.session;
    const answer = await fetch(path, {
      method, headers: { 'Authorization': `Bearer ${who.token}`, 'X-SharpSight-User': who.user,
                         ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined });
    return answer;
  }
  async function api(method, path, body, session) {
    const answer = await request(method, path, body, session);
    let data = null;
    try { data = await answer.json(); } catch (_) { /* no body */ }
    if (!answer.ok) {
      const reason = data && (data.error || data.detail);
      const error = new Error(typeof reason === 'string' ? reason : `${method} ${path} → ${answer.status}`);
      error.status = answer.status; error.body = data;
      throw error;
    }
    return data;
  }
  async function blob(path) {
    const answer = await request('GET', path);
    if (!answer.ok) throw new Error(`${path} → ${answer.status}`);
    return URL.createObjectURL(await answer.blob());
  }
  async function download(path, name) {
    try {
      const link = h('a', { href: await blob(path), download: name });
      document.body.append(link); link.click(); link.remove();
    } catch (error) { toast(error.message, 'bad'); }
  }

  // ── small pieces ───────────────────────────────────────────────────────────────────────────────────────
  let toastTimer = null;
  function toast(message, kind) {
    document.querySelectorAll('.toast').forEach((el) => el.remove());
    document.body.append(h('div', { class: `toast ${kind || ''}`, text: message }));
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => document.querySelectorAll('.toast').forEach((el) => el.remove()), kind === 'bad' ? 9000 : 4000);
  }
  const STATUS = {
    certified: ['ok', 'Certified'], not_certified: ['warn', 'Not certified'], generated: ['info', 'Generated'],
    local: ['', 'Local'], not_available: ['', 'Not available'], shared: ['accent', 'Shared'],
    DRAFT: ['', 'DRAFT'], AI_GENERATED: ['info', 'AI_GENERATED'], VALIDATING: ['info', 'VALIDATING'],
    VALIDATION_FAILED: ['bad', 'VALIDATION_FAILED'], READY_FOR_REVIEW: ['accent', 'READY_FOR_REVIEW'],
    APPROVED_FOR_UAT: ['info', 'APPROVED_FOR_UAT'], UAT: ['info', 'UAT'], UAT_FAILED: ['bad', 'UAT_FAILED'],
    APPROVED_FOR_PROD: ['info', 'APPROVED_FOR_PROD'], PROD: ['ok', 'PROD'],
    pass: ['ok', 'pass'], warn: ['warn', 'warn'], fail: ['bad', 'fail'], skipped: ['', 'skipped'],
    succeeded: ['ok', 'succeeded'], failed: ['bad', 'failed'], running: ['info', 'running'],
  };
  function badge(status, label) {
    const [kind, text] = STATUS[status] || ['', String(status ?? '—')];
    return h('span', { class: `badge ${kind}`, text: label || text });
  }
  const dot = (status) => h('i', { class: `dot ${(STATUS[status] || [''])[0]}` });
  function table(columns, rows, onRow) {
    return h('table', { class: 'grid' },
      h('thead', null, h('tr', null, columns.map((c) => h('th', { text: c.title })))),
      h('tbody', null, rows.length ? rows.map((row) => h('tr', { class: onRow ? 'click' : null, onclick: onRow ? () => onRow(row) : null },
        columns.map((c) => h('td', { class: c.class || null }, c.cell(row)))))
        : h('tr', null, h('td', { colspan: columns.length, class: 'muted', text: 'Nothing here yet.' }))));
  }
  function field(label, control) { return h('label', { class: 'field' }, label, control); }
  function select(value, options, onchange, attrs) {
    const el = h('select', { class: 'input', onchange: (e) => onchange(e.target.value), ...(attrs || {}) },
      options.map(([v, text]) => h('option', { value: v, text })));
    el.value = value ?? '';
    return el;
  }
  function dialog(title, body, buttons) {
    const close = () => overlay.remove();
    const overlay = h('div', { class: 'overlay', onclick: (e) => { if (e.target === overlay) close(); } },
      h('div', { class: 'dialog stack' }, h('h2', { text: title }), body,
        h('div', { class: 'row', style: 'justify-content:flex-end;margin-top:6px' },
          (buttons || []).map(([text, kind, fn]) => h('button', { class: `btn ${kind || ''}`, text, onclick: () => fn(close) })),
          h('button', { class: 'btn', text: 'Close', onclick: close }))));
    document.body.append(overlay);
    return close;
  }
  /* « Impact analysis » before a change: what the service refused with is shown, and the person decides. */
  function impactBox(impact) {
    const used = impact.used_by || {};
    return h('div', { class: `note ${impact.severity === 'high' ? 'bad' : impact.total ? 'warn' : ''} stack` },
      h('div', { style: 'font-weight:600', text: impact.message }),
      (used.dashboards || []).length ? h('div', null, h('span', { class: 'muted', text: 'Dashboards  ' }),
        used.dashboards.map((d) => h('span', { class: 'row', style: 'display:inline-flex;margin-right:10px' }, d.title || d.id, badge(d.status)))) : null,
      (used.charts || []).length ? h('div', { class: 'small' }, h('span', { class: 'muted', text: 'Charts  ' }),
        used.charts.map((c) => `${c.title} (${c.dashboard})`).join(' · ')) : null,
      ['metrics', 'datasets', 'shared_charts', 'shared_sections'].map((key) => (used[key] || []).length
        ? h('div', { class: 'small' }, h('span', { class: 'muted', text: `${key.replace('_', ' ')}  ` }), used[key].join(' · ')) : null),
      (used.clients || []).length ? h('div', { class: 'small' }, h('span', { class: 'muted', text: 'Clients  ' }), used.clients.join(' · ')) : null);
  }

  // ── navigation ─────────────────────────────────────────────────────────────────────────────────────────
  const NAV = [
    ['', [['home', 'Home']]],
    ['DEVELOP', [['projects', 'Projects'], ['dashboards', 'Dashboards'], ['data', 'Data Assets'], ['metrics', 'Metrics'],
                 ['templates', 'Templates'], ['datalab', 'SQL / Data Lab']]],
    ['GOVERN', [['tests', 'Tests'], ['certification', 'Certification'], ['review', 'Code Review'],
                ['impact', 'Impact Analysis'], ['catalog', 'Data Catalog']]],
    ['DELIVER', [['releases', 'Releases'], ['pipelines', 'Pipelines'], ['environments', 'Environments'],
                 ['deployments', 'Deployments']]],
    ['OPERATE', [['monitoring', 'Monitoring'], ['dataquality', 'Data Quality'], ['performance', 'Performance'],
                 ['audit', 'Audit Logs']]],
    ['CLIENTS', [['organizations', 'Organizations'], ['workspaces', 'Workspaces'], ['users', 'Users'],
                 ['permissions', 'Permissions']]],
  ];
  function go(view, ...args) {
    const target = '#/' + [view, ...args.filter((a) => a !== undefined && a !== null)].map(enc).join('/');
    if (location.hash === target) render(); else location.hash = target;
  }
  function readRoute() {
    const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent);
    S.route = { view: parts[0] || 'home', args: parts.slice(1) };
  }

  // ── signing in ─────────────────────────────────────────────────────────────────────────────────────────
  function signIn(message) {
    const user = h('input', { class: 'input', value: 'dev.user', autocomplete: 'username' });
    const token = h('input', { class: 'input', type: 'password', autocomplete: 'current-password', placeholder: 'AIBI_API_TOKEN, from .env' });
    const problem = h('div', { class: 'small', style: 'color:var(--error)', text: message || '' });
    const submit = async (event) => {
      event.preventDefault();
      const session = { user: user.value.trim(), token: token.value.trim() };
      try {
        S.me = await api('GET', '/me', undefined, session);
        S.session = session;
        sessionStorage.setItem('studio', JSON.stringify(session));
        render();
      } catch (error) { problem.textContent = error.status === 401 ? 'That is not the API token.' : error.message; }
    };
    return h('form', { class: 'card stack signin', onsubmit: submit },
      h('div', { class: 'brand' }, h('i'), 'SharpSight BI Studio'),
      h('div', { class: 'muted', text: 'In the platform a person signs in to the portal. In this kit the Studio is the engine’s own page: name a user of registry/security/rbac.yaml, and give the gateway token.' }),
      field('User', user), field('API token', token), problem,
      h('button', { class: 'btn primary', type: 'submit', text: 'Sign in' }),
      h('div', { class: 'small muted', text: 'dev.user builds · lead.reviewer reviews · release.manager releases and certifies' }));
  }
  function signOut() { sessionStorage.removeItem('studio'); S.session = null; S.me = null; render(); }
  async function switchUser(user) {
    try {
      const session = { ...S.session, user };
      S.me = await api('GET', '/me', undefined, session);
      S.session = session;
      sessionStorage.setItem('studio', JSON.stringify(session));
      render();
    } catch (error) { toast(error.message, 'bad'); }
  }

  // ── the frame ──────────────────────────────────────────────────────────────────────────────────────────
  function topbar() {
    const search = h('input', { class: 'input', style: 'width:300px', placeholder: 'Search assets…  (datasets, metrics, charts, dashboards)',
      onkeydown: (e) => { if (e.key === 'Enter') go('catalog', e.target.value); } });
    const who = h('input', { class: 'input', style: 'width:140px', value: S.me.user, title: 'Act as another user of rbac.yaml',
      onchange: (e) => switchUser(e.target.value.trim()) });
    return h('div', { class: 'topbar' },
      h('a', { class: 'brand', style: 'color:inherit', onclick: () => go('home') }, h('i'), 'SharpSight BI Studio'),
      h('span', { class: 'muted', text: `Workspace: ${S.me.tenant}` }),
      h('span', { class: 'grow' }), search,
      h('span', { class: 'small muted', text: S.me.roles.join(', ') || 'no role' }), who,
      h('button', { class: 'btn ghost', text: 'Sign out', onclick: signOut }));
  }
  function nav() {
    const current = S.route.view === 'builder' ? 'dashboards' : S.route.view === 'asset' ? { dataset: 'data', dimension: 'data',
      chart: 'data', section: 'data', metric: 'metrics', template: 'templates', chart_template: 'templates', dashboard: 'dashboards' }[S.route.args[0]] : S.route.view;
    return h('nav', { class: 'nav' }, NAV.map(([group, items]) => [group ? h('h4', { text: group }) : null,
      items.map(([view, label]) => h('a', { class: `${view === 'home' ? 'home ' : ''}${view === current ? 'on' : ''}`, text: label, onclick: () => go(view) }))]));
  }
  async function render() {
    const app = document.getElementById('app');
    const mine = ++S.token;
    if (!S.config) {
      try { S.config = await (await fetch('/builder/config')).json(); } catch (_) { S.config = { tokens: { colors: {} } }; }
      const colors = S.config.tokens.colors || {};
      const names = { primary: 'primary', info: 'info', success: 'success', warning: 'warning', error: 'error', text: 'text',
                      text_muted: 'muted', border: 'border', surface: 'surface', surface_muted: 'bg' };
      for (const [token, name] of Object.entries(names)) if (colors[token]) document.documentElement.style.setProperty(`--${name}`, colors[token]);
    }
    if (!S.session) {
      const kept = sessionStorage.getItem('studio');
      if (kept) {
        try { const session = JSON.parse(kept); S.me = await api('GET', '/me', undefined, session); S.session = session; } catch (_) { sessionStorage.removeItem('studio'); }
      }
      if (!S.session) { app.replaceChildren(signIn()); return; }
    }
    readRoute();
    const main = h('main', { class: 'main' }, h('div', { class: 'page muted', text: 'Loading…' }));
    app.replaceChildren(topbar(), h('div', { class: 'frame' }, nav(), main));
    const view = Studio.views[S.route.view] || Studio.views.home;
    try {
      const content = await view(...S.route.args);
      if (mine === S.token) main.replaceChildren(content);
    } catch (error) {
      if (error.status === 401) return signOut();
      if (mine === S.token) main.replaceChildren(h('div', { class: 'page' }, h('div', { class: 'note bad', text: error.message })));
    }
  }
  /* After an action that took a while: draw the screen again ONLY if the person is still on it. An approval
     takes seconds; by the time it answers they may be composing a dashboard, and that must not be reloaded. */
  function refresh(from) { if (location.hash === from) render(); }
  function page(title, lead, ...content) {
    return h('div', { class: 'page' }, h('h1', { text: title }), lead ? h('p', { class: 'lead', text: lead }) : null, ...content);
  }
  function start() {
    window.addEventListener('hashchange', render);
    // leaving the page with a dashboard half composed: the browser asks first
    window.addEventListener('beforeunload', (event) => { if (Studio.unsaved && Studio.unsaved()) { event.preventDefault(); event.returnValue = ''; } });
    render();
  }

  return { S, h, enc, clone, fmt, api, blob, download, toast, badge, dot, table, field, select, dialog, impactBox, go, render, refresh, page, start,
           can: (capability) => S.me.capabilities.includes(capability), views: {} };
})();
