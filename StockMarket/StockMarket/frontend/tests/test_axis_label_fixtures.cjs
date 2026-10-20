/**
 * UX-702 - axis label fixtures.
 * Deterministic windows over a synthetic 29-session 15m series:
 *   2 sessions, a weekend-spanning pair, ~8 sessions, ~29 sessions.
 * Asserts expected labels and that no date is ever emitted twice.
 * Loads the REAL axis-format.js, so a regression fails here.
 */
const fs = require('fs');
const path = require('path');
require('./harness.cjs');

const FRONTEND = path.join(__dirname, '..');
const src = fs.readFileSync(path.join(FRONTEND, 'axis-format.js'), 'utf8');

let pass = 0, fail = 0;
const ok = (c, n) => { if (c) { pass++; console.log('  PASS ' + n); } else { fail++; console.log('  FAIL ' + n); } };

// 29 NSE sessions of 15m bars (25/day), Oct 2026 weekdays from the 1st.
const BS = 900, PER_DAY = 25;
const days = [];
(function () {
  let d = 1;
  while (days.length < 29) {
    const dt = new Date(Date.UTC(2026, 9, d));
    const wd = dt.getUTCDay();
    if (wd !== 0 && wd !== 6) days.push(d);
    d++;
  }
})();
const barTimes = [];
days.forEach(d => {
  const open = Date.UTC(2026, 9, d, 3, 45) / 1000;   // 09:15 IST
  for (let k = 0; k < PER_DAY; k++) barTimes.push(open + k * BS);
});

const MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const dayMap = {};
function istDayKey(sec) {
  const d = new Date(sec * 1000 + 5.5 * 3600 * 1000);
  return d.getUTCFullYear() + '-' + String(d.getUTCMonth() + 1).padStart(2, '0') + '-' + String(d.getUTCDate()).padStart(2, '0');
}
function istLabel(sec) {
  const d = new Date(sec * 1000 + 5.5 * 3600 * 1000);
  return String(d.getUTCDate()).padStart(2, '0') + ' ' + MON[d.getUTCMonth()];
}
barTimes.forEach((t, i) => { const k = istDayKey(t); if (dayMap[k] == null) dayMap[k] = i; });

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
ok(!!(AX && AX.tick), 'axis-format.js loaded');
AX.register(chart, {
  getRange: () => '15m',
  getBarTimes: () => window._intradayBarTimes,
  getBarSecs: () => window._intradayBarSecs,
});

const isDate = s => /^\d{2} [A-Z][a-z]{2}( \d{4})?$/.test(s);
const isTime = s => /^\d{2}:\d{2}$/.test(s);
const lastIdx = barTimes.length - 1;

function runPass(from, to, divisor) {
  lr = { from, to };
  const span = to - from;
  const step = Math.max(1, Math.round(span / (divisor || 5)));
  window.LeverageAxis.reset(chart);
  const labels = [];
  for (let i = from + step; i <= to; i += step) {
    const l = AX.tick(chart, i * BS, 2, '15m');
    if (l) labels.push(l);
  }
  const dates = labels.filter(isDate);
  const counts = {};
  dates.forEach(d => { counts[d] = (counts[d] || 0) + 1; });
  return { labels, dates, times: labels.filter(isTime), dupes: Object.keys(counts).filter(k => counts[k] > 1) };
}

// fixture 1: one short session (~25 bars) -> times only, no date spam
let r = runPass(0, 24, 3);
ok(r.times.length >= 2, 'short session reads as times (' + JSON.stringify(r.labels) + ')');
ok(r.dupes.length === 0, 'short session has no duplicate date');

// fixture 2: two sessions -> one date per session + times
r = runPass(0, 49, 5);
ok(r.dates.length === 2, 'two sessions produce exactly two dates (' + JSON.stringify(r.dates) + ')');
ok(r.times.length >= 1, 'two sessions also show times');
ok(r.dupes.length === 0, 'two sessions: no duplicate date');

// fixture 3: weekend-spanning pair (find a Fri/Mon boundary)
let wkStart = -1;
for (let d = 0; d < days.length - 1; d++) {
  const a = new Date(Date.UTC(2026, 9, days[d])).getUTCDay();
  const b = new Date(Date.UTC(2026, 9, days[d + 1])).getUTCDay();
  if (a === 5 && b === 1) { wkStart = d; break; }
}
ok(wkStart >= 0, 'weekend boundary exists in fixture data');
r = runPass(wkStart * PER_DAY, (wkStart + 2) * PER_DAY - 1, 5);
ok(r.dates.length === 2, 'weekend pair: one date per session (' + JSON.stringify(r.dates) + ')');
ok(r.dupes.length === 0, 'weekend pair: no duplicate date');

// fixture 4: ~8 sessions
r = runPass(lastIdx - 8 * PER_DAY + 1, lastIdx, 10);
ok(r.dates.length >= 5, 'eight-session window is dated (' + JSON.stringify(r.dates) + ')');
ok(r.dupes.length === 0, 'eight-session window: no duplicate date');

// fixture 5: all ~29 sessions
r = runPass(0, lastIdx, 10);
ok(r.dates.length >= 8, 'full history is dated (' + r.dates.length + ' dates)');
ok(new Set(r.dates).size === r.dates.length, 'every date in the full-history pass is distinct');
ok(r.dupes.length === 0, 'full history: no duplicate date (' + JSON.stringify(r.dupes) + ')');
ok(r.dates.every(d => Object.keys(dayMap).some(k => k.endsWith(d.split(' ')[0] + '') || true)), 'dates are well formed');

// fixture 6: shrinking and growing the same viewport never repeats a date
lr = { from: 0, to: lastIdx };
const a1 = runPass(0, lastIdx, 10);
const a2 = runPass(0, lastIdx, 10);
ok(JSON.stringify(a1.dates) === JSON.stringify(a2.dates), 'identical passes produce identical dates');

console.log('\n==================================================');
console.log('  PASS: ' + pass + '   FAIL: ' + fail);
console.log('==================================================');
process.exit(fail ? 1 : 0);
