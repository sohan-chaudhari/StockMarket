/**
 * BUG-1 regression: the live (forming) intraday candle's OPEN must be the
 * first traded price of THAT bucket. Only the first bucket of the trading day
 * may open at the official day open.
 *
 * Root cause being guarded: for intraday ranges dashboard.js keeps
 * _chartDataCache in SEQUENTIAL bar time (index * barSecs), NOT epoch seconds,
 * so the previous "is the last cached bar from today?" date comparison always
 * evaluated to true and every 5m/15m/30m/1h forming candle opened at the 09:15
 * day open. The fix compares the live bucket to the session start instead.
 *
 * Run: node tests/test_live_candle_first_bar.cjs
 */
'use strict';
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, '..', 'dashboard.js'), 'utf8');

let pass = 0, fail = 0;
function ok(cond, name) {
  if (cond) { pass++; console.log('  PASS ' + name); }
  else { fail++; console.log('  FAIL ' + name); }
}
function eq(actual, expected, name) {
  ok(Object.is(actual, expected), name + (Object.is(actual, expected) ? '' : ' (expected ' + expected + ', got ' + actual + ')'));
}

// --- Source-level guards (regression protection) ---------------------------
ok(src.indexOf('var _isFirstBar = (snappedMs === sessionStartMs);') !== -1,
   'first-bar is detected via session-start comparison');
ok(!/_chartDataCache\[_chartDataCache\.length - 1\]\.time\s*\+\s*\(5\.5 \* 3600\)/.test(src),
   'old sequential-time date comparison has been removed');
ok(src.indexOf('var _seed = (_chartDataCache && _chartDataCache.length > 0') !== -1,
   'forming candle seeds from the backend-merged current bucket when present');

// --- Behavioural check of the defect the fix addresses ---------------------
// Reproduces the OLD comparison exactly: given a sequential bar time it always
// reports "first bar" because the resulting date is in 1970.
function oldIsFirstBar(sequentialLastTime) {
  const lastDate = new Date((sequentialLastTime + (5.5 * 3600)) * 1000).toISOString().slice(0, 10);
  const todayStr = new Date(Date.now() + 5.5 * 3600000).toISOString().slice(0, 10);
  return lastDate !== todayStr;
}
// Reproduces the NEW comparison: session-anchored 09:15 IST buckets.
function newIsFirstBar(snappedMs, sessionStartMs) { return snappedMs === sessionStartMs; }

const SESSION_START_MIN = 9 * 60 + 15;
const IST_OFFSET_MS = 5.5 * 3600 * 1000;
const utcMidnight = Date.UTC(2026, 9, 6, 0, 0, 0);            // arbitrary trading day
const sessionStartMs = (utcMidnight - IST_OFFSET_MS) + SESSION_START_MIN * 60000;

const bucket0915 = sessionStartMs;
const bucket1200 = sessionStartMs + (12 * 60 - SESSION_START_MIN) * 60000;
const bucket1555 = sessionStartMs + (15 * 60 + 25 - SESSION_START_MIN) * 60000;

eq(oldIsFirstBar(149 * 300), true, 'OLD logic wrongly reports first-bar mid-session');
eq(newIsFirstBar(bucket0915, sessionStartMs), true, 'NEW logic: 09:15 bucket is the first bar');
eq(newIsFirstBar(bucket1200, sessionStartMs), false, 'NEW logic: 12:00 bucket is NOT the first bar');
eq(newIsFirstBar(bucket1555, sessionStartMs), false, 'NEW logic: 15:25 bucket is NOT the first bar');

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
