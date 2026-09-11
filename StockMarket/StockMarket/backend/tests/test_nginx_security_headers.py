"""Tests for the CSP + SECURITY HEADERS PRODUCTION HARDENING phase.

Security headers for LEVERAGE are managed at the Nginx layer (nginx.conf,
repo root), not in the FastAPI app -- main.py sets no security headers at
all (verified: only a CacheControlMiddleware exists there). These tests
statically parse nginx.conf rather than running a real Nginx process,
mirroring this suite's established pattern for config-only correctness
(test_sentry_config.py, test_database_engine_config.py both AST-parse
Python source instead of importing/running the real thing) -- there is no
AST for Nginx config, so this uses regex extraction against the literal
`add_header` lines instead. A real `nginx -t` syntax check was run
separately in a disposable `nginx:alpine` container as part of this phase's
manual validation (not repeated here, since these tests care about
*content*, not Nginx's own parser correctness).

The exact CSP origin allowlist asserted below is not arbitrary -- it is the
direct output of a full repo-wide grep audit of frontend/*.{html,js} for
<script>/<link>/fetch/WebSocket/eval/inline-handler usage (see nginx.conf's
own comment above the Content-Security-Policy-Report-Only line for the
full per-origin justification). If a future change legitimately needs a
new origin, update both nginx.conf's policy AND this test in the same
change -- a mismatch here means one of the two is wrong, not that the test
should be loosened without re-auditing.
"""
import os
import re
import unittest

NGINX_CONF_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "nginx.conf",
)


