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
      if (!prices || !prices[ticker] || !prices[ticker].current) return;
      var wsLive = prices[ticker];
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

    if (pEl2) pEl2.innerText = fmtPrice(price);

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
    if (pEl2) {
      var prevPrice = window.lastLivePrice;
      window.lastLivePrice = price;
    }

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

    // Skip forming candle and OHLC updates while loadData is in progress
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
      var istMidnight = new Date(now.getFullYear(), now.getMonth(), now.getDate()); // IST midnight
      var sessionStartMs = istMidnight.getTime() + SESSION_START_MIN * 60000;
      var offsetFromSession = now.getTime() - sessionStartMs;
      var snappedMs;
      if (offsetFromSession < 0) {
        snappedMs = sessionStartMs;
      } else {
        snappedMs = sessionStartMs + Math.floor(offsetFromSession / ms) * ms;
      }
      // Convert IST ms to UTC epoch seconds for Lightweight Charts
      var IST_OFFSET_MS = 5.5 * 3600 * 1000;
      var snappedSec = Math.floor((snappedMs - IST_OFFSET_MS) / 1000);
      if (!window._formingCandles) window._formingCandles = {};
      if (!window._formingCandles['i'] || window._formingCandles['i'].time !== snappedSec) {
        if (window._lastCandleClose === null) return;
        var existing = window._lastHistoricalCandle;
        if (existing && existing.time === snappedSec) {
          window._formingCandles['i'] = {
            time: snappedSec,
            open: existing.open,
            high: Math.max(existing.high, live.current),
            low: Math.min(existing.low, live.current),
            close: live.current
          };
        } else {
          var openPrice = live.current > 0 ? live.current : (window._formingCandles['i'] ? window._formingCandles['i'].close : window._lastCandleClose);
          window._formingCandles['i'] = { time: snappedSec, open: openPrice, high: Math.max(openPrice, live.current), low: Math.min(openPrice, live.current), close: live.current };
        }
      }
      var fc = window._formingCandles['i'];
      fc.high = Math.max(fc.high, live.current);
      fc.low  = Math.min(fc.low,  live.current);
      fc.close = live.current;
      window._lastHistoricalCandle = { time: snappedSec, open: fc.open, high: fc.high, low: fc.low, close: fc.close };
      if (bigCandleSeries) bigCandleSeries.update(fc);
      if (pEl2) pEl2.innerText = fmtPrice(live.current);
      if (oEl2) oEl2.innerText = fc.open.toFixed(2);
      if (hEl2) hEl2.innerText = fc.high.toFixed(2);
      if (lEl2) lEl2.innerText = fc.low.toFixed(2);
      if (cEl2) cEl2.innerText = live.current.toFixed(2);
    } else {
