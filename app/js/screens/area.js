/* Step 1, building area (board 1d): the detected outline with handles; also the waiting screen, the walk to the next
   package and the request to read the scale cues of an area again. */
import { ACCENT, MUTED, NEUTRAL, REGION_COLOURS, fmt, h, postJSON, put, sheetUrl } from '../core.js';
import { t, tn, tv } from '../i18n.js';
import { round1 } from '../measure.js';
import { J, P, atCursor, bboxOf, keyOf, packagesOf, polygonOf, ppmOf, refreshJob, scaleStale, selectedDrawings } from '../state.js';
import { busyScreen, go, keepFocus, loadPreview, mount, notify, once, post, pressable, projectName, resumeWorkflow, stepHead, useViewer } from '../ui.js';
import { RectTool, scaled } from '../viewer.js';

// --- Step 1: building area (board 1d) ------------------------------------------------------------------------------

const sizeText = (r, ppm) => ppm ? `${fmt.n((r.x1 - r.x0) / ppm)} × ${fmt.n((r.y1 - r.y0) / ppm)} m` : `${Math.round(r.x1 - r.x0)} × ${Math.round(r.y1 - r.y0)} px`;
const scaleRect = (r, k) => ({ x0: r.x0 * k, y0: r.y0 * k, x1: r.x1 * k, y1: r.y1 * k });

function renderArea() {
  const { job, pk, pi, pkg } = atCursor();
  if (!job || !job.analysis) return renderWaiting(job);
  if (!pkg) return go('upload');                          // an analysis without pages
  const sel = new Set(selectedDrawings(job, pkg).map((d) => d.id));
  const rects = {};                                  // drawing id -> editable rect in sheet px (the tool works in preview px)
  for (const d of pkg.drawings) rects[d.id] = bboxOf(polygonOf(job, pkg, d));
  let current = pkg.drawings.find((d) => sel.has(d.id)) || pkg.drawings[0];
  const f = pkg.preview.scale;                       // preview px per sheet px
  const stage = h('main', { class: 'stage grid-bg' });
  const top = h('div', { class: 'stage-top' },
    h('div', { class: 'pill' }, h('span', { class: 'muted' }, t('area.sheetOf', { i: P.cursor.sheet + 1, n: P.sheets.length })), h('b', null, job.name), pk.length > 1 ? h('span', { class: 'muted' }, '· ' + t('area.pageOf', { i: pi + 1, n: pk.length })) : null));
  stage.append(top, h('div', { class: 'stage-note' }, t('area.note')));
  const list = h('div', { class: 'drawing-list' });
  const panel = h('aside', { class: 'panel' },
    stepHead(1, t('area.eyebrow'), t('area.title')),
    h('p', { class: 'muted pretty' }, t('area.text')),
    pkg.drawings.length > 1 ? list : null,            // which drawing to extract: only a question when the sheet has several
    pkg.drawings.length === 0 ? h('div', { class: 'warn-box' }, t('area.noDrawing')) : null,
    h('div', { class: 'foot' },
      h('button', { class: 'btn', onclick: once(() => confirmArea(job, pkg, rects, new Set())) }, t('area.skip')),
      h('button', { class: 'btn btn-primary', disabled: !pkg.drawings.length, onclick: once(() => confirmArea(job, pkg, rects, sel)) }, t('area.confirm'))));
  mount(h('div', { class: 'step' }, stage, panel), projectName());
  const viewer = useViewer(stage);
  const tool = new RectTool(current ? scaleRect(rects[current.id], f) : { x0: 0, y0: 0, x1: 0, y1: 0 }, (r) => { if (current) rects[current.id] = scaleRect(r, 1 / f); drawList(); });
  if (current) viewer.tool = tool;
  function drawList() { keepFocus(drawListNow); }
  function drawListNow() {
    put(list, h('div', { class: 'section-title' }, tn('area.drawingsOnSheet', pkg.drawings.length)));
    for (const d of pkg.drawings) {
      const name = d.title || `${tv('kind', d.kind)} ${d.id}`;
      const cb = h('input', { type: 'checkbox', checked: sel.has(d.id), 'aria-label': t('area.extractNamed', { name }), onclick: (e) => { e.stopPropagation(); },
        onchange: (e) => { e.target.checked ? sel.add(d.id) : sel.delete(d.id); drawList(); viewer.draw(); } });
      const choose = () => { current = d; tool.r = scaleRect(rects[d.id], f); viewer.tool = tool; drawList(); viewer.draw(); };
      list.append(h('div', pressable(choose, { class: 'drawing-row' + (d === current ? ' on' : ''), 'aria-pressed': String(d === current) }),
        cb, h('div', { class: 'clip' }, h('div', { class: 't' }, name), h('div', { class: 's' }, `${d.id} · ${sizeText(rects[d.id], ppmOf(job, pkg, d))}${d.storey ? ' · ' + d.storey : ''}${d.kind_confidence === 'low' ? ' · ' + t('area.kindAssumed') : ''}`)),
        h('span', { class: 'kind' }, tv('kind', d.kind))));
    }
  }
  drawList();
  viewer.overlay = (c, v) => {
    for (const r of pkg.regions) {
      if (r.class === 'drawing' || r.class === 'frame' || !r.polygon_px || r.polygon_px.length < 3) continue;
      const col = REGION_COLOURS[r.class] || NEUTRAL;
      v.poly(scaled(r.polygon_px, f), { stroke: col, width: 1, dash: [4, 3], fill: col + '14' });
      const b = bboxOf(r.polygon_px); v.label(b.x0 * f, b.y0 * f, tv('region', r.class), { colour: col, size: 10, dy: -2 });
    }
    for (const d of pkg.drawings) {
      const pts = scaled(d.polygon_px, f);
      if (d === current) v.poly(pts, { stroke: ACCENT, width: 1, dash: [5, 4] });
      else { v.poly(pts, { stroke: sel.has(d.id) ? ACCENT : NEUTRAL, width: 1.5, dash: [6, 4] }); const b = bboxOf(pts); v.label(b.x0, b.y0, `${tv('kind', d.kind)}${sel.has(d.id) ? '' : ' ' + t('area.notExtracted')}`, { colour: sel.has(d.id) ? ACCENT : MUTED }); }
    }
    if (current) {
      const r = rects[current.id];
      v.label(r.x0 * f, r.y0 * f, t('area.detected', { size: sizeText(r, ppmOf(job, pkg, current)) }), { dy: -14 });
    }
  };
  loadPreview(viewer, job, pkg, panel);
}
async function confirmArea(job, pkg, rects, sel) {
  const body = {};
  for (const d of pkg.drawings) {
    const r = rects[d.id]; const det = bboxOf(d.polygon_px);
    const changed = ['x0', 'y0', 'x1', 'y1'].some((k) => Math.abs(r[k] - det[k]) > 0.5);
    body[keyOf(pkg, d)] = { extract: sel.has(d.id), area_confirmed: true,
      polygon_px: changed ? [[r.x0, r.y0], [r.x1, r.y0], [r.x1, r.y1], [r.x0, r.y1]].map((p) => p.map(round1)) : null };
  }
  const r = await post(sheetUrl(job.id, '/confirm'), { confirm: body }); if (!r) return;
  job.confirm = r.confirm;
  // the analysis read the scale cues inside the detected areas: they are read again only for a drawing whose area
  // changed since, or that had none read (not a floor plan to begin with); otherwise step 2 opens at once
  const keys = pkg.drawings.filter((d) => sel.has(d.id) && scaleStale(job.confirm[keyOf(pkg, d)])).map((d) => keyOf(pkg, d));
  if (keys.length) await requestScale(job, keys, false, true);
  const first = selectedDrawings(job, pkg)[0];
  if (first) go('scale', { ...P.cursor, drawing: pkg.drawings.indexOf(first) });
  else nextPackage();
}

