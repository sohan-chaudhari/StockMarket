"""Regression test: the OHLC legend must not be clobbered while hovering a bar.

Reported: hovering the 09:15 candle showed the correct OHLC and then "suddenly
changed", which looked like the candle itself was wrong.

Cause: the OHLC legend elements (#ohlc-open/high/low/close/volume) have several
writers, and the live one -- `processBigChartPrice()`, called on EVERY websocket
tick -- wrote the day's running open/high/low/close/volume into the same
elements. So a hovered bar showed its own OHLC and the next tick replaced it.
(The stored candle was always correct; the legend was lying.)

Fix: the crosshair handler marks `window._crosshairBarActive = true` while the
crosshair sits on a bar (and clears it when it leaves / has no bar data), and
both the live tick updater and the chart re-render skip the legend while it is
set.
"""
import os
import re
import unittest

FRONTEND = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "frontend", "dashboard.js")


class OhlcLegendHoverGuardTests(unittest.TestCase):
    def setUp(self):
        with open(FRONTEND, "r", encoding="utf-8") as f:
            self.src = f.read()

    def _fn(self, name):
        i = self.src.find("function %s(" % name)
        self.assertNotEqual(i, -1, "%s not found" % name)
        return self.src[i:i + 6000]

    def test_crosshair_sets_and_clears_the_flag(self):
        self.assertIn("window._crosshairBarActive = true;", self.src,
                      "the hovered-bar legend write must mark the crosshair active")
        self.assertGreaterEqual(
            self.src.count("window._crosshairBarActive = false;"), 2,
            "the flag must be cleared when the crosshair leaves the chart and "
            "when it is over no bar data")

    def test_live_tick_updater_skips_the_legend_while_hovering(self):
        body = self._fn("processBigChartPrice")
        self.assertIn("var _legendLive = !window._crosshairBarActive;", body)
        # every legend write in the live updater must be guarded
        for el in ("ohlc-open", "ohlc-high", "ohlc-low", "ohlc-close", "ohlc-volume"):
            for m in re.finditer(r"getElementById\('%s'\)" % el, body):
                line_start = body.rfind("\n", 0, m.start()) + 1
                line = body[line_start:body.find("\n", m.start())]
                if ".innerText =" not in line:
                    continue  # declaration only
                self.assertIn("_legendLive", line,
                              "unguarded live write: %s" % line.strip())

    def test_render_legend_is_guarded(self):
        # The (re)render path writes the newest bar into the legend; it must also
        # respect an active crosshair.
        i = self.src.find("// Chart (re)rendered: show the newest bar in the legend")
        self.assertNotEqual(i, -1, "render legend guard missing")
        self.assertIn("if (!window._crosshairBarActive) {", self.src[i:i + 200])

    def test_legend_writes_are_not_duplicated_unguarded(self):
        # Sanity: no writer may bypass the guard by writing the legend inline.
        self.assertNotRegex(
            self.src,
            r"getElementById\('ohlc-high'\)\s*\)\s*\{\s*\w+\.innerText\s*=\s*live\.high",
            "an unguarded live high write reappeared")


if __name__ == "__main__":
    unittest.main()
