/* SharpSight Studio — the visual builder of the brief's sections 46 to 60, inside Superset.
 *
 * WHERE IT LIVES. Superset 6.1.0 takes an extension panel in SQL Lab and nowhere else (docs/ai-bi.md §3), so
 * this is not a panel: it is a page of Superset's own, under Superset's own top bar, served by
 * superset-addon/sharpsight_bi.py. Same address, same session, same theme.
 *
 * WHAT IT EDITS. Not a dashboard — a SPECIFICATION. The canvas is an editor for the layout and the fields a
 * specification carries, which is why there is no second renderer, no second chart editor and no second SQL
 * Lab here (the brief's section 0 forbids all three). Superset draws the dashboard; the Studio says what the
 * dashboard should be; the engine's ten gates decide whether that may become a version.
 *
 * NO FRAMEWORK, NO BUILD STEP. The page already holds Superset's React, for the bar; a second React on one
 * page is a risk nobody needed to take for a workspace this size. Plain ES modules, served as they are in
 * the repository — so the kit ships a workspace without needing Node.
 *
 *     studio.js   this file: what each command does, and the keyboard
 *     view.js     drawing the bar, the three panes and the drawer
 *     state.js    the draft, and every move that may be made on it
 *     api.js      what is asked of the extension's API, with the person's Superset session
 *     ui.js       nodes, buttons, toasts, a modal
 */
import { client } from './api.js';
import * as act from './state.js';
import { state } from './state.js';
import { close, isOpen, node, shortcuts, toast } from './ui.js';
import * as view from './view.js';

const el = document.getElementById('sharpsight-studio');
const api = client(el);

const commands = {
  redraw: () => view.draw(el, api, commands),
  reload: () => load(),
  help: () => view.help(),
  gallery: slot => view.gallery(typeof slot === 'string' ? slot : null, api, commands),

  select(slot) {
    state.selected = slot;
    commands.redraw();
  },

  async validate() {
    state.tab = 'validation';
    if (state.dirty) await act.save();
    try {
      state.validation = await api.check();
      const summary = state.validation.summary;
      toast(summary.passed ? `Validation passed — ${summary.checks} checks.`
        : `Validation failed — ${summary.failures} of ${summary.checks} checks.`,
        { kind: summary.passed ? 'good' : 'bad' });
    } catch (problem) {
      toast(problem.message, { kind: 'bad', seconds: 0 });
    }
    commands.redraw();
  },

  async tiles() {
    const waiting = toast('Photographing the dashboard — every chart runs its own query…', { seconds: 0 });
    try {
      state.payload.pictures = await api.tiles();
      toast('Tiles taken.', { kind: 'good' });
    } catch (problem) {
      toast(`The tiles could not be taken: ${problem.message}`, { kind: 'bad', seconds: 0 });
    } finally {
      waiting.remove();
      commands.redraw();
    }
  },

  async commit() {
    const comment = window.prompt('What does this version change?', '');
    if (comment === null) return;
    try {
      const started = await api.commit(comment);
      state.run = { id: started.run_id, status: 'running', steps: [] };
      state.tab = 'report';
      commands.redraw();
      watch(started.run_id);
    } catch (problem) {
      toast(problem.message, { kind: 'bad', seconds: 0 });
    }
  },

  async propose(request) {
    const asked = (request || '').trim();
    if (!asked) return;
    state.said.push({ who: 'person', text: asked });
    state.thinking = true;
    state.proposal = null;
    commands.redraw();
    try {
      const made = await api.propose(asked);
      state.proposal = made;
      state.said.push({ who: 'studio', text: made.answer || made.summary
        || (made.unresolved || []).join(' ') || 'nothing is proposed' });
    } catch (problem) {
      state.said.push({ who: 'studio', text: problem.message });
      toast(problem.message, { kind: 'bad' });
    } finally {
      state.thinking = false;
      commands.redraw();
    }
  },

  async compose(wanted) {
    state.thinking = true;
    commands.redraw();
    try {
      const made = await api.compose(wanted);
      state.proposal = made;
      state.said.push({ who: 'person', text: `add ${wanted.metric} on ${wanted.slot}` });
      state.said.push({ who: 'studio', text: made.summary || (made.unresolved || []).join(' ') });
      if (!made.spec) toast((made.unresolved || []).join(' '), { kind: 'bad', seconds: 0 });
    } catch (problem) {
      toast(problem.message, { kind: 'bad', seconds: 0 });
    } finally {
      state.thinking = false;
      commands.redraw();
    }
  },

  async report() {
    try {
      state.report = await api.report();
    } catch (problem) {
      toast(problem.message, { kind: 'bad' });
    }
    commands.redraw();
  },

  async changes() {
    try {
      state.changes = await api.changes();
    } catch (problem) {
      toast(problem.message, { kind: 'bad' });
    }
    commands.redraw();
  },

  async lineage(dataset) {
    try {
      state.lineage[dataset] = await api.lineage(dataset);
    } catch (problem) {
      state.lineage[dataset] = null;
      toast(problem.message, { kind: 'bad' });
    }
    commands.redraw();
  },
};

