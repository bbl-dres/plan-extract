/* Results (board 1i of the UX study): layers left, figures and rooms right, both panels collapsible; a room pinned on the
   canvas opens a card. While the extraction runs the same screen shows the work in progress: the drawing at its working
   resolution, and walls, openings, stairs and rooms appearing on it as the pipeline finds them. Finished results can
   be seen in 3D (view3d.js, three.js vendored, loaded on demand). The scene itself is scene.js. */
import { ACCENT, CANVAS_FONT, confColour, every, fileUrl, fmt, getJSON, h, HALO, keyUrl, LAYER_COLOURS, MUTED, NEUTRAL, put, SELECTION, sheetUrl, stageInfo, store, SURFACE, T, TEXT } from '../core.js';
import { etaText } from '../progress.js';
import { t, tv } from '../i18n.js';
import { scaleNumber } from '../measure.js';
import { applyLive, emptyScene, openingLayer, sceneFromResults, segmentAt } from '../scene.js';
import { bboxOf, busy, drawingRows, J, keyOf, P, packagesOf, polygonOf, ppmOf, refreshJob, saveProject, screenName } from '../state.js';
import { badge, beforeUnmount, errorBox, focusHeading, go, guarded, icon, iconEl, infoCard, keepFocus, loadImage, mount, notify, post, pressable, pressSegment, progressBar, projectName, segmented, setDownloads, stageOverlay, useViewer } from '../ui.js';
import { drawPolyWithHoles, hexA, inPoly, labelPoint, scaled } from '../viewer.js';

// --- state kept across renders (and, for the panels and layers, across sessions) ---------------------------------------
const RKEY = 'fpx.results';
const RV = { colour: 'room', layers: { dimensions: true, wallAxes: false, walls: true, wallsRaw: false, doors: true, windows: true, passages: true, columns: true, stairs: true, rooms: true, roomsExact: false, gross: false, labels: true, graph: false, gf: true, overlay: false },
  selected: null, opacity: 1, shownKey: null, left: true, right: true, sort: 'confidence', view: '2d', tab: 'rooms', groups: {} };
const STORED = store.get(RKEY, {});
Object.assign(RV, STORED, { layers: RV.layers, groups: STORED.groups || {}, selected: null, shownKey: null });   // the layers start from the defaults on every visit (a 'show all' is not kept for ever); selected: {kind: room | wall | opening, id} or null
function remember() { store.set(RKEY, { colour: RV.colour, groups: RV.groups, opacity: RV.opacity, left: RV.left, right: RV.right, sort: RV.sort, view: RV.view, tab: RV.tab }); }
const REDUCED = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const GROUPS = [                                           // [i18n key, open at first, layers]: the floor and the rooms first, they are the result
  ['layers.groupFloor', true, [['gf', 'layers.gf']]],
  ['layers.groupRooms', true, [['rooms', 'layers.rooms'], ['roomsExact', 'layers.roomsExact'], ['gross', 'layers.gross'], ['labels', 'layers.roomLabels'], ['graph', 'layers.connections']]],
  ['layers.groupWalls', true, [['wallAxes', 'layers.wallAxes'], ['walls', 'layers.walls'], ['wallsRaw', 'layers.wallsRaw']]],
  ['layers.groupOpenings', true, [['doors', 'layers.doors'], ['windows', 'layers.windows'], ['passages', 'layers.passages']]],
  ['layers.groupParts', true, [['columns', 'layers.columns'], ['stairs', 'layers.stairs']]],
  ['layers.groupMore', false, [['dimensions', 'layers.dimensions'], ['overlay', 'layers.overlay']]],
];
const NARROW = window.matchMedia ? window.matchMedia('(max-width: 760px)') : { matches: false };   // a phone: the layers are a drawer, closed at first
let leftOpen = false;
const leftShown = () => NARROW.matches ? leftOpen : RV.left;
/** The label of a panel's show/hide edge button. */
const panelLabel = (side) => t(`results.${(side === 'left' ? leftShown() : RV.right) ? 'hide' : 'show'}${side === 'left' ? 'Layers' : 'Results'}`);
const LIVE_MS = 1500;                                        // the live record is asked for this often while the extraction runs
const FADE_MS = 700, ANIMATE_MS = 800;                       // a layer that appears fades in
const LABEL_MIN_ZOOM = 12;                                   // room labels from this many screen px per metre

let live = null;                                             // the live poll of the drawing on show: { key, stop, last, work, drawRight }
function stopLive() { if (live && live.stop) live.stop(); live = null; }
let v3 = null;                                               // the 3D view on show (view3d.js), or null
function stop3D() { if (v3) { v3.destroy(); v3 = null; } }
let renderSeq = 0;                                           // the latest render: one that is still loading when another starts stops

/** The step cursor of a drawing row (for the area and scale steps). */
function cursorOf(row) { return { sheet: P.sheets.indexOf(row.id), pkg: packagesOf(row.job).indexOf(row.pkg), drawing: row.pkg.drawings.indexOf(row.dr) }; }
/** Queue the extraction of the row's sheet and show its progress. */
async function startRun(row) {
  if (!await post(sheetUrl(row.id, '/run'), {})) return;
  await refreshJob(row.id);
  P.resultsKey = row.key; saveProject(); renderResults();
}
function scaleText(r) {
  const ppm = ppmOf(r.job, r.pkg, r.dr), N = scaleNumber(r.conf.dpi || r.pkg.summary.dpi, ppm);
  if (!ppm) return t('results.noScale');
  return N ? fmt.scaleN(N).replace(/ /g, '') + (r.conf.scale_source === 'measured' ? ` (${t('results.measured')})` : '') : `${fmt.n(ppm, 0)} px/m`;
}
/** The state of a drawing's extraction as a badge: its stage while it runs, finished with its time, failed; null before the run. */
function runStatus(r) {
  const j = r.job; const key = keyOf(r.pkg, r.dr);
  if (busy(j)) { const p = j.progress; const mine = p && (p.key === key || p.key === r.pkg.id); return badge(mine ? 'run' : 'wait', mine ? stageInfo(p.stage).label : j.status === 'running' ? t('results.waiting') : t('status.' + j.status)); }
  if (r.res && r.res.outputs) return badge('ok', t('results.finishedIn', { time: fmt.secs(r.res.times && r.res.times.total) }));
  if (r.res && (r.res.error || r.res.skipped)) return badge('err', t('results.failed'), r.res.error || r.res.skipped);
  if (j.status === 'error') return badge('err', t('badge.error'));
  return null;
}

