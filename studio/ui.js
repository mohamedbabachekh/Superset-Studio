/* The pieces every pane is drawn with: a node maker, buttons, toasts, a modal, and the keyboard.
 *
 * No framework and no build step (studio.js says why). What a framework would give and this does not is a
 * virtual DOM; what replaces it is that a redraw is cheap — a dozen tiles — and that the two places where it
 * would be felt (a resize drag, a text field being typed in) are handled without one: the drag moves CSS
 * variables and commits once, and a field commits on `change`, not on every keystroke.
 */
export const node = (tag, attrs = {}, ...children) => {
  const made = document.createElement(tag);
  Object.entries(attrs).forEach(([key, value]) => {
    // `aria-pressed="false"` is not the same as no aria-pressed: the first says « a toggle, and it is off »,
    // the second says nothing at all. So a false is kept for aria, and dropped everywhere else.
    const aria = key.startsWith('aria-');
    if (value === undefined || value === null || (value === false && !aria)) return;
    if (key === 'on') Object.entries(value).forEach(([event, handler]) => made.addEventListener(event, handler));
    else if (key === 'text') made.textContent = value;
    else if (key === 'style') made.setAttribute('style', value);
    else if (aria) made.setAttribute(key, String(Boolean(value) === value ? value : value));
    else made.setAttribute(key, value === true ? '' : String(value));
  });
  children.flat().forEach(child => child === null || child === undefined || child === false ? null
    : made.appendChild(typeof child === 'string' || typeof child === 'number'
      ? document.createTextNode(String(child)) : child));
  return made;
};

export const button = (label, onClick, attrs = {}) =>
  node('button', { class: 'ss-btn', type: 'button', ...attrs, on: { click: onClick, ...(attrs.on || {}) } }, label);

export const tag = (text, kind) => node('span', { class: 'ss-tag', 'data-kind': kind, text });

/* ── icons ───────────────────────────────────────────────────────────────────────────────────────────────
 * One line of SVG per kind of chart, drawn from the same 24-grid so a shelf of them reads as one set. They
 * are what a kind looks like, not what a chart looks like: the chart itself is a photograph of the real one
 * (aibi/picture.py). Inline, because this page loads no icon font and no second asset.
 */
const PATHS = {
  big_number: 'M4 17h4V7H4zM11 17h3V11h-3zM17 17h3V4h-3z',
  line: 'M3 17l5-6 4 3 5-8 4 5',
  area: 'M3 17l5-6 4 3 5-8 4 5v6H3z',
  bar: 'M4 19V9h4v10zM10 19V5h4v14zM16 19v-7h4v7z',
  pie: 'M12 3a9 9 0 109 9h-9z',
  histogram: 'M3 19V13h3v6zM7 19V9h3v10zM11 19V5h3v14zM15 19V11h3v8zM19 19v-4h2v4z',
  table: 'M3 5h18v14H3zM3 10h18M9 10v9M15 10v9',
  map: 'M9 4L3 6v14l6-2 6 2 6-2V4l-6 2z M9 4v14 M15 6v14',
  other: 'M4 4h16v16H4zM4 9h16M9 9v11',
};

export function icon(kind, attrs = {}) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.6');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  Object.entries(attrs).forEach(([key, value]) => svg.setAttribute(key, String(value)));
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', PATHS[kind] || PATHS.other);
  svg.appendChild(path);
  return svg;
}

export const field = (label, control) => node('label', { class: 'ss-field' }, node('span', { text: label }), control);

export const pair = (name, value) => node('li', {}, node('span', { text: name }),
  node('span', { text: value === null || value === undefined || value === '' ? '—' : String(value) }));

export const select = (value, options, onChange, attrs = {}) =>
  node('select', { ...attrs, on: { change: event => onChange(event.target.value) } },
    options.map(option => {
      const [key, label] = Array.isArray(option) ? option : [option, option || '—'];
      return node('option', { value: key, selected: (value || '') === key }, label);
    }));

/* ── toasts: what happened, without stopping what is being done ──────────────────────────────────────────── */
const toasts = node('div', { class: 'ss-toasts', role: 'status', 'aria-live': 'polite' });

export function toast(said, { kind = 'plain', action = null, label = '', seconds = 6 } = {}) {
  if (!toasts.isConnected) document.body.appendChild(toasts);
  const made = node('div', { class: 'ss-toast', 'data-kind': kind }, node('span', { text: said }),
    action && node('button', { type: 'button', text: label, on: { click: () => { made.remove(); action(); } } }),
    node('button', { type: 'button', text: '✕', 'aria-label': 'Dismiss', on: { click: () => made.remove() } }));
  toasts.appendChild(made);
  if (seconds) setTimeout(() => made.remove(), seconds * 1000);
  return made;
}

/* ── one modal at a time: the chart gallery, the shortcuts ───────────────────────────────────────────────── */
let open = null;

