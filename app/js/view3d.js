/* The 3D view of the results (three.js, vendored under js/vendor/three): the working image as the ground, walls,
   columns, doors, windows and stairs as prisms, rooms as flat slabs, the GF outline at wall height. Loaded on demand
   when the user switches to 3D. Heights are nominal (display only), as in the pilot viewer. */
import * as THREE from 'three';
import { OrbitControls } from './vendor/three/OrbitControls.js';
import { t } from './i18n.js';
import { openingLayer, segmentAt } from './scene.js';

const H = { wall: 2.8, door: 2.1, window: [0.9, 2.1], column: 2.8, stair: 0.45 };
const AZ = 20, EL = 37;                                      // the pilot viewer's opening angle (degrees)

const hex = (c) => new THREE.Color(c);

/** Polygon rings in working px -> a THREE.Shape in metres (x right, y = image y; the mesh is rotated flat afterwards). */
function shapeOf(poly, ppm) {
  const pt = ([x, y]) => new THREE.Vector2(x / ppm, -y / ppm);
  const shape = new THREE.Shape(poly[0].map(pt));
  for (const hole of poly.slice(1)) shape.holes.push(new THREE.Path(hole.map(pt)));
  return shape;
}
function prism(polys, ppm, depth, colour, { y = 0, opacity = 1 } = {}) {
  const shapes = polys.map((p) => shapeOf(p, ppm)).filter((s) => s.curves.length >= 3);
  if (!shapes.length) return null;
  const geo = new THREE.ExtrudeGeometry(shapes, { depth, bevelEnabled: false });
  const mat = new THREE.MeshLambertMaterial({ color: hex(colour), transparent: opacity < 1, opacity });
  const m = new THREE.Mesh(geo, mat);
  m.rotation.x = -Math.PI / 2; m.position.y = y;
  return m;
}
function slab(polys, ppm, colour, { y = 0.02, opacity = 0.55 } = {}) {
  const shapes = polys.map((p) => shapeOf(p, ppm)).filter((s) => s.curves.length >= 3);
  if (!shapes.length) return null;
  const geo = new THREE.ShapeGeometry(shapes);
  const mat = new THREE.MeshBasicMaterial({ color: hex(colour), transparent: true, opacity, side: THREE.DoubleSide, depthWrite: false });
  const m = new THREE.Mesh(geo, mat);
  m.rotation.x = -Math.PI / 2; m.position.y = y; m.renderOrder = 1;
  return m;
}
/** A label in space: the text on a canvas, as a sprite 0.6 m tall that always faces the camera. */
function textSprite(text, colour, pos) {
  const cv = document.createElement('canvas'), ctx = cv.getContext('2d'), font = "600 44px 'IBM Plex Sans', system-ui, sans-serif";
  ctx.font = font;
  const w = Math.ceil(ctx.measureText(text).width) + 28;
  cv.width = w; cv.height = 64;
  ctx.font = font; ctx.fillStyle = 'rgba(255,255,255,.92)'; ctx.beginPath(); ctx.roundRect(0, 0, w, 64, 12); ctx.fill();
  ctx.fillStyle = colour; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(text, w / 2, 34);
  const tex = new THREE.CanvasTexture(cv); tex.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false }));
  s.userData.aspect = w / 64; s.scale.set(0.6 * w / 64, 0.6, 1); s.position.set(pos[0], pos[1], pos[2]); s.renderOrder = 3;
  return s;
}
function loop(ring, ppm, y, colour, dashed, close = true) {
  const pts = ring.map(([x, yy]) => new THREE.Vector3(x / ppm, y, yy / ppm));
  if (close) pts.push(pts[0].clone());
  const geo = new THREE.BufferGeometry().setFromPoints(pts);
  const mat = dashed ? new THREE.LineDashedMaterial({ color: hex(colour), dashSize: 0.3, gapSize: 0.2 }) : new THREE.LineBasicMaterial({ color: hex(colour) });
  const line = new THREE.Line(geo, mat);
  if (dashed) line.computeLineDistances();
  return line;
}

/**
 * Build the 3D view inside `stage`. scene: the results scene of screens/results.js (working px); ppm: working px per
 * metre; opts: { imageUrl, size: [w, h], colours: {walls, doors, windows, passages, columns, stairs, voids, gf},
 * roomColour(room) -> '#hex', layers, opacity, onSelect(id|null), onRender() }.
 * Returns { setLayers, setRoomColours, setOpacity, select, fit, projectPoint(x, y) -> [sx, sy] | null, resize, destroy }.
 */
