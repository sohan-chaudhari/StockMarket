// PER-TOOL LOOP: instantiate every registered tool, drive it through its full
// creation sequence, render it, and check anchors/geometry. Real execution --
// not code reading.
const H = require('./harness.cjs');
const _log = console.log; console.log = function(){};
const fs = require('fs');
const path = require('path');
// Load the real drawing engine first -- it defines CoordinateMapper,
// getSnappedAnchor, SnappingEngine etc. that drawings.js depends on.
const coreSrc = fs.readFileSync(path.join(__dirname, '..', 'drawing-core.js'), 'utf8');
try { (0, eval)(coreSrc); } catch (e) { console.error('CORE LOAD FAILED:', e.message); process.exit(2); }
if (!window.coordinateMapper && typeof CoordinateMapper === 'function') {
  window.coordinateMapper = new CoordinateMapper();
}
const src = fs.readFileSync(path.join(__dirname, '..', 'drawings.js'), 'utf8');
// Test-only: expose the IIFE-local registry. The production file is NOT
// modified; this rewrites the source string in memory before evaluation.
const MARK = 'window.toolManager = null;';
const idx = src.indexOf(MARK);
if (idx < 0) { console.error('could not locate injection point'); process.exit(2); }
const INJECT = 'window.__ToolDefinitions = ToolDefinitions; ';
const patched = src.slice(0, idx) + INJECT + src.slice(idx);
try { (0, eval)(patched); } catch (e) { console.error('LOAD FAILED:', e.message); process.exit(2); }

const TOOLS = window.__ToolDefinitions || null;
if (!TOOLS) { console.error('Tool registry not exposed on window'); process.exit(2); }

const bars = H.bars;
const chartState = window.coordinateMapper || null;
const results = [];
const keys = Object.keys(TOOLS).filter(k => TOOLS[k] && TOOLS[k].class);

function mkPos(i) {
  const b = bars[40 + i * 12];
  return { x: 100 + i * 60, y: 300 - i * 20, logical: 40 + i * 12, time: b.time, price: b.close };
}

for (const key of keys) {
  const def = TOOLS[key];
  const Cls = def.class;
  const need = def.points === 0 ? 3 : (def.points === 99 ? 4 : def.points);
  const r = { key, cls: Cls.name, need, ok: true, notes: [] };
  try {
    const inst = new Cls(mkPos(0), chartState, { points: def.points });  // matches live path (drawings.js:359)
    // drive creation
    for (let i = 1; i < need; i++) {
      if (typeof inst.addPoint === 'function') inst.addPoint(mkPos(i), chartState);
    }
    if (typeof inst.isComplete === 'function') {
      r.complete = !!inst.isComplete();
      if (!r.complete) { r.ok = false; r.notes.push('isComplete()=false after ' + need + ' pts'); }
    }
    if (typeof inst.isValid === 'function') {
      r.valid = !!inst.isValid();
      if (!r.valid) { r.ok = false; r.notes.push('isValid()=false'); }
    }
    // anchors
    // getAnchorPoints(pixels) takes the PIXEL array from getPixels(chartState)
    if (typeof inst.getAnchorPoints === 'function' && typeof inst.getPixels === 'function') {
      const px = inst.getPixels(chartState);
      const a = inst.getAnchorPoints(px);
      r.anchors = Array.isArray(a) ? a.length : 0;
      r.pixels = Array.isArray(px) ? px.length : 0;
    }
    // canonical time present on coords?
    if (inst.coords && inst.coords.length) {
      r.coordsHaveTime = inst.coords.every(c => c && c.time !== undefined && c.time !== null);
    }
    // render
    if (typeof inst.draw === 'function') {
      inst.draw(H.makeCtx(), chartState);
      r.drew = true;
    }
  } catch (e) {
    r.ok = false;
    r.error = (e && e.message ? e.message : String(e)).split('\n')[0].slice(0, 110);
  }
  results.push(r);
}

const broken = results.filter(r => r.error);
const warn = results.filter(r => !r.error && !r.ok);
const good = results.filter(r => r.ok);
console.log = _log;
console.log('TOOLS EXERCISED : ' + results.length);
console.log('  clean         : ' + good.length);
console.log('  warnings      : ' + warn.length);
console.log('  THREW         : ' + broken.length);
if (broken.length) {
  console.log('\n--- THREW (grouped) ---');
  const byMsg = {};
  broken.forEach(r => { (byMsg[r.error] = byMsg[r.error] || []).push(r.key); });
  Object.entries(byMsg).sort((a,b)=>b[1].length-a[1].length).forEach(([m, ks]) => {
    console.log('  [' + ks.length + '] ' + m);
    console.log('        ' + ks.slice(0, 10).join(', ') + (ks.length > 10 ? ' ...' : ''));
  });
}
if (warn.length) {
  console.log('\n--- WARNINGS (grouped) ---');
  const byN = {};
  warn.forEach(r => { const k = r.notes.join('; '); (byN[k] = byN[k] || []).push(r.key); });
  Object.entries(byN).sort((a,b)=>b[1].length-a[1].length).forEach(([m, ks]) => {
    console.log('  [' + ks.length + '] ' + m);
    console.log('        ' + ks.slice(0, 12).join(', ') + (ks.length > 12 ? ' ...' : ''));
  });
}
const noTime = results.filter(r => r.coordsHaveTime === false);
console.log('\ncoords WITHOUT canonical time: ' + noTime.length + (noTime.length ? ' -> ' + noTime.slice(0,10).map(r=>r.key).join(', ') : ''));
fs.writeFileSync(path.join(__dirname, 'tool_results.json'), JSON.stringify(results, null, 1));
