/* How far a job is and how long it will take: the pipeline reports which stage runs, this module turns that into a
   bar that moves and a time estimate. The typical seconds per stage were measured on the public server (8 cores) for
   the demo sheet (a 6 MP working image) and scale with the size of the drawing; the text stage is almost free when
   the server read the text ahead while the user confirmed. Pure: no page access. */
import { t } from './i18n.js';

const DEMO_MP = 6.2;                                       // working pixels of the demo drawing (3225 x 1928 at 50 px/m)
// [prefix of the pipeline's stage label, typical seconds, i18n key]
const ANALYSIS = [
  ['waiting for the worker', 0, 'stage.waiting'], ['0a normalisation', 4, 'stage.layout'], ['1c scale', 5, 'stage.scaleCues']];
const EXTRACTION = [
  ['waiting for the worker', 0, 'stage.waiting'], ['reading the text ahead', 30, 'stage.ahead'], ['starting', 1, 'stage.starting'],
  ['1 sheet text', 0.5, 'stage.sheetText'], ['1b layout', 0.5, 'stage.layout'], ['1c scale', 0.5, 'stage.scaleCues'],
  ['2 text (OCR', 30, 'stage.ocr'], ['0 triage', 2, 'stage.triage'], ['2 text layer', 0.5, 'stage.textLayer'],
  ['3 segmentation', 18, 'stage.segmentation'], ['3 walls', 3, 'stage.walls'], ['4 openings', 1, 'stage.openings'],
  ['5 stairs', 0.5, 'stage.stairs'], ['6 rooms', 2, 'stage.rooms'], ['7 room', 0.5, 'stage.roomAttributes'],
  ['8 derived', 1, 'stage.derived'], ['9 scale', 0.5, 'stage.checks'], ['10 export', 1, 'stage.export']];
const SCALED = new Set(['2 text (OCR', 'reading the text ahead', '3 segmentation', '0 triage', '3 walls']);   // grow with the drawing

/** The size factor of a drawing: its working pixels against the demo's (clamped: a tiny crop still costs a few seconds). */
function sizeFactor(workMp) { return workMp ? Math.min(4, Math.max(0.3, workMp / DEMO_MP)) : 1; }

/** The plan of a job kind: [{prefix, seconds, key}], with the text stage almost free when it was (or is being) read
    ahead; the read-ahead itself, when the run waits for it, takes its full time from the moment it began. */
function plan(kind, { ahead = false, workMp = null } = {}) {
  const f = sizeFactor(workMp);
  return (kind === 'analysis' ? ANALYSIS : EXTRACTION).map(([prefix, seconds, key]) => ({
    prefix, key, seconds: ahead && prefix === '2 text (OCR' ? 0.5 : SCALED.has(prefix) ? seconds * f : seconds }));
}

/** Where a job stands: `stage` is the pipeline's label, `inStage` the seconds since it began. Returns the fraction
    done (0..1), the seconds left (null when unknown), the stage's text and whether it runs longer than usual. */
function estimate(steps, stage, inStage = 0) {
  const s = String(stage || '');
  const i = steps.findIndex((x) => s.startsWith(x.prefix));
  const total = steps.reduce((a, x) => a + x.seconds, 0);
  if (i < 0) return { fraction: 0, left: total, label: s || t('stage.working'), late: false };
  const before = steps.slice(0, i).reduce((a, x) => a + x.seconds, 0);
  const cur = steps[i].seconds;
  const done = before + Math.min(inStage, cur);
  return { fraction: total ? done / total : 0, left: Math.max(0, Math.round(total - done)), label: t(steps[i].key), late: cur > 0 && inStage > cur * 1.5 + 5 };
}

/** "about 40 s left", "about 2 min left", "almost done", or that it takes longer than usual. */
function etaText({ left, late, fraction }) {
  if (late) return t('eta.longer');
  if (left == null) return '';
  if (left <= 3 || fraction >= 0.97) return t('eta.soon');
  if (left >= 90) return t('eta.minutes', { m: Math.round(left / 60) });
  return t('eta.seconds', { s: left < 10 ? left : Math.round(left / 5) * 5 });
}

export { estimate, etaText, plan, sizeFactor };
