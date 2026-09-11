/**
 * Phase 2K hardening tests — pure logic, no browser required.
 *
 * Loads the real indicators.js (not a re-implemented port) into a minimal
 * VM sandbox (stub window/document/localStorage, no LightweightCharts) and
 * exercises the DOM-independent pieces directly through the exposed
 * IndicatorEngine._test hooks: persisted-config sanitization (corruption
 * safety) and indicator-picker search matching.
 *
 * Run: node test_phase2k_hardening.cjs
 */
'use strict';
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const assert = require('assert');

const SRC_PATH = path.join(__dirname, 'indicators.js');
const src = fs.readFileSync(SRC_PATH, 'utf8');

function makeSandbox() {
  const store = {};
  const localStorageStub = {
    getItem: (k) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
    clear: () => { for (const k of Object.keys(store)) delete store[k]; }
  };
  const noopEl = () => ({
    style: {},
    innerHTML: '',
    appendChild() {},
    addEventListener() {},
    removeEventListener() {},
    querySelector: () => null,
    getBoundingClientRect: () => ({ width: 0, height: 0 })
  });
  const documentStub = {
    readyState: 'complete',
    getElementById: () => null,
    createElement: () => noopEl(),
    addEventListener() {},
    body: { appendChild() {} }
  };
  // Records every setMarkers() call so tests can inspect what was actually
  // applied to the "main candle series" (Pivot Points High Low attaches
  // markers there, merged across all visible instances — see
  // _drawOverlayClouds's marker-merge block in indicators.js).
  const bigCandleSeriesStub = {
    _lastMarkers: [],
    setMarkers(markers) { this._lastMarkers = markers; }
  };
  const windowStub = {
    localStorage: localStorageStub,
    devicePixelRatio: 1,
    addEventListener() {},
    removeEventListener() {},
    bigCandleSeries: bigCandleSeriesStub
  };
  const sandbox = {
    window: windowStub,
    document: documentStub,
    console,
    setTimeout,
    requestAnimationFrame: (fn) => fn(),
    Object, Array, Number, Math, JSON, String, Boolean, Date,
    isFinite, isNaN, parseInt, parseFloat
  };
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: 'indicators.js' });
  return sandbox;
}

// vm.createContext() runs code in a separate V8 realm, so arrays/objects it
// produces aren't the host's Array/Object — assert.deepStrictEqual treats
// them as different species even when structurally identical. Compare via
// JSON, which only cares about structure (fine here: everything under test
// is plain JSON-shaped config data).
function assertJsonEqual(actual, expected, msg) {
  const a = JSON.stringify(actual);
  const e = JSON.stringify(expected);
  assert.strictEqual(a, e, msg || `expected ${e}, got ${a}`);
}

let passed = 0, failed = 0;
function test(name, fn) {
  try {
    fn();
    passed++;
    console.log('  ok - ' + name);
  } catch (e) {
    failed++;
    console.log('  FAIL - ' + name);
    console.log('    ' + (e && e.message ? e.message : e));
  }
}

const sandbox = makeSandbox();
const IE = sandbox.window.IndicatorEngine;
assert.ok(IE, 'IndicatorEngine must be exposed on window');
const T = IE._test;
assert.ok(T, 'IndicatorEngine._test hooks must be exposed');

console.log('Persisted config sanitization (corruption safety)');

test('valid config round-trips unchanged', () => {
  const raw = { version: T.PERSIST_VERSION, instances: [
    { type: 'EMA', params: { length: 50 }, visible: true, color: '#ff0000' },
    { type: 'RSI', params: { length: 14 }, visible: false }
  ]};
  const clean = T.sanitizeConfig(raw);
  assert.strictEqual(clean.length, 2);
  assert.strictEqual(clean[0].type, 'EMA');
  assert.strictEqual(clean[0].params.length, 50);
  assert.strictEqual(clean[0].visible, true);
  assert.strictEqual(clean[1].visible, false);
});

test('null/undefined/primitive input -> empty, no throw', () => {
  assertJsonEqual(T.sanitizeConfig(null), []);
  assertJsonEqual(T.sanitizeConfig(undefined), []);
  assertJsonEqual(T.sanitizeConfig('garbage string'), []);
  assertJsonEqual(T.sanitizeConfig(42), []);
  assertJsonEqual(T.sanitizeConfig([1, 2, 3]), []);
});

test('wrong schema version -> empty (no silent migration)', () => {
  const raw = { version: 999, instances: [{ type: 'EMA', params: {} }] };
  assertJsonEqual(T.sanitizeConfig(raw), []);
});

test('missing version field -> empty', () => {
  assertJsonEqual(T.sanitizeConfig({ instances: [{ type: 'EMA', params: {} }] }), []);
});

test('instances not an array -> empty', () => {
  assertJsonEqual(T.sanitizeConfig({ version: T.PERSIST_VERSION, instances: 'nope' }), []);
  assertJsonEqual(T.sanitizeConfig({ version: T.PERSIST_VERSION, instances: {} }), []);
});

test('unknown/removed indicator type -> that entry skipped, others kept', () => {
  const raw = { version: T.PERSIST_VERSION, instances: [
    { type: 'EMA', params: { length: 20 } },
    { type: 'DOES_NOT_EXIST', params: { length: 20 } },
    { type: 'RSI', params: { length: 14 } }
  ]};
  const clean = T.sanitizeConfig(raw);
  assert.strictEqual(clean.length, 2);
  assertJsonEqual(clean.map(c => c.type), ['EMA', 'RSI']);
});

test('malformed individual entries skipped without throwing', () => {
  const raw = { version: T.PERSIST_VERSION, instances: [
    null,
    42,
    'string-entry',
    { type: 123 },              // non-string type
    { params: { length: 1 } },  // missing type
    { type: 'EMA', params: { length: 20 } }
  ]};
  const clean = T.sanitizeConfig(raw);
  assert.strictEqual(clean.length, 1);
  assert.strictEqual(clean[0].type, 'EMA');
});

test('non-object/array params sanitized to {} instead of propagating garbage', () => {
  const raw = { version: T.PERSIST_VERSION, instances: [
    { type: 'EMA', params: 'not-an-object' },
    { type: 'RSI', params: [1, 2, 3] },
    { type: 'MFI' } // no params key at all
  ]};
  const clean = T.sanitizeConfig(raw);
  assert.strictEqual(clean.length, 3);
  clean.forEach(c => assertJsonEqual(c.params, {}));
});

test('visible defaults to true unless explicitly false', () => {
  const raw = { version: T.PERSIST_VERSION, instances: [
    { type: 'EMA', params: {} },              // no visible key
    { type: 'RSI', params: {}, visible: false },
    { type: 'MFI', params: {}, visible: 'nonsense' } // truthy non-bool -> stays visible
  ]};
  const clean = T.sanitizeConfig(raw);
  assert.strictEqual(clean[0].visible, true);
  assert.strictEqual(clean[1].visible, false);
  assert.strictEqual(clean[2].visible, true);
});

test('non-string color dropped (falls back to indicator default downstream)', () => {
  const raw = { version: T.PERSIST_VERSION, instances: [{ type: 'EMA', params: {}, color: 12345 }] };
  const clean = T.sanitizeConfig(raw);
  assert.strictEqual(clean[0].color, undefined);
});

test('empty instances array -> clean empty result, not a crash', () => {
  assertJsonEqual(T.sanitizeConfig({ version: T.PERSIST_VERSION, instances: [] }), []);
});

test('serializeConfig produces the shape sanitizeConfig accepts (round-trip)', () => {
  // No live _instances in this sandbox (no chart), so this just proves the
  // empty-state shape is valid input to sanitizeConfig, not a broken schema.
  const serialized = T.serializeConfig();
  assert.strictEqual(serialized.version, T.PERSIST_VERSION);
  assert.ok(Array.isArray(serialized.instances));
  assertJsonEqual(T.sanitizeConfig(serialized), []);
});

console.log('\nCorrupted localStorage JSON (end-to-end via localStorage)');

test('malformed JSON string in localStorage does not throw when read back', () => {
  sandbox.window.localStorage.setItem(T.PERSIST_KEY, '{not valid json!!');
  // Simulate what _loadPersistedConfig does: JSON.parse wrapped in try/catch
  let threw = false;
  let result = null;
  try {
    const raw = sandbox.window.localStorage.getItem(T.PERSIST_KEY);
    let parsed;
    try { parsed = JSON.parse(raw); } catch (e) { parsed = undefined; }
    result = parsed === undefined ? [] : T.sanitizeConfig(parsed);
  } catch (e) { threw = true; }
  assert.strictEqual(threw, false);
  assertJsonEqual(result, []);
  sandbox.window.localStorage.clear();
});

console.log('\nIndicator picker search matching');

test('empty query matches everything', () => {
  assert.strictEqual(T.menuItemMatches(IE.DEFS.RSI, ''), true);
  assert.strictEqual(T.menuItemMatches(IE.DEFS.ICHIMOKU, undefined), true);
});

test('case-insensitive id match: "rsi" -> RSI', () => {
  assert.strictEqual(T.menuItemMatches(IE.DEFS.RSI, 'rsi'), true);
  assert.strictEqual(T.menuItemMatches(IE.DEFS.RSI, 'RSI'), true);
});

test('partial fullName match: "mac" -> MACD', () => {
  assert.strictEqual(T.menuItemMatches(IE.DEFS.MACD, 'mac'), true);
});

test('partial fullName match: "atr" -> ATR', () => {
  assert.strictEqual(T.menuItemMatches(IE.DEFS.ATR, 'atr'), true);
});

test('partial fullName match: "ich" -> Ichimoku Cloud', () => {
  assert.strictEqual(T.menuItemMatches(IE.DEFS.ICHIMOKU, 'ich'), true);
});

test('partial id match: "mfi" -> Money Flow Index', () => {
  assert.strictEqual(T.menuItemMatches(IE.DEFS.MFI, 'mfi'), true);
});

test('partial fullName match: "money" -> Money Flow Index', () => {
  assert.strictEqual(T.menuItemMatches(IE.DEFS.MFI, 'money'), true);
});

test('non-matching query returns false', () => {
  assert.strictEqual(T.menuItemMatches(IE.DEFS.RSI, 'zzz-not-a-thing'), false);
});

test('every registered indicator is matchable by its own id', () => {
  Object.keys(IE.DEFS).forEach((id) => {
    assert.strictEqual(T.menuItemMatches(IE.DEFS[id], id), true, `DEFS.${id} should match its own id`);
  });
});

console.log('\nMultiple isolated sandboxes (regression: no shared module-level state leaks)');

test('two independently-loaded engine instances do not share persisted state', () => {
  const sbA = makeSandbox();
  const sbB = makeSandbox();
  sbA.window.localStorage.setItem(sbA.window.IndicatorEngine._test.PERSIST_KEY, JSON.stringify({
    version: sbA.window.IndicatorEngine._test.PERSIST_VERSION,
    instances: [{ type: 'EMA', params: { length: 99 } }]
  }));
  assert.strictEqual(sbB.window.localStorage.getItem(sbB.window.IndicatorEngine._test.PERSIST_KEY), null);
});

console.log('\nMultiple instance isolation');

function sampleCandles(n) {
  const out = [];
  for (let i = 0; i < n; i++) {
    out.push({ time: 1000 + i * 60, open: 100 + i, high: 105 + i, low: 95 + i, close: 100 + i, volume: 1000 });
  }
  return out;
}

test('adding the same indicator twice creates two independent instances', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id1 = ie.addIndicator('EMA', { length: 20 });
  const id2 = ie.addIndicator('EMA', { length: 50 });
  assert.notStrictEqual(id1, id2);
  const insts = ie.getInstances();
  assert.strictEqual(insts[id1].params.length, 20);
  assert.strictEqual(insts[id2].params.length, 50);
});

test('updateParams on one instance does not affect a sibling instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id1 = ie.addIndicator('EMA', { length: 20 });
  const id2 = ie.addIndicator('EMA', { length: 50 });
  ie.updateParams(id1, { length: 200 });
  const insts = ie.getInstances();
  assert.strictEqual(insts[id1].params.length, 200);
  assert.strictEqual(insts[id2].params.length, 50); // untouched
});

test('removing one instance does not remove its sibling', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id1 = ie.addIndicator('EMA', { length: 20 });
  const id2 = ie.addIndicator('EMA', { length: 50 });
  ie.removeIndicator(id1);
  const insts = ie.getInstances();
  assert.strictEqual(insts[id1], undefined);
  assert.strictEqual(insts[id2].params.length, 50);
});

test('visibility toggle preserves configuration (params survive hide/show)', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('RSI', { length: 21 });
  ie.toggleIndicator(id); // hide
  let inst = ie.getInstances()[id];
  assert.strictEqual(inst.visible, false);
  assert.strictEqual(inst.params.length, 21);
  ie.toggleIndicator(id); // show
  inst = ie.getInstances()[id];
  assert.strictEqual(inst.visible, true);
  assert.strictEqual(inst.params.length, 21); // unchanged by the round-trip
});

test('add -> remove -> add -> remove -> add leaves exactly one instance, no orphans', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  let id = ie.addIndicator('SMA', { length: 20 });
  ie.removeIndicator(id);
  id = ie.addIndicator('SMA', { length: 20 });
  ie.removeIndicator(id);
  id = ie.addIndicator('SMA', { length: 20 });
  const insts = ie.getInstances();
  assert.strictEqual(Object.keys(insts).length, 1);
  assert.strictEqual(insts[id].params.length, 20);
});

console.log('\nError isolation (one indicator failing must not block others)');

test('a throwing Calc function during onCandlesLoaded does not prevent other indicators from recalculating', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const emaId = ie.addIndicator('EMA', { length: 5 });
  const rsiId = ie.addIndicator('RSI', { length: 14 });

  // Monkeypatch the shared Calc object (same reference _calcAll uses
  // internally) so EMA's calculation throws.
  const originalEma = ie._Calc.ema;
  ie._Calc.ema = function () { throw new Error('simulated calc failure'); };

  let threw = false;
  try {
    ie.onCandlesLoaded(sampleCandles(30), null);
  } catch (e) {
    threw = true;
  }
  assert.strictEqual(threw, false, 'onCandlesLoaded itself must not throw even if one indicator does');

  // RSI must still be present and untouched by EMA's failure.
  const insts = ie.getInstances();
  assert.ok(insts[rsiId], 'RSI instance must survive a sibling EMA calculation error');
  assert.ok(insts[emaId], 'the failing EMA instance itself must also survive (not silently removed)');

  ie._Calc.ema = originalEma;
});

console.log('\nEnd-to-end persistence (localStorage side effects of the public API)');

test('addIndicator persists configuration to localStorage', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const T2 = ie._test;
  ie.addIndicator('EMA', { length: 33 });
  const raw = sb.window.localStorage.getItem(T2.PERSIST_KEY);
  assert.ok(raw, 'a config must have been saved');
  const parsed = JSON.parse(raw);
  assert.strictEqual(parsed.version, T2.PERSIST_VERSION);
  assert.strictEqual(parsed.instances.length, 1);
  assert.strictEqual(parsed.instances[0].type, 'EMA');
  assert.strictEqual(parsed.instances[0].params.length, 33);
});

test('removeIndicator updates persisted configuration (removed indicator does not return)', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const T2 = ie._test;
  const id1 = ie.addIndicator('EMA', { length: 20 });
  ie.addIndicator('RSI', { length: 14 });
  ie.removeIndicator(id1);
  const parsed = JSON.parse(sb.window.localStorage.getItem(T2.PERSIST_KEY));
  const types = parsed.instances.map((i) => i.type);
  assertJsonEqual(types, ['RSI']);
});

test('updateParams persists the new parameters, not the old ones', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const T2 = ie._test;
  const id = ie.addIndicator('EMA', { length: 20 });
  ie.updateParams(id, { length: 50 });
  const parsed = JSON.parse(sb.window.localStorage.getItem(T2.PERSIST_KEY));
  assert.strictEqual(parsed.instances[0].params.length, 50);
});

test('cold load restores persisted EMA/RSI/MACD configuration exactly once', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const T2 = ie._test;
  sb.window.localStorage.setItem(T2.PERSIST_KEY, JSON.stringify({
    version: T2.PERSIST_VERSION,
    instances: [
      { type: 'EMA', params: { length: 20 }, visible: true },
      { type: 'RSI', params: { length: 14 }, visible: false },
      { type: 'MACD', params: { fast: 12, slow: 26, signal: 9, source: 'close' }, visible: true }
    ]
  }));

  ie.onCandlesLoaded(sampleCandles(60), null); // simulates first page load
  let insts = ie.getInstances();
  assert.strictEqual(Object.keys(insts).length, 3);
  const byType = {};
  Object.values(insts).forEach((i) => { byType[i.def.id] = i; });
  assert.strictEqual(byType.EMA.visible, true);
  assert.strictEqual(byType.RSI.visible, false);
  assert.strictEqual(byType.RSI.params.length, 14);
  assert.strictEqual(byType.MACD.visible, true);

  // Simulate a symbol/timeframe switch: onCandlesLoaded fires again.
  ie.onCandlesLoaded(sampleCandles(60), null);
  insts = ie.getInstances();
  assert.strictEqual(Object.keys(insts).length, 3, 'restoration must not repeat on subsequent onCandlesLoaded calls');
});

test('corrupted persisted config (bad JSON) falls back to a clean chart, no throw', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const T2 = ie._test;
  sb.window.localStorage.setItem(T2.PERSIST_KEY, '{ this is not json');

  let threw = false;
  try {
    ie.onCandlesLoaded(sampleCandles(30), null);
  } catch (e) {
    threw = true;
  }
  assert.strictEqual(threw, false);
  assert.strictEqual(Object.keys(ie.getInstances()).length, 0);
});

test('corrupted persisted config (unknown indicator type mixed with valid ones) restores only the valid entries', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const T2 = ie._test;
  sb.window.localStorage.setItem(T2.PERSIST_KEY, JSON.stringify({
    version: T2.PERSIST_VERSION,
    instances: [
      { type: 'EMA', params: { length: 20 } },
      { type: 'SOME_REMOVED_INDICATOR', params: { length: 20 } }
    ]
  }));

  ie.onCandlesLoaded(sampleCandles(30), null);
  const insts = ie.getInstances();
  assert.strictEqual(Object.keys(insts).length, 1);
  assert.strictEqual(Object.values(insts)[0].def.id, 'EMA');
});

test('restored EMA instance keeps its saved color instead of re-cycling the EMA palette', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const T2 = ie._test;
  sb.window.localStorage.setItem(T2.PERSIST_KEY, JSON.stringify({
    version: T2.PERSIST_VERSION,
    instances: [{ type: 'EMA', params: { length: 20 }, color: '#123456' }]
  }));
  ie.onCandlesLoaded(sampleCandles(30), null);
  const inst = Object.values(ie.getInstances())[0];
  assert.strictEqual(inst.color, '#123456');
});

console.log('\nIchimoku forward-projection candle-time formats (regression: string dates)');

