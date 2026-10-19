/**
 * CRIT-02 regression: the client's follow-up on a `backfilling` intraday chart.
 *
 * Reported: opening a chart on 5m showed a gap; switching to 15m appeared to
 * "backfill" it; switching back to 5m showed no gap.
 *
 * The backend repairs 5m synchronously when the provider allows it. When the
 * provider is rate-limited the work falls to the background worker, which can
 * take a minute or more -- and the endpoint keeps reporting `backfill_status:
 * "backfilling"` until that ticker's 5m job settles (main.py computes it from
 * fill_needed / job_queued / has_open_job).
 *
 * The client's follow-up used to be 2 polls at a flat 3.5s (~7s), so it gave up
 * long before the worker finished: the chart kept showing the gap until something
 * else re-triggered a load. Reopening the ticker, or switching timeframe and
 * back, re-ran the load -- which is why that appeared to be the thing that fixed
 * it. The follow-up now backs off over a bounded window and stops as soon as the
 * backend stops reporting backfilling.
 *
 * Source-level assertions (the poller is inline in a large closure, so it cannot
 * be extracted and driven in node), matching the style already used for frontend
 * invariants in this suite.
 */
const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function ok(c, n) { if (c) { pass++; console.log('  PASS ' + n); } else { fail++; console.log('  FAIL ' + n); } }

const src = fs.readFileSync(path.join(__dirname, '..', 'dashboard.js'), 'utf8');
// drop whole-line comments so quoted old code in the fix's explanation cannot match
const codeOnly = src.split('\n').filter((l) => !l.trim().startsWith('//')).join('\n');

// ---- the schedule -------------------------------------------------------------
const schedMatch = codeOnly.match(/var _bgPollDelays = \[([0-9,\s]+)\];/);
ok(schedMatch !== null, 'the follow-up uses a backoff schedule');

let delays = [];
if (schedMatch) delays = schedMatch[1].split(',').map((n) => parseInt(n.trim(), 10)).filter((n) => !isNaN(n));
ok(delays.length >= 4, 'the schedule has more than the old 2 attempts (' + delays.length + ' polls)');
ok(delays.every ((d, i) => i === 0 || d >= delays[i - 1]), 'the delays are non-decreasing (a backoff, not a hammer): ' + JSON.stringify(delays));
const totalMs = delays.reduce((a, b) => a + b, 0);
ok(totalMs >= 60000, 'the follow-up window is long enough for a worker repair (' + Math.round(totalMs / 1000) + 's)');
ok(totalMs <= 180000, 'the follow-up window stays bounded (' + Math.round(totalMs / 1000) + 's, no unbounded polling)');

// ---- how it is driven --------------------------------------------------------
ok(codeOnly.indexOf('_bgPollDelays[Math.min(_bgPollCount - 1, _bgPollDelays.length - 1)]') !== -1,
   'each poll waits the scheduled delay instead of a flat timeout');
ok(codeOnly.indexOf("if (refStatus === 'backfilling' && _bgPollCount < _bgPollDelays.length)") !== -1,
   'the follow-up continues only while the backend still reports backfilling, and stays bounded');
ok(codeOnly.indexOf('_bgPollCount < 2') === -1,
   'the old 2-attempt bound is gone');
ok(codeOnly.indexOf('}, 3500);') === -1,
   'the old flat 3500ms timeout is gone');

// ---- the guards that must survive -------------------------------------------
ok(codeOnly.indexOf('if (myLoadId !== _loadId || window._pendingRange) return;') !== -1,
   'a stale follow-up still aborts when a newer load owns the chart');
ok(codeOnly.indexOf("if (backfillStatus === 'backfilling' && isIntradayRange && !_retryAttempt)") !== -1,
   'the follow-up is still only started for a backfilling intraday load');
ok(codeOnly.indexOf('if (!refreshed || myLoadId !== _loadId) return;') !== -1,
   'a late response cannot be applied to a chart that moved on');

// ---- the merge itself must be unchanged (no zoom/pan reset) -------------------
ok(codeOnly.indexOf('_renderChartData(refData, range);') !== -1, 'repaired bars are still merged via _renderChartData');
ok(codeOnly.indexOf('from: prevLogical.from + addedBars,') !== -1,
   'the viewport is still preserved by shifting with the number of added bars');
ok(codeOnly.indexOf('processBigChartPrice({ current: window.lastLivePrice }, ticker);') !== -1,
   'the live forming candle is still re-applied after a merge');

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
