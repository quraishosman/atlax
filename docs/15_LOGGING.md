# ATLAX Logging Specification

Version: 1.0  
Status: Draft  
Purpose: Define required logging and audit behavior.

---

## Logging Rule

Never silently ignore errors.

Everything important gets logged.

---

## Required Logs

ATLAX must log:

- Webhook accepts and rejects.
- Webhook authentication failures.
- Webhook schema validation failures.
- Webhook duplicate or replay rejections.
- Detector results.
- Strategy decisions.
- Confidence breakdowns.
- Alerts.
- Alert delivery retryable states when no enabled channel confirms delivery.
- User approvals and rejections.
- Approval consumer claims, execution-ready releases, and terminal execution
  outcomes.
- Execution requests.
- MT5 order-check results.
- MT5 order-send results.
- MT5 return codes and broker comments.
- Trade updates.
- Configuration changes.
- Risk rejections.
- Adaptive lot-target selections and rejections, including symbol, loss tier,
  realized daily-loss percentage, target lot, and broker-derived risk-safe lot.
- Errors.
- MCP access.
- AI advisory requests, where allowed.
- Signal Passport creation, outcome, expiry, and query failures.
- FVG Signal Passport registration and persistence failures.
- H1 momentum shadow evaluations, including bias, relationship, run length,
  base score, proposed score, adjustment, and configuration snapshot.
- CRT quality-tightening shadow evaluations, including policy version, status,
  direction, ratios, stable reason codes, current detector result, and
  counterfactual suppression result.
- FVG watch dispatch failures and unavailable-adapter fallbacks, including
  candidate ID, symbol, timeframe, and bounded error context.
- Market-closed cycle skips, including UTC day, schedule timezone, and
  configured trading weekdays.
- Approval payload quarantine events, including candidate ID, previous status,
  bounded validation reason, and UTC timestamp. Raw payloads and price fields
  must not be logged.
- Signal replay capture success, skipped/unknown reasons, initialization
  failures, and repository failures. Logs include only signal ID, snapshot ID,
  symbol, timeframe, and a bounded reason; raw candle payloads must not be
  logged.
- Signal replay outcome synchronization, including appended-candle counts,
  authoritative terminal outcomes, expiry, unknown states, and terminal
  consistency conflicts. Raw candle payloads and private account data must not
  be logged.
- Signal replay authorization decisions, request cooldowns, render success or
  typed failure reasons, cache events, and Telegram replay delivery results.
  Logs may include bounded signal ID, chat ID, byte count, and latency, but
  must never include raw candles, chart payloads, bot tokens, or recipient
  authorization lists.
- Signal replay aggregate report success, unavailable states, period, and
  bounded coverage counts. Reports must not log signal IDs, raw prices, or
  recipient dimensions.
- New-entry circuit-breaker evaluations and transitions, including state,
  machine-readable reason code, bounded reason, allow/block result, and
  notification failures. Circuit-breaker logs must never include credentials,
  secrets, raw broker payloads, or private payment data.
- Runtime-state health-check failures and operational-metrics persistence
  failures, with bounded error context and a recovery suggestion.
- Trade reconciliation start, completion, unknown state, conflict, missing
  ledger record, and missing broker state events. Logs may include bounded
  execution or broker identifiers and safe counts, but must not include raw
  broker payloads or credentials.
- Prop-Firm profile monitor transitions, including baseline approval, stale
  status, changed-version review requirements, invalid or missing profiles,
  and monitor persistence failures. Logs may include profile ID, version,
  content hash, source retrieval timestamp, status, and bounded reason, but
  must never include credentials or raw external provider payloads.
- Trading-account registry validation failures, selected-target reconnect
  requests, and selected-terminal identity mismatches. Audit records may
  contain a saved profile ID and actor only; they must never contain MT5
  passwords, a raw Telegram setup reply, or broker credentials.

---

## Error Fields

Account-mode runtime must emit structured events for account identity reads,
mode changes, binding verification, profile/context readiness, account
mismatches, Prop-Firm entry blocks, and Prop-Firm entry resizes. These events
may include secret-free account fingerprints, bounded login/server display
fields where operationally required, mode, profile ID, profile version, status,
and reason. They must never include passwords, API keys, payment secrets, or
raw account credentials.