function create3D(stage, scene, ppm, opts) {
  const el = document.createElement('div');
  el.className = 'view3d';
  stage.append(el);
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0x000000, 0);
  el.append(renderer.domElement);
  renderer.domElement.setAttribute('role', 'img');
  renderer.domElement.setAttribute('aria-label', t('results.view3dLabel'));
  const world = new THREE.Scene();
  const [W, Hh] = opts.size;
  const w = W / ppm, h = Hh / ppm, cx = w / 2, cz = h / 2;
  const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 10 * Math.max(w, h) + 50);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.dampingFactor = 0.12; controls.maxPolarAngle = Math.PI / 2 - 0.02; controls.minDistance = 1;
  controls.target.set(cx, 0, cz);
  world.add(new THREE.HemisphereLight(0xffffff, 0x9aa3b2, 1.1));
  const sun = new THREE.DirectionalLight(0xffffff, 1.0); sun.position.set(cx - w, 2.5 * Math.max(w, h), cz + h); world.add(sun);

  // the drawing on the ground
  const planeMat = new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: opts.opacity == null ? 1 : opts.opacity, side: THREE.DoubleSide });
  const plane = new THREE.Mesh(new THREE.PlaneGeometry(w, h), planeMat);
  plane.rotation.x = -Math.PI / 2; plane.position.set(cx, 0, cz); plane.renderOrder = 0;   // the ground first, the slabs over it
  world.add(plane);
  let dead = false;                                          // destroyed before the texture arrived: it is dropped, not drawn
  new THREE.TextureLoader().load(opts.imageUrl,
    (tex) => { if (dead) { tex.dispose(); return; } tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy()); planeMat.map = tex; planeMat.needsUpdate = true; el.dataset.texture = 'loaded'; render(); },
    undefined, (err) => { el.dataset.texture = 'failed'; console.warn('3D: the drawing could not be loaded as a texture', err); });

  // the layers as groups
  const C = opts.colours;
  const groups = {}, labels = [];                            // the dimension labels keep a readable size whatever the distance
  const add = (name, mesh) => { if (!mesh) return; const g = groups[name] || (groups[name] = new THREE.Group()); g.add(mesh); };
  const wallMesh = prism(scene.walls, ppm, H.wall, C.walls);
  if (wallMesh) wallMesh.userData.kind = 'wall';
  add('walls', wallMesh);
  add('columns', prism(scene.columns, ppm, H.column, C.columns));
  add('stairs', prism(scene.stairs, ppm, H.stair, C.stairs, { opacity: 0.9 }));
  for (const v of scene.voids) add('stairs', slab(v.polys, ppm, C.voids, { y: 0.03, opacity: 0.35 }));
  for (const o of scene.openings) {
    const kind = openingLayer(o);
    const [y0, y1] = kind === 'windows' ? H.window : [0, H.door];
    const m = prism(o.polys, ppm, y1 - y0, C[kind], { y: y0 });
    if (m) { m.userData.kind = 'opening'; m.userData.id = o.id; }
    add(kind, m);
  }
  const roomMeshes = new Map();
  const rooms = new THREE.Group(); groups.rooms = rooms;
  for (const r of scene.rooms) {
    const m = slab(r.polys, ppm, opts.roomColour(r));
    if (!m) continue;
    m.userData.kind = 'room'; m.userData.id = r.id; roomMeshes.set(r.id, m); rooms.add(m);
  }
  for (const ring of scene.gf.map((p) => p[0])) add('gf', loop(ring, ppm, H.wall + 0.05, C.gf, true));
  for (const d of scene.dims || []) {                       // the overall dimensions on the floor, outside the building
    const g = new THREE.Group();
    g.add(loop([d.a, d.b], ppm, 0.02, C.text || '#1a1d21', false, false));
    for (const [p, q] of d.ext) g.add(loop([p, q], ppm, 0.02, C.text || '#1a1d21', false, false));
    const label = textSprite(d.text, C.text || '#1a1d21', [(d.a[0] + d.b[0]) / 2 / ppm, 0.35, (d.a[1] + d.b[1]) / 2 / ppm]);
    labels.push(label); g.add(label);
    add('dimensions', g);
  }
  for (const sg of scene.segments) if (sg.line.length >= 2) add('wallAxes', loop(sg.line, ppm, H.wall + 0.03, C.axes || C.gf, false, false));   // the axes along the wall tops
  for (const g of Object.values(groups)) world.add(g);
  let outline = null;                                        // the selected room's edge at wall height

  function setLayers(layers) {
    for (const [name, g] of Object.entries(groups)) g.visible = layers[name] !== false;
    render();
  }
  function setRoomColours(roomColour) {
    for (const r of scene.rooms) { const m = roomMeshes.get(r.id); if (m) m.material.color = hex(roomColour(r)); }
    render();
  }
  function setOpacity(a) { planeMat.opacity = a; render(); }
  /** Highlight a selection ({kind: room | wall | opening, id} or null): the room's outline on the floor, a wall's band
      or an opening's outline at wall height; the other rooms dim while a room is selected. */
  function select(sel) {
    if (outline) { world.remove(outline); outline.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); }); outline = null; }
    const roomId = sel && sel.kind === 'room' ? sel.id : null;
    for (const [rid, m] of roomMeshes) m.material.opacity = roomId && rid !== roomId ? 0.35 : 0.55;
    const col = C.selection || '#2f6ea6';
    const g = new THREE.Group();
    let ring = null, top = 0.1;                            // the tinted body of the element, and its outline on top
    if (roomId) {
      const r = scene.rooms.find((x) => x.id === roomId);
      if (r && r.polys[0]) { g.add(slab(r.polys, ppm, col, { y: 0.06, opacity: 0.45 })); ring = r.polys[0][0]; }
    } else if (sel && sel.kind === 'wall') {
      const s = scene.segments.find((x) => x.id === sel.id);
      if (s && s.band.length) { g.add(prism([[s.band]], ppm, H.wall + 0.03, col, { opacity: 0.55 })); ring = s.band; top = H.wall + 0.06; }
    } else if (sel && sel.kind === 'opening') {
      const o = scene.openings.find((x) => x.id === sel.id);
      if (o && o.polys[0]) { const win = openingLayer(o) === 'windows'; const [y0, y1] = win ? H.window : [0, H.door]; g.add(prism(o.polys, ppm, y1 - y0 + 0.03, col, { y: y0, opacity: 0.6 })); ring = o.polys[0][0]; top = y1 + 0.06; }
    }
    if (ring) { g.add(loop(ring, ppm, top, col, false)); outline = g; world.add(g); }
    render();
  }
  function fit() {
    const d = Math.hypot(w, h) * 1.05;
    const az = (AZ * Math.PI) / 180, elv = (EL * Math.PI) / 180;
    camera.position.set(cx + d * Math.cos(elv) * Math.sin(az), d * Math.sin(elv), cz + d * Math.cos(elv) * Math.cos(az));
    controls.target.set(cx, 0, cz); controls.update(); render();
  }
  /** A point of the plan (working px) on the screen of the 3D view, or null when it is behind the camera. */
  function projectPoint(x, y, height = 0.1) {
    const v = new THREE.Vector3(x / ppm, height, y / ppm).project(camera);
    if (v.z > 1) return null;
    const rect = renderer.domElement.getBoundingClientRect();
    return [(v.x + 1) / 2 * rect.width, (1 - v.y) / 2 * rect.height];
  }
  function resize() {
    const r = stage.getBoundingClientRect();
    if (!r.width || !r.height) return;
    renderer.setSize(r.width, r.height, false);
    renderer.domElement.style.width = '100%'; renderer.domElement.style.height = '100%';
    camera.aspect = r.width / r.height; camera.updateProjectionMatrix(); render();
  }
  let pending = false;
  function sizeLabels() {
    for (const s of labels) { const d = camera.position.distanceTo(s.position), hgt = d * 0.022; s.scale.set(hgt * s.userData.aspect, hgt, 1); }
  }
  function render() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; sizeLabels(); renderer.render(world, camera); if (opts.onRender) opts.onRender(); });
  }
  // picking: a click without a drag selects the room under the pointer
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
  let down = null;
  renderer.domElement.addEventListener('pointerdown', (e) => { down = [e.clientX, e.clientY]; });
  renderer.domElement.addEventListener('pointerup', (e) => {
    if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 3) { down = null; return; }
    down = null;
    const rect = renderer.domElement.getBoundingClientRect();
    ndc.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    // openings and walls stand above the room slabs: the first visible hit decides; a wall hit is mapped to the
    // segment of the wall graph under the point (the walls are one mesh)
    const pickable = Object.values(groups).filter((g) => g.visible).flatMap((g) => g.children).filter((m) => m.userData.kind);
    const hit = ray.intersectObjects(pickable, false)[0];
    let sel = null;
    if (hit) {
      const u = hit.object.userData;
      if (u.kind === 'wall') { const s = segmentAt(scene, [hit.point.x * ppm, hit.point.z * ppm], 0.5 * ppm); sel = s ? { kind: 'wall', id: s.id } : null; }
      else sel = { kind: u.kind, id: u.id };
    }
    if (opts.onSelect) opts.onSelect(sel);
  });
  controls.addEventListener('change', render);
  const ro = new ResizeObserver(resize); ro.observe(stage);
  // the damping of the controls needs frames only while something moves: the loop runs from a pointer or wheel
  // event until the controls report no change for a second, then sleeps (no idle CPU for an open 3D view)
  let damping = null, quiet = 0;
  const tick = () => { quiet = controls.update() ? 0 : quiet + 1; if (quiet < 60) { if (!quiet) render(); damping = requestAnimationFrame(tick); } else damping = null; };
  const wake = () => { quiet = 0; if (damping == null) damping = requestAnimationFrame(tick); };
  controls.addEventListener('start', wake);
  renderer.domElement.addEventListener('wheel', wake, { passive: true });
  resize(); fit(); wake();
  function destroy() {
    dead = true;
    if (damping != null) cancelAnimationFrame(damping); ro.disconnect(); controls.dispose();
    world.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material) { if (o.material.map) o.material.map.dispose(); o.material.dispose(); } });
    renderer.dispose(); renderer.forceContextLoss(); el.remove();   // the browser allows only a few WebGL contexts at a time
  }
  return { setLayers, setRoomColours, setOpacity, select, fit, projectPoint, resize, destroy, _groups: groups };   // _groups: the tests
}

export { create3D };
