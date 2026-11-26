# ATLAX Codebase Audit Remediation Plan

Version: 1.0  
Status: ACTIVE - remediation tracking  
Audit date: 2026-08-14  
Authority: `AGENTS.md`, `docs/02_ENGINEERING_STANDARDS.md`, `docs/03_ARCHITECTURE.md`, `docs/10_RISK_ENGINE.md`, `docs/11_EXECUTION_ENGINE.md`, `docs/16_SECURITY.md`

## Purpose

This document is the master register for the whole-codebase audit. Every
confirmed defect, security issue, runtime mismatch, test failure, and document
conflict must remain tracked here until it has evidence of closure.

No new live-trading feature is complete while a Critical or High item in this
register is open. Trading rules must not be changed by this plan. Strategy
behavior changes require a separate approved rulebook or ADR update.

## Operating Rules

- Keep the scanner unchanged during read-only audit work.
- Before live execution changes, use a safety gate: `approval.require_manual=true`
  or `execution.enabled=false` in the deployment environment.
- Never commit credentials, subscriber data, payment data, journals, audit logs,
  runtime state, or generated Python artifacts.
- Use the existing Settings Engine and repository factories; do not add hidden
  defaults or direct service-to-service storage calls.
- Every fix needs a focused test, structured logging, documentation alignment,
  and a rollback note.
- A finding moves to `VERIFIED` only after its acceptance test passes in the
  relevant environment. `FIXED` means code changed but verification is still
  pending.

## Status Values

`OPEN` = confirmed and unresolved  
`IN_PROGRESS` = actively being changed  
`FIXED` = implementation completed, verification pending  
`VERIFIED` = implementation and acceptance evidence complete  
`BLOCKED` = external dependency prevents progress; record the dependency

## Current Progress

Phase 1 and Phase 2 implementation has started. `FIXED` below means the code
change is present and focused tests pass; deployment verification, credential
rotation, Git-history cleanup, or live broker rehearsal may still be required.

Evidence collected on 2026-08-14:

- `tests/unit/api/test_server_auth.py`: 18 passed.
- `tests/unit/risk/test_risk_engine.py` and
  `tests/unit/execution/test_execution_engine.py`: 61 passed.
- The sanitized live YAML passed every existing Settings Engine loader with
  process-only validation credentials.
- The active scanner process was not stopped or restarted during this work.

## Master Issue Register

