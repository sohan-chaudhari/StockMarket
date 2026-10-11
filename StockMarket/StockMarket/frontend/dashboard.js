/* ==========================================
   DASHBOARD SPECIFIC LOGIC
   ========================================== */

// Phase 3: pure decision for the forming candle's OPEN price. Extracted so it
// can be unit-tested (see frontend/tests/test_forming_open.cjs).
//   1. the backend's authoritative forming candle (seed) when available;
//   2. the official day open for the session's first bucket;
//   3. the PREVIOUS completed candle's close -- an APPROXIMATION of the bucket's
//      first trade price, never the arbitrary page-load price;
//   4. the live price only as a last resort (unverified).
function _computeFormingOpen(seedOpen, isFirstBar, dayOpen, prevClose, liveCurrent) {
  if (seedOpen != null && seedOpen > 0) return seedOpen;
  if (isFirstBar && dayOpen != null && dayOpen > 0) return dayOpen;
  if (prevClose != null && prevClose > 0) return prevClose;
  return liveCurrent;
}

function escapeHTML(str) { var div = document.createElement('div'); div.appendChild(document.createTextNode(str)); return div.innerHTML; }

// Compact Indian volume formatting: 13.94 Cr, 1.17 L, 38.00 K
function formatVolumeCompact(n) {
  if (n == null || n === '') return '--';
  if (typeof n === 'string' && (n.includes('Cr') || n.includes('L') || n.includes('K'))) return n;
  var num = Number(n);
  if (isNaN(num)) return String(n);
  if (num === 0) return '0';
  var abs = Math.abs(num);
  if (abs >= 10000000) {
    return (num / 10000000).toFixed(2) + ' Cr';
  }
  if (abs >= 100000) {
    return (num / 100000).toFixed(2) + ' L';
  }
  if (abs >= 1000) {
    return (num / 1000).toFixed(2) + ' K';
  }
  return num.toLocaleString('en-IN');
}
window.formatVolumeCompact = formatVolumeCompact;
window.fmtCompact = formatVolumeCompact;

/* ==========================================
   DASHBOARD PAGE INIT
   ========================================== */
document.addEventListener('DOMContentLoaded', function () {
  if (document.querySelector('.dashboard-main')) {
    initDashboard();
  }
});

// Handle bfcache restore: when navigating 'back' to dashboard, instantly reconnect WS
window.addEventListener('pageshow', function (event) {
  if (event.persisted && document.querySelector('.dashboard-main')) {
    // Page was restored from bfcache
    if (window.DashboardWS && !window.DashboardWS.isConnected()) {
      window.DashboardWS.start();
    }
    // Force a fresh fetch of index prices immediately to catch up on missed data
    if (typeof fetchIndexPrices === 'function') {
      fetchIndexPrices();
    }
  }
});

function initDashboard() {
  // Hide global loader immediately so skeleton/cached UI is visible
  var loader = document.getElementById('leverage-loader');
  if (loader) loader.style.display = 'none';

  // Default market to open; real status arrives via fetchMarketStatus/WS market_status
  window._marketOpen = true;
  try {
    var mo = localStorage.getItem('market_open');
    var moTs = parseInt(localStorage.getItem('market_open_ts') || '0', 10);
    if (mo === 'false' && (Date.now() - moTs < 3600000)) {
      window._marketOpen = false;
    }
  } catch(e) {}

  initThemeToggle();

  // Seed UI instantly from last known prices (eliminates "--" flash)
  seedUIFromCache();

  // Start WebSocket for live price updates (always — backend handles market-open internally)
  if (window.DashboardWS) {
    window.DashboardWS.start();
  }

  // Register WS event handlers
  if (!window._wsListenersRegistered) {
    window.addEventListener('dashboard_price_update', onPriceUpdate);
    window.addEventListener('dashboard_market_movers', onMarketMovers);
    window.addEventListener('dashboard_market_status', onMarketStatus);
    window._wsListenersRegistered = true;
  }

  // REST fallback: fetch index prices every 10s if WS hasn't pushed recently
  window._lastWsPriceTime = 0;
  if (!window._restFallbackTimer) {
    window._restFallbackTimer = setInterval(function() {
      if (window._marketOpen === false) return; // Do not poll/toggle when market is closed
      if (Date.now() - window._lastWsPriceTime < 12000) return;
      fetch('/api/live-prices', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({tickers:['NIFTY','SENSEX','BANKNIFTY','FINNIFTY','MIDCAP','SMALLCAP']}) })
        .then(function(r){ return r.json(); })
        .then(function(data){
          if (data && typeof data === 'object') {
            updateTickerStrip(data);
            var ct = window._chartTicker || 'NIFTY';
            var d = data[ct];
            if (d && d.current) {
              updateMinichartOHLC(d);
            }
          }
        })
        .catch(function(){});
    }, 30000);
  }

  // Render strip first (shows cached values or "--")
  try { renderTickerStrip(); }  catch (e) { console.error('renderTickerStrip err:', e); }
  try { initNiftyChart(); }     catch (e) { console.error('initNiftyChart err:', e); }
  try { fetchMarketSentiment(); } catch (e) { console.error('fetchMarketSentiment err:', e); }
  if (!window._sentimentInterval) {
    window._sentimentInterval = setInterval(function () {
      try { fetchMarketSentiment(); } catch (e) {}
    }, 120000);
  }

  // Fire all data fetches in parallel — no need to sequence them
  fetchIndexPrices();
  fetchMarketStatus();
  fetchMarketMovers('all');
  setTimeout(function () {
    ['large', 'mid', 'small'].forEach(function (c) { fetchMarketMovers(c); });
  }, 300);
  fetchWatchlist();

  // Watchlist refresh every 15s to keep in sync with watchlist page additions
  if (!window._wlRefreshInterval) {
    window._wlRefreshInterval = setInterval(function () {
      try { fetchWatchlist(); } catch (e) {}
    }, 15000);
  }

  // Market movers refresh every 10 s (skip when WS is connected — WS pushes movers)
  if (!window._moversInterval) {
    window._moversInterval = setInterval(function () {
      if (window._marketOpen === false) return;
      if (window.DashboardWS && window.DashboardWS.isConnected()) return;
      try { fetchMarketMovers(); } catch (e) { console.error('refresh movers err:', e); }
    }, 10000);
  }
}

/* ── Helper to resolve ticker aliases (e.g. NIFTY <-> NIFTY50) ─────────────── */
function getPriceForTicker(prices, ticker) {
  if (!prices || !ticker) return null;
  var clean = String(ticker).toUpperCase().replace(/\.(NS|BO)$/i, '');
  if (prices[clean] && prices[clean].current) return prices[clean];
  if (prices[ticker] && prices[ticker].current) return prices[ticker];
  var aliases = {
    'NIFTY': ['NIFTY50', 'NIFTY 50', '^NSEI'],
    'NIFTY50': ['NIFTY', 'NIFTY 50', '^NSEI'],
    'BANKNIFTY': ['NIFTYBANK', '^NSEBANK'],
    'NIFTYBANK': ['BANKNIFTY', '^NSEBANK'],
    'FINNIFTY': ['NIFTYFIN', 'NIFTY_FIN_SERVICE', '^NIFTY_FIN_SERVICE.NS'],
    'SENSEX': ['^BSESN', 'BSESN'],
    'MIDCAP': ['MIDCPNIFTY', '^NSEMDCP50'],
    'SMALLCAP': ['NIFTYSMLCAP100', '^CNXSC']
  };
  var altList = aliases[clean] || [];
  for (var i = 0; i < altList.length; i++) {
    var alt = altList[i];
    if (prices[alt] && prices[alt].current) return prices[alt];
  }
  return null;
}
window.getPriceForTicker = getPriceForTicker;

/* ── Helper to update dashboard minichart header + OHLC footer ────────────── */
function updateMinichartOHLC(d) {
  if (!d) return;
  window._lastMinichartOHLC = d;
  var pEl = document.getElementById('niftyPrice');
  if (pEl && d.current != null && d.current > 0) {
    window._lastMainPrice = d.current;
    pEl.innerText = Number(d.current).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  var prev = (d.prev_close != null && d.prev_close > 0) ? d.prev_close : ((d.open != null && d.open > 0) ? d.open : d.current);
  if (d.current != null && d.current > 0) {
    var diff = (d.change != null && !isNaN(d.change)) ? Number(d.change) : (d.current - prev);
    var pct  = (d.change_pct != null && !isNaN(d.change_pct)) ? Number(d.change_pct) : ((prev > 0) ? (diff / prev) * 100 : 0);
    var sign = diff >= 0 ? '+' : '';
    var cEl  = document.getElementById('niftyChange');
    if (cEl) {
      cEl.innerText = sign + diff.toFixed(2) + ' (' + sign + pct.toFixed(2) + '%)';
      cEl.className = 'price-change ' + (diff >= 0 ? 'text-green' : 'text-red');
    }
  }

  var oEl = document.getElementById('niftyO');
  if (oEl && d.open != null && d.open > 0) oEl.innerText = Number(d.open).toFixed(2);
  var hEl = document.getElementById('niftyH');
  if (hEl && d.high != null && d.high > 0) hEl.innerText = Number(d.high).toFixed(2);
  var lEl = document.getElementById('niftyL');
  if (lEl && d.low != null && d.low > 0)  lEl.innerText = Number(d.low).toFixed(2);
  var cEl2 = document.getElementById('niftyC');
  if (cEl2 && d.prev_close != null && d.prev_close > 0) cEl2.innerText = Number(d.prev_close).toFixed(2);

  // Synchronize matching ticker strip element simultaneously so both always show identical price
  var activeTkr = (window._chartTicker || 'NIFTY').toUpperCase().replace(/\.(NS|BO)$/i, '');
  var stripPEl = document.getElementById('strip-price-' + activeTkr);
  var stripCEl = document.getElementById('strip-change-' + activeTkr);
  if (stripPEl && d.current != null && d.current > 0) {
    window._lastTickPrices = window._lastTickPrices || {};
    window._lastTickPrices[activeTkr] = d.current;
    stripPEl.innerText = '₹' + Number(d.current).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (stripCEl) {
      var diff2 = (d.change != null && !isNaN(d.change)) ? Number(d.change) : (d.current - prev);
      var pct2  = (d.change_pct != null && !isNaN(d.change_pct)) ? Number(d.change_pct) : ((prev > 0) ? (diff2 / prev) * 100 : 0);
      var s2    = diff2 >= 0 ? '▲ +' : '▼ ';
      stripCEl.innerText = s2 + diff2.toFixed(2) + ' (' + pct2.toFixed(2) + '%)';
      stripCEl.className = 'change ' + (diff2 >= 0 ? 'text-green' : 'text-red');
    }
  }
}
window.updateMinichartOHLC = updateMinichartOHLC;

/* ── Seed UI from localStorage (called before WS connects) ──────────────── */
function seedUIFromCache() {
  try {
    var raw = localStorage.getItem('llp');
    var ts  = parseInt(localStorage.getItem('llp_ts') || '0', 10);
    if (!raw || (Date.now() - ts > 300000)) return; // 5-min TTL
    if (new Date(ts).toDateString() !== new Date().toDateString()) return; // Never seed cross-day stale prices
    var prices = JSON.parse(raw);

    // Seed ticker strip (elements already in DOM from home.html)
    updateTickerStrip(prices);

    // Seed dashboard chart header (dynamic ticker)
    window._chartTicker = window._chartTicker || 'NIFTY';
    var chartData = getPriceForTicker(prices, window._chartTicker);
    if (chartData && chartData.current) {
      updateMinichartOHLC(chartData);
      updateNiftyLiveCandle(window._chartTicker, chartData.current);
    }
  } catch (e) { /* non-critical */ }
}

/**
 * Real-time candlestick & area updates for the dashboard minichart.
 * Updates both the forming candle and area series using identical 5m bucket time.
 */
function updateNiftyLiveCandle(ticker, price, serverMs) {
  var activeTicker = window._chartTicker || 'NIFTY';
  if (ticker && ticker !== activeTicker) return;
  if (!price || price <= 0) return;
  if (!window._niftyCandleSeries || !window._niftyIntervalSec) return;

  var _off = window._serverClockOffset;
  var nowMs = (serverMs != null && !isNaN(serverMs)) ? serverMs : (Date.now() + (_off != null && !isNaN(_off) ? _off : 0));
  var IST_OFFSET_MS = 5.5 * 3600 * 1000;
  var _istDate = new Date(nowMs + IST_OFFSET_MS);
  var _istDay = _istDate.getUTCDay();
  var _istMin = _istDate.getUTCHours() * 60 + _istDate.getUTCMinutes();
  var _isMarketActive = (window._marketOpen !== undefined) ? window._marketOpen : (_istDay >= 1 && _istDay <= 5 && _istMin >= (9 * 60 + 15) && _istMin < (15 * 60 + 30));

  if (!_isMarketActive) return;

  // If historical candles haven't finished loading yet, remember the price
  // so loadChartData applies it immediately the moment historical data arrives.
  if (!window._lastHistoricalCandle) {
    window._pendingMinichartPrice = price;
    return;
  }

  var nowSec = Math.floor(nowMs / 1000);
  var IST_OFFSET_SEC = 5.5 * 3600;
  var SESSION_START_MIN = 9 * 60 + 15;
  var SESSION_START_SEC = SESSION_START_MIN * 60;
  var bucketSec = window._niftyIntervalSec;
  var istSec = (nowSec + IST_OFFSET_SEC) % 86400;
  var secInSession = istSec - SESSION_START_SEC;
  var bucketIdx = Math.max(0, Math.floor(secInSession / bucketSec));
  var bucketIstSec = SESSION_START_SEC + bucketIdx * bucketSec;
  var istMidnightUtcSec = Math.floor((nowMs + IST_OFFSET_SEC * 1000) / 86400000) * 86400 - IST_OFFSET_SEC;
  var candleTime = istMidnightUtcSec + bucketIstSec;

  var existing = window._lastHistoricalCandle;
  if (!window._formingNiftyCandle || candleTime !== window._lastNiftyTime) {
    window._lastNiftyTime = candleTime;
    if (existing && existing.time === candleTime) {
      window._formingNiftyCandle = {
        open: existing.open,
        high: Math.max(existing.high, price),
        low: Math.min(existing.low, price),
        close: price
      };
    } else {
      window._formingNiftyCandle = {
        open: (existing && existing.close) ? existing.close : price,
        high: price,
        low: price,
        close: price
      };
    }
  }

  var fc = window._formingNiftyCandle;
  if (!fc) return;
  fc.close = price;
  if (price > fc.high) fc.high = price;
  if (price < fc.low)  fc.low = price;

  try {
    if (window._niftyCandleSeries && candleTime != null && !isNaN(candleTime) && fc.open != null && fc.high != null && fc.low != null && fc.close != null) {
      window._niftyCandleSeries.update({
        time: candleTime,
        open: fc.open,
        high: fc.high,
        low: fc.low,
        close: fc.close
      });
      window._lastHistoricalCandle = {
        time: candleTime,
        open: fc.open,
        high: fc.high,
        low: fc.low,
        close: fc.close
      };
    }
  } catch (e) {}

  if (window._niftyAreaSeries && candleTime != null && !isNaN(candleTime) && price != null && !isNaN(price)) {
    try {
      window._niftyAreaSeries.update({ time: candleTime, value: price });
    } catch (e) {}
  }
}

/* ── WS event handlers ───────────────────────────────────────────────────── */
function onPriceUpdate(evt) {
  // When the market is closed the backend can still relay the broker's last
  // snapshot, and applying it kept the ticker strip, minichart and Top
  // Gainers/Losers rows visibly changing after the session ended. Match the
  // REST fallback's guard below so a closed market renders a static snapshot.
  if (window._marketOpen === false) return;
  var raw = evt.detail;
  if (!raw) return;
  var prices = raw.prices || raw;
  window._lastWsPriceTime = Date.now();

  // 1. Ticker strip
  updateTickerStrip(prices);

  // 2. Dashboard chart header + OHLC footer (dynamic ticker)
  window._chartTicker = window._chartTicker || 'NIFTY';
  var chartTickerData = getPriceForTicker(prices, window._chartTicker);
  if (chartTickerData && chartTickerData.current) {
    updateMinichartOHLC(chartTickerData);
    updateNiftyLiveCandle(window._chartTicker, chartTickerData.current);
  }

  // 3. Mover list prices (update ALL instances across gainers, losers, and most active)
  Object.keys(prices).forEach(function (ticker) {
    var d = prices[ticker];
    if (!d || d.current === undefined || d.current === null) return;
    var cleanTicker = ticker.replace('.NS', '').replace('.BO', '').trim().toUpperCase();
    var cur = Number(d.current);
    if (isNaN(cur) || cur <= 0) return;

    window._lastMoverPrices = window._lastMoverPrices || {};
    var lastMv = window._lastMoverPrices[cleanTicker];
    var isUp = lastMv != null ? cur > lastMv : null;
    window._lastMoverPrices[cleanTicker] = cur;

    // 3a. Update all price elements matching this ticker
    var priceElements = document.querySelectorAll('.mv-price-' + cleanTicker + ', [id$="-' + cleanTicker + '"].mover-price, #mv-price-' + cleanTicker);
    priceElements.forEach(function (pEl) {
      pEl.innerText = '\u20B9' + cur.toFixed(2);
    });

    // 3b. Update previous close display
    if (d.prev_close != null && Number(d.prev_close) > 0) {
      var prevElements = document.querySelectorAll('.mv-prev-' + cleanTicker + ', [id$="-' + cleanTicker + '"].mover-prev, #mv-prev-' + cleanTicker);
      prevElements.forEach(function (prevEl) {
        prevEl.innerText = 'Prev: \u20B9' + Number(d.prev_close).toFixed(2);
      });
    }

    // 3c. Update change %, abs change, and volume for every row
    var rowElements = document.querySelectorAll('.mv-row-' + cleanTicker + ', [data-ticker="' + cleanTicker + '"], #mv-item-' + cleanTicker);
    rowElements.forEach(function (itemEl) {
      var isGainerCard = !!(itemEl.closest && itemEl.closest('#gainersList'));
      var isLoserCard  = !!(itemEl.closest && itemEl.closest('#losersList'));
      var chEl = itemEl.querySelector('.mv-change');
      if (chEl) {
        var pct = d.change_pct != null ? Number(d.change_pct) : null;
        var prevP = (d.prev_close != null && Number(d.prev_close) > 0) ? Number(d.prev_close) : null;
        if (pct == null && prevP != null && prevP > 0) {
          pct = ((cur - prevP) / prevP) * 100;
        }
        if (pct != null && !isNaN(pct)) {
          // Strict Sanity Guard: Top Gainers must NEVER show a negative stock, Top Losers must NEVER show a positive stock
          if (isGainerCard && pct < 0) {
            if (typeof fetchMarketMovers === 'function') fetchMarketMovers(window._currentMoverCap, true);
            return;
          }
          if (isLoserCard && pct > 0) {
            if (typeof fetchMarketMovers === 'function') fetchMarketMovers(window._currentMoverCap, true);
            return;
          }

          var arrow = pct >= 0 ? '▲' : '▼';
          var absStr = '';
          if (prevP != null && prevP > 0) {
            var absChg = cur - prevP;
            absStr = ' <span class="mv-abs-change" style="white-space:nowrap;">(' + (absChg >= 0 ? '+₹' : '-₹') + Math.abs(absChg).toFixed(2) + ')</span>';
          }
          chEl.innerHTML = arrow + ' ' + Math.abs(pct).toFixed(2) + '%' + absStr;
          chEl.className = 'mv-change mv-change-' + cleanTicker + ' ' + (pct >= 0 ? 'text-green' : 'text-red');
        }
      }

      // Update volume. Require > 0: an LTP-only tick carries no cumulative day
      // volume, so a 0 must never overwrite a volume already rendered from the
      // baseline/DB (that produced "Volume: 0" on Top Gainers/Losers).
      var volEl = itemEl.querySelector('.mv-volume');
      if (volEl && d.volume != null && Number(d.volume) > 0) {
        volEl.innerText = 'Volume: ' + formatVolumeCompact(d.volume);
      }
    });
  });

  // 4. Watchlist card prices (elements added with id="wl-price-TICKER")
  Object.keys(prices).forEach(function (ticker) {
    var d      = prices[ticker];
    var pEl2   = document.getElementById('wl-price-' + ticker);
    var chEl   = document.getElementById('wl-change-' + ticker);
    if (pEl2 && d && d.current !== undefined && d.current !== null) {
      var price = d.current;
      window._lastWlPrices = window._lastWlPrices || {};
      window._lastWlPrices[ticker] = price;
      var prev  = (d.prev_close != null && d.prev_close > 0) ? d.prev_close : ((d.open != null && d.open > 0) ? d.open : price);
      var dif   = price - prev;
      var pc    = prev > 0 ? (dif / prev) * 100 : 0;
      var sg    = dif >= 0 ? '▲ +' : '▼ ';
      var col   = dif >= 0 ? 'text-green' : 'text-red';
      pEl2.innerText = '\u20B9' + price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      if (chEl) {
        chEl.innerText = sg + Math.abs(dif).toFixed(2) + ' (' + Math.abs(pc).toFixed(2) + '%)';
        chEl.className = 'wl-change ' + col;
      }
    }
  });

}

function onMarketMovers(evt) {
  var data = evt.detail;
  if (!data) return;

  // Store in client-side cap cache for 0ms instant tab switching
  var payloadCap = data.cap || 'all';
  window._moverCapCache = window._moverCapCache || {};
  window._moverCapCache[payloadCap] = data;

  // If this movers payload is for a different cap tier than currently active, ignore
  var currentCap = window._currentMoverCap || 'all';
  if (payloadCap !== currentCap) {
    return;
  }
  if (data.market_open !== undefined) {
    window._marketOpen = data.market_open;
    updateMarketStatusUI();
    try { localStorage.setItem('market_open', data.market_open ? 'true' : 'false'); } catch(e) {}
  }
  var actualGainers = (data.gainers || []).filter(function (s) { return s && Number(s.change_pct) > 0; });
  var actualLosers  = (data.losers || []).filter(function (s) { return s && Number(s.change_pct) < 0; });
  actualGainers.sort(function (a, b) { return Number(b.change_pct) - Number(a.change_pct); });
  actualLosers.sort( function (a, b) { return Number(a.change_pct) - Number(b.change_pct); });
  var actualActive  = (data.most_active || []).slice();
  actualActive.sort( function (a, b) { return Number(b.volume || 0) - Number(a.volume || 0); });
  var isDashboard = !!document.querySelector('.dashboard-main');
  var maxGainers = isDashboard ? 5 : 8;
  var maxLosers  = isDashboard ? 5 : 8;
  renderMoverList('gainersList', actualGainers.slice(0, maxGainers));
  renderMoverList('losersList',  actualLosers.slice(0, maxLosers));
  renderMoverList('activeList',  actualActive.slice(0, maxGainers));
  // Subscribe all mover tickers to WS for live price updates
  var moverTickers = [];
  (data.gainers || []).forEach(function(s) { if (s.ticker) moverTickers.push(s.ticker); });
  (data.losers || []).forEach(function(s) { if (s.ticker && moverTickers.indexOf(s.ticker) === -1) moverTickers.push(s.ticker); });
  (data.most_active || []).forEach(function(s) { if (s.ticker && moverTickers.indexOf(s.ticker) === -1) moverTickers.push(s.ticker); });
  if (window.DashboardWS && moverTickers.length) window.DashboardWS.addTickers(moverTickers);
}

function onMarketStatus(evt) {
  var msg  = evt.detail;
  var textEl = document.getElementById('marketStatusText');
  var dotEl  = document.querySelector('.pulse-dot');
  if (!textEl || !dotEl) return;
  if (msg && msg.status === 'open') {
    textEl.innerText = 'Markets Open';
    dotEl.classList.remove('closed');
    window._marketOpen = true;
  } else {
    textEl.innerText = 'Markets Closed';
    dotEl.classList.add('closed');
    window._marketOpen = false;
  }
}

/* ── Expiry calculation for major Indian Indices ─────────────────────────── */
function getIndexExpiryInfo(ticker) {
  var EXPIRY_DAYS = {
    'MIDCAP': 1, 'MIDCPNIFTY': 1, 'BANKEX': 1,
    'FINNIFTY': 2,
    'BANKNIFTY': 3,
    'NIFTY': 2, 'NIFTY50': 2, // Tuesday (NSE Nifty weekly expiry)
    'SENSEX': 4,              // Thursday (BSE Sensex weekly expiry)
    'SMALLCAP': 4
  };
  var t = (ticker || '').toUpperCase().replace(/\s+/g, '');
  var expDay = EXPIRY_DAYS[t];
  if (expDay == null) {
    if (t.indexOf('FIN') >= 0) expDay = 2;
    else if (t.indexOf('BANK') >= 0) expDay = 3;
    else if (t.indexOf('SENSEX') >= 0) expDay = 4;
    else if (t.indexOf('MID') >= 0) expDay = 1;
    else if (t.indexOf('NIFTY') >= 0) expDay = 2;
    else return null;
  }
  var now = new Date();
  var utc = now.getTime() + (now.getTimezoneOffset() * 60000);
  var ist = new Date(utc + (3600000 * 5.5));
  var currentDay = ist.getDay();
  var totalMins = ist.getHours() * 60 + ist.getMinutes();
  var daysToAdd = (expDay - currentDay + 7) % 7;
  if (daysToAdd === 0 && totalMins >= 930) daysToAdd = 7;
  if (daysToAdd === 0) return { text: 'Exp: Today', badgeClass: 'exp-today' };
  if (daysToAdd === 1) return { text: 'Exp: Tomorrow', badgeClass: 'exp-tomorrow' };
  var expDate = new Date(ist.getTime() + (daysToAdd * 86400000));
  var months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var dNum = expDate.getDate();
  return { text: 'Exp: ' + (dNum < 10 ? '0' : '') + dNum + ' ' + months[expDate.getMonth()], badgeClass: 'exp-normal' };
}

/* ── Update ticker strip from a prices map ───────────────────────────────── */
/* ── Update ticker strip from a prices map ───────────────────────────────── */
var TICKER_STRIP_INDICES = ['NIFTY','SENSEX','BANKNIFTY','FINNIFTY','MIDCAP','SMALLCAP'];
function updateTickerStrip(prices) {
  if (!prices || typeof prices !== 'object') return;
  var container = document.getElementById('indexTickerContainer');
  var activeChartTkr = (window._chartTicker || 'NIFTY').toUpperCase().replace(/\.(NS|BO)$/i, '');

  TICKER_STRIP_INDICES.forEach(function (ticker) {
    var d = (typeof getPriceForTicker === 'function') ? getPriceForTicker(prices, ticker) : prices[ticker];
    if (!d || !d.current) return;
    var priceEl  = document.getElementById('strip-price-' + ticker);
    var changeEl = document.getElementById('strip-change-' + ticker);
    var expEl    = document.getElementById('strip-expiry-' + ticker) || document.getElementById('strip-exp-' + ticker);
    if (!priceEl || !changeEl) {
      if (!container) return;
      var card = document.createElement('div');
      card.className = 'ticker-card';
      card.onclick = function(){ window.location.href='/overview.html?ticker='+ticker; };
      var label = ticker === 'NIFTY' ? 'NIFTY 50' : (ticker === 'BANKNIFTY' ? 'BANK NIFTY' : (ticker === 'FINNIFTY' ? 'NIFTY FIN' : ticker));
      card.innerHTML = '<h4>' + label + '</h4>' +
        '<div class="price" id="strip-price-' + ticker + '" data-sid="sp-' + ticker + '">--</div>' +
        '<div class="change text-green" id="strip-change-' + ticker + '" data-sid="sc-' + ticker + '">--</div>' +
        '<div id="strip-expiry-' + ticker + '"></div>';
      container.appendChild(card);
      priceEl  = document.getElementById('strip-price-' + ticker);
      changeEl = document.getElementById('strip-change-' + ticker);
      expEl    = document.getElementById('strip-expiry-' + ticker);
      if (!priceEl || !changeEl) return;
    }

    if (expEl) {
      var exp = (typeof _nextExpiryDate === 'function') ? _nextExpiryDate(ticker) : null;
      if (exp) {
        var _DAYS = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
        var _MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        var nowIST = new Date(Date.now() + 5.5 * 60 * 60 * 1000);
        var todayStr = nowIST.toISOString().slice(0, 10);
        var tomIST = new Date(nowIST); tomIST.setUTCDate(tomIST.getUTCDate() + 1);
        var tomorrowStr = tomIST.toISOString().slice(0, 10);
        var ds = exp.dateStr;
        var ed = exp.date;
        var dateLabel = _DAYS[ed.getUTCDay()] + ' ' + ed.getUTCDate() + ' ' + _MONTHS[ed.getUTCMonth()];
        if (ds === todayStr) {
          expEl.innerHTML = '<span class="expiry-badge expiry-today" title="Expiry: ' + dateLabel + '" style="display:inline-flex;align-items:center;background:rgba(242,54,69,0.14);color:#ff6b7a;border:1px solid rgba(242,54,69,0.28);font-size:0.62rem;padding:2px 7px;border-radius:6px;font-weight:600;letter-spacing:0.02em;margin-top:3px;cursor:default;">Expiry Today</span>';
        } else if (ds === tomorrowStr) {
          expEl.innerHTML = '<span class="expiry-badge expiry-tomorrow" title="Expiry: ' + dateLabel + '" style="display:inline-flex;align-items:center;background:rgba(245,158,11,0.14);color:#fbbf24;border:1px solid rgba(245,158,11,0.28);font-size:0.62rem;padding:2px 7px;border-radius:6px;font-weight:600;letter-spacing:0.02em;margin-top:3px;cursor:default;">Expiry Tomorrow</span>';
        } else {
          expEl.innerHTML = '<span class="expiry-badge expiry-future" style="display:inline-block;color:#83838d;font-size:0.62rem;margin-top:3px;">Expiry: ' + dateLabel + '</span>';
        }
      } else {
        var expInfo = getIndexExpiryInfo(ticker);
        if (expInfo) {
          expEl.innerHTML = '<span class="expiry-badge expiry-future" style="display:inline-block;color:#83838d;font-size:0.62rem;margin-top:3px;">' + expInfo.text + '</span>';
        }
      }
    }

    var prev = (d.prev_close != null && d.prev_close > 0) ? d.prev_close : ((d.open != null && d.open > 0) ? d.open : d.current);
    var diff = (d.change != null && !isNaN(d.change)) ? Number(d.change) : (d.current - prev);
    var pct  = (d.change_pct != null && !isNaN(d.change_pct)) ? Number(d.change_pct) : ((prev > 0) ? (diff / prev) * 100 : 0);
    var sign = diff >= 0 ? '▲ +' : '▼ ';

    window._lastTickPrices = window._lastTickPrices || {};
    var lastTick = window._lastTickPrices[ticker];
    var newText = '₹' + d.current.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

    window._lastTickPrices[ticker] = d.current;
    priceEl.innerText  = newText;
    changeEl.innerText = sign + diff.toFixed(2) + ' (' + pct.toFixed(2) + '%)';
    changeEl.className = 'change ' + (diff >= 0 ? 'text-green' : 'text-red');

    // CRITICAL: Synchronize minichart header + OHLC footer at the exact same moment if this is the active chart ticker
    if (ticker === activeChartTkr || (activeChartTkr === 'NIFTY' && (ticker === 'NIFTY' || ticker === 'NIFTY50'))) {
      updateMinichartOHLC(d);
      updateNiftyLiveCandle(ticker, d.current);
    }
  });
}
window.updateTickerStrip = updateTickerStrip;


/* 1. Theme Toggle with persistence */
function initThemeToggle() {
  var btn = document.getElementById('themeToggle');
  if (!btn) return;
  var saved = localStorage.getItem('theme');
  if (saved === 'light') {
    document.body.classList.add('light-mode');
    btn.innerText = '☀️';
  }
  btn.addEventListener('click', function () {
    var isLight = document.body.classList.toggle('light-mode');
    btn.innerText = isLight ? '☀️' : '🌙';
    localStorage.setItem('theme', isLight ? 'light' : 'dark');
  });
}

/* 2. Market Status (Local IST check with NSE holiday awareness) */
var NSE_HOLIDAYS_2025 = [
  '2025-01-26','2025-02-26','2025-03-14','2025-03-31','2025-04-10','2025-04-14',
  '2025-04-18','2025-05-01','2025-08-15','2025-08-27','2025-10-01','2025-10-02',
  '2025-10-20','2025-10-22','2025-11-05','2025-12-25'
];
// NSE 2026 official holidays (verified against NSE circular)
var NSE_HOLIDAYS_2026 = [
  '2026-01-26', // Republic Day
  '2026-02-16', // Maha Shivaratri
  '2026-03-02', // Holi
  '2026-03-20', // Ugadi / Gudi Padwa
  '2026-03-27', // Good Friday
  '2026-04-10', // Ram Navami
  '2026-04-14', // Dr. Ambedkar Jayanti
  '2026-04-15', // Mahavir Jayanti / Good Friday
  '2026-05-01', // Maharashtra Day
  '2026-05-14', // Buddha Purnima
  '2026-06-27', // Bakri Eid (Id-Ul-Adha)
  '2026-08-15', // Independence Day
  '2026-08-24', // Ganesh Chaturthi
  '2026-09-15', // Milad-Un-Nabi
  '2026-10-02', // Gandhi Jayanti / Dussehra
  '2026-10-19', // Diwali (Laxmi Pujan)
  '2026-10-20', // Diwali (Balipratipada)
  '2026-11-04', // Guru Nanak Jayanti
  '2026-12-25'  // Christmas
];
function _isNSEHoliday(istDateStr) {
  if (NSE_HOLIDAYS_2025.indexOf(istDateStr) !== -1) return true;
  if (NSE_HOLIDAYS_2026.indexOf(istDateStr) !== -1) return true;
  return false;
}
function fetchMarketStatus() {
  var textEl = document.getElementById('marketStatusText');
  var dotEl  = document.querySelector('.pulse-dot');
  if (!textEl || !dotEl) return;
  var now  = new Date();
  var ist  = new Date(now.getTime() + (3600000 * 5.5));
  var day  = ist.getUTCDay();
  var h    = ist.getUTCHours();
  var m    = ist.getUTCMinutes();
  var isWd = day >= 1 && day <= 5;
  var open = h > 9 || (h === 9 && m >= 15);
  var cls  = h < 15 || (h === 15 && m < 30);
  var dateStr = ist.getUTCFullYear() + '-' + String(ist.getUTCMonth()+1).padStart(2,'0') + '-' + String(ist.getUTCDate()).padStart(2,'0');
  var isHoliday = _isNSEHoliday(dateStr);
  if (isWd && !isHoliday && open && cls) {
    textEl.innerText = 'Markets Open';
    dotEl.classList.remove('closed');
    window._marketOpen = true;
    localStorage.setItem('market_open', 'true');
    localStorage.setItem('market_open_ts', Date.now().toString());
  } else {
    textEl.innerText = 'Markets Closed';
    dotEl.classList.add('closed');
    window._marketOpen = false;
    localStorage.setItem('market_open', 'false');
    localStorage.setItem('market_open_ts', Date.now().toString());
  }
}

/* 2b. Fetch Index Prices (populates server cache + seeds UI immediately) */
async function fetchIndexPrices() {
  // Always fetch from server first (live data). localStorage cache is used
  // only as instant seed BEFORE the REST call returns — see seedUIFromCache().
  // We skip the TTL check here so prices are always fresh on page load.
  try {
    var ct = window._chartTicker || 'NIFTY';
    var reqTickers = ['NIFTY','SENSEX','BANKNIFTY','FINNIFTY','MIDCAP','SMALLCAP'];
    if (reqTickers.indexOf(ct) === -1) reqTickers.push(ct);
    var res = await fetch('/api/live-prices', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tickers: reqTickers })
    });
    if (res.ok) {
      var data = await res.json();
      if (data) {
        // Update ticker strip
        updateTickerStrip(data);

        // Update chart header + OHLC based on selected dropdown ticker
        var chartData = getPriceForTicker(data, ct) || getPriceForTicker(data, 'NIFTY');
        if (chartData && chartData.current) {
          updateMinichartOHLC(chartData);
          var _off = window._serverClockOffset;
          var _nowMs = Date.now() + (_off != null && !isNaN(_off) ? _off : 0);
          updateNiftyLiveCandle(ct, chartData.current, _nowMs);
        }
      }

        // Broadcast as CustomEvent so all listeners (incl. onPriceUpdate) fire
        window.dispatchEvent(new CustomEvent('dashboard_price_update', { detail: { prices: data, serverTs: new Date().toISOString() } }));
        // Merge into localStorage
        try {
          var existing = JSON.parse(localStorage.getItem('llp') || '{}');
          var merged   = Object.assign({}, existing, data);
          var mergedStr = JSON.stringify(merged);
          if (mergedStr.length > 2e6) {
            merged = Object.assign({}, data);
            mergedStr = JSON.stringify(merged);
          }
          localStorage.setItem('llp',    mergedStr);
          localStorage.setItem('llp_ts', Date.now().toString());
        } catch (e) {}
      }
  } catch (e) { console.error('fetchIndexPrices err:', e); }
}

