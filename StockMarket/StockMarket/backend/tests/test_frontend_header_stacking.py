"""Regression test for BUG-3: the chart page search dropdown must not be hidden
behind the chart page's own header controls.

The injected nav header (.header, nav.css) and the stock page's
.sticky-header-container are SIBLING stacking contexts. Whichever has the higher
z-index paints on top, and the search dropdown lives INSIDE .header — so if the
chart header outranks the nav header, the dropdown is occluded by the
Back/Trade/Positions/News buttons. The chart header must therefore stay below
the nav header's z-index.

Static-parse style, mirroring tests/test_nginx_security_headers.py (no browser
required).
"""
import os
import re
import unittest


_HERE = os.path.dirname(os.path.abspath(__file__))
_FRONTEND = os.path.normpath(os.path.join(_HERE, "..", "..", "frontend"))


def _read(name):
    with open(os.path.join(_FRONTEND, name), encoding="utf-8") as fh:
        content = fh.read()
    # Strip CSS/HTML comments so a commented-out declaration can never be
    # mistaken for a live one (this bit the first version of this test).
    return re.sub(r"/\*.*?\*/", "", content, flags=re.S)


def _zindex_for(css, selector):
    """Return the first z-index declared in a rule block for `selector`."""
    for m in re.finditer(re.escape(selector) + r"\s*\{([^}]*)\}", css, re.S):
        zm = re.search(r"z-index\s*:\s*(-?\d+)", m.group(1))
        if zm:
            return int(zm.group(1))
    return None


class TestChartSearchDropdownStacking(unittest.TestCase):
    def setUp(self):
        self.nav_css = _read("nav.css")
        self.stock_html = _read("stock.html")

    def test_nav_header_defines_a_stacking_context(self):
        nav_z = _zindex_for(self.nav_css, ".header")
        self.assertIsNotNone(nav_z, "nav.css .header must declare a z-index")
        self.nav_z = nav_z

    def test_chart_header_sits_below_nav_header(self):
        nav_z = _zindex_for(self.nav_css, ".header")
        chart_z = _zindex_for(self.stock_html, ".sticky-header-container")
        self.assertIsNotNone(chart_z, "stock.html .sticky-header-container must declare a z-index")
        self.assertLess(
            chart_z, nav_z,
            f"chart header z-index ({chart_z}) must be below nav header ({nav_z}) "
            "or the search dropdown inside .header is painted behind it",
        )

    def test_chart_header_controls_do_not_escape(self):
        # .header-controls is inside .sticky-header-container's stacking context;
        # keep it below the nav header too so it can never paint over the
        # dropdown via DOM ordering.
        nav_z = _zindex_for(self.nav_css, ".header")
        controls_z = _zindex_for(self.stock_html, ".header-controls")
        self.assertIsNotNone(controls_z)
        self.assertLess(controls_z, nav_z)


if __name__ == "__main__":
    unittest.main()
