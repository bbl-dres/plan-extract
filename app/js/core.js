/* Constants, design-token colours for the canvas, DOM, fetch, timer and storage helpers, URLs, number formats. */
import { currentLang, t } from './i18n.js';

// canvas colours are read from the design tokens (css/tokens.css), so the legend and the drawing agree
const T = (name, fallback) => (getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback);
const PAL_FALLBACK = ['#4e79a7', '#f28e2b', '#59a14f', '#e15759', '#76b7b2', '#edc948', '#b07aa1', '#ff9da7', '#9c755f', '#86bcb6'];
const PAL = PAL_FALLBACK.map((c, i) => T(`--pal-${i + 1}`, c));   // the room colours
const CONF = { high: T('--success', '#2e9e5b'), medium: T('--warning', '#d99a1e'), low: T('--danger', '#d0433b'), confirmed: T('--accent-canvas', '#2f6ea6'), none: T('--danger', '#d0433b') };
const ACCENT = T('--accent-canvas', '#2f6ea6');   // the accent as hex: canvas strokes and alpha fills need it
const AMBER = T('--warning', '#d99a1e');
const NEUTRAL = T('--neutral-canvas', '#8a8f99');
const TEXT = T('--text', '#1a1d21'), MUTED = T('--muted', '#646b74');
const SURFACE = T('--surface', '#fff'), SELECTION = T('--selection-canvas', '#2f6ea6');
const HALO = T('--halo-canvas', 'rgba(255,255,255,.9)'), SHADOW = T('--shadow-canvas', 'rgba(0,0,0,.12)');   // behind labels and the selection; the sheet's shadow
const LAYER_COLOURS = Object.fromEntries(['walls', 'doors', 'windows', 'passages', 'columns', 'stairs', 'voids', 'graph', 'gf', 'gross'].map((k) => [k, T(`--${k}`, '#777')]));
const REGION_FALLBACK = { 'title block': '#e67800', legend: '#00a0a0', 'scale bar': '#c800c8', 'scale note': '#c80078',
  'north arrow': '#7800dc', notes: '#787800', 'key plan': '#dc0000', caption: '#0078ff', other: '#5a5a5a', frame: '#969696' };
const REGION_COLOURS = Object.fromEntries(Object.entries(REGION_FALLBACK).map(([k, c]) => [k, T(`--region-${k.replace(/ /g, '-')}`, c)]));
const CANVAS_FONT = "'IBM Plex Sans', system-ui, sans-serif";
const PKEY = 'fpx-workflow-project';

// ---------------------------------------------------------------------------------------------------------------------
// Small helpers

const $ = (sel, el = document) => el.querySelector(sel);
const put = (el, ...kids) => el.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false));

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'style') el.style.cssText = v;
    else if (k === 'html') el.innerHTML = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return el;
}

/** Run `fn` every `ms` milliseconds, one run at a time (the next waits for the last to finish), skipped while the page
    is hidden; the first run at once with `now`. Returns the function that stops it. */
function every(ms, fn, now = false) {
  let timer = null, stopped = false;
  const round = async () => {
    if (stopped) return;
    if (!document.hidden) { try { await fn(); } catch (e) { /* the next round tries again */ } }
    if (!stopped) timer = setTimeout(round, ms);
  };
  timer = setTimeout(round, now ? 0 : ms);
  return () => { stopped = true; clearTimeout(timer); };
}

/** JSON in localStorage; a private window or blocked storage reads the fallback and keeps nothing. */
const store = {
  get(key, fallback) { try { const v = JSON.parse(localStorage.getItem(key)); return v == null ? fallback : v; } catch (e) { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* private window, storage full */ } },
};

// ---------------------------------------------------------------------------------------------------------------------
// The API

const sheetUrl = (id, tail = '') => `/api/sheets/${id}${tail}`;
const fileUrl = (id, name, download = false) => sheetUrl(id, `/file/${encodeURIComponent(name)}${download ? '?download=1' : ''}`);
const keyUrl = (id, route, key) => sheetUrl(id, `/${route}?key=${encodeURIComponent(key)}`);

// the error codes of the server (openapi.json, Error) the page says in its own words; others show the server's message
const ERROR_TEXTS = { server_busy: 'error.busy', disk_full: 'error.diskFull', sheet_busy: 'error.sheetBusy', unsupported_type: 'error.unsupported',
  unreadable: 'error.unreadable', too_many_pages: 'error.tooManyPages', too_large: 'error.tooLarge', empty: 'error.empty',
  implausible_scale: 'error.implausibleScale', not_found: 'error.notFound' };