/* 3. Ticker Strip — elements are pre-rendered in home.html, just update values */
function renderTickerStrip() {
  var container = document.getElementById('indexTickerContainer');
  if (!container) return;

  // Seed from localStorage (covers case where inline script ran but data was > 10 min)
  try {
    var raw = localStorage.getItem('llp');
    if (raw) updateTickerStrip(JSON.parse(raw));
  } catch (e) {}

  // Scroll buttons
  var leftBtn  = document.getElementById('stripLeftBtn');
  var rightBtn = document.getElementById('stripRightBtn');
  if (leftBtn)  leftBtn.addEventListener('click',  function () { container.scrollBy({ left: -200, behavior: 'smooth' }); });
  if (rightBtn) rightBtn.addEventListener('click', function () { container.scrollBy({ left:  200, behavior: 'smooth' }); });
}

/* 4. Dashboard Chart (dynamic ticker) */

  // Filter out non-market hours (weekends and times outside 09:15-15:30 IST)
  function filterMarketHours(data) {
    if (!data) return [];
    return data.filter(function(p) {
      if (!p || !p.time) return false;
      var t = typeof p.time === 'number' ? new Date(p.time * 1000) : new Date(p.time);
      if (isNaN(t.getTime())) return true; // keep if we can't parse
      var day = t.getUTCDay(); // Actually, API might return UTC strings.
      // Convert to IST
      var ist = new Date(t.getTime() + 5.5 * 60 * 60 * 1000);
      var istDay = ist.getUTCDay();
      if (istDay === 0 || istDay === 6) return false; // Skip weekend
      var hours = ist.getUTCHours();
      var mins = ist.getUTCMinutes();
      var timeval = hours * 100 + mins;
      if (timeval < 915 || timeval > 1530) return false;
      return true;
    });
  }

function toTimeNum(v) {
  if (v == null) return 0;
  if (typeof v === 'number') return v;
  if (typeof v === 'string') {
    var n = Number(v);
    if (!isNaN(n)) return n;
    // Parse date-only strings (YYYY-MM-DD) as UTC to avoid local timezone offset
    if (/^\d{4}-\d{2}-\d{2}$/.test(v)) {
      var ms = new Date(v + 'T00:00:00Z').getTime();
      return isNaN(ms) ? 0 : Math.floor(ms / 1000);
    }
    var ms = new Date(v).getTime();
    return isNaN(ms) ? 0 : Math.floor(ms / 1000);
  }
  return 0;
}

// P1.2: Lightweight Charts' candlestick/bar colorer throws "Value is null"
// (or "Value is undefined") at PAINT time when a bar's open/close is not a
// finite number > 0. The initial-load path drops such bars, but the incremental
// merge paths (lazy-load, WS-reconnect gap-fill, candle/latest) did not -- one
// bad bar raised an uncaught error on the next repaint. Normalize OHLCV to
// finite numbers, or return null so the caller drops the bar.
function _sanitizeOHLCV(p) {
  if (!p || p.time == null) return null;
  var o = Number(p.open), h = Number(p.high), l = Number(p.low), c = Number(p.close);
  if (!(isFinite(o) && isFinite(h) && isFinite(l) && isFinite(c))) return null;
  if (!(o > 0 && h > 0 && l > 0 && c > 0)) return null;
  var v = Number(p.volume);
  return { open: o, high: h, low: l, close: c, volume: isFinite(v) ? v : 0 };
}

// P2.3: derive the per-tick bucket-volume delta from the CUMULATIVE day volume.
// The WS/poll `volume` field is the whole day's cumulative volume (the same feed
// the backend aggregator consumes), so a bucket's volume is the running delta
// within the bucket. Returns {delta, base}; `base` is the new cumulative baseline
// for the ticker (unchanged when the tick carries no usable volume).
function _bucketVolumeDelta(prevBase, dayVol) {
  var v = Number(dayVol);
  if (!isFinite(v) || v <= 0) return { delta: 0, base: prevBase };
  if (typeof prevBase === 'number' && v >= prevBase) return { delta: v - prevBase, base: v };
  // no baseline yet (first tick / new ticker), or a reset (new session /
  // reconnect where the cumulative counter restarted): re-baseline, no delta.
  return { delta: 0, base: v };
}

// Convert any time value (Unix seconds, business-day object, or YYYY-MM-DD string) to epoch seconds.
// Used by drawing-tool coordinate helpers so they work in both UTC and business-day chart modes.
function _bdToSec(t) {
  if (!t && t !== 0) return 0;
  if (typeof t === 'number') return t;
  if (typeof t === 'object' && t.year) return Date.UTC(t.year, t.month - 1, t.day) / 1000;
  if (typeof t === 'string') {
    if (/^\d{4}-\d{2}-\d{2}$/.test(t)) return new Date(t + 'T00:00:00Z').getTime() / 1000;
    var n = Number(t);
    return isNaN(n) ? 0 : n;
  }
  return 0;
}

// Convert an epoch-seconds value to an IST "YYYY-MM-DD" date string.
// IST = UTC+5:30. Used to format 1D candle times for Lightweight Charts business-day mode.
function _toIST1DDate(epochSec) {
  var IST_OFF_MS = 5.5 * 3600 * 1000;
  var d = new Date(epochSec * 1000 + IST_OFF_MS);
  var y = d.getUTCFullYear();
  var m = String(d.getUTCMonth() + 1).padStart(2, '0');
  var day = String(d.getUTCDate()).padStart(2, '0');
  return y + '-' + m + '-' + day;
}

// Return the next Monday-Friday date string (YYYY-MM-DD) after the given date string.
function _nextBizDay(dateStr) {
  var d = new Date(dateStr + 'T00:00:00Z');
  do { d.setUTCDate(d.getUTCDate() + 1); } while (d.getUTCDay() === 0 || d.getUTCDay() === 6);
  var y = d.getUTCFullYear();
  var m = String(d.getUTCMonth() + 1).padStart(2, '0');
  var day = String(d.getUTCDate()).padStart(2, '0');
  return y + '-' + m + '-' + day;
}

// Add arbitrary number of days to YYYY-MM-DD date string and return YYYY-MM-DD string.
function _addDays(dateStr, days) {
  var d = new Date(dateStr + 'T00:00:00Z');
  d.setUTCDate(d.getUTCDate() + days);
  var y = d.getUTCFullYear();
  var m = String(d.getUTCMonth() + 1).padStart(2, '0');
  var day = String(d.getUTCDate()).padStart(2, '0');
  return y + '-' + m + '-' + day;
}

function _processMinichartBars(data) {
  if (!data || !Array.isArray(data)) return [];
  var validBars = [];
  for (var i = 0; i < data.length; i++) {
    var p = data[i];
    if (!p) continue;
    var t = toTimeNum(p.time);
    if (t == null || isNaN(t) || t <= 0) continue;
    var o = p.open != null ? Number(p.open) : null;
    var h = p.high != null ? Number(p.high) : null;
    var l = p.low != null ? Number(p.low) : null;
    var c = p.close != null ? Number(p.close) : null;
    if (c == null || isNaN(c) || c <= 0) continue;
    if (o == null || isNaN(o) || o <= 0) o = c;
    if (h == null || isNaN(h) || h <= 0) h = Math.max(o, c);
    if (l == null || isNaN(l) || l <= 0) l = Math.min(o, c);
    validBars.push({
      time: t,
      open: o,
      high: Math.max(h, o, c),
      low: Math.min(l, o, c),
      close: c
    });
  }
  if (validBars.length === 0) return [];
  validBars.sort(function (a, b) { return a.time - b.time; });
  var uniqueBars = [];
  var seenTimes = {};
  for (var k = 0; k < validBars.length; k++) {
    var bar = validBars[k];
    if (!seenTimes[bar.time]) {
      seenTimes[bar.time] = true;
      uniqueBars.push(bar);
    } else {
      uniqueBars[uniqueBars.length - 1] = bar;
    }
  }
  return uniqueBars;
}

function loadChartData(ticker) {
  if (!window._niftyCandleSeries) return;

  var cleanT = (ticker || window._chartTicker || 'NIFTY').toUpperCase().replace(/\.(NS|BO)$/i, '');
  window._minichartActiveTicker = cleanT;
  window._minichartLoadingMore = false;
  window._minichartNoMoreHistory = false;
  window._minichartAllCandles = [];
  window._minichartEarliestTime = null;

  // Fast cache restore on startup: instant 0ms render so user sees candles immediately
  var cacheKey = 'minichart_' + cleanT;
  try {
    var rawCache = sessionStorage.getItem(cacheKey);
    if (rawCache) {
      var cachedData = JSON.parse(rawCache);
      if (Array.isArray(cachedData) && cachedData.length > 0) {
        _applyNiftyChartData(cleanT, cachedData, true);
      }
    }
  } catch (e) {}

  // Initial load: 300 candles = ~4 trading days (75 candles/day * 4 = 300)
  fetch('/api/stock-data/intraday/paginated?ticker=' + encodeURIComponent(cleanT) + '&interval=5m&limit=300')
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (data && data.data) { data = data.data; }
      if (!Array.isArray(data) || data.length === 0) return;
      try { sessionStorage.setItem(cacheKey, JSON.stringify(data)); } catch (e) {}
      _applyNiftyChartData(cleanT, data, false);
    })
    .catch(function () {});
}

function _loadOlderMinichartData() {
  if (window._minichartLoadingMore || window._minichartNoMoreHistory) return;
  var activeTicker = (window._chartTicker || 'NIFTY').toUpperCase().replace(/\.(NS|BO)$/i, '');
  if (!window._minichartEarliestTime || !window._minichartAllCandles || window._minichartAllCandles.length === 0) return;

  window._minichartLoadingMore = true;
  var beforeTs = window._minichartEarliestTime;

  fetch('/api/stock-data/intraday/paginated?ticker=' + encodeURIComponent(activeTicker) + '&interval=5m&before=' + beforeTs + '&limit=300')
    .then(function (r) { return r.json(); })
    .then(function (res) {
      var older = (res && res.data) ? res.data : (Array.isArray(res) ? res : []);
      if (!Array.isArray(older) || older.length < 5) {
        window._minichartNoMoreHistory = true;
        window._minichartLoadingMore = false;
        return;
      }

      older = filterMarketHours(older);
      var validOlder = _processMinichartBars(older);
      if (validOlder.length === 0) {
        window._minichartNoMoreHistory = true;
        window._minichartLoadingMore = false;
        return;
      }

      var currentT = (window._chartTicker || 'NIFTY').toUpperCase().replace(/\.(NS|BO)$/i, '');
      if (currentT !== activeTicker) {
        window._minichartLoadingMore = false;
        return;
      }

      var existingMap = {};
      window._minichartAllCandles.forEach(function(b) { existingMap[b.time] = true; });
      var newUniqueOlder = validOlder.filter(function(b) { return !existingMap[b.time]; });

      if (newUniqueOlder.length === 0) {
        window._minichartNoMoreHistory = true;
        window._minichartLoadingMore = false;
        return;
      }

      var merged = newUniqueOlder.concat(window._minichartAllCandles);
      merged.sort(function(a, b) { return a.time - b.time; });

      var addedCount = newUniqueOlder.length;
      window._minichartAllCandles = merged;
      window._minichartEarliestTime = merged[0].time;

      var prevRange = null;
      if (window._niftyChart && window._niftyChart.timeScale) {
        try { prevRange = window._niftyChart.timeScale().getVisibleLogicalRange(); } catch(e) {}
      }

      var areaData = merged.map(function(p) { return { time: p.time, value: p.close }; });
      var candleData = merged.map(function(p) {
        return { time: p.time, open: p.open, high: p.high, low: p.low, close: p.close };
      });

      if (window._niftyAreaSeries) {
        try { window._niftyAreaSeries.setData(areaData); } catch(e) {}
      }
      if (window._niftyCandleSeries) {
        try { window._niftyCandleSeries.setData(candleData); } catch(e) {}
      }
      var sma = calcSMA(areaData, 5);
      if (sma.length > 0 && window._niftySMASeries) {
        try { window._niftySMASeries.setData(sma); } catch(e) {}
      }

      // Smooth scroll preservation: offset logical range by newly prepended bars count
      if (prevRange && window._niftyChart && window._niftyChart.timeScale) {
        try {
          window._niftyChart.timeScale().setVisibleLogicalRange({
            from: prevRange.from + addedCount,
            to: prevRange.to + addedCount
          });
        } catch(e) {}
      }

      window._minichartLoadingMore = false;
    })
    .catch(function () {
      window._minichartLoadingMore = false;
    });
}

