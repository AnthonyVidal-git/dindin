"""Technical indicators.

Every function returns a list the same length as its input, with ``None``
where there is not enough history yet. That keeps series aligned with the
candles they came from, which matters for charts and for avoiding
off-by-one look-ahead bugs.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

Series = list[Optional[float]]


def _check_period(period: int) -> None:
    if not isinstance(period, int) or period < 1:
        raise ValueError("period must be a positive integer")


def sma(values: Sequence[float], period: int) -> Series:
    _check_period(period)
    out: Series = [None] * len(values)
    total = 0.0
    for i, value in enumerate(values):
        total += value
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def ema(values: Sequence[Optional[float]], period: int) -> Series:
    """Exponential moving average, seeded with the SMA of the first
    ``period`` non-empty values. Leading ``None`` values are skipped."""
    _check_period(period)
    out: Series = [None] * len(values)
    k = 2 / (period + 1)
    seed: list[float] = []
    prev: Optional[float] = None
    for i, value in enumerate(values):
        if value is None:
            continue
        if prev is None:
            seed.append(value)
            if len(seed) == period:
                prev = sum(seed) / period
                out[i] = prev
            continue
        prev = value * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values: Sequence[float], period: int = 14) -> Series:
    """Relative Strength Index with Wilder smoothing."""
    _check_period(period)
    out: Series = [None] * len(values)
    if len(values) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain, avg_loss = gains / period, losses / period

    def value(g: float, l: float) -> float:
        if l == 0:
            return 100.0 if g > 0 else 50.0
        return 100 - 100 / (1 + g / l)

    out[period] = value(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[i] = value(avg_gain, avg_loss)
    return out


def macd(values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[Series, Series, Series]:
    """Returns (macd line, signal line, histogram)."""
    if fast >= slow:
        raise ValueError("fast period must be smaller than slow period")
    fast_ema, slow_ema = ema(values, fast), ema(values, slow)
    line: Series = [f - s if f is not None and s is not None else None for f, s in zip(fast_ema, slow_ema)]
    sig = ema(line, signal)
    hist: Series = [m - s if m is not None and s is not None else None for m, s in zip(line, sig)]
    return line, sig, hist


def bollinger(values: Sequence[float], period: int = 20, k: float = 2.0) -> tuple[Series, Series, Series]:
    """Returns (middle, upper, lower) bands using population std-dev."""
    _check_period(period)
    mid = sma(values, period)
    upper: Series = [None] * len(values)
    lower: Series = [None] * len(values)
    for i in range(period - 1, len(values)):
        mean = mid[i]
        window = values[i - period + 1 : i + 1]
        std = math.sqrt(sum((x - mean) ** 2 for x in window) / period)
        upper[i] = mean + k * std
        lower[i] = mean - k * std
    return mid, upper, lower


def crossed_above(a: Series, b: Series) -> bool:
    """True when ``a`` crossed above ``b`` on the last bar."""
    if len(a) < 2 or None in (a[-1], a[-2], b[-1], b[-2]):
        return False
    return a[-2] <= b[-2] and a[-1] > b[-1]


def crossed_below(a: Series, b: Series) -> bool:
    if len(a) < 2 or None in (a[-1], a[-2], b[-1], b[-2]):
        return False
    return a[-2] >= b[-2] and a[-1] < b[-1]
