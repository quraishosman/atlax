# ATLAX Safety and Operations Center Plan

**Version:** 1.0  
**Status:** Phase 5 implemented - Phase 6 ready  
**Owner:** ATLAX Engineering  
**Related specifications:** `docs/10_RISK_ENGINE.md`, `docs/11_EXECUTION_ENGINE.md`, `docs/13_CONFIGURATION.md`, `docs/15_LOGGING.md`, `docs/16_SECURITY.md`, `docs/42_PROP_FIRM_LOSS_BUDGET_PLAN.md`, `docs/43_PROP_FIRM_ACCOUNT_MODE_SWITCH_PLAN.md`

## 1. Purpose

Create one operational safety layer for ATLAX automatic trading. The layer
must make the active account, risk budget, execution health, trade state, and
failure conditions visible before new entries are allowed.

This plan improves reliability and observability. It does not add a new
strategy, change CRT validity, or allow AI to make trading decisions.

## 2. Design Principles

1. The current MT5 login is the active account identity.
2. A Prop-Firm profile is selected once and matched to the account binding.
3. `AUTO_ENFORCED` applies the matched profile automatically after verification.
4. Unknown, stale, mismatched, or incomplete data blocks new entries.
5. Protective management of existing approved positions follows the existing
   Execution Engine and protection-guard rules.
6. Every safety decision is persisted, logged, and auditable.
7. All thresholds and timing values come from validated configuration.
8. No password, token, wallet secret, or broker credential is displayed or
   stored in operational snapshots.

## 3. Target Operational States

```text
ARMED
  Account, profile, market data, storage, and risk context are verified.

WARNING
  Trading may continue, but a configured warning condition is active.

BLOCKED
  New entries are rejected because a safety condition is active.

PAUSED
  New entries are intentionally paused by an authorized operator.

UNKNOWN
  Required information cannot be verified. New entries fail closed.
```

The state must include a machine-readable reason code, human-readable reason,
first-seen timestamp, last-updated timestamp, and actor when an operator caused
the change.

## 4. Phase 0 - Governance and Inventory

### Deliverables

- Confirm this plan and its reason-code vocabulary.
- Inventory current Risk Engine, Execution Engine, MT5 adapter, runtime state,
  journal, audit log, Telegram menu, and monitoring counters.
- Identify existing safety controls so they are reused instead of duplicated.
- Define which controls protect new entries and which may manage open positions.

### Gate

No implementation begins until each safety decision has one documented owner:

```text
Account identity       Account-mode runtime
Loss budget            Risk Engine / Prop-Firm runtime
Order lifecycle        Execution Engine / Trade Manager
Operational health     Operations monitor
User controls          Telegram admin boundary
```

### 4.1 Current Inventory

The repository already contains these relevant controls:

| Area | Existing implementation | Current boundary |
|---|---|---|
| Account identity | `core/market_data/mt5_account_identity.py` | Reads login, server, currency, and terminal metadata from MT5. |
| Account binding | `core/risk/account_mode.py` | Persists an approved account binding and verifies mismatches. |
| Automatic mode | `AUTO_ENFORCED` in `core/models/account_mode.py` and `core/market_data/scanner.py` | Automatically applies the selected profile after identity verification. |
| Loss budget | `core/risk/loss_budget.py` and `core/risk/prop_firm_runtime.py` | Calculates dynamic overall and daily buffers from the typed profile and account snapshot. |
| Risk approval | `core/risk/risk_engine.py` | Consumes the Prop-Firm context before sizing and entry approval. |
| Execution | `core/execution/execution_engine.py` and `core/execution/trade_manager.py` | Sends MT5 orders and manages approved positions. |
| Closed-deal reconciliation | `core/analytics/realized_pnl.py` and scanner reconciliation flow | Reconciles matching ATLAX exit deals and records realized results. |
| Operational counters | `core/operations/monitoring.py` | Tracks account-mode, delivery, quality, volume-profile, and routing counters. |
| Admin health | `MarketScanner._show_admin_health` | Shows scanner, Telegram, payment, subscriber, and prediction counts. |
| Emergency pause | `MarketScanner._handle_risk_callback` | Provides a manual pause/resume control and a basic daily-loss check. |
| Runtime persistence | Configured runtime store and `data/runtime_operations_metrics.json` | Persists restart-safe state and counters. |
| Audit | `data/admin_audit.jsonl` and operational audit helpers | Records administrator actions and selected operational events. |

