/**
 * Phase 1 UX batch 1+2/3/4 contracts (static, dependency-free).
 * Asserts the disclosures, exchange wiring, identity guard, history badge,
 * ranking note, error capture - and that the internal 1m pipeline is untouched.
 */
const fs = require('fs');
const path = require('path');
const F = path.join(__dirname, '..');
const read = f => fs.readFileSync(path.join(F, f), 'utf8');

let pass = 0, fail = 0;
const ok = (c, n) => { if (c) { pass++; console.log('  PASS ' + n); } else { fail++; console.log('  FAIL ' + n); } };

const DISC = 'Simulated - paper trading only. No real order is placed.';
const stockHtml = read('stock.html');
const stockLogic = read('stock-logic.js');
const portfolio = read('portfolio.html');
const dash = read('dashboard.js');
const nav = read('nav.js');
const indexHtml = read('index.html');

console.log('\n[1] UX-601 paper-trading disclosure');
ok(stockHtml.includes('id="paperTradeDisclosure"') && stockHtml.includes(DISC), 'trade panel carries the disclosure');
ok((stockLogic.split(DISC).length - 1) >= 2, 'disclosure appears in the order message and the success detail');
ok(portfolio.includes(DISC), 'portfolio balance area carries the disclosure');
ok(stockLogic.includes('role="note"') || stockHtml.includes('role="note"'), 'disclosure is marked as a note for AT');
ok(!/Order Executed Successfully/.test(stockLogic), 'success copy no longer claims a real execution');
ok(!/\bAMO Order Queued \(09:15 AM Market Open\)/.test(stockLogic), 'AMO copy no longer claims a real order was queued');

console.log('\n[2] UX-313 exchange identity');
ok(stockHtml.includes('id="tradeModalExchange"'), 'exchange slot exists beside the order ticker');
ok(stockLogic.includes('window.leverageExchangeFor'), 'exchange resolver exists');
ok(/r\.exchange/.test(stockLogic), 'exchange is read from the instrument record, not derived from the ticker');
ok(/return null;\s*$|\s*return null;/.test(stockLogic) && /if \(!s\) return null;/.test(stockLogic), 'resolver returns null when unknown (never guesses)');
ok(/ALL_STOCKS/.test(stockLogic), 'uses the existing instrument universe');

console.log('\n[3] UX-201 unsupported 1m');
const vrLine = dash.split('\n').find(l => l.indexOf('var VALID_RANGES =') !== -1) || '';
ok(vrLine.length > 0 && vrLine.indexOf("'1m'") === -1, 'user-facing range whitelist no longer offers 1m');
ok(dash.includes("'1m': 60") || dash.includes("'1m': 60,"), 'internal 1m candle mapping preserved');

console.log('\n[4] UX-210 payload identity');
ok(/window\._leveragePayloadTickerOk = function/.test(dash), 'identity checker exposed');
ok(dash.includes('_leveragePayloadTickerOk(resp)'), 'history page is checked before use');
ok(dash.includes('discarded history page for a different ticker'), 'mismatch is discarded (no retry)');
ok(!/_leveragePayloadTickerOk[\s\S]{0,400}fetch\(/.test(dash), 'guard performs no additional request');
ok(/NIFTY50: 'NIFTY'/.test(dash) && /BSESN: 'SENSEX'/.test(dash), 'documented index aliases resolve to one instrument');
ok(/NIFTY_FIN_SERVICE: 'FINNIFTY'/.test(dash) && /NSEMDCP50: 'MIDCAP'/.test(dash) && /CNXSC: 'SMALLCAP'/.test(dash),
   'documented underscore/caret aliases resolve too');

console.log('\n[5] UX-212 history completeness');
ok(/function _updateHistoryBadge/.test(dash), 'badge renderer defined');
ok(dash.includes('_updateHistoryBadge(chartParent)'), 'badge is rendered with the chart');
ok(dash.includes("'History from '"), 'badge states the earliest loaded date');
ok(dash.includes('scroll left to load earlier data'), 'badge explains how earlier data is reached');
ok(/_hasMoreHistoricalData !== false/.test(dash), 'badge reflects completeness instead of implying full history');

console.log('\n[6] UX-602 ranking neutrality');
ok(indexHtml.includes('not an investment recommendation'), 'movers carry a neutrality statement');
ok(/Ranked by % change from the previous close/.test(indexHtml), 'ranking criterion and window are stated');

console.log('\n[7] UX-701 error capture (console-only)');
ok(nav.includes('__levErrCapture'), 'capture state present');
ok(nav.includes('addEventListener("error"') && nav.includes('addEventListener("unhandledrejection"'), 'both error sources handled');
ok(nav.includes('if (s.seen[key]) return;'), 'duplicates suppressed');
ok(nav.includes('s.count >= s.max'), 'hard cap prevents flooding');
ok(!/fetch\s*\(/.test(nav.slice(nav.indexOf('__levErrCapture') - 400)), 'no network call added');
const capStart = nav.indexOf('Minimal, dependency-free local error capture');
const capBlock = capStart === -1 ? '' : nav.slice(capStart, nav.indexOf('})();', capStart) + 5);
ok(capBlock.length > 0 && !/localStorage|sessionStorage|authorization|bearer|password|cookie|document\.cookie/i.test(capBlock),
   'capture block does not reference credentials');

console.log('\n[8] UX-210 main-load guard + UX-213 legend reset');
ok(/_leveragePayloadTickerOk\(jsonResp\)/.test(dash), 'main chart load is checked for instrument identity');
ok(dash.includes("'[Chart] discarded load for a different ticker'"), 'main-load mismatch is discarded (no retry)');
ok(!/_leveragePayloadTickerOk\(jsonResp\)[\s\S]{0,300}fetch\(/.test(dash), 'main-load guard issues no extra request');
ok(/if \(isRangeSwitch\) \{[\s\S]{0,320}ohlc-open/.test(dash), 'OHLC legend resets on a genuine range switch only');
ok(!/ohlc-open[\s\S]{0,120}--[\s\S]{0,40}(if \(true\)|setInterval)/.test(dash), 'legend reset is not unconditional or repeated');
ok(/getComputedStyle\(host\)\.position === 'static'/.test(dash), 'history badge host gets a positioning context');
console.log('\n==================================================');
console.log('  PASS: ' + pass + '   FAIL: ' + fail);
console.log('==================================================');
process.exit(fail ? 1 : 0);
