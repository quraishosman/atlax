# ATLAX Volume Profile Confluence Implementation Plan

Version: 1.0  
Status: APPROVED - Live confluence metadata active; confidence promotion pending  
Approval date: 2026-08-17  
Approval authority: Project lead  
Governing ADR: `docs/decisions/ADR-017.md`  
Owner: ATLAX Engineering  
Related documents: `docs/03_ARCHITECTURE.md`, `docs/06_DATA_MODELS.md`, `docs/09_CONFIDENCE_ENGINE.md`, `docs/13_CONFIGURATION.md`, `docs/27_KEY_LEVEL_EVALUATOR_PLAN.md`, `docs/rulebooks/KEY_LEVEL_EVALUATOR.md`

## Current Implementation Status

The approved V1 calculator, completed-period engine, key-level bridge, cache,
Signal Passport fields, operational counters, and shadow ledger are wired into
the scanner. The scanner evaluates the previous completed day and week using
the configured instrument-class source timeframe and records the configuration
snapshot used for each result. Telegram context and Premium confluence metadata
are available for diagnostics, but live confidence weighting remains disabled.

The runtime configuration is now `enabled: true`, `shadow_mode: false`,
`confidence_factor_enabled: false`, and
`require_for_premium_confluence: false`. Matched Volume Profile evidence is
therefore live in alert metadata, the Signal Passport, and the explicitly
selected Premium VP-only route. The confidence score, standard delivery,
detector behavior, risk, and execution remain unchanged until a separate
confidence-weight approval is recorded.

## 1. Objective

Add Volume Profile as an independent, explainable confluence source for ATLAX
signals. Volume Profile must enrich market context and key-level evidence; it
must not redefine CRT detection, create direction, bypass confidence or risk,
or place trades.

The initial implementation must preserve current CRT behavior. A valid CRT must
not be suppressed merely because Volume Profile is missing, unavailable, or
still in shadow mode.

## 2. Architectural Boundary

```text
MT5 completed candles
        |
        v
Volume Profile Engine
        |
        +--> POC / VAH / VAL levels
        |
        v
Key-Level Evaluator
        |
        v
Trade-candidate confluence metadata
        |
        +--> shadow confidence comparison
        +--> optional Premium Confluence routing after approval
```

Responsibilities remain separated:

- **Market Data Layer:** obtains closed candles and volume fields.
- **Volume Profile Engine:** calculates deterministic volume-at-price levels.
- **Key-Level Evaluator:** checks whether the CRT parent range is near a
  completed profile level.
- **Confidence Engine:** may score the confluence only after a separate weight
  is approved and configured.
- **Alert Engine:** displays explainable confluence metadata only.
- **Risk and Execution Engines:** remain unchanged.

The CRT detector must continue to use its approved OHLC rules. Volume Profile
must not be called from the CRT detector to decide whether a CRT exists.

## 3. V1 Scope

V1 includes only the following profile levels:

1. Previous completed day POC.
2. Previous completed day VAH.
3. Previous completed day VAL.
4. Previous completed week POC.
5. Previous completed week VAH.
6. Previous completed week VAL.

V1 does not include:

- The current incomplete day or week.
- Developing POC or developing value area.
- Footprint delta, bid/ask imbalance, or order-book data.
- Directional assumptions such as "VAL always means buy".
- HVN/LVN routing thresholds.
- A new CRT rule.
- A mandatory delivery or execution filter.

Source timeframe and bin size are instrument-class configuration, not global
constants. The initial proposal supports at least:

| Instrument class | Proposed source timeframe | Proposed bin-size policy |
| --- | --- | --- |
| Forex non-JPY | M15 or M5, selected by configuration and validation | Class-specific tick size |
| Forex JPY pairs | M15 or M5, selected by configuration and validation | Class-specific tick size |
| Gold/metals | M5, subject to validation | Wider class-specific tick size |

These are configuration recommendations, not approved runtime values. A
symbol-level override must be available for instruments whose price scale or
liquidity differs from its class. No implementation may assume that one bin
size is appropriate for EURUSD, USDJPY, and XAUUSD.

The current period is never included in a profile used for a completed parent
evaluation. This prevents lookahead and keeps the result reproducible.

## 4. Volume Source Contract

MT5 bar data exposes both `tick_volume` and `real_volume` fields when provided
by the terminal. ATLAX must preserve the source identity in every profile.

The source policy is:

```text
real_volume available and approved -> REAL_VOLUME
otherwise approved broker tick volume -> MT5_TICK_VOLUME
neither available or volume is invalid -> UNKNOWN
```

