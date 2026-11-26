# ATLAX Webhook Shadow Acceptance Report

Date: 2026-08-15  
Status: Shadow acceptance complete; live downstream influence not approved  
Authority: `docs/35_WEBHOOK_DOWNSTREAM_CONTRACT_PROPOSAL.md` and `docs/decisions/ADR-014.md`

## Scope

This acceptance run validates the optional TradingView webhook boundary from
checked-in fixtures only. TradingView is currently dormant; the active source
is the local MT5 market-data feed. This report does not authorize Strategy,
Confidence, Risk, Alert, or Execution behavior for webhook events.

## Replay Matrix

| Case | Expected result | Evidence |
|---|---|---|
| Complete detected detector event | `OK`, immutable result | `shadow_replay.json` fixture and validator test |
| Complete `detected=false` event | `OK`, retained as no-detection evidence | Fixture and validator test |
| Incomplete detector metadata | `UNKNOWN`, queue acknowledged after ledger write | Fixture, consumer, and persistence test |
| Retired `M1` timeframe | `UNKNOWN`, no downstream influence | Fixture, timeframe-policy validator, and persistence test |
| Detector-provided `BUY` direction | `UNKNOWN`, strategy decision rejected | Fixture and validator test |
| Malformed normalized envelope | `UNKNOWN` at validator boundary | Fixture and validator test; intake schema rejects malformed HTTP input earlier |
| Duplicate payload | One canonical queue item | Fixture-derived idempotency test |
| Handler/storage failure | Retry, then `DEAD_LETTER` according to policy | Existing durable queue failure test |

## Result

The focused queue and shadow-consumer suite passed **14 tests**. The complete
API unit suite passed **33 tests** after the replay fixtures were added.

The consumer persists a processing result before acknowledging a queue item.
Operational exceptions still use retry/dead-letter handling. No test invokes
Telegram delivery, Strategy evaluation, confidence scoring, risk approval,
MT5 execution, or subscriber routing.

## Remaining Promotion Gate

AUD-013 remains `IN_PROGRESS` until the project lead separately approves all
of the following:

1. monitored shadow runs against real producer events;
2. comparison of producer metadata with approved detector fixtures over an
   agreed observation period;
3. review of `UNKNOWN`, duplicate, retry, and dead-letter rates; and
4. a typed Strategy handoff that is explicitly approved for live influence.
