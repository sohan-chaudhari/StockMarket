import unittest
from datetime import datetime
from resampler import CandleResampler, TieredResampler


def make_5m_candle(timestamp, open_, high, low, close, volume):
    return {"timestamp": timestamp, "open": open_, "high": high, "low": low, "close": close, "volume": volume}


class TestCandleResampler(unittest.TestCase):
    def setUp(self):
        self.three_candles = [
            make_5m_candle(datetime(2026, 7, 1, 9, 15), 100, 105, 95, 102, 1000),
            make_5m_candle(datetime(2026, 7, 1, 9, 20), 102, 108, 101, 107, 800),
            make_5m_candle(datetime(2026, 7, 1, 9, 25), 107, 110, 106, 109, 1200),
        ]

    def test_resample_to_15m(self):
        result = CandleResampler.resample_5m_to(self.three_candles, "15m")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["open"], 100)
        self.assertEqual(result[0]["high"], 110)
        self.assertEqual(result[0]["low"], 95)
        self.assertEqual(result[0]["close"], 109)
        self.assertEqual(result[0]["volume"], 3000)

    def test_resample_to_30m(self):
        candles = self.three_candles + [
            make_5m_candle(datetime(2026, 7, 1, 9, 30), 109, 112, 108, 110, 600),
            make_5m_candle(datetime(2026, 7, 1, 9, 35), 110, 115, 109, 113, 900),
            make_5m_candle(datetime(2026, 7, 1, 9, 40), 113, 114, 111, 112, 700),
        ]
        result = CandleResampler.resample_5m_to(candles, "30m")
        # Pandas 30min buckets: [9:00, 9:30) and [9:30, 10:00)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["open"], 100)
        self.assertEqual(result[1]["close"], 112)
        self.assertEqual(result[0]["volume"] + result[1]["volume"], 5200)

    def test_resample_to_1h(self):
        many = []
        for hour in range(9, 12):
            for minute in [15, 20, 25, 30, 35, 40, 45, 50, 55, 0, 5, 10]:
                if hour == 9 and minute < 15:
                    continue
                if hour == 15 and minute > 30:
                    break
                many.append(make_5m_candle(
                    datetime(2026, 7, 1, hour, minute),
                    100 + (hour - 9) * 5 + minute / 10,
                    105 + (hour - 9) * 5 + minute / 10,
                    95 + (hour - 9) * 5,
                    102 + (hour - 9) * 5 + minute / 10,
                    1000,
                ))
        result = CandleResampler.resample_5m_to(many[:12], "1h")
        self.assertGreater(len(result), 0)
        self.assertEqual(result[0]["open"], many[0]["open"])

    def test_resample_to_4h(self):
        many = []
        for hour in [9, 10, 11, 12, 13, 14, 15]:
            if hour == 15 and 15 > 30:
                break
            for minute in [15, 20, 25, 30, 35, 40, 45, 50, 55]:
                if hour == 15 and minute > 30:
                    break
                if hour == 9 and minute < 15:
                    continue
                many.append(make_5m_candle(
                    datetime(2026, 7, 1, hour, minute),
                    100, 105, 95, 102, 1000,
                ))
            if hour == 15:
                break
        result = CandleResampler.resample_5m_to(many, "4h")
        self.assertGreater(len(result), 0)

    def test_resample_to_1d(self):
        many = []
        for hour in range(9, 16):
            for minute in [15, 25, 35, 45, 55]:
                if hour == 15 and minute > 30:
                    break
                if hour == 9 and minute < 15:
                    break
                many.append(make_5m_candle(
                    datetime(2026, 7, 1, hour, minute),
                    100, 105, 95, 102, 1000,
                ))
        result = CandleResampler.resample_5m_to(many, "1D")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["open"], 100)

    def test_resample_to_1w(self):
        candles = []
        for day in range(1, 4):
            d = datetime(2026, 7, day, 10, 0)
            candles.append(make_5m_candle(d, 100, 105, 95, 102, 1000))
        result = CandleResampler.resample_5m_to(candles, "1W")
        self.assertGreater(len(result), 0)

    def test_resample_to_1m(self):
        candles = [make_5m_candle(datetime(2026, 7, d, 10, 0), 100, 105, 95, 102, 1000) for d in range(1, 15)]
        result = CandleResampler.resample_5m_to(candles, "1M")
        self.assertGreater(len(result), 0)

    def test_resample_empty_candles(self):
        result = CandleResampler.resample_5m_to([], "15m")
        self.assertEqual(result, [])

    def test_validate_ohlc_valid(self):
        candle = {"open": 100, "high": 105, "low": 95, "close": 102}
        self.assertTrue(CandleResampler.validate_ohlc(candle))

    def test_validate_ohlc_invalid(self):
        candle = {"open": 100, "high": 90, "low": 110, "close": 102}
        self.assertFalse(CandleResampler.validate_ohlc(candle))

    def test_validate_ohlc_missing_key(self):
        candle = {"open": 100, "high": 105}
        self.assertFalse(CandleResampler.validate_ohlc(candle))

    def test_resample_unsupported_tf(self):
        with self.assertRaises(ValueError):
            CandleResampler.resample_5m_to(self.three_candles, "3m")

    def test_resample_preserves_ohlc_order(self):
        result = CandleResampler.resample_5m_to(self.three_candles, "15m")
        self.assertGreaterEqual(result[0]["high"], result[0]["open"])
        self.assertGreaterEqual(result[0]["high"], result[0]["close"])
        self.assertLessEqual(result[0]["low"], result[0]["open"])
        self.assertLessEqual(result[0]["low"], result[0]["close"])


class TestTieredResampler(unittest.TestCase):
    def test_split_by_tiers(self):
        candles = [
            make_5m_candle(datetime(2026, 1, 15, 10, 0), 100, 105, 95, 102, 1000),
            make_5m_candle(datetime(2024, 6, 15, 10, 0), 200, 205, 195, 202, 2000),
            make_5m_candle(datetime(2021, 3, 15, 10, 0), 300, 305, 295, 302, 3000),
            make_5m_candle(datetime(2016, 8, 15, 10, 0), 400, 405, 395, 402, 4000),
        ]
        tiers = TieredResampler.split_by_tiers(candles)
        self.assertEqual(len(tiers["5m"]), 1)
        self.assertEqual(len(tiers["15m"]), 1)
        self.assertEqual(len(tiers["1h"]), 1)
        self.assertEqual(len(tiers["1d"]), 1)


if __name__ == "__main__":
    unittest.main()