export function modal({ title, body, footer }) {
  close();
  const scrim = node('div', { class: 'ss-scrim', role: 'dialog', 'aria-modal': 'true', 'aria-label': title,
    on: { click: event => { if (event.target === scrim) close(); } } },
    node('div', { class: 'ss-modal' },
      node('header', {}, node('span', { text: title }), node('span', { class: 'ss-spacer' }),
        button('✕', close, { 'data-weight': 'quiet', 'aria-label': 'Close' })),
      node('div', { class: 'ss-body' }, body),
      footer && node('footer', {}, footer)));
  document.body.appendChild(scrim);
  open = scrim;
  // what a person came to type into, before the button that closes it
  const first = scrim.querySelector('.ss-body input, .ss-body select, .ss-body textarea')
    || scrim.querySelector('.ss-body button') || scrim.querySelector('button');
  if (first) first.focus();
  return scrim;
}

export function close() {
  if (open) open.remove();
  open = null;
}

export const isOpen = () => Boolean(open);

/* ── the theme: light by default, dark by choice ─────────────────────────────────────────────────────────
 * Light is the default because this is a page for reading data and Superset's own shell around it is light.
 * The choice is kept in the browser, per person, and the page is drawn from tokens so the two themes are one
 * stylesheet. Storage can throw (a private window, blocked site data), so every touch of it is guarded and
 * the page renders without it.
 */
const THEME = 'sharpsight-studio-theme';

export function theme(wanted) {
  if (wanted) {
    document.documentElement.setAttribute('data-ss-theme', wanted);
    try { window.localStorage.setItem(THEME, wanted); } catch (error) { /* a page without storage still draws */ }
    return wanted;
  }
  let kept = null;
  try { kept = window.localStorage.getItem(THEME); } catch (error) { kept = null; }
  const now = kept || 'light';
  document.documentElement.setAttribute('data-ss-theme', now);
  return now;
}

export const themeNow = () => document.documentElement.getAttribute('data-ss-theme') || 'light';

/* ── the command palette: everything the workspace can do, by name ───────────────────────────────────────
 * Linear's idea, and the reason it is here: a workspace with three panes, a drawer and thirty actions is
 * faster to drive by name than by hunting. It lists the same commands the bar and the panes offer — it adds
 * no power a person does not already have, and no command that is refused elsewhere is offered here.
 */
export function palette(entries) {
  const box = node('input', { class: 'ss-search', placeholder: 'Type a command, or a chart…',
    'aria-label': 'Command palette' });
  const list = node('div', { class: 'ss-palette', role: 'listbox' });
  let shown = entries;
  let at = 0;

  const run = entry => { close(); entry.run(); };
  const paint = () => {
    list.textContent = '';
    if (!shown.length) {
      list.appendChild(node('div', { class: 'ss-note', style: 'padding: 10px 2px', text: 'nothing by that name' }));
      return;
    }
    shown.forEach((entry, n) => list.appendChild(node('button', {
      class: 'ss-palette-row', type: 'button', role: 'option', 'aria-selected': n === at,
      on: { click: () => run(entry), mousemove: () => { if (at !== n) { at = n; paint(); } } },
    }, node('span', { class: 'ss-where', text: entry.where }), node('span', { text: entry.label }),
      entry.keys ? node('span', { class: 'ss-key', text: entry.keys }) : null)));
    const chosen = list.children[at];
    if (chosen && chosen.scrollIntoView) chosen.scrollIntoView({ block: 'nearest' });
  };

  box.addEventListener('input', () => {
    const wanted = box.value.trim().toLowerCase();
    shown = !wanted ? entries : entries.filter(entry =>
      `${entry.where} ${entry.label}`.toLowerCase().includes(wanted));
    at = 0;
    paint();
  });
  box.addEventListener('keydown', event => {
    if (event.key === 'ArrowDown') { at = Math.min(at + 1, shown.length - 1); paint(); event.preventDefault(); }
    else if (event.key === 'ArrowUp') { at = Math.max(at - 1, 0); paint(); event.preventDefault(); }
    else if (event.key === 'Enter' && shown[at]) { run(shown[at]); event.preventDefault(); }
  });
  paint();
  modal({ title: 'Commands', body: node('div', {}, box, list) });
}

/* ── the keyboard: every move the mouse can make, and a card that lists them ─────────────────────────────── */
export function shortcuts(map) {
  window.addEventListener('keydown', event => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((event.target || {}).tagName || '');
    const combo = [event.metaKey || event.ctrlKey ? 'mod' : '', event.shiftKey ? 'shift' : '', event.key]
      .filter(Boolean).join('+');
    const handler = map[combo] || map[event.key];
    if (!handler) return;
    if (typing && !/mod/.test(combo) && event.key !== 'Escape') return;
    if (handler(event) !== false) event.preventDefault();
  });
}

export const KEYS = [
  ['mod+k', 'the command palette'],
  ['?', 'this card'],
  ['mod+s', 'save the draft'],
  ['mod+z / mod+shift+z', 'undo / redo'],
  ['← →', 'select the chart before or after'],
  ['shift+← →', 'move the selected chart along its row'],
  ['shift+↑ ↓', 'move its row up or down'],
  ['[ ]', 'narrower, wider'],
  ['- +', 'the row shorter, taller'],
  ['Backspace', 'take the selected chart off the dashboard'],
  ['a', 'add a chart'],
  ['v', 'validate'],
  ['/', 'search the explorer'],
  ['Escape', 'deselect, or close this'],
];
