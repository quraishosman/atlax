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
10. Reconcile later order/deal/position updates through trade-management and journal logging.

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

If MT5 returns an ambiguous or pending state, ATLAX must not guess. The trade-management layer must reconcile later broker state before marking the execution complete.

---

## Forbidden Behavior

MT5 must not:

- Perform complex detection.
- Invent entries.
- Override risk.
- Execute without approval when manual approval is required.
- Execute with invalid configuration.
