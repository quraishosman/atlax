# ATLAX Execution Engine Specification

Version: 1.0  
Status: Draft  
Purpose: Define MT5 execution responsibilities and limits.

---

## Platform

Execution is MetaTrader 5 only.

TradingView never executes trades.

Official references:

- Python `order_check`: https://www.mql5.com/en/docs/python_metatrader5/mt5ordercheck_py
- Python `order_send`: https://www.mql5.com/en/docs/python_metatrader5/mt5ordersend_py
- MQL5 `OrderSend`: https://www.mql5.com/en/docs/trading/ordersend
- MQL5 trade request structure: https://www.mql5.com/en/docs/constants/structures/mqltraderequest

---

## Responsibilities

The Execution Engine handles:

- Risk calculation handoff.
- Lot size.
- Stop loss.
- Take profit.
- Order execution.
- Trade modification.
- Partial close.
- Break even.
- Trailing stop.
- Trade lifecycle logging.

When `execution.require_alert_eligibility` is enabled, the scanner must also
confirm a successful Telegram alert route before submitting an automatic MT5
entry. Alert suppression, transport failure, and unavailable Telegram routing
are fail-closed execution conditions; log-only output is not an entry
authorization.

With `execution.move_sl_to_breakeven: true`, the Trade Manager protects an
open position after the broker reports positive live profit; TP1 is not the
breakeven trigger. TP1 partial close is separately guarded against an actual
market fill that makes the configured midpoint unfavorable or still losing.
When transaction-cost buffering is enabled, breakeven is entry plus validated
broker costs for BUY or entry minus those costs for SELL. Missing broker cost
data falls back to entry and emits an explicit warning.

Trade-management target policy:

- TP1 is the configured midpoint target and is handled by the Trade Manager
  for partial close when it is favorable relative to the actual filled entry
  and the position is profitable.
- TP2 is the approved opposite-extreme target and must remain attached to the
  broker position as the hard final take-profit target.
- Moving the stop loss to breakeven must preserve TP2. Breakeven protection is
  triggered by positive live broker profit, independently of TP1.

### Profit Protection Phase 2 (MFE and Giveback)

The Trade Manager records maximum favorable excursion (MFE) for each tracked
position using the actual filled entry and the original approved stop:

```text
R = abs(actual_entry_price - original_stop_price)
current_R = favorable_price_distance / R
MFE_R = max(previous_MFE_R, current_R)
giveback_R = MFE_R - current_R
```

While `profit_protection.shadow_mode` is true, ATLAX persists the MFE state and
logs the counterfactual protective action without sending an MT5 modification.
With the approved live configuration (`shadow_mode: false`), ATLAX also
supports a progressive R-based trailing stop:

```text
trailing_lock_R = max(minimum_lock_R, MFE_R - trailing_distance_R)
```

After `MFE_R >= trailing_activation_R`, the trailing lock is eligible whenever
it is supported by the current price and is at least `trailing_step_R` above
the last broker-confirmed lock. The stop can only ratchet in the protective
direction. A failed MT5 modification never advances persisted lock state, so
the same protective level remains retryable on a later management cycle. Every
successful update preserves TP2. Both trailing and giveback protection measure
target proximity only against TP2, the hard final take-profit; TP1 partial
management must not activate protective stop tightening. The existing giveback and closed-candle
momentum rules remain additional protection paths; they do not move the stop
backward. Partial and full close actions are not part of this live path.
Missing or invalid entry/stop state produces `UNKNOWN` telemetry rather than an
inferred risk distance.

### Profit Protection Phase 3 (Closed-Candle Momentum)

The Phase 3 observer reads only closed candles from the configured source
timeframe. For a BUY position, all confirmation candles must close bearish; for
a SELL position, all must close bullish. Neutral, forming, missing, or
insufficient candles do not qualify. A qualifying event also requires the
position to remain above the configured current-profit R floor after the MFE
activation and giveback thresholds are met. In shadow mode it emits
`momentum_exit_would_apply` once per source candle. In the approved live mode,
`TIGHTEN_STOP` submits a protective SL update using the same lock calculation.

The live-capable path supports only `TIGHTEN_STOP`: it submits a protective SL
modification, preserves TP2, and refuses to move an existing stop backward.

