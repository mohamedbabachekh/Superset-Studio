/* Drawing the workspace: the command bar, three panes and a drawer (the brief's §47).
 *
 * Everything here reads `state` and draws it; nothing here decides what may be changed — state.js offers the
 * moves, and the engine judges them. A redraw rebuilds the panes; the two places where that would be felt
 * are kept out of it (a resize drag moves CSS variables until it is released, a text field commits on
 * `change`), so there is no virtual DOM to maintain for a workspace of a dozen tiles.
 */
import * as act from './state.js';
import { state } from './state.js';
import { KEYS, button, close, field, icon, modal, node, pair, select, tag, theme, themeNow, toast }
  from './ui.js';

export function draw(el, client, commands) {
  const where = window.scrollY;
  el.textContent = '';
  if (state.loading) {
    el.appendChild(skeleton());
    return;
  }
  const barNode = bar(commands);
  el.appendChild(barNode);
  // the drawers of a narrow screen start below the bar, whose height depends on how far it wraps
  document.documentElement.style.setProperty('--ss-bar-h', `${barNode.offsetHeight || 56}px`);
  banners(el, commands);
  const panes = node('div', { class: 'ss-panes', 'data-explorer': state.explorer ? 'open' : 'closed',
    'data-copilot': state.copilot ? 'open' : 'closed' });
  if (state.explorer) panes.appendChild(explorer(commands));
  panes.appendChild(canvas(client, commands));
  if (state.copilot) panes.appendChild(rightPane(client, commands));
  el.appendChild(panes);
  el.appendChild(drawer(commands));
  window.scrollTo(0, where);
}

function skeleton() {
  return node('div', { class: 'ss-panes', style: 'padding-top: 24px' },
    node('div', { class: 'ss-skeleton', style: 'height: 320px' }),
    node('div', { class: 'ss-skeleton', style: 'height: 460px' }),
    node('div', { class: 'ss-skeleton', style: 'height: 320px' }));
}

/* ── the command bar (§47.2) ─────────────────────────────────────────────────────────────────────────────── */
const AGO = at => {
  if (!at) return null;
  const seconds = Math.max(0, (Date.now() - new Date(at).getTime()) / 1000);
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  return new Date(at).toLocaleTimeString();
};

function bar(commands) {
  const project = state.payload.project;
  const saved = state.saving ? 'Saving…' : state.dirty ? 'Unsaved changes'
    : state.savedAt ? `Draft saved ${AGO(state.savedAt)}` : 'No draft';
  const gate = { PROD: 'good', UAT: 'good', APPROVED_FOR_UAT: 'good', APPROVED_FOR_PROD: 'good',
    READY_FOR_REVIEW: 'warn', VALIDATION_FAILED: 'bad', UAT_FAILED: 'bad' }[project.gate];
  return node('div', { class: 'ss-bar' },
    node('div', { class: 'ss-crumb' },
      node('span', { class: 'ss-dim', text: 'SharpSight Studio' }), node('span', { class: 'ss-dim', text: '/' }),
      node('span', { text: project.title || project.project_id }), node('span', { class: 'ss-dim', text: '/' }),
      node('span', { text: project.dashboard_title })),
    tag(project.environment, 'env'),
    project.version && tag(`v${project.version}`),
    project.gate && tag(project.gate.replace(/_/g, ' ').toLowerCase(), gate),
    tag(saved, state.dirty ? 'warn' : state.savedAt ? 'good' : undefined),
    node('span', { class: 'ss-spacer' }),
    node('div', { class: 'ss-group' },
      button('↶', () => act.undo('undo'), { 'data-weight': 'quiet', title: 'Undo (⌘Z)',
        'aria-label': 'Undo', disabled: !state.past.length }),
      button('↷', () => act.undo('redo'), { 'data-weight': 'quiet', title: 'Redo (⇧⌘Z)',
        'aria-label': 'Redo', disabled: !state.future.length })),
    node('div', { class: 'ss-group' },
      button('Add chart', commands.gallery, { disabled: !act.editable() || !state.payload.free_slots.length,
        title: state.payload.free_slots.length ? 'Put a governed chart on a free slot of the template (a)'
          : 'Every slot of this template already holds a chart' }),
      button('Tiles', commands.tiles, { title: 'Photograph the real dashboard again' }),
      button('Preview', () => window.open(project.dashboard, '_blank', 'noopener'),
        { disabled: !project.dashboard, title: 'Open the live dashboard in Superset: filters, tooltips, cross-filters' })),
    node('div', { class: 'ss-group' },
      button('Validate', commands.validate, { title: 'Check the draft against the registry and the template (v)' }),
      button('Save draft', act.save, { disabled: !state.dirty || !act.editable(),
        title: state.dirty ? 'Save now (⌘S)' : 'Nothing has changed since the last save' }),
      button('Export .ssbi', commands.export_, {
        disabled: !(state.payload.versions || []).some(version => version.package),
        title: (state.payload.versions || []).some(version => version.package)
          ? 'The immutable package of the latest version — what a deployment service imports elsewhere'
          : 'No version of this project has a package yet' }),
      button('Commit version', commands.commit, { 'data-weight': 'primary',
        disabled: !act.editable() || !state.savedAt || state.dirty,
        title: !act.editable() ? 'This project is read-only here'
          : state.dirty ? 'Save the draft first' : !state.savedAt ? 'There is no draft to commit'
            : 'Make this draft a version, through the engine’s ten gates' })),
    node('div', { class: 'ss-group' },
      button('⌘K', commands.palette, { 'data-weight': 'quiet', title: 'Every command, by name (⌘K)',
        'aria-label': 'Command palette' }),
      button('◧', () => { state.explorer = !state.explorer; commands.redraw(); },
        { 'data-weight': 'quiet', 'aria-pressed': state.explorer, 'aria-label': 'The explorer', title: 'The explorer' }),
      button('◨', () => { state.copilot = !state.copilot; commands.redraw(); },
        { 'data-weight': 'quiet', 'aria-pressed': state.copilot, 'aria-label': 'The copilot', title: 'The copilot' }),
      button('◐', () => { theme(themeNow() === 'dark' ? 'light' : 'dark'); commands.redraw(); },
        { 'data-weight': 'quiet', 'aria-pressed': themeNow() === 'dark', 'aria-label': 'Dark or light',
          title: 'Dark or light — kept in this browser' }),
      button('?', commands.help, { 'data-weight': 'quiet', title: 'Keyboard shortcuts (?)' })));
}

