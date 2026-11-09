"""Performance metrics computed from an equity curve and closed trades."""

from __future__ import annotations

import math
from typing import Sequence

SECONDS_PER_YEAR = 365 * 24 * 3600


def max_drawdown(equity: Sequence[float]) -> float:
    """Largest peak-to-trough drop, as a positive fraction (0.25 = -25%)."""
    peak = -math.inf
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def drawdown_series(equity: Sequence[float]) -> list[float]:
    peak = -math.inf
    out = []
    for value in equity:
        peak = max(peak, value)
        out.append((value - peak) / peak if peak > 0 else 0.0)
    return out


def _returns(equity: Sequence[float]) -> list[float]:
    return [b / a - 1 for a, b in zip(equity, equity[1:]) if a > 0]


def sharpe(equity: Sequence[float], periods_per_year: float) -> float:
    rets = _returns(equity)
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    std = math.sqrt(var)
    return mean / std * math.sqrt(periods_per_year) if std > 0 else 0.0


def sortino(equity: Sequence[float], periods_per_year: float) -> float:
    rets = _returns(equity)
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    downside = math.sqrt(sum(min(r, 0.0) ** 2 for r in rets) / len(rets))
    return mean / downside * math.sqrt(periods_per_year) if downside > 0 else 0.0


def summarize(
    equity: Sequence[float],
    trade_pnls: Sequence[float],
    seconds_per_bar: float,
    bars_in_market: int,
    benchmark_return: float,
) -> dict:
    """All headline numbers in one dict. Fractions, not percentages."""
    if not equity:
        raise ValueError("equity curve is empty")
    start, end = equity[0], equity[-1]
    bars = len(equity)
    periods_per_year = SECONDS_PER_YEAR / seconds_per_bar if seconds_per_bar > 0 else 252
    years = (bars - 1) * seconds_per_bar / SECONDS_PER_YEAR if seconds_per_bar > 0 else 0
    total_return = end / start - 1 if start > 0 else 0.0
    cagr = (end / start) ** (1 / years) - 1 if years > 0 and start > 0 and end > 0 else 0.0

    wins = [p for p in trade_pnls if p > 0]
    losses = [p for p in trade_pnls if p <= 0]
    gross_loss = -sum(losses)
    # None means "undefined" (no losing trades); JSON has no Infinity.
    profit_factor = sum(wins) / gross_loss if gross_loss > 0 else None

    return {
        "start_equity": start,
        "end_equity": end,
        "total_return": total_return,
        "benchmark_return": benchmark_return,
        "excess_return": total_return - benchmark_return,
        "cagr": cagr,
        "sharpe": sharpe(equity, periods_per_year),
        "sortino": sortino(equity, periods_per_year),
        "max_drawdown": max_drawdown(equity),
        "trades": len(trade_pnls),
        "win_rate": len(wins) / len(trade_pnls) if trade_pnls else 0.0,
        "profit_factor": profit_factor,
        "avg_trade": sum(trade_pnls) / len(trade_pnls) if trade_pnls else 0.0,
        "best_trade": max(trade_pnls, default=0.0),
        "worst_trade": min(trade_pnls, default=0.0),
        "exposure": bars_in_market / bars,
    }