| ID | Severity | Area | Finding | Status | Evidence / acceptance test |
|---|---|---|---|---|---|
| AUD-001 | Critical | Secrets | Live credentials and secret-bearing values are present in tracked configuration. | FIXED | Current YAML values redacted; secret loaders are environment-only; credential rotation and Git-history scan remain deployment work. |
| AUD-002 | Critical | Privacy / Git | Subscriber, payment, journal, audit, SQLite, logs, and runtime JSON files are tracked. | FIXED | Runtime exclusions added to `.gitignore`; 195 explicitly identified runtime/generated paths removed from the Git index while local files were preserved; `git ls-files` now reports none of the protected patterns. |
| AUD-003 | Critical | Execution | Live execution mode must be explicit and documented. | ACCEPTED FOR CONTROLLED LIVE MODE | Project lead approved `approval.require_manual=false` on 2026-08-17. ADR-016 records the decision; Risk Engine, MT5 preflight, broker validation, audit logging, and reconciliation remain mandatory. |
| AUD-004 | Critical | Risk | `weekly_pnl` is always zero and `trades_today` is always zero. | FIXED | Scanner now computes UTC day/week realized PnL and opened-trade counts from ATLAX magic-filtered MT5 deals. Live history reconciliation remains pending. |
| AUD-005 | Critical | Trade management | Breakeven code references undefined `watch`. | FIXED | TP2 is passed explicitly and preserved; regression coverage added. |
| AUD-006 | High | FVG / Telegram | Scanner calls missing `broadcast_fvg_watch()` and `broadcast_fvg_entry()` methods. | FIXED | Added both Telegram broadcast methods with subscriber confidence, plan, pair, and key-level routing plus persisted FVG watch context; added Alert Engine Signal Passport registration; captured XAUUSD replay now verifies Passport registration, admin/subscriber Alert 1 delivery, watch persistence, live-zone trigger, and admin/subscriber Alert 2 delivery. Post-restart live deployment verification remains operational work. |
| AUD-007 | High | CRT detection | Active `require_confirmation_candle=false` makes the detector evaluate the last two candles as parent/sweep, while approved fixtures and documentation use the three-candle structure. | VERIFIED | Documented both rulebook-approved modes, added active YAML two-candle bullish and bearish acceptance tests, and retained three-candle gold-standard coverage. No live setting was changed. |
| AUD-008 | High | Approval | Manual API approval changes queue status but has no normal scanner worker that executes the approved request. | FIXED | Added execution-ready gating, atomic `APPROVED -> EXECUTING` claims, terminal outcome transitions, normal-request consumption, and delayed-FVG release; focused approval/scanner tests pass. Live restart and broker rehearsal remain pending. |
| AUD-009 | High | Risk sizing | One global pip value and contract size are used for forex, JPY, and gold; tiny stops can produce oversized lots. | FIXED | Live scanner snapshots now require complete MT5 tick economics and volume constraints; incomplete metadata rejects sizing fail-closed. USDJPY/XAUUSD calculations, partial metadata rejection, scanner wiring, and execution/risk integration tests pass. MT5 asset-by-asset rehearsal remains pending. |
| AUD-010 | High | MT5 execution | Fill policy is fixed to `RETURN` instead of being selected from symbol execution capabilities. | FIXED | Execution resolves requested mode against broker symbol capabilities and fails closed when none is supported. |
| AUD-011 | High | Execution contract | Execution model documentation says TP1 while the approved execution policy requires TP2 attached to the broker position. | FIXED | Execution model docstring and execution documentation now agree on TP2 attachment with TP1 partial management. |
| AUD-012 | High | Execution reliability | Retrying `order_send` after an ambiguous exception or `None` result may duplicate a broker-accepted order. | FIXED | Ambiguous responses are recorded as `AMBIGUOUS` and never blindly retried; regression coverage added. |
| AUD-013 | High | Webhooks | TradingView intake validates and acknowledges payloads but does not enqueue them for downstream processing. | IN_PROGRESS | Added a durable SQLite intake queue plus an approved shadow consumer with strict detector-event/provenance validation, immutable `OK`/`UNKNOWN` processing results, restart recovery, idempotency, retry/dead-letter handling, and accepted replay fixtures. Strategy, confidence, risk, alert, and execution influence remains disabled pending monitored shadow runs and a separate promotion approval. |
| AUD-014 | High | Analytics | Signal outcome tracking stores placeholder PnL values instead of calculated realized PnL. | FIXED | Alert-level outcomes calculate deterministic signal-level `pnl_r`; broker-authoritative tick/cost math and MT5 exit-deal reconciliation are implemented; scanner risk-period totals and the private execution ledger use broker results; closed positions persist `OK` or `UNKNOWN` settlement status. Live MT5 close-deal rehearsal remains operational verification. |
| AUD-015 | High | API config | Config writes validate YAML syntax only, write directly, and do not validate the full Settings Engine schema atomically. | FIXED | Full document validation, sanitized backup, atomic replacement, and audit record are implemented and loader-validated. |
| AUD-016 | High | API security | Config GET can return the complete YAML, and journal/metrics/heatmap are authenticated but not admin-scoped. | FIXED | Secret paths are redacted; sensitive analytics routes require admin; storage failures return explicit 503 responses. |
| AUD-017 | High | Repository security | Google Sheets webhook URL has a hardcoded fallback and is also stored in tracked config. | FIXED | Hardcoded fallback removed; analytics loader and sender require the environment variable. Deployment rotation remains pending. |
| AUD-018 | High | Storage | Runtime still defaults to local JSON/SQLite; PostgreSQL dual-read and Cloud SQL cutover evidence are incomplete. | IN_PROGRESS | Added a read-only fail-closed cutover preflight and manifest-verified frozen snapshot tooling. Two database-backed rehearsals, Cloud SQL provisioning, and coordinated cutover evidence remain pending. |
| AUD-019 | Medium | Alert delivery | Deduplication is updated even when no delivery channel succeeds, which can suppress a retry. | FIXED | Alert Engine deduplication waits for confirmed delivery; Telegram records `delivered`, `suppressed`, or `retryable`; scanner-level state is also withheld for eligible transport failures. Zero-delivery retry logging and regression coverage remain in place. |
| AUD-020 | Medium | Authentication | Signup bootstrap uses a check-then-create race; concurrent first signups can contend for admin initialization. | FIXED | SQLite `BEGIN IMMEDIATE` and PostgreSQL table locking make bootstrap atomic. Concurrency rehearsal remains pending. |
| AUD-021 | Medium | Authentication | JWTs lack an explicit issuer/token type, and auth cookie security defaults to non-secure. | FIXED | Dashboard issuer/type validation and production secure-cookie enforcement are implemented and API-tested. |
| AUD-022 | Medium | Dashboard | UI route protection checks authentication but not role, even though several pages are admin-only by behavior. | VERIFIED | Added role-aware `AdminRoute`, an explicit access-denied route, and admin-only navigation boundary; backend role tests remain green and dashboard lint/build pass. |
| AUD-023 | Medium | Configuration | FVG minimum gap and LTF ATR/candle settings are instantiated with hardcoded values in the scanner. | FIXED | Added required validated `fvg` settings, wired startup/API validation and scanner construction, and removed scanner defaults; config-loader regression coverage added. |
| AUD-024 | Medium | Timeframes | M1 remains accepted by the candle feed and stale M1 artifacts remain in the repository despite its retirement from active routing. | FIXED | Added a shared retired-timeframe policy; active instrument/config construction, MT5 feed resolution, timeframe seconds, webhook intake, subscriber routing, and MCP filters reject or exclude M1. Historical rulebook references remain non-runtime documentation. |
| AUD-025 | Medium | Observability | Several API and analytics paths convert storage failures into empty or zero-valued success responses. | FIXED | MCP journal, performance, list, baseline, and market-context queries return explicit `status: UNKNOWN` envelopes; advisory reports stop or label unavailable context; approval queue failures return HTTP 503; Passport history and Signal Terminal now expose unavailable storage as `UNKNOWN`; partial market-context row failures are counted and surfaced. |
| AUD-026 | Medium | Governance | Source-intake approval text contradicts its later pending/research-only text; several plans mix live and pending phase language. | VERIFIED | Reconciled the CRT intake and research log on 2026-08-15: approved rulebook entries are authoritative, unresolved research remains non-executable, and pending primary sources cannot amend the rulebook without project-lead approval. |
| AUD-027 | Medium | Tests | CRT detector suite has 20 failures; execution/risk/alert/storage suite has 1 environment-sensitive failure. | VERIFIED | CRT fixtures now use an explicit confirmation branch and approved sweep bounds without changing live detector settings; the complete unit suite passes 331 tests. |
| AUD-028 | Low | Dead code | Legacy FVG `add_watch()` constructs an invalid symbol value even though `add_watch_full()` is the active path. | FIXED | Removed the uncalled invalid method, documented `add_watch_full()` as canonical, and added a persisted-watch/trigger regression. |

