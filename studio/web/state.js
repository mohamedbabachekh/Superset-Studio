/* What the Studio is holding: the draft being edited, and every move that may be made on it.
 *
 * One rule runs through this file: the specification is the truth, and the engine is its judge. Each move
 * below is also a rule in aibi/layout.py, which checks it again when the draft is saved and once more when
 * it is committed. What is here is not a second authority — it is so that a person is never offered a move
 * the engine will refuse, and is told why when one is impossible.
 */
import { toast } from './ui.js';

export const state = {
  payload: null,       // what the engine last said about this project
  spec: null,          // the specification being edited
  token: 'none',       // the draft this page is a change to (§50.3)
  selected: null,
  // WHICH SCREEN. The Studio is four of them on one page — the builder, the copilot, the governed data, and
  // the releases — and the rail switches between them without leaving Superset's page.
  screen: 'design',   // design · ai · model · release
  pane: 'components', // the builder's left pane: components · templates · outline
  modelTab: 'overview',
  modelAsset: null,   // the dataset being read on the modelling screen
  schema: {},         // its columns, by dataset id — names, types, roles; never a row
  releaseTab: 'versions',
  dirty: false,
  saving: false,
  savedAt: null,
  loading: true,
  tab: 'validation',
  validation: null,
  report: null,
  lineage: {},
  changes: null,
  run: null,
  targets: null,            // where a version may be deployed — read when the drawer asks
  deployments: null,
  deploying: false,
  datasets: [],
  metrics: [],
  search: '',
  explorer: true,
  copilot: true,
  said: [],            // the conversation with the copilot, in order
  proposal: null,
  thinking: false,
  past: [],
  future: [],
};

let redraw = () => {};
let api = null;

export function wire(client, draw) {
  api = client;
  redraw = draw;
}

/* ── reading the specification ───────────────────────────────────────────────────────────────────────────── */
export const component = slot => (state.spec.components || []).find(c => c.slot === slot);
export const described = slot => (state.payload.components || []).find(c => c.slot === slot) || {};
export const slotOf = slot => (state.payload.template.slots || {})[slot] || {};
export const editable = () => Boolean(state.payload.project.may_edit && !state.payload.project.held);
export const title = slot => (component(slot) || {}).title || described(slot).title || slot;

export function minWidth(slot) {
  const least = state.payload.template.min_slot_width || {};
  const purpose = slotOf(slot).purpose;
  if (least[purpose] !== undefined) return least[purpose];
  return least.chart === undefined ? 1 : least.chart;
}

/** The rows as they are drawn: the specification's own layout, or the template's, with only the slots that
 *  hold a chart. The engine materialises it the same way (aibi/layout.py::materialise). */
export function rows() {
  if (!state.spec.layout || !state.spec.layout.length) state.spec.layout = rowsOf(state.spec);
  return state.spec.layout;
}

/** The same reading of ANY specification, without touching it — what a proposal would be laid out as. */
export function rowsOf(spec) {
  if (spec.layout && spec.layout.length) return spec.layout;
  const held = new Set((spec.components || []).map(c => c.slot));
  return (state.payload.template.rows || []).map(row => ({
    row: row.row, height: row.height,
    slots: row.slots.filter(place => held.has(place.id)).map(place => ({ id: place.id, width: place.width })),
  })).filter(row => row.slots.length);
}

export const rowOf = slot => rows().find(row => row.slots.some(place => place.id === slot));
export const sectionOfRow = name => ((state.payload.template.sections || [])
  .find(section => (section.rows || []).includes(name)) || {}).id;

/* ── changing it ─────────────────────────────────────────────────────────────────────────────────────────── */
export function change(what) {
  if (!editable()) return false;
  const before = JSON.stringify(state.spec);
  what();
  if (JSON.stringify(state.spec) === before) return false;
  state.past.push(before);
  if (state.past.length > 60) state.past.shift();
  state.future = [];
  state.dirty = true;
  state.validation = null;
  redraw();
  autosave();
  return true;
}

export function undo(which) {
  const from = which === 'undo' ? state.past : state.future;
  const to = which === 'undo' ? state.future : state.past;
  if (!from.length) return;
  to.push(JSON.stringify(state.spec));
  state.spec = JSON.parse(from.pop());
  state.dirty = true;
  redraw();
  autosave();
}