function banners(el, commands) {
  const project = state.payload.project;
  if (project.held) {
    el.appendChild(node('div', { class: 'ss-callout', style: 'margin: 0 16px 12px', text:
      `This project stands at ${project.gate}. This kit has one BI engine, so editing it here would rewrite `
      + 'the promoted dashboard in place: it is read-only until a next version is allowed.' }));
  } else if (!project.may_edit) {
    el.appendChild(node('div', { class: 'ss-callout', style: 'margin: 0 16px 12px', text:
      'You may read this project and not change it: that is what the registry says of your role.' }));
  }
  if ((state.payload.problems || []).length) {
    el.appendChild(node('div', { class: 'ss-callout', 'data-kind': 'bad', style: 'margin: 0 16px 12px' },
      node('strong', { text: 'This layout cannot be committed as it is: ' }),
      state.payload.problems.join('; ')));
  }
  if (state.run) el.appendChild(runBanner(commands));
}

function runBanner(commands) {
  const run = state.run;
  const steps = run.steps || [];
  const done = steps.filter(step => step.status === 'done' || step.status === 'skipped').length;
  const running = steps.filter(step => step.status === 'running').map(step => step.title || step.key).join(', ');
  const kind = run.status === 'failed' ? 'bad' : run.status === 'succeeded' ? 'good' : 'info';
  return node('div', { class: 'ss-callout', 'data-kind': kind, style: 'margin: 0 16px 12px' },
    run.status === 'running' ? node('span', { class: 'ss-wait' }) : null,
    ' ' + (run.status === 'running' ? `Committing — step ${done} of ${steps.length}${running ? `: ${running}` : ''}`
      : run.status === 'succeeded' ? 'Committed. The version awaits review.'
        : `The commit failed: ${run.error || ''}`) + ' ',
    run.status !== 'running' ? button('Reload', commands.reload, { style: 'margin-left: 8px' }) : null);
}

/* ── the explorer and the outline (§47.3, §8) ─────────────────────────────────────────────────────────────── */
function explorer(commands) {
  const match = text => !state.search || String(text).toLowerCase().includes(state.search.toLowerCase());
  const tree = node('ul', { class: 'ss-tree' });

  tree.appendChild(node('h3', {}, node('span', { text: 'Outline' }),
    node('span', { class: 'ss-faint', text: `${(state.spec.components || []).length} charts` })));
  (state.payload.template.sections || []).forEach(section => {
    const slots = act.rows().filter(row => (section.rows || []).includes(row.row))
      .flatMap(row => row.slots.map(place => place.id));
    if (!slots.length) return;
    tree.appendChild(node('li', { class: 'ss-section-head' }, section.title || section.id));
    slots.filter(slot => match(act.title(slot)) || match(slot)).forEach(slot => {
      const governance = act.described(slot).governance || {};
      tree.appendChild(node('li', { class: 'ss-sub', 'aria-selected': state.selected === slot },
        node('button', { class: 'ss-name', type: 'button', text: act.title(slot),
          on: { click: () => commands.select(slot) } }),
        node('span', { class: 'ss-cert', 'data-certified': Boolean(governance.metric_certified),
          title: governance.metric_certified ? 'certified metric' : 'the metric is not certified', text: '●' })));
    });
  });
  const parked = (state.spec.sections || []).flatMap(section => section.parked || []);
  if (parked.length) {
    tree.appendChild(node('h3', {}, 'Parked'));
    parked.forEach(component => tree.appendChild(node('li', { class: 'ss-sub' },
      node('span', { class: 'ss-name', text: component.title || component.slot }),
      node('span', { class: 'ss-faint', text: 'off the dashboard' }))));
  }
  if (state.payload.free_slots.length) {
    tree.appendChild(node('h3', {}, 'Free slots'));
    state.payload.free_slots.filter(free => match(free.slot) || match(free.purpose)).forEach(free =>
      tree.appendChild(node('li', { class: 'ss-sub' },
        node('button', { class: 'ss-name', type: 'button', text: `${free.slot} · ${free.purpose}`,
          on: { click: () => commands.gallery(free.slot) } }),
        node('span', { class: 'ss-faint', text: free.mandatory ? 'mandatory' : 'add' }))));
  }

  const library = state.payload.visualizations || [];
  if (library.length) {
    tree.appendChild(node('h3', {}, node('span', { text: 'Components' }),
      node('span', { class: 'ss-faint', text: `${library.filter(kind => kind.status === 'available').length}` })));
    const byPurpose = {};
    // a kind no slot of this template accepts still belongs in the library, under « elsewhere »: a library
    // that hides what cannot be used here would be hiding the reason it cannot
    library.forEach(kind => ((kind.purposes || []).length ? kind.purposes : ['elsewhere']).forEach(purpose => {
      byPurpose[purpose] = byPurpose[purpose] || [];
      byPurpose[purpose].push(kind);
    }));
    Object.entries(byPurpose).forEach(([purpose, kinds]) => {
      const shown = kinds.filter(kind => match(kind.id) || match(purpose));
      if (!shown.length) return;
      tree.appendChild(node('li', { class: 'ss-section-head' }, purpose));
      tree.appendChild(node('li', { class: 'ss-shelf' }, node('div', { class: 'ss-cards' },
        shown.map(kind => {
          const free = (state.payload.free_slots || []).filter(slot => (slot.accepts || []).includes(kind.id));
          const ready = kind.status === 'available' && free.length && act.editable();
          const state_ = kind.status !== 'available' ? 'absent' : free.length ? 'free' : 'full';
          return node('button', { class: 'ss-kind', type: 'button', 'data-kind': kind.id,
            'data-state': state_, draggable: ready, title: ready
              ? `Drag onto a row, or click: it goes on ${free.map(slot => slot.slot).join(' or ')}`
              : kind.status !== 'available'
                ? `${kind.id} is approved but not installed in this BI engine: a dashboard asking for it is refused`
                : `every slot that accepts a ${kind.id} already holds a chart`,
            on: {
              dragstart: event => { event.dataTransfer.setData('text/plain', `type:${kind.id}`); },
              click: () => (ready ? commands.gallery(free[0].slot, kind.id)
                : toast(kind.status !== 'available'
                  ? `${kind.id} is approved but not installed in this BI engine.`
                  : `Every slot that accepts a ${kind.id} already holds a chart.`, { kind: 'bad' })),
            } },
          icon(kind.id, { class: 'ss-kind-icon' }),
          node('span', { class: 'ss-kind-name', text: kind.id }),
          node('span', { class: 'ss-kind-state', text: state_ === 'absent' ? 'not installed'
            : state_ === 'free' ? `${free.length} free` : 'full' }));
        }))));
    });
  }

  tree.appendChild(node('h3', {}, node('span', { text: 'Datasets' }),
    node('span', { class: 'ss-faint', text: String(state.datasets.length) })));
  state.datasets.filter(d => match(d.id) || match(d.name)).slice(0, 30).forEach(dataset =>
    tree.appendChild(node('li', { class: 'ss-sub', title: dataset.description || '' },
      node('span', { class: 'ss-name', text: dataset.id }),
      node('span', { class: 'ss-cert', 'data-certified': Boolean(dataset.certified),
        text: dataset.certified ? '✓' : '○' }))));
  tree.appendChild(node('h3', {}, node('span', { text: 'Metrics' }),
    node('span', { class: 'ss-faint', text: String(state.metrics.length) })));
  state.metrics.filter(m => match(m.id) || match(m.name)).slice(0, 30).forEach(metric =>
    tree.appendChild(node('li', { class: 'ss-sub', title: metric.documentation || '' },
      node('span', { class: 'ss-name', text: metric.id }),
      node('span', { class: 'ss-cert', 'data-certified': Boolean(metric.certified),
        text: metric.certified ? '✓' : '○' }))));

  return node('section', { class: 'ss-card ss-pane' },
    node('header', {}, 'Project'),
    node('div', { class: 'ss-body' },
      node('input', { class: 'ss-search', id: 'ss-search', placeholder: 'Search assets…  (/)',
        value: state.search, 'aria-label': 'Search assets',
        on: { input: event => { state.search = event.target.value; commands.redraw(); } } }),
      tree));
}

