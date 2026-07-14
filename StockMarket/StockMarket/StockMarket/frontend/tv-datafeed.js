/* TradingView Advanced Charts — Custom Datafeed API
 * Connects our backend REST APIs + AngelOne WebSocket to the chart.
 */

var TVDatafeed = (function () {
  'use strict';

  // ── Resolution helpers ──────────────────────────────────────────────
  var RESOLUTION_MAP = {
    '1':  { interval: '1m',  type: 'intraday' },
    '5':  { interval: '5m',  type: 'intraday' },
    '15': { interval: '15m', type: 'intraday' },
    '30': { interval: '30m', type: 'intraday' },
    '60': { interval: '1h',  type: 'intraday' },
    '120':{ interval: '1h',  type: 'intraday' },
    '240':{ interval: '1h',  type: 'intraday' },
    '1D': { interval: '1D',  type: 'daily' },
    '1W': { interval: '1W',  type: 'weekly' },
    '1M': { interval: '1M',  type: 'monthly' },
  };
  var INTRADAY_RESOLUTIONS = ['1','5','15','30','60','120','240'];

  function resolutionToApi(res) {
    return RESOLUTION_MAP[res] || { interval: res, type: 'daily' };
  }

  // TV bar time expects UTC epoch MILLISECONDS.
  function toTVTime(v) {
    if (v == null) return 0;
    if (typeof v === 'number') return Math.floor(v * 1000);
    if (typeof v === 'string') {
      if (/^\d{4}-\d{2}-\d{2}$/.test(v)) {
        return new Date(v + 'T00:00:00Z').getTime();
      }
      var n = Number(v);
      if (!isNaN(n)) return Math.floor(n * 1000);
      return new Date(v).getTime();
    }
    return 0;
  }

  // ── NSE stock info helper ───────────────────────────────────────────
  // Hard-coded common symbols; unknown symbols are resolved via API.
  var KNOWN_SYMBOLS = {
    'NIFTY':      { ticker:'NIFTY',     name:'NIFTY',     description:'Nifty 50 Index',                     type:'index',  exchange:'NSE', pricescale:100, minmov:1 },
    'BANKNIFTY':  { ticker:'BANKNIFTY', name:'BANKNIFTY', description:'Nifty Bank Index',                    type:'index',  exchange:'NSE', pricescale:100, minmov:1 },
    'SENSEX':     { ticker:'SENSEX',    name:'SENSEX',    description:'BSE Sensex Index',                    type:'index',  exchange:'BSE', pricescale:100, minmov:1 },
    'FINNIFTY':   { ticker:'FINNIFTY',  name:'FINNIFTY',  description:'Nifty Financial Services Index',      type:'index',  exchange:'NSE', pricescale:100, minmov:1 },
    'RELIANCE':   { ticker:'RELIANCE',  name:'RELIANCE',  description:'Reliance Industries Ltd',             type:'stock',  exchange:'NSE', pricescale:100, minmov:1 },
    'TCS':        { ticker:'TCS',       name:'TCS',       description:'Tata Consultancy Services Ltd',       type:'stock',  exchange:'NSE', pricescale:100, minmov:1 },
    'HDFCBANK':   { ticker:'HDFCBANK',  name:'HDFCBANK',  description:'HDFC Bank Ltd',                       type:'stock',  exchange:'NSE', pricescale:100, minmov:1 },
    'INFY':       { ticker:'INFY',      name:'INFY',      description:'Infosys Ltd',                         type:'stock',  exchange:'NSE', pricescale:100, minmov:1 },
    'ICICIBANK':  { ticker:'ICICIBANK', name:'ICICIBANK', description:'ICICI Bank Ltd',                      type:'stock',  exchange:'NSE', pricescale:100, minmov:1 },
    'SBIN':       { ticker:'SBIN',      name:'SBIN',      description:'State Bank of India',                 type:'stock',  exchange:'NSE', pricescale:100, minmov:1 },
  };

  function makeSymbolInfo(base) {
    return {
      ticker:               base.ticker,
      name:                 base.name,
      description:          base.description,
      type:                 base.type || 'stock',
      session:              '0915-1530',
      timezone:             'Asia/Kolkata',
      exchange:             base.exchange || 'NSE',
      minmov:               base.minmov || 1,
      pricescale:           base.pricescale || 100,
      minmove2:             0,
      fractional:           false,
      has_intraday:         true,
      intraday_multipliers: ['1','5','15','30','60'],
      has_daily:            true,
      has_weekly_and_monthly: true,
      has_empty_bars:       true,
      visible_plots_set:    'ohlcv',
      volume_precision:     0,
      data_status:          'streaming',
      supported_resolutions: ['1','5','15','30','60','120','240','1D','1W','1M'],
    };
  }

  // Fetch unknown symbol info from backend
  function fetchSymbolInfo(symbolName) {
    // Check ALL_STOCKS first (preloaded from backend, instant)
    var upper = (symbolName || '').toUpperCase().replace('.NS', '').replace('.BO', '');
    var match = (window.ALL_STOCKS || []).find(function (s) {
      return s.ticker === upper;
    });
    if (match) {
      return Promise.resolve(makeSymbolInfo({
        ticker: match.ticker, name: match.name, description: match.name,
        exchange: match.exchange || 'NSE', type: 'stock'
      }));
    }
    // Fallback: use the ticker name itself — always resolve so TV shows the chart
    // (the chart will display "No data" if getBars returns empty, which is a better
    // UX than "unknown symbol" error)
    return Promise.resolve(makeSymbolInfo({
      ticker: upper, name: upper, description: upper,
      exchange: 'NSE', type: 'stock'
    }));
  }

  var configurationData = {
    supported_resolutions: ['1','5','15','30','60','120','240','1D','1W','1M'],
    exchanges: [
      { value: 'NSE', name: 'NSE', desc: 'National Stock Exchange' },
      { value: 'BSE', name: 'BSE', desc: 'Bombay Stock Exchange' },
    ],
    symbols_types: [
      { name: 'Stock', value: 'stock' },
      { name: 'Index', value: 'index' },
    ],
    supports_search: true,
    supports_group_request: false,
    supports_marks: false,
    supports_timescale_marks: false,
    supports_time: true,
  };

  // ── Real-time subscription state ────────────────────────────────────
  var _subscriptions = {};   // subscriberUID -> { symbolInfo, resolution, callback, lastBar }
  var _streamingActive = false;
  var _lastWSUpdateTs = 0;

  // WS reconnect gap-fill: fetch missed candles since last update
  function _fillWSGap(ticker, resolution) {
    var ri = resolutionToApi(resolution);
    if (!ri || ri.type !== 'intraday') return;
    var since = Math.floor(_lastWSUpdateTs / 1000);
    if (since === 0) return;
    var url = '/api/stock-data/intraday/since?ticker=' + encodeURIComponent(ticker) + '&interval=' + ri.interval + '&since=' + since;
    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!Array.isArray(data) || data.length === 0) return;
        // Find the subscription for this ticker
        var uid = Object.keys(_subscriptions).find(function (u) {
          return _subscriptions[u].symbolInfo.ticker === ticker;
        });
        if (!uid) return;
        var sub = _subscriptions[uid];
        // Replay each missed bar
        data.forEach(function (p) {
          var bar = {
            time: toTVTime(p.time),
            open: p.open, high: p.high, low: p.low, close: p.close,
            volume: p.volume || 0,
          };
          if (!_validateOHLC(bar.open, bar.high, bar.low, bar.close)) {
            var fixed = _fixOHLC(bar.open, bar.high, bar.low, bar.close);
            bar.open = fixed.open; bar.high = fixed.high; bar.low = fixed.low; bar.close = fixed.close;
          }
          // Only update lastBar if this bar is newer
          if (!sub.lastBar || bar.time > sub.lastBar.time) {
            sub.lastBar = bar;
          }
          if (sub.callback) sub.callback(bar);
        });
      })
      .catch(function () {});
  }

  function _startStreaming() {
    if (_streamingActive) return;
    _streamingActive = true;

    // Gap-fill: fetch missed bars since last WS update before resubscribing
    var uids = Object.keys(_subscriptions);
    uids.forEach(function (uid) {
      var sub = _subscriptions[uid];
      if (sub && sub.symbolInfo) {
        _fillWSGap(sub.symbolInfo.ticker, sub.resolution);
      }
    });

    window.addEventListener('dashboard_price_update', _onWSUpdate);
  }

  function _stopStreaming() {
    _streamingActive = false;
    window.removeEventListener('dashboard_price_update', _onWSUpdate);
  }

  function _onWSUpdate(evt) {
    var raw = evt.detail;
    if (!raw) return;
    _lastWSUpdateTs = Date.now();
    var prices = raw.prices || raw;
    if (!prices) return;
    var uids = Object.keys(_subscriptions);
    if (uids.length === 0) return;
    uids.forEach(function (uid) {
      var sub = _subscriptions[uid];
      if (!sub) return;
      var d = prices[sub.symbolInfo.ticker];
      if (!d || d.current === undefined || d.current === null) return;
      _processTick(sub, d);
    });
  }

  function _getBarTime(nowMs, resolution) {
    if (resolution === '1D') {
      var d = new Date(nowMs);
      return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate())).getTime();
    }
    var minMap = { '1':1, '5':5, '15':15, '30':30, '60':60, '120':120, '240':240 };
    var mins = minMap[resolution];
    if (!mins) return nowMs;
    // NSE session starts at 09:15 IST = 33300 seconds = 33300000 ms past IST midnight
    var NSE_SESSION_START_MS = 33300000;
    // Convert nowMs to IST milliseconds-since-midnight
    var d = new Date(nowMs);
    var istOffset = 330 * 60 * 1000; // +5:30 in ms
    var istMs = nowMs + istOffset;
    var istMidnight = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
    var istDayMs = (istMs - istMidnight + 86400000) % 86400000;

    // If before session start, snap to session start
    if (istDayMs < NSE_SESSION_START_MS) {
      return istMidnight + NSE_SESSION_START_MS - istOffset;
    }

    var bucketMs = mins * 60 * 1000;
    var offsetFromSession = istDayMs - NSE_SESSION_START_MS;
    var snapped = Math.floor(offsetFromSession / bucketMs) * bucketMs + NSE_SESSION_START_MS;
    return istMidnight + snapped - istOffset;
  }

  function _validateOHLC(o, h, l, c) {
    if (h < o || h < c) return false;
    if (l > o || l > c) return false;
    if (h < l) return false;
    return true;
  }

  function _fixOHLC(o, h, l, c) {
    var fh = Math.max(h, o, c);
    var fl = Math.min(l, o, c);
    return { open: o, high: fh, low: fl, close: c };
  }

  function _processTick(sub, tick) {
    var nowMs = Date.now() + (window._serverClockOffset || 0);
    var barTime = _getBarTime(nowMs, sub.resolution);
    var lastBar = sub.lastBar;
    if (!lastBar || barTime !== lastBar.time) {
      var o = tick.current, h = tick.current, l = tick.current, c = tick.current;
      if (!_validateOHLC(o, h, l, c)) {
        var fixed = _fixOHLC(o, h, l, c);
        o = fixed.open; h = fixed.high; l = fixed.low; c = fixed.close;
      }
      sub.lastBar = { time: barTime, open: o, high: h, low: l, close: c, volume: 0 };
    } else {
      var h2 = Math.max(lastBar.high, tick.current);
      var l2 = Math.min(lastBar.low, tick.current);
      var c2 = tick.current;
      if (!_validateOHLC(lastBar.open, h2, l2, c2)) {
        var fixed2 = _fixOHLC(lastBar.open, h2, l2, c2);
        h2 = fixed2.high; l2 = fixed2.low; c2 = fixed2.close;
      }
      sub.lastBar.high = h2;
      sub.lastBar.low  = l2;
      sub.lastBar.close = c2;
    }
    if (sub.callback) {
      sub.callback(sub.lastBar);
    }
  }

  // ── Datafeed object ─────────────────────────────────────────────────
  var datafeed = {
    onReady: function (callback) {
      setTimeout(function () { callback(configurationData); }, 0);
    },

    searchSymbols: function (userInput, exchange, symbolType, onResult) {
      var query = (userInput || '').toUpperCase();
      var results = [];
      Object.keys(KNOWN_SYMBOLS).forEach(function (t) {
        if (t.indexOf(query) !== -1) {
          var s = KNOWN_SYMBOLS[t];
          results.push({ symbol: s.ticker, ticker: s.ticker, description: s.description, exchange: s.exchange, type: s.type });
        }
      });
      if (results.length > 0) {
        setTimeout(function () { onResult(results); }, 0);
      } else {
        // Try backend search
        fetch('/api/stock-data/search?query=' + encodeURIComponent(userInput))
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (Array.isArray(data)) onResult(data);
            else onResult([]);
          })
          .catch(function () { onResult([]); });
      }
    },

    resolveSymbol: function (symbolName, onResolve, onError) {
      var upper = (symbolName || '').toUpperCase().replace('.NS', '');
      var known = KNOWN_SYMBOLS[upper];
      if (known) {
        setTimeout(function () { onResolve(makeSymbolInfo(known)); }, 0);
        return;
      }
      fetchSymbolInfo(symbolName).then(function (info) {
        if (info) {
          onResolve(info);
        } else {
          onError('unknown_symbol');
        }
      });
    },

    getBars: function (symbolInfo, resolution, periodParams, onHistory, onError) {
      var ticker = symbolInfo.ticker || symbolInfo.name || '';
      var from = periodParams.from;
      var to = periodParams.to;
      var ri = resolutionToApi(resolution);

      function makeBars(data) {
        if (!Array.isArray(data) || data.length === 0) {
          return [];
        }
        return data.map(function (p) {
          var o = p.open, h = p.high, l = p.low, c = p.close;
          if (!_validateOHLC(o, h, l, c)) {
            var fixed = _fixOHLC(o, h, l, c);
            o = fixed.open; h = fixed.high; l = fixed.low; c = fixed.close;
          }
          return {
            time:   toTVTime(p.time),
            open:   o,
            high:   h,
            low:    l,
            close:  c,
            volume: p.volume || 0,
          };
        }).filter(function (b) { return b.time >= from * 1000 && b.time < to * 1000; });
      }

      if (ri.type === 'intraday') {
        var url = '/api/stock-data/intraday?ticker=' + encodeURIComponent(ticker) + '&interval=' + ri.interval;
        fetch(url)
          .then(function (r) { return r.json(); })
          .then(function (data) {
            var bars = makeBars(data);
            onHistory(bars, { noData: bars.length === 0 });
          })
          .catch(function (e) { onError(e); });
      } else if (ri.type === 'weekly') {
        fetch('/api/stock-data/weekly?ticker=' + encodeURIComponent(ticker))
          .then(function (r) { return r.json(); })
          .then(function (data) {
            var bars = makeBars(data);
            onHistory(bars, { noData: bars.length === 0 });
          })
          .catch(function (e) { onError(e); });
      } else if (ri.type === 'monthly') {
        fetch('/api/stock-data/monthly?ticker=' + encodeURIComponent(ticker))
          .then(function (r) { return r.json(); })
          .then(function (data) {
            var bars = makeBars(data);
            onHistory(bars, { noData: bars.length === 0 });
          })
          .catch(function (e) { onError(e); });
      } else {
        var url2 = '/api/stock-data/range?ticker=' + encodeURIComponent(ticker) + '&range=ALL';
        fetch(url2)
          .then(function (r) { return r.json(); })
          .then(function (data) {
            var bars = makeBars(data);
            onHistory(bars, { noData: bars.length === 0 });
          })
          .catch(function (e) { onError(e); });
      }
    },

    subscribeBars: function (symbolInfo, resolution, onRealtimeCallback, subscriberUID) {
      _subscriptions[subscriberUID] = {
        symbolInfo: symbolInfo,
        resolution: resolution,
        callback: onRealtimeCallback,
        lastBar: null,
      };
      // Ensure WS is subscribed to this ticker
      if (window.DashboardWS) {
        window.DashboardWS.addTickers([symbolInfo.ticker]);
      }
      _startStreaming();
    },

    unsubscribeBars: function (subscriberUID) {
      delete _subscriptions[subscriberUID];
      if (Object.keys(_subscriptions).length === 0) {
        _stopStreaming();
      }
    },

    getServerTime: function (callback) {
      fetch('/api/time')
        .then(function (r) { return r.json(); })
        .then(function (t) { callback(t.server_time); })
        .catch(function () { callback(Math.floor(Date.now() / 1000)); });
    },
  };

  return datafeed;
})();
