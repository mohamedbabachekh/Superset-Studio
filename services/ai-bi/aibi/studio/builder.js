'use strict';
/* The dashboard builder — a frame around Superset, not a second BI tool.

   The middle of the screen IS Superset: the project's live dashboard, in a frame, and Superset's own Explore
   when a chart is edited. Nothing here draws a chart. Around it stands what is SharpSight's: the asset
   library, the dashboard's structure (sections, and the template's slots), the governance checklist, and
   the gates a build goes through.

   Every change is sent to the service at once. If the specification is valid it is applied to Superset and
   the frame shows the result; if a mandatory slot is still empty it is kept as a draft, and the screen says
   what is missing. What a person changes in Explore is read back by the service — the part that can be
   said in governed terms — or refused, with the reason. */
(() => {
  const { S, h, enc, clone, api, toast, badge, dot, field, select, dialog, impactBox, go } = Studio;
  const TYPES = { big_number: ['#', 'KPI'], line: ['∿', 'Line'], area: ['◭', 'Area'], bar: ['▤', 'Bar'], histogram: ['▥', 'Histogram'],
                  pie: ['◔', 'Pie'], table: ['▦', 'Table'], map: ['◈', 'Map'], telecom_qoe: ['✦', 'Telecom QoE'] };
  const TABS = { metrics: ['metric'], datasets: ['dataset', 'dimension'], charts: ['chart', 'section'], templates: ['chart_template', 'template'] };
  const KIND = { dataset: 'Dataset', dimension: 'Dimension', chart: 'Chart', section: 'Section', metric: 'Metric',
                 chart_template: 'Chart template', template: 'Dashboard template', dashboard: 'Dashboard' };
  const FIELD = { type: 'type', metric: 'metric', dimension: 'dimension', dataset: 'dataset', title: 'title', row_limit: 'rows shown',
                  time_grain: 'time grain', bins: 'bins', sort: 'order', number_format: 'number format' };
  let B = null;
  Studio.unsaved = () => Boolean(B && B.busy);        // nothing is held only in the page: a change is in flight, or it is saved

  const fill = (el, ...kids) => el.replaceChildren(...kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false)
    .map((k) => (k instanceof Node ? k : document.createTextNode(String(k)))));
  const superset = (path) => Studio.supersetUrl(path);

  Studio.views.builder = async (projectId) => {
    if (!projectId) return Studio.views.dashboards();
    const asked = S.token;
    const [doc, cards] = await Promise.all([api('GET', `/builder/projects/${enc(projectId)}`),
                                           api('GET', `/assets?project_id=${enc(projectId)}`)]);
    // Another screen was asked for while this one loaded: it must not take over the builder's state.
    if (asked !== S.token) return h('div');
    const r = {};
    B = { id: projectId, doc, spec: clone(doc.spec), cards, sel: { kind: 'dashboard' }, mode: 'dashboard', tab: 'metrics', scope: 'all',
          q: '', collapsed: new Set(), closed: new Set(), details: {}, lastSlot: null, busy: false, frameUrl: null, explore: null,
          changes: null, r };
    B.frame = h('iframe', { class: 'superset', title: 'Superset' });
    B.el = h('div', { class: 'builder' },
      r.head = h('div', { class: 'bhead' }),
      h('div', { class: 'bbody' },
        r.left = h('div', { class: 'lib' }),
        h('div', { class: 'center' }, r.bar = h('div', { class: 'cbar' }),
          r.frameBox = h('div', { class: 'framebox' }, B.frame, r.empty = h('div', { class: 'frame-empty' })),
          r.alt = h('div', { class: 'canvas stack' })),
        r.right = h('div', { class: 'insp' })),
      r.status = h('div', { class: 'status' }));
    showDashboard();
    draw();
    checkSuperset();
    return B.el;
  };

  // ── the frame: Superset itself ─────────────────────────────────────────────────────────────────────────
  function frameTo(url) { B.frameUrl = url; B.frame.src = url; }
  function showDashboard() {
    B.explore = null;
    if (B.doc.superset) frameTo(superset(`/superset/dashboard/${B.doc.superset.dashboard_id}/?standalone=2`));
  }
  function showExplore(slot) {
    const chart = B.doc.superset && B.doc.superset.charts[slot];
    if (!chart) return toast('This chart is not in Superset yet.', 'bad');
    B.explore = slot; B.mode = 'dashboard';
    frameTo(superset(`/explore/?slice_id=${chart.id}`));
    draw();
  }
  async function checkSuperset() {
    const here = B;
    try {
      const found = await api('GET', `/builder/projects/${enc(B.id)}/superset`);
      if (here !== B) return;
      B.changes = found;
      draw();
    } catch (error) { toast(error.message, 'bad'); }
  }

  // ── the working specification ──────────────────────────────────────────────────────────────────────────
  const comp = (slot) => B.spec.components.find((c) => c.slot === slot);
  const parked = (slot) => B.spec.sections.flatMap((s) => s.parked || []).find((c) => c.slot === slot);
  const slotDef = (slot) => B.doc.template.slots[slot];
  const sectionOf = (slot) => B.doc.canvas.find((s) => s.slots.includes(slot));
  const card = (kind, id) => B.cards.find((c) => c.kind === kind && c.id === id);
  const available = (kind) => ((S.config.visualizations || {})[kind] || {}).status === 'available';
  const needs = (kind) => ((S.config.visualizations || {})[kind] || {}).needs || {};
  function own(spec, id) {
    let section = spec.sections.find((s) => s.id === id);
    if (!section) { section = { id, visibility: 'visible', filters: [], parked: [] }; spec.sections.push(section); }
    section.filters = section.filters || []; section.parked = section.parked || [];
    return section;
  }
  function put(spec, slot, body) {
    spec.components = spec.components.filter((c) => c.slot !== slot);
    spec.components.push({ options: {}, ...body, slot });
    const order = B.doc.template.layout.flatMap((row) => row.slots.map((p) => p.id));
    spec.components.sort((a, b) => order.indexOf(a.slot) - order.indexOf(b.slot));
  }
  function defaultDimension(type, metricCard) {
    if (!needs(type).dimension) return null;                 // a KPI has none; a line and a map find theirs by role
    return metricCard.facts.dimensions.find((d) => d !== 'date') || metricCard.facts.dimensions[0] || null;
  }
  async function reload() {
    const [doc, cards] = await Promise.all([api('GET', `/builder/projects/${enc(B.id)}`), api('GET', `/assets?project_id=${enc(B.id)}`)]);
    Object.assign(B.doc, doc); B.spec = clone(doc.spec); B.cards = cards;
  }
  /* Send the working specification. Valid: it is applied to Superset and the frame shows it. Not yet valid:
     it is kept as a draft, and the bar above the frame says what is missing. */
  async function send() {
    const here = B, had = Boolean(B.doc.superset);
    B.busy = true; draw();
    try {
      const doc = await api('POST', `/builder/projects/${enc(B.id)}/apply`, { spec: B.spec });
      if (here !== B) return;
      Object.assign(B.doc, doc); B.spec = clone(doc.spec);
      B.cards = await api('GET', `/assets?project_id=${enc(B.id)}`);
      if (here !== B) return;
      if (doc.applied) { if (B.explore && doc.superset.charts[B.explore]) showExplore(B.explore); else showDashboard(); }
      if (doc.applied && !had) toast('It is in Superset now: what you see in the middle is Superset’s own dashboard.', 'ok');
    } catch (error) {
      toast(error.message, 'bad');
      if (here === B) await reload().catch(() => {});
    }
    if (here !== B) return;
    B.busy = false;
    draw();
    checkSuperset();
  }
  async function change(fn) {
    if (!B.doc.editable) { toast(B.doc.locked_because || 'This project cannot be changed.', 'bad'); return; }
    if (B.busy) { toast('Superset is being brought up to date — one change at a time.'); return; }
    fn(B.spec);
    await send();
  }
  async function act(method, path, body, done) {
    if (B.busy) return;
    const here = B;
    B.busy = true; draw();
    try {
      const doc = await api(method, `/builder/projects/${enc(B.id)}/${path}`, body);
      if (here !== B) return;
      Object.assign(B.doc, doc); B.spec = clone(doc.spec);
      B.cards = await api('GET', `/assets?project_id=${enc(B.id)}`);
      if (here !== B) return;
      showDashboard();
      toast(done(doc), 'ok');
    } catch (error) { toast(error.message, 'bad'); }
    if (here !== B) return;
    B.busy = false;
    draw();
    checkSuperset();
  }

  // ── dropping an asset ──────────────────────────────────────────────────────────────────────────────────
  function dropOnSlot(slot, asset) {
    const def = slotDef(slot), existing = comp(slot), found = card(asset.kind, asset.id);
    if (!found) return;
    B.sel = { kind: 'slot', id: slot }; B.lastSlot = slot;
    if (asset.kind === 'metric') {
      const type = existing ? existing.type : def.accepts.find(available);
      const keep = existing && existing.dimension && found.facts.dimensions.includes(existing.dimension);
      return change((spec) => put(spec, slot, { ...(existing || {}), type, metric: asset.id, asset: null, title: null, dataset: null,
                                                dimension: keep ? existing.dimension : defaultDimension(type, found) }));
    }
    if (asset.kind === 'chart') {
      const facts = found.facts;
      if (!def.accepts.includes(facts.type)) return toast(`Slot ${slot} accepts ${def.accepts.join(', ')} — not a ${facts.type}.`, 'bad');
      if (found.scope === 'shared') return change((spec) => put(spec, slot, { type: facts.type, metric: facts.metric, asset: found.id }));
      return change((spec) => put(spec, slot, { ...clone(comp(facts.slot) || {}), asset: null }));     // a copy of a local chart
    }
    if (asset.kind === 'dataset') {
      if (!existing) return toast('Drop a metric or a chart here first: a dataset serves a chart.', 'bad');
      return change((spec) => { const c = spec.components.find((x) => x.slot === slot); c.dataset = asset.id; c.asset = null; });
    }
    if (asset.kind === 'dimension') {
      if (!existing) return toast('Drop a dimension on a chart, on a section, or on the filter bar.', 'bad');
      return change((spec) => { const c = spec.components.find((x) => x.slot === slot); c.dimension = asset.id; c.asset = null; c.title = null; });
    }
    if (asset.kind === 'chart_template') {
      if (found.status !== 'certified') return toast(`« ${found.name} » cannot be drawn yet: ${found.facts.reason}`, 'bad');
      const kinds = [found.facts.visualization, ...found.facts.alternatives].filter((k) => def.accepts.includes(k));
      if (!kinds.length) return toast(`Slot ${slot} accepts ${def.accepts.join(', ')} — « ${found.name} » is drawn as ${found.facts.visualization}.`, 'bad');
      B.sel = { kind: 'pending', slot, template: found.id, type: kinds[0], dimension: (existing || {}).dimension || '',
                metric: (existing || B.spec.components[0] || {}).metric || '' };
      return draw();
    }
    if (asset.kind === 'section') return fillSection(sectionOf(slot).id, asset.id);
    toast(`A ${KIND[asset.kind] || asset.kind} is not something a slot takes.`, 'bad');
  }
  async function fillSection(sectionId, assetId) {
    if (!B.doc.editable) return toast(B.doc.locked_because, 'bad');
    if (B.busy) return;
    try {
      const found = await api('POST', '/builder/fill-section', { spec: B.spec, section: sectionId, asset: assetId });
      B.spec = clone(found.spec); B.sel = { kind: 'section', id: sectionId };
      toast(found.notes.join(' · '), found.notes.some((n) => n.includes('found no slot')) ? 'bad' : 'ok');
      await send();
    } catch (error) { toast(error.message, 'bad'); }
  }
  function dragSource(found) {
    return { draggable: B.doc.editable ? 'true' : null,
             ondragstart: (e) => { e.dataTransfer.setData('text/plain', JSON.stringify({ kind: found.kind, id: found.id })); e.dataTransfer.effectAllowed = 'copy'; } };
  }
  function dropTarget(onDrop) {
    return { ondragover: (e) => { e.preventDefault(); e.currentTarget.classList.add('over'); },
             ondragleave: (e) => e.currentTarget.classList.remove('over'),
             ondrop: (e) => { e.preventDefault(); e.stopPropagation(); e.currentTarget.classList.remove('over');
                              try { onDrop(JSON.parse(e.dataTransfer.getData('text/plain'))); } catch (_) { /* not one of ours */ } } };
  }

  // ── building ───────────────────────────────────────────────────────────────────────────────────────────
  async function build() {
    if (B.busy) return;
    let started;
    try { started = await api('POST', `/builder/projects/${enc(B.id)}/build`); } catch (error) { return toast(error.message, 'bad'); }
    const body = h('div', { class: 'stack' });
    const close = dialog('Building: validation, the picture, a version, a package', body, []);
    const here = B;                       // the builder this build belongs to: it reports there, or nowhere
    const follow = async () => {
      const run = await api('GET', `/runs/${started.run_id}`);
      fill(body, Studio.runSteps(run));
      if (run.status === 'running') return setTimeout(follow, 2000);
      if (here !== B) return;
      await reload();
      if (here !== B) return;
      showDashboard(); draw(); checkSuperset();
      if (run.status === 'succeeded') toast(`Version ${run.version} is READY_FOR_REVIEW.`, 'ok');
    };
    follow().catch((error) => { close(); toast(error.message, 'bad'); });
  }

  // ── drawing: each region on its own, so that the frame is never torn down ──────────────────────────────
  function draw() {
    const keep = {};
    B.el.querySelectorAll('[data-scroll]').forEach((el) => { keep[el.dataset.scroll] = el.scrollTop; });
    fill(B.r.head, head());
    fill(B.r.left, left());
    fill(B.r.bar, bar());
    const framed = B.mode === 'dashboard';
    B.r.left.style.display = B.r.right.style.display = B.wide && framed ? 'none' : '';
    B.r.frameBox.style.display = framed ? '' : 'none';
    B.r.alt.style.display = framed ? 'none' : '';
    B.frame.style.display = B.doc.superset ? '' : 'none';
    B.r.empty.style.display = B.doc.superset ? 'none' : '';
    if (!B.doc.superset) fill(B.r.empty, notYet());
    if (!framed) fill(B.r.alt, B.mode === 'data' ? dataMode() : governanceMode());
    fill(B.r.right, inspector());
    fill(B.r.status, statusBar());
    B.el.querySelectorAll('[data-scroll]').forEach((el) => { if (keep[el.dataset.scroll]) el.scrollTop = keep[el.dataset.scroll]; });
  }
  function seg(current, options, onpick) {
    return h('div', { class: 'seg' }, options.map(([value, label]) => h('button', { class: value === current ? 'on' : '', text: label, onclick: () => onpick(value) })));
  }
  function head() {
    const doc = B.doc;
    return [h('a', { text: '← Dashboards', onclick: () => go('dashboards') }),
      h('div', { class: 'grow ellipsis' }, h('span', { style: 'font-weight:700;font-size:15px', text: B.spec.dashboard.title }),
        h('span', { class: 'muted', text: `   ${B.id}${doc.version ? ' · v' + doc.version : ''} · ${doc.environment}` })),
      B.busy ? h('span', { class: 'badge info', text: 'updating Superset…' }) : null,
      badge(doc.status),
      doc.editable ? null : h('span', { class: 'badge warn', title: doc.locked_because || '', text: 'read-only' }),
      seg(B.mode, [['dashboard', 'Dashboard'], ['data', 'Data'], ['governance', 'Governance']], (m) => { B.mode = m; draw(); }),
      doc.dashboard_url ? h('a', { href: superset(new URL(doc.dashboard_url).pathname), target: '_blank', rel: 'noopener', text: 'Open in Superset ↗' }) : null];
  }
  function notYet() {
    const failed = (B.doc.checks || []).filter((c) => c.status === 'fail');
    return h('div', { class: 'stack', style: 'max-width:560px' },
      h('h2', { text: 'Nothing in Superset yet' }),
      h('div', { class: 'muted', text: 'This panel is Superset: the dashboard appears here as soon as the specification is valid. On the left, pick a slot and drop a metric on it.' }),
      failed.length ? h('div', { class: 'note warn stack' }, h('b', { text: 'Still missing' }), failed.slice(0, 6).map((c) => h('div', { class: 'small', text: `${c.name}${c.detail ? ' — ' + c.detail : ''}` }))) : null);
  }

  /* Above the frame: the dashboard's filters; what SharpSight has to say about what Superset shows. */
  function bar() {
    const doc = B.doc, found = B.changes, rows = [];
    rows.push(h('div', { class: 'filters', ...dropTarget((asset) => {
      if (asset.kind !== 'dimension') return toast('Only a dimension becomes a filter.', 'bad');
      change((spec) => { if (!spec.filters.includes(asset.id)) spec.filters.push(asset.id); }); }) },
      h('span', { class: 'muted small', text: 'FILTERS' }),
      B.spec.filters.map((column) => h('span', { class: 'chip' }, (card('dimension', column) || { name: column }).name,
        h('button', { title: 'Remove', text: '×', onclick: () => change((spec) => { spec.filters = spec.filters.filter((f) => f !== column); }) }))),
      B.spec.filters.length ? null : h('span', { class: 'muted small', text: 'none — drop a dimension here' }),
      h('span', { class: 'grow' }),
      doc.superset ? [B.explore ? h('button', { class: 'btn', text: '← Back to the dashboard', onclick: () => { showDashboard(); draw(); checkSuperset(); } }) : null,
        h('span', { class: 'small muted', text: B.explore ? `Superset · Explore · ${(doc.titles || {})[B.explore] || B.explore}` : 'Superset · the live dashboard' }),
        h('button', { class: 'btn ghost', title: 'Reload the frame, and ask Superset what was changed there', text: '⟳', onclick: () => { frameTo(B.frameUrl); checkSuperset(); } }),
        h('button', { class: 'btn ghost', title: B.wide ? 'Show the library and the inspector again' : 'Give Superset the whole width', text: B.wide ? '⇥⇤' : '⇤⇥',
          onclick: () => { B.wide = !B.wide; draw(); } })] : null));
    if (!doc.editable) rows.push(h('div', { class: 'note warn small', text: `Read-only: ${doc.locked_because}` }));
    if (doc.draft && doc.superset) {
      const failed = (doc.checks || []).filter((c) => c.status === 'fail');
      rows.push(h('div', { class: 'note warn small' }, h('b', { text: 'Not in Superset yet. ' }),
        `What you composed is kept, and Superset still shows what was valid: ${failed.slice(0, 3).map((c) => `${c.name}${c.detail ? ' (' + c.detail + ')' : ''}`).join('; ')}`));
    }
    if (found && found.charts.length) {
      const taken = found.charts.filter((c) => Object.keys(c.changed).length && !c.refused.length);
      rows.push(h('div', { class: 'note stack small', 'data-edited': 'yes' },
        h('b', { text: 'Edited in Superset — not yet in the specification' }),
        found.charts.map((c) => h('div', null, h('b', { text: `${c.title || c.slot} ` }), h('span', { class: 'muted', text: `(${c.slot})  ` }),
          Object.entries(c.changed).map(([key, v]) => `${FIELD[key] || key}: ${v.specification ?? 'default'} → ${v.superset ?? 'default'}`).join(' · '),
          c.refused.length ? h('span', { style: 'color:var(--error)', text: ` cannot be taken — ${c.refused.join('; ')}` }) : null,
          c.not_carried.length ? h('span', { class: 'muted', text: `  · a specification has no place for: ${c.not_carried.join(', ')} (set back at the next build)` }) : null)),
        doc.editable ? h('div', { class: 'row' },
          h('button', { class: 'btn primary', text: 'Take into the specification', disabled: !taken.length || B.busy,
            onclick: () => act('POST', 'adopt', undefined, (d) => `Taken: ${d.taken.join(', ')}.${Object.keys(d.refused).length ? ' Left as specified: ' + Object.keys(d.refused).join(', ') + '.' : ''}`) }),
          h('button', { class: 'btn', text: 'Put back as specified', disabled: B.busy, onclick: () => act('POST', 'put-back', { to: 'specification' }, () => 'Superset holds the specification again.') })) : null));
    }
    if (found && found.version && found.version.differs && doc.editable) {
      rows.push(h('div', { class: 'note warn small row', 'data-version-differs': 'yes' },
        h('span', { class: 'grow', text: `Superset no longer shows version ${found.version.version} (${found.version.status}): a reviewer would be refused. Build to validate what is there now.` }),
        h('button', { class: 'btn', text: `Put ${found.version.version} back`, disabled: B.busy,
          onclick: () => act('POST', 'put-back', { to: 'version' }, () => `Superset shows version ${found.version.version} again.`) })));
    }
    if (doc.superset) rows.push(h('div', { class: 'small muted', text: 'This panel is Superset itself. If it asks you to sign in, do it there, with a Superset account that may see this dashboard — a draft is seen by an administrator (in this kit: admin).' }));
    return rows;
  }

  // ── the left panel: the dashboard's structure, and the asset library ───────────────────────────────────
  function left() {
    const list = h('div', { class: 'scroll', 'data-scroll': 'lib' });
    const refill = () => fill(list, assetList());
    refill();
    return [h('div', { class: 'structure', 'data-scroll': 'structure' }, h('div', { class: 'ptitle', text: 'DASHBOARD' }), structure()),
      h('div', { class: 'head stack' }, h('div', { class: 'ptitle', text: 'DATA & ASSETS' }),
        h('input', { class: 'input', placeholder: 'Search assets…', value: B.q, oninput: (e) => { B.q = e.target.value; refill(); } }),
        h('div', { class: 'tabs' }, Object.keys(TABS).map((key) => h('button', { class: `tab ${B.tab === key ? 'on' : ''}`,
          text: key[0].toUpperCase() + key.slice(1), onclick: () => { B.tab = key; draw(); } }))),
        ['datasets', 'charts'].includes(B.tab) ? seg(B.scope, [['all', 'All'], ['shared', 'Shared'], ['local', 'Local']], (v) => { B.scope = v; draw(); }) : null),
      list];
  }
  /* Sections, and in each the template's slots — the filled ones and the empty ones. This is where a slot is
     picked and where an asset is dropped: the frame beside it is Superset's, and takes neither. */
  function structure() {
    const titles = B.doc.titles || {}, problems = B.doc.problems || {};
    return h('div', { class: 'tree' },
      h('div', { class: `node ${B.sel.kind === 'dashboard' ? 'on' : ''}`, 'data-dashboard': 'yes', onclick: () => { B.sel = { kind: 'dashboard' }; draw(); } },
        h('b', { class: 'grow ellipsis', text: B.spec.dashboard.title })),
      h('div', { class: 'kids' }, B.doc.canvas.map((section) => {
        const collapsed = B.collapsed.has(section.id), hidden = section.visibility === 'hidden';
        return [h('div', { class: `node osection ${hidden ? 'hidden' : ''} ${B.sel.kind === 'section' && B.sel.id === section.id ? 'on' : ''}`, 'data-section': section.id,
          onclick: () => { B.sel = { kind: 'section', id: section.id }; draw(); },
          ...dropTarget((asset) => {
            if (asset.kind === 'section') return fillSection(section.id, asset.id);
            if (asset.kind === 'dimension') return change((spec) => { const s = own(spec, section.id); if (!s.filters.includes(asset.id)) s.filters.push(asset.id); });
            toast('Drop a metric or a chart on a slot; a shared section or a dimension on a section.', 'bad'); }) },
          h('span', { title: collapsed ? 'Expand' : 'Collapse', text: collapsed ? '▸' : '▾',
            onclick: (e) => { e.stopPropagation(); collapsed ? B.collapsed.delete(section.id) : B.collapsed.add(section.id); draw(); } }),
          h('span', { class: 'grow ellipsis title', style: 'font-weight:600', text: section.title || section.id }),
          section.filters.map((f) => h('span', { class: 'badge info', text: `filter: ${f}` })),
          section.asset ? h('span', { class: 'badge accent', text: `shared: ${section.asset}` }) : null,
          hidden ? h('span', { class: 'badge warn', text: 'hidden' }) : null),
        collapsed ? null : h('div', { class: 'kids' }, section.rows.flatMap((row) => row.slots).map((slot) => {
          const c = slot.component, on = B.sel.kind === 'slot' && B.sel.id === slot.slot;
          const pick = (e) => { e.stopPropagation(); B.sel = { kind: 'slot', id: slot.slot }; B.lastSlot = slot.slot; draw(); };
          if (!c) return h('div', { class: `node oslot empty ${on ? 'on' : ''}`, 'data-slot': slot.slot, onclick: pick, ...dropTarget((asset) => dropOnSlot(slot.slot, asset)) },
            '+', h('span', { class: 'grow ellipsis small', text: `${slot.purpose} · ${slot.accepts.join(' / ')}` }), slot.mandatory ? h('span', { class: 'badge warn', text: 'mandatory' }) : null);
          return h('div', { class: `node oslot ${on ? 'on' : ''}`, 'data-slot': slot.slot, onclick: pick, ondblclick: () => showExplore(slot.slot),
            ...dropTarget((asset) => dropOnSlot(slot.slot, asset)) },
            h('span', { class: 'muted', title: c.type, text: (TYPES[c.type] || ['·'])[0] }),
            h('span', { class: 'grow ellipsis stitle', style: slot.parked ? 'opacity:.55' : '', text: titles[slot.slot] || c.title || slot.slot }),
            problems[slot.slot] ? h('span', { class: 'badge bad', text: `${problems[slot.slot]} ✗` }) : null,
            B.changes && B.changes.charts.some((x) => x.slot === slot.slot) ? h('span', { class: 'badge info', title: 'Edited in Superset', text: 'edited' }) : null,
            c.asset ? h('span', { class: 'badge accent', text: 'shared' }) : null, slot.parked ? h('span', { class: 'badge warn', text: 'parked' }) : null);
        }))];
      })));
  }
  function assetList() {
    const q = B.q.trim().toLowerCase();
    const shown = B.cards.filter((c) => TABS[B.tab].includes(c.kind)
      && (B.scope === 'all' || !['datasets', 'charts'].includes(B.tab) || c.scope === B.scope)
      && (!q || `${c.name} ${c.id} ${c.description || ''}`.toLowerCase().includes(q)));
    const groups = new Map();
    const favorites = shown.filter((c) => c.favorite);
    if (favorites.length) groups.set('★ Favorites', favorites);
    for (const found of shown) {
      const name = found.kind === 'dimension' ? `Dimensions · ${found.domain_name}` : found.scope === 'local' && found.kind === 'chart'
        ? 'Local to this dashboard' : found.domain_name;
      if (!groups.has(name)) groups.set(name, []);
      groups.get(name).push(found);
    }
    if (!shown.length) return [h('div', { class: 'muted', style: 'padding:10px 4px', text: 'No asset matches.' })];
    return [...groups.entries()].map(([name, items]) => h('div', { class: 'folder' },
      h('div', { class: 'fname', onclick: () => { B.closed.has(name) ? B.closed.delete(name) : B.closed.add(name); draw(); } },
        B.closed.has(name) ? '▸' : '▾', h('span', { class: 'grow', text: name }), h('span', { class: 'muted small', text: items.length })),
      B.closed.has(name) ? null : items.map(assetRow)));
  }
  function assetRow(found) {
    const usable = found.status !== 'not_available';
    const star = h('span', { class: `star ${found.favorite ? 'on' : ''}`, title: 'Favorite', text: '★', onclick: async (e) => {
      e.stopPropagation();
      try { await api(found.favorite ? 'DELETE' : 'PUT', `/assets/${found.kind}/${enc(found.id)}/favorite`); found.favorite = !found.favorite; draw(); }
      catch (error) { toast(error.message, 'bad'); } } });
    return h('div', { class: `asset ${B.sel.kind === 'asset' && B.sel.card === found ? 'on' : ''} ${usable ? '' : 'off'}`, 'data-asset': `${found.kind}:${found.id}`,
      title: found.description || found.id, onclick: () => { B.sel = { kind: 'asset', card: found }; draw(); }, ...(usable ? dragSource(found) : {}) },
      dot(found.status), h('span', { class: 'grow ellipsis', text: found.name }),
      found.kind === 'chart' && found.facts.type ? h('span', { class: 'muted', title: found.facts.type, text: (TYPES[found.facts.type] || ['·'])[0] }) : null,
      found.used_by.dashboards ? h('span', { class: 'muted small', title: `used by ${found.used_by.dashboards} dashboard(s), ${found.used_by.charts} chart(s)`, text: found.used_by.dashboards }) : null,
      found.scope === 'local' ? null : star);
  }

  // ── data mode ──────────────────────────────────────────────────────────────────────────────────────────
  async function detail(kind, id) {
    const key = `${kind}:${id}`;
    if (!B.details[key]) B.details[key] = await api('GET', `/assets/${kind}/${enc(id)}`);
    return B.details[key];
  }
  function dataMode() {
    const out = [h('h2', { text: 'Datasets of this dashboard' })];
    const datasets = [...new Set(B.spec.components.map((c) => c.dataset).filter(Boolean))];
    const metrics = [...new Set(B.spec.components.map((c) => c.metric))];
    if (!datasets.length) out.push(h('div', { class: 'muted', text: 'No chart yet.' }));
    for (const id of datasets) {
      const holder = h('div', { class: 'card stack' }, h('div', { class: 'muted', text: `${id} …` }));
      out.push(holder);
      detail('dataset', id).then((d) => {
        const sample = h('div');
        fill(holder,
          h('div', { class: 'row wrap' }, h('b', { text: d.name }), badge(d.status), h('span', { class: 'mono muted', text: d.definition.relation }),
            h('span', { class: 'grow' }), h('span', { class: 'small muted', text: `Refresh: ${d.facts.refresh} · Rows: ${Studio.fmt(d.facts.rows)} · used by ${d.used_by.dashboards} dashboard(s)` })),
          d.definition.derived_from && d.definition.derived_from.length ? h('div', { class: 'small', text: `Relationships: derived from ${d.definition.derived_from.join(', ')} — grain ${d.definition.grain.join(', ')}` })
            : h('div', { class: 'small muted', text: `Grain: ${(d.definition.grain || []).join(', ')}` }),
          Studio.table([{ title: 'COLUMN', cell: (x) => x[0] }, { title: 'LABEL', cell: (x) => x[1].label }, { title: 'TYPE', cell: (x) => x[1].type },
                        { title: 'ROLE', cell: (x) => x[1].role || '—' }, { title: 'CLASSIFICATION', cell: (x) => badge(x[1].readable ? 'pass' : 'fail', x[1].classification) }],
            Object.entries(d.definition.columns)),
          h('div', { class: 'row' }, h('button', { class: 'btn', text: 'Preview rows', onclick: async () => {
            try {
              const rows = await api('POST', '/tools/get_dataset_sample', { arguments: { dataset_id: id, limit: 8 } });
              fill(sample, Studio.table(rows.columns.map((name, i) => ({ title: name.toUpperCase(), cell: (x) => String(x[i] ?? '') })), rows.rows));
            } catch (error) { toast(error.message, 'bad'); } } }),
            h('a', { text: 'Lineage and impact →', onclick: () => go('asset', 'dataset', id) })), sample);
      }).catch((error) => fill(holder, h('div', { class: 'note bad', text: error.message })));
    }
    out.push(h('h2', { text: 'Metrics', style: 'margin-top:8px' }));
    for (const id of metrics) {
      const holder = h('div', { class: 'card stack' }, h('div', { class: 'muted', text: `${id} …` }));
      out.push(holder);
      detail('metric', id).then((m) => fill(holder,
        h('div', { class: 'row wrap' }, h('b', { text: m.name }), badge(m.status), h('span', { class: 'badge', text: m.facts.classification }),
          h('span', { class: 'grow' }), h('span', { class: 'small muted', text: `${m.facts.aggregation} · ${m.facts.format} ${m.facts.unit} · by ${m.facts.dimensions.join(', ')}` })),
        h('pre', { class: 'code mono', text: m.definition.expression }), h('div', { class: 'small muted', text: m.description })))
        .catch((error) => fill(holder, h('div', { class: 'note bad', text: error.message })));
    }
    return out;
  }

  // ── governance mode ────────────────────────────────────────────────────────────────────────────────────
  function governanceMode() {
    const mark = { pass: ['✓', 'var(--success)'], warn: ['⚠', '#b27d00'], fail: ['✗', 'var(--error)'] };
    const failed = B.doc.checks.filter((c) => c.status === 'fail'), warned = B.doc.checks.filter((c) => c.status === 'warn');
    return [h('h2', { text: 'Governance' }),
      h('div', { class: 'card' }, B.doc.governance.map((item) => h('div', { class: 'check' },
        h('b', { style: `color:${mark[item.status][1]};width:16px`, text: mark[item.status][0] }),
        h('div', { class: 'grow' }, h('div', { style: 'font-weight:600', text: item.check }), h('div', { class: 'small muted', text: item.detail }))))),
      h('h2', { text: 'Who it is for' }),
      h('div', { class: 'card stack' },
        h('div', { class: 'row wrap' }, B.doc.audience.length ? B.doc.audience.map((role) => h('span', { class: 'badge accent', text: role }))
          : h('span', { class: 'muted', text: 'No role yet: it needs a chart on a dataset.' })),
        (B.doc.row_rules || []).map((rule) => h('div', { class: 'small' }, h('b', { text: rule.role }), ` sees of ${rule.dataset} only: `, h('span', { class: 'mono', text: rule.clause }))),
        h('div', { class: 'small muted', text: 'Roles granted every dataset of the dashboard and cleared for every metric on it. Set in Superset when it is applied; asked of Superset when it is published.' })),
      h('h2', { text: `The specification: ${B.doc.validation.checks} checks, ${failed.length} failed, ${warned.length} warning(s)` }),
      h('div', { class: 'card' }, [...failed, ...warned].length ? [...failed, ...warned].map((c) => h('div', { class: 'check' },
        h('b', { style: `color:${mark[c.status][1]};width:16px`, text: mark[c.status][0] }),
        h('div', { class: 'grow' }, h('div', { style: 'font-weight:600', text: `${c.name}${c.subject ? ' · ' + c.subject : ''}` }), h('div', { class: 'small muted', text: c.detail }))))
        : h('div', { class: 'muted', text: 'Every check of the specification passes. Data, Superset, security, performance and the picture are checked when it is built.' }))];
  }

  // ── the inspector: what is selected, and only that ─────────────────────────────────────────────────────
  function inspector() {
    const body = h('div', { class: 'scroll', 'data-scroll': 'insp' });
    const kind = B.sel.kind;
    fill(body, kind === 'slot' && !comp(B.sel.id) && !parked(B.sel.id) ? emptySlot(B.sel.id) : kind === 'slot' ? chartPanel(B.sel.id)
      : kind === 'section' ? sectionPanel(B.sel.id) : kind === 'asset' ? assetPanel(B.sel.card) : kind === 'pending' ? pendingPanel() : dashboardPanel());
    return body;
  }
  const group = (title, ...kids) => h('div', { class: 'group stack' }, h('h4', { text: title }), ...kids);
  const text = (value, onchange, attrs) => h('input', { class: 'input', value: value ?? '', disabled: !B.doc.editable, onchange: (e) => onchange(e.target.value), ...(attrs || {}) });
  const area = (value, onchange) => h('textarea', { class: 'input', disabled: !B.doc.editable, onchange: (e) => onchange(e.target.value) }, value ?? '');
  const dimensionOptions = (extra) => [['', '—'], ...[...new Set([...(extra || []), ...B.cards.filter((c) => c.kind === 'dimension').map((c) => c.id)])].map((id) => [id, (card('dimension', id) || { name: id }).name])];

  function dashboardPanel() {
    const d = B.spec.dashboard, tokens = S.config.tokens;
    const edit = (key) => (value) => change((spec) => { spec.dashboard[key] = value || (key === 'title' ? spec.dashboard.title : null); });
    const add = select('', dimensionOptions().filter(([id]) => !B.spec.filters.includes(id)), (v) => v && change((spec) => spec.filters.push(v)), { disabled: !B.doc.editable });
    return [h('div', { class: 'kind', text: 'DASHBOARD' }), h('h3', { text: d.title }),
      group('METADATA', field('Title', text(d.title, edit('title'))), field('Description', area(d.description, (v) => change((spec) => { spec.dashboard.description = v; }))),
        field('Owner', text(d.owner, edit('owner'), { placeholder: 'a person or a team' })),
        Studio.kv([['Id', h('span', { class: 'mono', text: d.id })], ['Version', d.version], ['Status', B.doc.status]])),
      group('LAYOUT', field('Template', select(B.doc.template.template.id, B.cards.filter((c) => c.kind === 'template').map((c) => [c.id, `${c.name} (v${c.facts.version})`]), changeTemplate, { disabled: !B.doc.editable })),
        h('div', { class: 'small muted', text: `${B.doc.template.template.description} — ${Object.keys(B.doc.template.slots).length} slots in ${B.doc.canvas.length} sections. A template fixes where things go; the dashboard fills its slots.` })),
      group('THEME', field('Colour scheme', select(B.spec.theme.color_scheme || '', [['', `template’s (${B.doc.template.theme.color_scheme})`], ...tokens.color_schemes.map((s) => [s, s])],
        (v) => change((spec) => { spec.theme.color_scheme = v || null; }), { disabled: !B.doc.editable })),
        h('div', { class: 'small muted', text: 'Approved schemes only (registry/design/tokens.yaml). No free CSS.' })),
      group('GLOBAL FILTERS', h('div', { class: 'row wrap' }, B.spec.filters.map((f) => h('span', { class: 'chip' }, f, h('button', { text: '×', onclick: () => change((spec) => { spec.filters = spec.filters.filter((x) => x !== f); }) })))),
        field(`Add a filter (at most ${B.doc.template.filters.max} in all)`, add)),
      group('PERMISSIONS', h('div', { class: 'row wrap' }, B.doc.audience.map((x) => h('span', { class: 'badge accent', text: x }))),
        h('div', { class: 'small muted', text: `${(B.doc.row_rules || []).length} row-level rule(s). Derived from SharpSight’s RBAC, never chosen here — see Governance.` })),
      group('RESPONSIVE BEHAVIOR', h('div', { class: 'small', text: B.doc.template.responsive.viewports.map((v) => `${v.name} ${v.width}px`).join(' · ') }),
        h('div', { class: 'small muted', text: B.doc.template.responsive.collapse_empty_slots ? 'An empty slot gives its columns to its neighbours.' : 'Empty slots keep their place.' }))];
  }
  async function changeTemplate(id) {
    if (id === B.doc.template.template.id || !B.doc.editable || B.busy) return;
    try {
      const probe = clone(B.spec); probe.dashboard.template = id; probe.sections = [];
      const found = await api('POST', '/builder/inspect', { spec: probe, project_id: B.id });
      const slots = found.template.slots;                     // what has no slot in the new template cannot stay; every section is named
      await change((spec) => {
        spec.dashboard.template = id;
        spec.components = spec.components.filter((c) => slots[c.slot] && slots[c.slot].accepts.includes(c.type));
        spec.sections = found.canvas.map((s) => ({ id: s.id, visibility: 'visible', filters: [], parked: [] }));
      });
      toast('Template changed: charts kept where the new template has a slot that accepts them.', 'ok');
    } catch (error) { toast(error.message, 'bad'); }
  }

  function sectionPanel(id) {
    const section = B.doc.canvas.find((s) => s.id === id);
    const set = (key) => (value) => change((spec) => { own(spec, id)[key] = value || null; });
    const charts = section.slots.map(comp).filter(Boolean);
    const allShared = charts.length > 0 && charts.every((c) => c.asset);
    const toggle = (visibility) => change((spec) => {
      const s = own(spec, id);
      if (visibility === 'hidden') { s.parked = spec.components.filter((c) => section.slots.includes(c.slot)); spec.components = spec.components.filter((c) => !section.slots.includes(c.slot)); }
      else { spec.components.push(...s.parked); s.parked = []; }
      s.visibility = visibility;
    });
    return [h('div', { class: 'kind', text: 'SECTION' }), h('h3', { text: section.title || section.id }),
      group('SECTION', field('Title — a header on the Superset dashboard', text(section.title, set('title'))), field('Description', area(section.description, set('description'))),
        field('Owner', text(section.owner, set('owner'), { placeholder: 'a person or a team' }))),
      group('VISIBILITY', select(section.visibility, [['visible', 'Visible on the dashboard'], ['hidden', 'Hidden — kept in the specification, not in Superset']], toggle, { disabled: !B.doc.editable })),
      group('FILTERS', h('div', { class: 'row wrap' }, section.filters.map((f) => h('span', { class: 'chip' }, f, h('button', { text: '×', onclick: () => change((spec) => { const s = own(spec, id); s.filters = s.filters.filter((x) => x !== f); }) })))),
        field('A filter that reaches this section only', select('', dimensionOptions().filter(([f]) => !section.filters.includes(f)), (v) => v && change((spec) => own(spec, id).filters.push(v)), { disabled: !B.doc.editable }))),
      group('LAYOUT', h('div', { class: 'small', text: section.rows.map((row) => `${row.row}: ${row.slots.map((s) => s.width).join(' + ')} of 12`).join(' · ') }),
        h('div', { class: 'small muted', text: `${section.slots.length} slots, ${charts.length} filled. The rows are the template’s.` })),
      group('SHARED', section.asset ? [h('div', { class: 'small', text: `Filled from shared section « ${section.asset} ».` }),
          h('button', { class: 'btn', text: 'Detach from the shared section', disabled: !B.doc.editable, onclick: () => change((spec) => { own(spec, id).asset = null; }) })]
        : [h('div', { class: 'small muted', text: allShared ? 'Every chart here is shared: the section can be shared too.' : 'A section is shared when all its charts are shared charts.' }),
           h('button', { class: 'btn', text: 'Save as a shared section…', disabled: !allShared || !Studio.can('assets.write') || !B.doc.editable, onclick: () => shareSection(id, section, charts) })]),
      group('PERMISSIONS', h('div', { class: 'small muted', text: 'A section has the dashboard’s audience. Superset grants a dashboard, not a part of one — a narrower audience for one section could not be enforced, so it is not offered.' }))];
  }
  function shareSection(id, section, charts) {
    const name = h('input', { class: 'input', value: section.title || id });
    dialog('Save as a shared section', h('div', { class: 'stack' }, field('Name', name),
      h('div', { class: 'small muted', text: `Its charts, in order: ${charts.map((c) => c.asset).join(', ')}` })), [['Save', 'primary', async (close) => {
        try {
          const saved = await api('POST', '/assets/section', { asset: { name: name.value, description: section.description || '', charts: charts.map((c) => c.asset), filters: section.filters } });
          close(); await change((spec) => { own(spec, id).asset = saved.id; });
          toast(`Shared section « ${saved.name} » is in the library.`, 'ok');
        } catch (error) { toast(error.message, 'bad'); } }]]);
  }

  function emptySlot(slot) {
    const def = slotDef(slot);
    const metrics = B.cards.filter((c) => c.kind === 'metric');
    return [h('div', { class: 'kind', text: 'EMPTY SLOT' }), h('h3', { text: slot }),
      h('div', { style: 'margin-top:8px' }, Studio.kv([['For', def.purpose], ['Accepts', def.accepts.join(', ')], ['Mandatory', def.mandatory ? 'yes' : 'no'], ['Section', sectionOf(slot).title]])),
      group('ADD A CHART', h('div', { class: 'small muted', text: 'Drop a metric, a shared chart or a chart template on the slot — or pick a metric:' }),
        select('', [['', '—'], ...metrics.map((m) => [m.id, m.name])], (v) => v && dropOnSlot(slot, { kind: 'metric', id: v }), { disabled: !B.doc.editable }))];
  }
  function pendingPanel() {
    const p = B.sel, template = card('chart_template', p.template), def = slotDef(p.slot);
    const kinds = [template.facts.visualization, ...template.facts.alternatives].filter((k) => def.accepts.includes(k));
    const metric = card('metric', p.metric);
    const create = async (share) => {
      try {
        const made = await api('POST', '/chart-templates/create', { template: p.template, metric: p.metric, dimension: p.dimension || null, type: p.type, share });
        B.sel = { kind: 'slot', id: p.slot };
        await change((spec) => put(spec, p.slot, share ? { type: made.chart.type, metric: made.chart.metric, asset: made.chart.id }
          : { type: made.chart.type, metric: made.chart.metric, dimension: made.chart.dimension, options: made.chart.options }));
        if (made.problems.length) toast(made.problems.join(' · '), 'bad');
      } catch (error) { toast(error.message, 'bad'); }
    };
    return [h('div', { class: 'kind', text: 'CREATE FROM TEMPLATE' }), h('h3', { text: `${template.name} → ${p.slot}` }),
      h('div', { class: 'small muted', style: 'margin-top:6px', text: template.description }),
      group('1 · METRIC', select(p.metric, [['', '—'], ...B.cards.filter((c) => c.kind === 'metric').map((m) => [m.id, m.name])], (v) => { p.metric = v; p.dimension = ''; draw(); })),
      template.facts.needs.includes('dimension') ? group('2 · DIMENSION', select(p.dimension, [['', template.id === 'geographic' ? 'by its geography' : '—'],
        ...(metric ? metric.facts.dimensions : []).map((d) => [d, (card('dimension', d) || { name: d }).name])], (v) => { p.dimension = v; draw(); })) : null,
      kinds.length > 1 ? group('3 · STYLE', select(p.type, kinds.map((k) => [k, (TYPES[k] || ['', k])[1]]), (v) => { p.type = v; draw(); })) : null,
      h('div', { class: 'row', style: 'margin-top:14px' },
        h('button', { class: 'btn primary', text: 'Use in this slot', disabled: !p.metric, onclick: () => create(false) }),
        h('button', { class: 'btn', text: 'Create as shared', disabled: !p.metric || !Studio.can('assets.write'), onclick: () => create(true) }))];
  }

  /* A chart. What it IS is said; how it looks and is tuned is Superset's Explore, one click away. */
  function chartPanel(slot) {
    const c = comp(slot) || parked(slot), shared = Boolean(c.asset);
    const metric = card('metric', c.metric), live = B.doc.superset && B.doc.superset.charts[slot];
    const failing = B.doc.checks.filter((k) => k.status === 'fail' && (k.slot === slot || (k.subject || '').split(' ')[0] === slot));
    const edited = B.changes && B.changes.charts.find((x) => x.slot === slot);
    const set = Object.entries(c.options || {}).filter(([, v]) => v !== null && v !== undefined).map(([k, v]) => `${FIELD[k] || k}: ${v}`);
    const edit = (fn) => change((spec) => { const x = spec.components.find((k) => k.slot === slot); if (x) fn(x); });
    return [h('div', { class: 'kind', text: c.type === 'big_number' ? 'KPI CARD' : 'CHART' }), h('h3', { text: (B.doc.titles || {})[slot] || c.title || slot }),
      h('div', { class: 'row wrap', style: 'padding:6px 0 0' }, shared ? h('span', { class: 'badge accent', text: `shared: ${c.asset}` }) : h('span', { class: 'badge', text: 'local to this dashboard' }),
        h('span', { class: 'badge', text: `slot ${slot}` }), parked(slot) ? h('span', { class: 'badge warn', text: 'parked' }) : null,
        edited ? h('span', { class: 'badge info', text: 'edited in Superset' }) : null),
      failing.length ? h('div', { class: 'note bad small', style: 'margin-top:8px' }, failing.map((k) => h('div', { text: `✗ ${k.name}: ${k.detail}` }))) : null,
      group('WHAT IT IS', Studio.kv([['Chart', (TYPES[c.type] || ['', c.type])[1]], ['Metric', metric ? metric.name : c.metric], ['By', c.dimension ? (card('dimension', c.dimension) || { name: c.dimension }).name : '—'],
        ['Dataset', c.dataset || 'from the registry'], set.length ? ['Set', set.join(' · ')] : null, ['In Superset', live ? `chart ${live.id}` : 'not yet']])),
      group('EDIT', h('button', { class: 'btn primary', text: 'Edit in Superset (Explore)', disabled: !live, onclick: () => showExplore(slot) }),
        h('div', { class: 'small muted', text: 'Top N, time grain, order, title: change them in Explore and save there. The Studio then shows what changed, and « Take into the specification » makes it part of the dashboard — or says why it cannot be.' }),
        h('div', { class: 'small muted', text: 'Its metric, its dimension or its dataset: drop another one from the library on the slot. Its kind of chart: drop a chart template.' })),
      group('DESCRIPTION', area(c.description, (v) => edit((x) => { x.description = v || null; }))),
      shared ? h('div', { class: 'note small', style: 'margin-top:8px', text: 'Defined once, in the library: every dashboard that uses it follows. Edited in Explore and taken, this one becomes a local copy.' }) : null,
      h('div', { class: 'row wrap', style: 'margin-top:14px' },
        shared ? h('button', { class: 'btn', text: 'Stop referencing', disabled: !B.doc.editable, onclick: () => edit((x) => { x.asset = null; }) })
          : h('button', { class: 'btn', text: 'Share this chart…', disabled: !B.doc.editable || !Studio.can('assets.write') || failing.length > 0, onclick: () => shareChart(slot, c) }),
        shared ? h('button', { class: 'btn', text: 'Open in the library', onclick: () => go('asset', 'chart', c.asset) }) : null,
        h('button', { class: 'btn danger', text: 'Remove', disabled: !B.doc.editable, onclick: () => change((spec) => { spec.components = spec.components.filter((x) => x.slot !== slot); }) }))];
  }
  function shareChart(slot, c) {
    const name = h('input', { class: 'input', value: (B.doc.titles || {})[slot] || '' });
    const description = h('textarea', { class: 'input' }, c.description || '');
    dialog('Share this chart', h('div', { class: 'stack' }, field('Name', name), field('Description', description),
      h('div', { class: 'small muted', text: 'It becomes one definition in the library. This dashboard will reference it; so can any other.' })), [['Share', 'primary', async (close) => {
        try {
          const saved = await api('POST', '/assets/chart', { asset: { name: name.value, description: description.value, type: c.type, metric: c.metric, dimension: c.dimension, options: c.options } });
          close(); await change((spec) => { const x = spec.components.find((k) => k.slot === slot); x.asset = saved.id; });
          toast(`« ${saved.name} » is shared. It is not certified until someone else certifies it.`, 'ok');
        } catch (error) { toast(error.message, 'bad'); } }]]);
  }

  function assetPanel(found) {
    const facts = Object.entries(found.facts || {}).filter(([, v]) => v !== null && v !== '' && !(Array.isArray(v) && !v.length));
    const usable = ['metric', 'chart', 'dataset', 'dimension', 'chart_template'].includes(found.kind) && B.lastSlot && B.doc.editable;
    const impact = h('div');
    if (found.scope !== 'local' && !['dashboard'].includes(found.kind)) {
      api('GET', `/assets/${found.kind}/${enc(found.id)}/impact`).then((i) => fill(impact, impactBox(i))).catch(() => {});
    }
    return [h('div', { class: 'kind', text: (KIND[found.kind] || found.kind).toUpperCase() }), h('h3', { text: found.name }),
      h('div', { class: 'row wrap', style: 'padding-top:6px' }, badge(found.status), badge(found.scope), found.domain_name ? h('span', { class: 'badge', text: found.domain_name }) : null),
      h('div', { class: 'small', style: 'margin-top:8px', text: found.description || '' }),
      group('ABOUT', Studio.kv([['Id', h('span', { class: 'mono', text: found.id })], found.owner ? ['Owner', found.owner] : null,
        ...facts.map(([k, v]) => [k.replace(/_/g, ' '), Array.isArray(v) ? v.join(', ') : String(v)])])),
      group('USED BY', h('div', { class: 'small', text: `${found.used_by.dashboards} dashboard(s) · ${found.used_by.charts} chart(s)` }), impact),
      h('div', { class: 'row wrap', style: 'margin-top:14px' },
        usable ? h('button', { class: 'btn primary', text: `Use in ${B.lastSlot}`, onclick: () => dropOnSlot(B.lastSlot, found) }) : null,
        found.scope === 'local' ? null : h('button', { class: 'btn', text: 'Open asset →', onclick: () => go('asset', found.kind, found.id) }))];
  }

  // ── the bottom bar ─────────────────────────────────────────────────────────────────────────────────────
  function statusBar() {
    const v = B.doc.validation, doc = B.doc, found = B.changes;
    const assets = new Set([...B.spec.components.flatMap((c) => [c.dataset && `d:${c.dataset}`, `m:${c.metric}`, c.asset && `c:${c.asset}`]).filter(Boolean)]);
    const warnings = doc.governance.filter((g) => g.status !== 'pass').length;
    const toGovernance = () => { B.mode = 'governance'; draw(); };
    const same = found && found.version && !found.version.differs && !doc.draft;       // Superset still shows the validated version
    const state = B.busy ? 'Bringing Superset up to date…' : doc.draft ? 'Kept as a draft: not valid yet, so not in Superset.'
      : !doc.superset ? '' : same ? `Superset shows version ${doc.version}, as validated.` : doc.version ? `Superset shows what you composed since version ${doc.version}: not validated yet.`
      : 'In Superset, in DEV: not validated yet.';
    return [h('a', { class: 'row', style: `color:${v.passed ? 'var(--success)' : 'var(--error)'};font-weight:600`, onclick: toGovernance },
        v.passed ? '✓ Validation' : `✗ ${v.failures} failed`, h('span', { class: 'muted small', style: 'font-weight:400', text: `${v.checks} checks` })),
      h('span', { text: `${assets.size} assets` }),
      h('a', { style: warnings ? 'color:#b27d00' : 'color:var(--muted)', text: `${warnings} warning${warnings === 1 ? '' : 's'}`, onclick: toGovernance }),
      h('span', { class: 'grow small muted ellipsis', 'data-state': 'yes', text: state }),
      h('button', { class: 'btn primary', text: 'Build', title: 'Validate what Superset shows — data, security, performance, the picture — and make it a version',
        disabled: !doc.editable || B.busy || !v.passed || !doc.applied || Boolean(same), onclick: build }),
      h('button', { class: 'btn', text: 'Deploy →', title: 'Review and promotion: UAT, then PROD', disabled: !doc.version, onclick: () => go('review') })];
  }
})();
