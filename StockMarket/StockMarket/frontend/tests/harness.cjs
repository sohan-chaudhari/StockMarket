// Headless harness: stubs the browser surface so drawings.js can load in node
// and every registered tool can be instantiated and exercised for real.
function makeCtx() {
  const noop = () => {};
  const ctx = new Proxy({}, {
    get(t, k) {
      if (k === 'canvas') return { width: 1200, height: 600 };
      if (k === 'measureText') return () => ({ width: 40, actualBoundingBoxAscent: 8, actualBoundingBoxDescent: 2 });
      if (k === 'createLinearGradient' || k === 'createRadialGradient')
        return () => ({ addColorStop: noop });
      if (k === 'getImageData') return () => ({ data: new Uint8ClampedArray(4) });
      if (typeof k === 'string' && k.startsWith('__')) return undefined;
      return t[k] !== undefined ? t[k] : noop;
    },
    set(t, k, v) { t[k] = v; return true; }
  });
  return ctx;
}
global.CanvasRenderingContext2D = function () {};
global.CanvasRenderingContext2D.prototype = {};
global.Path2D = function () { return { addPath: () => {}, rect: () => {}, arc: () => {} }; };
global.Image = function () { return {}; };
global.DOMMatrix = function () { return { a:1,b:0,c:0,d:1,e:0,f:0 }; };
const DAY = 86400;
const bars = [];
for (let i = 0; i < 300; i++) {
  const base = 100 + Math.sin(i / 9) * 12;
  bars.push({ time: 1700000000 + i * DAY, open: base, high: base + 3, low: base - 3, close: base + 1, volume: 1000 + i });
}
const elStub = () => ({
  style: {}, classList: { add(){}, remove(){}, toggle(){}, contains(){return false;} },
  addEventListener(){}, removeEventListener(){}, appendChild(){}, removeChild(){},
  setAttribute(){}, getAttribute(){return null;}, getBoundingClientRect(){return {left:0,top:0,width:1200,height:600,right:1200,bottom:600};},
  querySelector(){return null;}, querySelectorAll(){return [];}, getContext(){return makeCtx();},
  children: [], dataset: {}, focus(){}, blur(){}, remove(){}, insertBefore(){}, contains(){return false;},
  cloneNode(){return elStub();}, value: '', textContent: '', innerHTML: ''
});
global.document = {
  createElement: () => elStub(), createElementNS: () => elStub(),
  getElementById: () => null, querySelector: () => null, querySelectorAll: () => [],
  addEventListener(){}, removeEventListener(){}, body: elStub(), documentElement: elStub(),
  activeElement: null, head: elStub()
};
try { Object.defineProperty(global, 'navigator', { value: { userAgent: 'node', maxTouchPoints: 0 }, configurable: true }); } catch (e) {}
global.localStorage = { _d:{}, getItem(k){return this._d[k]||null;}, setItem(k,v){this._d[k]=String(v);}, removeItem(k){delete this._d[k];}, clear(){this._d={};} };
global.requestAnimationFrame = cb => setTimeout(() => cb(Date.now()), 0);
global.cancelAnimationFrame = clearTimeout;
global.devicePixelRatio = 1;
global.getComputedStyle = () => ({ getPropertyValue: () => '' });

const timeScale = {
  logicalToCoordinate: l => (l - 0) * 6,
  coordinateToLogical: x => x / 6,
  getVisibleLogicalRange: () => ({ from: 0, to: 200 }),
  getVisibleRange: () => ({ from: bars[0].time, to: bars[199].time }),
  setVisibleLogicalRange(){}, subscribeVisibleLogicalRangeChange(){}, unsubscribeVisibleLogicalRangeChange(){}
};
const series = {
  priceToCoordinate: p => 600 - (p - 60) * 5,
  coordinateToPrice: y => 60 + (600 - y) / 5,
  setData(){}, update(){}, applyOptions(){}, options: () => ({})
};
global.window = global;
global.window.bigChart = { timeScale: () => timeScale, subscribeCrosshairMove(){}, applyOptions(){}, priceScale: () => ({ applyOptions(){}, options: () => ({ mode: 0 }) }) };
global.window.bigCandleSeries = series;
global.window._chartCandles = bars;
global.window.addEventListener = () => {};
global.window.removeEventListener = () => {};
global.window.console = console;
module.exports = { bars, makeCtx, timeScale, series };
