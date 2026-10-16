/* Project state (which sheet jobs, where the user is), the job records from the server, polling, access to the analysis. */
import { PKEY, api, every, getJSON, onNotFound, sheetUrl, store } from './core.js';

const BUSY = ['analysing', 'scaling', 'queued', 'running'];   // as BUSY in server.py (a test keeps the two equal)
const POLL_MS = 1500;
const LOG_STAMP = 9;                                         // a log line starts with "HH:MM:SS "

// ---------------------------------------------------------------------------------------------------------------------
// Project state (which sheet jobs belong to the project, in which order, and where the user is)

const newProject = () => ({ name: '', sheets: [], screen: 'upload', cursor: { sheet: 0, pkg: 0, drawing: 0 }, resultsKey: null });
const P = loadProject();                // mutated in place; resetProject() for a new project
const J = {};                           // job id -> job record from the server
let screenName = null;                  // the screen on show; ui.go() sets it through setScreen (an import cannot be assigned)
function setScreen(name) { screenName = name; }

function loadProject() {
  const p = store.get(PKEY, null);
  return p && Array.isArray(p.sheets) ? Object.assign(newProject(), p) : newProject();
}
function saveProject() { store.set(PKEY, P); }
function resetProject() { Object.assign(P, newProject()); saveProject(); }

/** A sheet leaves the project (the server deleted it, or the user removed it). */
function forgetSheet(id) { P.sheets = P.sheets.filter((s) => s !== id); delete J[id]; saveProject(); }
/** Delete a sheet on the server (a busy one is discarded when its worker is done) and forget it. */
async function deleteSheet(id) {
  try { await api(sheetUrl(id), { method: 'DELETE' }); } catch (e) { /* already gone */ }
  forgetSheet(id);
}

let goneListener = null;                // set by the app: where to go when the server no longer has a sheet
function onSheetGone(fn) { goneListener = fn; }
onNotFound((id) => { if (P.sheets.includes(id)) { forgetSheet(id); if (goneListener) goneListener(id); } });   // whichever request learns it

/** The job record from the server; the revision the client holds goes along, and an unchanged job costs one small
    reply. A sheet the server no longer has (removed, or expired on the public server) leaves the project; when the
    server cannot be reached the record stays as it is (a missing one is asked for again by the next poll). */
async function refreshJob(id) {
  try {
    const have = J[id];
    const r = await getJSON(sheetUrl(id, have && have.rev != null ? `?rev=${have.rev}` : ''));
    if (!r.unchanged) { if (!have || r.rev == null || have.rev == null || r.rev >= have.rev) J[id] = r; }   // two polls in flight: the older reply is dropped
    else if (r.queue && have && have.progress) Object.assign(have.progress, r.queue);   // a waiting job: its position and wait move while the record does not
  } catch (e) {
    if (e.status === 404) { forgetSheet(id); if (goneListener) goneListener(id); }
  }
  return J[id];
}

async function refreshAll() { await Promise.all(P.sheets.map(refreshJob)); }

let healthReq = null;
/** The server's pipeline check (GET /api/health: what the pipeline needs, public mode), asked once per page. */
function getHealth() { return healthReq || (healthReq = getJSON('/api/health').catch((e) => { healthReq = null; throw e; })); }   // a blip at boot is not remembered
/** True for the failure of a page served by a plain file server, which has no API: /api/health answers 404 there, not
    with the app's JSON. (A network error is not enough: a short outage must not hide the app.) */
const noAppServer = (e) => e.status === 404 && !e.code;

function busy(job) { return BUSY.includes(job.status); }
/** The last line of the job log without its time stamp, or the fallback. */
const lastLogLine = (job, fallback) => job.log && job.log.length ? job.log[job.log.length - 1].slice(LOG_STAMP) : fallback;

let jobsListener = null;                // set by the app: what to re-render when jobs change (ui.onJobsChanged)
function onJobs(fn) { jobsListener = fn; }
let stopPolling = null, polls = 0;
const KEEP_ALIVE_POLLS = 120;           // every three minutes the sheet on show is asked for, so the public server keeps it (its "seen" moves)
/** Poll the busy sheets (and those not loaded yet); the listener hears which records changed (a new revision). */
function startPolling() {
  if (stopPolling) return;
  stopPolling = every(POLL_MS, async () => {
    const ids = P.sheets.filter((id) => !J[id] || busy(J[id]));
    const shown = P.resultsKey ? P.resultsKey.split('|')[0] : null;
    if (++polls % KEEP_ALIVE_POLLS === 0 && shown && P.sheets.includes(shown) && !ids.includes(shown)) ids.push(shown);
    if (!ids.length) return;
    const before = ids.map((id) => J[id] && J[id].rev);
    await Promise.all(ids.map(refreshJob));
    const changed = ids.filter((id, k) => (J[id] && J[id].rev) !== before[k]);
    if (changed.length && jobsListener) jobsListener(changed);
  });
}

