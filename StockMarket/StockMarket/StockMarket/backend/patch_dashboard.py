"""
Safely patches dashboard.js with a Python script.
Only touches the 3 targeted regions, uses AST-free approach since it's JS.
"""

with open('../frontend/dashboard.js', 'r', encoding='utf-8') as f:
    content = f.read()

# ── PATCH 1: Fix loadData cacheInterval mapping for 1W and 1M ─────────────────
OLD1 = """    // Resolve interval from range for cache key
    var cacheInterval = range;
    if (range === '1D') cacheInterval = '5m';
    else if (range === '1W') cacheInterval = '15m';"""

NEW1 = """    // Resolve interval from range for cache key
    var cacheInterval = range;
    if      (range === '1D') cacheInterval = '5m';
    else if (range === '1W') cacheInterval = 'weekly';
    else if (range === '1M') cacheInterval = 'monthly';"""

# ── PATCH 2: Fix the URL routing in loadData ──────────────────────────────────
OLD2 = """      var url = '/api/stock-data/range?ticker=' + encodeURIComponent(ticker) + '&range=' + range;
      if (range === '1D')                                         url = '/api/stock-data/intraday?ticker=' + encodeURIComponent(ticker) + '&interval=5m';
      else if (range === '1W')                                    url = '/api/stock-data/intraday?ticker=' + encodeURIComponent(ticker) + '&interval=15m';
      else if (['5m','15m','30m','1h'].indexOf(range) !== -1)     url = '/api/stock-data/intraday?ticker=' + encodeURIComponent(ticker) + '&interval=' + range;"""

NEW2 = """      // Build API URL for each timeframe
      var url;
      if (range === '1m')                                         url = '/api/stock-data/intraday?ticker=' + encodeURIComponent(ticker) + '&interval=1m';
      else if (range === '1D')                                    url = '/api/stock-data/intraday?ticker=' + encodeURIComponent(ticker) + '&interval=5m';
      else if (range === '1W')                                    url = '/api/stock-data/weekly?ticker='   + encodeURIComponent(ticker);
      else if (range === '1M')                                    url = '/api/stock-data/monthly?ticker='  + encodeURIComponent(ticker);
      else if (['5m','15m','30m','1h'].indexOf(range) !== -1)     url = '/api/stock-data/intraday?ticker=' + encodeURIComponent(ticker) + '&interval=' + range;
      else                                                        url = '/api/stock-data/range?ticker='    + encodeURIComponent(ticker) + '&range=' + range;"""

# ── PATCH 3: Fix processBigChartPrice intraday candle snapping ────────────────
OLD3 = """    var intervalMin = { '5m':5, '15m':15, '30m':30, '1h':60 }[activeRange];
    var isIntraday = intervalMin !== undefined;

    if (isIntraday) {
      var ms = intervalMin * 60 * 1000;
      var snapped = Math.floor(now.getTime() / ms) * ms;
      var snappedSec = snapped / 1000;
      if (!window._formingCandles) window._formingCandles = {};
      if (!window._formingCandles['i'] || window._formingCandles['i'].time !== snappedSec) {
        // Use previous candle's close as the new candle's open
        var prevClose = window._formingCandles['i'] ? window._formingCandles['i'].close : (window._lastCandleClose || live.current);
        window._formingCandles['i'] = { time: snappedSec, open: prevClose, high: Math.max(prevClose, live.current), low: Math.min(prevClose, live.current), close: live.current };
      }
      var fc = window._formingCandles['i'];
      fc.high = Math.max(fc.high, live.current);
      fc.low  = Math.min(fc.low,  live.current);
      fc.close = live.current;
      if (bigCandleSeries) bigCandleSeries.update(fc);"""

NEW3 = """    var intervalMin = { '1m':1, '5m':5, '15m':15, '30m':30, '1h':60 }[activeRange];
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
        // Guard: wait for historical data before building live candle
        if (window._lastCandleClose === null) return;
        // Open = previous candle's close (never live.current — prevents gap candles)
        var prevClose = window._formingCandles['i'] ? window._formingCandles['i'].close : window._lastCandleClose;
        window._formingCandles['i'] = { time: snappedSec, open: prevClose, high: Math.max(prevClose, live.current), low: Math.min(prevClose, live.current), close: live.current };
      }
      var fc = window._formingCandles['i'];
      fc.high = Math.max(fc.high, live.current);
      fc.low  = Math.min(fc.low,  live.current);
      fc.close = live.current;
      if (bigCandleSeries) bigCandleSeries.update(fc);"""

# Apply all patches
errors = []
for i, (old, new) in enumerate([(OLD1, NEW1), (OLD2, NEW2), (OLD3, NEW3)], 1):
    if old in content:
        content = content.replace(old, new, 1)
        print(f"PATCH {i}: Applied OK")
    else:
        errors.append(i)
        print(f"PATCH {i}: NOT FOUND — searching for partial match...")
        # Find closest match
        for line in old.split('\n')[:3]:
            line = line.strip()
            if line and line in content:
                print(f"  Found partial: {line[:70]}")
                break

if errors:
    print(f"WARNING: Patches {errors} could not be applied. File NOT written.")
else:
    with open('../frontend/dashboard.js', 'w', encoding='utf-8') as f:
        f.write(content)
    print("SUCCESS: All 3 patches applied to dashboard.js")
