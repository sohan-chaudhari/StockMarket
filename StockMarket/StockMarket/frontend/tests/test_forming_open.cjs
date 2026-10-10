/**
 * Phase 3 regression: the live (forming) candle's OPEN must never be seeded from
 * an arbitrary page-load price (live.current). When the backend's authoritative
 * forming candle is not in the loaded data, the open falls back to the PREVIOUS
 * completed candle's close -- an approximation of the bucket's first trade price.
 *
 * Run: node tests/forming_open.test.cjs
 */
'use strict';
const fs = require('fs');
const path = require('path');
const assert = require('assert');

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
ok(src.indexOf('function _computeFormingOpen(') !== -1,
   'forming-open decision is a named, testable function');
ok(src.indexOf('_computeFormingOpen(_seed ? _seed.open : null') !== -1,
   'forming candle seeds its open via _computeFormingOpen(...)');
ok(!/live\.current\s*>\s*0\s*\?\s*live\.current\s*:/.test(src),
   'page-load price is no longer the open fallback');

// --- Behavioural check of the REAL extracted function ----------------------
const marker = 'function _computeFormingOpen(';
const start = src.indexOf(marker);
assert(start !== -1, '_computeFormingOpen not found');
let i = src.indexOf('{', start), depth = 0, end = -1;
for (; i < src.length; i++) {
  const ch = src[i];
  if (ch === '{') depth++;
  else if (ch === '}') { depth--; if (depth === 0) { end = i + 1; break; } }
}
assert(end !== -1, 'could not parse _computeFormingOpen body');
// eslint-disable-next-line no-eval
const computeFormingOpen = eval('(' + src.slice(start, end) + ')');

// 1. authoritative seed (backend forming candle) wins
eq(computeFormingOpen(101, false, 99, 98, 100), 101, 'authoritative seed wins');
eq(computeFormingOpen(101, true, 99, 98, 100), 101, 'seed wins on first bar too');
// 2. session's first bucket -> official day open
eq(computeFormingOpen(null, true, 99, 98, 100), 99, 'first bucket uses day open');
// 3. mid-session, no seed -> PREVIOUS CLOSE (not the page-load price)
eq(computeFormingOpen(null, false, 99, 98, 100), 98, 'mid-session uses previous close, not live.current');
// 4. live price only as a last resort
eq(computeFormingOpen(null, false, 99, 0, 100), 100, 'live last resort when no prev close');
eq(computeFormingOpen(null, false, 0, null, 100), 100, 'live last resort when prev close null');
// 5. zero/undefined seed is not authoritative
eq(computeFormingOpen(0, false, 0, 98, 100), 98, 'seed 0 falls through');
eq(computeFormingOpen(undefined, false, 0, 98, 100), 98, 'seed undefined falls through');
// 6. first bucket without a day open falls back to the previous close
eq(computeFormingOpen(null, true, 0, 98, 100), 98, 'first bucket without day open');

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