let pending = null;
let inflight = null;
export function autosave() {
  clearTimeout(pending);
  pending = setTimeout(save, 1200);
}

/** Save the draft. Resolves to whether what is on the page is now what the engine holds: false when the
 *  save was refused, so that a caller about to ask the engine something about the draft can refuse too. */
export function save() {
  if (state.saving) return inflight;
  if (!state.dirty || !editable()) return Promise.resolve(!state.dirty);
  clearTimeout(pending);
  state.saving = true;
  redraw();
  // what is sent, so that a move made while the save is on its way is not marked as saved with it
  const sent = JSON.stringify(state.spec);
  inflight = (async () => {
    try {
      const said = await api.save(JSON.parse(sent), state.token);
      state.token = said.token;
      state.savedAt = said.saved_at;
      state.dirty = JSON.stringify(state.spec) !== sent;
      state.payload.problems = said.problems;
      state.payload.layout_of = said.layout_of;
      if (state.dirty) autosave();
      return true;
    } catch (problem) {
      if (problem.status === 409) {
        toast('Someone else saved this draft since you opened it. Nothing of yours was saved.',
          { kind: 'bad', action: () => window.location.reload(), label: 'Reload', seconds: 0 });
      } else {
        toast(`Not saved: ${problem.message}`, { kind: 'bad', seconds: 0 });
      }
      return false;
    } finally {
      state.saving = false;
      inflight = null;
      redraw();
    }
  })();
  return inflight;
}

/** Make the engine hold what the page holds before asking it anything about the draft. The copilot, the
 *  gallery and the inspector's proposals are all made on the SAVED draft: asked with edits still unsaved,
 *  the engine would answer about a draft the person has already changed, and applying its answer would
 *  throw those edits away. Resolves to false when the draft could not be saved — and then nothing is asked. */
export async function settle() {
  clearTimeout(pending);
  if (state.saving && !(await inflight)) return false;
  if (!state.dirty) return true;
  return save();
}

/* ── the moves ───────────────────────────────────────────────────────────────────────────────────────────── */
export function moveWithin(slot, by) {
  change(() => {
    const row = rowOf(slot);
    const at = row.slots.findIndex(place => place.id === slot);
    const to = at + by;
    if (to < 0 || to >= row.slots.length) return;
    [row.slots[at], row.slots[to]] = [row.slots[to], row.slots[at]];
  });
}

export function moveRow(name, by) {
  change(() => {
    const all = rows();
    const at = all.findIndex(row => row.row === name);
    const to = at + by;
    if (to < 0 || to >= all.length) return;
    [all[at], all[to]] = [all[to], all[at]];
  });
}

export function resize(slot, by) {
  const row = rowOf(slot);
  const at = row.slots.findIndex(place => place.id === slot);
  const mine = row.slots[at];
  const other = row.slots[at + 1] || row.slots[at - 1];
  const spare = state.payload.template.grid.columns - row.slots.reduce((sum, p) => sum + p.width, 0);
  if (mine.width + by < minWidth(slot)) {
    toast(`${slot} is already as narrow as ${state.payload.template.id} allows a ${slotOf(slot).purpose}.`);
    return;
  }
  if (by > 0 && !other && spare < by) {
    toast(`${slot} has the whole row: there is nothing left to grow into.`);
    return;
  }
  if (by > 0 && other && other.width - by < minWidth(other.id)) {
    toast(`${other.id} cannot give up a column: it is at its smallest allowed width.`);
    return;
  }
  change(() => {
    mine.width += by;
    if (other) other.width -= by;
  });
}

export function taller(name, by) {
  change(() => {
    const row = rows().find(r => r.row === name);
    row.height = Math.max(6, Math.min(120, row.height + by));
  });
}

/* ── the dashboard's own fields ──────────────────────────────────────────────────────────────────────────
 * A filter, a title, a brand: what a specification says about the dashboard rather than about one chart.
 * Each is bounded by the template — how many filters it allows, which roles of column may be filtered on —
 * and the engine checks it again (dashboard_agent.validate).
 */
