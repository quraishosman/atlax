# ATLAX Profit Protection and Momentum Exit Plan

Version: 1.0  
Status: Live protective stop tightening enabled; full-close promotion pending  
Date: 2026-09-19  
Owner: ATLAX Engineering

## 1. Problem

ATLAX currently uses the configured TP1 midpoint as the first trigger for
partial close and breakeven handling. A market order can fill in the middle of
the setup, after the original TP1 level, or with spread and commission costs
that still leave the live position negative. In that case, TP1 is not a valid
profit-protection trigger and must never close a losing position.

The broader objective is to protect profitable trades when price loses
momentum instead of requiring every position to reach TP2.

## 2. Scope And Boundaries

This is an Execution/Trade Manager feature only.

- It does not change CRT detection, strategy direction, confidence, alert
  routing, or subscriber signals.
- It applies only to open MT5 positions with the ATLAX magic number.
- Risk approval, broker preflight, stop-distance validation, and execution
  reconciliation remain mandatory.
- The Trade Manager must never move a stop farther from a protected position
  or convert a losing position into a forced profit-protection close.
- All behavior is configurable and logged.

## 3. Phase 1: Positive-Profit Breakeven And TP1 Guard

Phase 1 is approved and implemented with the existing
`execution.move_sl_to_breakeven` setting.

### 3.1 Positive-profit breakeven rule

After a position is registered, the manager checks the broker-reported live
position profit on every scan cycle. When:

```text
position.profit > 0
```

the manager requests a stop-loss move to the actual filled entry price. This
check is independent of TP1. A failed broker modification is retried on a
later cycle and is logged; it is never treated as successful without a broker
success response.

The broker-reported profit check is preferred because it includes the live
position's broker-side PnL context. A price-only fallback is used only when a
test or adapter does not expose `position.profit`.

### 3.2 TP1 safety rule

TP1 partial close is allowed only when all of the following are true:

1. The actual filled entry makes TP1 favorable:
   - BUY: `TP1 > actual_entry_price`
   - SELL: `TP1 < actual_entry_price`
2. The live position profit is positive.
3. The broker price has reached TP1 in the trade direction.

If the actual fill makes TP1 unfavorable, TP1 partial management is marked
inapplicable for that position and the position continues under the approved
stop-loss, TP2, and positive-profit breakeven rules.

## 4. Phase 2: MFE And Trailing Profit Protection

Phase 2 will track maximum favorable excursion (MFE) using normalized risk:

```text
R = abs(actual_entry_price - original_stop_price)
current_R = favorable_price_distance / R
MFE_R = max(previous_MFE_R, current_R)
giveback_R = MFE_R - current_R
```

After `MFE_R` reaches the configured activation threshold, the live trailing
stop uses:

```text
trailing_lock_R = max(minimum_lock_R, MFE_R - trailing_distance_R)
```

ATLAX submits a stop update when the candidate lock is supported by current
price and is at least `trailing_step_R` above the last broker-confirmed lock.
The stop only ratchets in the protective direction, preserves TP2, and remains
retryable when MT5 rejects a modification. The existing giveback profit-lock
and closed-candle momentum rules remain additional tightening paths. Shadow
mode records the proposed trail without sending it; the repository live
configuration enables the trailing stop.

Trailing and giveback protection use TP2, the hard final take-profit target,
as their sole proximity reference. TP1 remains a partial-close target only;
reaching or nearing TP1 must not independently tighten the stop. Protection is
eligible only after the configured `target_proximity_fraction` of the distance
from the actual fill to TP2 has been travelled in the favorable direction.

## 5. Phase 3: Closed-Candle Momentum-Loss Exit

Momentum loss uses only the configured source timeframe's closed candles, not a
single noisy tick. The Phase 3 shadow observer evaluates the latest
`confirmation_bars` candles for the position direction:

- BUY momentum loss requires every confirming candle to close bearish.
- SELL momentum loss requires every confirming candle to close bullish.
- Neutral, missing, forming, or insufficient candles do not qualify.
- The same source candle close is evaluated once per position.

The configuration defines:

- source timeframe;
- number of confirming closed candles;
- maximum allowed MFE giveback;
- minimum current profit floor;
- whether the action is a stop tightening, partial close, or full close.

The shadow event additionally requires MFE to have reached `activation_r` and
the current position to remain above `minimum_current_profit_r`. The
counterfactual lock uses the same protective lock formula as Phase 2 and is
logged as `momentum_exit_would_apply`. In live mode, the approved
`TIGHTEN_STOP` action sends a protective SL update; it does not close or
reverse a position. The repository configuration enables
`momentum_loss_enabled: true` and `momentum_shadow_mode: false`. Partial
reduction and full close require separate approval after their own validation.

## 6. State Required

Each managed position must persist:

- position ticket and symbol;
- direction and actual filled entry;
- original stop and approved TP1/TP2;
- original lot size;
- whether TP1 is favorable for the actual fill;
- whether breakeven has succeeded;
- MFE and protection state added in Phase 2;
- last management action and UTC timestamp.

State must survive scanner restarts and must be scoped to ATLAX's magic number.

## 7. Observability

Required events include:

- `profit_protection_position_registered`;
- `breakeven_positive_profit_detected`;
- `breakeven_move_succeeded`;
- `breakeven_move_failed`;
- `tp1_partial_close_skipped_unfavorable_fill`;
- `tp1_partial_close_skipped_not_profitable`;
- `profit_lock_would_apply` and `momentum_exit_would_apply` when the matching
  protection setting is enabled in shadow mode;
- `profit_lock_applied` and protective live-stop events when the approved live
  action is enabled.

Each event must include ticket, symbol, direction, actual entry, current
profit, and the relevant target or stop level without credentials.

## 8. Verification Plan

Tests must cover:

- positive BUY profit moves SL to entry;
- positive SELL profit moves SL to entry;
- losing positions do not move to breakeven;
- TP1 above a BUY fill can close only while profitable;
- TP1 below or equal to a BUY fill is skipped;
- TP1 below a SELL fill can close only while profitable;
- TP1 above or equal to a SELL fill is skipped;
- failed SL modification is retried and not marked complete;
- persisted `breakeven_moved` state prevents duplicate modifications;
- existing TP2 is preserved when SL moves to breakeven.
- original stop is persisted for normalized-R calculations;
- MFE is monotonic across scan cycles and survives a restart;
- live trailing stop advances with MFE and preserves TP2;
- TP1 proximity does not activate trailing or giveback protection; TP2
  proximity does;
- rejected trailing-stop modifications do not advance persisted lock state and
  are retried;
- `profit_lock_would_apply` is emitted after the configured giveback;
- Phase 2 shadow evaluation sends no MT5 order request.

Phase 2 and Phase 3 live protective stop tightening is enabled only by the
approved repository configuration. Partial-close and full-close actions remain
disabled until separately reviewed and approved.

## 9. Implementation Status

- [x] Phase 1 plan approved.
- [x] Positive live-profit breakeven trigger implemented.
- [x] TP1 unfavorable-fill guard implemented.
- [x] TP1 losing-position guard implemented.
- [x] Breakeven state persistence implemented.
- [x] Focused smoke coverage added.
- [x] Phase 2 MFE/profit-lock implementation.
- [x] Configurable live R-based trailing stop implementation.
- [x] Phase 3 closed-candle momentum-loss observer implemented.
- [x] Phase 3 shadow behavior retained for explicit shadow configurations.
- [x] Protective stop-tightening path implemented and promoted for live use.
- [x] Repository configuration sets `shadow_mode: false` and
  `momentum_shadow_mode: false`.
- [x] Breakeven cost buffer uses available broker costs and tick economics.
- [ ] Partial-close or full-close momentum action separately specified and
  approved.
