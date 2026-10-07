/**
 * Issue-4 regression: adding the SAME indicator with the SAME parameters twice
 * must reuse the existing instance (one card), while instances with DIFFERENT
 * parameters must remain distinct.
 *
 * Loads the REAL indicators.js headlessly against a stubbed chart, then drives
 * IndicatorEngine.addIndicator() exactly like the "+" menu does.
 */
const fs = require('fs');
const path = require('path');
const H = require('./harness.cjs');

let pass = 0, fail = 0;
function ok(cond, name) {
  if (cond) { pass++; console.log('  PASS ' + name); }
  else { fail++; console.log('  FAIL ' + name); }
}

function fakeSeries() {
  return {
    setData() {}, update() {}, applyOptions() {}, setMarkers() {},
    priceScale() { return { applyOptions() {}, options() { return {}; } }; },
    options() { return {}; },
    createPriceLine() { return { applyOptions() {} }; }, removePriceLine() {},
    priceToCoordinate() { return 300; }, coordinateToPrice() { return 100; },
    data() { return []; }
  };
}

Object.assign(H.timeScale, {
  options: () => ({ barSpacing: 8 }),
  applyOptions() {}, width: () => 900, height: () => 26,
  timeToCoordinate: () => 100, coordinateToTime: () => 1700000000,
  scrollPosition: () => 0, scrollToPosition() {}, fitContent() {},
  zoomIn() {}, zoomOut() {}, setVisibleRange() {},
  subscribeVisibleTimeRangeChange() {}, unsubscribeVisibleTimeRangeChange() {},
  subscribeSizeChange() {}, unsubscribeSizeChange() {}
});
window.bigChart = {
  timeScale: () => H.timeScale,
  subscribeCrosshairMove() {}, unsubscribeCrosshairMove() {}, applyOptions() {},
  priceScale: () => ({ applyOptions() {}, options: () => ({ mode: 0 }) }),
  addCandlestickSeries: fakeSeries, addLineSeries: fakeSeries, addAreaSeries: fakeSeries,
  addHistogramSeries: fakeSeries, addBarSeries: fakeSeries, addBaselineSeries: fakeSeries,
  removeSeries() {}, paneSize: () => ({ height: 400 }), subscribeClick() {}
};
window.bigCandleSeries = fakeSeries();
window._chartCandles = H.bars;
window.chart = window.bigChart;

const _log = console.log; console.log = function () {};
try {
  (0, eval)(fs.readFileSync(path.join(__dirname, '..', 'indicators.js'), 'utf8'));
} catch (e) {
  console.log = _log;
  console.error('indicators.js LOAD FAILED:', e.message);
  process.exit(2);
}
console.log = _log;
const IE = window.IndicatorEngine;

console.log('\n[1] engine loaded');
ok(IE && typeof IE.addIndicator === 'function', 'IndicatorEngine.addIndicator available');

console.log('\n[2] overlay indicator (SMA) de-duplicates identical params');
const sma1 = IE.addIndicator('SMA');
const sma2 = IE.addIndicator('SMA');
ok(sma1 === sma2, 'second SMA (same default params) reuses the first instance id');
const sma3 = IE.addIndicator('SMA', { length: 50 });
ok(sma3 !== sma1, 'SMA with different length is a distinct instance');

console.log('\n[3] pane indicator (RSI) de-duplicates identical params');
const rsi1 = IE.addIndicator('RSI');
const rsi2 = IE.addIndicator('RSI');
ok(rsi1 === rsi2, 'second RSI (same default params) reuses the first instance id');
const rsi3 = IE.addIndicator('RSI', { length: 21 });
ok(rsi3 !== rsi1, 'RSI with different length is a distinct instance');

console.log('\n[4] source guard: dedupe call present in addIndicator');
const src = fs.readFileSync(path.join(__dirname, '..', 'indicators.js'), 'utf8');
ok(src.indexOf('_findIdenticalInstance(def.id, mergedParams)') !== -1,
   'addIndicator consults _findIdenticalInstance before creating an instance');

console.log('\n==================================================');
console.log('  PASS: ' + pass + '   FAIL: ' + fail);
console.log('==================================================');
process.exit(fail ? 1 : 0);