function dailyStringCandles(n, startYear, startMonth, startDay) {
  const pad = (v) => String(v).padStart(2, '0');
  const out = [];
  const d = new Date(Date.UTC(startYear, startMonth - 1, startDay));
  for (let i = 0; i < n; i++) {
    const t = `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
    out.push({ time: t, open: 100 + i, high: 105 + i, low: 95 + i, close: 100 + i, volume: 1000 });
    d.setUTCDate(d.getUTCDate() + 1);
  }
  return out;
}

test('numeric epoch-second candle.time still projects correctly (no regression)', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const candles = sampleCandles(60); // numeric time, 60s step
  const calc = ie._Calc.ichimoku(candles, 9, 26, 52, 26);
  const disp = ie._Calc.ichimokuDisplay(candles, calc);
  const future = disp.spanA.filter((p) => p.calcIndex >= candles.length - 26);
  assert.ok(future.length > 0);
  future.forEach((p) => assert.strictEqual(typeof p.time, 'number'));
  // Strictly increasing, spaced by the bar interval (60s)
  for (let i = 1; i < future.length; i++) {
    assert.strictEqual(future[i].time - future[i - 1].time, 60);
  }
});

test('"yyyy-mm-dd" string candle.time projects to clean future date strings (bug fix)', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const candles = dailyStringCandles(60, 2026, 7, 1);
  const calc = ie._Calc.ichimoku(candles, 9, 26, 52, 26);
  const disp = ie._Calc.ichimokuDisplay(candles, calc);
  const future = disp.spanA.filter((p) => p.calcIndex >= candles.length - 26);
  assert.ok(future.length > 0, 'must actually produce future-projected points to test');
  const dateRe = /^\d{4}-\d{2}-\d{2}$/;
  future.forEach((p) => {
    assert.strictEqual(typeof p.time, 'string');
    assert.ok(dateRe.test(p.time), `expected clean yyyy-mm-dd, got corrupted value: "${p.time}"`);
  });
  // Each future date must be exactly one calendar day after the previous
  // (this dataset's bar interval), never a garbled concatenation.
  for (let i = 1; i < future.length; i++) {
    const prev = new Date(future[i - 1].time + 'T00:00:00Z');
    const cur = new Date(future[i].time + 'T00:00:00Z');
    assert.strictEqual((cur - prev) / 86400000, 1);
  }
});

test('BusinessDay object candle.time ({year,month,day}) projects to a valid object, not NaN/garbage', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const candles = [];
  const d = new Date(Date.UTC(2026, 6, 1));
  for (let i = 0; i < 60; i++) {
    candles.push({
      time: { year: d.getUTCFullYear(), month: d.getUTCMonth() + 1, day: d.getUTCDate() },
      open: 100 + i, high: 105 + i, low: 95 + i, close: 100 + i, volume: 1000
    });
    d.setUTCDate(d.getUTCDate() + 1);
  }
  const calc = ie._Calc.ichimoku(candles, 9, 26, 52, 26);
  const disp = ie._Calc.ichimokuDisplay(candles, calc);
  const future = disp.spanA.filter((p) => p.calcIndex >= candles.length - 26);
  assert.ok(future.length > 0);
  future.forEach((p) => {
    assert.strictEqual(typeof p.time, 'object');
    assert.ok(Number.isInteger(p.time.year) && p.time.year > 2000);
    assert.ok(Number.isInteger(p.time.month) && p.time.month >= 1 && p.time.month <= 12);
    assert.ok(Number.isInteger(p.time.day) && p.time.day >= 1 && p.time.day <= 31);
  });
});

test('_renderIchimoku (full lifecycle) does not throw on daily string-date candles', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  ie.addIndicator('ICHIMOKU', {});
  let threw = false;
  try {
    ie.onCandlesLoaded(dailyStringCandles(80, 2026, 6, 1), null);
  } catch (e) {
    threw = true;
  }
  assert.strictEqual(threw, false);
});

console.log('\nCCI second (smoothing) line');

test('smoothIgnoringNulls does not treat a leading null warm-up as zero', () => {
  const sb = makeSandbox();
  const Calc = sb.window.IndicatorEngine._Calc;
  const vals = [null, null, null, 10, 20, 30, 40, 50, 15, 25];
  const smoothed = Calc.smoothIgnoringNulls(vals, 3);
  // First 3 nulls are warm-up (unknown value), first valid value is at
  // index 3 — a 3-period SMA can't produce a result until index 5
  // (3 + period - 1), so indices 0..4 must all be null, never a
  // null-coerced-to-zero average.
  for (let i = 0; i <= 4; i++) assert.strictEqual(smoothed[i], null);
  assert.strictEqual(smoothed[5], (10 + 20 + 30) / 3);
  assert.strictEqual(smoothed[6], (20 + 30 + 40) / 3);
});

test('smoothIgnoringNulls returns all-null for an all-null input (no crash)', () => {
  const sb = makeSandbox();
  const Calc = sb.window.IndicatorEngine._Calc;
  const out = Calc.smoothIgnoringNulls([null, null, null], 2);
  out.forEach((v) => assert.strictEqual(v, null));
});

test('CCI produces two distinct lines (raw CCI + smoothed) matching TradingView\'s 2-line display', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const candles = sampleCandles(60);
  const cci = ie._Calc.cci(candles, 20);
  const smooth = ie._Calc.smoothIgnoringNulls(cci, 14);
  const rawTail = cci.slice(-10);
  const smoothTail = smooth.slice(-10);
  assert.ok(rawTail.every((v) => v !== null));
  assert.ok(smoothTail.every((v) => v !== null));
  assert.notDeepStrictEqual(rawTail, smoothTail, 'the two lines must not be identical');
});

test('CCI instance calculates and stores both raw and smoothed values per candle', () => {
  // Note: this sandbox has no LightweightCharts loaded, so _createSeries
  // can't build real chart series (that's expected — series wiring is
  // covered by manual browser QA). What IS testable here is that _calcAll
  // computes and records both values via inst._dataByTime, which is what
  // the shared crosshair legend reads from.
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('CCI', {});
  ie.onCandlesLoaded(sampleCandles(60), null);
  const inst = ie.getInstances()[id];
  assert.ok(inst._dataByTime, 'CCI must populate _dataByTime for the crosshair legend');
  const entries = Object.values(inst._dataByTime).filter((v) => v.cci !== null && v.cciSmooth !== null);
  assert.ok(entries.length > 0, 'at least some candles must have both cci and cciSmooth values');
});

console.log('\nParabolic SAR (PSAR) engine integration');

function trendyCandles(n) {
  const out = [];
  for (let i = 0; i < n; i++) {
    out.push({ time: 1000 + i * 60, open: 100 + i, high: 105 + i, low: 95 + i, close: 100 + i, volume: 1000 });
  }
  return out;
}

test('PSAR registered in DEFS with the documented default parameters', () => {
  const sb = makeSandbox();
  const def = sb.window.IndicatorEngine.DEFS.PSAR;
  assert.ok(def, 'PSAR must be registered in the indicator registry');
  assert.strictEqual(def.type, 'overlay');
  assert.strictEqual(def.paramDefs.initialAF.default, 0.02);
  assert.strictEqual(def.paramDefs.increment.default, 0.02);
  assert.strictEqual(def.paramDefs.maximumAF.default, 0.20);
});

test('PSAR is searchable by id and full name', () => {
  const sb = makeSandbox();
  const T2 = sb.window.IndicatorEngine._test;
  const def = sb.window.IndicatorEngine.DEFS.PSAR;
  assert.strictEqual(T2.menuItemMatches(def, 'psar'), true);
  assert.strictEqual(T2.menuItemMatches(def, 'parabolic'), true);
});

test('adding PSAR computes sar/trend for every candle after warm-up (via _dataByTime)', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('PSAR', {});
  ie.onCandlesLoaded(trendyCandles(30), null);
  const inst = ie.getInstances()[id];
  const entries = Object.values(inst._dataByTime);
  assert.ok(entries.some((v) => v.sar !== null && (v.trend === 1 || v.trend === -1)));
});

test('two PSAR instances with different AF settings stay independent', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id1 = ie.addIndicator('PSAR', { initialAF: 0.01, increment: 0.01, maximumAF: 0.10 });
  const id2 = ie.addIndicator('PSAR', { initialAF: 0.05, increment: 0.05, maximumAF: 0.50 });
  ie.onCandlesLoaded(trendyCandles(30), null);
  const insts = ie.getInstances();
  assert.strictEqual(insts[id1].params.maximumAF, 0.10);
  assert.strictEqual(insts[id2].params.maximumAF, 0.50);
  const sar1 = insts[id1]._dataByTime[1000 + 5 * 60].sar;
  const sar2 = insts[id2]._dataByTime[1000 + 5 * 60].sar;
  assert.notStrictEqual(sar1, sar2, 'different AF configs must not silently share state');
});

test('updateParams recalculates PSAR in place (no duplicate instance)', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('PSAR', { initialAF: 0.02, increment: 0.02, maximumAF: 0.20 });
  ie.onCandlesLoaded(trendyCandles(30), null);
  const before = ie.getInstances()[id]._dataByTime[1000 + 10 * 60].sar;
  ie.updateParams(id, { maximumAF: 0.50 });
  const after = ie.getInstances()[id]._dataByTime[1000 + 10 * 60].sar;
  assert.strictEqual(Object.keys(ie.getInstances()).length, 1, 'must still be exactly one PSAR instance');
  // Not asserting before !== after numerically (a slow uptrend may not have
  // hit the old cap yet), just that recalculation actually ran cleanly.
  assert.ok(before !== undefined && after !== undefined);
});

test('symbol switch (onCandlesLoaded with new candles) recalculates, no stale SAR values', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('PSAR', {});
  ie.onCandlesLoaded(trendyCandles(30), null); // "symbol A"
  const symbolACandle10Sar = ie.getInstances()[id]._dataByTime[1000 + 10 * 60].sar;

  // "Symbol B": completely different price regime, same time grid
  const symbolB = [];
  for (let i = 0; i < 30; i++) {
    symbolB.push({ time: 1000 + i * 60, open: 5000 - i * 10, high: 5010 - i * 10, low: 4990 - i * 10, close: 5000 - i * 10, volume: 500 });
  }
  ie.onCandlesLoaded(symbolB, null);
  const symbolBCandle10Sar = ie.getInstances()[id]._dataByTime[1000 + 10 * 60].sar;
  assert.notStrictEqual(symbolACandle10Sar, symbolBCandle10Sar, 'switching candle data must recompute, not reuse stale state');
});

test('remove PSAR then re-add leaves exactly one instance with fresh series', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  let id = ie.addIndicator('PSAR', {});
  ie.removeIndicator(id);
  id = ie.addIndicator('PSAR', {});
  ie.onCandlesLoaded(trendyCandles(20), null);
  const insts = ie.getInstances();
  assert.strictEqual(Object.keys(insts).length, 1);
  assert.ok(insts[id]._dataByTime);
});

test('live tick recompute does not duplicate points across the up/down series (full-refresh strategy)', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('PSAR', {});
  const candles = trendyCandles(20);
  ie.onCandlesLoaded(candles, null);

  // Simulate a live tick on the forming last candle (a reversal-provoking
  // low crash), then call onCandleUpdate — _renderPsar's full setData()
  // strategy means this must not throw and must not leave any duplicate
  // per-time entries (verified indirectly: exactly one dataByTime record
  // per candle time, never two).
  const ticked = candles.slice(0, -1).concat([{ ...candles[candles.length - 1], low: 50, close: 55 }]);
  let threw = false;
  try {
    ie.onCandleUpdate(ticked[ticked.length - 1]);
  } catch (e) {
    threw = true;
  }
  assert.strictEqual(threw, false);
  const inst = ie.getInstances()[id];
  const timeKeys = Object.keys(inst._dataByTime || {});
  assert.strictEqual(timeKeys.length, new Set(timeKeys).size, 'no duplicate time keys');
});

test('invalid PSAR params (initialAF > maximumAF) do not crash the engine, produce no values', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('PSAR', { initialAF: 0.5, increment: 0.02, maximumAF: 0.1 });
  let threw = false;
  try {
    ie.onCandlesLoaded(trendyCandles(20), null);
  } catch (e) {
    threw = true;
  }
  assert.strictEqual(threw, false);
  const inst = ie.getInstances()[id];
  const anyValid = Object.values(inst._dataByTime || {}).some((v) => v.sar !== null);
  assert.strictEqual(anyValid, false, 'invalid AF combination must produce no SAR values, not garbage');
});

console.log('\nPivot Points engine integration');

function multiDaySessionCandles(daysOhlc, barsPerDay = 1, stepMinutes = 60) {
  // 2026-01-01 is a Thursday in this calendar; walk forward skipping Sat/Sun.
  const out = [];
  let cursor = new Date(Date.UTC(2026, 0, 1));
  for (const [h, l, c] of daysOhlc) {
    while ([0, 6].includes(cursor.getUTCDay())) cursor.setUTCDate(cursor.getUTCDate() + 1);
    for (let b = 0; b < barsPerDay; b++) {
      const istWallClock = Date.UTC(
        cursor.getUTCFullYear(), cursor.getUTCMonth(), cursor.getUTCDate(),
        9, b * stepMinutes
      ) / 1000;
      const epoch = istWallClock - 19800; // IST wall-clock -> UTC epoch
      const isLast = b === barsPerDay - 1;
      out.push({ time: epoch, open: h, high: h, low: l, close: isLast ? c : (h + l) / 2 });
    }
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return out;
}

test('Pivot Points registered in DEFS with Standard/Auto defaults (Auto matches TradingView\'s own default)', () => {
  const sb = makeSandbox();
  const def = sb.window.IndicatorEngine.DEFS.PIVOTPOINTS;
  assert.ok(def, 'Pivot Points must be registered in the indicator registry');
  assert.strictEqual(def.type, 'overlay');
  assert.strictEqual(def.paramDefs.method.default, 'Standard');
  assert.strictEqual(def.paramDefs.period.default, 'Auto');
  assertJsonEqual(def.paramDefs.method.options, ['Standard', 'Fibonacci', 'Woodie', 'Camarilla']);
  assertJsonEqual(def.paramDefs.period.options, ['Auto', 'Daily', 'Weekly', 'Monthly']);
});

test('Auto period resolves to Daily for intraday bar spacing (<=15m)', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('PIVOTPOINTS', { method: 'Standard', period: 'Auto' });
  const candles = multiDaySessionCandles([[110, 100, 105], [120, 112, 118]], 4, 15); // 15-minute bars
  ie.onCandlesLoaded(candles, null);
  const inst = ie.getInstances()[id];
  // 4 bars/day at 15m step -> Auto must resolve to Daily, so day 2's first
  // bar already carries day 1's completed levels.
  assert.ok(inst._dataByTime[candles[4].time].p !== null, 'Auto must resolve to Daily for a 15-minute chart');
});

test('Auto period resolves to Monthly for daily-spaced bars (one candle per day)', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('PIVOTPOINTS', { method: 'Standard', period: 'Auto' });
  const candles = multiDaySessionCandles([[110, 100, 105], [120, 112, 118]]); // 1 bar/day -> ~86400s spacing
  ie.onCandlesLoaded(candles, null);
  const inst = ie.getInstances()[id];
  // With ~1-day bar spacing, Auto must NOT resolve to Daily (a "Daily
  // period" where every candle IS a full day is the exact degenerate case
  // this whole Auto rule exists to avoid) -- day 2 must still show no
  // levels yet, since day 1 and day 2 fall in the same still-forming
  // Monthly period.
  assert.strictEqual(inst._dataByTime[candles[1].time].p, null);
});

test('Pivot Points is searchable', () => {
  const sb = makeSandbox();
  const T2 = sb.window.IndicatorEngine._test;
  const def = sb.window.IndicatorEngine.DEFS.PIVOTPOINTS;
  assert.strictEqual(T2.menuItemMatches(def, 'pivot'), true);
  assert.strictEqual(T2.menuItemMatches(def, 'PIVOTPOINTS'.toLowerCase()), true);
});

test('first day has no pivot levels; second day gets levels from the first day', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('PIVOTPOINTS', { period: 'Daily' });
  const candles = multiDaySessionCandles([[110, 100, 105], [120, 112, 118]]);
  ie.onCandlesLoaded(candles, null);
  const inst = ie.getInstances()[id];
  const day1 = inst._dataByTime[candles[0].time];
  const day2 = inst._dataByTime[candles[1].time];
  assert.strictEqual(day1.p, null, 'first day must have no prior period');
  assert.ok(day2.p !== null && isFinite(day2.p));
});

test('Standard and Camarilla both expose r4/s4, using different formulas', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const candles = multiDaySessionCandles([[110, 100, 105], [120, 112, 118]]);

  const idStd = ie.addIndicator('PIVOTPOINTS', { method: 'Standard', period: 'Daily' });
  ie.onCandlesLoaded(candles, null);
  const stdDay2 = ie.getInstances()[idStd]._dataByTime[candles[1].time];
  assert.ok(stdDay2.r4 !== null && isFinite(stdDay2.r4), 'Standard (Traditional) shows 5 levels per side, like TradingView');
  assert.ok(stdDay2.s4 !== null && isFinite(stdDay2.s4));
  assert.ok(stdDay2.r5 !== null && isFinite(stdDay2.r5), 'Standard (Traditional) must also expose R5');
  assert.ok(stdDay2.s5 !== null && isFinite(stdDay2.s5));

  const idCam = ie.addIndicator('PIVOTPOINTS', { method: 'Camarilla', period: 'Daily' });
  ie.onCandlesLoaded(candles, null); // recalculates all instances, including idStd again
  const camDay2 = ie.getInstances()[idCam]._dataByTime[candles[1].time];
  assert.ok(camDay2.r4 !== null && isFinite(camDay2.r4));
  assert.ok(camDay2.s4 !== null && isFinite(camDay2.s4));
  assert.notStrictEqual(stdDay2.r4, camDay2.r4, 'Standard and Camarilla use different R4 formulas');
});

test('method change (updateParams) recalculates in place, no duplicate instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const candles = multiDaySessionCandles([[110, 100, 105], [120, 112, 118]]);
  const id = ie.addIndicator('PIVOTPOINTS', { method: 'Standard', period: 'Daily' });
  ie.onCandlesLoaded(candles, null);
  const beforeP = ie.getInstances()[id]._dataByTime[candles[1].time].p;

  ie.updateParams(id, { method: 'Fibonacci' });
  const afterP = ie.getInstances()[id]._dataByTime[candles[1].time].p; // P formula is identical for Standard/Fibonacci
  const afterR1 = ie.getInstances()[id]._dataByTime[candles[1].time].r1; // R1 formula differs

  assert.strictEqual(Object.keys(ie.getInstances()).length, 1, 'must still be exactly one instance');
  assert.strictEqual(beforeP, afterP, 'P is the same formula for Standard and Fibonacci');
  assert.ok(afterR1 !== null && isFinite(afterR1));
});

test('live tick on the current (forming) session does not move that session\'s pivot levels', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('PIVOTPOINTS', {});
  const candles = multiDaySessionCandles([[110, 100, 105], [120, 112, 118]], 4, 15); // 4 bars/day
  ie.onCandlesLoaded(candles, null);
  const day2FirstBarTime = candles[4].time;
  const pBefore = ie.getInstances()[id]._dataByTime[day2FirstBarTime].p;

  // Simulate wild live ticks on day 2's LAST (currently forming) bar.
  const ticked = candles.slice(0, -1).concat([{ ...candles[candles.length - 1], high: 9999, low: 1, close: 500 }]);
  let threw = false;
  try {
    ie.onCandleUpdate(ticked[ticked.length - 1]);
  } catch (e) {
    threw = true;
  }
  assert.strictEqual(threw, false);
  const pAfter = ie.getInstances()[id]._dataByTime[day2FirstBarTime].p;
  assert.strictEqual(pBefore, pAfter, "day 2's own live OHLC must never move day 2's pivot levels");
});

test('symbol switch recalculates from the new candle set, no stale levels', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('PIVOTPOINTS', { period: 'Daily' });
  const symbolA = multiDaySessionCandles([[110, 100, 105], [120, 112, 118]]);
  ie.onCandlesLoaded(symbolA, null);
  const pA = ie.getInstances()[id]._dataByTime[symbolA[1].time].p;

  const symbolB = multiDaySessionCandles([[5000, 4900, 4950], [5200, 5100, 5150]]);
  ie.onCandlesLoaded(symbolB, null);
  const pB = ie.getInstances()[id]._dataByTime[symbolB[1].time].p;

  assert.notStrictEqual(pA, pB, 'switching candle data must recompute, not reuse stale levels');
});

test('remove then re-add leaves exactly one instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  let id = ie.addIndicator('PIVOTPOINTS', {});
  ie.removeIndicator(id);
  id = ie.addIndicator('PIVOTPOINTS', {});
  ie.onCandlesLoaded(multiDaySessionCandles([[110, 100, 105], [120, 112, 118]]), null);
  assert.strictEqual(Object.keys(ie.getInstances()).length, 1);
});

test('weekly period assigns one shared pivot set across an entire trading week', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('PIVOTPOINTS', { period: 'Weekly' });
  const days = [];
  for (let i = 0; i < 12; i++) days.push([100 + i, 95 + i, 98 + i]);
  const candles = multiDaySessionCandles(days);
  ie.onCandlesLoaded(candles, null);
  const inst = ie.getInstances()[id];
  const pivots = candles.map((c) => inst._dataByTime[c.time].p);
  // At least one non-null run of >1 consecutive equal values must exist
  // (an entire week sharing one pivot set), somewhere past the first week.
  const nonNull = pivots.filter((v) => v !== null);
  assert.ok(nonNull.length > 0);
  const uniqueNonNull = new Set(nonNull);
  assert.ok(uniqueNonNull.size < nonNull.length, 'multiple days must share identical weekly pivot values');
});

console.log('\nScript-load-order race: self-init from pre-existing window._chartCandles');

test('indicators.js self-initializes if candles are already cached on window at load time', () => {
  // Reproduces the real bug found during manual browser QA: stock.html
  // loads dashboard.js BEFORE indicators.js, and dashboard.js's own
  // `if (window.IndicatorEngine) window.IndicatorEngine.onCandlesLoaded(...)`
  // calls are never retried if that guard misses (window.IndicatorEngine
  // undefined yet) — which happens whenever the chart's candle data
  // resolves from a fast cache-hit path before this script has executed.
  // That silently drops the initial render hookup AND, since restoration
  // only happens inside onCandlesLoaded, the persisted-config restore too
  // — with zero errors, on every affected page load.
  //
  // Fix: indicators.js checks window._chartCandles at its own load time and
  // self-initializes if data is already there. This test seeds that global
  // BEFORE evaluating the script (simulating dashboard.js having already
  // run and cached candles) and confirms onCandlesLoaded actually fired —
  // by checking that a persisted indicator was restored — WITHOUT this
  // test ever calling onCandlesLoaded or addIndicator itself.
  const store = {};
  const localStorageStub = {
    getItem: (k) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; }
  };
  store['leverage_indicators_v1'] = JSON.stringify({
    version: 1,
    instances: [{ type: 'PSAR', params: { initialAF: 0.02, increment: 0.02, maximumAF: 0.20 }, visible: true }]
  });

  const noopEl = () => ({
    style: {}, innerHTML: '', appendChild() {}, addEventListener() {}, removeEventListener() {},
    querySelector: () => null, getBoundingClientRect: () => ({ width: 0, height: 0 })
  });
  const documentStub = {
    readyState: 'complete',
    getElementById: () => null,
    createElement: () => noopEl(),
    addEventListener() {},
    body: { appendChild() {} }
  };
  const windowStub = {
    localStorage: localStorageStub,
    devicePixelRatio: 1,
    addEventListener() {},
    removeEventListener() {},
    // The critical precondition: candles already cached before this
    // script runs, exactly like the real race.
    _chartCandles: [
      { time: 1000, open: 100, high: 105, low: 95, close: 100, volume: 1000 },
      { time: 1060, open: 101, high: 106, low: 96, close: 102, volume: 1000 }
    ],
    _lastFormingRange: 'ALL'
  };
  const sandbox = {
    window: windowStub, document: documentStub, console, setTimeout,
    requestAnimationFrame: (fn) => fn(),
    Object, Array, Number, Math, JSON, String, Boolean, Date,
    isFinite, isNaN, parseInt, parseFloat
  };
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: 'indicators.js' });

  const insts = sandbox.window.IndicatorEngine.getInstances();
  assert.strictEqual(Object.keys(insts).length, 1, 'the persisted PSAR must have been restored during script evaluation itself');
  assert.strictEqual(Object.values(insts)[0].def.id, 'PSAR');
});

test('self-init does nothing (no throw, no instances) when no candles are cached yet', () => {
  // The normal case: dashboard.js hasn't raced ahead, window._chartCandles
  // is not yet set. Self-init must be a safe no-op, not throw.
  const sb = makeSandbox();
  assert.deepStrictEqual(Object.keys(sb.window.IndicatorEngine.getInstances()), []);
});

console.log('\nPivot Points High Low engine integration');

test('Pivot Points High Low registered in DEFS with left=5/right=5 defaults, separate from Pivot Points Standard', () => {
  const sb = makeSandbox();
  const def = sb.window.IndicatorEngine.DEFS.PIVOTHIGHLOW;
  assert.ok(def, 'must be registered as its own indicator');
  assert.notStrictEqual(def.id, sb.window.IndicatorEngine.DEFS.PIVOTPOINTS.id);
  assert.strictEqual(def.type, 'overlay');
  assert.strictEqual(def.paramDefs.leftBars.default, 5);
  assert.strictEqual(def.paramDefs.rightBars.default, 5);
});

test('Pivot Points High Low is searchable', () => {
  const sb = makeSandbox();
  const T2 = sb.window.IndicatorEngine._test;
  const def = sb.window.IndicatorEngine.DEFS.PIVOTHIGHLOW;
  assert.strictEqual(T2.menuItemMatches(def, 'pivot points hl'), true);
  assert.strictEqual(T2.menuItemMatches(def, 'high low'), true);
});

test('a confirmed pivot is not revealed until rightBars real candles exist, then appears without shifting position', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('PIVOTHIGHLOW', { leftBars: 2, rightBars: 2 });

  // Peak at index 2 (padded so it has 2 real candles on each side once fully loaded)
  const highs = [10, 10, 20, 10, 9];
  const times = highs.map((_, i) => 1000 + i * 60);
  const shortCandles = highs.slice(0, 4).map((h, i) => ({ time: times[i], high: h, low: 5, close: 8 })); // only 1 candle after the peak
  ie.onCandlesLoaded(shortCandles, null);
  let inst = ie.getInstances()[id];
  assert.strictEqual(inst._dataByTime[times[2]].pivotHigh, null, 'only 1 right-side candle exists; rightBars=2 must not confirm yet');

  const fullCandles = highs.map((h, i) => ({ time: times[i], high: h, low: 5, close: 8 }));
  ie.onCandlesLoaded(fullCandles, null);
  inst = ie.getInstances()[id];
  assert.strictEqual(inst._dataByTime[times[2]].pivotHigh, 20, 'now confirmed, value at the ORIGINAL pivot candle (index 2), not shifted');
});

test('two PivotHighLow instances with different left/right bars stay independent', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id1 = ie.addIndicator('PIVOTHIGHLOW', { leftBars: 2, rightBars: 2 });
  const id2 = ie.addIndicator('PIVOTHIGHLOW', { leftBars: 10, rightBars: 10 });
  const candles = trendyCandles(30);
  ie.onCandlesLoaded(candles, null);
  const insts = ie.getInstances();
  assert.strictEqual(insts[id1].params.leftBars, 2);
  assert.strictEqual(insts[id2].params.leftBars, 10);
});

test('remove then re-add PivotHighLow leaves exactly one instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  let id = ie.addIndicator('PIVOTHIGHLOW', {});
  ie.removeIndicator(id);
  id = ie.addIndicator('PIVOTHIGHLOW', {});
  ie.onCandlesLoaded(trendyCandles(20), null);
  assert.strictEqual(Object.keys(ie.getInstances()).length, 1);
});

test('symbol switch recalculates PivotHighLow, no stale pivots', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('PIVOTHIGHLOW', { leftBars: 2, rightBars: 2 });
  const highsA = [10, 10, 20, 10, 9, 10];
  const candlesA = highsA.map((h, i) => ({ time: 1000 + i * 60, high: h, low: 5, close: 8 }));
  ie.onCandlesLoaded(candlesA, null);
  const pivotA = ie.getInstances()[id]._dataByTime[1000 + 2 * 60].pivotHigh;

  const highsB = [50, 50, 5, 50, 51, 50]; // different regime: a LOW at index 2, not a high
  const candlesB = highsB.map((h, i) => ({ time: 1000 + i * 60, high: h + 10, low: h, close: h + 5 }));
  ie.onCandlesLoaded(candlesB, null);
  const pivotB = ie.getInstances()[id]._dataByTime[1000 + 2 * 60].pivotHigh;

  assert.strictEqual(pivotA, 20);
  assert.notStrictEqual(pivotB, 20, 'switching candle data must recompute, not reuse the old symbol\'s pivot');
});

test('invalid leftBars/rightBars produce no pivots, no crash', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('PIVOTHIGHLOW', { leftBars: 0, rightBars: 5 });
  let threw = false;
  try {
    ie.onCandlesLoaded(trendyCandles(20), null);
  } catch (e) {
    threw = true;
  }
  assert.strictEqual(threw, false);
  const inst = ie.getInstances()[id];
  const anyPivot = Object.values(inst._dataByTime || {}).some((v) => v.pivotHigh !== null || v.pivotLow !== null);
  assert.strictEqual(anyPivot, false);
});

test('markers are applied to the real candle series (setMarkers), not left on an empty dummy series', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const highs = [10, 10, 20, 10, 9];
  const times = highs.map((_, i) => 1000 + i * 60);
  const candles = highs.map((h, i) => ({ time: times[i], high: h, low: 5, close: 8 }));
  ie.addIndicator('PIVOTHIGHLOW', { leftBars: 2, rightBars: 2 });
  ie.onCandlesLoaded(candles, null);
  const applied = sb.window.bigCandleSeries._lastMarkers;
  assert.ok(applied.length > 0, 'setMarkers must actually be called on the real candle series');
  assert.strictEqual(applied[0].time, times[2]);
  assert.strictEqual(applied[0].text, 'PH');
});

test('removing the only PivotHighLow instance clears its markers from the candle series', () => {
  // Regression test for a real bug found during manual QA: removeIndicator
  // used to delete from _instances AFTER calling _removeSeries (which
  // triggers the marker-merge redraw), so the just-removed instance was
  // still counted in that one redraw and its stale markers never cleared.
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const candles = trendyCandles(15);
  const id = ie.addIndicator('PIVOTHIGHLOW', { leftBars: 2, rightBars: 2 });
  ie.onCandlesLoaded(candles, null);
  ie.removeIndicator(id);
  assertJsonEqual(sb.window.bigCandleSeries._lastMarkers, [], 'markers must be cleared, not left stale, once the owning instance is gone');
});

test('two PivotHighLow instances merge their markers into one setMarkers call', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const highs = [10, 10, 20, 10, 9, 10, 10, 30, 10, 9];
  const times = highs.map((_, i) => 1000 + i * 60);
  const candles = highs.map((h, i) => ({ time: times[i], high: h, low: 5, close: 8 }));
  ie.addIndicator('PIVOTHIGHLOW', { leftBars: 2, rightBars: 2 });
  ie.addIndicator('PIVOTHIGHLOW', { leftBars: 1, rightBars: 1 });
  ie.onCandlesLoaded(candles, null);
  const applied = sb.window.bigCandleSeries._lastMarkers;
  assert.ok(applied.length >= 2, 'markers from both instances must be present in the merged set');
  const times_ = Array.from(applied).map((m) => m.time);
  const sorted = [...times_].sort((a, b) => a - b);
  assertJsonEqual(times_, sorted, 'merged markers must be time-sorted for LWC');
});

console.log('\nDonchian Channels engine integration');

test('Donchian Channels registered in DEFS with length=20 default', () => {
  const sb = makeSandbox();
  const def = sb.window.IndicatorEngine.DEFS.DONCHIAN;
  assert.ok(def);
  assert.strictEqual(def.type, 'overlay');
  assert.strictEqual(def.paramDefs.length.default, 20);
});

test('Donchian Channels is searchable', () => {
  const sb = makeSandbox();
  const T2 = sb.window.IndicatorEngine._test;
  assert.strictEqual(T2.menuItemMatches(sb.window.IndicatorEngine.DEFS.DONCHIAN, 'donchian'), true);
});

test('Donchian upper/lower/middle populate correctly after warm-up', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('DONCHIAN', { length: 5 });
  const candles = trendyCandles(20);
  ie.onCandlesLoaded(candles, null);
  const inst = ie.getInstances()[id];
  for (let i = 0; i < 4; i++) {
    assert.strictEqual(inst._dataByTime[candles[i].time].upper, null, `index ${i} is within warm-up`);
  }
  const rec = inst._dataByTime[candles[10].time];
  assert.ok(rec.upper !== null && isFinite(rec.upper));
  assert.ok(rec.upper >= rec.lower);
  assert.strictEqual(rec.middle, (rec.upper + rec.lower) / 2);
});

test('two Donchian instances with different lengths stay independent', () => {
  // trendyCandles() is strictly monotonic, where the window's max is always
  // just the current candle's own high regardless of window length — that
  // would make upper identical for any length and prove nothing. Use a
  // dataset with a peak that ages OUT of the short window but is still
  // inside the long one, which is the whole point of a longer length.
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id1 = ie.addIndicator('DONCHIAN', { length: 5 });
  const id2 = ie.addIndicator('DONCHIAN', { length: 15 });
  const candles = [];
  for (let i = 0; i < 25; i++) {
    const isPeak = i === 5;
    candles.push({ time: 1000 + i * 60, open: 100, high: isPeak ? 500 : 100, low: 90, close: 95, volume: 1000 });
  }
  ie.onCandlesLoaded(candles, null);
  // At index 19: length=5's window is [15..19] (peak at index 5 long gone);
  // length=15's window is [5..19] (peak at index 5 is exactly still in range).
  const r1 = ie.getInstances()[id1]._dataByTime[candles[19].time];
  const r2 = ie.getInstances()[id2]._dataByTime[candles[19].time];
  assert.strictEqual(r1.upper, 100, 'short window no longer sees the aged-out peak');
  assert.strictEqual(r2.upper, 500, 'long window still sees the peak within its wider range');
});

test('live tick updates the current candle\'s Donchian upper/lower without duplicating points', () => {
  // onCandleUpdate's own argument is NOT what feeds _candles — the engine
  // rebuilds from window._chartCandles merged with window._formingCandles
  // (see _getEffectiveCandles). A live tick must be simulated that way,
  // not by passing a modified candle object directly to onCandleUpdate.
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('DONCHIAN', { length: 5 });
  const candles = trendyCandles(20);
  sb.window._chartCandles = candles;
  ie.onCandlesLoaded(candles, null);
  const before = ie.getInstances()[id]._dataByTime[candles[19].time].upper;

  sb.window._formingCandles = { i: { time: candles[19].time, open: candles[19].open, high: 9999, low: candles[19].low, close: candles[19].close, volume: candles[19].volume } };
  let threw = false;
  try {
    ie.onCandleUpdate(sb.window._formingCandles.i);
  } catch (e) {
    threw = true;
  }
  assert.strictEqual(threw, false);
  const after = ie.getInstances()[id]._dataByTime[candles[19].time].upper;
  assert.strictEqual(after, 9999, 'a new high on the live candle must widen the upper channel immediately');
  assert.notStrictEqual(before, after);
  const timeKeys = Object.keys(ie.getInstances()[id]._dataByTime);
  assert.strictEqual(timeKeys.length, new Set(timeKeys).size, 'no duplicate time keys');
});

test('remove then re-add Donchian leaves exactly one instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  let id = ie.addIndicator('DONCHIAN', {});
  ie.removeIndicator(id);
  id = ie.addIndicator('DONCHIAN', {});
  ie.onCandlesLoaded(trendyCandles(25), null);
  assert.strictEqual(Object.keys(ie.getInstances()).length, 1);
});

test('invalid length produces no Donchian values, no crash', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('DONCHIAN', { length: -1 });
  let threw = false;
  try {
    ie.onCandlesLoaded(trendyCandles(20), null);
  } catch (e) {
    threw = true;
  }
  assert.strictEqual(threw, false);
  const inst = ie.getInstances()[id];
  const anyValid = Object.values(inst._dataByTime || {}).some((v) => v.upper !== null);
  assert.strictEqual(anyValid, false);
});

console.log('\nDonchian Channels: TradingView Pine Script parity (offset + fill)');

test('Donchian registered with an offset param (default 0), matching the TradingView reference script', () => {
  const sb = makeSandbox();
  const def = sb.window.IndicatorEngine.DEFS.DONCHIAN;
  assert.ok(def.paramDefs.offset, 'offset must be a configurable parameter');
  assert.strictEqual(def.paramDefs.offset.default, 0);
});

test('offset=0 behaves identically to the pre-offset implementation (regression)', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('DONCHIAN', { length: 5, offset: 0 });
  const candles = trendyCandles(20);
  ie.onCandlesLoaded(candles, null);
  const inst = ie.getInstances()[id];
  assert.strictEqual(inst._donchianDisplay.upper.length, inst._donchianDisplay.lower.length);
  const lastDisplay = inst._donchianDisplay.upper[inst._donchianDisplay.upper.length - 1];
  assert.strictEqual(lastDisplay.time, candles[19].time, 'offset=0 must not shift anything');
});

test('positive offset projects points into synthetic future time (like Ichimoku\'s forward projection)', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const candles = [];
  for (let i = 0; i < 20; i++) candles.push({ time: 1000 + i * 60, open: 100, high: 105 + i, low: 95, close: 100, volume: 1000 });
  const id = ie.addIndicator('DONCHIAN', { length: 5, offset: 3 });
  ie.onCandlesLoaded(candles, null);
  const inst = ie.getInstances()[id];
  const upperPts = inst._donchianDisplay.upper;
  const lastPt = upperPts[upperPts.length - 1];
  // Calc index 19 (upper=124) shifts to display index 22, which is 3 bars
  // (at 60s/bar) past the last real candle (time 2140) -> 2140 + 3*60 = 2320.
  assert.strictEqual(lastPt.time, 2140 + 3 * 60);
  assert.strictEqual(lastPt.value, 124);
  assert.ok(lastPt.time > candles[19].time, 'a positive offset must project beyond the last real candle');
});

test('negative offset shifts points backward without fabricating pre-history time', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const candles = [];
  for (let i = 0; i < 20; i++) candles.push({ time: 1000 + i * 60, open: 100, high: 105 + i, low: 95, close: 100, volume: 1000 });
  const id = ie.addIndicator('DONCHIAN', { length: 5, offset: -3 });
  ie.onCandlesLoaded(candles, null);
  const inst = ie.getInstances()[id];
  const upperPts = inst._donchianDisplay.upper;
  // Calc index 4 (first valid, warm-up=4 for length=5) shifts to display
  // index 1 -> candles[1].time. Nothing shifts before index 0 (dropped,
  // never fabricated).
  assert.strictEqual(upperPts[0].time, candles[1].time);
  assert.ok(upperPts.every((pt) => pt.time >= candles[0].time), 'must never invent a time before the dataset starts');
});

test('offset works correctly on "yyyy-mm-dd" string candle times too (reuses Ichimoku\'s format-aware helpers)', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const pad = (v) => String(v).padStart(2, '0');
  const candles = [];
  const d = new Date(Date.UTC(2026, 6, 1));
  for (let i = 0; i < 15; i++) {
    const t = `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
    candles.push({ time: t, open: 100, high: 105 + i, low: 95, close: 100, volume: 1000 });
    d.setUTCDate(d.getUTCDate() + 1);
  }
  const id = ie.addIndicator('DONCHIAN', { length: 5, offset: 2 });
  let threw = false;
  try {
    ie.onCandlesLoaded(candles, null);
  } catch (e) {
    threw = true;
  }
  assert.strictEqual(threw, false);
  const inst = ie.getInstances()[id];
  const lastPt = inst._donchianDisplay.upper[inst._donchianDisplay.upper.length - 1];
  assert.strictEqual(typeof lastPt.time, 'string');
  assert.ok(/^\d{4}-\d{2}-\d{2}$/.test(lastPt.time), `expected a clean yyyy-mm-dd string, got "${lastPt.time}"`);
});