V1 must not silently combine broker volume from different brokers or claim
that MT5 tick volume is consolidated global Forex volume. Spot Forex is an OTC,
fragmented market, so broker volume is a local activity proxy.

Every profile result must include:

- `volume_source`.
- `volume_source_broker` when available.
- `volume_quality`: `REAL`, `BROKER_TICK_PROXY`, or `UNKNOWN`.
- The source timeframe used to build the profile.
- The profile period start and end in UTC.
- The number of source bars and the number of bars with usable volume.

Missing or zero volume must produce `UNKNOWN` when the configured minimum data
contract cannot be satisfied. It must not fall back to a price-only profile.

## 5. Profile Calculation Contract

### 5.1 Input

The engine receives a symbol, a completed profile period, a lower source
timeframe, closed candles, a price tick size, and a validated configuration.
All candles must belong to the same symbol and source timeframe.

The profile period is half-open:

```text
[period_start_utc, period_end_utc)
```

Only candles whose complete open-to-close interval belongs to the period are
eligible. Forming candles and candles outside the period are rejected.

### 5.2 Price Rows

The configured bin width is a positive integer number of symbol ticks:

```text
bin_width = configured_bin_ticks * tick_size
```

For each source candle `i`, let:

```text
L_i = candle.low
H_i = candle.high
V_i = selected volume value
```

The candle touches every profile row whose price interval intersects
`[L_i, H_i]`. If it touches `N_i` rows, the V1 range-uniform allocation is:

```text
allocated_volume(i, row) = V_i / N_i
```

The row volume is:

```text
row_volume(row) = SUM allocated_volume(i, row)
```

This is a deterministic approximation because OHLCV bars do not reveal the
exact intrabar price path. The allocation method must be stored as
`RANGE_UNIFORM` in the profile metadata. A later lower-timeframe or tick-based
method requires its own validation.

### 5.3 Point Of Control

The POC is the row with the greatest `row_volume`:

```text
POC = argmax(row_volume(row))
```

Tie-breaking is deterministic:

1. Choose the row closest to the profile midpoint.
2. If still tied, choose the lower price row.

### 5.4 Value Area

Let `p` be the configured value-area percentage expressed as a fraction and
`T` be total profile volume:

```text
target_volume = T * p
```

Start with the POC row. At each step compare the next eligible row above and
below the current value-area boundaries. Add the side with the larger row
volume until the accumulated volume reaches the target.

Tie-breaking:

1. Choose the side closer to the POC.
2. If equally distant, choose the upper row.

The highest included row is VAH and the lowest included row is VAL. The
algorithm, percentage, bin width, source, and profile period must be persisted
so the result can be reproduced.

## 6. Confluence Matching

The Volume Profile levels become an additional source for the existing
Key-Level Evaluator. V1 reuses the approved key-level tolerance:

```text
Tolerance = max(parent_timeframe_ATR * 0.10, minimum_tolerance_pips * pip_size)
```

A profile level matches the parent when:

```text
level_price >= parent.low - tolerance
AND
level_price <= parent.high + tolerance
```

The result is metadata, not a new detector rule. It must preserve every
matched level rather than reducing multiple matches to a hidden score.

If multiple profile levels match, V1 sets the boolean to `true` once and keeps
the complete matched-level list and `matched_level_count` for explanation and
analytics. Multiple matches do not add extra confidence points or create an
implicit strength grade. Any future multi-match scoring requires a separate
approved rule and configurable weight.

The V1 result contract is:

```json
{
  "status": "VALID",
  "volume_profile_confluence": true,
  "matched_levels": [
    {
      "level_type": "PREVIOUS_DAY_VAL",
      "price": "1.08320",
      "distance": "0.00000",
      "distance_atr": "0.00000"
    }
  ],
  "profile_period": "PREVIOUS_DAY",
  "profile_start_utc": "2026-08-17T00:00:00Z",
  "profile_end_utc": "2026-08-18T00:00:00Z",
  "volume_source": "MT5_TICK_VOLUME",
  "volume_quality": "BROKER_TICK_PROXY",
  "value_area_percent": 0.70,
  "allocation_method": "RANGE_UNIFORM",
  "configuration_snapshot_id": "..."
}
```

The status values are:

- `VALID`: the completed profile was calculated successfully.
- `VALID` with `volume_profile_confluence=false`: the profile was valid but no
  configured level matched.
- `UNKNOWN`: required source data, volume, or configuration was unavailable.