/* ── the canvas (§47.4) ──────────────────────────────────────────────────────────────────────────────────── */
function canvas(client, commands) {
  const sections = state.payload.template.sections || [];
  const inner = node('div', { class: 'ss-canvas' });
  const announced = new Set();
  const drawn = act.rows();
  drawn.forEach(row => {
    const section = sections.find(s => (s.rows || []).includes(row.row));
    if (section && section.title && !announced.has(section.id)) {
      announced.add(section.id);
      inner.appendChild(node('div', { class: 'ss-section-title' }, section.title));
    }
    inner.appendChild(node('div', { class: 'ss-rowblock' }, rowBar(row, commands), rowNode(row, client, commands)));
  });
  if (!drawn.length) {
    inner.appendChild(node('div', { class: 'ss-callout', 'data-kind': 'info' },
      'This project has no chart on its layout. Add one from a free slot of its template.'));
  }
  // Zoom and the template's own viewports (§47.4). The canvas is a grid of twelfths, so « responsive
  // preview » is the width the dashboard will actually be read at — the template declares them — and the
  // zoom is how much of it fits on this screen.
  const zoom = state.zoom || 1;
  const viewport = state.viewport || 0;
  const frame = node('div', { class: 'ss-frame', style: viewport
    ? `width: ${viewport}px; transform: scale(${zoom}); `
    : `width: ${Math.round(100 / zoom)}%; transform: scale(${zoom});` }, inner);
  const viewports = [[0, 'Fit']].concat((state.payload.template.viewports || [])
    .map(one => [one.width, `${one.name} ${one.width}`]));
  return node('section', { class: 'ss-card', style: 'padding: 6px' },
    node('header', {},
      node('span', { text: state.payload.layout_of === 'specification'
        ? 'Canvas — the layout this project placed' : `Canvas — the template's own layout` }),
      node('span', { class: 'ss-spacer' }),
      node('span', { class: 'ss-faint', text: tilesTaken() }),
      select(String(viewport), viewports.map(([width, label]) => [String(width), label]),
        value => { state.viewport = Number(value); commands.redraw(); },
        { class: 'ss-mini', 'aria-label': 'The width it is read at' }),
      node('button', { class: 'ss-btn', 'data-weight': 'quiet', type: 'button', text: '−',
        'aria-label': 'Zoom out', title: 'Zoom out',
        on: { click: () => { state.zoom = Math.max(0.5, zoom - 0.1); commands.redraw(); } } }),
      node('span', { class: 'ss-faint', style: 'min-width: 34px; text-align: center',
        text: `${Math.round(zoom * 100)}%` }),
      node('button', { class: 'ss-btn', 'data-weight': 'quiet', type: 'button', text: '+',
        'aria-label': 'Zoom in', title: 'Zoom in',
        on: { click: () => { state.zoom = Math.min(1, zoom + 0.1); commands.redraw(); } } })),
    node('div', { class: 'ss-body ss-viewport', style: 'padding: 8px' }, frame));
}

function tilesTaken() {
  const taken = state.payload.pictures;
  if (!taken) return 'no tiles yet — « Tiles » photographs the dashboard';
  const missing = (taken.missing || []).length;
  return `tiles taken ${AGO(taken.taken_at)}`
    + (missing ? ` · ${missing} chart${missing > 1 ? 's' : ''} not in the picture` : '');
}

function rowBar(row, commands) {
  const used = row.slots.reduce((sum, place) => sum + place.width, 0);
  const columns = state.payload.template.grid.columns;
  return node('div', { class: 'ss-rowbar' },
    node('span', { text: `${row.row} · ${used}/${columns} columns · height ${row.height}` }),
    node('span', { class: 'ss-spacer' }),
    act.editable() && node('button', { type: 'button', title: 'Move this row up', 'aria-label': `Move row ${row.row} up`,
      text: '↑', on: { click: () => act.moveRow(row.row, -1) } }),
    act.editable() && node('button', { type: 'button', title: 'Move this row down', 'aria-label': `Move row ${row.row} down`,
      text: '↓', on: { click: () => act.moveRow(row.row, 1) } }),
    act.editable() && node('button', { type: 'button', title: 'Shorter', 'aria-label': `Row ${row.row} shorter`,
      text: '–', on: { click: () => act.taller(row.row, -4) } }),
    act.editable() && node('button', { type: 'button', title: 'Taller', 'aria-label': `Row ${row.row} taller`,
      text: '+', on: { click: () => act.taller(row.row, 4) } }));
}

