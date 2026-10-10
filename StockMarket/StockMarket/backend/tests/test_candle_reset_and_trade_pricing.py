"""Phase 1 remediation regression tests.

Scope A -- 5m candle reset:
  Expiring a pending candle must NOT reset a newer, actively-forming bucket.

Scope B -- paper-trading price integrity:
  The executed price must be the server's authoritative market price; a client
  price must never determine it, and a missing/stale market price must reject.
"""
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import main
import schemas
import trade_service
from aggregator import Live5mBuilder, Pending5mCandle
from execution_engine import PriceMonitorService

IST = main.IST
TICKER = "ZZRESET"


class LateBufferExpiryTests(unittest.TestCase):
    """Scope A -- the exact expiry behaviour (extracted for testability)."""

    def setUp(self):
        self.b = Live5mBuilder()

    def _pending(self, ticker, bucket_start, close=100.0, volume=2500):
        return Pending5mCandle(
            ticker=ticker, bucket_start=bucket_start, deadline=0.0,
            open=98.0, high=101.0, low=97.0, close=close, volume=volume,
        )

    def test_expiry_does_not_reset_a_newer_forming_bucket(self):
        """The defect: expiry ~60s into the NEXT bucket replaced that bucket's
        accumulated high/low/volume and reset its open."""
        old_bucket = datetime(2026, 10, 9, 10, 15)
        new_bucket = datetime(2026, 10, 9, 10, 20)
        self.b.active_candles[TICKER] = {"5m": {
            "timestamp": new_bucket, "open": 100.0, "high": 106.0, "low": 99.0,
            "close": 105.0, "volume": 4000,
        }}
        now_epoch = datetime(2026, 10, 9, 10, 21, 0, tzinfo=IST).timestamp()
        self.b._expire_pending_candle(self._pending(TICKER, old_bucket), now_epoch)

        cur = self.b.active_candles[TICKER]["5m"]
        self.assertEqual(cur["timestamp"], new_bucket, "bucket must not change")
        self.assertEqual((cur["open"], cur["high"], cur["low"], cur["close"]),
                         (100.0, 106.0, 99.0, 105.0), "OHLC must be preserved")
        self.assertEqual(cur["volume"], 4000, "volume must be preserved")

    def test_expiry_reseeds_only_when_still_on_the_expired_bucket(self):
        """The legacy path is preserved: if the ticker is still on the expired
        bucket (no newer candle), seed the current bucket."""
        old_bucket = datetime(2026, 10, 9, 10, 15)
        self.b.active_candles[TICKER] = {"5m": {
            "timestamp": old_bucket, "open": 98.0, "high": 101.0, "low": 97.0,
            "close": 100.0, "volume": 2500,
        }}
        now_epoch = datetime(2026, 10, 9, 10, 21, 0, tzinfo=IST).timestamp()
        self.b._expire_pending_candle(self._pending(TICKER, old_bucket), now_epoch)

        cur = self.b.active_candles[TICKER]["5m"]
        self.assertEqual(cur["timestamp"], datetime(2026, 10, 9, 10, 20))
        self.assertEqual(cur["volume"], 0)

    def test_expiry_with_no_active_candle_is_safe(self):
        now_epoch = datetime(2026, 10, 9, 10, 21, 0, tzinfo=IST).timestamp()
        self.b._expire_pending_candle(
            self._pending("ZZNONE", datetime(2026, 10, 9, 10, 15)), now_epoch)
        self.assertNotIn("ZZNONE", self.b.active_candles)

    def test_expiry_flushes_the_expired_candle_once(self):
        old_bucket = datetime(2026, 10, 9, 10, 15)
        new_bucket = datetime(2026, 10, 9, 10, 20)
        self.b.active_candles[TICKER] = {"5m": {
            "timestamp": new_bucket, "open": 100.0, "high": 106.0, "low": 99.0,
            "close": 105.0, "volume": 4000,
        }}
        now_epoch = datetime(2026, 10, 9, 10, 21, 0, tzinfo=IST).timestamp()
        self.b._expire_pending_candle(self._pending(TICKER, old_bucket, close=100.0), now_epoch)
        flushed = [c for c in self.b._flush_batch if c["ticker"] == TICKER]
        self.assertEqual(len(flushed), 1)
        self.assertEqual(flushed[0]["timestamp"], old_bucket)
        self.assertEqual(flushed[0]["close"], 100.0)


