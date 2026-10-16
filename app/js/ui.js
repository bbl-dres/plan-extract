/* Screen mounting and routing, the current viewer, shared pieces (notices, guarded buttons, pressable rows, segmented
   controls, the error box, the step head). */
import { $, every, fileUrl, h, postJSON, put, sheetUrl } from './core.js';
import { t, tn } from './i18n.js';
import { estimate, etaText, plan } from './progress.js';
import { atCursor, busy, deleteSheet, drawingRows, firstUnconfirmed, J, P, resetProject, saveProject, screenName, setScreen } from './state.js';
import { Viewer } from './viewer.js';

const TOAST_MS = 8000;
const LOG_LINES = 25;                    // log lines in an error box

// ---------------------------------------------------------------------------------------------------------------------
// Screens

let current = null;                      // the canvas viewer of the screen on show; the next mount destroys it
function useViewer(stage, label) { if (current) current.destroy(); current = new Viewer(stage); current.setLabel(label || t('viewer.sheet')); return current; }
const SCREENS = {};                      // name -> render function, filled by app.js (no module cycle with the screens)
function register(name, fn) { SCREENS[name] = fn; }
const onUnmount = [];                    // what a screen wants torn down when the next one mounts (e.g. the 3D view)
function beforeUnmount(fn) { onUnmount.push(fn); }
let navigated = false;                   // go() was called: the next mount is a new screen, its heading takes focus
let waiting = false;                     // the screen on show waits for the server (busy screen, scale wait)
/** Put a screen in place. Returns true when it is a new screen (go), false for a refresh by a poll; only a new screen
    moves the focus to its heading, so a poll never takes it from where the user is. */
function mount(node, crumb, { wait = false } = {}) {
  if (current) { current.destroy(); current = null; }
  while (onUnmount.length) { try { onUnmount.pop()(); } catch (e) { /* already gone */ } }
  put($('#screen'), node);
  const c = $('#crumb'); c.hidden = !crumb; $('#crumb-text').textContent = crumb || '';
  $('#btn-start-over').hidden = !P.sheets.length;
  setDownloads(null);
  document.onkeydown = null;
  waiting = wait;
  const fresh = navigated;
  navigated = false;
  if (fresh) focusHeading();
  return fresh;
}
/** The screen's first heading (or the first h2 of `root`) takes focus: a screen reader announces the new screen,
    keyboard users start at its top. */
function focusHeading(root = null) {
  const head = (root || $('#screen')).querySelector('h2');
  if (head) { head.tabIndex = -1; head.focus({ preventScroll: true }); }
}
/** Re-render through `render()` (which replaces nodes) and give the focus back to the node that stands for the same
    thing: the same layer, group, row, tab, or else the same kind of element with the same text. Without it a
    keyboard user falls back to the top of the page after every toggle. */
