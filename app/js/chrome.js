/* The header pieces both pages share (the app, the API documentation): a dialog opened by its button and the
   language select. */
import { $ } from './core.js';
import { currentLang, setLang } from './i18n.js';

/** A dialog (help, login): opened by its button, closed by its × button, a click beside it, or Escape. */
function dialog(id, opener) {
  const box = $(`#${id}`), btn = $(opener);
  if (!box || !btn) return;
  const open = () => { box.hidden = false; $(`#${id}-close`).focus(); };
  const close = () => { box.hidden = true; btn.focus(); };
  btn.onclick = open;
  $(`#${id}-close`).onclick = close;
  box.onclick = (e) => { if (e.target === box) close(); };
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !box.hidden) close(); });
}

/** The language select of the header: shows the page's language, changes it (the page reloads in the new one). */
function langSelect(sel) {
  if (!sel) return;
  sel.value = currentLang();
  sel.onchange = () => setLang(sel.value);
}

export { dialog, langSelect };