function rowNode(row, client, commands) {
  const made = node('div', { class: 'ss-row', 'data-row': row.row, on: {
    dragover: event => { event.preventDefault(); made.dataset.over = 'true'; },
    dragleave: () => { delete made.dataset.over; },
    drop: event => {
      event.preventDefault();
      delete made.dataset.over;
      const kind = act.drop(event.dataTransfer.getData('text/plain'), row.row);
      if (!kind) return;
      const free = act.slotFor(kind, row.row);
      if (!free) {
        toast(`This row's section has no free slot that accepts a ${kind}.`, { kind: 'bad' });
        return;
      }
      commands.gallery(free.slot, kind);
    },
  } });
  row.slots.forEach(place => made.appendChild(tile(place, row, client, commands)));
  const spare = state.payload.template.grid.columns - row.slots.reduce((sum, p) => sum + p.width, 0);
  const free = state.payload.free_slots.find(slot => slot.section === act.sectionOfRow(row.row));
  if (spare >= 2 && free && act.editable()) {
    made.appendChild(node('button', { class: 'ss-empty', type: 'button', style: `--w: ${spare}`,
      title: `Add a ${free.purpose} chart on ${free.slot}`,
      on: { click: () => commands.gallery(free.slot) } }, `+ ${free.purpose}`));
  }
  return made;
}

const slotOf = slot => (state.payload.template.slots || {})[slot] || {};

function tile(place, row, client, commands) {
  const taken = state.payload.pictures;
  const shot = taken && taken.slots && taken.slots[place.id];
  const height = Math.max(78, row.height * 8 - 34);
  const described = act.described(place.id);
  return node('article', { class: 'ss-tile', style: `--w: ${place.width}`, 'data-slot': place.id,
    'aria-selected': state.selected === place.id, tabindex: '0', role: 'button',
    'aria-label': `${act.title(place.id)}, ${place.width} of 12 columns`,
    draggable: act.editable(), on: {
      click: () => commands.select(place.id),
      // Enter or Space on a focused tile selects it; focus alone does not, or a redraw would move the
      // focus out from under the keyboard.
      keydown: event => { if (event.key === 'Enter' || event.key === ' ') commands.select(place.id); },
      dragstart: event => {
        event.dataTransfer.setData('text/plain', `slot:${place.id}`);
        const canvasNode = event.target.closest('.ss-canvas');
        if (canvasNode) canvasNode.dataset.guides = 'true';
      },
      dragend: event => {
        const canvasNode = event.target.closest('.ss-canvas');
        if (canvasNode) delete canvasNode.dataset.guides;
      },
    } },
    node('div', { class: 'ss-label', text: `${place.id} · ${slotOf(place.id).purpose || ''}` }),
    node('div', { class: 'ss-handles' }, ['nw', 'n', 'ne', 'w', 'e', 'sw', 's', 'se']
      .map(where => node('i', { class: 'ss-handle', 'data-at': where }))),
    node('header', {},
      node('strong', { text: act.title(place.id) }),
      node('span', { class: 'ss-spacer' }),
      described.governance && described.governance.metric_certified === false
        ? node('span', { class: 'ss-cert', 'data-certified': 'false', title: 'the metric is not certified',
            text: '⚠' }) : null,
      node('span', { class: 'ss-slot', text: `${place.id} · ${place.width}/12` })),
    shot
      ? node('img', { class: 'ss-shot', src: client.picture(place.id, taken.taken_at), loading: 'lazy',
          alt: `${act.title(place.id)}, as the dashboard drew it`, style: `height: ${height}px` })
      : node('div', { class: 'ss-shot-missing', style: `height: ${height}px` },
          taken ? 'this chart was not in the picture' : 'no picture taken yet'),
    act.editable() && node('div', { class: 'ss-grip', title: 'Drag to resize',
      on: { pointerdown: grip(place) } }));
}

/** The drag changes nothing until it is released: it moves two tiles' widths in the page, and the
 *  specification once. Otherwise three columns would be three steps to undo. */
function grip(place) {
  return event => {
    event.preventDefault();
    event.stopPropagation();
    const tileNode = event.target.closest('.ss-tile');
    const rowNode_ = event.target.closest('.ss-row');
    const line = act.rowOf(place.id);
    const at = line.slots.findIndex(s => s.id === place.id);
    const other = line.slots[at + 1] || line.slots[at - 1];
    const neighbour = other && rowNode_.querySelector(`[data-slot="${other.id}"]`);
    const columns = state.payload.template.grid.columns;
    const column = rowNode_.getBoundingClientRect().width / columns;
    const from = event.clientX;
    const was = place.width;
    const wasOther = other ? other.width : 0;
    const spare = columns - line.slots.reduce((sum, s) => sum + s.width, 0);
    let wanted = was;
    const canvasNode = rowNode_.closest('.ss-canvas');
    if (canvasNode) canvasNode.dataset.guides = 'true';
    const move = inner => {
      const by = Math.round((inner.clientX - from) / column);
      const most = other ? was + wasOther - act.minWidth(other.id) : was + spare;
      wanted = Math.max(act.minWidth(place.id), Math.min(most, was + by));
      tileNode.style.setProperty('--w', wanted);
      if (neighbour) neighbour.style.setProperty('--w', wasOther - (wanted - was));
    };
    const stop = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', stop);
      if (canvasNode) delete canvasNode.dataset.guides;
      if (wanted !== was) {
        act.change(() => {
          place.width = wanted;
          if (other) other.width = wasOther - (wanted - was);
        });
      }
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', stop);
  };
}

/* ── the right pane: the inspector (§47.5) and the copilot (§47.6) ───────────────────────────────────────── */
function rightPane(client, commands) {
  return node('div', { class: 'ss-pane ss-stack' }, inspector(client, commands), copilot(commands));
}

