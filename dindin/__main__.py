"""Command line interface.

    python -m dindin serve                 # dashboard at http://127.0.0.1:8000
    python -m dindin backtest --strategy rsi --bars 2000
    python -m dindin backtest --csv prices.csv --param fast=20 --param slow=50
    python -m dindin compare --seed 3
    python -m dindin backtest --symbol BTCUSDT --timeframe 1h --bars 2000   # real market data
"""

from __future__ import annotations

import argparse
import sys
import webbrowser

from . import __version__, market
from .data import SyntheticMarket, load_csv
from .engine import RiskConfig, run_backtest
from .strategies import STRATEGIES, create_strategy


def _params(pairs: list[str]) -> dict:
    out = {}
    for pair in pairs:
        name, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"--param expects name=value, got '{pair}'")
        out[name.strip()] = value.strip()
    return out


def _candles(args):
    if args.csv:
        return load_csv(args.csv)
    if args.symbol:
        return market.fetch_history(args.symbol, args.timeframe, args.bars)
    return SyntheticMarket(seed=args.seed).candles(args.bars)


def _risk(args) -> RiskConfig:
    return RiskConfig(stop_loss=args.stop_loss, take_profit=args.take_profit, fee_rate=args.fee)


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.2f}%"


def _print_metrics(m: dict) -> None:
    pf = "n/a" if m["profit_factor"] is None else f"{m['profit_factor']:.2f}"
    rows = [
        ("Total return", _pct(m["total_return"])),
        ("Buy & hold", _pct(m["benchmark_return"])),
        ("CAGR", _pct(m["cagr"])),
        ("Sharpe", f"{m['sharpe']:.2f}"),
        ("Sortino", f"{m['sortino']:.2f}"),
        ("Max drawdown", f"{-m['max_drawdown'] * 100:.2f}%"),
        ("Trades", str(m["trades"])),
        ("Win rate", f"{m['win_rate'] * 100:.1f}%"),
        ("Profit factor", pf),
        ("Exposure", f"{m['exposure'] * 100:.1f}%"),
        ("End equity", f"{m['end_equity']:,.2f}"),
    ]
    width = max(len(k) for k, _ in rows)
    for key, value in rows:
        print(f"  {key:<{width}}  {value:>12}")


def cmd_serve(args) -> None:
    from .server import make_server

    server = make_server(args.host, args.port, verbose=args.verbose)
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '') else args.host}:{args.port}"
    print(f"dindin {__version__} running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.RequestHandlerClass.live.stop()
        server.server_close()


def cmd_backtest(args) -> None:
    strategy = create_strategy(args.strategy, _params(args.param))
    result = run_backtest(_candles(args), strategy, args.cash, _risk(args))
    print(f"{strategy.label}  {strategy.params}")
    _print_metrics(result.metrics)


def cmd_compare(args) -> None:
    candles = _candles(args)
    results = [run_backtest(candles, create_strategy(k), args.cash, _risk(args)) for k in STRATEGIES]
    results.sort(key=lambda r: r.metrics["total_return"], reverse=True)
    print(f"{'strategy':<22}{'return':>10}{'sharpe':>9}{'max dd':>10}{'trades':>8}")
    for r in results:
        m = r.metrics
        print(f"{r.strategy['label']:<22}{_pct(m['total_return']):>10}{m['sharpe']:>9.2f}"
              f"{-m['max_drawdown'] * 100:>9.2f}%{m['trades']:>8}")
    print(f"{'buy & hold':<22}{_pct(results[0].metrics['benchmark_return']):>10}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dindin", description="Backtesting and paper trading bot.")
    parser.add_argument("--version", action="version", version=f"dindin {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="start the web dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    serve.add_argument("--verbose", action="store_true", help="log every request")
    serve.set_defaults(func=cmd_serve)

    for name, func, text in (("backtest", cmd_backtest, "run one strategy"),
                             ("compare", cmd_compare, "run every strategy on the same data")):
        p = sub.add_parser(name, help=text)
        if name == "backtest":
            p.add_argument("--strategy", default="sma_cross", choices=list(STRATEGIES))
            p.add_argument("--param", action="append", default=[], metavar="NAME=VALUE")
        p.add_argument("--csv", help="OHLCV CSV file (time,open,high,low,close[,volume])")
        p.add_argument("--symbol", help="real market data from Binance, e.g. BTCUSDT")
        p.add_argument("--timeframe", default="1h", choices=list(market.TIMEFRAMES))
        p.add_argument("--bars", type=int, default=1000, help="number of candles (synthetic or real)")
        p.add_argument("--seed", type=int, default=42)
        p.add_argument("--cash", type=float, default=10_000)
        p.add_argument("--stop-loss", type=float, default=0.05)
        p.add_argument("--take-profit", type=float, default=0.10)
        p.add_argument("--fee", type=float, default=0.001)
        p.set_defaults(func=func)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (ValueError, OSError, market.MarketError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
