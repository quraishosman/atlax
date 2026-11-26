# ATLAX Signal Passport Specification

Version: 1.0  
Status: Active  
Purpose: Define the permanent identity, lifecycle, history, and public performance view for every delivered ATLAX signal.

---

## Responsibility

The Signal Passport belongs to the Analytics layer. It records what happened to
an alert after the Alert Engine delivered it. It does not detect patterns,
choose entries, change confidence, manage risk, or execute trades.

## Permanent Identity

Every delivered signal has one stable `signal_id`. ATLAX uses the existing UUID
`AlertEvent.alert_id` as that identifier. The same value is stored as the
journal `record_id` and remains stable across restarts, Telegram delivery,
outcome tracking, history, and reports.

The Passport never uses a list position, Telegram message ID, timestamp, or
pair/timeframe combination as the primary identity.

## Required Passport Fields

- `signal_id` - permanent ATLAX alert UUID.
- `pair` - configured instrument symbol.
- `style` - profile context used for routing.
- `timeframe` - source timeframe.
- `created_at` - UTC time when the signal was recorded.
- `status` - current lifecycle state.
- `invalidation_reason` - populated when the monitored invalidation level is reached; otherwise empty or `UNKNOWN`.
- `final_outcome` - final monitored result when available.
- `outcome_at` - UTC time when a final result was recorded.

When the ATR quality overlay is evaluated, the same Passport record also stores
the immutable quality evidence:

- `atr_method` and `atr_period`.
- `atr_value`.
- `parent_range` and `parent_atr_ratio`.
- `sweep_depth` and `sweep_atr_ratio`.
- `atr_grade`.
- `atr_configuration_snapshot_id`.
- `atr_evaluation_status` and the human-readable evaluation explanation.

These fields describe the signal-quality evaluation only. They do not change
the detector result, confidence score, risk calculation, or execution request.
Signals created before this schema extension use `NOT_APPLICABLE` for the ATR
evaluation status.

When the deterministic key-level evaluator is available, the same Passport
record also stores the location evidence summary:

- `key_level_status` and `key_level_zone`.
- `key_level_match_count`.
- `key_level_configuration_snapshot_id`.
- Internal matched-level evidence and the evaluation explanation.

The public Passport exposes only the status, location summary, match count, and
configuration snapshot identifier. It does not expose raw matched prices or
the internal evidence blob to subscribers.

The existing journal also retains the internal confidence and price-reference
fields required by the analytics contract. Subscriber views expose only
approved signal information and aggregate outcomes.

## Lifecycle Contract

These are storage and presentation states. They do not define a new trading
rule:

| Status | Meaning | Final outcome |
| --- | --- | --- |
| `OPEN` | Signal is recorded and remains in the existing monitoring flow. | `UNKNOWN` |
| `RESOLVED` | Existing tracker recorded a final monitored target or invalidation result. | `WIN` or `LOSS` |
| `EXPIRED` | Existing monitoring window ended without a final target or invalidation result. | `UNKNOWN` |
| `UNKNOWN` | Legacy or incomplete data cannot be classified safely. | `UNKNOWN` |

Final outcomes are derived only from existing tracker events:

- `tp2_hit` -> `WIN`
- `sl_hit` -> `LOSS`, with `invalidation_reason=stop_loss_hit`
- tracking-window expiry -> `EXPIRED` with `final_outcome=UNKNOWN`

An intermediate midpoint event does not close the Passport. The signal remains
`OPEN` until the existing tracker records a final outcome or expiry.

## Performance Contract

Reports distinguish:

- `total_signals` - all Passport records created in the period.
- `open_signals` - records still open.
- `resolved_signals` - records with `WIN` or `LOSS`.
- `wins` and `losses` - resolved outcomes only.
- `unknown_outcomes` - expired, legacy, or incomplete outcomes.
- `win_rate_pct` - wins divided by resolved signals; unknown data is not treated as a loss.

Reports are available by pair and style, while individual history preserves
timeframe context. A report with no resolved signals must not claim a win rate.

## Subscriber Surface

Active subscribers may use:

- `/history` to view recent stored Passport records.
- `/weekly` to view the last seven-day aggregate report by pair and style.

Subscriber output must not include account identifiers, broker fields, lot
sizes, private PnL, execution controls, credentials, internal source code, or
undocumented strategy implementation details. Missing data is shown as
`UNKNOWN`.

The Telegram Passport presentation shows the pair, trading style, timeframe,
human-readable UTC creation time, lifecycle status, and final outcome. It does
not show the permanent UUID, and a winning record does not show an
invalidation-reason field. Loss reasons are rendered as customer-facing text,
not machine identifiers. History is paginated in groups of ten records, and
the public aggregate is available as both a daily and seven-day report. Pair
and style blocks use readable labels such as `Day Trader` and keep the pair
symbol in its configured uppercase form.

## Storage And Audit

The selected journal backend is the authoritative store. SQLite and PostgreSQL
persist the same Passport, ATR, and key-level fields, and the migration path
preserves those fields when present. Passport lifecycle updates are idempotent by `signal_id`; a repeated outcome event must not
overwrite a final result with a different result.

The system logs Passport creation, final outcome, expiry, query failures, and
Telegram history/report views without logging secrets.

## Acceptance Criteria

- Two signals on the same pair and timeframe have different permanent IDs.
- A restart does not change a signal ID or lose its stored lifecycle state.
- Pair and style reports count only the requested period and preserve open versus resolved counts.
- A stop-loss result records the invalidation reason.
- Expiry remains `UNKNOWN` rather than being counted as a loss.
- `/history` and `/weekly` fail closed with a clear `UNKNOWN` message when the journal is unavailable.
- Existing detector, strategy, confidence, risk, execution, and alert-routing behavior is unchanged.
