"""P2.3 — Live forming-candle volume regression.

Root cause: the WS/poll `volume` field is the CUMULATIVE day volume. The intraday
live branch of processBigChartPrice() never wrote a volume onto the forming candle
nor updated the volume series, so the current bucket's volume bar stayed at its
load-time value (0 at bucket start). The fix derives the per-tick day-volume delta
(via the shipped _bucketVolumeDelta helper, mirroring the backend aggregator) and
accumulates it onto the forming bucket.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(os.path.dirname(BACKEND_DIR), "frontend")
DASH = os.path.join(FRONTEND_DIR, "dashboard.js")


def _src():
    with open(DASH, "r", encoding="utf-8") as f:
        return f.read()


def _extract_function(src, name):
    idx = src.index("function " + name + "(")
    i = src.index("{", idx)
    depth, j = 0, i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[idx:j + 1]
        j += 1
    raise AssertionError("unbalanced braces extracting " + name)


class FormingCandleVolumeStaticTests(unittest.TestCase):
    def setUp(self):
        self.src = _src()

    def test_delta_helper_defined_once(self):
        self.assertEqual(self.src.count("function _bucketVolumeDelta("), 1)

    def test_delta_helper_used_in_live_path(self):
        body = _extract_function(self.src, "processBigChartPrice")
        self.assertIn("_bucketVolumeDelta(", body)
        self.assertIn("bigVolumeSeries.update(", body)
        self.assertIn("fc.volume += _volDelta", body)

    def test_forming_candle_is_seeded_with_volume(self):
        self.assertIn("close: live.current, volume: 0 }", self.src)


@unittest.skipUnless(shutil.which("node"), "node not available")
class BucketVolumeDeltaBehaviourTests(unittest.TestCase):
    def _run(self, ticks):
        fn = _extract_function(_src(), "_bucketVolumeDelta")
        script = (
            fn + "\n"
            "var base = undefined, bucketVol = 0, out = [];\n"
            "var ticks = " + json.dumps(ticks) + ";\n"
            "for (var i = 0; i < ticks.length; i++) {\n"
            "  var r = _bucketVolumeDelta(base, ticks[i]);\n"
            "  base = r.base; bucketVol += r.delta;\n"
            "  out.push({ day: ticks[i], delta: r.delta, base: r.base, bucket: bucketVol });\n"
            "}\n"
            "console.log(JSON.stringify(out));\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
            fh.write(script)
            path = fh.name
        try:
            proc = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(path)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_cumulative_day_volume_yields_running_bucket_delta(self):
        out = self._run([10000, 10050, 10100, 10100, 20100])
        self.assertEqual([r["delta"] for r in out], [0, 50, 50, 0, 10000])
        self.assertEqual([r["bucket"] for r in out], [0, 50, 100, 100, 10100])
        # first tick only establishes the baseline (no bogus whole-day jump)
        self.assertEqual(out[0]["base"], 10000)

    def test_reset_reconnect_rebaselines_without_negative_delta(self):
        out = self._run([10000, 10200, 5000, 5100])
        self.assertEqual([r["delta"] for r in out], [0, 200, 0, 100])
        self.assertEqual(out[2]["base"], 5000)

    def test_missing_or_zero_volume_is_ignored(self):
        out = self._run([None, 0, "", 8000, 8100])
        self.assertEqual([r["delta"] for r in out], [0, 0, 0, 0, 100])
        # a zero/missing tick must not clobber an established baseline
        self.assertEqual(out[3]["base"], 8000)


if __name__ == "__main__":
    unittest.main()