function _applyNiftyChartData(ticker, data, isCached) {
  if (!window._niftyCandleSeries) return;
  var activeTicker = (window._chartTicker || 'NIFTY').toUpperCase().replace(/\.(NS|BO)$/i, '');
  var cleanT = (ticker || '').toUpperCase().replace(/\.(NS|BO)$/i, '');
  if (cleanT !== activeTicker) return;

  data = filterMarketHours(data);
  if (!data || data.length === 0) return;

  var uniqueBars = _processMinichartBars(data);
  if (uniqueBars.length === 0) return;

  window._minichartAllCandles = uniqueBars;
  window._minichartEarliestTime = uniqueBars[0].time;

  var areaData = uniqueBars.map(function (p) {
    return { time: p.time, value: p.close };
  });
  if (window._niftyAreaSeries) {
    try {
      window._niftyAreaSeries.setData(areaData);
    } catch(e) {
      console.warn('[Dashboard] niftyAreaSeries setData error:', e);
    }
  }

  var candleData = uniqueBars.map(function (p) {
    return { time: p.time, open: p.open, high: p.high, low: p.low, close: p.close };
  });
  if (window._niftyCandleSeries) {
    try {
      window._niftyCandleSeries.setData(candleData);
    } catch(e) {
      console.warn('[Dashboard] niftyCandleSeries setData error:', e);
    }
    if (candleData.length > 0) {
      var lastBar = candleData[candleData.length - 1];
      window._lastHistoricalCandle = lastBar;

      // Synchronize minichart OHLC to accurately reflect the latest session displayed
      var IST_OFFSET_SEC = 19800; // UTC+5:30
      var getDayStr = function(tSec) {
        var d = new Date((tSec + IST_OFFSET_SEC) * 1000);
        return d.getUTCFullYear() + '-' + String(d.getUTCMonth() + 1).padStart(2, '0') + '-' + String(d.getUTCDate()).padStart(2, '0');
      };
      var latestDay = getDayStr(lastBar.time);
      var todayBars = candleData.filter(function(c) { return getDayStr(c.time) === latestDay; });
      var prevBars  = candleData.filter(function(c) { return getDayStr(c.time) < latestDay; });

      if (todayBars.length > 0) {
        var dayOpen = todayBars[0].open;
        var dayHigh = Math.max.apply(null, todayBars.map(function(c) { return c.high; }));
        var dayLow  = Math.min.apply(null, todayBars.map(function(c) { return c.low; }));
        var dayClose = todayBars[todayBars.length - 1].close;
        var prevClose = prevBars.length > 0 ? prevBars[prevBars.length - 1].close : null;

        var livePrice = null;
        if (window._lastTickPrices && window._lastTickPrices[cleanT] > 0) {
          livePrice = window._lastTickPrices[cleanT];
        } else {
          var stripEl = document.getElementById('strip-price-' + cleanT);
          if (stripEl && stripEl.innerText) {
            var parsed = parseFloat(stripEl.innerText.replace(/[^\d.]/g, ''));
            if (!isNaN(parsed) && parsed > 0) livePrice = parsed;
          }
        }

        var authoritativePrice = null;
        try {
          var raw = localStorage.getItem('llp');
          if (raw) {
            var cached = JSON.parse(raw);
            var cachedT = (typeof getPriceForTicker === 'function') ? getPriceForTicker(cached, cleanT) : cached[cleanT];
            if (cachedT) {
              if (cachedT.current > 0) authoritativePrice = cachedT.current;
              if (cachedT.prev_close > 0) prevClose = cachedT.prev_close;
              if (cachedT.open > 0) dayOpen = cachedT.open;
              if (cachedT.high > 0) dayHigh = cachedT.high;
              if (cachedT.low > 0) dayLow = cachedT.low;
            }
          }
        } catch (e) {}

        var currentPriceToUse = (livePrice != null && livePrice > 0) ? livePrice
          : ((authoritativePrice != null && authoritativePrice > 0) ? authoritativePrice : dayClose);

        updateMinichartOHLC({
          current: currentPriceToUse,
          open: dayOpen,
          high: dayHigh,
          low: dayLow,
          prev_close: prevClose
        });

        // Only apply live forming candle if price is consistent with the ticker range
        var livePriceToApply = (window._pendingMinichartPrice != null && window._pendingMinichartPrice > 0)
          ? window._pendingMinichartPrice
          : currentPriceToUse;
        if (livePriceToApply && livePriceToApply > 0 && Math.abs(livePriceToApply - lastBar.close) / lastBar.close < 0.35) {
          updateNiftyLiveCandle(cleanT, livePriceToApply);
        }
      }
    }
  }

  var sma = calcSMA(areaData, 5);
  if (sma.length > 0 && window._niftySMASeries) {
    try {
      window._niftySMASeries.setData(sma);
    } catch(e) {
      console.warn('[Dashboard] niftySMASeries setData error:', e);
    }
  }

  // Auto-scale price scale and fit time scale cleanly
  if (window._niftyChart) {
    try {
      window._niftyChart.priceScale('right').applyOptions({ autoScale: true });
      if (window._niftyChart.timeScale) {
        var totalBars = candleData.length;
        if (totalBars > 0) {
          var numVisible = Math.min(totalBars, 80);
          window._niftyChart.timeScale().setVisibleLogicalRange({
            from: Math.max(0, totalBars - numVisible),
            to: totalBars + 20
          });
        } else {
          window._niftyChart.timeScale().fitContent();
        }
      }
    } catch(e) {
      try { window._niftyChart.timeScale().fitContent(); } catch(e2) {}
    }
  }
}
function _resolveBarEpochSec(time, range) {
  if (time == null) return null;
  range = range || window.activeRange || 'ALL';
  var IST_MS = 5.5 * 3600 * 1000;
  
  if (typeof time === 'object' && time !== null && time.year) {
    return Date.UTC(time.year, time.month - 1, time.day) / 1000;
  }
  if (typeof time === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(time)) {
    return new Date(time + 'T00:00:00Z').getTime() / 1000;
  }
  
  var _num = Number(time);
  if (isNaN(_num)) return null;
  
  if (window._intradayBarTimes && window._intradayBarSecs && window._intradayBarTimes.length > 0) {
    var barIdx = Math.round(_num / window._intradayBarSecs);
    if (barIdx >= 0 && barIdx < window._intradayBarTimes.length) {
      return window._intradayBarTimes[barIdx];
    }
    // Infinite future extrapolation (arbitrary distance into future whitespace)
    if (barIdx >= window._intradayBarTimes.length) {
      var kBars = barIdx - (window._intradayBarTimes.length - 1);
      var lastEpoch = window._intradayBarTimes[window._intradayBarTimes.length - 1];
      var isIntraday = ['1m','3m','5m','10m','15m','30m','1h','2h','4h'].indexOf(range) !== -1;

      if (isIntraday) {
        var barMin = window._intradayBarSecs / 60;
        var lastD = new Date(lastEpoch * 1000 + IST_MS);
        var lastMin = lastD.getUTCHours() * 60 + lastD.getUTCMinutes();
        var minRemainingToday = Math.max(0, (15 * 60 + 25) - lastMin);
        var barsRemainingToday = Math.floor(minRemainingToday / barMin);

        if (kBars <= barsRemainingToday) {
          var targetMin = lastMin + kBars * barMin;
          lastD.setUTCHours(Math.floor(targetMin / 60), targetMin % 60, 0, 0);
          return (lastD.getTime() - IST_MS) / 1000;
        }

        var kLeft = kBars - barsRemainingToday; // >= 1
        var barsPerDay = Math.max(1, Math.floor(375 / barMin)); // 75 for 5m, 25 for 15m, 12 for 30m
        var fullTradingDays = Math.floor((kLeft - 1) / barsPerDay);
        var barInDay = (kLeft - 1) % barsPerDay;

        var totalTradingDays = fullTradingDays + 1;
        var weeks = Math.floor(totalTradingDays / 5);
        var calDays = weeks * 7;
        var remDays = totalTradingDays % 5;
        
        var curDayOfWeek = (lastD.getUTCDay() + calDays) % 7;
        for (var i = 0; i < remDays; i++) {
          calDays++;
          curDayOfWeek = (curDayOfWeek + 1) % 7;
          if (curDayOfWeek === 6) { calDays += 2; curDayOfWeek = 1; } // Saturday -> Monday
          else if (curDayOfWeek === 0) { calDays += 1; curDayOfWeek = 1; }
        }

        lastD.setUTCDate(lastD.getUTCDate() + calDays);
        var targetMinSession = 9 * 60 + 15 + barInDay * barMin;
        lastD.setUTCHours(Math.floor(targetMinSession / 60), targetMinSession % 60, 0, 0);
        return (lastD.getTime() - IST_MS) / 1000;
      }

      // Non-intraday future extrapolation
      var lastDNon = new Date(lastEpoch * 1000 + IST_MS);
      if (range === '1M') {
        lastDNon.setUTCMonth(lastDNon.getUTCMonth() + kBars);
      } else if (range === '1W') {
        lastDNon.setUTCDate(lastDNon.getUTCDate() + kBars * 7);
      } else {
        var wks = Math.floor(kBars / 5);
        var cDays = wks * 7;
        var rDays = kBars % 5;
        var cDay = (lastDNon.getUTCDay() + cDays) % 7;
        for (var j = 0; j < rDays; j++) {
          cDays++;
          cDay = (cDay + 1) % 7;
          if (cDay === 6) { cDays += 2; cDay = 1; }
          else if (cDay === 0) { cDays += 1; cDay = 1; }
        }
        lastDNon.setUTCDate(lastDNon.getUTCDate() + cDays);
      }
      return (lastDNon.getTime() - IST_MS) / 1000;
    }
    // Past extrapolation (before first bar)
    if (barIdx < 0) {
      var firstEpoch = window._intradayBarTimes[0];
      return firstEpoch + barIdx * window._intradayBarSecs;
    }
  }
  
  return _num;
}

function formatIST(time, isTimeOnly) {
    if (time == null) return '';
    var epochSec = _resolveBarEpochSec(time);
    if (!epochSec || isNaN(epochSec)) return '';
    var IST_OFFSET_MS = 5.5 * 60 * 60 * 1000;
    var d = new Date(epochSec * 1000 + IST_OFFSET_MS);
    var h = d.getUTCHours().toString().padStart(2, '0');
    var m = d.getUTCMinutes().toString().padStart(2, '0');
    var day = d.getUTCDate().toString().padStart(2, '0');
    var month = (d.getUTCMonth() + 1).toString().padStart(2, '0');
    var year = d.getUTCFullYear();
    
    var isDateOnly = (typeof time === 'object' && time !== null && time.year)
                  || (typeof time === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(time));
    if (isDateOnly) return day + '/' + month + '/' + year;
    if (isTimeOnly) return h + ':' + m;
    return day + '/' + month + '/' + year + ' ' + h + ':' + m;
}

function initNiftyChart() {
  refreshMarketStatus();
  var container = document.getElementById('niftyChart');
  if (!container) return;
  if (window._niftyChart) {
    try { window._niftyChart.remove(); } catch(e) {}
    window._niftyChart = null;
  }
  container.innerHTML = '';
  window._niftyChartInited = true;
  var h = Math.max(container.clientHeight || 280, 200);
  var w = container.clientWidth || 400;

  // Fix: Translate normal vertical mouse wheel into horizontal timeline panning for Nifty chart
  container.addEventListener('wheel', function(e) {
    if (!e.shiftKey && !e.ctrlKey) {
      e.preventDefault();
      e.stopPropagation();
      const isHorizontal = Math.abs(e.deltaX) > Math.abs(e.deltaY);
      var forged = new WheelEvent('wheel', {
        bubbles: true, cancelable: true,
        clientX: e.clientX, clientY: e.clientY,
        deltaX: e.deltaX, deltaY: e.deltaY, deltaZ: e.deltaZ, deltaMode: e.deltaMode,
        ctrlKey: !isHorizontal,
        shiftKey: isHorizontal,
        altKey: e.altKey, metaKey: e.metaKey
      });
      e.target.dispatchEvent(forged);
    }
  }, { capture: true, passive: false });

  window._niftyChart = LightweightCharts.createChart(container, {
    width: w,
    height: h,
    layout: {
      background: { type: 'solid', color: 'transparent' },
      textColor: '#d1d4dc',
      fontSize: 11,
      fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
    },
    grid: {
      vertLines: { color: 'rgba(255, 255, 255, 0.03)' },
      horzLines: { color: 'rgba(255, 255, 255, 0.03)' }
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: {
        color: '#9598A1',
        width: 1,
        style: LightweightCharts.LineStyle.LargeDashed,
        visible: true,
        labelVisible: true,
      },
      horzLine: {
        color: '#9598A1',
        width: 1,
        style: LightweightCharts.LineStyle.LargeDashed,
        visible: true,
        labelVisible: true,
      }
    },
    rightPriceScale: {
      visible: true,
      borderColor: 'rgba(197, 203, 206, 0.8)',
      autoScale: true,
      alignLabels: true,
      mode: 0,
      scaleMargins: { top: 0.22, bottom: 0.22 },
      entireTextOnly: false,
      ticksVisible: true
    },
    localization: {
      timeFormatter: function(time) { return formatIST(time, false); }
    },
    timeScale: {
      visible: true,
      borderColor: 'rgba(197, 203, 206, 0.8)',
      timeVisible: true,
      secondsVisible: false,
      rightOffset: 25,
      barSpacing: 6,
      minBarSpacing: 2,
      fixLeftEdge: false,
      fixRightEdge: false,
      tickMarkFormatter: function (time, markType) {
        if (window._miniChartTick) return window._miniChartTick(time, markType);
        if (markType === LightweightCharts.TickMarkType.Time) return formatIST(time, true);
        var epochSec = time;
        if (typeof time === 'object' && time.year) {
          epochSec = Date.UTC(time.year, time.month - 1, time.day) / 1000;
        }
        if (isNaN(epochSec)) return '';
        var IST_OFFSET_MS = 5.5 * 60 * 60 * 1000;
        var d = new Date(epochSec * 1000 + IST_OFFSET_MS);
        return d.getUTCDate() + '/' + (d.getUTCMonth() + 1);
      } 
    },
    handleScroll: true,
    handleScale: true,
  });
  // Shared LEVERAGE axis formatter for the mini chart (raw 5m epoch data).
  if (window.LeverageAxis) {
    window.LeverageAxis.register(window._niftyChart, {
      getRange: function () { return '5m'; },
      getBarTimes: function () { return null; },
      getBarSecs: function () { return null; },
      resolveEpoch: function (t) { return window.LeverageAxis.toEpoch(t); }
    });
    window._miniChartTick = function (time, markType) {
      return window.LeverageAxis.tick(window._niftyChart, time, markType, '5m');
    };
  } else {
    window._miniChartTick = null;
  }
  window._niftyAreaSeries = window._niftyChart.addAreaSeries({
    lineColor: '#089981',
    topColor: 'rgba(8,153,129,0.25)',
    bottomColor: 'rgba(8,153,129,0.02)',
    lineWidth: 2,
    priceFormat: { type: 'price', precision: 2, minMove: 0.05 }
  });
  window._niftyCandleSeries = window._niftyChart.addCandlestickSeries({
    upColor: '#089981',
    downColor: '#f23645',
    borderVisible: false,
    wickUpColor: '#089981',
    wickDownColor: '#f23645',
    wickVisible: true,
    thinBars: false,
    priceFormat: { type: 'price', precision: 2, minMove: 0.05 }
  });
  window._niftyCandleSeries.applyOptions({ visible: false });
  window._niftyIntervalSec = 300; // 5-minute candles (300 seconds)
  window._niftySMASeries = window._niftyChart.addLineSeries({
    color: '#f5a623',
    lineWidth: 1.5,
    priceLineVisible: false,
    priceFormat: { type: 'price', precision: 2, minMove: 0.05 }
  });
  window._niftySMASeries.applyOptions({ visible: false });

  // Update header and footer OHLC on crosshair hover, matching bigChart behavior
  window._niftyChart.subscribeCrosshairMove(function (param) {
    if (!param.time || !param.point || param.point.x < 0 || param.point.y < 0) {
      if (window._lastMinichartOHLC) {
        updateMinichartOHLC(window._lastMinichartOHLC);
      }
      return;
    }
    var cd = null;
    if (window._niftyCandleSeries && param.seriesData && param.seriesData.get(window._niftyCandleSeries)) {
      cd = param.seriesData.get(window._niftyCandleSeries);
    } else if (window._niftyAreaSeries && param.seriesData && param.seriesData.get(window._niftyAreaSeries)) {
      cd = param.seriesData.get(window._niftyAreaSeries);
    }
    if (cd) {
      var pEl = document.getElementById('niftyPrice');
      var oEl = document.getElementById('niftyO');
      var hEl = document.getElementById('niftyH');
      var lEl = document.getElementById('niftyL');
      var cEl2 = document.getElementById('niftyC');
      var curVal = cd.close != null ? cd.close : cd.value;
      if (pEl && curVal != null) {
        pEl.innerText = Number(curVal).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      }
      if (oEl && cd.open != null) oEl.innerText = Number(cd.open).toFixed(2);
      if (hEl && cd.high != null) hEl.innerText = Number(cd.high).toFixed(2);
      if (lEl && cd.low != null)  lEl.innerText = Number(cd.low).toFixed(2);
      if (cEl2 && cd.close != null) cEl2.innerText = Number(cd.close).toFixed(2);
    }
  });

  if (window.ResizeObserver) {
    window._niftyResizeObserver = new ResizeObserver(function () {
      if (!window._niftyChart) return;
      var cw = container.clientWidth || 400;
      var ch = Math.max(container.clientHeight || 300, 200);
      window._niftyChart.applyOptions({ width: cw, height: ch });
    });
    window._niftyResizeObserver.observe(container);
  }

  // Subscribe to visible range changes for lazy loading older minichart candles when scrolling left
  if (window._niftyChart && window._niftyChart.timeScale) {
    window._niftyChart.timeScale().subscribeVisibleLogicalRangeChange(function (newRange) {
      if (!newRange) return;
      if (newRange.from <= 10) {
        _loadOlderMinichartData();
      }
    });
  }

  // Custom Ticker Dropdown Controller
  var customDd = document.getElementById('chartTickerDropdown');
  var customBtn = document.getElementById('chartTickerBtn');
  var customLabel = document.getElementById('chartTickerLabel');
  var customMenu = document.getElementById('chartTickerMenu');
  var sel = document.getElementById('chartTickerSelect');

  if (customBtn && customDd) {
    customBtn.onclick = function(e) {
      e.stopPropagation();
      customDd.classList.toggle('open');
      customBtn.setAttribute('aria-expanded', customDd.classList.contains('open') ? 'true' : 'false');
    };

    if (customMenu) {
      customMenu.onclick = function(e) {
        var item = e.target.closest('.custom-dropdown-item');
        if (!item) return;
        var val = item.getAttribute('data-value');
        if (!val) return;

        if (customLabel) customLabel.textContent = item.textContent.trim();
        customMenu.querySelectorAll('.custom-dropdown-item').forEach(function(el) {
          el.classList.toggle('active', el === item);
        });

        customDd.classList.remove('open');
        customBtn.setAttribute('aria-expanded', 'false');

        if (sel) {
          sel.value = val;
          sel.dispatchEvent(new Event('change'));
        }
      };
    }

    document.addEventListener('click', function(e) {
      if (customDd && !customDd.contains(e.target)) {
        customDd.classList.remove('open');
        if (customBtn) customBtn.setAttribute('aria-expanded', 'false');
      }
    });
  }

  // Ticker selector
  if (sel) {
    window._chartTicker = sel.value;
    sel.addEventListener('change', function () {
      var newTicker = sel.value;
      window._chartTicker = newTicker;
      if (customLabel) {
        var opt = sel.options[sel.selectedIndex];
        if (opt) customLabel.textContent = opt.textContent;
      }
      if (customMenu) {
        customMenu.querySelectorAll('.custom-dropdown-item').forEach(function(el) {
          el.classList.toggle('active', el.getAttribute('data-value') === newTicker);
        });
      }
      window._formingNiftyCandle = null;
      window._lastHistoricalCandle = null;
      window._lastMainPrice = null;
      window._lastMinichartOHLC = null;
      window._pendingMinichartPrice = null;
      window._lastNiftyTime = null;

      // Clear series data immediately so previous ticker data (24k) does not linger on new ticker (72k)
      if (window._niftyAreaSeries) window._niftyAreaSeries.setData([]);
      if (window._niftyCandleSeries) window._niftyCandleSeries.setData([]);
      if (window._niftySMASeries) window._niftySMASeries.setData([]);

      // Reset price scale
      if (window._niftyChart) {
        try {
          window._niftyChart.priceScale('right').applyOptions({ autoScale: true });
        } catch (e) {}
      }

      // 1. Immediately update header from cache for snappy UI
      try {
        var raw = localStorage.getItem('llp');
        if (raw) {
          var cached = JSON.parse(raw);
          var ct = window._chartTicker;
          var d = (typeof getPriceForTicker === 'function') ? getPriceForTicker(cached, ct) : cached[ct];
          if (d && d.current) {
            updateMinichartOHLC(d);
          }
        }
      } catch (e) {}

      // 2. Fetch fresh index prices immediately
      if (typeof fetchIndexPrices === 'function') fetchIndexPrices();

      // 3. Load full chart data and sync OHLC from candles
      loadChartData(window._chartTicker);

      // 4. Also subscribe via WS
      if (window.DashboardWS) window.DashboardWS.addTickers([window._chartTicker]);
    });
  }

  loadChartData(window._chartTicker || 'NIFTY');
  // Subscribe
  if (window.DashboardWS && window._chartTicker) window.DashboardWS.addTickers([window._chartTicker]);
}
var bigChart = null;
var activeRange = 'ALL';
var bigCandleSeries = null;
window._marketOpen  = false;

function updateMarketStatusUI() {
  var el = document.getElementById('ws-status');
  if (!el) return;
  if (!window._marketOpen) {
    el.textContent = '● Closed';
    el.style.color = '#a1a1aa';
    return;
  }
  // FE-06: market being open doesn't mean the feed is actually live -- a
  // silent WS drop (connection stuck at readyState OPEN with no data
  // flowing) used to still show "Live" with no indication anything was
  // wrong. Reflect the real feed state instead of assuming it from hours.
  var wsHealthy = !window.DashboardWS || window.DashboardWS.isHealthy();
  if (wsHealthy) {
    el.textContent = '● Live';
    el.style.color = '#089981';
  } else {
    el.textContent = '● Reconnecting';
    el.style.color = '#f0a020';
    if (!window._wsWasReconnecting) {
      el.classList.remove('ws-flash-anim');
      void el.offsetWidth;
      el.classList.add('ws-flash-anim');
    }
    window._wsWasReconnecting = true;
  }
  if (wsHealthy) window._wsWasReconnecting = false;
}

async function refreshMarketStatus() {
  try {
    var mo = localStorage.getItem('market_open');
    var moTs = parseInt(localStorage.getItem('market_open_ts') || '0', 10);
    if (mo !== null && (Date.now() - moTs < 600000)) {
      window._marketOpen = mo === 'true';
      updateMarketStatusUI();
      return;
    }
  } catch(e) {}
  // FE-06: always re-evaluate the badge here, even when isConnected() is
  // true -- readyState alone can't tell a silently-dead connection apart
  // from a live one, so skipping the update in that case would leave a
  // stale "Live" badge showing even after the feed actually went quiet.
  updateMarketStatusUI();
}

/* ─── URL Param helpers ─── */
function _updateURLParam(key, value) {
  try {
    var url = new URL(window.location);
    url.searchParams.set(key, value);
    history.replaceState(null, '', url);
  } catch(e) {}
}
var VALID_RANGES = ['1m','5m','15m','30m','1h','1D','1W','1M','3M','6M','1Y','ALL'];

// F&O weekly expiry day-of-week for major NSE/BSE indices (0=Sun,1=Mon,2=Tue,3=Wed,4=Thu,5=Fri)
var _FNO_EXPIRY_DAY = {
  'NIFTY': 2, 'NIFTY50': 2, // Tuesday (NSE Nifty weekly expiry)
  'BANKNIFTY': 3,
  'FINNIFTY': 2,
  'MIDCPNIFTY': 1, 'MIDCAP': 1,
  'SENSEX': 4, // Thursday (BSE Sensex weekly expiry)
  'BANKEX': 1,
  'SMALLCAP': 4,
};

// NSE trading holidays — expiry shifts to previous trading day when coinciding
var _NSE_HOLIDAYS_SET = (function () {
  var h = [
    '2025-01-26','2025-02-19','2025-03-17','2025-03-31','2025-04-10',
    '2025-04-14','2025-04-18','2025-05-01','2025-08-15','2025-08-27',
    '2025-10-02','2025-10-21','2025-10-24','2025-11-05','2025-12-25',
    '2026-01-26','2026-02-19','2026-03-03','2026-04-02','2026-04-03',
    '2026-04-14','2026-05-01','2026-08-15','2026-10-02','2026-12-25',
  ];
  var s = {};
  for (var i = 0; i < h.length; i++) s[h[i]] = true;
  return s;
}());