class AuthoritativePriceTests(unittest.TestCase):
    """Scope B -- the server-authoritative execution price resolver."""

    def test_returns_provider_price(self):
        svc = PriceMonitorService()
        with patch.object(svc, "get_price", return_value=123.45):
            self.assertEqual(svc.resolve_execution_price("RELIANCE"), 123.45)

    def test_none_when_no_price(self):
        svc = PriceMonitorService()
        with patch.object(svc, "get_price", return_value=None):
            self.assertIsNone(svc.resolve_execution_price("ILLIQUID"))

    def test_none_when_non_positive(self):
        svc = PriceMonitorService()
        with patch.object(svc, "get_price", return_value=0.0):
            self.assertIsNone(svc.resolve_execution_price("X"))
        with patch.object(svc, "get_price", return_value=-5.0):
            self.assertIsNone(svc.resolve_execution_price("X"))


class TradeRoutePricingTests(unittest.TestCase):
    """Scope B -- the live trade routes use the server price and reject on none."""

    def _order(self, entry_price=999.0):
        return schemas.PlaceOrderRequest(
            ticker="RELIANCE", position_type="LONG", quantity=1,
            entry_price=entry_price, take_profit=None, stop_loss=None,
            client_order_id="cid-1",
        )

    def _user(self):
        u = MagicMock()
        u.user_id = 1
        return u

    def test_place_order_rejects_when_no_market_price(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with patch("execution_engine.PriceMonitorService.resolve_execution_price", return_value=None):
            with self.assertRaises(main.HTTPException) as cm:
                main.place_order(self._order(), self._user(), db)
        self.assertEqual(cm.exception.status_code, 409)

    def test_place_order_uses_server_price_not_client(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        captured = {}

        def fake_open(**kwargs):
            captured["entry_price"] = kwargs["entry_price"]
            pos = MagicMock(); pos.id = 7
            return pos, "ok", None

        with patch("execution_engine.PriceMonitorService.resolve_execution_price", return_value=100.0):
            with patch.object(trade_service.TradingService, "open_position", side_effect=fake_open):
                resp = main.place_order(self._order(entry_price=999.0), self._user(), db)
        self.assertEqual(captured["entry_price"], 100.0, "client price must be ignored")
        self.assertFalse(resp["is_duplicate"])

    def test_close_position_rejects_when_no_market_price(self):
        db = MagicMock()
        pos = MagicMock(); pos.ticker = "ILLIQUID"
        db.query.return_value.filter.return_value.first.return_value = pos
        req = schemas.ClosePositionRequest(position_id=1, closing_price=999.0)
        with patch("execution_engine.PriceMonitorService.resolve_execution_price", return_value=None):
            with self.assertRaises(main.HTTPException) as cm:
                main.close_position(req, db, self._user())
        self.assertEqual(cm.exception.status_code, 409)

    def test_close_position_uses_server_price_not_client(self):
        db = MagicMock()
        pos = MagicMock(); pos.ticker = "RELIANCE"; pos.id = 1; pos.realized_pnl = 5.0
        db.query.return_value.filter.return_value.first.return_value = pos
        captured = {}

        def fake_close(**kwargs):
            captured["closing_price"] = kwargs["closing_price"]
            return pos, None

        req = schemas.ClosePositionRequest(position_id=1, closing_price=999.0)
        with patch("execution_engine.PriceMonitorService.resolve_execution_price", return_value=100.0):
            with patch.object(trade_service.TradingService, "close_position", side_effect=fake_close):
                main.close_position(req, db, self._user())
        self.assertEqual(captured["closing_price"], 100.0, "client price must be ignored")


class TradeRouterPricingSourceGuards(unittest.TestCase):
    """The legacy /api/trade/open and /api/trade/close routes had NO validation."""

    def test_router_routes_use_resolver(self):
        import routers.trade_router as tr
        src = open(tr.__file__, encoding="utf-8").read()
        self.assertEqual(src.count("resolve_execution_price"), 2,
                         "both /open and /close must resolve the server price")
        self.assertNotIn("entry_price=req.entry_price", src)
        self.assertNotIn("closing_price=req.closing_price", src)


if __name__ == "__main__":
    unittest.main()
