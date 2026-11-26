# ATLAX Risk Engine Specification

Version: 1.0  
Status: Draft  
Purpose: Define deterministic risk management boundaries.

---

## Responsibility

The Risk Engine protects the trader from invalid or excessive exposure.

It must be deterministic, configurable, and auditable.

---

## Required Controls

Risk controls must include:

- Risk per trade.
- Maximum daily loss.
- Maximum weekly loss.
- Maximum open trades.
- Maximum trades per day.
- Maximum correlated trades.
- Spread limits.
- News restrictions.
- Profile-specific limits.

## Prop-Firm Loss-Budget Integration

Prop-Firm Mode is an optional input to the Risk Engine. When a validated
`PropFirmRiskContext` is supplied, the engine evaluates the selected,
versioned rule profile before lot-size arithmetic. When no context is supplied,
existing ATLAX risk behavior remains unchanged.

The loss-budget evaluator resolves account values at runtime. It never uses a
fixed funded balance or fixed maximum-loss amount:

```text
overall_floor = source_defined_reference - maximum_loss_amount
remaining_overall_buffer = max(0, current_equity - overall_floor)

remaining_daily_buffer = max(0, current_equity - daily_floor)
  when the selected account has a daily-loss rule

governing_buffer = min(remaining_overall_buffer, remaining_daily_buffer)
  when both limits apply
```

Open ATLAX risk and the configured cost reserve are deducted before the new
trade budget is calculated. The smaller loss-budget allowance caps the existing
broker-metadata-first lot-size calculation. A stale profile, mismatched account
snapshot, missing reference value, invalid rule, or exhausted budget returns a
fail-closed `RiskEvaluation` with rejection code
`PROP_FIRM_LOSS_BUDGET` where applicable.

The budget decision is persisted with the risk evaluation for audit and
includes the profile ID/version, floors, remaining buffers, open risk, cost
reserve, allowed new-trade risk, status, and reason. This phase does not enable
Prop-Firm Mode in the live scanner. When a validated recovery configuration and
persisted recovery state are supplied, the budget is additionally multiplied
by the recovery state: NORMAL uses 1, CAUTION and RECOVERY use configured
values no greater than 1, and PAUSED or RULE_VIOLATION use 0. The maximum-loss
amount is the recovery reference capital; nominal account balance is not used
as a hidden substitute. Missing recovery observations return UNKNOWN and block
new trades. Recovery state transitions and multipliers are persisted in the
decision for audit, but live scanner wiring remains a later approved phase.

The budget context may provide separate transaction-cost and slippage reserves.
They are added to the configured reserve before available budget is calculated
and are retained as separate audit fields. Missing reserves must not be
replaced with estimates by the Risk Engine.

---

## Forbidden Behavior

The Risk Engine must not:

- Use AI to choose risk.
- Guess missing risk parameters.
- Allow execution when configuration is invalid.
- Bypass limits because a setup has high confidence.
- Accept hidden defaults.

---

## Failure Mode

Invalid or missing risk configuration must fail closed.

The system must log:

- Reason.
- Timestamp.
- Context.
- Recovery suggestion.

## Live Account Snapshot Requirements

The scanner supplies the Risk Engine with UTC-bounded realized PnL for the
current day and ISO week, trades opened today, and positions filtered by the
ATLAX magic number. The daily trade count comes from MT5 `history_deals_get`
between the current UTC midnight and the snapshot time. It counts distinct
ATLAX opening deals, deduplicating partial fills by position identifier and
using broker order or deal tickets only when a position identifier is absent.

If MT5 history is unavailable, or a matching ATLAX opening deal cannot be
identified safely, the scanner marks the count unavailable. The Risk Engine
must reject new execution with `DAILY_TRADE_COUNT_UNAVAILABLE`; unavailable is
never treated as zero. Deals that cannot be attributed to ATLAX are excluded.
Missing or malformed values on a matching ATLAX exit deal are logged and make
the affected realized-PnL window unavailable; they must never be skipped or
replaced with fabricated PnL.

Daily realized PnL uses the current UTC day beginning at 00:00 UTC. Weekly
realized PnL uses the current UTC ISO week beginning Monday 00:00 UTC. Each
window ends at the snapshot time. Only matching ATLAX magic-number exit deals
are included. For each exit, net cash PnL is the broker-reported:

```text
profit + swap + commission + fee
```

An available history response with no matching ATLAX exits is a known zero.
Unavailable history, or a malformed matching exit deal, makes the affected PnL
window unavailable. Both the Risk Engine and the pre-entry circuit breaker must
block new execution with `DAILY_PNL_UNAVAILABLE` or `WEEKLY_PNL_UNAVAILABLE`;
unavailable is never treated as zero.

For live sizing, the scanner also supplies broker symbol metadata:

```text
value_per_price_unit = trade_tick_value / trade_tick_size
cash_risk_per_lot = stop_distance_price * value_per_price_unit
raw_lot_size = risk_amount / cash_risk_per_lot
```

Broker volume minimum, maximum, and step override global compatibility values
when available. A live scanner snapshot marks broker metadata as required; if
any tick-economics or volume field is missing or invalid, the Risk Engine must
reject the candidate and wait for a refreshed MT5 symbol snapshot. Offline
tests and deterministic calculations may use the configured global fallback
only when `broker_metadata_required` is explicitly false.

This prevents a global forex pip value or contract-size assumption from being
used for JPY pairs, gold, or any other instrument with different tick
economics. The live cash-risk calculation remains:

```text
value_per_price_unit = trade_tick_value / trade_tick_size
cash_risk_per_lot = stop_distance_price * value_per_price_unit
raw_lot_size = risk_amount / cash_risk_per_lot
```

## Non-Gold Daily-Loss Lot Ladder

The approved non-gold execution policy selects a fixed target volume using
realized ATLAX daily loss as a percentage of current account balance:

- below 1% daily loss: `1.00` lot;
- from 1% daily loss: `0.80` lot;
- from 2% daily loss: `0.50` lot.

The target applies to every strategy profile and only to symbols that do not
match `risk.adaptive_lot_sizing.excluded_symbol_prefixes`; the approved
repository configuration excludes gold with the `XAU` prefix. The broker-
metadata-first risk calculation remains a hard upper bound. If the selected
target exceeds the risk-safe, broker-supported volume, ATLAX rejects the
entry with `ADAPTIVE_LOT_EXCEEDS_RISK`; it never enlarges a trade beyond the
approved risk. An unsupported target volume also fails closed. All thresholds,
target volumes, and exclusions are validated configuration, not code defaults.
