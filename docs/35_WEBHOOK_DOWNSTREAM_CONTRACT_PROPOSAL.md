# ATLAX Webhook Downstream Contract Proposal

Version: 0.2  
Status: APPROVED - shadow mode only (2026-08-15)  
Authority requested: `docs/05_API_SPECIFICATION.md`, `docs/03_ARCHITECTURE.md`,
`docs/07_DETECTOR_SPECIFICATION.md`, and `docs/rulebooks/CRT_RULEBOOK.md`

Deployment note: TradingView is currently dormant because the active ATLAX
deployment uses the local MT5 market-data feed. This optional integration must
not be started or treated as a production dependency without a later release
decision.

## Purpose

Define the missing contract between the durable TradingView webhook queue and
the ATLAX Detector/Strategy boundary. This proposal is governance-only until
approved. It does not authorize TradingView events to create candidates,
change confidence, pass risk, send alerts, or execute orders.

## Current Boundary

The current runtime safely performs these steps:

```text
TradingView request
  -> authenticated schema validation
  -> normalized ATLAX envelope
  -> durable idempotent queue
```

The queue is intentionally not connected to Strategy or Execution. A queue
item must remain `RECEIVED` or move through retry/dead-letter handling until an
approved downstream handler exists. A no-op handler must not mark an event
`PROCESSED`, because that would acknowledge and lose a market event.

## Proposed Processing Contract

After approval, one worker may process a claimed queue item through this
deterministic boundary:

```text
queue item
  -> validate source and schema version
  -> validate closed-candle and timeframe policy
  -> validate detector payload against the approved detector contract
  -> create an immutable detector event record
  -> pass only the typed detector output to Strategy
  -> acknowledge queue item only after the handler succeeds
```

The handler must not call the broker, Alert Engine, Risk Engine, or Execution
Engine directly. Strategy, Confidence, Risk, Alert, and Execution remain
separate stages with their existing approval gates.

## Required Input Authority

The accepted `payload.metadata` must either:

1. contain the complete typed output required by the approved CRT Detector
   contract; or
2. contain enough raw candle data for ATLAX to run the approved detector
   locally using the same documented rulebook and configuration snapshot.

The handler must reject the event as `UNKNOWN` when neither form is complete.
It must never infer missing OHLC values, direction, parent/sweep roles,
confidence, or invalidation levels.

The event must identify:

- symbol and active timeframe;
- closed-candle event timestamp and timezone;
- detector name and detector contract version;
- TradingView script and settings versions;
- source correlation ID;
- rulebook/configuration snapshot used by the producer, when applicable;
- all detector evidence required by the approved rulebook contract.

The raw detector output must not contain a strategy `BUY` or `SELL` decision
unless the governing strategy specification explicitly authorizes that field.
Direction remains a Strategy output under the current architecture.

## Failure And Replay Rules

- Invalid or incomplete payload: record a structured `UNKNOWN` reason and do
  not create a candidate.
- Retired timeframe: reject and do not process downstream.
- Duplicate fingerprint: process only the canonical queue item once.
- Handler exception: call `mark_failed`; retry using queue policy, then move to
  `DEAD_LETTER`.
- Ambiguous detector result: preserve the event for audit and return `UNKNOWN`.
- Successful processing: mark `PROCESSED` only after the detector event and
  every approved downstream handoff has been persisted.

## Approved Contract Decisions

The project lead approved the following contract boundary for shadow mode on
2026-08-15:

1. TradingView is an external detector-event producer for shadow comparison;
   it is not an authority that can change live ATLAX behavior.
2. Shadow mode accepts complete detector-result metadata only. It does not run
   a local detector from incomplete raw candles.
3. Detector metadata uses contract version `1.0` and must include
   `scriptVersion`, `settingsVersion`, `rulebookVersion`,
   `configurationVersion`, `confidence`, `reason`, `invalidation`, and a
   non-empty `evidence` object.
4. The event preserves the producer provenance and the supplied rulebook and
   configuration snapshot strings in the immutable result.
5. A complete `detected=false` event is retained as an auditable `OK` result;
   an incomplete event is retained as `UNKNOWN`.

The project lead approved the contract for shadow-mode implementation on
2026-08-15. ADR-014 records the approval boundary. The runtime remains
unwired to Strategy, Confidence, Risk, Alert, and Execution until the shadow
replay evidence is accepted.

## Implementation After Approval

1. Add a typed adapter for the approved payload form. **Completed in shadow
   mode.** The adapter requires detector provenance, the generic detector
   result fields, and non-empty evidence; it never accepts a strategy
   direction from the detector payload.
2. Add a queue worker that claims, validates, persists, and acknowledges with
   retry/dead-letter behavior. **Completed as an explicit one-shot consumer**
   in `run_webhook_shadow_consumer.py`. Processing results are immutable and
   stored beside the durable queue.
3. Add replay fixtures for valid, duplicate, malformed, retired-timeframe,
   incomplete, and handler-failure events. **Completed for shadow acceptance.**
   The checked-in matrix is recorded in
   `tests/fixtures/webhooks/shadow_replay.json` and summarized in
   `docs/36_WEBHOOK_SHADOW_ACCEPTANCE_REPORT.md`.
4. Run shadow mode with no alert or execution side effects. **Completed for
   the local replay suite.**
5. Compare detector outputs against approved fixtures and obtain operational
   signoff before enabling downstream influence. **Pending.**
