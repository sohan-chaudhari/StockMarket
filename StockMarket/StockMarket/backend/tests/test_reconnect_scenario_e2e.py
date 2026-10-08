"""REAL reconnect / candle-gap verification (Issue 2), driven in a real browser.

Loads the actual shipped `stock.html` + `dashboard.js` (nothing is
reimplemented) into headless Chromium, mocks only the backend HTTP responses,
and reproduces the reported production scenario:

    chart open on 5m  ->  current candle has been running ~2 minutes  ->
    network drops  ->  offline ~8 minutes  ->  network returns

Then it verifies, WITHOUT navigating away:
  A. the completed candles that appeared during the outage are restored,
  B. the running candle is corrected from authoritative data (not stuck stale),
  C. the chart dataset has no duplicate (ticker, timeframe, timestamp) bars,
  D. no fake candles are invented for the outage window,
  E. live WebSocket ticks resume and keep updating the same running candle,
  F. the chart becomes correct while still on the same page,
  G. different outage lengths (< 1 candle, ~1 candle, several candles) and
      intervals (1m, 5m, 15m, 30m, 1h) all reconcile.

The backend contract mocked here is exactly the one the app already relies on:
`GET /api/stock-data/intraday/paginated?ticker=..&interval=..&limit=..` returns
completed candles for the whole session plus the authoritative (aggregator-
merged) forming candle for the current bucket.
"""
import calendar
import datetime
import functools
import http.server
import json
import os
import socketserver
import threading
import unittest
import urllib.parse

FRONTEND_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "frontend")
)

try:
    from playwright.sync_api import sync_playwright
    _HAS_PW = True
except Exception:
    _HAS_PW = False

