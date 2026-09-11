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

  
  var urlParams = new URLSearchParams(window.location.search);
  var urlTicker = urlParams.get('ticker');
  if (urlTicker && DASHBOARD_TICKERS.indexOf(urlTicker) === -1) {
      DASHBOARD_TICKERS.push(urlTicker);
  }

  var reconnectAttempt = 0;
  var pingTimer        = null;
  var pongTimeout      = null;
  var ws               = null;
  var wsActive         = false;  // true while the page wants a WS connection
  // FE-06: readyState === OPEN only means the socket itself hasn't errored
  // or closed -- it does NOT mean data is still arriving. A connection that
  // silently stopped receiving broadcasts (server-side drop, dead proxy
  // hop, etc.) can sit at readyState OPEN indefinitely. Track the last
  // time ANY message actually arrived so callers can tell "connected" apart
  // from "connected and actually alive".
  var lastMessageTime  = 0;

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
          // Accept all price updates — the backend already throttles at 200ms (AngelOne)
          // and 30s (yfinance fallback). Stale-check based on server clock offset was
          // silently dropping all messages when the offset got miscalibrated.
          window._lastWsPriceTime = Date.now();
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
          break;

        case 'ping':
          // Respond to server pings so it knows we're alive
          if (ws && ws.readyState === WebSocket.OPEN) {
            try { ws.send(JSON.stringify({ type: 'pong' })); } catch (e) {}
          }
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

    newWs.onmessage = function (evt) {
      lastMessageTime = Date.now();
      try { handleMessage(JSON.parse(evt.data)); } catch (e) {}
    };

    newWs.onclose = function () {
      console.log('[DashWS] Disconnected');
      stopPing();
      scheduleReconnect();
    };

    newWs.onerror = function (e) {
      console.error('[DashWS] Error:', e);
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
    },
    /** FE-06: connected AND actually receiving messages -- catches a
     * silent drop that isConnected() alone would miss (readyState can
     * stay OPEN with no data flowing). 45s = 30s ping interval + margin. */
    isHealthy: function () {
      if (!ws || ws.readyState !== WebSocket.OPEN) return false;
      if (lastMessageTime === 0) return false; // never received anything yet
      return (Date.now() - lastMessageTime) < 45000;
    }
  };
}());