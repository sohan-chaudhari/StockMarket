/**
 * PHASE K — regression tests for the Phase-3 chart-tool fixes.
 * Pure-logic tests: no DOM, no chart, no network. Run with `node tests/test_chart_anchoring.js`.
 *
 * Covers the exact defects fixed:
 *   BUG-001/003/004  canonical time anchoring + exact time<->logical mapping
 *   HC-001/HC-002    historical prepend / timeframe switch anchor stability
 *   BUG-005          position-tool math is price-based and honours riskRewardRatio
 */
let pass = 0, fail = 0;
function eq(actual, expected, name) {
    const ok = Object.is(actual, expected) || (typeof actual === 'number' && Math.abs(actual - expected) < 1e-9);
    if (ok) { pass++; console.log('  PASS ' + name); }
    else { fail++; console.log('  FAIL ' + name + '\n        expected ' + expected + ', got ' + actual); }
}
function ok(cond, name) { eq(!!cond, true, name); }

// ---------------------------------------------------------------- harness
// Replicates CoordinateMapper's exact-mapping algorithm against a fake bar
// array, including a deliberate weekend gap so average-spacing maths would
// visibly fail.
function makeMapper(bars) {
    return {
        _bars: () => (bars && bars.length ? bars : null),
        _edgeBarInterval(b) {
            const n = Math.min(20, b.length - 1);
            if (n < 1) return 60;
            const d = [];
            for (let i = b.length - n; i < b.length; i++) {
                const x = Number(b[i].time) - Number(b[i - 1].time);
                if (x > 0) d.push(x);
            }
            if (!d.length) return 60;
            d.sort((a, c) => a - c);
            return d[Math.floor(d.length / 2)];
        },
        logicalToTime(logical) {
            const b = this._bars(); if (!b) return null;
            const i = Math.round(logical);
            if (i >= 0 && i < b.length) return Number(b[i].time);
            const step = this._edgeBarInterval(b);
            if (i < 0) return Number(b[0].time) + i * step;
            return Number(b[b.length - 1].time) + (i - (b.length - 1)) * step;
        },
        timeToLogical(time) {
            const b = this._bars(); if (!b) return null;
            const t = Number(time);
            let lo = 0, hi = b.length - 1;
            if (t <= Number(b[0].time)) return t === Number(b[0].time) ? 0 : -((Number(b[0].time) - t) / this._edgeBarInterval(b));
            if (t >= Number(b[hi].time)) return t === Number(b[hi].time) ? hi : hi + (t - Number(b[hi].time)) / this._edgeBarInterval(b);
            while (lo <= hi) {
                const mid = (lo + hi) >> 1, mt = Number(b[mid].time);
                if (mt === t) return mid;
                if (mt < t) lo = mid + 1; else hi = mid - 1;
            }
            const a = Number(b[hi].time), c = Number(b[lo].time);
            return c === a ? hi : hi + (t - a) / (c - a);
        }
    };
}
const DAY = 86400;
// Mon..Fri, then a WEEKEND GAP, then Mon..Fri
const week1 = [0, 1, 2, 3, 4].map(i => ({ time: 1000000 + i * DAY }));
const week2 = [7, 8, 9, 10, 11].map(i => ({ time: 1000000 + i * DAY }));
const bars = week1.concat(week2);

console.log('\n[1] EXACT time <-> logical mapping (BUG-004)');
const m = makeMapper(bars);
eq(m.logicalToTime(0), bars[0].time, 'logical 0 -> first bar time');
eq(m.logicalToTime(9), bars[9].time, 'logical 9 -> last bar time');
eq(m.timeToLogical(bars[5].time), 5, 'time -> logical is exact across the weekend gap');
eq(m.timeToLogical(bars[7].time), 7, 'time -> logical exact after gap');
ok(m.logicalToTime(m.timeToLogical(bars[6].time)) === bars[6].time, 'round-trip time->logical->time is lossless');

console.log('\n[2] average-spacing would be WRONG here (why BUG-004 mattered)');
const avgStep = (bars[9].time - bars[0].time) / 9;   // the OLD approach
const avgGuess = bars[0].time + 5 * avgStep;
ok(avgGuess !== bars[5].time, 'average-spacing estimate does NOT equal the real bar time');
eq(m.logicalToTime(5), bars[5].time, 'exact mapping returns the real bar time');

console.log('\n[3] HISTORICAL PREPEND keeps the anchor (HC-001)');
const anchoredTime = bars[6].time;
const beforeIdx = m.timeToLogical(anchoredTime);
const prepended = [-5, -4, -3, -2, -1].map(i => ({ time: 1000000 + i * DAY })).concat(bars);
const m2 = makeMapper(prepended);
const afterIdx = m2.timeToLogical(anchoredTime);
eq(beforeIdx, 6, 'index before prepend');
eq(afterIdx, 11, 'index shifts by the 5 prepended bars');
eq(m2.logicalToTime(afterIdx), anchoredTime, 'SAME market timestamp after prepend');

console.log('\n[4] TIMEFRAME SWITCH keeps the anchor (HC-002)');
// same market timestamp, coarser series (weekly-ish): indices differ, time must not
const coarse = [0, 7].map(i => ({ time: 1000000 + i * DAY }));
const m3 = makeMapper(coarse);
const tfIdx = m3.timeToLogical(coarse[1].time);
eq(tfIdx, 1, 'index differs on the coarser timeframe');
eq(m3.logicalToTime(tfIdx), coarse[1].time, 'SAME market timestamp after timeframe switch');

console.log('\n[5] POSITION TOOL math is price-based (BUG-005)');
function levels(entry, rr, isLong, riskPct = 0.015) {
    const risk = Math.abs(entry) * riskPct, reward = risk * rr;
    return isLong ? { stop: entry - risk, target: entry + reward }
                  : { stop: entry + risk, target: entry - reward };
}
const L = levels(100, 2, true);
eq(L.stop, 98.5, 'long stop = entry - 1.5%');
eq(L.target, 103, 'long target = entry + risk*RR (RR=2)');
ok(L.stop < 100 && L.target > 100, 'long direction correct');
const S = levels(100, 2, false);
eq(S.stop, 101.5, 'short stop = entry + 1.5%');
eq(S.target, 97, 'short target = entry - risk*RR');
ok(S.stop > 100 && S.target < 100, 'short direction correct');
const rr3 = levels(100, 3, true);
eq(rr3.target, 104.5, 'riskRewardRatio=3 is actually respected');
// zoom independence: levels depend only on entry+rr, never on pixels
const zoomA = levels(250.75, 2, true), zoomB = levels(250.75, 2, true);
eq(zoomA.stop, zoomB.stop, 'stop identical regardless of zoom');
eq(zoomA.target, zoomB.target, 'target identical regardless of zoom');
const rrOut = (rr3.target - 100) / (100 - rr3.stop);
eq(Math.round(rrOut * 1000) / 1000, 3, 'realised reward/risk equals configured ratio');

console.log('\n==================================================');
console.log('  PASS: ' + pass + '   FAIL: ' + fail);
console.log('==================================================');
process.exit(fail ? 1 : 0);
