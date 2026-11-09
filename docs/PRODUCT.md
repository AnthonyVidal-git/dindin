# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Python standard library only for the engine and HTTP server (no runtime dependencies); vanilla HTML/CSS/JS dashboard with hand-drawn canvas charts. pytest for tests.

## Users

Primary: developers, recruiters and hiring engineers browsing GitHub, who clone the repo and run it locally to judge the author's skill. Secondary: hobbyist traders who want to backtest simple strategies on their own CSV data without risking money.

## Product Purpose

An open-source algorithmic trading bot for learning and research: a backtesting engine plus a live paper-trading loop on a simulated market. Success means a visitor can run one command, see a strategy trade in real time, compare strategies with honest metrics, and read code clean enough to trust.

## Positioning

Zero-dependency, fully transparent engine: every indicator, fill, fee and metric is readable plain Python with tests, and the same strategy code runs in backtest and in live paper mode.

## Operating Context

Run locally (`python -m dindin serve`), viewed in a desktop browser, usually on a developer's machine alongside an editor. Sessions are short, exploratory: pick a strategy, tweak parameters, run, compare.

## Capabilities and Constraints

- Strategies: SMA crossover, RSI mean reversion, MACD, Bollinger breakout.
- Risk: position sizing by % of equity, stop-loss, take-profit, fees and slippage.
- Data: seeded synthetic market (geometric Brownian motion with regimes) or user CSV (OHLCV).
- Metrics: total return, CAGR, Sharpe, Sortino, max drawdown, win rate, profit factor, exposure.
- Paper trading only. Real market data from Binance public endpoints (REST + WebSocket); no orders, no API keys.
- UI must be bilingual (Portuguese and English toggle) and support light and dark themes.

## Brand Commitments

None established. Name in use: "Dindin". No personal names, emails or identifying details anywhere in the shipped project.

## Evidence on Hand

No real performance data, users, or testimonials. All market data shown is synthetic and must be labeled as such. Never claim profitability.

## Product Principles

1. Honest numbers: show losses as clearly as gains; label simulated data.
2. Transparency over magic: every result traceable to readable code.
3. One command to run, nothing to install.
4. Educational, never financial advice.

## Accessibility & Inclusion

WCAG AA contrast in both themes; gains/losses never encoded by color alone (sign and arrow too); keyboard operable controls; respects reduced motion.