## Phased Work Plan

### Phase 0 - Safety Gate And Baseline

Status: `OPEN`  
Goal: Prevent additional live exposure while fixes are being verified.

- Record the active scanner PID, configuration snapshot, open ATLAX positions,
  and current local-store backup hashes.
- Require manual approval or disable execution in the deployment environment.
- Do not delete local data; create a restricted backup outside Git.
- Create a remediation branch and preserve unrelated user changes.

Exit gate:

- No new automatic order can pass the deployment safety gate.
- Backup and rollback evidence is recorded.

### Phase 1 - Secrets, Privacy, And API Containment

Status: `OPEN`  
Issues: `AUD-001`, `AUD-002`, `AUD-015`, `AUD-016`, `AUD-017`, `AUD-020`, `AUD-021`, `AUD-022`

- Rotate exposed Telegram, MT5, AI, dashboard, webhook, and Apps Script
  credentials before removing them from source.
- Add complete runtime/data/cache/log exclusions and remove tracked artifacts
  from Git without destroying the local working copies.
- Replace config writes with full schema validation, atomic writes, versioning,
  audit events, and rollback.
- Redact secret-bearing config reads and enforce role boundaries in both API and
  dashboard.

Exit gate:

- Secret scan is clean for tracked files and Git history remediation is complete.
- Security tests cover authentication, roles, cookies, CORS, config mutation,
  and secret redaction.