function keepFocus(render) {
  const a = document.activeElement;
  const find = a && a !== document.body && a.isConnected ? focusFinder(a) : null;
  render();
  if (!find) return;
  const el = find();
  if (el && el.isConnected && el !== document.activeElement) el.focus({ preventScroll: true });
}
function focusFinder(a) {
  const holder = a.closest('[data-layer],[data-group],[data-id],[role=tab]');
  const within = (el) => (el && holder && a !== holder ? el.querySelector(a.tagName.toLowerCase() + (a.className ? '.' + a.className.split(' ')[0] : '')) || el : el);
  if (holder && holder.dataset.layer != null) return () => within(document.querySelector(`[data-layer="${holder.dataset.layer}"]`));
  if (holder && holder.dataset.group != null) return () => within(document.querySelector(`[data-group="${holder.dataset.group}"]`));
  if (holder && holder.dataset.id != null) return () => within(document.querySelector(`[data-id="${holder.dataset.id}"]`));
  if (holder && holder.getAttribute('role') === 'tab') { const i = [...holder.parentNode.children].indexOf(holder); return () => document.querySelectorAll('[role=tab]')[i]; }
  const text = (a.textContent || '').trim(), cls = a.className, tag = a.tagName;
  return () => [...document.querySelectorAll(tag)].find((e) => e.className === cls && (e.textContent || '').trim() === text);
}
function go(name, cursor) {
  if (cursor) P.cursor = Object.assign({ sheet: 0, pkg: 0, drawing: 0 }, cursor);
  P.screen = name; setScreen(name); saveProject();
  navigated = true;
  (SCREENS[name] || SCREENS.upload)();
}
/** On to the first step not yet confirmed (building area, then scale), else the results. */
function resumeWorkflow() {
  const c = firstUnconfirmed();
  if (c) return go(c.scale ? 'scale' : 'area', c);
  if (drawingRows().length) return go('results');
  const si = P.sheets.findIndex((id) => J[id] && J[id].analysis);   // every sheet skipped: nothing to show, choose a drawing
  if (si < 0) return go('upload');
  notify(t('upload.nothingSelected'), 'info');
  go('area', { sheet: si, pkg: 0, drawing: 0 });
}
// a poll saw jobs change: refresh the list screens in place; a step screen is re-rendered only when it waits for its
// own sheet and that is done (a screen the user works on must not lose handles or measuring points)
function onJobsChanged(changed) {
  if (screenName === 'upload') SCREENS.upload({ changed });
  else if (screenName === 'results') SCREENS.results({ changed });
  else if (screenName === 'area' || screenName === 'scale') {
    const { id, job } = atCursor();
    if (waiting && changed.includes(id) && job && !busy(job)) SCREENS[screenName]();
  }
}

// ---------------------------------------------------------------------------------------------------------------------
// Shared pieces

/** The live region of the notices: made once at start, so the first notice is announced too. */
function toastRegion() {
  return $('#toasts') || document.body.appendChild(h('div', { id: 'toasts', class: 'toasts', role: 'status', 'aria-live': 'polite' }));
}
/** A notice at the bottom of the window (kind err | ok | info), announced to assistive technology; closes by itself. */
function notify(text, kind = 'err', ms = TOAST_MS) {
  const toast = h('div', { class: `toast toast-${kind}` }, h('span', null, text), h('button', { class: 'btn-x', 'aria-label': t('common.dismiss'), onclick: () => toast.remove() }, iconEl('x')));
  toastRegion().append(toast);
  setTimeout(() => toast.remove(), ms);
  return toast;
}
/** POST a JSON body; on failure the message is shown as a notice and null comes back (the callers carry on). */
async function post(path, body) {
  try { return await postJSON(path, body); } catch (e) { notify(e.message); return null; }
}
/** A click handler that disables its button while the action runs: a double click sends one request, not two. */
/** Handlers that share one pending flag: while one of them runs, the others do nothing (Skip and Confirm of a step
    both end in a navigation; only one may). */
function once(fn) {
  return async (e) => {
    const btn = e && e.currentTarget;
    if (once.pending) return;
    once.pending = true;
    if (btn) btn.disabled = true;
    try { await fn(e); } finally { once.pending = false; if (btn && btn.isConnected) btn.disabled = false; }
  };
}
function guarded(fn) {
  return async (e) => {
    const btn = e && e.currentTarget;
    if (btn) { if (btn.disabled) return; btn.disabled = true; }
    try { await fn(e); } finally { if (btn && btn.isConnected) btn.disabled = false; }
  };
}
/** The attributes of a row that works as a button: click, Enter and Space (keys on a control inside the row, such as
    its check box, stay the control's). */
function pressable(fn, attrs = {}) {
  return { ...attrs, tabindex: 0, role: 'button', onclick: fn,
    onkeydown: (e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); fn(e); } } };
}
/** The card pinned to a selected element of the plan (a room, a wall, an opening): a title, a line under it, label and
    value rows, and hints on what to check. The same card for every kind, in 2D and 3D. */