// the scale cues are read for a drawing's area: the analysis reads them in the detected area, a changed area or a
// drawing chosen later reads them again (POST /scale). Asked once per drawing, area and kind, so a screen that renders
// again does not ask twice; a failure is not tried again by itself (confirming the area again does)
const SCALE_REQUESTED = new Set();
const scaleTag = (job, key, doors) => `${job.id}|${key}|${doors ? 'doors' : 'cues'}|${JSON.stringify(((job.confirm || {})[key] || {}).polygon_px || null)}`;
const scaleRequested = (job, key, doors = false) => SCALE_REQUESTED.has(scaleTag(job, key, doors));
/** Queue the scale step for the drawings `keys` of the sheet (doors: the door-width search with the segmenter); true
    when queued now. `again`: the user asked, so drawings asked for before are asked for again. */
async function requestScale(job, keys, doors = false, again = false) {
  if (again) keys.forEach((k) => SCALE_REQUESTED.delete(scaleTag(job, k, doors)));
  const fresh = keys.filter((k) => !scaleRequested(job, k, doors));
  if (!fresh.length) return false;
  fresh.forEach((k) => SCALE_REQUESTED.add(scaleTag(job, k, doors)));
  try { await postJSON(sheetUrl(job.id, '/scale'), { keys: fresh, doors }); } catch (e) { notify(e.message); return false; }
  await refreshJob(job.id);
  return true;
}

function nextPackage() {
  const job = J[P.sheets[P.cursor.sheet]];
  if (P.cursor.pkg + 1 < packagesOf(job).length) return go('area', { ...P.cursor, pkg: P.cursor.pkg + 1, drawing: 0 });
  resumeWorkflow();
}
/** A step opened before its sheet is analysed (a deep link, a reload): the busy screen with the way back. */
function renderWaiting(job) {
  if (!job) return go('upload');
  busyScreen([job], { title: projectName() || t('upload.newProject'), back: () => go('upload') });
}

export { nextPackage, renderArea, renderWaiting, requestScale, scaleRequested };
