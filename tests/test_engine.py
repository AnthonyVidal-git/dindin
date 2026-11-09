import unittest

from dindin.data import Candle, SyntheticMarket, parse_csv
from dindin.engine import Engine, RiskConfig, run_backtest
from dindin.metrics import max_drawdown, summarize
from dindin.strategies import BUY, HOLD, STRATEGIES, Strategy, create_strategy


class ScriptedStrategy(Strategy):
    """Emits a fixed list of actions, one per bar after warmup."""

    key = "scripted"

    def __init__(self, actions):
        super().__init__()
        self.actions = list(actions)

    @property
    def warmup(self):
        return 1

    def signal(self, closes):
        return self.actions.pop(0) if self.actions else HOLD


def bar(t, o, h, l, c):
    return Candle(t, o, h, l, c, 1.0)


NO_COSTS = dict(fee_rate=0, slippage=0, stop_loss=0, take_profit=0, position_size=1.0)


class EngineTests(unittest.TestCase):
    def test_round_trip_without_costs(self):
        engine = Engine(ScriptedStrategy([BUY, HOLD, -1]), 1000, RiskConfig(**NO_COSTS))
        for i, price in enumerate([100, 110, 120]):
            engine.on_candle(bar(i + 1, price, price, price, price))
        self.assertIsNone(engine.position)
        self.assertAlmostEqual(engine.cash, 1200)
        self.assertAlmostEqual(engine.trades[0].pnl, 200)
        self.assertEqual(engine.trades[0].bars_held, 2)

    def test_fees_and_slippage_reduce_pnl(self):
        risk = RiskConfig(fee_rate=0.001, slippage=0.001, stop_loss=0, take_profit=0, position_size=1.0)
        engine = Engine(ScriptedStrategy([BUY, -1]), 1000, risk)
        engine.on_candle(bar(1, 100, 100, 100, 100))
        engine.on_candle(bar(2, 100, 100, 100, 100))
        self.assertLess(engine.cash, 1000)
        self.assertAlmostEqual(engine.trades[0].pnl, engine.cash - 1000)

    def test_stop_loss_fills_at_stop_or_gap_open(self):
        risk = RiskConfig(**{**NO_COSTS, "stop_loss": 0.05})
        engine = Engine(ScriptedStrategy([BUY]), 1000, risk)
        engine.on_candle(bar(1, 100, 100, 100, 100))
        engine.on_candle(bar(2, 99, 99, 90, 92))  # trades through 95
        self.assertEqual(engine.trades[0].exit_reason, "stop_loss")
        self.assertAlmostEqual(engine.trades[0].exit_price, 95)

        engine = Engine(ScriptedStrategy([BUY]), 1000, risk)
        engine.on_candle(bar(1, 100, 100, 100, 100))
        engine.on_candle(bar(2, 80, 82, 79, 81))  # gaps below the stop
        self.assertAlmostEqual(engine.trades[0].exit_price, 80)

    def test_take_profit(self):
        risk = RiskConfig(**{**NO_COSTS, "take_profit": 0.1})
        engine = Engine(ScriptedStrategy([BUY]), 1000, risk)
        engine.on_candle(bar(1, 100, 100, 100, 100))
        engine.on_candle(bar(2, 101, 115, 100, 112))
        self.assertEqual(engine.trades[0].exit_reason, "take_profit")
        self.assertAlmostEqual(engine.trades[0].exit_price, 110)

    def test_rejects_out_of_order_candles(self):
        engine = Engine(ScriptedStrategy([]), 1000)
        engine.on_candle(bar(5, 1, 1, 1, 1))
        with self.assertRaises(ValueError):
            engine.on_candle(bar(5, 1, 1, 1, 1))

    def test_every_strategy_backtests_and_balances(self):
        candles = SyntheticMarket(seed=1).candles(600)
        for key in STRATEGIES:
            with self.subTest(strategy=key):
                result = run_backtest(candles, create_strategy(key), 10_000)
                self.assertEqual(len(result.equity), len(candles))
                pnl = sum(t["pnl"] for t in result.trades)
                self.assertAlmostEqual(result.metrics["end_equity"], 10_000 + pnl, places=6)
                self.assertGreaterEqual(result.metrics["exposure"], 0)
                self.assertLessEqual(result.metrics["exposure"], 1)

    def test_no_lookahead_prefix_invariance(self):
        """Results on the first N bars must not change when more bars follow."""
        candles = SyntheticMarket(seed=9).candles(400)
        short = run_backtest(candles[:300], create_strategy("sma_cross"), 10_000)
        full = run_backtest(candles, create_strategy("sma_cross"), 10_000)
        short_fills = [f for f in short.fills if f["reason"] != "end_of_data"]
        self.assertEqual(short_fills, full.fills[: len(short_fills)])

    def test_validation(self):
        with self.assertRaises(ValueError):
            RiskConfig(position_size=2)
        with self.assertRaises(ValueError):
            RiskConfig.from_dict({"leverage": 10})
        with self.assertRaises(ValueError):
            create_strategy("sma_cross", {"fast": 50, "slow": 20})
        with self.assertRaises(ValueError):
            create_strategy("nope")
        with self.assertRaises(ValueError):
            run_backtest(SyntheticMarket().candles(10), create_strategy("sma_cross"))


class DataAndMetricsTests(unittest.TestCase):
    def test_synthetic_market_is_deterministic(self):
        self.assertEqual(SyntheticMarket(seed=3).candles(50), SyntheticMarket(seed=3).candles(50))
        for c in SyntheticMarket(seed=3).candles(200):
            self.assertTrue(c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high)

    def test_parse_csv_variants(self):
        text = "Date,Open,High,Low,Close,Volume\n2024-01-02,10,11,9,10.5,100\n2024-01-01,9,10,8,9.5,\n"
        candles = parse_csv(text)
        self.assertEqual([c.close for c in candles], [9.5, 10.5])  # sorted by time
        self.assertEqual(candles[0].volume, 0.0)
        ms = parse_csv("timestamp,open,high,low,close\n1700000000000,1,1,1,1\n")
        self.assertEqual(ms[0].time, 1_700_000_000)

    def test_parse_csv_errors(self):
        with self.assertRaises(ValueError):
            parse_csv("time,open,close\n1,2,3\n")
        with self.assertRaises(ValueError):
            parse_csv("time,open,high,low,close\n1,a,1,1,1\n")

    def test_max_drawdown(self):
        self.assertAlmostEqual(max_drawdown([100, 120, 90, 130, 117]), 0.25)
        self.assertEqual(max_drawdown([1, 2, 3]), 0.0)

    def test_summary_profit_factor_undefined_without_losses(self):
        m = summarize([100, 110], [10.0], 86400, 1, 0.0)
        self.assertIsNone(m["profit_factor"])
        self.assertEqual(m["win_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
