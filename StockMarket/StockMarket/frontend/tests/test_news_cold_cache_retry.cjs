/**
 * Phase 2 (B2) regression: the News page cold-cache retry.
 *
 * /api/news/search returns [] on the very FIRST request for a ticker while the
 * backend refreshes its provider cache in the background (main.py's
 * news_search schedules a task and returns immediately). news.html used to
 * flash "No news found" on that first empty response even though the second
 * request returns articles.
 *
 * news.html now shows a syncing state and retries EXACTLY ONCE before
 * declaring "no news". This test drives the REAL inline page script extracted
 * from news.html (not a reimplementation) with a scripted fetch.
 */
const fs = require('fs');
const path = require('path');
const H = require('./harness.cjs');

let pass = 0, fail = 0;
function ok(c, n) { if (c) { pass++; console.log('  PASS ' + n); } else { fail++; console.log('  FAIL ' + n); } }

// ---- recording DOM stubs ---------------------------------------------------
const els = {};
function el(id) {
  if (!els[id]) els[id] = {
    id, style: {}, value: '', textContent: '', innerHTML: '',
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener() {}, appendChild() {}, removeChild() {}, setAttribute() {},
    getAttribute() { return null; }, querySelector() { return null; }, querySelectorAll() { return []; },
    focus() {}, blur() {}, remove() {},
  };
  return els[id];
}
global.document.getElementById = (id) => el(id);
global.document.querySelectorAll = () => [];
global.document.querySelector = () => null;
global.location = { search: '', pathname: '/news.html', href: 'http://localhost/news.html', origin: 'http://localhost' };

// ---- scripted fetch --------------------------------------------------------
let scripted = [];
let fetchCount = 0;
const FAIL = '__HTTP_500__';   // provider error marker
function stubFetch(url) {
  fetchCount++;
  const next = scripted.length ? scripted.shift() : [];
  if (next === FAIL) {
    return Promise.resolve({ ok: false, status: 500, json: async () => ({}) });
  }
  return Promise.resolve({ ok: true, json: async () => next });
}
global.fetch = stubFetch;
window.fetch = stubFetch;

// ---- load the REAL inline page script --------------------------------------
const html = fs.readFileSync(path.join(__dirname, '..', 'news.html'), 'utf8');
const m = html.match(/<script>([\s\S]*?)<\/script>/);
(0, eval)(m[1]);
const app = window.newsApp;
ok(app && typeof app.filterNews === 'function', 'news.html inline app loaded');

const ARTICLE = {
  title: 'Reliance Industries reports strong Q4 results',
  excerpt: 'Reliance Industries beat street estimates on all fronts.',
  url: 'https://www.scanx.trade/reliance-q4',
  source: 'ScanX',
  published_at: new Date().toISOString(),
  sentiment: { label: 'Bullish', sentiment_score: 0.6 },
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  // let the page's own boot fetch settle so it does not pollute fetchCount
  await sleep(150);

  console.log('\n[1] cold cache: [] then articles -> exactly ONE retry, article rendered');
  delete els['newsContainer'];
  el('newsSearch').value = 'RELIANCE';
  scripted = [[], [ARTICLE]]; fetchCount = 0;
  app.filterNews('RELIANCE');
  await sleep(350);                       // debounce window
  ok(fetchCount === 1, 'first (cold) request made');
  await sleep(2700);                      // single retry delay (2500ms) + fetch
  ok(fetchCount === 2, 'exactly one retry -- no uncontrolled request loop');
  ok(/Reliance Industries reports strong/.test(el('newsContainer').innerHTML),
     'article rendered after the single retry');

  console.log('\n[2] genuine empty: [] then [] -> ONE retry then "No news found"');
  delete els['newsContainer'];
  el('newsSearch').value = 'ZZNONEWS';
  scripted = [[], []]; fetchCount = 0;
  app.filterNews('ZZNONEWS');
  await sleep(350);
  ok(fetchCount === 1, 'first request made');
  await sleep(2700);
  ok(fetchCount === 2, 'exactly one retry (a genuine empty still resolves, no loop)');
  ok(/No news found for/.test(el('newsContainer').innerHTML),
     'empty state shown only after the single retry');

  console.log('\n[3] warm cache: articles on the first response -> NO retry');
  delete els['newsContainer'];
  el('newsSearch').value = 'RELIANCE';
  scripted = [[ARTICLE]]; fetchCount = 0;
  app.filterNews('RELIANCE');
  await sleep(350);
  ok(fetchCount === 1, 'first request made');
  await sleep(2700);
  ok(fetchCount === 1, 'no retry when the first response already has articles');
  ok(/Reliance Industries reports strong/.test(el('newsContainer').innerHTML),
     'article rendered from the first response');

  console.log('\n[4] provider failure (HTTP 500) -> ONE retry then graceful empty, no crash/loop');
  delete els['newsContainer'];
  el('newsSearch').value = 'RELIANCE';
  scripted = [FAIL, FAIL]; fetchCount = 0;
  app.filterNews('RELIANCE');
  await sleep(350);
  ok(fetchCount === 1, 'first request made against the failing provider');
  await sleep(2700);
  ok(fetchCount === 2, 'exactly one retry against the failing provider (no loop)');
  ok(/No news found for/.test(el('newsContainer').innerHTML),
     'provider failure degrades to the empty state instead of crashing');

  console.log('\n==================================================');
  console.log('  PASS: ' + pass + '   FAIL: ' + fail);
  console.log('==================================================');
  process.exit(fail ? 1 : 0);
})();