function infoCard(box, { title, subtitle, rows, hints, onClose }) {
  put(box, h('div', { class: 'head' }, h('div', { class: 'clip' }, h('b', { class: 'ttl' }, title), subtitle ? h('div', { class: 'muted small' }, subtitle) : null),
      h('button', { class: 'btn-x', 'aria-label': t('common.close'), onclick: onClose }, iconEl('x'))),
    h('div', { class: 'kv-grid small' }, rows.filter(Boolean).map(([k, v, cls]) => [h('span', null, k), h('span', { class: cls || null }, v)])),
    hints && hints.length ? h('ul', { class: 'hints small' }, hints.map((x) => h('li', { class: x.cls || null, lang: x.en ? 'en' : null }, x.text))) : null);
}
/** A segmented control: one button per [value, label], the current one pressed. */
function segmented(label, items, currentValue, onPick, disabled = () => false) {
  return h('div', { class: 'seg', role: 'group', 'aria-label': label },
    items.map(([value, text, title]) => h('button', { class: value === currentValue ? 'on' : '', 'aria-pressed': String(value === currentValue), 'data-value': value,
      disabled: disabled(value), title, onclick: () => onPick(value) }, text)));
}
/** Press the button of `value` in a segmented control. */
function pressSegment(seg, value) {
  for (const b of seg.children) { const on = b.dataset.value === value; b.classList.toggle('on', on); b.setAttribute('aria-pressed', String(on)); }
}
/** A progress bar with the stage in plain words and the time left, for a job of `kind` ('analysis' | 'extraction').
    `update()` reads the job's current stage and moves the bar; a ticker keeps the estimate moving between polls.
    Returns the element with `update` and `stop`. */
function progressBar(job, kind, opts = {}, onUpdate = null) {
  const steps = plan(kind, opts);
  const stageEl = h('span', { role: 'status', 'aria-live': 'polite' }), etaEl = h('span', { class: 'muted small' }), fill = h('i');   // the stage is announced, the countdown is not
  const el = h('div', { class: 'stack-sm progress-box' },
    h('div', { class: 'row kv' }, stageEl, etaEl),
    h('div', { class: 'progress-line', role: 'progressbar', 'aria-valuemin': 0, 'aria-valuemax': 100 }, fill));
  let stage = null, since = performance.now();
  const update = () => {
    const p = (J[job.id] || job).progress || {};
    if (p.stage !== stage) {                             // the server says when the stage began; a client clock off by minutes is ignored
      stage = p.stage;
      const ago = p.since ? Math.min(600, Math.max(0, Date.now() / 1000 - p.since)) : 0;
      since = performance.now() - ago * 1000;
    }
    const inStage = (performance.now() - since) / 1000;
    const waiting = /^waiting for the worker/.test(stage || '');
    if (waiting && p.wait != null) steps[0].seconds = inStage + p.wait;   // the server's estimate until a worker takes the job, told with every poll
    const e = estimate(steps, stage, inStage);
    if (waiting && p.position > 0) e.label = tn('stage.queuedAhead', p.position);   // jobs ahead of this one in its lane
    stageEl.textContent = e.label; etaEl.textContent = etaText(e);
    fill.style.width = `${Math.round(100 * e.fraction)}%`;
    el.children[1].setAttribute('aria-valuenow', Math.round(100 * e.fraction));
    if (onUpdate) onUpdate(e);
    return e;
  };
  update();
  el.stop = every(1000, update);
  el.update = update;
  return el;
}
/** A spinner with a word over the stage (the canvas): what the page waits for, so that an empty or unchanged drawing
    never looks like nothing happens. `set(title, text)` changes the words, `remove()` takes it away. */
function stageOverlay(stage, title, text) {
  const tt = h('b', null, title), tx = h('div', { class: 'muted small' }, text || '');
  const box = h('div', { class: 'stage-overlay', role: 'status', 'aria-live': 'polite' },
    h('div', { class: 'overlay-card' }, h('span', { class: 'spinner' }), h('div', { class: 'stack-0' }, tt, tx)));
  stage.append(box);
  return { set(a, b) { tt.textContent = a; tx.textContent = b || ''; }, remove() { box.remove(); } };
}
/** The screen shown while sheets are analysed: a progress bar per sheet with the stage in plain words and the time
    left, the way out. Used by the upload page (with the abort) and by a step opened before its sheet is analysed
    (with the way back). */
