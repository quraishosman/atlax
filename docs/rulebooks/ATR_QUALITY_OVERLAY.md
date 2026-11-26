# ATR Quality Overlay Rulebook

Version: 1.0  
Status: APPROVED  
Owner: ATLAX Engineering  
Approval: Project lead approved for live premium routing on 2026-08-13  
Purpose: Define an ATR-based quality classification and premium delivery filter without changing core CRT detection.

---

## 1. Authority and Scope

This document defines an optional quality overlay for already detected CRT
events. It does not replace or amend the approved CRT rules.

The existing CRT detector remains authoritative for:

- Whether a CRT pattern is detected.
- Bullish or bearish CRT classification.
- Close-back and invalidation behavior.
- Detector output boundaries.

The overlay may classify a detected event as `A+`, `A`, `B`, `C`, or `UNKNOWN`.
It must never turn a non-detected event into a detected event, and it must
never turn a detected event into a different CRT classification.

Related authorities:

- `docs/rulebooks/CRT_RULEBOOK.md`
- `docs/07_DETECTOR_SPECIFICATION.md`
- `docs/09_CONFIDENCE_ENGINE.md`
- `docs/15_LOGGING.md`
- `docs/21_SIGNAL_PASSPORT.md`
- `docs/22_SUBSCRIPTION_LIFECYCLE.md`
- `docs/25_ATR_QUALITY_OVERLAY_PLAN.md`
- `docs/decisions/ADR-010.md`

Only the project lead may promote this document from `PROPOSED` to
`APPROVED`.

---

## 2. Non-Negotiable Boundaries

The ATR overlay must not:

- Change `CRTDetector` validity rules.
- Add a new `BUY` or `SELL` decision to the detector.
- Change entry, stop-loss, take-profit, lot-size, or execution behavior.
- Override the Risk Engine.
- Suppress the existing standard subscriber signal path unless a separate
  product decision explicitly approves that change.
- Treat a quality grade as a probability claim until outcome evidence supports
  the claim.

The overlay is a downstream classification and routing input. Premium routing
is an entitlement decision, not a new CRT rule.

---

## 3. Inputs

Required inputs for a valid ATR quality result:

- A detected CRT event from the existing detector.
- Parent candle high and low.
- Sweep candle high and low.
- Symbol and timeframe.
- ATR value computed from closed candles.
- ATR period and calculation method.
- Configuration snapshot ID.

If any required input is absent, invalid, or ambiguous, the result is
`UNKNOWN`. The base CRT event remains stored and follows the normal routing
path.

---

## 4. ATR Calculation

The current ATLAX market-data implementation uses a simple arithmetic mean of
True Range values. This is documented here to make the result reproducible.
Wilder's smoothed ATR is the preferred future method for stable volatility
measurements, but changing to it requires a separate approved decision and
backward-comparison report. This overlay does not silently switch methods.

For candle `i`, define True Range as:

```text
TR_i = max(
    High_i - Low_i,
    abs(High_i - Close_(i-1)),
    abs(Low_i - Close_(i-1))
)
```

For a configured period `n`:

```text
ATR_n(t) = (TR_(t-n+1) + TR_(t-n+2) + ... + TR_t) / n
```

Rules:

1. Use only closed candles.
2. `n` comes from the instrument market-data configuration.
3. The current configuration convention is `n = 14` when no instrument
   override is supplied.
4. ATR must be greater than zero.
5. Insufficient candles produce `UNKNOWN`, not zero-quality or a guessed ATR.
6. ATR is measured in the instrument's price units. Ratios are unitless.

---

## 5. Normalized Measurements

Let:

```text
ParentHigh = H_p
ParentLow  = L_p
SweepHigh  = H_s
SweepLow   = L_s
ATR        = ATR_n(t)
```

Parent range:

```text
ParentRange = R_p = H_p - L_p
```

Parent range normalized by ATR:

```text
ParentATRRatio = P = R_p / ATR
```