An `UNKNOWN` result must not suppress a CRT in V1.

## 7. Confidence And Routing Policy

### 7.1 Shadow Stage

During shadow mode, the engine must calculate and log:

- The live score without the Volume Profile factor.
- A counterfactual score with the factor marked true when a match exists.
- A counterfactual score with the factor marked false when no match exists.
- The result when the factor is omitted or `UNKNOWN`.
- Any profile, plan, or confidence-threshold outcome that would change.

Shadow calculations must not change Telegram delivery, subscriber routing,
approval, risk, or execution.

### 7.2 Promotion Stage

After validation and explicit approval, a configurable confidence factor may be
added. The factor weight must be documented in the Confidence Engine and loaded
from configuration; it must not be hardcoded.

If the factor is `UNKNOWN`, the existing confidence engine policy for missing
factors applies: exclude it from the denominator and rescale only according to
the approved scoring contract.

### 7.3 Premium Confluence Stage

After promotion, Premium routing may expose a setting such as:

```text
require_volume_profile_confluence = true
```

When enabled, only a complete `VALID` positive Volume Profile result satisfies
that preference. `UNKNOWN` is not treated as a match. Standard routing remains
unchanged unless a separate approved plan enables the preference.

This setting must remain independent from:

- `Key-Level Only`.
- ATR A/A+ quality.
- High-Quality Only.
- HTF momentum.
- Nested CRT suppression.

## 8. Proposed Configuration Schema

The following schema is proposed for the Settings Engine. It must not be added
to live configuration until Phase 0 approval is recorded.

```yaml
volume_profile:
  enabled: false
  shadow_mode: true
  version: "0.1.0"
  volume_source: MT5_TICK_VOLUME
  real_volume_fallback: true
  profile_periods:
    - PREVIOUS_DAY
    - PREVIOUS_WEEK
  source_timeframe_by_instrument_class:
    forex_non_jpy: M15
    forex_jpy: M15
    gold: M5
  source_timeframe_symbol_overrides: {}
  value_area_percent: 0.70
  bin_size_ticks_by_instrument_class:
    forex_non_jpy: 10
    forex_jpy: 10
    gold: 20
  bin_size_ticks_symbol_overrides: {}
  allocation_method: RANGE_UNIFORM
  minimum_source_bars: 50
  minimum_valid_volume_bars: 40
  use_current_period: false
  confidence_factor_enabled: false
  confidence_factor_weight: 0
  require_for_premium_confluence: false
```

Configuration validation must reject:

- Non-positive bin sizes.
- Value-area percentages outside `(0, 1)`.
- Current-period usage for completed-profile matching.
- Unsupported volume sources.
- Invalid or missing source timeframes.
- A class or symbol without a validated source timeframe or bin size.
- A live confidence factor without an approved non-zero documented weight.

## 9. Implementation Phases

### Phase 0 - Governance And Approval

- Approve this document as the Volume Profile authority.
- Decide whether V1 uses broker tick volume, real volume when available, or an
  approved external futures proxy.
- Approve the value-area percentage, source timeframe, bin size, and allocation
  method.
- Decide whether Premium Confluence may use this factor after validation.
- Record the decision in a new ADR before live behavior changes.

### Phase 1 - Data Plumbing

- Extend the MT5 candle feed to retain `tick_volume` and `real_volume` when
  present.
- Preserve the selected source and quality classification.
- Keep OHLC-only CRT fixtures valid and backward-compatible.
- Add tests for missing, zero, and valid volume fields.

### Phase 2 - Pure Profile Engine

- Add a stateless Volume Profile calculator.
- Implement completed-period selection in UTC.
- Implement row construction, range-uniform allocation, POC, VAH, and VAL.
- Add deterministic tie-breaking and reproducibility tests.
- Return `UNKNOWN` for invalid or insufficient input.

### Phase 3 - Key-Level Integration

- Add Volume Profile as a named provider in the Key-Level Evaluator.
- Reuse the approved key-level tolerance.
- Preserve matched price, level type, source, distance, and profile identity.
- Cache results by symbol, instrument class, profile period, source timeframe,
  volume source, bin size, allocation method, value-area percentage, and
  configuration snapshot ID. The cache key must prevent a profile built with a
  previous configuration from being reused after a live reload.
- Add structured logs for calculations, cache hits, matches, and UNKNOWN
  results.

### Phase 4 - Shadow Validation