// Returns the next expiry date for the given index ticker (>= today in IST).
// Handles holiday shifts: if scheduled day is a holiday, steps back to the
// previous trading day; if that pushed it before today, moves to next week.
function _nextExpiryDate(ticker) {
  var expDay = _FNO_EXPIRY_DAY[ticker.toUpperCase()];
  if (expDay === undefined) return null;
  var nowIST = new Date(Date.now() + 5.5 * 60 * 60 * 1000);
  var todayStr = nowIST.toISOString().slice(0, 10);
  var totalMins = nowIST.getUTCHours() * 60 + nowIST.getUTCMinutes();
  for (var week = 0; week <= 1; week++) {
    var d = new Date(nowIST);
    var daysUntil = (expDay - d.getUTCDay() + 7) % 7 + week * 7;
    if (week === 0 && daysUntil === 0 && totalMins >= 930) continue;
    d.setUTCDate(d.getUTCDate() + daysUntil);
    var dStr = d.toISOString().slice(0, 10);
    var steps = 0;
    while (steps < 5 && (_NSE_HOLIDAYS_SET[dStr] || d.getUTCDay() === 0 || d.getUTCDay() === 6)) {
      d.setUTCDate(d.getUTCDate() - 1);
      dStr = d.toISOString().slice(0, 10);
      steps++;
    }
    if (dStr >= todayStr) return { date: d, dateStr: dStr };
  }
  return null;
}

// Renders expiry badges on the index strip cards (home page).
// Shows "Expiry Today" (red), "Expiry Tomorrow" (amber), or the next date (subtle).
function _initExpiryBadges() {
  var _DAYS = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
  var _MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  var nowIST = new Date(Date.now() + 5.5 * 60 * 60 * 1000);
  var todayStr = nowIST.toISOString().slice(0, 10);
  var tomIST = new Date(nowIST); tomIST.setUTCDate(tomIST.getUTCDate() + 1);
  var tomorrowStr = tomIST.toISOString().slice(0, 10);

  ['NIFTY','SENSEX','BANKNIFTY','FINNIFTY','MIDCPNIFTY','MIDCAP','SMALLCAP'].forEach(function (t) {
    var el = document.getElementById('strip-expiry-' + t) || document.getElementById('strip-exp-' + t);
    if (!el) return;
    var exp = _nextExpiryDate(t);
    if (!exp) return;
    var ds = exp.dateStr;
    var ed = exp.date;
    var dateLabel = _DAYS[ed.getUTCDay()] + ' ' + ed.getUTCDate() + ' ' + _MONTHS[ed.getUTCMonth()];
    var badge;
    if (ds === todayStr) {
      badge = '<span class="expiry-badge expiry-today" title="Expiry: ' + dateLabel + '" style="display:inline-flex;align-items:center;background:rgba(242,54,69,0.14);color:#ff6b7a;border:1px solid rgba(242,54,69,0.28);font-size:0.62rem;padding:2px 7px;border-radius:6px;font-weight:600;letter-spacing:0.02em;margin-top:3px;cursor:default;">Expiry Today</span>';
    } else if (ds === tomorrowStr) {
      badge = '<span class="expiry-badge expiry-tomorrow" title="Expiry: ' + dateLabel + '" style="display:inline-flex;align-items:center;background:rgba(245,158,11,0.14);color:#fbbf24;border:1px solid rgba(245,158,11,0.28);font-size:0.62rem;padding:2px 7px;border-radius:6px;font-weight:600;letter-spacing:0.02em;margin-top:3px;cursor:default;">Expiry Tomorrow</span>';
    } else {
      badge = '<span class="expiry-badge expiry-future" style="display:inline-block;color:#83838d;font-size:0.62rem;margin-top:3px;">Expiry: ' + dateLabel + '</span>';
    }
    el.innerHTML = badge;
  });
}

// Run on pages that have the index strip (home page)
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _initExpiryBadges);
} else {
  _initExpiryBadges();
}

