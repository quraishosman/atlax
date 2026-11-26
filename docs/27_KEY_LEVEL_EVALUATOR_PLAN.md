# ATLAX Key-Level Evaluator Implementation Plan

Version: 1.0  
Status: ACTIVE  
Authority: `docs/rulebooks/KEY_LEVEL_EVALUATOR.md` and ADR-011

## Objective

Populate the existing `parent_at_htf_zone` quality field with deterministic,
auditable key-level evidence. The evaluator must enrich CRT quality metadata;
it must not redefine CRT detection, strategy direction, risk, execution, or
take-profit behavior.

## V1 Scope

V1 evaluates a parent candle independently when the parent is closed and caches
the result by `symbol + timeframe + parent_candle_open_time`. The CRT detector
retrieves that result when it evaluates the parent/sweep sequence.

V1 sources:

1. Previous completed D1 high and low.
2. Previous completed W1 high and low.
3. Confirmed H4 swing highs and lows.
4. Confirmed D1 swing highs and lows.

The current, incomplete D1 or W1 candle is never used. A swing is usable only
after its two right-side confirmation candles have closed.

## V1 Matching Formula

The candidate tolerance used by the current V1 implementation is:

```text
Tolerance = max(ParentTimeframeATR * 0.10, pip_size * 5)
```

`ParentTimeframeATR` is calculated from the configured simple true-range mean
ending at the parent close. Later candles cannot alter a cached parent result.

A parent is at a key level when:

```text
KeyLevel >= ParentLow - Tolerance
AND KeyLevel <= ParentHigh + Tolerance
```

The exact tolerance is locked in the approved V1 rulebook. The current runtime
uses the validated configuration values.

## Implementation Phases

### Phase 0: Authority And Configuration (Completed)

- Rulebook and ADR V1 approval recorded on 2026-08-14.
- Add validated `key_level_evaluator` settings.
- Generate a configuration snapshot ID.
- Shadow mode was used during validation; the current runtime configuration is
  enabled under the approved V1 authority.

### Phase 1: Pure Level Providers

- Add previous completed day/week providers.
- Add strict two-left/two-right confirmed swing detection.
- Reject forming candles and future-confirmed swings.
- Return `UNKNOWN` when required source data is unavailable.

### Phase 2: Evaluator Contract

- Return status, boolean result, matched level types, matched prices,
  source timeframes, distances, missing sources, tolerance, and snapshot ID.
- Preserve all matched levels for audit and Signal Passport analytics.
- Keep multi-level matches metadata-only in V1; apply the existing HTF-zone
  confidence weight once.

### Phase 3: Market Data Integration

- Extend MT5 candle retrieval for on-demand D1, W1, H4, and D1 history.
- Cache source candles and level snapshots per symbol and closed timestamp.
- Evaluate the parent independently before CRT detection.
- Pass the result into the existing detector quality metadata contract.

### Phase 4: Shadow Validation

- Compute and log evaluations without changing detector output or confidence.
- Compare `UNKNOWN`, `true`, and `false` rates by symbol and timeframe.
- Collect the live score and counterfactual scores with the key-level factor
  true, false, and omitted. These comparisons are audit-only.
- Compare the live score with the no-factor score at the profile threshold and
  count any threshold outcome changes as potential delivery-suppression
  changes.
- Confirm no current-day/current-week lookahead exists.
- Persist aggregate counters through the runtime-state boundary.
- Review the counters with the admin-only `/key_level_shadow` command.

### Phase 5: Live Metadata And Delivery (Runtime Enabled)

- Metadata injection is enabled in the current runtime configuration.
- The existing confidence engine can score the configured HTF-zone factor.
- Keep Standard delivery behavior unchanged except for the now-visible
  quality context.
- Enforce `Key-Level Only` only when the evaluator returns a complete valid
  positive result.

### Phase 6: Verification And Operations (Automated Verification Complete)

- Add logs for evaluation, cache hits, missing data, and failures.
- Store key-level evidence with the Signal Passport.
- Test local persistence and validate PostgreSQL schema/migration paths.
- PostgreSQL live integration remains an environment operation after the
  database endpoint is available; it is not simulated by unit tests.
- Add operational counts for key-level matches and suppressed deliveries.
- Restart the scanner after rollout so the running process loads the validation
  collector, then review `/key_level_shadow` after real candidates are scored.
- Record the live comparison cohort and delivery review in
  `docs/28_KEY_LEVEL_CONFIDENCE_VALIDATION.md`; keep promotion decisions open
  until comparable no-factor samples are sufficient.

## Deferred Sources

Previous month levels, session highs/lows, equal highs/lows, FVG levels, order
blocks, supply/demand zones, BOS/CHoCH levels, and round numbers require their
own documented providers and tests. They are not silently included in V1.

## Acceptance Criteria

- The same closed input produces the same evaluation and snapshot ID.
- No forming D1/W1 candle or unconfirmed swing can match.
- Missing required history returns `UNKNOWN` and never admits Key-Level Only.
- Standard and Premium ATR routing remain separate from key-level preference.
- Multiple matched levels do not add undocumented confidence points.
- Every accepted or suppressed decision is explainable from stored metadata and
  structured logs.