### 4.2 Phase 0 Gap Register

The following gaps are confirmed and remain for later phases:

1. The admin health view does not show the active MT5 identity, selected
   profile, verification state, or remaining overall/daily buffers.
2. There is no single read-only Operations Snapshot combining account, risk,
   execution, data, storage, and delivery health.
3. The existing manual daily-loss pause is not the unified circuit-breaker
   state machine defined by this plan.
4. Reconciliation is now implemented for open positions, pending orders, and
   matching closed deals; broader historical/manual-trade review remains a
   later operational concern.
5. There is no dedicated watchdog for scanner, MT5 IPC, data freshness,
   Telegram delivery, runtime persistence, and journal health.
6. Circuit-breaker and reconciliation reason codes are standardized; watchdog
   reason codes remain for a later phase.

### 4.3 Phase 1 Boundary

Phase 1 will implement only the read-only Operations Snapshot and its tests.
It will reuse existing account-mode and loss-budget providers, expose no new
trade decision, and make no changes to order sizing or execution. Phase 2 may
consume the snapshot for circuit-breaker decisions only after the snapshot
fields and failure semantics are verified.

**Phase 0 result:** Governance owners are identified, existing controls are
inventoried, the gap register is recorded, and Phase 1 has a bounded scope.
No live trading behavior was changed during this phase.

## 5. Phase 1 - Safety and Operations Snapshot

Add a read-only operational snapshot available to the administrator through
Telegram and future dashboard/API surfaces.

### Required fields

- Active MT5 login, server, currency, and terminal identity when available.
- Account mode: Normal, Shadow, Enforced, or Auto-Enforced.
- Account verification status and reason.
- Selected firm, program, phase, account type, and profile version.
- Balance, equity, opening equity, and applicable reference balance.
- Overall loss limit and remaining overall buffer.
- Daily loss limit, reset timezone, and remaining daily buffer.
- Current floating loss, ATLAX open risk, and configured reserves.
- Current risk multiplier and recovery state.
- Last successful MT5 connection and market-data cycle.
- Last alert, last execution, and last reconciliation timestamps.
- Active circuit-breaker state and reason codes.

### Security rules

- Admin-only access.
- No broker password, API key, or payment secret.
- Redact sensitive request data from logs.
- Snapshot reads must use allow-listed fields only.

### Phase 1 Implementation Status

The read-only Operations Snapshot is implemented in
`core/operations/snapshot.py` and exposed through the admin Telegram menu.
It reports the active MT5 identity, account mode, profile, verification state,
balance, equity, loss-budget buffers, scanner cycle, MT5 health, Telegram
health, and sanitized operational metrics. It does not change entry approval,
lot sizing, or execution behavior.

### Phase 1 Completion Gate

- [x] Read-only snapshot contract and sanitization.
- [x] Admin Telegram access with operational audit entry.
- [x] Snapshot unit coverage.

## 6. Phase 2 - Automatic Circuit Breaker

Add a deterministic guard before new trade approval.

### Block conditions

- Account identity is missing or does not match the binding.
- Selected Prop-Firm profile is missing, stale, or invalid.
- Overall or daily loss budget is exhausted.
- Open risk or broker cost valuation is unavailable.
- MT5 IPC connection is unavailable.
- Required market data is stale or incomplete.
- Storage or journal persistence fails.
- Configuration validation fails.
- A configured loss-streak or drawdown warning reaches its approved limit.
- Spread, slippage, or execution-health limits are exceeded.

