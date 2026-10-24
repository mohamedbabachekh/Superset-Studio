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
  // A redraw rebuilds the panes, which throws away whatever was focused — and a search box that loses the
  // caret after every letter is a search box nobody can type in. What had focus is put back, with its
  // caret, as long as it has an id.
  const had = document.activeElement;
  const hadId = had && had.id && el.contains(had) ? had.id : null;
  const caret = hadId && typeof had.selectionStart === 'number' ? had.selectionStart : null;
  el.textContent = '';
  if (state.loading) {
    el.appendChild(skeleton());
    return;
  }
  const top = topBar(commands);
  el.appendChild(top);
  // the drawers of a narrow screen start below the bar, whose height depends on how far it wraps
  document.documentElement.style.setProperty('--ss-bar-h', `${top.offsetHeight || 56}px`);
  el.appendChild(tabRow(commands));
  banners(el, commands);
  el.appendChild(({ design: designScreen, ai: aiScreen, model: modelScreen,
    release: releaseScreen }[state.screen] || designScreen)(client, commands));
  el.appendChild(rail(commands));
  window.scrollTo(0, where);
  if (hadId) {
    const back = document.getElementById(hadId);
    if (back) {
      back.focus({ preventScroll: true });
      if (caret !== null && typeof back.setSelectionRange === 'function') {
        try { back.setSelectionRange(caret, caret); } catch (error) { /* a type that has no caret */ }
      }
    }
  }
}

/* ── the four screens ─────────────────────────────────────────────────────────────────────────────────────
 * One page, four screens, switched from the rail: the BUILDER (a dashboard being composed), the ASSISTANT
 * (the same proposal, with room to read it beside what it would make), the GOVERNED DATA (datasets, metrics,
 * the dbt behind them) and the RELEASES (versions, validation, environments, what was deployed where).
 *
 * They are screens and not pages because they are one project: the draft being edited, the proposal just
 * made and the version about to be released are the same thing seen from four sides, and a page load
 * between them would throw away the first.
 */
function designScreen(client, commands) {
  const panes = node('div', { class: 'ss-panes', 'data-explorer': state.explorer ? 'open' : 'closed',
    'data-copilot': state.copilot ? 'open' : 'closed' });
  if (state.explorer) panes.appendChild(leftPane(commands));
  panes.appendChild(middle(client, commands));
  if (state.copilot) panes.appendChild(rightPane(client, commands));
  return panes;
}

/* The rail (ADR 0023, and the owner's layouts): where a person goes from here. Four of these entries are
 * screens of this page; the rest are addresses — SharpSight's own pages, the ones this person may open (the
 * add-on puts them in the page, applying the same rule the menu obeys), and Superset's, which it refuses
 * itself if they are not theirs. It is a short cut, not a second application. */
const SUPERSET_PAGES = [
  ['home', 'Home', '/superset/welcome/'],
  ['datasets', 'Datasets', '/tablemodelview/list/'],
  ['charts', 'Charts', '/chart/list/'],
  ['dashboards', 'Dashboards', '/dashboard/list/'],
  ['sql', 'SQL Lab', '/sqllab/'],
];
const SHARPSIGHT_ICONS = { Projects: 'projects', 'BI Factory': 'ai', 'Data Quality': 'quality',
  'Impact Analysis': 'impact', Releases: 'releases', 'Audit trail': 'audit', 'Data models (dbt)': 'dbt' };

// what the rail holds, in the owner's own order. `screen` is a screen of this page; `page` is one of
// Superset's; `link` is a SharpSight page, shown only if this person was given it.
const RAIL = [
  ['home', 'Home', { page: '/superset/welcome/' }],
  ['projects', 'Projects', { link: 'Projects' }],
  ['datasets', 'Datasets', { page: '/tablemodelview/list/' }],
  ['metrics', 'Metrics', { screen: 'model', tab: 'metrics' }],
  ['charts', 'Charts', { page: '/chart/list/' }],
  ['dashboards', 'Dashboards', { screen: 'design' }],
  ['sql', 'SQL Lab', { page: '/sqllab/' }],
  ['modeling', 'Data Modeling', { screen: 'model' }],
  ['ai', 'AI Assistant', { screen: 'ai' }],
  ['deployments', 'Deployments', { screen: 'release' }],
  ['settings', 'Settings', { settings: true }],
];

function railLinks() {
  try { return JSON.parse(document.getElementById('sharpsight-studio').dataset.links || '[]'); }
  catch (error) { return []; }
}

function rail(commands) {
  const links = railLinks();
  const here = window.location.pathname;
  const link = (kind, label, href, current) => node('a', { class: 'ss-rail-link', href,
    'aria-current': current ? 'page' : false, title: label },
  icon(kind, { class: 'ss-rail-icon' }), node('span', { class: 'ss-rail-label', text: label }));
  // « Metrics » and « Data Modeling » are the same screen on two tabs, so only one of them is where you are
  const at = where => state.screen === where.screen
    && (where.tab ? state.modelTab === where.tab : !(where.screen === 'model' && state.modelTab === 'metrics'));
  const go = (kind, label, where) => node('button', { class: 'ss-rail-link', type: 'button',
    'aria-current': at(where) ? 'page' : false, title: label,
    on: { click: () => commands.screen(where.screen, where.tab) } },
  icon(kind, { class: 'ss-rail-icon' }), node('span', { class: 'ss-rail-label', text: label }));

  const main = node('div', { class: 'ss-rail-group' });
  RAIL.forEach(([kind, label, what]) => {
    if (what.screen) main.appendChild(go(kind, label, what));
    else if (what.page) main.appendChild(link(kind, label, what.page, false));
    else if (what.settings) {
      main.appendChild(node('button', { class: 'ss-rail-link', type: 'button', title: label,
        on: { click: () => settings(commands) } },
      icon(kind, { class: 'ss-rail-icon' }), node('span', { class: 'ss-rail-label', text: label })));
    } else {
      const found = links.find(one => one.name === what.link);
      if (found) main.appendChild(link(kind, label, found.href, here.startsWith(found.href)));
    }
  });
  // everything else this person may open in SharpSight: the record dashboards, dbt's documentation
  const rest = links.filter(one => one.name !== 'Projects');
  return node('nav', { class: 'ss-rail', 'aria-label': 'SharpSight' },
    node('div', { class: 'ss-logo' }, icon('studio', { class: 'ss-logo-mark' }),
      node('span', {}, node('strong', { text: 'SharpSight' }),
        node('span', { class: 'ss-logo-sub', text: 'BI Studio' }))),
    main,
    rest.length ? node('div', { class: 'ss-rail-group' },
      node('span', { class: 'ss-rail-head', text: 'More in SharpSight' }),
      rest.map(one => link(SHARPSIGHT_ICONS[one.name] || 'other', one.name, one.href,
        here.startsWith(one.href)))) : null,
    node('div', { class: 'ss-rail-group' },
      node('span', { class: 'ss-rail-head', text: 'Superset' }),
      SUPERSET_PAGES.slice(2).map(([kind, label, href]) => link(kind, label, href, false))));
}

/* The row under the bar: what is being worked on, and what can be done to it. Both belong to the screen —
 * the builder's tabs are areas of a dashboard, the releases' are stages of a release. */
const AREAS = [['design', 'Design'], ['data', 'Data'], ['filters', 'Filters'],
  ['interactions', 'Interactions'], ['parameters', 'Parameters']];
const MODEL_TABS = [['overview', 'Overview'], ['datasets', 'Datasets'], ['metrics', 'Metrics'],
  ['models', 'dbt Models'], ['lineage', 'Lineage'], ['tests', 'Tests']];
const RELEASE_TABS = [['versions', 'Version History'], ['validation', 'Validation'],
  ['environments', 'Environments'], ['releases', 'Releases']];

function tabRow(commands) {
  const of = {
    design: [AREAS, state.area || 'design', key => { state.area = key; }],
    model: [MODEL_TABS, state.modelTab, key => { state.modelTab = key; }],
    release: [RELEASE_TABS, state.releaseTab, key => { state.releaseTab = key; }],
    ai: [[], null, () => {}],
  }[state.screen] || [AREAS, state.area || 'design', key => { state.area = key; }];
  const [tabs, now, pick] = of;
  return node('div', { class: 'ss-areas', role: 'tablist', 'aria-label': 'What is being worked on' },
    tabs.map(([key, label]) => node('button', { type: 'button', role: 'tab', text: label,
      'aria-selected': now === key, on: { click: () => { pick(key); commands.redraw(); } } })),
    node('span', { class: 'ss-spacer' }),
    actions(commands));
}

function actions(commands) {
  const project = state.payload.project;
  if (state.screen === 'ai') {
    const made = state.proposal;
    return node('div', { class: 'ss-group' },
      button('Preview', () => window.open(project.dashboard, '_blank', 'noopener'),
        { disabled: !project.dashboard, title: 'The live dashboard in Superset, as it is now' }),
      button('Edit in Studio', () => { state.screen = 'design'; commands.redraw(); },
        { title: 'Back to the canvas' }),
      button('Apply', () => { act.apply(made); state.screen = 'design'; commands.redraw(); },
        { 'data-weight': 'primary',
          disabled: !made || !made.spec || !(made.validation || {}).passed,
          title: !made ? 'Ask for a change first'
            : !(made.validation || {}).passed ? 'This proposal would not pass validation'
              : 'Take this proposal into the draft — one undo takes it back' }));
  }
  if (state.screen === 'release') {
    return node('div', { class: 'ss-group' },
      button('Export (.ssbi)', commands.export_, {
        disabled: !(state.payload.versions || []).some(version => version.package),
        title: (state.payload.versions || []).some(version => version.package)
          ? 'The immutable package of the latest version — what a deployment carries'
          : 'No version of this project has a package yet' }),
      button('+ New Release', commands.commit, { 'data-weight': 'primary',
        disabled: !act.editable() || !state.savedAt || state.dirty,
        title: !act.editable() ? 'This project is read-only here'
          : state.dirty ? 'Save the draft first' : !state.savedAt ? 'There is no draft to release'
            : 'Make this draft a version, through the engine’s ten gates' }));
  }
  if (state.screen === 'model') {
    const docs = (railLinks().find(one => one.name === 'Data models (dbt)') || {}).href;
    return node('div', { class: 'ss-group' },
      button('Open dbt docs', () => window.open(docs, '_blank', 'noopener'),
        { disabled: !docs, title: docs ? 'dbt’s own documentation and lineage, as it was built'
          : 'dbt’s documentation is not one of the pages you were given' }));
  }
  return node('div', {}, node('div', { class: 'ss-group' },
    button('↶', () => act.undo('undo'), { 'data-weight': 'quiet', title: 'Undo (⌘Z)',
      'aria-label': 'Undo', disabled: !state.past.length }),
    button('↷', () => act.undo('redo'), { 'data-weight': 'quiet', title: 'Redo (⇧⌘Z)',
      'aria-label': 'Redo', disabled: !state.future.length }),
    button('⋯', commands.palette, { 'data-weight': 'quiet', 'aria-label': 'Everything else',
      title: 'Validate, add a chart, photograph the tiles, deploy… — every command, by name (⌘K)' })),
  node('div', { class: 'ss-group' },
    button('Preview', () => window.open(project.dashboard, '_blank', 'noopener'),
      { disabled: !project.dashboard,
        title: 'Open the live dashboard in Superset: filters, tooltips, cross-filters' }),
    button('Save', act.save, { 'data-weight': 'primary', disabled: !state.dirty || !act.editable(),
      title: state.dirty ? 'Save the draft now (⌘S)' : 'Nothing has changed since the last save' })));
}

