# ATLAX Operations Monitoring

Version: 1.0  
Status: Active

## Purpose

ATLAX keeps persisted operational counters for diagnosing signal routing without
changing detector, confidence, risk, execution, or subscription decisions.

## Counters

The key-level shadow ledger records:

- Total parent evaluations.
- `VALID` and `UNKNOWN` evaluation counts.
- Key-level matches and open-structure results.
- Confidence samples and audit-only threshold crossings.

The operations ledger records:

- Alert-engine suppressions and their reasons, including threshold and deduplication gates.
- Subscriber suppressions and their reasons, including `key_level_only`, confidence, and ATR entitlement gates.
- Subscriber messages accepted by Telegram.
- Nested CRT routing decisions, including aligned child events, contained
  same-timeframe suppression, opposing-bias suppression, and controller expiry.

Runtime state is stored through the configured runtime-state adapter. With the
current local backend, the operations counters are written to
`data/runtime_operations_metrics.json`. The PostgreSQL adapter remains subject
to the documented migration and dual-read verification process.

## Inspection

Administrators can use `/key_level_shadow` to review key-level validation and
delivery counters together. Structured logs remain the detailed audit trail:

- `key_level_evaluated`
- `alert_suppressed`
- `subscriber_delivery_suppressed`
- `subscriber_telegram_sent`
- `broadcast_done`
- `nested_crt_controller_started`
- `nested_crt_controller_replaced`
- `nested_crt_controller_expired`
- `nested_crt_routing_decision`
- `nested_crt_suppressed`

The nested routing counter group contains:

- `allowed`
- `suppressed`
- `shadow_suppressed`
- `controller_expired`
- `by_reason`

The stable reason codes currently include `nested_m15_contained`,
`nested_m5_opposing_bias`, and `nested_m5_aligned_bias`. A suppressed nested
event is recorded as routing evidence and is intentionally not inserted into
the confidence-scored Signal Passport trade stream.

Counters are diagnostic only. They do not promote an `UNKNOWN` evaluation, alter
confidence scores, or bypass subscriber routing and risk controls.
