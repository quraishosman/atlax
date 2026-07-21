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
