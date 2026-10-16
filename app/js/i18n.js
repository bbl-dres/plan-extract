/* Interface texts in German, French, Italian and English: app/i18n.json holds one entry per text with its four
   languages ({"key": {"de": ..., "fr": ..., "it": ..., "en": ...}}). German unless the visitor chose another language:
   ?lang= in the address first, then the language menu (remembered in the browser). Texts that come from the pipeline
   (log lines, QA messages, scale evidence) stay English. */
const LANGS = ['de', 'fr', 'it', 'en'];
const DEFAULT = 'de';
const KEY = 'fpx-workflow-lang';
let lang = DEFAULT;
let TEXTS = {};

function chosen() {
  const q = new URLSearchParams(location.search).get('lang');
  if (LANGS.includes(q)) return q;
  try { const s = localStorage.getItem(KEY); if (LANGS.includes(s)) return s; } catch (e) { /* storage blocked */ }
  return DEFAULT;
}

/** Load the texts and settle the language; called once, before the first screen renders. */
async function loadTexts() {
  lang = chosen();
  try { localStorage.setItem(KEY, lang); } catch (e) { /* storage blocked */ }
  for (let attempt = 0; attempt < 2 && !Object.keys(TEXTS).length; attempt++) {    // a blip at boot: once more before the English of the page stays
    try { TEXTS = await (await fetch('/i18n.json', { cache: 'no-cache' })).json(); } catch (e) { TEXTS = {}; }
  }
  if (!Object.keys(TEXTS).length) console.warn('i18n.json could not be loaded: the page keeps its English');
  document.documentElement.lang = lang;
}

/** The text of `key` in the current language (English when it is missing), {name} filled from vars. */
function t(key, vars) {
  const e = TEXTS[key];
  let s = e ? (e[lang] ?? e.en ?? key) : key;
  if (vars) s = s.replace(/\{(\w+)\}/g, (m, k) => (vars[k] ?? m));
  return s;
}

/** A count: `key.one` where the language uses the singular (1; in French also 0), `key.other` otherwise; {n} is the count. */
const PLURALS = {};
function tn(key, n, vars) {
  const rule = PLURALS[lang] || (PLURALS[lang] = new Intl.PluralRules(lang));
  return t(`${key}.${rule.select(n) === 'one' ? 'one' : 'other'}`, { n, ...vars });
}

/** A value from the pipeline in plain words (a drawing kind, a region class, a scale cue, a confidence):
    `group.value` when i18n.json has it, else the value itself. */
function tv(group, value) { const k = `${group}.${value}`; return TEXTS[k] ? t(k) : String(value ?? ''); }

/** The page's own texts: data-i18n (text), data-i18n-html (markup from i18n.json), data-i18n-title and
    data-i18n-aria-label. An element keeps its English when i18n.json has no entry for it. */
function applyTexts(root = document) {
  for (const el of root.querySelectorAll('[data-i18n]')) if (TEXTS[el.dataset.i18n]) el.textContent = t(el.dataset.i18n);
  for (const el of root.querySelectorAll('[data-i18n-html]')) if (TEXTS[el.dataset.i18nHtml]) el.innerHTML = t(el.dataset.i18nHtml);
  for (const el of root.querySelectorAll('[data-i18n-title]')) if (TEXTS[el.dataset.i18nTitle]) el.title = t(el.dataset.i18nTitle);
  for (const el of root.querySelectorAll('[data-i18n-aria-label]')) if (TEXTS[el.dataset.i18nAriaLabel]) el.setAttribute('aria-label', t(el.dataset.i18nAriaLabel));
}

/** Switch the language: remembered, put in the address, and the page reloads (the project and its sheets come back). */
function setLang(next) {
  if (!LANGS.includes(next) || next === lang) return;
  try { localStorage.setItem(KEY, next); } catch (e) { /* storage blocked */ }
  const url = new URL(location.href);
  url.searchParams.set('lang', next);
  location.replace(url);
}

const currentLang = () => lang;

export { applyTexts, currentLang, loadTexts, setLang, t, tn, tv };
