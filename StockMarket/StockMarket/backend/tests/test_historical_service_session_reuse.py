"""RT-01 regression: historical_service.login() must reuse
angelone_service's already-authenticated session when both are configured
with the same broker credentials, instead of calling generateSession() a
second time under the same account -- Angel One revokes the prior token on
a new login, so an independent historical-service login can silently kill
the live price feed for every user.

HISTORICAL_API_KEY/CLIENT_ID/PASSWORD/TOTP_TOKEN are currently set in .env
to an exact copy of the live ANGELONE_* credentials (confirmed by reading
both), so the reuse path is the one that actually runs in production today.
"""
import unittest
from unittest.mock import MagicMock, patch

from historical_service import HistoricalDataService
from angelone_service import angelone_service as live_service


class _FreshHistoricalService(HistoricalDataService):
    """HistoricalDataService is a singleton -- bypass it so tests don't
    leak state into each other or the real process-wide instance."""
    def __new__(cls):
        obj = object.__new__(cls)
        obj._initialized = False
        return obj


def _make_hist_service(api_key="shared_key"):
    svc = _FreshHistoricalService()
    svc.api_key = api_key
    svc.client_id = "hist_client"
    svc.password = "hist_pw"
    svc.totp_token = "hist_totp"
    svc.smart_api = None
    svc.is_logged_in = False
    svc._initialized = True
    return svc


class TestHistoricalServiceSessionReuse(unittest.TestCase):
    def setUp(self):
        self._orig_is_logged_in = live_service.is_logged_in
        self._orig_smart_api = live_service.smart_api
        self._orig_api_key = live_service.api_key

    def tearDown(self):
        live_service.is_logged_in = self._orig_is_logged_in
        live_service.smart_api = self._orig_smart_api
        live_service.api_key = self._orig_api_key

    def test_reuses_live_session_when_credentials_match(self):
        live_service.is_logged_in = True
        live_service.smart_api = MagicMock(name="live_smart_api")
        live_service.api_key = "shared_key"

        hist = _make_hist_service(api_key="shared_key")

        with patch("historical_service.SmartConnect") as mock_connect:
            result = hist.login()

        self.assertTrue(result)
        self.assertTrue(hist.is_logged_in)
        self.assertIs(hist.smart_api, live_service.smart_api)
        mock_connect.assert_not_called()  # no second generateSession()

    def test_falls_through_to_independent_login_when_live_not_logged_in(self):
        # Standalone scripts (e.g. the migration CLI) where angelone_service
        # was never logged in must still be able to log in on their own.
        live_service.is_logged_in = False
        live_service.smart_api = None
        live_service.api_key = "shared_key"

        hist = _make_hist_service(api_key="shared_key")

        mock_smart_api = MagicMock()
        mock_smart_api.generateSession.return_value = {"status": True}
        with patch("historical_service.SmartConnect", return_value=mock_smart_api) as mock_connect, \
             patch("historical_service.pyotp.TOTP") as mock_totp:
            mock_totp.return_value.now.return_value = "123456"
            result = hist.login()

        self.assertTrue(result)
        self.assertIs(hist.smart_api, mock_smart_api)
        mock_connect.assert_called_once_with(api_key="shared_key")
        mock_smart_api.generateSession.assert_called_once()

    def test_independent_login_when_credentials_genuinely_differ(self):
        # If real separate historical credentials are ever provisioned, the
        # reuse path must not kick in just because the live service happens
        # to be logged in -- they're different accounts/sessions.
        live_service.is_logged_in = True
        live_service.smart_api = MagicMock(name="live_smart_api")
        live_service.api_key = "live_only_key"

        hist = _make_hist_service(api_key="genuinely_separate_key")

        mock_smart_api = MagicMock()
        mock_smart_api.generateSession.return_value = {"status": True}
        with patch("historical_service.SmartConnect", return_value=mock_smart_api) as mock_connect, \
             patch("historical_service.pyotp.TOTP") as mock_totp:
            mock_totp.return_value.now.return_value = "123456"
            result = hist.login()

        self.assertTrue(result)
        self.assertIsNot(hist.smart_api, live_service.smart_api)
        mock_connect.assert_called_once()


if __name__ == "__main__":
    unittest.main()