- Enable calculation with `shadow_mode=true` only.
- Do not change detector output, confidence, Telegram delivery, or execution.
- Record profile matches and counterfactual confidence results in the journal
  and reporting export.
- Compare matched versus unmatched signals by symbol, timeframe, style, plan,
  and profile period.
- Review at least 3-5 trading days and a meaningful resolved-signal cohort.
- Track whether the factor would have changed delivery or Premium routing.

### Phase 5 - Signal Passport And Telegram Context

- Store Volume Profile metadata in the Signal Passport.
- Add concise alert context only after the display wording is approved.
- Keep the technical profile details available for admin diagnostics and replay.
- Do not expose broker-volume limitations as a hidden assumption; label the
  source in admin and analytics views.

### Phase 6 - Confidence Promotion

- Review shadow results and approve a numeric confidence weight, if justified.
- Add the factor to the documented Confidence Engine contract.
- Run score-with/without comparisons and regression tests.
- Keep the factor disabled until the configuration snapshot is approved.

### Phase 7 - Premium Routing Promotion

- Add the Premium-only confluence preference after separate approval.
- Test Standard, Premium, Key-Level Only, High-Quality Only, and combined
  settings independently.
- Confirm UNKNOWN profile data is fail-closed for the preference and does not
  accidentally admit a Premium Confluence-only alert.
- Monitor delivery suppression and subscriber routing after rollout.

## 10. Logging And Metrics

Every evaluation must be explainable through structured events containing:

- Symbol and parent timeframe.
- Parent candle open time.
- Profile period and source timeframe.
- Volume source and quality.
- Number of source bars and valid-volume bars.
- Bin width and value-area percentage.
- POC, VAH, and VAL prices.
- Matched level types and distances.
- Evaluation status and reason codes.
- Configuration snapshot ID.

Operational counters:

- `volume_profile_calculated_total`.
- `volume_profile_unknown_total`.
- `volume_profile_match_total`.
- `volume_profile_no_match_total`.
- `volume_profile_cache_hit_total`.
- `volume_profile_shadow_route_change_total`.
- `volume_profile_suppressed_total` only after Premium routing is promoted.

## 11. Testing Requirements

### Unit Tests

- Correct bin allocation for one candle and multiple candles.
- POC selection and deterministic ties.
- Value-area expansion and deterministic ties.
- Previous-day and previous-week period boundaries.
- Forming-candle rejection.
- Missing and zero-volume UNKNOWN behavior.
- Tick-size and symbol precision handling.
- Reproducibility for identical input and configuration.

### Integration Tests

- MT5 rate fields map to the candle model without changing CRT behavior.
- Key-Level Evaluator returns both legacy and Volume Profile matches.
- Cache keys prevent current-period or configuration leakage.
- Shadow mode produces no delivery or execution changes.
- Premium confluence preference does not affect Standard users.
- UNKNOWN is handled according to the approved routing policy.

### Validation Tests

- Compare signals with and without Volume Profile confluence.
- Compare win rate, resolved rate, expectancy, drawdown, and alert volume.
- Break down outcomes by pair, timeframe, style, plan, profile period, and
  volume source.
- Verify no lookahead from the current day/week.
- Verify replay charts and Signal Passports contain the same profile snapshot
  used during evaluation.

## 12. Acceptance Criteria

The feature is not complete until:

- Volume source and limitations are documented.
- Closed-period profile calculation is deterministic and tested.
- POC, VAH, and VAL are persisted with reproducible metadata.
- CRT detector behavior is unchanged.
- Missing volume returns `UNKNOWN` without suppressing standard CRT detection.
- Shadow mode has collected enough resolved data for review.
- Any confidence weight is explicitly approved and configurable.
- Any Premium routing preference is explicitly approved and independently
  tested.
- Signal Passport, logs, reports, and replay use the same profile snapshot.
- Risk, approval, execution, and MT5 behavior remain unchanged.

## 13. Open Decisions Before Coding

1. Is MT5 broker tick volume acceptable for V1, or must ATLAX use an approved
   CME futures proxy for currency and gold volume?
2. Which source timeframe should build the previous-day and previous-week
   profiles for every configured instrument?
3. What bin size should be approved per instrument class?
4. Should V1 use only POC/VAH/VAL, leaving HVN/LVN for a later proposal?
5. What minimum shadow cohort is sufficient to approve a confidence weight?
6. Should Premium Confluence require any one matched profile level or a specific
   level type?

Until these decisions are approved, Volume Profile remains a proposed,
shadow-only confluence capability and must not alter live delivery or execution.
