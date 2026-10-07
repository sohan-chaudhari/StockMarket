"""P2.2 — Logo request cascade regression.

/logos/ is proxied to the app (not nginx-cached). The generated-fallback branch
already sent `Cache-Control: public, max-age=86400`, but the real-file branch sent
no cache header, so browsers re-requested every visible logo on every page load.
These tests pin the corrected contract for both branches.
"""
import asyncio
import glob
import os
import unittest

import main


def _call(name):
    return asyncio.run(main.get_stock_logo(name))


class LogoCacheTests(unittest.TestCase):
    def test_existing_logo_is_cacheable(self):
        logos = glob.glob(os.path.join(main.FRONTEND_DIR, "logos", "*.svg"))
        if not logos:
            self.skipTest("no logo files present")
        r = _call(os.path.basename(logos[0]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(dict(r.headers).get("cache-control"), "public, max-age=86400")

    def test_missing_logo_fallback_is_cacheable_and_not_404(self):
        r = _call("ZZZ_P22_MISSING_LOGO.svg")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(dict(r.headers).get("cache-control"), "public, max-age=86400")


if __name__ == "__main__":
    unittest.main()
