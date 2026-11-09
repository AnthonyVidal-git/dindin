import unittest

from dindin import indicators as ind


class IndicatorTests(unittest.TestCase):
    def test_sma_aligns_with_input(self):
        self.assertEqual(ind.sma([1, 2, 3, 4, 5], 3), [None, None, 2.0, 3.0, 4.0])

    def test_ema_seeds_with_sma(self):
        out = ind.ema([1, 2, 3, 4], 3)
        self.assertEqual(out[:2], [None, None])
        self.assertAlmostEqual(out[2], 2.0)
        self.assertAlmostEqual(out[3], 3.0)  # 4*0.5 + 2*0.5

    def test_ema_skips_leading_none(self):
        out = ind.ema([None, None, 2, 2, 2], 2)
        self.assertEqual(out[:3], [None, None, None])
        self.assertAlmostEqual(out[3], 2.0)

    def test_rsi_extremes(self):
        self.assertEqual(ind.rsi(list(range(1, 30)), 14)[-1], 100.0)
        self.assertAlmostEqual(ind.rsi(list(range(30, 1, -1)), 14)[-1], 0.0)
        self.assertEqual(ind.rsi([5.0] * 20, 14)[-1], 50.0)

    def test_rsi_stays_in_range(self):
        values = [100 + (i % 7) * (-1) ** i for i in range(200)]
        self.assertTrue(all(0 <= v <= 100 for v in ind.rsi(values, 14) if v is not None))

    def test_bollinger_flat_series_has_zero_width(self):
        mid, up, low = ind.bollinger([10.0] * 25, 20, 2)
        self.assertEqual((mid[-1], up[-1], low[-1]), (10.0, 10.0, 10.0))
        self.assertIsNone(mid[18])

    def test_macd_lengths_match(self):
        line, sig, hist = ind.macd([float(i) for i in range(60)])
        self.assertEqual(len(line), len(sig), )
        self.assertEqual(len(hist), 60)
        self.assertIsNotNone(hist[-1])

    def test_crossovers(self):
        self.assertTrue(ind.crossed_above([1, 3], [2, 2]))
        self.assertFalse(ind.crossed_above([3, 4], [2, 2]))
        self.assertTrue(ind.crossed_below([3, 1], [2, 2]))
        self.assertFalse(ind.crossed_below([None, 1], [2, 2]))

    def test_invalid_period(self):
        with self.assertRaises(ValueError):
            ind.sma([1, 2], 0)
        with self.assertRaises(ValueError):
            ind.macd([1.0] * 50, fast=26, slow=12)


if __name__ == "__main__":
    unittest.main()
