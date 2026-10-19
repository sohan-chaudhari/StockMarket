/**
 * CRIT-01 regression: "candles appear in two positions / duplicated", worst when
 * switching timeframe.
 *
 * Root cause (proven on production with a Proxy installed on the LIVE forming
 * candle, capturing the stack):
 *
 *   1. dashboard.js builds the daily forming candle with a STRING key:
 *        window._formingCandles['d'] = { time: '2026-10-19', ... }
 *   2. it calls bigCandleSeries.update(fc) -- passing the app's OWN object.
 *   3. Lightweight Charts rewrites `time` IN PLACE on that object, turning the
 *      string into a BusinessDay object {year, month, day} (stack:
 *      Kn -> preprocessData -> update inside lightweight-charts.js).
 *   4. the guard `window._formingCandles['d'].time !== periodStr` then compared
 *      an OBJECT against a STRING, so it was true on every following tick.
 *   5. the code therefore believed the still-forming candle had "just completed"
 *      and pushed a copy into _chartDataCache on EVERY tick. Measured: 1317
 *      duplicate copies of a single day for NIFTY, stored under two different
 *      time representations.
 *   6. any later re-render of that polluted cache (a timeframe switch re-renders
 *      it via the memory-preview path) draws the same candle more than once, in
 *      more than one place.
 *
 * The intraday branch was unaffected only because its guard uses the primitive
 * `realTime` rather than `.time`.
 *
 * The fix: key the forming candle on an immutable primitive (`periodKey`), never
 * hand the library an app-owned object, and dedupe against a NORMALISED time so
 * a string and a BusinessDay for the same day cannot both be stored.
 */
const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function ok(c, n) { if (c) { pass++; console.log('  PASS ' + n); } else { fail++; console.log('  FAIL ' + n); } }

const DASH = path.join(__dirname, '..', 'dashboard.js');
const src = fs.readFileSync(DASH, 'utf8');

// line-based extraction of a top-level function, validated by compiling it
function extractFn(source, name) {
  const lines = source.split('\n');
  const startIdx = lines.findIndex((l) => l.startsWith('function ' + name + '('));
  if (startIdx < 0) throw new Error('function not found: ' + name);
  for (let i = startIdx + 1; i < lines.length; i++) {
    if (/^\}\s*$/.test(lines[i])) {
      const body = lines.slice(startIdx, i + 1).join('\n');
      new Function(body + '\nreturn ' + name + ';');
      return body;
    }
  }
  throw new Error('unterminated: ' + name);
}

const normBarTimeKey = new Function(extractFn(src, '_normBarTimeKey') + '\nreturn _normBarTimeKey;')();

// ---- 1. the normaliser collapses every accepted representation ----------------
ok(normBarTimeKey('2026-10-19') === '2026-10-19', 'a date string normalises to itself');
ok(normBarTimeKey({ year: 2026, month: 10, day: 19 }) === '2026-10-19',
   'a BusinessDay object normalises to the same string as the date');
ok(normBarTimeKey({ year: 2026, month: 1, day: 5 }) === '2026-01-05', 'month/day are zero-padded');
ok(normBarTimeKey(98700) === '98700', 'an intraday index is passed through');
ok(normBarTimeKey(null) === '' && normBarTimeKey(undefined) === '', 'null/undefined are safe');
ok(normBarTimeKey('2026-10-19') === normBarTimeKey({ year: 2026, month: 10, day: 19 }),
   'the SAME day in both representations compares equal (the whole point)');

// ---- 2. the exact production sequence ----------------------------------------
{
  // the app creates the forming daily candle (as the source does)
  const periodStr = '2026-10-19';
  const forming = { time: periodStr, periodKey: String(periodStr), open: 100, high: 101, low: 99, close: 100.5 };
  const cache = [{ time: '2026-10-18', open: 1, high: 1, low: 1, close: 1, volume: 1 },
                 { time: periodStr,     open: 1, high: 1, low: 1, close: 1, volume: 1 }];

  // emulate what lightweight-charts provably does to the object you pass to update()
  forming.time = { year: 2026, month: 10, day: 19 };

  // OLD guard: object !== string -> true on every tick -> duplicate pushed
  const oldGuardWouldRefire = (forming.periodKey === undefined)
    ? (forming.time !== periodStr) : (forming.time !== periodStr);
  ok(oldGuardWouldRefire === true,
     'the OLD guard compared an object with a string and re-fired on every tick (documents the bug)');

  // NEW guard: an immutable primitive, immune to the library's rewrite
  ok(forming.periodKey === String(periodStr),
     'the NEW periodKey guard still matches after the library rewrote .time -> no false "just completed"');

  // NEW dedupe: normalised, so the same day cannot be stored twice
  const lastKey = normBarTimeKey(cache[cache.length - 1].time);
  const completedKey = normBarTimeKey(forming.time);
  ok(lastKey === completedKey, 'the dedupe key sees the cached string and the mutated object as the same day');
  ok(lastKey === '2026-10-19' && completedKey === '2026-10-19', 'both normalise to the dated key');

  // and a genuinely NEW day must still be accepted (no over-deduping)
  const nextKey = normBarTimeKey('2026-10-20');
  ok(nextKey !== lastKey, 'a different day is still appended (the dedupe is not over-broad)');
}

// ---- 3. the fix must stay in the shipped source -------------------------------
{
  // Whole-line comments are dropped: the fix's own explanation quotes the old
  // guard verbatim, and a naive contains() would flag that documentation.
  const codeOnly = src.split('\n').filter((l) => !l.trim().startsWith('//')).join('\n');

  ok(/_normBarTimeKey\s*\(/.test(codeOnly), 'source: _normBarTimeKey exists');
  ok(codeOnly.indexOf('_formingCandles[\'d\'].periodKey !== _periodKey') !== -1,
     'source: the daily/weekly/monthly guard compares the immutable periodKey');
  ok(codeOnly.indexOf('_formingCandles[\'d\'].time !== periodStr') === -1,
     'source: the old .time-vs-string guard is gone (comments excluded)');

  // no app-owned object may be handed to the charting library's update()
  const bareUpdate = /\.update\(\s*(fc|candle|bar|justCompleted[A-Za-z]*)\s*\)/.exec(codeOnly);
  ok(bareUpdate === null,
     'source: no .update(<app object>) call remains (the library mutates what it is given)'
     + (bareUpdate ? ' -- found ' + bareUpdate[0] : ''));

  const updateCalls = codeOnly.match(/bigCandleSeries\.update\(/g) || [];
  ok(updateCalls.length === 2, 'source: both candlestick update() call sites are present (intraday + daily)');
  ok(codeOnly.indexOf('bigCandleSeries.update({ time: fc.time') !== -1,
     'source: the daily update passes a fresh literal');
  ok(/_normBarTimeKey\(_chartDataCache\[_chartDataCache\.length - 1\]\.time\)/.test(codeOnly),
     'source: the just-completed push is deduped on the normalised key');
}

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