### Required behavior

- Reject only new entries when the guard blocks.
- Preserve approved protective management for existing positions when the
  Execution Engine authorizes it.
- Record the exact reason code and relevant safe-to-display values.
- Send one deduplicated administrator notification per state transition.
- Require an explicit authorized resume after a hard block where documented.
- Never use a zero, stale, or guessed value as a substitute for missing data.

### Configuration

All warning thresholds, cooldowns, notification intervals, and resume policy
must be validated Settings Engine values. No loss-streak, spread, slippage, or
cooldown number may be embedded in code.

### Phase 2 Implementation Status

The first circuit-breaker slice is implemented in
`core/risk/circuit_breaker.py` and is loaded through
`core/settings/circuit_breaker_config.py`. The scanner evaluates it immediately
before the existing Risk Engine entry decision. It fail-closes new entries when
MT5 trading capability, runtime storage, verified Auto/Enforced Prop-Firm
context, or existing daily/weekly risk limits are unavailable or breached.

The guard persists restart-safe state under
`operations.circuit_breaker`, records state transitions in the operational
metrics ledger, and can send one administrator notification per transition.
It does not modify, close, or pause management of existing positions. Prop-Firm
Shadow mode remains observational and continues through the existing live risk
path; only Enforced and Auto-Enforced modes use the verified Prop-Firm block.

Current validated configuration enables all Phase 2 block flags and transition
notifications in `config/atlax.yaml`.

### Phase 2 Completion Gate

- [x] Validated circuit-breaker configuration.
- [x] Deterministic pre-entry evaluation with fail-closed UNKNOWN handling.
- [x] Existing daily/weekly Risk Engine limits reused without new thresholds.
- [x] Restart-safe state and transition counters.
- [x] Shadow mode preserved as non-blocking audit behavior.
- [x] Focused unit tests for allow, block, unknown, and transition deduplication.

## 7. Phase 3 - MT5 Trade Reconciliation

Build a restart-safe reconciliation cycle between MT5 and ATLAX storage.

### Reconcile

- Current open positions and pending orders.
- ATLAX magic number and ownership.
- Entry price, volume, stop-loss, take-profit, and direction.
- Broker ticket and ATLAX execution identifier.
- Closed deals since the last successful checkpoint.
- Profit, commission, swap, and close reason when available.

### Outcomes

```text
MATCHED
MISSING_FROM_ATLAX
MISSING_FROM_MT5
MANUAL_OR_UNKNOWN
CONFLICT
UNKNOWN
```

`CONFLICT` and `UNKNOWN` must block new entries until reconciled or explicitly
cleared by an authorized operator. The system must never duplicate an order to
repair an uncertain state.

### Phase 3 Implementation Status

The reconciliation contract is implemented in
`core/operations/reconciliation.py`. At the start of every scanner cycle, ATLAX
reads MT5 open positions and pending orders, compares ATLAX-owned records using
the configured magic number and broker identifiers, settles matching closed
deals through the existing broker-authoritative PnL path, and persists the
sanitized report under `operations.reconciliation`.

Execution attempts are now persisted at both live execution callsites, and a
confirmed position identifier is linked to the execution ledger. Matching is
strict: ATLAX never claims ownership from symbol, direction, or timing alone.
Missing identifiers, unavailable MT5 state, conflicts, and persistence errors
remain `UNKNOWN` and fail closed for new entries. Existing positions are not
closed, modified, or duplicated by this phase.

The admin Operations Snapshot displays the latest reconciliation status and
the operational metrics ledger records reconciliation totals and statuses.

### Phase 3 Completion Gate

