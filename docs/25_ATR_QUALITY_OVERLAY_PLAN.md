# ATR Quality Overlay Implementation Plan

Version: 0.1  
Status: APPROVED - LIVE PREMIUM ROUTING  
Related rulebook: `docs/rulebooks/ATR_QUALITY_OVERLAY.md`  
Related decision: `docs/decisions/ADR-010.md`

## Objective

Add an ATR-based premium signal-quality route while preserving all existing
CRT detection, standard subscriber delivery, strategy, risk, and execution
behavior.

The implementation must classify existing detected CRT events. It must not
create a second CRT detector.

---

## Phase 0: Authority and Product Decision

Status: Completed - approved by project lead on 2026-08-13

1. Review and approve the ATR overlay rulebook.
2. Confirm that premium subscribers receive only `A` and `A+` setups.
3. Confirm that standard subscribers retain the existing signal route.
4. Decide whether an `A` setup may also be sent to standard subscribers as a
   normal signal. The recommended answer is yes, to preserve behavior.
5. Confirm premium plan name, price, payment route, and entitlement lifecycle.
6. Confirm that ATR quality does not control automatic execution in this
   phase.

Deliverable: Approved product and rulebook decisions.

---

## Phase 1: Mathematical and Data Contract

Status: Completed

Document and test the following formulas:

```text
TR_i = max(
    High_i - Low_i,
    abs(High_i - Close_(i-1)),
    abs(Low_i - Close_(i-1))
)
```

```text
ATR_n(t) = sum(TR_i for the last n closed candles) / n
```

```text
ParentRange = ParentHigh - ParentLow
ParentATRRatio = ParentRange / ATR
```

```text
BullishSweepDepth = max(0, ParentLow - SweepLow)
BearishSweepDepth = max(0, SweepHigh - ParentHigh)
SweepATRRatio = SweepDepth / ATR
```

Notation mapping:

```text
P = ParentATRRatio = output field parentAtrRatio
S = SweepATRRatio  = output field sweepAtrRatio
```

Proposed grade bands:

```text
P_HIGH = ParentATRRatio >= 0.80
P_A    = 0.60 <= ParentATRRatio < 0.80
P_B    = 0.45 <= ParentATRRatio < 0.60
P_WEAK = ParentATRRatio < 0.45

S_HIGH = SweepATRRatio >= 0.25
S_A    = 0.20 <= SweepATRRatio < 0.25
S_B    = 0.15 <= SweepATRRatio < 0.20
S_WEAK = SweepATRRatio < 0.15
```

Proposed grade matrix:

```text
             S_HIGH   S_A   S_B   S_WEAK
P_HIGH          A+      A     A      C
P_A              A      A     B      C
P_B              A      B     B      C
P_WEAK           C      C     C      C
```

The grade is ordinal for routing:

```text
A+ > A > B > C
```

`UNKNOWN` is not a grade. It means ATR quality could not be computed and it
does not pass the premium route.

The current ATLAX ATR implementation uses a simple arithmetic mean of True
Range. Wilder's smoothed ATR is a high-priority future decision, but it must
be evaluated separately and must not be introduced during this overlay phase.

Deliverable: deterministic formula contract with fixture values. The evaluator
is implemented in `core/confidence/atr_quality.py`; it is not wired to the
scanner, alert router, subscriber plans, or execution.

---

## Phase 2: Settings Engine Contract

Status: Completed; premium entitlement and routing pending

Add a validated configuration section for the overlay. Every threshold must
be configurable and included in the configuration snapshot.

Required settings:

- Overlay enabled state.
- Premium minimum grade.
- A and A+ parent ATR thresholds.
- A and A+ sweep ATR thresholds.
- ATR method and period source.
- Missing-data policy.
- Parent and sweep band boundaries used by the grade matrix.
- Advanced regime settings, disabled by default.

Invalid configuration must fail closed and preserve the last valid settings.

Deliverable: validated settings model, loader, configuration documentation,
and configuration tests. Implemented in
`core/settings/atr_quality_config.py`; live configuration remains disabled.

---

## Phase 3: Independent ATR Quality Evaluator

Status: Completed; scanner and routing integration pending

Create a stateless, deterministic evaluator under the quality/confidence
boundary.

Responsibilities:

- Consume an existing detected CRT result.
- Calculate normalized parent range and sweep depth.
- Produce grade, explanation, and data-quality status.
- Return `UNKNOWN` when required ATR inputs are missing.
- Preserve the original detector output unchanged.

Forbidden responsibilities:

- Detecting CRT.
- Returning `BUY` or `SELL`.
- Setting entry, stop loss, take profit, lot size, or risk.
- Calling Telegram or MT5.

Deliverable: immutable ATR quality result model and unit tests.

---

## Phase 4: Subscriber Entitlement Model

Status: Completed; routing integration pending

Add a plan-level entitlement rather than a user-ID check.

Proposed behavior:

```text
standard.atr_quality_filter = false
premium.atr_quality_filter  = true
premium.minimum_grade        = A
```

Existing subscribers must receive a backward-compatible default plan. The
entitlement must be stored with the subscription/payment lifecycle so expiry
automatically removes premium ATR access.

Deliverable: plan configuration, subscription persistence, migration/defaults,
and entitlement tests.

