# ATLAX API Specification

Version: 1.0  
Status: Draft  
Purpose: Define typed contracts between ATLAX services.

---

## Contract Rule

Modules must communicate through typed contracts.

Avoid vague messages such as:

```text
CRT detected
```

Use structured payloads with stable field names, validation, and versioning.

---

## Detector Result Contract

Example shape:

```json
{
  "schemaVersion": "1.0",
  "detector": "CRTDetector",
  "symbol": "EURUSD",
  "timeframe": "M15",
  "detected": true,
  "confidence": 93,
  "timestamp": "2026-07-14T10:15:00Z",
  "reason": "UNKNOWN_UNTIL_RULEBOOK_DEFINED",
  "invalidation": "UNKNOWN_UNTIL_RULEBOOK_DEFINED",
  "metadata": {
    "parentHigh": 1.17425,
    "parentLow": 1.1718,
    "crtType": "UNKNOWN_UNTIL_RULEBOOK_DEFINED"
  }
}
```

This is a contract example, not CRT rule authority.

---

## Required Envelope Fields

All inter-service messages should include:

- `schemaVersion`
- `messageId`
- `createdAt`
- `source`
- `correlationId`
- `payload`

---

## TradingView Webhook Intake Contract

Official reference:

- https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/

TradingView webhook payloads must be treated as external, untrusted input.

ATLAX webhook intake must:

- Accept only documented webhook endpoints.
- Require authenticated requests using the approved security mechanism in `docs/16_SECURITY.md`.
- Prefer `application/json` payloads.
- Reject malformed JSON when JSON is expected.
- Reject plain-text payloads unless a versioned parser is explicitly documented.
- Validate every payload against the active webhook schema.
- Attach a server-side `receivedAt` timestamp.
- Generate or preserve a `correlationId`.
- Store the raw payload only according to security and retention policy.
- Normalize valid payloads into the standard ATLAX message envelope.
- Acknowledge accepted webhook requests quickly and perform downstream processing asynchronously.
- Log accepted, rejected, malformed, duplicate, unauthorized, and delayed payloads.

Accepted normalized webhook envelopes are durably queued before the HTTP
response is returned. The intake fingerprint is derived from the validated
TradingView payload, so a retried request is idempotent: it returns the first
canonical envelope and does not create a second queue item. The approved
shadow consumer claims `RECEIVED` items, validates the detector-event
contract, persists one immutable `OK` or `UNKNOWN` result, and marks the
message `PROCESSED` only after that result is durable. Handler failures are
retried according to the configured queue policy and then moved to
`DEAD_LETTER`; they must never be silently acknowledged.

The queue and approved shadow consumer are transport/validation boundaries.
They do not define detector rules, create trade candidates, calculate
confidence, approve risk, send alerts, or execute orders. TradingView events
must not influence those layers until a separate promotion is reviewed and
approved.

Minimum normalized webhook event fields:

```json
{
  "schemaVersion": "1.0",
  "messageId": "evt_01HYPOTHETICAL",
  "createdAt": "2026-07-14T10:15:00Z",
  "receivedAt": "2026-07-14T10:15:01Z",
  "source": {
    "platform": "TradingView",
    "scriptName": "UNKNOWN",
    "scriptVersion": "UNKNOWN",
    "settingsVersion": "UNKNOWN"
  },
  "correlationId": "corr_01HYPOTHETICAL",
  "payload": {
    "symbol": "EURUSD",
    "timeframe": "M15",
    "eventType": "detector_result",
    "detector": "CRTDetector",
    "detected": true,
    "eventTime": "2026-07-14T10:15:00Z",
    "metadata": {}
  }
}
```

This contract defines transport and normalization only. It does not define CRT rules, strategy direction, confidence scoring, risk, or execution behavior.

---

## Trade Candidate Contract

A strategy output may produce:

- `Trade Candidate`
- `No Trade`
- `UNKNOWN`

Trade candidates must include:

- Candidate ID.
- Source detector event IDs.
- Strategy name.
- Symbol.
- Timeframe context.
- Profile context.
- Direction only when authorized by strategy documentation.
- Entry model.
- Stop-loss model.
- Take-profit model.
- Confidence breakdown.
- Risk requirements.
- Explanation.

---

## API Governance

Final API schemas must be versioned, validated, and tested before implementation.

Unknown fields should fail closed unless an explicit migration policy allows them.

## ATLAX AI Context Contract

ATLAX AI requests use the following read-only context categories:

```json
{
  "schemaVersion": "1.0",
  "audience": "subscriber",
  "question": "What high-impact news is scheduled today?",
  "signalPerformance": {
    "periodDays": 30,
    "totalSignals": 0,
    "resolvedSignals": 0,
    "wins": 0,
    "losses": 0,
    "winRatePct": 0.0,
    "openSignals": 0,
    "bySymbol": {},
    "byProfile": {},
    "byTimeframe": {}
  },
  "heatmap": {"pairs": []},
  "news": [],
  "correlation": {"status": "UNKNOWN"},
  "publicFaq": {"status": "OK", "content": "reviewed public product FAQ"}
}
```

The context is aggregate and sanitized. `totalPnl`, account identifiers,
broker fields, credentials, exact lot sizes, and execution controls are not
subscriber contract fields. The FAQ content must remain public product
information and must not contain internal rulebooks, source code, credentials,
or private analytics. Missing or insufficient source data must remain
`UNKNOWN`; the AI must not fill it in.
`UNKNOWN`; the AI must not fill it in.

## Signal Passport Contract

The Signal Passport uses the Alert Engine's permanent alert_id as signal_id. A
public history record contains:

    schemaVersion: 1.0
    signalId: uuid
    pair: EURUSD
    style: day_trader
    timeframe: H1
    createdAt: 2026-08-09T10:15:00+00:00
    status: RESOLVED
    invalidationReason: ""
    finalOutcome: WIN
    outcomeAt: 2026-08-09T12:15:00+00:00

Passport performance aggregates separate total, open, resolved, wins, losses,
and unknown outcomes and are available by pair and style. They do not expose
account PnL, broker fields, lot sizes, or execution controls to subscriber
surfaces.

## Administrator Operations View

`GET /api/admin/operations` is an administrator-only, read-only endpoint. It
returns the latest sanitized operations snapshot, account-mode label, watchdog
component state, circuit-breaker state, reconciliation state, recent execution
quality facts, and bounded audit history. The `limit` query parameter is
bounded to 1--50.

The response must not contain credentials, tokens, wallet values, raw MT5
payloads, or execution-quality metadata. Missing runtime evidence is returned
as `UNKNOWN` or `snapshot_available: false`; the API must not manufacture
account, health, or performance values.

## Administrator Command Center Overview

`GET /api/admin/overview` is an administrator-only, read-only aggregation for
the dashboard command center. It composes existing deterministic stores and
runtime state into one bounded payload:

- journal performance metrics;
- heatmap, correlation, and daily signal telemetry;
- approval queue count;
- verified Prop-Firm control posture and selected profile summary;
- active subscriber and plan counts, including near-expiry access;
- payment status counts, confirmed revenue, and a short recent-intent list;
- account mode, watchdog, entry guard, reconciliation, loss-budget, account,
  execution-quality, and bounded audit summaries.

The overview is an observability contract, not a control surface. It must not
add mutation capabilities or duplicate payment, risk, execution, or approval
logic. Payment destinations, submitted transaction proofs, credentials, raw
configuration, and private provider payloads are excluded. Each dashboard
consumer must treat missing values as `UNKNOWN` or an empty state rather than
inventing a value.