function inspector(client, commands) {
  if (!state.selected) return dashboardInspector(commands);
  const slot = state.selected;
  const own = act.component(slot) || {};
  const said = act.described(slot);
  const governance = said.governance || {};
  const row = act.rowOf(slot);
  const place = row && row.slots.find(s => s.id === slot);
  const tabs = { content: 'Content', chart: 'Chart', governance: 'Governance', data: 'Data & dbt' };
  const body = node('div', { class: 'ss-body' });
  const which = state.inspector || 'content';

  if (which === 'content') {
    body.appendChild(field('Title', node('input', { value: own.title || '', maxlength: 80,
      disabled: !act.editable(), on: { change: e => act.setField(slot, 'title', e.target.value) } })));
    body.appendChild(field('What it is for', node('textarea', { rows: 2, maxlength: 300,
      disabled: !act.editable(), on: { change: e => act.setField(slot, 'description', e.target.value) } },
      own.description || '')));
    body.appendChild(node('ul', { class: 'ss-gov' },
      pair('Measures', own.metric), pair('Broken down by', own.dimension), pair('From', own.dataset)));
    body.appendChild(node('p', { class: 'ss-note', style: 'margin-top: 10px', text:
      'What a chart measures is the registry’s, not this page’s: ask the copilot for another chart, '
      + 'or add one on a free slot. The engine checks the metric is valid on the dataset and that you are '
      + 'cleared for it.' }));
  }
  if (which === 'chart') {
    body.appendChild(node('div', { class: 'ss-two' },
      field('Top N', node('input', { type: 'number', min: 1, max: 1000, value: (own.options || {}).row_limit || '',
        disabled: !act.editable() || !['bar', 'table', 'pie'].includes(own.type),
        on: { change: e => act.setField(slot, 'row_limit', e.target.value) } })),
      field('Time grain', select((own.options || {}).time_grain, ['', 'P1D', 'P1W', 'P1M'],
        value => act.setField(slot, 'time_grain', value),
        { disabled: !act.editable() || !['line', 'area'].includes(own.type) })),
      field('Order', select((own.options || {}).sort, ['', 'desc', 'asc'],
        value => act.setField(slot, 'sort', value),
        { disabled: !act.editable() || !['bar', 'table'].includes(own.type) })),
      field('Numbers', select((own.options || {}).number_format, ['', 'default', 'compact'],
        value => act.setField(slot, 'number_format', value), { disabled: !act.editable() }))));
    body.appendChild(node('div', { class: 'ss-rowbar', style: 'opacity: 1; margin: 6px 0 12px' },
      node('span', { text: place ? `Row ${row.row}, ${place.width}/12` : '' }),
      node('span', { class: 'ss-spacer' }),
      act.editable() && node('button', { type: 'button', title: 'Earlier on the row (⇧←)', text: '←',
        on: { click: () => act.moveWithin(slot, -1) } }),
      act.editable() && node('button', { type: 'button', title: 'Later on the row (⇧→)', text: '→',
        on: { click: () => act.moveWithin(slot, 1) } }),
      act.editable() && node('button', { type: 'button', title: 'Narrower ([)', text: '–',
        on: { click: () => act.resize(slot, -1) } }),
      act.editable() && node('button', { type: 'button', title: 'Wider (])', text: '+',
        on: { click: () => act.resize(slot, 1) } })));
    body.appendChild(node('ul', { class: 'ss-gov' },
      pair('Kind', said.type || own.type), pair('Purpose', said.purpose),
      pair('The slot accepts', (said.accepts || []).join(', ')),
      pair('Mandatory', said.mandatory ? 'yes — it cannot be taken off' : 'no')));
    if (act.editable() && !said.mandatory) {
      body.appendChild(node('div', { style: 'margin-top: 12px' },
        button('Take off the dashboard', () => act.park(slot),
          { title: 'It is kept in the specification, parked in its section (Backspace)' })));
    }
    body.appendChild(node('p', { class: 'ss-note', style: 'margin-top: 10px', text:
      'A colour, a legend, an axis or an ECharts option is Superset’s own: a specification has no place '
      + 'for it, so a commit sets it back. Explore is open; what it changes does not last.' }));
  }
  if (which === 'governance') {
    body.appendChild(node('ul', { class: 'ss-gov' },
      pair('Metric', own.metric),
      pair('Certified', governance.metric_certified ? 'yes' : 'NO — it may not go to PROD'),
      pair('Version', governance.metric_version), pair('Owner', governance.metric_owner),
      pair('Definition', governance.metric_definition),
      pair('Dataset', own.dataset),
      pair('Dataset certified', governance.dataset_certified ? 'yes' : 'no'),
      pair('Dataset owner', governance.dataset_owner), pair('Domain', governance.dataset_domain),
      pair('Grain', (governance.dataset_grain || []).join(', ')),
      pair('Section', said.section)));
    body.appendChild(node('p', { class: 'ss-note', style: 'margin-top: 10px', text:
      'Who may open the dashboard this becomes is decided from these: the roles granted every dataset it '
      + 'uses, and the row-level rules that follow them.' }));
  }
  if (which === 'data') {
    const lineage = state.lineage[own.dataset];
    body.appendChild(node('ul', { class: 'ss-gov' },
      pair('Dataset', own.dataset), pair('Owner', governance.dataset_owner),
      pair('Grain', (governance.dataset_grain || []).join(', '))));
    if (lineage === undefined) {
      body.appendChild(node('div', { style: 'margin-top: 10px' },
        button('Read its lineage', () => commands.lineage(own.dataset),
          { disabled: !own.dataset, title: 'The dbt model it is built from, and what that model is built from' })));
    } else if (lineage === null) {
      body.appendChild(node('p', { class: 'ss-note', text: 'the engine has no lineage for this dataset' }));
    } else {
      body.appendChild(node('div', { class: 'ss-code', text: JSON.stringify(lineage, null, 2).slice(0, 1800) }));
    }
    body.appendChild(node('p', { class: 'ss-note', style: 'margin-top: 10px', text:
      'No sample of the data is shown here, on purpose: a developer’s Superset account is given no '
      + 'database in SQL Lab, and this page is not a way round that.' }));
  }

  return node('section', { class: 'ss-card' },
    node('header', {}, node('span', { text: `Inspector — ${slot}` })),
    node('div', { style: 'padding: 8px 10px 0' }, node('div', { class: 'ss-tabs' },
      Object.entries(tabs).map(([key, label]) => node('button', { type: 'button', text: label,
        'aria-selected': which === key, on: { click: () => { state.inspector = key; commands.redraw(); } } })))),
    body);
}

/* With nothing selected, the inspector is the DASHBOARD's: what a specification says about the whole of it
 * (§47.5, « for a dashboard: layout, filters, theme, security, metadata »). */
