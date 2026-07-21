# ATLAX Onboarding

## Developer & AI Onboarding Guide

Version: 1.0  
Status: Active  
Project Type: Professional Trading Intelligence Platform  
Primary Repository: ATLAX  
Owner: Project Lead

---

## Mission

ATLAX is **not a trading strategy**.

ATLAX is a modular market intelligence platform capable of detecting, validating, scoring, and executing trading opportunities using deterministic rules.

The first supported strategy is:

- Candle Range Theory (CRT)

Future strategies should be able to plug into the platform without requiring architectural changes.

---

## Project Philosophy

ATLAX is built around one simple principle:

> Every trading decision must be explainable by code.

If a human cannot explain why a trade exists using objective rules, the software cannot execute it.

- No assumptions.
- No intuition.
- No guessing.
- No hidden logic.

Everything must be deterministic.

---

## Critical Rule

This repository is **not** allowed to invent trading rules.

Never.

If documentation is missing, the correct behavior is:

1. Stop.
2. Document the ambiguity.
3. Request clarification.

Never fill the gaps.

---

## Single Source Of Truth

Only the engineering specification defines system behavior.

The highest-priority behavior sources are:

- `docs/rulebooks/CRT_RULEBOOK.md`
- `docs/03_ARCHITECTURE.md`
- `docs/05_API_SPECIFICATION.md`
- `docs/13_CONFIGURATION.md`

Supporting engine specifications in `docs/00_*.md` through `docs/17_*.md` define module responsibilities, interfaces, safety rules, testing, logging, security, and contribution requirements.

If implementation differs from documentation, documentation wins.

---

## Development Priorities

1. Correctness
2. Reliability
3. Maintainability
4. Performance

Never sacrifice correctness for speed.

---

## Project Goals

The platform should:

- Detect opportunities.
- Evaluate quality.
- Score opportunities.
- Alert the trader.
- Execute trades, optionally.
- Manage trades.
- Log everything.
- Generate analytics.

Everything else is secondary.

---

## Platform Architecture

ATLAX consists of independent services.

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

## Layer Responsibilities

### Market Data Layer

Responsible for:

- Receiving candles.
- Receiving ticks.
- Receiving sessions.
- Receiving economic calendar data.
- Receiving spreads.

Nothing else.

Never detect trading setups.

### Detector Layer

Responsible only for identifying patterns.

Every detector is independent.

Examples:

- CRT Detector
- Liquidity Detector
- Market Structure Detector
- FVG Detector
- Session Detector
- Trend Detector
- News Detector

No detector should know another detector exists.

Each detector returns structured data.

Example:

```text
Detected
Confidence
Reason
Invalidation
Timestamp
Metadata
```

Never return `BUY` or `SELL`.

### Strategy Engine

Consumes detector outputs.

Produces:

- Trade Candidate
- No Trade

Never talks directly to TradingView.

Never talks directly to MT5.

### Confidence Engine

Responsible for quality scoring.

Example:

```text
CRT         20
Liquidity   20
MSS         15
FVG         10
HTF Bias    20
Session     10
RR           5
Maximum    100
```

Scoring must remain configurable.

Never hardcode.

### Alert Engine

Produces alerts only.

Examples:

- TradingView
- Telegram
- Discord
- Webhook
- Email
- Push Notification

Never execute trades.

### Execution Engine

MT5 only.

Responsible for:

- Risk Calculation
- Lot Size
- SL
- TP
- Execution
- Trade Modification
- Partial Close
- Break Even
- Trailing Stop

Nothing else.

### Analytics Layer

Responsible for:

- Trade Journal
- Statistics
- Performance
- Screenshots
- Monthly Reports
- Expectancy
- Win Rate
- Risk Metrics

---

## Supported Platforms

### TradingView

Purpose:

- Detection
- Visualization
- Alerts
- Scoring

Never execute trades.

### MetaTrader 5

Purpose:

- Execution
- Risk
- Trade Management
- Journal

Never perform complex detection.

---

## Design Principles

Use:

- SOLID
- Clean Architecture
- Clean Code
- Composition over inheritance
- Dependency Injection
- Low Coupling
- High Cohesion

Avoid:

- God Objects
- Massive files
- Global state
- Magic numbers
- Circular dependencies

---

## Coding Standards

