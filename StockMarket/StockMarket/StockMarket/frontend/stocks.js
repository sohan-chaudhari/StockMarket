// Stocks Data - loaded from backend with localStorage cache
// Cache key / version key
var _STOCKS_CACHE_KEY    = 'leverage_stocks';
var _STOCKS_VERSION_KEY  = 'leverage_stocks_version';

window.ALL_STOCKS = [];

window.addEventListener('error', function (e) {
  if (e.target.tagName === 'IMG' && e.target.src && e.target.src.includes('/logos/')) {
    e.target.style.display = 'none';
    e.preventDefault();
    e.stopPropagation();
  }
}, true);

(async function loadStocks() {
  'use strict';

  // ── 1. Fetch the lightweight version string from server ──────────────────
  var serverVersion = null;
  try {
    var vRes = await fetch('/api/stocks-version');
    if (vRes.ok) {
      var vData = await vRes.json();
      serverVersion = vData.version || null;
    }
  } catch (e) {
    // Network error – will try stale cache below
  }

  // ── 2. Check localStorage cache ──────────────────────────────────────────
  var storedVersion, storedRaw;
  try { storedVersion = localStorage.getItem(_STOCKS_VERSION_KEY); } catch(e) {}
  try { storedRaw     = localStorage.getItem(_STOCKS_CACHE_KEY); } catch(e) {}

  // Cache HIT: version matches and data exists
  if (serverVersion && serverVersion === storedVersion && storedRaw) {
    try {
      window.ALL_STOCKS = JSON.parse(storedRaw);
      window.dispatchEvent(new Event('stocksLoaded'));
      console.log('[Stocks] Cache hit – ' + window.ALL_STOCKS.length + ' stocks (localStorage)');
      return;
    } catch (parseErr) {
      // Corrupt cache – fall through to re-fetch
    }
  }

  // ── 3. Version mismatch / no cache – fetch fresh from API ───────────────
  try {
    var ac = new AbortController();
    var to = setTimeout(function () { ac.abort(); }, 15000);
    var response = await fetch('/api/all-stocks', { signal: ac.signal });
    clearTimeout(to);
    if (!response.ok) throw new Error('HTTP ' + response.status);
    var stocks = await response.json();
    window.ALL_STOCKS = stocks;

    // Persist to localStorage
    if (serverVersion) localStorage.setItem(_STOCKS_VERSION_KEY, serverVersion);
    try {
      localStorage.setItem(_STOCKS_CACHE_KEY, JSON.stringify(stocks));
    } catch (quotaErr) {
      // localStorage full (very rare) – not critical, just skip save
    }

    window.dispatchEvent(new Event('stocksLoaded'));
    console.log('[Stocks] Fetched ' + stocks.length + ' stocks from API');

  } catch (fetchErr) {
    console.error('[Stocks] API fetch failed:', fetchErr);

    // ── 4. Last resort: serve stale cache even if version is old ──────────
    if (storedRaw) {
      try {
        window.ALL_STOCKS = JSON.parse(storedRaw);
        window.dispatchEvent(new Event('stocksLoaded'));
        console.warn('[Stocks] Serving stale cache as offline fallback – ' + window.ALL_STOCKS.length + ' stocks');
      } catch (e) {
        window.ALL_STOCKS = [];
      }
    }
  }
})();
