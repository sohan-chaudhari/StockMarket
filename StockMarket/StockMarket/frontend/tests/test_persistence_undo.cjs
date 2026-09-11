/**
 * Regression tests for defects found by REAL-BROWSER QA that the earlier
 * static checks and the node tool-sweep both missed:
 *
 *  - DrawingModel.toJSON() dropped `time`. An identical points line exists in
 *    clone(), so a single-occurrence patch hit clone() and left the real
 *    serializer untouched: saved drawings were still bar-index only.
 *  - createDrawing()'s renderer built coords without logical/time, so the very
 *    first syncModel() wiped the canonical anchor back to null.
 */
const H = require('./harness.cjs');
const fs = require('fs'), path = require('path');
(0, eval)(fs.readFileSync(path.join(__dirname, '..', 'drawing-core.js'), 'utf8'));
if (!window.coordinateMapper && typeof CoordinateMapper === 'function') window.coordinateMapper = new CoordinateMapper();

let pass = 0, fail = 0;
const ok = (c, n) => { if (c) { pass++; console.log('  PASS ' + n); } else { fail++; console.log('  FAIL ' + n); } };

const bars = H.bars;
const pts = [{ time: bars[60].time, price: bars[60].close }, { time: bars[100].time, price: bars[100].close }];

console.log('\n[1] SERIALIZATION keeps the canonical anchor');
const eng = new DrawingEngine();
const res = eng.createDrawing('trendline', pts, {});
ok(!!res.drawing, 'createDrawing succeeded');
const d = eng.drawings[0];
ok(d.coords[0].time === pts[0].time, 'renderer coords carry time');
ok(d.coords[0].logical !== undefined && d.coords[0].logical !== null, 'renderer coords carry derived logical');
ok(d.model.toJSON().points[0].time === pts[0].time, 'toJSON() persists time (the real serializer)');
eng.syncModel(d);
ok(d.model.points[0].time === pts[0].time, 'syncModel preserves time (does not null it)');

console.log('\n[2] PERSISTENCE round-trip');
const json = JSON.parse(JSON.stringify(d.model.toJSON()));
const back = DrawingModel.fromJSON(json);
ok(back.points[0].time === pts[0].time, 'time survives serialize -> deserialize');
ok(Math.abs(back.points[0].price - pts[0].price) < 1e-9, 'price survives round-trip');
ok(back.schemaVersion === 3, 'schemaVersion is 3');

console.log('\n[3] LEGACY v2 ({logical,price}) upgrade');
const up = DrawingModel.fromJSON({
  schemaVersion: 2, id: 'legacy1', type: 'trendline',
  points: [{ logical: 60, price: 105 }, { logical: 100, price: 110 }], style: {}
});
ok(up.points[0].time === bars[60].time, 'legacy logical resolved to exact bar time');
ok(up.toJSON().points[0].time === bars[60].time, 'upgraded record serializes WITH time');
ok(up.points[0].price === 105, 'legacy price preserved (not discarded)');

console.log('\n[4] UNDO / REDO');
const n0 = eng.drawings.length;
eng.undo(); const n1 = eng.drawings.length;
eng.redo(); const n2 = eng.drawings.length;
ok(n1 === n0 - 1, 'create -> undo removes the drawing');
ok(n2 === n0, 'redo restores it');
ok(eng.drawings[0].model.points[0].time === pts[0].time, 'redo restores the exact anchor');

const dd = eng.drawings[0];
dd.coords = [{ logical: 60, price: 200, time: bars[60].time }, { logical: 100, price: 210, time: bars[100].time }];
eng.syncAndCapture(dd, 'move');
ok(eng.drawings[0].model.points[0].price === 200, 'move recorded');
eng.undo();
const r = eng.drawings[0].model.points[0];
ok(Math.abs(r.price - pts[0].price) < 1e-6, 'move -> undo restores price');
ok(r.time === pts[0].time, 'move -> undo restores time');

console.log('\n==================================================');
console.log('  PASS: ' + pass + '   FAIL: ' + fail);
console.log('==================================================');
process.exit(fail ? 1 : 0);
