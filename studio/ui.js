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
    if (value === undefined || value === null || value === false) return;
    if (key === 'on') Object.entries(value).forEach(([event, handler]) => made.addEventListener(event, handler));
    else if (key === 'text') made.textContent = value;
    else if (key === 'style') made.setAttribute('style', value);
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
  const first = scrim.querySelector('input, select, textarea, button');
  if (first) first.focus();
  return scrim;
}

export function close() {
  if (open) open.remove();
  open = null;
}

export const isOpen = () => Boolean(open);

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