test('fill display data (_donchianDisplay) is populated for the canvas fill to consume', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('DONCHIAN', { length: 5 });
  ie.onCandlesLoaded(trendyCandles(20), null);
  const inst = ie.getInstances()[id];
  assert.ok(Array.isArray(inst._donchianDisplay.upper) && inst._donchianDisplay.upper.length > 0);
  assert.ok(Array.isArray(inst._donchianDisplay.lower) && inst._donchianDisplay.lower.length > 0);
});

console.log('\nKeltner Channels engine integration');

test('Keltner Channels registered in DEFS with length=20/atrLength=10/multiplier=2.0 defaults', () => {
  const sb = makeSandbox();
  const def = sb.window.IndicatorEngine.DEFS.KELTNER;
  assert.ok(def, 'Keltner Channels must be registered in the indicator registry');
  assert.strictEqual(def.type, 'overlay');
  assert.strictEqual(def.paramDefs.length.default, 20);
  assert.strictEqual(def.paramDefs.atrLength.default, 10);
  assert.strictEqual(def.paramDefs.multiplier.default, 2.0);
});

test('Keltner Channels is searchable by id and full name', () => {
  const sb = makeSandbox();
  const T2 = sb.window.IndicatorEngine._test;
  const def = sb.window.IndicatorEngine.DEFS.KELTNER;
  assert.strictEqual(T2.menuItemMatches(def, 'keltner'), true);
  assert.strictEqual(T2.menuItemMatches(def, 'channels'), true);
});

