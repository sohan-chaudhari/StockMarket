/**
 * Regression: intraday x-axis dates must be emitted ONCE per trading day, even
 * when Lightweight Charts re-anchors the visible range *during* one render, and
 * the chart must never be parked past its own candles.
 *
 * Root cause being locked down
 * ----------------------------
 * `_renderChartData` seeds the (invisible) whitespace series with future bars so
 * the time scale has room to the right. That pad was `candles + 3000` for
 * intraday (and +365 / +150 / +80 on 1D / 1W / 1M), which inflated the chart's
 * whole time domain several times over. Lightweight Charts pins the visible
 * range's right edge to "last bar in the domain + rightOffset" on the paint that
 * follows setData, so a freshly opened chart landed in that empty region: the
 * candles were pushed off screen (measured live: last candle at x = -17196 on an
 * 880px chart) and every axis tick past the last real bar returned '' -- the
 * duplicated-then-missing dates reported on a 15m chart.
 *
 * Two independent guards are asserted here:
 *   1. axis-format.js clears its "already labelled" sets at most once per
 *      animation frame, so a mid-render range drift can no longer wipe them
 *      between two ticks of the same render (that wipe is the only way the same
 *      date can be printed twice).
 *   2. dashboard.js keeps the whitespace pad bounded.
 *
 * Loads the REAL axis-format.js, so this fails if the formatter regresses.
 */
const fs = require('fs');
const path = require('path');
require('./harness.cjs');

const FRONTEND = path.join(__dirname, '..');
const src = fs.readFileSync(path.join(FRONTEND, 'axis-format.js'), 'utf8');

let pass = 0, fail = 0;
function ok(cond, name) {
  if (cond) { pass++; console.log('  PASS ' + name); }
  else { fail++; console.log('  FAIL ' + name); }
}

// ── synthetic 15m series: 8 NSE sessions x 25 bars, 09:15-15:30 IST ─────────
const BS = 900;
const BAR_MIN = 15;
const BARS_PER_DAY = 25;
const sessions = [12, 13, 14, 15, 16, 19, 20, 21];   // Oct 2026 weekdays
const barTimes = [];
sessions.forEach(d => {
  const open = Date.UTC(2026, 9, d, 3, 45) / 1000;  // 03:45 UTC == 09:15 IST
  for (let k = 0; k < BARS_PER_DAY; k++) barTimes.push(open + k * BS);
});

const dayFirst = {};
function istDay(epochSec) {
  const d = new Date(epochSec * 1000 + 5.5 * 3600 * 1000);
  return d.getUTCFullYear() + '-' + String(d.getUTCMonth() + 1).padStart(2, '0') + '-' + String(d.getUTCDate()).padStart(2, '0');
}
barTimes.forEach((t, i) => { const k = istDay(t); if (dayFirst[k] == null) dayFirst[k] = i; });

window._intradayBarTimes = barTimes;
window._intradayBarSecs = BS;
window._intradayDayFirstBarMap = dayFirst;

// ── controllable stub chart ─────────────────────────────────────────────────
let lr = { from: 0, to: 146 };
const timeScale = {
  getVisibleLogicalRange: () => lr,
  getVisibleRange: () => ({ from: Math.max(0, lr.from) * BS, to: lr.to * BS }),
  width: () => 880,
  subscribeVisibleLogicalRangeChange() {},
};
const chart = { timeScale: () => timeScale };

const _log = console.log;
console.log = () => {};
try { (0, eval)(src); } catch (e) { console.log = _log; console.error('axis-format.js LOAD FAILED: ' + e.message); process.exit(2); }
console.log = _log;

const AX = window.LeverageAxis;
ok(AX && typeof AX.tick === 'function', 'axis-format.js loaded and exposes tick()');

AX.register(chart, {
  getRange: () => '15m',
  getBarTimes: () => window._intradayBarTimes,
  getBarSecs: () => window._intradayBarSecs,
});

function tickAt(i, mt) { return AX.tick(chart, i * BS, mt == null ? 2 : mt, '15m'); }
function sweep(from, to) {
  const out = [];
  for (let i = from; i <= to; i++) { const l = tickAt(i); if (l) out.push({ i, l }); }
  return out;
}
const isDate = s => /\d{2} [A-Z][a-z]{2}/.test(s);

async function main() {
  console.log('\n[1] one date per trading day across a multi-day window');
  const labels = sweep(0, 146);
  const dates = labels.filter(o => isDate(o.l));
  const counts = {};
  dates.forEach(o => { counts[o.l] = (counts[o.l] || 0) + 1; });
  const dupes = Object.keys(counts).filter(k => counts[k] > 1);
  ok(dates.length >= 4, 'multi-day window produced date labels (' + dates.length + ': ' +
     JSON.stringify(dates.map(o => o.l)) + ')');
  ok(dupes.length === 0, 'no date repeated in a single render (dupes: ' + JSON.stringify(dupes) + ')');

  console.log('\n[2] a mid-render range drift must not duplicate a date');
  const last = dates[dates.length - 1];            // e.g. the last day in the window
  const again = tickAt(last.i);
  ok(again === '', 'the same day is suppressed later in the SAME render (got "' + again + '")');
  lr = { from: last.i, to: last.i + 146 };         // the chart re-anchors mid-render
  const drifted = tickAt(last.i);
  ok(drifted === '',
     'after a mid-render range drift the day is still NOT dated twice (got "' + drifted + '")');

  console.log('\n[3] the next animation frame still re-labels (zoom must keep working)');
  await new Promise(r => setTimeout(r, 5));        // flush the stubbed rAF: frame ends
  lr = { from: Math.max(0, last.i - 20), to: last.i + 126 };   // a real zoom/pan
  const next = tickAt(last.i);
  ok(next === last.l, 'a new frame re-emits the date (got "' + next + '", want "' + last.l + '")');

  console.log('\n[4] ticks past the last real bar stay unlabelled (no fabricated sessions)');
  ok(tickAt(barTimes.length + 5) === '', 'an index past the bar map returns an empty label');

  console.log('\n[5] source guard: the whitespace runway stays bounded');
  const dash = fs.readFileSync(path.join(FRONTEND, 'dashboard.js'), 'utf8');
  ok(dash.indexOf('candleData.length + 3000') === -1, 'intraday pad is no longer +3000 bars');
  ok(/candleData\.length \+ 40;/.test(dash), 'intraday pad is bounded (candles + 40)');
  ok(dash.indexOf('wi <= 365') === -1 && dash.indexOf('wi <= 150') === -1 && dash.indexOf('wi <= 80') === -1,
     'daily/weekly/monthly pads are no longer +365 / +150 / +80');
  ok(/wi <= 30;/.test(dash) && /wi <= 12;/.test(dash) && /wi <= 6;/.test(dash),
     'daily/weekly/monthly pads are bounded (30 / 12 / 6)');

  console.log('\n==================================================');
  console.log('  PASS: ' + pass + '   FAIL: ' + fail);
  console.log('==================================================');
  process.exit(fail ? 1 : 0);
}
main();
