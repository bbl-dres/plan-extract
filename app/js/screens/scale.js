/* Step 2, plan scale (boards 2a-2c): proposal, cues, resolution, measuring a known distance. */
import { $, ACCENT, AMBER, T, fmt, h, put, sheetUrl } from '../core.js';
import { t } from '../i18n.js';
import { UNITS, dist, parseLength, parseNumber, ppmFromScale, round1, scaleNumber } from '../measure.js';
import { nextPackage, renderWaiting, requestScale, scaleRequested } from './area.js';
import { P, atCursor, bboxOf, busy, confOf, keyOf, polygonOf, refreshJob, saveProject, scaleStale, screenName, selectedDrawings, sheetConfirmed } from '../state.js';
import { errorBox, focusHeading, go, guarded, iconEl, keepFocus, loadPreview, mount, post, projectName, stageOverlay, stepHead, useViewer } from '../ui.js';
import { MeasureTool, hexA, scaled } from '../viewer.js';

// --- Step 2: plan scale (boards 2a, 2b, 2c) ------------------------------------------------------------------------

/** The scale step while the server reads the cues: the drawing on the stage, a spinner in the panel. */
function renderScaleWait(job, pkg, dr) {
  const doors = !!(job.progress && job.progress.doors);
  const stage = h('main', { class: 'stage grid-bg' });
  const panel = h('aside', { class: 'panel' }, stepHead(2, t('scale.eyebrow'), t('scale.title')),
    h('div', { class: 'row row-md' }, h('span', { class: 'spinner' }),
      h('span', null, doors ? t('scale.waitDoors') : t('scale.waitCues'))),
    h('p', { class: 'muted small pretty' }, doors ? t('scale.waitDoorsTime') : t('scale.waitCuesTime')),
    job.error ? errorBox(job) : null,
    h('div', { class: 'foot' }, h('button', { class: 'btn', onclick: () => go('area', { ...P.cursor }) }, t('scale.back'))));
  mount(h('div', { class: 'step' }, stage, panel), projectName(), { wait: true });
  const viewer = useViewer(stage); const f = pkg.preview.scale;
  viewer.overlay = (c, v) => { v.poly(scaled(polygonOf(job, pkg, dr), f), { stroke: ACCENT, width: 1.5, dash: [6, 4] }); };
  loadPreview(viewer, job, pkg, panel).then(() => {        // the wait is visible on the drawing too (a phone shows the panel below it)
    if (stage.isConnected) stageOverlay(stage, doors ? t('scale.waitDoors') : t('scale.waitCues'), doors ? t('scale.waitDoorsTime') : t('scale.waitCuesTime'));
  });
}
function scaleCase(dr) {
  const s = dr.scale || {}; const agree = (s.agreeing || []).map((a) => a.cue);
  if (!s.px_per_m) return 'none';
  if (agree.includes('viewport') || agree.includes('model_units')) return 'exact';
  if (agree.includes('scale_note') || agree.includes('sheet_scale_note')) return 'note';
  if (agree.includes('dimension_strings')) return 'dims';
  if (agree.includes('scale_bar') || agree.includes('linked')) return 'bar';
  if (agree.includes('door_widths')) return 'doors';
  return 'other';
}
const CASE_TEXT = {                                       // i18n keys: the explanation and the warning of each case
  exact: ['scale.case.exact', null],
  note: ['scale.case.note', null],
  dims: ['scale.case.dims', null],
  bar: ['scale.case.bar', 'scale.case.barWarn'],
  doors: ['scale.case.doors', 'scale.case.doorsWarn'],
  other: ['scale.case.other', null],
  none: ['scale.case.none', 'scale.case.noneWarn'],
};

