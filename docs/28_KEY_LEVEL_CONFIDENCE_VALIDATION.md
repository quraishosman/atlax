# Key-Level Confidence Impact Validation

**Document Version:** 1.0  
**Status:** ACTIVE VALIDATION  
**Validation start:** 2026-08-14 01:49 Africa/Dar_es_Salaam  
**Authority:** `docs/27_KEY_LEVEL_EVALUATOR_PLAN.md`, `docs/09_CONFIDENCE_ENGINE.md`

## Purpose

Validate the live `parent_at_htf_zone` confidence contribution without changing
the live alert decision. For each valid candidate, ATLAX records:

- the live score using the current key-level value;
- a counterfactual score with the key-level factor omitted;
- counterfactual scores with the factor forced to `true` and `false`;
- the profile threshold and whether either live path falls below it.

The counterfactual path is audit-only. It does not replace the live score,
route alerts, execute trades, or alter subscriber entitlements.

## Initial Live Evidence

The scanner was restarted with the validation collector at 01:49. It then
recorded valid evaluations across EURUSD, GBPUSD, XAUUSD, and USDJPY on the
configured timeframes.

| Metric | Observed value |
|---|---:|
| Key-level evaluations | 160 |
| Valid evaluations | 160 |
| Unknown evaluations | 0 |
| Zone matches | 153 |
| Non-matches | 7 |
| Confidence samples in ledger | 9 |
| Comparable no-factor samples | 2 |
| Fresh validation events captured after restart | 1 |

The ledger also contains seven confidence samples from before this clean
stdout capture and should not be treated as a separate validation cohort.

### Fresh candidate comparison

| Candidate | Profile | Live | Without key level | Threshold | Result |
|---|---|---:|---:|---:|---|
| XAUUSD M5 BUY | Scalper | 45.45 | 31.82 | 44.00 | Live profile alert admitted; no-factor path would be suppressed |

The forced-factor comparison was `45.45` with `true` and `25.45` with `false`.
This confirms that the live key-level value reached the Confidence Engine and
that the score difference is explainable by the documented factor.

## Delivery Check

For the observed candidate:

- Profile alert suppression: **No**. The live score was above the profile
  threshold.
- Subscriber Telegram delivery: **Suppressed as expected** because the
  affected subscribers have a configured minimum confidence of `60.00`, while
  the live score was `45.45`.
- Automatic execution: **Blocked as expected** by the existing daily-loss
  protection (`DAILY_LOSS_HIT`). This was independent of the key-level factor.
- No unexpected suppression attributable to the key-level evaluator was
  observed.

## Acceptance Status

The wiring and first live impact check pass. The validation remains open until
the comparable no-factor sample is large enough for a stable cohort report.
Continue reviewing:

- `data/runtime_key_level_shadow_stats.json`;
- structured `key_level_confidence_validation` events in the scanner output;
- `alert_suppressed`, `subscriber_delivery_suppressed`, and risk decision logs.

No promotion or threshold change should be based on this initial sample alone.
