/* Upload screen (board 1c): drop zone, the uploads in progress, sheet list, the explainer film, pipeline check. */
import { ApiError, api, getJSON, h, put, sheetUrl, stageInfo } from '../core.js';
import { currentLang, t, tn } from '../i18n.js';
import { J, P, busy, deleteSheet, drawingRows, getHealth, packagesOf, refreshJob, saveProject, screenName } from '../state.js';
import { abortProject, badge, busyScreen, go, guarded, iconEl, keepFocus, mount, notify, projectName, resumeWorkflow } from '../ui.js';

// --- Upload (board 1c) ---------------------------------------------------------------------------------------------

const UPLOAD_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>';
const ACCEPT = '.pdf,.png,.jpg,.jpeg,.tif,.tiff,.dxf,.dwg';
const MAX_UPLOAD = 300 * 1024 * 1024;                     // as server.py MAX_UPLOAD and nginx's client_max_body_size
const RATE_WAIT_MS = 11000, RATE_TRIES = 6;               // the public server takes about 6 uploads a minute (deploy/nginx.conf)
let health = null, demo;                 // demo: undefined until asked, then the server's demo record or null
const sending = { files: [], index: 0, fraction: 0, waiting: false };   // the uploads in progress, one at a time
let progress = null;                     // the progress row on show: { bar, label }, updated in place
const advanced = new Set();              // sheets the page has moved on from by itself

async function renderUpload({ changed } = {}) {
  if (changed && maybeAdvance()) return;
  const adding = drawingRows().length > 0;
  if (demo === undefined) { try { demo = (await getJSON('/api/demo')).demo; } catch (e) { demo = null; } if (screenName !== 'upload') return; }
  // sheets being analysed and nothing else: no drop zone, a spinner and the way out
  const pending = P.sheets.filter((id) => J[id] && busy(J[id]));
  if (!adding && !sending.files.length && pending.length && !P.sheets.some((id) => J[id] && !busy(J[id]))) return keepFocus(() => busyScreen(pending.map((id) => J[id]), { abort: abortProject }));
  const input = h('input', { type: 'file', multiple: true, hidden: true, accept: ACCEPT, onchange: (e) => { uploadFiles([...e.target.files]); e.target.value = ''; } });
  const drop = h('div', { class: 'dropzone', onclick: () => input.click(),
    ondragover: (e) => { e.preventDefault(); drop.classList.add('over'); }, ondragleave: () => drop.classList.remove('over'),
    ondrop: (e) => { e.preventDefault(); drop.classList.remove('over'); uploadFiles([...e.dataTransfer.files]); } },
    h('span', { class: 'icon', 'aria-hidden': 'true', html: UPLOAD_ICON }), h('div', { class: 'title' }, t('upload.drop')),
    h('button', { class: 'btn btn-primary btn-lg', onclick: (e) => { e.stopPropagation(); input.click(); } }, t('upload.choose')),
    h('div', { class: 'hint' }, t('upload.formats')),
    demo && !adding ? h('p', { class: 'demo-hint' }, t('upload.demoHint'), ' ',
      h('button', { type: 'button', class: 'demo-link', title: [demo.title, demo.source, demo.licence].filter(Boolean).join(' · '), onclick: guarded(async (e) => { e.stopPropagation(); await startDemo(); }) }, t('upload.demoLink'))) : null);
  const list = h('div', { class: 'file-list' });
  for (const id of P.sheets) {
    const job = J[id]; if (!job) continue;
    const rows = packagesOf(job);
    const n = rows.reduce((a, p) => a + p.drawings.length, 0);
    const st = job.status;
    const sub = st === 'analysing' || st === 'scaling' ? stageInfo(job.progress && job.progress.stage).label
      : st === 'error' ? job.error : st === 'ready' ? t('upload.analysed', { pages: tn('upload.pages', rows.length), drawings: tn('upload.drawings', n) })
      : st === 'done' ? t('upload.extracted') : t('status.' + st);
    const state = st === 'analysing' ? badge('run', t('badge.analysing')) : st === 'scaling' ? badge('run', t('badge.scaling')) : st === 'error' ? badge('err', t('badge.error')) : st === 'ready' ? badge('ok', t('badge.ready')) : badge('wait', t('status.' + st));
    list.append(h('div', { class: 'file-row' },
      h('div', { class: 'clip' }, h('div', { class: 'name' }, job.name), h('div', { class: 'sub', title: sub, lang: st === 'error' ? 'en' : null }, sub)),
      h('div', { class: 'actions' }, state,
        st === 'error' ? h('a', { class: 'btn', href: sheetUrl(id, '/log'), target: '_blank', rel: 'noopener' }, t('upload.log')) : null,
        h('button', { class: 'btn-x', title: t('upload.remove'), 'aria-label': t('upload.removeNamed', { name: job.name }), onclick: guarded(() => removeSheet(id)) }, iconEl('x')))));
  }
  progress = null;
  if (sending.files.length) list.append(uploadRow());
  const anyReady = P.sheets.some((id) => J[id] && (J[id].status === 'ready' || J[id].status === 'done'));
  const anyBusy = sending.files.length || P.sheets.some((id) => J[id] && busy(J[id]));
  const cont = anyReady && !anyBusy ? h('button', { class: 'btn btn-primary btn-lg', onclick: resumeWorkflow }, t('upload.continue')) : null;
  const status = h('div', { class: 'status-line' }, h('span', { class: 'spinner' }), t('upload.checking'));
  const main = h('main', { class: 'upload grid-bg' }, h('div', { class: 'upload-inner' },
    h('div', { class: 'stack-sm' }, h('h2', null, adding ? t('upload.addSheet') : t('upload.tagline')),
      h('p', { class: 'muted pretty' }, adding ? t('upload.addText') : t('upload.taglineText'))),
    drop, input, list.childElementCount ? list : null, cont, adding ? h('button', { class: 'btn btn-lg', onclick: () => go('results') }, t('upload.backToResults')) : howItWorks(), status));
  keepFocus(() => mount(main, adding ? projectName() : null));
  if (!health) { try { health = await getHealth(); } catch (e) { health = { ok: false, items: { server: { ok: false, detail: e.message } }, retry: true }; } }
  if (health.retry) { const h0 = health; health = null; return showHealth(h0); }   // not kept: the next visit asks again
  showHealth(health);
  function showHealth(health) {
  if (screenName !== 'upload' || !status.isConnected) return;
  put(status, h('span', { class: 'dot10 ' + (health.ok ? 'c-success' : 'c-danger') }),
    health.ok ? (health.public ? t('upload.healthPublic', { minutes: health.keep_minutes }) : t('upload.healthLocal'))
      : t(health.public ? 'upload.missingServer' : 'upload.missingMachine'));
  if (health.public && health.ok) return;                  // visitors need no list of libraries
  const rows = Object.entries(health.items || {}).map(([k, v]) => h('div', { class: 'row' }, h('span', { class: v.ok ? 'ok' : 'bad' }, v.ok ? '✓' : '✗'), h('b', null, k), h('span', { class: 'muted', lang: 'en' }, v.detail)));
  const det = h('details', null, h('summary', { title: health.pipeline_dir || '' }, t('upload.pipelineCheck')), h('div', { class: 'health' }, rows));
  if (!health.ok) det.open = true;
  status.after(det);
  }
}
const rerender = () => { if (screenName === 'upload') renderUpload(); };