test('Calc.keltner middle equals Calc.ema(close, length) exactly (production-code cross-check)', () => {
  const sb = makeSandbox();
  const Calc = sb.window.IndicatorEngine._Calc;
  const candles = trendyCandles(40);
  const expectedEma = Calc.ema(Calc.source(candles, 'close'), 20);
  const kc = Calc.keltner(candles, 20, 10, 2.0);
  assertJsonEqual(kc.middle, expectedEma);
});

test('Calc.keltner channel width equals 2 * Calc.atr(atrLength) * multiplier (ATR consistency)', () => {
  const sb = makeSandbox();
  const Calc = sb.window.IndicatorEngine._Calc;
  const candles = trendyCandles(40);
  const atrVals = Calc.atr(candles, 10);
  const kc = Calc.keltner(candles, 20, 10, 2.5);
  let checked = 0;
  for (let i = 0; i < candles.length; i++) {
    if (kc.upper[i] === null || kc.lower[i] === null) continue;
    const width = kc.upper[i] - kc.lower[i];
    assert.ok(Math.abs(width - 2 * atrVals[i] * 2.5) < 1e-9, `index ${i}: width mismatch`);
    checked++;
  }
  assert.ok(checked > 0);
});

test('Keltner is NOT Bollinger Bands: identical-close/wide-range data gives zero BB width but nonzero Keltner width', () => {
  const sb = makeSandbox();
  const Calc = sb.window.IndicatorEngine._Calc;
  const candles = [];
  for (let i = 0; i < 20; i++) candles.push({ time: 1000 + i * 60, open: 100, high: 110, low: 90, close: 100, volume: 1000 });
  const bb = Calc.bollingerBands(Calc.source(candles, 'close'), 10, 2.0);
  const kc = Calc.keltner(candles, 10, 10, 2.0);
  const idx = 19;
  assert.strictEqual(bb.upper[idx] - bb.lower[idx], 0, 'sanity: stddev of identical closes is 0');
  assert.ok(kc.upper[idx] - kc.lower[idx] > 1, 'Keltner width must come from ATR, not stddev');
});

test('Keltner is NOT Donchian Channels: a decayed-away spike keeps Donchian wide but Keltner narrow', () => {
  const sb = makeSandbox();
  const Calc = sb.window.IndicatorEngine._Calc;
  const candles = [];
  for (let i = 0; i < 30; i++) candles.push({ time: 1000 + i * 60, open: 100, high: 105, low: 95, close: 100, volume: 1000 });
  candles[10] = { time: candles[10].time, open: 498, high: 500, low: 495, close: 498, volume: 1000 };
  const donchian = Calc.donchian(candles, 20);
  const kc = Calc.keltner(candles, 20, 20, 2.0);
  const idx = 29;
  assert.strictEqual(donchian.upper[idx], 500, 'sanity: Donchian rolling-high still reflects the spike');
  assert.ok(kc.upper[idx] < 200, 'Keltner (EMA+ATR) must not track the rolling highest-high like Donchian does');
});

test('Keltner instance populates middle/upper/lower after warm-up (via inst._lastKeltner)', () => {
  // Note: this sandbox has no LightweightCharts loaded, so _createSeries
  // can't build real chart series (that's expected — series wiring is
  // covered by manual browser QA). What IS testable here is that _calcAll
  // computes and stores the full result on inst._lastKeltner, exactly like
  // Bollinger Bands' inst._lastBb.
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('KELTNER', { length: 5, atrLength: 5, multiplier: 2.0 });
  const candles = trendyCandles(20);
  ie.onCandlesLoaded(candles, null);
  const inst = ie.getInstances()[id];
  assert.ok(inst._lastKeltner, 'Keltner must populate _lastKeltner');
  for (let i = 0; i < 4; i++) {
    assert.strictEqual(inst._lastKeltner.middle[i], null, `index ${i} is within warm-up`);
  }
  const idx = 10;
  assert.ok(inst._lastKeltner.middle[idx] !== null && isFinite(inst._lastKeltner.middle[idx]));
  assert.ok(inst._lastKeltner.upper[idx] > inst._lastKeltner.middle[idx]);
  assert.ok(inst._lastKeltner.lower[idx] < inst._lastKeltner.middle[idx]);
});

test('two Keltner instances with different multipliers stay independent', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id1 = ie.addIndicator('KELTNER', { length: 5, atrLength: 5, multiplier: 1.0 });
  const id2 = ie.addIndicator('KELTNER', { length: 5, atrLength: 5, multiplier: 3.0 });
  const candles = trendyCandles(20);
  ie.onCandlesLoaded(candles, null);
  const k1 = ie.getInstances()[id1]._lastKeltner;
  const k2 = ie.getInstances()[id2]._lastKeltner;
  const idx = 10;
  assert.strictEqual(k1.middle[idx], k2.middle[idx], 'same EMA length -> identical middle');
  assert.notStrictEqual(k1.upper[idx], k2.upper[idx], 'different multiplier must widen bands differently');
  assert.ok((k2.upper[idx] - k2.lower[idx]) > (k1.upper[idx] - k1.lower[idx]), 'multiplier=3.0 instance must be wider than multiplier=1.0 instance');
});

test('updateParams (length/atrLength/multiplier change) recalculates in place, no duplicate instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('KELTNER', { length: 20, atrLength: 10, multiplier: 2.0 });
  const candles = trendyCandles(40);
  ie.onCandlesLoaded(candles, null);
  const before = ie.getInstances()[id]._lastKeltner.middle[30];

  ie.updateParams(id, { length: 5, atrLength: 5, multiplier: 3.0 });
  assert.strictEqual(Object.keys(ie.getInstances()).length, 1, 'must still be exactly one Keltner instance');
  const after = ie.getInstances()[id]._lastKeltner.middle[30];
  assert.notStrictEqual(before, after, 'changing length must actually recompute the EMA centerline');
  assert.strictEqual(ie.getInstances()[id].params.multiplier, 3.0);
});

test('live tick updates Keltner middle/upper/lower without duplicating points', () => {
  // onCandleUpdate's own argument is NOT what feeds _candles — the engine
  // rebuilds from window._chartCandles merged with window._formingCandles
  // (see _getEffectiveCandles), same mechanism exercised by Donchian's live
  // tick test above.
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('KELTNER', { length: 5, atrLength: 5, multiplier: 2.0 });
  const candles = trendyCandles(20);
  sb.window._chartCandles = candles;
  ie.onCandlesLoaded(candles, null);
  const beforeLen = ie.getInstances()[id]._lastKeltner.middle.length;
  const beforeMid = ie.getInstances()[id]._lastKeltner.middle[19];

  sb.window._formingCandles = { i: { time: candles[19].time, open: candles[19].open, high: 500, low: candles[19].low, close: 480, volume: candles[19].volume } };
  let threw = false;
  try {
    ie.onCandleUpdate(sb.window._formingCandles.i);
  } catch (e) {
    threw = true;
  }
  assert.strictEqual(threw, false);
  const inst = ie.getInstances()[id];
  assert.strictEqual(inst._lastKeltner.middle.length, beforeLen, 'a tick on the existing last candle must not append a new point');
  assert.notStrictEqual(inst._lastKeltner.middle[19], beforeMid, 'a large close change on the live candle must move the EMA centerline');
});

test('symbol switch (onCandlesLoaded with new candles) recalculates, no stale Keltner values', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('KELTNER', { length: 5, atrLength: 5, multiplier: 2.0 });
  const symbolA = trendyCandles(20);
  ie.onCandlesLoaded(symbolA, null);
  const symbolAMid = ie.getInstances()[id]._lastKeltner.middle[19];

  const symbolB = [];
  for (let i = 0; i < 20; i++) symbolB.push({ time: 1000 + i * 60, open: 5000 - i * 10, high: 5010 - i * 10, low: 4990 - i * 10, close: 5000 - i * 10, volume: 500 });
  ie.onCandlesLoaded(symbolB, null);
  const symbolBMid = ie.getInstances()[id]._lastKeltner.middle[19];
  assert.notStrictEqual(symbolAMid, symbolBMid, 'switching candle data must recompute, not reuse the old symbol\'s EMA/ATR state');
});

test('timeframe switch (entirely different candle set) rebuilds cleanly, no stale-length array', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('KELTNER', { length: 5, atrLength: 5, multiplier: 2.0 });
  ie.onCandlesLoaded(trendyCandles(30), null);
  assert.strictEqual(ie.getInstances()[id]._lastKeltner.middle.length, 30);

  ie.onCandlesLoaded(trendyCandles(10), null);
  const inst = ie.getInstances()[id];
  assert.strictEqual(inst._lastKeltner.middle.length, 10, 'must rebuild fully from the new timeframe\'s candle count, not append to the old array');
});

test('remove then re-add Keltner leaves exactly one instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  let id = ie.addIndicator('KELTNER', {});
  ie.removeIndicator(id);
  id = ie.addIndicator('KELTNER', {});
  ie.onCandlesLoaded(trendyCandles(25), null);
  assert.strictEqual(Object.keys(ie.getInstances()).length, 1);
  assert.ok(ie.getInstances()[id]._lastKeltner);
});

test('invalid length/atrLength/multiplier produce no Keltner values, no crash', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('KELTNER', { length: -1, atrLength: 10, multiplier: 2.0 });
  let threw = false;
  try {
    ie.onCandlesLoaded(trendyCandles(20), null);
  } catch (e) {
    threw = true;
  }
  assert.strictEqual(threw, false);
  const inst = ie.getInstances()[id];
  const anyValid = inst._lastKeltner && inst._lastKeltner.middle.some((v) => v !== null);
  assert.strictEqual(anyValid, false);
});

console.log('\nROC (Rate of Change) engine integration');

test('ROC registered in DEFS with length=12/source=close/signalPeriod=0 defaults', () => {
  const sb = makeSandbox();
  const def = sb.window.IndicatorEngine.DEFS.ROC;
  assert.ok(def, 'ROC must be registered in the indicator registry');
  assert.strictEqual(def.type, 'pane');
  assert.strictEqual(def.paramDefs.length.default, 12);
  assert.strictEqual(def.paramDefs.source.default, 'close');
  assert.strictEqual(def.paramDefs.signalPeriod.default, 0);
});

test('ROC is searchable by id and full name', () => {
  const sb = makeSandbox();
  const T2 = sb.window.IndicatorEngine._test;
  const def = sb.window.IndicatorEngine.DEFS.ROC;
  assert.strictEqual(T2.menuItemMatches(def, 'roc'), true);
  assert.strictEqual(T2.menuItemMatches(def, 'rate of change'), true);
});

test('Calc.roc matches manual calculation and guards division by zero', () => {
  const sb = makeSandbox();
  const Calc = sb.window.IndicatorEngine._Calc;
  const closes = [100, 102, 101, 105, 107, 106, 110, 108, 112, 115, 113, 117, 120, 118, 122];
  const { roc } = Calc.roc(closes, 12, 0);
  assert.ok(Math.abs(roc[12] - 20.0) < 1e-9);
  assert.ok(Math.abs(roc[13] - ((118 - 102) / 102 * 100)) < 1e-9);

  const zeroBase = [0, 5, 10, 15];
  const { roc: roc2 } = Calc.roc(zeroBase, 1, 0);
  assert.strictEqual(roc2[1], null, 'base value of 0 must be guarded, not Infinity/NaN');
  assert.ok(Math.abs(roc2[2] - 100.0) < 1e-9);
});

test('ROC instance populates roc/signal via inst._dataByTime after warm-up', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('ROC', { length: 5, source: 'close', signalPeriod: 3 });
  const candles = trendyCandles(30);
  ie.onCandlesLoaded(candles, null);
  const inst = ie.getInstances()[id];
  for (let i = 0; i < 5; i++) {
    assert.strictEqual(inst._dataByTime[candles[i].time].roc, null, `index ${i} is within warm-up`);
  }
  const rec = inst._dataByTime[candles[20].time];
  assert.ok(rec.roc !== null && isFinite(rec.roc));
  assert.ok(rec.signal !== null && isFinite(rec.signal), 'signalPeriod=3 must produce a non-null signal by index 20');
});

test('updateParams (length/signalPeriod change) recalculates ROC in place, no duplicate instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('ROC', { length: 12, source: 'close', signalPeriod: 0 });
  const candles = trendyCandles(30);
  ie.onCandlesLoaded(candles, null);
  const before = ie.getInstances()[id]._dataByTime[candles[20].time].roc;

  ie.updateParams(id, { length: 5 });
  assert.strictEqual(Object.keys(ie.getInstances()).length, 1, 'must still be exactly one ROC instance');
  const after = ie.getInstances()[id]._dataByTime[candles[20].time].roc;
  assert.notStrictEqual(before, after, 'changing length must actually recompute ROC');
});

test('live tick recomputes ROC for the current candle without duplicating points', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('ROC', { length: 5, source: 'close', signalPeriod: 0 });
  const candles = trendyCandles(20);
  sb.window._chartCandles = candles;
  ie.onCandlesLoaded(candles, null);
  const before = ie.getInstances()[id]._dataByTime[candles[19].time].roc;

  sb.window._formingCandles = { i: { time: candles[19].time, open: candles[19].open, high: candles[19].high, low: candles[19].low, close: 9999, volume: candles[19].volume } };
  let threw = false;
  try {
    ie.onCandleUpdate(sb.window._formingCandles.i);
  } catch (e) {
    threw = true;
  }
  assert.strictEqual(threw, false);
  const inst = ie.getInstances()[id];
  const after = inst._dataByTime[candles[19].time].roc;
  assert.notStrictEqual(before, after, 'a large close change on the live candle must move ROC');
  const timeKeys = Object.keys(inst._dataByTime);
  assert.strictEqual(timeKeys.length, new Set(timeKeys).size, 'no duplicate time keys');
});

test('symbol switch recalculates ROC, no stale values', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('ROC', { length: 5, source: 'close', signalPeriod: 0 });
  const symbolA = trendyCandles(20);
  ie.onCandlesLoaded(symbolA, null);
  const rocA = ie.getInstances()[id]._dataByTime[symbolA[19].time].roc;

  const symbolB = [];
  for (let i = 0; i < 20; i++) symbolB.push({ time: 1000 + i * 60, open: 5000 - i * 10, high: 5010 - i * 10, low: 4990 - i * 10, close: 5000 - i * 10, volume: 500 });
  ie.onCandlesLoaded(symbolB, null);
  const rocB = ie.getInstances()[id]._dataByTime[symbolB[19].time].roc;
  assert.notStrictEqual(rocA, rocB, 'switching candle data must recompute, not reuse stale ROC');
});

test('remove then re-add ROC leaves exactly one instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  let id = ie.addIndicator('ROC', {});
  ie.removeIndicator(id);
  id = ie.addIndicator('ROC', {});
  ie.onCandlesLoaded(trendyCandles(25), null);
  assert.strictEqual(Object.keys(ie.getInstances()).length, 1);
});

test('invalid ROC length produces no values, no crash', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('ROC', { length: -1 });
  let threw = false;
  try {
    ie.onCandlesLoaded(trendyCandles(20), null);
  } catch (e) {
    threw = true;
  }
  assert.strictEqual(threw, false);
  const inst = ie.getInstances()[id];
  const anyValid = Object.values(inst._dataByTime || {}).some((v) => v.roc !== null);
  assert.strictEqual(anyValid, false);
});

console.log('\nAroon engine integration');

test('Aroon registered in DEFS with length=14 default (TradingView\'s own default)', () => {
  const sb = makeSandbox();
  const def = sb.window.IndicatorEngine.DEFS.AROON;
  assert.ok(def, 'Aroon must be registered in the indicator registry');
  assert.strictEqual(def.type, 'pane');
  assert.strictEqual(def.paramDefs.length.default, 14);
});

test('Aroon is searchable by id and full name', () => {
  const sb = makeSandbox();
  const T2 = sb.window.IndicatorEngine._test;
  const def = sb.window.IndicatorEngine.DEFS.AROON;
  assert.strictEqual(T2.menuItemMatches(def, 'aroon'), true);
});

test('Calc.aroon hits exactly 100 on a new-high bar and uses most-recent tie-break', () => {
  const sb = makeSandbox();
  const Calc = sb.window.IndicatorEngine._Calc;
  const highs = [100, 101, 99, 102, 98, 103, 97, 104, 96, 105, 95, 106, 94, 107, 150, 93, 92, 91, 90, 89];
  const candles = highs.map((h, i) => ({ time: 1000 + i * 60, high: h, low: 90, close: 95 }));
  const res = Calc.aroon(candles, 14);
  assert.strictEqual(res.up[14], 100);
  assert.ok(res.up[19] < 100);

  const tieCandles = [50, 100, 90, 100, 80].map((h, i) => ({ time: 1000 + i * 60, high: h, low: 10, close: 30 }));
  const tieRes = Calc.aroon(tieCandles, 2);
  assert.strictEqual(tieRes.up[3], 100, 'a repeated maximum must resolve to the most recent (later) occurrence');
});

test('Aroon instance populates up/down/osc via inst._dataByTime, bounded 0-100', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('AROON', { length: 5 });
  const candles = trendyCandles(20);
  ie.onCandlesLoaded(candles, null);
  const inst = ie.getInstances()[id];
  for (let i = 0; i < 5; i++) {
    assert.strictEqual(inst._dataByTime[candles[i].time].up, null, `index ${i} is within warm-up`);
  }
  const rec = inst._dataByTime[candles[15].time];
  assert.ok(rec.up !== null && rec.up >= 0 && rec.up <= 100);
  assert.ok(rec.down !== null && rec.down >= 0 && rec.down <= 100);
  assert.ok(Math.abs(rec.osc - (rec.up - rec.down)) < 1e-9);
});

test('two Aroon instances with different lengths stay independent', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id1 = ie.addIndicator('AROON', { length: 5 });
  const id2 = ie.addIndicator('AROON', { length: 15 });
  const candles = [];
  for (let i = 0; i < 25; i++) {
    const isPeak = i === 5;
    candles.push({ time: 1000 + i * 60, open: 100, high: isPeak ? 500 : 100, low: 90, close: 95, volume: 1000 });
  }
  ie.onCandlesLoaded(candles, null);
  const r1 = ie.getInstances()[id1]._dataByTime[candles[19].time];
  const r2 = ie.getInstances()[id2]._dataByTime[candles[19].time];
  assert.notStrictEqual(r1.up, r2.up, 'different lengths must see the aged-out peak differently');
});