- Functions should be short.
- Classes should have one responsibility.
- Variable names should describe intent.
- Avoid abbreviations.
- Comment why, not what.

---

## Configuration

Everything configurable:

- Risk
- Sessions
- Spread
- RR
- Timeframes
- Symbols
- Thresholds
- News Filter

Never hardcode values.

---

## Error Handling

Never silently ignore errors.

Every failure must include:

- Reason
- Timestamp
- Context
- Recovery suggestion

---

## Logging

Everything important gets logged.

Examples:

- Detector Results
- Strategy Decisions
- Alerts
- Executions
- Trade Updates
- Configuration Changes
- Errors

---

## Documentation Standard

Every module requires:

- Purpose
- Inputs
- Outputs
- Dependencies
- Example
- Limitations

---

## AI Development Rules

If you are an AI agent working on ATLAX:

- Never invent trading rules.
- Never change strategy behavior.
- Never modify CRT logic unless explicitly instructed by the documented rulebook.
- Never assume missing parameters.
- Always ask when documentation is incomplete.
- When uncertain, return `UNKNOWN` instead of guessing.

---

## Rulebook Policy

Trading logic belongs only inside:

- `docs/rulebooks/CRT_RULEBOOK.md`

Implementation must consume rules.

Implementation must never define rules.

---

## Current Project Scope

Version 1 supports:

- CRT

Future versions may support:

- Multiple strategies
- Strategy Marketplace
- Portfolio Management
- Cloud Sync
- AI Analytics
- Machine Learning Research

These future features must not influence current architecture.

Design for extension, not implementation.

---

## Repository Structure

```text
ATLAX/

docs/
    00_PROJECT_VISION.md
    01_ONBOARDING.md
    02_ENGINEERING_STANDARDS.md
    03_ARCHITECTURE.md
    04_SYSTEM_DESIGN.md
    05_API_SPECIFICATION.md
    06_DATA_MODELS.md
    07_DETECTOR_SPECIFICATION.md
    08_STRATEGY_ENGINE.md
    09_CONFIDENCE_ENGINE.md
    10_RISK_ENGINE.md
    11_EXECUTION_ENGINE.md
    12_ANALYTICS_ENGINE.md
    13_CONFIGURATION.md
    14_TESTING.md
    15_LOGGING.md
    16_SECURITY.md
    17_CONTRIBUTING.md
    glossary.md

    rulebooks/
        CRT_RULEBOOK.md

    decisions/
        ADR-001.md
        ADR-002.md
        ADR-003.md

core/
    detectors/
    strategy/
    confidence/
    alerts/
    execution/
    analytics/
    risk/
    sessions/
    logging/

tradingview/

mt5/

tests/

config/

scripts/
```

---

## Workflow

1. Read `docs/00_PROJECT_VISION.md`.
2. Read `docs/01_ONBOARDING.md`.
3. Read `docs/02_ENGINEERING_STANDARDS.md`.
4. Read `docs/03_ARCHITECTURE.md`.
5. Read the relevant engine specification.
6. Read `docs/rulebooks/CRT_RULEBOOK.md` before any CRT work.
7. Review applicable ADRs in `docs/decisions/`.
8. Design.
9. Review.
10. Implement only documented behavior.
11. Test.
12. Document.

---

## Pull Request Rules

Every PR must answer:

- What changed?
- Why?
- Which rulebook section is affected?
- Any architectural changes?
- Any breaking changes?
- Tests included?
- Documentation updated?

---

## Non-Negotiable Rules

- Never invent trading rules.
- Never hardcode strategy decisions.
- Never mix execution with detection.
- Never bypass risk management.
- Never skip logging.
- Never ignore documentation.
- Never optimize before correctness.
- Never make assumptions.

When documentation and code disagree, documentation wins.

When documentation is incomplete, stop and request clarification.

---

## Definition Of Done

A feature is complete only when:

- It matches documentation.
- It passes tests.
- It has documentation.
- It has logging.
- It has configuration.
- It has no hardcoded values.
- It has no architectural violations.
- It produces deterministic behavior.

Otherwise, it is not complete.

---

## Final Principle

ATLAX is intended to behave like a professional institutional trading platform.

Every decision must be:

- Deterministic.
- Auditable.
- Explainable.
- Repeatable.
- Maintainable.

If a feature cannot satisfy those five properties, it does not belong in ATLAX.
