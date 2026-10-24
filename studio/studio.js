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
import { close, isOpen, node, palette, shortcuts, theme, themeNow, toast } from './ui.js';
import * as view from './view.js';

const el = document.getElementById('sharpsight-studio');
const api = client(el);

// what is said when a proposal is not asked for because the draft it would be made on could not be saved
const NOT_ASKED = 'Nothing was asked: your unsaved changes could not be saved first, and a proposal is made '
  + 'on the saved draft.';
// …and when a validation is not run for the same reason: it checks the SAVED draft, so a pass on it would
// be a pass for a layout the person has already changed
const NOT_VALIDATED = 'Nothing was validated: your unsaved changes could not be saved first, and a '
  + 'validation is made on the saved draft.';

const commands = {
  redraw: () => view.draw(el, api, commands),
  reload: () => load(),
  help: () => view.help(),
  gallery: (slot, kind) => view.gallery(typeof slot === 'string' ? slot : null,
    typeof kind === 'string' ? kind : null, commands),

  select(slot) {
    state.selected = slot;
    commands.redraw();
  },

  async validate() {
    state.tab = 'validation';
    try {
      if (await act.settle()) {
        state.validation = await api.check();
      } else {
        // an earlier result left on the tab would read as this draft's: it is taken away, and the reason said
        state.validation = null;
        toast(NOT_VALIDATED, { kind: 'bad' });
        commands.redraw();
        return;
      }
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
      // the copilot reads the SAVED draft: what has not been saved yet is saved first, or nothing is asked
      if (!(await act.settle())) {
        state.said.push({ who: 'studio', text: NOT_ASKED });
        return;
      }
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
      if (!(await act.settle())) {
        toast(NOT_ASKED, { kind: 'bad' });
        return;
      }
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

  /** A chart's content changed from the inspector's own controls. It is the same proposal the words and the
   *  gallery produce — shown in the copilot, applied by hand — because changing what a chart measures is
   *  checked, not typed. */
  async alter(wanted) {
    state.thinking = true;
    state.copilot = true;
    commands.redraw();
    try {
      if (!(await act.settle())) {
        toast(NOT_ASKED, { kind: 'bad' });
        return;
      }
      const made = await api.alter(wanted);
      state.proposal = made;
      // an empty field is one taken away — "broken down by: none" — and is said so, not as a blank
      state.said.push({ who: 'person', text: Object.entries(wanted)
        .filter(([name]) => name !== 'slot')
        .map(([name, value]) => `${name} → ${value === '' ? 'none' : value}`).join(', ')
        + ` on ${wanted.slot}` });
      state.said.push({ who: 'studio', text: made.summary || (made.unresolved || []).join(' ') });
      if (!made.spec) toast((made.unresolved || []).join(' '), { kind: 'bad', seconds: 9 });
    } catch (problem) {
      toast(problem.message, { kind: 'bad', seconds: 0 });
    } finally {
      state.thinking = false;
      commands.redraw();
    }
  },

  /** Take a version to another Superset. The engine refuses a version nobody else has approved for that
   *  gate before anything is sent, so this is a button, not a decision. */
  async deploy(version, target, gate, comment = '') {
    state.deploying = true;
    commands.redraw();
    const waiting = toast(`Deploying ${version} to ${target} — importing, repointing, granting, reading back…`,
      { seconds: 0 });
    try {
      const done = await api.deploy(version, { target, gate, comment });
      state.deployments = [...(state.deployments || []), done];
      toast(done.status === 'succeeded'
        ? `${version} is in ${target}: ${done.checks.filter(c => c.status === 'pass').length} checks passed.`
        : `The deployment failed: ${done.error}`,
      { kind: done.status === 'succeeded' ? 'good' : 'bad', seconds: 0 });
    } catch (problem) {
      toast(problem.message, { kind: 'bad', seconds: 0 });
    } finally {
      waiting.remove();
      state.deploying = false;
      state.tab = 'deployments';
      commands.redraw();
    }
  },

  async deployments() {
    try {
      const [targets, done] = await Promise.all([api.targets(), api.deployments()]);
      state.targets = targets;
      state.deployments = done;
    } catch (problem) {
      state.targets = state.targets || [];
      toast(problem.message, { kind: 'bad' });
    }
    commands.redraw();
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

  /** Everything the workspace can do, by name. It offers no command the panes do not, and none that is
   *  refused elsewhere: a disabled action is left out, with the same reason the bar gives. */
  palette() {
    const free = state.payload.free_slots || [];
    const versions = state.payload.versions || [];
    const entries = [
      { where: 'Do', label: 'Add a chart', keys: 'a', run: () => commands.gallery(),
        when: act.editable() && free.length },
      { where: 'Do', label: 'Validate the draft', keys: 'v', run: commands.validate, when: true },
      { where: 'Do', label: 'Save the draft', keys: '⌘S', run: act.save, when: state.dirty && act.editable() },
      { where: 'Do', label: 'Commit a version', run: commands.commit,
        when: act.editable() && state.savedAt && !state.dirty },
      { where: 'Do', label: 'Take the tiles again', run: commands.tiles, when: true },
      { where: 'Do', label: 'Preview the live dashboard', run: () =>
        window.open(state.payload.project.dashboard, '_blank', 'noopener'),
      when: Boolean(state.payload.project.dashboard) },
      { where: 'Do', label: 'Export the latest package (.ssbi)', run: commands.export_,
        when: versions.some(version => version.package) },
      { where: 'Do', label: 'Throw the draft away', run: commands.discard, when: Boolean(state.savedAt) },
      { where: 'View', label: 'Dark or light', run: () => { theme(themeNow() === 'dark' ? 'light' : 'dark');
        commands.redraw(); }, when: true },
      { where: 'View', label: 'The explorer', run: () => { state.explorer = !state.explorer;
        commands.redraw(); }, when: true },
      { where: 'View', label: 'The copilot', run: () => { state.copilot = !state.copilot;
        commands.redraw(); }, when: true },
      { where: 'View', label: 'Keyboard shortcuts', keys: '?', run: commands.help, when: true },
      ...['validation', 'changes', 'report', 'releases', 'about'].map(tab => ({
        where: 'Drawer', label: tab === 'about' ? 'What this workspace is' : `Show ${tab}`,
        run: () => { state.tab = tab; commands.redraw(); }, when: true })),
      ...act.rows().flatMap(row => row.slots.map(place => ({
        where: 'Chart', label: act.title(place.id), run: () => commands.select(place.id), when: true }))),
      ...free.map(slot => ({ where: 'Free slot', label: `Add a ${slot.purpose} on ${slot.slot}`,
        run: () => commands.gallery(slot.slot), when: act.editable() })),
    ];
    palette(entries.filter(entry => entry.when));
  },

  export_() {
    const versions = (state.payload.versions || []).filter(version => version.package);
    const latest = versions[versions.length - 1];
    if (!latest) {
      toast('No version of this project has a package yet.', { kind: 'bad' });
      return;
    }
    window.open(api.packageUrl(latest.version), '_blank', 'noopener');
    toast(`Downloading ${latest.package} — the immutable package of version ${latest.version}. `
      + 'Importing it elsewhere is the deployment service\u2019s, and PROD still needs a second person.',
    { seconds: 9 });
  },

  async discard() {
    if (!window.confirm('Throw this draft away? The committed version and what Superset holds are untouched.')) {
      return;
    }
    try {
      await api.drop();
      toast('The draft is gone.', { kind: 'good' });
      await load();
    } catch (problem) {
      toast(problem.message, { kind: 'bad', seconds: 0 });
    }
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
  'mod+k': () => commands.palette(),
  'mod+K': () => commands.palette(),
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
  theme();
  act.wire(api, commands.redraw);
  // On a narrow screen the side panes are drawers, closed to begin with: a canvas squeezed into a third of
  // a laptop's width is worse than no canvas at all (§47.1).
  if (window.innerWidth < 1240) {
    state.explorer = false;
    state.copilot = false;
  }
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

// the bar wraps differently as the window changes, and the drawers start below it
let wide = window.innerWidth >= 1240;
window.addEventListener('resize', () => {
  const now = window.innerWidth >= 1240;
  if (now !== wide) {
    wide = now;
    // closed because the screen was narrow, and back when it is not: a pane a person closed at full width
    // and then narrowed past is no reason to leave them with one pane when they widen again.
    state.explorer = now;
    state.copilot = now;
  }
  if (state.payload) commands.redraw();
});

window.addEventListener('beforeunload', event => {
  if (state.dirty) { event.preventDefault(); event.returnValue = ''; }
});

start();