function dashboardInspector(commands) {
  const tabs = { about: 'Dashboard', filters: 'Filters', security: 'Security', layout: 'Layout' };
  const which = state.dashboardTab || 'about';
  const body = node('div', { class: 'ss-body' });
  const header = state.spec.dashboard || {};
  const security = state.payload.security || {};
  const template = state.payload.template;

  if (which === 'about') {
    body.appendChild(field('Title', node('input', { value: header.title || '', maxlength: 120,
      disabled: !act.editable(), on: { change: e => act.setDashboard('title', e.target.value) } })));
    body.appendChild(field('Description', node('textarea', { rows: 2, maxlength: 500, disabled: !act.editable(),
      on: { change: e => act.setDashboard('description', e.target.value) } }, header.description || '')));
    body.appendChild(field('Owner', node('input', { value: header.owner || '', maxlength: 80,
      disabled: !act.editable(), on: { change: e => act.setDashboard('owner', e.target.value) } })));
    body.appendChild(node('ul', { class: 'ss-gov' },
      pair('Template', `${template.id} v${template.version}`),
      pair('Charts', (state.spec.components || []).length),
      pair('Version', state.payload.project.version),
      pair('Gate', state.payload.project.gate),
      pair('Environment', state.payload.project.environment)));
  }
  if (which === 'filters') {
    const offered = state.payload.filter_options || [];
    const used = state.spec.filters || [];
    body.appendChild(node('p', { class: 'ss-note', text:
      `${used.length} of ${template.filters.max} this template allows. A filter is a column of a dataset this `
      + `dashboard already uses, in a role the template permits (${template.filters.allowed_roles.join(', ')}).` }));
    if (!offered.length) {
      body.appendChild(node('div', { class: 'ss-callout', text:
        'No column of this dashboard\u2019s datasets may be filtered on under this template.' }));
    }
    body.appendChild(node('div', { class: 'ss-pick' }, offered.map(option => node('label', {},
      node('input', { type: 'checkbox', checked: used.includes(option.column), disabled: !act.editable(),
        on: { change: event => act.setFilter(option.column, event.target.checked) } }),
      node('span', { class: 'ss-what' }, node('strong', { text: option.label }),
        node('span', { class: 'ss-faint', text: ` ${option.column} · ${option.role}` }),
        option.reaches_every_chart ? null
          : node('div', { class: 'ss-faint', text: 'not on every dataset: it will not reach every chart' })),
      node('span', { class: 'ss-cert', 'data-certified': option.reaches_every_chart,
        text: option.reaches_every_chart ? '✓' : '○' })))));
  }
  if (which === 'security') {
    body.appendChild(node('p', { class: 'ss-note', text:
      'Who may open the dashboard this becomes — SharpSight decides it from what the dashboard uses, and the '
      + 'engine enforces it in Superset when a version is built, then asks Superset whether it agrees.' }));
    body.appendChild(node('ul', { class: 'ss-gov' },
      pair('May open it', (security.audience || []).join(', ')),
      pair('Classifications', (security.classifications || []).join(', ')),
      pair('Row-level rules', (security.row_rules || []).length)));
    (security.row_rules || []).slice(0, 8).forEach(rule => body.appendChild(node('div', { class: 'ss-code',
      style: 'margin-top: 6px', text: `${rule.role} on ${rule.dataset}: ${rule.clause}` })));
    if ((security.uncertified || []).length) {
      body.appendChild(node('div', { class: 'ss-callout', style: 'margin-top: 10px' },
        node('strong', { text: 'Not certified: ' }), security.uncertified.join(', ')));
    }
  }
  if (which === 'layout') {
    body.appendChild(node('ul', { class: 'ss-gov' },
      pair('Grid', `${template.grid.columns} columns, ${template.grid.row_unit_px}px a unit`),
      pair('Rows', act.rows().length),
      pair('Sections', (template.sections || []).map(section => section.title || section.id).join(', ')),
      pair('Laid out by', state.payload.layout_of === 'specification' ? 'this project' : 'the template')));
    body.appendChild(field('Brand', select((state.spec.theme || {}).brand || '',
      [['', 'SharpSight\u2019s own'], 'sharpsight', 'inwi'], value => act.setDashboard('brand', value),
      { disabled: !act.editable() })));
    body.appendChild(node('p', { class: 'ss-note', text:
      'A brand is an accent and a series of colours, nothing else: the page around the dashboard stays '
      + 'SharpSight\u2019s. Everything else about the layout is the template\u2019s, and the canvas rearranges '
      + 'it within what the template allows.' }));
  }

  return node('section', { class: 'ss-card' },
    node('header', {}, node('span', { text: 'The dashboard' }),
      node('span', { class: 'ss-spacer' }),
      node('span', { class: 'ss-faint', text: 'select a chart for its own' })),
    node('div', { style: 'padding: 8px 10px 0' }, node('div', { class: 'ss-tabs' },
      Object.entries(tabs).map(([key, label]) => node('button', { type: 'button', text: label,
        'aria-selected': which === key,
        on: { click: () => { state.dashboardTab = key; commands.redraw(); } } })))),
    body);
}

function copilot(commands) {
  const body = node('div', { class: 'ss-body' });
  if (state.said.length) {
    body.appendChild(node('div', { class: 'ss-chat' }, state.said.map(said =>
      node('div', { class: 'ss-said', 'data-who': said.who, text: said.text }))));
  }
  if (state.proposal) body.appendChild(proposal(commands));
  if (!state.proposal) {
    body.appendChild(node('div', { class: 'ss-chips' }, (state.payload.ask || []).map(example =>
      node('button', { class: 'ss-chip', type: 'button', text: example,
        on: { click: () => commands.propose(example) } }))));
  }
  const box = node('textarea', { class: 'ss-search', rows: 2, id: 'ss-ask',
    placeholder: 'Ask for a change — « add a distribution of QoE score », « make the trend weekly »…',
    disabled: !act.editable() || state.thinking,
    on: { keydown: event => {
      if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); commands.propose(box.value); }
    } } });
  body.appendChild(box);
  body.appendChild(node('div', { style: 'display: flex; gap: 8px; margin-top: 8px; align-items: center' },
    button(state.thinking ? 'Reading…' : 'Propose', () => commands.propose(box.value),
      { 'data-weight': 'primary', disabled: !act.editable() || state.thinking }),
    state.said.length ? button('Clear', () => { state.said = []; state.proposal = null; commands.redraw(); },
      { 'data-weight': 'quiet' }) : null,
    node('span', { class: 'ss-faint', text: 'Enter to ask' })));
  body.appendChild(node('div', { class: 'ss-callout', 'data-kind': 'info', style: 'margin-top: 12px', text:
    'This copilot is deterministic: no language model has been chosen for this kit, which calls nobody '
    + '(aibi/llm.py is the seam). It reads the same governed vocabulary the planner reads, it proposes and '
    + 'never changes anything by itself, and what it does not understand it says it does not understand. '
    + 'A model plugs into the same seam and answers with the same proposal.' }));
  return node('section', { class: 'ss-card' }, node('header', {}, 'SharpSight AI'), body);
}