### Prop-Firm Protection Boundary

When Prop-Firm Mode is supplied to the execution layer, the existing Trade
Manager remains the sole owner of MT5 position modifications. An optional
`PropFirmProtectionGuard` checks the active profile version and freshness before
each protective action. It permits only risk-reducing breakeven, profit-lock,
closed-candle momentum stop tightening, and broker-confirmed profitable
partial-close actions. It rejects stale or unknown profile data, backward stop
moves, and unconfirmed profitable closes. A hard loss-limit breach blocks new
entries but does not prevent a valid protective stop or profitable reduction of
an already-open position.

Every authorization and block records the profile ID/version, action, ticket,
symbol, recovery state, and remaining buffer. TP2 is preserved on stop-only
updates. The guard is optional and does not change current live behavior until
Prop-Firm Mode is explicitly wired and promoted.
Unsupported live actions such as `PARTIAL_CLOSE` and `FULL_CLOSE` fail closed
and are logged.
Broker stop-distance rejection remains a failed modification and is retried by
the normal management cycle.

---

## Required Inputs

Execution requests must include:

- Approved candidate ID.
- Profile.
- Symbol.
- Timeframe context.
- Entry model.
- Stop-loss model.
- Take-profit model.
- Risk settings snapshot.
- Approval status.

---

## MT5 Request Lifecycle

The Execution Engine must treat MT5 execution as a checked lifecycle, not a single fire-and-forget call.

Required lifecycle:

1. Receive an approved ATLAX execution request.
2. Validate that the request matches the approved candidate, profile, symbol, timeframe context, and risk settings snapshot.
3. Build a broker-compatible `MqlTradeRequest` from approved execution settings only.
4. Run a pre-flight check using the MT5 order-check mechanism before sending the order.
5. Refuse execution if the check result indicates insufficient funds, invalid request fields, invalid volume, invalid stop-loss or take-profit, market closure, disabled symbol, or any other broker/platform rejection.
6. Send the order only after pre-flight validation passes.
7. Inspect the returned MT5 result code after sending.
8. Treat a successful send call as acceptance for processing only, not proof of final execution.
9. Record order ID, deal ID, return code, external return code, broker comment, and final execution status when available.
10. Reconcile later order/deal/position updates through the Operations
    Reconciliation cycle and journal logging.

The platform-specific request fields depend on order type and execution mode. ATLAX must not assume that all brokers require the same field set.

Minimum MT5 request concepts that must be represented or deliberately omitted with reason:

- `action`
- `symbol`
- `volume`
- `type`
- `price`, when required by execution mode
- `sl`
- `tp`
- `deviation`, when applicable
- `type_filling`
- `type_time`, when applicable
- `expiration`, for expiring pending orders
- `magic` or equivalent ATLAX identifier
- `comment` containing a sanitized ATLAX candidate reference
- `position`, when modifying or closing a position

---

## Execution Result Handling

Every execution attempt must persist:

- ATLAX candidate ID.
- Approval record ID.
- Profile.
- Symbol.
- Timeframe context.
- Risk settings snapshot.
- Lot-size calculation inputs and result.
- MT5 request payload after secret redaction.
- Order-check result.
- Order-send result.
- MT5 return code and external return code, when available.
- Broker/platform comment.
- Final execution status.
- Error reason and recovery suggestion, if failed.

If MT5 returns an ambiguous or pending state, ATLAX must not guess. The
Operations Reconciliation cycle must reconcile later broker state before the
execution is treated as complete.

### Execution Attempt Ledger

Every returned `ExecutionRecord` is written to the shared execution ledger
before the approval consumer finalizes its terminal approval status. The
SQLite and PostgreSQL implementations use an `execution_records` table with
`execution_record_id` as the idempotency key and `candidate_id` as the join to
the Signal Passport. The stored request is the already-sanitized
`MT5RequestPayload`; broker credentials and other secrets are never persisted.

The ledger stores the profile, symbol, timeframe, direction, lot size, risk
evaluation ID, pre-flight result, order/deal IDs, broker comment, execution
status, failure/recovery fields, and attempted/completed UTC timestamps. A
duplicate write for the same execution record ID is ignored. This ledger is an
execution audit trail; it does not by itself mark a signal as a win or loss.