- [x] Open-position and pending-order comparison.
- [x] ATLAX magic-number ownership check.
- [x] Execution-record persistence and broker-position linking.
- [x] Closed-deal settlement through broker-authoritative PnL.
- [x] `MATCHED`, `MISSING_FROM_ATLAX`, `MISSING_FROM_MT5`, `CONFLICT`, and
  `UNKNOWN` outcomes.
- [x] `CONFLICT` and `UNKNOWN` new-entry blocking.
- [x] Focused reconciliation and persistence tests.

## 8. Phase 4 - Prop-Firm Rule Profile Monitor

Extend the source-backed profile catalog with operational freshness checks.

Each profile must include:

- Firm and program identity.
- Exact phase and account type.
- Currency and nominal balance when provided.
- Overall-loss and daily-loss definitions.
- Reset timezone and reference basis.
- Source URLs, retrieval timestamp, version, content hash, and approval state.

### Monitor behavior

- Mark profiles stale after the configured age.
- Detect content-hash changes.
- Preserve the previous approved version for audit comparison.
- Require review before a changed profile can be used for live enforcement.
- Keep the last approved profile available for evidence, but fail closed when
  the live profile is no longer approved.

The monitor must not scrape or infer undocumented rules into live behavior.

### Phase 4 Implementation Status

The source-backed profile monitor is implemented in
`core/risk/prop_firm_profile_monitor.py`. It validates each typed profile with
the configured `account_mode.max_profile_age_hours`, records the initial
catalog as a governed baseline, preserves the previously approved version and
content hash, and marks changed profiles `REVIEW_REQUIRED`. Stale, invalid,
missing, and unavailable profile state is fail-closed and never becomes live
Prop-Firm enforcement automatically.

The scanner refreshes the monitor at startup and at the beginning of each scan
cycle. Automatic account binding and runtime context resolution require the
selected profile ID/version to remain exactly live-approved. The admin
Operations Snapshot exposes the selected profile monitor status, while the
operations metrics ledger counts monitor transitions without storing secrets.
The monitor stores only profile metadata, source URLs, timestamps, versions,
and hashes; it does not store credentials or scrape external rule pages.

### Phase 4 Completion Gate

- [x] Source-backed freshness validation using configured profile age.
- [x] Initial catalog baseline recording.
- [x] Version/content-hash change detection.
- [x] Previous approved identity preservation for audit comparison.
- [x] Fail-closed live enforcement gate for stale, changed, invalid, missing,
  or unavailable profiles.
- [x] Operations Snapshot profile-monitor status.
- [x] Structured transition logging and profile-monitor counters.
- [x] Focused profile-monitor and contract tests.

## 9. Phase 5 - Execution Quality Journal

Record execution facts for every approved order and position update:

- Candidate and Signal Passport identifiers.
- Requested and actual entry prices.
- Requested and actual volume.
- Spread and slippage when available.
- Broker return code and comment.
- Execution latency.
- Stop-loss and take-profit values.
- Profit-protection and circuit-breaker actions.
- Final outcome, costs, and close reason.

The journal must distinguish setup quality from execution quality. Analytics may
report both, but must not rewrite detector or strategy rules automatically.

### Phase 5 Implementation Status

The execution-quality journal is implemented in
`core/models/execution_quality.py`, `core/analytics/journal.py`,
`core/storage/repositories.py`, and `core/storage/service_adapters.py`.
SQLite and PostgreSQL persist an idempotent `execution_quality_events` ledger
separate from the execution-attempt ledger. The scanner records the initial
execution result, actual broker position linkage, and broker-reported
settlement. The Trade Manager records protection actions such as breakeven,
profit-lock, momentum stop updates, and partial closes with `APPLIED`,
`FAILED`, or `BLOCKED` status. Circuit-breaker transitions are recorded with
their safe reason code and allow/block result.

When a requested market price is absent, actual-price and slippage fields stay
unknown rather than being inferred. Position linkage is emitted only after the
journal accepts the link, and repeated writes are ignored by the event ID. No
raw MT5 payloads, credentials, or private broker secrets are stored in the
quality ledger.

