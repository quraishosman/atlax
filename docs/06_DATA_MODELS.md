# ATLAX Data Models

Version: 1.0  
Status: Draft  
Purpose: Define canonical data entities used across ATLAX.

---

## Core Entities

ATLAX must model:

- Candle.
- Tick.
- Session.
- Spread.
- Economic event.
- Detector result.
- Strategy candidate.
- Confidence breakdown.
- Alert.
- Approval decision.
- Execution request.
- Trade.
- Trade lifecycle event.
- Configuration snapshot.
- Audit log entry.
- Analytics report.

---

## Candle

Required fields:

- `symbol`
- `timeframe`
- `openTime`
- `closeTime`
- `open`
- `high`
- `low`
- `close`
- `volume`, when available
- `source`

---

## Detector Result

Required fields:

- `detector`
- `symbol`
- `timeframe`
- `detected`
- `confidence`, if documented
- `reason`
- `invalidation`
- `timestamp`
- `metadata`

Detector results must not include execution commands.

---

## Trade Journal Record

Required fields:

- `tradeId`
- `candidateId`
- `profile`
- `strategy`
- `symbol`
- `timeframe`
- `direction`
- `entry`
- `stopLoss`
- `takeProfit`
- `riskSettingsSnapshot`
- `configurationSnapshotId`
- `confidenceBreakdown`
- `openTime`
- `closeTime`
- `status`
- `outcome`
- `pnl`
- `rMultiple`

Exact persistence schema is pending implementation design.
