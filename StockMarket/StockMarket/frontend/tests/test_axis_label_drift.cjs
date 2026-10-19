/**
 * Regression: the intraday x-axis must read like TradingView at every zoom.
 *
 * Reported symptoms this locks down
 * ---------------------------------
 * 1. Zoomed in, the same date appeared two or three times where times were
 *    expected. Cause: the formatter ignored the zoom level and dated any tick
 *    that fell inside its own `bars / 4` window, so several ticks printed one day.
 * 2. After scrolling left (lazy-load prepends older bars), the axis was empty --
 *    no date, no time. Cause: in the "date" tier every tick that was not inside
 *    that same window returned '', so a whole axis could be blank; a render split
 *    across frames made it worse because the per-frame "already labelled" clear
 *    could already have been consumed.
 *
 * Policy now: the first tick of each trading day is dated, later ticks of that day
 * show their time while the real visible span is <= 3 trading days, and stay
 * blank beyond that. "Already labelled" resets at the start of every render pass
 * (range change, or tick index going backwards), never on a frame timer.
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
const BARS_PER_DAY = 25;
const sessions = [12, 13, 14, 15, 16, 19, 20, 21];   // Oct 2026 weekdays
const barTimes = [];
sessions.forEach(d => {
  const open = Date.UTC(2026, 9, d, 3, 45) / 1000;  // 03:45 UTC == 09:15 IST
  for (let k = 0; k < BARS_PER_DAY; k++) barTimes.push(open + k * BS);
});
const dayMap = {};
function istDay(epochSec) {
  const d = new Date(epochSec * 1000 + 5.5 * 3600 * 1000);
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

// One render pass: LWC lays ticks out left to right.
function sweep(from, to, markType) {
  const out = [];
  for (let i = from; i <= to; i++) {
    const l = AX.tick(chart, i * BS, markType, '15m');
    if (l) out.push({ i, l });
  }
  return out;
}
function dupDates(labels) {
  const c = {};
  labels.filter(o => isDate(o.l)).forEach(o => { c[o.l] = (c[o.l] || 0) + 1; });
  return Object.keys(c).filter(k => c[k] > 1);
}

function main() {
  console.log('\n[1] zoomed in (~2 sessions, markType=Time): times, one date per day');
  lr = { from: 0, to: 49 };
  let labels = sweep(0, 49, 3);
  const dates = labels.filter(o => isDate(o.l));
  const times = labels.filter(o => isTime(o.l));
  ok(times.length >= 30, 'most ticks are times (' + times.length + ')');
  ok(dates.length === 2, 'exactly one date per session (' + JSON.stringify(dates.map(o => o.l)) + ')');
  ok(dupDates(labels).length === 0, 'no date printed twice (dupes: ' + JSON.stringify(dupDates(labels)) + ')');
  ok(labels.length === 50, 'no tick left unlabelled when zoomed in (' + labels.length + '/50)');

  console.log('\n[1b] a 2-session view ACROSS A WEEKEND (Fri+Mon) still reads as times');
  // Fri 16 Oct + Mon 19 Oct = 2 trading days but 3.3 calendar days. Measuring the
  // zoom in calendar days wrongly classified this as "zoomed out" and dropped the
  // times, which is why the axis looked wrong when zoomed in on a Monday.
  lr = { from: 100, to: 149 };
  const wk = sweep(100, 149, 3);
  const wkDates = wk.filter(o => isDate(o.l));
  const wkTimes = wk.filter(o => isTime(o.l));
  const calSpan = (barTimes[149] - barTimes[100]) / 86400;
  ok(calSpan > 3, 'window really spans more than 3 calendar days (' + calSpan.toFixed(2) + ')');
  ok(wkTimes.length >= 30, 'still renders times (' + wkTimes.length + ')');
  ok(wkDates.length === 2, 'one date per session (' + JSON.stringify(wkDates.map(o => o.l)) + ')');
  ok(dupDates(wk).length === 0, 'no date printed twice');

  console.log('\n[2] zoomed out (~6 sessions, markType=DayOfMonth): dates, never blank');
  lr = { from: 0, to: 149 };
  labels = sweep(0, 149, 2);
  const d2 = labels.filter(o => isDate(o.l));
  const t2 = labels.filter(o => isTime(o.l));
  ok(d2.length >= 6, 'every visible session is dated (' + d2.length + ': ' + JSON.stringify(d2.map(o => o.l)) + ')');
  ok(dupDates(labels).length === 0, 'no date printed twice (dupes: ' + JSON.stringify(dupDates(labels)) + ')');
  ok(t2.length === 0, 'no times mixed into the wide view (' + t2.length + ')');
  ok(labels.length >= 6, 'axis is not empty (' + labels.length + ' labels)');

  console.log('\n[3] a second render pass (tick index goes backwards) re-labels');
  // Same range, second pass from the left: this is the case where a per-frame
  // clear had already been consumed and the whole axis came out blank.
  const second = sweep(0, 149, 2);
  const secondDates = second.filter(o => isDate(o.l)).map(o => o.l);
  ok(secondDates.length >= 6,
     'second pass still dates every session (' + JSON.stringify(secondDates) + ')');
  ok(JSON.stringify(secondDates) === JSON.stringify(d2.map(o => o.l)),
     'second pass produces the same labels as the first');

  console.log('\n[4] a missing / stale day map cannot blank the axis');
  window._intradayDayFirstBarMap = null;
  lr = { from: 0, to: 149 };
  const noMap = sweep(0, 149, 2);
  ok(noMap.filter(o => isDate(o.l)).length >= 6,
     'dates still rendered without the day map (' + noMap.filter(o => isDate(o.l)).length + ')');
  window._intradayDayFirstBarMap = Object.assign({}, dayMap);

  console.log('\n[5] ticks past the last real bar stay unlabelled (no fabricated sessions)');
  ok(AX.tick(chart, (barTimes.length + 5) * BS, 2, '15m') === '',
     'an index past the bar map returns an empty label');

  console.log('\n[6] source guard: the whitespace runway stays bounded');
  const dash = fs.readFileSync(path.join(FRONTEND, 'dashboard.js'), 'utf8');
  ok(dash.indexOf('candleData.length + 3000') === -1, 'intraday pad is no longer +3000 bars');
  ok(/candleData\.length \+ 40;/.test(dash), 'intraday pad is bounded (candles + 40)');

  console.log('\n==================================================');
  console.log('  PASS: ' + pass + '   FAIL: ' + fail);
  console.log('==================================================');
  process.exit(fail ? 1 : 0);
}
main();
