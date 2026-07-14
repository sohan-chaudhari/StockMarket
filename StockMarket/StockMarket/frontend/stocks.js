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
  // Serve stale cache immediately as fallback, then refresh silently in background
  if (storedRaw) {
    try {
      window.ALL_STOCKS = JSON.parse(storedRaw);
      window.dispatchEvent(new Event('stocksLoaded'));
      console.log('[Stocks] Showing cached stocks while fetching fresh data...');
    } catch (e) {}
  }

  // Background retry up to 2 times
  for (var attempt = 0; attempt < 2; attempt++) {
    try {
      var ac = new AbortController();
      var to = setTimeout(function () { ac.abort(); }, 15000);
      var response = await fetch('/api/all-stocks', { signal: ac.signal });
      clearTimeout(to);
      if (!response.ok) throw new Error('HTTP ' + response.status);
      var stocks = await response.json();
      window.ALL_STOCKS = stocks;

      if (serverVersion) localStorage.setItem(_STOCKS_VERSION_KEY, serverVersion);
      try {
        localStorage.setItem(_STOCKS_CACHE_KEY, JSON.stringify(stocks));
      } catch (quotaErr) {}

      window.dispatchEvent(new Event('stocksLoaded'));
      console.log('[Stocks] Fetched ' + stocks.length + ' stocks from API');
      return;

    } catch (fetchErr) {
      if (attempt < 1) {
        await new Promise(function (r) { setTimeout(r, 3000); });
      }
    }
  }
  console.warn('[Stocks] API unavailable, using cached data');
})();