function alphaOf(s, layer) { if (s.done || REDUCED || !s.t0[layer]) return 1; return Math.min(1, (performance.now() - s.t0[layer]) / FADE_MS); }
function animate(viewer, ms = ANIMATE_MS) { const t0 = performance.now(); const step = () => { viewer.draw(); if (performance.now() - t0 < ms) requestAnimationFrame(step); }; requestAnimationFrame(step); }
const kindOn = (o) => RV.layers[openingLayer(o)];
const kindColour = (o) => LAYER_COLOURS[openingLayer(o)];
const roomColour = (r) => RV.colour === 'confidence' ? confColour(r.confidence) : r.colour;
const roomName = (r) => r.name || (r.small_region ? t('results.smallRegion') : '');
const same = (a, b) => !!a && !!b && a.kind === b.kind && a.id === b.id;
const extracting = (j) => j.status === 'queued' || j.status === 'running';   // busy() also covers the analysis and the scale step
const isRoom = (sel, id) => !!sel && sel.kind === 'room' && sel.id === id;

// --- the screen -----------------------------------------------------------------------------------------------------
async function renderResults({ changed } = {}) {
  const rows = drawingRows();
  if (!rows.length) return go('upload');
  const row = rows.find((r) => r.key === P.resultsKey) || rows.find((r) => r.res && r.res.outputs) || rows[0];
  // a poll: only this row's job matters; a finished view on show stays (it would lose selection and zoom), and a
  // running one only refreshes its progress panel
  if (changed) {
    if (!changed.includes(row.id)) return;
    if (RV.shownKey === row.key && !busy(row.job)) return;
    if (live && live.key === row.key && busy(row.job)) { live.drawRight(); return; }
  }
  const seq = ++renderSeq;
  const stale = () => seq !== renderSeq || screenName !== 'results';
  P.resultsKey = row.key; saveProject();
  stopLive(); stop3D();
  const done = !busy(row.job) && !!(row.res && row.res.outputs);
  RV.shownKey = done ? row.key : null; RV.selected = null;
  const key = keyOf(row.pkg, row.dr);

  // --- layout: layers | canvas | figures and rooms, both sides collapsible
  const left = h('aside', { class: 'side left', hidden: !leftShown(), 'aria-label': t('results.layers') });
  const stage = h('main', { class: 'stage grid-bg' });
  const right = h('aside', { class: 'side right', hidden: !RV.right, 'aria-label': t('results.results') });
  const wrap = h('div', { class: 'results' + (leftShown() ? '' : ' no-left') + (RV.right ? '' : ' no-right') }, left, stage, right);
  const fresh = mount(wrap, projectName());
  beforeUnmount(() => { stopLive(); stop3D(); });
  const viewer = useViewer(stage, done ? t('results.viewerDone') : t('results.viewerRunning'));
  const toggle = (side) => {
    if (side === 'left' && NARROW.matches) leftOpen = !leftOpen; else { RV[side] = !RV[side]; remember(); }
    const on = side === 'left' ? leftShown() : RV.right;
    (side === 'left' ? left : right).hidden = !on;
    wrap.classList.toggle('no-' + side, !on);
    edges[side].innerHTML = icon(side === 'left' ? (on ? 'chevronLeft' : 'chevronRight') : (on ? 'chevronRight' : 'chevronLeft'));
    edges[side].setAttribute('aria-expanded', String(on));
    edges[side].setAttribute('aria-label', panelLabel(side));
  };
  const edges = {
    left: h('button', { class: 'edge-toggle edge-left', 'aria-expanded': String(leftShown()), 'aria-label': panelLabel('left'), onclick: () => toggle('left') }, iconEl(leftShown() ? 'chevronLeft' : 'chevronRight')),
    right: h('button', { class: 'edge-toggle edge-right', 'aria-expanded': String(RV.right), 'aria-label': panelLabel('right'), onclick: () => toggle('right') }, iconEl(RV.right ? 'chevronRight' : 'chevronLeft')),
  };
  stage.append(edges.left, edges.right);
  const opacity = h('input', { type: 'range', min: 0, max: 1, step: 0.05, value: RV.opacity, class: 'ctl-range', 'aria-label': t('results.planOpacity'), oninput: (e) => { RV.opacity = +e.target.value; viewer.imgAlpha = RV.opacity; pct.textContent = `${Math.round(RV.opacity * 100)}%`; remember(); viewer.draw(); if (v3) v3.setOpacity(RV.opacity); } });
  const pct = h('span', { class: 'pct' }, `${Math.round(RV.opacity * 100)}%`);
  const viewSeg = segmented(t('results.view'), [['2d', '2D'], ['3d', '3D', done ? t('results.title3d') : t('results.after3d')]], done ? RV.view : '2d', (v) => setView(v), (v) => v === '3d' && !done);
  stage.append(h('div', { class: 'stage-bottom' }, viewSeg, h('div', { class: 'vsep' }), h('div', { class: 'row' }, h('span', { class: 'muted' }, t('results.plan')), opacity, pct),
    h('div', { class: 'vsep' }), h('button', { class: 'btn-fit', 'aria-label': t('results.fit'), title: t('results.fitTitle'), onclick: () => { if (v3) v3.fit(); else { viewer.fit(); viewer.draw(); } } }, iconEl('home'))));
  const popup = h('div', { class: 'popup', hidden: true, role: 'dialog', 'aria-label': t('results.room') });
  const leader = h('div', { class: 'callout', hidden: true, 'aria-hidden': 'true', html: '<svg><line class="callout-line"/><circle class="callout-dot" r="3.5"/></svg>' });
  stage.append(leader, popup);
  // the scale bar of the 2D view: a round length that spans 60 to 160 px at the zoom of the moment, as a web map shows it
  const scalebar = h('div', { class: 'scalebar', hidden: true, 'aria-hidden': 'true' }, h('span', { class: 'sb-label' }), h('span', { class: 'sb-bar' }));
  stage.append(scalebar);
  function drawScalebar(v) {
    if (!scene.done || !D || RV.view !== '2d') { scalebar.hidden = true; return; }
    const pxm = D.px_per_m * v.view.s;
    const nice = [0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500].filter((L) => L * pxm <= 160).pop() || 0.5;
    scalebar.lastChild.style.width = `${Math.round(nice * pxm)}px`;
    scalebar.firstChild.textContent = `${fmt.n(nice, nice < 1 ? 1 : 0)} m`;
    scalebar.hidden = false;
  }
  /** The overall dimensions as an architect draws them: a thin line a little outside the building, extension lines to
      the outline, a slash at each end and the length in metres. */
  function drawDims(c, v) {
    for (const d of scene.dims || []) {
      for (const [p, q] of d.ext) v.poly([p, q], { stroke: hexA(TEXT, .5), width: 1, close: false });
      v.poly([d.a, d.b], { stroke: TEXT, width: 1.2, close: false });
      c.strokeStyle = TEXT; c.lineWidth = 1.5;
      for (const p of [d.a, d.b]) { const [sx, sy] = v.toScreen(p[0], p[1]); c.beginPath(); c.moveTo(sx - 4, sy + 4); c.lineTo(sx + 4, sy - 4); c.stroke(); }
      const [sx, sy] = v.toScreen((d.a[0] + d.b[0]) / 2, (d.a[1] + d.b[1]) / 2);
      c.save(); c.translate(sx, sy); if (d.side === 'left') c.rotate(-Math.PI / 2);
      c.font = `600 11px ${CANVAS_FONT}`; c.textAlign = 'center'; c.textBaseline = 'bottom'; c.lineWidth = 3; c.strokeStyle = HALO; c.strokeText(d.text, 0, -5); c.fillStyle = TEXT; c.fillText(d.text, 0, -5);
      c.restore();
    }
  }
  viewer.imgAlpha = RV.opacity;

  // --- data: the results when done, the live record while it runs
  let D = null, scene = emptyScene(), overlayImg = null;
  const f = row.pkg.preview.scale;
  if (done) {
    try { D = await getJSON(keyUrl(row.id, 'results', key)); }
    catch (e) { if (!stale()) right.append(h('div', { class: 'error-box' }, e.message)); return; }
    if (stale()) return;
    if (D.error) { right.append(h('div', { class: 'error-box' }, D.error)); return; }
    scene = sceneFromResults(D);
    const home = scene.gf.length ? bboxOf(scene.gf[0][0]) : scene.walls.length ? bboxOf(scene.walls.flatMap((p) => p[0])) : null;
    loadImage(viewer, fileUrl(row.id, D.files.work), home && { ...home, pad: 60 }, t('wait.plan')).catch((e) => right.prepend(h('div', { class: 'error-box' }, e.message)));
    setDownloads(downloads(row, D));
  } else {
    const pb = bboxOf(scaled(polygonOf(row.job, row.pkg, row.dr), f));              // the sheet, framed on the drawing, until the working image exists
    loadImage(viewer, fileUrl(row.id, row.pkg.preview.file), { ...pb, pad: 40 }).catch(() => {});
    setDownloads(null);
  }
  // the size of the drawing, for the time estimate: its working pixels at 50 px/m
  const pb0 = bboxOf(polygonOf(row.job, row.pkg, row.dr)), ppm0 = ppmOf(row.job, row.pkg, row.dr);
  const workMp = ppm0 ? (pb0.x1 - pb0.x0) * (pb0.y1 - pb0.y0) * (50 / ppm0) ** 2 / 1e6 : null;
  let bar = null, wait = null;                               // the progress bar and the word over the stage while it runs
  const review = D && D.review && D.review.rooms ? D.review.rooms : {};
  const needs = D ? needsReview(D, scene) : [];

  // --- 2D | 3D: the canvas stays as it is; the 3D view (three.js, loaded on first use) sits over it
  async function setView(mode) {
    if (mode === '3d' && !done) return;
    RV.view = mode; remember();
    pressSegment(viewSeg, mode);
    if (mode === '2d') { stop3D(); viewer.canvas.hidden = false; viewer.resize(); drawPopup(); return; }
    scalebar.hidden = true;                                // no scale in a perspective
    let mod;
    try { mod = await import('../view3d.js'); } catch (e) { RV.view = '2d'; remember(); notify(t('results.no3d', { msg: e.message })); return setView('2d'); }
    if (RV.view !== '3d' || !scene.done || stale()) return;
    stop3D(); viewer.canvas.hidden = true;
    v3 = mod.create3D(stage, scene, D.px_per_m, {
      imageUrl: fileUrl(row.id, D.files.work), size: D.work_px || [viewer.img ? viewer.img.width : 1000, viewer.img ? viewer.img.height : 1000],
      colours: { walls: T('--walls-3d', '#d6d9df'), selection: SELECTION, text: TEXT, axes: LAYER_COLOURS.walls, doors: LAYER_COLOURS.doors, windows: LAYER_COLOURS.windows, passages: LAYER_COLOURS.passages, columns: LAYER_COLOURS.columns, stairs: LAYER_COLOURS.stairs, voids: LAYER_COLOURS.voids, gf: LAYER_COLOURS.gf },
      roomColour, layers: RV.layers, opacity: RV.opacity, onSelect: (sel) => select(sel), onRender: () => placePopup(viewer) });
    v3.setLayers(RV.layers); v3.select(RV.selected); stage.fpx3d = v3;   // for the tests: screen coordinates of plan points
  }

  // --- canvas
  viewer.overlay = (c, v) => {
    if (scene.frame === 'sheet') { v.poly(scaled(polygonOf(row.job, row.pkg, row.dr), f), { stroke: ACCENT, width: 1.5, dash: [6, 4] }); return; }
    const layer = (name, on, draw) => { if (!on) return; const a = alphaOf(scene, name); if (a <= 0) return; c.globalAlpha = a; draw(); c.globalAlpha = 1; };
    if (RV.layers.overlay && overlayImg && overlayImg.complete) { const [x, y] = v.toScreen(0, 0); c.globalAlpha = 0.85; c.drawImage(overlayImg, x, y, overlayImg.width * v.view.s, overlayImg.height * v.view.s); c.globalAlpha = 1; }
    layer('walls', RV.layers.walls, () => { for (const poly of scene.walls) drawPolyWithHoles(c, v, poly, hexA(LAYER_COLOURS.walls, .85), null); });
    layer('wallsRaw', RV.layers.wallsRaw, () => { for (const poly of scene.wallsRaw) drawPolyWithHoles(c, v, poly, hexA(LAYER_COLOURS.walls, .25), LAYER_COLOURS.walls, [3, 2], 1); });
    layer('wallAxes', RV.layers.wallAxes, () => {              // the axes of the wall graph with their nodes (the ends, snapped together):
      const lines = scene.segments.filter((sg) => sg.line.length >= 2);   // a dark core in a light halo, so they read on the walls and on paper
      for (const sg of lines) v.poly(sg.line, { stroke: HALO, width: 4, close: false });
      for (const sg of lines) v.poly(sg.line, { stroke: LAYER_COLOURS.walls, width: 1.5, close: false });
      for (const sg of lines) for (const p of [sg.line[0], sg.line[sg.line.length - 1]]) {
        const [sx, sy] = v.toScreen(p[0], p[1]);
        c.beginPath(); c.arc(sx, sy, 3.5, 0, Math.PI * 2); c.fillStyle = SURFACE; c.fill();
        c.beginPath(); c.arc(sx, sy, 2, 0, Math.PI * 2); c.fillStyle = LAYER_COLOURS.walls; c.fill();
      }
    });
    layer('columns', RV.layers.columns, () => { for (const poly of scene.columns) drawPolyWithHoles(c, v, poly, LAYER_COLOURS.columns, null); });
    layer('stairs', RV.layers.stairs, () => { for (const poly of scene.stairs) drawPolyWithHoles(c, v, poly, hexA(LAYER_COLOURS.stairs, .35), LAYER_COLOURS.stairs); for (const o of scene.voids) for (const poly of o.polys) drawPolyWithHoles(c, v, poly, hexA(LAYER_COLOURS.voids, .3), LAYER_COLOURS.voids, [6, 4]); });
    layer('rooms', RV.layers.rooms, () => { for (const r of scene.rooms) { const col = roomColour(r); for (const poly of r.polys) drawPolyWithHoles(c, v, poly, hexA(col, .38), col, null, 1.5); } });
    layer('roomsExact', RV.layers.roomsExact, () => { for (const r of scene.rooms) { const col = roomColour(r); for (const poly of r.polysExact || []) drawPolyWithHoles(c, v, poly, RV.layers.rooms ? null : hexA(col, .2), col, [3, 2], 1); } });
    layer('gross', RV.layers.gross, () => { for (const r of scene.rooms) for (const poly of r.polysGross || []) drawPolyWithHoles(c, v, poly, null, LAYER_COLOURS.gross, [5, 3], 1.5); });
    layer('openings', true, () => { for (const o of scene.openings) { if (!kindOn(o)) continue; const col = kindColour(o); for (const poly of o.polys) drawPolyWithHoles(c, v, poly, hexA(col, .75), col); } });
    layer('gf', RV.layers.gf, () => { for (const poly of scene.gf) drawPolyWithHoles(c, v, poly, null, LAYER_COLOURS.gf, [8, 5], 2); });
    if (RV.layers.graph && scene.done) {
      const byId = Object.fromEntries(scene.rooms.map((r) => [r.id, r])); const op = Object.fromEntries(scene.openings.map((o) => [o.id, o]));
      for (const e of scene.graph) { const a = byId[e.a], b = byId[e.b], o = op[e.opening]; if (!a || !b || !a.centre || !b.centre) continue; const mid = o && o.polys[0] ? labelPoint(o.polys[0][0]) : [(a.centre[0] + b.centre[0]) / 2, (a.centre[1] + b.centre[1]) / 2]; v.poly([a.centre, mid, b.centre], { stroke: LAYER_COLOURS.graph, width: 1.5, close: false }); }
    }
    if (hovered && !same(hovered, RV.selected)) for (const ring of ringsOf(hovered)) v.poly(ring, { stroke: hexA(ACCENT, .7), width: 2 });
    if (RV.selected) for (const ring of ringsOf(RV.selected)) { v.poly(ring, { stroke: HALO, width: 7 }); v.poly(ring, { stroke: SELECTION, width: 2.5 }); }
    layer('dimensions', RV.layers.dimensions && scene.done, () => drawDims(c, v));
    if (RV.layers.labels && scene.done && v.view.s * (D ? D.px_per_m : 1) > LABEL_MIN_ZOOM) for (const r of scene.rooms) {
      if (!r.centre) continue; const [sx, sy] = v.toScreen(r.centre[0], r.centre[1]);
      c.textAlign = 'center'; c.textBaseline = 'middle'; c.lineWidth = 3; c.strokeStyle = HALO;
      c.font = `600 11px ${CANVAS_FONT}`; c.strokeText(roomName(r), sx, sy - 6); c.fillStyle = TEXT; c.fillText(roomName(r), sx, sy - 6);
      if (r.area_net != null) { c.font = `10px ${CANVAS_FONT}`; c.strokeText(fmt.m2(r.area_net), sx, sy + 7); c.fillStyle = MUTED; c.fillText(fmt.m2(r.area_net), sx, sy + 7); }
    }
    placePopup(v);
    drawScalebar(v);
  };
  let hovered = null;
  // --- what sits under a point: an opening first (small), then a wall segment, then a room; hidden layers cannot be picked
  const itemOf = (sel) => !sel ? null : sel.kind === 'room' ? scene.rooms.find((x) => x.id === sel.id) : sel.kind === 'wall' ? scene.segments.find((x) => x.id === sel.id) : scene.openings.find((x) => x.id === sel.id);
  const ringsOf = (sel) => {
    const it = itemOf(sel);
    if (!it) return [];
    if (sel.kind === 'wall') return it.band.length ? [it.band] : [];
    const polys = sel.kind === 'room' && !RV.layers.rooms && RV.layers.roomsExact ? it.polysExact : it.polys;
    return (polys || []).map((poly) => poly[0]);
  };
  const inside = (ip, poly) => inPoly(ip, poly[0]) && !poly.slice(1).some((hole) => inPoly(ip, hole));
  const pick = (ip) => {
    for (let i = scene.openings.length - 1; i >= 0; i--) { const o = scene.openings[i]; if (o.id && kindOn(o) && o.polys.some((poly) => inside(ip, poly))) return { kind: 'opening', id: o.id }; }
    if (RV.layers.walls || RV.layers.wallsRaw || RV.layers.wallAxes) {             // the axis band, or the drawn wall mass near an axis
      const sg = segmentAt(scene, ip, 3) || (scene.walls.some((poly) => inside(ip, poly)) ? segmentAt(scene, ip, 0.5 * scene.ppm) : null);
      if (sg) return { kind: 'wall', id: sg.id };
    }
    if (RV.layers.rooms || RV.layers.roomsExact) for (let i = scene.rooms.length - 1; i >= 0; i--) {
      const r = scene.rooms[i], polys = RV.layers.rooms ? r.polys : r.polysExact;
      if ((polys || []).some((poly) => inside(ip, poly))) return { kind: 'room', id: r.id };
    }
    return null;
  };
  viewer.onClick = (ip) => { if (!scene.done) return; select(pick(ip)); };
  viewer.tool = { hover: (ip, sp, v) => { if (!scene.done) return; const sel = pick(ip); v.canvas.style.cursor = sel ? 'pointer' : 'grab'; if (!same(sel, hovered)) { hovered = sel; v.draw(); } } };
  stage.fpxViewer = viewer;                                 // for the tests: screen coordinates of plan points

  // --- the card pinned to the selected element
  /** The selected element's box on screen (2D: the canvas transform; 3D: the projected outline), or null. */
  function itemPoints(sel) {
    const pts = ringsOf(sel)[0] || [];
    return v3 ? pts.map((q) => v3.projectPoint(q[0], q[1])).filter(Boolean) : pts.map(([x, y]) => viewer.toScreen(x, y));
  }
  function itemBox(sel) { const sp = itemPoints(sel); return sp.length ? bboxOf(sp) : null; }
  /** The point of the outline (screen px) nearest to a point. */
  function nearestOnRing(ring, x, y) {
    let best = null, bd = Infinity;
    for (let i = 0; i < ring.length; i++) {
      const [ax, ay] = ring[i], [bx, by] = ring[(i + 1) % ring.length];
      const dx = bx - ax, dy = by - ay, L2 = dx * dx + dy * dy;
      const u = L2 ? Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / L2)) : 0;
      const qx = ax + u * dx, qy = ay + u * dy, d = Math.hypot(qx - x, qy - y);
      if (d < bd) { bd = d; best = [qx, qy]; }
    }
    return best;
  }
  /** The card is docked in the stage's top-right corner, in 2D and 3D alike, joined to the selected element by a line
      with a dot on its outline; only when the element lies under that corner does it dock top-left instead, so it
      never covers what was selected. On a phone it is a sheet at the bottom of the stage. */
  const PHONE = window.matchMedia ? window.matchMedia('(max-width: 760px)') : { matches: false };
  function placePopup(v) {
    if (popup.hidden) { leader.hidden = true; return; }
    const b = RV.selected && itemBox(RV.selected);
    if (!b) { popup.hidden = true; leader.hidden = true; return; }
    if (PHONE.matches) { popup.classList.add('sheet'); popup.style.left = popup.style.top = ''; leader.hidden = true; return; }
    popup.classList.remove('sheet');
    const W = v.w, H = v.h, w = popup.offsetWidth || 260, hh = popup.offsetHeight || 120, gap = 12, m = 12;
    const ib = { x0: Math.max(0, b.x0), y0: Math.max(0, b.y0), x1: Math.min(W, b.x1), y1: Math.min(H, b.y1) };   // the part on the stage
    const cx = (ib.x0 + ib.x1) / 2, cy = (ib.y0 + ib.y1) / 2;
    const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));
    const overlap = (c) => Math.max(0, Math.min(c.x + w, ib.x1) - Math.max(c.x, ib.x0)) * Math.max(0, Math.min(c.y + hh, ib.y1) - Math.max(c.y, ib.y0));
    const corners = [{ x: W - m - w, y: m }, { x: m, y: m }];
    const best = overlap(corners[0]) === 0 || overlap(corners[1]) >= overlap(corners[0]) ? corners[0] : corners[1];
    popup.style.left = `${Math.round(best.x)}px`; popup.style.top = `${Math.round(best.y)}px`;
    const px = clamp(cx, best.x, best.x + w), py = clamp(cy, best.y, best.y + hh);   // the card's point nearest the element's centre
    const near = nearestOnRing(itemPoints(RV.selected), px, py) || [cx, cy];       // the element's outline point nearest the card
    const [ex, ey] = [clamp(near[0], 0, W), clamp(near[1], 0, H)];
    const apart = Math.hypot(px - ex, py - ey) > gap + 4 && !(px > best.x && px < best.x + w && py > best.y && py < best.y + hh && ex > best.x && ex < best.x + w && ey > best.y && ey < best.y + hh);
    leader.hidden = !apart;
    if (apart) { const [line, dot] = [leader.querySelector('line'), leader.querySelector('circle')]; line.setAttribute('x1', px); line.setAttribute('y1', py); line.setAttribute('x2', ex); line.setAttribute('y2', ey); dot.setAttribute('cx', ex); dot.setAttribute('cy', ey); }
  }
  const roomLabel = (id) => id === 'outside' ? t('card.outside') : !id ? '–' : (roomName(scene.rooms.find((x) => x.id === id) || {}) || id);
  /** The card's content for a room, a wall or an opening: title, a line under it, rows, hints. */
  function cardFor(sel) {
    const it = itemOf(sel);
    if (!it) return null;
    const hint = (text, cls = 'warn', en = false) => ({ text, cls, en });
    if (sel.kind === 'room') {
      const dev = it.area_deviation_pct;
      const hints = (it.reasons || []).map(reasonText).filter(([text]) => !/^(stamp area matches|no contradiction)/.test(text) && text !== t('reason.stampMatches') && text !== t('reason.noContradiction')).map(([text, en]) => hint(text, it.confidence === 'low' ? 'err' : 'warn', en));
      for (const f of it.face_flags || []) hints.push(hint(/^face shared/.test(f) ? t('reason.faceShared') : /^no closed face/.test(f) ? t('reason.noFace') : f, 'warn', !/^(face shared|no closed face)/.test(f)));
      return { title: roomName(it) || t('results.noStamp'), subtitle: it.number ? `${t('results.number')} ${it.number}` : null,
        rows: [[t('card.netExact'), fmt.m2(it.area_net)], it.area_net_simple != null ? [t('card.netSimple'), fmt.m2(it.area_net_simple)] : null,
          it.area_gross != null ? [t('card.gross'), fmt.m2(it.area_gross)] : null,
          [t('results.stamp'), it.area_stamp != null ? [fmt.m2(it.area_stamp), ' ', h('span', { class: Math.abs(dev || 0) > 5 ? 'danger-text' : 'muted' }, dev != null ? `(${fmt.pct(dev)})` : '')] : t('results.noneRead')],
          [t('results.confidence'), tv('conf', it.confidence), 'conf-' + it.confidence + ' strong']], hints };
    }
    if (sel.kind === 'wall') {
      const hints = [];
      if (it.thickness_measured != null) hints.push(hint(t('hint.wallRun', { t: fmt.n(it.thickness_measured, 2) }), 'info'));
      if ((it.notes || []).includes('extended')) hints.push(hint(t('hint.wallExtended'), 'info'));
      if ((it.notes || []).includes('open end')) hints.push(hint(t('hint.wallOpenEnd')));
      if (!(it.rooms || []).some((r) => r && r !== 'outside')) hints.push(hint(t('hint.wallNoRoom')));
      if (it.thickness != null && it.thickness < 0.08) hints.push(hint(t('hint.wallThin')));
      if (it.thickness != null && it.thickness > 0.8) hints.push(hint(t('hint.wallThick')));
      const [a, b] = it.rooms || [null, null];
      return { title: `${t('card.wall')} ${it.id}`, subtitle: tv('wallKind', it.kind || 'unknown'),
        rows: [[t('card.thickness'), it.thickness != null ? `${fmt.n(it.thickness, 2)} m` : '–'], [t('card.length'), it.length != null ? `${fmt.n(it.length)} m` : '–'],
          it.construction ? [t('card.construction'), tv('construction', it.construction)] : null,
          [t('card.rooms'), `${roomLabel(a)} · ${roomLabel(b)}`], [t('results.openings'), String((it.openings || []).length)]], hints };
    }
    const hints = [];
    if (it.flag) hints.push(hint(it.flag, 'info', true));
    if (it.kind !== 'window' && it.width != null && it.width < 0.6) hints.push(hint(t('hint.openingNarrow')));
    if (it.width != null && it.width > 2.5) hints.push(hint(t('hint.openingWide')));
    if (!it.host) hints.push(hint(t('hint.openingNoHost')));
    const [a, b] = it.connects || [null, null];
    if (it.kind !== 'window' && a && b && a === b) hints.push(hint(t('hint.openingSameRoom')));
    if (it.kind !== 'window' && (!a || !b)) hints.push(hint(t('hint.openingUnknownSide')));
    return { title: `${tv('kind', it.kind)} ${it.id}`, subtitle: it.exterior ? t('card.exteriorOpening') : t('card.interiorOpening'),
      rows: [[t('card.width'), it.width != null ? `${fmt.n(it.width, 2)} m` : '–'], [t('card.hostWall'), it.host || '–'],
        it.kind !== 'window' ? [t('card.connects'), `${roomLabel(a)} ↔ ${roomLabel(b)}`] : null], hints };
  }
  function drawPopup() {
    const card = RV.selected && cardFor(RV.selected);
    if (!card) { popup.hidden = true; return; }
    infoCard(popup, { ...card, onClose: () => select(null) });
    popup.hidden = false;
    placePopup(viewer);
  }
  function select(sel, { fromList = false } = {}) {
    RV.selected = sel && itemOf(sel) ? sel : null; drawPopup(); drawRooms(); viewer.draw(); if (v3) v3.select(RV.selected);
    if (RV.selected && RV.selected.kind === 'room' && !fromList) { const el = right.querySelector(`[data-id="${RV.selected.id}"]`); if (el) el.scrollIntoView({ block: 'nearest' }); }
    if (RV.selected && fromList && PHONE.matches) stage.scrollIntoView({ behavior: REDUCED ? 'auto' : 'smooth', block: 'start' });   // the plan is above the list on a phone
  }
  async function setChecked(r, checked) {
    if (!await post(sheetUrl(row.id, '/review'), { key, rooms: { [r.id]: { checked } } })) return drawRooms();   // the box goes back
    review[r.id] = Object.assign(review[r.id] || {}, { checked });
    drawFigures(); drawRooms(); drawPopup();
  }
  document.onkeydown = (e) => { if (e.key === 'Escape' && screenName === 'results' && RV.selected) select(null); };

  // --- left: layers
  const byLayer = (name) => scene.openings.filter((o) => openingLayer(o) === name).length;
  const counts = () => ({ wallAxes: scene.segments.length, walls: scene.segments.length || scene.walls.length, wallsRaw: null, doors: byLayer('doors'), windows: byLayer('windows'), passages: byLayer('passages'),
    columns: scene.columns.length, stairs: scene.stairs.length + scene.voids.length, rooms: scene.rooms.length, roomsExact: null,
    gross: scene.rooms.filter((r) => r.polysGross && r.polysGross.length).length, labels: null, graph: scene.graph.length, gf: null, dimensions: null, overlay: null });
  function drawLeft() { keepFocus(drawLeftNow); }
  function drawLeftNow() {
    const n = counts();
    const all = (on) => { for (const k of Object.keys(RV.layers)) if (k !== 'overlay') RV.layers[k] = on; remember(); drawLeft(); viewer.draw(); if (v3) v3.setLayers(RV.layers); };
    const colourBy = (value) => { RV.colour = value; remember(); drawLeft(); drawRooms(); viewer.draw(); if (v3) v3.setRoomColours(roomColour); };
    const conf = { high: 0, medium: 0, low: 0 }; for (const r of scene.rooms) conf[r.confidence] = (conf[r.confidence] || 0) + 1;
    const legend = (level, cls, title) => h('div', null, h('span', { class: 'swatch ' + cls }), h('span', { title, class: 'help' }, tv('conf', level)), h('span', { class: 'n' }, conf[level]));
    put(left,
      h('div', { class: 'side-head' }, h('h2', null, t('results.layers')), h('span', { class: 'row' }, h('button', { class: 'btn-link small', onclick: () => all(true) }, t('results.showAll')), h('button', { class: 'btn-link small', onclick: () => all(false) }, t('results.hideAll')))),
      h('div', { class: 'stack' }, h('div', { class: 'section-title' }, t('results.colourBy')),
        segmented(t('results.colourBy'), [['room', t('results.room')], ['confidence', t('results.confidence')]], RV.colour, colourBy),
        RV.colour === 'confidence' ? h('div', { class: 'legend-rows' }, legend('high', 'c-success', t('results.confHighTitle')), legend('medium', 'c-warning', t('results.confMediumTitle')), legend('low', 'c-danger', t('results.confLowTitle'))) : null),
      GROUPS.map(([name, open, items]) => h('details', { class: 'layer-group', 'data-group': name, open: RV.groups[name] == null ? open : RV.groups[name],
        ontoggle: (e) => { if (RV.groups[name] !== e.target.open) { RV.groups[name] = e.target.open; remember(); } } },
        h('summary', null, h('span', { class: 'name' }, t(name)), groupEye(items)), h('div', { class: 'layers' }, items.map(([k, label]) => layerRow(k, t(label), n[k]))))));
  }
  /** The eye of a group row: on while any of its layers is on; a click hides them all, or shows them all when none
      is on, and leaves the group open or closed as it was. */
  function groupEye(items) {
    const on = items.some(([k]) => RV.layers[k]);
    return h('button', { type: 'button', class: 'eye' + (on ? '' : ' off'), role: 'switch', 'aria-checked': String(on), 'aria-label': on ? t('results.hideGroup') : t('results.showGroup'),
      html: icon(on ? 'eye' : 'eyeOff'), onclick: (e) => { e.preventDefault(); e.stopPropagation(); setLayers(Object.fromEntries(items.map(([k]) => [k, !on]))); } });
  }
  function setLayers(changes) {
    Object.assign(RV.layers, changes); remember();
    if (changes.overlay && !overlayImg && D) { overlayImg = new Image(); overlayImg.onload = () => viewer.draw(); overlayImg.src = fileUrl(row.id, D.files.overlay); }
    drawLeft(); viewer.draw(); if (v3) v3.setLayers(RV.layers);
  }
  function layerRow(k, name, n) {
    const colour = LAYER_COLOURS[k] || (k === 'overlay' ? NEUTRAL : null);
    const cls = k === 'rooms' ? (RV.colour === 'confidence' ? 'sw-conf' : 'sw-rooms') : k === 'roomsExact' ? 'sw-rooms sw-exact' : k === 'wallsRaw' ? 'sw-raw' : k === 'wallAxes' ? 'sw-axis'
      : k === 'labels' ? 'sw-outline' : k === 'gf' ? 'sw-dashed' : k === 'gross' ? 'sw-gross' : k === 'graph' ? 'sw-line' : k === 'dimensions' ? 'sw-dim' : '';
    const on = !!RV.layers[k];
    return h('button', { type: 'button', class: 'layer-row' + (on ? '' : ' off'), role: 'switch', 'aria-checked': String(on), 'data-layer': k, onclick: () => setLayers({ [k]: !on }) },
      h('span', { class: 'swatch ' + cls, style: colour && !cls ? `background:${colour}` : '' }), h('span', { class: 'name' }, n ? `${name} (${n})` : name),
      h('span', { class: 'eye', 'aria-hidden': 'true', html: icon(on ? 'eye' : 'eyeOff') }));
  }

  // --- right: the progress while it runs, the figures and rooms when done
  const figures = h('div'), roomList = h('div', { class: 'room-table' }), issues = h('div', { class: 'issues-list' });
  const tabPanel = h('div', { role: 'tabpanel' });
  const tabBtn = (name, label) => h('button', { role: 'tab', 'aria-selected': String(RV.tab === name), onclick: () => { RV.tab = name; remember(); drawTabs(); } }, label);
  const tabs = h('div', { class: 'tabs', role: 'tablist', 'aria-label': t('results.tabsLabel') });
  const showRow = (r) => { P.resultsKey = r.key; go('results'); };
  function drawTabs() {
    keepFocus(() => {
      put(tabs, tabBtn('rooms', `${t('results.rooms')} · ${scene.rooms.length}`), tabBtn('issues', `${t('results.issues')} · ${D ? needs.length + D.qa.length : 0}`));
      put(tabPanel, RV.tab === 'issues' ? issues : roomList);
    });
  }
  let barKind = null;
  function drawRight() {
    const j = J[row.id] || row.job, running = busy(j);           // the fresh job record: the poll replaces J[id]
    const kind = extracting(j) ? 'extraction' : 'analysis';     // a sheet being analysed or scaled again is not extracting yet
    if (!done) {
      const failed = !running && (j.status === 'error' || !!(row.res && (row.res.error || row.res.skipped)));
      if (running && (!bar || barKind !== kind)) {       // once per kind of work: the bar keeps its own clock between polls
        if (bar) bar.stop();
        barKind = kind;
        bar = progressBar(j, kind, { ahead: !!(j.progress && j.progress.ahead), workMp }, (e) => { if (wait) wait.set(e.label, etaText(e)); });
        if (!wait) wait = stageOverlay(stage, '', '');
        bar.update();
      }
      const rowsNow = drawingRows();                     // the records move with every poll; the closure's rows do not
      keepFocus(() => put(right,
        h('div', { class: 'side-head' }, h('h2', null, running ? (kind === 'extraction' ? t('results.extracting') : t('results.preparing')) : failed ? t('results.extractionFailed') : t('results.readyToExtract')), running ? badge('run', t('results.running')) : failed ? badge('err', t('results.failed')) : null),
        running ? bar : null,
        h('p', { class: 'muted small pretty' }, running ? (kind === 'extraction' ? t('results.extractingText') : t('results.preparingText'))
          : failed ? t('results.failedText') : t('results.readyText')),
        rowsNow.length > 1 ? h('div', { class: 'run-list' }, rowsNow.map((r) => h('div', pressable(() => showRow(r), { class: 'run-row' + (r.key === row.key ? ' on' : ''), 'aria-current': r.key === row.key ? 'true' : null }),
          h('div', { class: 'f' }, h('div', { class: 'n' }, r.job.name + (r.dr.title ? ` · ${r.dr.title}` : '')), h('div', { class: 'm' }, `${scaleText(r)} · ${r.conf.polygon_px ? t('results.areaByHand') : t('results.areaConfirmed')}`, runStatus(r)))))) : null,
        !running && j.status === 'error' && j.error ? errorBox(j) : null,
        !running && row.res && (row.res.error || row.res.skipped) ? h('div', { class: 'error-box', lang: 'en' }, row.res.error || row.res.skipped) : null,
        h('div', { class: 'foot' }, h('button', { class: 'btn', onclick: () => go('scale', cursorOf(row)) }, t('results.backToScale')),
          !running ? h('button', { class: 'btn btn-primary', onclick: guarded(() => startRun(row)) }, row.res ? t('results.runAgain') : t('results.run')) : null)));
      return;
    }
    const pick = rows.length > 1 ? h('select', { class: 'select', 'aria-label': t('results.drawing'), onchange: (e) => showRow({ key: e.target.value }) },
      rows.map((r, i) => h('option', { value: r.key, selected: r.key === row.key }, (r.label || r.dr.title || t('results.drawingN', { n: i + 1 })) + ' · ' + r.job.name))) : null;
    put(right,
      h('div', { class: 'side-head' }, h('h2', { class: 'clip' }, row.label || row.dr.title || row.job.name), badge('ok', t('results.finished'))),
      h('div', { class: 'row row-md small' }, h('button', { class: 'btn-link', title: t('results.backTitle'), onclick: () => go('scale', cursorOf(row)) }, t('results.backToSteps')),
        h('button', { class: 'btn-link', title: t('results.runAgainTitle'), onclick: guarded(() => startRun(row)) }, t('results.runAgain'))),
      pick, figures, tabs, tabPanel);
    drawFigures(); drawRooms(); drawIssues(); drawTabs();
  }
  function drawFigures() {
    if (!D) return;
    const checked = scene.rooms.filter((r) => review[r.id] && review[r.id].checked).length;
    const gf = D.floor.gf_area, ngf = D.floor.sum_net, kf = gf && ngf && gf > ngf ? gf - ngf : null;   // SIA 416: GF = NGF + KF
    put(figures, h('div', { class: 'kv-grid figures', title: t('results.areaNote') },
      h('span', null, t('results.gf')), h('span', { class: 'strong' }, fmt.m2(gf)),
      h('span', null, t('results.ngf')), h('span', { class: 'strong' }, fmt.m2(ngf)),
      D.floor.sum_gross ? [h('span', null, t('results.gross')), h('span', null, fmt.m2(D.floor.sum_gross))] : null,
      kf != null ? [h('span', null, t('results.kf')), h('span', null, fmt.m2(kf))] : null,
      h('span', null, t('results.rooms')), h('span', null, `${scene.rooms.length}${checked ? ' · ' + t('results.nChecked', { n: checked }) : ''}`),
      h('span', null, t('results.openings')), h('span', null, scene.openings.length),
      h('span', null, t('results.needsReview')), h('span', { class: needs.length ? 'danger-text strong' : '' }, needs.length),
      h('span', null, t('results.scale')), h('span', null, scaleText(row))));
  }
  function drawRooms() {
    if (!D) return;
    const order = { low: 0, medium: 1, high: 2 };
    const sorted = [...scene.rooms].sort((a, b) => RV.sort === 'area' ? (b.area_net || 0) - (a.area_net || 0) : (order[a.confidence] ?? 3) - (order[b.confidence] ?? 3) || (b.area_net || 0) - (a.area_net || 0));
    const sortBtn = (by, label, title) => h('button', { class: 'btn-link text' + (RV.sort === by ? ' strong' : ''), 'aria-pressed': String(RV.sort === by), title, onclick: () => { RV.sort = by; remember(); drawRooms(); } }, label, RV.sort === by ? ' ↑' : '');
    keepFocus(() => put(roomList, h('div', { class: 'room-row head' }, h('span', { title: t('results.checkedTitle'), class: 'ico', html: icon('check') }), h('span'), h('span', null, t('results.room')), h('span', { class: 'ar' }, sortBtn('area', t('results.area'), t('results.sortByArea'))), h('span', { class: 'cf' }, sortBtn('confidence', t('results.confShort'), t('results.sortByConfidence')))),
      sorted.map((r) => h('div', pressable(() => select({ kind: 'room', id: r.id }, { fromList: true }), { class: 'room-row' + (isRoom(RV.selected, r.id) ? ' on' : ''), 'data-id': r.id, 'aria-pressed': String(isRoom(RV.selected, r.id)) }),
        h('input', { type: 'checkbox', 'aria-label': t('results.roomChecked', { name: roomName(r) || t('results.room') }), checked: !!(review[r.id] && review[r.id].checked), onclick: (e) => e.stopPropagation(), onchange: (e) => setChecked(r, e.target.checked) }),
        h('span', { class: 'swatch', style: `background:${roomColour(r)}` }), h('span', { class: 'nm' }, roomName(r) || t('results.noStamp')),
        h('span', { class: 'ar' }, fmt.n(r.area_net)), h('span', { class: 'cf conf-' + r.confidence }, tv('conf', r.confidence))))));
  }
  function drawIssues() {
    if (!D) return;
    put(issues,
      needs.length ? h('div', { class: 'section-title' }, t('results.needsReview')) : null,
      needs.length ? h('div', { class: 'issue-table' },                           // a table like the rooms: what, where, why
        h('div', { class: 'issue-row head' }, h('span'), h('span', null, t('results.room')), h('span', null, t('results.issue'))),
        needs.map((n) => h('div', n.room ? pressable(() => select({ kind: 'room', id: n.room }), { class: 'issue-row' }) : { class: 'issue-row' },
          h('span', { class: 'dot10 ' + n.cls }), h('span', { class: 'nm', title: n.id }, n.title), h('span', { class: 'd', lang: n.en ? 'en' : null }, n.text)))) : h('p', { class: 'muted small' }, t('results.nothingToReview')),
      h('details', { class: 'mt-2' }, h('summary', null, t('results.showChecks', { n: D.qa.length })),   // the pipeline's own list, for those who want it
        h('div', { class: 'qa-list' }, D.qa.map((q) => h('div', { class: 'qa-row' }, h('span', { class: 'sev sev-' + q.severity }, tv('severity', q.severity)), h('span', { lang: 'en' }, h('b', null, q.check), q.element ? ` · ${q.element}` : '', ': ', q.message))))));
  }

  drawLeft(); drawRight();                               // the panels are filled after the mount
  if (fresh) focusHeading(right);
  if (done && RV.view === '3d') setView('3d');
  beforeUnmount(() => { if (bar) bar.stop(); });
  if (!done) {
    live = { key: row.key, stop: null, last: null, work: null, drawRight };
    const tick = async () => {
      const j = J[row.id] || row.job;
      if (!extracting(j)) return;                        // nothing runs: no live record to wait for
      const L = await getJSON(keyUrl(row.id, 'live', key));
      if (!live || live.key !== row.key || !L || !L.stage || L.updated === live.last) return;
      if (j.progress && j.progress.since && L.updated < j.progress.since) return;   // the previous run's record, not yet replaced
      live.last = L.updated;
      if (L.work && live.work !== L.work) { live.work = L.work; viewer.load(fileUrl(row.id, L.work), { x0: 0, y0: 0, x1: L.size[0], y1: L.size[1], pad: 40 }).catch(() => {}); }
      applyLive(scene, L, performance.now()); drawLeft(); animate(viewer);
      if (wait && (scene.walls.length || scene.rooms.length)) { wait.remove(); wait = null; }   // the plan itself shows the progress now
    };
    live.stop = every(LIVE_MS, tick, true);
  }
}
/** A room's review reason from the pipeline (qa.py, a handful of English patterns) in the page's language; anything
    else as it is. Returns [text, isEnglish]. */
