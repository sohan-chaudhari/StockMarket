/**
 * Regression: the intraday x-axis must read like TradingView at every zoom, and a
 * session must NEVER be dated twice -- including when the library repaints the same
 * view repeatedly.
 *
 * Measured behaviour of lightweight-charts (live, production)
 * ----------------------------------------------------------
 * Ticks are requested in ascending index order with no repeats, e.g.
 *   672, 684, 696, 708, 720      (61-bar view, spacing span/5)
 * and the SAME view is repainted many times: one full pass, then partial passes
 * (crosshair move, delayed paint, the live forming candle nudging the range).
 * Every repaint re-asks tickMarkFormatter. So any rule that clears the "already
 * dated" set between passes lets the trailing tick of an already-dated session
 * print its date again -- a session dated at BOTH ends ("16 Oct" ... "16 Oct"),
 * which is exactly what users reported.
 *
 * Policy: the label is memoised per bar index for the current window, so a tick
 * returns the same string in every pass and a repeated date is impossible. Within
 * a window: first tick of a session -> date; later ticks -> time while <= 5 trading
 * sessions are visible, blank beyond. The memo resets only when the window changes
 * (zoom / pan / new data).
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

// -- synthetic 15m series: 10 sessions x 25 bars, 09:15-15:30 IST ------------
const BS = 900, BARS_PER_DAY = 25;
const sessions = [5, 6, 7, 8, 9, 12, 13, 14, 15, 16];   // Oct 2026 weekdays
const barTimes = [];
sessions.forEach(d => {
  const open = Date.UTC(2026, 9, d, 3, 45) / 1000;
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
  width: () => 1640,
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

// the measured tick grid for a window: ascending, ~one tick per fifth of the span
function tickGrid(from, to, divisor) {
  const span = to - from;
  const step = Math.max(1, Math.round(span / (divisor || 5)));
  const out = [];
  for (let i = from + step; i <= to; i += step) out.push(i);
  return out;
}
function ask(indices, markType) {
  const out = {};
  indices.forEach(i => { out[i] = AX.tick(chart, i * BS, markType == null ? 2 : markType, '15m'); });
  return out;
}
const labelsOf = m => Object.keys(m).map(k => m[k]).filter(Boolean);
function datedSessions(m) {
  const c = {};
  labelsOf(m).filter(isDate).forEach(l => { c[l] = (c[l] || 0) + 1; });
  return c;
}

function main() {
  console.log('\n[1] zoomed in (~2 sessions): times, one date per session');
  lr = { from: 0, to: 49 };
  let m = ask(tickGrid(0, 49), 3);
  ok(labelsOf(m).filter(isTime).length >= 2, 'shows times (' + JSON.stringify(labelsOf(m)) + ')');
  ok(labelsOf(m).filter(isDate).length === 2, 'one date per session (' + JSON.stringify(labelsOf(m).filter(isDate)) + ')');
  ok(Object.values(datedSessions(m)).every(n => n === 1), 'no date twice');

  console.log('\n[2] a 2-session view ACROSS A WEEKEND (Fri+Mon) still shows times');
  lr = { from: 100, to: 149 };
  m = ask(tickGrid(100, 149), 3);
  const calSpan = (barTimes[149] - barTimes[100]) / 86400;
  ok(calSpan > 3, 'window spans > 3 calendar days (' + calSpan.toFixed(2) + ')');
  ok(labelsOf(m).filter(isTime).length >= 2, 'still shows times (' + JSON.stringify(labelsOf(m)) + ')');

  console.log('\n[3] zoomed out (~5 sessions): one date per session, dates only');
  lr = { from: 0, to: 124 };
  m = ask(tickGrid(0, 124), 2);
  const d3 = datedSessions(m);
  ok(Object.keys(d3).length >= 4, 'sessions dated (' + JSON.stringify(Object.keys(d3)) + ')');
  ok(labelsOf(m).filter(isTime).length === 0, 'no times mixed in');
  ok(Object.values(d3).every(n => n === 1), 'no date twice');

  console.log('\n[4] REPAINTS of one view must return identical labels (the bug)');
  // This is the reported "16 Oct ... 16 Oct": the same view repainted, and the
  // trailing tick re-printed its session date.
  lr = { from: 0, to: 199 };                       // wide, like the screenshot
  const grid = tickGrid(0, 199, 10);
  const first = ask(grid, 2);
  const repaintFull = ask(grid, 2);                // a full repaint
  const repaintReverse = ask(grid.slice().reverse(), 2);   // repaint in another order
  const trailingOnly = ask([grid[grid.length - 1]], 2);    // only the last tick
  const leadingOnly = ask([grid[0]], 2);                   // only the first tick
  let same = true, sameRev = true, sameTail = true, sameLead = true;
  grid.forEach(i => {
    if (repaintFull[i] !== first[i]) same = false;
    if (repaintReverse[i] !== first[i]) sameRev = false;
  });
  if (trailingOnly[grid[grid.length - 1]] !== first[grid[grid.length - 1]]) sameTail = false;
  if (leadingOnly[grid[0]] !== first[grid[0]]) sameLead = false;
  ok(same, 'full repaint returns the same labels');
  ok(sameRev, 'repaint in reverse order returns the same labels');
  ok(sameTail, 'trailing-tick-only repaint returns the same label ("' + trailingOnly[grid[grid.length - 1]] + '")');
  ok(sameLead, 'leading-tick-only repaint returns the same label ("' + leadingOnly[grid[0]] + '")');
  const d4 = datedSessions(first);
  ok(Object.values(d4).every(n => n === 1),
     'each visible session dated exactly once (' + JSON.stringify(d4) + ')');
  ok(Object.keys(d4).length >= 5, 'and every visible session is dated (' + Object.keys(d4).length + ')');

  console.log('\n[5] a changed window (zoom) re-labels cleanly, still one date per session');
  lr = { from: 40, to: 189 };
  const zoomed = ask(tickGrid(40, 189, 5), 2);
  const d5 = datedSessions(zoomed);
  ok(Object.values(d5).every(n => n === 1), 'no date twice after a zoom (' + JSON.stringify(d5) + ')');
  ok(Object.keys(d5).length >= 3, 'dates present after a zoom (' + Object.keys(d5).length + ')');

  console.log('\n[6] a missing / stale day map cannot blank the axis');
  window._intradayDayFirstBarMap = null;
  lr = { from: 0, to: 124 };
  m = ask(tickGrid(0, 124), 2);
  ok(labelsOf(m).filter(isDate).length >= 4, 'dates still rendered without the day map');
  window._intradayDayFirstBarMap = Object.assign({}, dayMap);

  console.log('\n[7] ticks past the last real bar stay unlabelled');
  ok(AX.tick(chart, (barTimes.length + 5) * BS, 2, '15m') === '', 'index past the bar map returns an empty label');

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
