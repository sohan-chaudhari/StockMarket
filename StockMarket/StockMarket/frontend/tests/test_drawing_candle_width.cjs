/**
 * Issue-3 regression: drawing a tool must NOT change the candle/bar width.
 *
 * Root cause was `d instanceof (window.EmojiDrawing || Object)` in
 * drawing-core.js — `window.EmojiDrawing` was never assigned so it fell back
 * to `Object`, and every drawing is `instanceof Object`, so EVERY drawing was
 * classified "below-candle". That made drawVisibleCandlesOverlay() repaint the
 * candles on the drawing overlay (with its own barSpacing*0.75 body width) on
 * every redraw, visibly changing candle width.
 *
 * This loads the REAL drawing-core.js (same technique as test_all_tools.cjs)
 * and exercises the real DrawingEngine.isBelowCandleLayer() classifier.
 */
const fs = require('fs');
const path = require('path');
const H = require('./harness.cjs');

let pass = 0, fail = 0;
function ok(cond, name) {
  if (cond) { pass++; console.log('  PASS ' + name); }
  else { fail++; console.log('  FAIL ' + name); }
}

const coreSrc = fs.readFileSync(path.join(__dirname, '..', 'drawing-core.js'), 'utf8');
try { (0, eval)(coreSrc); } catch (e) { console.error('CORE LOAD FAILED:', e.message); process.exit(2); }

const DE = window.DrawingEngine;
const L = window.Layer;

console.log('\n[1] classifier exists');
ok(DE && typeof DE.isBelowCandleLayer === 'function', 'DrawingEngine.isBelowCandleLayer is defined');

console.log('\n[2] plain drawings are NOT below-candle (the regression)');
ok(DE.isBelowCandleLayer({}) === false,
   'bare object is NOT below-candle (this returned true before the fix)');
ok(DE.isBelowCandleLayer({ getObjectDef: () => null, model: { layer: L.DRAWINGS_ABOVE } }) === false,
   'plain drawing (getObjectDef -> null) is NOT below-candle');
ok(DE.isBelowCandleLayer({ getObjectDef: () => null, style: { color: '#fff' } }) === false,
   'styled plain drawing is NOT below-candle');
ok(DE.isBelowCandleLayer(null) === false, 'null is NOT below-candle');

console.log('\n[3] genuine below-candle objects ARE still below');
ok(DE.isBelowCandleLayer({ getObjectDef: () => ({ geometry: 'emoji', defaults: { behindCandles: true, emoji: 'X' } }) }) === true,
   'emoji object IS below-candle');
ok(DE.isBelowCandleLayer({ model: { layer: L.DRAWINGS_BEHIND } }) === true,
   'DRAWINGS_BEHIND layer IS below-candle');
ok(DE.isBelowCandleLayer({ model: { layer: L.BACKGROUND } }) === true,
   'BACKGROUND layer IS below-candle');
ok(DE.isBelowCandleLayer({ style: { behindCandles: true } }) === true,
   'style.behindCandles IS below-candle');

console.log('\n[4] source guard: the broken fallback must never come back');
ok(coreSrc.indexOf('window.EmojiDrawing || Object') === -1,
   '`window.EmojiDrawing || Object` removed from drawing-core.js');

console.log('\n==================================================');
console.log('  PASS: ' + pass + '   FAIL: ' + fail);
console.log('==================================================');
process.exit(fail ? 1 : 0);
