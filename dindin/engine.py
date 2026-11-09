"""Event-driven trading engine shared by backtests and live paper trading.

Execution model (kept simple on purpose, and documented so results are
honest):

* Long-only spot trading, one position at a time.
* Signals are computed on a bar's close and filled at that close, adjusted
  by slippage. The strategy never sees data from a later bar.
* Stop-loss and take-profit are checked intrabar against the high/low of
  the *next* bars. If the bar opens past the stop (a gap), the fill is at
  the open, which is worse, like in real markets. If both levels are
  touched in the same bar, the stop wins (pessimistic assumption).
* Fees are charged on both sides as a fraction of notional.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, asdict, field
from typing import Optional, Sequence

from .data import Candle
from .metrics import drawdown_series, summarize
from .strategies import BUY, SELL, Strategy


@dataclass
class RiskConfig:
    position_size: float = 0.95  # fraction of equity committed per trade
    stop_loss: float = 0.05  # 0 disables
    take_profit: float = 0.10  # 0 disables
    fee_rate: float = 0.001  # 0.1% per side
    slippage: float = 0.0005  # 0.05% adverse price move per fill

    def __post_init__(self) -> None:
        limits = {
            "position_size": (0.01, 1.0),
            "stop_loss": (0.0, 0.5),
            "take_profit": (0.0, 2.0),
            "fee_rate": (0.0, 0.05),
            "slippage": (0.0, 0.05),
        }
        for name, (lo, hi) in limits.items():
            try:
                value = float(getattr(self, name))
            except (TypeError, ValueError):
                raise ValueError(f"{name} must be a number") from None
            if not lo <= value <= hi:
                raise ValueError(f"{name} must be between {lo:g} and {hi:g}")
            setattr(self, name, value)

    @classmethod
    def from_dict(cls, data: dict | None) -> "RiskConfig":
        data = data or {}
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"unknown risk setting(s): {', '.join(sorted(unknown))}")
        return cls(**data)


@dataclass
class Fill:
    time: int
    side: str  # "buy" | "sell"
    price: float
    quantity: float
    fee: float
    reason: str  # "signal" | "stop_loss" | "take_profit" | "end_of_data"
    bar: int


@dataclass
class Trade:
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float  # net of all fees
    return_pct: float
    exit_reason: str
    bars_held: int


@dataclass
class Position:
    quantity: float
    entry_price: float
    entry_time: int
    entry_bar: int
    cost: float  # cash spent including entry fee


class Engine:
    def __init__(self, strategy: Strategy, initial_cash: float = 10_000.0, risk: Optional[RiskConfig] = None) -> None:
        if initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        self.strategy = strategy
        self.risk = risk or RiskConfig()
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.position: Optional[Position] = None
        self.fills: list[Fill] = []
        self.trades: list[Trade] = []
        self.equity: list[float] = []
        self.times: list[int] = []
        self.bars_in_market = 0
        self.bar = -1
        self.last_candle: Optional[Candle] = None
        self._closes: deque[float] = deque(maxlen=strategy.window)

    # ---- state -----------------------------------------------------------
    def equity_at(self, price: float) -> float:
        held = self.position.quantity * price if self.position else 0.0
        return self.cash + held

    # ---- orders ----------------------------------------------------------
    def _buy(self, price: float, candle: Candle) -> Fill:
        exec_price = price * (1 + self.risk.slippage)
        budget = self.equity_at(price) * self.risk.position_size
        quantity = budget / (exec_price * (1 + self.risk.fee_rate))
        fee = quantity * exec_price * self.risk.fee_rate
        cost = quantity * exec_price + fee
        self.cash -= cost
        self.position = Position(quantity, exec_price, candle.time, self.bar, cost)
        fill = Fill(candle.time, "buy", exec_price, quantity, fee, "signal", self.bar)
        self.fills.append(fill)
        return fill

    def _sell(self, price: float, candle: Candle, reason: str) -> Fill:
        pos = self.position
        assert pos is not None
        exec_price = price * (1 - self.risk.slippage)
        proceeds = pos.quantity * exec_price
        fee = proceeds * self.risk.fee_rate
        self.cash += proceeds - fee
        pnl = proceeds - fee - pos.cost
        self.trades.append(
            Trade(pos.entry_time, candle.time, pos.entry_price, exec_price, pos.quantity, pnl,
                  pnl / pos.cost, reason, self.bar - pos.entry_bar)
        )
        fill = Fill(candle.time, "sell", exec_price, pos.quantity, fee, reason, self.bar)
        self.fills.append(fill)
        self.position = None
        return fill

    def _check_exits(self, candle: Candle) -> Optional[Fill]:
        pos = self.position
        if pos is None:
            return None
        sl, tp = self.risk.stop_loss, self.risk.take_profit
        if sl > 0:
            stop = pos.entry_price * (1 - sl)
            if candle.low <= stop:
                return self._sell(min(candle.open, stop), candle, "stop_loss")
        if tp > 0:
            target = pos.entry_price * (1 + tp)
            if candle.high >= target:
                return self._sell(max(candle.open, target), candle, "take_profit")
        return None

    # ---- main loop -------------------------------------------------------
    def on_candle(self, candle: Candle) -> list[Fill]:
        """Process one bar and return the fills it produced."""
        if self.last_candle is not None and candle.time <= self.last_candle.time:
            raise ValueError("candles must be in strictly increasing time order")
        self.bar += 1
        self.last_candle = candle
        fills: list[Fill] = []

        # Exits are only checked on bars after the entry bar.
        if self.position is not None and self.position.entry_bar < self.bar:
            exit_fill = self._check_exits(candle)
            if exit_fill:
                fills.append(exit_fill)

        self._closes.append(candle.close)
        if len(self._closes) >= self.strategy.warmup:
            action = self.strategy.signal(list(self._closes))
            if action == BUY and self.position is None and not fills:
                fills.append(self._buy(candle.close, candle))
            elif action == SELL and self.position is not None:
                fills.append(self._sell(candle.close, candle, "signal"))

        if self.position is not None:
            self.bars_in_market += 1
        self.equity.append(self.equity_at(candle.close))
        self.times.append(candle.time)
        return fills

    def close_all(self, reason: str = "end_of_data") -> Optional[Fill]:
        if self.position is None or self.last_candle is None:
            return None
        fill = self._sell(self.last_candle.close, self.last_candle, reason)
        self.equity[-1] = self.cash
        return fill

    def summary(self, candles_first_close: float, seconds_per_bar: float) -> dict:
        last = self.last_candle.close if self.last_candle else candles_first_close
        benchmark = last / candles_first_close - 1 if candles_first_close > 0 else 0.0
        return summarize(
            [self.initial_cash] + self.equity,
            [t.pnl for t in self.trades],
            seconds_per_bar,
            self.bars_in_market,
            benchmark,
        )


@dataclass
class BacktestResult:
    strategy: dict
    risk: dict
    metrics: dict
    candles: list[dict]
    equity: list[float]
    benchmark: list[float]
    drawdown: list[float]
    overlays: dict[str, list]
    fills: list[dict] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def bar_seconds(candles: Sequence[Candle]) -> float:
    if len(candles) < 2:
        return 86400.0
    gaps = sorted(b.time - a.time for a, b in zip(candles, candles[1:]))
    return float(gaps[len(gaps) // 2]) or 86400.0  # median survives weekends/holes


def run_backtest(
    candles: Sequence[Candle],
    strategy: Strategy,
    initial_cash: float = 10_000.0,
    risk: Optional[RiskConfig] = None,
) -> BacktestResult:
    if len(candles) < strategy.warmup + 2:
        raise ValueError(f"need at least {strategy.warmup + 2} candles for {strategy.key}, got {len(candles)}")
    engine = Engine(strategy, initial_cash, risk)
    for candle in candles:
        engine.on_candle(candle)
    engine.close_all()

    first_close = candles[0].close
    closes = [c.close for c in candles]
    return BacktestResult(
        strategy=strategy.describe(),
        risk=asdict(engine.risk),
        metrics=engine.summary(first_close, bar_seconds(candles)),
        candles=[c.to_dict() for c in candles],
        equity=engine.equity,
        benchmark=[initial_cash * c / first_close for c in closes],
        drawdown=drawdown_series(engine.equity),
        overlays=strategy.overlays(closes),
        fills=[asdict(f) for f in engine.fills],
        trades=[asdict(t) for t in engine.trades],
    )
