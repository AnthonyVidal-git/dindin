"""Trading strategies.

A strategy looks at closing prices up to the current bar and answers with
BUY, SELL or HOLD. It never sees the future: the engine only hands it
history it already has. The same class runs in backtests and live mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from . import indicators as ind

BUY, SELL, HOLD = 1, -1, 0


@dataclass(frozen=True)
class Param:
    name: str
    default: float
    minimum: float
    maximum: float
    step: float = 1
    integer: bool = True


class Strategy:
    key = ""
    label = ""
    params_spec: tuple[Param, ...] = ()

    def __init__(self, **params: float) -> None:
        self.params: dict[str, float] = {}
        known = {p.name: p for p in self.params_spec}
        unknown = set(params) - set(known)
        if unknown:
            raise ValueError(f"unknown parameter(s) for {self.key}: {', '.join(sorted(unknown))}")
        for spec in self.params_spec:
            raw = params.get(spec.name, spec.default)
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{spec.name} must be a number") from None
            if not spec.minimum <= value <= spec.maximum:
                raise ValueError(f"{spec.name} must be between {spec.minimum:g} and {spec.maximum:g}")
            self.params[spec.name] = int(value) if spec.integer else value
        self.validate()

    def validate(self) -> None:
        """Hook for cross-parameter checks."""

    @property
    def warmup(self) -> int:
        """Bars of history needed before signals are meaningful."""
        raise NotImplementedError

    @property
    def window(self) -> int:
        """How much history the engine keeps for this strategy. A fixed
        window keeps live mode O(1) per bar and identical to backtests."""
        return self.warmup * 4 + 50

    def signal(self, closes: Sequence[float]) -> int:
        raise NotImplementedError

    def overlays(self, closes: Sequence[float]) -> dict[str, list]:
        """Indicator lines drawn on the price chart."""
        return {}

    def describe(self) -> dict:
        return {"key": self.key, "label": self.label, "params": dict(self.params)}


class SmaCrossover(Strategy):
    key = "sma_cross"
    label = "SMA crossover"
    params_spec = (Param("fast", 10, 2, 100), Param("slow", 30, 3, 300))

    def validate(self) -> None:
        if self.params["fast"] >= self.params["slow"]:
            raise ValueError("fast must be smaller than slow")

    @property
    def warmup(self) -> int:
        return self.params["slow"] + 1

    def signal(self, closes: Sequence[float]) -> int:
        fast, slow = ind.sma(closes, self.params["fast"]), ind.sma(closes, self.params["slow"])
        if ind.crossed_above(fast, slow):
            return BUY
        if ind.crossed_below(fast, slow):
            return SELL
        return HOLD

    def overlays(self, closes: Sequence[float]) -> dict[str, list]:
        return {
            f"SMA {self.params['fast']}": ind.sma(closes, self.params["fast"]),
            f"SMA {self.params['slow']}": ind.sma(closes, self.params["slow"]),
        }


class RsiReversion(Strategy):
    key = "rsi"
    label = "RSI mean reversion"
    params_spec = (
        Param("period", 14, 2, 100),
        Param("oversold", 30, 5, 50),
        Param("overbought", 70, 50, 95),
    )

    def validate(self) -> None:
        if self.params["oversold"] >= self.params["overbought"]:
            raise ValueError("oversold must be below overbought")

    @property
    def warmup(self) -> int:
        return self.params["period"] + 2

    def signal(self, closes: Sequence[float]) -> int:
        values = ind.rsi(closes, self.params["period"])
        if len(values) < 2 or values[-1] is None or values[-2] is None:
            return HOLD
        prev, now = values[-2], values[-1]
        if prev >= self.params["oversold"] > now:
            return BUY
        if prev <= self.params["overbought"] < now:
            return SELL
        return HOLD


class MacdMomentum(Strategy):
    key = "macd"
    label = "MACD momentum"
    params_spec = (Param("fast", 12, 2, 50), Param("slow", 26, 3, 100), Param("signal", 9, 2, 50))

    def validate(self) -> None:
        if self.params["fast"] >= self.params["slow"]:
            raise ValueError("fast must be smaller than slow")

    @property
    def warmup(self) -> int:
        return self.params["slow"] + self.params["signal"] + 1

    def signal(self, closes: Sequence[float]) -> int:
        line, sig, _ = ind.macd(closes, self.params["fast"], self.params["slow"], self.params["signal"])
        if ind.crossed_above(line, sig):
            return BUY
        if ind.crossed_below(line, sig):
            return SELL
        return HOLD

    def overlays(self, closes: Sequence[float]) -> dict[str, list]:
        return {f"EMA {self.params['slow']}": ind.ema(closes, self.params["slow"])}


class BollingerReversion(Strategy):
    key = "bollinger"
    label = "Bollinger reversion"
    params_spec = (Param("period", 20, 5, 200), Param("k", 2.0, 0.5, 4.0, step=0.1, integer=False))

    @property
    def warmup(self) -> int:
        return self.params["period"] + 1

    def signal(self, closes: Sequence[float]) -> int:
        mid, upper, lower = ind.bollinger(closes, self.params["period"], self.params["k"])
        if lower[-1] is None:
            return HOLD
        price = closes[-1]
        if price < lower[-1]:
            return BUY
        if price > mid[-1]:
            return SELL
        return HOLD

    def overlays(self, closes: Sequence[float]) -> dict[str, list]:
        mid, upper, lower = ind.bollinger(closes, self.params["period"], self.params["k"])
        return {"BB mid": mid, "BB upper": upper, "BB lower": lower}


STRATEGIES: dict[str, type[Strategy]] = {
    cls.key: cls for cls in (SmaCrossover, RsiReversion, MacdMomentum, BollingerReversion)
}


def create_strategy(key: str, params: dict | None = None) -> Strategy:
    try:
        cls = STRATEGIES[key]
    except KeyError:
        raise ValueError(f"unknown strategy '{key}'. Options: {', '.join(STRATEGIES)}") from None
    return cls(**(params or {}))


def catalog() -> list[dict]:
    return [
        {
            "key": cls.key,
            "label": cls.label,
            "params": [p.__dict__ for p in cls.params_spec],
        }
        for cls in STRATEGIES.values()
    ]
