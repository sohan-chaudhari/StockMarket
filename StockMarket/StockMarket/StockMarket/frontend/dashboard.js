/* ==========================================
   DASHBOARD SPECIFIC LOGIC
   ========================================== */

function escapeHTML(str) { var div = document.createElement('div'); div.appendChild(document.createTextNode(str)); return div.innerHTML; }

/* ==========================================
   DASHBOARD WS — singleton shared across
   dashboard.js and index.js via custom events
   ========================================== */
(function () {
  'use strict';

  // All tickers the dashboard needs live prices for
  var DASHBOARD_TICKERS = [
    'NIFTY', 'BANKNIFTY', 'SENSEX', 'FINNIFTY',
    'MIDCAP', 'SMALLCAP', 'NIFTYMIDCAP100', 'NIFTYSMLCAP100',
    'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK', 'BHARTIARTL',
    'SBIN', 'HINDUNILVR', 'ITC', 'LT', 'KOTAKBANK', 'AXISBANK',
    'ASIANPAINT', 'MARUTI', 'WIPRO', 'TITAN', 'SUNPHARMA', 'BAJFINANCE',
    'ADANIENT', 'TATAMOTORS', 'NTPC', 'ONGC', 'POWERGRID', 'ULTRACEMCO',
    'TATASTEEL', 'JSWSTEEL', 'TECHM', 'HAL', 'BEL', 'ZOMATO', 'TRENT',
    'BANDHANBNK', 'MARICO', 'DIVISLAB', 'HINDALCO', 'GRASIM', 'HEROMOTOCO',
    'DRREDDY', 'CIPLA', 'APOLLOHOSP', 'HDFCLIFE', 'SBILIFE', 'EICHERMOT'
  ];

  var reconnectAttempt = 0;
  var pingTimer        = null;
  var pongTimeout      = null;
  var ws               = null;
  var wsActive         = false;  // true while the page wants a WS connection

  function getReconnectDelay(attempt) {
    var base   = Math.min(1000 * Math.pow(2, attempt), 30000); // cap 30 s
    var jitter = Math.random() * 1000;
    return base + jitter;
  }

  function startPing() {
    stopPing();
    pingTimer = setInterval(function () {
      if (ws && ws.readyState === WebSocket.OPEN) {
        try { ws.send(JSON.stringify({ type: 'ping' })); } catch (e) {}
        // If no pong within 10 s, force reconnect
        pongTimeout = setTimeout(function () {
          console.warn('[DashWS] Pong timeout – reconnecting');
          try { ws.close(); } catch (e) {}
        }, 10000);
      }
    }, 30000);
  }

  function stopPing() {
    if (pingTimer)   { clearInterval(pingTimer);  pingTimer   = null; }
    if (pongTimeout) { clearTimeout(pongTimeout); pongTimeout = null; }
  }

  function handleMessage(msg) {
    try {
      switch (msg.type) {

        case 'connected':
          reconnectAttempt = 0;
          // Subscribe with required tickers
          if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'subscribe', topics: DASHBOARD_TICKERS }));
          }
          break;

        case 'price_update':
          // Calibrate server clock offset from first message
          if (msg.ts && window._serverClockOffset == null) {
            var serverMs = new Date(msg.ts).getTime();
            if (!isNaN(serverMs) && serverMs > 0) {
              window._serverClockOffset = serverMs - Date.now();
            }
          }
          // Skip stale yfinance data when we already have fresher AngelOne WS data
          if (msg.data && msg.ts) {
            var correctedNow = Date.now() + (window._serverClockOffset || 0);
            var msgAge = correctedNow - new Date(msg.ts).getTime();
            var staleThreshold = window._marketOpen === false ? 300000 : 30000;
            if (msgAge > staleThreshold) {
              break;
            }
          }
          // Persist to localStorage for instant seed on next visit
          try {
            var existing = JSON.parse(localStorage.getItem('llp') || '{}');
            var merged   = Object.assign({}, existing, msg.data);
            var mergedStr = JSON.stringify(merged);
            if (mergedStr.length > 2e6) {
              // Exceeded ~2MB; keep only current batch to stay under quota
              merged = Object.assign({}, msg.data);
              mergedStr = JSON.stringify(merged);
            }
            localStorage.setItem('llp',    mergedStr);
            localStorage.setItem('llp_ts', Date.now().toString());
          } catch (e) {}
          // Notify all listeners
          window.dispatchEvent(new CustomEvent('dashboard_price_update', { detail: { prices: msg.data, serverTs: msg.ts } }));
          break;

        case 'market_movers':
          window.dispatchEvent(new CustomEvent('dashboard_market_movers', { detail: msg.data }));
          break;

        case 'market_status':
          window.dispatchEvent(new CustomEvent('dashboard_market_status', { detail: msg }));
          break;

        case 'pong':
          if (pongTimeout) { clearTimeout(pongTimeout); pongTimeout = null; }
          break;

        case 'heartbeat':
          // Server alive — nothing needed
          break;
      }
    } catch (e) {
      console.error('[DashWS] handleMessage error:', e);
    }
  }

  function connect() {
    if (!wsActive) return;
    var protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    var url      = protocol + '//' + window.location.host + '/ws/dashboard';

    var newWs;
    try { newWs = new WebSocket(url); ws = newWs; } catch (e) {
      console.error('[DashWS] WebSocket creation failed:', e);
      scheduleReconnect();
      return;
    }

    newWs.onopen = function () {
      console.log('[DashWS] Connected');
      startPing();
      if (DASHBOARD_TICKERS.length > 0 && newWs.readyState === WebSocket.OPEN) {
        newWs.send(JSON.stringify({ type: 'subscribe', topics: DASHBOARD_TICKERS }));
      }
    };

    ws.onmessage = function (evt) {
      try { handleMessage(JSON.parse(evt.data)); } catch (e) {}
    };

    ws.onclose = function () {
      console.log('[DashWS] Disconnected');
      stopPing();
      scheduleReconnect();
    };

    ws.onerror = function (e) {
      console.error('[DashWS] Error:', e);
      // onclose fires after onerror – reconnect handled there
    };
  }

  function scheduleReconnect() {
    if (!wsActive) return;
    var delay = getReconnectDelay(reconnectAttempt);
    console.log('[DashWS] Reconnect in ' + (delay / 1000).toFixed(1) + 's (attempt ' + (reconnectAttempt + 1) + ')');
    reconnectAttempt = Math.min(reconnectAttempt + 1, 10);
    setTimeout(connect, delay);
  }

  /* Reconnect when tab becomes visible (page hidden can throttle WS) */
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden && wsActive && (!ws || ws.readyState !== WebSocket.OPEN)) {
      connect();
    }
  });

  /* Public API */
  window.DashboardWS = {
    start: function (extraTickers) {
      if (extraTickers) {
        extraTickers.forEach(function (t) {
          if (DASHBOARD_TICKERS.indexOf(t) === -1) DASHBOARD_TICKERS.push(t);
        });
      }
      if (!wsActive) {
        wsActive = true;
        connect();
      } else if (!ws || ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) {
        connect();
      }
    },
    stop: function () {
      wsActive = false;
      stopPing();
      if (ws) { try { ws.close(); } catch (e) {} ws = null; }
    },
    /** Subscribe additional tickers after initial connection */
    addTickers: function (tickers) {
      tickers.forEach(function (t) {
        if (DASHBOARD_TICKERS.indexOf(t) === -1) DASHBOARD_TICKERS.push(t);
      });
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'subscribe', topics: DASHBOARD_TICKERS }));
      }
    },
    /** Check if WebSocket is currently connected */
    isConnected: function () {
      return ws && ws.readyState === WebSocket.OPEN;
    }
  };
}());


