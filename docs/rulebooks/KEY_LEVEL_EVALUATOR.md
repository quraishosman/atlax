# ATLAX Key-Level Evaluator Rulebook

Version: 1.0  
Status: APPROVED  
Owner: ATLAX Engineering  
Approval: Explicitly approved by the project lead on 2026-08-14

## 1. Scope

This rulebook defines a deterministic quality evaluator for the existing CRT
parent candle. It populates `parent_at_htf_zone` and related quality metadata.
It does not change whether CRT is detected, the CRT direction, strategy entry,
risk, execution, or subscriber plan entitlement.

## 2. Evaluation Timing

The evaluator runs independently for each newly available closed parent candle.
It does not wait for a sweep or a valid CRT. The result is cached by:

```text
symbol + parent timeframe + parent candle open time
```

When the CRT detector evaluates the parent/sweep sequence, it retrieves the
cached result and receives the boolean as external quality metadata.

## 3. Closed-Data Rule

Only fully closed candles may create levels or be used for matching.

- The current incomplete D1 candle is never Previous Day.
- The current incomplete W1 candle is never Previous Week.
- Previous Day and Previous Week use the last completed MT5 D1/W1 bars whose
  close time is not later than the parent candle open time. This strict cutoff
  excludes the parent day/week even when the parent itself is a D1/W1 candle.
- A swing is unavailable until its right-side confirmation candles have
  closed. No future candle may be used relative to the parent evaluation time.

MT5 broker D1/W1 candle boundaries are canonical. Local computer timezone and
local calendar-day reconstruction must not redefine those periods.

## 4. V1 Key-Level Sources

The first implementation includes:

- Previous completed D1 high.
- Previous completed D1 low.
- Previous completed W1 high.
- Previous completed W1 low.
- Confirmed H4 swing highs and lows.
- Confirmed D1 swing highs and lows.

V1 does not include month levels, sessions, equal highs/lows, FVGs, order
blocks, supply/demand, BOS/CHoCH, or round numbers.

## 5. Confirmed Swing Rules

With configurable left and right windows `L` and `R`, the V1 default is
`L = 2` and `R = 2`.

```text
Swing High at i:
High[i] > High[i-L ... i-1]
AND High[i] > High[i+1 ... i+R]

Swing Low at i:
Low[i] < Low[i-L ... i-1]
AND Low[i] < Low[i+1 ... i+R]
```

Strict inequalities mean equal highs/lows do not become swings in V1. Equal
levels are a deferred liquidity-pool provider. Only the configured latest
number of confirmed swings is retained.

## 6. Parent Matching

Let `P_low` and `P_high` be the parent candle boundaries, `K` a key-level
price, and `T` the configured tolerance:

```text
T = max(parent_timeframe_atr * tolerance_atr_multiplier,
        pip_size * minimum_tolerance_pips)
```

`parent_timeframe_atr` is the simple mean of the configured number of true
ranges ending at the closed parent candle. Candles after the parent close are
never included, so a later market update cannot change an earlier evaluation.

The approved V1 values are `tolerance_atr_multiplier = 0.10` and
`minimum_tolerance_pips = 5`. They are enforced only through the validated
configuration and remain subject to the fail-closed behavior below.

```text
matches(K) =
    K >= P_low - T
    AND K <= P_high + T
```

This includes a level inside the parent range, a boundary touch, and a close
approach within the configured buffer. No subjective visual rejection rule is
added in V1.

## 7. Output Contract

The evaluator returns:

- `VALID` with `parent_at_htf_zone=true` when one or more configured levels
  match.
- `VALID` with `parent_at_htf_zone=false` when required source data is complete
  and no configured level matches.
- `UNKNOWN` with `parent_at_htf_zone=null` when the parent, ATR, pip size, or
  required source history is missing or invalid.

Every match includes level type, actual price, source timeframe, distance in
price units, and distance in ATR units. The result also includes missing source
names, tolerance, and configuration snapshot ID.

Multiple matches set the boolean once. They do not add extra confidence points
in V1. Their metadata is retained for later evidence-based decisions.

During shadow mode, ATLAX may calculate comparison scores and persist aggregate
validation counters. Those hypothetical scores must never gate, route, or
execute an alert.

## 8. Delivery Boundary

Standard and Premium routing remain independent of this evaluator:

```text
Standard: valid normal alert + subscriber preferences
Premium: valid normal alert + ATR Premium entitlement + subscriber preferences
```

`Key-Level Only` admits an alert only when the result is complete `VALID` and
`parent_at_htf_zone=true`. `false` and `UNKNOWN` are excluded for that user
preference. When the preference is off, both key-level and open-structure
setups remain eligible.

## 9. Forbidden Behavior

The evaluator must never:

- Create a CRT event.
- Change a CRT classification.
- Infer BUY or SELL.
- Use an unclosed candle.
- Use a future-confirmed swing.
- Add undocumented confidence points.
- Override risk, execution, or Premium ATR rules.
- Guess when data is incomplete.