function reasonText(s) {
  const r = String(s || '');
  let m;
  if ((m = r.match(/^(\d+) stamps: a wall may be missing/))) return [t('reason.stamps', { n: m[1] }), false];
  if (/^no stamp/.test(r)) return [t('reason.noStamp'), false];
  if (/^very small/.test(r)) return [t('reason.verySmall'), false];
  if (/^stair: no void/.test(r)) return [t('reason.stairNoVoid'), false];
  if ((m = r.match(/^(?:net|polygon) area ([\d.]+) m² vs stamp ([\d.]+) \(([+-]?\d+)%\)/))) return [t('reason.stampDeviation', { area: fmt.n(+m[1]), stamp: fmt.n(+m[2]), dev: fmt.pct(+m[3]) }), false];
  if ((m = r.match(/^void ([\d.]+) m² \((.*)\) excluded/))) return [t('reason.voidExcluded', { area: fmt.m2(+m[1]), label: m[2] }), false];
  if (/^small unlabelled region/.test(r)) return [t('reason.smallRegion'), false];
  if (/^stamp area matches/.test(r)) return [t('reason.stampMatches'), false];
  if (/^no contradiction/.test(r)) return [t('reason.noContradiction'), false];
  return [r, true];
}
/** The rooms, voids and checks that ask for a look: low confidence or a review note, voids deducted from GF, high QA
    findings. `en`: the text comes from the pipeline (English). */
