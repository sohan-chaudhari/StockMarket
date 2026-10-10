/**
 * Phase 2 (B2) regression: the Overview page cold-cache retry + proxy fallback.
 *
 * The Overview news section calls /api/news/search/{ticker} first and falls
 * back to /api/news/ticker/{ticker}. On a cold cache the first search response
 * is [] (the backend schedules a background refresh), which used to push the
 * section straight to the fallback and show "No recent news articles found".
 *
 * overview.html now shows a syncing state and retries the search EXACTLY ONCE
 * before falling back. fetchTickerNews() runs at page startup, so each scenario
 * re-evaluates the REAL inline page script (fresh closure) with a scripted
 * fetch -- exercising the real code, not a reimplementation.
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
    focus() {}, blur() {}, remove() {}, onclick: null,
  };
  return els[id];
}
global.document.getElementById = (id) => el(id);
global.document.querySelectorAll = () => [];
global.document.querySelector = () => null;

// ---- WebSocket stub (connectWS runs at startup) ----------------------------
global.WebSocket = function () { this.readyState = 0; this.send = function () {}; this.close = function () {}; };
global.WebSocket.OPEN = 1;

// sessionStorage is used by the page's auth helper; the harness only stubs
// localStorage.
global.sessionStorage = { _d: {}, getItem(k) { return this._d[k] || null; }, setItem(k, v) { this._d[k] = String(v); }, removeItem(k) { delete this._d[k]; }, clear() { this._d = {}; } };

// ---- scripted fetch, keyed by URL ------------------------------------------
let searchResponses = [];
let tickerResponses = [];
let searchCalls = 0, tickerCalls = 0;
const FAIL = '__HTTP_500__';   // provider error marker
function stubFetch(url) {
  const u = String(url);
  let body = {};
  if (u.indexOf('/api/news/search/') >= 0) {
    searchCalls++;
    body = searchResponses.length ? searchResponses.shift() : [];
  } else if (u.indexOf('/api/news/ticker/') >= 0) {
    tickerCalls++;
    body = tickerResponses.length ? tickerResponses.shift() : { articles: [] };
  }
  if (body === FAIL) return Promise.resolve({ ok: false, status: 500, json: async () => ({}) });
  return Promise.resolve({ ok: true, json: async () => body });
}
global.fetch = stubFetch;
window.fetch = stubFetch;

// ---- real page-cache.js + the real inline page script ----------------------
const pcSrc = fs.readFileSync(path.join(__dirname, '..', 'page-cache.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'overview.html'), 'utf8');
const inline = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].pop()[1];

function boot() {
  (0, eval)(pcSrc);
  (0, eval)(inline);
}

const ARTICLE = {
  title: 'Reliance Industries reports strong Q4 results',
  excerpt: 'Reliance Industries beat street estimates on all fronts.',
  url: 'https://www.scanx.trade/reliance-q4',
  source: 'ScanX',
  published_at: new Date().toISOString(),
  sentiment: { label: 'Bullish', sentiment_score: 0.6 },
};
const PROXY_ARTICLE = {
  title: 'RELIANCE gained 1.20% to 2900.00',
  summary: 'RELIANCE traded between 2880.00 and 2910.00, closing at 2900.00.',
  sentiment: 'positive', source: 'Market Data', url: '', published_at: '2026-10-09',
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  // ---- scenario 1: cold search -> one retry -> article (no fallback) ------
  console.log('\n[1] cold search -> ONE retry -> article (proxy fallback NOT used)');
  localStorage.clear();
  delete els['newsListContainer'];
  searchResponses = [[], [ARTICLE]]; tickerResponses = [];
  searchCalls = 0; tickerCalls = 0;
  global.location = { search: '?ticker=RELIANCE', pathname: '/overview.html', href: 'http://localhost/overview.html', origin: 'http://localhost' };
  boot();
  await sleep(350);
  ok(searchCalls === 1, 'first (cold) search made');
  ok(/Syncing live news/.test(el('newsListContainer').innerHTML), 'syncing state shown while retrying');
  await sleep(2900);
  ok(searchCalls === 2, 'search retried exactly once (got ' + searchCalls + ')');
  ok(/Reliance Industries reports strong/.test(el('newsListContainer').innerHTML),
     'article rendered after the single retry');
  ok(tickerCalls === 0, 'proxy fallback NOT used when the retry succeeds');

  // ---- scenario 2: both searches empty -> one retry -> proxy fallback -----
  console.log('\n[2] both searches empty -> ONE retry -> proxy fallback');
  localStorage.clear();
  delete els['newsListContainer'];
  searchResponses = [[], []]; tickerResponses = [{ articles: [PROXY_ARTICLE] }];
  searchCalls = 0; tickerCalls = 0;
  global.location = { search: '?ticker=RELIANCE', pathname: '/overview.html', href: 'http://localhost/overview.html', origin: 'http://localhost' };
  boot();
  await sleep(350);
  ok(searchCalls === 1, 'first search made');
  await sleep(2900);
  ok(searchCalls === 2, 'search retried exactly once before falling back (got ' + searchCalls + ')');
  ok(tickerCalls === 1, 'proxy fallback used after the retry was also empty');
  ok(/RELIANCE gained/.test(el('newsListContainer').innerHTML), 'fallback article rendered');

  // ---- scenario 3: provider failure everywhere -> graceful empty ----------
  console.log('\n[3] provider failure (HTTP 500) on search AND fallback -> graceful empty');
  localStorage.clear();
  delete els['newsListContainer'];
  searchResponses = [FAIL, FAIL]; tickerResponses = [FAIL];
  searchCalls = 0; tickerCalls = 0;
  global.location = { search: '?ticker=RELIANCE', pathname: '/overview.html', href: 'http://localhost/overview.html', origin: 'http://localhost' };
  boot();
  await sleep(350);
  ok(searchCalls === 1, 'first search made against the failing provider');
  await sleep(2900);
  ok(searchCalls === 2, 'search retried exactly once (no loop)');
  ok(tickerCalls === 1, 'proxy fallback attempted after the retry failed');
  ok(/No recent news/.test(el('newsListContainer').innerHTML),
     'degrades to the empty state instead of a stuck "syncing" placeholder');

  console.log('\n==================================================');
  console.log('  PASS: ' + pass + '   FAIL: ' + fail);
  console.log('==================================================');
  process.exit(fail ? 1 : 0);
})();