function watch(id) {
  const timer = setInterval(async () => {
    try {
      state.run = await api.run(id);
    } catch (problem) {
      state.run = { id, status: 'failed', error: problem.message, steps: [] };
    }
    if (state.run.status !== 'running') {
      clearInterval(timer);
      toast(state.run.status === 'succeeded' ? 'Committed: the version awaits review.'
        : `The commit failed: ${state.run.error || ''}`,
        { kind: state.run.status === 'succeeded' ? 'good' : 'bad', seconds: 0 });
      if (state.run.status === 'succeeded') await load();
    }
    commands.redraw();
  }, 3000);
}

/* ── the keyboard: every move the mouse can make ─────────────────────────────────────────────────────────── */
function neighbour(by) {
  const slots = act.rows().flatMap(row => row.slots.map(place => place.id));
  if (!slots.length) return;
  const at = slots.indexOf(state.selected);
  state.selected = slots[(at + by + slots.length) % slots.length] || slots[0];
  commands.redraw();
}

shortcuts({
  '?': () => view.help(),
  'mod+s': () => act.save(),
  'mod+z': () => act.undo('undo'),
  'mod+shift+z': () => act.undo('redo'),
  'mod+shift+Z': () => act.undo('redo'),
  Escape: () => { if (isOpen()) close(); else { state.selected = null; commands.redraw(); } },
  ArrowLeft: () => neighbour(-1),
  ArrowRight: () => neighbour(1),
  'shift+ArrowLeft': () => state.selected && act.moveWithin(state.selected, -1),
  'shift+ArrowRight': () => state.selected && act.moveWithin(state.selected, 1),
  'shift+ArrowUp': () => state.selected && act.moveRow(act.rowOf(state.selected).row, -1),
  'shift+ArrowDown': () => state.selected && act.moveRow(act.rowOf(state.selected).row, 1),
  '[': () => state.selected && act.resize(state.selected, -1),
  ']': () => state.selected && act.resize(state.selected, 1),
  '-': () => state.selected && act.taller(act.rowOf(state.selected).row, -4),
  '+': () => state.selected && act.taller(act.rowOf(state.selected).row, 4),
  Backspace: () => state.selected && act.park(state.selected),
  a: () => commands.gallery(),
  v: () => commands.validate(),
  '/': () => { const box = document.getElementById('ss-search'); if (box) box.focus(); },
});

/* ── opening ─────────────────────────────────────────────────────────────────────────────────────────────── */
async function load() {
  const payload = await api.studio();
  state.payload = payload;
  state.spec = JSON.parse(JSON.stringify(payload.spec));
  state.token = payload.token;
  state.savedAt = payload.draft ? payload.draft.saved_at : null;
  state.dirty = false;
  state.loading = false;
  state.past = [];
  state.future = [];
  state.run = state.run && state.run.status === 'running' ? state.run : null;
  commands.redraw();
}

async function start() {
  act.wire(api, commands.redraw);
  commands.redraw();
  try {
    await load();
  } catch (problem) {
    state.loading = false;
    el.textContent = '';
    el.appendChild(node('div', { class: 'ss-callout', 'data-kind': 'bad', style: 'margin: 16px', text:
      `This project could not be opened: ${problem.message}` }));
    return;
  }
  // the governed lists, after the workspace is on the screen: the canvas does not wait for them
  try { state.datasets = await api.datasets(); } catch (problem) { state.datasets = []; }
  try { state.metrics = await api.metrics(); } catch (problem) { state.metrics = []; }
  commands.redraw();
}

window.addEventListener('beforeunload', event => {
  if (state.dirty) { event.preventDefault(); event.returnValue = ''; }
});

start();
