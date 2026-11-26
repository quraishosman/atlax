# ATLAX Confidence Engine Specification

Version: 1.0  
Status: Draft  
Purpose: Define explainable scoring, confidence breakdowns, and future learning boundaries.

---

## Responsibility

The Confidence Engine scores opportunity quality.

It does not detect patterns, create strategy rules, execute trades, or override risk management.

## H1 Momentum Context

ATLAX may attach a closed-candle H1 momentum evaluation to M5 and M15
candidates. The evaluator uses only closed H1 candles whose close time is not
after the child event. One directional candle establishes a bias; consecutive
candles are recorded as a run. An opposing or unknown context is evidence for
quality analysis, not a detector or strategy suppression rule.

The current rollout is shadow-only. Exact aligned, opposing, sideways, and
run-length score adjustments require approval in `docs/decisions/ADR-013.md`.
Until then, the live score remains the static weighted model and the H1 result
is logged for validation.

---

## Configurable Scoring

All scoring weights must be configurable and documented.

Example categories may include:

- CRT.
- Liquidity.
- Market structure.
- FVG.
- HTF bias.
- Session.
- Risk-reward.
- Spread.

Example values are placeholders until approved in `docs/13_CONFIGURATION.md` or a promoted strategy specification.

---

## Required Output

Confidence output must include:

- Final score.
- Breakdown by factor.
- Configuration snapshot ID.
- Data inputs used.
- Missing or `UNKNOWN` factors.
- Explanation.
- Version.

---

## Learning Engine

ATLAX may support an explainable Learning Engine as a future or advisory extension.

The Learning Engine may analyze historical closed trades and setup metadata to produce evidence such as:

- Win rate by pattern group.
- Average R by pattern group.
- Performance by timeframe.
- Performance by session.
- Performance by symbol.
- Performance by market regime, if documented.

Learning must never:

- Define what a CRT is.
- Change detector behavior.
- Invent trading rules.
- Override risk management.
- Execute trades.
- Hide why a score changed.

---

## Adaptive Confidence Boundary

Adaptive confidence is not active trading authority until explicitly approved.

Any adaptive formula must define:

- Static scoring contribution.
- Learning contribution.
- Minimum dataset size.
- Minimum sample size per feature group.
- Validation method.
- Versioning.
- Rollback.
- Failure mode.

If data is insufficient, return `UNKNOWN` or advisory-only output.

---

## Learning Inputs

The Learning Engine may use historical records such as:

- Candidate ID.
- Trade ID.
- Profile.
- Pair.
- Timeframe.
- Session.
- Setup characteristics.
- Detector outputs.
- Confidence score at decision time.
- Confluence state.
- Risk settings used.
- Entry, stop, and target model.
- Outcome.
- PnL.
- R multiple.
- Trade duration.
- Market regime, if documented.

All inputs must be reproducible from stored ATLAX data.

---

## Learning Outputs

Learning output must include:

- Historical statistic or score.
- Sample size.
- Data window.
- Feature breakdown.
- Model version.
- Reliability status.
- Human-readable explanation.
- Data quality warnings.

When data is insufficient, the output must say `UNKNOWN` or clearly mark itself advisory.

---

## Model Requirements

Preferred first model type:

- Simple statistical aggregation.
- Weighted averages.
- Segmented win-rate and expectancy tables.
- Transparent calculations.

Advanced machine-learning models are not allowed for live confidence scoring until separately documented, validated, and approved.

The model must preserve enough information for a trader or developer to understand why a score changed.

---

## Validation Requirements

Before learning output can influence alerts or execution, ATLAX must validate:

- Minimum dataset size.
- Minimum sample size per feature group.
- Backtest on historical data.
- Out-of-sample validation.
- Overfitting checks.
- Drift or regime-change warnings.
- Comparison against static baseline.
- Clear failure mode when data is insufficient.

If validation fails:

- Keep using the last approved confidence model.
- Log the failure.
- Mark learning output unavailable or advisory only.
- Return a recovery suggestion.

---

## Feedback Loop

When a trade closes, ATLAX should record:

- Original detection data.
- Candidate data.
- Profile and timeframe context.
- Confidence breakdown.
- User approval decision.
- Execution details.
- Final outcome.

Retraining or recalculation must be versioned, logged, reproducible, and reversible.

Example retraining intervals such as every 50 trades, daily, or weekly are open questions until approved.

---

## Open Questions

These were answered and approved in the Phase 5 Learning Foundation plan
(2026-09-18). The answers are binding and must not be changed without a
follow-up ADR.

1. **What minimum dataset size is required before learning can influence confidence?**
   A minimum of **500 closed trades** across all profiles is required before
   any weight adjustment suggestion can be made.

2. **What minimum sample size is required per feature group?**
   A minimum of **30 closed trades** per feature group (e.g. "M15 EURUSD
   London"). Groups below this threshold report `UNKNOWN (insufficient data)`.

3. **Should learning start after 100, 500, or 1000 closed trades?**
   Advisory analysis and table population begin at **100 closed trades**.
   Weight adjustment suggestions require the full **500 trade** baseline.

4. **Should retraining happen after a fixed number of trades, daily, weekly, or manually?**
   Aggregations update **weekly** (weekend batch job). Weight changes are
   **manual only** and require explicit Project Lead approval via an ADR.

5. **Are advanced ML models allowed, or only transparent statistical models?**
   **Only transparent statistical models** — win rate, expectancy (R),
   weighted averages, and segmented tables. No black-box ML models for live
   confidence scoring. Full explainability is mandatory.

6. **Is learning advisory only, or can it affect alert thresholds?**
   **Advisory only.** The Learning Engine never automatically modifies the
   config, alert thresholds, or live weights.

7. **Must every learning model pass backtesting before live use?**
   **Yes.** Any proposed weight adjustment must be validated in a
   counterfactual simulation against the static baseline, showing improved
   expectancy before promotion.

8. **How should market regime be defined?**
   Deterministically using Daily (D1) candles, implemented in
   `core/analytics/market_regime.py`. Five regimes:
   - `TRENDING_BULLISH` — close above D1 EMA20 with higher highs (last 4 D1 candles).
   - `TRENDING_BEARISH` — close below D1 EMA20 with lower lows (last 4 D1 candles).
   - `RANGING` — no consistent HH/LL structure relative to EMA20.
   - `HIGH_VOLATILITY` — current D1 ATR > 1.5x 30-day average ATR.
   - `UNKNOWN` — fewer than 20 closed D1 candles available.

9. **Which document owns the final adaptive confidence formula?**
   `docs/09_CONFIDENCE_ENGINE.md` remains the primary authority. Specific
   parameter promotions are approved via individual ADRs (e.g. ADR-014).

10. **How should the system behave when learning conflicts with static confidence?**
    **Static confidence always wins.** Conflicts generate an advisory
    "Anomaly Report" for the Project Lead. Live routing continues to use the
    static model until an ADR explicitly approves a change.
