/* Floor plan workflow app: upload -> building area -> scale -> results and export (the extraction starts when the scale is confirmed).
   Follows the boards of docs/wireframes/261007_Viewer and Workflow UX study.html (1c, 1d, 2a-2c, 1g/1h). Entry module: registers the screens and boots. */
import { renderArea } from './screens/area.js';
import { $ } from './core.js';
import { applyTexts, currentLang, loadTexts, t } from './i18n.js';
import { renderResults } from './screens/results.js';
import { renderScale } from './screens/scale.js';
import { renderUpload } from './screens/upload.js';
import { drawingRows, getHealth, J, noAppServer, onJobs, onSheetGone, P, refreshAll, resetProject, saveProject, startPolling } from './state.js';
import { dialog, langSelect } from './chrome.js';
import { abortProject, go, initChrome, notify, onJobsChanged, register } from './ui.js';

for (const [name, fn] of Object.entries({ upload: renderUpload, area: renderArea, scale: renderScale, results: renderResults })) register(name, fn);
// the storeys and run steps of projects saved before October 2026: they open the results (the redirect can go in 2027)
for (const old of ['storeys', 'run']) register(old, () => go('results'));
onJobs(onJobsChanged);

/** The page without its app server: opened from disk, from a plain file server, or the server is not running. */
function showOffline() { $('#app').hidden = true; $('#offline').hidden = false; }

async function boot() {
  if (location.protocol === 'file:') return showOffline();          // i18n.json cannot load from disk either: the English stays
  await loadTexts();
  applyTexts();
  $('#app').hidden = false;
  dialog('help', '#btn-help');
  dialog('login', '#btn-login');                    // a demo: the login (and its API tokens) is not there yet
  langSelect($('#lang-select'));
  initChrome();
  // the public server keeps nothing: the help says how long a sheet lives, and starting over deletes the sheets there
  let pub = null;
  getHealth().then((hl) => {
    if (!hl.public) return;
    pub = hl;
    $('#help-storage').textContent = t('help.storagePublic', { minutes: hl.keep_minutes });
  }).catch((e) => { if (noAppServer(e)) showOffline(); });
  onSheetGone(() => { notify(pub ? t('error.sheetExpired', { minutes: pub.keep_minutes }) : t('error.sheetGone'), 'info'); go(drawingRows().length ? 'results' : 'upload'); });
  $('#btn-start-over').onclick = () => {
    if (pub) { if (confirm(t('app.startOverPublic'))) abortProject(); return; }
    if (!confirm(t('app.startOverLocal'))) return;
    resetProject(); go('upload');
  };
  // deep link: ?sheets=<id>,<id>&screen=area|scale|results&key=<id>|<package>/<drawing>&pkg=0&drawing=0&sheet=0 (also used for screenshots)
  const q = new URLSearchParams(location.search);
  if (q.get('sheets')) {
    P.sheets = q.get('sheets').split(',').filter(Boolean); P.resultsKey = q.get('key') || null;
    P.screen = q.get('screen') || 'upload'; P.cursor = { sheet: +(q.get('sheet') || 0), pkg: +(q.get('pkg') || 0), drawing: +(q.get('drawing') || 0) };
    const keep = q.get('lang') ? `?lang=${currentLang()}` : '';    // the language stays in the address
    saveProject(); history.replaceState(null, '', location.pathname + keep);
  }
  await refreshAll();
  startPolling();
  const first = P.sheets.length ? P.screen : 'upload';
  if ((first === 'area' || first === 'scale') && !(J[P.sheets[P.cursor.sheet]] || {}).analysis) return go('upload');
  go(first);
}
document.addEventListener('DOMContentLoaded', boot);
