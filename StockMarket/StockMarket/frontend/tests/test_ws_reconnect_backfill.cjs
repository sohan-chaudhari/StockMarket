/**
 * Issue-2 regression: after a WebSocket reconnect the chart must reconcile the
 * candles missed during the outage using the SAME architecture as a normal
 * chart load — loadData() -> /api/stock-data/intraday/paginated (server-side gap
 * detection + backfill) -> _renderChartData() -> _buildIntradayDisplay() (which
 * remaps real IST epochs to sequential bar indices).
 *
 * It must NOT repeat the old behaviour of fetching /api/stock-data/intraday/since
 * and merging raw epoch bars into the index-based _chartDataCache.
 */
const fs = require('fs');
const path = require('path');

let pass = 0, fail = 0;
function ok(cond, name) {
  if (cond) { pass++; console.log('  PASS ' + name); }
  else { fail++; console.log('  FAIL ' + name); }
}

const src = fs.readFileSync(path.join(__dirname, '..', 'dashboard.js'), 'utf8');

console.log('\n[1] locate the big-chart WS handler');
const start = src.indexOf('_bigChartWsHandler = function wsBigChart');
const end = src.indexOf("window.addEventListener('dashboard_price_update', window._bigChartWsHandler)");
ok(start >= 0 && end > start, 'found _bigChartWsHandler body');
const body = src.slice(start, end);

console.log('\n[2] reconnect path uses the existing reconciliation');
ok(body.indexOf("fetch('/api/stock-data/intraday/since") === -1,
   'no longer fetches /api/stock-data/intraday/since');
ok(body.indexOf('window.loadData(activeRange)') !== -1,
   'delegates to window.loadData(activeRange)');
ok(body.indexOf('combined.push(c)') === -1,
   'removed the raw-epoch merge into _chartDataCache');
ok(body.indexOf("fetch('/api/stock-data/candle/latest") === -1,
   'removed the epoch-domain forming-candle repair');

console.log('\n[3] gate: reconcile only when consecutive server ticks crossed a candle boundary');
ok(body.indexOf("(typeof serverTs === 'number') ? serverTs : Date.parse(serverTs)") !== -1,
   'normalizes serverTs (ISO string OR numeric ms) before any bucket math');
ok(body.indexOf('_bucketOf(_serverMs) !== _bucketOf(window._lastTickServerTs)') !== -1,
   'uses a server-timestamp session-aligned bucket-crossing test');
ok(body.indexOf('window._lastTickServerTs = _serverMs') !== -1,
   'stores the NORMALIZED millisecond value for the next comparison');
function crossed(nowMs, lastMs, barSecs) {
  const barMs = barSecs * 1000, IST = 5.5 * 3600 * 1000, SESS = (9 * 60 + 15) * 60000;
  const b = (ms) => {
    const dayStart = Math.floor((ms + IST) / 86400000) * 86400000 - IST;
    const sessStart = dayStart + SESS;
    const o = ms - sessStart;
    return o < 0 ? sessStart : sessStart + Math.floor(o / barMs) * barMs;
  };
  return b(nowMs) !== b(lastMs);
}
const M = 60 * 1000;
// 2026-10-07 12:07 IST == 06:37 UTC
const t = (h, mi) => Date.UTC(2026, 9, 7, h, mi);
ok(crossed(t(6, 39), t(6, 37), 300) === false, '2-min gap inside the same 5m bucket -> no reload');
ok(crossed(t(6, 41), t(6, 37), 300) === true, '4-min gap crossing a 5m boundary -> reconcile');
ok(crossed(t(6, 45), t(6, 37), 300) === true, '8-min gap (reported scenario) -> reconcile');
ok(crossed(t(6, 42), t(6, 37), 300) === true, 'exactly one 5m bar -> reconcile');
ok(crossed(t(6, 40), t(6, 39), 60) === true, '1-min bar crossed on a 1m chart -> reconcile');

console.log('\n[4] index-domain renderer intact (used by the delegated path)');
ok(src.indexOf('function _buildIntradayDisplay') !== -1, '_buildIntradayDisplay still present');
ok(src.indexOf('window._intradayBarTimes = realBars.map') !== -1, 'index lookup table still built');

console.log('\n==================================================');
console.log('  PASS: ' + pass + '   FAIL: ' + fail);
console.log('==================================================');
process.exit(fail ? 1 : 0);
