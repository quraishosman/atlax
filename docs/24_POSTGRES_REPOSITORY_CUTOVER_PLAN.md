# ATLAX PostgreSQL Repository Cutover Plan

Status: Active
Version: 1.0

## Objective

Move ATLAX's shared business state from process-local JSON/SQLite stores to
PostgreSQL without partial cutovers, silent data loss, or inconsistent reads
between the scanner, Telegram bot, API, and dashboard.

The local stores remain the rollback copy until every business repository has
been migrated and the comparison gates below pass.

## Non-Negotiable Safety Rules

- Do not switch the live backend through an environment variable while only
  some repositories have PostgreSQL support.
- Do not synchronize a full local snapshot by deleting or replacing remote
  rows. Payment and subscription state must be updated row by row.
- Do not overwrite a newer PostgreSQL row with an older local snapshot.
- Do not expose the PostgreSQL URL, raw SQL, or database credentials to AI or
  subscriber-facing code.
- Keep local files and SQLite databases read-only during the final cutover
  window; retain them for rollback until restore confidence is established.
- Every mutation must be auditable and every failed storage operation must fail
  closed at the caller boundary.

## Phases

### Phase 1: Foundation - complete

- Define backend configuration and TLS settings.
- Create the PostgreSQL schema and Docker migration target.
- Add schema bootstrap, local import, and count verification tools.
- Keep `ATLAX_STORAGE_BACKEND=local` as the default.
- Keep `ATLAX_STORAGE_CUTOVER_ENABLED=false` until migration verification and
  staging dual-read checks are complete.

### Phase 2: Explicit Repository Adapters - complete

- Add direct, transaction-scoped PostgreSQL repositories for auth, subscribers,
  invite codes, payments, journal/Signal Passport, approvals, audit, and
  runtime state.
- Use stable primary keys and JSONB only for record fields that are already
  part of the documented local contract.
- Protect mutable rows with an `updated_at` monotonic-write guard.
- Keep adapters independently testable and select them through the storage
  factory. Local mode remains the default and preserves the existing JSON and
  SQLite files through compatibility adapters.

Exit gate: adapter tests pass, SQL is parameterized, and no adapter has a
delete-or-replace operation for payment or subscription state. This gate is
complete.

### Phase 3: Dual-Read Verification

- Run the importer against a database clone, never production first.
- Compare counts, primary-key sets, and canonical record fingerprints for
  subscribers, payments, journal entries, audit events, and runtime state.
- Compare selected payment status histories and Signal Passport outcomes by
  permanent ID.
- Report `UNKNOWN` when a source cannot be read; never convert an unavailable
  source into an empty successful result.

Exit gate: zero mismatches in two consecutive rehearsals from the same frozen
local snapshot.

### Phase 4: Repository Wiring - complete

- Inject repository interfaces into the scanner, Telegram bot, API, dashboard
  services, payment service, subscription lifecycle, Signal Passport queries,
  approval engine, and MCP read-only analytics gateway.
- Keep local and PostgreSQL implementations behind the same documented
  interface.
- Add an explicit deployment release flag that refuses startup if any required
  repository is still local while PostgreSQL is selected.

Exit gate: all business writes in a staging run land in PostgreSQL and all
components read from the same database. The application callsites are wired
through the factories; the staging evidence still must be collected before
enabling the release flag.

### Phase 5: Controlled Cutover

1. Freeze or pause business writes.
2. Take a final local backup and run the final import.
3. Run dual-read verification and inspect the report.
4. Switch scanner, bot, API, and dashboard together.
5. Run smoke tests for signup, subscriber access, payment approval, signal
   history, approval queue, and audit history.
6. Keep local state read-only during the rollback window.

Exit gate: operational sign-off and a tested rollback procedure.

## Runtime Activation Sequence

The code cutover is implemented but remains fail-closed until a PostgreSQL
database has been provisioned, imported, and verified. Use the following
sequence in a staging environment, with the values supplied through the
deployment secret manager rather than committed files:

```text
ATLAX_STORAGE_BACKEND=postgres
ATLAX_STORAGE_CUTOVER_ENABLED=false
ATLAX_DATABASE_URL=postgresql://...
```

1. Initialize the schema with `python -m core.storage.bootstrap`.
2. Import the frozen local snapshot with `python -m core.storage.migrate_local --data-dir data`.
3. Capture a frozen local copy with
   `python run_storage_snapshot.py --data-dir data --snapshot-dir data/cutover_snapshot`.
4. Run `python -m core.storage.verify_migration --data-dir data` and inspect the report.
5. Run `python -m core.storage.cutover_preflight --data-dir data --snapshot-dir data/cutover_snapshot`; it must
   report a successful read-only comparison and no configuration blockers.
6. Complete the dual-read rehearsals described in Phase 3 using the same
   verified snapshot.
7. Set `ATLAX_STORAGE_CUTOVER_ENABLED=true` and restart scanner, bot, API,
   payment, and dashboard processes together.
8. Run the Phase 5 smoke tests and retain the local stores unchanged during
   the rollback window.

If `ATLAX_STORAGE_BACKEND=postgres` is set while the cutover flag is false,
runtime factories refuse to start. This prevents a partial deployment from
silently mixing local and PostgreSQL state.

### Phase 6: Managed Cloud Operation

- Deploy PostgreSQL to Google Cloud SQL with TLS, backups, and least-privilege
  roles.
- Keep application and read-only MCP database users separate.
- Use secret management for connection strings and credentials.
- Test restore and failover procedures before treating Cloud SQL as the only
  recovery source.

## Current Deliverables

- `core/storage/repositories.py` contains the explicit adapter boundary.
- `core/storage/dual_read.py` provides read-only fingerprint comparison.
- `core/storage/frozen_snapshot.py` captures SQLite-safe, manifest-verified
  local state for repeatable rehearsals.
- `core/storage/cutover_preflight.py` provides a read-only fail-closed go-live
  check that combines configuration checks with dual-read verification.
- `docs/decisions/ADR-009.md` records why the cutover is staged.

The application code is wired for both backends. The repository remains in
local mode until Cloud SQL credentials, migration verification, and operational
sign-off are available.
