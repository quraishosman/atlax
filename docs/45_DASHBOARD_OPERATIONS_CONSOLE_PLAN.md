# ATLAX Dashboard Operations Console

**Version:** 1.0  
**Status:** Implemented baseline  
**Owner:** ATLAX Engineering

## Purpose

The administrator dashboard is an operations console for the existing ATLAX
trading operating system. It is not a second strategy engine and it does not
create new trading rules. Its job is to make already-approved backend state
easy to scan: market context, signal flow, execution quality, risk posture,
subscriber access, and payment lifecycle.

## Implemented Surface

The navigation is grouped into:

- **Control plane:** Command Center, Market Intelligence, Signal Journal.
- **Decision flow:** Approval Queue, Execution & Risk.
- **Commercial:** Subscribers & Access, Billing & Revenue.
- **System:** System Configuration and session termination.

The Command Center shows live-data cards for scanner/watchdog state, entry
guard, active access, performance metrics, account mode, loss buffers, heatmap
radar, approvals, billing flow, recent execution-quality events, and daily
signal telemetry. Market Intelligence shows persisted heatmap and correlation
state. Billing & Revenue shows safe payment lifecycle summaries and plan mix.

## Read-Only API Contract

`GET /api/admin/overview` is admin-only and returns `schema_version: 1`. It is
assembled from the existing journal, runtime state, payment store, subscriber
database, approval engine, and sanitized operations view. It is bounded for
dashboard use and refreshes on a short polling interval.

The contract excludes wallet destinations, submitted transaction proofs,
credentials, JWT values, raw broker payloads, raw configuration, and payment
provider secrets. The endpoint does not approve payments, change risk mode,
modify subscribers, or execute trades.

## Visual Rules

- Dense, scan-friendly operational layout with no marketing hero screen.
- Status, risk, and delivery state use clear color and text labels together.
- Empty or unavailable backend data is shown as an explicit empty state.
- Existing admin authentication and cookie session boundaries remain required.
- Controls link to existing audited workflows instead of duplicating mutations.

## Validation

- `npm run build` must pass for the dashboard bundle.
- API route tests must verify the endpoint rejects unauthenticated and regular
  users and that payment proofs and destinations are absent.
- `git diff --check` must pass.
- Visual verification should be performed at desktop and narrow viewport sizes
  after each substantial layout change.

## Future Extensions

Replay Lab, detailed signal-passport analytics, economic-calendar detail, and
prop-firm control actions may be added only after their read contracts and
mutation boundaries are documented. This baseline intentionally displays only
data already available through approved stores and runtime state.