### Phase 2 - Risk And Execution Safety

Status: `OPEN`  
Issues: `AUD-003`, `AUD-004`, `AUD-005`, `AUD-008`, `AUD-009`, `AUD-010`, `AUD-011`, `AUD-012`

- Implement authoritative daily/weekly PnL and trade-count snapshots.
- Scope open positions and history to the approved ATLAX magic number unless a
  documented account-wide policy says otherwise.
- Use MT5 symbol tick/contract metadata, minimum stop distance, volume limits,
  and execution-mode-compatible filling policies.
- Repair approval consumption and make execution idempotent and reconcilable.
- Repair partial-profit management and verify TP1 partial close plus TP2 final
  target preservation.

Exit gate:

- All risk rejection controls have passing unit and fake-MT5 integration tests.
- No order can be sent with missing risk state, unsupported broker fields, or an
  ambiguous prior submission.

### Phase 3 - Detector, FVG, And Alert Correctness

Status: `OPEN`  
Issues: `AUD-006`, `AUD-007`, `AUD-019`, `AUD-023`, `AUD-024`, `AUD-028`

- Resolve the confirmation-candle policy through the rulebook and update config,
  fixtures, detector tests, and alert explanations together.
- Repair the FVG alert path or remove it from production until its Telegram,
  entitlement, Passport, and execution behavior is complete.
- Make FVG and ATR inputs configuration-driven.
- Complete M1 retirement at every boundary and clean stale artifacts.
- Ensure delivery failure is retryable and every sent/suppressed event remains
  auditable.

Exit gate:

- Approved bullish, bearish, invalidation, nested, FVG, and no-confirmation
  fixtures pass.
- A captured live FVG event produces either a complete alert or an explicit,
  persisted failure without aborting unrelated instrument scans.

### Phase 4 - Webhook And Analytics Integrity

Status: `OPEN`  
Issues: `AUD-013`, `AUD-014`, `AUD-025`

- Add durable webhook intake, idempotency, replay protection, and a downstream
  consumer after the proposed downstream contract is approved.
- Replace placeholder PnL with verified realized-outcome calculations.
- Make failures visible as errors or `UNKNOWN`, never empty successful reports.
- Reconcile Signal Passport, journal, Google Sheets, Telegram outcome updates,
  and execution records by permanent IDs.

Exit gate:

- A webhook replay is processed once.
- A synthetic WIN, LOSS, partial TP1, expiry, and ambiguous execution produce
  consistent Passport, journal, Sheets, and report records.

### Phase 5 - PostgreSQL Cutover

Status: `BLOCKED`  
Issues: `AUD-018`

Cloud SQL provisioning and database-backed rehearsal are paused because the
required cloud billing/card setup is not available. No further Cloud SQL
changes are part of the current local release phase.

- Provision Cloud SQL with TLS, backups, least-privilege roles, and monitoring.
- Import a frozen local snapshot into a clone.
- Run dual-read comparisons twice with zero mismatches.
- Switch scanner, Telegram, API, payments, approvals, journal, audit, runtime
  state, dashboard, and MCP together.
- Keep local storage read-only during the rollback window.

Exit gate:

- Cloud SQL is authoritative only after migration evidence and operational signoff.

### Phase 6 - Tests, Documentation, And Release Readiness

Status: `VERIFIED`  
Issues: `AUD-026`, `AUD-027`

- Repair the detector failures without silently weakening approved rules.
- Add CI checks for tests, secret scans, tracked runtime files, compile/import,
  configuration validation, and documentation status consistency.
- Update all affected specifications and ADRs with final behavior and evidence.
- Run a production-like smoke test covering signup, subscription, alert routing,
  premium filtering, Signal Passport, payment records, execution gates, and
  storage recovery.

Exit gate:

- CRT governance language is consistent and the authority boundary is explicit.
- The complete Python unit suite and dashboard build/lint gates pass.
- The overall audit remains open until unrelated live/deployment items are
  verified or explicitly blocked with an owner and external dependency.