function busyScreen(jobs, { title = t('upload.newProject'), text, abort, back } = {}) {
  const bars = [];
  const card = (job) => {
    if (job.status === 'error') return errorBox(job);
    const bar = progressBar(job, 'analysis');
    bars.push(bar);
    return h('div', { class: 'stack-sm' }, h('div', { class: 'name' }, job.name), bar);
  };
  const main = h('main', { class: 'upload grid-bg' }, h('div', { class: 'upload-inner' },
    h('div', { class: 'stack-sm' }, h('h2', null, title), h('p', { class: 'muted pretty' }, text || t('busy.reading'))),
    h('div', { class: 'card busy-card' },
      jobs.map(card),
      h('div', { class: 'row' }, abort ? h('button', { class: 'btn btn-lg', onclick: guarded(abort) }, t('busy.abort')) : null,
        back ? h('button', { class: 'btn btn-lg', onclick: back }, t('busy.back')) : null))));
  beforeUnmount(() => bars.forEach((b) => b.stop()));
  mount(main, null, { wait: true });
}
/** Abort: the sheets are removed (a busy one is discarded by the server when its worker is done) and the project starts empty. */
async function abortProject() {
  const ids = [...P.sheets];
  resetProject();
  await Promise.all(ids.map(deleteSheet));
  go('upload');
}
/** The Download menu of the header: items [{label, note, href, name}] or null to hide it. */
function setDownloads(items) {
  const wrap = $('#download-menu'); if (!wrap) return;
  const list = wrap.querySelector('.menu-list'), btn = wrap.querySelector('button');
  wrap.hidden = !items || !items.length;
  list.hidden = true; btn.setAttribute('aria-expanded', 'false');
  put(list, (items || []).map((it) => it.soon                // announced, not yet written: shown, not a link
    ? h('span', { role: 'menuitem', 'aria-disabled': 'true', class: 'soon' }, h('b', null, it.label), h('span', { class: 'muted small' }, it.note))
    : h('a', { role: 'menuitem', href: it.href, download: it.name }, h('b', null, it.label), it.note ? h('span', { class: 'muted small' }, it.note) : null)));
}
/** Wire the header once: the Download menu opens on click, closes on Escape, on a click outside and after a choice;
    the live region of the notices. */