// ---------------------------------------------------------------------------------------------------------------------
// Access to the analysis

const keyOf = (pkg, dr) => `${pkg.id}/${dr.id}`;
const packagesOf = (job) => (job && job.analysis && job.analysis.packages) || [];
const confOf = (job, pkg, dr) => (job.confirm && job.confirm[keyOf(pkg, dr)]) || {};
function ppmOf(job, pkg, dr) { const c = confOf(job, pkg, dr); return c.px_per_m || (dr.scale && dr.scale.px_per_m) || null; }
function selectedDrawings(job, pkg) { return pkg.drawings.filter((d) => { const c = confOf(job, pkg, d); return c.extract === undefined ? d.kind === 'floor plan' : !!c.extract; }); }
function polygonOf(job, pkg, dr) { const c = confOf(job, pkg, dr); return c.polygon_px || dr.polygon_px; }
function bboxOf(pts) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [x, y] of pts) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
  return { x0, y0, x1, y1 };
}
function drawingLabel(job, pkg, dr) { const c = confOf(job, pkg, dr); return c.storey || dr.storey || ''; }
/** True when a drawing's scale cues must be read (again): none read yet, or read in another area than the confirmed one. */
function scaleStale(c) {
  return !c || c.scale_pending !== false || JSON.stringify(c.polygon_px || null) !== JSON.stringify(c.scale_area || null);
}

/** The sheet, page and drawing at the cursor, clamped to what the analysis has. */
function atCursor() {
  const id = P.sheets[P.cursor.sheet], job = J[id], pk = packagesOf(job);
  const pi = Math.max(0, Math.min(P.cursor.pkg, pk.length - 1)), pkg = pk[pi];
  const dr = pkg ? pkg.drawings[Math.max(0, Math.min(P.cursor.drawing, pkg.drawings.length - 1))] : undefined;
  return { id, job, pk, pi, pkg, dr };
}

/** The drawings of the project selected for extraction, in sheet, page and drawing order. */
function drawingRows() {
  const rows = [];
  for (const id of P.sheets) {
    const job = J[id];
    if (!job || !job.analysis) continue;
    for (const pkg of packagesOf(job)) for (const dr of selectedDrawings(job, pkg)) {
      const key = `${id}|${keyOf(pkg, dr)}`;
      const res = job.results && job.results[keyOf(pkg, dr)];
      rows.push({ key, id, job, pkg, dr, conf: confOf(job, pkg, dr), res, label: drawingLabel(job, pkg, dr) });
    }
  }
  return rows;
}

/** True when every selected drawing of the sheet has a confirmed scale (the extraction can start). */
function sheetConfirmed(job) {
  return packagesOf(job).every((pk) => selectedDrawings(job, pk).every((d) => confOf(job, pk, d).scale_confirmed));
}

function firstUnconfirmed() {
  for (let si = 0; si < P.sheets.length; si++) {
    const job = J[P.sheets[si]];
    if (!job || !job.analysis) continue;
    const pk = packagesOf(job);
    for (let pi = 0; pi < pk.length; pi++) {
      const anyUnconfirmed = pk[pi].drawings.some((d) => !confOf(job, pk[pi], d).area_confirmed);
      if (anyUnconfirmed) return { sheet: si, pkg: pi, drawing: 0 };
      const sel = selectedDrawings(job, pk[pi]);
      const di = sel.findIndex((d) => !confOf(job, pk[pi], d).scale_confirmed);
      if (di >= 0) return { sheet: si, pkg: pi, drawing: pk[pi].drawings.indexOf(sel[di]), scale: true };
    }
  }
  return null;
}

export { J, P, atCursor, bboxOf, busy, confOf, deleteSheet, drawingRows, firstUnconfirmed, getHealth, keyOf, lastLogLine, noAppServer, onJobs, onSheetGone, packagesOf, polygonOf, ppmOf, refreshAll, refreshJob, resetProject, saveProject, scaleStale, screenName, selectedDrawings, setScreen, sheetConfirmed, startPolling };