async function initBigChart() {
  var params = new URLSearchParams(window.location.search);
  var rawTicker = params.get('ticker') || 'RELIANCE';
  var ticker = rawTicker.toUpperCase().replace(/\.(NS|BO)$/i, '');
  var initialRange = params.get('range') || 'ALL';
  if (VALID_RANGES.indexOf(initialRange) === -1) initialRange = 'ALL';

  window.currentTicker = ticker;
  window._chartTicker = ticker;
  window.lastLivePrice = null;
  window._lastHistoricalCandle = null;
  window._formingCandles = {};
  var isIdx = ['NIFTY','SENSEX','BANKNIFTY','FINNIFTY','MIDCAP','SMALLCAP','BSE500','NIFTYMIDCAP100','NIFTYSMLCAP100'].indexOf(ticker) !== -1;
  var tradeBtn = document.querySelector('.trade-btn');
  if (tradeBtn) {
    tradeBtn.style.display = isIdx ? 'none' : 'block';
  }
  if (window.toolManager) window.toolManager.loadDrawings();
  
  var titleEl = document.getElementById('ticker-name');
  if (titleEl) titleEl.innerText = ticker;
  var chartTicker = document.getElementById('chart-ticker-name');
  if (chartTicker) chartTicker.innerText = ticker;
    var badgeEl = document.getElementById('ticker-badge');
  if (badgeEl) badgeEl.innerText = ticker;

  var chartContainer = document.getElementById('chart');

  // Fix: Translate normal vertical mouse wheel into horizontal timeline panning
  if (chartContainer) {
    chartContainer.addEventListener('wheel', function(e) {
      if (!e.shiftKey && !e.ctrlKey) {
        e.preventDefault();
        e.stopPropagation();
        const isHorizontal = Math.abs(e.deltaX) > Math.abs(e.deltaY);
        var forged = new WheelEvent('wheel', {
          bubbles: true, cancelable: true,
          clientX: e.clientX, clientY: e.clientY,
          deltaX: e.deltaX, deltaY: e.deltaY, deltaZ: e.deltaZ, deltaMode: e.deltaMode,
          ctrlKey: !isHorizontal,
          shiftKey: isHorizontal,
          altKey: e.altKey, metaKey: e.metaKey
        });
        e.target.dispatchEvent(forged);
      }
    }, { capture: true, passive: false });
  }

  window._rightOffset = 40;
  // Read height from the CSS-controlled #chart-container (clamp-based, always visible)
  var chartParent = document.getElementById('chart-container');
  var chartH = Math.max(420, chartParent ? chartParent.offsetHeight : 500);
  var chartW = Math.max(200, chartParent ? chartParent.clientWidth : 800);

  var bigChart = window.bigChart = LightweightCharts.createChart(chartContainer, {
    width: chartW,
    height: chartH,
    layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#d1d4dc' },
    grid: { 
      vertLines: { visible: false }, 
      horzLines: { visible: false } 
    },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal, vertLine: { labelVisible: true }, horzLine: { labelVisible: true } },
    rightPriceScale: { 
      autoScale: true,
      borderColor: 'rgba(197,203,206,0.8)', 
      scaleMargins: { top: 0.05, bottom: 0.1 },
      minimumWidth: (window.innerWidth <= 480 ? 54 : (window.innerWidth <= 768 ? 58 : 68))
    },
    localization: {
      timeFormatter: function(time) {
        var range = window.activeRange || '';
        var MON = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        var IST = 5.5 * 3600 * 1000;
        var epochSec = _resolveBarEpochSec(time, range);
        if (!epochSec || isNaN(epochSec)) return '';
        var d = new Date(epochSec * 1000 + IST);
        var dd = String(d.getUTCDate()).padStart(2,'0');
        var mon = MON[d.getUTCMonth()];
        var yr = d.getUTCFullYear();
        var hh = String(d.getUTCHours()).padStart(2,'0');
        var mm = String(d.getUTCMinutes()).padStart(2,'0');
        // Crosshair label: show finest sensible detail for each timeframe
        if (range === '1M') return mon + ' ' + yr;
        if (range === '1D' || range === '1W' || range === '3M' || range === '6M' || range === '1Y' || range === 'ALL') return dd + ' ' + mon + ' ' + yr;
        return dd + ' ' + mon + ' ' + yr + ' ' + hh + ':' + mm;
      }
    },
    handleScroll: true,
    handleScale: true,
    timeScale: {
      borderColor: 'rgba(197,203,206,0.8)',
      timeVisible: true,
      rightOffset: 12,
      fixLeftEdge: false,
      lockVisibleTimeRangeOnResize: true,
      uniformDistribution: false,
      // Minimum bar spacing: prevents zoom-out so extreme that wicks become
      // sub-pixel and invisible. 0.5px per bar = ~2000 candles visible at once.
      minBarSpacing: 0.5,
      tickMarkFormatter: function(time, markType) {
        // Delegates to the shared LEVERAGE axis formatter (axis-format.js).
        // The legacy body below is retained only as a fallback if that script
        // is unavailable, so behaviour degrades gracefully.
        if (window._bigChartTick) return window._bigChartTick(time, markType);
        if (time == null) return '';
        var range = window.activeRange || 'ALL';
        var intraday = ['1m','3m','5m','10m','15m','30m','1h','2h','4h'].indexOf(range) !== -1;

        // ── Resolve time → epochSec ───────────────────────────────────────────────
        var epochSec = _resolveBarEpochSec(time, range);
        if (!epochSec || isNaN(epochSec)) return '';
        var barIndex = null;
        if (window._intradayBarTimes && window._intradayBarSecs) {
          barIndex = Math.round(Number(time) / window._intradayBarSecs);
        }
        var d = new Date(epochSec * 1000 + 5.5 * 3600 * 1000);
        if (isNaN(d.getTime())) return '';

        var M = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        var _yr  = d.getUTCFullYear();
        var _mo  = M[d.getUTCMonth()];
        var _moi = d.getUTCMonth(); // 0-11
        var _dd  = String(d.getUTCDate()).padStart(2, '0');
        var hh   = String(d.getUTCHours()).padStart(2, '0');
        var mm   = String(d.getUTCMinutes()).padStart(2, '0');

        var timeStr = hh + ':' + mm;
        var dateStr = _dd + ' ' + _mo;

        // Query visible logical range to measure current zoom density
        var visibleBars = 75;
        if (window.bigChart) {
          try {
            var ivr = window.bigChart.timeScale().getVisibleLogicalRange();
            if (ivr && ivr.to != null && ivr.from != null) {
              visibleBars = Math.max(1, Math.round(ivr.to - ivr.from));
            }
          } catch(e) {}
        }

        // ── INTRADAY (1m, 3m, 5m, 10m, 15m, 30m, 1h, 2h, 4h) ─────────────────────
        if (intraday) {
          var dateKey = _yr + '-' + String(_moi + 1).padStart(2, '0') + '-' + _dd;
          var isDayAnchor = false;
          var isMonthStart = (_dd === '01' || _dd === '02' || _dd === '03');
          var isYearStart = (_moi === 0 && isMonthStart);

          var barSecs = window._intradayBarSecs || 300;
          var barMin = barSecs / 60;
          var barsPerDay = Math.max(1, Math.floor(375 / barMin));
          
          // Snap tick step to standard LWC time intervals
          var stdStepsSec = [86400, 43200, 21600, 14400, 7200, 3600, 1800, 900, 600, 300, 60];
          var targetSec = (visibleBars / 9) * barSecs;
          var stepSec = barSecs;
          for (var _si = 0; _si < stdStepsSec.length; _si++) {
            if (targetSec >= stdStepsSec[_si] * 0.75) {
              stepSec = stdStepsSec[_si];
              break;
            }
          }
          var stepBars = Math.max(1, Math.round(stepSec / barSecs));

          var dayStartIdx = 0;
          if (barIndex !== null && window._intradayBarTimes && barIndex < window._intradayBarTimes.length) {
            dayStartIdx = (window._intradayDayFirstBarMap && window._intradayDayFirstBarMap[dateKey] !== undefined)
              ? window._intradayDayFirstBarMap[dateKey]
              : 0;

            if (dayStartIdx > 0 && dayStartIdx < window._intradayBarTimes.length) {
              var prevDayEpoch = window._intradayBarTimes[dayStartIdx - 1];
              var prevDayD = new Date(prevDayEpoch * 1000 + 5.5 * 3600 * 1000);
              if (prevDayD.getUTCMonth() !== _moi || prevDayD.getUTCFullYear() !== _yr) isMonthStart = true;
              if (prevDayD.getUTCFullYear() !== _yr) isYearStart = true;
            }
          } else if (barIndex !== null && window._intradayBarTimes && window._intradayBarTimes.length > 0) {
            // Future whitespace past loaded data:
            var loadedCount = window._intradayBarTimes.length;
            var lastEpoch = window._intradayBarTimes[loadedCount - 1];
            var lastD = new Date(lastEpoch * 1000 + 5.5 * 3600 * 1000);
            var lastMin = lastD.getUTCHours() * 60 + lastD.getUTCMinutes();
            var minRemainingToday = Math.max(0, (15 * 60 + 25) - lastMin);
            var barsRemainingToday = Math.floor(minRemainingToday / barMin);
            var kBars = barIndex - (loadedCount - 1);
            if (kBars <= barsRemainingToday) {
              dayStartIdx = loadedCount - 1 - (barsPerDay - 1 - barsRemainingToday);
            } else {
              var kLeft = kBars - barsRemainingToday;
              var barInDay = (kLeft - 1) % barsPerDay;
              dayStartIdx = barIndex - barInDay;
            }
            if (_dd === '01' || _dd === '02' || _dd === '03') isMonthStart = true;
            if (_moi === 0 && isMonthStart) isYearStart = true;
          }

          var barsIntoDay = (barIndex !== null) ? (barIndex - dayStartIdx) : 0;
          // First tick of the day is uniquely anchored
          if (barsIntoDay >= 0 && barsIntoDay < stepBars) {
            isDayAnchor = true;
          }

          // Day Anchor for the trading session (only 1 tick per day at market open):
          if (isDayAnchor) {
            // Closer zoom (<= 100 bars): show date + exact opening time anchor (e.g. "24 Sep 09:15" or "24 Sep 09:45")
            if (visibleBars <= 100) return dateStr + ' ' + timeStr;
            // Normal & Medium zoom (100 < visibleBars <= 800 bars): show date anchor (e.g. "24 Sep")
            if (visibleBars <= 800) return isYearStart ? (dateStr + ' ' + _yr) : dateStr;
            // Wide zoom (800 < visibleBars <= 2500 bars): show month/year or date
            if (visibleBars <= 2500) return isMonthStart ? (_mo + ' ' + _yr) : dateStr;
            // Extreme zoom (> 2500 bars): show month/year
            return isYearStart ? String(_yr) : (isMonthStart ? (_mo + ' ' + _yr) : _mo);
          }

          // All other intraday ticks within session:
          // ALWAYS SHOW INTRADAY TIME (e.g. 10:00, 11:20, 12:20, 13:20...)
          if (visibleBars <= 800) {
            return timeStr;
          }
          // Wide zoom (> 800 bars): show dates
          if (visibleBars <= 2500) {
            return dateStr;
          }
          // Extreme zoom (> 2500 bars): show month
          return _mo;
        }

        // ── NON-INTRADAY (1D, 1W, 1M, 3M, 6M, 1Y, ALL) ───────────────────────────
        if (range === '1D') {
          if (visibleBars <= 70) return dateStr;
          if (visibleBars <= 250) {
            if (_moi === 0 || _dd === '01') return _mo + ' ' + _yr;
            return dateStr;
          }
          if (_moi === 0) return String(_yr);
          return _mo;
        }

        if (range === '1W') {
          if (visibleBars <= 60) return dateStr;
          if (visibleBars <= 200) {
            if (_moi === 0) return _mo + ' ' + _yr;
            return _mo;
          }
          return String(_yr);
        }

        if (range === '1M') {
          if (visibleBars <= 60) return _mo + ' ' + _yr;
          return String(_yr);
        }

        // Default / ALL / Multi-Year fallback:
        if (visibleBars <= 100) return dateStr;
        if (visibleBars <= 350) return _mo + ' ' + _yr;
        return String(_yr);
      }
    }
  });

  // Shared, chart-bound LEVERAGE axis formatter (axis-format.js).
  if (window.LeverageAxis) {
    window.LeverageAxis.register(bigChart, {
      getRange: function () { return window.activeRange || 'ALL'; },
      getBarTimes: function () { return window._intradayBarTimes || null; },
      getBarSecs: function () { return window._intradayBarSecs || null; },
      resolveEpoch: function (t) {
        var bt = window._intradayBarTimes, bs = window._intradayBarSecs;
        if (bt && bs && typeof t === 'number') {
          var i = Math.round(t / bs);
          return (i >= 0 && i < bt.length) ? bt[i] : null;
        }
        return window.LeverageAxis.toEpoch(t);
      }
    });
    window._bigChartTick = function (time, markType) {
      return window.LeverageAxis.tick(bigChart, time, markType, window.activeRange || 'ALL');
    };
    window.customTickMarkFormatter = window._bigChartTick;
  } else {
    window.customTickMarkFormatter = bigChart.timeScale().options().tickMarkFormatter;
  }

  var bigCandleSeries = window.bigCandleSeries = bigChart.addCandlestickSeries({
    upColor: '#089981', downColor: '#f23645',
    borderDownColor: '#f23645', borderUpColor: '#089981',
    wickDownColor: '#f23645', wickUpColor: '#089981',
    wickVisible: true,
    priceFormat: { type: 'price', minMove: 0.01 }
  });

  let bigVolumeSeries = window.bigVolumeSeries = bigChart.addHistogramSeries({
    color: '#089981',
    priceFormat: { type: 'volume' },
    priceScaleId: '',
    scaleMargins: { top: 0.85, bottom: 0 }
  });
  bigChart.priceScale('').applyOptions({
    scaleMargins: { top: 0.85, bottom: 0 },
  });

  var bigWhitespaceSeries = window.bigWhitespaceSeries = bigChart.addLineSeries({
    color: 'rgba(0,0,0,0)',
    lineWidth: 1,
    lineVisible: false,
    pointMarkersVisible: false,
    crosshairMarkerVisible: false,
    priceLineVisible: false,
    lastValueVisible: false,
    autoscaleInfoProvider: function() { return null; }
  });

  // Apply a perfect square dotted CSS background grid (42px x 42px) that mimics TradingView
  if (chartParent) {
    chartParent.style.backgroundImage = "url(\"data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%20width%3D%2742%27%20height%3D%2742%27%3E%3Cline%20x1%3D%270%27%20y1%3D%270%27%20x2%3D%270%27%20y2%3D%2742%27%20stroke%3D%27rgba%28255%2C255%2C255%2C0.32%29%27%20stroke-dasharray%3D%271%2C3%27%20stroke-width%3D%271%27%2F%3E%3Cline%20x1%3D%270%27%20y1%3D%270%27%20x2%3D%2742%27%20y2%3D%270%27%20stroke%3D%27rgba%28255%2C255%2C255%2C0.32%29%27%20stroke-dasharray%3D%271%2C3%27%20stroke-width%3D%271%27%2F%3E%3C%2Fsvg%3E\")";
    chartParent.style.backgroundRepeat = "repeat";
    chartParent.style.backgroundColor = "#0A0A0B";
    chartParent.style.backgroundPosition = "0px 0px";
  }

  // Synchronize the vertical grid lines with horizontal chart scroll movements
  bigChart.timeScale().subscribeVisibleLogicalRangeChange(function(range) {
    if (!range) return;
    var x0 = bigChart.timeScale().logicalToCoordinate(0);
    if (x0 !== null && chartParent) {
      chartParent.style.backgroundPositionX = x0 + "px";
    }
  });

  window.getChartCoordinateAPI = function () {
    if (!window.bigChart || !window.bigCandleSeries) return null;
    return {
      // --- Logical Index Coordinate System ---
      // coord.time is treated as a logical bar index (0-based from first bar).
      // Logical indexes can extend beyond the last candle — no clamping.
      // All conversion uses: x = (logical - lr[0]) * pixelsPerBar

      _logicalToTime: function (logical) {
        // Convert logical index to Unix timestamp (for save/backward compat).
        // Uses _bdToSec so this works in both UTC (intraday) and business-day (1D) chart modes.
        var lr = window.bigChart.timeScale().getVisibleLogicalRange();
        var vr = window.bigChart.timeScale().getVisibleRange();
        if (lr && vr && lr[1] > lr[0] && vr.from != null && vr.to != null) {
          var fromSec = _bdToSec(vr.from), toSec = _bdToSec(vr.to);
          var secsPerBar = (toSec - fromSec) / (lr[1] - lr[0]);
          return toSec + (logical - lr[1]) * secsPerBar;
        }
        return logical;
      },
      _timeToLogical: function (time) {
        // Convert Unix timestamp to logical index (for backward compat / snap).
        // Uses _bdToSec so this works in both UTC and business-day chart modes.
        var lr = window.bigChart.timeScale().getVisibleLogicalRange();
        var vr = window.bigChart.timeScale().getVisibleRange();
        if (lr && vr && lr[1] > lr[0] && vr.from != null && vr.to != null) {
          var fromSec = _bdToSec(vr.from), toSec = _bdToSec(vr.to);
          var secsPerBar = (toSec - fromSec) / (lr[1] - lr[0]);
          return lr[1] + (_bdToSec(time) - toSec) / secsPerBar;
        }
        return time;
      },

      coordToPixel: function (coord) {
        if (!coord) return null;
        if (coord.time == null && coord.price == null) return null;
        var lr = window.bigChart.timeScale().getVisibleLogicalRange();
        var y = coord.price != null ? window.bigCandleSeries.priceToCoordinate(coord.price) : null;
        if (!lr || lr[1] <= lr[0]) {
          var x = coord.time != null ? window.bigChart.timeScale().timeToCoordinate(coord.time) : null;
          if ((x == null || y == null) && (x != null || y != null)) return { x: x || 0, y: y || 0 };
          return null;
        }
        var chartWidth = window.bigChart.timeScale().width();
        var ro = (typeof this.getRightOffset === 'function' ? this.getRightOffset() : 40);
        var visibleBars = (lr[1] - lr[0]) + ro;
        var pixelsPerBar = chartWidth / visibleBars;
        var logical = coord.time;
        var x = (logical - lr[0]) * pixelsPerBar;
        if (y == null && x == null) return null;
        return { x: x, y: y || 0 };
      },
      pixelToCoord: function (x, y) {
        var lr = window.bigChart.timeScale().getVisibleLogicalRange();
        if (lr && lr[1] > lr[0]) {
          var chartWidth = window.bigChart.timeScale().width();
          var ro = (typeof this.getRightOffset === 'function' ? this.getRightOffset() : 40);
          var visibleBars = (lr[1] - lr[0]) + ro;
          var pixelsPerBar = chartWidth / visibleBars;
          var logical = lr[0] + x / pixelsPerBar;
          var price = window.bigCandleSeries.coordinateToPrice(y);
          return { time: logical, price: price };
        }
        var time = window.bigChart.timeScale().coordinateToTime(x);
        var price = window.bigCandleSeries.coordinateToPrice(y);
        return { time: time, price: price };
      },
      xToTime: function (x) {
        var lr = window.bigChart.timeScale().getVisibleLogicalRange();
        if (lr && lr[1] > lr[0]) {
          var chartWidth = window.bigChart.timeScale().width();
          var ro = (typeof this.getRightOffset === 'function' ? this.getRightOffset() : 40);
          var visibleBars = (lr[1] - lr[0]) + ro;
          var pixelsPerBar = chartWidth / visibleBars;
          return lr[0] + x / pixelsPerBar;
        }
        return window.bigChart.timeScale().coordinateToTime(x);
      },
      timeToX: function (time) {
        var lr = window.bigChart.timeScale().getVisibleLogicalRange();
        if (lr && lr[1] > lr[0]) {
          var chartWidth = window.bigChart.timeScale().width();
          var ro = (typeof this.getRightOffset === 'function' ? this.getRightOffset() : 40);
          var visibleBars = (lr[1] - lr[0]) + ro;
          var pixelsPerBar = chartWidth / visibleBars;
          return (time - lr[0]) * pixelsPerBar;
        }
        if (time != null) return window.bigChart.timeScale().timeToCoordinate(time);
        return null;
      },
      priceToY: function (price) {
        return window.bigCandleSeries.priceToCoordinate(price);
      },
      yToPrice: function (y) {
        return window.bigCandleSeries.coordinateToPrice(y);
      },
      getMagnetPoint: function (x, y) {
        return this.pixelToCoord(x, y);
      },
      getBarSpacing: function () {
        var lr = window.bigChart.timeScale().getVisibleLogicalRange();
        if (!lr) return 20;
        return window.bigChart.timeScale().width() / (lr[1] - lr[0] || 1);
      },
      getRightOffset: function () {
        return window._rightOffset || 40;
      }
    };
  };

  // --- Candle Countdown Timer Logic ---
  chartContainer.style.position = 'relative';

  var countdownEl = document.createElement('div');
  countdownEl.id = 'candle-countdown';
  countdownEl.style.position = 'absolute';
  countdownEl.style.right = '0px';
  countdownEl.style.width = '60px'; // Approx width of the right scale
  countdownEl.style.height = '18px';
  countdownEl.style.backgroundColor = 'transparent';
  countdownEl.style.color = '#fff';
  countdownEl.style.fontSize = '12px';
  countdownEl.style.fontWeight = '500';
  countdownEl.style.textAlign = 'center';
  countdownEl.style.lineHeight = '18px';
  countdownEl.style.zIndex = '100';
  countdownEl.style.pointerEvents = 'none';
  countdownEl.style.display = 'none';
  countdownEl.style.borderBottomLeftRadius = '4px';
  chartContainer.appendChild(countdownEl);

  setInterval(function() {
    if (!window.bigCandleSeries || !window.activeRange) {
      countdownEl.style.display = 'none';
      return;
    }
    
    var intervalMin = { '1m':1, '3m':3, '5m':5, '10m':10, '15m':15, '30m':30, '1h':60, '2h':120, '4h':240 }[window.activeRange];
    if (!intervalMin) {
      countdownEl.style.display = 'none';
      return; // Not intraday (e.g. 1D, 1W, 1M)
    }
    
    var SESSION_START_MIN = 9 * 60 + 15; // 09:15 IST
    var SESSION_END_MIN   = 15 * 60 + 30; // 15:30 IST
    var IST_OFFSET_MS     = 5.5 * 3600 * 1000;
    
    var now = new Date();
    var utcMs = now.getTime();
    var _istNow = new Date(utcMs + IST_OFFSET_MS);
    var _istDay = _istNow.getUTCDay(); // 0=Sun, 6=Sat
    var _istMin = _istNow.getUTCHours() * 60 + _istNow.getUTCMinutes();
    
    // Strict guard: only show countdown during active NSE market hours on weekdays
    if (_istDay === 0 || _istDay === 6 || _istMin < SESSION_START_MIN || _istMin >= SESSION_END_MIN) {
      countdownEl.style.display = 'none';
      return; // Market closed
    }
    
    var ms = intervalMin * 60 * 1000;
    var istDayStartMs = (Math.floor((utcMs + IST_OFFSET_MS) / 86400000) * 86400000) - IST_OFFSET_MS;
    var sessionStartMs = istDayStartMs + SESSION_START_MIN * 60000;
    var offsetFromSession = utcMs - sessionStartMs;
    
    var currentCandleStart = sessionStartMs + Math.floor(offsetFromSession / ms) * ms;
    var nextCloseMs = currentCandleStart + ms;
    
    var diff = nextCloseMs - utcMs;
    if (diff < 0) diff = 0;
    
    var totalSeconds = Math.floor(diff / 1000);
    var mm = Math.floor(totalSeconds / 60);
    var ss = totalSeconds % 60;
    var timeStr = String(mm).padStart(2, '0') + ':' + String(ss).padStart(2, '0');
    
    countdownEl.innerText = timeStr;
    
    var priceToSnap = window.lastLivePrice;
    if (priceToSnap == null && window._lastHistoricalCandle) {
      priceToSnap = window._lastHistoricalCandle.close;
    }
    if (priceToSnap == null) {
      countdownEl.style.display = 'none';
      return;
    }
    
    var y = window.bigCandleSeries.priceToCoordinate(priceToSnap);
    if (y === null || y < 0 || y > chartContainer.clientHeight) {
      countdownEl.style.display = 'none';
      return;
    }
    
    // BUGFIX: was `y + 11`, which put the countdown's top edge immediately
    // against (and on some prices/zoom levels, inside) the native
    // lightweight-charts last-price label's bottom edge -- both are visible
    // at the same time only during market hours while a candle is forming,
    // which is exactly when this got reported as visually overlapping.
    // Widened to a more conservative gap below the native label instead of
    // assuming its exact height to the pixel.
    countdownEl.style.top = (y + 20) + 'px';
    countdownEl.style.display = 'block';
    
    var fc = window._formingCandles && window._formingCandles['i'];
    if (fc) {
       var isUp = fc.close >= fc.open;
       countdownEl.style.backgroundColor = isUp ? '#089981' : '#f23645';
    } else {
       countdownEl.style.backgroundColor = '#089981';
    }
  }, 1000);
  // ------------------------------------------
  
  var toolTip = document.createElement('div');
  toolTip.className = 'floating-tooltip';
  toolTip.style.position = 'absolute';
  toolTip.style.display = 'none';
  toolTip.style.padding = '8px';
  toolTip.style.boxSizing = 'border-box';
  toolTip.style.fontSize = '12px';
  toolTip.style.color = '#fff';
  toolTip.style.backgroundColor = 'rgba(19, 23, 34, 0.9)';
  toolTip.style.border = '1px solid #2a2e39';
  toolTip.style.borderRadius = '4px';
  toolTip.style.zIndex = '1000';
  toolTip.style.pointerEvents = 'none';
  document.getElementById('chart-container').appendChild(toolTip);

  bigChart.subscribeCrosshairMove(function(param) {
    if (!param.time || param.point.x < 0 || param.point.y < 0) {
      toolTip.style.display = 'none';
      var activeFc = window._formingCandles ? (window._formingCandles['i'] || window._formingCandles['d']) : null;
      if (activeFc && activeFc.close > 0) {
        var pEl = document.getElementById('chart-ticker-price'); if (pEl) pEl.innerText = activeFc.close.toFixed(2);
        var chEl = document.getElementById('chart-change');
        if (chEl && _chartDataCache && _chartDataCache.length > 0) {
          var prevClose = _chartDataCache[_chartDataCache.length - 1].close;
          if (activeFc.time === _chartDataCache[_chartDataCache.length - 1].time && _chartDataCache.length > 1) {
            prevClose = _chartDataCache[_chartDataCache.length - 2].close;
          }
          if (prevClose > 0) {
            var diff = activeFc.close - prevClose;
            var pct = (diff / prevClose) * 100;
            chEl.innerText = (diff >= 0 ? '+ ' : '- ') + Math.abs(diff).toFixed(2) + ' (' + (diff >= 0 ? '+' : '') + pct.toFixed(2) + '%)';
            var col = diff >= 0 ? '#089981' : '#f23645';
            chEl.style.color = col;
            if (pEl) pEl.style.color = col;
          }
        }
        var oEl = document.getElementById('ohlc-open');     if (oEl) { oEl.innerText = activeFc.open.toFixed(2); oEl.style.color = '#d1d4dc'; }
        var hEl = document.getElementById('ohlc-high');     if (hEl) { hEl.innerText = activeFc.high.toFixed(2); hEl.style.color = '#089981'; }
        var lEl = document.getElementById('ohlc-low');      if (lEl) { lEl.innerText = activeFc.low.toFixed(2); lEl.style.color = '#f23645'; }
        var cEl = document.getElementById('ohlc-close');    if (cEl) { cEl.innerText = activeFc.close.toFixed(2); cEl.style.color = '#d1d4dc'; }
        var volEl = document.getElementById('ohlc-volume');
        if (volEl && activeFc.volume) {
          volEl.innerText = Number(activeFc.volume).toLocaleString('en-IN');
          volEl.style.color = '#d1d4dc';
        }
      } else if (window._lastHistoricalCandle) {
        var last = window._lastHistoricalCandle;
        var pEl = document.getElementById('chart-ticker-price'); if (pEl) pEl.innerText = last.close.toFixed(2);
        var chEl = document.getElementById('chart-change');
        if (chEl && _chartDataCache && _chartDataCache.length > 1) {
          var prevClose = _chartDataCache[_chartDataCache.length - 2].close;
          if (prevClose > 0) {
            var diff = last.close - prevClose;
            var pct = (diff / prevClose) * 100;
            chEl.innerText = (diff >= 0 ? '+ ' : '- ') + Math.abs(diff).toFixed(2) + ' (' + (diff >= 0 ? '+' : '') + pct.toFixed(2) + '%)';
            var col = diff >= 0 ? '#089981' : '#f23645';
            chEl.style.color = col;
            if (pEl) pEl.style.color = col;
          }
        }
        var oEl = document.getElementById('ohlc-open');     if (oEl) { oEl.innerText = last.open.toFixed(2); oEl.style.color = '#d1d4dc'; }
        var hEl = document.getElementById('ohlc-high');     if (hEl) { hEl.innerText = last.high.toFixed(2); hEl.style.color = '#089981'; }
        var lEl = document.getElementById('ohlc-low');      if (lEl) { lEl.innerText = last.low.toFixed(2); lEl.style.color = '#f23645'; }
        var cEl = document.getElementById('ohlc-close');    if (cEl) { cEl.innerText = last.close.toFixed(2); cEl.style.color = '#d1d4dc'; }
        var volEl = document.getElementById('ohlc-volume');
        if (volEl) {
          volEl.innerText = last.volume ? fmtCompact(last.volume) : '--';
          volEl.style.color = '#d1d4dc';
        }
      }
      return;
    }
    var price = param.seriesData.get(bigCandleSeries);
    if (!price) {
      // Bug 4 fix: try area/line series if candle series is hidden
      var allSeries = [bigCandleSeries, bigVolumeSeries];
      for (var si = 0; si < allSeries.length; si++) {
        var sp = allSeries[si] ? param.seriesData.get(allSeries[si]) : null;
        if (sp) { price = sp; break; }
      }
      if (!price) {
        toolTip.style.display = 'none';
        return;
      }
    }
    var vol = typeof bigVolumeSeries !== 'undefined' ? param.seriesData.get(bigVolumeSeries) : null;
    var dateStr;
    if (typeof param.time === 'string') {
      // Bug 2 fix: format YYYY-MM-DD into readable date
      var parts = param.time.split('-');
      if (parts.length === 3) {
        var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        dateStr = parseInt(parts[2], 10) + ' ' + months[parseInt(parts[1], 10) - 1] + ' ' + parts[0];
      } else {
        dateStr = param.time;
      }
    } else {
      // Convert UTC epoch to IST for display (+5:30)
      var IST_OFFSET_MS = 5.5 * 60 * 60 * 1000;
      var d = new Date(param.time * 1000 + IST_OFFSET_MS);
      var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
      var day = d.getUTCDate();
      var mon = months[d.getUTCMonth()];
      var yr  = d.getUTCFullYear();
      var hh  = String(d.getUTCHours()).padStart(2, '0');
      var mm  = String(d.getUTCMinutes()).padStart(2, '0');
      dateStr = day + ' ' + mon + ' ' + yr + ', ' + hh + ':' + mm + ' IST';
    }

    // Bug 1 fix: prev close from data cache, current price from WS
    var prevCloseStr = '--';
    var currentPriceStr = '--';
    if (_chartDataCache && _chartDataCache.length > 0) {
      var currentTime = param.time;
      for (var ci = 0; ci < _chartDataCache.length; ci++) {
        if (_chartDataCache[ci].time === currentTime) {
          if (ci > 0) {
            prevCloseStr = _chartDataCache[ci - 1].close.toFixed(2);
          }
          break;
        }
      }
    }
    if (window.lastLivePrice != null) {
      currentPriceStr = window.lastLivePrice.toFixed(2);
    }

    // Update floating legend
    var pEl = document.getElementById('chart-ticker-price'); if (pEl) pEl.innerText = price.close.toFixed(2);
    var chEl2 = document.getElementById('chart-change');
    if (chEl2) {
      var chClose = parseFloat(prevCloseStr);
      if (!isNaN(chClose) && chClose > 0) {
        var chDiff2 = price.close - chClose;
        var chPct2 = (chDiff2 / chClose) * 100;
        chEl2.innerText = (chDiff2 >= 0 ? '+ ' : '- ') + Math.abs(chDiff2).toFixed(2) + ' (' + (chDiff2 >= 0 ? '+' : '') + chPct2.toFixed(2) + '%)';
        var col2 = chDiff2 >= 0 ? '#089981' : '#f23645';
        chEl2.style.color = col2;
        if (pEl) pEl.style.color = col2;
      }
    }
    var oEl = document.getElementById('ohlc-open');     if (oEl) { oEl.innerText = price.open.toFixed(2); oEl.style.color = '#d1d4dc'; }
    var hEl = document.getElementById('ohlc-high');     if (hEl) { hEl.innerText = price.high.toFixed(2); hEl.style.color = '#089981'; }
    var lEl = document.getElementById('ohlc-low');      if (lEl) { lEl.innerText = price.low.toFixed(2); lEl.style.color = '#f23645'; }
    var cEl = document.getElementById('ohlc-close');    if (cEl) { cEl.innerText = price.close.toFixed(2); cEl.style.color = '#d1d4dc'; }
    var volEl = document.getElementById('ohlc-volume');
    if (volEl) {
      var vVal = vol ? (vol.value || vol.volume || 0) : 0;
      if (vVal > 0) {
        volEl.innerText = fmtCompact(vVal);
      } else {
        volEl.innerText = '--';
      }
      volEl.style.color = '#d1d4dc';
    }
  });

  /* --- Chart Resize Handler (ResizeObserver + window.resize fallback) --- */
  function resizeChart() {
    var parent = document.getElementById('chart-container');
    if (!parent) return;
    var h = parent.offsetHeight || (window._chartLastSize ? window._chartLastSize.height : 400);
    var w = parent.clientWidth  || (window._chartLastSize ? window._chartLastSize.width : 600);
    window._chartLastSize = { height: h, width: w };
    var minPriceScaleWidth = window.innerWidth <= 480 ? 54 : (window.innerWidth <= 768 ? 58 : 68);
    if (bigChart) {
      bigChart.applyOptions({
        height: h,
        width: w,
        rightPriceScale: {
          minimumWidth: minPriceScaleWidth
        }
      });
    }
  }
  (function() {
    var _rp = document.getElementById('chart-container');
    if (typeof ResizeObserver !== 'undefined' && _rp) {
      new ResizeObserver(function() { requestAnimationFrame(resizeChart); }).observe(_rp);
    }
  })();
  window.addEventListener('resize', resizeChart);
  document.addEventListener('fullscreenchange', function() { setTimeout(resizeChart, 200); });
  requestAnimationFrame(resizeChart);

  /* --- Timezone Indicator --- */
  var tzLabel = document.createElement('div');
  tzLabel.textContent = 'IST';
  tzLabel.style.cssText = 'position:absolute;bottom:32px;right:90px;z-index:10;font-size:10px;color:#555;background:rgba(10,10,11,0.8);padding:2px 6px;border-radius:3px;pointer-events:none;font-family:monospace;';
  var chartContainerEl = document.getElementById('chart-container');
  if (chartContainerEl) chartContainerEl.appendChild(tzLabel);

  /* --- Fullscreen Toggle --- */
  var fullscreenBtn = document.createElement('button');
  fullscreenBtn.id = 'fullscreenBtn';
  fullscreenBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="#666" stroke-width="1.5"><path d="M3 9v4h4M13 7V3H9M3 7V3h4M13 9v4H9"/></svg>';
  fullscreenBtn.title = 'Fullscreen (F)';
  fullscreenBtn.style.cssText = 'position:absolute;bottom:32px;right:60px;z-index:10;background:rgba(10,10,11,0.8);border:1px solid #2a2e39;border-radius:4px;padding:4px;cursor:pointer;display:flex;align-items:center;justify-content:center;';
  fullscreenBtn.onclick = toggleFullscreen;
  if (chartContainerEl) chartContainerEl.appendChild(fullscreenBtn);

  function toggleFullscreen() {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(function(){});
    } else {
      document.exitFullscreen().catch(function(){});
    }
    setTimeout(resizeChart, 300);
  }

  /* ─── Keyboard Shortcuts ─── */
  var _kbdHandler = function(e) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    switch(e.key) {
      case 'f': case 'F': toggleFullscreen(); e.preventDefault(); break;
      case 'r': case 'R': 
        if (window._chartCandles && window._chartCandles.length > 0) {
          var _ei = window._chartCandles.length - 1;
          bigChart.timeScale().setVisibleLogicalRange({ from: Math.max(0, _ei - 90), to: _ei + 5 });
        } else {
          bigChart.timeScale().fitContent();
        }
        e.preventDefault(); 
        break;
      case 'ArrowLeft': bigChart.timeScale().scrollToPosition(bigChart.timeScale().scrollPosition() - 100, false); e.preventDefault(); break;
      case 'ArrowRight': bigChart.timeScale().scrollToPosition(bigChart.timeScale().scrollPosition() + 100, false); e.preventDefault(); break;
      case '+': case '=': bigChart.timeScale().zoomIn(); e.preventDefault(); break;
      case '-': case '_': bigChart.timeScale().zoomOut(); e.preventDefault(); break;
    }
  };
  window.addEventListener('keydown', _kbdHandler);
  window._chartCleanup = window._chartCleanup || [];
  window._chartCleanup.push(function(){ window.removeEventListener('keydown', _kbdHandler); });

  function fmtPrice(v) {
    if (v == null) return '--';
    return '₹' + Number(v).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  // Compact Indian number format: 20L, 2.5Cr, 1.2K
  function fmtCompact(n) {
    n = Number(n);
    if (!n || isNaN(n)) return '--';
    var abs = Math.abs(n);
    if (abs >= 1e7) return (n / 1e7).toFixed(abs >= 1e8 ? 1 : 2).replace(/\.0+$/, '') + ' Cr';
    if (abs >= 1e5) return (n / 1e5).toFixed(abs >= 1e6 ? 1 : 2).replace(/\.0+$/, '') + ' L';
    if (abs >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'K';
    return String(Math.round(n));
  }
  window.fmtCompact = fmtCompact;

  function showChartError(msg) {
    var errEl = document.getElementById('error-msg');
    if (errEl) { errEl.style.display = 'block'; errEl.textContent = msg; }
    console.error('Chart error:', msg);
  }

  function hideChartError() {
    var errEl = document.getElementById('error-msg');
    if (errEl) errEl.style.display = 'none';
  }

  var _chartDataCache = null;
  var _earliestLoadedTime = null;
  var _isLoadingMore = false;
  var _lazyLoadSubscribed = false;

  // P1.1: intraday initial-load AND lazy-load page size, in bars.
  // Rationale: the default viewport shows the last 90 bars; this is ~6.7x that
  // (comfortable first view + several scrollback pages), ~2x the minichart's
  // 300-bar page, and well above the 10-bar lazy-load trigger buffer. Previously
  // every intraday open fetched limit=15000 (~0.5 MB for 5m, ~4-6k bars) even
  // though only ~90 bars are visible; older history now streams in on scroll.
  var INTRADAY_PAGE_BARS = 600;

  function _setupLazyLoad() {
    if (!bigChart || _lazyLoadSubscribed) return;
    _lazyLoadSubscribed = true;
    bigChart.timeScale().subscribeVisibleTimeRangeChange(function () {
      _maybeLazyLoad();
    });
  }

  function _maybeLazyLoad() {
    if (_isLoadingMore || window._hasMoreHistoricalData === false) return;
    var range = window._loadedRange;
    if (!range || _earliestLoadedTime == null) return;
    // P1.1: intraday is now paginated too. Older bars are fetched with the real
    // earliest epoch and the sequential index base is rebuilt in _fetchMoreData,
    // so lazy-load is enabled (previously disabled because it needed renumbering).
    var timeRange = bigChart.timeScale().getVisibleRange();
    if (!timeRange) return;
    var intervalMap = { '1m': 60, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600 };
    var tickSec = intervalMap[range];
    var buffer;
    if (tickSec) {
      buffer = tickSec * 10;
    } else if (range === '1W') {
      buffer = 86400 * 14;   // trigger fetch when user scrolls within 2 weeks of oldest 1W bar
    } else if (range === '1M') {
      buffer = 86400 * 45;   // trigger fetch when user scrolls within 45 days of oldest 1M bar
    } else {
      // Daily ranges: use 5-day buffer
      buffer = 86400 * 5;
    }
    if (_bdToSec(timeRange.from) >= _bdToSec(_earliestLoadedTime) + buffer) return;
    _fetchMoreData(_earliestLoadedTime, range);
  }

  function _fetchMoreData(beforeTime, range) {
    _isLoadingMore = true;
    // FE-04: capture the load generation this fetch started under. If the
    // user switches range/timeframe before this (slow, background) fetch
    // resolves, window.loadData() bumps _loadId for the new load -- without
    // this check, the stale response would still land and merge history
    // for the WRONG timeframe into the chart currently on screen.
    var myLoadId = _loadId;
    var isIntraday = ['1m','5m','15m','30m','1h'].indexOf(range) !== -1;
    var isWeeklyMonthly = (range === '1W' || range === '1M');
    var url;
    if (isIntraday) {
      // P1.1: display times are index-based for intraday, so _earliestLoadedTime is 0;
      // use the true earliest loaded epoch for the "before" cursor.
      var beforeEpochIntra = (window._intradayEarliestEpoch != null) ? window._intradayEarliestEpoch : beforeTime;
      url = '/api/stock-data/intraday/paginated?ticker=' + encodeURIComponent(ticker)
          + '&interval=' + range + '&before=' + beforeEpochIntra + '&limit=' + INTRADAY_PAGE_BARS;
    } else if (isWeeklyMonthly) {
      // beforeTime is a "YYYY-MM-DD" string for 1W/1M; convert to epoch seconds for the endpoint
      var _bMs = (typeof beforeTime === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(beforeTime))
        ? new Date(beforeTime + 'T00:00:00Z').getTime()
        : (beforeTime * 1000);
      var beforeEpoch = Math.floor(_bMs / 1000);
      url = '/api/stock-data/history-before?ticker=' + encodeURIComponent(ticker)
          + '&timeframe=' + range + '&before=' + beforeEpoch;
    } else {
      // For daily ranges, convert to ISO date string (already a string in 1D mode)
      var beforeDate = /^\d{4}-\d{2}-\d{2}$/.test(beforeTime)
        ? beforeTime
        : new Date(beforeTime * 1000).toISOString().split('T')[0];
      url = '/api/stock-data/range/paginated?ticker=' + encodeURIComponent(ticker)
          + '&range=' + range + '&before=' + beforeDate + '&limit=15000';
    }
    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (resp) {
        if (myLoadId !== _loadId) {
          // A newer range/timeframe load started while this was in flight --
          // discard the result instead of corrupting the current chart.
          _isLoadingMore = false;
          return;
        }
        // history-before returns a plain array; paginated endpoints return {data, has_more}
        if (Array.isArray(resp)) {
          resp = { data: resp, has_more: null };
        }
        if (resp && resp.data && resp.data.length > 0) {
          // filterMarketHours only makes sense for intraday; 1W/1M bars are midnight timestamps
          // and would be incorrectly removed by the time-of-day check.
          if (isIntraday) { resp.data = filterMarketHours(resp.data); }
          var _isNonIntra = ['1m','5m','15m','30m','1h'].indexOf(range) === -1;
          var incoming = resp.data.map(function (p) {
            var s = _sanitizeOHLCV(p);
            if (!s) return null;
            var t = _isNonIntra ? _toIST1DDate(toTimeNum(p.time)) : toTimeNum(p.time);
            return { time: t, open: s.open, high: s.high, low: s.low, close: s.close, volume: s.volume };
          }).filter(function (x) { return x !== null; });
          if (isIntraday) {
            // P1.1: prepend older bars, rebuild the sequential index base, keep viewport.
            _prependIntradayAndRender(incoming, resp.has_more);
            _isLoadingMore = false;
            return;
          }
          var combined = _chartDataCache ? _chartDataCache.slice() : [];
          var currentLogical = bigChart.timeScale().getVisibleLogicalRange();
          var existingTimes = {};
          combined.forEach(function (c) { existingTimes[c.time] = true; });
          var addedCount = 0;
          incoming.forEach(function (c) {
            if (!existingTimes[c.time]) { combined.push(c); existingTimes[c.time] = true; addedCount++; }
          });
          if (typeof combined[0] !== 'undefined' && typeof combined[0].time === 'string') {
            combined.sort(function (a, b) { return a.time < b.time ? -1 : a.time > b.time ? 1 : 0; });
          } else {
            combined.sort(function (a, b) { return a.time - b.time; });
          }
          _chartDataCache = combined;
          window._chartCandles = _chartDataCache;
          _earliestLoadedTime = combined.length > 0 ? combined[0].time : null;
          bigCandleSeries.setData(combined);
          if (bigVolumeSeries) {
            var volData = combined.map(function (p) { return { time: p.time, value: p.volume, color: p.close >= p.open ? 'rgba(8,153,129,0.3)' : 'rgba(242,54,69,0.3)' }; });
            bigVolumeSeries.setData(volData);
          }
          if (window.IndicatorEngine) window.IndicatorEngine.onCandlesLoaded(combined, range);
          if (currentLogical && addedCount > 0) {
            bigChart.timeScale().setVisibleLogicalRange({
              from: currentLogical.from + addedCount,
              to: currentLogical.to + addedCount
            });
          }
          if (resp.has_more === false || addedCount === 0) {
            window._hasMoreHistoricalData = false;
          }
        } else {
          window._hasMoreHistoricalData = false;
        }
        _isLoadingMore = false;
      })
      .catch(function () { _isLoadingMore = false; });
  }

  // P1.1: build the sequential-index display view for intraday bars.
  // realBars: ascending [{time: epochSec, open, high, low, close, volume}].
  // Sets the window lookup tables used by tickMarkFormatter/formatIST and the live
  // forming-candle mapper, and returns bars whose time = index * barSecs. Extracted
  // from _renderChartData so pagination can rebuild the index base when older
  // history is prepended, without duplicating the mapping logic.
  function _buildIntradayDisplay(realBars, barSecs) {
    window._intradayBarTimes = realBars.map(function (b) { return b.time; });
    window._intradayBarSecs  = barSecs;
    window._intradayBarSeqMap = null;   // force rebuild against the new index base
    window._intradayDayFirstBarMap = {};
    var _IST_MS = 5.5 * 3600 * 1000;
    for (var _i = 0; _i < realBars.length; _i++) {
      var _dObj = new Date(realBars[_i].time * 1000 + _IST_MS);
      var _dKey = _dObj.getUTCFullYear() + '-' + String(_dObj.getUTCMonth() + 1).padStart(2, '0') + '-' + String(_dObj.getUTCDate()).padStart(2, '0');
      if (window._intradayDayFirstBarMap[_dKey] === undefined) {
        window._intradayDayFirstBarMap[_dKey] = _i;
      }
    }
    return realBars.map(function (b, i) {
      return { time: i * barSecs, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume };
    });
  }

  // P1.1: merge an older intraday page (real epochs) with the currently loaded bars,
  // rebuild the sequential display view, and keep the viewport anchored on the same
  // bars so prepending older history does not visually jump the chart.
  function _prependIntradayAndRender(incomingRealBars, hasMore) {
    var barSecs = window._intradayBarSecs || 300;
    var curReal = [];
    if (window._intradayBarTimes && _chartDataCache) {
      for (var i = 0; i < window._intradayBarTimes.length; i++) {
        var c = _chartDataCache[i];
        if (c) curReal.push({ time: window._intradayBarTimes[i], open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume });
      }
    }
    var merged = curReal.concat(incomingRealBars);
    merged.sort(function (a, b) { return a.time - b.time; });
    var seen = {}, uniq = [];
    merged.forEach(function (c) { if (!seen[c.time]) { seen[c.time] = true; uniq.push(c); } });
    var addedCount = uniq.length - curReal.length;

    var displayData = _buildIntradayDisplay(uniq, barSecs);
    window._intradayEarliestEpoch = uniq.length > 0 ? uniq[0].time : null;
    _chartDataCache = displayData;
    window._chartCandles = _chartDataCache;
    _earliestLoadedTime = 0;   // index-based display always starts at 0

    var currentLogical = bigChart.timeScale().getVisibleLogicalRange();
    bigCandleSeries.setData(displayData);
    try { if (window.LeverageAxis && window.bigChart) window.LeverageAxis.reset(window.bigChart); } catch (e) {}
    if (bigVolumeSeries) {
      bigVolumeSeries.setData(displayData.map(function (p) {
        return { time: p.time, value: p.volume, color: p.close >= p.open ? 'rgba(8,153,129,0.3)' : 'rgba(242,54,69,0.3)' };
      }));
    }
    // The live forming candle's sequential index shifts by the number of prepended bars.
    if (addedCount > 0 && window._formingCandles && window._formingCandles['i']) {
      // Intraday bar `time` is a SEQUENTIAL index (i * barSecs), so shifting the
      // forming candle for N prepended bars must add N * barSecs -- NOT N.
      // Adding only N left the forming index ~N bars behind the rebuilt cache,
      // so subsequent live ticks updated an OLD candle instead of the current
      // one (price moved but the running candle appeared frozen) after any
      // lazy-load prepend (scroll-left / zoom-out into history).
      window._formingCandles['i'].time += addedCount * barSecs;
    }
    if (window.IndicatorEngine) window.IndicatorEngine.onCandlesLoaded(displayData, window._loadedRange);
    if (currentLogical && addedCount > 0) {
      bigChart.timeScale().setVisibleLogicalRange({ from: currentLogical.from + addedCount, to: currentLogical.to + addedCount });
    }
    window._hasMoreHistoricalData = (hasMore !== false) && addedCount > 0;
  }

  // Shared chart data renderer (used by cache path and API path)
  function _renderChartData(data, range) {
    // Always clear both series before setting new data so Lightweight Charts can switch
    // between UTC mode (intraday) and business-day mode (1D) without format conflicts.
    bigCandleSeries.setData([]);
    if (bigVolumeSeries) bigVolumeSeries.setData([]);
    if (window.bigWhitespaceSeries) window.bigWhitespaceSeries.setData([]);

    if (!data || data.length === 0) {
      bigChart.timeScale().fitContent();
      window._loadedRange = range;
      window._formingCandles = {};
      var errEl = document.getElementById('error-msg');
      if (errEl) { errEl.textContent = 'No chart data for ' + ticker; errEl.style.display = 'block'; }
      return;
    }
    var _isIntraday = ['1m','3m','5m','10m','15m','30m','1h','2h','4h'].indexOf(range) !== -1;
    // Non-intraday ranges (1D, 1W, 1M, 3M, 6M, 1Y, ALL) use "YYYY-MM-DD" business-day strings.
    var isDailyWeeklyMonthly = !_isIntraday;
    var formatted = data.filter(function(p) {
      return p.open > 0 && p.high > 0 && p.low > 0 && p.close > 0;
    }).map(function (p) {
      var t = isDailyWeeklyMonthly ? _toIST1DDate(toTimeNum(p.time)) : toTimeNum(p.time);
      return { time: t, open: p.open, high: p.high, low: p.low, close: p.close, volume: p.volume || 0 };
    });
    if (isDailyWeeklyMonthly) {
      formatted.sort(function (a, b) { return a.time < b.time ? -1 : a.time > b.time ? 1 : 0; });
    } else {
      formatted.sort(function (a, b) { return a.time - b.time; });
    }
    var seen = {}, unique = [];
    formatted.forEach(function (p) { if (!seen[p.time]) { seen[p.time] = true; unique.push(p); } });

    window._hasMoreHistoricalData = true;

    // For intraday timeframes: remap timestamps to sequential indices so consecutive trading
    // sessions appear adjacent with no weekend/overnight gaps. A global lookup table lets
    // tickMarkFormatter and formatIST show the correct real date/time for each bar.
    var _BAR_SECS = { '1m': 60, '3m': 180, '5m': 300, '10m': 600, '15m': 900, '30m': 1800, '1h': 3600, '2h': 7200, '4h': 14400 }[range];
    var displayData;
    if (!isDailyWeeklyMonthly && _BAR_SECS && unique.length > 0) {
      window._intradayEarliestEpoch = unique[0].time;
      displayData = _buildIntradayDisplay(unique, _BAR_SECS);
    } else {
      window._intradayEarliestEpoch = null;
      window._intradayBarTimes = null;
      window._intradayBarSecs  = null;
      window._intradayBarSeqMap = null;
      window._intradayDayFirstBarMap = null;
      displayData = unique;
    }

    _chartDataCache = displayData.slice();
    window._chartCandles = _chartDataCache;
    _earliestLoadedTime = displayData.length > 0 ? displayData[0].time : null;

    var candleData = displayData;
    // Set activeRange and timeVisible BEFORE setData so tickMarkFormatter reads the
    // correct range on the very first synchronous render LWC does during setData.
    window.activeRange = range;
    window._loadedRange = range;
    // Sequential mode (intraday sequential mapping) needs timeVisible:true.
    // 1D/1W/1M chronological mode doesn't need timeVisible:true (no time sub-day ticks needed).
    var _seqMode = _isIntraday;
    var _rightOffset = range === '1M' ? 1 : range === '1W' ? 2 : range === '1D' ? 5 : 12;
    if (window.bigChart) {
      var minPriceScaleWidth = window.innerWidth <= 480 ? 54 : (window.innerWidth <= 768 ? 58 : 68);
      window.bigChart.applyOptions({
        rightPriceScale: { minimumWidth: minPriceScaleWidth },
        timeScale: {
          timeVisible: _seqMode,
          rightOffset: _rightOffset,
          uniformDistribution: false,
          tickMarkFormatter: window.customTickMarkFormatter
        }
      });
    }

    bigCandleSeries.setData(candleData);
    try { if (window.LeverageAxis && window.bigChart) window.LeverageAxis.reset(window.bigChart); } catch (e) {}
    if (unique.length > 0) {
      window._lastHistoricalCandle = unique[unique.length - 1];
    }
    if (bigVolumeSeries) {
      var volData = displayData.map(function (p) {
        return { time: p.time, value: p.volume, color: p.close >= p.open ? 'rgba(8,153,129,0.3)' : 'rgba(242,54,69,0.3)' };
      });
      bigVolumeSeries.setData(volData);
    }

    if (window.bigWhitespaceSeries && displayData.length > 0) {
      var wsData = [];
      var baseVal = (candleData[0] && candleData[0].close) ? candleData[0].close : 100;
      if (_isIntraday && _BAR_SECS) {
        var totalBars = candleData.length + 3000;
        for (var wi = 0; wi < totalBars; wi++) {
          wsData.push({ time: wi * _BAR_SECS, value: baseVal });
        }
      } else if (isDailyWeeklyMonthly) {
        wsData = candleData.map(function(p) { return { time: p.time, value: baseVal }; });
        var lastTime = candleData[candleData.length - 1].time;
        if (range === '1W') {
          var curW = lastTime;
          for (var wi = 1; wi <= 150; wi++) {
            curW = _addDays(curW, 7);
            wsData.push({ time: curW, value: baseVal });
          }
        } else if (range === '1M') {
          var curM = lastTime;
          for (var wi = 1; wi <= 80; wi++) {
            curM = _addDays(curM, 30);
            wsData.push({ time: curM, value: baseVal });
          }
        } else {
          var curD = lastTime;
          for (var wi = 1; wi <= 365; wi++) {
            curD = _nextBizDay(curD);
            wsData.push({ time: curD, value: baseVal });
          }
        }
      }
      window.bigWhitespaceSeries.setData(wsData);
    }
    if (unique.length > 0) {
      var endIdx = displayData.length - 1;
      var applyViewport = function() {
        if (!bigChart) return;
        if (range === '1W') {
          var w1start = Math.max(0, endIdx - 52);
          bigChart.timeScale().setVisibleLogicalRange({ from: w1start, to: endIdx + 2 });
        } else if (range === '1M') {
          var m1start = Math.max(0, endIdx - 60);
          bigChart.timeScale().setVisibleLogicalRange({ from: m1start, to: endIdx + 1 });
        } else {
          var startIdx = Math.max(0, endIdx - 90);
          bigChart.timeScale().setVisibleLogicalRange({ from: startIdx, to: endIdx + 5 });
        }
      };
      applyViewport();
      requestAnimationFrame(applyViewport);
      setTimeout(applyViewport, 30);
      setTimeout(applyViewport, 120);
    } else {
      bigChart.timeScale().fitContent();
    }

    _setupLazyLoad();

    window._formingCandles = {};
    window._lastFormingRange = range;
    window._lastCandleClose = unique[unique.length - 1].close;

    window.lastChartTime = unique[unique.length - 1].time;
    if (window.IndicatorEngine) window.IndicatorEngine.onCandlesLoaded(displayData, range);
    var last = unique[unique.length - 1];
    window._lastHistoricalCandle = last;

    var SESSION_START_MIN = 9 * 60 + 15;
    var SESSION_END_MIN   = 15 * 60 + 30;
    var _nowForGate = getIstNow();
    var _istDay = _nowForGate.getDay();
    var _istMin = _nowForGate.getHours() * 60 + _nowForGate.getMinutes();
    var _isMarketOpen = (_istDay >= 1 && _istDay <= 5 && _istMin >= SESSION_START_MIN && _istMin < SESSION_END_MIN);

    // Look for live price specific to this ticker
    var cachedLive = null;
    try {
      var rawLlp = localStorage.getItem('llp');
      if (rawLlp) {
        var parsedLlp = JSON.parse(rawLlp);
        var cleanT = ticker.replace(/\.(NS|BO)$/i, '');
        cachedLive = parsedLlp[ticker] || parsedLlp[cleanT];
      }
    } catch (e) {}

    var liveToApply = (cachedLive && cachedLive.current && cachedLive.current > 0) ? cachedLive : (window.lastLivePrice != null && window.lastLivePrice > 0 ? { current: window.lastLivePrice } : null);

    window._pendingRange = null;

    if (_isMarketOpen && liveToApply && liveToApply.current > 0) {
      processBigChartPrice(liveToApply, ticker);
    } else {
      var initialDisplayPrice = last.close;
      var pEl = document.getElementById('header-price');  if (pEl) pEl.innerText = fmtPrice(initialDisplayPrice);
      var cpEl = document.getElementById('chart-ticker-price'); if (cpEl) cpEl.innerText = fmtPrice(initialDisplayPrice);
      var chEl3 = document.getElementById('chart-change');
      if (chEl3 && unique.length > 1) {
        var pc3 = unique[unique.length - 2].close;
        if (pc3 > 0) {
          var d3 = initialDisplayPrice - pc3;
          var p3 = (d3 / pc3) * 100;
          chEl3.innerText = (d3 >= 0 ? '+ ' : '- ') + Math.abs(d3).toFixed(2) + ' (' + (d3 >= 0 ? '+' : '') + p3.toFixed(2) + '%)';
          var col3 = d3 >= 0 ? '#089981' : '#f23645';
          chEl3.style.color = col3;
          if (cpEl) cpEl.style.color = col3;
          if (pEl) pEl.style.color = col3;
        }
      }
      var oEl = document.getElementById('ohlc-open');     if (oEl) { oEl.innerText = last.open.toFixed(2); oEl.style.color = '#d1d4dc'; }
      var hEl = document.getElementById('ohlc-high');     if (hEl) { hEl.innerText = last.high.toFixed(2); hEl.style.color = '#089981'; }
      var lEl = document.getElementById('ohlc-low');      if (lEl) { lEl.innerText = last.low.toFixed(2); lEl.style.color = '#f23645'; }
      var cEl = document.getElementById('ohlc-close');    if (cEl) { cEl.innerText = last.close.toFixed(2); cEl.style.color = '#d1d4dc'; }
      var volEl = document.getElementById('ohlc-volume');
      if (volEl) {
        volEl.innerText = last.volume ? Number(last.volume).toLocaleString('en-IN') : '--';
        volEl.style.color = '#d1d4dc';
      }
    }
  }

  function aggregateToDaily(candles) {
    var dailyMap = {};
    candles.forEach(function(c) {
      var dayStart = Math.floor(c.time / 86400) * 86400;
      if (!dailyMap[dayStart]) {
        dailyMap[dayStart] = { time: dayStart, open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume || 0 };
      } else {
        var d = dailyMap[dayStart];
        d.high = Math.max(d.high, c.high);
        d.low = Math.min(d.low, c.low);
        d.close = c.close;
        d.volume = (d.volume || 0) + (c.volume || 0);
      }
    });
    return Object.values(dailyMap).sort(function(a, b) { return a.time - b.time; });
  }

  var _loadId = 0;
  var _loadController = null;
  var MAX_LOAD_RETRIES = 3;
  var LOAD_RETRY_DELAYS_MS = [2000, 5000, 10000];

  // Returns true if a retry was scheduled (caller should suppress its own
  // error UI for this attempt), false if retries are exhausted or this load
  // has already been superseded by a newer one (range switch / page nav).
  function _retryLoadData(range, attempt, myLoadId) {
    if (attempt >= MAX_LOAD_RETRIES) return false;
    if (myLoadId !== _loadId) return false;
    var delay = LOAD_RETRY_DELAYS_MS[attempt] || LOAD_RETRY_DELAYS_MS[LOAD_RETRY_DELAYS_MS.length - 1];
    setTimeout(function () {
      if (myLoadId !== _loadId) return; // superseded while we were waiting
      window.loadData(range, attempt + 1);
    }, delay);
    return true;
  }

  // BUGFIX (candle-build delay/stall): window._loadedRange only gets set by
  // _renderChartData() on a SUCCESSFUL fetch. If the initial fetch for a
  // freshly-opened ticker fails (network blip, non-200, etc — plausible
  // since a cold ticker's first view kicks off the backend's lazy-recovery
  // REST call), _loadedRange never gets set, and processBigChartPrice's own
  // `activeRange !== window._loadedRange` gate (used to decide whether a
  // live price tick should update the forming candle) then permanently
  // blocks candle-building for the rest of the page session -- the price
  // text keeps updating live (that path doesn't check _loadedRange) while
  // the candle bar never appears, with no self-correction. Retrying here
  // (bounded, not called for an intentional AbortError, and only when this
  // is still the most recently requested load) closes that gap without
  // changing behavior for the already-common success path.
  window.loadData = async function (range, _retryAttempt) {
    _retryAttempt = _retryAttempt || 0;
    window._pendingRange = range;
    _updateURLParam('range', range);
    document.querySelectorAll('.range-item, .range-selector button').forEach(function(el) {
      var r = el.getAttribute('data-range') || el.textContent;
      if (r === '1H') r = '1h';
      if (r === range) {
        el.classList.add('active');
      } else {
        el.classList.remove('active');
      }
    });
    // Don't nullify _lastCandleClose — keep previous value so forming candle
    // can still render during loading (header/OHLC text updates continuously).
    var loader2 = document.getElementById('leverage-loader');
    if (loader2) loader2.style.display = 'flex';
    hideChartError();

    // Cancel previous in-flight request
    if (_loadController) try { _loadController.abort(); } catch (e) {}
    var myLoadId = ++_loadId;
    _loadController = new AbortController();
    var signal = _loadController.signal;

    // Resolve interval from range for cache key
    var cacheInterval = range;

    // 3-tier cache: memory → IndexedDB → API (stale-while-revalidate)
    // NOTE: For intraday ranges, skip cache so backend gap-fill always triggers on chart open.
    // 1W / 1M also skip IDB cache — gap-fill data changes daily, old cache shows wrong history.
    var isIntradayRange = ['1m','5m','15m','30m','1h'].indexOf(range) !== -1;
    var isWeeklyMonthly = (range === '1W' || range === '1M');
    var memKey = ticker + '|' + cacheInterval + '|' + range;
    var hasRenderedPreview = false;

    var memData = (!isIntradayRange && !isWeeklyMonthly && window._recentRanges) ? window._recentRanges.get(memKey) : null;
    if (memData) {
      _renderChartData(memData, range);
      hasRenderedPreview = true;
      if (loader2) loader2.style.display = 'none';
      var le = document.getElementById('loading'); if (le) le.style.display = 'none';
    } else if (!isIntradayRange && !isWeeklyMonthly && window._idbGetCandles) {
      try {
        var idbData = await window._idbGetCandles(ticker, cacheInterval, range);
        if (idbData && myLoadId === _loadId) {
          if (window._recentRanges) window._recentRanges.set(memKey, idbData);
          _renderChartData(idbData, range);
          hasRenderedPreview = true;
          if (loader2) loader2.style.display = 'none';
          var le2 = document.getElementById('loading'); if (le2) le2.style.display = 'none';
        }
      } catch (e) {}
    }

    try {
      var url = '/api/stock-data/range?ticker=' + encodeURIComponent(ticker) + '&range=' + range;
      if (['1m','5m','15m','30m','1h'].indexOf(range) !== -1)         url = '/api/stock-data/intraday/paginated?ticker=' + encodeURIComponent(ticker) + '&interval=' + range + '&limit=' + INTRADAY_PAGE_BARS;
      else if (range === '1D')                                         url = '/api/stock-data/range?ticker=' + encodeURIComponent(ticker) + '&range=ALL';
      else if (range === '1W')                                         url = '/api/stock-data/weekly?ticker=' + encodeURIComponent(ticker);
      else if (range === '1M')                                         url = '/api/stock-data/monthly?ticker=' + encodeURIComponent(ticker);

      var res  = await fetch(url, { signal: signal });
      if (myLoadId !== _loadId) { hideLoader(loader2); return; }
      if (!res.ok) {
        if (_retryLoadData(range, _retryAttempt, myLoadId)) return;
        showChartError('Server returned ' + res.status + ' for ' + range + ' data');
        return;
      }
      var data = await res.json();
      if (data && data.data) { data = data.data; }

      if (!Array.isArray(data)) {
        showChartError('Unexpected response format for ' + range + ' data');
        return;
      }
      if (myLoadId !== _loadId) { hideLoader(loader2); return; }
      _renderChartData(data, range);
      // Write to caches on successful API fetch (skip for intraday to avoid stale gaps)
      try {
        if (!isIntradayRange && !isWeeklyMonthly && window._recentRanges) window._recentRanges.set(memKey, data);
        if (!isIntradayRange && !isWeeklyMonthly && window._idbSetCandles) window._idbSetCandles(ticker, cacheInterval, range, data);
      } catch (e) {}
    } catch (e) {
      if (e.name === 'AbortError') { hideLoader(loader2); return; }
      if (_retryLoadData(range, _retryAttempt, myLoadId)) return;
      showChartError('Error loading ' + range + ' data: ' + e.message);
    }
    finally {
      window._pendingRange = null;
      if (loader2) loader2.style.display = 'none';
      var loadingEl = document.getElementById('loading');
      if (loadingEl) loadingEl.style.display = 'none';
      var cachedLiveFinal = null;
      try {
        var rawLlpFinal = localStorage.getItem('llp');
        if (rawLlpFinal) {
          var parsedLlpFinal = JSON.parse(rawLlpFinal);
          var cleanTFinal = ticker.replace(/\.(NS|BO)$/i, '');
          cachedLiveFinal = parsedLlpFinal[ticker] || parsedLlpFinal[cleanTFinal];
        }
      } catch(e) {}
      var finalLive = (cachedLiveFinal && cachedLiveFinal.current) ? cachedLiveFinal : (window.lastLivePrice ? { current: window.lastLivePrice } : null);
      if (finalLive && finalLive.current > 0) {
        processBigChartPrice(finalLive, ticker);
      }
    }
  };

  function hideLoader(el) { if (el) el.style.display = 'none'; var le = document.getElementById('loading'); if (le) le.style.display = 'none'; }

  // Activate the correct range button from URL
  document.querySelectorAll('.range-item').forEach(function(el) {
    el.classList.toggle('active', el.textContent === initialRange);
    if (el.textContent === initialRange && document.getElementById('currentRangeText')) {
      document.getElementById('currentRangeText').innerText = initialRange;
    }
  });
  loadData(initialRange).catch(function (e) { showChartError('Initial load error: ' + e.message); });

  // Seed price & gain from localStorage cache immediately (same source as dashboard)
  try {
    var cachedRaw = localStorage.getItem('llp');
    var cachedTs  = parseInt(localStorage.getItem('llp_ts') || '0', 10);
    if (cachedRaw && (Date.now() - cachedTs < 300000)) {
      var cachedPrices = JSON.parse(cachedRaw);
      var cleanTicker = ticker.replace(/\.(NS|BO)$/i, '');
      var cachedLive = cachedPrices[ticker] || cachedPrices[cleanTicker];
      if (cachedLive && cachedLive.current) {
        processBigChartPrice(cachedLive, ticker);
      }
    }
  } catch (e) {}

  // Fetch live price immediately (before 5s HTTP poll) so gain shows right away
  setTimeout(async function () {
    try {
      var res = await fetch('/api/live-prices', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tickers: [ticker] })
      });
      var data = await res.json();
      var cleanTicker = ticker.replace(/\.(NS|BO)$/i, '');
      var liveData = data[ticker] || data[cleanTicker];
      if (liveData && liveData.current) {
        processBigChartPrice(liveData, ticker);
      }
    } catch (e) { console.error('Initial live price fetch error:', e); }
  }, 50);

  function hideLoadingEl() {
    var el = document.getElementById('loading');
    if (el) el.style.display = 'none';
  }
  hideLoadingEl();

  refreshMarketStatus();
  setInterval(refreshMarketStatus, 60000);

  setInterval(function() {
    var ageEl = document.getElementById('ws-tick-age');
    if (!ageEl || !window._wsLastTickTime || !window._marketOpen) {
      if (ageEl) ageEl.textContent = '';
      return;
    }
    var sec = Math.round((Date.now() - window._wsLastTickTime) / 1000);
    ageEl.textContent = sec < 5 ? '' : sec < 60 ? sec + 's ago' : Math.round(sec / 60) + 'm ago';
  }, 3000);

  // Start WebSocket for real-time updates
  if (window.DashboardWS) {
    window.DashboardWS.start([ticker]);
    window.DashboardWS.addTickers([ticker]);
    // Remove previous listener to avoid leaks on re-init
    if (window._bigChartWsHandler) {
      window.removeEventListener('dashboard_price_update', window._bigChartWsHandler);
    }
    window._bigChartWsHandler = function wsBigChart(evt) {
      var detail = evt.detail;
      var prices = detail && detail.prices ? detail.prices : detail;
      if (!prices) return;
      // Normalize ticker: AngelOne WS broadcasts without .NS/.BO suffix
      var cleanTicker = ticker.replace(/\.(NS|BO)$/i, '');
      var wsLive = prices[ticker] || prices[cleanTicker] || prices[ticker.toUpperCase()] || prices[cleanTicker.toUpperCase()];
      if (!wsLive || !wsLive.current) return;
      var serverTs = (detail && detail.serverTs) ? detail.serverTs : null;
      // Detect WS reconnect: only repair intraday forming candles during active market hours
      var now = Date.now();
      var activeRange = window._loadedRange;
      var SESSION_START_MIN = 9 * 60 + 15;
      var SESSION_END_MIN   = 15 * 60 + 30;
      var _nowForGate = getIstNow();
      var _istDay = _nowForGate.getDay();
      var _istMin = _nowForGate.getHours() * 60 + _nowForGate.getMinutes();
      var _isMarketOpen = (_istDay >= 1 && _istDay <= 5 && _istMin >= SESSION_START_MIN && _istMin < SESSION_END_MIN);
      var isIntradayRange = ['1m','3m','5m','10m','15m','30m','1h','2h','4h'].indexOf(activeRange) !== -1;

      // ── WS reconnect / gap reconciliation ───────────────────────────────
      // The previous implementation fetched /api/stock-data/intraday/since and
      // merged the returned RAW EPOCH bars straight into _chartDataCache. But
      // _chartDataCache holds intraday bars as SEQUENTIAL INDICES (i * barSecs,
      // produced by _buildIntradayDisplay), so the two numeric domains never
      // aligned: missed candles were not placed on the loaded timeline, and the
      // forming-candle seed lookup (index barTime vs epoch last-bar time) failed,
      // re-opening the running candle at the first post-reconnect tick.
      //
      // Reuse the exact reconciliation every other load already uses —
      // loadData() → /api/stock-data/intraday/paginated (server-side gap
      // detection + backfill + authoritative forming candle) →
      // _renderChartData() → _buildIntradayDisplay() (index remap) — instead of
      // maintaining a second, divergent merge path. Triggered only when two
      // consecutive server ticks straddle a candle boundary -- i.e. a completed
      // candle may have been missed -- so a silence that stays inside the same
      // bucket does not reload the chart.
      var _barSecsByRange = { '1m': 60, '3m': 180, '5m': 300, '10m': 600, '15m': 900, '30m': 1800, '1h': 3600, '2h': 7200, '4h': 14400 };
      var _activeBarSecs = _barSecsByRange[activeRange];
      // The WS delivers `serverTs` as an ISO-8601 datetime string (the
      // backend's msg.ts), NOT epoch milliseconds -- normalize to a number
      // before any bucket arithmetic. (Regression guard: doing numeric math on
      // the raw string yielded NaN, and `NaN !== NaN` made the gate true on
      // EVERY tick, which continuously reloaded the chart during market hours.)
      var _serverMs = (typeof serverTs === 'number') ? serverTs : Date.parse(serverTs);
      if (_isMarketOpen && isIntradayRange && _activeBarSecs && _serverMs &&
          window._lastTickServerTs && typeof window.loadData === 'function') {
        // Reconcile when the silence between two consecutive server ticks
        // CROSSED a candle boundary -- that is precisely when a completed
        // candle may have been missed. A shorter silence staying inside the
        // same bucket needs no reload (the running candle simply continues).
        // Using the previous tick's SERVER timestamp (not the client clock)
        // keeps this deterministic and immune to client clock skew; a fixed
        // ">= one full bar" threshold was found (via the reconnect E2E) to
        // miss cases where e.g. a 4-minute gap on a 5m chart spans a boundary.
        var _barMs = _activeBarSecs * 1000;
        var _IST_OFF = 5.5 * 3600 * 1000;
        var _sessStartMin = 9 * 60 + 15; // NSE opens 09:15 IST
        var _bucketOf = function (ms) {
          var dayStart = Math.floor((ms + _IST_OFF) / 86400000) * 86400000 - _IST_OFF;
          var sessStart = dayStart + _sessStartMin * 60000;
          var o = ms - sessStart;
          return o < 0 ? sessStart : sessStart + Math.floor(o / _barMs) * _barMs;
        };
        if (_bucketOf(_serverMs) !== _bucketOf(window._lastTickServerTs)) {
          window.loadData(activeRange).catch(function () {});
        }
      }
      window._lastPriceUpdateMs = now;
      window._lastTickServerTs = _serverMs || window._lastTickServerTs;
      if (wsLive.current !== undefined) {
        processBigChartPrice({
          current: wsLive.current,
          open: wsLive.open,
          high: wsLive.high,
          low: wsLive.low,
          prev_close: wsLive.prev_close,
          volume: wsLive.volume
        }, ticker, serverTs);
      }
    };
    window.addEventListener('dashboard_price_update', window._bigChartWsHandler);
  }

  // Shared live price update handler for both WS and HTTP poll
  function getIstNow() {
    var d = new Date();
    var utc = d.getTime() + d.getTimezoneOffset() * 60000;
    var off = window._serverClockOffset;
    var offset = (off != null && !isNaN(off)) ? off : 0;
    return new Date(utc + 330 * 60000 + offset);
  }

  function processBigChartPrice(live, tkr, serverTs) {
    window._wsLastTickTime = Date.now();
    var now = serverTs ? new Date(serverTs) : getIstNow();
    var dayStr = now.getFullYear() + '-' + String(now.getMonth()+1).padStart(2,'0') + '-' + String(now.getDate()).padStart(2,'0');
    var pEl2 = document.getElementById('header-price'); var cpEl2 = document.getElementById('chart-ticker-price');
    var oEl2 = document.getElementById('ohlc-open');
    var hEl2 = document.getElementById('ohlc-high');
    var lEl2 = document.getElementById('ohlc-low');
    var cEl2 = document.getElementById('ohlc-close');

    var price = Number(live.current);
    if (isNaN(price)) price = 0;

    // ── Update page title with live price ─────────────────────────
    var tickerName = document.getElementById('ticker-name');
    if (tickerName && document.title.indexOf('₹') === -1) {
      document.title = tickerName.innerText + ' ₹' + fmtPrice(price);
    }

    // ── Update OHLC display from live data ─────────────────────────
    // Only overwrite when live has valid (>0) values so chart-candle
    // OHLC is preserved when the data source (e.g. AngelOne WS for
    // indices) doesn't provide daily open/high/low.
    if (live.open != null && live.open > 0 && oEl2) { oEl2.innerText = live.open.toFixed(2); oEl2.style.color = '#d1d4dc'; }

    var vwapEl = document.getElementById('header-vwap');
    var ucEl = document.getElementById('header-uc');
    var lcEl = document.getElementById('header-lc');
    
    // Mock VWAP using (H+L+C)/3 if values exist, else fallback to current price
    if (vwapEl && live.current) {
        let vwapMock = live.current;
        if (live.high && live.low) {
            vwapMock = (live.high + live.low + live.current) / 3;
        }
        vwapEl.innerText = vwapMock.toFixed(2);
    }
    
    // Mock Circuit Limits (10% standard)
    if (live.prev_close && live.prev_close > 0) {
        if (ucEl) ucEl.innerText = (live.prev_close * 1.10).toFixed(2);
        if (lcEl) lcEl.innerText = (live.prev_close * 0.90).toFixed(2);
    }
    if (live.high != null && live.high > 0 && hEl2) { hEl2.innerText = live.high.toFixed(2); hEl2.style.color = '#089981'; }
    if (live.low  != null && live.low  > 0 && lEl2) { lEl2.innerText = live.low.toFixed(2); lEl2.style.color = '#f23645'; }
    if (cEl2) { cEl2.innerText = price.toFixed(2); cEl2.style.color = '#d1d4dc'; }
    var volEl2 = document.getElementById('ohlc-volume');
    if (volEl2) {
      volEl2.innerText = live.volume ? Number(live.volume).toLocaleString('en-IN') : '--';
      volEl2.style.color = '#d1d4dc';
    }

    if (pEl2) pEl2.innerText = fmtPrice(price);
    if (cpEl2) cpEl2.innerText = fmtPrice(price);

    var chEl = document.getElementById('header-change');
    var pClose = (live.prev_close != null && live.prev_close > 0) ? live.prev_close : ((live.open != null && live.open > 0) ? live.open : price);
    var color = '#d1d4dc';
    if (pClose > 0) {
      var chDiff = price - pClose;
      var chPct  = (chDiff / pClose) * 100;
      var absDiff = Math.abs(chDiff);
      color = chDiff >= 0 ? '#089981' : '#f23645';
      if (chEl) {
        if (absDiff < 0.005) {
          chEl.innerText = '0.00 (0.00%)';
          chEl.className = 'text-muted';
        } else {
          chEl.innerText = (chDiff >= 0 ? '+ ' : '- ') + absDiff.toFixed(2) + ' (' + (chDiff >= 0 ? '+' : '') + chPct.toFixed(2) + '%)';
          chEl.className = chDiff >= 0 ? 'text-green' : 'text-red';
        }
      }
    }
    if (pEl2) pEl2.style.color = color;
    if (cpEl2) cpEl2.style.color = color;

    // Update chart floating legend change
    var chartChEl = document.getElementById('chart-change');
    if (chartChEl && pClose > 0) {
      chartChEl.innerText = (chDiff >= 0 ? '+ ' : '- ') + absDiff.toFixed(2) + ' (' + (chDiff >= 0 ? '+' : '') + chPct.toFixed(2) + '%)';
      chartChEl.style.color = color;
    }

    // Flash animation on header price removed as requested
    if (pEl2) window.lastLivePrice = price;

    // Update sector tag
    var secEl = document.getElementById('sector-tag');
    if (secEl && live.sector) {
      var secText = live.sector;
      if (live.sectorPct != null) {
        var secSn = live.sectorPct >= 0 ? '+' : '';
        secText += ' (' + secSn + live.sectorPct.toFixed(2) + '%)';
      }
      secEl.textContent = secText;
      secEl.style.display = 'inline-block';
      secEl.style.color = live.sectorPct >= 0 ? '#089981' : '#f23645';
    } else if (secEl) {
      secEl.style.display = 'none';
    }

    // Always update header price even when chart is loading
    if (pEl2) {
      pEl2.innerText = fmtPrice(live.current);
      pEl2.style.color = color;
    }
    if (cEl2) cEl2.innerText = live.current.toFixed(2);

    // Skip forming candle chart series update while loadData is in progress
    // (still compute in-memory candle so it's ready when data loads)
    if (window._pendingRange) return;

    var activeRange = 'ALL';
    var activeEl = document.querySelector('.range-item.active, .range-selector button.active');
    if (activeEl) {
      activeRange = (activeEl.getAttribute('data-range') || activeEl.textContent || '').trim();
      if (activeRange === '1H') activeRange = '1h';
    }
    // The RENDERED range is authoritative. Some intraday ranges (e.g. 1m) have
    // no range-selector button, so relying on the active button alone silently
    // disabled the live forming candle for them (found by the reconnect E2E).
    if (window._loadedRange) activeRange = window._loadedRange;

    // Only build forming candle if the series data matches the active range
    if (activeRange !== window._loadedRange) return;

    if (activeRange !== window._lastFormingRange) {
      window._formingCandles = {};
      window._lastFormingRange = activeRange;
    }

    var SESSION_START_MIN = 9 * 60 + 15; // 555 min from IST midnight
    var SESSION_END_MIN   = 15 * 60 + 30; // 15:30 IST
    var IST_OFFSET_MS = 5.5 * 3600 * 1000;
    var utcMs = now.getTime();
    // Guard: only build forming candle during NSE market hours (Mon-Fri 09:15-15:30 IST).
    // Without this, an unnecessary candle appears at the current wall-clock time after market close
    // (e.g. on a Saturday or weekend) because WS still sends cached price ticks.
    var _istNow = new Date(utcMs + IST_OFFSET_MS);
    var _istDay = _istNow.getUTCDay(); // 0=Sun, 1=Mon…5=Fri, 6=Sat
    var _istMin = _istNow.getUTCHours() * 60 + _istNow.getUTCMinutes();
    if (_istDay === 0 || _istDay === 6 || _istMin < SESSION_START_MIN || _istMin >= SESSION_END_MIN) {
      return; // Market closed — skip forming candle update entirely for all timeframes (intraday and 1D/1W/1M)
    }

    var intervalMin = { '1m':1, '3m':3, '5m':5, '10m':10, '15m':15, '30m':30, '1h':60, '2h':120, '4h':240 }[activeRange];
    var isIntraday = intervalMin !== undefined;

    if (isIntraday) {
      var ms = intervalMin * 60 * 1000;
      // IST session-aligned candle snapping: NSE starts 09:15 IST
      // IST midnight in UTC epoch ms (works regardless of browser timezone)
      var istDayStartMs = (Math.floor((utcMs + IST_OFFSET_MS) / 86400000) * 86400000) - IST_OFFSET_MS;
      var sessionStartMs = istDayStartMs + SESSION_START_MIN * 60000;
      var offsetFromSession = utcMs - sessionStartMs;
      var snappedMs;
      if (offsetFromSession < 0) {
        snappedMs = sessionStartMs;
      } else {
        snappedMs = sessionStartMs + Math.floor(offsetFromSession / ms) * ms;
      }
      // snappedMs is UTC epoch ms; LW Charts wants UTC epoch seconds
      var snappedSec = Math.floor(snappedMs / 1000);
      // In sequential-time mode convert snappedSec → the bar's sequential index × barSecs
      var barTime = snappedSec;
      if (window._intradayBarTimes && window._intradayBarSecs) {
        if (!window._intradayBarSeqMap) {
          window._intradayBarSeqMap = {};
          window._intradayBarTimes.forEach(function(t, i) {
            window._intradayBarSeqMap[t] = i * window._intradayBarSecs;
          });
        }
        var _mapped = window._intradayBarSeqMap[snappedSec];
        if (_mapped !== undefined) {
          barTime = _mapped;
        } else {
          var _ni = window._intradayBarTimes.length;
          window._intradayBarTimes.push(snappedSec);
          barTime = _ni * window._intradayBarSecs;
          window._intradayBarSeqMap[snappedSec] = barTime;
        }
      }
      // P2.3: accumulate the per-tick day-volume delta onto the current forming
      // bucket (mirrors the backend aggregator), so the live volume bar reflects
      // THIS bucket rather than its load-time value (usually 0 at bucket start).
      if (!window._lastDayVolByTicker) window._lastDayVolByTicker = {};
      var _bv = _bucketVolumeDelta(window._lastDayVolByTicker[tkr], live.volume);
      window._lastDayVolByTicker[tkr] = _bv.base;
      var _volDelta = _bv.delta;
      if (!window._formingCandles) window._formingCandles = {};
      if (!window._formingCandles['i'] || window._formingCandles['i'].realTime !== snappedSec) {
        // FE-05: the candle about to be replaced just completed. Append it
        // to the same array drawing-core.js treats as the authoritative
        // source for anchor math (window._chartCandles / _chartDataCache),
        // kept in sync on initial load and pagination but never here --
        // without this, drawing anchors drift further out of sync with
        // what's on screen the longer a chart stays open.
        var justCompletedI = window._formingCandles['i'];
        if (justCompletedI && _chartDataCache) {
          var alreadyHaveI = _chartDataCache.length > 0 && _chartDataCache[_chartDataCache.length - 1].time === justCompletedI.time;
          if (!alreadyHaveI) {
            _chartDataCache.push({ time: justCompletedI.time, open: justCompletedI.open, high: justCompletedI.high, low: justCompletedI.low, close: justCompletedI.close, volume: justCompletedI.volume || 0 });
            window._chartCandles = _chartDataCache;
          }
        }
        if (window._lastCandleClose === null && _chartDataCache && _chartDataCache.length > 0) {
          window._lastCandleClose = _chartDataCache[_chartDataCache.length - 1].close;
        }
        var fallbackClose = (window._lastCandleClose !== null) ? window._lastCandleClose : live.current;
        // Bug-1 fix: only the FIRST bucket of the trading day opens at the
        // official day open; every later bucket opens at its own first traded
        // price. The previous check compared _chartDataCache's last bar time
        // against today's date — but for intraday ranges _chartDataCache stores
        // SEQUENTIAL bar indices (i * barSecs), not epoch seconds, so the
        // comparison always evaluated to "first bar" and EVERY 5m/15m/30m/1h
        // forming candle wrongly opened at the 09:15 day open (large phantom
        // body/wick that never matched the historical candles). Intraday bars
        // are session-anchored above, so the session's first bucket is exactly
        // sessionStartMs.
        var _isFirstBar = (snappedMs === sessionStartMs);
        // If the loaded data already contains the CURRENT bucket, the backend
        // merged its authoritative forming candle into the REST response — seed
        // from it instead of restarting at the current tick. Without this,
        // opening the chart mid-bucket resets the candle's open/high/low to the
        // price at load time instead of the bucket's true values.
        var _seed = (_chartDataCache && _chartDataCache.length > 0 &&
                     _chartDataCache[_chartDataCache.length - 1].time === barTime)
          ? _chartDataCache[_chartDataCache.length - 1] : null;
        var openPrice = _computeFormingOpen(_seed ? _seed.open : null, _isFirstBar, live.open, fallbackClose, live.current);
        var _initHigh = _seed ? Math.max(_seed.high, live.current) : Math.max(openPrice, live.current);
        var _initLow  = _seed ? Math.min(_seed.low,  live.current) : Math.min(openPrice, live.current);
        window._formingCandles['i'] = { time: barTime, realTime: snappedSec, open: openPrice, high: _initHigh, low: _initLow, close: live.current, volume: 0 };
      }
      var fc = window._formingCandles['i'];
      fc.high = Math.max(fc.high, live.current);
      fc.low  = Math.min(fc.low,  live.current);
      fc.close = live.current;
      // P2.3: keep the forming bucket's volume bar live (see _bucketVolumeDelta).
      if (typeof fc.volume !== 'number' || !isFinite(fc.volume)) fc.volume = 0;
      fc.volume += _volDelta;
      if (bigVolumeSeries) {
        try {
          bigVolumeSeries.update({ time: fc.time, value: fc.volume, color: fc.close >= fc.open ? 'rgba(8,153,129,0.3)' : 'rgba(242,54,69,0.3)' });
        } catch(e) {}
      }
      if (bigCandleSeries && fc) { try { bigCandleSeries.update(fc); } catch(e) { /* ignore benign transition update */ } }
      if (window.IndicatorEngine) window.IndicatorEngine.onCandleUpdate(fc);
      if (pEl2) pEl2.innerText = fmtPrice(live.current);
      if (oEl2) oEl2.innerText = fc.open.toFixed(2);
      if (hEl2) hEl2.innerText = fc.high.toFixed(2);
      if (lEl2) lEl2.innerText = fc.low.toFixed(2);
      if (cEl2) cEl2.innerText = live.current.toFixed(2);
    } else {
      // For 1W/1M, calculate period-start string instead of today's date
      var periodStr = dayStr;
      if (activeRange === '1W') {
        // Monday of current week
        var dayOfWeek = now.getDay(); // 0=Sun, 1=Mon, ...
        var monOffset = (dayOfWeek === 0 ? -6 : 1 - dayOfWeek); // go back to Monday
        var mon = new Date(now);
        mon.setDate(now.getDate() + monOffset);
        periodStr = mon.getFullYear() + '-' + String(mon.getMonth()+1).padStart(2,'0') + '-' + String(mon.getDate()).padStart(2,'0');
      } else if (activeRange === '1M') {
        periodStr = now.getFullYear() + '-' + String(now.getMonth()+1).padStart(2,'0') + '-01';
      }
      if (!window._formingCandles) window._formingCandles = {};
      // For daily, weekly, monthly we use chronological date strings YYYY-MM-DD
      if (!window._formingCandles['d'] || window._formingCandles['d'].time !== periodStr) {
        // FE-05: same fix as the intraday branch above -- persist the
        // just-completed 1D/1W/1M candle before it's replaced.
        var justCompletedD = window._formingCandles['d'];
        if (justCompletedD && _chartDataCache) {
          var alreadyHaveD = _chartDataCache.length > 0 && _chartDataCache[_chartDataCache.length - 1].time === justCompletedD.time;
          if (!alreadyHaveD) {
            _chartDataCache.push({ time: justCompletedD.time, open: justCompletedD.open, high: justCompletedD.high, low: justCompletedD.low, close: justCompletedD.close, volume: justCompletedD.volume || 0 });
            window._chartCandles = _chartDataCache;
          }
        }
        var prevFc = window._formingCandles['d'];
        var dOpen = (live.open != null && live.open > 0) ? live.open : (prevFc && prevFc.open > 0 ? prevFc.open : live.current);
        var dHigh = (live.high != null && live.high > 0) ? live.high : (prevFc && prevFc.high > 0 ? prevFc.high : live.current);
        var dLow  = (live.low  != null && live.low  > 0) ? live.low  : (prevFc && prevFc.low > 0 ? prevFc.low : live.current);
        window._formingCandles['d'] = { time: periodStr, open: dOpen, high: dHigh, low: dLow, close: live.current };
      }
      var fc = window._formingCandles['d'];
      fc.high = Math.max(fc.high, (live.high != null && live.high > 0) ? live.high : live.current);
      fc.low  = Math.min(fc.low,  (live.low  != null && live.low  > 0) ? live.low  : live.current);
      fc.close = live.current;
      if (bigCandleSeries) {
        try {
          bigCandleSeries.update(fc);
          if (window.IndicatorEngine) window.IndicatorEngine.onCandleUpdate(fc);
        } catch(e) {
          /* ignore benign transition update */
        }
      }
      // Update today's volume bar on the volume series (the whitespace bar that was
      // pre-seeded for today gets replaced with real volume data).
      if (bigVolumeSeries && live.volume) {
        try {
          bigVolumeSeries.update({
            time: fc.time,
            value: Number(live.volume),
            color: fc.close >= fc.open ? 'rgba(8,153,129,0.3)' : 'rgba(242,54,69,0.3)'
          });
        } catch(e) {}
      }
      if (pEl2) pEl2.innerText = fmtPrice(live.current);
      if (oEl2) oEl2.innerText = fc.open.toFixed(2);
      if (hEl2) hEl2.innerText = fc.high.toFixed(2);
      if (lEl2) lEl2.innerText = fc.low.toFixed(2);
      if (cEl2) cEl2.innerText = live.current.toFixed(2);
    }
  }
}

