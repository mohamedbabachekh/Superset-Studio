/* SharpSight Studio — the visual builder of the brief's sections 46 to 60, inside Superset.
 *
 * WHERE IT LIVES. Superset 6.1.0 takes an extension panel in SQL Lab and nowhere else (docs/ai-bi.md §3), so
 * this is not a panel: it is a page of Superset's own, under Superset's own top bar, served by
 * superset-addon/sharpsight_bi.py. Same address, same session, same theme.
 *
 * WHAT IT EDITS. Not a dashboard — a SPECIFICATION. The canvas below is an editor for the layout and the
 * fields a specification carries, which is why there is no second renderer, no second chart editor and no
 * second SQL Lab here (the brief's section 0 forbids all three). Superset draws the dashboard; the Studio
 * says what the dashboard should be, and the engine's ten gates decide whether it may become a version.
 *
 * WHAT A TILE IS. A photograph of that chart, cut out of one picture of the real dashboard (aibi/picture.py).
 * It is as old as the picture; « Tiles » takes another. For a live dashboard, with filters and tooltips,
 * « Preview » opens Superset's own.
 *
 * NO FRAMEWORK, ON PURPOSE. The page already holds Superset's React, for the bar; a second React on one page
 * is a risk nobody needed to take for a workspace this size. Plain DOM, no build step, nothing to keep in
 * step with Superset's module federation.
 */
const BASE = '/extensions/sharpsight/bi';
const el = document.getElementById('sharpsight-studio');
const PROJECT = el.dataset.project;
const STEPS_SHOWN = 4;

const state = {
  payload: null,          // what the engine last said about this project
  spec: null,             // the specification being edited (the draft's, or the version's until one is saved)
  token: 'none',          // the draft this page is a change to (§50.3: a save against an older one is refused)
  selected: null,         // the slot whose inspector is open
  dirty: false,           // edited here and not yet saved
  saving: false,
  savedAt: null,
  problem: null,          // what to say at the top when something was refused
  tab: 'validation',
  validation: null,
  run: null,
  datasets: [],
  metrics: [],
  search: '',
  explorer: true,
  copilot: true,
  past: [],
  future: [],
};

/* ── talking to the engine, through Superset's own session ─────────────────────────────────────────────── */
let csrf = null;

async function token() {
  if (csrf) return csrf;
  if (el.dataset.csrf) return (csrf = el.dataset.csrf);              // rendered into the page by Flask
  try {                                                               // what an extension's panel is given
    csrf = await window.superset.authentication.getCSRFToken();
  } catch (error) {
    const answer = await fetch('/api/v1/security/csrf_token/', { credentials: 'same-origin' });
    csrf = (await answer.json()).result;
  }
  return csrf;
}