// --- the explainer film of the landing page ---------------------------------------------------------------------------
// media/how-it-works-<lang>.webm, rendered from media/how-it-works.html by media/render.py, in a player of the page's
// own: a play mark over the poster, a bar with play/pause, the timeline, the clock, replay and full screen. Nothing
// plays by itself. The player is kept across re-renders of the screen so a film being watched plays on.
let film = null;                         // { el, player, pauseBtn }
const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
function howItWorks() {
  if (!film) {
    const lang = currentLang();
    const el = h('video', { class: 'how-film', src: `/media/how-it-works-${lang}.webm`, poster: `/media/how-it-works-${lang}.jpg`, preload: 'metadata', playsinline: true, tabindex: 0, 'aria-label': t('upload.filmLabel') });
    el.playsInline = true;
    const stage = h('div', { class: 'how-stage paused' }, el, h('span', { class: 'how-play', 'aria-hidden': 'true' }, iconEl('play')));
    const pauseBtn = h('button', { type: 'button', class: 'how-btn', 'aria-label': t('upload.play') }, iconEl('play'));
    const time = h('input', { type: 'range', class: 'ctl-range how-time', min: 0, max: 30, step: 0.1, value: 0, 'aria-label': t('upload.timeline') });
    const shown = h('span', { class: 'how-clock' }, '0:00 / 0:30');
    const player = h('div', { class: 'how-player' }, stage, h('div', { class: 'how-bar' }, pauseBtn, time, shown,
      h('button', { type: 'button', class: 'how-btn', 'aria-label': t('upload.replay'), onclick: () => { el.currentTime = 0; el.play().catch(() => {}); } }, iconEl('rotate')),
      h('button', { type: 'button', class: 'how-btn', 'aria-label': t('upload.fullscreen'), onclick: () => {
        if (document.fullscreenElement) document.exitFullscreen();
        else if (player.requestFullscreen) player.requestFullscreen().catch(() => {});
        else if (el.webkitEnterFullscreen) el.webkitEnterFullscreen();   // iOS: the video alone, in the system player
      } }, iconEl('maximize'))));
    film = { el, player, pauseBtn };
    const toggle = () => { if (el.paused) el.play().catch(() => {}); else el.pause(); };
    stage.onclick = toggle;
    pauseBtn.onclick = toggle;
    el.onkeydown = (e) => {
      if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); toggle(); }
      else if (e.key === 'ArrowRight') el.currentTime = Math.min(el.duration || 30, el.currentTime + 5);
      else if (e.key === 'ArrowLeft') el.currentTime = Math.max(0, el.currentTime - 5);
      else if (e.key === 'Home') el.currentTime = 0;
    };
    const state = () => { stage.classList.toggle('paused', el.paused); pauseBtn.setAttribute('aria-label', el.paused ? t('upload.play') : t('upload.pause')); put(pauseBtn, iconEl(el.paused ? 'play' : 'pause')); };
    el.onplay = state; el.onpause = state; el.onended = state;
    const tick = () => { const d = el.duration || 30; time.max = d; time.value = el.currentTime; shown.textContent = `${clock(el.currentTime)} / ${clock(d)}`; };
    el.onloadedmetadata = tick; el.ontimeupdate = tick;
    time.oninput = () => { el.currentTime = +time.value; tick(); };
  }
  const playing = !film.el.paused;                        // a media element pauses when a re-render moves it: play on
  setTimeout(() => { if (playing && film.el.isConnected) film.el.play().catch(() => {}); }, 0);
  return h('section', { class: 'how stack' }, h('h3', { class: 'section-title' }, t('upload.howTitle')), film.player, h('p', { class: 'know muted' }, t('upload.know')));
}

