/** Fix 1: an empty page while backfilling must read as PENDING, not as an error.
 *  (Contract test: dashboard.js has no headless DOM harness, so this pins the
 *  single choke point that every chart error goes through.) */
const fs = require('fs'); const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'dashboard.js'), 'utf8');
let pass = 0, fail = 0;
const ok = (c, n) => { if (c) { pass++; console.log('  PASS ' + n); } else { fail++; console.log('  FAIL ' + n); } };
const fn = src.slice(src.indexOf('function showChartError'), src.indexOf('function hideChartError'));
ok(/No chart data\\b/i.test(fn), 'recognises the empty-data message as pending');
ok(fn.includes('Fetching history'), 'shows a neutral fetching line for it');
ok(fn.includes("console.info('Chart pending"), 'logs pending at info level, not as an error');
ok(fn.includes('errEl.textContent = msg;'), 'real errors still render their own message');
ok(!/#f23645|#ff5252/i.test(fn), 'no red error colour is forced onto the pending case');
console.log('\n  PASS: ' + pass + '   FAIL: ' + fail);
process.exit(fail ? 1 : 0);