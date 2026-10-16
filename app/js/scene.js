/* The results of a drawing as a scene for the canvas (screens/results.js) and the 3D view (view3d.js): everything in
   working px of the working image. Rooms carry three polygons: the simplified net one (the wall graph's face minus the
   wall bands, drawn by default), the accurate one traced from the pixels, and the gross one to the wall axes. Walls
   come as the regularised bands (drawn by default), the pixel outline, and the segments of the wall graph (centre
   line, thickness, the rooms on each side) that the viewer picks. No page access: plain data in, plain data out. */
import { PAL, fmt } from './core.js';
import { labelPoint } from './viewer.js';

function emptyScene() { return { frame: 'sheet', walls: [], wallsRaw: [], segments: [], openings: [], stairs: [], voids: [], columns: [], rooms: [], gf: [], dims: [], graph: [], ppm: 1, t0: {} }; }
const ringsOf = (g) => !g ? [] : g.type === 'Polygon' ? [g.coordinates] : g.type === 'MultiPolygon' ? g.coordinates : [];
/** The layer of an opening: windows, doors or passages (any other kind is drawn and counted as a passage). */
const openingLayer = (o) => o.kind === 'window' ? 'windows' : (o.kind || '').includes('door') ? 'doors' : 'passages';

/** The band of a wall segment: its centre line (working px) widened by half its thickness, as one ring. */
function bandOf(line, halfPx) {
  const [a, b] = [line[0], line[line.length - 1]];
  const dx = b[0] - a[0], dy = b[1] - a[1], L = Math.hypot(dx, dy) || 1;
  const nx = -dy / L * halfPx, ny = dx / L * halfPx;
  return [[a[0] + nx, a[1] + ny], [b[0] + nx, b[1] + ny], [b[0] - nx, b[1] - ny], [a[0] - nx, a[1] - ny]];
}

/** The distance from a point to a polyline (both working px). */
function distToLine(p, line) {
  let best = Infinity;
  for (let i = 1; i < line.length; i++) {
    const [ax, ay] = line[i - 1], [bx, by] = line[i];
    const dx = bx - ax, dy = by - ay, L2 = dx * dx + dy * dy;
    const t = L2 ? Math.max(0, Math.min(1, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2)) : 0;
    best = Math.min(best, Math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy));
  }
  return best;
}
/** The wall segment under a point (working px): the axis whose band, widened by `margin` px, covers the point and
    is nearest to it; null when none does. The 2D view passes a few px, the 3D view half a metre, since a hit on the
    wall mass lands on the drawn face, which is thicker than the fitted axis band here and there. */
function segmentAt(scene, p, margin = 0) {
  let best = null, bestD = Infinity;
  for (const sg of scene.segments) {
    if (sg.line.length < 2) continue;
    const d = distToLine(p, sg.line) - sg.half;
    if (d <= margin && d < bestD) { best = sg; bestD = d; }
  }
  return best;
}

/** The overall dimensions of the floor outline: one chain along the bottom and one along the left of its extent, a
    little outside the building, with the length in metres (working px; the extension lines reach the outline). */
function dimensionsOf(gf, ppm) {
  const pts = gf.flatMap((poly) => poly[0]);
  if (pts.length < 3) return [];
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [x, y] of pts) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
  const off = 1.2 * ppm, over = 0.3 * ppm, text = (px) => `${fmt.n(px / ppm, 2)} m`;   // next to the drawing's own chain, so the two can be compared (the scale check)
  return [
    { side: 'bottom', a: [x0, y1 + off], b: [x1, y1 + off], ext: [[[x0, y1], [x0, y1 + off + over]], [[x1, y1], [x1, y1 + off + over]]], text: text(x1 - x0) },
    { side: 'left', a: [x0 - off, y0], b: [x0 - off, y1], ext: [[[x0, y0], [x0 - off - over, y0]], [[x0, y1], [x0 - off - over, y1]]], text: text(y1 - y0) },
  ];
}

/** The final results as a scene: plan metres -> working px of the working image. */
function sceneFromResults(D) {
  const ppm = D.px_per_m, ex = D.extent;
  const W = (X, Y) => [(X - ex[0]) * ppm, (ex[3] - Y) * ppm];
  const polys = (g) => ringsOf(g).map((poly) => poly.map((ring) => ring.map(([X, Y]) => W(X, Y))));
  const s = emptyScene(); s.frame = 'work'; s.done = true; s.ppm = ppm;
  s.walls = D.walls.flatMap((w) => polys(w));
  s.wallsRaw = (D.walls_raw || []).flatMap((w) => polys(w));
  s.columns = D.columns.flatMap((o) => polys(o));
  s.stairs = D.stairs.flatMap((o) => polys(o));
  s.voids = D.voids.map((v) => ({ ...v, polys: polys(v.geometry) }));
  s.openings = D.openings.map((o) => ({ ...o, polys: polys(o.geometry) }));
  s.rooms = D.rooms.map((r, i) => {
    const exact = polys(r.geometry), simple = r.geometry_simple ? polys(r.geometry_simple) : null, gross = r.geometry_gross ? polys(r.geometry_gross) : null;
    const p = simple || exact;
    return { ...r, i, polys: p, polysExact: exact, polysGross: gross, colour: PAL[i % PAL.length], centre: labelPoint(p[0] ? p[0][0] : []) };
  });
  s.segments = (D.wall_segments || []).map((w) => {
    const line = (w.centre_line && w.centre_line.coordinates || []).map(([X, Y]) => W(X, Y));
    const half = (w.thickness || 0) * ppm / 2;
    return { ...w, line, half, band: line.length >= 2 ? bandOf(line, Math.max(half, 2)) : [] };
  });
  s.gf = D.floor.gf ? polys(D.floor.gf) : [];
  s.dims = dimensionsOf(s.gf, ppm);
  s.graph = D.connectivity;
  return s;
}
/** A live record (bridge._live) into the scene; layers that just appeared get their fade-in start (`now`, ms). */
function applyLive(s, L, now) {
  const one = (rings) => rings.map((r) => [r]);
  const set = (layer, value, has) => { if (has && !s.t0[layer]) s.t0[layer] = now; s[layer] = value; };
  s.frame = 'work';
  set('walls', one(L.walls || []), (L.walls || []).length);
  set('openings', (L.openings || []).map((o) => ({ kind: o.kind, polys: one(o.rings || []) })), (L.openings || []).length);
  set('stairs', one(L.stairs || []), (L.stairs || []).length);
  set('rooms', (L.rooms || []).map((r, i) => ({ i, name: r.name, polys: one(r.rings || []), colour: PAL[i % PAL.length], confidence: 'medium', centre: labelPoint(r.rings && r.rings[0] ? r.rings[0] : []) })), (L.rooms || []).length);
  set('gf', one(L.gf || []), (L.gf || []).length);
}

export { applyLive, bandOf, emptyScene, openingLayer, sceneFromResults, segmentAt };