function initChrome() {
  toastRegion();
  const wrap = $('#download-menu'); if (!wrap) return;
  const list = wrap.querySelector('.menu-list'), btn = wrap.querySelector('button');
  const close = () => { const inside = list.contains(document.activeElement); list.hidden = true; btn.setAttribute('aria-expanded', 'false'); if (inside) btn.focus(); };
  btn.addEventListener('click', () => { list.hidden = !list.hidden; btn.setAttribute('aria-expanded', String(!list.hidden)); if (!list.hidden) { const a = list.querySelector('a'); if (a) a.focus(); } });
  list.addEventListener('click', (e) => { if (e.target.closest('a')) close(); });
  list.addEventListener('keydown', (e) => {                // a menu: the arrows move, Home and End jump
    const items = [...list.querySelectorAll('a')], i = items.indexOf(document.activeElement);
    const to = e.key === 'ArrowDown' ? (i + 1) % items.length : e.key === 'ArrowUp' ? (i - 1 + items.length) % items.length : e.key === 'Home' ? 0 : e.key === 'End' ? items.length - 1 : -1;
    if (to >= 0 && items.length) { items[to].focus(); e.preventDefault(); }
  });
  document.addEventListener('click', (e) => { if (!wrap.contains(e.target)) close(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !list.hidden) { close(); btn.focus(); } });
}
function projectName() {
  if (P.name) return P.name;
  const rows = drawingRows();
  if (rows.length) { const j = rows[0].job; return (j.name || '').replace(/\.[^.]+$/, ''); }
  const id = P.sheets[0]; return id && J[id] ? (J[id].name || '').replace(/\.[^.]+$/, '') : '';
}
/** A status badge: kind ok | warn | err | run | wait ('run' shows a spinner instead of the dot). */
function badge(kind, text, title) {
  return h('span', { class: `badge badge-${kind}`, title }, kind === 'run' ? h('span', { class: 'spinner' }) : h('span', { class: 'dot' }), text);
}
/** The head of a step panel: the two step bars (steps up to `step` on; 3 = both, the results), the eyebrow and the title. */
function stepHead(step, eyebrow, title) {
  return [h('div', { class: 'progress-bars', role: 'img', 'aria-label': step >= 3 ? t('step.bothDone') : t('step.of2', { step }) }, [1, 2].map((k) => h('span', { class: k <= step ? 'on' : '' }))),
    h('div', null, h('div', { class: 'eyebrow' }, eyebrow), h('h2', null, title))];
}
const previewUrl = (job, pkg) => fileUrl(job.id, pkg.preview.file);
/** An image into a viewer with a word over the stage while it loads (a preview is a megabyte: seconds on a slow line). */
function loadImage(viewer, url, home = null, title = t('wait.preview')) {
  const wait = stageOverlay(viewer.stage, title);
  return viewer.load(url, home).finally(() => wait.remove());
}
/** The sheet preview into a viewer (framed on `home` when given); when it fails, an error box at the top of the panel. */
function loadPreview(viewer, job, pkg, panel, home = null) {
  return loadImage(viewer, previewUrl(job, pkg), home).catch((e) => { if (panel) panel.prepend(errorBox(job, e.message)); });
}
function errorBox(job, text) {
  const pre = h('pre', { hidden: true, lang: 'en' }, (job.log || []).slice(-LOG_LINES).join('\n'));
  return h('div', { class: 'error-box' },
    h('div', null, h('b', null, t('error.label') + ' '), text || job.error || t('error.unknown')),
    h('div', null, h('button', { class: 'btn-link', onclick: () => { pre.hidden = !pre.hidden; } }, t('error.showLog')),
      ' · ', h('a', { href: sheetUrl(job.id, '/log'), target: '_blank', rel: 'noopener' }, t('error.fullLog'))), pre);
}

/** Line icons (Feather, MIT; 24-unit grid, stroke = currentColor) as markup for an `html` attribute. */
const ICONS = {
  x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  check: '<polyline points="20 6 9 17 4 12"/>',
  play: '<polygon points="6 4 20 12 6 20" fill="currentColor" stroke="none"/>',
  pause: '<rect x="5" y="4" width="5" height="16" rx="1" fill="currentColor" stroke="none"/><rect x="14" y="4" width="5" height="16" rx="1" fill="currentColor" stroke="none"/>',
  maximize: '<path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>',
  home: '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>',
  chevronDown: '<polyline points="6 9 12 15 18 9"/>',
  chevronLeft: '<polyline points="15 18 9 12 15 6"/>',
  chevronRight: '<polyline points="9 18 15 12 9 6"/>',
  rotate: '<polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>',
  eye: '<path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z"/><circle cx="12" cy="12" r="3"/>',
  eyeOff: '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>',
};
const icon = (name) => `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" focusable="false">${ICONS[name]}</svg>`;
/** An icon as an element (decorative: the button or row around it carries the name). */
const iconEl = (name) => h('span', { class: 'ico', 'aria-hidden': 'true', html: icon(name) });

export { abortProject, badge, beforeUnmount, busyScreen, errorBox, focusHeading, go, guarded, icon, infoCard, iconEl, initChrome, keepFocus, once, loadImage, loadPreview, mount, notify, onJobsChanged, post, pressable, pressSegment, progressBar, projectName, register, resumeWorkflow, segmented, setDownloads, stageOverlay, stepHead, useViewer };
