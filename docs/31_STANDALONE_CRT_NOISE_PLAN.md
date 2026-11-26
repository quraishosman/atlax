# Standalone CRT Noise Analysis Plan

Version: 0.1  
Status: PROPOSED - shadow analysis only  
Owner: ATLAX Engineering  
Purpose: Measure weak standalone CRT events without changing the approved CRT
detector, standard delivery path, or nested routing policy.

## 1. Boundary

This document addresses a CRT that is not contained by an active controller.
It does not change the approved nested M15 -> M5 relationship in
`docs/30_NESTED_CRT_ROUTING_PLAN.md`.

The current CRT detector already applies the approved parent requirements,
including the minimum parent range and minimum parent body ratio. The ATR
quality overlay classifies detected events and routes premium entitlements, but
it does not remove valid CRTs from the standard subscriber path.

No standalone event may be suppressed by this proposal until the project lead
approves a rulebook amendment or a separate downstream routing policy.

## 2. Current Evidence

The active configuration now uses `max_sweep_retracement_pct: 0.55`. This
aligns the deployed value with the approved `CRT-SWEEP-004` configurable
boundary. It limits how far the second candle may retrace into the parent
range; it does not by itself define a minimum sweep-wick or minimum sweep
depth rule.

The active parent floor remains `0.50 ATR`, and the active minimum parent body
ratio remains `0.25`. Raising either value would be a separate behavior change
that requires an approved threshold and outcome evidence.

The first post-enforcement sample contained:

- 107 journal records in the measured 24-hour window.
- 73 M5 records, 33 M15 records, and 1 H1 record.
- 31 records with complete ATR measurements.
- 0 ATR-measured records below the approved parent range floor of `0.50 ATR`.
- 12 ATR-measured records with sweep depth below `0.25 ATR`.
- 76 older records without ATR metadata; these cannot be graded by this plan.

The last two measurements are evidence for analysis, not an approval to
filter. Missing ATR data is `UNKNOWN`, not proof that a candle is weak.

Until a standalone threshold is approved, the implementation's audit-only
`candidate` count uses the existing valid ATR grade `C`. This is a measurement
label, not a new detector rule and not a delivery decision.

## 3. Proposed Shadow Measurements

For every detected event outside an active nested controller, record:

- Symbol and timeframe.
- Parent ATR ratio `P`.
- Sweep ATR ratio `S`.
- ATR grade and evaluation status.
- Confidence score before any hypothetical standalone filter.
- Key-level status.
- Existing subscriber-delivery result.

The measurement must be audit-only. It must not alter strategy, risk,
execution, Signal Passport creation, or subscriber delivery.

## 4. Candidate Research Rules

These are research candidates only. They are not active rules:

```text
P = ParentRange / ATR
S = SweepDepth / ATR
candidate_weak_sweep = S < proposed_minimum_sweep_atr_ratio
```

The proposed sweep threshold must be selected from observed outcomes and
reviewed by trading style and timeframe. It must not be copied into the CRT
detector as an undocumented rule.

### Decision required for the requested noise reduction

The request to reject a weak second candle needs two separate, measurable
rules before implementation:

1. Parent quality floor: define the minimum `P = ParentRange / ATR` for all
   standard delivery, or define it as a routing policy for selected plans.
2. Sweep strength floor: define whether “small wick” means external sweep
   depth, wick length, wick-to-range ratio, or another approved measurement,
   then define its threshold.

The existing ATR overlay has research-grade measurements for parent ratio and
sweep ratio, but those measurements are not yet an approved standard detector
gate. A daily target such as 15 signals is a monitoring objective, not a
deterministic rule and must not be implemented as a quota.

Possible future policies require separate approval:

1. Premium-only quality gate: use the existing ATR entitlement route.
2. Standard delivery filter: suppress weak standalone events for all
   subscribers.
3. Confidence-only adjustment: retain delivery but expose the measurement in
   scoring and reports.

The second option has the largest product and behavioral impact and must not be
implemented implicitly.

## 5. Required Observability

Add or verify counters for:

- `standalone_noise_evaluated`
- `standalone_noise_unknown`
- `standalone_noise_candidate`
- `standalone_noise_would_suppress`

Required decision evidence must include a stable reason code and must never
include credentials, tokens, or subscriber identifiers.

## 6. Acceptance Criteria Before Promotion

Promotion requires all of the following:

1. At least one complete observation window with ATR data for each active
   timeframe and trading style.
2. Outcome comparison for candidate weak-sweep events versus other events.
3. Delivery-volume comparison showing the expected reduction and no accidental
   suppression of aligned nested M5 events.
4. Regression tests for `UNKNOWN`, boundary values, and each selected policy.
5. An approved rulebook or ADR stating the selected threshold and affected
   subscriber plans.

Until these criteria are met, `standalone_noise_filter` remains absent from
runtime enforcement and no code may invent its threshold.
