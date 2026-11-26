# H1 Momentum Confidence Plan

Status: PHASE 2 ACTIVE - IMPLEMENTATION IN SHADOW MODE  
Version: 1.0  
Date: 2026-08-14

## Purpose

Use completed H1 candles as a deterministic, soft confidence context for M5
and M15 CRT candidates. A single completed directional H1 candle establishes a
small directional bias. A consecutive run of two or more candles is recorded as
stronger evidence. An opposing or sideways context lowers the proposed score
in the validation record, but does not suppress the detector event or the raw
candidate.

This plan does not change the CRT rulebook, detector behavior, nested CRT
routing, risk controls, or automatic execution. It adds an explainable
confidence input and a measured counterfactual score until the project lead
approves the numeric penalties and bonuses.

## Scope

The first relationship is configuration-driven `H1 -> M5` and `H1 -> M15`.
The H1 context is evaluated only for those child timeframes. Other timeframes
remain unchanged until a separate relationship is approved.

## Deterministic Evaluation

Only candles with `is_closed == true` and `close_time <= event_timestamp` are
eligible. The currently forming H1 candle and any candle that closes after the
CRT event are excluded. This prevents look-ahead bias.

For each eligible H1 candle:

```text
direction(candle) =
  BULLISH when close > open
  BEARISH when close < open
  NEUTRAL when close = open
```

The evaluator counts the consecutive directional run ending at the latest
eligible H1 candle. The first candle establishes `BULLISH` or `BEARISH` bias;
two or more consecutive candles are marked as a stronger run in the evidence.
Neutral, missing, or insufficient data produces `UNKNOWN` rather than an
invented bias.

The candidate relationship is:

```text
BUY  + BULLISH => ALIGNED
SELL + BEARISH => ALIGNED
BUY  + BEARISH => OPPOSING
SELL + BULLISH => OPPOSING
SIDEWAYS or UNKNOWN => SIDEWAYS or UNKNOWN
```

Sideways/consolidation classification is configuration-owned. Until its body,
range, ATR, lookback, and overlap thresholds are explicitly approved, the
runtime reports the context as `UNKNOWN` and does not change the live score.

## Confidence Policy

The proposed score is calculated as:

```text
proposed_score = clamp(
  base_score
  + aligned_adjustment
  + run_bonus_per_extra_candle * max(run_length - 1, 0)
  + opposing_adjustment
  + sideways_adjustment
  + unknown_adjustment,
  0,
  100
)
```

The adjustment values are configuration values and are not active in this
phase. No value such as 35, 45, or 50 is silently hardcoded. The validation
record stores `base_score`, `proposed_score`, the adjustment, bias, alignment,
run length, source candle close times, and delivery-threshold comparison.

## Subscriber Delivery

Subscriber delivery is evaluated at the final Telegram boundary:

```text
deliver when subscriber is active
  AND style/timeframe matches
  AND pair filter matches
  AND final_score >= subscriber_min_confidence
```

An explicit subscriber minimum is allowed to receive a lower-confidence event
even when it is below the profile default. A subscriber using `Profile Default`
continues to use the profile threshold. Invalid thresholds fail closed.

The admin/profile threshold remains the admin-channel gate. It must not prevent
the Telegram subscriber route from evaluating an explicit lower threshold.

## Rollout Phases

### Phase 1: Evidence and shadow mode

- Add the closed-candle H1 evaluator.
- Attach its result to candidate confidence metadata.
- Log base and proposed scores.
- Preserve live score and existing execution behavior.
- Count aligned, opposing, sideways, unknown, and threshold outcomes.

### Phase 2: Threshold validation

- Collect real evaluations in the Google Sheets feedback contract and local
  audit logs.
- Keep the H1 evidence compact in the existing Google Sheets `notes` column;
  do not add another column for every intermediate value.
- Persist relationship counts and base/proposed/adjustment totals in the
  operations ledger and expose them through the existing admin validation
  report.
- Compare proposed score against each subscriber's saved threshold.
- Review outcomes by pair, child timeframe, H1 run length, and alignment.
- Approve exact score adjustments and sideways thresholds in an ADR.

### Phase 3: Controlled confidence promotion

- Enable the documented adjustment through configuration.
- Keep detector output, Signal Passport, and automatic execution independent of
  Telegram subscriber delivery.
- Restart and monitor the scanner with rollback to `enabled: false`.

## Acceptance Criteria

- No forming H1 candle contributes to a child score.
- A one-candle H1 run is distinguishable from a two-or-more candle run.
- Opposing candidates are retained and auditable.
- An explicit lower subscriber threshold can receive a score below the profile
  default when all other routing rules pass.
- No premium, key-level, pair, style, or subscription filters are bypassed.
- Live behavior is unchanged while `shadow_mode: true` or `enabled: false`.
- Tests cover bullish, bearish, opposing, neutral, look-ahead, missing data,
  and per-subscriber threshold behavior.