def _read_nginx_conf():
    with open(NGINX_CONF_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _extract_header_value(conf_text, header_name):
    """Returns the quoted value of the first `add_header <header_name> "...";`
    line, or None if that header is never set."""
    pattern = re.compile(
        r'add_header\s+' + re.escape(header_name) + r'\s+"((?:[^"\\]|\\.)*)"\s*(?:always\s*)?;'
    )
    match = pattern.search(conf_text)
    return match.group(1) if match else None


def _csp_directives(csp_value):
    """Splits a CSP header value into {directive_name: [tokens...]}."""
    directives = {}
    for part in csp_value.split(";"):
        part = part.strip()
        if not part:
            continue
        tokens = part.split()
        directives[tokens[0]] = tokens[1:]
    return directives


class NginxConfLoads(unittest.TestCase):
    def test_nginx_conf_exists_and_is_nonempty(self):
        self.assertTrue(os.path.isfile(NGINX_CONF_PATH), f"expected nginx.conf at {NGINX_CONF_PATH}")
        self.assertGreater(len(_read_nginx_conf()), 0)


class PreExistingHeadersUnchanged(unittest.TestCase):
    """This phase must not alter the three headers already in production."""

    def setUp(self):
        self.conf = _read_nginx_conf()

    def test_x_content_type_options_unchanged(self):
        self.assertEqual(_extract_header_value(self.conf, "X-Content-Type-Options"), "nosniff")

    def test_x_frame_options_unchanged(self):
        self.assertEqual(_extract_header_value(self.conf, "X-Frame-Options"), "SAMEORIGIN")

    def test_referrer_policy_unchanged(self):
        self.assertEqual(_extract_header_value(self.conf, "Referrer-Policy"), "strict-origin-when-cross-origin")

    def test_no_hsts_header_added(self):
        """TLS terminates externally (AWS ALB/ACM) -- HSTS must NOT be set
        at this Nginx layer, which cannot guarantee HTTPS is in effect."""
        self.assertIsNone(_extract_header_value(self.conf, "Strict-Transport-Security"))
        self.assertNotIn("Strict-Transport-Security", self.conf)


class CspIsReportOnlyNotEnforced(unittest.TestCase):
    def setUp(self):
        self.conf = _read_nginx_conf()

    def test_enforcing_csp_header_is_not_set(self):
        """Only the Report-Only variant may be present -- enforcing CSP
        would break the app given its pervasive inline scripts/styles
        (proven by the frontend audit, not asserted here)."""
        # A plain `add_header Content-Security-Policy ` (not
        # -Report-Only) must not exist anywhere.
        self.assertIsNone(re.search(r'add_header\s+Content-Security-Policy\s+"', self.conf))

    def test_report_only_csp_header_is_set(self):
        self.assertIsNotNone(_extract_header_value(self.conf, "Content-Security-Policy-Report-Only"))

    def test_no_report_uri_or_report_to_configured(self):
        """No CSP-report collection endpoint exists in the backend and this
        phase deliberately does not add one or an external reporting
        service -- violations surface only in each browser's own console."""
        csp = _extract_header_value(self.conf, "Content-Security-Policy-Report-Only")
        self.assertNotIn("report-uri", csp)
        self.assertNotIn("report-to", csp)


class CspDirectiveContent(unittest.TestCase):
    """Directive-by-directive proof that the policy matches exactly what
    the frontend audit found -- nothing broader, nothing missing."""

    @classmethod
    def setUpClass(cls):
        conf = _read_nginx_conf()
        csp = _extract_header_value(conf, "Content-Security-Policy-Report-Only")
        assert csp is not None, "Content-Security-Policy-Report-Only header not found in nginx.conf"
        cls.csp_raw = csp
        cls.directives = _csp_directives(csp)

    def test_default_src_self_only(self):
        self.assertEqual(self.directives.get("default-src"), ["'self'"])

    def test_script_src_self_and_cdnjs_only(self):
        # cdnjs.cloudflare.com: jspdf (static <script> in portfolio.html) and
        # html2pdf.js (createElement('script') in overview.html/stock-ui.js).
        self.assertEqual(sorted(self.directives.get("script-src", [])),
                          sorted(["'self'", "https://cdnjs.cloudflare.com"]))

    def test_style_src_self_googlefonts_and_cdnjs_only(self):
        # fonts.googleapis.com: Google Fonts <link rel="stylesheet">.
        # cdnjs.cloudflare.com: Font Awesome CSS (portfolio.html).
        self.assertEqual(sorted(self.directives.get("style-src", [])),
                          sorted(["'self'", "https://fonts.googleapis.com", "https://cdnjs.cloudflare.com"]))

    def test_font_src_self_gstatic_and_cdnjs_only(self):
        # fonts.gstatic.com: the actual Google Fonts font files.
        # cdnjs.cloudflare.com: Font Awesome's webfont files.
        self.assertEqual(sorted(self.directives.get("font-src", [])),
                          sorted(["'self'", "https://fonts.gstatic.com", "https://cdnjs.cloudflare.com"]))

    def test_img_src_is_self_only(self):
        # Audit found zero data:/external image usage anywhere in HTML/CSS.
        self.assertEqual(self.directives.get("img-src"), ["'self'"])

    def test_connect_src_is_self_only(self):
        # Every fetch() call found is a relative /api/... path (API_BASE ==
        # ''); the WebSocket URL is built from window.location.host, always
        # same-origin. 'self' covers same-origin ws:/wss: per the Fetch spec.
        self.assertEqual(self.directives.get("connect-src"), ["'self'"])

    def test_worker_frame_child_src_are_none(self):
        # Zero <iframe>, Worker, ServiceWorker, or WebAssembly usage found.
        self.assertEqual(self.directives.get("worker-src"), ["'none'"])
        self.assertEqual(self.directives.get("frame-src"), ["'none'"])
        self.assertEqual(self.directives.get("child-src"), ["'none'"])

    def test_object_src_and_base_uri_locked_down(self):
        self.assertEqual(self.directives.get("object-src"), ["'none'"])
        self.assertEqual(self.directives.get("base-uri"), ["'self'"])

    def test_form_action_and_frame_ancestors_self(self):
        self.assertEqual(self.directives.get("form-action"), ["'self'"])
        # Mirrors the existing X-Frame-Options: SAMEORIGIN exactly.
        self.assertEqual(self.directives.get("frame-ancestors"), ["'self'"])

    def test_no_unsafe_inline_or_unsafe_eval_anywhere(self):
        self.assertNotIn("'unsafe-inline'", self.csp_raw)
        self.assertNotIn("'unsafe-eval'", self.csp_raw)

    def test_no_wildcard_or_data_or_blob_origins(self):
        self.assertNotIn("*", self.csp_raw)
        self.assertNotIn("data:", self.csp_raw)
        self.assertNotIn("blob:", self.csp_raw)

    def test_only_the_three_audited_third_party_origins_appear(self):
        found_origins = set(re.findall(r"https://[a-zA-Z0-9.-]+", self.csp_raw))
        self.assertEqual(
            found_origins,
            {"https://fonts.googleapis.com", "https://fonts.gstatic.com", "https://cdnjs.cloudflare.com"},
        )


class PermissionsPolicyHeader(unittest.TestCase):
    def setUp(self):
        self.conf = _read_nginx_conf()

    def test_permissions_policy_disables_unused_apis(self):
        value = _extract_header_value(self.conf, "Permissions-Policy")
        self.assertIsNotNone(value)
        for api in ("geolocation", "camera", "microphone", "payment"):
            self.assertIn(f"{api}=()", value)


class WebsocketAndProxyBehaviorUntouched(unittest.TestCase):
    """This phase must not touch the WebSocket proxy architecture: no
    per-location add_header that would shadow the server-level security
    headers on the /ws/* path, and the existing WS-specific proxy settings
    (buffering off, long read/send timeouts) must be exactly as before."""

    def setUp(self):
        self.conf = _read_nginx_conf()

    def test_ws_location_block_still_present_and_unbuffered(self):
        ws_block_match = re.search(
            r"location\s*~\s*\^\/ws\/\(dashboard\|user\)\$\s*\{(.*?)\n    \}",
            self.conf, re.DOTALL,
        )
        self.assertIsNotNone(ws_block_match, "WebSocket location block not found")
        ws_block = ws_block_match.group(1)
        self.assertIn("proxy_buffering off;", ws_block)
        self.assertIn("proxy_read_timeout 3600s;", ws_block)
        self.assertIn("proxy_send_timeout 3600s;", ws_block)
        self.assertIn('proxy_set_header Upgrade $http_upgrade;', ws_block)

    def test_no_location_level_add_header_shadows_server_level_headers(self):
        """Nginx quirk: if a location block defines its OWN add_header, it
        silently discards ALL server-level add_header directives for that
        location (they are not merged). Neither location block may add its
        own add_header, or the new headers (and the pre-existing three)
        would silently stop applying there."""
        for location_pattern in (
            r"location\s*~\s*\^\/ws\/\(dashboard\|user\)\$\s*\{(.*?)\n    \}",
            r"location\s*/\s*\{(.*?)\n    \}",
        ):
            match = re.search(location_pattern, self.conf, re.DOTALL)
            self.assertIsNotNone(match)
            self.assertNotIn("add_header", match.group(1))


if __name__ == "__main__":
    unittest.main()