### Phase 5 Completion Gate

- [x] Typed execution-quality event contract and event types.
- [x] Idempotent SQLite persistence and bounded query API.
- [x] PostgreSQL schema and repository/service-adapter support.
- [x] Execution result, position linkage, protection-action, circuit-breaker,
  and settlement event wiring.
- [x] Focused journal and Trade Manager tests.
- [x] Execution Engine and Logging Specification updates.

## 10. Phase 6 - Reliability Watchdog

Add a watchdog for the complete runtime path.

### Monitored components

- Scanner process and instance lock.
- MT5 IPC connection.
- Market-data freshness.
- Telegram polling and delivery.
- Runtime-state persistence.
- Journal and audit persistence.
- Payment and subscriber services where enabled.
- Configuration reload health.

### Watchdog behavior

- Emit heartbeat and last-success timestamps.
- Detect repeated failures with configurable backoff.
- Deduplicate notifications.
- Alert the administrator when recovery requires action.
- Never start a second scanner when the instance lock is held.
- Preserve fail-closed entry behavior during watchdog uncertainty.

### Phase 6 Implementation Status

The reliability watchdog is implemented in `core/operations/watchdog.py` and
loaded through `core/settings/watchdog_config.py`. The scanner publishes
restart-safe health snapshots for its process lifecycle, instance lock, MT5
IPC, market-data freshness, Telegram polling/delivery, runtime state, journal,
audit log, subscriber/payment availability, and configuration-file drift.
Each component carries a bounded status, reason, observation time, and last
successful observation when available.

Watchdog failure notifications require the configured consecutive-failure
threshold and are deduplicated by failure signature. Unchanged failures do
not repeat by default; the configured cooldown is used only when explicit
`repeat_notifications` mode is enabled. The watchdog never starts or stops a scanner, changes an entry decision, modifies
an open position, or treats an unknown health value as healthy. Its current
state is visible in the admin Operations Snapshot under `health.watchdog`.

### Phase 6 Completion Gate

- [x] Validated heartbeat, staleness, failure-threshold, and notification
  cooldown configuration.
- [x] Restart-safe health state under `operations.watchdog`.
- [x] Scanner, lock, MT5, market-data, Telegram, runtime, journal, audit,
  subscriber/payment, and configuration health probes.
- [x] Last-success timestamps and bounded reasons.
- [x] Deduplicated administrator notifications.
- [x] Operations Snapshot visibility.
- [x] Focused watchdog and regression tests.

## 11. Phase 7 - Telegram and Dashboard Controls

Admin-only controls:

- View Operations Snapshot.
- View active account and profile.
- Pause new entries.
- Resume after verification.
- View circuit-breaker reasons.
- View reconciliation conflicts.
- View recent execution-quality events.
- View profile freshness and approval status.
- View audit history.

The user interface must clearly distinguish:

- `Shadow`: evaluates without changing live risk decisions.
- `Normal`: uses ordinary ATLAX risk policy.
- `Auto-Enforced`: applies the matched verified Prop-Firm profile live.
- `Blocked`: prevents new entries because required verification failed.

### Phase 7 Implementation Status

Phase 7 is implemented across both administrator surfaces. Telegram exposes the
Operations Snapshot and recent Execution Quality views behind the existing
admin authorization gate; each view records an operational audit event. The
snapshot persists its latest sanitized copy under `operations.snapshot` so the
dashboard can read the same evidence without opening an MT5 or execution
control path.

The dashboard Operations Center is served by the admin-only
`GET /api/admin/operations` endpoint. It displays account/profile context,
watchdog components, the new-entry circuit breaker, reconciliation status,
recent execution-quality events, and bounded audit history. The API whitelists
quality fields and excludes event metadata, credentials, and raw broker
payloads. When no full snapshot has been published, the UI shows that state
explicitly instead of inventing account data.

