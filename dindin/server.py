"""Local HTTP server: JSON API plus the static dashboard.

Standard library only. Binds to 127.0.0.1 by default so nothing is exposed
to the network unless you ask for it.
"""

from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from . import market
from .data import SyntheticMarket, parse_csv
from .engine import RiskConfig, run_backtest
from .live import LiveSession
from .strategies import STRATEGIES, catalog, create_strategy

WEB_ROOT = (Path(__file__).resolve().parent.parent / "web").resolve()
MAX_BODY = 5 * 1024 * 1024  # 5 MB, enough for a large CSV
MAX_BARS = 20_000


class ApiError(Exception):
    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = status


def _int(data: dict, key: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(data.get(key, default))
    except (TypeError, ValueError):
        raise ApiError(f"{key} must be an integer") from None
    if not lo <= value <= hi:
        raise ApiError(f"{key} must be between {lo} and {hi}")
    return value


def _float(data: dict, key: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(data.get(key, default))
    except (TypeError, ValueError):
        raise ApiError(f"{key} must be a number") from None
    if not lo <= value <= hi:
        raise ApiError(f"{key} must be between {lo:g} and {hi:g}")
    return value


def _candles(data: dict):
    if data.get("csv"):
        candles = parse_csv(str(data["csv"]))
        if len(candles) > MAX_BARS:
            candles = candles[-MAX_BARS:]
        return candles
    if data.get("source") == "binance":
        bars = _int(data, "bars", 1000, 50, market.MAX_HISTORY)
        candles = market.fetch_history(str(data.get("symbol", "BTCUSDT")), str(data.get("timeframe", "1h")), bars)
        if len(candles) < 50:
            raise ApiError("the exchange returned too little history for this market")
        return candles
    bars = _int(data, "bars", 1000, 50, MAX_BARS)
    seed = _int(data, "seed", 42, 0, 2**31 - 1)
    return SyntheticMarket(seed=seed).candles(bars)


def handle_backtest(data: dict) -> dict:
    strategy = create_strategy(str(data.get("strategy", "sma_cross")), data.get("params"))
    risk = RiskConfig.from_dict(data.get("risk"))
    cash = _float(data, "initial_cash", 10_000, 1, 1e12)
    return run_backtest(_candles(data), strategy, cash, risk).to_dict()


def handle_compare(data: dict) -> dict:
    candles = _candles(data)
    risk = RiskConfig.from_dict(data.get("risk"))
    cash = _float(data, "initial_cash", 10_000, 1, 1e12)
    rows = []
    for key in STRATEGIES:
        result = run_backtest(candles, create_strategy(key), cash, risk)
        rows.append({"strategy": result.strategy, "metrics": result.metrics, "equity": result.equity})
    first = candles[0].close
    benchmark = [cash * c.close / first for c in candles]
    rows.sort(key=lambda row: row["metrics"]["total_return"], reverse=True)
    return {"rows": rows, "benchmark": benchmark, "times": [c.time for c in candles],
            "candles": [c.to_dict() for c in candles]}


class Handler(BaseHTTPRequestHandler):
    server_version = f"dindin/{__version__}"
    live: LiveSession  # set by make_server

    # ---- helpers ---------------------------------------------------------
    def log_message(self, fmt: str, *args) -> None:  # quieter console
        if self.server.verbose:  # type: ignore[attr-defined]
            super().log_message(fmt, *args)

    def _send_json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            raise ApiError("invalid Content-Length") from None
        if length > MAX_BODY:
            raise ApiError("request body too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        if length == 0:
            return {}
        try:
            data = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ApiError("body must be valid JSON") from None
        if not isinstance(data, dict):
            raise ApiError("body must be a JSON object")
        return data

    def _serve_static(self, path: str) -> None:
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (WEB_ROOT / rel).resolve()
        if WEB_ROOT not in target.parents or not target.is_file():  # blocks ../ traversal
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        body = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _dispatch(self, method: str) -> None:
        url = urlparse(self.path)
        route = (method, url.path.rstrip("/") or "/")
        try:
            if route == ("GET", "/api/health"):
                self._send_json({"status": "ok", "version": __version__})
            elif route == ("GET", "/api/market"):
                self._send_json({"symbols": list(market.SYMBOLS), "timeframes": list(market.TIMEFRAMES)})
            elif route == ("GET", "/api/strategies"):
                self._send_json({"strategies": catalog(), "risk_defaults": RiskConfig().__dict__})
            elif route == ("POST", "/api/backtest"):
                self._send_json(handle_backtest(self._read_json()))
            elif route == ("POST", "/api/compare"):
                self._send_json(handle_compare(self._read_json()))
            elif route == ("GET", "/api/live"):
                since = parse_qs(url.query).get("since", ["0"])[0]
                self._send_json(self.live.snapshot(int(since) if since.isdigit() else 0))
            elif route == ("POST", "/api/live/start"):
                self.live.start(**self._live_args(self._read_json()))
                self._send_json(self.live.snapshot())
            elif route == ("POST", "/api/live/stop"):
                self.live.stop()
                self._send_json(self.live.snapshot())
            elif method == "GET" and not url.path.startswith("/api/"):
                self._serve_static(url.path)
            else:
                raise ApiError("not found", HTTPStatus.NOT_FOUND)
        except market.MarketError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_GATEWAY)
        except ApiError as exc:
            self._send_json({"error": str(exc)}, exc.status)
        except ValueError as exc:  # validation errors from the engine
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception:  # noqa: BLE001 - never leak a traceback to the client
            self.log_error("unhandled error on %s %s", method, self.path)
            self._send_json({"error": "internal server error"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    @staticmethod
    def _live_args(data: dict) -> dict:
        allowed = {"strategy", "params", "risk", "initial_cash", "seed", "interval", "source", "symbol", "timeframe"}
        unknown = set(data) - allowed
        if unknown:
            raise ApiError(f"unknown field(s): {', '.join(sorted(unknown))}")
        return data

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")


def make_server(host: str = "127.0.0.1", port: int = 8000, verbose: bool = False) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"live": LiveSession()})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    server.verbose = verbose  # type: ignore[attr-defined]
    return server