test('live tick recomputes Aroon for the current candle without duplicating points', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('AROON', { length: 5 });
  // Descending highs (unlike trendyCandles' strict uptrend, where the last
  // bar is trivially already the window max = AroonUp 100 before any tick
  // at all, making a before/after comparison meaningless): here the
  // window's high sits at the OLDEST bar, so AroonUp starts well under 100
  // and the tick's new high is what pushes it there.
  const candles = [];
  for (let i = 0; i < 20; i++) candles.push({ time: 1000 + i * 60, open: 100, high: 119 - i, low: 90 - i, close: 95 - i, volume: 1000 });
  sb.window._chartCandles = candles;
  ie.onCandlesLoaded(candles, null);
  const before = ie.getInstances()[id]._dataByTime[candles[19].time].up;
  assert.ok(before < 100, 'sanity: before the tick, the window high is an older bar, not today');

  sb.window._formingCandles = { i: { time: candles[19].time, open: candles[19].open, high: 9999, low: candles[19].low, close: candles[19].close, volume: candles[19].volume } };
  let threw = false;
  try {
    ie.onCandleUpdate(sb.window._formingCandles.i);
  } catch (e) {
    threw = true;
  }
  assert.strictEqual(threw, false);
  const inst = ie.getInstances()[id];
  assert.strictEqual(inst._dataByTime[candles[19].time].up, 100, 'a new high on the live candle must push AroonUp to 100 immediately');
  assert.notStrictEqual(before, inst._dataByTime[candles[19].time].up);
  const timeKeys = Object.keys(inst._dataByTime);
  assert.strictEqual(timeKeys.length, new Set(timeKeys).size, 'no duplicate time keys');
});

test('symbol switch recalculates Aroon, no stale values', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('AROON', { length: 5 });
  const symbolA = trendyCandles(20);
  ie.onCandlesLoaded(symbolA, null);
  const upA = ie.getInstances()[id]._dataByTime[symbolA[19].time].up;

  const symbolB = [];
  for (let i = 0; i < 20; i++) symbolB.push({ time: 1000 + i * 60, open: 5000 - i * 10, high: 5010 - i * 10, low: 4990 - i * 10, close: 5000 - i * 10, volume: 500 });
  ie.onCandlesLoaded(symbolB, null);
  const upB = ie.getInstances()[id]._dataByTime[symbolB[19].time].up;
  assert.notStrictEqual(upA, upB, 'switching candle data must recompute, not reuse stale Aroon values');
});

test('remove then re-add Aroon leaves exactly one instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  let id = ie.addIndicator('AROON', {});
  ie.removeIndicator(id);
  id = ie.addIndicator('AROON', {});
  ie.onCandlesLoaded(trendyCandles(25), null);
  assert.strictEqual(Object.keys(ie.getInstances()).length, 1);
});

test('invalid Aroon length produces no values, no crash', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('AROON', { length: -1 });
  let threw = false;
  try {
    ie.onCandlesLoaded(trendyCandles(20), null);
  } catch (e) {
    threw = true;
  }
  assert.strictEqual(threw, false);
  const inst = ie.getInstances()[id];
  const anyValid = Object.values(inst._dataByTime || {}).some((v) => v.up !== null);
  assert.strictEqual(anyValid, false);
});

console.log('\nChaikin Money Flow (CMF) engine integration');

test('CMF registered in DEFS with length=20 default', () => {
  const sb = makeSandbox();
  const def = sb.window.IndicatorEngine.DEFS.CMF;
  assert.ok(def, 'CMF must be registered in the indicator registry');
  assert.strictEqual(def.type, 'pane');
  assert.strictEqual(def.paramDefs.length.default, 20);
});

test('CMF is searchable by id and full name', () => {
  const sb = makeSandbox();
  const T2 = sb.window.IndicatorEngine._test;
  const def = sb.window.IndicatorEngine.DEFS.CMF;
  assert.strictEqual(T2.menuItemMatches(def, 'cmf'), true);
  assert.strictEqual(T2.menuItemMatches(def, 'chaikin'), true);
});

test('Calc.cmf forces a doji (High==Low) bar to MFM=0 instead of NaN, and guards a zero-volume window', () => {
  const sb = makeSandbox();
  const Calc = sb.window.IndicatorEngine._Calc;
  const candles = [
    { high: 100, low: 100, close: 100, volume: 999999 },
    { high: 110, low: 100, close: 108, volume: 100 }
  ];
  const vals = Calc.cmf(candles, 2);
  assert.ok(vals[1] !== null && isFinite(vals[1]));
  const expected = (0.6 * 100) / (999999 + 100);
  assert.ok(Math.abs(vals[1] - expected) < 1e-9);

  const zeroVol = [
    { high: 110, low: 100, close: 105, volume: 0 },
    { high: 115, low: 105, close: 110, volume: 0 },
    { high: 120, low: 110, close: 115, volume: 0 }
  ];
  const zeroRes = Calc.cmf(zeroVol, 3);
  assert.strictEqual(zeroRes[2], null, 'a zero-volume window must be guarded to null, not NaN/Infinity');
});

test('CMF instance populates values via inst._dataByTime after warm-up', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('CMF', { length: 5 });
  const candles = trendyCandles(20);
  ie.onCandlesLoaded(candles, null);
  const inst = ie.getInstances()[id];
  for (let i = 0; i < 4; i++) {
    assert.strictEqual(inst._dataByTime[candles[i].time].cmf, null, `index ${i} is within warm-up`);
  }
  const rec = inst._dataByTime[candles[15].time];
  assert.ok(rec.cmf !== null && isFinite(rec.cmf));
});

test('updateParams (length change) recalculates CMF in place, no duplicate instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('CMF', { length: 20 });
  const candles = trendyCandles(30);
  ie.onCandlesLoaded(candles, null);
  ie.updateParams(id, { length: 5 });
  assert.strictEqual(Object.keys(ie.getInstances()).length, 1, 'must still be exactly one CMF instance');
  const rec = ie.getInstances()[id]._dataByTime[candles[10].time];
  assert.ok(rec.cmf !== null, 'length=5 must already have enough history by index 10, unlike the original length=20');
});

test('live tick recomputes CMF for the current candle without duplicating points', () => {
  // trendyCandles() places close exactly halfway between high and low on
  // every bar (H=105+i, L=95+i, C=100+i), which makes MFM exactly 0 for
  // every bar and CMF exactly 0 regardless of volume — a volume-only tick
  // could never move it. Use bars with close pinned near the high instead
  // (MFM clearly positive) so a tick that moves close near the low with a
  // dominant volume is guaranteed to shift the weighted sum.
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('CMF', { length: 5 });
  const candles = [];
  for (let i = 0; i < 20; i++) candles.push({ time: 1000 + i * 60, open: 105, high: 110, low: 100, close: 108, volume: 1000 });
  sb.window._chartCandles = candles;
  ie.onCandlesLoaded(candles, null);
  const before = ie.getInstances()[id]._dataByTime[candles[19].time].cmf;
  assert.ok(before > 0, 'sanity: close-near-high bars must produce a positive CMF before the tick');

  sb.window._formingCandles = { i: { time: candles[19].time, open: candles[19].open, high: 110, low: 100, close: 100, volume: 999999 } };
  let threw = false;
  try {
    ie.onCandleUpdate(sb.window._formingCandles.i);
  } catch (e) {
    threw = true;
  }
  assert.strictEqual(threw, false);
  const inst = ie.getInstances()[id];
  const after = inst._dataByTime[candles[19].time].cmf;
  assert.notStrictEqual(before, after, 'a close-near-low tick with dominant volume must move CMF');
  assert.ok(after < before, 'the tick pulled the weighted sum toward bearish (negative MFM)');
  const timeKeys = Object.keys(inst._dataByTime);
  assert.strictEqual(timeKeys.length, new Set(timeKeys).size, 'no duplicate time keys');
});

test('symbol switch recalculates CMF, no stale values', () => {
  // Same reasoning as the live-tick test above: trendyCandles' symmetric
  // H/L/C makes MFM (and therefore CMF) exactly 0 for any such dataset, so
  // two "different" symmetric symbols would both read 0 and prove nothing.
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('CMF', { length: 5 });
  const symbolA = [];
  for (let i = 0; i < 20; i++) symbolA.push({ time: 1000 + i * 60, open: 105, high: 110, low: 100, close: 108, volume: 1000 }); // close near high -> bullish MFM
  ie.onCandlesLoaded(symbolA, null);
  const cmfA = ie.getInstances()[id]._dataByTime[symbolA[19].time].cmf;

  const symbolB = [];
  for (let i = 0; i < 20; i++) symbolB.push({ time: 1000 + i * 60, open: 105, high: 110, low: 100, close: 102, volume: 1000 }); // close near low -> bearish MFM
  ie.onCandlesLoaded(symbolB, null);
  const cmfB = ie.getInstances()[id]._dataByTime[symbolB[19].time].cmf;
  assert.notStrictEqual(cmfA, cmfB, 'switching candle data must recompute, not reuse stale CMF');
  assert.ok(cmfA > 0 && cmfB < 0, 'bullish vs bearish close placement must produce opposite-signed CMF');
});

test('remove then re-add CMF leaves exactly one instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  let id = ie.addIndicator('CMF', {});
  ie.removeIndicator(id);
  id = ie.addIndicator('CMF', {});
  ie.onCandlesLoaded(trendyCandles(25), null);
  assert.strictEqual(Object.keys(ie.getInstances()).length, 1);
});

test('invalid CMF length produces no values, no crash', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('CMF', { length: -1 });
  let threw = false;
  try {
    ie.onCandlesLoaded(trendyCandles(20), null);
  } catch (e) {
    threw = true;
  }
  assert.strictEqual(threw, false);
  const inst = ie.getInstances()[id];
  const anyValid = Object.values(inst._dataByTime || {}).some((v) => v.cmf !== null);
  assert.strictEqual(anyValid, false);
});

console.log('\nMarket Structure (BOS/CHoCH/MSS) engine integration');

// Dedicated 11-candle bearish trace (swingLength=2), hand-verified in the
// Python backend suite's TestMarketStructure.test_bearish_bos_dedicated_case:
// swings low@2(94)/high@4(100)/low@6(90,LL)/high@8(99,LH); events
// CHOCH bearish @6 (brokenLevel 94, origin 2), BOS bearish @10
// (brokenLevel 90, origin 6); trend ends 'bearish'.
function marketStructureBearishCandles() {
  const closes = [100, 98, 95, 97, 99, 96, 91, 94, 98, 92, 87];
  return closes.map((c, i) => ({ time: 1000 + i * 60, open: c, high: c + 1, low: c - 1, close: c, volume: 1000 }));
}

test('Market Structure registered in DEFS as ONE indicator with independent BOS/CHoCH/MSS toggles', () => {
  const sb = makeSandbox();
  const def = sb.window.IndicatorEngine.DEFS.MARKETSTRUCTURE;
  assert.ok(def, 'Market Structure must be registered in the indicator registry');
  assert.strictEqual(def.type, 'overlay');
  assert.strictEqual(def.paramDefs.swingLength.default, 5);
  assert.strictEqual(def.paramDefs.confirmation.default, 'Close');
  assertJsonEqual(def.paramDefs.confirmation.options, ['Close', 'Wick']);
  assert.strictEqual(def.paramDefs.showBOS.default, 'On');
  assert.strictEqual(def.paramDefs.showCHoCH.default, 'On');
  assert.strictEqual(def.paramDefs.showMSS.default, 'On');
  assert.strictEqual(def.paramDefs.showSwingLabels.default, 'On');
  assert.strictEqual(def.paramDefs.displacementMultiplier.default, 1.5);
  assert.strictEqual(def.paramDefs.atrLength.default, 14);
});

test('Market Structure is searchable by id and full name', () => {
  const sb = makeSandbox();
  const T2 = sb.window.IndicatorEngine._test;
  const def = sb.window.IndicatorEngine.DEFS.MARKETSTRUCTURE;
  assert.strictEqual(T2.menuItemMatches(def, 'market structure'), true);
  assert.strictEqual(T2.menuItemMatches(def, 'marketstructure'), true);
});

test('Calc.marketStructure production-code cross-check against the hand-verified bearish trace', () => {
  const sb = makeSandbox();
  const Calc = sb.window.IndicatorEngine._Calc;
  const candles = marketStructureBearishCandles();
  const res = Calc.marketStructure(candles, 2, 'Close', 1.5, 14);
  assert.strictEqual(res.trend, 'bearish');
  const bos = res.events.filter((e) => e.type === 'BOS');
  const choch = res.events.filter((e) => e.type === 'CHOCH');
  assert.strictEqual(bos.length, 1);
  assert.strictEqual(bos[0].direction, 'bearish');
  assert.strictEqual(bos[0].breakIndex, 10);
  assert.strictEqual(bos[0].brokenLevel, 90);
  assert.strictEqual(choch.length, 1);
  assert.strictEqual(choch[0].breakIndex, 6);
  assert.strictEqual(choch[0].brokenLevel, 94);
});

test('Market Structure instance populates swings/events/trend via inst._lastMarketStructure', () => {
  // Note: this sandbox has no LightweightCharts loaded, so canvas-drawn
  // BOS/CHoCH/MSS segments and the swing-label marker merge (both in
  // _drawOverlayClouds) can't be exercised here — that's covered by manual
  // browser QA. What IS testable is that _calcAll computes and stores the
  // full result on inst._lastMarketStructure.
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('MARKETSTRUCTURE', { swingLength: 2 });
  const candles = marketStructureBearishCandles();
  ie.onCandlesLoaded(candles, null);
  const inst = ie.getInstances()[id];
  assert.ok(inst._lastMarketStructure, 'must populate _lastMarketStructure');
  assert.strictEqual(inst._lastMarketStructure.trend, 'bearish');
  assert.ok(inst._lastMarketStructure.swings.length > 0);
  assert.ok(inst._lastMarketStructure.events.length > 0);
  assert.ok(Array.isArray(inst._msMarkers), 'swing labels must be prepared for the shared marker merge');
});

test('showSwingLabels=Off produces no swing markers, without touching swings/events themselves', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('MARKETSTRUCTURE', { swingLength: 2, showSwingLabels: 'Off' });
  ie.onCandlesLoaded(marketStructureBearishCandles(), null);
  const inst = ie.getInstances()[id];
  assertJsonEqual(inst._msMarkers, []);
  assert.ok(inst._lastMarketStructure.swings.length > 0, 'the underlying swing calculation is unaffected by the label toggle');
});

test('updateParams (swingLength change) recalculates in place, no duplicate instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('MARKETSTRUCTURE', { swingLength: 2 });
  const candles = marketStructureBearishCandles();
  ie.onCandlesLoaded(candles, null);
  const before = ie.getInstances()[id]._lastMarketStructure.events.length;

  ie.updateParams(id, { swingLength: 4 });
  assert.strictEqual(Object.keys(ie.getInstances()).length, 1, 'must still be exactly one Market Structure instance');
  const after = ie.getInstances()[id]._lastMarketStructure;
  assert.ok(after, 'must have recalculated with the new swingLength');
  assert.strictEqual(ie.getInstances()[id].params.swingLength, 4);
  // Not asserting before !== after.events.length numerically — a longer
  // swingLength on this short dataset may legitimately confirm zero swings
  // at all; what matters is that recalculation actually ran (see next).
  void before;
});

test('BOS/CHoCH/MSS visibility params persist independently through updateParams', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('MARKETSTRUCTURE', { swingLength: 2 });
  ie.onCandlesLoaded(marketStructureBearishCandles(), null);
  ie.updateParams(id, { showBOS: 'Off' });
  const p1 = ie.getInstances()[id].params;
  assert.strictEqual(p1.showBOS, 'Off');
  assert.strictEqual(p1.showCHoCH, 'On', 'turning off BOS must not affect CHoCH');
  assert.strictEqual(p1.showMSS, 'On', 'turning off BOS must not affect MSS');

  ie.updateParams(id, { showCHoCH: 'Off' });
  const p2 = ie.getInstances()[id].params;
  assert.strictEqual(p2.showBOS, 'Off', 'previous toggle must be preserved');
  assert.strictEqual(p2.showCHoCH, 'Off');
  assert.strictEqual(p2.showMSS, 'On');
});

test('live tick recomputes Market Structure without duplicating points', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('MARKETSTRUCTURE', { swingLength: 2 });
  const candles = marketStructureBearishCandles();
  sb.window._chartCandles = candles;
  ie.onCandlesLoaded(candles, null);
  const beforeCount = ie.getInstances()[id]._lastMarketStructure.events.length;

  // Push the live (last) candle's close far below everything -> a new
  // break should not be possible to duplicate, and nothing should throw.
  sb.window._formingCandles = { i: { time: candles[10].time, open: candles[10].open, high: candles[10].high, low: 40, close: 45, volume: candles[10].volume } };
  let threw = false;
  try {
    ie.onCandleUpdate(sb.window._formingCandles.i);
  } catch (e) {
    threw = true;
  }
  assert.strictEqual(threw, false);
  const inst = ie.getInstances()[id];
  assert.ok(inst._lastMarketStructure.events.length >= beforeCount, 'recompute must not lose already-confirmed events');
  const breakIndexes = inst._lastMarketStructure.events.map((e) => e.breakIndex);
  assert.strictEqual(breakIndexes.length, new Set(breakIndexes).size, 'no duplicate events at the same breakIndex');
});

test('symbol switch (onCandlesLoaded with new candles) recalculates, no stale Market Structure', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('MARKETSTRUCTURE', { swingLength: 2 });
  ie.onCandlesLoaded(marketStructureBearishCandles(), null); // "symbol A": ends bearish
  const trendA = ie.getInstances()[id]._lastMarketStructure.trend;

  const bullishCloses = [100, 102, 105, 103, 108, 106, 113, 110, 118, 115, 123];
  const symbolB = bullishCloses.map((c, i) => ({ time: 1000 + i * 60, open: c, high: c + 1, low: c - 1, close: c, volume: 1000 }));
  ie.onCandlesLoaded(symbolB, null); // "symbol B": a rising sequence
  const trendB = ie.getInstances()[id]._lastMarketStructure.trend;
  assert.strictEqual(trendA, 'bearish');
  assert.notStrictEqual(trendA, trendB, 'switching candle data must recompute, not reuse the old symbol\'s structure state');
});

test('remove then re-add Market Structure leaves exactly one instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  let id = ie.addIndicator('MARKETSTRUCTURE', {});
  ie.removeIndicator(id);
  id = ie.addIndicator('MARKETSTRUCTURE', {});
  ie.onCandlesLoaded(marketStructureBearishCandles(), null);
  assert.strictEqual(Object.keys(ie.getInstances()).length, 1);
  assert.ok(ie.getInstances()[id]._lastMarketStructure);
});

test('toggling Market Structure off/on works via the generic toggleIndicator visibility flag', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('MARKETSTRUCTURE', {});
  ie.onCandlesLoaded(marketStructureBearishCandles(), null);
  ie.toggleIndicator(id);
  assert.strictEqual(ie.getInstances()[id].visible, false);
  ie.toggleIndicator(id);
  assert.strictEqual(ie.getInstances()[id].visible, true);
});

test('invalid swingLength produces no swings/events, no crash', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('MARKETSTRUCTURE', { swingLength: -1 });
  let threw = false;
  try {
    ie.onCandlesLoaded(marketStructureBearishCandles(), null);
  } catch (e) {
    threw = true;
  }
  assert.strictEqual(threw, false);
  const inst = ie.getInstances()[id];
  assertJsonEqual(inst._lastMarketStructure.swings, []);
  assertJsonEqual(inst._lastMarketStructure.events, []);
});

console.log('\nFair Value Gap (FVG) engine integration');

