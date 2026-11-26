# ATLAX PostgreSQL Storage Architecture

Status: Active
Version: 1.0

## Target

ATLAX is moving from process-local SQLite and JSON files toward one
PostgreSQL-backed storage service shared by:

    ATLAX scanner and Telegram bot
                |
                v
    PostgreSQL: Cloud SQL in production
                |
                +-- API and web dashboard
                +-- payment records
                +-- subscribers and access state
                +-- Signal Passport and journal
                +-- approval records
                +-- audit events

Google Cloud SQL for PostgreSQL is the initial managed production target.
AlloyDB is not required for the current workload.

## Current Migration Phase

Phase 1 provides:

- validated storage backend configuration
- versioned PostgreSQL schema
- schema bootstrap
- idempotent import of current local files and SQLite databases
- a Docker PostgreSQL target for local migration testing

Local storage remains the default until the import and repository cutover are
verified. Do not set the live scanner to PostgreSQL while only part of the
application has been migrated.

## Configuration

Use these environment variables:

    ATLAX_STORAGE_BACKEND=local
    ATLAX_STORAGE_CUTOVER_ENABLED=false
    ATLAX_DATABASE_URL=
    ATLAX_DATABASE_SSLMODE=require
    ATLAX_DATABASE_CONNECT_TIMEOUT_SECONDS=10

For PostgreSQL, set the backend to postgres and provide a complete connection
URL. The URL is a secret-bearing value and must never be committed or logged.
Cloud SQL deployments should use require, verify-ca, or verify-full according
to the approved network and certificate design.

Runtime services also require `ATLAX_STORAGE_CUTOVER_ENABLED=true`. This
fail-closed gate allows schema bootstrap and migration verification while the
application remains local, then switches scanner, bot, API, payments,
approvals, journal, audit, runtime state, and MCP readers together.

## Local Migration Test

Start only the local PostgreSQL target:

    docker compose -f docker-compose.postgres.yml --env-file .env up -d postgres

Set the local environment to the database target, then create the schema:

    python -m core.storage.bootstrap

Import the current local state:

    python -m core.storage.migrate_local --data-dir data

Verify counts before any cutover:

    python -m core.storage.verify_migration --data-dir data

The importer is idempotent and does not delete or modify the source files.
Back up the data directory before every migration rehearsal.

## Imported Data

The importer maps:

- users.sqlite to auth_users
- subscribers.json to subscribers
- invite_codes.json to invite_codes
- payment_intents.json to payment_intents
- journal.sqlite to journal_entries
- admin_audit.jsonl to audit_events
- runtime JSON files to runtime_state

The local source remains the rollback copy until post-import counts and
selected record samples are verified.

## Cloud SQL Deployment

Create a PostgreSQL database in Google Cloud SQL and supply its connection
details through the runtime environment. Prefer a private IP or the Cloud SQL
Auth Proxy/connector rather than exposing the database publicly. Database
credentials belong in the deployment secret manager, never in YAML, source
control, Telegram messages, or AI prompts.

The scanner, API, and dashboard must use the same database URL after the
repository cutover. A deployment is incomplete if one component still writes
business data to local JSON while another writes PostgreSQL.

## Security Boundary

The application database user must be separate from the read-only MCP
analytics user. AI tools access approved sanitized repository methods only;
they never receive the PostgreSQL URL, raw SQL access, or direct database
credentials.

Required controls:

- TLS for remote connections.
- Least-privilege database roles.
- automated backups and tested restore procedures.
- connection timeout and pool limits.
- redacted database errors.
- audit events for administrative and lifecycle mutations.
- no storage of broker credentials or wallet private keys.

## Cutover Order

1. Create and secure the Cloud SQL instance.
2. Bootstrap the schema.
3. Import and verify local data.
4. Complete repository adapters for auth, subscribers, payments, journal,
   approvals, audit, and runtime state.
5. Run dual-read comparison in a non-production environment.
6. Stop writes briefly, run a final import, and verify counts.
7. Switch scanner, API, and dashboard together.
8. Keep the local backup read-only until rollback confidence is established.

Payment and subscription writes must not be switched by an implicit
environment-only shortcut. The cutover requires an explicit repository
release, a final import, and a rollback window.