Implementation evidence:

- `subscription_plan` is persisted on local and PostgreSQL-backed subscriber
  records.
- Payment intents carry the selected plan through confirmation and activation.
- Existing records without a plan resolve to `standard`; invalid plans resolve
  to `unknown` and do not receive premium ATR access.
- Premium entitlement requires an active, non-expired subscription and the
  configured premium plan filter.
- Entitlement resolution is separate from detector output, Telegram delivery,
  risk, and execution. No live routing behavior changes while the overlay is
  disabled.

---

## Phase 5: Alert Routing Integration

Status: Completed; Signal Passport metadata pending

Evaluate the ATR overlay once per detected signal, before subscriber fan-out.

Routing rules:

```text
StandardDelivery = CRTDetected
```

```text
PremiumDelivery = CRTDetected
                 AND ATRStatus = VALID
                 AND Grade >= PremiumMinimumGrade
```

Requirements:

- Do not run detection once per subscriber.
- Do not mutate the signal for one subscriber only.
- Store the complete signal and ATR result in the Signal Passport.
- Log premium suppression with a deterministic reason.
- Keep admin diagnostics available.
- Keep Telegram formatting separate from quality calculation.

Deliverable: routing integration and delivery tests.

Implementation evidence:

- The scanner evaluates ATR quality once per detected instrument event and
  carries the immutable result into each alert event.
- Standard subscribers remain on the existing CRT delivery route.
- Premium subscribers require an active, non-expired premium entitlement,
  `VALID` ATR quality, and a grade at or above the configured plan minimum.
- `B`, `C`, `UNKNOWN`, missing, expired, and invalid premium cases are
  suppressed with deterministic log reasons.
- The admin alert path and execution/risk path are unchanged. The live
  overlay remains disabled in `config/atlax.yaml` until controlled activation.

---

## Phase 6: Signal Passport and Analytics

Status: Completed; validation and controlled activation pending

Persist ATR quality metadata with the signal record:

- ATR method.
- ATR period.
- ATR value.
- Parent range.
- Parent ATR ratio.
- Sweep depth.
- Sweep ATR ratio.
- Grade.
- Configuration snapshot ID.
- Evaluation status.

Analytics may report performance by grade only after sufficient outcome data
exists. The label `A` must not be described as a guaranteed or statistically
proven win-rate category before validation.

Deliverable: Signal Passport schema update, migration, and report fields.

Implementation evidence:

- SQLite and PostgreSQL journals now persist the ATR method, period, value,
  parent and sweep measurements, grade, configuration snapshot, evaluation
  status, and explanation.
- Existing journal rows migrate safely with `NOT_APPLICABLE` ATR status.
- The alert engine passes the immutable ATR result into Signal Passport
  creation without changing the execution or risk payload.
- Passport history exposes the stored ATR quality metadata, while performance
  reports do not claim grade-specific win rates before sufficient validation
  data exists.

---

## Phase 7: Testing and Validation

Status: Completed; focused validation passed, shadow activation pending

Required tests:

1. Existing CRT fixtures produce the same detector results.
2. ATR formulas match hand-calculated Decimal results.
3. Bullish and bearish sweep depth calculations are symmetric.
4. ATR zero returns `UNKNOWN`.
5. Missing ATR candles return `UNKNOWN`.
6. Standard routing remains unchanged.
7. Premium routing accepts A and A+ only.
8. Premium routing rejects B, C, and UNKNOWN.
9. Matrix edge cases classify without gaps or overlaps.
10. Signal Passport stores suppressed signals.
11. Subscription expiry removes premium routing access.
12. Configuration changes are validated and audited.
13. No ATR overlay output can reach MT5 execution as a risk override.

Validation should begin in shadow mode, where grades are logged but premium
filtering is not enforced. Compare grade distributions and outcomes before
enabling live premium filtering.

Deliverable: test report and approval evidence.

---

## Phase 8: Controlled Activation

Status: Live premium enforcement active

Activation order:

1. Deploy the evaluator disabled or in shadow mode.
2. Verify logs and Signal Passport records.
3. Verify standard subscriber delivery is unchanged.
4. Verify premium entitlements and expiry behavior.
5. Enable premium A/A+ filtering through validated configuration.
6. Monitor suppression counts, delivery failures, and grade distribution.
7. Record the release and configuration snapshot.

Advanced maximum-range and volatility-regime filters remain disabled until
their own evidence and approval are complete.

Implementation evidence:

- `ATRQualityConfig` exposes `OFF`, `SHADOW`, and `ENFORCE` operational modes.
- Shadow mode logs grades and premium routing decisions without enforcing the
  ATR grade threshold.
- Subscription activity, expiry, and plan entitlement checks remain active in
  shadow mode.
- Production configuration is currently `enabled: true` and
  `shadow_mode: false`; premium grade filtering is active.

---

## Completion Criteria

This feature is complete only when:

- The rulebook is approved.
- The implementation plan is reflected in code and tests.
- Existing CRT behavior is unchanged.
- Standard subscriber behavior is unchanged.
- Premium routing is plan-based and expiry-aware.
- ATR calculations are reproducible and logged.
- Missing data fails closed for premium delivery.
- Signal Passport records both delivered and suppressed events.
- No ATR rule changes automatic execution without separate approval.