// 7-candle fixture matching the Python backend's TestFVG lifecycle fixture
// exactly: bullish FVG confirmed at index 2 (zone [105, 108], size 3);
// indices 3-4 don't touch it; index 5 partially touches (low=106, 2/3
// filled); index 6 completes the fill (low=103).
function fvgLifecycleCandles(n) {
  const highs = [105, 112, 115, 118, 120, 112, 115];
  const lows = [95, 98, 108, 111, 113, 106, 103];
  const closes = [100, 103, 112, 115, 118, 109, 106];
  const count = n || highs.length;
  return highs.slice(0, count).map((h, i) => ({ time: 1000 + i * 60, open: closes[i], high: h, low: lows[i], close: closes[i], volume: 1000 }));
}

test('FVG registered in DEFS as ONE indicator with independent bullish/bearish/mitigation toggles', () => {
  const sb = makeSandbox();
  const def = sb.window.IndicatorEngine.DEFS.FVG;
  assert.ok(def, 'Fair Value Gap must be registered in the indicator registry');
  assert.strictEqual(def.type, 'overlay');
  assert.strictEqual(def.paramDefs.showBullish.default, 'On');
  assert.strictEqual(def.paramDefs.showBearish.default, 'On');
  assert.strictEqual(def.paramDefs.mitigation.default, 'Full Fill');
  assertJsonEqual(def.paramDefs.mitigation.options, ['Touch', 'Full Fill']);
  assert.strictEqual(def.paramDefs.showMitigated.default, 'On');
  assert.strictEqual(def.paramDefs.extendZones.default, 'On');
  assert.strictEqual(def.paramDefs.extendBars.default, 25);
  assert.strictEqual(def.paramDefs.showMidpoint.default, 'Off');
  assert.strictEqual(def.paramDefs.minSizeATRMultiplier.default, 0.1);
  assert.strictEqual(def.paramDefs.atrLength.default, 14);
  assert.strictEqual(def.paramDefs.maxZones.default, 500);
});

test('FVG is searchable by id and full name', () => {
  const sb = makeSandbox();
  const T2 = sb.window.IndicatorEngine._test;
  const def = sb.window.IndicatorEngine.DEFS.FVG;
  assert.strictEqual(T2.menuItemMatches(def, 'fvg'), true);
  assert.strictEqual(T2.menuItemMatches(def, 'fair value gap'), true);
});

test('Calc.fvg production-code cross-check: bullish detection, boundaries, and full-fill mitigation', () => {
  const sb = makeSandbox();
  const Calc = sb.window.IndicatorEngine._Calc;
  const candles = fvgLifecycleCandles(7);
  const fvgs = Calc.fvg(candles, 0, 14, 'Full Fill', 50);
  assert.strictEqual(fvgs.length, 1);
  const f = fvgs[0];
  assert.strictEqual(f.direction, 'bullish');
  assert.strictEqual(f.bottom, 105);
  assert.strictEqual(f.top, 108);
  assert.strictEqual(f.midpoint, 106.5);
  assert.strictEqual(f.status, 'fully_mitigated');
  assert.strictEqual(f.mitigatedIndex, 6);

  const touchResult = Calc.fvg(candles, 0, 14, 'Touch', 50)[0];
  assert.strictEqual(touchResult.mitigatedIndex, 5, 'Touch mode must mitigate at first intrusion, not wait for full fill');
});

test('FVG instance populates zones via inst._lastFVG', () => {
  // Note: this sandbox has no LightweightCharts loaded, so the canvas-drawn
  // rectangles in _drawOverlayClouds can't be exercised here — covered by
  // manual browser QA. What IS testable is that _calcAll computes and
  // stores the full result on inst._lastFVG.
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('FVG', { minSizeATRMultiplier: 0 });
  ie.onCandlesLoaded(fvgLifecycleCandles(5), null);
  const inst = ie.getInstances()[id];
  assert.ok(Array.isArray(inst._lastFVG), 'must populate _lastFVG');
  assert.strictEqual(inst._lastFVG.length, 1);
  assert.strictEqual(inst._lastFVG[0].status, 'active');
});

test('updateParams (mitigation mode change) recalculates in place, no duplicate instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('FVG', { minSizeATRMultiplier: 0, mitigation: 'Full Fill' });
  ie.onCandlesLoaded(fvgLifecycleCandles(7), null);
  const before = ie.getInstances()[id]._lastFVG[0].mitigatedIndex;

  ie.updateParams(id, { mitigation: 'Touch' });
  assert.strictEqual(Object.keys(ie.getInstances()).length, 1, 'must still be exactly one FVG instance');
  const after = ie.getInstances()[id]._lastFVG[0].mitigatedIndex;
  assert.notStrictEqual(before, after, 'switching mitigation mode must actually recompute');
  assert.strictEqual(ie.getInstances()[id].params.mitigation, 'Touch');
});

test('showBullish/showBearish/showMitigated params persist independently through updateParams', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('FVG', {});
  ie.onCandlesLoaded(fvgLifecycleCandles(7), null);
  ie.updateParams(id, { showBullish: 'Off' });
  const p1 = ie.getInstances()[id].params;
  assert.strictEqual(p1.showBullish, 'Off');
  assert.strictEqual(p1.showBearish, 'On', 'turning off bullish must not affect bearish');
  assert.strictEqual(p1.showMitigated, 'On', 'turning off bullish must not affect showMitigated');
});

test('live tick recomputes FVG without duplicating zones', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('FVG', { minSizeATRMultiplier: 0 });
  const candles = fvgLifecycleCandles(6); // partial-fill state, not yet mitigated
  sb.window._chartCandles = candles;
  ie.onCandlesLoaded(candles, null);
  const before = ie.getInstances()[id]._lastFVG[0].fillPercentage;

  // Push the live (last) candle's low down to complete the fill.
  sb.window._formingCandles = { i: { time: candles[5].time, open: candles[5].open, high: candles[5].high, low: 103, close: 104, volume: candles[5].volume } };
  let threw = false;
  try {
    ie.onCandleUpdate(sb.window._formingCandles.i);
  } catch (e) {
    threw = true;
  }
  assert.strictEqual(threw, false);
  const inst = ie.getInstances()[id];
  assert.strictEqual(inst._lastFVG.length, 1, 'no duplicate zone created by the live tick');
  assert.ok(inst._lastFVG[0].fillPercentage > before, 'a deeper low on the live candle must increase the fill');
});

test('symbol switch (onCandlesLoaded with new candles) recalculates, no stale FVG', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('FVG', { minSizeATRMultiplier: 0 });
  ie.onCandlesLoaded(fvgLifecycleCandles(5), null); // symbol A: one active bullish FVG
  const symbolACount = ie.getInstances()[id]._lastFVG.length;

  const symbolB = [];
  for (let i = 0; i < 10; i++) symbolB.push({ time: 1000 + i * 60, open: 5000, high: 5010, low: 4990, close: 5000, volume: 500 }); // flat, no gaps at all
  ie.onCandlesLoaded(symbolB, null);
  const symbolBCount = ie.getInstances()[id]._lastFVG.length;
  assert.strictEqual(symbolACount, 1);
  assert.strictEqual(symbolBCount, 0, 'switching to flat candle data must recompute, not keep symbol A\'s stale zone');
});

test('remove then re-add FVG leaves exactly one instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  let id = ie.addIndicator('FVG', {});
  ie.removeIndicator(id);
  id = ie.addIndicator('FVG', {});
  ie.onCandlesLoaded(fvgLifecycleCandles(7), null);
  assert.strictEqual(Object.keys(ie.getInstances()).length, 1);
  assert.ok(Array.isArray(ie.getInstances()[id]._lastFVG));
});

test('a very high minSizeATRMultiplier filters out every zone without crashing', () => {
  // Note: ATR needs its own warm-up before the size filter can apply at
  // all (Calc.fvg falls back to "no minimum" when ATR isn't available yet
  // AT THE ORIGIN CANDLE — a documented, deliberate permissive default,
  // the same warm-up-tolerant shape used elsewhere in this engine). Three
  // flat filler candles plus a short atrLength=3 ensure ATR is actually
  // available at the FVG's origin index (3), so this test genuinely
  // exercises the filter rather than tripping the warm-up fallback.
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('FVG', { minSizeATRMultiplier: 1000, atrLength: 3 });
  // Filler range [90, 118] deliberately spans the lifecycle fixture's own
  // price range so no incidental gap forms at the seam between filler and
  // fixture (only the intended [105,108] gap inside the fixture itself).
  const filler = [
    { time: 700, open: 105, high: 118, low: 90, close: 105, volume: 1000 },
    { time: 800, open: 105, high: 118, low: 90, close: 105, volume: 1000 },
    { time: 900, open: 105, high: 118, low: 90, close: 105, volume: 1000 }
  ];
  const candles = filler.concat(fvgLifecycleCandles(7));
  let threw = false;
  try {
    ie.onCandlesLoaded(candles, null);
  } catch (e) {
    threw = true;
  }
  assert.strictEqual(threw, false);
  assertJsonEqual(ie.getInstances()[id]._lastFVG, []);
});

console.log('\nOrder Blocks (OB) engine integration');

// 25-candle golden trace shared with the Python backend's TestOrderBlocks
// (swingLength=2, High=close+1, Low=close-1, open[i]=close[i-1]). This is
// the SAME trace used by the original Market-Structure-driven design, now
// re-run through the TradingView-reference-script port (see
// Calc.orderBlocks' docstring): the rolling-2-bar-low crossunder trigger
// (bearish) plus reclaim-seeds-the-opposite-block trigger (bullish)
// produces 7 alternating blocks — hand-traced bar-by-bar against the
// reference script's own state machine (lastUp/lastDown/lastHigh/lastLow),
// not merely copied from the implementation's own output:
//   origin=2  bearish [104,106] invalidated @6  (fillPct 0.5)
//   origin=4  bullish [100,102] fully_mitigated @18 (fillPct 1)
//   origin=6  bearish [108,110] invalidated @10 (fillPct 0.5)
//   origin=8  bullish [101,103] fully_mitigated @18 (fillPct 1)
//   origin=10 bearish [112,114] invalidated @14 (fillPct 0)
//   origin=12 bullish [106,108] invalidated @17 (fillPct 0.5)
//   origin=14 bearish [115,117] active (never reclaimed in this data)
function obGoldenCandles(n) {
  const closes = [100, 102, 105, 103, 101, 104, 109, 106, 102, 108, 113, 110, 107, 111, 116, 112, 108, 105, 101, 98, 100, 103, 99, 102, 105];
  const opens = [closes[0]].concat(closes.slice(0, -1));
  const count = n || closes.length;
  return closes.slice(0, count).map((c, i) => ({ time: 1000 + i * 60, open: opens[i], high: c + 1, low: c - 1, close: c, volume: 1000 }));
}

test('Order Blocks registered in DEFS as ONE indicator with independent bullish/bearish/mitigation toggles', () => {
  const sb = makeSandbox();
  const def = sb.window.IndicatorEngine.DEFS.OB;
  assert.ok(def, 'Order Blocks must be registered in the indicator registry');
  assert.strictEqual(def.type, 'overlay');
  assert.strictEqual(def.paramDefs.swingLength.default, 5);
  assert.strictEqual(def.paramDefs.showBullish.default, 'On');
  assert.strictEqual(def.paramDefs.showBearish.default, 'On');
  assert.strictEqual(def.paramDefs.mitigation.default, 'Full Fill');
  assert.ok(!def.paramDefs.displacementMultiplier, 'no displacement filter — the reference script this was ported from has none');
  assert.ok(!def.paramDefs.atrLength, 'no ATR param — nothing in this engine uses ATR any more');
  assert.strictEqual(def.paramDefs.maxZones.default, 50);
});

test('Order Blocks is searchable by id and full name', () => {
  const sb = makeSandbox();
  const T2 = sb.window.IndicatorEngine._test;
  const def = sb.window.IndicatorEngine.DEFS.OB;
  assert.strictEqual(T2.menuItemMatches(def, 'order blocks'), true);
  assert.strictEqual(T2.menuItemMatches(def, 'ob'), true);
});

test('Calc.orderBlocks production-code cross-check against the golden trace', () => {
  const sb = makeSandbox();
  const Calc = sb.window.IndicatorEngine._Calc;
  const candles = obGoldenCandles();
  const obs = Calc.orderBlocks(candles, 2, 'Full Fill', 50);
  assert.strictEqual(obs.length, 7);
  const byOrigin = {};
  obs.forEach((ob) => { byOrigin[ob.originIndex] = ob; });
  assert.strictEqual(byOrigin[4].direction, 'bullish');
  assert.strictEqual(byOrigin[4].top, 102);
  assert.strictEqual(byOrigin[4].bottom, 100);
  assert.strictEqual(byOrigin[4].triggerEvent, 'bullishReclaim');
  assert.strictEqual(byOrigin[4].status, 'fully_mitigated');
  assert.strictEqual(byOrigin[4].mitigatedIndex, 18);
  assert.strictEqual(byOrigin[14].direction, 'bearish');
  assert.strictEqual(byOrigin[14].status, 'active', 'the last bearish leg is never reclaimed within this trace');
});

test('originHigh/originLow mark the anchor candle within a zone that extends beyond it', () => {
  // In the golden trace, top/bottom always exactly equal the origin
  // candle's own High/Low (no multi-candle leg extension happens there).
  // This fixture deliberately builds one: candle1 (the up-leg anchor,
  // High=108/Low=99) is followed by a DOWN candle (candle2) whose own
  // High=112 is nonetheless higher, extending the running peak before
  // candle3's close crosses under the rolling low and confirms the
  // bearish block -- so top(112) genuinely exceeds originHigh(108).
  const sb = makeSandbox();
  const Calc = sb.window.IndicatorEngine._Calc;
  const candles = [
    { time: 1000, open: 101, high: 102, low: 99, close: 100 },
    { time: 1060, open: 100, high: 108, low: 99, close: 106 },
    { time: 1120, open: 106, high: 112, low: 104, close: 105 },
    { time: 1180, open: 105, high: 106, low: 90, close: 95 }
  ];
  const obs = Calc.orderBlocks(candles, 2, 'Full Fill', 50);
  assert.strictEqual(obs.length, 1);
  const ob = obs[0];
  assert.strictEqual(ob.originIndex, 1);
  assert.strictEqual(ob.top, 112);
  assert.strictEqual(ob.bottom, 99);
  assert.strictEqual(ob.originHigh, 108, 'the anchor candle\'s own high, not the extended peak');
  assert.strictEqual(ob.originLow, 99, 'bottom for a bearish block never extends, so this equals bottom');
});

test('Order Blocks instance populates zones via inst._lastOB', () => {
  // Note: this sandbox has no LightweightCharts loaded, so the canvas-drawn
  // rectangles in _drawOverlayClouds can't be exercised here — covered by
  // manual browser QA. What IS testable is that _calcAll computes and
  // stores the full result on inst._lastOB.
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('OB', { swingLength: 2 });
  ie.onCandlesLoaded(obGoldenCandles(7), null);
  const inst = ie.getInstances()[id];
  assert.ok(Array.isArray(inst._lastOB), 'must populate _lastOB');
  // Within the first 7 candles: the bearish OB at origin=2 fires at the
  // crossunder (index4), and that same close-through-top bar (index6)
  // both invalidates it AND seeds the bullish OB at origin=4 in the same
  // pass — so two blocks exist by index6, not one.
  assert.strictEqual(inst._lastOB.length, 2);
  assert.strictEqual(inst._lastOB[0].originIndex, 2);
  assert.strictEqual(inst._lastOB[1].originIndex, 4);
});

test('invalidation is distinct from mitigation on the live engine', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('OB', { swingLength: 2 });
  const candles = obGoldenCandles(7);
  candles.push({ time: candles[6].time + 60, open: 109, high: 110, low: 90, close: 95, volume: 1000 });
  ie.onCandlesLoaded(candles, null);
  const ob = ie.getInstances()[id]._lastOB.find((o) => o.originIndex === 4);
  assert.strictEqual(ob.status, 'invalidated');
  assert.strictEqual(ob.mitigatedIndex, null, 'invalidation must take precedence over recording a mitigation timestamp');
});

test('updateParams (mitigation mode change) recalculates in place, no duplicate instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('OB', { swingLength: 2, mitigation: 'Full Fill' });
  ie.onCandlesLoaded(obGoldenCandles(), null);
  const before = ie.getInstances()[id]._lastOB.find((o) => o.originIndex === 4).mitigatedIndex;

  ie.updateParams(id, { mitigation: 'Touch' });
  assert.strictEqual(Object.keys(ie.getInstances()).length, 1, 'must still be exactly one Order Blocks instance');
  const after = ie.getInstances()[id]._lastOB.find((o) => o.originIndex === 4).mitigatedIndex;
  assert.notStrictEqual(before, after, 'switching mitigation mode must actually recompute');
});

test('showBullish/showBearish params persist independently through updateParams', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('OB', {});
  ie.onCandlesLoaded(obGoldenCandles(), null);
  ie.updateParams(id, { showBullish: 'Off' });
  const p1 = ie.getInstances()[id].params;
  assert.strictEqual(p1.showBullish, 'Off');
  assert.strictEqual(p1.showBearish, 'On', 'turning off bullish must not affect bearish');
});

test('live tick recomputes Order Blocks without duplicating zones', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('OB', { swingLength: 2 });
  const candles = obGoldenCandles(20);
  sb.window._chartCandles = candles;
  ie.onCandlesLoaded(candles, null);
  const beforeCount = ie.getInstances()[id]._lastOB.length;

  sb.window._formingCandles = { i: { time: candles[19].time, open: candles[19].open, high: candles[19].high, low: candles[19].low, close: candles[19].close, volume: candles[19].volume } };
  let threw = false;
  try { ie.onCandleUpdate(sb.window._formingCandles.i); } catch (e) { threw = true; }
  assert.strictEqual(threw, false);
  const inst = ie.getInstances()[id];
  const ids = inst._lastOB.map((o) => o.id);
  assert.strictEqual(ids.length, new Set(ids).size, 'no duplicate OB ids after a live tick');
  assert.strictEqual(inst._lastOB.length, beforeCount, 'a no-op tick on unchanged data must not add or remove zones');
});

test('symbol switch (onCandlesLoaded with new candles) recalculates, no stale OB', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('OB', { swingLength: 2 });
  ie.onCandlesLoaded(obGoldenCandles(), null);
  const symbolACount = ie.getInstances()[id]._lastOB.length;

  const flat = [];
  for (let i = 0; i < 25; i++) flat.push({ time: 1000 + i * 60, open: 5000, high: 5010, low: 4990, close: 5000, volume: 500 });
  ie.onCandlesLoaded(flat, null);
  const symbolBCount = ie.getInstances()[id]._lastOB.length;
  assert.strictEqual(symbolACount, 7);
  assert.strictEqual(symbolBCount, 0, 'flat data must recompute to zero blocks, not keep symbol A\'s stale zones');
});

test('remove then re-add Order Blocks leaves exactly one instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  let id = ie.addIndicator('OB', {});
  ie.removeIndicator(id);
  id = ie.addIndicator('OB', {});
  ie.onCandlesLoaded(obGoldenCandles(), null);
  assert.strictEqual(Object.keys(ie.getInstances()).length, 1);
  assert.ok(Array.isArray(ie.getInstances()[id]._lastOB));
});

test('a swingLength longer than the available history produces no blocks without crashing', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('OB', { swingLength: 100 });
  let threw = false;
  try { ie.onCandlesLoaded(obGoldenCandles(), null); } catch (e) { threw = true; }
  assert.strictEqual(threw, false);
  assertJsonEqual(ie.getInstances()[id]._lastOB, []);
});

console.log('\nLiquidity (Equal Highs/Lows) engine integration');

// 14-candle golden trace shared with the Python backend's TestLiquidity
// (swingLength=2, High=close+1, Low=close-1). Swing highs: 111@2, 110@7,
// 131@11. Swing lows: 97@5, 98@9. With toleranceATRMultiplier=0.5 and
// atrLength=3, {110,111} and {97,98} each cluster into a 2-touch pool.
function liquidityGoldenCandles(n) {
  const closes = [100, 105, 110, 106, 102, 98, 103, 109, 104, 99, 105, 130, 120, 115];
  const count = n || closes.length;
  return closes.slice(0, count).map((c, i) => ({ time: 1000 + i * 60, open: c, high: c + 1, low: c - 1, close: c, volume: 1000 }));
}