After a filled position is discovered, the ledger stores the MT5 position
identifier and keeps its settlement state as `PENDING`. When the position is
no longer open, the scanner reconciles matching exit deals using the ATLAX
magic number and position identifier. Broker-reported profit, swap, commission,
and fee are persisted as `OK`; unavailable or malformed close history is
persisted as `UNKNOWN`. A missing close deal is never treated as zero PnL.

### Execution Quality Journal

In addition to the execution-attempt ledger, ATLAX stores a separate typed
`execution_quality_events` ledger. It records operational facts without
changing Signal Passport status or strategy behavior. The event types are:

- `EXECUTION_RESULT`: sanitized request/result facts, broker return code,
  comment, status, and measured attempt latency.
- `POSITION_LINKED`: actual broker position identifier, fill price, volume,
  spread, and slippage when both requested and actual prices exist.
- `PROTECTION_ACTION`: breakeven, trailing/profit-lock, momentum stop, or partial-close
  action with `APPLIED`, `FAILED`, or `BLOCKED` status.
- `POSITION_REALIZED`: broker-authoritative gross PnL, costs, net PnL, close
  reason, and settlement status.
- `CIRCUIT_BREAKER`: safety transition, reason code, and allow/block result.

Execution latency is the UTC duration from execution attempt to broker result.
Slippage is the absolute price distance between an explicitly requested price
and the discovered fill; market orders without a requested price leave it
unknown. Missing broker facts remain unknown and are never replaced with zero.
Every event is idempotent by event ID, while execution and settlement state
remain independently idempotent by their own ledger keys.

---

## Forbidden Behavior

MT5 must not:

- Perform complex detection.
- Invent entries.
- Override risk.
- Execute without approval when manual approval is required.
- Execute with invalid configuration.

## Submission Safety

An `order_send` exception or `None` response is `AMBIGUOUS`, not a safe retry
condition. ATLAX must reconcile broker positions/orders using the ATLAX magic
number and sanitized comment before any retry is considered.

The requested filling mode is resolved against the broker symbol's execution
capabilities. `RETURN` is not sent for Market Execution symbols when the
broker does not support it; a supported FOK or IOC mode is selected instead.
If the broker rejects the selected FOK/IOC mode with an invalid-fill response,
ATLAX re-runs `order_check` with the safe alternate mode and sends only after
that alternate check passes. If no mode passes preflight, execution fails
closed and the rejected checks are persisted.

## Approval Consumer Lifecycle

Manual approval is a persisted handoff, not an execution call. The scanner's
approval consumer owns the following idempotent state transitions:

1. `PENDING` is created after Strategy and Risk approve an execution request.
2. An authorized admin changes it to `APPROVED`.
3. The consumer atomically claims an execution-ready request as `EXECUTING`.
4. The consumer executes through the Execution Engine exactly once.
5. The result is persisted as `EXECUTED`, `EXECUTION_FAILED`,
   `EXECUTION_PENDING_RECONCILIATION`, or `EXECUTION_AMBIGUOUS`.

If a persisted approval payload cannot be hydrated because it is malformed or
uses an unsupported shape, it is moved to `QUARANTINED` with the original JSON,
candidate ID, reason, and UTC quarantine timestamp preserved for audit. The
consumer skips that row and continues processing valid approvals. ATLAX must
never infer missing entry, stop-loss, or take-profit values from summary
columns.

Requests waiting for a delayed LTF FVG entry carry `execution_ready=false` only
when `fvg.execution_gate_enabled` is explicitly `true`. The approved live
configuration keeps this gate disabled, so an FVG remains confluence evidence
and a valid risk-approved CRT becomes execution-ready immediately. When the
delayed workflow is enabled, the FVG trigger releases the gate; approval may
occur before or after the price trigger. A compare-and-set claim prevents
scanner restarts or duplicate workers from submitting the same candidate twice.

The configured approval expiry applies to both `PENDING` and `APPROVED`
requests. Expired requests become `EXPIRED` and cannot be claimed after a
scanner restart or queue outage.
