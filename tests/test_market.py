"""Market data tests. The exchange is faked, so these run offline."""

import io
import json
import unittest
import urllib.error
from unittest import mock

from dindin import market
from dindin.live import LiveSession, wait_for_bars

MINUTE_MS = 60_000


def kline(open_ms, close=100.0, span_ms=MINUTE_MS):
    return [open_ms, str(close), str(close + 1), str(close - 1), str(close), "5.0", open_ms + span_ms - 1, "0", 1, "0", "0", "0"]


class FakeExchange:
    """Answers /api/v3/klines like Binance does, from a fixed series."""

    def __init__(self, count=400, start_ms=1_700_000_000_000):
        self.rows = [kline(start_ms + i * MINUTE_MS, 100 + (i % 17) - (i % 5) * 0.7) for i in range(count)]
        self.calls = []

    def __call__(self, path, params, timeout=8.0):
        self.calls.append(params)
        rows = self.rows
        if "endTime" in params:
            rows = [r for r in rows if r[0] <= params["endTime"]]
        return rows[-params["limit"]:]


class MarketTests(unittest.TestCase):
    def test_validate(self):
        self.assertEqual(market.validate("btcusdt", "1m"), ("BTCUSDT", "1m"))
        with self.assertRaises(ValueError):
            market.validate("FAKECOIN", "1m")
        with self.assertRaises(ValueError):
            market.validate("BTCUSDT", "3s")

    def test_parse_kline(self):
        candle, close_ms = market.parse_kline(kline(1_700_000_000_000, 42.5))
        self.assertEqual(candle.time, 1_700_000_000)
        self.assertEqual(candle.close, 42.5)
        self.assertEqual(close_ms, 1_700_000_059_999)
        with self.assertRaises(market.MarketError):
            market.parse_kline(["garbage"])

    def test_recent_splits_closed_and_forming(self):
        fake = FakeExchange(count=3)
        now = fake.rows[-1][0] + 10_000  # inside the last candle
        with mock.patch.object(market, "_get", fake):
            closed, forming = market.fetch_recent("BTCUSDT", "1m", now_ms=now)
        self.assertEqual(len(closed), 2)
        self.assertEqual(forming.time, fake.rows[-1][0] // 1000)

    def test_history_pages_past_the_request_limit(self):
        fake = FakeExchange(count=2600)
        with mock.patch.object(market, "_get", fake), mock.patch.object(market, "MAX_PER_REQUEST", 1000):
            candles = market.fetch_history("ETHUSDT", "1m", 2500)
        self.assertEqual(len(candles), 2500)
        self.assertGreater(len(fake.calls), 2)
        times = [c.time for c in candles]
        self.assertEqual(times, sorted(set(times)))

    def test_falls_back_to_second_host(self):
        payload = json.dumps([kline(1_700_000_000_000)]).encode()
        seen = []

        def fake_urlopen(request, timeout):
            seen.append(request.full_url)
            if len(seen) == 1:
                raise urllib.error.URLError("blocked")
            return io.BytesIO(payload)

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            rows = market._get("/api/v3/klines", {"symbol": "BTCUSDT"})
        self.assertEqual(len(rows), 1)
        self.assertTrue(seen[0].startswith(market.HOSTS[0]))
        self.assertTrue(seen[1].startswith(market.HOSTS[1]))

    def test_all_hosts_down_raises_market_error(self):
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
            with self.assertRaises(market.MarketError):
                market._get("/api/v3/klines", {})


class LiveRealMarketTests(unittest.TestCase):
    def test_live_session_on_real_market_feed(self):
        fake = FakeExchange(count=400)
        session = LiveSession()
        with mock.patch.object(market, "_get", fake), mock.patch("dindin.live.REAL_POLL_SECONDS", 0.05):
            session.start(strategy="sma_cross", source="binance", symbol="BTCUSDT", timeframe="1m")
            snap = session.snapshot()
            self.assertEqual(snap["config"]["source"], "binance")
            self.assertEqual(len(snap["candles"]), 300)
            self.assertEqual(snap["live_from"], 300)
            # a new candle closes on the exchange
            fake.rows.append(kline(fake.rows[-1][0] + MINUTE_MS, 123.0))
            fake.rows.append(kline(fake.rows[-1][0] + MINUTE_MS, 124.0))  # forming
            with mock.patch("time.time", return_value=(fake.rows[-1][0] + 1000) / 1000):
                self.assertTrue(wait_for_bars(session, snap["bar"] + 1, timeout=3))
            session.stop()
        self.assertIsNone(session.snapshot()["error"])


if __name__ == "__main__":
    unittest.main()