Bullish sweep depth:

```text
BullishSweepDepth = D_s = max(0, L_p - L_s)
```

Bearish sweep depth:

```text
BearishSweepDepth = D_s = max(0, H_s - H_p)
```

Sweep depth normalized by ATR:

```text
SweepATRRatio = S = D_s / ATR
```

The direction comes from the existing detector classification. The overlay
must not infer direction independently.

Notation mapping is consistent across the documents:

```text
P = ParentATRRatio = output field parentAtrRatio
S = SweepATRRatio  = output field sweepAtrRatio
```

---

## 6. Approved Quality Grades

The thresholds below are the approved values derived from the ATR research
document and are active only through the validated Settings Engine.

The previous independent-condition approach was too strict because it could
classify a strong parent with a medium sweep as `C`, or a medium parent with a
strong sweep as `C`. The overlay therefore uses explicit parent and sweep
bands, followed by a deterministic quality matrix.

### Parent bands

```text
P_HIGH   = P >= 0.80
P_A      = 0.60 <= P < 0.80
P_B      = 0.45 <= P < 0.60
P_WEAK   = P < 0.45
```

### Sweep bands

```text
S_HIGH   = S >= 0.25
S_A      = 0.20 <= S < 0.25
S_B      = 0.15 <= S < 0.20
S_WEAK   = S < 0.15
```

### Grade matrix

| Parent band | S_HIGH | S_A | S_B | S_WEAK |
| --- | --- | --- | --- | --- |
| P_HIGH | A+ | A | A | C |
| P_A | A | A | B | C |
| P_B | A | B | B | C |
| P_WEAK | C | C | C | C |

The matrix is equivalent to these rules:

```text
A+ if P_HIGH AND S_HIGH
A  if (P_HIGH AND (S_A OR S_B))
   OR (S_HIGH AND (P_A OR P_B))
   OR (P_A AND S_A)
B  if (P_A AND S_B)
   OR (P_B AND (S_A OR S_B))
C  if P_WEAK OR S_WEAK
```

The matrix guarantees that every valid `P` and `S` combination has exactly
one grade. It also makes the intended behavior explicit:

- `P = 0.90`, `S = 0.18` becomes `A`.
- `P = 0.55`, `S = 0.30` becomes `A`.
- `P = 0.70`, `S = 0.12` remains `C` because the sweep is clearly weak.

Grades are ordinal quality labels:

```text
A+ > A > B > C
```

`UNKNOWN` is not an ordinal grade. It means the quality result cannot be
computed and must fail the premium route.

The grade is descriptive quality metadata. It is not a statement that the
setup has a particular probability of winning.

### UNKNOWN quality

```text
ATR is missing
OR ATR <= 0
OR required candles are unavailable
OR required measurements are invalid
```

---

## 7. Premium Delivery Policy

The existing standard signal behavior remains the default:

```text
StandardDelivery = CRTDetected
```

Premium delivery uses the ATR overlay as an entitlement filter:

```text
PremiumDelivery = CRTDetected
                 AND ATRQualityStatus = VALID
                 AND Grade >= ConfiguredPremiumMinimumGrade
```

The approved premium default is:

```text
ConfiguredPremiumMinimumGrade = A
```

Therefore, premium subscribers receive `A+` and `A` setups. `B`, `C`, and
`UNKNOWN` setups are not sent through the premium ATR route.

The signal must still be recorded in the Signal Passport regardless of
delivery result.

If the business requirement is that standard subscribers must never receive a
signal that happens to have an `A` grade, that is a separate product and
routing change. It is not part of this overlay.

---

## 8. Optional Advanced Measurements

These measurements are research-only in Version 0.1. They must not filter
signals until separately approved and validated.

### Parent range upper bound

```text
ParentRange <= MaxParentATRMultiple * ATR
```

Proposed research value:

```text
MaxParentATRMultiple = 2.50
```

### Sweep depth upper bound

