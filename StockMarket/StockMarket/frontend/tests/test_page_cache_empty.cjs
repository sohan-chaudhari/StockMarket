/**
 * Regression test for the overview "no news" bug.
 *
 * PageCache.fetch() treated an EMPTY array as valid cache content and, because
 * its ttl equalled its expiry, served it for the whole TTL with NO background
 * refresh. overview.html therefore showed "No recent news articles found" while
 * news.html (a direct fetch) showed the same ticker's articles.
 *
 * Fix: an empty array is never cached and never served from cache.
 */
const fs = require('fs');
const path = require('path');
const H = require('./harness.cjs');

let pass = 0, fail = 0;
function ok(c, n) { if (c) { pass++; console.log('  PASS ' + n); } else { fail++; console.log('  FAIL ' + n); } }

// ---- fetch stub driving the responses -------------------------------------
let resp = [];
let fetchCount = 0;
function stubFetch(u) {
  if (String(u).indexOf('/api/auth/me') >= 0) return Promise.resolve({ ok: true, json: async () => ({}) });
  fetchCount++;
  const body = resp;
  return Promise.resolve({ ok: true, json: async () => body });
}
global.fetch = stubFetch;
window.fetch = stubFetch;

// ---- load the real page-cache.js ------------------------------------------
const src = fs.readFileSync(path.join(__dirname, '..', 'page-cache.js'), 'utf8');
try { (0, eval)(src); } catch (e) { console.error('LOAD FAILED:', e.message); process.exit(2); }
const PC = window.PageCache;
ok(PC && typeof PC.fetch === 'function', 'PageCache.fetch available');

const keyFor = (url) => 'lv_pc_' + url.replace(/[^a-zA-Z0-9]/g, '_').slice(0, 120);
const stored = (url) => { try { return JSON.parse(localStorage.getItem(keyFor(url))); } catch (e) { return null; } };

console.log('\n[1] empty array is NOT cached');
(async () => {
  localStorage.clear();
  resp = []; fetchCount = 0;
  const url1 = '/api/news/search/AAA';
  const seen1 = [];
  await PC.fetch(url1, null, 600000, (d, fc) => seen1.push({ d, fc }));
  ok(fetchCount === 1, 'empty response still fetches once');
  ok(seen1.length >= 1 && Array.isArray(seen1[0].d) && seen1[0].d.length === 0, 'callback received the empty array');
  ok(stored(url1) === null, 'nothing written to cache for an empty result');

  console.log('\n[2] a pre-existing cached EMPTY entry is ignored (fresh fetch happens)');
  localStorage.clear();
  localStorage.setItem(keyFor(url1), JSON.stringify({ data: [], cachedAt: Date.now() }));
  resp = []; fetchCount = 0;
  const seen2 = [];
  await PC.fetch(url1, null, 600000, (d, fc) => seen2.push({ d, fc }));
  ok(fetchCount === 1, 'cached empty must NOT be served as fresh -- a fetch is made');
  ok(!seen2.some(x => x.fc === true), 'callback never reported fromCache for an empty cached entry');

  console.log('\n[3] non-empty array IS cached and served within TTL');
  localStorage.clear();
  const url2 = '/api/news/search/BBB';
  resp = [{ a: 1 }]; fetchCount = 0;
  const seen3 = [];
  await PC.fetch(url2, null, 600000, (d, fc) => seen3.push({ d, fc }));
  ok(fetchCount === 1, 'first call fetches');
  ok(stored(url2) !== null, 'non-empty result is cached');
  resp = [{ a: 2 }]; fetchCount = 0;
  const seen4 = [];
  await PC.fetch(url2, null, 600000, (d, fc) => seen4.push({ d, fc }));
  ok(fetchCount === 0, 'second call within TTL does NOT re-fetch');
  ok(seen4[0] && seen4[0].fc === true && seen4[0].d[0].a === 1, 'second call is served from cache');

  console.log('\n==================================================');
  console.log('  PASS: ' + pass + '   FAIL: ' + fail);
  console.log('==================================================');
  process.exit(fail ? 1 : 0);
})();
