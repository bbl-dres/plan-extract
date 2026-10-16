/* The arithmetic of the scale step: distances, typed lengths, scale numbers. Pure (no page, no state); lengths in
   metres, distances in sheet px. */
const UNITS = { m: 1, cm: 0.01, mm: 0.001, ft: 0.3048, in: 0.0254 };
const INCH = 0.0254;

const dist = (p, q) => Math.hypot(q[0] - p[0], q[1] - p[1]);
/** A typed number: '2,5' and '2.5' alike; NaN when it is none. */
const parseNumber = (text) => parseFloat(String(text).replace(',', '.'));
/** A typed length in metres, NaN unless it is a positive number. */
function parseLength(text, unit) { const v = parseNumber(text); return v > 0 ? v * (UNITS[unit] || 1) : NaN; }
/** N of the scale 1 : N from the resolution (dpi) and the pixels per metre; null when either is missing. */
const scaleNumber = (dpi, ppm) => dpi && ppm ? dpi / INCH / ppm : null;
/** Pixels per metre from the resolution and N (the inverse of scaleNumber). */
const ppmFromScale = (dpi, N) => dpi / INCH / N;
const round1 = (v) => Math.round(v * 10) / 10;

export { UNITS, dist, parseLength, parseNumber, ppmFromScale, round1, scaleNumber };