IST = datetime.timedelta(hours=5, minutes=30)
INTERVAL_MIN = {"1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60}
SESSION_START_MIN = 9 * 60 + 15
SESSION_END_MIN = 15 * 60 + 30

# A fixed NSE trading day (Wed) so the test is independent of the wall clock.
DAY = (2026, 10, 7)


def _ist_epoch_sec(hour, minute, second=0):
    """True UTC epoch seconds for an IST wall-clock time on DAY."""
    y, mo, d = DAY
    utc = datetime.datetime(y, mo, d, hour, minute, second) - IST
    return calendar.timegm(utc.timetuple())


BASE = _ist_epoch_sec(9, 15)            # session open (09:15 IST) in true-UTC sec

# The REAL production WebSocket delivers `serverTs` as an ISO-8601 datetime
# string with a +05:30 offset (e.g. "2026-10-07T22:27:28.940889+05:30"), NOT as
# epoch milliseconds. Tests must dispatch exactly that format so a regression
# that assumes numeric milliseconds (as once shipped) cannot pass again.
_IST_TZ = datetime.timezone(IST)


def _iso_ist(epoch_ms):
    return datetime.datetime.fromtimestamp(epoch_ms / 1000.0, tz=_IST_TZ).isoformat()


def _bucket(sec, bar_secs):
    return BASE + ((sec - BASE) // bar_secs) * bar_secs


def build_page(interval, now_sec):
    """Mock /intraday/paginated payload: session bars up to the current bucket
    (completed bars + the authoritative merged forming candle)."""
    bar_secs = INTERVAL_MIN[interval] * 60
    cur = _bucket(now_sec, bar_secs)
    session_end = BASE + (SESSION_END_MIN - SESSION_START_MIN) * 60
    cur = min(cur, session_end)  # never invent a bucket past the close
    rows = []
    k = 0
    t = BASE
    while t < cur:
        base = 100 + k
        rows.append({"time": t, "open": base, "high": base + 3.0, "low": base - 1.0,
                     "close": base + 1.0, "volume": 1000 + k})
        k += 1
        t += bar_secs
    # authoritative forming candle for the current bucket (what the aggregator
    # merges server-side). Deliberately strongly "spiked" so a client that kept
    # its own pre-disconnect candle would be visibly wrong.
    fo = 500.0 + k
    rows.append({"time": cur, "open": fo, "high": fo + 9.0, "low": fo - 2.0,
                 "close": fo + 4.0, "volume": 5000 + k})
    return rows


def build_older_page(interval, before_sec, count=600):
    """Older bars strictly before `before_sec` -- the lazy-load 'before' page."""
    bar_secs = INTERVAL_MIN[interval] * 60
    rows = []
    t = before_sec - count * bar_secs
    k = 0
    while t < before_sec:
        base = 50 + k
        rows.append({"time": t, "open": base, "high": base + 2.0, "low": base - 1.0,
                     "close": base + 0.5, "volume": 100 + k})
        k += 1
        t += bar_secs
    return rows


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@unittest.skipUnless(_HAS_PW, "playwright not available")
class ReconnectScenarioE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = functools.partial(_Handler, directory=FRONTEND_DIR)
        cls.httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls._pw = sync_playwright().start()
        try:
            cls.browser = cls._pw.chromium.launch()
        except Exception as e:
            cls._pw.stop()
            cls.httpd.shutdown()
            cls.httpd.server_close()
            raise unittest.SkipTest("chromium not available: %s" % e)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        cls.httpd.shutdown()
        cls.httpd.server_close()

    # ------------------------------------------------------------------ setup

    def _open_page(self, interval, now_sec, ticker="RECOTEST", elder=False):
        state = {"now_sec": now_sec, "ticker": ticker, "interval": interval, "elder": elder}
        ctx = self.browser.new_context(timezone_id="Asia/Kolkata")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        def route(r):
            u = urllib.parse.urlparse(r.request.url)
            q = urllib.parse.parse_qs(u.query)
            path = u.path
            if path == "/api/stock-data/intraday/paginated":
                itv = (q.get("interval") or [state["interval"]])[0]
                before = q.get("before")
                if before and state.get("elder"):
                    # lazy-load 'before' page: strictly older bars (used by the
                    # prepend-alignment regression test)
                    rows = build_older_page(itv, int(float(before[0])))
                    r.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"data": rows, "has_more": True}))
                else:
                    # default: same recent page with has_more False, so no other
                    # scenario accidentally prepends history
                    rows = build_page(itv, state["now_sec"])
                    r.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"data": rows, "has_more": False}))
            elif path == "/api/csrf-token":
                r.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"csrf_token": "t"}))
            elif path == "/api/time":
                r.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"ts": state["now_sec"] * 1000}))
            elif path == "/api/live-prices":
                r.fulfill(status=200, content_type="application/json",
                          body=json.dumps({state["ticker"]: {"current": 101.0, "prev_close": 100.0}}))
            elif path == "/api/all-stocks":
                r.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"stocks": [], "version": "v"}))
            elif path == "/api/auth/me":
                r.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"user_id": 1, "email": "t@e.com", "virtual_balance": 100000.0}))
            else:
                r.fulfill(status=200, content_type="application/json", body="{}")

        page.route("**/api/**", route)

        offset = now_sec * 1000 - int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)

        page.add_init_script(
            "try{"
            "sessionStorage.setItem('token','test');"
            "sessionStorage.setItem('user',JSON.stringify({user_id:1,email:'t@e.com',virtual_balance:100000}));"
            "localStorage.setItem('llp',JSON.stringify({%s:{current:101.0,open:100.0,high:102.0,low:99.0,prev_close:100.0,volume:1000}}));"
            "localStorage.setItem('llp_ts',String(Date.now()));"
            "window._serverClockOffset=%d;"
            "class FakeWS{constructor(){(this).readyState=0;var s=this;setTimeout(function(){s.readyState=1;if(s.onopen)s.onopen();},0);}"
            "send(){}close(){this.readyState=3;if(this.onclose)this.onclose({code:1000});}addEventListener(){}removeEventListener(){}}"
            "window.WebSocket=FakeWS;"
            "}catch(e){}" % ("'" + state["ticker"] + "'", offset)
        )
        page.goto(f"http://127.0.0.1:{self.port}/stock.html?ticker={ticker}&range={interval}")
        return ctx, page, state, errors

    def _wait_loaded(self, page, interval, errors, timeout=25000):
        try:
            page.wait_for_function(
                "() => window._loadedRange === %r && window._chartCandles && window._chartCandles.length > 2"
                % interval,
                timeout=timeout,
            )
        except Exception:
            raise AssertionError(
                "chart never loaded for %s. page errors=%r" % (interval, errors[:8]))

    def _dispatch_tick(self, page, ticker, price, bucket_ms):
        # Send the SAME format production sends: an ISO-8601 string with offset.
        page.evaluate(
            """([tkr, price, ts]) => {
                 window.dispatchEvent(new CustomEvent('dashboard_price_update', {
                   detail: { prices: { [tkr]: { current: price, open: price, high: price, low: price,
                                              prev_close: price, volume: 7000 } }, serverTs: ts }
                 }));
               }""",
            [ticker, price, _iso_ist(bucket_ms)],
        )

    def _set_now(self, page, now_sec):
        page.evaluate("([s]) => { window._serverClockOffset = s*1000 - Date.now(); }", [now_sec])

    # --------------------------------------------------------------- scenario

    def _reconcile(self, interval, initial_sec, reconnect_sec, ticker="RECOTEST"):
        ctx, page, state, errors = self._open_page(interval, initial_sec)
        try:
            self._wait_loaded(page, interval, errors)
            bar_secs = INTERVAL_MIN[interval] * 60
            page.evaluate("() => { window.__navSentinel = 'SAME_PAGE'; }")
            init_bucket = _bucket(initial_sec, bar_secs)

            # production always has ticks before a disconnect
            self._dispatch_tick(page, ticker, 555.0, init_bucket * 1000)
            page.wait_for_timeout(150)
            pre_close = page.evaluate(
                "() => (window._formingCandles && window._formingCandles['i']) ? window._formingCandles['i'].close : null")

            # ---- outage: authoritative clock advances; the page stays idle ----
            state["now_sec"] = reconnect_sec
            self._set_now(page, reconnect_sec)

            reconnect_bucket = _bucket(reconnect_sec, bar_secs)
            expected = build_page(interval, reconnect_sec)
            missed = [b for b in expected if init_bucket < b["time"] < reconnect_bucket]
            wait_bucket = missed[-1]["time"] if missed else reconnect_bucket

            # first tick after the network returns -> triggers the reconcile
            self._dispatch_tick(page, ticker, 777.0, reconnect_bucket * 1000)
            page.wait_for_function(
                "() => (window._intradayBarTimes||[]).indexOf(%d) !== -1" % wait_bucket,
                timeout=15000,
            )
            page.wait_for_timeout(400)

            snap = page.evaluate(
                """() => ({
                    barTimes: (window._intradayBarTimes||[]).slice(),
                    cacheTimes: (window._chartCandles||[]).map(c => c.time),
                    forming: (window._formingCandles && window._formingCandles['i']) ? {
                        time: window._formingCandles['i'].time,
                        realTime: window._formingCandles['i'].realTime,
                        open: window._formingCandles['i'].open,
                        high: window._formingCandles['i'].high,
                        low: window._formingCandles['i'].low,
                        close: window._formingCandles['i'].close
                    } : null,
                    range: window._loadedRange,
                    sentinel: window.__navSentinel,
                    href: location.href
                })"""
            )
            snap["_pre_close"] = pre_close
            snap["_expected"] = expected
            snap["_missed"] = missed
            snap["_reconnect_bucket"] = reconnect_bucket
            return ctx, page, state, errors, snap
        except Exception:
            ctx.close()
            raise

    def _assert_reconciled(self, snap, interval):
        bar_secs = INTERVAL_MIN[interval] * 60
        expected = snap["_expected"]
        expected_times = [b["time"] for b in expected]
        bar_times = snap["barTimes"]
        missed = snap["_missed"]
        reconnect_bucket = snap["_reconnect_bucket"]

        # A. missing completed candles restored
        for b in missed:
            self.assertIn(b["time"], bar_times,
                          "%s: missed completed candle %s not restored" % (interval, b["time"]))
        self.assertEqual(bar_times, expected_times,
                         "%s: chart timeline must match the authoritative session" % interval)

        # B. running candle corrected (when the outage crossed a boundary)
        self.assertIsNotNone(snap["forming"], "%s: no forming candle after reconnect" % interval)
        self.assertEqual(snap["forming"]["realTime"], reconnect_bucket,
                         "%s: running candle not on the current bucket" % interval)
        if missed:
            auth = next(b for b in expected if b["time"] == reconnect_bucket)
            self.assertAlmostEqual(snap["forming"]["open"], auth["open"], places=6,
                                   msg="%s: running candle open not corrected" % interval)
            self.assertGreaterEqual(snap["forming"]["high"], auth["high"] - 1e-6,
                                    "%s: running candle high not authoritative" % interval)
        self.assertNotEqual(snap["_pre_close"], snap["forming"]["close"],
                            "%s: running candle stuck at the pre-disconnect value" % interval)

        # C. no duplicate bars
        self.assertEqual(len(bar_times), len(set(bar_times)),
                         "%s: duplicate timestamps in _intradayBarTimes" % interval)
        self.assertEqual(len(snap["cacheTimes"]), len(set(snap["cacheTimes"])),
                         "%s: duplicate timestamps in _chartDataCache" % interval)

        # D. no fake candles
        for t in bar_times:
            self.assertGreaterEqual(t, BASE, "%s: bar before session open" % interval)
            self.assertEqual((t - BASE) % bar_secs, 0, "%s: misaligned/fake bar %s" % (interval, t))
        self.assertLessEqual(max(bar_times), reconnect_bucket,
                             "%s: bar invented past the current bucket" % interval)

        # F. no navigation
        self.assertEqual(snap["sentinel"], "SAME_PAGE",
                         "%s: recovery must not navigate away" % interval)
        self.assertIn("stock.html", snap["href"])

    def _scenario(self, interval, initial_sec, reconnect_sec):
        ctx, page, state, errors, snap = self._reconcile(interval, initial_sec, reconnect_sec)
        try:
            self._assert_reconciled(snap, interval)
        finally:
            ctx.close()

    def _scenario_with_resume(self, interval, initial_sec, reconnect_sec):
        ctx, page, state, errors, snap = self._reconcile(interval, initial_sec, reconnect_sec)
        try:
            self._assert_reconciled(snap, interval)
            bar_secs = INTERVAL_MIN[interval] * 60
            reconnect_bucket = snap["_reconnect_bucket"]
            bars_after_reconcile = page.evaluate("() => (window._chartCandles||[]).length")

            # E. live resume: a further tick in the SAME bucket updates close only
            self._dispatch_tick(page, "RECOTEST", 799.0, reconnect_bucket * 1000 + 30000)
            page.wait_for_timeout(300)
            after = page.evaluate(
                """() => ({ close: window._formingCandles['i'].close,
                            realTime: window._formingCandles['i'].realTime,
                            bars: (window._chartCandles||[]).length,
                            sentinel: window.__navSentinel,
                            href: location.href })"""
            )
            self.assertEqual(after["realTime"], reconnect_bucket, "live tick moved the running candle")
            self.assertEqual(after["close"], 799.0, "live tick did not update the running candle close")
            self.assertEqual(after["bars"], bars_after_reconcile,
                             "an in-bucket live tick must not add a bar")
            self.assertEqual(after["sentinel"], "SAME_PAGE",
                             "recovery must happen without navigating away")
        finally:
            ctx.close()

    # ------------------------------- gate regression (ISO serverTs) ---------

    def test_iso_serverts_no_reload_within_bucket_one_on_crossing(self):
        """Regression guard for the production load-storm bug: `serverTs` is an
        ISO string. Ticks inside the SAME candle bucket must NOT call
        loadData(); crossing a bucket boundary must call it exactly once."""
        interval = "5m"
        bar_secs = 300
        initial_sec = _ist_epoch_sec(11, 10)          # bucket 11:10
        ctx, page, state, errors = self._open_page(interval, initial_sec)
        try:
            self._wait_loaded(page, interval, errors)
            page.evaluate(
                "() => { window.__loadCalls = 0; const o = window.loadData;"
                " window.loadData = function(){ window.__loadCalls++; return o.apply(this, arguments); };"
                " window.__navSentinel = 'SAME_PAGE'; }")
            bucket = _bucket(initial_sec, bar_secs)

            # 6 ticks, all inside the SAME bucket, in production ISO format
            for i in range(6):
                self._dispatch_tick(page, "RECOTEST", 100.0 + i, bucket * 1000 + i * 1000)
            page.wait_for_timeout(600)
            self.assertEqual(
                page.evaluate("() => window.__loadCalls"), 0,
                "ticks inside the same candle bucket must NOT reload the chart")

            # now cross into the next bucket -> exactly one reconcile
            next_sec = initial_sec + bar_secs + 30
            state["now_sec"] = next_sec
            self._set_now(page, next_sec)
            self._dispatch_tick(page, "RECOTEST", 200.0, (bucket + bar_secs) * 1000)
            page.wait_for_timeout(1200)
            self.assertEqual(
                page.evaluate("() => window.__loadCalls"), 1,
                "crossing a candle boundary must trigger exactly one reload")
            self.assertEqual(page.evaluate("() => window.__navSentinel"), "SAME_PAGE")

            # _lastTickServerTs must hold the NORMALIZED numeric ms value
            last = page.evaluate(
                "() => ({ t: typeof window._lastTickServerTs, v: window._lastTickServerTs })")
            self.assertEqual(last["t"], "number", "_lastTickServerTs must be a normalized number")
            self.assertEqual(int(last["v"]), (bucket + bar_secs) * 1000)
        finally:
            ctx.close()

    def test_numeric_serverts_is_still_supported(self):
        """The gate must keep working if a numeric epoch-ms serverTs is supplied
        (the normalization accepts both forms)."""
        interval = "5m"
        bar_secs = 300
        initial_sec = _ist_epoch_sec(11, 10)
        ctx, page, state, errors = self._open_page(interval, initial_sec)
        try:
            self._wait_loaded(page, interval, errors)
            bucket = _bucket(initial_sec, bar_secs)
            page.evaluate(
                """([tkr, ts]) => { window.dispatchEvent(new CustomEvent('dashboard_price_update',
                     { detail: { prices: { [tkr]: { current: 111, open: 111, high: 111, low: 111, prev_close: 111, volume: 1 } }, serverTs: ts } })); }""",
                ["RECOTEST", bucket * 1000])
            page.wait_for_timeout(400)
            last = page.evaluate(
                "() => ({ t: typeof window._lastTickServerTs, v: window._lastTickServerTs })")
            self.assertEqual(last["t"], "number")
            self.assertEqual(int(last["v"]), bucket * 1000)
        finally:
            ctx.close()

    # ------------------------------- lazy-load prepend alignment ------------

    def test_lazy_load_prepend_keeps_forming_candle_aligned(self):
        """Regression: after older history is lazily prepended, the forming
        candle's sequential index must be shifted by addedCount*barSecs so live
        ticks keep updating the CURRENT candle (not an old one)."""
        interval = "5m"
        initial_sec = _ist_epoch_sec(12, 7)
        # elder=True makes the mock serve genuinely older history for the lazy-load
        # 'before' requests, so the automatic prepend path is exercised end-to-end.
        ctx, page, state, errors = self._open_page(interval, initial_sec, elder=True)
        try:
            self._wait_loaded(page, interval, errors)
            # The chart fits inside its viewport, so lazy-load prepends history.
            page.wait_for_function(
                "() => (window._chartCandles || []).length > 100", timeout=20000)
            page.wait_for_timeout(900)
            snap = page.evaluate(
                "() => ({ bars: (window._chartCandles||[]).length,"
                " last: (window._chartCandles||[]).slice(-1)[0].time,"
                " bt: (window._intradayBarTimes||[]).length,"
                " f: (window._formingCandles && window._formingCandles['i']) ? window._formingCandles['i'].time : null })")
            self.assertGreater(snap["bars"], 100,
                               "older history must have been prepended")
            self.assertIsNotNone(snap["f"], "forming candle must still exist")
            self.assertEqual(snap["f"], snap["last"],
                             "forming candle index must equal the last cache bar index "
                             "after a prepend (addedCount*barSecs shift)")
        finally:
            ctx.close()

    # ------------------------------- G: outage lengths x intervals ----------

    def test_5m_outage_less_than_one_candle(self):
        # 12:07 -> 12:09: same 5m bucket, no completed candle missed
        self._scenario("5m", _ist_epoch_sec(12, 7), _ist_epoch_sec(12, 9))

    def test_5m_outage_about_one_candle(self):
        # 12:07 -> 12:11: the 12:05 bucket completes during the outage
        self._scenario("5m", _ist_epoch_sec(12, 7), _ist_epoch_sec(12, 11))

    def test_5m_outage_multiple_candles_with_resume(self):
        # the reported scenario: 12:07 -> 12:15:30 (8 min, 2 buckets complete)
        self._scenario_with_resume("5m", _ist_epoch_sec(12, 7), _ist_epoch_sec(12, 15, 30))

    def test_1m_multiple_candles(self):
        self._scenario("1m", _ist_epoch_sec(12, 7), _ist_epoch_sec(12, 15, 30))

    def test_15m_multiple_candles(self):
        self._scenario("15m", _ist_epoch_sec(12, 7), _ist_epoch_sec(12, 47))

    def test_30m_multiple_candles(self):
        self._scenario("30m", _ist_epoch_sec(12, 7), _ist_epoch_sec(13, 7))

    def test_1h_multiple_candles(self):
        self._scenario("1h", _ist_epoch_sec(12, 7), _ist_epoch_sec(14, 7))


if __name__ == "__main__":
    unittest.main()