const middle = (client, commands) => ({
  design: () => canvas(client, commands),
  data: () => dataArea(commands),
  filters: () => filtersArea(commands),
  interactions: () => interactionsArea(),
  parameters: () => parametersArea(),
}[state.area || 'design'] || (() => canvas(client, commands)))();

function dataArea(commands) {
  const used = [...new Set((state.spec.components || []).map(component => component.dataset))].filter(Boolean);
  const metrics = [...new Set((state.spec.components || []).map(component => component.metric))];
  const body = node('div', { class: 'ss-body' });
  body.appendChild(node('p', { class: 'ss-note', text:
    'What this dashboard reads, and where it comes from. A dataset is governed: it has an owner, a grain and '
    + 'a dbt model behind it, and who may read it decides who may open the dashboard.' }));
  used.forEach(id => {
    const dataset = (state.datasets || []).find(one => one.id === id) || { id };
    const lineage = state.lineage[id];
    body.appendChild(node('div', { class: 'ss-panel' },
      node('h4', {}, dataset.name || id, node('span', { class: 'ss-cert',
        'data-certified': Boolean(dataset.certified), text: dataset.certified ? ' ✓ certified' : ' ○ not certified' })),
      node('ul', { class: 'ss-gov' },
        pair('Id', id), pair('Owner', dataset.owner), pair('Domain', dataset.domain),
        pair('Grain', (dataset.grain || []).join(', ')),
        pair('Used by', (state.spec.components || []).filter(c => c.dataset === id).length + ' charts')),
      lineage === undefined
        ? button('Read its lineage', () => commands.lineage(id), { style: 'margin-top: 8px' })
        : node('div', { class: 'ss-code', text: JSON.stringify(lineage, null, 2).slice(0, 1200) })));
  });
  body.appendChild(node('h4', { style: 'margin-top: 14px', text: 'Metrics' }));
  metrics.forEach(id => {
    const metric = (state.metrics || []).find(one => one.id === id) || { id };
    body.appendChild(node('div', { class: 'ss-panel' },
      node('h4', {}, metric.name || id, node('span', { class: 'ss-cert',
        'data-certified': Boolean(metric.certified),
        text: metric.certified ? ' ✓ certified' : ' ⚠ not certified' })),
      node('p', { class: 'ss-note', text: metric.documentation || 'no documentation in the registry' }),
      node('ul', { class: 'ss-gov' }, pair('Owner', metric.owner), pair('Version', metric.version),
        pair('Broken down by', (metric.dimensions || []).join(', ')))));
  });
  return node('section', { class: 'ss-card' }, node('header', {}, 'Data — what this dashboard reads'), body);
}

function filtersArea(commands) {
  const offered = state.payload.filter_options || [];
  const used = state.spec.filters || [];
  const template = state.payload.template;
  const body = node('div', { class: 'ss-body' });
  body.appendChild(node('p', { class: 'ss-note', text:
    `${used.length} of ${template.filters.max} this template allows, in the roles it permits `
    + `(${template.filters.allowed_roles.join(', ')}). A filter is a column of a dataset this dashboard `
    + 'already reads; one that is not on every dataset will not reach every chart.' }));
  body.appendChild(node('div', { class: 'ss-pick' }, offered.map(option => node('label', {},
    node('input', { type: 'checkbox', checked: used.includes(option.column), disabled: !act.editable(),
      on: { change: event => act.setFilter(option.column, event.target.checked) } }),
    node('span', { class: 'ss-what' }, node('strong', { text: option.label }),
      node('span', { class: 'ss-faint', text: ` ${option.column} · ${option.role}` }),
      node('div', { class: 'ss-faint', text: option.reaches_every_chart
        ? `on every dataset this dashboard reads` : `only on ${option.datasets.join(', ')}` })),
    node('span', { class: 'ss-cert', 'data-certified': option.reaches_every_chart,
      text: option.reaches_every_chart ? '✓' : '○' })))));
  if (!offered.length) {
    body.appendChild(node('div', { class: 'ss-callout', text:
      'No column of this dashboard\u2019s datasets may be filtered on under this template.' }));
  }
  return node('section', { class: 'ss-card' }, node('header', {}, 'Filters — what a reader may narrow'), body);
}

function interactionsArea() {
  const used = state.spec.filters || [];
  const sections = state.payload.template.sections || [];
  return node('section', { class: 'ss-card' }, node('header', {}, 'Interactions'),
    node('div', { class: 'ss-body' },
      node('p', { class: 'ss-note', text:
        'What a specification carries about interaction, and what it does not. What it carries is scope: '
        + 'which charts a filter reaches. What it does not — cross-filtering, drill-through, a chart\u2019s '
        + 'own emitted filters — is Superset\u2019s own, set on the dashboard there, and a commit does not '
        + 'undo it because a specification never claimed it.' }),
      node('h4', { style: 'margin-top: 12px', text: 'The dashboard\u2019s filters' }),
      used.length
        ? node('ul', { class: 'ss-gov' }, used.map(column => pair(column, 'every chart that reads it')))
        : node('p', { class: 'ss-note', text: 'none' }),
      node('h4', { style: 'margin-top: 12px', text: 'Sections' }),
      node('ul', { class: 'ss-gov' }, sections.map(section => {
        const own = (state.spec.sections || []).find(one => one.id === section.id) || {};
        return pair(section.title || section.id, (own.filters || []).length
          ? `its own filters: ${own.filters.join(', ')}` : 'the dashboard\u2019s filters only');
      })),
      node('div', { class: 'ss-callout', 'data-kind': 'info', style: 'margin-top: 12px', text:
        'A section\u2019s own filter reaches that section\u2019s charts and no others. It is set in a '
        + 'specification, which today means on the command line or by the copilot — the Studio shows it.' })));
}

/* Parameters: what a dashboard is given rather than what it holds — the template it is bound to, the widths
 * it declares it will be read at, the theme, and the defaults a chart inherits. A specification carries
 * these; it carries no Jinja parameter, because nothing in this kit templates a governed query. */
function parametersArea() {
  const template = state.payload.template;
  const header = state.spec.dashboard || {};
  const theme_ = state.spec.theme || {};
  const grains = [...new Set((state.spec.components || [])
    .map(component => (component.options || {}).time_grain).filter(Boolean))];
  return node('section', { class: 'ss-card' }, node('header', {}, 'Parameters'),
    node('div', { class: 'ss-body' },
      node('div', { class: 'ss-panel' },
        node('h4', { text: 'The template it is bound to' }),
        node('ul', { class: 'ss-gov' },
          pair('Template', `${template.id} v${template.version}`),
          pair('Name', template.name),
          pair('Grid', `${template.grid.columns} columns, ${template.grid.row_height || '—'} per row unit`),
          pair('Sections', (template.sections || []).map(one => one.title || one.id).join(', ')),
          pair('Slots', Object.keys(template.slots || {}).length))),
      node('div', { class: 'ss-panel' },
        node('h4', { text: 'The widths it is read at' }),
        node('ul', { class: 'ss-gov' }, (template.viewports || []).length
          ? (template.viewports || []).map(one => pair(one.name || `${one.width}px`,
            `${one.width}px — ${one.columns || template.grid.columns} columns`))
          : [pair('—', 'this template declares none')]),
        node('p', { class: 'ss-note', text:
          'The canvas can be seen at each of them (the device row above the canvas): a dashboard is read on '
          + 'the width it is read on, not on the one it was built on.' })),
      node('div', { class: 'ss-panel' },
        node('h4', { text: 'Defaults' }),
        node('ul', { class: 'ss-gov' },
          pair('Theme', theme_.name || 'SharpSight’s own, from the engine'),
          pair('Time grains in use', grains.join(', ') || 'each chart’s own'),
          pair('Owner', header.owner),
          pair('Refresh', 'a tag on the dashboard in Superset (refresh:06:30, refresh:30m, refresh:off)'))),
      node('div', { class: 'ss-callout', 'data-kind': 'info', text:
        'There are no Jinja parameters here. A governed query is a dbt model and a registered metric, built '
        + 'before a dashboard asks for it — nothing on this page templates SQL, which is why nothing on this '
        + 'page can be made to read what you are not cleared for.' })));
}

function skeleton() {
  return node('div', { class: 'ss-panes', style: 'padding-top: 24px' },
    node('div', { class: 'ss-skeleton', style: 'height: 320px' }),
    node('div', { class: 'ss-skeleton', style: 'height: 460px' }),
    node('div', { class: 'ss-skeleton', style: 'height: 320px' }));
}

/* ── the command bar (§47.2) ─────────────────────────────────────────────────────────────────────────────── */
const who = () => (document.getElementById('sharpsight-studio').dataset.user || '?');
const initials = name => name.split(/[.\s_-]+/).filter(Boolean).slice(0, 2)
  .map(part => part[0].toUpperCase()).join('') || '?';

