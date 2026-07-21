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