### Phase 7 Completion Gate

- [x] Admin-only Telegram Operations Snapshot.
- [x] Admin-only Telegram execution-quality view and refresh controls.
- [x] Admin-only dashboard Operations Center.
- [x] Explicit Normal, Shadow, Auto-Enforced, and Blocked state labels.
- [x] Watchdog, circuit-breaker, reconciliation, profile, and audit visibility.
- [x] Secret-free API and UI payload boundaries.
- [x] Focused authorization, redaction, and Telegram rendering tests.

## 12. Phase 8 - Logging and Metrics

Add structured events and counters for:

- `account_identity_detected`
- `account_binding_verified`
- `account_binding_mismatch`
- `prop_firm_profile_stale`
- `prop_firm_profile_monitor_transition`
- `prop_firm_profile_monitor_failed`
- `operations_state_changed`
- `circuit_breaker_blocked`
- `circuit_breaker_resumed`
- `trade_reconciliation_started`
- `trade_reconciliation_conflict`
- `trade_reconciliation_completed`
- `execution_quality_recorded`
- `watchdog_heartbeat`
- `watchdog_failure`

Counters should include totals and the current state, without storing secrets.

## 13. Phase 9 - Testing Strategy

### Unit tests

- Loss-budget calculations use the profile basis and timezone.
- Missing values return `UNKNOWN` and block new entries.
- Account mismatch blocks enforcement.
- Auto-Enforced activates only for a matching binding.
- Shadow does not alter the live risk result.
- Normal does not silently claim Prop-Firm protection.
- Circuit-breaker transitions are deterministic.
- Reconciliation detects missing, duplicate, manual, and conflicting trades.
- Profile staleness and version changes are handled safely.

### Integration tests

- MT5 connected and disconnected.
- Account login changed while the scanner is running.
- Scanner restart with open positions.
- Telegram delivery failure.
- Storage write failure.
- Duplicate scanner startup.
- Broker order accepted, rejected, and returned ambiguously.

### Acceptance tests

1. Login to the approved account and confirm `AUTO_ENFORCED` and `VERIFIED`.
2. Login to a different account and confirm `MISMATCH` plus blocked entries.
3. Remove required profile data and confirm `UNKNOWN` plus blocked entries.
4. Reconnect the approved account and confirm automatic recovery after
   verification.
5. Reconcile an open position after scanner restart without duplicate orders.
6. Confirm every transition appears in Telegram, logs, and audit storage.

## 14. Phase 10 - Rollout

### Shadow and audit period

- Run reconciliation and watchdog observations without changing existing
  execution decisions.
- Review blocked, unknown, mismatch, and conflict events.
- Confirm snapshots contain no secrets.
- Confirm notifications are actionable and not duplicated.

### Live promotion gate

Promote only when:

- Focused and integration tests pass.
- Account identity is verified.
- The selected profile is source-backed, approved, and fresh.
- Reconciliation has no unresolved conflicts.
- MT5 IPC and data freshness are healthy.
- Circuit-breaker behavior has been tested.
- Rollback and pause controls have been exercised.

### Rollback

Rollback must pause new entries, preserve protective management where allowed,
record the reason, and leave the account binding and audit history intact.

## 15. Completion Checklist

- [x] Phase 0 inventory and ownership boundaries recorded.
- [ ] Documentation and reason codes approved.
- [x] Operations snapshot implemented.
- [x] Automatic circuit breaker implemented.
- [x] MT5 reconciliation implemented.
- [x] Prop-Firm profile monitor implemented.
- [x] Execution-quality journal implemented.
- [x] Watchdog implemented.
- [x] Telegram and dashboard views implemented.
- [ ] Structured logging and counters verified.
- [ ] Unit and integration tests pass.
- [ ] Live acceptance tests pass.
- [ ] Rollback tested.
- [ ] Project lead approves live promotion.
