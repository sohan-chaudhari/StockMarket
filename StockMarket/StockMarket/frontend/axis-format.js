/*
 * axis-format.js -- shared TradingView-style X-axis tick formatter for LEVERAGE.
 *
 * Frontend-only, pure/stateless with respect to chart data: it never mutates
 * candles, never calls the network, and never touches the intraday
 * sequential-index architecture. It only maps a Lightweight Charts tick
 * (time, markType) to a display label, using the real IST date/time.
 *
 * Design:
 *   - one shared implementation used by the big chart, mini chart, market
 *     chart and indicator panes (registered per chart instance).
 *   - honours Lightweight Charts `markType` (Year/Month/DayOfMonth/Time)
 *     and refines it with LEVERAGE trading-session rules.
 *   - real trading-day / month / year boundaries are derived from the real
 *     bar sequence (`_intradayBarTimes`), never from `day == 1|2|3` or
 *     `barsIntoDay < stepBars`.
 *   - future whitespace (sequential index beyond the last real bar) is
 *     suppressed instead of fabricating non-trading dates.
 *
 * Display timezone is fixed to IST (Asia/Kolkata) via a constant +5.5h
 * offset, independent of the browser timezone.
 */
(function () {
  'use strict';

  var LWC = window.LightweightCharts;
  // LWC v4.1.3: Year=0, Month=1, DayOfMonth=2, Time=3, TimeWithSeconds=4
  var TMT = (LWC && LWC.TickMarkType) || {
    Year: 0, Month: 1, DayOfMonth: 2, Time: 3, TimeWithSeconds: 4
  };

  var MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
             'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var IST_MS = 5.5 * 3600 * 1000;
  var INTRADAY = {
    '1m': 1, '3m': 1, '5m': 1, '10m': 1, '15m': 1, '30m': 1,
    '1h': 1, '2h': 1, '4h': 1
  };
  var BAR_MIN = {
    '1m': 1, '3m': 3, '5m': 5, '10m': 10, '15m': 15, '30m': 30,
    '1h': 60, '2h': 120, '4h': 240
  };

  var CFG = new WeakMap();   // chart -> config
  var ST  = new WeakMap();   // chart -> render state { bars, last }

  function pad2(n) { n = n | 0; return n < 10 ? '0' + n : '' + n; }
  function isIntradayRange(r) { return !!INTRADAY[r]; }

  // epoch seconds -> IST calendar parts. Timezone-independent.
  function parts(epochSec) {
    if (epochSec == null || !isFinite(epochSec)) return null;
    var d = new Date(epochSec * 1000 + IST_MS);
    if (isNaN(d.getTime())) return null;
    return {
      y: d.getUTCFullYear(), moi: d.getUTCMonth(), d: d.getUTCDate(),
      hh: d.getUTCHours(), mm: d.getUTCMinutes()
    };
  }
  function dayKeyOf(p) { return p.y * 10000 + (p.moi + 1) * 100 + p.d; }
  function monKeyOf(p) { return p.y * 100 + (p.moi + 1); }

  // Accept number | {year,month,day} | 'YYYY-MM-DD' | numeric string -> epoch seconds.
  function toEpoch(t) {
    if (t == null) return null;
    if (typeof t === 'number') return isFinite(t) ? t : null;
    if (typeof t === 'object' && t.year) return Date.UTC(t.year, t.month - 1, t.day) / 1000;
    if (typeof t === 'string') {
      if (/^\d{4}-\d{2}-\d{2}$/.test(t)) {
        return Date.UTC(+t.slice(0, 4), +t.slice(5, 7) - 1, +t.slice(8, 10)) / 1000;
      }
      var n = Number(t);
      return isNaN(n) ? null : n;
    }
    return null;
  }

  function state(chart) {
    var s = ST.get(chart);
    if (!s) { s = { last: null, seen: { d: {}, m: 0, y: 0 }, lastCall: 0, renderIdxs: null, rangeKey: null, labels: null, labelKey: null, passIdx: null, barSecs: null, bt0: null }; ST.set(chart, s); }
    return s;
  }

  function onRangeChange(chart) {
    return function () {
      // Reset the render memo + labelled sets so the next pass re-anchors
      // day/month/year boundaries. The visible-bar count is read live below.
      var s = state(chart);
      // Only the render memo is reset here; the "already labelled" sets are
      // reset by the visible-range key check inside tick(), which cannot fire
      // mid-render.
      s.last = null;
    };
  }

  function defaultConfig() {
    return {
      getRange: function () { return window.activeRange || 'ALL'; },
      getBarTimes: function () { return window._intradayBarTimes || null; },
      getBarSecs: function () { return window._intradayBarSecs || null; },
      resolveEpoch: function (t) {
        var bt = window._intradayBarTimes, bs = window._intradayBarSecs;
        if (bt && bs && typeof t === 'number') {
          var i = Math.round(t / bs);
          return (i >= 0 && i < bt.length) ? bt[i] : null;
        }
        return toEpoch(t);
      }
    };
  }

  function register(chart, cfg) {
    if (!chart || !chart.timeScale) return;
    var base = defaultConfig();
    var c = cfg || {};
    c.getRange     = c.getRange     || base.getRange;
    c.getBarTimes  = c.getBarTimes  || base.getBarTimes;
    c.getBarSecs   = c.getBarSecs   || base.getBarSecs;
    c.resolveEpoch = c.resolveEpoch || base.resolveEpoch;
    CFG.set(chart, c);
    state(chart);
    try { chart.timeScale().subscribeVisibleLogicalRangeChange(onRangeChange(chart)); } catch (e) {}

    // Session-date overlay (the strip's dates are painted by us, not the library).
    ensureOverlay(chart);
    try { chart.timeScale().subscribeVisibleLogicalRangeChange(function () { drawOverlay(chart); }); } catch (e) {}
    try { if (typeof chart.subscribeSizeChange === 'function') chart.subscribeSizeChange(function () { drawOverlay(chart); }); } catch (e) {}
    var rec0 = OVERLAY.get(chart);
    if (rec0 && !rec0.timer) rec0.timer = setInterval(function () { drawOverlay(chart); }, 1200);
    setTimeout(function () { drawOverlay(chart); }, 0);
  }

  function reset(chart) {
    if (!chart) return;
    var s = ST.get(chart);
    if (s) { s.last = null; s.seen = { d: {}, m: 0, y: 0 }; s.renderIdxs = null; s.labels = null; s.labelKey = null; s.passIdx = null; s.barSecs = null; s.bt0 = null; }
    var rec = OVERLAY.get(chart);
    if (rec) { rec.sig = ''; drawOverlay(chart); }
  }

  function barsOf(chart) {
    // Read the CURRENT visible range every call. A cached value could be stale
    // during initial load (empty/fitted range) and would wrongly push the
    // formatter into the date-only tier until the next range event.
    try {
      var lr = chart.timeScale().getVisibleLogicalRange();
      if (lr && lr.to != null && lr.from != null) {
        return Math.max(1, Math.round(lr.to - lr.from));
      }
    } catch (e) {}
    return 75;
  }

  function widthOf(chart) {
    try { var w = chart.timeScale().width(); return w > 0 ? w : 0; } catch (e) { return 0; }
  }

  // ------ intraday (5m/15m/30m/1h/4h and raw-epoch intraday) ------------------------------------------------------
  // One rule, with no per-tick "is this bar within N bars of the day start"
  // lookback window:
  //   - the FIRST tick of a trading day is dated ("16 Sep");
  //   - every later tick of that day shows its time ("10:30") while the view is
  //     zoomed in (about three sessions or fewer), so zooming in reads as times
  //     instead of the same date printed two or three times;
  //   - once the view is zoomed out past that, the rest of a day stays blank,
  //     which is what TradingView does when dates carry the axis.
  //
  // "Already dated" is tracked per day and reset at the start of every render
  // pass (see tick()), so a day can never be dated twice. It also guarantees the
  // axis can never come out empty: the first tick of every day in view always
  // gets its date, even if no tick lands on that day's 09:15 bar and even if the
  // day map has no entry for it.
  // ------ intraday (5m/15m/30m/1h/4h and raw-epoch intraday) --------------------
  // TIMES ONLY. Session DATES are painted by the overlay (drawOverlay) instead.
  //
  // Why this is not negotiable: lightweight-charts v4.1.3 memoises every tick
  // label in a 50-entry LRU keyed by the mark's OWN time (`class pn`, cacheKey =
  // the bar's time). A mark that has been formatted once is never formatted
  // again. So a label that depends on the zoom level -- or on which marks were
  // asked for before it -- is frozen the first time it is computed and can never
  // self-correct. Every "date the first tick of the day" scheme therefore leaves
  // duplicated dates on screen permanently, which is exactly the reported bug.
  //
  // A TIME label is always correct for its own bar no matter when it was
  // computed, so memoising this function is safe.
  function fmtIntraday(o) {
    var p = o.p;
    // Year/Month marks are resolved from the SYNTHETIC (index-based) timeline,
    // so they do not correspond to real months. The overlay owns that job.
    if (o.mt === TMT.Year || o.mt === TMT.Month) return '';

    // While the overlay is active it draws EVERY intraday label (times and dates)
    // in one layout pass, so the library must draw none -- otherwise a time and a
    // session date are painted on top of each other ("152150ct"). Suppression has
    // to be total: whether a given mark is suppressed cannot depend on the zoom,
    // because the decision is frozen in the library's label cache.
    if (o.overlay) return '';

    // Fallback when the overlay cannot attach: still show a date, but only on the
    // day's OWN first bar. That is a pure function of the bar, so it can never
    // produce a duplicate.
    if (o.dayOffset === 0) {
      var dstr = pad2(p.d) + ' ' + MON[p.moi];
      return (p.moi === 0 && p.d === 1) ? (dstr + ' ' + p.y) : dstr;
    }
    return o.wide ? '' : (pad2(p.hh) + ':' + pad2(p.mm));
  }

  // ------ daily / weekly / monthly (business-day times) ---------------------------------------------------------------------
  function fmtDay(o) {
    var p = o.p;
    var mon = MON[p.moi];
    var yr  = '' + p.y;
    if (o.mt === TMT.Year)  return yr;
    if (o.mt === TMT.Month) return (p.moi === 0 || o.newYr) ? (mon + ' ' + yr) : mon;
    if (o.bars <= 40)  return '' + p.d;             // close zoom: day number
    if (o.bars <= 300) return pad2(p.d) + ' ' + mon; // normal: "08 Oct"
    return mon;                                      // zoomed out: month
  }

  // ------ session-date overlay -------------------------------------------------
  // A small canvas laid over the time-scale strip. It paints exactly ONE date per
  // trading session, at that session's first bar, thinning by real pixel distance
  // -- the way TradingView does it. Because it is our own canvas, redrawn from the
  // bar sequence and the visible range, it is immune to the library's per-mark
  // label cache and can never duplicate a date.
  var OVERLAY = new WeakMap();

  function dayStartOf(bt, idx) {
    var p0 = parts(bt[idx]);
    if (!p0) return idx;
    var k0 = dayKeyOf(p0), i = idx, guard = 0;
    while (i > 0 && guard++ < 3000) {
      var pp = parts(bt[i - 1]);
      if (!pp || dayKeyOf(pp) !== k0) break;
      i--;
    }
    return i;
  }

  function ensureOverlay(chart) {
    var rec = OVERLAY.get(chart);
    if (rec && rec.canvas && rec.canvas.isConnected) return rec;
    var host = null;
    try { host = chart.chartElement(); } catch (e) {}
    if (!host) return null;
    try { if (getComputedStyle(host).position === 'static') host.style.position = 'relative'; } catch (e) {}
    var cv = document.createElement('canvas');
    cv.style.cssText = 'position:absolute;left:0;bottom:0;pointer-events:none;z-index:5;';
    host.appendChild(cv);
    rec = { canvas: cv, ctx: cv.getContext('2d'), sig: '', timer: null };
    OVERLAY.set(chart, rec);
    return rec;
  }

  function overlayReady(chart) {
    var rec = OVERLAY.get(chart);
    return !!(rec && rec.canvas && rec.canvas.isConnected);
  }

  function drawOverlay(chart) {
    var rec = ensureOverlay(chart);
    if (!rec || !rec.ctx) return;
    var cfg = CFG.get(chart) || defaultConfig();
    var bt = cfg.getBarTimes ? cfg.getBarTimes() : null;
    var w = 0, h = 0;
    try { w = chart.timeScale().width(); h = chart.timeScale().height(); } catch (e) {}
    if (!(w > 0) || !(h > 0)) return;

    // Not an intraday range, or no bar sequence: keep the strip empty.
    if (!isIntradayRange(cfg.getRange()) || !bt || !bt.length) {
      if (rec.sig !== 'off') { rec.sig = 'off'; rec.canvas.width = 1; rec.canvas.height = 1; }
      return;
    }
    var lr = null;
    try { lr = chart.timeScale().getVisibleLogicalRange(); } catch (e) {}
    if (!lr || lr.from == null || lr.to == null) return;

    var sig = Math.round(lr.from) + ':' + Math.round(lr.to) + ':' + w + ':' + h + ':' + bt.length;
    if (sig === rec.sig) return;
    rec.sig = sig;

    var dpr = window.devicePixelRatio || 1;
    rec.canvas.width = Math.round(w * dpr);
    rec.canvas.height = Math.round(h * dpr);
    rec.canvas.style.width = w + 'px';
    rec.canvas.style.height = h + 'px';
    var ctx = rec.ctx;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    var opts = {};
    try { opts = chart.options() || {}; } catch (e) {}
    var layout = opts.layout || {};
    var fs = layout.fontSize || 12;
    ctx.font = (layout.fontStyle ? layout.fontStyle + ' ' : '') + fs + 'px ' +
               (layout.fontFamily || '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif');
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = layout.textColor || '#d1d4dc';

    var iFrom = Math.max(0, Math.ceil(lr.from));
    var iTo = Math.min(bt.length - 1, Math.floor(lr.to));
    var pxPerBar = (iTo > iFrom) ? (w / (iTo - iFrom)) : 0;

    // -- One label per session, then times in the remaining gaps ---------------
    // The overlay owns the whole strip for intraday, so a date and a time can
    // never be painted over each other. Dates are placed first (they must never
    // be dropped), then times fill only the free space between them.
    var todo = [];
    var prevDay = iFrom > 0 ? parts(bt[iFrom - 1]) : null;
    var sessions = 0;
    for (var i = iFrom; i <= iTo; i++) {
      var p1 = parts(bt[i]);
      if (!p1) { prevDay = null; continue; }
      var isStart = !prevDay || dayKeyOf(p1) !== dayKeyOf(prevDay);
      prevDay = p1;
      if (!isStart) continue;
      sessions++;
      var lbl = pad2(p1.d) + ' ' + MON[p1.moi];
      if (p1.moi === 0 && p1.d === 1) lbl += ' ' + p1.y;
      todo.push({ idx: i, text: lbl, date: true });
    }

    // Times only while the view is genuinely intraday-ish; aligned to a fixed bar
    // grid (idx % step) so labels do not jump around while panning.
    var step = (pxPerBar > 0) ? Math.max(1, Math.round(64 / pxPerBar)) : 0;
    if (sessions <= 6 && step > 0) {
      for (var j = iFrom; j <= iTo; j++) {
        if (j % step !== 0) continue;
        var pj = parts(bt[j]);
        if (!pj) continue;
        todo.push({ idx: j, text: pad2(pj.hh) + ':' + pad2(pj.mm), date: false });
      }
    }
    todo.sort(function (a, b) {
      return ((b.date ? 1 : 0) - (a.date ? 1 : 0)) || (a.idx - b.idx);
    });

    var placed = [];
    var draws = [];
    for (var q = 0; q < todo.length; q++) {
      var item = todo[q];
      var x = null;
      try { x = chart.timeScale().logicalToCoordinate(item.idx); } catch (e) {}
      if (x == null || !isFinite(x)) continue;
      var hw = 0;
      try { hw = ctx.measureText(item.text).width / 2; } catch (e) { hw = 16; }
      var dx = x;
      if (dx - hw < 1) dx = hw + 1;                 // keep the text inside the strip
      if (dx + hw > w - 1) dx = w - 1 - hw;
      if (dx + hw < 0 || dx - hw > w) continue;
      var clash = false;
      for (var k = 0; k < placed.length; k++) {
        if (Math.abs(dx - placed[k].x) < placed[k].hw + hw + 10) { clash = true; break; }
      }
      if (clash) continue;
      placed.push({ x: dx, hw: hw });
      draws.push({ x: dx, text: item.text });
    }
    for (var z = 0; z < draws.length; z++) ctx.fillText(draws[z].text, draws[z].x, h / 2);
  }

  function tick(chart, time, markType, rangeOverride) {
    if (!chart || !chart.timeScale) return '';
    var cfg = CFG.get(chart) || defaultConfig();
    var range = rangeOverride || cfg.getRange();
    var intraday = isIntradayRange(range);

    var idx = null, epoch = null, prevP = null;
    var _barSecs = null, _bt0 = null, _bt = null;   // identity of the bar sequence in use

    if (intraday) {
      var bt = cfg.getBarTimes();
      var bs = cfg.getBarSecs();
      if (bt && bs) {
        idx = Math.round(Number(time) / bs);
        // Future whitespace / out-of-range: do not fabricate a session label.
        if (!(idx >= 0 && idx < bt.length)) return '';
        epoch = bt[idx];
        if (idx > 0) prevP = parts(bt[idx - 1]);
        // Changing these means the bar sequence was replaced (timeframe switch,
        // range reload), so every memoised label is meaningless.
        _barSecs = bs;
        _bt0 = bt[0];
        _bt = bt;
      } else {
        epoch = cfg.resolveEpoch(time);       // raw-epoch intraday (mini, market)
      }
    } else {
      epoch = cfg.resolveEpoch(time);         // business-day
    }
    if (epoch == null) return '';
    var p = parts(epoch);
    if (!p) return '';

    var s = state(chart);
    // -- Reset the "already labelled" sets once per RENDER PASS --
    // Measured call sequence from lightweight-charts: ticks are requested in
    // ascending index order with no repeats (e.g. 672, 684, 696, 708, 720 in a
    // 2.5-session view). A pass is therefore one ordered sweep, and it always
    // begins at the LEFT EDGE of the view. Clearing anywhere else is what caused
    // the duplicate dates: while the user zooms, the visible range changes between
    // two ticks of the same pass, so the old range-key comparison wiped the sets
    // mid-pass and the day was dated a second time ("15 Oct" ... "15 Oct").
    // Order-independent and mid-pass safe:
    //   - this tick sits in the left-edge band of the view -> a pass is starting;
    //   - the previous call was a while ago (>= 250 ms) -> a new paint.
    // -- Reset the "already labelled" sets once per PAINT, never per tick --
    // The visible range must NEVER drive this reset. During a zoom or a pan (and
    // whenever the live forming candle nudges the right edge) the range changes
    // BETWEEN two ticks of the same paint. Resetting there wiped `seen` before
    // every call, so every tick looked like the first tick of its day and the
    // same date was printed over and over -- measured on the shipped code: one
    // paint, window drifting by a single bar, printed "16 Oct" three times and
    // "21 Oct" five times. That is the duplicate-date bug.
    //
    // A paint starts when:
    //   - it is the first call ever,
    //   - the sweep restarts to the LEFT (lightweight-charts asks for ticks in
    //     ascending index order, so a lower index means a new paint began),
    //   - the formatter has been idle for a frame or more, or
    //   - the bar sequence itself was replaced (timeframe switch / reload).
    var _now = (window.performance && window.performance.now) ? window.performance.now() : Date.now();
    var _prevIdx = (s.passIdx != null) ? s.passIdx : null;
    var _newPass = (s.labelKey == null)
      || (s.lastCall && (_now - s.lastCall) >= 250)
      || (_prevIdx != null && idx != null && idx < _prevIdx)
      || (s.barSecs != null && _barSecs != null && (s.barSecs !== _barSecs || s.bt0 !== _bt0));
    if (_newPass) {
      s.labelKey = 'painted';
      s.barSecs = _barSecs;
      s.bt0 = _bt0;
      s.labels = {};
      s.seen = { d: {}, m: 0, y: 0 };
    }
    if (idx != null) s.passIdx = idx;
    if (idx != null && s.labels && s.labels[idx] !== undefined) return s.labels[idx];
    // Does the visible range span more than one trading day? Date labels are only
    // meaningful then -- a single-session view (all visible bars on one trading
    // day) must show times only, with no date at all.
    var multiDay = false;
    var tradingDays = null;
    try {
      var _vr = chart.timeScale().getVisibleRange();
      if (_vr && _vr.from != null && _vr.to != null) {
        var _eF = null, _eT = null;
        if (intraday && idx != null) {
          var _bt = cfg.getBarTimes(), _bs = cfg.getBarSecs();
          if (_bt && _bs) {
            var _iF = Math.max(0, Math.min(_bt.length - 1, Math.round(Number(_vr.from) / _bs)));
            var _iT = Math.max(0, Math.min(_bt.length - 1, Math.round(Number(_vr.to) / _bs)));
            _eF = _bt[_iF]; _eT = _bt[_iT];
          }
        }
        if (_eF == null) { _eF = toEpoch(_vr.from); _eT = toEpoch(_vr.to); }
        // TRADING days in view: real bars in the window / bars per session. This
        // (not elapsed calendar days) decides between times and dates. An elapsed
        // measure is wrong because a two-session Friday+Monday view spans four
        // calendar days, so a zoomed-in view was classified as zoomed out and
        // lost its times.
        if (_bt && _bs && _iF != null && _iT != null && _iT >= _iF) {
          tradingDays = (_iT - _iF + 1) / Math.max(1, Math.floor(375 / (BAR_MIN[range] || 5)));
        } else if (_eF != null && _eT != null && isFinite(_eF) && isFinite(_eT)) {
          tradingDays = Math.abs(_eT - _eF) / 86400;
        }
        var _pF = parts(_eF), _pT = parts(_eT);
        if (_pF && _pT) multiDay = dayKeyOf(_pF) !== dayKeyOf(_pT);
      }
    } catch (e) {}
    var bars = barsOf(chart);
    var width = widthOf(chart);

    var last = s.last;
    // A backwards jump means LWC started a new render pass; re-anchor.
    if (last && idx != null && last.idx != null && idx < last.idx) { last = null; s.last = null; }

    var first = !last;
    var newDay = first, newMon = false, newYr = false;
    if (last) {
      newDay = dayKeyOf(p) !== last.dayKey;
      newMon = monKeyOf(p) !== last.monKey;
      newYr  = p.y !== last.y;
    }
    // Definitive boundary from the real bar sequence (previous real bar).
    var realDayBoundary = prevP ? (dayKeyOf(p) !== dayKeyOf(prevP)) : null;
    var realMonthBoundary = prevP ? (prevP.y === p.y && prevP.moi !== p.moi) : null;
    var realYearBoundary = prevP ? (prevP.y !== p.y) : null;
    if (realDayBoundary) newDay = true;

    if (!last || idx == null || last.idx == null || idx >= last.idx) {
      s.last = { idx: idx, dayKey: dayKeyOf(p), monKey: monKeyOf(p), y: p.y };
    }

    // "Already labelled" sets, reset only when the visible range changes (see
    // onRangeChange). They persist across re-renders so a given trading day can
    // never be dated twice, no matter what order Lightweight Charts calls the
    // formatter in or how a render is split across frames.
    s.lastCall = (window.performance && window.performance.now) ? window.performance.now() : Date.now();

    var wide = (tradingDays != null)
      ? (tradingDays > 5)
      : (bars > 3 * Math.max(1, Math.floor(375 / (BAR_MIN[range] || 5))));
    // Where this mark sits inside its trading session, and how many bars ~52px of
    // the strip covers (so the overlay's date has clear space).
    var dayOffset = null, dateGutterBars = null;
    if (intraday && idx != null && _bt) {
      dayOffset = idx - dayStartOf(_bt, idx);
      var _pxPerBar = (width > 0 && bars > 0) ? (width / bars) : 0;
      if (_pxPerBar > 0) dateGutterBars = Math.max(1, Math.ceil(52 / _pxPerBar));
    }

    var o = { p: p, mt: markType, bars: bars, width: width, range: range, s: s, idx: idx,
              first: first, realDayBoundary: realDayBoundary,
              realMonthBoundary: realMonthBoundary, realYearBoundary: realYearBoundary,
              newDay: newDay, newMon: newMon, newYr: newYr, multiDay: multiDay,
              tradingDays: tradingDays, wide: wide, dayOffset: dayOffset,
              dateGutterBars: dateGutterBars, overlay: intraday ? overlayReady(chart) : false };
    var _out = intraday ? fmtIntraday(o) : fmtDay(o);
    if (idx != null && s.labels) s.labels[idx] = _out;
    return _out;
  }

  window.LeverageAxis = {
    version: '1.0.0',
    TMT: TMT,
    register: register,
    reset: reset,
    tick: tick,
    parts: parts,
    toEpoch: toEpoch,
    isIntradayRange: isIntradayRange
  };
})();