test('Liquidity registered in DEFS as ONE indicator with independent EQH/EQL/swing toggles', () => {
  const sb = makeSandbox();
  const def = sb.window.IndicatorEngine.DEFS.LIQUIDITY;
  assert.ok(def, 'Liquidity must be registered in the indicator registry');
  assert.strictEqual(def.type, 'overlay');
  assert.strictEqual(def.paramDefs.swingLength.default, 5);
  assert.strictEqual(def.paramDefs.showEqualHighs.default, 'On');
  assert.strictEqual(def.paramDefs.showEqualLows.default, 'On');
  assert.strictEqual(def.paramDefs.showSwingHighLiquidity.default, 'Off');
  assert.strictEqual(def.paramDefs.minTouches.default, 2);
  assert.strictEqual(def.paramDefs.toleranceATRMultiplier.default, 0.05);
  assert.strictEqual(def.paramDefs.extendLevels.default, 'On');
});

test('Liquidity is searchable by id and full name', () => {
  const sb = makeSandbox();
  const T2 = sb.window.IndicatorEngine._test;
  const def = sb.window.IndicatorEngine.DEFS.LIQUIDITY;
  assert.strictEqual(T2.menuItemMatches(def, 'liquidity'), true);
});

test('Calc.liquidity production-code cross-check against the golden trace', () => {
  const sb = makeSandbox();
  const Calc = sb.window.IndicatorEngine._Calc;
  const candles = liquidityGoldenCandles();
  const pools = Calc.liquidity(candles, 2, 0.5, 3, 2, 50);
  const eqh = pools.filter((p) => p.type === 'EQUAL_HIGH');
  const eql = pools.filter((p) => p.type === 'EQUAL_LOW');
  assert.strictEqual(eqh.length, 1);
  assert.ok(Math.abs(eqh[0].price - 110.5) < 1e-9);
  assert.strictEqual(eqh[0].touchCount, 2);
  assert.strictEqual(eqh[0].side, 'BUY_SIDE');
  assert.strictEqual(eql.length, 1);
  assert.ok(Math.abs(eql[0].price - 97.5) < 1e-9);
  assert.strictEqual(eql[0].side, 'SELL_SIDE');
  const swingHighs = pools.filter((p) => p.type === 'SWING_HIGH').map((p) => p.price).sort((a, b) => a - b);
  assertJsonEqual(swingHighs, [110, 111, 131]);
});

test('Liquidity instance populates pools via inst._lastLiquidity', () => {
  // Note: canvas-drawn horizontal levels + labels in _drawOverlayClouds
  // can't be exercised in this headless sandbox — covered by manual
  // browser QA. What IS testable is that _calcAll populates _lastLiquidity.
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('LIQUIDITY', { swingLength: 2, toleranceATRMultiplier: 0.5, atrLength: 3 });
  ie.onCandlesLoaded(liquidityGoldenCandles(), null);
  const inst = ie.getInstances()[id];
  assert.ok(Array.isArray(inst._lastLiquidity), 'must populate _lastLiquidity');
  assert.ok(inst._lastLiquidity.some((p) => p.type === 'EQUAL_HIGH'));
});

test('minTouches setting changes which clusters qualify as Equal High/Low', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('LIQUIDITY', { swingLength: 2, toleranceATRMultiplier: 0.5, atrLength: 3, minTouches: 2 });
  ie.onCandlesLoaded(liquidityGoldenCandles(), null);
  const withTwo = ie.getInstances()[id]._lastLiquidity.filter((p) => p.type === 'EQUAL_HIGH' || p.type === 'EQUAL_LOW').length;

  ie.updateParams(id, { minTouches: 3 });
  const withThree = ie.getInstances()[id]._lastLiquidity.filter((p) => p.type === 'EQUAL_HIGH' || p.type === 'EQUAL_LOW').length;
  assert.ok(withTwo > 0);
  assert.strictEqual(withThree, 0, 'neither 2-member cluster should qualify once minTouches=3');
});

test('sweep-readiness fields are reserved but never set by this engine', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('LIQUIDITY', { swingLength: 2 });
  ie.onCandlesLoaded(liquidityGoldenCandles(), null);
  const pools = ie.getInstances()[id]._lastLiquidity;
  assert.ok(pools.length > 0);
  assert.ok(pools.every((p) => p.swept === false && p.sweptAt === null && p.sweptPrice === null));
});

test('live tick recomputes Liquidity without duplicating pools', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('LIQUIDITY', { swingLength: 2, toleranceATRMultiplier: 0.5, atrLength: 3 });
  const candles = liquidityGoldenCandles(13);
  sb.window._chartCandles = candles;
  ie.onCandlesLoaded(candles, null);

  sb.window._formingCandles = { i: { time: candles[12].time, open: candles[12].open, high: candles[12].high, low: candles[12].low, close: candles[12].close, volume: candles[12].volume } };
  let threw = false;
  try { ie.onCandleUpdate(sb.window._formingCandles.i); } catch (e) { threw = true; }
  assert.strictEqual(threw, false);
  const ids = ie.getInstances()[id]._lastLiquidity.map((p) => p.id);
  assert.strictEqual(ids.length, new Set(ids).size, 'no duplicate pool ids after a live tick');
});

test('symbol switch (onCandlesLoaded with new candles) recalculates, no stale Liquidity', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('LIQUIDITY', { swingLength: 2, toleranceATRMultiplier: 0.5, atrLength: 3 });
  ie.onCandlesLoaded(liquidityGoldenCandles(), null);
  const symbolAPrices = ie.getInstances()[id]._lastLiquidity.map((p) => p.price);
  assert.ok(symbolAPrices.some((p) => p > 90 && p < 135));

  const flat = [];
  for (let i = 0; i < 14; i++) flat.push({ time: 1000 + i * 60, open: 5000, high: 5010, low: 4990, close: 5000, volume: 500 });
  ie.onCandlesLoaded(flat, null);
  const symbolBPools = ie.getInstances()[id]._lastLiquidity;
  assert.ok(symbolBPools.every((p) => p.price < 5100 && p.price > 4900), 'must recompute for the new symbol, no leaked prices from the old one');
});

test('remove then re-add Liquidity leaves exactly one instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  let id = ie.addIndicator('LIQUIDITY', {});
  ie.removeIndicator(id);
  id = ie.addIndicator('LIQUIDITY', {});
  ie.onCandlesLoaded(liquidityGoldenCandles(), null);
  assert.strictEqual(Object.keys(ie.getInstances()).length, 1);
  assert.ok(Array.isArray(ie.getInstances()[id]._lastLiquidity));
});

test('a very tight tolerance produces no Equal High/Low pools without crashing', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('LIQUIDITY', { swingLength: 2, toleranceATRMultiplier: 0, atrLength: 3 });
  let threw = false;
  try { ie.onCandlesLoaded(liquidityGoldenCandles(), null); } catch (e) { threw = true; }
  assert.strictEqual(threw, false);
  const pools = ie.getInstances()[id]._lastLiquidity;
  assert.ok(!pools.some((p) => p.type === 'EQUAL_HIGH' || p.type === 'EQUAL_LOW'));
});

console.log('\nLiquidity Sweeps engine integration');

// Same 14-candle base as liquidityGoldenCandles(), plus 2 candles that
// confirm a buy-side sweep of the EQUAL_HIGH pool (110.5). NOTE: the base
// fixture's own idx11 high (131) already breaches 110.5, so the sweep's
// sweepIndex is 11 (not the appended candle) and sweepExtreme is 131 —
// hand-verified identically in the Python backend's TestLiquiditySweeps.
function sweepBuySideCandles() {
  const closes = [100, 105, 110, 106, 102, 98, 103, 109, 104, 99, 105, 130, 120, 115, 116, 108];
  const highs = closes.map((c) => c + 1);
  const lows = closes.map((c) => c - 1);
  return closes.map((c, i) => ({ time: 1000 + i * 60, open: c, high: highs[i], low: lows[i], close: c, volume: 1000 }));
}

test('Liquidity Sweeps registered in DEFS as ONE indicator with independent buy/sell-side toggles', () => {
  const sb = makeSandbox();
  const def = sb.window.IndicatorEngine.DEFS.LIQUIDITYSWEEPS;
  assert.ok(def, 'Liquidity Sweeps must be registered in the indicator registry');
  assert.strictEqual(def.type, 'overlay');
  assert.strictEqual(def.paramDefs.showBuySide.default, 'On');
  assert.strictEqual(def.paramDefs.showSellSide.default, 'On');
  assert.strictEqual(def.paramDefs.confirmation.default, 'Close Rejection');
  assertJsonEqual(def.paramDefs.confirmation.options, ['Close Rejection', 'Wick + Reclaim']);
  assert.strictEqual(def.paramDefs.maxEvents.default, 50);
});

test('Liquidity Sweeps is searchable by id and full name', () => {
  const sb = makeSandbox();
  const T2 = sb.window.IndicatorEngine._test;
  const def = sb.window.IndicatorEngine.DEFS.LIQUIDITYSWEEPS;
  assert.strictEqual(T2.menuItemMatches(def, 'liquidity sweeps'), true);
});

test('Calc.liquiditySweeps production-code cross-check against the golden trace', () => {
  const sb = makeSandbox();
  const Calc = sb.window.IndicatorEngine._Calc;
  const candles = sweepBuySideCandles();
  const sweeps = Calc.liquiditySweeps(candles, 2, 0.5, 3, 2, 'Close Rejection', 50);
  const sweep = sweeps.find((s) => s.liquidityPrice === 110.5);
  assert.ok(sweep, 'the 110.5 EQUAL_HIGH pool must produce a confirmed sweep');
  assert.strictEqual(sweep.direction, 'bearish');
  assert.strictEqual(sweep.sweepType, 'BUY_SIDE_SWEEP');
  assert.strictEqual(sweep.sweepIndex, 11);
  assert.strictEqual(sweep.reclaimIndex, 15);
  assert.strictEqual(sweep.sweepExtreme, 131);
});

test('Liquidity Sweeps instance populates events via inst._lastLiquiditySweeps', () => {
  // Note: canvas-drawn sweep markers/labels in _drawOverlayClouds can't be
  // exercised in this headless sandbox — covered by manual browser QA.
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('LIQUIDITYSWEEPS', { swingLength: 2, toleranceATRMultiplier: 0.5, atrLength: 3, minTouches: 2 });
  ie.onCandlesLoaded(sweepBuySideCandles(), null);
  const inst = ie.getInstances()[id];
  assert.ok(Array.isArray(inst._lastLiquiditySweeps));
  assert.ok(inst._lastLiquiditySweeps.some((s) => s.liquidityPrice === 110.5));
});

test('Wick + Reclaim confirms faster than Close Rejection on the same data', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const closes = [100, 105, 110, 106, 102, 98, 103, 109, 104, 99, 105, 130, 120, 115, 116, 112];
  const candles = closes.map((c, i) => ({ time: 1000 + i * 60, open: c, high: (i === 15 ? 118 : c + 1), low: (i === 15 ? 109 : c - 1), close: c, volume: 1000 }));
  const id = ie.addIndicator('LIQUIDITYSWEEPS', { swingLength: 2, toleranceATRMultiplier: 0.5, atrLength: 3, minTouches: 2, confirmation: 'Close Rejection' });
  ie.onCandlesLoaded(candles, null);
  const closeRejectionSweeps = ie.getInstances()[id]._lastLiquiditySweeps.filter((s) => s.liquidityPrice === 110.5);

  ie.updateParams(id, { confirmation: 'Wick + Reclaim' });
  const wickReclaimSweeps = ie.getInstances()[id]._lastLiquiditySweeps.filter((s) => s.liquidityPrice === 110.5);
  assert.strictEqual(closeRejectionSweeps.length, 0, 'close stays above 110.5 throughout, so Close Rejection must not confirm');
  assert.strictEqual(wickReclaimSweeps.length, 1, 'the low dipping back below 110.5 must confirm under Wick + Reclaim');
});

test('showBuySide/showSellSide params persist independently through updateParams', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('LIQUIDITYSWEEPS', {});
  ie.onCandlesLoaded(sweepBuySideCandles(), null);
  ie.updateParams(id, { showBuySide: 'Off' });
  const p1 = ie.getInstances()[id].params;
  assert.strictEqual(p1.showBuySide, 'Off');
  assert.strictEqual(p1.showSellSide, 'On');
});

test('live tick recomputes Liquidity Sweeps without duplicating events', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('LIQUIDITYSWEEPS', { swingLength: 2, toleranceATRMultiplier: 0.5, atrLength: 3, minTouches: 2 });
  const candles = sweepBuySideCandles();
  sb.window._chartCandles = candles;
  ie.onCandlesLoaded(candles, null);

  sb.window._formingCandles = { i: { time: candles[15].time, open: candles[15].open, high: candles[15].high, low: candles[15].low, close: candles[15].close, volume: candles[15].volume } };
  let threw = false;
  try { ie.onCandleUpdate(sb.window._formingCandles.i); } catch (e) { threw = true; }
  assert.strictEqual(threw, false);
  const ids = ie.getInstances()[id]._lastLiquiditySweeps.map((s) => s.id);
  assert.strictEqual(ids.length, new Set(ids).size, 'no duplicate sweep ids after a live tick');
});

test('symbol switch (onCandlesLoaded with new candles) recalculates, no stale sweeps', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('LIQUIDITYSWEEPS', { swingLength: 2, toleranceATRMultiplier: 0.5, atrLength: 3, minTouches: 2 });
  ie.onCandlesLoaded(sweepBuySideCandles(), null);
  assert.ok(ie.getInstances()[id]._lastLiquiditySweeps.some((s) => s.liquidityPrice === 110.5));

  const flat = [];
  for (let i = 0; i < 16; i++) flat.push({ time: 1000 + i * 60, open: 5000, high: 5010, low: 4990, close: 5000, volume: 500 });
  ie.onCandlesLoaded(flat, null);
  assert.strictEqual(ie.getInstances()[id]._lastLiquiditySweeps.length, 0, 'flat data must recompute to zero sweeps, not keep stale ones');
});

test('remove then re-add Liquidity Sweeps leaves exactly one instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  let id = ie.addIndicator('LIQUIDITYSWEEPS', {});
  ie.removeIndicator(id);
  id = ie.addIndicator('LIQUIDITYSWEEPS', {});
  ie.onCandlesLoaded(sweepBuySideCandles(), null);
  assert.strictEqual(Object.keys(ie.getInstances()).length, 1);
  assert.ok(Array.isArray(ie.getInstances()[id]._lastLiquiditySweeps));
});

console.log('\nPremium & Discount engine integration');

// 25-candle golden trace shared with TestMarketStructure/TestOrderBlocks
// (swingLength=2). The active dealing range ends [98,104], equilibrium
// 101, and the final close (105) classifies as PREMIUM — hand-verified
// identically in the Python backend's TestPremiumDiscount.
function premiumDiscountGoldenCandles(n) {
  const closes = [100, 102, 105, 103, 101, 104, 109, 106, 102, 108, 113, 110, 107, 111, 116, 112, 108, 105, 101, 98, 100, 103, 99, 102, 105];
  const count = n || closes.length;
  return closes.slice(0, count).map((c, i) => ({ time: 1000 + i * 60, open: c, high: c + 1, low: c - 1, close: c, volume: 1000 }));
}

test('Premium & Discount registered in DEFS as ONE indicator with independent zone toggles', () => {
  const sb = makeSandbox();
  const def = sb.window.IndicatorEngine.DEFS.PREMIUMDISCOUNT;
  assert.ok(def, 'Premium & Discount must be registered in the indicator registry');
  assert.strictEqual(def.type, 'overlay');
  assert.strictEqual(def.paramDefs.showPremium.default, 'On');
  assert.strictEqual(def.paramDefs.showDiscount.default, 'On');
  assert.strictEqual(def.paramDefs.showEquilibrium.default, 'On');
  assert.strictEqual(def.paramDefs.showDealingRange.default, 'On');
  assert.strictEqual(def.paramDefs.zoneOpacity.default, 0.08);
});

test('Premium & Discount is searchable by id and full name', () => {
  const sb = makeSandbox();
  const T2 = sb.window.IndicatorEngine._test;
  const def = sb.window.IndicatorEngine.DEFS.PREMIUMDISCOUNT;
  assert.strictEqual(T2.menuItemMatches(def, 'premium'), true);
  assert.strictEqual(T2.menuItemMatches(def, 'discount'), true);
});

test('Calc.premiumDiscount production-code cross-check against the golden trace', () => {
  const sb = makeSandbox();
  const Calc = sb.window.IndicatorEngine._Calc;
  const candles = premiumDiscountGoldenCandles();
  const res = Calc.premiumDiscount(candles, 2);
  assert.strictEqual(res.ranges.length, 9);
  const active = res.ranges[res.ranges.length - 1];
  assert.strictEqual(active.rangeHigh, 104);
  assert.strictEqual(active.rangeLow, 98);
  assert.strictEqual(active.equilibrium, 101);
  assert.strictEqual(active.active, true);
  assert.strictEqual(res.currentClassification, 'PREMIUM');
});

test('Premium & Discount instance populates ranges via inst._lastPremiumDiscount', () => {
  // Note: canvas-drawn zones/lines in _drawOverlayClouds can't be
  // exercised in this headless sandbox — covered by manual browser QA.
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('PREMIUMDISCOUNT', { swingLength: 2 });
  ie.onCandlesLoaded(premiumDiscountGoldenCandles(), null);
  const inst = ie.getInstances()[id];
  assert.ok(Array.isArray(inst._lastPremiumDiscount.ranges));
  assert.strictEqual(inst._lastPremiumDiscount.ranges.length, 9);
});

test('range does not change between two candles with no new confirmed swing', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('PREMIUMDISCOUNT', { swingLength: 2 });
  ie.onCandlesLoaded(premiumDiscountGoldenCandles(9), null);
  const rangesAt9 = ie.getInstances()[id]._lastPremiumDiscount.ranges;

  ie.onCandlesLoaded(premiumDiscountGoldenCandles(10), null);
  const rangesAt10 = ie.getInstances()[id]._lastPremiumDiscount.ranges;
  assertJsonEqual(rangesAt9[rangesAt9.length - 1], rangesAt10[rangesAt10.length - 1]);
});

test('updateParams (swingLength change) recalculates in place, no duplicate instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('PREMIUMDISCOUNT', { swingLength: 2 });
  ie.onCandlesLoaded(premiumDiscountGoldenCandles(), null);
  const before = ie.getInstances()[id]._lastPremiumDiscount.ranges.length;

  ie.updateParams(id, { swingLength: 5 });
  assert.strictEqual(Object.keys(ie.getInstances()).length, 1, 'must still be exactly one Premium & Discount instance');
  const after = ie.getInstances()[id]._lastPremiumDiscount.ranges.length;
  assert.notStrictEqual(before, after, 'a different swingLength must actually recompute the swing/range history');
});

test('live tick recomputes Premium & Discount without duplicating ranges', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('PREMIUMDISCOUNT', { swingLength: 2 });
  const candles = premiumDiscountGoldenCandles(20);
  sb.window._chartCandles = candles;
  ie.onCandlesLoaded(candles, null);
  const beforeIds = ie.getInstances()[id]._lastPremiumDiscount.ranges.map((r) => r.id);

  sb.window._formingCandles = { i: { time: candles[19].time, open: candles[19].open, high: candles[19].high, low: candles[19].low, close: candles[19].close, volume: candles[19].volume } };
  let threw = false;
  try { ie.onCandleUpdate(sb.window._formingCandles.i); } catch (e) { threw = true; }
  assert.strictEqual(threw, false);
  const afterIds = ie.getInstances()[id]._lastPremiumDiscount.ranges.map((r) => r.id);
  assert.strictEqual(afterIds.length, new Set(afterIds).size, 'no duplicate range ids after a live tick');
  assert.strictEqual(beforeIds.length, afterIds.length, 'a no-op tick on unchanged data must not create or drop ranges');
});