/** The row of the upload in progress: name, n of m, a progress bar (updated in place by showProgress). */
function uploadRow() {
  const f = sending.files[sending.index];
  progress = { bar: h('i'), label: h('div', { class: 'sub' }) };
  const row = h('div', { class: 'file-row', role: 'status' },
    h('div', { class: 'clip stack-sm' }, h('div', { class: 'name' }, t('upload.uploading', { name: f.name, i: sending.index + 1, n: sending.files.length })),
      progress.label, h('div', { class: 'progress-line' }, progress.bar)),
    h('div', { class: 'actions' }, badge('run', t('upload.sending'))));
  showProgress();
  return row;
}
function showProgress() {
  if (!progress) return;
  progress.bar.style.width = `${Math.round(100 * sending.fraction)}%`;
  progress.label.textContent = sending.waiting ? t('upload.rateWait') : `${Math.round(100 * sending.fraction)} %`;
}

/** Upload files one at a time; files dropped meanwhile join the queue. */
async function uploadFiles(files) {
  if (!files.length) return;
  const running = sending.files.length > 0;
  sending.files.push(...files);
  if (running) return rerender();
  while (sending.index < sending.files.length) {
    const f = sending.files[sending.index];
    Object.assign(sending, { fraction: 0, waiting: false });
    rerender();
    try {
      const r = await sendFile(f);
      P.sheets.push(r.id); saveProject();
      await refreshJob(r.id);
    } catch (e) {
      notify(`${f.name}: ${e.message}`);
    }
    sending.index++;
  }
  Object.assign(sending, { files: [], index: 0 });
  if (!maybeAdvance()) rerender();
}
/** One upload; a refusal by the rate limit of the public server waits and tries again. */
async function sendFile(f) {
  if (f.size > MAX_UPLOAD) throw new ApiError(413, null);   // the proxy would cut the connection mid-upload
  for (let tries = 1; ; tries++) {
    try { return await xhrUpload(f); } catch (e) {
      if (e.status !== 429 || tries >= RATE_TRIES) throw e;
      sending.waiting = true; showProgress();
      await new Promise((resolve) => setTimeout(resolve, RATE_WAIT_MS));
      sending.waiting = false;
    }
  }
}
/** POST /api/upload with the file as the body (XMLHttpRequest: fetch reports no upload progress). */
function xhrUpload(f) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open('POST', `/api/upload?name=${encodeURIComponent(f.name)}`);
    x.setRequestHeader('Content-Type', 'application/octet-stream');
    x.upload.onprogress = (e) => { if (e.lengthComputable) { sending.fraction = e.loaded / e.total; showProgress(); } };
    x.onload = () => {
      let data = null;
      try { data = JSON.parse(x.responseText); } catch (e) { /* an error page of the proxy */ }
      if (x.status >= 200 && x.status < 300 && data) resolve(data); else reject(new ApiError(x.status, data));
    };
    x.onerror = () => reject(new ApiError(0, null));
    x.send(f);
  });
}
/** The demo sheet: the server creates the job from its bundled copy; the rest is the normal flow. */
async function startDemo() {
  try {
    const r = await api('/api/demo', { method: 'POST' });
    P.sheets.push(r.id); saveProject();
    await refreshJob(r.id);
  } catch (e) { return notify(e.message); }
  if (!maybeAdvance()) rerender();
}
async function removeSheet(id) {
  if (!confirm(t('upload.removeConfirm'))) return;
  await deleteSheet(id);
  rerender();
}
/** When the uploads are analysed and nothing else is pending, move on to step 1 by itself (once per sheet). */
function maybeAdvance() {
  if (screenName !== 'upload' || sending.files.length || P.sheets.some((id) => !J[id] || busy(J[id]))) return false;
  const fresh = P.sheets.filter((id) => J[id].status === 'ready' && !advanced.has(id));
  if (!fresh.length) return false;
  fresh.forEach((id) => advanced.add(id));
  resumeWorkflow();
  return true;
}

export { renderUpload };