export function setFilter(column, on) {
  const template = state.payload.template;
  const already = state.spec.filters || [];
  if (on && already.length >= template.filters.max) {
    toast(`Template ${template.id} allows ${template.filters.max} filters on a dashboard. `
      + 'Take one off first.', { kind: 'bad' });
    return;
  }
  const offered = (state.payload.filter_options || []).find(option => option.column === column);
  if (on && offered && !offered.reaches_every_chart) {
    toast(`${column} is not a column of every dataset this dashboard uses, so the filter will not reach `
      + 'every chart. Validation says which.', { seconds: 8 });
  }
  change(() => {
    state.spec.filters = on ? [...already, column] : already.filter(name => name !== column);
  });
}

export function setDashboard(name, value) {
  change(() => {
    if (name === 'brand') {
      state.spec.theme = { ...(state.spec.theme || {}) };
      if (value) state.spec.theme.brand = value;
      else delete state.spec.theme.brand;
      return;
    }
    state.spec.dashboard = { ...state.spec.dashboard, [name]: value || undefined };
  });
}

/** A chart dragged to another row. The engine refuses it across sections — a section has an owner and
 *  filters of its own — so it is refused here too, with the same reason. */
export function drop(what, onto) {
  // two things can be dropped on a row: a chart that is already on the canvas, and a kind of chart from the
  // library. The second is not a move — it is an offer to fill a free slot of that row's section.
  if (String(what).startsWith('type:')) return String(what).slice(5);
  const slot = String(what).replace(/^slot:/, '');
  const from = rowOf(slot);
  if (!from || from.row === onto) return;
  if (sectionOfRow(from.row) !== sectionOfRow(onto)) {
    toast(`${slot} belongs to section ${sectionOfRow(from.row)} and that row is ${sectionOfRow(onto)}'s. `
      + 'A chart does not leave the section it is accountable to — with this template, every section is one row.',
      { kind: 'bad', seconds: 9 });
    return;
  }
  change(() => {
    const row = rows().find(r => r.row === onto);
    const taken = from.slots.splice(from.slots.findIndex(place => place.id === slot), 1)[0];
    row.slots.push(taken);
    state.spec.layout = rows().filter(r => r.slots.length);
  });
  return null;
}

/** A free slot of that row's section that accepts this kind of chart — what a drop from the library means. */
export function slotFor(kind, row) {
  const section = sectionOfRow(row);
  return (state.payload.free_slots || []).find(free => free.section === section
    && (free.accepts || []).includes(kind)) || null;
}

export function setField(slot, name, value) {
  change(() => {
    const target = component(slot);
    if (name === 'title') target.title = value || undefined;
    else if (name === 'description') target.description = value || undefined;
    else {
      target.options = target.options || {};
      if (value === '' || value === null) delete target.options[name];
      else target.options[name] = (name === 'row_limit' || name === 'bins') ? Number(value) : value;
    }
  });
}

/** Take a chart off the dashboard. It is kept in the specification, parked in its section, exactly as the
 *  copilot parks one — so a commit does not lose what it was. */
export function park(slot) {
  if (slotOf(slot).mandatory) {
    toast(`${slot} is mandatory in template ${state.payload.template.id}: it cannot be taken off.`, { kind: 'bad' });
    return;
  }
  const taken = component(slot);
  if (!taken) return;
  change(() => {
    const sectionId = described(slot).section;
    state.spec.sections = state.spec.sections || [];
    let section = state.spec.sections.find(s => s.id === sectionId);
    if (!section && sectionId) {
      section = { id: sectionId, parked: [] };
      state.spec.sections.push(section);
    }
    if (section) section.parked = [...(section.parked || []), taken];
    state.spec.components = state.spec.components.filter(c => c.slot !== slot);
    state.spec.layout = rows().map(row => ({ ...row, slots: row.slots.filter(p => p.id !== slot) }))
      .filter(row => row.slots.length);
    if (state.selected === slot) state.selected = null;
  });
  toast(`${title(slot)} is off the dashboard, kept in the specification.`,
    { action: () => undo('undo'), label: 'Undo' });
}

/** What the copilot or the gallery proposed, taken into the draft. The proposal itself changed nothing:
 *  this is an ordinary save, checked like any other. */
export function apply(proposal) {
  if (!proposal || !proposal.spec) return;
  change(() => { state.spec = JSON.parse(JSON.stringify(proposal.spec)); });
  state.proposal = null;
  state.said.push({ who: 'studio', text: `Applied: ${proposal.summary}` });
  toast('Applied to the draft.', { kind: 'good', action: () => undo('undo'), label: 'Undo' });
}
