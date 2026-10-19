/**
 * Regression: the intraday x-axis must read like TradingView at every zoom, and
 * must never print one session's date twice -- including while the user is zooming.
 *
 * Reported symptoms this locks down
 * ---------------------------------
 * 1. Zooming in showed the same date two or three times where times belong.
 *    Measured cause: lightweight-charts asks for tick labels in ascending order
 *    with no repeats, but the code cleared its "already dated" set whenever the
 *    visible range differed *inside* tick(). While a user zoomed, the range
 *    changed between two ticks of the same pass, the set was wiped mid-pass, and
 *    the session was dated a second time ("15 Oct" ... "15 Oct").
 * 2. After scrolling left (lazy-load prepends bars) the axis was empty. Every
 *    tick outside a `bars / 4` window returned '', so a whole axis could be blank.
 *
 * Policy: first tick of each session -> date; later ticks -> time while <= 5
 * trading sessions are visible, blank beyond. "Already dated" resets once per
 * render pass (left edge of the view, or a >=250 ms gap), never on a range
 * comparison, so a mid-zoom range change cannot duplicate a date.
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

// -- synthetic 15m series: 8 NSE sessions x 25 bars, 09:15-15:30 IST ----------
const BS = 900, BARS_PER_DAY = 25;
const sessions = [12, 13, 14, 15, 16, 19, 20, 21];   // Oct 2026 weekdays
const barTimes = [];
sessions.forEach(d => {
  const open = Date.UTC(2026, 9, d, 3, 45) / 1000;  // 03:45 UTC == 09:15 IST
  for (let k = 0; k < BARS_PER_DAY; k++) barTimes.push(open + k * BS);
});
const dayMap = {};
function istDay(sec) {
  const d = new Date(sec * 1000 + 5.5 * 3600 * 1000);
  return d.getUTCFullYear() + '-' + String(d.getUTCMonth() + 1).padStart(2, '0') + '-' + String(d.getUTCDate()).padStart(2, '0');
}
barTimes.forEach((t, i) => { const k = istDay(t); if (dayMap[k] == null) dayMap[k] = i; });

window._intradayBarTimes = barTimes;
window._intradayBarSecs = BS;
window._intradayDayFirstBarMap = Object.assign({}, dayMap);

let lr = { from: 0, to: 49 };
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

const isDate = s => /^\d{2} [A-Z][a-z]{2}( \d{4})?$/.test(s);
const isTime = s => /^\d{2}:\d{2}$/.test(s);

// One render pass, emulating the measured library behaviour: ascending indices,
// roughly one tick per fifth of the visible window, starting a few bars in.
function renderPass(from, to, markType) {
  const span = to - from;
  const step = Math.max(1, Math.round(span / 5));
  const start = from + Math.min(step, Math.max(2, Math.round(span / 20)));
  const out = [];
  for (let i = start; i <= to; i += step) {
    const l = AX.tick(chart, i * BS, markType, '15m');
    out.push({ i, l });
  }
  return out;
}
const labelsOf = a => a.map(x => x.l).filter(Boolean);
function dupDates(a) {
  const c = {};
  labelsOf(a).filter(isDate).forEach(l => { c[l] = (c[l] || 0) + 1; });
  return Object.keys(c).filter(k => c[k] > 1);
}

function main() {
  console.log('\n[1] zoomed in (~2 sessions): times, one date per session');
  lr = { from: 0, to: 49 };
  let p = renderPass(0, 49, 3);
  let L = labelsOf(p);
  const dates = L.filter(isDate), times = L.filter(isTime);
  ok(times.length >= 3, 'shows times (' + JSON.stringify(L) + ')');
  ok(dates.length === 2, 'one date per session (' + JSON.stringify(dates) + ')');
  ok(dupDates(p).length === 0, 'no date twice (dupes: ' + JSON.stringify(dupDates(p)) + ')');
  ok(L.length === p.length, 'no tick left unlabelled when zoomed in (' + L.length + '/' + p.length + ')');

  console.log('\n[2] a 2-session view ACROSS A WEEKEND (Fri+Mon) still shows times');
  lr = { from: 100, to: 149 };
  p = renderPass(100, 149, 3);
  L = labelsOf(p);
  const calSpan = (barTimes[149] - barTimes[100]) / 86400;
  ok(calSpan > 3, 'window really spans > 3 calendar days (' + calSpan.toFixed(2) + ')');
  ok(L.filter(isTime).length >= 3, 'still shows times (' + JSON.stringify(L) + ')');
  ok(L.filter(isDate).length === 2, 'one date per session (' + JSON.stringify(L.filter(isDate)) + ')');
  ok(dupDates(p).length === 0, 'no date twice');

  console.log('\n[3] zoomed out (~6 sessions): one date per session, no times, not empty');
  lr = { from: 0, to: 149 };
  p = renderPass(0, 149, 2);
  L = labelsOf(p);
  ok(L.filter(isDate).length >= 5, 'sessions dated (' + JSON.stringify(L.filter(isDate)) + ')');
  ok(L.filter(isTime).length === 0, 'no times mixed in (' + L.filter(isTime).length + ')');
  ok(dupDates(p).length === 0, 'no date twice (dupes: ' + JSON.stringify(dupDates(p)) + ')');
  ok(L.length >= 5, 'axis is not empty (' + L.length + ' labels)');

  console.log('\n[4] a range change DURING a pass must not duplicate a date (the bug)');
  // The real sequence that produced "15 Oct ... 15 Oct": the visible range moves
  // between two ticks of the same pass (interactive zoom). The old code compared
  // the range inside tick() and cleared the sets mid-pass.
  lr = { from: 100, to: 149 };
  AX.tick(chart, 100 * BS, 2, '15m');            // pass start -> clears, dates 16 Oct
  const datedOnce = AX.tick(chart, 125 * BS, 2, '15m');  // same session
  lr = { from: 104, to: 153 };                   // the user is still zooming
  const afterDrift = AX.tick(chart, 130 * BS, 2, '15m'); // same session again
  ok(!isDate(afterDrift),
     'same session is not dated again after a mid-pass range change (got "' + afterDrift + '")');

  console.log('\n[5] a fresh pass after a zoom re-labels (zoom-in must keep dates)');
  lr = { from: 40, to: 189 };
  p = renderPass(40, 189, 2);
  L = labelsOf(p);
  ok(L.filter(isDate).length >= 3, 'dates rendered after the zoom (' + JSON.stringify(L.filter(isDate)) + ')');
  ok(dupDates(p).length === 0, 'no date twice');

  console.log('\n[6] a missing / stale day map cannot blank the axis');
  window._intradayDayFirstBarMap = null;
  lr = { from: 0, to: 149 };
  p = renderPass(0, 149, 2);
  ok(labelsOf(p).filter(isDate).length >= 5, 'dates still rendered without the day map');
  window._intradayDayFirstBarMap = Object.assign({}, dayMap);

  console.log('\n[7] ticks past the last real bar stay unlabelled');
  ok(AX.tick(chart, (barTimes.length + 5) * BS, 2, '15m') === '',
     'an index past the bar map returns an empty label');

  console.log('\n[8] source guard: the whitespace runway stays bounded');
  const dash = fs.readFileSync(path.join(FRONTEND, 'dashboard.js'), 'utf8');
  ok(dash.indexOf('candleData.length + 3000') === -1, 'intraday pad is no longer +3000 bars');
  ok(/candleData\.length \+ 40;/.test(dash), 'intraday pad is bounded (candles + 40)');

  console.log('\n==================================================');
  console.log('  PASS: ' + pass + '   FAIL: ' + fail);
  console.log('==================================================');
  process.exit(fail ? 1 : 0);
}
main();
