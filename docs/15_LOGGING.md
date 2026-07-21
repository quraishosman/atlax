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
- User approvals and rejections.
- Execution requests.
- MT5 order-check results.
- MT5 order-send results.
- MT5 return codes and broker comments.
- Trade updates.
- Configuration changes.
- Risk rejections.
- Errors.
- MCP access.
- AI advisory requests, where allowed.

---

## Error Fields

Every failure must include:

- Reason.
- Timestamp.
- Context.
- Recovery suggestion.

---

## Audit Requirements

Logs needed for audit must be append-only or tamper-evident where possible.

Sensitive values must be redacted.

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