## Required Verification Commands

Run from `C:\Users\HP\Desktop\Atlax`:

```powershell
python -m pytest -q tests/unit
python -m pytest -q tests/unit/execution tests/unit/risk tests/unit/alerts tests/unit/storage
python -m pytest -q tests/unit/detectors/crt tests/unit/strategy/test_nested_crt_router.py
git diff --check
git ls-files data
git ls-files | Select-String '__pycache__|\.pyc$'
```

For live or staging verification, also retain:

- configuration snapshot ID;
- scanner and API process IDs;
- migration comparison report;
- Telegram delivery/rejection counts;
- risk rejection and execution status counts;
- Signal Passport outcome reconciliation report;
- rollback test result.

## Change Log

| Date | Change | Evidence |
|---|---|---|
| 2026-08-14 | Initial whole-codebase audit register created. | Audit review of Desktop checkout, focused tests, live scanner log, and governing documents. |
| 2026-08-14 | Phase 1 and Phase 2 hardening started: environment-only secrets, API containment, atomic bootstrap/config writes, approval gate, broker-aware risk sizing, UTC risk periods, broker fill resolution, ambiguous-order handling, and TP2 preservation. | API 18 passed; risk/execution 61 passed; complete sanitized YAML passed all Settings Engine loaders. |
| 2026-08-14 | Repository privacy cleanup completed for AUD-002. | Added runtime/generated ignore rules and removed 195 tracked runtime/database/log/bytecode paths from the Git index without deleting local data; preservation checks passed. |
| 2026-08-14 | Approval lifecycle completed for AUD-008. | Added typed approval execution states, SQLite/PostgreSQL compare-and-set transitions, execution-ready gating for delayed FVG entries, and scanner consumption; focused suite 7 passed. |
| 2026-08-14 | FVG Telegram delivery slice completed for AUD-006. | Added watch and entry broadcast methods, persisted key-level/profile-threshold context on FVG watches, and tested admin plus premium subscriber delivery; focused suite 13 passed. |
| 2026-08-14 | AUD-007 confirmation-policy audit paused at a governance decision. | The approved fixtures require three-candle confirmation, while the active config is `require_confirmation_candle=false`; the same fixtures also exceed the active `max_sweep_retracement_pct=0.66`. No unapproved live threshold or fixture change was made. Project-lead decision is required. |
| 2026-08-15 | AUD-007 confirmation-policy mismatch verified and closed. | `docs/07_DETECTOR_SPECIFICATION.md` now defines both configured modes; active YAML two-candle bullish/bearish acceptance tests pass alongside the three-candle fixtures. Live configuration remains unchanged. |
| 2026-08-14 | Alert delivery retryability completed for AUD-019. | Deduplication now follows confirmed channel delivery; zero-delivery alerts log `alert_delivery_retryable`; failure-then-recovery regression passes. |
| 2026-08-14 | FVG runtime inputs completed for AUD-023. | Added fail-closed `fvg` configuration for gap threshold, LTF candle count, LTF ATR period, and watch expiry; scanner/API loaders and settings tests pass. |
| 2026-08-14 | M1 retirement completed for AUD-024. | Active runtime boundaries reject or exclude M1; MT5/timeframe regression tests now assert retirement behavior. |
| 2026-08-14 | Legacy FVG watcher path completed for AUD-028. | Removed invalid `add_watch()`, retained only explicit-symbol registration, and added watcher regression coverage. |
| 2026-08-14 | FVG Passport and captured replay acceptance completed for AUD-006. | FVG Alert 1 now registers canonical alert/outcome metadata and journal evidence through the Alert Engine; `tests/unit/alerts/test_fvg_replay.py` replays captured XAUUSD values through both Telegram alerts, Passport history, persisted watch creation, and entry triggering. |
| 2026-08-14 | Signal-level outcome PnL correction started for AUD-014. | Replaced hard-coded tracker PnL values with documented R-multiple math; added `pnl_r` to Sheets feedback and 4 analytics regression tests. Execution cash-PnL reconciliation remains open. |
| 2026-08-14 | Durable webhook intake started for AUD-013. | Added `WebhookIntakeStore` with idempotent fingerprints, atomic claims, retry/dead-letter states, and consumer tests; API and configuration specifications now define the queue boundary. Downstream detector-to-strategy wiring remains intentionally pending contract approval. |
| 2026-08-14 | Analytics failure visibility advanced for AUD-025. | MCP journal and market-context list tools now return explicit `OK`/`UNKNOWN` envelopes; advisory daily, weekly, and anomaly flows no longer interpret unavailable storage as zero activity. Added failure-state regression coverage. |
| 2026-08-14 | Broker PnL reconciliation advanced for AUD-014. | Added `realized_pnl.py` for tick-economics and MT5 exit-deal reconciliation including signed swap, commission, and fee; scanner risk-period totals now use the reconciler. Execution-to-Passport persistence remains open. |
| 2026-08-14 | Execution-attempt persistence added for AUD-014. | Added SQLite/PostgreSQL `execution_records` storage, idempotent `record_id` writes, sanitized MT5 request persistence, scanner wiring, and focused coverage: 3 persistence tests plus the broader analytics/scanner target set passed 13 tests. |
| 2026-08-14 | Closed execution settlement completed for AUD-014. | Filled positions now link to the MT5 position identifier; closed-position history is reconciled by magic and position ID, and broker profit/cost fields persist as `OK` or `UNKNOWN`. Focused execution, scanner, PnL, and persistence coverage passed 39 tests. |
| 2026-08-14 | Analytics failure visibility completed for AUD-025. | Approval queue storage failures now return HTTP 503; Passport history exposes a status-bearing result for user-facing callers; Signal Terminal avoids zero-valued success output; malformed market-context rows produce `UNKNOWN` with a skip count. API, Passport, MCP, and Telegram regression coverage passed 44 tests. |
| 2026-08-15 | AUD-013 shadow contract approved and implemented. | ADR-014 records project-lead approval for shadow-only processing. Added `WebhookShadowConsumer`, immutable queue-side processing results, strict detector/provenance validation, and focused queue/consumer replay coverage. No Strategy, Confidence, Risk, Alert, or Execution callsite is enabled; fixture comparison and operational signoff remain pending. |
| 2026-08-15 | AUD-013 shadow fixture acceptance completed. | Added `tests/fixtures/webhooks/shadow_replay.json` for complete, no-detection, incomplete, retired-timeframe, unauthorized-direction, malformed, and duplicate cases; `docs/36_WEBHOOK_SHADOW_ACCEPTANCE_REPORT.md` records the matrix. Focused webhook coverage passed 14 tests; monitored real-event runs and live downstream promotion remain pending. |
| 2026-08-15 | Current detector source recorded. | ADR-015 records that the local MT5 feed is authoritative for the active deployment; TradingView remains dormant and optional. PostgreSQL cutover work proceeds against the local scanner, Telegram, API, dashboard, payments, journal, audit, runtime state, and MCP services. |
| 2026-08-15 | PostgreSQL cutover preflight started. | Added `core/storage/cutover_preflight.py` and storage tests. The command is read-only and fail-closed; it reports `BLOCKED` until PostgreSQL is configured and dual-read verification succeeds. |
| 2026-08-15 | Frozen snapshot rehearsal tooling completed. | Added `core/storage/frozen_snapshot.py` and `run_storage_snapshot.py`; SQLite files use the backup API, manifest hashes are verified before comparison, and storage coverage passed 10 tests. Cloud SQL comparison remains an external deployment step. |
| 2026-08-15 | Dashboard role boundary and dependency hardening completed. | Added client-side admin route protection and an access-denied surface; upgraded locked dashboard dependencies, cleared production npm audit findings, and verified `npm run lint`, `npm run build`, and the 331-test Python unit suite. |
| 2026-08-15 | CRT governance status reconciliation completed. | Updated `CRT_SOURCE_INTAKE.md` and `CRT_RESEARCH_LOG.md` to separate approved rulebook entries from pending or research-only claims; AUD-026 is verified. |
| 2026-08-15 | AUD-009 broker-aware live sizing hardening completed. | `AccountState` now marks live metadata as required; Risk Engine rejects missing/partial tick economics or volume constraints, while offline fallback remains explicit. USDJPY/XAUUSD, risk, scanner, and execution coverage passed 69 tests. |
