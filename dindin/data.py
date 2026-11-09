"""Market data: OHLCV candles, a seeded synthetic market and a CSV loader."""

from __future__ import annotations

import csv
import math
import random
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Candle:
    time: int  # unix seconds (UTC)
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_dict(self) -> dict:
        return asdict(self)


class SyntheticMarket:
    """Geometric Brownian motion with random bull/bear/sideways regimes.

    The same seed always produces the same price path, so backtests are
    reproducible and tests are deterministic.
    """

    def __init__(
        self,
        seed: int = 42,
        start_price: float = 100.0,
        volatility: float = 0.018,
        interval: int = 86400,
        start_time: int = 1_700_000_000,
    ) -> None:
        if start_price <= 0:
            raise ValueError("start_price must be positive")
        if volatility <= 0:
            raise ValueError("volatility must be positive")
        self._rng = random.Random(seed)
        self.price = float(start_price)
        self.volatility = float(volatility)
        self.interval = int(interval)
        self.time = int(start_time)
        self._regime = 0
        self._regime_left = 0

    def next_candle(self) -> Candle:
        rng = self._rng
        if self._regime_left <= 0:
            self._regime = rng.choice((-1, 0, 1))
            self._regime_left = rng.randint(40, 160)
        self._regime_left -= 1

        sigma = self.volatility
        drift = self._regime * sigma * 0.06
        steps = 4
        open_ = self.price
        path = [open_]
        price = open_
        for _ in range(steps):
            shock = rng.gauss(0.0, 1.0)
            price *= math.exp((drift - 0.5 * sigma**2) / steps + sigma * shock / math.sqrt(steps))
            path.append(price)

        high = max(path) * (1 + abs(rng.gauss(0, sigma * 0.25)))
        low = min(path) * (1 - abs(rng.gauss(0, sigma * 0.25)))
        move = abs(price / open_ - 1)
        volume = rng.uniform(800, 1400) * (1 + move * 40)

        self.time += self.interval
        self.price = price
        return Candle(self.time, round(open_, 4), round(high, 4), round(low, 4), round(price, 4), round(volume, 2))

    def candles(self, count: int) -> list[Candle]:
        if count < 1:
            raise ValueError("count must be at least 1")
        return [self.next_candle() for _ in range(count)]


_ALIASES = {
    "time": ("time", "timestamp", "date", "datetime", "open_time"),
    "open": ("open", "o"),
    "high": ("high", "h"),
    "low": ("low", "l"),
    "close": ("close", "c", "adj close", "adj_close"),
    "volume": ("volume", "vol", "v"),
}


def _parse_time(raw: str) -> int:
    raw = raw.strip()
    try:
        value = float(raw)
        return int(value / 1000) if value > 1e11 else int(value)  # ms or s
    except ValueError:
        pass
    text = raw.replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def parse_csv(text: str) -> list[Candle]:
    """Parse OHLCV CSV text. Header names are matched case-insensitively.

    Volume is optional. Rows are sorted by time.
    """
    reader = csv.reader(text.strip().splitlines())
    try:
        header = [h.strip().lower() for h in next(reader)]
    except StopIteration:
        raise ValueError("CSV is empty") from None

    columns: dict[str, int] = {}
    for field, names in _ALIASES.items():
        for name in names:
            if name in header:
                columns[field] = header.index(name)
                break
    missing = [f for f in ("time", "open", "high", "low", "close") if f not in columns]
    if missing:
        raise ValueError(f"CSV is missing columns: {', '.join(missing)}")

    candles = []
    for line_no, row in enumerate(reader, start=2):
        if not row or not any(cell.strip() for cell in row):
            continue
        try:
            o, h, l, c = (float(row[columns[k]]) for k in ("open", "high", "low", "close"))
            v = float(row[columns["volume"]]) if "volume" in columns and row[columns["volume"]].strip() else 0.0
            candles.append(Candle(_parse_time(row[columns["time"]]), o, max(h, o, c), min(l, o, c), c, v))
        except (ValueError, IndexError) as exc:
            raise ValueError(f"CSV line {line_no}: {exc}") from None
    if not candles:
        raise ValueError("CSV has no data rows")
    candles.sort(key=lambda candle: candle.time)
    return candles


def load_csv(path: str | Path) -> list[Candle]:
    return parse_csv(Path(path).read_text(encoding="utf-8-sig"))