/* --- Advanced Charting Logic --- */
function calcSMA(data, period) {
  var sma = [];
  if (!data || !Array.isArray(data) || data.length < period) return sma;
  for (var i = 0; i < data.length; i++) {
    if (i < period - 1) continue;
    var sum = 0;
    var valid = true;
    for (var j = 0; j < period; j++) {
      var item = data[i - j];
      var val = (item && item.close != null) ? Number(item.close) : ((item && item.value != null) ? Number(item.value) : null);
      if (val == null || isNaN(val)) {
        valid = false;
        break;
      }
      sum += val;
    }
    if (valid && data[i] && data[i].time != null) {
      var avg = +(sum / period).toFixed(2);
      if (!isNaN(avg)) {
        sma.push({ time: data[i].time, value: avg });
      }
    }
  }
  return sma;
}

function updateChartMode(mode) {
  if (!window._niftyChart) return;
  
  var btnArea = document.getElementById("btnArea");
  var btnCandle = document.getElementById("btnCandle");
  
  if (mode === "Area") {
    window._niftyAreaSeries.applyOptions({ visible: true });
    window._niftyCandleSeries.applyOptions({ visible: false });
    if(btnArea) btnArea.classList.add("active");
    if(btnCandle) btnCandle.classList.remove("active");
  } else {
    window._niftyAreaSeries.applyOptions({ visible: false });
    window._niftyCandleSeries.applyOptions({ visible: true });
    if(btnCandle) btnCandle.classList.add("active");
    if(btnArea) btnArea.classList.remove("active");
  }
}