async function ask(method, path, body) {
  const headers = { Accept: 'application/json' };
  if (method !== 'GET') {
    headers['Content-Type'] = 'application/json';
    headers['X-CSRFToken'] = await token();
  }
  const answer = await fetch(BASE + path, {
    method, headers, credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let said = {};
  try { said = await answer.json(); } catch (error) { said = {}; }
  if (!answer.ok) {
    const problem = new Error(said.message || `refused (${answer.status})`);
    problem.status = answer.status;
    throw problem;
  }
  return said.result;
}

const api = {
  studio: () => ask('GET', `/projects/${encodeURIComponent(PROJECT)}/studio`),
  save: (spec, had) => ask('PUT', `/projects/${encodeURIComponent(PROJECT)}/draft`, { spec, token: had }),
  drop: () => ask('DELETE', `/projects/${encodeURIComponent(PROJECT)}/draft`),
  check: () => ask('POST', `/projects/${encodeURIComponent(PROJECT)}/draft/validate`),
  commit: comment => ask('POST', `/projects/${encodeURIComponent(PROJECT)}/draft/commit`, { comment }),
  tiles: () => ask('POST', `/projects/${encodeURIComponent(PROJECT)}/pictures`),
  run: id => ask('GET', `/runs/${encodeURIComponent(id)}`),
  datasets: () => ask('GET', '/datasets'),
  metrics: () => ask('GET', '/metrics'),
};

/* ── the specification being edited ───────────────────────────────────────────────────────────────────────
 * Every change goes through `change()`: it keeps one step of history, marks the page dirty and redraws. The
 * layout is the specification's own shape — rows of slots with a width in twelfths — because that shape is
 * Superset's (aibi/layout.py).
 */
function rows() {
  if (!state.spec.layout || !state.spec.layout.length) {
    state.spec.layout = state.payload.layout.map(row => ({
      row: row.row, height: row.height,
      slots: row.slots.filter(slot => component(slot.id)).map(slot => ({ id: slot.id, width: slot.width })),
    })).filter(row => row.slots.length);
  }
  return state.spec.layout;
}

const component = slot => (state.spec.components || []).find(c => c.slot === slot);
const described = slot => (state.payload.components || []).find(c => c.slot === slot) || {};
const slotOf = slot => (state.payload.template.slots || {})[slot] || {};
const minWidth = slot => {
  const least = state.payload.template.min_slot_width || {};
  const purpose = slotOf(slot).purpose;
  return least[purpose] === undefined ? (least.chart === undefined ? 1 : least.chart) : least[purpose];
};
const editable = () => state.payload.project.may_edit && !state.payload.project.held;

function change(what) {
  if (!editable()) return;
  state.past.push(JSON.stringify(state.spec));
  if (state.past.length > 50) state.past.shift();
  state.future = [];
  what();
  state.dirty = true;
  state.problem = null;
  draw();
  autosave();
}

function undo(which) {
  const from = which === 'undo' ? state.past : state.future;
  const to = which === 'undo' ? state.future : state.past;
  if (!from.length) return;
  to.push(JSON.stringify(state.spec));
  state.spec = JSON.parse(from.pop());
  state.dirty = true;
  draw();
  autosave();
}

let pending = null;
function autosave() {
  clearTimeout(pending);
  pending = setTimeout(save, 1200);
}

async function save() {
  if (!state.dirty || state.saving || !editable()) return;
  state.saving = true;
  draw();
  try {
    const said = await api.save(state.spec, state.token);
    state.token = said.token;
    state.savedAt = said.saved_at;
    state.dirty = false;
    state.payload.problems = said.problems;
    state.validation = null;
  } catch (problem) {
    state.problem = problem.status === 409
      ? `${problem.message} — nothing was saved. Reload to take what they saved.`
      : `not saved: ${problem.message}`;
  } finally {
    state.saving = false;
    draw();
  }
}

/* ── the moves a canvas may make ──────────────────────────────────────────────────────────────────────────
 * Each is checked again by the engine (aibi/layout.py), which is the only authority; these guards are here so
 * a person is not offered a move that will be refused.
 */
function moveWithin(slot, by) {
  change(() => {
    const row = rows().find(r => r.slots.some(s => s.id === slot));
    const at = row.slots.findIndex(s => s.id === slot);
    const to = at + by;
    if (to < 0 || to >= row.slots.length) return;
    [row.slots[at], row.slots[to]] = [row.slots[to], row.slots[at]];
  });
}

function moveRow(name, by) {
  change(() => {
    const all = rows();
    const at = all.findIndex(r => r.row === name);
    const to = at + by;
    if (to < 0 || to >= all.length) return;
    [all[at], all[to]] = [all[to], all[at]];
  });
}

function resize(slot, by) {
  const row = rows().find(r => r.slots.some(s => s.id === slot));
  const at = row.slots.findIndex(s => s.id === slot);
  const neighbour = row.slots[at + 1] || row.slots[at - 1];
  const mine = row.slots[at];
  if (by > 0 && !neighbour) {                       // alone on its row: it may grow into the empty columns
    const used = row.slots.reduce((sum, s) => sum + s.width, 0);
    if (used + by > state.payload.template.grid.columns) return;
  } else if (by > 0 && neighbour.width - by < minWidth(neighbour.id)) {
    return;
  }
  if (mine.width + by < minWidth(slot)) return;
  change(() => {
    mine.width += by;
    if (neighbour && by > 0) neighbour.width -= by;
    else if (neighbour && by < 0) neighbour.width -= by;
  });
}

function taller(name, by) {
  change(() => {
    const row = rows().find(r => r.row === name);
    row.height = Math.max(6, Math.min(120, row.height + by));
  });
}

function drop(slot, onto) {
  // A chart moved to another row. The engine refuses it when the row belongs to another section, so the
  // drop is refused here too, with the same reason — a section has an owner and filters of its own.
  const from = rows().find(r => r.slots.some(s => s.id === slot));
  if (!from || from.row === onto) return;
  const sections = state.payload.template.sections || [];
  const sectionOf = name => (sections.find(s => (s.rows || []).includes(name)) || {}).id;
  if (sectionOf(from.row) !== sectionOf(onto)) {
    state.problem = `${slot} belongs to section ${sectionOf(from.row)}: a chart does not leave the section it `
      + `is accountable to. With this template every section is one row, so a chart stays on its own row.`;
    draw();
    return;
  }
  change(() => {
    const row = rows().find(r => r.row === onto);
    const taken = from.slots.splice(from.slots.findIndex(s => s.id === slot), 1)[0];
    row.slots.push(taken);
  });
}

function field(slot, name, value) {
  change(() => {
    const target = component(slot);
    if (name === 'title') target.title = value || undefined;
    else if (name === 'description') target.description = value || undefined;
    else {
      target.options = target.options || {};
      if (value === '') delete target.options[name];
      else target.options[name] = name === 'row_limit' || name === 'bins' ? Number(value) : value;
    }
  });
}

/* ── drawing ──────────────────────────────────────────────────────────────────────────────────────────── */
const node = (tag, attrs = {}, ...children) => {
  const made = document.createElement(tag);
  Object.entries(attrs).forEach(([key, value]) => {
    if (value === undefined || value === null || value === false) return;
    if (key === 'on') Object.entries(value).forEach(([event, handler]) => made.addEventListener(event, handler));
    else if (key === 'style') made.setAttribute('style', value);
    else if (key === 'text') made.textContent = value;
    else if (key === 'html') made.innerHTML = value;                 // only ever from strings written here
    else made.setAttribute(key, value === true ? '' : String(value));
  });
  children.flat().forEach(child => child && made.appendChild(
    typeof child === 'string' ? document.createTextNode(child) : child));
  return made;
};
const button = (label, onClick, attrs = {}) =>
  node('button', { class: 'ss-btn', type: 'button', ...attrs, on: { click: onClick } }, label);

function draw() {
  const where = window.scrollY;
  el.textContent = '';
  el.appendChild(bar());
  if (state.problem) el.appendChild(node('div', { class: 'ss-callout', 'data-kind': 'bad',
    style: 'margin: 0 16px 12px', text: state.problem }));
  if (state.payload.project.held) {
    el.appendChild(node('div', { class: 'ss-callout', style: 'margin: 0 16px 12px', text:
      `This project stands at ${state.payload.project.gate}. This kit has one BI engine, so editing it here `
      + 'would rewrite the promoted dashboard in place: it is read-only until a next version is allowed.' }));
  }
  if (state.run) el.appendChild(runCallout());
  const panes = node('div', { class: 'ss-panes', 'data-explorer': state.explorer ? 'open' : 'closed',
    'data-copilot': state.copilot ? 'open' : 'closed' });
  if (state.explorer) panes.appendChild(explorer());
  panes.appendChild(canvas());
  if (state.copilot) panes.appendChild(copilot());
  el.appendChild(panes);
  el.appendChild(drawer());
  window.scrollTo(0, where);
}

function bar() {
  const project = state.payload.project;
  const saved = state.saving ? 'Saving…' : state.dirty ? 'Unsaved' : state.savedAt ? 'Draft saved' : 'No draft';
  return node('div', { class: 'ss-bar' },
    node('div', { class: 'ss-crumb' }, node('span', { text: 'SharpSight Studio / ' }),
      project.title || project.project_id, node('span', { text: ` / ${project.dashboard_title}` })),
    node('span', { class: 'ss-tag', 'data-kind': 'env', text: project.environment }),
    project.version && node('span', { class: 'ss-tag', text: `v${project.version}` }),
    project.gate && node('span', { class: 'ss-tag', 'data-kind': `gate-${project.gate}`, text: project.gate }),
    node('span', { class: 'ss-tag', 'data-kind': state.dirty ? 'dirty' : 'clean', text: saved }),
    node('span', { class: 'ss-spacer' }),
    button('↶', () => undo('undo'), { title: 'Undo', disabled: !state.past.length }),
    button('↷', () => undo('redo'), { title: 'Redo', disabled: !state.future.length }),
    button('Tiles', refreshTiles, { title: 'Take the pictures again, from the real dashboard' }),
    button('Preview', () => window.open(project.dashboard, '_blank'), { disabled: !project.dashboard,
      title: 'Superset draws the dashboard itself: filters, tooltips, cross-filters' }),
    button('Validate', validate),
    button('Save draft', save, { disabled: !state.dirty || !editable() }),
    button('Commit version', commit, { 'data-weight': 'primary',
      disabled: !editable() || !state.savedAt || state.dirty }),
    button(state.explorer ? '◧' : '▢', () => { state.explorer = !state.explorer; draw(); },
      { title: 'The explorer' }),
    button(state.copilot ? '◨' : '▢', () => { state.copilot = !state.copilot; draw(); },
      { title: 'The copilot' }));
}

/* the explorer (§47.3) and the outline (§8): the project's own charts, then the governed assets */
function explorer() {
  const match = text => !state.search || String(text).toLowerCase().includes(state.search.toLowerCase());
  const tree = node('ul', { class: 'ss-tree' });
  const sections = state.payload.template.sections || [];
  tree.appendChild(node('h3', { text: 'Outline' }));
  sections.forEach(section => {
    const slots = rows().filter(row => (section.rows || []).includes(row.row))
      .flatMap(row => row.slots.map(slot => slot.id));
    if (!slots.length) return;
    tree.appendChild(node('li', { style: 'color: var(--ss-muted)' },
      node('span', { text: section.title || section.id })));
    slots.filter(slot => match(title(slot))).forEach(slot => tree.appendChild(node('li',
      { 'aria-selected': state.selected === slot, style: 'padding-left: 14px' },
      node('button', { type: 'button', text: title(slot), on: { click: () => select(slot) } }),
      node('span', { class: 'ss-cert', 'data-certified': described(slot).governance
        && described(slot).governance.metric_certified, text: '●' }))));
  });
  tree.appendChild(node('h3', { text: `Datasets (${state.datasets.length})` }));
  state.datasets.filter(d => match(d.id) || match(d.name)).slice(0, 40).forEach(dataset =>
    tree.appendChild(node('li', {}, node('span', { text: dataset.id }),
      node('span', { class: 'ss-cert', 'data-certified': dataset.certified, text: dataset.certified ? '✓' : '○' }))));
  tree.appendChild(node('h3', { text: `Metrics (${state.metrics.length})` }));
  state.metrics.filter(m => match(m.id) || match(m.name)).slice(0, 40).forEach(metric =>
    tree.appendChild(node('li', {}, node('span', { text: metric.id }),
      node('span', { class: 'ss-cert', 'data-certified': metric.certified, text: metric.certified ? '✓' : '○' }))));

  return node('section', { class: 'ss-card ss-pane' },
    node('header', {}, 'Project'),
    node('div', { class: 'ss-body' },
      node('input', { class: 'ss-search', placeholder: 'Search assets…', value: state.search,
        on: { input: event => { state.search = event.target.value; draw(); } } }),
      tree,
      node('p', { class: 'ss-note', style: 'margin-top: 12px', text:
        'A chart is added to a project by a request to the engine, not from this list: a project starts with a '
        + 'request, and every chart it holds is a governed metric on a governed dataset.' })));
}

const title = slot => {
  const own = component(slot) || {};
  return own.title || described(slot).title || slot;
};

function select(slot) {
  state.selected = state.selected === slot ? null : slot;
  draw();
}

/* the canvas (§47.4): the rows in force, each tile a photograph of the real chart */
function canvas() {
  const sections = state.payload.template.sections || [];
  const canvasNode = node('div', { class: 'ss-canvas' });
  const announced = new Set();
  rows().forEach((row, index) => {
    const section = sections.find(s => (s.rows || []).includes(row.row));
    if (section && section.title && !announced.has(section.id)) {
      announced.add(section.id);
      canvasNode.appendChild(node('div', { class: 'ss-section-title' }, section.title));
    }
    canvasNode.appendChild(rowBar(row, index));
    canvasNode.appendChild(rowNode(row));
  });
  if (!rows().length) {
    canvasNode.appendChild(node('p', { class: 'ss-note', text: 'This project has no chart on its layout.' }));
  }
  return node('section', { class: 'ss-card', style: 'padding: 6px' },
    node('header', {}, `Canvas — ${state.payload.layout_of === 'specification'
      ? 'the layout this project placed' : `the template's own layout (${state.payload.template.id})`}`,
      node('span', { class: 'ss-spacer' }),
      node('span', { class: 'ss-note', text: tilesTaken() })),
    node('div', { class: 'ss-body', style: 'padding: 8px' }, canvasNode));
}

function tilesTaken() {
  const taken = state.payload.pictures;
  if (!taken) return 'no tiles yet — « Tiles » photographs the dashboard';
  const missing = (taken.missing || []).length;
  return `tiles from ${new Date(taken.taken_at).toLocaleTimeString()}`
    + (missing ? ` · ${missing} chart${missing > 1 ? 's' : ''} not in the picture` : '');
}

function rowBar(row, index) {
  const used = row.slots.reduce((sum, slot) => sum + slot.width, 0);
  return node('div', { class: 'ss-rowbar' },
    node('span', { text: `${row.row} · ${used}/${state.payload.template.grid.columns} columns · height ${row.height}` }),
    node('span', { class: 'ss-spacer' }),
    editable() && node('button', { type: 'button', title: 'Move this row up', text: '↑',
      on: { click: () => moveRow(row.row, -1) } }),
    editable() && node('button', { type: 'button', title: 'Move this row down', text: '↓',
      on: { click: () => moveRow(row.row, 1) } }),
    editable() && node('button', { type: 'button', title: 'Shorter', text: '–',
      on: { click: () => taller(row.row, -4) } }),
    editable() && node('button', { type: 'button', title: 'Taller', text: '+',
      on: { click: () => taller(row.row, 4) } }));
}

function rowNode(row) {
  const made = node('div', { class: 'ss-row', 'data-row': row.row, on: {
    dragover: event => { event.preventDefault(); made.dataset.over = 'true'; },
    dragleave: () => { delete made.dataset.over; },
    drop: event => {
      event.preventDefault();
      delete made.dataset.over;
      drop(event.dataTransfer.getData('text/plain'), row.row);
    },
  } });
  row.slots.forEach(slot => made.appendChild(tile(slot, row)));
  return made;
}

function tile(slot, row) {
  const taken = state.payload.pictures;
  const shot = taken && taken.slots && taken.slots[slot.id];
  const source = `${BASE}/projects/${encodeURIComponent(PROJECT)}/picture/${encodeURIComponent(slot.id)}`
    + `?v=${encodeURIComponent((taken && taken.taken_at) || '0')}`;
  const height = Math.max(70, row.height * 8 - 34);
  return node('article', { class: 'ss-tile', style: `--w: ${slot.width}`, 'data-slot': slot.id,
    'aria-selected': state.selected === slot.id, draggable: editable(), on: {
      click: () => select(slot.id),
      dragstart: event => { event.dataTransfer.setData('text/plain', slot.id); },
    } },
    node('header', {}, node('strong', { text: title(slot.id) }),
      node('span', { class: 'ss-spacer' }),
      node('span', { class: 'ss-slot', text: `${slot.id} · ${slot.width}/12` })),
    shot
      ? node('img', { class: 'ss-shot', src: source, alt: `${title(slot.id)}, as the dashboard drew it`,
          style: `height: ${height}px` })
      : node('div', { class: 'ss-shot-missing', style: `height: ${height}px` },
          taken ? 'this chart was not in the picture' : 'no picture taken yet'),
    editable() && node('div', { class: 'ss-grip', title: 'Drag to resize', on: { pointerdown: grip(slot) } }));
}

function grip(slot) {
  // The drag itself changes nothing: it moves two tiles' widths in the page, and the specification is
  // changed once, on release. Otherwise a drag of three columns would be three steps to undo, and the
  // canvas would be rebuilt under the pointer at every pixel.
  return event => {
    event.preventDefault();
    event.stopPropagation();
    const tile = event.target.closest('.ss-tile');
    const row = event.target.closest('.ss-row');
    const line = rows().find(r => r.slots.some(s => s.id === slot.id));
    const at = line.slots.findIndex(s => s.id === slot.id);
    const other = line.slots[at + 1] || line.slots[at - 1];
    const neighbour = other && row.querySelector(`[data-slot="${other.id}"]`);
    const columns = state.payload.template.grid.columns;
    const column = row.getBoundingClientRect().width / columns;
    const from = event.clientX;
    const was = slot.width;
    const wasOther = other ? other.width : 0;
    const spare = columns - line.slots.reduce((sum, s) => sum + s.width, 0);
    let wanted = was;
    const move = inner => {
      const by = Math.round((inner.clientX - from) / column);
      const most = other ? was + wasOther - minWidth(other.id) : was + spare;
      wanted = Math.max(minWidth(slot.id), Math.min(most, was + by));
      tile.style.setProperty('--w', wanted);
      if (neighbour) neighbour.style.setProperty('--w', wasOther - (wanted - was));
    };
    const stop = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', stop);
      if (wanted !== was) {
        change(() => {
          slot.width = wanted;
          if (other) other.width = wasOther - (wanted - was);
        });
      }
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', stop);
  };
}

/* the inspector (§47.5) and the copilot (§47.6) share the right-hand pane */
function copilot() {
  return node('div', { class: 'ss-pane', style: 'display: grid; gap: 16px' }, inspector(), assistant());
}

function inspector() {
  if (!state.selected) {
    return node('section', { class: 'ss-card' }, node('header', {}, 'Inspector'),
      node('div', { class: 'ss-body ss-note' }, 'Select a chart on the canvas.'));
  }
  const slot = state.selected;
  const own = component(slot) || {};
  const said = described(slot);
  const governance = said.governance || {};
  const row = rows().find(r => r.slots.some(s => s.id === slot));
  const place = row.slots.find(s => s.id === slot);
  return node('section', { class: 'ss-card' },
    node('header', {}, `Inspector — ${slot}`),
    node('div', { class: 'ss-body' },
      node('label', { class: 'ss-field' }, node('span', { text: 'Title' }),
        node('input', { value: own.title || '', maxlength: 80, disabled: !editable(),
          on: { change: event => field(slot, 'title', event.target.value) } })),
      node('label', { class: 'ss-field' }, node('span', { text: 'What it is for' }),
        node('textarea', { rows: 2, maxlength: 300, disabled: !editable(),
          on: { change: event => field(slot, 'description', event.target.value) } }, own.description || '')),
      node('div', { style: 'display: grid; grid-template-columns: 1fr 1fr; gap: 10px' },
        node('label', { class: 'ss-field' }, node('span', { text: 'Top N' }),
          node('input', { type: 'number', min: 1, max: 1000, value: (own.options || {}).row_limit || '',
            disabled: !editable(), on: { change: event => field(slot, 'row_limit', event.target.value) } })),
        node('label', { class: 'ss-field' }, node('span', { text: 'Time grain' }),
          select_('time_grain', (own.options || {}).time_grain, ['', 'P1D', 'P1W', 'P1M'], slot)),
        node('label', { class: 'ss-field' }, node('span', { text: 'Order' }),
          select_('sort', (own.options || {}).sort, ['', 'desc', 'asc'], slot)),
        node('label', { class: 'ss-field' }, node('span', { text: 'Numbers' }),
          select_('number_format', (own.options || {}).number_format, ['', 'default', 'compact'], slot))),
      node('div', { class: 'ss-rowbar', style: 'margin: 6px 0 12px' },
        node('span', { text: `Place: row ${row.row}, ${place.width}/12` }),
        node('span', { class: 'ss-spacer' }),
        editable() && node('button', { type: 'button', title: 'Earlier on the row', text: '←',
          on: { click: () => moveWithin(slot, -1) } }),
        editable() && node('button', { type: 'button', title: 'Later on the row', text: '→',
          on: { click: () => moveWithin(slot, 1) } }),
        editable() && node('button', { type: 'button', title: 'Narrower', text: '–',
          on: { click: () => resize(slot, -1) } }),
        editable() && node('button', { type: 'button', title: 'Wider', text: '+',
          on: { click: () => resize(slot, 1) } })),
      node('h3', { style: 'font-size: 11px; text-transform: uppercase; letter-spacing: .04em;'
        + 'color: var(--ss-muted); margin-bottom: 6px', text: 'Governance' }),
      node('ul', { class: 'ss-gov' },
        pair('Chart', `${said.type || own.type} · ${said.purpose || ''}`),
        pair('Metric', `${own.metric} ${governance.metric_certified ? '✓ certified' : '⚠ not certified'}`),
        pair('Definition', governance.metric_definition || '—'),
        pair('Metric owner', governance.metric_owner || '—'),
        pair('Dataset', `${own.dataset || '—'} ${governance.dataset_certified ? '✓' : '⚠'}`),
        pair('Dataset owner', governance.dataset_owner || '—'),
        pair('Domain', governance.dataset_domain || '—'),
        pair('Grain', (governance.dataset_grain || []).join(', ') || '—'),
        pair('Section', said.section || '—'),
        pair('Accepts', (said.accepts || []).join(', ') || '—'),
        pair('Mandatory', said.mandatory ? 'yes — it cannot be removed' : 'no')),
      node('p', { class: 'ss-note', style: 'margin-top: 10px', text:
        'The metric, the dimension and the dataset are the registry’s, not this page’s: changing what a '
        + 'chart measures is a request to the engine, which checks the metric is valid on the dataset and that '
        + 'you are cleared for it.' })));
}

const pair = (name, value) => node('li', {}, node('span', { text: name }), node('span', { text: value }));

function select_(name, value, options, slot) {
  return node('select', { disabled: !editable(), on: { change: event => field(slot, name, event.target.value) } },
    options.map(option => node('option', { value: option, selected: (value || '') === option },
      option || '—')));
}

function assistant() {
  return node('section', { class: 'ss-card' },
    node('header', {}, 'SharpSight AI'),
    node('div', { class: 'ss-body' },
      node('textarea', { class: 'ss-search ss-chat', disabled: true, placeholder:
        'Add a 30-day trend for QoE score…' }),
      node('div', { style: 'display: flex; gap: 8px; margin-top: 8px' },
        button('Propose', () => {}, { disabled: true }),
        button('Apply', () => {}, { disabled: true }),
        button('Reject', () => {}, { disabled: true })),
      node('div', { class: 'ss-callout', 'data-kind': 'info', style: 'margin-top: 12px', text:
        'Not wired yet, on purpose: no language model has been chosen for this kit, which calls nobody '
        + '(aibi/llm.py is the seam). The engine’s planner is deterministic and a request is made on the '
        + 'command line — bash ai-bi.sh request. When a model is chosen, this panel answers with a proposal: '
        + 'a diff of the draft, what it touches, and Preview / Apply / Reject. It will never change the draft '
        + 'without that.' })));
}

/* the drawer (§47.7) */
function drawer() {
  const tabs = { validation: 'Validation', layout: 'Layout', run: 'Commit', about: 'What this is' };
  const body = node('div', { class: 'ss-body' });
  if (state.tab === 'validation') body.appendChild(validationTab());
  if (state.tab === 'layout') body.appendChild(layoutTab());
  if (state.tab === 'run') body.appendChild(runTab());
  if (state.tab === 'about') body.appendChild(aboutTab());
  return node('section', { class: 'ss-card ss-drawer' },
    node('header', {}, node('div', { class: 'ss-tabs' }, Object.entries(tabs).map(([key, label]) =>
      node('button', { type: 'button', 'aria-selected': state.tab === key, text: label,
        on: { click: () => { state.tab = key; draw(); } } })))),
    body);
}

function validationTab() {
  const problems = state.payload.problems || [];
  const made = node('div', {});
  if (problems.length) {
    made.appendChild(node('div', { class: 'ss-callout', 'data-kind': 'bad' },
      node('strong', { text: 'This layout cannot be committed:' }),
      node('ul', {}, problems.map(problem => node('li', { text: problem })))));
  }
  if (!state.validation) {
    made.appendChild(node('p', { class: 'ss-note', text:
      'Validate reads the draft against the registry and the template — the slots, the visualizations, the '
      + 'metrics, the datasets, and your clearance for each. It touches nothing in Superset.' }));
    return made;
  }
  const report = state.validation;
  made.appendChild(node('p', {}, node('strong', { text: report.summary.passed ? '✓ passed' : '✗ failed' }),
    ` — ${report.summary.checks} checks, ${report.summary.failures} failed, ${report.summary.warnings} warned `
    + `(of the ${report.of})`));
  made.appendChild(node('ul', { class: 'ss-checks' }, report.checks
    .filter(check => check.status !== 'pass' || !report.summary.failures)
    .map(check => node('li', { 'data-status': check.status },
      node('b', { text: check.status }),
      node('span', {}, node('span', { text: check.name }),
        check.subject && node('span', { class: 'ss-note', text: ` — ${check.subject}` }),
        check.detail && node('div', { class: 'ss-note', text: check.detail }))))));
  return made;
}

function layoutTab() {
  const lines = rows().map(row => `${row.row} (height ${row.height}): `
    + row.slots.map(slot => `${slot.id} ${slot.width}/12`).join(' · '));
  return node('div', {},
    node('p', { class: 'ss-note', text: state.payload.layout_of === 'specification'
      ? 'This project carries its own layout: a commit keeps it, and the next build draws it.'
      : 'This project has no layout of its own yet: it is drawn by its template. Move or resize anything and '
        + 'the layout becomes the project’s.' }),
    node('pre', { style: 'font: 12px/1.6 ui-monospace, monospace; white-space: pre-wrap', text: lines.join('\n') }),
    node('p', { class: 'ss-note', text: `Rows, widths in twelfths and heights in grid units — Superset's own `
      + `shape, which is why a layout written here and read back is the same layout.` }));
}

function runTab() {
  if (!state.run) {
    return node('p', { class: 'ss-note', text:
      'A commit makes the draft a version — through the same road as every other commit: the engine’s ten '
      + 'quality gates, the BI engine, a look at the dashboard in a browser, a git commit and a package. '
      + 'Nothing here is a shortcut past them, and nothing here approves: a reviewer does that elsewhere.' });
  }
  const run = state.run;
  return node('div', {},
    node('p', {}, node('strong', { text: `Run ${run.id} — ${run.status}` }),
      run.error ? node('div', { class: 'ss-callout', 'data-kind': 'bad', text: run.error }) : null),
    node('ul', { class: 'ss-checks' }, (run.steps || []).map(step => node('li', { 'data-status':
      step.status === 'failed' ? 'fail' : step.status === 'done' ? 'pass' : 'warn' },
      node('b', { text: step.status }), node('span', { text: step.key })))));
}

function aboutTab() {
  return node('div', { class: 'ss-note' },
    node('p', {}, 'This workspace edits a SPECIFICATION, not a dashboard. Superset draws every chart and '
      + 'every dashboard; the Studio says what they should be, and the engine decides whether that may '
      + 'become a version.'),
    node('p', {}, 'So: no second renderer, no second chart editor, no second SQL Lab — the brief’s own '
      + 'first rules. What the canvas may change is the layout within what the template allows, and the '
      + 'fields a specification carries (a title, a top N, a time grain, an order, a number format). '
      + 'Everything else about a chart — a colour, a legend, an ECharts option — is Superset’s own, and '
      + 'a commit sets it back: it has no place in a specification.'),
    node('p', {}, 'A tile is a photograph of the real chart, cut out of one picture of the real dashboard. '
      + 'For the live thing, Preview opens Superset’s own dashboard.'));
}

function runCallout() {
  const run = state.run;
  const done = (run.steps || []).filter(step => step.status === 'done' || step.status === 'skipped').length;
  const kind = run.status === 'failed' ? 'bad' : 'info';
  return node('div', { class: 'ss-callout', 'data-kind': kind, style: 'margin: 0 16px 12px' },
    run.status === 'running' ? node('span', { class: 'ss-wait' }) : null,
    ` ${run.status === 'running' ? `Committing — step ${done} of ${(run.steps || []).length}`
      : run.status === 'succeeded' ? `Committed: ${PROJECT} v${run.version || ''} awaits review`
      : `The commit failed: ${run.error || ''}`} `,
    run.status !== 'running' ? button('Reload', load, { style: 'margin-left: 8px' }) : null,
    run.status === 'running' ? node('span', { class: 'ss-note', text:
      (run.steps || []).filter(step => step.status === 'running').map(step => step.key).join(', ') }) : null);
}

/* ── the commands of the bar ──────────────────────────────────────────────────────────────────────────── */
async function validate() {
  state.tab = 'validation';
  if (state.dirty) await save();
  try {
    state.validation = await api.check();
  } catch (problem) {
    state.problem = problem.message;
  }
  draw();
}

async function refreshTiles() {
  state.problem = null;
  try {
    state.payload.pictures = await api.tiles();
  } catch (problem) {
    state.problem = `the tiles could not be taken: ${problem.message}`;
  }
  draw();
}

async function commit() {
  const comment = window.prompt('What does this version change?', '');
  if (comment === null) return;
  try {
    const started = await api.commit(comment);
    state.run = { id: started.run_id, status: 'running', steps: [] };
    state.tab = 'run';
    draw();
    watch(started.run_id);
  } catch (problem) {
    state.problem = problem.message;
    draw();
  }
}

function watch(id) {
  const timer = setInterval(async () => {
    try {
      state.run = await api.run(id);
    } catch (problem) {
      state.run = { id, status: 'failed', error: problem.message, steps: [] };
    }
    if (state.run.status !== 'running') {
      clearInterval(timer);
      if (state.run.status === 'succeeded') await load();
    }
    draw();
  }, 3000);
}

/* ── opening ──────────────────────────────────────────────────────────────────────────────────────────── */
async function load() {
  const payload = await api.studio();
  state.payload = payload;
  state.spec = JSON.parse(JSON.stringify(payload.spec));
  state.token = payload.token;
  state.savedAt = payload.draft ? payload.draft.saved_at : null;
  state.dirty = false;
  state.past = [];
  state.future = [];
  draw();
}

async function start() {
  try {
    await load();
  } catch (problem) {
    el.textContent = '';
    el.appendChild(node('div', { class: 'ss-callout', 'data-kind': 'bad', style: 'margin: 16px', text:
      `This project could not be opened: ${problem.message}` }));
    return;
  }
  // the governed lists, after the workspace is on the screen: the canvas does not wait for them
  try { state.datasets = await api.datasets(); } catch (problem) { state.datasets = []; }
  try { state.metrics = await api.metrics(); } catch (problem) { state.metrics = []; }
  draw();
}

window.addEventListener('beforeunload', event => {
  if (state.dirty) { event.preventDefault(); event.returnValue = ''; }
});

start();
export { state, rows, resize, drop, moveRow, moveWithin, draw, load };