function proposal(commands) {
  const made = state.proposal;
  const validation = made.validation || {};
  return node('div', { class: 'ss-proposal' },
    node('h4', { text: made.answer ? 'An answer' : 'Proposed' }),
    made.answer ? node('p', { text: made.answer }) : null,
    made.changes && made.changes.length ? node('ul', { class: 'ss-changes' },
      made.changes.map(change => node('li', { text: change.says }))) : null,
    made.summary && !made.answer ? node('p', { class: 'ss-note', text: made.summary }) : null,
    (made.unresolved || []).length ? node('div', { class: 'ss-callout', style: 'margin: 8px 0' },
      made.unresolved.map(said => node('div', { text: said }))) : null,
    validation.checks ? node('p', { class: 'ss-faint',
      text: `Validation: ${validation.passed ? 'would pass' : 'would FAIL'} — ${validation.checks} checks, `
        + `${(validation.failures || []).length} failing, ${(validation.warnings || []).length} warning` }) : null,
    (validation.failures || []).length ? node('div', { class: 'ss-callout', 'data-kind': 'bad' },
      validation.failures.map(said => node('div', { text: said }))) : null,
    made.spec ? node('div', { style: 'display: flex; gap: 8px; margin-top: 10px' },
      button('Apply to the draft', () => { act.apply(made); commands.redraw(); },
        { 'data-weight': 'primary', disabled: !validation.passed,
          title: validation.passed ? 'Take this into the draft — one undo takes it back'
            : 'This would not pass validation' }),
      button('Reject', () => { state.proposal = null; commands.redraw(); })) : null);
}

/* ── the drawer (§47.7) ──────────────────────────────────────────────────────────────────────────────────── */
function drawer(commands) {
  const problems = (state.payload.problems || []).length;
  const failures = state.validation ? state.validation.summary.failures : 0;
  const tabs = [
    ['validation', 'Validation', failures + problems, failures + problems ? 'bad' : null],
    ['changes', 'Changes', (state.payload.diff || []).length, null],
    ['report', 'Last run', null, null],
    ['releases', 'Releases', (state.payload.versions || []).length, null],
    ['about', 'What this is', null, null],
  ];
  const body = node('div', { class: 'ss-body' });
  ({ validation: validationTab, changes: changesTab, report: reportTab, releases: releasesTab,
    about: aboutTab }[state.tab] || validationTab)(body, commands);
  return node('section', { class: 'ss-card ss-drawer' },
    node('header', {}, node('div', { class: 'ss-tabs' }, tabs.map(([key, label, count, kind]) =>
      node('button', { type: 'button', 'aria-selected': state.tab === key,
        on: { click: () => { state.tab = key; commands.redraw(); } } }, label,
        count ? node('span', { class: 'ss-count', 'data-kind': kind, text: String(count) }) : null)))),
    body);
}

function validationTab(body, commands) {
  if (!state.validation) {
    body.appendChild(node('p', { class: 'ss-note', text:
      'Validate reads the draft against the registry and the template — the slots, the visualizations, the '
      + 'metrics, the datasets, and your clearance for each. It touches nothing in Superset. The levels '
      + 'beyond it (the data, the security probes, the render, the picture, the timings) are what a commit '
      + 'runs: « Last run » shows them.' }));
    body.appendChild(node('div', { style: 'margin-top: 10px' }, button('Validate now', commands.validate)));
    return;
  }
  const report = state.validation;
  body.appendChild(node('p', {}, node('strong', { text: report.summary.passed ? '✓ passed' : '✗ failed' }),
    ` — ${report.summary.checks} checks, ${report.summary.failures} failed, ${report.summary.warnings} warned`
    + ` (of the ${report.of})`));
  const shown = report.checks.filter(check => check.status !== 'pass' || !report.summary.failures);
  body.appendChild(node('ul', { class: 'ss-checks' }, shown.map(check =>
    node('li', { 'data-status': check.status },
      node('b', { text: check.status }),
      node('span', {}, node('span', { text: check.name }),
        check.subject ? node('span', { class: 'ss-faint', text: ` — ${check.subject}` }) : null,
        check.detail ? node('div', { class: 'ss-note', text: check.detail }) : null)))));
}

function changesTab(body, commands) {
  const diff = state.payload.diff || [];
  body.appendChild(node('p', { class: 'ss-note', text: state.payload.editing === 'draft'
    ? `What this draft changes about version ${state.payload.project.version || '—'}:`
    : 'There is no draft: what is shown is the committed version itself.' }));
  if (diff.length) body.appendChild(node('div', { class: 'ss-code', text: diff.join('\n') }));
  body.appendChild(node('div', { style: 'margin-top: 12px' },
    button('What Superset holds now', commands.changes,
      { title: 'Compare the dashboard in Superset with the version that was validated' })));
  if (state.changes) {
    const found = state.changes;
    body.appendChild(node('p', { style: 'margin-top: 10px' },
      node('strong', { text: found.same_as_validated ? 'Superset shows the validated version'
        : 'Superset shows something else' }),
      found.why_not ? node('div', { class: 'ss-note', text: found.why_not }) : null));
    const edited = (found.charts || []).filter(chart => Object.keys(chart.changed || {}).length
      || (chart.refused || []).length);
    if (edited.length) {
      body.appendChild(node('ul', { class: 'ss-checks' }, edited.map(chart =>
        node('li', { 'data-status': (chart.refused || []).length ? 'fail' : 'warn' },
          node('b', { text: (chart.refused || []).length ? 'refused' : 'edited' }),
          node('span', {}, node('span', { text: `${chart.title} (${chart.slot})` }),
            node('div', { class: 'ss-note', text: Object.entries(chart.changed || {})
              .map(([name, change]) => `${name}: ${change.specification} → ${change.superset}`).join('; ')
              + (chart.refused || []).join('; ') }))))));
    }
  }
}