function toggleSMA() {
  var btnSMA = document.getElementById("btnSMA");
  if (!btnSMA || !window._niftySMASeries) return;
  
  var isActive = btnSMA.classList.contains("active");
  if (isActive) {
    window._niftySMASeries.applyOptions({ visible: false });
    btnSMA.classList.remove("active");
  } else {
    window._niftySMASeries.applyOptions({ visible: true });
    btnSMA.classList.add("active");
  }
}

document.addEventListener("DOMContentLoaded", function() {
  var btnArea = document.getElementById("btnArea");
  var btnCandle = document.getElementById("btnCandle");
  var btnSMA = document.getElementById("btnSMA");
  
  if (btnArea) btnArea.addEventListener("click", function() { updateChartMode("Area"); });
  if (btnCandle) btnCandle.addEventListener("click", function() { updateChartMode("Candle"); });
  if (btnSMA) btnSMA.addEventListener("click", toggleSMA);
});

async function fetchMarketSentiment() {
  // Cached: sentiment is derived from news and barely moves minute to minute,
  // so it should paint from cache on return visits rather than re-fetching.
  if (window.PageCache) {
    PageCache.fetch('/api/scanx/news/market-sentiment', null, 300000, function (data) {
      if (data) _applyMarketSentiment(data);
    });
    return;
  }
  try {
    var res = await fetch("/api/scanx/news/market-sentiment");
    if (!res.ok) return;
    var data = await res.json();
    _applyMarketSentiment(data);
  } catch (e) {
    console.error('fetchMarketSentiment error:', e);
  }
}