/* ==========================================
   DASHBOARD PAGE INIT
   ========================================== */
document.addEventListener('DOMContentLoaded', function () {
  if (document.querySelector('.dashboard-main')) {
    initDashboard();
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

  // REST fallback: fetch index prices every 30s if WS hasn't pushed recently
  window._lastWsPriceTime = 0;
  if (!window._restFallbackTimer) {
    window._restFallbackTimer = setInterval(function() {
      if (Date.now() - window._lastWsPriceTime < 35000) return;
      fetch('/api/live-prices', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({tickers:['NIFTY','SENSEX','BANKNIFTY','FINNIFTY','MIDCAP','SMALLCAP']}) })
        .then(function(r){ return r.json(); })
        .then(function(data){
          if (data && typeof data === 'object') {
            window._lastWsPriceTime = Date.now();
            updateTickerStrip(data);
            var ct = window._chartTicker || 'NIFTY';
            var d = data[ct];
            if (d && d.current) {
              var pEl = document.getElementById('niftyPrice');
              if (pEl) pEl.innerText = d.current.toLocaleString('en-IN', { minimumFractionDigits: 2 });
              var prev = (d.prev_close != null && d.prev_close > 0) ? d.prev_close : ((d.open != null && d.open > 0) ? d.open : d.current);
              var diff = d.current - prev;
              var pct  = (prev > 0) ? (diff / prev) * 100 : 0;
              var cEl  = document.getElementById('niftyChange');
              if (cEl) {
                cEl.innerText = (diff >= 0 ? '+' : '') + diff.toFixed(2) + ' (' + (diff >= 0 ? '+' : '') + pct.toFixed(2) + '%)';
                cEl.className = 'price-change ' + (diff >= 0 ? 'text-green' : 'text-red');
              }
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
  fetchMarketMovers();
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

/* ── Seed UI from localStorage (called before WS connects) ──────────────── */
function seedUIFromCache() {
  try {
    var raw = localStorage.getItem('llp');
    var ts  = parseInt(localStorage.getItem('llp_ts') || '0', 10);
    if (!raw || (Date.now() - ts > 600000)) return; // 10-min TTL
    var prices = JSON.parse(raw);

    // Seed ticker strip (elements already in DOM from home.html)
    updateTickerStrip(prices);

    // Seed dashboard chart header (dynamic ticker)
    window._chartTicker = window._chartTicker || 'NIFTY';
    var chartData = prices[window._chartTicker];
    if (chartData && chartData.current) {
      var pEl = document.getElementById('niftyPrice');
      if (pEl) pEl.innerText = chartData.current.toLocaleString('en-IN', { minimumFractionDigits: 2 });
      var prev = (chartData.prev_close != null && chartData.prev_close > 0) ? chartData.prev_close : ((chartData.open != null && chartData.open > 0) ? chartData.open : chartData.current);
      var diff = chartData.current - prev;
      var pct  = (prev > 0) ? (diff / prev) * 100 : 0;
      var cEl  = document.getElementById('niftyChange');
      if (cEl) {
        var sign = diff >= 0 ? '+' : '';
        cEl.innerText   = sign + diff.toFixed(2) + ' (' + sign + pct.toFixed(2) + '%)';
        cEl.className   = 'price-change ' + (diff >= 0 ? 'text-green' : 'text-red');
      }
      var oEl = document.getElementById('niftyO');
      if (oEl && chartData.open) oEl.innerText = chartData.open.toFixed(2);
      var hEl = document.getElementById('niftyH');
      if (hEl && chartData.high) hEl.innerText = chartData.high.toFixed(2);
      var lEl = document.getElementById('niftyL');
      if (lEl && chartData.low)  lEl.innerText = chartData.low.toFixed(2);
      var cEl2 = document.getElementById('niftyC');
      if (cEl2 && chartData.prev_close) cEl2.innerText = chartData.prev_close.toFixed(2);
    }
  } catch (e) { /* non-critical */ }
}

/* ── WS event handlers ───────────────────────────────────────────────────── */
function onPriceUpdate(evt) {
  var raw = evt.detail;
  if (!raw) return;
  var prices = raw.prices || raw;
  window._lastWsPriceTime = Date.now();

  // 1. Ticker strip
  updateTickerStrip(prices);

  // 2. Dashboard chart header + OHLC footer (dynamic ticker)
  window._chartTicker = window._chartTicker || 'NIFTY';
  var chartTickerData = prices[window._chartTicker];
  if (chartTickerData && chartTickerData.current) {
    var pEl = document.getElementById('niftyPrice');
    if (pEl) pEl.innerText = chartTickerData.current.toLocaleString('en-IN', { minimumFractionDigits: 2 });
    var prev = (chartTickerData.prev_close != null && chartTickerData.prev_close > 0) ? chartTickerData.prev_close : ((chartTickerData.open != null && chartTickerData.open > 0) ? chartTickerData.open : chartTickerData.current);
    var diff = chartTickerData.current - prev;
    var pct  = (prev > 0) ? (diff / prev) * 100 : 0;
    var sign = diff >= 0 ? '+' : '';
    var cEl  = document.getElementById('niftyChange');
    if (cEl) {
      cEl.innerText = sign + diff.toFixed(2) + ' (' + sign + pct.toFixed(2) + '%)';
      cEl.className = 'price-change ' + (diff >= 0 ? 'text-green' : 'text-red');
    }
    
    var oEl = document.getElementById('niftyO');
    if (oEl && chartTickerData.open) oEl.innerText = chartTickerData.open.toFixed(2);
    var hEl = document.getElementById('niftyH');
    if (hEl && chartTickerData.high) hEl.innerText = chartTickerData.high.toFixed(2);
    var lEl = document.getElementById('niftyL');
    if (lEl && chartTickerData.low) lEl.innerText = chartTickerData.low.toFixed(2);

    // Real-time candlestick streaming via WebSocket
    if (window._niftyCandleSeries && window._niftyIntervalSec) {
      var _off = window._serverClockOffset;
      var nowSec = Math.floor((Date.now() + (_off != null && !isNaN(_off) ? _off : 0)) / 1000);
      var candleTime = Math.floor(nowSec / window._niftyIntervalSec) * window._niftyIntervalSec;

      if (!window._formingNiftyCandle || candleTime !== window._lastNiftyTime) {
        window._lastNiftyTime = candleTime;
        var existing = window._lastHistoricalCandle;
        if (existing && existing.time === candleTime) {
          window._formingNiftyCandle = {
            open: existing.open,
            high: Math.max(existing.high, chartTickerData.current),
            low: Math.min(existing.low, chartTickerData.current),
            close: chartTickerData.current
          };
        } else {
          window._formingNiftyCandle = {
            open: chartTickerData.current,
            high: chartTickerData.current,
            low: chartTickerData.current,
            close: chartTickerData.current
          };
        }
      }

      var fc = window._formingNiftyCandle;
      fc.close = chartTickerData.current;
      if (chartTickerData.current > fc.high) fc.high = chartTickerData.current;
      if (chartTickerData.current < fc.low)  fc.low = chartTickerData.current;

      try {
        window._niftyCandleSeries.update({
          time: window._lastNiftyTime,
          open: fc.open,
          high: fc.high,
          low: fc.low,
          close: fc.close
        });
        window._lastHistoricalCandle = {
          time: window._lastNiftyTime,
          open: fc.open,
          high: fc.high,
          low: fc.low,
          close: fc.close
        };
      } catch (e) {}

      if (window._niftyAreaSeries) {
        try {
          window._niftyAreaSeries.update({ time: window._lastNiftyTime, value: chartTickerData.current });
        } catch (e) {}
      }
    }
  }

  // 3. Mover list prices (elements added with id="mv-price-TICKER" and id="mv-prev-TICKER")
  Object.keys(prices).forEach(function (ticker) {
    var d    = prices[ticker];
    var pEl  = document.getElementById('mv-price-' + ticker);
    if (pEl && d && d.current !== undefined && d.current !== null) {
      var cur = Number(d.current);
      pEl.innerText = '\u20B9' + cur.toFixed(2);
      // Update previous close display
      var prevEl = document.getElementById('mv-prev-' + ticker);
      if (prevEl && d.prev_close != null) {
        prevEl.innerText = 'Prev: \u20B9' + Number(d.prev_close).toFixed(2);
      }
      // Update change percentage in mover row
      var itemEl = document.getElementById('mv-item-' + ticker);
      if (itemEl) {
        var chEl = itemEl.querySelector('.mv-change');
        if (chEl && d.change_pct != null) {
          var pct = Number(d.change_pct);
          var arrow = pct >= 0 ? '▲' : '▼';
          chEl.innerText = arrow + ' ' + Math.abs(pct).toFixed(2) + '%';
          chEl.className = 'mv-change ' + (pct >= 0 ? 'text-green' : 'text-red');
        }
        // Update volume for most active
        var volEl = itemEl.querySelector('.mv-volume');
        if (volEl && d.volume != null) {
          volEl.innerText = Number(d.volume).toLocaleString('en-IN');
        }
      }
    }
  });

  // 4. Watchlist card prices (elements added with id="wl-price-TICKER")
  Object.keys(prices).forEach(function (ticker) {
    var d      = prices[ticker];
    var pEl2   = document.getElementById('wl-price-' + ticker);
    var chEl   = document.getElementById('wl-change-' + ticker);
    if (pEl2 && d && d.current !== undefined && d.current !== null) {
      var price = d.current;
      var prev  = (d.prev_close != null && d.prev_close > 0) ? d.prev_close : ((d.open != null && d.open > 0) ? d.open : price);
      var dif   = price - prev;
      var pc    = prev > 0 ? (dif / prev) * 100 : 0;
      var sg    = dif >= 0 ? '▲ +' : '▼ ';
      var col   = dif >= 0 ? 'text-green' : 'text-red';
      pEl2.innerText = '\u20B9' + price.toLocaleString('en-IN', { minimumFractionDigits: 2 });
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
  var allMovers = (data.gainers || []).concat(data.losers || []);
  var seenTickers = {};
  allMovers.forEach(function (s) { if (s && s.ticker) seenTickers[s.ticker] = true; });
  (data.most_active || []).forEach(function (s) {
    if (s && s.ticker && !seenTickers[s.ticker]) { allMovers.push(s); seenTickers[s.ticker] = true; }
  });
  var actualGainers = allMovers.filter(function (s) { return s && s.change_pct >= 0; });
  var actualLosers  = allMovers.filter(function (s) { return s && s.change_pct <  0; });
  actualGainers.sort(function (a, b) { return b.change_pct - a.change_pct; });
  actualLosers.sort( function (a, b) { return a.change_pct - b.change_pct; });
  var actualActive  = (data.most_active || []).slice();
  actualActive.sort( function (a, b) { return (b.volume || 0) - (a.volume || 0); });
  var isDashboard = !!document.querySelector('.dashboard-main');
  var maxGainers = isDashboard ? 5 : 8;
  var maxLosers  = isDashboard ? 5 : 8;
  if (actualGainers.length) renderMoverList('gainersList', actualGainers.slice(0, maxGainers));
  if (actualLosers.length)  renderMoverList('losersList',  actualLosers.slice(0, maxLosers));
  if (actualActive.length)  renderMoverList('activeList',  actualActive.slice(0, maxGainers));
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

/* ── Update ticker strip from a prices map ───────────────────────────────── */
var TICKER_STRIP_INDICES = ['NIFTY','SENSEX','BANKNIFTY','FINNIFTY','MIDCAP','SMALLCAP'];
function updateTickerStrip(prices) {
  var container = document.getElementById('indexTickerContainer');
  TICKER_STRIP_INDICES.forEach(function (ticker) {
    var d = prices[ticker];
    if (!d || !d.current) return;
    var priceEl  = document.getElementById('strip-price-' + ticker);
    var changeEl = document.getElementById('strip-change-' + ticker);
    if (!priceEl || !changeEl) {
      if (!container) return;
      var card = document.createElement('div');
      card.className = 'ticker-card';
      card.onclick = function(){ window.location.href='/index_chart.html?ticker='+ticker; };
      card.innerHTML = '<h4>' + ticker + '</h4><div class="price" id="strip-price-'+ticker+'" data-sid="sp-'+ticker+'">--</div><div class="change" id="strip-change-'+ticker+'" data-sid="sc-'+ticker+'">--</div>';
      container.appendChild(card);
      priceEl  = document.getElementById('strip-price-' + ticker);
      changeEl = document.getElementById('strip-change-' + ticker);
      if (!priceEl || !changeEl) return;
    }
    var prev = (d.prev_close != null && d.prev_close > 0) ? d.prev_close : ((d.open != null && d.open > 0) ? d.open : d.current);
    var diff = d.current - prev;
    var pct  = (prev > 0) ? (diff / prev) * 100 : 0;
    var sign = diff >= 0 ? '▲ +' : '▼ ';
    priceEl.innerText  = '₹' + d.current.toLocaleString('en-IN', { minimumFractionDigits: 2 });
    changeEl.innerText = sign + diff.toFixed(2) + ' (' + pct.toFixed(2) + '%)';
    changeEl.className = 'change ' + (diff >= 0 ? 'text-green' : 'text-red');
  });
}


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
var NSE_HOLIDAYS_2026 = [
  '2026-01-26','2026-02-16','2026-03-06','2026-03-27','2026-03-30','2026-04-02',
  '2026-04-14','2026-05-01','2026-08-15','2026-09-16','2026-10-02','2026-10-09',
  '2026-11-05','2026-11-23','2026-12-25'
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
  // Check if we already have fresh index data in localStorage
  try {
    var cached = JSON.parse(localStorage.getItem('llp') || 'null');
    var cachedTs = parseInt(localStorage.getItem('llp_ts') || '0', 10);
    var cacheTTL = (window._marketOpen === false) ? 3600000 : 15000; // 1h if closed, 15s if open
    if (cached && cached['NIFTY'] && cached['NIFTY'].current &&
        cached['SENSEX'] && cached['SENSEX'].current &&
        (Date.now() - cachedTs < cacheTTL)) {
      // Already fresh — no REST call needed, WS will refresh shortly
      updateTickerStrip(cached);
      // Also seed current chart header O/H/L/C from cache
      var ct = window._chartTicker || 'NIFTY';
      var niftyCached = cached[ct] || cached['NIFTY'];
      if (niftyCached && niftyCached.current) {
        var pElC = document.getElementById('niftyPrice');
        if (pElC) pElC.innerText = niftyCached.current.toLocaleString('en-IN', { minimumFractionDigits: 2 });
        var prevC = (niftyCached.prev_close != null && niftyCached.prev_close > 0) ? niftyCached.prev_close : ((niftyCached.open != null && niftyCached.open > 0) ? niftyCached.open : niftyCached.current);
        var diffC = niftyCached.current - prevC;
        var pctC = (prevC > 0) ? (diffC / prevC) * 100 : 0;
        var cElC = document.getElementById('niftyChange');
        if (cElC) {
          cElC.innerText = (diffC >= 0 ? '+' : '') + diffC.toFixed(2) + ' (' + (diffC >= 0 ? '+' : '') + pctC.toFixed(2) + '%)';
          cElC.className = 'price-change ' + (diffC >= 0 ? 'text-green' : 'text-red');
        }
        var oElC = document.getElementById('niftyO');
        if (oElC && niftyCached.open) oElC.innerText = niftyCached.open.toFixed(2);
        var hElC = document.getElementById('niftyH');
        if (hElC && niftyCached.high) hElC.innerText = niftyCached.high.toFixed(2);
        var lElC = document.getElementById('niftyL');
        if (lElC && niftyCached.low)  lElC.innerText = niftyCached.low.toFixed(2);
        var cEl2C = document.getElementById('niftyC');
        if (cEl2C && niftyCached.prev_close) cEl2C.innerText = niftyCached.prev_close.toFixed(2);
      }
      // Update chart area series from cache data (fallback when WS idle)
      if (window._niftyAreaSeries && niftyCached && niftyCached.current) {
        try {
          var _nowSec = Math.floor((Date.now() + (window._serverClockOffset != null && !isNaN(window._serverClockOffset) ? window._serverClockOffset : 0)) / 1000);
          window._niftyAreaSeries.update({ time: _nowSec, value: niftyCached.current });
        } catch (e) {}
      }
      return;
    }
  } catch (e) {}

  // No fresh cache — fetch from server
  try {
    var res = await fetch('/api/live-prices', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tickers: ['NIFTY','SENSEX','BANKNIFTY','FINNIFTY'] })
    });
    if (res.ok) {
      var data = await res.json();
      if (data) {
        // Update ticker strip
        updateTickerStrip(data);

        // Update chart header + OHLC based on selected dropdown ticker
        var ct = window._chartTicker || 'NIFTY';
        var nifty = data[ct] || data['NIFTY'];
        if (nifty && nifty.current) {
          var pEl = document.getElementById('niftyPrice');
          if (pEl) pEl.innerText = nifty.current.toLocaleString('en-IN', { minimumFractionDigits: 2 });
          var prev = (nifty.prev_close != null && nifty.prev_close > 0) ? nifty.prev_close : ((nifty.open != null && nifty.open > 0) ? nifty.open : nifty.current);
          var diff = nifty.current - prev;
          var pct  = (prev > 0) ? (diff / prev) * 100 : 0;
          var sign = diff >= 0 ? '+' : '';
          var cEl  = document.getElementById('niftyChange');
          if (cEl) {
            cEl.innerText = sign + diff.toFixed(2) + ' (' + sign + pct.toFixed(2) + '%)';
            cEl.className = 'price-change ' + (diff >= 0 ? 'text-green' : 'text-red');
          }
          
          var oEl = document.getElementById('niftyO');
          if (oEl && nifty.open) oEl.innerText = nifty.open.toFixed(2);
          var hEl = document.getElementById('niftyH');
          if (hEl && nifty.high) hEl.innerText = nifty.high.toFixed(2);
          var lEl = document.getElementById('niftyL');
          if (lEl && nifty.low)  lEl.innerText = nifty.low.toFixed(2);
          var cEl2 = document.getElementById('niftyC');
          if (cEl2 && nifty.prev_close) cEl2.innerText = nifty.prev_close.toFixed(2);
        }

        // Also update NIFTY chart area series from HTTP data (fallback when WS idle)
        if (window._niftyAreaSeries) {
          try {
            var _nowSec = Math.floor((Date.now() + (window._serverClockOffset != null && !isNaN(window._serverClockOffset) ? window._serverClockOffset : 0)) / 1000);
            window._niftyAreaSeries.update({ time: _nowSec, value: nifty.current });
          } catch (e) {}
        }
      }

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
function loadChartData(ticker) {
  if (!window._niftyCandleSeries) return;
  fetch('/api/stock-data/intraday?ticker=' + encodeURIComponent(ticker) + '&interval=1m').then(function (r) { return r.json(); }).then(function (data) {
    if (!Array.isArray(data) || data.length === 0) return;
    var areaData = data.map(function (p) {
      return { time: (typeof p.time === 'number' ? p.time : Number(p.time)), value: p.close };
    });
    areaData.sort(function (a, b) { return a.time - b.time; });
    if (window._niftyAreaSeries) window._niftyAreaSeries.setData(areaData);
    var candleData = data.map(function (p) {
      return { time: (typeof p.time === 'number' ? p.time : Number(p.time)), open: p.open, high: p.high, low: p.low, close: p.close };
    });
    candleData.sort(function (a, b) { return a.time - b.time; });
    if (window._niftyCandleSeries) {
      window._niftyCandleSeries.setData(candleData);
      if (candleData.length > 0) {
        window._lastHistoricalCandle = candleData[candleData.length - 1];
      }
    }
    var sma = calcSMA(areaData, 5);
    if (sma.length > 0 && window._niftySMASeries) {
      window._niftySMASeries.setData(sma);
    }
  }).catch(function () {});
}
function initNiftyChart() {
  refreshMarketStatus();
  var container = document.getElementById('niftyChart');
  if (!container || window._niftyChartInited) return;
  window._niftyChartInited = true;
  var h = Math.max(container.clientHeight || 300, 200);
  var w = container.clientWidth || 400;
  window._niftyChart = LightweightCharts.createChart(container, {
    width: w,
    height: h,
    layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#999' },
    grid: { vertLines: { visible: false }, horzLines: { color: '#2a2e39' } },
    crosshair: { mode: LightweightCharts.CrosshairMode.Magnet },
    rightPriceScale: { visible: true, borderColor: '#2a2e39' },
    timeScale: { visible: true, borderColor: '#2a2e39', timeVisible: true,
      tickMarkFormatter: function (time) { var d = new Date(time * 1000); return d.getDate() + '/' + (d.getMonth()+1); } },
    handleScroll: true, handleScale: true,
  });
  window._niftyAreaSeries = window._niftyChart.addAreaSeries({ lineColor: '#26a69a', topColor: 'rgba(38,166,154,0.3)', bottomColor: 'rgba(38,166,154,0.05)', lineWidth: 2 });
  window._niftyCandleSeries = window._niftyChart.addCandlestickSeries({ upColor: '#26a69a', downColor: '#ef5350', borderVisible: false, wickUpColor: '#26a69a', wickDownColor: '#ef5350' });
  window._niftyCandleSeries.applyOptions({ visible: false });
  window._niftyIntervalSec = 60;
  window._niftySMASeries = window._niftyChart.addLineSeries({ color: '#f5a623', lineWidth: 1, priceLineVisible: false });
  window._niftySMASeries.applyOptions({ visible: false });

  if (window.ResizeObserver) {
    window._niftyResizeObserver = new ResizeObserver(function () {
      if (!window._niftyChart) return;
      var cw = container.clientWidth || 400;
      var ch = Math.max(container.clientHeight || 300, 200);
      window._niftyChart.applyOptions({ width: cw, height: ch });
    });
    window._niftyResizeObserver.observe(container);
  }

  // Ticker selector
  var sel = document.getElementById('chartTickerSelect');
  if (sel) {
    window._chartTicker = sel.value;
    sel.addEventListener('change', function () {
      window._chartTicker = sel.value;
      window._formingNiftyCandle = null;
      loadChartData(window._chartTicker);
      // Also subscribe via WS
      if (window.DashboardWS) window.DashboardWS.addTickers([window._chartTicker]);
      // Immediately update header from cache for snappy UI
      try {
        var raw = localStorage.getItem('llp');
        if (raw) {
          var cached = JSON.parse(raw);
          var ct = window._chartTicker;
          var d = cached[ct];
          if (d && d.current) {
            var pEl = document.getElementById('niftyPrice');
            if (pEl) pEl.innerText = d.current.toLocaleString('en-IN', { minimumFractionDigits: 2 });
            var prev = (d.prev_close != null && d.prev_close > 0) ? d.prev_close : ((d.open != null && d.open > 0) ? d.open : d.current);
            var diff = d.current - prev;
            var pct  = (prev > 0) ? (diff / prev) * 100 : 0;
            var cEl  = document.getElementById('niftyChange');
            if (cEl) {
              var sign = diff >= 0 ? '+' : '';
              cEl.innerText = sign + diff.toFixed(2) + ' (' + sign + pct.toFixed(2) + '%)';
              cEl.className = 'price-change ' + (diff >= 0 ? 'text-green' : 'text-red');
            }
            var oEl = document.getElementById('niftyO');
            if (oEl && d.open) oEl.innerText = d.open.toFixed(2);
            var hEl = document.getElementById('niftyH');
            if (hEl && d.high) hEl.innerText = d.high.toFixed(2);
            var lEl = document.getElementById('niftyL');
            if (lEl && d.low)  lEl.innerText = d.low.toFixed(2);
            var cEl2 = document.getElementById('niftyC');
            if (cEl2 && d.prev_close) cEl2.innerText = d.prev_close.toFixed(2);
          }
        }
      } catch (e) {}
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
  if (window._marketOpen) {
    el.textContent = '● Live';
    el.style.color = '#26a69a';
  } else {
    el.textContent = '● Closed';
    el.style.color = '#ef5350';
  }
}

async function refreshMarketStatus() {
  // If market was already confirmed closed and we haven't cached it recently, skip
  try {
    var mo = localStorage.getItem('market_open');
    var moTs = parseInt(localStorage.getItem('market_open_ts') || '0', 10);
    if (mo === 'false' && (Date.now() - moTs < 600000)) return; // re-check every 10 min if closed
  } catch(e) {}
  try {
    var r = await fetch('/api/market-movers');
    var d = await r.json();
    window._marketOpen = d.market_open === true;
  } catch (e) { window._marketOpen = false; }
  updateMarketStatusUI();
}

async function initBigChart() {
  var params = new URLSearchParams(window.location.search);
  var rawTicker = params.get('ticker') || 'RELIANCE';
  var ticker = rawTicker.toUpperCase().replace(/\.(NS|BO)$/i, '');

  window.currentTicker = ticker;

  var titleEl = document.getElementById('ticker-name');
  if (titleEl) titleEl.innerText = ticker;
    var badgeEl = document.getElementById('ticker-badge');
  if (badgeEl) badgeEl.innerText = ticker;

  var chartContainer = document.getElementById('chart');

  // Read height from the CSS-controlled #chart-container (clamp-based, always visible)
  var chartParent = document.getElementById('chart-container');
  var chartH = Math.max(420, chartParent ? chartParent.offsetHeight : 500);
  var chartW = Math.max(200, chartParent ? chartParent.clientWidth : 800);

  bigChart = LightweightCharts.createChart(chartContainer, {
    width: chartW,
    height: chartH,
    layout:    { background: { type: 'solid', color: '#131722' }, textColor: '#d1d4dc' },
    grid:      { vertLines: { color: 'rgba(42,46,57,0.5)' }, horzLines: { color: 'rgba(42,46,57,0.5)' } },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: 'rgba(197,203,206,0.8)', scaleMargins: { top: 0.05, bottom: 0.25 } },
    timeScale: {
      borderColor: 'rgba(197,203,206,0.8)',
      timeVisible: true,
      rightOffset: 5,
      fixLeftEdge: false,
      lockVisibleTimeRangeOnResize: false,
      tickMarkFormatter: function(epochSec, markType) {
        // Guard: LWC may pass undefined/NaN for special padding ticks
        if (epochSec == null || isNaN(epochSec)) return '';
        // Epochs from backend are proper UTC. Convert to IST (+5:30) for display.
        var IST_OFFSET_MS = 5.5 * 60 * 60 * 1000;
        var d = new Date(epochSec * 1000 + IST_OFFSET_MS);
        if (isNaN(d.getTime())) return '';
        var MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
        // markType: 0=year, 1=month, 2=day, 3=time, 4=timeWithSeconds
        if (markType >= 3) {
          // Show HH:MM in IST
          return String(d.getUTCHours()).padStart(2,'0') + ':' + String(d.getUTCMinutes()).padStart(2,'0');
        } else if (markType === 2) {
          // Show date: "17 Mar"
          return d.getUTCDate() + ' ' + MONTHS[d.getUTCMonth()];
        } else if (markType === 1) {
          // Show month + year: "Mar 2026"
          return MONTHS[d.getUTCMonth()] + ' ' + d.getUTCFullYear();
        } else {
          // Year only
          return String(d.getUTCFullYear());
        }
      }
    }
  });

  bigCandleSeries = bigChart.addCandlestickSeries({
    upColor: '#26a69a', downColor: '#ef5350',
    borderDownColor: '#ef5350', borderUpColor: '#26a69a',
    wickDownColor: '#ef5350', wickUpColor: '#26a69a',
    priceFormat: { type: 'price', minMove: 0.01 }
  });

  let bigVolumeSeries = bigChart.addHistogramSeries({
    color: '#26a69a',
    priceFormat: { type: 'volume' },
    priceScaleId: '',
    scaleMargins: { top: 0.8, bottom: 0 }
  });
  
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
    if (window._chartDataCache && window._chartDataCache.length > 0) {
      var currentTime = param.time;
      for (var ci = 0; ci < window._chartDataCache.length; ci++) {
        if (window._chartDataCache[ci].time === currentTime) {
          if (ci > 0) {
            prevCloseStr = window._chartDataCache[ci - 1].close.toFixed(2);
          }
          break;
        }
      }
    }
    if (window.lastLivePrice != null) {
      currentPriceStr = window.lastLivePrice.toFixed(2);
    }

    toolTip.style.display = 'block';

    var tooltipWidth = 200;
    var chartContainer = document.getElementById('chart-container');
    var containerRect = chartContainer ? chartContainer.getBoundingClientRect() : { left: 0 };
    var leftPos = param.point.x;
    var rightEdge = containerRect.left + leftPos + tooltipWidth + 10;
    if (rightEdge > window.innerWidth) {
      leftPos = Math.max(0, param.point.x - tooltipWidth - 5);
    }
    toolTip.style.left = leftPos + 'px';
    toolTip.style.top = param.point.y + 'px';
    toolTip.innerHTML = '<div style="color: #2962FF">O: ' + price.open.toFixed(2) + '</div>' +
                        '<div style="color: #26a69a">H: ' + price.high.toFixed(2) + '</div>' +
                        '<div style="color: #ef5350">L: ' + price.low.toFixed(2) + '</div>' +
                        '<div style="color: #2962FF">C: ' + price.close.toFixed(2) + '</div>' +
                        (vol ? '<div style="color: #d1d4dc">V: ' + vol.value + '</div>' : '') +
                        '<div style="color: #ff9800">Prev: ' + prevCloseStr + '</div>' +
                        '<div style="color: #4caf50">Live: ' + currentPriceStr + '</div>' +
                        '<div style="color: #8a8a8a; font-size:10px;">' + dateStr + '</div>';
  });

  function resizeChart() {
    var parent = document.getElementById('chart-container');
    if (!parent) return;
    var h = parent.offsetHeight || (window._chartLastSize ? window._chartLastSize.height : 400);
    var w = parent.clientWidth  || (window._chartLastSize ? window._chartLastSize.width : 600);
    window._chartLastSize = { height: h, width: w };
    if (bigChart) bigChart.applyOptions({ height: h, width: w });
  }
  window.addEventListener('resize', resizeChart);
  requestAnimationFrame(resizeChart);

  function fmtPrice(v) {
    if (v == null) return '--';
    return '₹' + Number(v).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function showChartError(msg) {
    var errEl = document.getElementById('error-msg');
    if (errEl) { errEl.style.display = 'block'; errEl.textContent = msg; }
    console.error('Chart error:', msg);
  }

  function hideChartError() {
    var errEl = document.getElementById('error-msg');
    if (errEl) errEl.style.display = 'none';
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

  var _chartDataCache = null;
  var _earliestLoadedTime = null;
  var _isLoadingMore = false;
  var _lazyLoadSubscribed = false;

  function _setupLazyLoad() {
    if (!bigChart || _lazyLoadSubscribed) return;
    _lazyLoadSubscribed = true;
    bigChart.timeScale().subscribeVisibleTimeRangeChange(function () {
      _maybeLazyLoad();
    });
  }

  function _maybeLazyLoad() {
    if (_isLoadingMore) return;
    var range = window._loadedRange;
    if (!range || !_earliestLoadedTime) return;
    var timeRange = bigChart.timeScale().getVisibleRange();
    if (!timeRange) return;
    var intervalMap = { '1m': 60, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600 };
    var tickSec = intervalMap[range];
    var buffer;
    if (tickSec) {
      buffer = tickSec * 10;
    } else {
      // Daily ranges: use 5-day buffer
      buffer = 86400 * 5;
    }
    if (timeRange.from >= _earliestLoadedTime + buffer) return;
    _fetchMoreData(_earliestLoadedTime, range);
  }

  function _fetchMoreData(beforeTime, range) {
    _isLoadingMore = true;
    var isIntraday = ['1m','5m','15m','30m','1h'].indexOf(range) !== -1;
    var url;
    if (isIntraday) {
      url = '/api/stock-data/intraday/paginated?ticker=' + encodeURIComponent(ticker)
          + '&interval=' + range + '&before=' + beforeTime + '&limit=200';
    } else {
      // For daily ranges, convert epoch seconds to ISO date string
      var beforeDate = new Date(beforeTime * 1000).toISOString().split('T')[0];
      url = '/api/stock-data/range/paginated?ticker=' + encodeURIComponent(ticker)
          + '&range=' + range + '&before=' + beforeDate + '&limit=200';
    }
    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (resp) {
        if (resp && resp.data && resp.data.length > 0) {
          var incoming = resp.data.map(function (p) {
            return { time: toTimeNum(p.time), open: p.open, high: p.high, low: p.low, close: p.close, volume: p.volume || 0 };
          });
          var combined = _chartDataCache ? _chartDataCache.slice() : [];
          var existingTimes = {};
          combined.forEach(function (c) { existingTimes[c.time] = true; });
          incoming.forEach(function (c) {
            if (!existingTimes[c.time]) { combined.push(c); existingTimes[c.time] = true; }
          });
          combined.sort(function (a, b) { return a.time - b.time; });
          _chartDataCache = combined;
          _earliestLoadedTime = combined.length > 0 ? combined[0].time : null;
          bigCandleSeries.setData(combined);
          if (bigVolumeSeries) {
            var volData = combined.map(function (p) { return { time: p.time, value: p.volume, color: p.close >= p.open ? 'rgba(38,166,154,0.3)' : 'rgba(239,83,80,0.3)' }; });
            bigVolumeSeries.setData(volData);
          }
          bigChart.timeScale().scrollToPosition(0, false);
        }
        _isLoadingMore = false;
      })
      .catch(function () { _isLoadingMore = false; });
  }

  // Shared chart data renderer (used by cache path and API path)
  function _renderChartData(data, range) {
    if (!data || data.length === 0) {
      bigCandleSeries.setData([]);
      if (bigVolumeSeries) bigVolumeSeries.setData([]);
      bigChart.timeScale().fitContent();
      window._loadedRange = range;
      window._formingCandles = {};
      var errEl = document.getElementById('error-msg');
      if (errEl) { errEl.textContent = 'No chart data for ' + ticker; errEl.style.display = 'block'; }
      return;
    }
    var formatted = data.map(function (p) {
      return { time: toTimeNum(p.time), open: p.open, high: p.high, low: p.low, close: p.close, volume: p.volume || 0 };
    });
    formatted.sort(function (a, b) { return a.time - b.time; });
    var seen = new Set(), unique = [];
    formatted.forEach(function (p) { if (!seen.has(p.time)) { seen.add(p.time); unique.push(p); } });

    _chartDataCache = unique.slice();
    _earliestLoadedTime = unique.length > 0 ? unique[0].time : null;

    bigCandleSeries.setData(unique);
    if (unique.length > 0) {
      window._lastHistoricalCandle = unique[unique.length - 1];
    }
    if (bigVolumeSeries) {
      var volData = unique.map(function (p) { return { time: p.time, value: p.volume, color: p.close >= p.open ? 'rgba(38,166,154,0.3)' : 'rgba(239,83,80,0.3)' }; });
      bigVolumeSeries.setData(volData);
    }
    bigChart.timeScale().fitContent();

    _setupLazyLoad();

    window._formingCandles = {};
    window._lastFormingRange = range;
    window._loadedRange = range;
    window._lastCandleClose = unique[unique.length - 1].close;

    window.lastChartTime = unique[unique.length - 1].time;
    var last = unique[unique.length - 1];

    if (!window.lastLivePrice) {
      var pEl = document.getElementById('header-price');  if (pEl) pEl.innerText = fmtPrice(last.close);
    }
    var oEl = document.getElementById('ohlc-open');     if (oEl) oEl.innerText = last.open.toFixed(2);
    var hEl = document.getElementById('ohlc-high');     if (hEl) hEl.innerText = last.high.toFixed(2);
    var lEl = document.getElementById('ohlc-low');      if (lEl) lEl.innerText = last.low.toFixed(2);
    var cEl = document.getElementById('ohlc-close');    if (cEl) cEl.innerText = last.close.toFixed(2);
  }

  var _loadId = 0;
  var _loadController = null;

  window.loadData = async function (range) {
    window._pendingRange = range;
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
    if (range === '1D') cacheInterval = '5m';

    // 3-tier cache: memory → IndexedDB → API
    var memKey = ticker + '|' + cacheInterval + '|' + range;
    var memData = window._recentRanges ? window._recentRanges.get(memKey) : null;
    if (memData) { _renderChartData(memData, range); window._pendingRange = null; if (loader2) loader2.style.display = 'none'; var le = document.getElementById('loading'); if (le) le.style.display = 'none'; return; }

    if (window._idbGetCandles) {
      var idbData = await window._idbGetCandles(ticker, cacheInterval, range);
      if (idbData) {
        if (window._recentRanges) window._recentRanges.set(memKey, idbData);
        _renderChartData(idbData, range);
        window._pendingRange = null;
        if (loader2) loader2.style.display = 'none';
        var le2 = document.getElementById('loading'); if (le2) le2.style.display = 'none';
        return;
      }
    }

    try {
      var url = '/api/stock-data/range?ticker=' + encodeURIComponent(ticker) + '&range=' + range;
      if (range === '1D')                                         url = '/api/stock-data/intraday?ticker=' + encodeURIComponent(ticker) + '&interval=5m';
      else if (['1m','5m','15m','30m','1h'].indexOf(range) !== -1)     url = '/api/stock-data/intraday?ticker=' + encodeURIComponent(ticker) + '&interval=' + range;
      else if (range === '1W')                                    url = '/api/stock-data/weekly?ticker=' + encodeURIComponent(ticker);

      var res  = await fetch(url, { signal: signal });
      if (myLoadId !== _loadId) { hideLoader(loader2); return; }
      if (!res.ok) {
        showChartError('Server returned ' + res.status + ' for ' + range + ' data');
        return;
      }
      var data = await res.json();

      if (!Array.isArray(data)) {
        showChartError('Unexpected response format for ' + range + ' data');
        return;
      }
      if (myLoadId !== _loadId) { hideLoader(loader2); return; }
      _renderChartData(data, range);
      // Write to caches on successful API fetch
      try {
        if (window._recentRanges) window._recentRanges.set(memKey, data);
        if (window._idbSetCandles) window._idbSetCandles(ticker, cacheInterval, range, data);
      } catch (e) {}
    } catch (e) {
      if (e.name === 'AbortError') { hideLoader(loader2); return; }
      showChartError('Error loading ' + range + ' data: ' + e.message);
    }
    finally {
      window._pendingRange = null;
      if (loader2) loader2.style.display = 'none';
      var loadingEl = document.getElementById('loading');
      if (loadingEl) loadingEl.style.display = 'none';
    }
  };

  function hideLoader(el) { if (el) el.style.display = 'none'; var le = document.getElementById('loading'); if (le) le.style.display = 'none'; }

  loadData('ALL').catch(function (e) { showChartError('Initial load error: ' + e.message); });

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

  // Start WebSocket for real-time updates
  if (window.DashboardWS) {
    window.DashboardWS.start([ticker]);
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
      var wsLive = prices[ticker] || prices[cleanTicker];
      if (!wsLive || !wsLive.current) return;
      var serverTs = (detail && detail.serverTs) ? detail.serverTs : null;
      // Detect WS reconnect: gap > 30s since last price update
      var now = Date.now();
      var activeRange = window._loadedRange;
      if (window._lastPriceUpdateMs && (now - window._lastPriceUpdateMs > 30000) && serverTs) {
        // Fetch all candles since last known update time to fill missed data
        // Convert local time to IST epoch seconds (DB stores IST-naive timestamps)
        var lastUpdateUtcMs = window._lastPriceUpdateMs + (new Date().getTimezoneOffset() * 60000);
        var sinceSec = Math.floor((lastUpdateUtcMs + 330 * 60000) / 1000);
        if (['1m','5m','15m','30m','1h'].indexOf(activeRange) !== -1) {
          fetch('/api/stock-data/intraday/since?ticker=' + encodeURIComponent(ticker) + '&interval=' + activeRange + '&since=' + sinceSec)
            .then(function (r) { return r.json(); })
            .then(function (missed) {
              if (!Array.isArray(missed) || missed.length === 0) return;
              var incoming = missed.map(function (p) {
                return { time: toTimeNum(p.time), open: p.open, high: p.high, low: p.low, close: p.close, volume: p.volume || 0 };
              });
              var combined = _chartDataCache ? _chartDataCache.slice() : [];
              var existingTimes = {};
              combined.forEach(function (c) { existingTimes[c.time] = true; });
              incoming.forEach(function (c) {
                if (!existingTimes[c.time]) { combined.push(c); existingTimes[c.time] = true; }
              });
              combined.sort(function (a, b) { return a.time - b.time; });
              _chartDataCache = combined;
              bigCandleSeries.setData(combined);
              if (bigVolumeSeries) {
                var vd = combined.map(function (p) { return { time: p.time, value: p.volume, color: p.close >= p.open ? 'rgba(38,166,154,0.3)' : 'rgba(239,83,80,0.3)' }; });
                bigVolumeSeries.setData(vd);
              }
            }).catch(function () {});
        }
        // Also repair forming candle from the server's latest completed candle (use active range interval)
        var recoverInterval = (['1m','5m','15m','30m','1h'].indexOf(activeRange) !== -1) ? activeRange : '5m';
        fetch('/api/stock-data/candle/latest?ticker=' + encodeURIComponent(ticker) + '&interval=' + recoverInterval).then(function (r) { return r.json(); }).then(function (latest) {
          if (latest && latest.time && window._formingCandles) {
            window._formingCandles['i'] = { time: latest.time, open: latest.open, high: latest.high, low: latest.low, close: latest.close };
            if (bigCandleSeries) bigCandleSeries.update(window._formingCandles['i']);
          }
        }).catch(function () {});
      }
      window._lastPriceUpdateMs = now;
      if (wsLive.current !== undefined) {
        processBigChartPrice({
          current: wsLive.current,
          open: wsLive.open,
          high: wsLive.high,
          low: wsLive.low,
          prev_close: wsLive.prev_close
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
    var now = serverTs ? new Date(serverTs) : getIstNow();
    var dayStr = now.getFullYear() + '-' + String(now.getMonth()+1).padStart(2,'0') + '-' + String(now.getDate()).padStart(2,'0');
    var pEl2 = document.getElementById('header-price');
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
    if (live.open != null && live.open > 0 && oEl2) oEl2.innerText = live.open.toFixed(2);
    if (live.high != null && live.high > 0 && hEl2) hEl2.innerText = live.high.toFixed(2);
    if (live.low  != null && live.low  > 0 && lEl2) lEl2.innerText = live.low.toFixed(2);
    if (cEl2) cEl2.innerText = price.toFixed(2);

    var chEl = document.getElementById('header-change');
    var pClose = (live.prev_close != null && live.prev_close > 0) ? live.prev_close : ((live.open != null && live.open > 0) ? live.open : price);
    if (chEl && pClose > 0) {
      var chDiff = price - pClose;
      var chPct  = (chDiff / pClose) * 100;
      var absDiff = Math.abs(chDiff);
      if (absDiff < 0.005) {
        chEl.innerText = '0.00 (0.00%)';
        chEl.className = 'text-muted';
      } else {
        chEl.innerText = (chDiff >= 0 ? '+ ' : '- ') + absDiff.toFixed(2) + ' (' + (chDiff >= 0 ? '+' : '') + chPct.toFixed(2) + '%)';
        chEl.className = chDiff >= 0 ? 'text-green' : 'text-red';
      }
    }

    // Flash animation on header price
    if (pEl2 && window.lastLivePrice != null) {
      var prevPrice = window.lastLivePrice;
      if (Math.abs(price - prevPrice) > 0.01) {
        var flashColor = price > prevPrice ? 'rgba(0,230,118,0.15)' : 'rgba(255,0,85,0.15)';
        pEl2.style.transition = 'background 0s';
        pEl2.style.background = flashColor;
        pEl2.style.borderRadius = '4px';
        pEl2.style.padding = '2px 6px';
        setTimeout(function () {
          if (pEl2) { pEl2.style.transition = 'background 0.8s'; pEl2.style.background = 'transparent'; }
        }, 300);
      }
    }
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
      secEl.style.color = live.sectorPct >= 0 ? '#26a69a' : '#ef5350';
    } else if (secEl) {
      secEl.style.display = 'none';
    }

    // Always update header price even when chart is loading
    if (pEl2) pEl2.innerText = fmtPrice(live.current);
    if (cEl2) cEl2.innerText = live.current.toFixed(2);

    // Skip forming candle chart series update while loadData is in progress
    // (still compute in-memory candle so it's ready when data loads)
    if (window._pendingRange) return;

    var activeRange = (document.querySelector('.range-item.active') || {}).textContent || 'ALL';

    // Only build forming candle if the series data matches the active range
    if (activeRange !== window._loadedRange) return;

    if (activeRange !== window._lastFormingRange) {
      window._formingCandles = {};
      window._lastFormingRange = activeRange;
    }

    var intervalMin = { '1m':1, '5m':5, '15m':15, '30m':30, '1h':60 }[activeRange];
    var isIntraday = intervalMin !== undefined;

    if (isIntraday) {
      var ms = intervalMin * 60 * 1000;
      // IST session-aligned candle snapping: NSE starts 09:15 IST
      var SESSION_START_MIN = 9 * 60 + 15; // 555 min from IST midnight
      var IST_OFFSET_MS = 5.5 * 3600 * 1000;
      var utcMs = now.getTime();
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
      if (!window._formingCandles) window._formingCandles = {};
      if (!window._formingCandles['i'] || window._formingCandles['i'].time !== snappedSec) {
        // Guard: wait for historical data before building live candle
        if (window._lastCandleClose === null) return;
        // Open = first tick price of the new bucket (live.current) for accurate open display
        var openPrice = live.current > 0 ? live.current : (window._formingCandles['i'] ? window._formingCandles['i'].close : window._lastCandleClose);
        window._formingCandles['i'] = { time: snappedSec, open: openPrice, high: Math.max(openPrice, live.current), low: Math.min(openPrice, live.current), close: live.current };
      }
      var fc = window._formingCandles['i'];
      fc.high = Math.max(fc.high, live.current);
      fc.low  = Math.min(fc.low,  live.current);
      fc.close = live.current;
      if (bigCandleSeries) bigCandleSeries.update(fc);
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
      if (!window._formingCandles['d'] || window._formingCandles['d'].time !== periodStr) {
        var dOpen = (live.open != null && live.open > 0) ? live.open : live.current;
        var dHigh = (live.high != null && live.high > 0) ? live.high : live.current;
        var dLow  = (live.low  != null && live.low  > 0) ? live.low  : live.current;
        window._formingCandles['d'] = { time: periodStr, open: dOpen, high: dHigh, low: dLow, close: live.current };
      }
      var fc = window._formingCandles['d'];
      fc.high = Math.max(fc.high, (live.high != null && live.high > 0) ? live.high : live.current);
      fc.low  = Math.min(fc.low,  (live.low  != null && live.low  > 0) ? live.low  : live.current);
      fc.close = live.current;
      if (bigCandleSeries) bigCandleSeries.update(fc);
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
  for (var i = 0; i < data.length; i++) {
    if (i < period - 1) continue;
    var sum = 0;
    for (var j = 0; j < period; j++) {
      sum += data[i - j].close || data[i - j].value;
    }
    sma.push({ time: data[i].time, value: sum / period });
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
  try {
    var res = await fetch("/api/scanx/news/market-sentiment");
    if (!res.ok) return;
    var data = await res.json();
    var score = data.score;
    var label = data.label || "neutral";
    var summary = data.summary || "Market data is being processed.";

    var s = score != null ? score : 50;
    var labelColor = '#a1a1aa';
    if (label.toLowerCase() === 'positive' || label.toLowerCase() === 'bullish') labelColor = '#26a69a';
    else if (label.toLowerCase() === 'negative' || label.toLowerCase() === 'bearish') labelColor = '#ef5350';
    var scoreColor = '#a1a1aa';
    if (s > 60) scoreColor = '#26a69a';
    else if (s < 40) scoreColor = '#ef5350';

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
  if (!listData || listData.length === 0) {
    var msg = containerId === 'activeList' ? 'No volume data yet' : 'No data available';
    container.innerHTML = '<div style="color:var(--text-secondary);padding:1rem 0;text-align:center;">' + msg + '</div>';
    return;
  }
  var isActive = containerId === 'activeList';
  var html = '';
  listData.forEach(function(s) {
    var ticker = s.ticker || '';
    var priceStr = (s.current_price != null) ? Number(s.current_price).toFixed(2) : ((s.price != null) ? Number(s.price).toFixed(2) : '--');
    var prevClose = (s.prev_close != null) ? Number(s.prev_close).toFixed(2) : (s.price ? Number(s.price).toFixed(2) : '--');
    var changePct = s.change_pct != null ? Number(s.change_pct) : 0;
    var cls = changePct >= 0 ? 'text-green' : 'text-red';
    var sign = changePct >= 0 ? '▲ +' : '▼ ';
    var arrow = changePct >= 0 ? '▲' : '▼';
    var companyName = s.company_name || s.name || ticker;
    var volDisplay = s.volume_display || (s.volume != null ? Number(s.volume).toLocaleString('en-IN') : '');
    var sectorTag = s.sector ? '<span style="font-size:0.7rem;color:#a1a1aa;background:rgba(255,255,255,0.04);padding:1px 8px;border-radius:10px;margin-left:6px;">' + s.sector + '</span>' : '';
    var logoHtml;
    if (s.logo) {
      var c = _tickerColor(ticker);
      logoHtml = '<div class="mv-avatar" style="background:' + c + ';border:none;display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:600;color:#fff;"><img src="' + s.logo + '" alt="" style="width:28px;height:28px;border-radius:4px;object-fit:contain;" onerror="this.parentElement.style.fontSize=\'0\'"></div>';
    } else {
      logoHtml = '<div class="mv-avatar" style="background:' + _tickerColor(ticker) + ';font-size:0;"></div>';
    }
    var keyMetric = '<div class="mv-change ' + cls + '">' + arrow + ' ' + Math.abs(changePct).toFixed(2) + '%</div>' +
      '<div style="font-size:0.78rem;color:#a1a1aa;line-height:1.3;white-space:nowrap;">Volume: ' + volDisplay + '</div>';
    var isIndex = ['NIFTY','SENSEX','BANKNIFTY','FINNIFTY','MIDCAP','SMALLCAP','BSE500','NIFTYMIDCAP100','NIFTYSMLCAP100'].indexOf(ticker) !== -1;
    var moverLink = isIndex ? '/index_chart.html?ticker=' : '/stock.html?ticker=';
    html += '<div class="mover-row" id="mv-item-' + ticker + '" onclick="window.location.href=\'' + moverLink + encodeURIComponent(ticker) + '\'">' +
              '<div class="mover-left">' +
                logoHtml +
                '<div class="mover-info">' +
                  '<h4>' + ticker + sectorTag + '</h4>' +
                  '<p>' + companyName + '</p>' +
                '</div>' +
              '</div>' +
              '<div class="mover-right">' +
                '<div class="mover-price" id="mv-price-' + ticker + '">\u20B9' + priceStr + '</div>' +
                keyMetric +
                '<div class="mover-prev" id="mv-prev-' + ticker + '">Prev: \u20B9' + prevClose + '</div>' +
              '</div>' +
            '</div>';
  });
  container.innerHTML = html;
}

async function fetchMarketMovers() {
  try {
    var res = await fetch('/api/market-movers');
    if (res.ok) {
      var data = await res.json();
      onMarketMovers({ detail: data });
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
    try { _wlToken = sessionStorage.getItem('token'); } catch(e) {}
    var headers = {};
    if (_wlToken) headers['Authorization'] = 'Bearer ' + _wlToken;
    var res = await fetch('/api/watchlist', { headers: headers });
    if (res.ok) {
      var data = await res.json();
      if (data.watchlist && data.watchlist.length > 0) {
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
        if (match && match.logo) {
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
    var logoHtml;
    if (logo) {
      var c2 = _tickerColor(tkr);
      logoHtml = '<div class="wl-avatar" style="background:' + c2 + ';border:none;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:600;color:#fff;overflow:hidden;"><img src="' + logo + '" alt="" style="width:28px;height:28px;border-radius:4px;object-fit:contain;" onerror="this.parentElement.style.fontSize=\'0\'"></div>';
    } else {
      logoHtml = '<div class="wl-avatar" style="background:' + _tickerColor(tkr) + ';font-size:0;"></div>';
    }
    var isIdx = ['NIFTY','SENSEX','BANKNIFTY','FINNIFTY','MIDCAP','SMALLCAP','BSE500','NIFTYMIDCAP100','NIFTYSMLCAP100'].indexOf(tkr) !== -1;
    var wlLink = isIdx ? '/index_chart.html?ticker=' : '/stock.html?ticker=';
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
    if (cached && (Date.now() - cachedTs < 600000)) {
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