function reportTab(body, commands) {
  if (!state.report) {
    body.appendChild(node('p', { class: 'ss-note', text:
      'What the last commit found: the brief’s levels 2 to 6 — the SQL and the data, the security '
      + 'probes, the dashboard rendered in a browser, the picture, the timings. Only a run can produce them.' }));
    body.appendChild(node('div', { style: 'margin-top: 10px' }, button('Read the last run’s report',
      commands.report)));
    return;
  }
  const report = state.report;
  const checks = ((report.validation || {}).checks) || [];
  const by = {};
  checks.forEach(check => {
    by[check.category] = by[check.category] || { pass: 0, warn: 0, fail: 0, skipped: 0 };
    by[check.category][check.status] = (by[check.category][check.status] || 0) + 1;
  });
  body.appendChild(node('p', {}, node('strong', { text: `Version ${report.version} — ${report.status}` }),
    ` by ${report.created_by}, ${new Date(report.created_at).toLocaleString()}`));
  body.appendChild(node('table', { class: 'ss-table' },
    node('tr', {}, ['Level', 'Passed', 'Warned', 'Failed'].map(head => node('th', { text: head }))),
    Object.entries(by).map(([category, counts]) => node('tr', {},
      node('td', { text: category }), node('td', { text: String(counts.pass || 0) }),
      node('td', { text: String(counts.warn || 0) }), node('td', { text: String(counts.fail || 0) })))));
  const notable = checks.filter(check => check.status !== 'pass');
  if (notable.length) {
    body.appendChild(node('ul', { class: 'ss-checks', style: 'margin-top: 10px' }, notable.map(check =>
      node('li', { 'data-status': check.status }, node('b', { text: check.status }),
        node('span', {}, node('span', { text: `${check.category} · ${check.name}` }),
          check.detail ? node('div', { class: 'ss-note', text: check.detail }) : null)))));
  }
}

function releasesTab(body) {
  const versions = state.payload.versions || [];
  if (!versions.length) {
    body.appendChild(node('p', { class: 'ss-note', text: 'This project has no version yet.' }));
  } else {
    body.appendChild(node('table', { class: 'ss-table' },
      node('tr', {}, ['Version', 'Gate', 'Environment', 'By', 'When', 'Package'].map(h => node('th', { text: h }))),
      [...versions].reverse().map(version => node('tr', {},
        node('td', { text: version.version }), node('td', { text: version.status }),
        node('td', { text: version.state }), node('td', { text: version.created_by }),
        node('td', { text: new Date(version.created_at).toLocaleString() }),
        node('td', { text: version.package || '—' })))));
  }
  body.appendChild(node('div', { class: 'ss-callout', style: 'margin-top: 12px', text:
    'Promoting is not here, on purpose: UAT and PROD are approved on the command line, by someone other than '
    + 'the person who asked for the version — which is the one rule that could not be enforced if this page '
    + 'could do it. bash ai-bi.sh approve <project> UAT.' }));
}

function aboutTab(body) {
  body.appendChild(node('div', { class: 'ss-note' },
    node('p', {}, 'This workspace edits a SPECIFICATION, not a dashboard. Superset draws every chart and '
      + 'every dashboard; the Studio says what they should be, and the engine decides whether that may '
      + 'become a version.'),
    node('p', {}, 'So: no second renderer, no second chart editor, no second SQL Lab — the brief’s own '
      + 'first rules. What the canvas may change is the layout, within what the template allows, and the '
      + 'fields a specification carries. A tile is a photograph of the real chart; Preview opens the live '
      + 'dashboard.'),
    node('p', {}, 'Nothing here approves and nothing here promotes. A commit goes through the engine’s '
      + 'ten quality gates like every other commit.')));
}

/* ── the gallery (§48.3): the manual path, which is not a prompt ─────────────────────────────────────────── */
export function gallery(wanted, kind, commands) {
  const free = state.payload.free_slots;
  if (!free.length) {
    toast('Every slot of this template already holds a chart.', { kind: 'bad' });
    return;
  }
  const chosen = { slot: (free.find(f => f.slot === wanted) || free[0]).slot, metric: null, type: kind || null,
    dimension: '', time_grain: '' };
  const body = node('div', {});
  const draw_ = () => {
    body.textContent = '';
    const slot = free.find(f => f.slot === chosen.slot);
    body.appendChild(field('Where it goes', select(chosen.slot,
      free.map(f => [f.slot, `${f.slot} — a ${f.purpose} in ${f.section}${f.mandatory ? ' (mandatory)' : ''}`]),
      value => { chosen.slot = value; chosen.type = null; draw_(); })));
    body.appendChild(field('What it shows', node('div', { class: 'ss-pick' },
      state.metrics.map(metric => node('label', {},
        node('input', { type: 'radio', name: 'ss-metric', value: metric.id,
          checked: chosen.metric === metric.id, on: { change: () => { chosen.metric = metric.id; } } }),
        node('span', { class: 'ss-what' }, node('strong', { text: metric.name }),
          node('span', { class: 'ss-faint', text: ` ${metric.id}` }),
          node('div', { class: 'ss-faint', text: (metric.documentation || '').slice(0, 120) })),
        node('span', { class: 'ss-cert', 'data-certified': Boolean(metric.certified),
          text: metric.certified ? '✓' : '○' }))))));
    body.appendChild(node('div', { class: 'ss-two' },
      field('Drawn as', select(chosen.type, [['', 'let the registry choose'], ...(slot.accepts || [])],
        value => { chosen.type = value || null; })),
      field('Broken down by (optional)', node('input', { placeholder: 'region, rat…',
        on: { change: event => { chosen.dimension = event.target.value; } } }))));
    body.appendChild(node('p', { class: 'ss-note', text:
      'The slot decides what may be drawn there; the registry decides whether this metric can be drawn that '
      + 'way, and which governed dataset it comes from. What you get back is a proposal — nothing is saved '
      + 'until you apply it.' }));
  };
  draw_();
  modal({
    title: 'Add a chart',
    body,
    footer: [button('Cancel', close), button('Propose', async () => {
      if (!chosen.metric) { toast('Choose what it shows.', { kind: 'bad' }); return; }
      close();
      await commands.compose(chosen);
    }, { 'data-weight': 'primary' })],
  });
}

export function help() {
  modal({ title: 'Keyboard', body: node('ul', { class: 'ss-gov' },
    KEYS.map(([keys, what]) => pair(keys, what))) });
}