function _applyMarketSentiment(data) {
  try {
    var score = data.score;
    var label = data.label || "neutral";
    var summary = data.summary || "Market data is being processed.";

    var s = score != null ? score : 50;
    var labelColor = '#a1a1aa';
    if (label.toLowerCase() === 'positive' || label.toLowerCase() === 'bullish') labelColor = '#089981';
    else if (label.toLowerCase() === 'negative' || label.toLowerCase() === 'bearish') labelColor = '#f23645';
    var scoreColor = '#a1a1aa';
    if (s > 60) scoreColor = '#089981';
    else if (s < 40) scoreColor = '#f23645';

    // Update gauge elements: home.html static gauge OR dynamically created market-sentiment-container
    // Use IDs; create market-sentiment-container SVG once if absent
    var sLabel = document.getElementById("sentimentLabel");
    var sScore = document.getElementById("sentimentScore");
    var sSum = document.getElementById("sentimentSummary");
    var needle = document.getElementById("gaugeNeedle");
    var gaugeFill = document.getElementById("gaugeFill");
    var sentimentEl = document.getElementById("market-sentiment-container");

    if (sentimentEl && !needle) {
      // No static gauge on this page — create one inside the container (once)
      sentimentEl.innerHTML =
        '<div class="sentiment-box" style="margin-top:15px;padding:15px;background:#1a1e29;border-radius:8px;border:1px solid #2a2e39;">' +
          '<div class="sentiment-header" style="font-size:14px;font-weight:600;color:#d1d4dc;margin-bottom:10px;">MARKET SENTIMENT</div>' +
          '<div class="sentiment-body" style="display:flex;align-items:center;">' +
            '<svg class="sentiment-gauge" viewBox="0 0 100 50" style="width:120px;height:60px;overflow:visible;">' +
              '<defs><linearGradient id="gg2" x1="0%" x2="100%" y1="0%" y2="0%"><stop offset="0%" stop-color="var(--danger)"/><stop offset="50%" stop-color="#a1a1aa"/><stop offset="100%" stop-color="var(--success)"/></linearGradient></defs>' +
              '<path d="M 10 50 A 40 40 0 0 1 90 50" fill="none" stroke="#2a2e39" stroke-width="8"/>' +
              '<path class="gauge-fill" d="M 10 50 A 40 40 0 0 1 90 50" fill="none" stroke="url(#gg2)" stroke-width="8" stroke-linecap="round" stroke-dasharray="283" stroke-dashoffset="283"/>' +
              '<polygon class="gauge-needle" points="50,50 48,15 52,15" fill="#d1d4dc"/>' +
              '<circle cx="50" cy="50" r="5" fill="#d1d4dc"/>' +
            '</svg>' +
            '<div class="sentiment-text" style="margin-left:20px;">' +
              '<div class="sentiment-score-text" style="font-size:24px;font-weight:bold;color:' + scoreColor + ';">' + s + '</div>' +
              '<div class="sentiment-label-text" style="font-size:16px;text-transform:uppercase;color:' + labelColor + ';">' + escapeHTML(label) + '</div>' +
              '<div class="sentiment-summary" style="font-size:12px;color:#8a8a8a;margin-top:5px;">' + escapeHTML(summary) + '</div>' +
            '</div>' +
          '</div>' +
        '</div>';
      // Re-read references after creation
      sLabel = sentimentEl.querySelector('.sentiment-label-text');
      sScore = sentimentEl.querySelector('.sentiment-score-text');
      sSum = sentimentEl.querySelector('.sentiment-summary');
      needle = sentimentEl.querySelector('.gauge-needle');
      gaugeFill = sentimentEl.querySelector('.gauge-fill');
    }

    // Single update point — CSS transitions animate the transform/stroke-dashoffset changes
    if (sLabel) { sLabel.innerText = label; sLabel.style.color = labelColor; }
    if (sScore) { sScore.innerText = s; sScore.style.color = scoreColor; }
    if (sSum) sSum.innerText = summary;
    if (needle) needle.style.transform = "rotate(" + (-90 + (s / 100) * 180) + "deg)";
    if (gaugeFill) gaugeFill.style.strokeDashoffset = 283 - (s / 100) * 283;

    // Fallback for index.html #aiInsightsList — render gauge same as home.html
    var aiList = document.getElementById("aiInsightsList");
    if (aiList && !sLabel && !sentimentEl) {
      aiList.innerHTML =
        '<div class="sentiment-gauge-container">' +
          '<svg class="gauge-svg" viewBox="0 0 100 50">' +
            '<defs><linearGradient id="gaugeGradient" x1="0%" x2="100%" y1="0%" y2="0%"><stop offset="0%" stop-color="#ef5350"/><stop offset="50%" stop-color="#a1a1aa"/><stop offset="100%" stop-color="#26a69a"/></linearGradient></defs>' +
            '<path class="gauge-bg" d="M 10 50 A 40 40 0 0 1 90 50"/>' +
            '<path class="gauge-fill" d="M 10 50 A 40 40 0 0 1 90 50" style="stroke-dashoffset:' + (283 - (s / 100) * 283) + '"/>' +
            '<polygon class="gauge-needle" points="50,50 48,15 52,15" style="transform:rotate(' + (-90 + (s / 100) * 180) + 'deg);"/>' +
            '<circle cx="50" cy="50" r="4" fill="white"/>' +
          '</svg>' +
          '<div class="sentiment-text">' +
            '<div class="sentiment-score-text" style="color:' + scoreColor + ';">' + s + '</div>' +
            '<div class="sentiment-label-text" style="color:' + labelColor + ';">' + escapeHTML(label) + '</div>' +
            '<div class="sentiment-summary">' + escapeHTML(summary) + '</div>' +
          '</div>' +
        '</div>';
    }
  } catch(e) {
    console.error("Failed to fetch sentiment", e);
    var sLabel = document.getElementById("sentimentLabel");
    var sScore = document.getElementById("sentimentScore");
    var sSum = document.getElementById("sentimentSummary");
    if (sLabel) sLabel.innerText = "Unavailable";
    if (sScore) sScore.innerText = "--";
    if (sSum) sSum.innerText = "Sentiment data temporarily unavailable. Will retry automatically.";
  }
}

function _tickerColor(ticker) {
  var colors = ['#e74c3c','#3498db','#2ecc71','#9b59b6','#f39c12','#1abc9c','#e67e22','#2980b9','#27ae60','#8e44ad','#d35400','#16a085','#c0392b','#2c3e50','#7f8c8d','#34495e'];
  var hash = 0;
  for (var i = 0; i < ticker.length; i++) { hash = ((hash << 5) - hash) + ticker.charCodeAt(i); hash |= 0; }
  return colors[Math.abs(hash) % colors.length];
}

function renderMoverList(containerId, listData) {
  var container = document.getElementById(containerId);
  if (!container) return;
  if (containerId === 'gainersList' && Array.isArray(listData)) {
    listData = listData.filter(function(s) { return s && Number(s.change_pct) > 0; });
  } else if (containerId === 'losersList' && Array.isArray(listData)) {
    listData = listData.filter(function(s) { return s && Number(s.change_pct) < 0; });
  }
  if (!listData || listData.length === 0) {
    var msg = containerId === 'activeList' ? 'No volume data yet' : 'No data available';
    container.innerHTML = '<div style="color:var(--text-secondary);padding:1rem 0;text-align:center;">' + msg + '</div>';
    return;
  }
  var isActive = containerId === 'activeList';
  var html = '';
  listData.forEach(function(s) {
    var ticker = s.ticker || '';
    var cleanTicker = ticker.replace('.NS', '').replace('.BO', '').replace(/[^A-Za-z0-9]/g, '').toUpperCase();
    var rawPrice = (s.current_price != null) ? Number(s.current_price) : ((s.price != null) ? Number(s.price) : null);
    var changePct = s.change_pct != null ? Number(s.change_pct) : 0;
    var rawPrev = (s.prev_close != null) ? Number(s.prev_close) : null;
    // Derive prev_close from change_pct if not supplied directly
    if (rawPrev == null && rawPrice != null && changePct !== 0) {
      rawPrev = rawPrice / (1 + changePct / 100);
    }
    var priceStr = (rawPrice != null) ? rawPrice.toFixed(2) : '--';
    var prevClose = (rawPrev != null) ? rawPrev.toFixed(2) : '--';
    var absChange = (rawPrice != null && rawPrev != null) ? (rawPrice - rawPrev) : null;
    var absChangeStr = absChange != null ? (absChange >= 0 ? '+₹' : '-₹') + Math.abs(absChange).toFixed(2) : '';
    var cls = changePct >= 0 ? 'text-green' : 'text-red';
    var arrow = changePct >= 0 ? '▲' : '▼';
    var companyName = s.company_name || s.name || ticker;
    var volDisplay = (s.volume != null && !isNaN(Number(s.volume))) 
      ? formatVolumeCompact(s.volume) 
      : (s.volume_display ? formatVolumeCompact(s.volume_display) : '--');
    var sectorTag = s.sector ? '<span class="mover-sector-tag">' + s.sector + '</span>' : '';
    
    // Logo resolution: prefer s.logo if valid, else /logos/{cleanTicker}.svg with SVG onerror fallback to ticker initials
    var logoSrc = (s.logo && !s.logo.includes('gstatic.com') && !s.logo.includes('faviconV2')) ? s.logo : ('/logos/' + cleanTicker + '.svg');
    var c = _tickerColor(cleanTicker || ticker);
    var initialText = (cleanTicker || ticker).substring(0, 2).toUpperCase();
    var logoHtml = '<div class="mv-avatar" style="background:#ffffff;display:flex;align-items:center;justify-content:center;overflow:hidden;border:1px solid rgba(255,255,255,0.08);padding:2px;box-sizing:border-box;">' +
      '<img src="' + logoSrc + '" alt="' + cleanTicker + '" style="width:100%;height:100%;object-fit:contain;border-radius:50%;display:block;" onerror="this.onerror=null;this.parentElement.style.background=\'' + c + '\';this.parentElement.style.color=\'#ffffff\';this.parentElement.innerHTML=\'' + initialText + '\';">' +
      '</div>';

    var keyMetric = '<div class="mv-change mv-change-' + cleanTicker + ' ' + cls + '">' + arrow + ' ' + Math.abs(changePct).toFixed(2) + '%' +
      (absChangeStr ? ' <span class="mv-abs-change" style="white-space:nowrap;">(' + absChangeStr + ')</span>' : '') + '</div>' +
      '<div class="mv-volume mv-volume-' + cleanTicker + '">Volume: ' + volDisplay + '</div>';
    var isIndex = ['NIFTY','SENSEX','BANKNIFTY','FINNIFTY','MIDCAP','SMALLCAP','BSE500','NIFTYMIDCAP100','NIFTYSMLCAP100'].indexOf(cleanTicker) !== -1;
    var moverLink = isIndex ? '/overview.html?ticker=' : '/overview.html?ticker=';
    html += '<div class="mover-row mv-row-' + cleanTicker + '" id="mv-item-' + containerId + '-' + cleanTicker + '" data-ticker="' + cleanTicker + '" onclick="window.location.href=\'' + moverLink + encodeURIComponent(ticker) + '\'">' +
              '<div class="mover-left">' +
                logoHtml +
                '<div class="mover-info">' +
                  '<h4>' + ticker + (sectorTag ? ' ' + sectorTag : '') + '</h4>' +
                '</div>' +
              '</div>' +
              '<div class="mover-right">' +
                '<div class="mover-price mv-price-' + cleanTicker + '" id="mv-price-' + containerId + '-' + cleanTicker + '">\u20B9' + priceStr + '</div>' +
                keyMetric +
                '<div class="mover-prev mv-prev-' + cleanTicker + '" id="mv-prev-' + containerId + '-' + cleanTicker + '">Prev: \u20B9' + prevClose + '</div>' +
              '</div>' +
            '</div>';
  });
  container.innerHTML = html;
}

window._moverCapCache = {};
window._currentMoverCap = 'all';

window.setMoverCap = function (cap) {
  var targetCap = cap || 'all';
  window._currentMoverCap = targetCap;
  
  // 1. Instantly update active tab UI
  var tabs = document.querySelectorAll('.mover-tab');
  tabs.forEach(function (tab) {
    if (tab.getAttribute('data-cap') === targetCap) {
      tab.classList.add('active');
    } else {
      tab.classList.remove('active');
    }
  });

  // 2. Instantly render from client cache (0ms perceived latency!)
  if (window._moverCapCache && window._moverCapCache[targetCap]) {
    onMarketMovers({ detail: window._moverCapCache[targetCap] });
  }

  // 3. Fetch latest in background to keep data completely fresh
  fetchMarketMovers(targetCap, true);
};

async function fetchMarketMovers(cap, bypassCache) {
  var selectedCap = cap || window._currentMoverCap || 'all';
  var endpoint = '/api/market-movers?cap=' + encodeURIComponent(selectedCap);
  
  if (bypassCache) {
    try {
      var res = await fetch(endpoint);
      if (res.ok) {
        var data = await res.json();
        if ((data.cap || 'all') === (window._currentMoverCap || 'all')) {
          onMarketMovers({ detail: data });
        }
        return;
      }
    } catch (e) {}
  }

  // Routed through PageCache so returning to the dashboard paints the movers
  // instantly from the last response, then silently re-renders when the fresh
  // one lands.
  if (window.PageCache) {
    PageCache.fetch(endpoint, null, 15000, function (data) {
      if (data && (data.cap || 'all') === (window._currentMoverCap || 'all')) {
        onMarketMovers({ detail: data });
      }
    });
    return;
  }
  try {
    var res = await fetch(endpoint);
    if (res.ok) {
      var data = await res.json();
      if ((data.cap || 'all') === (window._currentMoverCap || 'all')) {
        onMarketMovers({ detail: data });
      }
    }
  } catch (e) {
    console.error('fetchMarketMovers error:', e);
  }
}

var _fetchingWatchlist = false;
async function fetchWatchlist() {
  if (_fetchingWatchlist) return;
  _fetchingWatchlist = true;
  var container = document.getElementById('watchlistContainer');
  if (!container) { _fetchingWatchlist = false; return; }

  var DEFAULT_WATCHLIST = ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK'];
  var tickers = DEFAULT_WATCHLIST.slice();
  var wlItems = [];

  try {
    var _wlToken = '';
    try { _wlToken = sessionStorage.getItem('token') || localStorage.getItem('token') || ''; } catch(e) {}
    var headers = {};
    if (_wlToken && _wlToken !== 'null' && _wlToken !== 'undefined' && _wlToken.trim() !== '') {
      headers['Authorization'] = 'Bearer ' + _wlToken.trim();
    }
    var res = await fetch('/api/watchlist', { headers: headers });
    if (res.ok) {
      var data = await res.json();
      if (data && Array.isArray(data.watchlist) && data.watchlist.length > 0) {
        wlItems = data.watchlist;
        tickers = wlItems.map(function(i) { return i.ticker || i; }).slice(0, 5);
        // Keep localStorage cache in sync so dashboard works even if API fails
        try { localStorage.setItem('_wl_cache', JSON.stringify(tickers)); } catch(e) {}
        var _fetchedFromApi = true;
      }
    }
  } catch(e) {
    // Not logged in or network error — try cache below
  }

  // Fallback: localStorage cache from watchlist page when API fails
  if (!_fetchedFromApi) try {
    var cached = JSON.parse(localStorage.getItem('_wl_cache') || '[]');
    if (cached.length > 0) {
      tickers = cached.slice(0, 5);
      wlItems = tickers.map(function(t) { return { ticker: t }; });
    }
  } catch(e) {}

  // Look up logos from ALL_STOCKS for items that don't have one yet
  if (typeof ALL_STOCKS !== 'undefined') {
    tickers.forEach(function(t, idx) {
      if (!wlItems[idx] || !wlItems[idx].logo) {
        var match = ALL_STOCKS.find(function(s) { return s.ticker === t; });
        if (match && match.logo && !match.logo.includes('gstatic.com') && !match.logo.includes('faviconV2')) {
          if (!wlItems[idx]) wlItems[idx] = { ticker: t };
          wlItems[idx].logo = match.logo;
        }
      }
    });
  }

  var html = tickers.map(function(t, idx) {
    var item = wlItems[idx] || {};
    var tkr = item.ticker || t;
    var logo = item.logo || '';
    var validLogo = logo && !logo.includes('gstatic.com') && !logo.includes('faviconV2');
    var logoHtml;
    if (validLogo) {
      var c2 = _tickerColor(tkr);
      logoHtml = '<div class="wl-avatar" style="background:' + c2 + ';border:none;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:600;color:#fff;overflow:hidden;"><img src="' + logo + '" alt="" style="width:28px;height:28px;border-radius:4px;object-fit:contain;display:block;" onerror="this.parentElement.style.fontSize=\'0\'"></div>';
    } else {
      logoHtml = '<div class="wl-avatar" style="background:' + _tickerColor(tkr) + ';font-size:0;"></div>';
    }
    var isIdx = ['NIFTY','SENSEX','BANKNIFTY','FINNIFTY','MIDCAP','SMALLCAP','BSE500','NIFTYMIDCAP100','NIFTYSMLCAP100'].indexOf(tkr) !== -1;
    var wlLink = isIdx ? '/overview.html?ticker=' : '/overview.html?ticker=';
    return '<div class="watchlist-card" onclick="window.location.href=\'' + wlLink + encodeURIComponent(tkr) + '\'">' +
              logoHtml +
              '<div class="wl-body">' +
                '<div class="wl-symbol">' + tkr + '</div>' +
                '<div class="wl-exchange">' + (isIdx ? 'Index' : (tkr.indexOf('.BO') !== -1 ? 'BSE' : 'NSE')) + '</div>' +
                '<div class="wl-price" id="wl-price-' + tkr + '">\u20B9--</div>' +
                '<div class="wl-change" id="wl-change-' + tkr + '"></div>' +
              '</div>' +
            '</div>';
  }).join('');
  container.innerHTML = html;

  // Seed watchlist from cached prices immediately so they never show "₹--"
  try {
    var cached = JSON.parse(localStorage.getItem('llp') || 'null');
    var cachedTs = parseInt(localStorage.getItem('llp_ts') || '0', 10);
    if (cached && (Date.now() - cachedTs < 300000) && (new Date(cachedTs).toDateString() === new Date().toDateString())) {
      Object.keys(cached).forEach(function(tkr) {
        var pEl2 = document.getElementById('wl-price-' + tkr);
        var chEl = document.getElementById('wl-change-' + tkr);
        var d = cached[tkr];
        if (pEl2 && d && d.current != null) {
          var prev = (d.prev_close != null && d.prev_close > 0) ? d.prev_close : ((d.open != null && d.open > 0) ? d.open : d.current);
          var dif = d.current - prev;
          var pc = prev > 0 ? (dif / prev) * 100 : 0;
          var sg = dif >= 0 ? '▲ +' : '▼ ';
          var col = dif >= 0 ? 'text-green' : 'text-red';
          pEl2.innerText = '\u20B9' + Number(d.current).toLocaleString('en-IN', { minimumFractionDigits: 2 });
          if (chEl) {
            chEl.innerText = sg + Math.abs(dif).toFixed(2) + ' (' + Math.abs(pc).toFixed(2) + '%)';
            chEl.className = 'wl-change ' + col;
          }
        }
      });
    }
  } catch(e) { /* non-critical */ }

  // Subscribe watchlist tickers to WS for live updates
  if (window.DashboardWS) window.DashboardWS.addTickers(tickers);

  // Fallback: fetch live prices via REST in case WS hasn't broadcast yet
  if (tickers.length > 0) {
    fetch('/api/live-prices', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({tickers:tickers}) })
      .then(function(r){ return r.json(); })
      .then(function(data){
        if (!data || typeof data !== 'object') return;
        tickers.forEach(function(tkr){
          var d = data[tkr];
          if (!d || d.current == null) return;
          var pEl = document.getElementById('wl-price-' + tkr);
          var chEl = document.getElementById('wl-change-' + tkr);
          if (!pEl) return;
          var prev = (d.prev_close != null && d.prev_close > 0) ? d.prev_close : ((d.open != null && d.open > 0) ? d.open : d.current);
          var dif = d.current - prev;
          var pc = prev > 0 ? (dif / prev) * 100 : 0;
          var sg = dif >= 0 ? '\u25B2 +' : '\u25BC ';
          var col = dif >= 0 ? 'text-green' : 'text-red';
          pEl.innerText = '\u20B9' + Number(d.current).toLocaleString('en-IN', { minimumFractionDigits: 2 });
          if (chEl) {
            chEl.innerText = sg + Math.abs(dif).toFixed(2) + ' (' + Math.abs(pc).toFixed(2) + '%)';
            chEl.className = 'wl-change ' + col;
          }
        });
        try {
          var existing = JSON.parse(localStorage.getItem('llp') || '{}');
          var merged = Object.assign({}, existing, data);
          localStorage.setItem('llp', JSON.stringify(merged));
          localStorage.setItem('llp_ts', Date.now().toString());
        } catch(e) {}
      })
      .catch(function(){ /* non-critical */ });
  }
  _fetchingWatchlist = false;
}

/* ==========================================
   PRICE ALERTS SYSTEM
   ========================================== */
window._priceAlerts = [];

function loadAlerts() {
  try {
    var raw = localStorage.getItem('priceAlerts');
    if (raw) window._priceAlerts = JSON.parse(raw);
  } catch(e) {}
}

function saveAlerts() {
  try { localStorage.setItem('priceAlerts', JSON.stringify(window._priceAlerts)); } catch(e) {}
}

window.addPriceAlert = function(ticker, targetPrice, direction) {
  var id = Date.now() + '_' + Math.random().toString(36).slice(2, 6);
  window._priceAlerts.push({ id: id, ticker: ticker, target: targetPrice, dir: direction || 'above', triggered: false, createdAt: Date.now() });
  saveAlerts();
  return id;
};

window.removePriceAlert = function(id) {
  window._priceAlerts = window._priceAlerts.filter(function(a) { return a.id !== id; });
  saveAlerts();
};

function checkAlerts(prices) {
  if (!window._priceAlerts || window._priceAlerts.length === 0) return;
  Object.keys(prices).forEach(function(ticker) {
    var d = prices[ticker];
    if (!d || !d.current) return;
    var price = d.current;
    window._priceAlerts.forEach(function(alert) {
      if (alert.triggered) return;
      if (alert.ticker !== ticker) return;
      var fired = false;
      if (alert.dir === 'above' && price >= alert.target) fired = true;
      if (alert.dir === 'below' && price <= alert.target) fired = true;
      if (fired) {
        alert.triggered = true;
        saveAlerts();
        if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
          new Notification('Price Alert: ' + ticker, {
            body: ticker + ' is now ' + (alert.dir === 'above' ? 'above' : 'below') + ' ₹' + alert.target + ' (₹' + price.toFixed(2) + ')',
            icon: '/logo.jpeg'
          });
        }
      }
    });
  });
}

// Hook into WS price updates
(function() {
  var _origHandler = window.addEventListener;
  window.addEventListener('dashboard_price_update', function(evt) {
    var raw = evt.detail;
    if (!raw) return;
    var prices = raw.prices || raw;
    checkAlerts(prices);
  });
  loadAlerts();
  // Request notification permission
  if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
    Notification.requestPermission();
  }
})();

/* ==========================================
   AUTO-INIT: chart pages
   ========================================== */
if (document.getElementById('chart')) {
  initBigChart().catch(function (e) { console.error('initBigChart error:', e); });
}
/* Auth check for pages with hardcoded nav (chart pages) */
(function checkAuth() {
  var token, userStr;
  try { token = sessionStorage.getItem('token'); } catch(e) {}
  try { userStr = sessionStorage.getItem('user'); } catch(e) {}
  var authButtons = document.getElementById('authButtons');
  var loginBtn = document.getElementById('loginBtn');
  var userInfo = document.getElementById('userInfo');
  if (!authButtons && !loginBtn && !userInfo) return;
  function setLoggedIn(show) {
    if (authButtons) authButtons.style.display = show ? 'none' : 'flex';
    if (loginBtn) loginBtn.style.display = show ? 'none' : 'inline-flex';
    if (userInfo) userInfo.style.display = show ? 'flex' : 'none';
  }
  if (token && userStr) {
    try {
      var user = JSON.parse(userStr);
      setLoggedIn(true);
      var balEl = document.getElementById('userBalance');
      if (balEl) balEl.textContent = '\u20B9' + Number(user.virtual_balance || 0).toLocaleString('en-IN', { minimumFractionDigits: 2 });
    } catch(e) {
      setLoggedIn(false);
    }
  } else {
    setLoggedIn(false);
  }
})();
