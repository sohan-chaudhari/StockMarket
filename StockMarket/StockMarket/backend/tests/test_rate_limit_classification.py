"""CRIT-04: the rate-limit classifier must recognise the service's OWN cooldown message.

The service raises, while it is inside a throttle pause:

    Historical API rate-limited (shared cooldown, 59s left)

Callers use that classification to decide whether retrying can help:

  * intraday_prefill._fetch_range:  `if _is_rate_limited(exc): break`  -- do not
    retry a throttle; it is account-wide, so retrying 5s/15s/30s later just re-hits
    the same cooldown
  * backfill_jobs / bulk_recovery: classify the failure as 'throttled' (longer backoff)

The keyword list held 'rate limit' (with a space) but the message says
'rate-limited' (hyphenated), so the classifier returned False for the service's own
message, the break never fired, and the prefill retried INTO the active cooldown --
re-hitting it each time and keeping the account permanently throttled, which is what
made a user's own repair ("Access denied because of exceeding access rate") fail.

These tests pin the classification AND the behaviour it controls.
"""
import inspect
import unittest

import historical_service as hs


class ClassifierTests(unittest.TestCase):
    def test_own_shared_cooldown_message_is_classified_as_rate_limited(self):
        msg = "Historical API rate-limited (shared cooldown, 59s left)"
        self.assertTrue(hs._looks_rate_limited(msg),
                        "the service's own cooldown message must be recognised")

    def test_real_provider_wording_still_matches(self):
        for m in ("Access denied because of exceeding access rate",
                  "Too many requests", "Request throttled", "rate limit exceeded"):
            self.assertTrue(hs._looks_rate_limited(m), m)

    def test_a_permanent_symbol_error_is_not_a_rate_limit(self):
        self.assertFalse(hs._looks_rate_limited("Ticker 'XYZ' not found"))
        self.assertFalse(hs._looks_rate_limited("Invalid symbol"))
        self.assertFalse(hs._looks_rate_limited(""))


class PrefillGateTests(unittest.TestCase):
    """Tie the SERVICE's own message template to the classifier, so the two cannot
    drift: if the wording changes, this fails instead of silently re-enabling the
    retry storm."""

    def test_the_service_message_template_renders_classifiable_text(self):
        src = inspect.getsource(hs)
        self.assertIn("shared cooldown", src, "the service still raises a cooldown message")
        rendered = "Historical API rate-limited (shared cooldown, 59s left)"
        self.assertTrue(hs._looks_rate_limited(rendered),
                        "the rendered service message must classify as a throttle")

    def test_the_prefill_break_is_gated_on_the_classifier_in_source(self):
        import intraday_prefill as ip
        src = inspect.getsource(ip._fetch_range)
        self.assertIn("if _is_rate_limited(exc):", src,
                      "the prefill must consult the classifier before retrying")
        self.assertIn("break", src, "a throttle must abandon the retry budget")


if __name__ == "__main__":
    unittest.main()
