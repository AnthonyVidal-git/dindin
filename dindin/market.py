"""Real market data from Binance's public REST API.

Only public market-data endpoints are used, so no account or API key is
needed and nothing here can place an order. Requests go to the public
data mirror first and fall back to the main API host.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from . import __version__
from .data import Candle

HOSTS = ("https://data-api.binance.vision", "https://api.binance.com")
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT")
TIMEFRAMES = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14_400, "1d": 86_400}
MAX_PER_REQUEST = 1000
MAX_HISTORY = 5000


class MarketError(Exception):
    """The exchange could not be reached or answered with something unusable."""


def validate(symbol: str, timeframe: str) -> tuple[str, str]:
    symbol = str(symbol).upper()
    if symbol not in SYMBOLS:
        raise ValueError(f"unsupported symbol '{symbol}'. Options: {', '.join(SYMBOLS)}")
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"unsupported timeframe '{timeframe}'. Options: {', '.join(TIMEFRAMES)}")
    return symbol, timeframe


def _get(path: str, params: dict, timeout: float = 8.0):
    query = urllib.parse.urlencode(params)
    last_error: Optional[Exception] = None
    for host in HOSTS:
        request = urllib.request.Request(f"{host}{path}?{query}", headers={"User-Agent": f"dindin/{__version__}"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 400:  # bad symbol or params: another host won't help
                raise MarketError(f"exchange rejected the request ({exc.code})") from None
            last_error = exc
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
    raise MarketError(f"could not reach the exchange: {last_error}")


def parse_kline(row: list) -> tuple[Candle, int]:
    """Binance kline row -> (candle, close time in ms)."""
    try:
        candle = Candle(int(row[0]) // 1000, float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5]))
        return candle, int(row[6])
    except (IndexError, TypeError, ValueError):
        raise MarketError("unexpected kline format from the exchange") from None


def fetch_recent(symbol: str, timeframe: str, limit: int = 3, now_ms: Optional[int] = None) -> tuple[list[Candle], Optional[Candle]]:
    """Latest candles split into (closed candles, the candle still forming)."""
    symbol, timeframe = validate(symbol, timeframe)
    rows = _get("/api/v3/klines", {"symbol": symbol, "interval": timeframe, "limit": max(1, min(limit, MAX_PER_REQUEST))})
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    closed: list[Candle] = []
    forming: Optional[Candle] = None
    for row in rows:
        candle, close_ms = parse_kline(row)
        if close_ms < now_ms:
            closed.append(candle)
        else:
            forming = candle
    return closed, forming


def fetch_history(symbol: str, timeframe: str, bars: int) -> list[Candle]:
    """The last ``bars`` closed candles, paging backwards past the 1000-row limit."""
    symbol, timeframe = validate(symbol, timeframe)
    bars = max(1, min(int(bars), MAX_HISTORY))
    now_ms = int(time.time() * 1000)
    candles: list[Candle] = []
    end_time: Optional[int] = None
    while len(candles) < bars:
        params = {"symbol": symbol, "interval": timeframe, "limit": min(MAX_PER_REQUEST, bars - len(candles) + 1)}
        if end_time is not None:
            params["endTime"] = end_time
        rows = _get("/api/v3/klines", params)
        if not rows:
            break
        page = [c for c, close_ms in map(parse_kline, rows) if close_ms < now_ms]
        candles = page + candles
        end_time = int(rows[0][0]) - 1
        if len(rows) < params["limit"]:
            break  # reached the start of the market's history
    unique = {c.time: c for c in candles}  # pages can overlap by one row
    return sorted(unique.values(), key=lambda c: c.time)[-bars:]