const AGO = at => {
  if (!at) return null;
  const seconds = Math.max(0, (Date.now() - new Date(at).getTime()) / 1000);
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  return new Date(at).toLocaleTimeString();
};

function topBar(commands) {
  const project = state.payload.project;
  const saved = state.saving ? 'Saving…' : state.dirty ? 'Unsaved changes'
    : state.savedAt ? `Draft saved ${AGO(state.savedAt)}` : 'No draft';
  const gate = { PROD: 'good', UAT: 'good', APPROVED_FOR_UAT: 'good', APPROVED_FOR_PROD: 'good',
    READY_FOR_REVIEW: 'warn', VALIDATION_FAILED: 'bad', UAT_FAILED: 'bad' }[project.gate];
  const projects = (railLinks().find(one => one.name === 'Projects') || {}).href;
  const search = node('input', { class: 'ss-topsearch', id: 'ss-search', type: 'search',
    placeholder: 'Search assets, metrics, charts…  (/)', value: state.search,
    'aria-label': 'Search assets, metrics and charts',
    on: { input: event => { state.search = event.target.value; commands.redraw(); } } });
  return node('div', { class: 'ss-bar' },
    projects ? node('a', { class: 'ss-btn ss-back', 'data-weight': 'quiet', href: projects,
      title: 'Every project', 'aria-label': 'Every project' }, icon('back', { class: 'ss-rail-icon' })) : null,
    node('div', { class: 'ss-crumb' }, node('span', { text: project.title || project.project_id })),
    tag(project.environment, 'env'),
    project.version && tag(`v${project.version}`),
    project.gate && tag(project.gate.replace(/_/g, ' ').toLowerCase(), gate),
    tag(saved, state.dirty ? 'warn' : state.savedAt ? 'good' : undefined),
    node('span', { class: 'ss-spacer' }),
    search,
    node('span', { class: 'ss-spacer' }),
    node('div', { class: 'ss-group' },
      button('⌘K', commands.palette, { 'data-weight': 'quiet', title: 'Every command, by name (⌘K)',
        'aria-label': 'Command palette' }),
      node('button', { class: 'ss-btn', 'data-weight': 'quiet', type: 'button',
        'aria-pressed': state.explorer, 'aria-label': 'The explorer', title: 'The explorer',
        on: { click: () => { state.explorer = !state.explorer; commands.redraw(); } } }, '◧'),
      node('button', { class: 'ss-btn', 'data-weight': 'quiet', type: 'button',
        'aria-pressed': state.copilot, 'aria-label': 'The copilot', title: 'The copilot',
        on: { click: () => { state.copilot = !state.copilot; commands.redraw(); } } }, '◨'),
      button('◐', () => { theme(themeNow() === 'dark' ? 'light' : 'dark'); commands.redraw(); },
        { 'data-weight': 'quiet', 'aria-pressed': themeNow() === 'dark', 'aria-label': 'Dark or light',
          title: 'Dark or light — kept in this browser' }),
      button('?', commands.help, { 'data-weight': 'quiet', title: 'Keyboard shortcuts (?)' })),
    node('span', { class: 'ss-avatar', title: `Signed in as ${who()}`, text: initials(who()) }));
}

/* Settings: what this page lets a person set, which is what a page may set — how it looks, and which panes
 * are open. Everything else about this project is the registry's or the engine's. */
