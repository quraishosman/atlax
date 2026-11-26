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
- Signal Passport.

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

MT5 broker timestamps are normalized to UTC at the Market Data boundary. The
`is_closed` flag is computed against the normalized current UTC clock, so a
forming candle must never enter a detector input sequence even when the broker
terminal reports server-local epoch values.

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

- `record_id`
- `candidate_id`
- `profile`
- `strategy_name`
- `symbol`
- `timeframe`
- `direction`
- `confidence_score`
- `confidence_breakdown`
- `lot_size`
- `entry_price`
- `sl_price`
- `tp_price`
- `exit_price`
- `pnl`
- `closed_at`
- `exit_reason`
- `tags`

---

## Signal Passport

The Signal Passport is the durable analytics identity for a delivered alert.
Its permanent `signal_id` is the same UUID as `AlertEvent.alert_id` and the
journal `record_id`.

Required lifecycle fields:

- `signal_id`
- `pair`
- `style`
- `timeframe`
- `created_at`
- `status`
- `invalidation_reason`
- `final_outcome`
- `outcome_at`

See `docs/21_SIGNAL_PASSPORT.md` for the lifecycle and public-reporting
contract.