function renderScale() {
  const { job, pkg, dr } = atCursor();
  if (!job || !job.analysis) return renderWaiting(job);
  if (!dr) return go('area', { ...P.cursor, drawing: 0 });        // a page without drawings has no scale to check
  // the analysis reads the scale cues; while they are read again (a changed area) or were never read, the step waits,
  // and asks for them once when nothing runs (a drawing chosen after the analysis, a read that failed)
  const key = keyOf(pkg, dr), conf = confOf(job, pkg, dr);
  const stale = !dr.scale || scaleStale(conf);
  if (stale && busy(job)) return renderScaleWait(job, pkg, dr);
  if (stale && !scaleRequested(job, key)) {
    requestScale(job, [key]).then(() => { if (screenName === 'scale') renderScale(); });
    return renderScaleWait(job, pkg, dr);
  }
  const s = dr.scale || {}; const f = pkg.preview.scale;      // preview px per sheet px
  const sel = selectedDrawings(job, pkg); const di = sel.indexOf(dr);
  const kind = scaleCase(dr);
  const noteN = s.note && s.note.scale;
  const noteBased = kind === 'note' && !(s.agreeing || []).some((a) => ['dimension_strings', 'scale_bar', 'linked', 'viewport', 'model_units'].includes(a.cue));
  // state of the step
  const st = { mode: conf.measured ? 'measured' : 'check', dpi: conf.dpi || pkg.summary.dpi || null, dpiChanged: !!conf.dpi,
    measured: conf.measured || null, pts: [], distance: conf.measured ? conf.measured.distance : '', unit: conf.measured ? conf.measured.unit : 'm' };
  const ppmProposal = s.px_per_m || null;
  const measuring = () => st.mode === 'measure' || st.mode === 'measured';
  /** The measured distance in sheet px: the tool's points are scaled back to sheet px as they come in. */
  const measuredPx = () => st.mode === 'measured' ? st.measured.px : st.pts.length === 2 ? dist(st.pts[0], st.pts[1]) : null;
  function ppmNow() {
    if (measuring()) { const px = measuredPx(), m = parseLength(st.distance, st.unit); return px && m > 0 ? px / m : null; }
    if (st.dpiChanged && noteBased && noteN && st.dpi) return ppmFromScale(st.dpi, noteN);
    return conf.px_per_m && conf.scale_source !== 'measured' ? conf.px_per_m : ppmProposal;
  }
  const N = (ppm) => scaleNumber(st.dpi, ppm);
  const stage = h('main', { class: 'stage grid-bg' });
  const topPill = h('div', { class: 'pill' });
  const btnReset = h('button', { class: 'btn btn-round', title: t('scale.measureReset'), 'aria-label': t('scale.measureReset'), onclick: () => { if (tool) tool.reset(); } }, iconEl('rotate'));
  const btnHelp = h('button', { class: 'btn btn-round strong', 'aria-label': t('top.help'), onclick: () => $('#btn-help').click() }, '?');
  stage.append(h('div', { class: 'stage-top' }, topPill, btnReset, btnHelp));
  const panel = h('aside', { class: 'panel' });
  mount(h('div', { class: 'step' }, stage, panel), projectName());
  const viewer = useViewer(stage);
  let tool = null, confirmBtn = null;
  const measure = new MeasureTool((pts) => { st.pts = pts.map((p) => [p[0] / f, p[1] / f]); renderPanel(); });
  measure.pxScale = 1 / f;
  viewer.overlay = (c, v) => {
    v.poly(scaled(polygonOf(job, pkg, dr), f), { stroke: ACCENT, width: 1.5, dash: [6, 4] });
    for (const b of (s.cue_boxes || [])) {
      const [x0, y0, x1, y1] = b.box;
      v.rect({ x0: x0 * f, y0: y0 * f, x1: x1 * f, y1: y1 * f }, { stroke: AMBER, width: 3, fill: hexA(AMBER, .08) });
      if (b.cue !== 'dimension_strings') v.label(x0 * f, y0 * f, b.label, { colour: T('--warning-text-strong', '#8a5a00'), dy: -8 });
    }
    if (st.mode === 'measured' && st.measured && st.measured.p1) {
      const p = scaled([st.measured.p1, st.measured.p2], f);
      v.poly(p, { stroke: ACCENT, width: 3, close: false }); p.forEach(([x, y]) => v.handle(x, y, 7));
      v.label((p[0][0] + p[1][0]) / 2, (p[0][1] + p[1][1]) / 2, `${Math.round(st.measured.px)} px`, { bg: ACCENT, align: 'center', dy: -10 });
    }
  };
  // the view frames the drawing and its scale cues (at most 3x closer than the whole sheet), also after a resize or Fit
  const b = bboxOf(polygonOf(job, pkg, dr));
  const cues = (s.cue_boxes || []).map((c) => c.box);
  const bb = bboxOf([[b.x0, b.y0], [b.x1, b.y1], ...cues.flatMap((c) => [[c[0], c[1]], [c[2], c[3]]])]);
  loadPreview(viewer, job, pkg, panel, { x0: bb.x0 * f, y0: bb.y0 * f, x1: bb.x1 * f, y1: bb.y1 * f, pad: 60, maxZoom: 3 });

  function dpiRow() {
    const apply = () => { const v = parseNumber(inp.value); if (v > 0) { st.dpi = v; st.dpiChanged = true; renderPanel(); } };
    const inp = h('input', { type: 'number', min: 10, max: 2400, step: 1, value: st.dpi || '', class: 'input-dpi', 'aria-label': t('scale.resolution'),
      onkeydown: (e) => { if (e.key === 'Enter') { e.preventDefault(); apply(); } } });
    const edit = () => { put(right, inp, h('span', { class: 'muted small' }, 'dpi'), h('button', { class: 'btn btn-sm btn-primary', onclick: apply }, t('scale.apply'))); inp.focus(); inp.select(); };
    const change = h('button', { class: 'btn btn-sm', onclick: edit }, t('scale.change'));
    const right = h('span', { class: 'row row-md' }, change, h('span', { class: 'big nowrap' }, st.dpi ? `${Math.round(st.dpi)} dpi` : t('scale.unknown')));
    return h('div', { class: 'kv kv-center' }, h('span', { class: 'stack-0' }, t('scale.resolution'), st.dpiChanged ? h('span', { class: 'muted small' }, t('scale.setByHand')) : null), right);
  }
  function renderPanel() {
    const ppm = ppmNow(); const n = N(ppm);
    const inMeasure = st.mode === 'measure';
    topPill.className = 'pill' + (inMeasure ? ' accent' : '');
    if (inMeasure) {
      const step = st.pts.length === 0 ? t('scale.click1') : st.pts.length === 1 ? t('scale.click2') : t('scale.enterDistance');
      put(topPill, h('span', { class: 'accent-text' }, t('scale.measureTitle')), h('b', null, step));
    } else put(topPill, h('span', { class: 'muted' }, t('scale.reviewing')), h('b', null, dr.title || job.name), sel.length > 1 ? h('span', { class: 'muted' }, '· ' + t('scale.drawingOf', { i: di + 1, n: sel.length })) : null);
    btnReset.hidden = !inMeasure;
    const head = stepHead(2, t('scale.eyebrow'), t('scale.title'));
    if (inMeasure) {
      // typing the distance updates only the computed rows: the field keeps its focus and caret
      const stepN = st.pts.length < 2 ? st.pts.length + 1 : 3;
      const stepText = [t('scale.click1'), t('scale.click2'), t('scale.enterDistance')][stepN - 1];
      const calc = h('span', { class: 'value-accent' }), measured = h('span');
      const update = () => {
        const p = ppmNow(), nn = N(p), px = measuredPx();
        put(measured, px ? t('scale.pxOnSheet', { px: Math.round(px) }) : '–');
        put(calc, nn ? fmt.scaleN(nn) : p ? `${fmt.n(p)} px/m` : '–');
        if (confirmBtn) confirmBtn.disabled = !p;
      };
      const inp = h('input', { type: 'text', inputmode: 'decimal', class: 'input-xl', value: st.distance, placeholder: t('scale.placeholder'), 'aria-label': t('scale.distance'),
        oninput: (e) => { st.distance = e.target.value; update(); } });
      const unit = h('select', { class: 'input-xl select-unit', 'aria-label': t('scale.unit'), onchange: (e) => { st.unit = e.target.value; update(); } },
        Object.keys(UNITS).map((u) => h('option', { value: u, selected: u === st.unit }, u)));
      const body = [
        h('div', { class: 'kv kv-center' }, h('span', { class: 'title-md' }, t('scale.measureTitle')), h('button', { class: 'btn-link', onclick: () => { st.mode = conf.measured ? 'measured' : 'check'; viewer.tool = null; viewer.canvas.style.cursor = 'grab'; renderPanel(); viewer.draw(); } }, t('common.cancel'))),
        h('p', { class: 'muted small pretty tight' }, t('scale.measureText')),
        h('div', { class: 'info-box' }, h('span', { class: 'num' }, String(stepN)), h('span', { class: 'strong' }, stepText)),
        h('div', { class: 'measure-grid' }, inp, unit),
        h('div', { class: 'scale-rows' },
          h('div', { class: 'kv' }, h('span', null, t('scale.measured')), measured),
          h('div', { class: 'kv' }, h('span', null, t('scale.calculated')), calc),
          dpiRow()),
      ];
      const focusInput = stepN === 3 && document.activeElement !== inp;
      put(panel, head, body, foot());
      update();
      if (focusInput) inp.focus();
      return;
    }
    let [text, warn] = CASE_TEXT[kind].map((k) => k && t(k));
    const dnote = (s.disagreeing || []).find((a) => a.cue === 'scale_note' || a.cue === 'sheet_scale_note');
    if (dnote && kind !== 'note') {
      const nN = s.note && s.note.scale ? `1:${s.note.scale}` : t('scale.aScaleNote');
      text = t('scale.noteUnverified', { note: nN, dpi: st.dpi ? Math.round(st.dpi) + ' dpi' : t('scale.assumedDpi'), ppm: fmt.n(dnote.px_per_m), ratio: dnote.ratio, what: kind === 'doors' ? t('scale.doorEstimate') : t('scale.measuredValue') });
      warn = kind === 'doors' ? t('scale.warnDoorsNote') : warn;
    }
    if (stale) warn = t('scale.staleWarn');                  // the cues of this area could not be read: they are those of the analysis
    const rows = [];
    if (st.mode === 'measured') {
      rows.push(h('div', { class: 'kv' }, h('span', null, t('scale.measured')), h('span', null, `${Math.round(st.measured.px)} px = ${st.measured.distance} ${st.measured.unit}`)));
      rows.push(h('div', { class: 'kv' }, h('span', null, t('scale.calculated')), h('span', { class: 'big accent-text' }, n ? fmt.scaleN(n) : '–')));
    } else {
      const shown = !st.dpiChanged && s.scale && Math.abs((ppm || 0) - (ppmProposal || 0)) < 1e-6 ? s.scale.replace(':', ' : ') : fmt.scaleN(n);
      rows.push(h('div', { class: 'kv' }, h('span', null, t('scale.scale')), h('span', { class: 'big' }, n || s.scale ? shown : ppm ? `${fmt.n(ppm)} px/m` : '–')));
    }
    rows.push(dpiRow());
    const cues = (s.agreeing || []).concat(s.disagreeing || []);
    const body = [h('p', { class: 'muted pretty' }, st.mode === 'measured' ? t('scale.setByMeasuring') : text),
      warn && st.mode !== 'measured' ? h('div', { class: 'warn-box pretty' }, warn) : null,
      h('div', { class: 'scale-rows' }, rows),
      kind === 'none' && !cues.some((a) => a.cue === 'door_widths')
        ? h('button', { class: 'btn btn-lg', title: t('scale.doorEstimateTitle'), onclick: guarded(async () => { await requestScale(job, [key], true); if (screenName === 'scale') renderScale(); }) }, t('scale.doorEstimateBtn')) : null,
      h('button', { class: 'btn btn-lg strong', onclick: () => { st.mode = 'measure'; st.pts = []; measure.pts = []; viewer.tool = measure; tool = measure; renderPanel(); viewer.draw(); } }, st.mode === 'measured' ? t('scale.measureAgain') : t('scale.correct'))];
    keepFocus(() => put(panel, head, body, foot()));
    confirmBtn.disabled = !ppm || confirming;
  }
  function foot() {
    confirmBtn = h('button', { class: 'btn btn-primary btn-xl', onclick: guarded(confirmScale) }, t('scale.confirm'));
    return h('div', { class: 'foot' }, h('button', { class: 'btn', onclick: () => go('area', { ...P.cursor }) }, t('scale.back')), confirmBtn);
  }
  let confirming = false;
  async function confirmScale() {
    const ppm = ppmNow(); if (!ppm || confirming) return;
    confirming = true;
    try { await confirmScaleNow(ppm); } finally { confirming = false; if (confirmBtn && confirmBtn.isConnected) confirmBtn.disabled = !ppmNow(); }
  }
  async function confirmScaleNow(ppm) {
    const body = { scale_confirmed: true, px_per_m: Math.round(ppm * 1000) / 1000, dpi: st.dpiChanged ? st.dpi : null };
    if (measuring()) {
      const p1 = st.mode === 'measured' ? st.measured.p1 : st.pts[0], p2 = st.mode === 'measured' ? st.measured.p2 : st.pts[1];
      body.scale_source = 'measured';
      body.measured = { px: round1(measuredPx()), distance: parseNumber(st.distance), unit: st.unit, p1: p1.map(round1), p2: p2.map(round1) };
    } else { body.scale_source = st.dpiChanged ? 'dpi changed' : 'proposal'; body.measured = null; }
    const r = await post(sheetUrl(job.id, '/confirm'), { confirm: { [key]: body } }); if (!r) return; job.confirm = r.confirm;
    const next = sel[di + 1];
    if (next) return go('scale', { ...P.cursor, drawing: pkg.drawings.indexOf(next) });
    if (sheetConfirmed(job)) {                             // the last scale of the sheet: the extraction starts now
      if (await post(sheetUrl(job.id, '/run'), {})) await refreshJob(job.id);
      P.resultsKey = `${job.id}|${key}`; saveProject();
    }
    nextPackage();
  }
  renderPanel();
  focusHeading();
}

export { renderScale };