```text
SweepDepth <= MaxSweepATRMultiple * ATR
```

Proposed research value:

```text
MaxSweepATRMultiple = 1.80
```

### Volatility regime ratio

```text
VolatilityRegimeRatio = ATR_14(t) / ATR_50(t)
```

Proposed research range:

```text
0.70 <= VolatilityRegimeRatio <= 1.50
```

### Expanding volatility indicator

```text
VolatilityExpanding = ATR_14(t) > ATR_14(t-1)
```

These advanced measurements may later become confidence inputs, report fields,
or a separate policy. They must not be silently converted into detector
invalidation rules.

---

## 9. Configuration Contract

The following is the approved configuration shape. Values must be loaded by the
Settings Engine and must not be hardcoded in Python.

```yaml
atr_quality_overlay:
  enabled: false
  shadow_mode: false
  minimum_premium_grade: A
  parent_a_min_atr_ratio: 0.60
  parent_a_plus_min_atr_ratio: 0.80
  sweep_a_min_atr_ratio: 0.20
  sweep_a_plus_min_atr_ratio: 0.25
  missing_data_policy: UNKNOWN
  advanced_regime_filter_enabled: false
  max_parent_atr_ratio: null
  max_sweep_atr_ratio: null
  volatility_regime_min_ratio: null
  volatility_regime_max_ratio: null
  default_plan: standard
  plans:
    standard:
      atr_quality_filter: false
      minimum_grade: null
    premium:
      atr_quality_filter: true
      minimum_grade: A
```

The overlay must remain fail-closed when disabled. Live enforcement requires
the approved configuration and a recorded configuration snapshot.

When `enabled: true` and `shadow_mode: true`, ATLAX evaluates and logs the
quality result and premium entitlement decision but does not enforce the ATR
grade threshold on premium delivery. Expiry and subscription-plan checks still
apply. Live grade filtering requires `shadow_mode: false` and separate
approval.

---

## 10. Output Contract

The overlay should produce a separate immutable result rather than modifying
the detector output in place.

```json
{
  "status": "VALID",
  "grade": "A",
  "atr": "0.00625",
  "atrPeriod": 14,
  "parentRange": "0.00500",
  "parentAtrRatio": "0.8000",
  "sweepDepth": "0.00125",
  "sweepAtrRatio": "0.2000",
  "method": "SIMPLE_TR_MEAN",
  "configurationSnapshotId": "...",
  "explanation": "Parent range is 0.80 ATR and sweep depth is 0.20 ATR."
}
```

The output must not contain an execution instruction, lot size, risk value,
stop-loss price, or take-profit price.

---

## 11. Audit and Logging Requirements

For every evaluated detected event, log:

- Signal or candidate ID.
- Symbol and timeframe.
- ATR period and method.
- ATR value or the reason for `UNKNOWN`.
- Parent ATR ratio.
- Sweep ATR ratio.
- Grade.
- Configuration snapshot ID.
- Subscriber routing decision.
- Suppression reason, if premium delivery is rejected.

Example suppression reason:

```text
atr_quality_below_premium_threshold
```

---

## 12. Mathematical Examples

Example bullish setup:

```text
ParentHigh = 1.10500
ParentLow  = 1.10000
SweepLow   = 1.09875
ATR        = 0.00625
```

```text
ParentRange = 1.10500 - 1.10000 = 0.00500
P = 0.00500 / 0.00625 = 0.80
D_s = 1.10000 - 1.09875 = 0.00125
S = 0.00125 / 0.00625 = 0.20
```

Result:

```text
Grade = A
```

It is not `A+` because the sweep ratio is below `0.25`.

---

## 13. Approval Checklist

Before activation, the project lead must approve:

- ATR calculation method.
- ATR period source and warmup behavior.
- A and A+ thresholds.
- Premium minimum grade.
- Missing-data behavior.
- Whether advanced regime measurements remain advisory.
- Historical validation and sample-size requirements.
- Subscriber plan and pricing entitlement.
