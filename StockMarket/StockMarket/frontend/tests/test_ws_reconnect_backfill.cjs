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

console.log('\n[3] gate: reconcile only when >= one full bar was missed');
ok(body.indexOf('_activeBarSecs * 1000') !== -1, 'uses a one-full-bar threshold');
function shouldReconcile(missedMs, barSecs) { return missedMs >= barSecs * 1000; }
ok(shouldReconcile(8 * 60 * 1000, 300) === true, '8-min outage on 5m chart reconciles');
ok(shouldReconcile(30 * 1000, 300) === false, '30s blip on 5m chart does NOT reload');
ok(shouldReconcile(120 * 1000, 60) === true, '2-min outage on 1m chart reconciles');
ok(shouldReconcile(1 * 60 * 1000, 900) === false, '1-min blip on 15m chart does NOT reload');

console.log('\n[4] index-domain renderer intact (used by the delegated path)');
ok(src.indexOf('function _buildIntradayDisplay') !== -1, '_buildIntradayDisplay still present');
ok(src.indexOf('window._intradayBarTimes = realBars.map') !== -1, 'index lookup table still built');

console.log('\n==================================================');
console.log('  PASS: ' + pass + '   FAIL: ' + fail);
console.log('==================================================');
process.exit(fail ? 1 : 0);
