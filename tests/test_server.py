import json
import threading
import unittest
import urllib.error
import urllib.request

from dindin.live import wait_for_bars
from dindin.server import make_server


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server("127.0.0.1", 0)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.RequestHandlerClass.live.stop()
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as err:
            with err:
                return err.code, err.read()

    def json(self, path, body=None):
        status, raw = self.request(path, body)
        return status, json.loads(raw)

    def test_health_and_catalog(self):
        self.assertEqual(self.json("/api/health")[1]["status"], "ok")
        status, data = self.json("/api/strategies")
        self.assertEqual(status, 200)
        self.assertEqual({s["key"] for s in data["strategies"]}, {"sma_cross", "rsi", "macd", "bollinger"})

    def test_backtest(self):
        status, data = self.json("/api/backtest", {"strategy": "rsi", "bars": 300, "seed": 4})
        self.assertEqual(status, 200)
        self.assertEqual(len(data["candles"]), 300)
        self.assertIn("sharpe", data["metrics"])

    def test_backtest_with_csv(self):
        rows = "\n".join(f"{1700000000 + i * 86400},{100 + i},{101 + i},{99 + i},{100.5 + i}" for i in range(80))
        status, data = self.json("/api/backtest", {"csv": "time,open,high,low,close\n" + rows})
        self.assertEqual(status, 200)
        self.assertEqual(len(data["candles"]), 80)

    def test_compare(self):
        status, data = self.json("/api/compare", {"bars": 300})
        self.assertEqual(status, 200)
        returns = [r["metrics"]["total_return"] for r in data["rows"]]
        self.assertEqual(returns, sorted(returns, reverse=True))

    def test_bad_input_is_400_not_500(self):
        for body in ({"strategy": "nope"}, {"bars": 5}, {"risk": {"stop_loss": 9}}, {"params": {"fast": "x"}}):
            with self.subTest(body=body):
                self.assertEqual(self.json("/api/backtest", body)[0], 400)
        status, _ = self.request("/api/backtest", {})
        self.assertEqual(status, 200)  # empty body means defaults

    def test_static_and_traversal(self):
        status, raw = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"<html", raw)
        self.assertEqual(self.request("/../dindin/server.py")[0], 404)
        self.assertEqual(self.request("/%2e%2e/README.md")[0], 404)
        self.assertEqual(self.request("/api/unknown")[0], 404)

    def test_live_session(self):
        status, data = self.json("/api/live/start", {"strategy": "sma_cross", "interval": 0.05, "seed": 2})
        self.assertEqual(status, 200)
        self.assertTrue(data["running"])
        self.assertTrue(wait_for_bars(self.server.RequestHandlerClass.live, data["bar"] + 3))
        status, data = self.json("/api/live/stop", {})
        self.assertFalse(data["running"])
        self.assertEqual(self.json("/api/live/start", {"interval": 99})[0], 400)


if __name__ == "__main__":
    unittest.main()
