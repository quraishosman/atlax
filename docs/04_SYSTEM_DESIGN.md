# ATLAX System Design

Version: 1.0  
Status: Draft  
Purpose: Define the service-level design and data flow across ATLAX.

---

## System Layers

```text
Market
  ->
Market Data Layer
  ->
Detector Layer
  ->
Strategy Engine
  ->
Confidence Engine
  ->
Alert Engine
  ->
Execution Engine
  ->
Trade Management
  ->
Analytics
```

Each layer has exactly one responsibility.

---

## Service Responsibilities

Market Data Layer:

- Receives candles.
- Receives ticks.
- Receives sessions.
- Receives economic calendar data.
- Receives spreads.
- Never detects setups.

Detector Layer:

- Identifies documented patterns.
- Returns structured detector outputs.
- Never returns `BUY` or `SELL`.

Strategy Engine:

- Consumes detector outputs.
- Produces `Trade Candidate` or `No Trade`.
- Never talks directly to TradingView or MT5.

Confidence Engine:

- Scores quality using documented, configurable inputs.
- Keeps scoring explainable.
- Does not execute trades.

Alert Engine:

- Sends alerts only.
- Never executes trades.

Execution Engine:

- MT5 only.
- Handles risk calculation, lot size, SL, TP, execution, modification, partial close, break even, and trailing stop.

Analytics:

- Journals trades.
- Produces statistics, reports, expectancy, win rate, and risk metrics.

---

## Extensions

Proposed extensions:

- Learning Engine, governed by `docs/09_CONFIDENCE_ENGINE.md`.
- AI advisory analysis, governed by `docs/12_ANALYTICS_ENGINE.md` and `docs/16_SECURITY.md`.
- MCP security gateway, governed by `docs/16_SECURITY.md`.

Extensions do not change core rulebook behavior.