/** A failed request: `status` (0 when the server was not reached), the server's `code`, a message to show. */
class ApiError extends Error {
  constructor(status, data) {
    const code = data && typeof data === 'object' ? data.code || null : null;
    const key = ERROR_TEXTS[code] || (status === 0 ? 'error.network' : status === 413 ? 'error.tooLarge' : status === 429 ? 'error.rateLimited'
      : status >= 500 ? 'error.server' : null);
    super(key ? t(key, { status }) : (data && data.error) || `${status}`);
    this.status = status; this.code = code;
  }
}
let notFoundHook = null;
/** What to do when a sheet request answers 404 not_found (the server no longer has it): `fn(id)`. */
function onNotFound(fn) { notFoundHook = fn; }
async function api(path, opts = {}) {
  let r;
  try { r = await fetch(path, opts); } catch (e) { throw new ApiError(0, null); }
  const ct = r.headers.get('content-type') || '';
  const data = ct.includes('json') ? await r.json().catch(() => null) : await r.text();
  if (!r.ok) {
    const err = new ApiError(r.status, data);
    const m = err.status === 404 && err.code === 'not_found' ? path.match(/^\/api\/sheets\/([^/?]+)/) : null;
    if (m && notFoundHook) notFoundHook(m[1]);
    throw err;
  }
  return data;
}
const getJSON = (p) => api(p);
const postJSON = (p, body) => api(p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

// ---------------------------------------------------------------------------------------------------------------------
// Numbers in the language of the page (de-CH 1’234.5, fr-CH 1 234,5)

const LOCALES = { de: 'de-CH', fr: 'fr-CH', it: 'it-CH', en: 'en-GB' };
const NUMBER_FORMATS = {};
function num(v, digits, grouping = true) {
  const key = `${currentLang()}|${digits}|${grouping}`;
  const nf = NUMBER_FORMATS[key] || (NUMBER_FORMATS[key] = new Intl.NumberFormat(LOCALES[currentLang()] || 'de-CH',
    { minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: grouping }));
  return nf.format(v);
}
const fmt = {
  m2: (v) => v == null ? '–' : `${num(+v, 1)} m²`,
  n: (v, d = 1) => v == null ? '–' : num(+v, d),
  pct: (v) => { if (v == null) return ''; const r = Math.round(v); return `${r > 0 ? '+' : r < 0 ? '−' : ''}${num(Math.abs(r), 0)} %`; },
  scaleN: (N) => N == null ? '–' : `1 : ${num(N >= 20 ? Math.round(N) : N, N >= 20 ? 0 : 1, false)}`,
  secs: (s) => { if (s == null) return '–'; const r = Math.round(s); return r >= 60 ? `${Math.floor(r / 60)} min ${r % 60} s` : `${r} s`; },
};

function confColour(c) { return CONF[c] || NEUTRAL; }

/** The pipeline's stage labels in plain words (i18n keys), with the position of the extraction stages (1..12) for a
    progress bar; the server's own states (starting, waiting for the worker) too. */
const STAGES = [
  ['starting', 'stage.starting', 0], ['waiting for the worker', 'stage.waiting', 0], ['reading the text ahead', 'stage.ahead', 0],
  ['0a normalisation', 'stage.layout', 0], ['1 sheet text', 'stage.sheetText', 0], ['1b layout', 'stage.layout', 0], ['1c door', 'stage.doorSearch', 0], ['1c scale', 'stage.scaleCues', 0],
  ['2 text (OCR', 'stage.ocr', 1], ['0 triage', 'stage.triage', 2], ['2 text layer', 'stage.textLayer', 3],
  ['3 segmentation', 'stage.segmentation', 4], ['3 walls', 'stage.walls', 5], ['4 openings', 'stage.openings', 6],
  ['5 stairs', 'stage.stairs', 7], ['6 rooms', 'stage.rooms', 8], ['7 room', 'stage.roomAttributes', 9], ['8 derived', 'stage.derived', 10],
  ['9 scale', 'stage.checks', 11], ['10 export', 'stage.export', 12]];
function stageInfo(stage) {
  const s = String(stage || '');
  const hit = STAGES.find(([prefix]) => s.startsWith(prefix));
  return hit ? { label: t(hit[1]), index: hit[2] } : { label: s || t('stage.working'), index: 0 };
}

export { $, ACCENT, AMBER, ApiError, CANVAS_FONT, HALO, LAYER_COLOURS, MUTED, NEUTRAL, PAL, PKEY, REGION_COLOURS, SELECTION, SHADOW, SURFACE, T, TEXT, api, confColour, every, fileUrl, fmt, getJSON, h, keyUrl, onNotFound, postJSON, put, sheetUrl, stageInfo, store };