function settings(commands) {
  modal({
    title: 'Settings',
    body: node('div', {},
      field('Theme', select(themeNow(), [['light', 'Light'], ['dark', 'Dark']],
        value => { theme(value); commands.redraw(); })),
      field('Panes', node('div', { class: 'ss-two' },
        node('label', { class: 'ss-check' },
          node('input', { type: 'checkbox', checked: state.explorer,
            on: { change: () => { state.explorer = !state.explorer; commands.redraw(); } } }),
          node('span', { text: 'The explorer, on the left' })),
        node('label', { class: 'ss-check' },
          node('input', { type: 'checkbox', checked: state.copilot,
            on: { change: () => { state.copilot = !state.copilot; commands.redraw(); } } }),
          node('span', { text: 'The inspector and the copilot, on the right' })))),
      node('p', { class: 'ss-note', style: 'margin-top: 10px', text:
        'The theme is kept in this browser, for you. Everything else about this project — who may open it, '
        + 'what it may read, what it may be drawn as — is the registry’s, and is changed there.' }),
      node('div', { class: 'ss-callout', 'data-kind': 'info', style: 'margin-top: 12px', text:
        'This workspace edits a SPECIFICATION, not a dashboard. Superset draws every chart and every '
        + 'dashboard; the Studio says what they should be, and the engine decides whether that may become a '
        + 'version. Nothing here approves and nothing here promotes.' })),
    footer: [button('Close', close, { 'data-weight': 'primary' })],
  });
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
/* The left pane, in three tabs — the owner's own layout: what can be put on a dashboard (Components), what
 * this one is bound to (Templates), and what is on it now (Outline). */
const LIBRARY = [
  ['Charts', ['line', 'bar', 'area', 'pie', 'map', 'table']],
  ['KPIs', ['big_number', 'gauge', 'progress']],
];
// a dashboard carries these, and they are not charts: a filter is a governed column, a row belongs to the
// template, and a text box is Superset's own furniture — each says so rather than being left out
const NOT_CHARTS = [['text', 'Text'], ['image', 'Image'], ['divider', 'Divider']];

function leftPane(commands) {
  const tabs = [['components', 'Components'], ['templates', 'Templates'], ['outline', 'Outline']];
  const which = state.pane || 'components';
  const body = node('div', { class: 'ss-body' });
  body.appendChild(node('input', { class: 'ss-search', id: 'ss-search-panel', type: 'search',
    placeholder: 'Search components…', value: state.search, 'aria-label': 'Search this panel',
    on: { input: event => { state.search = event.target.value; commands.redraw(); } } }));
  ({ components: componentsTab, templates: templatesTab, outline: outlineTab }[which]
    || componentsTab)(body, commands);
  return node('section', { class: 'ss-card ss-pane' },
    node('header', {}, node('div', { class: 'ss-tabs' }, tabs.map(([key, label]) =>
      node('button', { type: 'button', text: label, 'aria-selected': which === key,
        on: { click: () => { state.pane = key; commands.redraw(); } } })))),
    body);
}

const matches = text => !state.search || String(text).toLowerCase().includes(state.search.toLowerCase());

function kindCard(kind, commands) {
  const free = (state.payload.free_slots || []).filter(slot => (slot.accepts || []).includes(kind.id));
  const ready = kind.status === 'available' && free.length && act.editable();
  const where = kind.status !== 'available' ? 'absent' : free.length ? 'free' : 'full';
  return node('button', { class: 'ss-kind', type: 'button', 'data-kind': kind.id, 'data-state': where,
    draggable: ready, title: ready
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
  node('span', { class: 'ss-kind-state', text: where === 'absent' ? 'not installed'
    : where === 'free' ? `${free.length} free` : 'full' }));
}

function group(body, title, cards) {
  if (!cards.length) return;
  body.appendChild(node('h3', { class: 'ss-group-head', text: title }));
  body.appendChild(node('div', { class: 'ss-cards' }, cards));
}

function componentsTab(body, commands) {
  const library = state.payload.visualizations || [];
  const named = new Set(LIBRARY.flatMap(([, kinds]) => kinds));
  LIBRARY.forEach(([family, kinds]) => group(body, family, kinds
    .map(id => library.find(kind => kind.id === id))
    .filter(kind => kind && matches(kind.id))
    .map(kind => kindCard(kind, commands))));
  group(body, 'Other charts', library.filter(kind => !named.has(kind.id) && matches(kind.id))
    .map(kind => kindCard(kind, commands)));

  // Filters: a governed column the template allows, put on the dashboard — not a chart, and not dragged
  const options = (state.payload.filter_options || []).filter(one => matches(one.label) || matches(one.column));
  const used = state.spec.filters || [];
  group(body, 'Filters', options.slice(0, 12).map(option => node('button', { class: 'ss-kind',
    type: 'button', 'data-kind': option.role, 'data-state': used.includes(option.column) ? 'full' : 'free',
    disabled: !act.editable(),
    title: used.includes(option.column) ? `${option.column} is already a filter of this dashboard`
      : `Put ${option.column} on this dashboard as a filter`,
    on: { click: () => act.setFilter(option.column, !used.includes(option.column)) } },
  icon(option.role === 'time' ? 'date' : option.role === 'measure' ? 'range' : 'dropdown',
    { class: 'ss-kind-icon' }),
  node('span', { class: 'ss-kind-name', text: option.label || option.column }),
  node('span', { class: 'ss-kind-state', text: used.includes(option.column) ? 'on the dashboard' : 'add' }))));

  // Layout: the rows are the template's, and that is the whole point of a template
  const rows = act.rows().filter(row => matches(row.row));
  group(body, 'Layout', rows.map(row => {
    const used_ = row.slots.reduce((sum, place) => sum + place.width, 0);
    const columns = state.payload.template.grid.columns;
    return node('button', { class: 'ss-kind', type: 'button', 'data-kind': 'row',
      'data-state': used_ < columns ? 'free' : 'full',
      title: `Row ${row.row}: ${used_} of ${columns} columns, height ${row.height}. The template owns the `
        + 'rows; what you may change is what is on them, and how wide each is.',
      on: { click: () => { state.area = 'design'; commands.select(row.slots[0] && row.slots[0].id); } } },
    icon('row', { class: 'ss-kind-icon' }),
    node('span', { class: 'ss-kind-name', text: row.row }),
    node('span', { class: 'ss-kind-state', text: `${used_}/${columns}` }));
  }));

  group(body, 'Others', NOT_CHARTS.filter(([id]) => matches(id)).map(([id, label]) =>
    node('button', { class: 'ss-kind', type: 'button', 'data-kind': id, 'data-state': 'absent',
      title: `A specification has no place for a ${label.toLowerCase()}: it says what is MEASURED. Superset’s `
        + 'own dashboard editor can add one, and the next build will not keep it.',
      on: { click: () => toast(`A specification carries no ${label.toLowerCase()}: it says what a dashboard `
        + 'measures, and the next build would not keep one.', { kind: 'bad' }) } },
    icon(id, { class: 'ss-kind-icon' }),
    node('span', { class: 'ss-kind-name', text: label }),
    node('span', { class: 'ss-kind-state', text: 'not carried' }))));
}

function templatesTab(body, commands) {
  const template = state.payload.template;
  body.appendChild(node('div', { class: 'ss-panel' },
    node('h4', {}, template.name || template.id, node('span', { class: 'ss-tag', 'data-kind': 'good',
      style: 'margin-left: 8px', text: `v${template.version}` })),
    node('ul', { class: 'ss-gov' },
      pair('Id', template.id),
      pair('Grid', `${template.grid.columns} columns`),
      pair('Sections', (template.sections || []).length),
      pair('Slots', Object.keys(template.slots || {}).length),
      pair('Filters', `up to ${(template.filters || {}).max || '—'}`))));
  (template.sections || []).forEach(section => {
    const slots = Object.entries(template.slots || {})
      .filter(([slot]) => (act.rows().find(row => (section.rows || []).includes(row.row)
        && row.slots.some(place => place.id === slot))))
      .filter(([slot]) => matches(slot));
    if (!slots.length) return;
    body.appendChild(node('div', { class: 'ss-panel' },
      node('h4', { text: section.title || section.id }),
      node('ul', { class: 'ss-gov' }, slots.map(([slot, defined]) => pair(slot,
        `${defined.purpose} · ${(defined.accepts || []).join(', ')}${defined.mandatory ? ' · mandatory' : ''}`)))));
  });
  body.appendChild(node('div', { class: 'ss-callout', 'data-kind': 'info', text:
    'A project is bound to its template when it is created, and keeps it: the template is what makes a '
    + 'dashboard reviewable — the slots that must be filled, what each may be drawn as, and the sections a '
    + 'chart is accountable to. Another template is another project.' }));
}

function outlineTab(body, commands) {
  const match = matches;
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
  tree.appendChild(node('h3', {}, node('span', { text: 'Datasets' }),
    node('span', { class: 'ss-faint', text: String(state.datasets.length) })));
  state.datasets.filter(d => match(d.id) || match(d.name)).slice(0, 30).forEach(dataset =>
    tree.appendChild(node('li', { class: 'ss-sub', title: dataset.description || '' },
      node('button', { class: 'ss-name', type: 'button', text: dataset.id,
        on: { click: () => commands.openAsset(dataset.id) } }),
      node('span', { class: 'ss-cert', 'data-certified': Boolean(dataset.certified),
        text: dataset.certified ? '✓' : '○' }))));
  tree.appendChild(node('h3', {}, node('span', { text: 'Metrics' }),
    node('span', { class: 'ss-faint', text: String(state.metrics.length) })));
  state.metrics.filter(m => match(m.id) || match(m.name)).slice(0, 30).forEach(metric =>
    tree.appendChild(node('li', { class: 'ss-sub', title: metric.documentation || '' },
      node('span', { class: 'ss-name', text: metric.id }),
      node('span', { class: 'ss-cert', 'data-certified': Boolean(metric.certified),
        text: metric.certified ? '✓' : '○' }))));
  body.appendChild(tree);
}

/* ── the canvas (§47.4) ──────────────────────────────────────────────────────────────────────────────────── */
function canvas(client, commands) {
  return state.mode === 'live' ? liveCanvas(commands) : composeCanvas(client, commands);
}

/* LIVE: the dashboard Superset is drawing, inside the workspace, with the Studio's own chrome over it.
 *
 * There is no second renderer here and no second copy of anything: the frame is Superset's own standalone
 * dashboard (the address visual QA photographs), and the boxes drawn over it are read from ITS OWN DOM —
 * same origin, so the holders can be measured rather than guessed. Clicking one selects that chart in the
 * inspector; what the inspector then changes is the draft, which is not in this frame until it is committed,
 * and the banner says so.
 */
function liveCanvas(commands) {
  const dashboard = state.payload.project.dashboard;
  if (!dashboard) {
    return node('section', { class: 'ss-card' }, node('header', {}, 'Canvas — live'),
      node('div', { class: 'ss-body' }, node('div', { class: 'ss-callout', text:
        'This project has no dashboard in Superset yet: there is nothing live to show.' })));
  }
  const overlay = node('div', { class: 'ss-overlay' });
  const frame = node('iframe', { class: 'ss-live-frame', src: `${dashboard.split('?')[0]}?standalone=1`,
    title: 'The dashboard, as Superset draws it', loading: 'eager' });
  const byChart = {};
  (state.payload.components || []).forEach(component => {
    if (component.chart_id) byChart[component.chart_id] = component.slot;
  });

  const sync = () => {
    let holders = [];
    try {
      const inside = frame.contentDocument;
      if (!inside) return;
      holders = Array.from(inside.querySelectorAll('.dashboard-component-chart-holder'));
    } catch (error) {
      overlay.textContent = '';
      return;                                   // another origin one day: the frame still shows the dashboard
    }
    overlay.textContent = '';
    holders.forEach(holder => {
      const slice = holder.querySelector('[data-test-chart-id]');
      const slot = slice && byChart[Number(slice.getAttribute('data-test-chart-id'))];
      if (!slot) return;
      const box = holder.getBoundingClientRect();
      overlay.appendChild(node('button', { class: 'ss-box', type: 'button', 'data-slot': slot,
        'aria-selected': state.selected === slot, 'aria-label': `${act.title(slot)}, select it`,
        style: `left: ${box.left}px; top: ${box.top}px; width: ${box.width}px; height: ${box.height}px`,
        on: { click: () => commands.select(slot) } },
      node('span', { class: 'ss-label', text: `${slot} · ${slotOf(slot).purpose || ''}` }),
      state.selected === slot ? node('span', { class: 'ss-handles' },
        ['nw', 'n', 'ne', 'w', 'e', 'sw', 's', 'se'].map(where =>
          node('i', { class: 'ss-handle', 'data-at': where }))) : null));
    });
    overlay.dataset.boxes = String(overlay.children.length);
  };

  frame.addEventListener('load', () => {
    sync();
    [400, 1200, 2500].forEach(wait => setTimeout(sync, wait));   // charts settle after their own queries
    try {
      frame.contentWindow.addEventListener('scroll', sync, { passive: true });
      frame.contentWindow.addEventListener('resize', sync, { passive: true });
    } catch (error) { /* another origin: the boxes simply do not follow */ }
  });

  // a draft exists the moment one is saved in this session, even if the payload was read before it
  const stale = state.payload.editing === 'draft' || state.dirty || Boolean(state.savedAt);
  return node('section', { class: 'ss-card', style: 'padding: 6px' },
    node('header', {},
      node('span', { text: 'Canvas — the dashboard Superset is drawing' }),
      node('span', { class: 'ss-spacer' }),
      mode(commands),
      button('Open in Superset', () => window.open(dashboard, '_blank', 'noopener'), { 'data-weight': 'quiet' })),
    node('div', { class: 'ss-body', style: 'padding: 8px' },
      stale ? node('div', { class: 'ss-callout', style: 'margin-bottom: 8px', text:
        'This is what Superset holds. Your draft is not in it until it is committed — Compose shows the '
        + 'draft.' }) : null,
      node('div', { class: 'ss-live' }, frame, overlay)));
}

const mode = commands => node('div', { class: 'ss-switch', role: 'group', 'aria-label': 'How the canvas shows it' },
  ['compose', 'live'].map(which => node('button', { type: 'button', 'data-weight': 'quiet',
    class: 'ss-btn', 'aria-pressed': (state.mode || 'compose') === which,
    title: which === 'compose' ? 'The draft, as a layout you can move' : 'The dashboard, as Superset draws it',
    text: which === 'compose' ? 'Compose' : 'Live',
    on: { click: () => { state.mode = which; commands.redraw(); } } })));

function composeCanvas(client, commands) {
  const sections = state.payload.template.sections || [];
  const inner = node('div', { class: 'ss-canvas' });
  // the dashboard as a reader will meet it: its title, and the filters it carries as the chips they become
  inner.appendChild(node('div', { class: 'ss-sheet' },
    node('h2', { text: state.spec.dashboard.title }),
    node('span', { class: 'ss-faint', text: state.spec.dashboard.description || '' }),
    node('div', { class: 'ss-chipbar' }, (state.spec.filters || []).map(column => {
      const option = (state.payload.filter_options || []).find(one => one.column === column) || {};
      return node('span', { class: 'ss-filterchip',
        title: 'A filter this dashboard carries — set them in the Filters area',
        text: `${option.label || column}: all` });
    }))));
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
  const widest = Math.max(0, ...(state.payload.template.viewports || []).map(one => one.width));
  const devices = [['desktop', 'Fit to the pane', 0], ['tablet', 'Tablet, 1024', 1024],
    ['mobile', 'Phone, 420', 420]].concat(widest ? [['desktop', `Widest, ${widest}`, widest]] : []);
  return node('section', { class: 'ss-card', style: 'padding: 6px' },
    node('header', {},
      node('span', { text: state.payload.layout_of === 'specification'
        ? 'Canvas — the layout this project placed' : `Canvas — the template's own layout` }),
      node('span', { class: 'ss-spacer' }),
      node('span', { class: 'ss-faint', text: tilesTaken() }),
      mode(commands),
      node('div', { class: 'ss-devices', role: 'group', 'aria-label': 'The width it is read at' },
        devices.map(([kind, label, width]) => node('button', { class: 'ss-btn', 'data-weight': 'quiet',
          type: 'button', title: label, 'aria-label': label, 'aria-pressed': viewport === width,
          on: { click: () => { state.viewport = width; commands.redraw(); } } },
        icon(kind, { class: 'ss-device-icon' })))),
      node('div', { class: 'ss-zoom' },
        node('button', { class: 'ss-btn', 'data-weight': 'quiet', type: 'button', text: '−',
          'aria-label': 'Zoom out', title: 'Zoom out',
          on: { click: () => { state.zoom = Math.max(0.5, zoom - 0.1); commands.redraw(); } } }),
        node('span', { class: 'ss-faint', style: 'min-width: 34px; text-align: center',
          text: `${Math.round(zoom * 100)}%` }),
        node('button', { class: 'ss-btn', 'data-weight': 'quiet', type: 'button', text: '+',
          'aria-label': 'Zoom in', title: 'Zoom in',
          on: { click: () => { state.zoom = Math.min(1, zoom + 0.1); commands.redraw(); } } }))),
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
  const tabs = { data: 'Data', style: 'Style', interactions: 'Interactions', governance: 'Governance' };
  const body = node('div', { class: 'ss-body' });
  const which = state.inspector || 'data';
  const metric = (state.metrics || []).find(one => one.id === own.metric) || {};

  if (which === 'data') {
    // What a chart MEASURES is the registry's, so none of these edits the draft: each asks for a proposal,
    // which says whether it would pass and is applied — or not — by hand.
    const datasets = (state.datasets || []).map(dataset => [dataset.id,
      `${dataset.id}${dataset.certified ? '' : '  (not certified)'}`]);
    body.appendChild(field('Dataset', select(own.dataset || '',
      datasets.length ? datasets : [own.dataset || ''], value => commands.alter({ slot, dataset: value }),
      { disabled: !act.editable() })));
    body.appendChild(field('Chart type', select(own.type, (said.accepts || [own.type]),
      value => commands.alter({ slot, type: value }), { disabled: !act.editable() })));
    const metrics = (state.metrics || []).map(one => [one.id,
      `${one.name || one.id}${one.certified ? '' : '  (not certified)'}`]);
    body.appendChild(field('Metrics', node('div', {},
      select(own.metric, metrics.length ? metrics : [own.metric],
        value => commands.alter({ slot, metric: value }), { disabled: !act.editable() }),
      node('button', { class: 'ss-add', type: 'button', disabled: true,
        title: 'A chart on this template carries one governed metric. Two metrics on one chart is a second '
          + 'chart, on a slot that accepts it.' }, '+ Add metric'))));
    const dimensions = [...new Set([own.dimension, ...(metric.dimensions || [])])].filter(Boolean);
    body.appendChild(field('Broken down by', select(own.dimension || '',
      [['', '— none —'], ...dimensions], value => commands.alter({ slot, dimension: value }),
      { disabled: !act.editable() || ['big_number', 'histogram'].includes(own.type) })));
    body.appendChild(field('Time column', node('input', { value: (governance.dataset_grain || [])[0] || '',
      disabled: true, title: 'The time grain of the governed dataset: the registry declares it, and a '
        + 'dashboard reads it' })));
    body.appendChild(field('Time grain', select((own.options || {}).time_grain, ['', 'P1D', 'P1W', 'P1M'],
      value => act.setField(slot, 'time_grain', value),
      { disabled: !act.editable() || !['line', 'area'].includes(own.type) })));
    const filters = state.spec.filters || [];
    body.appendChild(field('Filters', node('div', {},
      filters.length
        ? node('div', { class: 'ss-chipbar' }, filters.map(column => node('span', { class: 'ss-filterchip',
          text: column })))
        : node('p', { class: 'ss-note', text: 'this dashboard carries none' }),
      node('button', { class: 'ss-add', type: 'button', disabled: !act.editable(),
        title: 'A filter belongs to the dashboard, not to one chart: it reaches every chart that reads its '
          + 'column', on: { click: () => { state.area = 'filters'; commands.redraw(); } } }, '+ Add filter'))));
    body.appendChild(node('p', { class: 'ss-note', style: 'margin-top: 10px', text:
      'The dataset, the chart type, the metric and the breakdown ask for a proposal rather than editing the '
      + 'draft: the engine checks the metric is registered and valid on the dataset, that the slot accepts '
      + 'the chart, and that you are cleared for it. You see what it would do before it does it.' }));
  }
  if (which === 'style') {
    body.appendChild(field('Title', node('input', { value: own.title || '', maxlength: 80,
      disabled: !act.editable(), on: { change: e => act.setField(slot, 'title', e.target.value) } })));
    body.appendChild(field('What it is for', node('textarea', { rows: 2, maxlength: 300,
      disabled: !act.editable(), on: { change: e => act.setField(slot, 'description', e.target.value) } },
    own.description || '')));
    // the kinds this slot accepts, as a row: switching is one click, and it is a proposal like any other
    body.appendChild(node('div', { class: 'ss-kindrow', role: 'group', 'aria-label': 'How it is drawn' },
      (said.accepts || []).map(kind => node('button', { class: 'ss-kindbtn', type: 'button',
        'data-kind': kind, 'aria-pressed': own.type === kind, title: own.type === kind
          ? `${kind} — how it is drawn now` : `Propose drawing it as a ${kind}`,
        disabled: !act.editable() || own.type === kind,
        on: { click: () => commands.alter({ slot, type: kind }) } },
      icon(kind, { class: 'ss-kindbtn-icon' }), node('span', { text: kind })))));
    body.appendChild(node('div', { class: 'ss-two' },
      field('Top N', node('input', { type: 'number', min: 1, max: 1000, value: (own.options || {}).row_limit || '',
        disabled: !act.editable() || !['bar', 'table', 'pie'].includes(own.type),
        on: { change: e => act.setField(slot, 'row_limit', e.target.value) } })),
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
    if (act.editable() && !said.mandatory) {
      body.appendChild(node('div', { style: 'margin-top: 4px' },
        button('Take off the dashboard', () => act.park(slot),
          { title: 'It is kept in the specification, parked in its section (Backspace)' })));
    }
    body.appendChild(node('p', { class: 'ss-note', style: 'margin-top: 10px', text:
      'A colour, a legend, an axis or an ECharts option is Superset’s own: a specification has no place '
      + 'for it, so a commit sets it back. Explore is open; what it changes does not last.' }));
  }
  if (which === 'interactions') {
    const filters = state.spec.filters || [];
    const reached = filters.filter(column => (state.payload.filter_options || [])
      .some(option => option.column === column
        && (option.reaches_every_chart || (option.datasets || []).includes(own.dataset))));
    body.appendChild(node('ul', { class: 'ss-gov' },
      pair('Reached by', reached.join(', ') || 'no filter of this dashboard'),
      pair('Not reached by', filters.filter(column => !reached.includes(column)).join(', ') || '—'),
      pair('Section', said.section),
      pair('Its section’s own filters', ((state.spec.sections || [])
        .find(one => one.id === said.section) || {}).filters?.join(', ') || 'none')));
    body.appendChild(node('p', { class: 'ss-note', style: 'margin-top: 10px', text:
      'A filter reaches a chart when the chart’s dataset carries its column — that is all a specification '
      + 'decides about interaction. Cross-filtering, drill-through and a chart’s own emitted filters are '
      + 'Superset’s own: set them on the dashboard there, and a commit will not undo them, because a '
      + 'specification never claimed them.' }));
    body.appendChild(node('div', { style: 'margin-top: 10px' },
      button('The dashboard’s filters', () => { state.area = 'filters'; commands.redraw(); })));
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
    body.appendChild(node('div', { style: 'margin-top: 10px' },
      button('Where it comes from', () => commands.openAsset(own.dataset), { disabled: !own.dataset,
        title: 'The dbt model behind this dataset, its columns and its tests — on the Data Modeling screen' })));
    body.appendChild(node('p', { class: 'ss-note', style: 'margin-top: 10px', text:
      'Who may open the dashboard this becomes is decided from these: the roles granted every dataset it '
      + 'uses, and the row-level rules that follow them. No sample of the data is shown anywhere in this '
      + 'workspace, on purpose.' }));
  }

  return node('section', { class: 'ss-card' },
    node('header', {}, node('span', { text: 'Chart Properties' }),
      node('span', { class: 'ss-faint', text: slot })),
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
/* ── the assistant's own screen (§47.6, and the owner's layout 2) ─────────────────────────────────────────
 * The same copilot as the pane, given the room to be read: what was asked, the plan it answers with, and —
 * beside it — the dashboard that plan would make. It proposes and never applies; « Apply » is in the tab
 * row above, and it is an ordinary draft save going through the same checks as any other.
 */
function aiScreen(client, commands) {
  return node('div', { class: 'ss-ai' }, assistant(commands), proposed(client, commands));
}

function assistant(commands) {
  const made = state.proposal;
  const body = node('div', { class: 'ss-body' });
  if (!state.said.length) {
    body.appendChild(node('p', { class: 'ss-note', text:
      'Ask for a dashboard, or for a change to this one, in your own words. What comes back is a PROPOSAL: '
      + 'what it would change, what it touches, and whether it would pass validation.' }));
  }
  state.said.forEach(said => body.appendChild(node('div', { class: 'ss-said', 'data-who': said.who },
    said.who === 'ai' ? icon('ai', { class: 'ss-said-mark' }) : null,
    node('span', { text: said.text }))));
  if (made) {
    if (made.understood) {
      body.appendChild(node('div', { class: 'ss-said', 'data-who': 'ai' },
        icon('ai', { class: 'ss-said-mark' }),
        node('span', { text: `Here is the plan: ${made.understood}` })));
    }
    if ((made.changes || []).length) {
      body.appendChild(node('ol', { class: 'ss-plan' }, made.changes.map(change =>
        node('li', { 'data-done': 'true' }, node('span', { class: 'ss-plan-mark', text: '✓' }),
          node('span', { text: change.says })))));
    }
    body.appendChild(proposal(commands));
  }
  if (!made) {
    body.appendChild(node('div', { class: 'ss-chips' }, (state.payload.ask || []).map(example =>
      node('button', { class: 'ss-chip', type: 'button', text: example,
        on: { click: () => commands.propose(example) } }))));
  }
  const box = node('textarea', { class: 'ss-search', rows: 2, id: 'ss-ask-screen',
    placeholder: 'Ask for changes, explanations or new visualizations…',
    disabled: !act.editable() || state.thinking,
    on: { keydown: event => {
      if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); commands.propose(box.value); }
    } } });
  body.appendChild(box);
  body.appendChild(node('div', { style: 'display: flex; gap: 8px; margin-top: 8px; align-items: center' },
    button(state.thinking ? 'Reading…' : 'Propose', () => commands.propose(box.value),
      { 'data-weight': 'primary', disabled: !act.editable() || state.thinking }),
    node('span', { class: 'ss-faint', text: 'Enter to ask' })));
  body.appendChild(node('div', { class: 'ss-callout', 'data-kind': 'info', style: 'margin-top: 12px', text:
    'This copilot is deterministic: no language model has been chosen for this kit, which calls nobody '
    + '(aibi/llm.py is the seam). It reads the same governed vocabulary the planner reads, it proposes and '
    + 'never changes anything by itself, and what it does not understand it says it does not understand.' }));
  return node('section', { class: 'ss-card ss-chatpane' },
    node('header', {}, icon('ai', { class: 'ss-kind-icon' }), node('span', { text: 'AI Copilot' }),
      node('span', { class: 'ss-spacer' }),
      button('+ New Chat', () => { state.said = []; state.proposal = null; commands.redraw(); },
        { 'data-weight': 'quiet', disabled: !state.said.length && !state.proposal })),
    body);
}

/* The dashboard the proposal would make, drawn the way the canvas draws this one: real tiles where a chart
 * already exists (a photograph of what Superset drew), and a named placeholder where it would be new. */
function proposed(client, commands) {
  const made = state.proposal;
  const spec = made && made.spec ? made.spec : state.spec;
  const rows = act.rowsOf(spec);
  const taken = state.payload.pictures;
  const inner = node('div', { class: 'ss-canvas' });
  inner.appendChild(node('div', { class: 'ss-sheet' },
    node('h2', { text: (spec.dashboard || {}).title || '' }),
    node('span', { class: 'ss-faint', text: (spec.dashboard || {}).description || '' })));
  rows.forEach(row => {
    const line = node('div', { class: 'ss-row', 'data-row': row.row, style: `--h: ${row.height}` });
    row.slots.forEach(place => {
      const component = (spec.components || []).find(one => one.slot === place.id) || {};
      const had = (state.spec.components || []).some(one => one.slot === place.id);
      const shot = had && taken && !(taken.missing || []).includes(place.id)
        ? client.picture(place.id, taken.taken_at) : null;
      line.appendChild(node('div', { class: 'ss-tile', 'data-slot': place.id, 'data-new': !had,
        style: `--w: ${place.width}` },
      node('div', { class: 'ss-tile-head' }, node('span', { text: component.title || place.id }),
        !had ? node('span', { class: 'ss-tag', 'data-kind': 'good', text: 'new' }) : null),
      shot ? node('img', { class: 'ss-shot', src: shot, alt: '' })
        : node('div', { class: 'ss-tile-empty' }, icon(component.type || 'other', { class: 'ss-kind-icon' }),
          node('span', { class: 'ss-faint', text: component.metric || '—' }))));
    });
    inner.appendChild(line);
  });
  return node('section', { class: 'ss-card' },
    node('header', {}, node('span', { text: (spec.dashboard || {}).title || 'The dashboard' }),
      made && made.spec ? node('span', { class: 'ss-tag', 'data-kind': 'good',
        text: '✦ proposed by SharpSight AI' }) : node('span', { class: 'ss-faint', text: 'as it is now' }),
      node('span', { class: 'ss-spacer' }),
      button('Open in Superset', () => window.open(state.payload.project.dashboard, '_blank', 'noopener'),
        { 'data-weight': 'quiet', disabled: !state.payload.project.dashboard })),
    node('div', { class: 'ss-body', style: 'padding: 10px' }, inner));
}

/* ── the governed data (the owner's layout 3) ─────────────────────────────────────────────────────────────
 * Datasets, metrics and the dbt behind them — read-only, because every one of them is the registry's. What
 * is NOT here is a row of data: the engine can sample a dataset for a tool call, and this page is given no
 * way to ask (ADR 0013). What a column IS — its name, type, role, classification — is metadata, and is here.
 */
function modelScreen(client, commands) {
  const chosen = state.modelAsset
    || (state.spec.components || []).map(one => one.dataset).find(Boolean)
    || (state.datasets[0] || {}).id;
  return node('div', { class: 'ss-panes', 'data-explorer': 'open', 'data-copilot': 'open' },
    modelTree(chosen, commands), modelMiddle(chosen, commands), modelDetails(chosen, commands));
}

function modelTree(chosen, commands) {
  const body = node('div', { class: 'ss-body' });
  body.appendChild(node('input', { class: 'ss-search', id: 'ss-search-models', type: 'search',
    placeholder: 'Search models…', value: state.search, 'aria-label': 'Search models',
    on: { input: event => { state.search = event.target.value; commands.redraw(); } } }));
  const byDomain = {};
  state.datasets.filter(one => matches(one.id) || matches(one.name) || matches(one.domain))
    .forEach(dataset => {
      const domain = dataset.domain || 'other';
      byDomain[domain] = byDomain[domain] || [];
      byDomain[domain].push(dataset);
    });
  const tree = node('ul', { class: 'ss-tree' });
  Object.entries(byDomain).forEach(([domain, datasets]) => {
    tree.appendChild(node('li', { class: 'ss-section-head' }, domain));
    datasets.forEach(dataset => tree.appendChild(node('li', { class: 'ss-sub',
      'aria-selected': chosen === dataset.id, title: dataset.description || '' },
    node('button', { class: 'ss-name', type: 'button', text: dataset.id,
      on: { click: () => commands.openAsset(dataset.id) } }),
    node('span', { class: 'ss-cert', 'data-certified': Boolean(dataset.certified),
      text: dataset.certified ? '✓' : '○' }))));
  });
  if (!state.datasets.length) {
    tree.appendChild(node('li', { class: 'ss-sub' }, node('span', { class: 'ss-faint',
      text: 'no governed dataset you may read' })));
  }
  body.appendChild(tree);
  return node('section', { class: 'ss-card ss-pane' },
    node('header', {}, node('span', { text: 'Models' }),
      node('span', { class: 'ss-faint', text: String(state.datasets.length) })), body);
}

function modelMiddle(chosen, commands) {
  const dataset = (state.datasets || []).find(one => one.id === chosen) || { id: chosen };
  const schema = state.schema[chosen];
  const lineage = state.lineage[chosen];
  const model = lineage && (lineage.nodes || []).find(one => one.name === lineage.model);
  const tab = state.modelTab;
  const body = node('div', { class: 'ss-body' });

  if (tab === 'metrics') {
    body.appendChild(node('p', { class: 'ss-note', text:
      'Every governed metric you may use. A metric is defined once, by its owner, and every chart that '
      + 'shows it shows the same number — which is the whole point of a semantic layer.' }));
    body.appendChild(node('table', { class: 'ss-table' },
      node('tr', {}, ['Metric', 'Owner', 'Version', 'Certified', 'What it is'].map(h => node('th', { text: h }))),
      state.metrics.filter(one => matches(one.id) || matches(one.name)).map(metric => node('tr', {},
        node('td', { text: metric.id }), node('td', { text: metric.owner || '—' }),
        node('td', { text: metric.version || '—' }),
        node('td', {}, node('span', { class: 'ss-cert', 'data-certified': Boolean(metric.certified),
          text: metric.certified ? '✓' : '○' })),
        node('td', { class: 'ss-faint', text: (metric.documentation || '').slice(0, 90) })))));
    return node('section', { class: 'ss-card' },
      node('header', {}, node('span', { text: 'Metrics' }),
        node('span', { class: 'ss-faint', text: `${state.metrics.length} you may use` })), body);
  }
  if (tab === 'datasets') {
    body.appendChild(node('table', { class: 'ss-table' },
      node('tr', {}, ['Dataset', 'Domain', 'Owner', 'Grain', 'Certified'].map(h => node('th', { text: h }))),
      state.datasets.filter(one => matches(one.id) || matches(one.name)).map(one => node('tr', {},
        node('td', {}, node('button', { class: 'ss-name', type: 'button', text: one.id,
          on: { click: () => commands.openAsset(one.id) } })),
        node('td', { text: one.domain || '—' }), node('td', { text: one.owner || '—' }),
        node('td', { text: (one.grain || []).join(', ') || '—' }),
        node('td', {}, node('span', { class: 'ss-cert', 'data-certified': Boolean(one.certified),
          text: one.certified ? '✓' : '○' }))))));
    return node('section', { class: 'ss-card' },
      node('header', {}, node('span', { text: 'Datasets' }),
        node('span', { class: 'ss-faint', text: `${state.datasets.length} you may read` })), body);
  }

  const tabs = [['columns', 'Columns'], ['lineage', 'Lineage'], ['tests', 'Tests'],
    ['documentation', 'Documentation']];
  const inner = tab === 'lineage' ? 'lineage' : tab === 'tests' ? 'tests'
    : state.modelInner || 'columns';
  if (inner === 'columns') {
    if (!schema) {
      body.appendChild(node('p', { class: 'ss-note', text: 'reading its columns…' }));
    } else {
      const columns = Object.entries(schema.columns || {});
      body.appendChild(node('table', { class: 'ss-table' },
        node('tr', {}, ['Column', 'Type', 'Role', 'Classification', 'You may read it']
          .map(h => node('th', { text: h }))),
        columns.filter(([name]) => matches(name)).map(([name, column]) => node('tr', {},
          node('td', { text: name }),
          node('td', { text: column.physical_type || column.type || '—' }),
          node('td', { text: column.role || '—' }),
          node('td', { text: column.classification || '—' }),
          node('td', {}, node('span', { class: 'ss-cert', 'data-certified': column.readable !== false,
            text: column.readable === false ? '○' : '✓' }))))));
      body.appendChild(node('div', { class: 'ss-callout', 'data-kind': 'info', style: 'margin-top: 12px',
        text: 'These are the columns, not the data. No sample is shown anywhere in this workspace: a '
          + 'developer’s Superset account is given no database in SQL Lab, and this page is not a way round '
          + 'that. A column you may not read is marked, and a chart that asked for it would be refused.' }));
    }
  }
  if (inner === 'lineage') {
    if (lineage === undefined) {
      body.appendChild(node('p', { class: 'ss-note', text: 'reading its lineage…' }));
    } else if (!lineage) {
      body.appendChild(node('p', { class: 'ss-note', text: 'the engine has no lineage for this dataset' }));
    } else {
      body.appendChild(node('p', { class: 'ss-note', text:
        `${dataset.id} is built by the dbt model ${lineage.model}, which is built from what is under it. `
        + 'This is dbt’s own graph, as it was built — not a guess from the SQL.' }));
      body.appendChild(node('ul', { class: 'ss-lineage' }, (lineage.nodes || []).map(one =>
        node('li', { 'data-kind': one.resource_type },
          node('span', { class: 'ss-name', text: one.name }),
          node('span', { class: 'ss-faint',
            text: `${one.resource_type} · ${one.materialized || '—'} · ${one.relation || ''}` }),
          (one.depends_on || []).length
            ? node('div', { class: 'ss-faint', text: `from ${(one.depends_on || [])
              .map(parent => parent.name).join(', ')}` }) : null))));
    }
  }
  if (inner === 'tests') {
    const tests = lineage ? (lineage.nodes || []).flatMap(one =>
      (one.tests || []).map(test => ({ model: one.name, test }))) : [];
    body.appendChild(node('p', { class: 'ss-note', text:
      'The tests dbt runs on these models when the governed project is built. A model whose tests do not '
      + 'pass does not become a governed dataset.' }));
    body.appendChild(tests.length
      ? node('ul', { class: 'ss-checks' }, tests.map(({ model: on, test }) =>
        node('li', { 'data-status': 'pass' }, node('b', { text: 'test' }),
          node('span', {}, node('span', { text: typeof test === 'string' ? test : (test.name || '') }),
            node('div', { class: 'ss-faint', text: on })))))
      : node('p', { class: 'ss-note', text: lineage ? 'this model declares no test' : 'reading…' }));
  }
  if (inner === 'documentation') {
    body.appendChild(node('ul', { class: 'ss-gov' },
      pair('What it is', dataset.description), pair('Owner', dataset.owner),
      pair('Domain', dataset.domain), pair('Grain', (dataset.grain || []).join(', ')),
      pair('Kind', dataset.kind), pair('dbt model', lineage ? lineage.model : '—'),
      pair('Where its source is', model ? model.path : '—')));
    body.appendChild(node('div', { class: 'ss-callout', 'data-kind': 'info', text:
      'The model’s SQL is in the governed dbt project (dbt/), not here: it is read-only to this workspace '
      + 'and to the engine’s own account, and it is built before any dashboard asks for it. « Open dbt docs »'
      + ' above opens dbt’s own documentation, with the compiled SQL and the graph.' }));
  }

  return node('section', { class: 'ss-card' },
    node('header', {}, node('span', { text: dataset.id }),
      node('span', { class: 'ss-tag', text: lineage ? lineage.model : 'dataset' }),
      node('span', { class: 'ss-spacer' }),
      node('span', { class: 'ss-cert', 'data-certified': Boolean(dataset.certified),
        text: dataset.certified ? '✓ certified' : '○ not certified' })),
    node('div', { style: 'padding: 8px 10px 0' }, node('div', { class: 'ss-tabs' },
      tabs.map(([key, label]) => node('button', { type: 'button', text: label,
        'aria-selected': inner === key,
        on: { click: () => {
          state.modelInner = key;
          if (key === 'lineage' || key === 'tests') state.modelTab = key;
          else if (state.modelTab === 'lineage' || state.modelTab === 'tests') state.modelTab = 'overview';
          commands.openAsset(chosen);
        } } })))),
    body);
}

function modelDetails(chosen, commands) {
  const dataset = (state.datasets || []).find(one => one.id === chosen) || { id: chosen };
  const lineage = state.lineage[chosen];
  const model = lineage && (lineage.nodes || []).find(one => one.name === lineage.model);
  const used = (state.spec.components || []).filter(one => one.dataset === chosen);
  return node('div', { class: 'ss-pane ss-stack' },
    node('section', { class: 'ss-card' }, node('header', {}, 'Model Details'),
      node('div', { class: 'ss-body' },
        node('ul', { class: 'ss-gov' },
          pair('Type', model ? model.resource_type : dataset.kind),
          pair('Materialization', model ? model.materialized : '—'),
          pair('Relation', model ? model.relation : '—'),
          pair('Owner', dataset.owner),
          pair('Domain', dataset.domain),
          pair('Grain', (dataset.grain || []).join(', '))),
        node('div', { class: 'ss-chipbar', style: 'margin-top: 8px' },
          [dataset.domain, dataset.kind, dataset.certified ? 'certified' : 'not certified']
            .filter(Boolean).map(what => node('span', { class: 'ss-filterchip', text: what }))))),
    node('section', { class: 'ss-card' }, node('header', {}, 'Description'),
      node('div', { class: 'ss-body' },
        node('p', { class: 'ss-note', text: dataset.description || 'no description in the registry' }))),
    node('section', { class: 'ss-card' }, node('header', {}, 'Used by this dashboard'),
      node('div', { class: 'ss-body' }, used.length
        ? node('ul', { class: 'ss-gov' }, used.map(one => pair(one.title || one.slot, one.metric)))
        : node('p', { class: 'ss-note', text: 'no chart of this dashboard reads it' }))),
    node('section', { class: 'ss-card' }, node('header', {}, 'Metrics on it'),
      node('div', { class: 'ss-body' },
        (lineage && (lineage.metrics || []).length)
          ? node('ul', { class: 'ss-gov' }, lineage.metrics.map(id => pair(id,
            ((state.metrics || []).find(one => one.id === id) || {}).certified ? 'certified' : '')))
          : node('p', { class: 'ss-note', text: 'read its lineage to see which metrics are valid on it' }))));
}

/* ── the releases (the owner's layout 4) ───────────────────────────────────────────────────────────────────
 * Versions, what each one's validation found, where each has been deployed — and the pipeline, which is a
 * PICTURE of where this project stands, not a set of buttons: DEV is this kit, and UAT and PROD are gates a
 * second person approves on the command line (ADR 0010, ADR 0017).
 */
function releaseScreen(client, commands) {
  const body = node('div', { class: 'ss-body' });
  ({ versions: versionsPanel, validation: validationPanel, environments: deploymentsTab,
    releases: releasesPanel }[state.releaseTab] || versionsPanel)(body, commands);
  const titles = { versions: 'Versions', validation: 'Validation', environments: 'Environments',
    releases: 'Releases' };
  return node('div', { class: 'ss-panes', 'data-explorer': 'closed', 'data-copilot': 'open' },
    node('section', { class: 'ss-card' },
      node('header', {}, node('span', { text: titles[state.releaseTab] || 'Versions' })), body),
    node('div', { class: 'ss-pane ss-stack' }, pipeline(commands), validationResults(commands)));
}

function versionsPanel(body, commands) {
  const versions = [...(state.payload.versions || [])].reverse();
  if (!versions.length) {
    body.appendChild(node('p', { class: 'ss-note', text:
      'This project has no version yet. A version is cut from the draft, through the engine’s ten gates — '
      + '« + New Release » above.' }));
    return;
  }
  body.appendChild(node('ul', { class: 'ss-versions' }, versions.map(version => {
    const kind = { PROD: 'good', UAT: 'good', APPROVED_FOR_UAT: 'good', APPROVED_FOR_PROD: 'good',
      READY_FOR_REVIEW: 'warn', VALIDATION_FAILED: 'bad', UAT_FAILED: 'bad' }[version.status];
    return node('li', {},
      node('span', { class: 'ss-avatar', title: version.created_by || '',
        text: initials(version.created_by || '?') }),
      node('div', { class: 'ss-version-body' },
        node('strong', { text: `v${version.version}` }),
        node('span', { class: 'ss-faint', text: `  ${new Date(version.created_at).toLocaleString()}` }),
        node('div', { class: 'ss-faint', text: version.title || version.package || '' })),
      tag(version.state || version.status, kind),
      node('button', { class: 'ss-btn', 'data-weight': 'quiet', type: 'button', text: '⋯',
        title: version.package ? 'Export this version’s package' : 'this version has no package',
        disabled: !version.package,
        on: { click: () => commands.export_(version.version) } }));
  })));
  body.appendChild(node('div', { class: 'ss-callout', style: 'margin-top: 12px', text:
    'Promoting is not here, on purpose: UAT and PROD are approved on the command line, by someone other than '
    + 'the person who asked for the version — the one rule that could not be enforced if this page could do '
    + 'it. bash ai-bi.sh approve <project> UAT.' }));
}

function releasesPanel(body, commands) {
  releasesTab(body);
  reportTab(body, commands);
}

function pipeline(commands) {
  const versions = state.payload.versions || [];
  const newest = status => [...versions].reverse()
    .find(version => (Array.isArray(status) ? status : [status]).includes(version.status));
  const latest = versions[versions.length - 1];
  const uat = newest(['APPROVED_FOR_UAT', 'UAT', 'APPROVED_FOR_PROD', 'PROD']);
  const prod = newest(['APPROVED_FOR_PROD', 'PROD']);
  const deployed = (state.deployments || []).filter(one => one.status === 'succeeded');
  const stage = (name, version, what, kind) => node('div', { class: 'ss-stage', 'data-kind': kind },
    node('strong', { text: name }),
    node('span', { class: 'ss-stage-state', text: what }),
    node('span', { class: 'ss-stage-version', text: version ? `v${version.version}` : '–' }));
  return node('section', { class: 'ss-card' }, node('header', {}, 'Release Pipeline'),
    node('div', { class: 'ss-body' },
      node('div', { class: 'ss-pipeline' },
        stage('DEV', latest, latest ? 'Current' : 'nothing yet', 'now'),
        node('span', { class: 'ss-arrow', text: '→' }),
        stage('UAT', uat, uat ? 'Approved' : 'Pending', uat ? 'done' : 'waiting'),
        node('span', { class: 'ss-arrow', text: '→' }),
        stage('PROD', prod, prod ? 'Approved' : 'Not deployed', prod ? 'done' : 'none')),
      deployed.length
        ? node('ul', { class: 'ss-gov', style: 'margin-top: 10px' }, deployed.slice(-3).map(one =>
          pair(one.target, `v${one.version} — ${new Date(one.started_at).toLocaleDateString()}`)))
        : node('p', { class: 'ss-note', style: 'margin-top: 10px', text:
          'Nothing has been deployed anywhere else yet. Environments says where it may go.' }),
      node('p', { class: 'ss-note', text:
        'A gate is a decision about a version, not a place: this kit has one BI engine. A deployment '
        + 'CARRIES a decision somebody else made — it never makes one.' })));
}

function validationResults(commands) {
  const body = node('div', { class: 'ss-body' });
  const report = state.validation;
  if (!report) {
    body.appendChild(node('p', { class: 'ss-note', text:
      'Level 1 of the brief’s validation, against the registry and the template — the slots, the '
      + 'visualizations, the metrics, the datasets, and your clearance for each. It touches nothing in '
      + 'Superset.' }));
  } else {
    const by = {};
    (report.checks || []).forEach(check => {
      const group_ = check.category || check.name;
      by[group_] = by[group_] || { status: 'pass', detail: '' };
      if (check.status === 'fail') by[group_] = { status: 'fail', detail: check.detail || check.name };
      else if (check.status === 'warn' && by[group_].status !== 'fail') {
        by[group_] = { status: 'warn', detail: check.detail || check.name };
      }
    });
    body.appendChild(node('ul', { class: 'ss-checks' }, Object.entries(by).map(([name, found]) =>
      node('li', { 'data-status': found.status },
        node('b', { text: found.status === 'pass' ? 'Passed' : found.status === 'warn' ? 'Warning' : 'Failed' }),
        node('span', {}, node('span', { text: name }),
          found.detail ? node('div', { class: 'ss-note', text: found.detail }) : null)))));
  }
  return node('section', { class: 'ss-card' },
    node('header', {}, node('span', { text: 'Validation Results' }), node('span', { class: 'ss-spacer' }),
      button('Run Validation', commands.validate, { 'data-weight': 'quiet' })),
    body);
}

/* What the Validation tab of the release screen shows: the draft read against the registry, and then what
 * SUPERSET itself holds against what was validated — the two questions a reviewer asks. */
function validationPanel(body, commands) {
  validationTab(body, commands);
  body.appendChild(node('h4', { style: 'margin-top: 16px', text: 'What Superset holds now' }));
  changesTab(body, commands);
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

/* Where a version has been, and where it may go. Deploying is not deciding: the engine refuses a version
 * nobody else approved for that gate, which is why this is a button and the approval is elsewhere. */
function deploymentsTab(body, commands) {
  if (state.targets === null) {
    body.appendChild(node('p', { class: 'ss-note', text:
      'A deployment takes the package that was validated to another Superset — the run environment — '
      + 'imports it, repoints its connection at that environment\u2019s warehouse, grants the audience '
      + 'SharpSight decided, and then reads it back: every chart is asked for its data before it is called '
      + 'deployed.' }));
    body.appendChild(node('div', { style: 'margin-top: 10px' },
      button('Where can this go?', commands.deployments)));
    return;
  }
  const versions = (state.payload.versions || []).filter(version => version.package);
  const latest = versions[versions.length - 1];
  // the gate the version was approved for — not yet the gate a deployment goes as: that also depends on the
  // target, which holds a version to its own gate (deploy.py, Deployer.refusal)
  const approved = { APPROVED_FOR_UAT: 'UAT', UAT: 'UAT', APPROVED_FOR_PROD: 'PROD', PROD: 'PROD' }[
    latest && latest.status];
  // a target that names no gate is held to PROD by the engine: a missing setting asks for the second person
  const needs = target => (target.gate === 'UAT' ? 'UAT' : 'PROD');
  // the stricter of the two, the same rule the engine applies — so the button never offers what it refuses
  const gateFor = target => (approved === 'PROD' || needs(target) === 'PROD' ? 'PROD' : 'UAT');
  const why = target => (!latest ? 'no version has a package yet'
    : !approved ? `${latest.version} is ${latest.status}: a deployment carries a version already approved `
      + 'for its gate, by someone other than whoever asked for it'
      : approved === 'UAT' && needs(target) === 'PROD'
        ? `${target.name} is a PROD target and ${latest.version} is approved for UAT only: approve it for `
          + `PROD first — bash ai-bi.sh approve ${state.payload.project.project_id} PROD`
        : null);
  if (!state.targets.length) {
    body.appendChild(node('div', { class: 'ss-callout', text:
      'No target is configured. A target is a Superset somewhere else, named in the engine\u2019s own '
      + 'environment (AIBI_TARGET_<NAME>_URL, _USER, _PASSWORD, _DB_NAME, _DB_URI, and _GATE=UAT for one '
      + 'that takes versions approved for UAT — without it a target is held to PROD) and written down '
      + 'nowhere in the repository.' }));
  } else {
    body.appendChild(node('table', { class: 'ss-table' },
      node('tr', {}, ['Target', 'Where', 'Its connection', 'Needs', ''].map(head => node('th', { text: head }))),
      state.targets.map(target => {
        const refused = why(target);
        return node('tr', {},
          node('td', { text: target.name }), node('td', { text: target.url }),
          node('td', { text: target.database }),
          node('td', { text: `approved for ${needs(target)}` }),
          node('td', {}, !target.from_studio
            ? node('span', { class: 'ss-faint', text: 'the command line only' })
            : [button(`Deploy ${latest ? latest.version : ''} →`,
              () => deployment({ version: latest.version, target: target.name, gate: gateFor(target),
                database: target.database }, commands),
              { 'data-weight': 'primary', disabled: Boolean(refused) || state.deploying,
                title: refused || `Take ${latest.version} to ${target.name} as ${gateFor(target)}` }),
            // a disabled button says nothing to a finger or a screen reader: the reason is written beside it
            refused ? node('div', { class: 'ss-note', text: refused }) : null]));
      })));
  }
  const done = state.deployments || [];
  body.appendChild(node('h4', { style: 'margin-top: 14px', text: 'What has been deployed' }));
  if (!done.length) {
    body.appendChild(node('p', { class: 'ss-note', text: 'nothing yet' }));
  }
  done.slice().reverse().forEach(one => body.appendChild(node('div', { class: 'ss-panel' },
    node('h4', {}, `${one.version} → ${one.target}`,
      node('span', { class: 'ss-tag', 'data-kind': one.status === 'succeeded' ? 'good' : 'bad',
        style: 'margin-left: 8px', text: one.status })),
    node('p', { class: 'ss-faint', text: `by ${one.by}, ${new Date(one.started_at).toLocaleString()}` }),
    one.error ? node('div', { class: 'ss-callout', 'data-kind': 'bad', text: one.error }) : null,
    node('ul', { class: 'ss-checks' }, (one.steps || []).map(step =>
      node('li', { 'data-status': step.status === 'done' ? 'pass'
        : step.status === 'failed' ? 'fail' : 'warn' },
      node('b', { text: step.status }),
      node('span', {}, node('span', { text: step.title }),
        step.detail ? node('div', { class: 'ss-note', text: step.detail }) : null)))),
    (one.checks || []).length
      ? node('div', { class: 'ss-readback' },
        node('h5', { text: 'What was read back afterwards' }),
        node('ul', { class: 'ss-checks' }, one.checks.map(check =>
          node('li', { 'data-status': check.status },
            node('b', { text: check.status }),
            node('span', {}, node('span', { text: check.check }),
              check.detail ? node('span', { class: 'ss-faint', text: ` \u2014 ${check.detail}` })
                : null)))))
      : null,
    one.dashboard && one.dashboard.url
      ? node('p', { style: 'margin-top: 8px' }, node('a', { href: one.dashboard.url, target: '_blank',
        rel: 'noopener', text: one.dashboard.url })) : null)));
}

/* ── deploying (§55): a sheet, because a native confirm box says nothing about what is going to happen ────
 * It is not a decision — the engine refuses a version nobody else approved for this gate before anything is
 * sent. What the sheet is for is saying WHAT a deployment does, in the order it does it, and taking the one
 * thing only the person can give: a note of what was checked, which goes into the record.
 */
export function deployment({ version, target, gate, database }, commands) {
  const note = node('input', { placeholder: 'what was checked, in a sentence (optional)', maxlength: '500' });
  modal({
    title: `Deploy ${version} to ${target}`,
    body: node('div', {},
      node('p', { class: 'ss-note', text:
        `What goes is the package version ${version} was cut as — Superset\u2019s own export of the `
        + 'dashboard, taken when it was committed and checked against its checksum. Nothing is generated '
        + 'again.' }),
      node('ul', { class: 'ss-steps' }, [
        `the governed connection in ${target}, called \u00ab ${database || 'the name the package expects'} \u00bb,`
          + ' pointed at that environment\u2019s warehouse',
        'the dashboard, its charts and its datasets, imported',
        'the connection repointed \u2014 an import keeps the one it finds',
        'the audience and the row-level rules SharpSight decided, granted there',
        'and then every chart asked for its data, because an import answers 200 for a dashboard that cannot'
          + ' draw',
      ].map(says => node('li', { text: says }))),
      field('Your note', note),
      node('p', { class: 'ss-faint', text:
        `It goes as ${gate}. A version is only carried to a gate somebody other than whoever asked for it `
        + 'has already approved it for; this sheet cannot approve anything.' })),
    footer: [button('Cancel', close), button(`Deploy to ${target}`, async () => {
      close();
      await commands.deploy(version, target, gate, note.value.trim());
    }, { 'data-weight': 'primary' })],
  });
}

/* ── the gallery (§48.3): the manual path, which is not a prompt ─────────────────────────────────────────── */
export function gallery(wanted, kind, commands) {
  const free = state.payload.free_slots;
  if (!free.length) {
    toast('Every slot of this template already holds a chart.', { kind: 'bad' });
    return;
  }
  // a dimension and a grain are sent only when one was given: the engine checks what it is sent, and an
  // empty string is not a grain it knows
  const chosen = { slot: (free.find(f => f.slot === wanted) || free[0]).slot, metric: null, type: kind || null };
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
        on: { change: event => {
          const given = event.target.value.trim();
          if (given) chosen.dimension = given;
          else delete chosen.dimension;
        } } }))));
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