Every failure must include:

- Reason.
- Timestamp.
- Context.
- Recovery suggestion.

---

## Audit Requirements

Logs needed for audit must be append-only or tamper-evident where possible.

Sensitive values must be redacted.

### Prop-Firm Control Events

The Telegram Prop-Firm control surface must record these operational audit
events with the actor, result, timestamp, selected profile ID/version where
available, and a short reason for denied actions:

- `prop_firm_menu_view`
- `prop_firm_profiles_view`
- `prop_firm_profile_selected`
- `prop_firm_enable`
- `prop_firm_disable`
- `prop_firm_pause`
- `prop_firm_resume`
- `prop_firm_shadow_report_view`

Audit metadata must contain only non-sensitive identifiers and decision
context. It must never include broker passwords, API keys, payment secrets,
or raw account credentials. A denied or malformed callback is logged before
the user receives the fail-closed response.

Prop-Firm shadow validation must emit:

- `prop_firm_shadow_evaluated` for a completed comparison, with category and
  approval booleans but no credentials.
- `prop_firm_shadow_unknown` when a context, profile, account snapshot, or
  shadow evaluation is unavailable.
- `prop_firm_shadow_observer_failed` when the observer itself fails; this must
  never interrupt the live risk or execution path.

Shadow records are observational evidence. They must not be interpreted as
live blocks or lot-size changes until a separately approved rollout enables
the deterministic Prop-Firm context in the Risk Engine.

---

## Platform Boundary Logs

TradingView webhook logs must include:

- Receive timestamp.
- Source platform.
- Source script version, when supplied.
- Settings version, when supplied.
- Symbol.
- Timeframe.
- Schema version.
- Correlation ID.
- Validation status.
- Rejection reason, if rejected.
- Processing latency.
- Queue status, including `RECEIVED`, `PROCESSED`, retryable failure, or
  `DEAD_LETTER`.
- Duplicate fingerprint decisions without logging raw secrets or payloads.
- Shadow consumer status (`OK` or `UNKNOWN`) and a sanitized reason.

MT5 execution logs must include:

- Candidate ID.
- Approval record ID.
- Profile.
- Symbol.
- Timeframe context.
- Risk snapshot ID.
- Sanitized request summary.
- Order-check status.
- Order-send status.
- Return codes.
- Broker comment after redaction.
- Final reconciliation status.

Execution-quality events must also include:

- Quality event ID and event type.
- Execution record ID, candidate ID, profile, symbol, and timeframe.
- Requested and actual price/volume when available.
- Spread, slippage, stop-loss, take-profit, and latency when available.
- Protection or circuit-breaker action, status, and bounded reason code.
- Settlement status, gross PnL, costs, net PnL, and close reason when broker
  reconciliation is complete.

Unknown broker facts must remain explicitly unknown. Quality logs may include
safe identifiers and bounded numeric execution facts, but must never include
raw MT5 payloads, credentials, tokens, wallet secrets, or unredacted private
account data.

Watchdog logs must include the event name (`watchdog_heartbeat` or
`watchdog_failure`), overall status, consecutive-failure count, component
statuses, and whether a notification was sent. Component reasons and last
successful timestamps are bounded and secret-free. A watchdog failure is an
operations signal only; it must not be interpreted as an automatic scanner
restart or as a replacement for the circuit breaker.
Unchanged failure signatures are notification-deduplicated by default. Any
periodic reminder mode must be explicitly configured and remains governed by
the notification cooldown.
Watchdog Telegram notices are tracked by chat/message ID and configured expiry
only; their message text is not persisted. Expired notices are deleted during
the next heartbeat or MT5 reconnect poll, including after a scanner restart.

Administrator operations views must record these audit actions:

- `admin_operations_snapshot_view`
- `admin_execution_quality_view`

These records identify the actor, result, and view action only. The dashboard
operations endpoint is read-only and must use the same sanitized fields as the
Telegram surface.