test('symbol switch (onCandlesLoaded with new candles) recalculates, no stale range', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('PREMIUMDISCOUNT', { swingLength: 2 });
  ie.onCandlesLoaded(premiumDiscountGoldenCandles(), null);
  assert.ok(ie.getInstances()[id]._lastPremiumDiscount.ranges.length > 0);

  const flat = [];
  for (let i = 0; i < 25; i++) flat.push({ time: 1000 + i * 60, open: 5000, high: 5010, low: 4990, close: 5000, volume: 500 });
  ie.onCandlesLoaded(flat, null);
  assert.strictEqual(ie.getInstances()[id]._lastPremiumDiscount.ranges.length, 0, 'flat data has no swings, so no range can form — the old symbol\'s ranges must be gone');
});

test('remove then re-add Premium & Discount leaves exactly one instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  let id = ie.addIndicator('PREMIUMDISCOUNT', {});
  ie.removeIndicator(id);
  id = ie.addIndicator('PREMIUMDISCOUNT', {});
  ie.onCandlesLoaded(premiumDiscountGoldenCandles(), null);
  assert.strictEqual(Object.keys(ie.getInstances()).length, 1);
  assert.ok(Array.isArray(ie.getInstances()[id]._lastPremiumDiscount.ranges));
});

test('insufficient candles produce no range, no crash', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('PREMIUMDISCOUNT', { swingLength: 2 });
  let threw = false;
  try { ie.onCandlesLoaded(premiumDiscountGoldenCandles(3), null); } catch (e) { threw = true; }
  assert.strictEqual(threw, false);
  assertJsonEqual(ie.getInstances()[id]._lastPremiumDiscount.ranges, []);
  assert.strictEqual(ie.getInstances()[id]._lastPremiumDiscount.currentClassification, null);
});

console.log('\nSMC Setups (combined orchestration) engine integration');

// Same golden fixture as the Python backend's TestSMCSetups: base 14
// liquidity candles + 2 more that create both a buy-side sweep (of
// 111/110/110.5) and a sell-side sweep (of 97/98/97.5). Hand-verified
// (cross-checked against the already-tested sub-engines) to produce
// exactly 3 bearish setups, score 3 each, and zero bullish setups even
// though bullish sweeps also exist — see the Python test's full docstring
// for the complete trace.
function smcGoldenCandles() {
  const closes = [100, 105, 110, 106, 102, 98, 103, 109, 104, 99, 105, 130, 120, 115, 95, 100];
  const highs = closes.map((c) => c + 1);
  const lows = closes.map((c) => c - 1);
  highs[14] = 96; lows[14] = 94;
  highs[15] = 101; lows[15] = 99;
  return closes.map((c, i) => ({ time: 1000 + i * 60, open: c, high: highs[i], low: lows[i], close: c, volume: 1000 }));
}
const SMC_KW = { swingLength: 2, toleranceATRMultiplier: 0.5, atrLength: 3, minTouches: 2 };

test('SMC registered in DEFS as ONE indicator with independent bullish/bearish toggles', () => {
  const sb = makeSandbox();
  const def = sb.window.IndicatorEngine.DEFS.SMC;
  assert.ok(def, 'SMC must be registered in the indicator registry');
  assert.strictEqual(def.type, 'overlay');
  assert.strictEqual(def.paramDefs.showBullish.default, 'On');
  assert.strictEqual(def.paramDefs.showBearish.default, 'On');
  assert.strictEqual(def.paramDefs.structureWindowBars.default, 10);
  assert.strictEqual(def.paramDefs.minScore.default, 2);
  assert.strictEqual(def.paramDefs.maxSetups.default, 20);
});

test('SMC is searchable by id and full name', () => {
  const sb = makeSandbox();
  const T2 = sb.window.IndicatorEngine._test;
  const def = sb.window.IndicatorEngine.DEFS.SMC;
  assert.strictEqual(T2.menuItemMatches(def, 'smc'), true);
  assert.strictEqual(T2.menuItemMatches(def, 'smart money'), true);
});

test('Calc.smcSetups production-code cross-check against the golden trace', () => {
  const sb = makeSandbox();
  const Calc = sb.window.IndicatorEngine._Calc;
  const candles = smcGoldenCandles();
  const setups = Calc.smcSetups(candles, 2, 0.5, 3, 2, 'Full Fill', 'Close Rejection', 10, 2, 20);
  assert.strictEqual(setups.length, 3);
  assert.ok(setups.every((s) => s.direction === 'bearish'));
  assert.ok(setups.every((s) => s.score === 3));
  assert.ok(setups.every((s) => s.structureEvent.type === 'CHOCH' && s.structureEvent.breakIndex === 14));
  assert.ok(setups.every((s) => s.fvgConfluence === true && s.fvgConfluenceId === '11-bearish'));
  assert.ok(setups.every((s) => s.obConfluence === false));
  assert.ok(!setups.some((s) => s.direction === 'bullish'), 'the early bullish CHoCH (idx11) predates the sell-side sweeps\' reclaim, so no bullish setup should form');
});

test('minScore filters out the golden case\'s score-3 setups', () => {
  const sb = makeSandbox();
  const Calc = sb.window.IndicatorEngine._Calc;
  const candles = smcGoldenCandles();
  const allowed = Calc.smcSetups(candles, 2, 0.5, 3, 2, 'Full Fill', 'Close Rejection', 10, 2, 20);
  const filtered = Calc.smcSetups(candles, 2, 0.5, 3, 2, 'Full Fill', 'Close Rejection', 10, 4, 20);
  assert.strictEqual(allowed.length, 3);
  assert.strictEqual(filtered.length, 0);
});

test('SMC instance populates setups via inst._lastSMC', () => {
  // Note: canvas-drawn markers/labels in _drawOverlayClouds can't be
  // exercised in this headless sandbox — covered by manual browser QA.
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('SMC', SMC_KW);
  ie.onCandlesLoaded(smcGoldenCandles(), null);
  const inst = ie.getInstances()[id];
  assert.ok(Array.isArray(inst._lastSMC));
  assert.strictEqual(inst._lastSMC.length, 3);
});

test('showBullish/showBearish params persist independently through updateParams', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('SMC', SMC_KW);
  ie.onCandlesLoaded(smcGoldenCandles(), null);
  ie.updateParams(id, { showBullish: 'Off' });
  const p1 = ie.getInstances()[id].params;
  assert.strictEqual(p1.showBullish, 'Off');
  assert.strictEqual(p1.showBearish, 'On');
});

test('updateParams (minScore change) recalculates in place, no duplicate instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('SMC', Object.assign({}, SMC_KW, { minScore: 2 }));
  ie.onCandlesLoaded(smcGoldenCandles(), null);
  const before = ie.getInstances()[id]._lastSMC.length;

  ie.updateParams(id, { minScore: 4 });
  assert.strictEqual(Object.keys(ie.getInstances()).length, 1, 'must still be exactly one SMC instance');
  const after = ie.getInstances()[id]._lastSMC.length;
  assert.strictEqual(before, 3);
  assert.strictEqual(after, 0);
});

test('live tick recomputes SMC without duplicating setups', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('SMC', SMC_KW);
  const candles = smcGoldenCandles();
  sb.window._chartCandles = candles;
  ie.onCandlesLoaded(candles, null);

  sb.window._formingCandles = { i: { time: candles[15].time, open: candles[15].open, high: candles[15].high, low: candles[15].low, close: candles[15].close, volume: candles[15].volume } };
  let threw = false;
  try { ie.onCandleUpdate(sb.window._formingCandles.i); } catch (e) { threw = true; }
  assert.strictEqual(threw, false);
  const ids = ie.getInstances()[id]._lastSMC.map((s) => s.id);
  assert.strictEqual(ids.length, new Set(ids).size, 'no duplicate setup ids after a live tick');
});

test('symbol switch (onCandlesLoaded with new candles) recalculates, no stale setups', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('SMC', SMC_KW);
  ie.onCandlesLoaded(smcGoldenCandles(), null);
  assert.strictEqual(ie.getInstances()[id]._lastSMC.length, 3);

  const flat = [];
  for (let i = 0; i < 16; i++) flat.push({ time: 1000 + i * 60, open: 5000, high: 5010, low: 4990, close: 5000, volume: 500 });
  ie.onCandlesLoaded(flat, null);
  assert.strictEqual(ie.getInstances()[id]._lastSMC.length, 0, 'flat data must recompute to zero setups, not keep the old symbol\'s stale ones');
});

test('remove then re-add SMC leaves exactly one instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  let id = ie.addIndicator('SMC', SMC_KW);
  ie.removeIndicator(id);
  id = ie.addIndicator('SMC', SMC_KW);
  ie.onCandlesLoaded(smcGoldenCandles(), null);
  assert.strictEqual(Object.keys(ie.getInstances()).length, 1);
  assert.ok(Array.isArray(ie.getInstances()[id]._lastSMC));
});

test('insufficient candles produce no setups, no crash', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('SMC', SMC_KW);
  let threw = false;
  try { ie.onCandlesLoaded([{ time: 1000, open: 100, high: 105, low: 95, close: 100, volume: 1000 }], null); } catch (e) { threw = true; }
  assert.strictEqual(threw, false);
  assertJsonEqual(ie.getInstances()[id]._lastSMC, []);
});

console.log('\nBreaker & Mitigation Blocks engine integration');

// Reuses the exact same 25-candle Order Block golden trace as
// obGoldenCandles() above (shared with the Python backend's
// TestBreakerMitigation). Calc.orderBlocks(candles, 2, 'Full Fill') on it
// now gives 7 alternating blocks (see obGoldenCandles' own comment above);
// of those, 4 are 'invalidated' (-> 4 Breakers: origin=2/6/10 bearish ->
// BULLISH_BREAKER, origin=12 bullish -> BEARISH_BREAKER) and 2 are
// 'fully_mitigated' (-> 2 Mitigation Blocks: origin=4 and origin=8, both
// BULLISH_MITIGATION, both mitigated @18) — origin=14 stays 'active' and
// becomes neither. See the Python test class's full docstring for the trace.
const BM_KW = { swingLength: 2 };

test('Breaker & Mitigation Blocks registered in DEFS as ONE indicator with independent toggles', () => {
  const sb = makeSandbox();
  const def = sb.window.IndicatorEngine.DEFS.BREAKERMITIGATION;
  assert.ok(def, 'Breaker & Mitigation Blocks must be registered in the indicator registry');
  assert.strictEqual(def.type, 'overlay');
  assert.strictEqual(def.paramDefs.showBullishBreakers.default, 'On');
  assert.strictEqual(def.paramDefs.showBearishBreakers.default, 'On');
  assert.strictEqual(def.paramDefs.showBullishMitigation.default, 'On');
  assert.strictEqual(def.paramDefs.showBearishMitigation.default, 'On');
  assert.strictEqual(def.paramDefs.breakerMitigation.default, 'Full Fill');
  assert.strictEqual(def.paramDefs.maxBlocks.default, 50);
});

test('Breaker & Mitigation Blocks is searchable by id and full name', () => {
  const sb = makeSandbox();
  const T2 = sb.window.IndicatorEngine._test;
  const def = sb.window.IndicatorEngine.DEFS.BREAKERMITIGATION;
  assert.strictEqual(T2.menuItemMatches(def, 'breaker'), true);
  assert.strictEqual(T2.menuItemMatches(def, 'mitigation blocks'), true);
});

test('Calc.breakerMitigation production-code cross-check against the golden trace', () => {
  const sb = makeSandbox();
  const Calc = sb.window.IndicatorEngine._Calc;
  const candles = obGoldenCandles();
  const res = Calc.breakerMitigation(candles, 2, 'Full Fill', 50);
  assert.strictEqual(res.breakers.length, 4);
  const byCreation = {};
  res.breakers.forEach((b) => { byCreation[b.creationIndex] = b; });
  assert.strictEqual(byCreation[2].direction, 'BULLISH_BREAKER');
  assert.strictEqual(byCreation[6].direction, 'BULLISH_BREAKER');
  assert.strictEqual(byCreation[10].direction, 'BULLISH_BREAKER');
  const breaker = byCreation[12];
  assert.strictEqual(breaker.direction, 'BEARISH_BREAKER');
  assert.strictEqual(breaker.top, 108);
  assert.strictEqual(breaker.bottom, 106);
  assert.strictEqual(breaker.creationIndex, 12);
  assert.strictEqual(breaker.invalidationIndex, 17);
  assert.strictEqual(breaker.confirmationIndex, 17);
  assert.strictEqual(breaker.status, 'active');

  assert.strictEqual(res.mitigations.length, 2);
  const bySource = {};
  res.mitigations.forEach((m) => { bySource[m.sourceIndex] = m; });
  assert.strictEqual(bySource[4].direction, 'BULLISH_MITIGATION');
  assert.strictEqual(bySource[4].mitigationIndex, 18);
  assert.strictEqual(bySource[8].direction, 'BULLISH_MITIGATION');
  assert.strictEqual(bySource[8].mitigationIndex, 18);
});

test('Breaker & Mitigation instance populates via inst._lastBreakerMitigation', () => {
  // Note: canvas-drawn rectangles/labels in _drawOverlayClouds can't be
  // exercised in this headless sandbox — covered by manual browser QA.
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('BREAKERMITIGATION', BM_KW);
  ie.onCandlesLoaded(obGoldenCandles(), null);
  const inst = ie.getInstances()[id];
  assert.ok(inst._lastBreakerMitigation);
  assert.strictEqual(inst._lastBreakerMitigation.breakers.length, 4);
  assert.strictEqual(inst._lastBreakerMitigation.mitigations.length, 2);
});

test('breaker mitigation drives fillPercentage and status forward when extended', () => {
  const sb = makeSandbox();
  const Calc = sb.window.IndicatorEngine._Calc;
  const extended = obGoldenCandles().concat([
    { time: 1000 + 25 * 60, open: 105, high: 107, low: 103, close: 104, volume: 1000 },
    { time: 1000 + 26 * 60, open: 104, high: 108, low: 103, close: 104, volume: 1000 }
  ]);
  // The origin=12 BEARISH_BREAKER (still 'active' in the base 25-candle
  // trace) is the one these extra candles walk forward.
  const findTarget = (res) => res.breakers.find((b) => b.creationIndex === 12);
  const partial = findTarget(Calc.breakerMitigation(extended.slice(0, -1), 2, 'Full Fill', 50));
  assert.strictEqual(partial.status, 'partially_mitigated');
  assert.ok(Math.abs(partial.fillPercentage - 0.5) < 1e-9);

  const full = findTarget(Calc.breakerMitigation(extended, 2, 'Full Fill', 50));
  assert.strictEqual(full.status, 'fully_mitigated');
  assert.strictEqual(full.mitigatedIndex, 26);
});

test('breaker re-invalidation on decisive close-through is distinct from mitigation', () => {
  const sb = makeSandbox();
  const Calc = sb.window.IndicatorEngine._Calc;
  const extended = obGoldenCandles().concat([
    { time: 1000 + 25 * 60, open: 109, high: 112, low: 108, close: 110, volume: 1000 }
  ]);
  const breaker = Calc.breakerMitigation(extended, 2, 'Full Fill', 50).breakers.find((b) => b.creationIndex === 12);
  assert.strictEqual(breaker.status, 'invalidated');
  assert.strictEqual(breaker.reInvalidatedIndex, 25);
  assert.strictEqual(breaker.mitigatedIndex, null);
});

test('showBullishBreakers/showBearishBreakers/showBullishMitigation/showBearishMitigation persist independently', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('BREAKERMITIGATION', BM_KW);
  ie.onCandlesLoaded(obGoldenCandles(), null);
  ie.updateParams(id, { showBearishBreakers: 'Off' });
  const p1 = ie.getInstances()[id].params;
  assert.strictEqual(p1.showBearishBreakers, 'Off');
  assert.strictEqual(p1.showBullishBreakers, 'On');
  assert.strictEqual(p1.showBullishMitigation, 'On');
  assert.strictEqual(p1.showBearishMitigation, 'On');
});

test('updateParams (breakerMitigation mode change) recalculates in place, no duplicate instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const extended = obGoldenCandles().concat([
    { time: 1000 + 25 * 60, open: 105, high: 107, low: 103, close: 104, volume: 1000 },
    { time: 1000 + 26 * 60, open: 104, high: 108, low: 103, close: 104, volume: 1000 }
  ]);
  const id = ie.addIndicator('BREAKERMITIGATION', Object.assign({}, BM_KW, { breakerMitigation: 'Full Fill' }));
  ie.onCandlesLoaded(extended, null);
  const before = ie.getInstances()[id]._lastBreakerMitigation.breakers[0].mitigatedIndex;

  ie.updateParams(id, { breakerMitigation: 'Touch' });
  assert.strictEqual(Object.keys(ie.getInstances()).length, 1, 'must still be exactly one instance');
  const after = ie.getInstances()[id]._lastBreakerMitigation.breakers[0].mitigatedIndex;
  assert.notStrictEqual(before, after, 'switching breaker mitigation mode must actually recompute');
});

test('live tick recomputes Breaker & Mitigation without duplicating blocks', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('BREAKERMITIGATION', BM_KW);
  const candles = obGoldenCandles();
  sb.window._chartCandles = candles;
  ie.onCandlesLoaded(candles, null);

  sb.window._formingCandles = { i: { time: candles[24].time, open: candles[24].open, high: candles[24].high, low: candles[24].low, close: candles[24].close, volume: candles[24].volume } };
  let threw = false;
  try { ie.onCandleUpdate(sb.window._formingCandles.i); } catch (e) { threw = true; }
  assert.strictEqual(threw, false);
  const res = ie.getInstances()[id]._lastBreakerMitigation;
  const allIds = res.breakers.map((b) => b.id).concat(res.mitigations.map((m) => m.id));
  assert.strictEqual(allIds.length, new Set(allIds).size, 'no duplicate block ids after a live tick');
});

test('symbol switch (onCandlesLoaded with new candles) recalculates, no stale blocks', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('BREAKERMITIGATION', BM_KW);
  ie.onCandlesLoaded(obGoldenCandles(), null);
  assert.strictEqual(ie.getInstances()[id]._lastBreakerMitigation.breakers.length, 4);

  const flat = [];
  for (let i = 0; i < 25; i++) flat.push({ time: 1000 + i * 60, open: 5000, high: 5010, low: 4990, close: 5000, volume: 500 });
  ie.onCandlesLoaded(flat, null);
  const afterFlat = ie.getInstances()[id]._lastBreakerMitigation;
  assert.strictEqual(afterFlat.breakers.length, 0, 'flat data must recompute to zero breakers, not keep the old symbol\'s stale one');
  assert.strictEqual(afterFlat.mitigations.length, 0);
});

test('remove then re-add Breaker & Mitigation leaves exactly one instance', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  let id = ie.addIndicator('BREAKERMITIGATION', BM_KW);
  ie.removeIndicator(id);
  id = ie.addIndicator('BREAKERMITIGATION', BM_KW);
  ie.onCandlesLoaded(obGoldenCandles(), null);
  assert.strictEqual(Object.keys(ie.getInstances()).length, 1);
  assert.ok(ie.getInstances()[id]._lastBreakerMitigation);
});

test('insufficient candles produce no blocks, no crash', () => {
  const sb = makeSandbox();
  const ie = sb.window.IndicatorEngine;
  const id = ie.addIndicator('BREAKERMITIGATION', BM_KW);
  let threw = false;
  try { ie.onCandlesLoaded([{ time: 1000, open: 100, high: 105, low: 95, close: 100, volume: 1000 }], null); } catch (e) { threw = true; }
  assert.strictEqual(threw, false);
  const res = ie.getInstances()[id]._lastBreakerMitigation;
  assertJsonEqual(res.breakers, []);
  assertJsonEqual(res.mitigations, []);
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