function needsReview(D, scene) {
  const out = [];
  for (const r of scene.rooms) if (r.confidence === 'low' || r.review) {
    const [text, en] = r.review ? [t('reason.smallRegion'), false] : (r.reasons || []).length ? reasonText(r.reasons[0]) : [t('results.lowConfidence'), false];
    out.push({ cls: r.confidence === 'low' ? 'c-danger' : 'c-warning', title: roomName(r) || t('results.noStamp'), id: r.id, room: r.id, text, en });
  }
  for (const v of scene.voids) if (v.gf_deducted) out.push({ cls: 'c-info', title: v.label || t('results.void'), id: v.id || 'void', text: t('results.voidDeducted', { area: fmt.m2(v.area) }) });
  for (const q of D.qa) if (q.severity === 'high' && !/^r\d/.test(q.element || '')) out.push({ cls: 'c-danger', title: q.check, id: q.element, text: reasonText(q.message)[0], en: reasonText(q.message)[1] });
  return out;
}
/** The export files of a finished drawing for the Download menu: Excel and IFC only when the pipeline wrote them
    (it skips them without openpyxl or ifcopenshell, and says so under exports); the PDF report is announced (soon),
    not yet written. */
function downloads(row, D) {
  const file = (name) => fileUrl(row.id, name, true);
  const stem = D.files.json.replace(/\.json$/, '');
  const written = (ext) => !D.exports || !(ext in D.exports) || !!D.exports[ext];
  return [
    { label: 'JSON', note: t('dl.json'), href: file(D.files.json), name: D.files.json },
    { label: 'Excel', note: t('dl.xlsx'), href: file(`${stem}.xlsx`), name: `${stem}.xlsx` },
    { label: 'DXF', note: t('dl.dxf'), href: file(D.files.dxf), name: D.files.dxf },
    { label: 'IFC', note: t('dl.ifc'), href: file(`${stem}.ifc`), name: `${stem}.ifc` },
    { label: 'CSV', note: t('dl.csv'), href: keyUrl(row.id, 'rooms.csv', keyOf(row.pkg, row.dr)), name: `${stem}-rooms.csv` },
    { label: 'PDF', note: t('dl.pdf'), name: `${stem}.pdf`, soon: true },
  ].filter((it) => written(it.name.split('.').pop()));
}

export { renderResults };
