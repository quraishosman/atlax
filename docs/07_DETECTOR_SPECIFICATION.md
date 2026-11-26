# ATLAX Detector Specification

Version: 1.0  
Status: Active  
Purpose: Define detector responsibilities and boundaries.

---

## Detector Rule

Detectors identify patterns.

Detectors do not make trade decisions.

Detectors must never return:

- `BUY`
- `SELL`
- `EXECUTE`
- Lot size.
- Risk percentage.
- Stop-loss decision unless defined as pattern metadata.
- Take-profit decision.

---

## Independence

Every detector is independent.

Examples:

- CRT Detector.
- Liquidity Detector.
- Market Structure Detector.
- FVG Detector.
- Session Detector.
- Trend Detector.
- News Detector.

No detector should know another detector exists.

The Strategy Engine is the integration point.

---

## Required Output

Each detector returns structured data:

```text
Detected
Confidence
Reason
Invalidation
Timestamp
Metadata
```

Fields that are not documented must return `UNKNOWN`.

---

## CRT Detector

The CRT detector cannot be implemented until `docs/rulebooks/CRT_RULEBOOK.md` defines:

- Parent candle rules.
- CRT candle rules.
- Bullish and bearish conditions.
- Invalidation.
- Quality metadata.
- Edge cases.

Until then, CRT behavior is `UNKNOWN`.

### CRT Confirmation Modes

The approved rulebook defines the three-candle CRT structure as the primary
model and permits the confirmation candle to be controlled by the validated
`crt.require_confirmation_candle` setting. The runtime modes are:

| Setting | Required sequence | Detection close |
| --- | --- | --- |
| `true` | Parent, sweep, confirmation | Confirmation candle (`N+2`) |
| `false` | Parent, sweep | Sweep candle (`N+1`) |

The active local scanner configuration currently uses `false`. This is an
explicit configured detector mode, not an undocumented strategy rule. The
gold-standard three-candle fixtures and the active two-candle configuration
both have dedicated tests. Changing this setting changes detector timing and
completeness, so it requires configuration review and a release verification;
it must not be inferred by the Strategy, Confidence, Alert, or Execution
layers.
