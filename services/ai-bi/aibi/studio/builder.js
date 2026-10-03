'use strict';
/* The dashboard builder: an asset library on the left, the dashboard as sections and slots in the middle, a
   contextual inspector on the right, validation at the bottom.

   What is edited is a SPECIFICATION on a template's slots — never the BI engine's own dashboard state. Every
   change is sent to the service (POST /builder/inspect), which resolves it, validates it and says how to
   draw it; « Build » then takes the saved draft through the same steps and gates as a generated one. */
(() => {
  const { S, h, enc, clone, api, toast, badge, dot, field, select, dialog, impactBox, go, blob } = Studio;
  const TYPES = { big_number: ['#', 'KPI'], line: ['∿', 'Line'], area: ['◭', 'Area'], bar: ['▤', 'Bar'], histogram: ['▥', 'Histogram'],
                  pie: ['◔', 'Pie'], table: ['▦', 'Table'], map: ['◈', 'Map'], telecom_qoe: ['✦', 'Telecom QoE'] };
  const TABS = { datasets: ['dataset', 'dimension'], charts: ['chart', 'section'], metrics: ['metric'], templates: ['chart_template', 'template'] };
  const KIND = { dataset: 'Dataset', dimension: 'Dimension', chart: 'Chart', section: 'Section', metric: 'Metric',
                 chart_template: 'Chart template', template: 'Dashboard template', dashboard: 'Dashboard' };
  let B = null;
  Studio.unsaved = () => Boolean(B && B.dirty);

  Studio.views.builder = async (projectId) => {
    if (!projectId) return Studio.views.dashboards();
    if (B && B.id === projectId && B.dirty) { draw(); return B.el; }
    const asked = S.token;
    const [doc, cards] = await Promise.all([api('GET', `/builder/projects/${enc(projectId)}`),
                                           api('GET', `/assets?project_id=${enc(projectId)}`)]);
    // Another screen was asked for while this one loaded: it must not take over the builder's state. (It did,
    // once: the page on screen was then wired to a project that was not the one drawn.)
    if (asked !== S.token) return h('div');
    B = { id: projectId, doc, spec: clone(doc.spec), cards, dirty: false, sel: { kind: 'dashboard' }, mode: 'design', left: 'assets',
          tab: 'datasets', scope: 'all', q: '', collapsed: new Set(), closed: new Set(), view: 'compose', details: {}, seq: 0,
          lastSlot: null, el: h('div', { class: 'builder' }) };
    draw();
    return B.el;
  };

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
  async function inspect() {
    const mine = ++B.seq, here = B;
    try {
      const found = await api('POST', '/builder/inspect', { spec: B.spec, project_id: B.id });
      if (here !== B || mine !== B.seq) return;
      Object.assign(B.doc, found);
      B.spec = clone(found.spec);
    } catch (error) { toast(error.message, 'bad'); }
    draw();
  }
  async function change(fn) {
    if (!B.doc.editable) { toast(B.doc.locked_because || 'This project cannot be changed.', 'bad'); return; }
    fn(B.spec);
    B.dirty = true;
    await inspect();
  }
  async function reloadCards() { B.cards = await api('GET', `/assets?project_id=${enc(B.id)}`); }

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
      return change((spec) => { spec.components.find((c) => c.slot === slot).dataset = asset.id; spec.components.find((c) => c.slot === slot).asset = null; });
    }
    if (asset.kind === 'dimension') {
      if (!existing) return toast('Drop a dimension on a chart, on a section, or on the filter bar.', 'bad');
      return change((spec) => { const c = spec.components.find((x) => x.slot === slot); c.dimension = asset.id; c.asset = null; c.title = null; });
    }
    if (asset.kind === 'chart_template') {
      if (found.status !== 'certified') return toast(`« ${found.name} » cannot be drawn yet: ${found.facts.reason}`, 'bad');
      const kinds = [found.facts.visualization, ...found.facts.alternatives].filter((k) => def.accepts.includes(k));
      if (!kinds.length) return toast(`Slot ${slot} accepts ${def.accepts.join(', ')} — « ${found.name} » is drawn as ${found.facts.visualization}.`, 'bad');
      B.sel = { kind: 'pending', slot, template: found.id, type: kinds[0], metric: (B.spec.components[0] || {}).metric || '', dimension: '' };
      return draw();
    }
    if (asset.kind === 'section') return fillSection(sectionOf(slot).id, asset.id);
    toast(`A ${KIND[asset.kind] || asset.kind} is not something a slot takes.`, 'bad');
  }
  async function fillSection(sectionId, assetId) {
    if (!B.doc.editable) return toast(B.doc.locked_because, 'bad');
    try {
      const found = await api('POST', '/builder/fill-section', { spec: B.spec, section: sectionId, asset: assetId });
      Object.assign(B.doc, found); B.spec = clone(found.spec); B.dirty = true; B.sel = { kind: 'section', id: sectionId };
      toast(found.notes.join(' · '), found.notes.some((n) => n.includes('found no slot')) ? 'bad' : 'ok');
      draw();
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

  // ── saving and building ────────────────────────────────────────────────────────────────────────────────
  async function save() {
    const here = B;
    try {
      const doc = await api('PUT', `/builder/projects/${enc(B.id)}/draft`, { spec: B.spec });
      if (here !== B) return true;
      Object.assign(B.doc, doc); B.spec = clone(doc.spec); B.dirty = false;
      await reloadCards();
      toast('Draft saved. Nothing in the BI engine has changed: what was built is still what was validated.', 'ok');
      draw();
      return true;
    } catch (error) { toast(error.message, 'bad'); return false; }
  }
  async function dropDraft() {
    try {
      const doc = await api('DELETE', `/builder/projects/${enc(B.id)}/draft`);
      Object.assign(B.doc, doc); B.spec = clone(doc.spec); B.dirty = false;
      await reloadCards(); draw();
    } catch (error) { toast(error.message, 'bad'); }
  }
  async function build() {
    if (B.dirty && !(await save())) return;
    let started;
    try { started = await api('POST', `/builder/projects/${enc(B.id)}/build`); } catch (error) { return toast(error.message, 'bad'); }
    const body = h('div', { class: 'stack' });
    const close = dialog('Building the draft', body, []);
    const here = B;                       // the builder this build belongs to: it reports there, or nowhere
    const follow = async () => {
      const run = await api('GET', `/runs/${started.run_id}`);
      body.replaceChildren(Studio.runSteps(run));
      if (run.status === 'running') return setTimeout(follow, 2000);
      if (here !== B) return;
      const doc = await api('GET', `/builder/projects/${enc(B.id)}`);
      if (here !== B) return;
      Object.assign(B.doc, doc); B.spec = clone(doc.spec); B.dirty = false;
      await reloadCards(); draw();
      if (run.status === 'succeeded') { toast(`Version ${run.version} is READY_FOR_REVIEW.`, 'ok'); }
    };
    follow().catch((error) => { close(); toast(error.message, 'bad'); });
  }

  // ── drawing ────────────────────────────────────────────────────────────────────────────────────────────
  function draw() {
    const keep = {};
    B.el.querySelectorAll('[data-scroll]').forEach((el) => { keep[el.dataset.scroll] = el.scrollTop; });
    B.el.replaceChildren(head(), h('div', { class: 'bbody' }, library(), center(), inspector()), statusBar());
    B.el.querySelectorAll('[data-scroll]').forEach((el) => { if (keep[el.dataset.scroll]) el.scrollTop = keep[el.dataset.scroll]; });
  }
  function seg(current, options, onpick) {
    return h('div', { class: 'seg' }, options.map(([value, label]) => h('button', { class: value === current ? 'on' : '', text: label, onclick: () => onpick(value) })));
  }
  function head() {
    const doc = B.doc;
    return h('div', { class: 'bhead' },
      h('a', { text: '← Dashboards', onclick: () => go('dashboards') }),
      h('div', { class: 'grow ellipsis' }, h('span', { style: 'font-weight:700;font-size:15px', text: B.spec.dashboard.title }),
        h('span', { class: 'muted', text: `   ${B.id}${doc.version ? ' · v' + doc.version : ''} · ${doc.environment}` })),
      badge(doc.status),
      doc.editable ? null : h('span', { class: 'badge warn', title: doc.locked_because || '', text: 'read-only' }),
      seg(B.mode, [['design', 'Design'], ['data', 'Data'], ['governance', 'Governance']], (m) => { B.mode = m; draw(); }),
      seg(B.view, [['compose', 'Compose'], ['preview', 'Last build']], (v) => { B.view = v; draw(); }),
      doc.dashboard_url ? h('a', { href: doc.dashboard_url, target: '_blank', rel: 'noopener', text: 'Open in Superset ↗' }) : null);
  }

  // ── the left panel: the asset library, or the outline ──────────────────────────────────────────────────
  function library() {
    const panel = h('div', { class: 'lib' });
    panel.append(h('div', { class: 'head' }, h('div', { class: 'tabs' },
      [['assets', 'DATA & ASSETS'], ['outline', 'OUTLINE']].map(([key, label]) => h('button', { class: `tab ${B.left === key ? 'on' : ''}`, text: label, onclick: () => { B.left = key; draw(); } })))));
    if (B.left === 'outline') { panel.append(h('div', { class: 'scroll', 'data-scroll': 'outline' }, outline())); return panel; }
    const list = h('div', { class: 'scroll', 'data-scroll': 'lib' });
    const fill = () => list.replaceChildren(...assetList());
    panel.append(h('div', { class: 'head stack' },
      h('input', { class: 'input', placeholder: 'Search assets…', value: B.q, oninput: (e) => { B.q = e.target.value; fill(); } }),
      h('div', { class: 'tabs' }, Object.keys(TABS).map((key) => h('button', { class: `tab ${B.tab === key ? 'on' : ''}`,
        text: key[0].toUpperCase() + key.slice(1), onclick: () => { B.tab = key; draw(); } }))),
      ['datasets', 'charts'].includes(B.tab) ? seg(B.scope, [['all', 'All'], ['shared', 'Shared'], ['local', 'Local']], (v) => { B.scope = v; draw(); }) : null));
    fill();
    panel.append(list);
    return panel;
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
  function outline() {
    const tree = B.doc.outline;
    const node = (label, selected, onclick, extra) => h('div', { class: `node ${selected ? 'on' : ''}`, onclick }, label, extra || null);
    return h('div', { class: 'tree' },
      node(h('b', { text: tree.dashboard.title }), B.sel.kind === 'dashboard', () => { B.sel = { kind: 'dashboard' }; draw(); }),
      h('div', { class: 'kids' }, tree.sections.map((section) => [
        node([h('span', { class: 'grow ellipsis', text: section.title }), section.visibility === 'hidden' ? h('span', { class: 'badge', text: 'hidden' }) : null,
              section.asset ? h('span', { class: 'badge accent', text: 'shared' }) : null],
          B.sel.kind === 'section' && B.sel.id === section.id, () => { B.sel = { kind: 'section', id: section.id }; draw(); }),
        h('div', { class: 'kids' }, section.charts.map((c) => node(
          [h('span', { class: 'muted', text: (TYPES[c.type] || ['·'])[0] }), h('span', { class: 'grow ellipsis', style: c.parked ? 'opacity:.55' : '', text: c.title }),
           c.problems ? h('span', { class: 'badge bad', text: c.problems }) : null, c.scope === 'shared' ? h('span', { class: 'badge accent', text: 'shared' }) : null],
          B.sel.kind === 'slot' && B.sel.id === c.slot, () => { B.sel = { kind: 'slot', id: c.slot }; B.lastSlot = c.slot; draw(); })),
          section.empty_slots.length ? h('div', { class: 'node muted small', text: `${section.empty_slots.length} empty slot(s)` }) : null)])));
  }

  // ── the middle: the dashboard, its data, or its governance ─────────────────────────────────────────────
  function center() {
    if (B.view === 'preview') return preview();
    if (B.mode === 'data') return dataMode();
    if (B.mode === 'governance') return governanceMode();
    const filters = h('div', { class: 'filters', ...dropTarget((asset) => {
      if (asset.kind !== 'dimension') return toast('Only a dimension becomes a filter.', 'bad');
      change((spec) => { if (!spec.filters.includes(asset.id)) spec.filters.push(asset.id); }); }) },
      h('span', { class: 'muted small', text: 'FILTERS' }),
      B.spec.filters.map((column) => h('span', { class: 'chip' }, (card('dimension', column) || { name: column }).name,
        h('button', { title: 'Remove', text: '×', onclick: (e) => { e.stopPropagation(); change((spec) => { spec.filters = spec.filters.filter((f) => f !== column); }); } }))),
      B.spec.filters.length ? null : h('span', { class: 'muted small', text: 'none — drop a dimension here' }));
    return h('div', { class: `canvas ${B.sel.kind === 'dashboard' ? 'sel' : ''}`, 'data-scroll': 'canvas',
      onclick: (e) => { if (e.target === e.currentTarget) { B.sel = { kind: 'dashboard' }; draw(); } } },
      B.doc.editable ? null : h('div', { class: 'note warn', style: 'margin-bottom:10px', text: `Read-only: ${B.doc.locked_because}` }),
      filters, B.doc.canvas.map(sectionBox));
  }
  function sectionBox(section) {
    const collapsed = B.collapsed.has(section.id), hidden = section.visibility === 'hidden';
    const box = h('div', { class: `section ${hidden ? 'hidden' : ''} ${B.sel.kind === 'section' && B.sel.id === section.id ? 'sel' : ''}`, 'data-section': section.id,
      ...dropTarget((asset) => {
        if (asset.kind === 'section') return fillSection(section.id, asset.id);
        if (asset.kind === 'dimension') return change((spec) => { const s = own(spec, section.id); if (!s.filters.includes(asset.id)) s.filters.push(asset.id); });
        toast('Drop a chart or a metric on a slot; a shared section or a dimension on a section.', 'bad'); }) });
    box.append(h('div', { class: 'shead', onclick: () => { B.sel = { kind: 'section', id: section.id }; draw(); } },
      h('button', { class: 'btn ghost', title: collapsed ? 'Expand' : 'Collapse', text: collapsed ? '▸' : '▾',
        onclick: (e) => { e.stopPropagation(); collapsed ? B.collapsed.delete(section.id) : B.collapsed.add(section.id); draw(); } }),
      h('span', { class: 'title', text: section.title || section.id }),
      h('span', { class: 'muted small grow ellipsis', text: section.description || '' }),
      section.filters.map((f) => h('span', { class: 'badge info', text: `filter: ${f}` })),
      section.owner ? h('span', { class: 'badge', text: section.owner }) : null,
      section.asset ? h('span', { class: 'badge accent', text: `shared: ${section.asset}` }) : null,
      hidden ? h('span', { class: 'badge warn', text: 'hidden' }) : null));
    if (!collapsed) box.append(h('div', { class: 'sbody' }, section.rows.map((row) => h('div', { class: 'srow' }, row.slots.map((slot) => slotBox(slot, row))))));
    return box;
  }
  function slotBox(slot, row) {
    const c = slot.component, selected = B.sel.kind === 'slot' && B.sel.id === slot.slot;
    const problems = (B.doc.problems || {})[slot.slot] || 0;
    const style = `grid-column: span ${slot.width}; min-height: ${Math.max(70, row.height * 3.4)}px`;
    const pick = (e) => { e.stopPropagation(); B.sel = { kind: 'slot', id: slot.slot }; B.lastSlot = slot.slot; draw(); };
    if (!c) {
      return h('div', { class: `slot empty ${selected ? 'sel' : ''}`, 'data-slot': slot.slot, style, onclick: pick, ...dropTarget((asset) => dropOnSlot(slot.slot, asset)) },
        h('div', { style: 'font-size:18px', text: '+' }), h('div', { class: 'small', text: `${slot.purpose} · ${slot.accepts.join(' / ')}` }),
        slot.mandatory ? h('span', { class: 'badge warn', text: 'mandatory' }) : null);
    }
    const metric = card('metric', c.metric);
    return h('div', { class: `slot ${c.type === 'big_number' ? 'kpi' : ''} ${selected ? 'sel' : ''} ${problems ? 'bad' : ''}`, 'data-slot': slot.slot, style, onclick: pick,
      ...dropTarget((asset) => dropOnSlot(slot.slot, asset)) },
      h('div', { class: 'corner' }, problems ? h('span', { class: 'badge bad', text: `${problems} ✗` }) : null,
        c.asset ? h('span', { class: 'badge accent', text: 'shared' }) : null, slot.parked ? h('span', { class: 'badge warn', text: 'parked' }) : null),
      h('div', { class: 'stitle ellipsis', style: problems || c.asset || slot.parked ? 'padding-right:64px' : '', text: (B.doc.titles || {})[slot.slot] || c.title || c.slot }),
      h('div', { class: 'small muted ellipsis', text: `${(TYPES[c.type] || ['', c.type])[1]} · ${metric ? metric.name : c.metric}${c.dimension ? ' by ' + c.dimension : ''}` }),
      h('div', { class: 'small muted ellipsis', text: c.dataset || 'dataset: from the registry' }),
      h('div', { class: 'glyph', text: (TYPES[c.type] || ['·'])[0] }));
  }
  function preview() {
    const box = h('div', { class: 'canvas', 'data-scroll': 'preview' });
    if (!B.doc.version) { box.append(h('div', { class: 'note', text: 'Not built yet. Save the draft, then Build: the service validates it, creates it in Superset, renders it and looks at it.' })); return box; }
    box.append(h('div', { class: 'row', style: 'margin-bottom:10px' }, h('b', { text: `Version ${B.doc.version}, as it was rendered and checked` }), badge(B.doc.status),
      B.dirty || B.doc.draft ? h('span', { class: 'badge warn', text: 'the draft differs from this' }) : null));
    const img = h('img', { class: 'shot', alt: 'The dashboard, as rendered at validation' });
    blob(`/projects/${enc(B.id)}/versions/${B.doc.version}/screenshot`).then((url) => { img.src = url; }).catch(() => img.replaceWith(h('div', { class: 'muted', text: 'No screenshot is kept for this version.' })));
    box.append(img);
    return box;
  }

  // ── data mode ──────────────────────────────────────────────────────────────────────────────────────────
  async function detail(kind, id) {
    const key = `${kind}:${id}`;
    if (!B.details[key]) B.details[key] = await api('GET', `/assets/${kind}/${enc(id)}`);
    return B.details[key];
  }
  function dataMode() {
    const box = h('div', { class: 'canvas stack', 'data-scroll': 'data' });
    const datasets = [...new Set(B.spec.components.map((c) => c.dataset).filter(Boolean))];
    const metrics = [...new Set(B.spec.components.map((c) => c.metric))];
    box.append(h('h2', { text: 'Datasets of this dashboard' }));
    if (!datasets.length) box.append(h('div', { class: 'muted', text: 'No chart yet.' }));
    for (const id of datasets) {
      const holder = h('div', { class: 'card stack' }, h('div', { class: 'muted', text: `${id} …` }));
      box.append(holder);
      detail('dataset', id).then((d) => {
        const sample = h('div');
        holder.replaceChildren(
          h('div', { class: 'row wrap' }, h('b', { text: d.name }), badge(d.status), h('span', { class: 'mono muted', text: d.definition.relation }),
            h('span', { class: 'grow' }), h('span', { class: 'small muted', text: `Refresh: ${d.facts.refresh} · Rows: ${Studio.fmt(d.facts.rows)} · used by ${d.used_by.dashboards} dashboard(s)` })),
          d.definition.derived_from && d.definition.derived_from.length ? h('div', { class: 'small', text: `Relationships: derived from ${d.definition.derived_from.join(', ')} — grain ${d.definition.grain.join(', ')}` })
            : h('div', { class: 'small muted', text: `Grain: ${(d.definition.grain || []).join(', ')}` }),
          Studio.table([{ title: 'COLUMN', cell: (r) => r[0] }, { title: 'LABEL', cell: (r) => r[1].label }, { title: 'TYPE', cell: (r) => r[1].type },
                        { title: 'ROLE', cell: (r) => r[1].role || '—' }, { title: 'CLASSIFICATION', cell: (r) => badge(r[1].readable ? 'pass' : 'fail', r[1].classification) }],
            Object.entries(d.definition.columns)),
          h('div', { class: 'row' }, h('button', { class: 'btn', text: 'Preview rows', onclick: async () => {
            try {
              const rows = await api('POST', '/tools/get_dataset_sample', { arguments: { dataset_id: id, limit: 8 } });
              sample.replaceChildren(Studio.table(rows.columns.map((name, i) => ({ title: name.toUpperCase(), cell: (r) => String(r[i] ?? '') })), rows.rows));
            } catch (error) { toast(error.message, 'bad'); } } }),
            h('a', { text: 'Lineage and impact →', onclick: () => go('asset', 'dataset', id) })), sample);
      }).catch((error) => holder.replaceChildren(h('div', { class: 'note bad', text: error.message })));
    }
    box.append(h('h2', { text: 'Metrics', style: 'margin-top:8px' }));
    for (const id of metrics) {
      const holder = h('div', { class: 'card stack' }, h('div', { class: 'muted', text: `${id} …` }));
      box.append(holder);
      detail('metric', id).then((m) => holder.replaceChildren(
        h('div', { class: 'row wrap' }, h('b', { text: m.name }), badge(m.status), h('span', { class: 'badge', text: m.facts.classification }),
          h('span', { class: 'grow' }), h('span', { class: 'small muted', text: `${m.facts.aggregation} · ${m.facts.format} ${m.facts.unit} · by ${m.facts.dimensions.join(', ')}` })),
        h('pre', { class: 'code mono', text: m.definition.expression }), h('div', { class: 'small muted', text: m.description })))
        .catch((error) => holder.replaceChildren(h('div', { class: 'note bad', text: error.message })));
    }
    box.append(h('div', { class: 'small muted', text: 'A query of your own: SQL / Data Lab, in the navigation — the governed read-only gateway. Free exploration is Superset’s SQL Lab.' }));
    return box;
  }

  // ── governance mode ────────────────────────────────────────────────────────────────────────────────────
  function governanceMode() {
    const mark = { pass: ['✓', 'var(--success)'], warn: ['⚠', '#b27d00'], fail: ['✗', 'var(--error)'] };
    const failed = B.doc.checks.filter((c) => c.status === 'fail'), warned = B.doc.checks.filter((c) => c.status === 'warn');
    return h('div', { class: 'canvas stack', 'data-scroll': 'gov' },
      h('h2', { text: 'Governance' }),
      h('div', { class: 'card' }, B.doc.governance.map((item) => h('div', { class: 'check' },
        h('b', { style: `color:${mark[item.status][1]};width:16px`, text: mark[item.status][0] }),
        h('div', { class: 'grow' }, h('div', { style: 'font-weight:600', text: item.check }), h('div', { class: 'small muted', text: item.detail }))))),
      h('h2', { text: 'Who it is for' }),
      h('div', { class: 'card stack' },
        h('div', { class: 'row wrap' }, B.doc.audience.length ? B.doc.audience.map((role) => h('span', { class: 'badge accent', text: role }))
          : h('span', { class: 'muted', text: 'No role yet: it needs a chart on a dataset.' })),
        (B.doc.row_rules || []).map((rule) => h('div', { class: 'small' }, h('b', { text: rule.role }), ` sees of ${rule.dataset} only: `, h('span', { class: 'mono', text: rule.clause }))),
        h('div', { class: 'small muted', text: 'Roles granted every dataset of the dashboard and cleared for every metric on it. Set in Superset when it is built; asked of Superset when it is published.' })),
      h('h2', { text: `The working specification: ${B.doc.validation.checks} checks, ${failed.length} failed, ${warned.length} warning(s)` }),
      h('div', { class: 'card' }, [...failed, ...warned].length ? [...failed, ...warned].map((c) => h('div', { class: 'check' },
        h('b', { style: `color:${mark[c.status][1]};width:16px`, text: mark[c.status][0] }),
        h('div', { class: 'grow' }, h('div', { style: 'font-weight:600', text: `${c.name}${c.subject ? ' · ' + c.subject : ''}` }), h('div', { class: 'small muted', text: c.detail }))))
        : h('div', { class: 'muted', text: 'Every check of the specification passes. Data, the BI engine, security, performance and the picture are checked when it is built.' })));
  }

  // ── the inspector: what is selected, and only that ─────────────────────────────────────────────────────
  function inspector() {
    const body = h('div', { class: 'scroll', 'data-scroll': 'insp' });
    const panel = h('div', { class: 'insp' }, body);
    const kind = B.sel.kind;
    if (kind === 'slot' && !comp(B.sel.id) && !parked(B.sel.id)) body.append(...emptySlot(B.sel.id));
    else if (kind === 'slot') body.append(...chartPanel(B.sel.id));
    else if (kind === 'section') body.append(...sectionPanel(B.sel.id));
    else if (kind === 'asset') body.append(...assetPanel(B.sel.card));
    else if (kind === 'pending') body.append(...pendingPanel());
    else body.append(...dashboardPanel());
    return panel;
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
      group('PARAMETERS', h('div', { class: 'small muted', text: 'None: a dashboard here has filters, not parameters.' })),
      group('PERMISSIONS', h('div', { class: 'row wrap' }, B.doc.audience.map((r) => h('span', { class: 'badge accent', text: r }))),
        h('div', { class: 'small muted', text: `${(B.doc.row_rules || []).length} row-level rule(s). Derived from SharpSight’s RBAC, never chosen here — see Governance.` })),
      group('RESPONSIVE BEHAVIOR', h('div', { class: 'small', text: B.doc.template.responsive.viewports.map((v) => `${v.name} ${v.width}px`).join(' · ') }),
        h('div', { class: 'small muted', text: B.doc.template.responsive.collapse_empty_slots ? 'An empty slot gives its columns to its neighbours.' : 'Empty slots keep their place.' }))];
  }
  async function changeTemplate(id) {
    if (id === B.doc.template.template.id) return;
    await change((spec) => { spec.dashboard.template = id; spec.sections = []; });
    await change((spec) => {                     // what has no slot in the new template cannot stay; every section is named
      spec.components = spec.components.filter((c) => B.doc.template.slots[c.slot] && B.doc.template.slots[c.slot].accepts.includes(c.type));
      spec.sections = B.doc.canvas.map((s) => ({ id: s.id, visibility: 'visible', filters: [], parked: [] }));
    });
    toast('Template changed: charts kept where the new template has a slot that accepts them.', 'ok');
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
      group('SECTION', field('Title', text(section.title, set('title'))), field('Description', area(section.description, set('description'))),
        field('Owner', text(section.owner, set('owner'), { placeholder: 'a person or a team' }))),
      group('VISIBILITY', select(section.visibility, [['visible', 'Visible on the dashboard'], ['hidden', 'Hidden — kept in the specification, not built']], toggle, { disabled: !B.doc.editable }),
        h('div', { class: 'small muted', text: 'Collapse and expand (▾) is how you look at it here; hidden is what gets built.' })),
      group('FILTERS', h('div', { class: 'row wrap' }, section.filters.map((f) => h('span', { class: 'chip' }, f, h('button', { text: '×', onclick: () => change((spec) => { const s = own(spec, id); s.filters = s.filters.filter((x) => x !== f); }) })))),
        field('A filter that reaches this section only', select('', dimensionOptions().filter(([f]) => !section.filters.includes(f)), (v) => v && change((spec) => own(spec, id).filters.push(v)), { disabled: !B.doc.editable }))),
      group('LAYOUT', h('div', { class: 'small', text: section.rows.map((row) => `${row.row}: ${row.slots.map((s) => s.width).join(' + ')} of 12`).join(' · ') }),
        h('div', { class: 'small muted', text: `${section.slots.length} slots, ${charts.length} filled. The rows are the template’s.` })),
      group('SHARED', section.asset ? [h('div', { class: 'small', text: `Filled from shared section « ${section.asset} ».` }),
          h('button', { class: 'btn', text: 'Detach from the shared section', disabled: !B.doc.editable, onclick: () => change((spec) => { own(spec, id).asset = null; }) })]
        : [h('div', { class: 'small muted', text: allShared ? 'Every chart here is shared: the section can be shared too.' : 'A section is shared when all its charts are shared charts.' }),
           h('button', { class: 'btn', text: 'Save as a shared section…', disabled: !allShared || !Studio.can('assets.write') || !B.doc.editable, onclick: () => shareSection(id, section, charts) })]),
      group('PERMISSIONS', h('div', { class: 'small muted', text: 'A section has the dashboard’s audience. Superset grants a dashboard, not a part of one — so a narrower audience for one section is not offered: it could not be enforced.' }))];
  }
  function shareSection(id, section, charts) {
    const name = h('input', { class: 'input', value: section.title || id });
    dialog('Save as a shared section', h('div', { class: 'stack' }, field('Name', name),
      h('div', { class: 'small muted', text: `Its charts, in order: ${charts.map((c) => c.asset).join(', ')}` })), [['Save', 'primary', async (close) => {
        try {
          const saved = await api('POST', '/assets/section', { asset: { name: name.value, description: section.description || '', charts: charts.map((c) => c.asset), filters: section.filters } });
          close(); await reloadCards(); await change((spec) => { own(spec, id).asset = saved.id; });
          toast(`Shared section « ${saved.name} » is in the library.`, 'ok');
        } catch (error) { toast(error.message, 'bad'); } }]]);
  }

  function emptySlot(slot) {
    const def = slotDef(slot);
    const metrics = B.cards.filter((c) => c.kind === 'metric');
    return [h('div', { class: 'kind', text: 'EMPTY SLOT' }), h('h3', { text: slot }),
      h('div', { style: 'margin-top:8px' }, Studio.kv([['For', def.purpose], ['Accepts', def.accepts.join(', ')], ['Mandatory', def.mandatory ? 'yes' : 'no'], ['Section', sectionOf(slot).title]])),
      group('ADD A CHART', h('div', { class: 'small muted', text: 'Drag a metric, a shared chart or a chart template here — or pick a metric:' }),
        select('', [['', '—'], ...metrics.map((m) => [m.id, m.name])], (v) => v && dropOnSlot(slot, { kind: 'metric', id: v }), { disabled: !B.doc.editable }))];
  }
  function pendingPanel() {
    const p = B.sel, template = card('chart_template', p.template), def = slotDef(p.slot);
    const kinds = [template.facts.visualization, ...template.facts.alternatives].filter((k) => def.accepts.includes(k));
    const metric = card('metric', p.metric);
    const create = async (share) => {
      try {
        const made = await api('POST', '/chart-templates/create', { template: p.template, metric: p.metric, dimension: p.dimension || null, type: p.type, share });
        if (share) await reloadCards();
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

  function chartPanel(slot) {
    const c = comp(slot) || parked(slot), def = slotDef(slot), shared = Boolean(c.asset), locked = !B.doc.editable || shared;
    const metric = card('metric', c.metric);
    const edit = (fn) => change((spec) => { const x = spec.components.find((k) => k.slot === slot); if (x) fn(x); });
    const option = (key, cast) => (value) => edit((x) => { x.options = { ...x.options, [key]: value === '' ? null : cast(value) }; });
    const failing = B.doc.checks.filter((k) => k.status === 'fail' && (k.slot === slot || (k.subject || '').split(' ')[0] === slot));
    const reaching = [...B.spec.filters, ...((B.spec.sections.find((s) => s.id === sectionOf(slot).id) || {}).filters || [])];
    const styles = [];
    if (['bar', 'table', 'pie'].includes(c.type)) styles.push(field('Rows shown (top N)', h('input', { class: 'input', type: 'number', min: 1, max: 1000, value: c.options.row_limit ?? '', disabled: locked, placeholder: 'all', onchange: (e) => option('row_limit', Number)(e.target.value) })),
      field('Order', select(c.options.sort || '', [['', 'largest first'], ['asc', 'smallest first']], option('sort', String), { disabled: locked })));
    if (['line', 'area'].includes(c.type)) styles.push(field('Time grain', select(c.options.time_grain || 'P1D', [['P1D', 'Day'], ['P1W', 'Week'], ['P1M', 'Month']], option('time_grain', String), { disabled: locked })));
    if (c.type === 'histogram') styles.push(field('Bins', h('input', { class: 'input', type: 'number', min: 4, max: 40, value: c.options.bins ?? '', disabled: locked, placeholder: '12', onchange: (e) => option('bins', Number)(e.target.value) })));
    if (c.type === 'big_number') styles.push(field('Number', select(c.options.number_format || '', [['', 'as the template decides'], ['compact', 'compact (1.2M)'], ['default', 'in full']], option('number_format', String), { disabled: locked })));
    const query = h('pre', { class: 'code mono', text: '…' });
    detail('metric', c.metric).then((m) => { query.textContent = `${m.definition.expression}\nfrom ${c.dataset || '(dataset from the registry)'}${c.dimension ? `\ngroup by ${c.dimension}` : ''}`; }).catch(() => { query.textContent = c.metric; });
    return [h('div', { class: 'kind', text: c.type === 'big_number' ? 'KPI CARD' : 'CHART' }), h('h3', { text: (B.doc.titles || {})[slot] || c.title || slot }),
      h('div', { class: 'row wrap', style: 'padding:6px 0 0' }, shared ? h('span', { class: 'badge accent', text: `shared: ${c.asset}` }) : h('span', { class: 'badge', text: 'local to this dashboard' }),
        h('span', { class: 'badge', text: `slot ${slot}` }), parked(slot) ? h('span', { class: 'badge warn', text: 'parked' }) : null),
      failing.length ? h('div', { class: 'note bad small', style: 'margin-top:8px' }, failing.map((k) => h('div', { text: `✗ ${k.name}: ${k.detail}` }))) : null,
      shared ? h('div', { class: 'note small', style: 'margin-top:8px', text: 'Defined once, in the library. Change it there — every dashboard that uses it follows — or stop referencing it to make this one a local copy.' }) : null,
      group('DATA',
        field('Dataset', select(c.dataset || '', [['', 'from the registry'], ...B.cards.filter((k) => k.kind === 'dataset').map((k) => [k.id, `${k.name}${k.scope === 'local' ? ' (local)' : ''}`])],
          (v) => edit((x) => { x.dataset = v || null; }), { disabled: locked })),
        field('Metric', select(c.metric, B.cards.filter((k) => k.kind === 'metric').map((k) => [k.id, k.name]), (v) => dropOnSlot(slot, { kind: 'metric', id: v }), { disabled: locked })),
        field('Dimension', select(c.dimension || '', dimensionOptions(metric ? metric.facts.dimensions : []), (v) => edit((x) => { x.dimension = v || null; x.title = null; }), { disabled: locked }))),
      group('FILTERS', reaching.length ? h('div', { class: 'row wrap' }, reaching.map((f) => h('span', { class: 'badge info', text: f }))) : h('div', { class: 'small muted', text: 'No filter reaches this chart.' })),
      group('STYLE',
        field('Chart type', select(c.type, def.accepts.map((k) => [k, `${(TYPES[k] || ['', k])[1]}${available(k) ? '' : ' — not installed'}`]), (v) => edit((x) => { x.type = v; x.title = null; if (!needs(v).dimension && v !== 'map') x.dimension = null; else if (!x.dimension && metric) x.dimension = defaultDimension(v, metric); }), { disabled: locked })),
        field('Title', text(c.title, (v) => edit((x) => { x.title = v || null; }), { placeholder: (B.doc.titles || {})[slot] || '', disabled: locked })),
        field('Description', area(c.description, (v) => edit((x) => { x.description = v || null; }))), ...styles,
        h('div', { class: 'small muted', text: 'Colours, labels and legend are the template’s and the design tokens’: one look across the dashboard.' })),
      group('ADVANCED', h('div', { class: 'small muted', text: 'Query — the governed metric, as registered:' }), query,
        h('div', { class: 'small muted', text: 'Caching and performance are the BI engine’s; timings are measured at each build (Operate → Performance).' })),
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
          close(); await reloadCards(); await change((spec) => { const x = spec.components.find((k) => k.slot === slot); x.asset = saved.id; });
          toast(`« ${saved.name} » is shared. It is not certified until someone else certifies it.`, 'ok');
        } catch (error) { toast(error.message, 'bad'); } }]]);
  }

  function assetPanel(found) {
    const facts = Object.entries(found.facts || {}).filter(([, v]) => v !== null && v !== '' && !(Array.isArray(v) && !v.length));
    const usable = ['metric', 'chart', 'dataset', 'dimension', 'chart_template'].includes(found.kind) && B.lastSlot && B.doc.editable;
    const impact = h('div');
    if (found.scope !== 'local' && !['dashboard'].includes(found.kind)) {
      api('GET', `/assets/${found.kind}/${enc(found.id)}/impact`).then((i) => impact.replaceChildren(impactBox(i))).catch(() => {});
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
    const v = B.doc.validation, doc = B.doc;
    const assets = new Set([...B.spec.components.flatMap((c) => [c.dataset && `d:${c.dataset}`, `m:${c.metric}`, c.asset && `c:${c.asset}`]).filter(Boolean)]);
    const warnings = doc.governance.filter((g) => g.status !== 'pass').length;
    const toGovernance = () => { B.mode = 'governance'; B.view = 'compose'; draw(); };
    return h('div', { class: 'status' },
      h('a', { class: 'row', style: `color:${v.passed ? 'var(--success)' : 'var(--error)'};font-weight:600`, onclick: toGovernance },
        v.passed ? '✓ Validation' : `✗ ${v.failures} failed`, h('span', { class: 'muted small', style: 'font-weight:400', text: `${v.checks} checks` })),
      h('span', { text: `${assets.size} assets` }),
      h('a', { style: warnings ? 'color:#b27d00' : 'color:var(--muted)', text: `${warnings} warning${warnings === 1 ? '' : 's'}`, onclick: toGovernance }),
      h('span', { class: 'grow small muted ellipsis', text: B.dirty ? 'Unsaved changes.' : doc.draft ? `Draft saved by ${doc.draft.saved_by} — not built yet.` : doc.version ? `As built: version ${doc.version}.` : '' }),
      doc.draft && doc.version && !B.dirty ? h('button', { class: 'btn', text: 'Drop the draft', disabled: !doc.editable, onclick: dropDraft }) : null,
      h('button', { class: 'btn', text: 'Save draft', disabled: !doc.editable || !B.dirty, onclick: save }),
      h('button', { class: 'btn primary', text: 'Build', title: 'Validate, create in Superset, render, check, version, package',
        disabled: !doc.editable || !(B.dirty || doc.draft) || !v.passed, onclick: build }),
      h('button', { class: 'btn', text: 'Deploy →', title: 'Review and promotion: UAT, then PROD', disabled: !doc.version, onclick: () => go('review') }));
  }
})();
