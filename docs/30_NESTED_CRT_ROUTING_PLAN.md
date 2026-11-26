# Nested CRT Routing and Noise Reduction Plan

Version: 1.0  
Status: ACTIVE - approved for implementation  
Approved By: Project Lead  
Approved Date: 2026-08-14  
Purpose: Reduce repeated CRT alerts formed inside an active 15-minute CRT without changing the CRT detector.

## Current Evidence

The current detector evaluates each configured timeframe independently. The
scanner's alert deduplication only prevents a repeat for the same symbol and
timeframe at the same latest candle. It does not understand containment or
directional relationships between events.

The current journal sample contains 104 entries in the last 24 hours:

- M5: 71
- M15: 33

This confirms that lower-timeframe routing is the first maintenance target.

## Proposed Behavior

The detector continues to produce raw M5 and M15 events. A routing policy is
evaluated after detection and before strategy, confidence, alert, and execution
processing.

### Controlling M15 CRT

An M15 CRT becomes a controlling event when it is detected and admitted by the
existing event deduplication rules. Its controlling range is the parent candle
range:

```text
controller_low  = controller.parent_candle.low
controller_high = controller.parent_candle.high
controller_range = [controller_low, controller_high]
```

The controller's bias is its detector classification:

```text
bullish_crt -> bullish bias
bearish_crt -> bearish bias
```

No BUY or SELL value is added to the detector output.

### Deterministic Containment

The initial full-wick containment proposal was too strict because a valid child
CRT can wick outside its own parent range. V1 therefore uses a body/close-aware
definition:

1. The child parent candle must be fully contained in the controller range.
2. The child sweep candle's close must be inside or on the controller range.
3. If a confirmation candle exists, its close must be inside or on the
   controller range.
4. Child sweep and confirmation wicks may exceed the controller range.

```text
contained(candidate, controller) =
  candidate.parent.low  >= controller_low
  and candidate.parent.high <= controller_high
  and controller_low <= candidate.sweep.close <= controller_high
  and (
    candidate.confirmation is absent
    or controller_low <= candidate.confirmation.close <= controller_high
  )
```

The comparison uses exact Decimal prices already present in the candle model.
No new pip buffer or tolerance is introduced by this feature. A child parent
that is outside the controller range is not nested, even if its close returns
inside.

The candidate must also be formed after the controller was detected:

```text
candidate.sweep_candle.close_time >= controller.detection_time
```

The implementation uses the controller detection timestamp for this comparison.
This prevents a lower-timeframe event that happened before the M15 CRT was known
from being retroactively classified as nested.

### Routing Rules

| Candidate | Inside active M15 controller | Result |
| --- | --- | --- |
| M15 | Yes | Suppress as `nested_m15_contained` regardless of bias |
| M5 | Yes, same bias as controller | Allow as `nested_m5_aligned_bias` |
| M5 | Yes, opposite bias | Suppress as `nested_m5_opposing_bias` |
| M5 or M15 | No active controller or outside its range | Preserve existing behavior |

Suppressed candidates are short-circuited after detector output and before
strategy and confidence scoring. The raw detector event, routing decision, and
suppression reason remain in the audit log and operations counters. An allowed
M5 candidate proceeds through the existing strategy and confidence pipeline.

## Controller Lifecycle

The controller is stored per symbol and controlling timeframe. V1 keeps one
active controller per configured relationship. It remains active until one of
these exact state transitions occurs:

1. **Mitigated:** for bullish CRT, a subsequent closed candle reaches
   `parent.high`; for bearish CRT, a subsequent closed candle reaches
   `parent.low`. Reason: `nested_controller_mitigated`.
2. **Invalidated:** for bullish CRT, a subsequent closed candle closes below
   `sweep.low`; for bearish CRT, a subsequent closed candle closes above
   `sweep.high`. Reason: `nested_controller_invalidated`.
3. **Replaced:** a later valid M15 CRT is not contained by the current
   controller. The later event becomes the sole active controller, regardless
   of whether its bias is the same or opposite. Reason:
   `nested_controller_replaced_outside`.

There is no fixed candle-count expiry in V1. The approved CRT mitigation and
invalidation rules are the lifecycle authority. A later M15 CRT that is
contained by the current controller is suppressed and does not replace it.

The default overlap rule is **latest admitted non-contained controller wins**.
This avoids ambiguous multiple-controller bias. Older controller state is
closed with `nested_controller_replaced_outside` and retained in the audit log.

The persisted controller record must contain:

- `symbol`
- `controller_timeframe`
- `event_id` derived from symbol, timeframe, and parent open time
- `classification`
- `parent_open_time`
- `sweep_open_time`
- `detection_time`
- `parent_low` and `parent_high`
- `sweep_low` and `sweep_high`
- `status`
- `last_observed_closed_time`
- `expiry_reason` and `expired_at`, when closed

The lifecycle state must be persisted through the existing runtime-state adapter
so a scanner restart does not silently reopen suppressed noise.

## Architectural Placement

The implementation belongs in a routing policy between the Detector Layer and
the Strategy Engine:

```text
Closed candles
  -> CRT Detector (raw event preserved)
  -> Nested CRT Routing Policy
  -> Strategy Engine
  -> Confidence Engine
  -> Alert Engine
```

The detector remains responsible only for identifying CRT patterns. The routing
policy does not alter detector output, confidence weights, risk, execution, or
subscriber preferences.

## Configuration

The policy must be configuration-driven and fail closed when its configuration
is malformed. The proposed configuration surface is:

```yaml
nested_crt_routing:
  enabled: false
  shadow_mode: true
  relationships:
    - controller_timeframe: M15
      child_timeframes: [M5]
      same_timeframe_policy: suppress_inside
      child_policy: allow_aligned_only
      containment_mode: parent_range_and_child_closes
```

The relationship list is intentionally extensible so H1 -> M15 or other
approved relationships can be added without redesigning the routing service.
The initial rollout used shadow mode to count would-be suppressions before
notification behavior changed. The M15 -> M5 relationship is now approved and
enabled after project-lead approval on 2026-08-14. New relationships still
require their own approval and validation.

## Observability

Every routing decision must preserve a structured audit event containing symbol,
controller timeframe, candidate timeframe, controller event time, candidate event
time, classifications, containment result, decision, and a stable reason code.
Prices may be included for debugging; credentials and user identifiers must not
be logged. Allowed M5 candidates carry the routing decision in their candidate
metadata. Suppressed detector events are retained in the routing audit ledger
because they do not have a confidence score or Signal Passport trade record.

Required counters:

- `nested_m15_contained_suppressed`
- `nested_m5_opposing_bias_suppressed`
- `nested_m5_aligned_bias_allowed`
- `nested_outside_controller_allowed`
- `nested_controller_expired`

## Implementation Phases

### Phase 0 - Governance - Completed

- Approved this policy and ADR-012.
- Amend CRT-MTF-002 so raw nested detections remain independent, while alert
  routing may apply this approved containment policy.
- Approved the lifecycle: mitigation, invalidation, or replacement by
  the latest valid non-contained M15 event; no fixed candle expiry.
- Approved the softer containment: child parent full range plus child
  sweep/confirmation closes inside, with wicks allowed outside.
- Approved suppressed events as audit records, not confidence-scored Signal
  Passport trades.

### Phase 1 - Shadow Evaluation

- Add the configuration schema and validation.
- Add a pure containment evaluator with Decimal-only comparisons.
- Persist controller state without suppressing alerts.
- Add counters and structured decision logs.

### Phase 2 - Test Coverage

- Same-timeframe M15 inside range is suppressed.
- Aligned M5 inside range is allowed.
- Opposite M5 inside range is suppressed.
- M5 outside range remains allowed.
- A candidate formed before the controller is not retroactively suppressed.
- A new non-contained same-direction M15 replaces the old controller.
- A new non-contained opposite-direction M15 replaces the old controller.
- Mitigation and stop-breach invalidation close the controller.
- Overlapping active ranges resolve to the latest admitted non-contained M15.
- Controller state survives a restart and expires at the approved lifecycle event.

### Phase 3 - Enforced Rollout

- **Completed 2026-08-14:** Enable enforcement for M15 -> M5 only.
- **Completed 2026-08-14:** Restart one scanner instance and verify singleton
  ownership.
- **Completed 2026-08-14:** Monitor signal volume, routing counters, delivery
  logs, and scanner errors.
- **Rollback:** Set `nested_crt_routing.enabled` to `false`, validate the
  configuration, and restart the scanner.

### Phase 4 - Post-Enforcement Observation

- Keep the M15 -> M5 relationship unchanged during the observation window.
- Compare nested routing counters against the journal's M5 and M15 volumes.
- Confirm that aligned M5 events continue through strategy and confidence.
- Confirm that contained M15 and opposing M5 events are represented in audit
  evidence when they occur.
- Do not add a standalone small-candle threshold under this document. That is a
  separate quality policy and must be approved independently.

## Explicit Non-Goals

- No change to CRT candle definitions.
- No new small-candle threshold in this feature.
- No confidence-score penalty used as a substitute for containment.
- No deletion of raw detector evidence or Signal Passport history.
- No change to execution or risk behavior.
