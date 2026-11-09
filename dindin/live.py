"""Live paper trading.

A background thread feeds candles to the engine as they appear, either from
a seeded synthetic market or from the real market (Binance public data).
The bot only trades on paper: no real order is ever sent anywhere.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import asdict
from typing import Optional

from . import market
from .data import Candle, SyntheticMarket
from .engine import Engine, RiskConfig
from .metrics import drawdown_series
from .strategies import Strategy, create_strategy

HISTORY = 300  # candles kept for the chart
REAL_POLL_SECONDS = 2.0


class LiveSession:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._config: dict = {}
        self._engine: Optional[Engine] = None
        self._market: Optional[SyntheticMarket] = None
        self._candles: deque[Candle] = deque(maxlen=HISTORY)
        self._forming: Optional[Candle] = None
        self._first_close: Optional[float] = None
        self._live_from_bar = 0
        self._error: Optional[str] = None
        self._interval = 0.5

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(
        self,
        strategy: str = "sma_cross",
        params: dict | None = None,
        risk: dict | None = None,
        initial_cash: float = 10_000.0,
        seed: int = 7,
        interval: float = 0.5,
        source: str = "synthetic",
        symbol: str = "BTCUSDT",
        timeframe: str = "1m",
        warm_bars: int = 120,
    ) -> None:
        strat: Strategy = create_strategy(strategy, params)
        risk_cfg = RiskConfig.from_dict(risk)
        if source not in ("synthetic", "binance"):
            raise ValueError("source must be 'synthetic' or 'binance'")
        if source == "binance":
            symbol, timeframe = market.validate(symbol, timeframe)
            interval = REAL_POLL_SECONDS
            # Network call outside the lock; a failure here is reported to the caller.
            history = market.fetch_history(symbol, timeframe, HISTORY)
            if len(history) < strat.warmup + 2:
                raise ValueError("not enough market history for this strategy")
        else:
            interval = float(interval)
            if not 0.05 <= interval <= 10:
                raise ValueError("interval must be between 0.05 and 10 seconds")
            history = []

        self.stop()
        with self._lock:
            self._engine = Engine(strat, float(initial_cash), risk_cfg)
            self._candles.clear()
            self._forming = None
            self._first_close = None
            self._error = None
            self._interval = interval
            self._config = {
                "strategy": strat.describe(), "risk": asdict(risk_cfg), "initial_cash": float(initial_cash),
                "source": source, "interval": interval,
            }
            if source == "binance":
                self._market = None
                self._config.update(symbol=symbol, timeframe=timeframe)
                # Replay recent real history so indicators are warm and the chart is full.
                for candle in history:
                    self._process(candle)
            else:
                self._market = SyntheticMarket(seed=int(seed))
                self._config["seed"] = int(seed)
                for _ in range(max(0, int(warm_bars))):
                    self._process(self._market.next_candle())
            self._live_from_bar = self._engine.bar + 1
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="live-session", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=REAL_POLL_SECONDS + 1)
        self._thread = None

    def _process(self, candle: Candle) -> None:
        assert self._engine is not None
        if self._first_close is None:
            self._first_close = candle.close
        self._candles.append(candle)
        self._engine.on_candle(candle)

    def _poll_real(self) -> None:
        cfg = self._config
        try:
            closed, forming = market.fetch_recent(cfg["symbol"], cfg["timeframe"], limit=3)
        except market.MarketError as exc:
            with self._lock:
                self._error = str(exc)
            return
        with self._lock:
            self._error = None
            last = self._engine.last_candle.time if self._engine and self._engine.last_candle else 0
            for candle in closed:
                if candle.time > last:
                    self._process(candle)
            self._forming = forming

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            if self._config.get("source") == "binance":
                self._poll_real()
            else:
                with self._lock:
                    self._process(self._market.next_candle())

    def snapshot(self, since_fill: int = 0) -> dict:
        with self._lock:
            engine = self._engine
            if engine is None:
                return {"running": False, "started": False}
            candles = list(self._candles)
            equity = engine.equity[-len(candles):]
            pos = engine.position
            forming = self._forming
            last = forming.close if forming else (candles[-1].close if candles else 0.0)
            closes = [c.close for c in candles]
            window_start = engine.bar - len(candles) + 1
            return {
                "running": self.running,
                "started": True,
                "config": self._config,
                "bar": engine.bar,
                "candles": [c.to_dict() for c in candles],
                "forming": forming.to_dict() if forming else None,
                "forming_equity": engine.equity_at(last) if forming else None,
                "live_from": self._live_from_bar - window_start,
                "equity": equity,
                "drawdown": drawdown_series(equity),
                "overlays": engine.strategy.overlays(closes),
                "fills": [asdict(f) for f in engine.fills[since_fill:]],
                "fill_count": len(engine.fills),
                "trades": [asdict(t) for t in engine.trades[-200:]],
                "trade_count": len(engine.trades),
                "position": None if pos is None else {
                    "quantity": pos.quantity,
                    "entry_price": pos.entry_price,
                    "unrealized": pos.quantity * last - pos.cost,
                },
                "metrics": engine.summary(self._first_close or last, self._bar_seconds()),
                "error": self._error,
            }

    def _bar_seconds(self) -> float:
        if self._config.get("source") == "binance":
            return float(market.TIMEFRAMES[self._config["timeframe"]])
        return float(self._market.interval) if self._market else 86_400.0


def wait_for_bars(session: LiveSession, bars: int, timeout: float = 5.0) -> bool:
    """Test helper: block until the session has processed ``bars`` bars."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if session.snapshot().get("bar", -1) >= bars:
            return True
        time.sleep(0.02)
    return False
