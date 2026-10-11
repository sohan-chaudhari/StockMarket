"""Regression test: the live chart must never be blanked by the loader again.

Symptom (reported): open a ticker's live chart (e.g. INFY) -> candles appear ->
the full-screen loading page suddenly replaces them.

Cause: dashboard.js's big-chart WS handler reconciles the chart when two
consecutive server ticks straddle a candle boundary by calling
`window.loadData(activeRange)`. loadData() unconditionally re-showed the
full-screen `#leverage-loader` overlay, so a BACKGROUND reload veiled an
already-rendered chart -- and for an intraday range that re-fetch can be slow
(server-side gap detection/backfill), leaving the loading page up.

Fix: only veil the chart while it is still empty (`!window._chartHasRendered`),
and record that flag once candles have actually been rendered.
"""
import os
import re
import unittest

FRONTEND_DASHBOARD = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "frontend", "dashboard.js")

FLEX_SHOW = "loader2.style.display = 'flex'"


class ChartLoaderNeverBlanksRenderedChart(unittest.TestCase):
    def setUp(self):
        with open(FRONTEND_DASHBOARD, "r", encoding="utf-8") as f:
            self.src = f.read()

    def _load_data_body(self):
        i = self.src.find("window.loadData = async function")
        self.assertNotEqual(i, -1, "window.loadData not found")
        j = self.src.find("function hideLoader(el)", i)
        self.assertNotEqual(j, -1, "end of loadData not found")
        return self.src[i:j]

    def test_overlay_is_shown_at_most_once_and_only_when_guarded(self):
        body = self._load_data_body()
        self.assertEqual(
            body.count(FLEX_SHOW), 1,
            "the full-screen overlay must be shown from exactly one guarded site")
        guard = body.find("_veilNeeded && !hasRenderedPreview")
        show = body.find(FLEX_SHOW)
        self.assertNotEqual(guard, -1, "veil guard missing")
        self.assertLess(guard, show, "the show must be guarded")

    def test_veil_is_derived_from_whether_the_chart_has_rendered(self):
        body = self._load_data_body()
        self.assertIn("var _veilNeeded = !window._chartHasRendered;", body)

    def test_rendering_candles_sets_the_flag(self):
        body = self._load_data_body()
        self.assertGreaterEqual(
            body.count("window._chartHasRendered = true"), 3,
            "memory preview, IndexedDB preview and the API render must all set it")
        self.assertIn("if (data.length > 0) window._chartHasRendered = true;", body)

    def test_boundary_reconciliation_still_reloads_the_chart(self):
        # The data-correctness behaviour must be preserved; only the veil changed.
        i = self.src.find("_bucketOf(_serverMs) !== _bucketOf(window._lastTickServerTs)")
        self.assertNotEqual(i, -1, "candle-boundary reconciliation missing")
        self.assertIn("window.loadData(activeRange)", self.src[i:i + 400])


if __name__ == "__main__":
    unittest.main()
