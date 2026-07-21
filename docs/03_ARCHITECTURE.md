# ATLAX Architecture Specification

## Multi-Timeframe Trading Intelligence Framework

Version: 1.0  
Status: Active, with open questions noted  
Purpose: Define the system architecture for multi-timeframe detection, profile-aware routing, alerting, execution handoff, and analytics.

---

## Architectural Requirement

ATLAX must support multiple CRT detections on the same instrument across different timeframes at the same time.

Price action is fractal. A single pair can produce separate valid events on M5, M15, H1, and H4. These events may belong to different trader profiles and must be routed, scored, alerted, executed, and journaled with profile and timeframe context.

This document defines system flow and boundaries. It does not define CRT detection rules. CRT behavior belongs in `docs/rulebooks/CRT_RULEBOOK.md`.

---

## Single-Timeframe Limitation

A limited design treats one pair as one signal:

```text
EURUSD H4 CRT -> Alert -> One user signal
```

ATLAX must instead support simultaneous profile-aware events:

```text
EURUSD at the same moment:

M5  CRT detected -> scalper context
M15 CRT detected -> scalper or day-trader context
H1  CRT detected -> day-trader or swing context
H4  CRT detected -> swing or position context
```

The system must preserve each event independently. It must not collapse all timeframes into one undifferentiated alert.

---

## Profile Model

Trader profiles are configurable. See `docs/13_CONFIGURATION.md` for the canonical settings model.

Default profile intent:

| Profile | Holding Style | Typical Timeframes | Alert Style |
| --- | --- | --- | --- |
| Scalper | Minutes | M1, M5, M15 | Every configured valid CRT event |
| Day Trader | Hours | M15, M30, H1 | Filtered by configured confluence |
| Swing Trader | Days or weeks | H1, H4, D1 | Strict confluence and optional HTF bias |

These are configuration defaults, not hardcoded strategy rules.

---

## System Flow

```text
TradingView
  |
  |  One configured detector instance per timeframe
  v
Webhook Intake
  |
  |  Normalized detection events
  v
Market Data / Event Store
  |
  v
Detector Output Registry
  |
  v
Strategy Engine
  |
  |  Trade Candidate or No Trade
  v
Confidence Engine
  |
  |  Configurable scoring
  v
Alert Router
  |
  |  Profile-aware alerts and approval flow
  v
Execution Handoff
  |
  |  MT5 only, if approved and enabled
  v
Trade Management
  |
  v
Analytics and Journal
```

---

## TradingView Role

TradingView may run one script per timeframe or use an approved multi-timeframe design.

Each event must include:

- Instrument
- Timeframe
- Event timestamp
- Pattern identifier
- Detector confidence, if documented
- Detector reason
- Invalidation information, if documented
- Metadata needed for downstream validation

Detector payloads must not return `BUY` or `SELL`. Directional trade decisions belong downstream in the documented strategy flow.

If a TradingView alert carries a trade direction in the future, that alert must be classified as a strategy output, not a raw detector output, and the governing rulebook must explicitly allow it.

### TradingView Platform Constraints

Official TradingView references:

- Webhook alerts: https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/
- Pine Script alerts: https://www.tradingview.com/pine-script-docs/concepts/alerts/

Platform facts that affect ATLAX architecture:

- TradingView sends webhook alerts as HTTP POST requests to the configured URL.
- If the alert message is valid JSON, TradingView sends it with an `application/json` content type; otherwise it sends `text/plain`.
- TradingView webhook processing has a documented timeout, so ATLAX webhook intake must acknowledge quickly and move expensive processing to asynchronous workers.
- TradingView webhook delivery can fail, so ATLAX must not treat webhook delivery as guaranteed.
- Pine Script code can create alert events, but running alerts are created from the TradingView chart UI.
- Running TradingView alerts store a snapshot of the script, inputs, symbol, and timeframe at creation time. Configuration changes that affect TradingView alerts require alert regeneration or another documented synchronization method.
- TradingView alerts operate on realtime alert events. If ATLAX requires closed-candle behavior, the Pine detector must encode that explicitly and the rulebook must authorize it.

Architecture impact:

- Webhook intake must validate content type and schema before accepting an event.
- Webhook intake must preserve raw payloads for audit and replay when allowed by security policy.
- Alert deployment must be treated as configuration state, not an invisible manual step.
- Any TradingView detector instance must identify its symbol, timeframe, script version, and settings version in the payload.

---

## Webhook Intake

Webhook intake normalizes incoming events.

Responsibilities:

- Validate payload schema.
- Reject malformed payloads.
- Attach receive timestamp.
- Preserve source timeframe.
- Preserve source platform.
- Log accepted and rejected payloads.
- Store enough context for replay and audit.

Webhook intake must not make trading decisions.

---

## Profile-Aware Routing

The alert router must route events by configured profile.

Example routing intent:

```text
If Scalper profile is enabled:
  evaluate configured scalper timeframes

If Day Trader profile is enabled:
  evaluate configured day-trader timeframes and confluence requirements

If Swing Trader profile is enabled:
  evaluate configured swing timeframes, confluence requirements, and HTF bias settings
```

Routing must be driven by configuration. No profile timeframe, threshold, confluence requirement, or risk setting may be hardcoded.

---

## Multi-Timeframe Confluence

ATLAX must support multi-timeframe confluence as a configurable quality input.

Confluence may consider:

- Same instrument.
- Same profile.
- Timeframe group.
- Detection recency.
- Alignment state.
- Higher-timeframe bias, when enabled by configuration.
- Minimum aligned timeframe count.
- Deduplication windows.

The exact scoring weights and directional interpretation are not defined here. They must be documented in the appropriate rulebook or configuration schema before implementation.

Any example values such as `+25` for HTF alignment, `+10` for mid-timeframe alignment, or caps at `100` are placeholders until documented as authoritative configuration.

---

## Alert Deduplication

ATLAX should prevent alert spam without hiding important events.

Deduplication must be configurable.

Supported concepts:

- Minimum seconds between alerts for the same pair.
- Group alerts in the same direction when direction is documented.
- Group related timeframe events into one profile-aware message.
- Preserve all raw events in logs even when user notifications are deduplicated.

Deduplication must affect notification behavior only. It must not delete audit history.

---

## User Approval Flow

Execution must default to manual approval unless configuration and documentation explicitly allow auto-execution.

A profile-aware alert should allow the user to choose:

- Approve scalper candidate.
- Approve day-trader candidate.
- Approve swing-trader candidate.
- Reject all.

Approval must preserve:

- Profile
- Timeframe
- Instrument
- Candidate ID
- Risk settings used
- Source event IDs
- User decision timestamp

---

## MT5 Execution Handoff

MT5 receives approved execution requests only.

Execution requests must include:

- Profile
- Instrument
- Timeframe context
- Entry model
- Stop-loss model
- Take-profit model
- Risk settings
- Candidate ID
- Approval status

MT5 must:

- Use profile-specific risk configuration.
- Calculate lot size from settings.
- Enforce risk limits.
- Log which profile generated the trade.
- Refuse execution if required configuration is missing or invalid.

MT5 must not perform complex detection.

---

## Analytics Requirements

Analytics must track results by:

- Profile
- Instrument
- Timeframe
- Strategy
- Candidate ID
- Detector event IDs
- Confidence score
- Confluence state
- Risk settings
- Execution status
- Trade outcome

This enables separate performance reporting for scalper, day-trader, and swing-trader workflows.

---

## Learning Engine Extension

ATLAX may support an explainable Learning Engine as a future/advisory extension.

Learning and adaptive-confidence boundaries are documented in `docs/09_CONFIDENCE_ENGINE.md`.

It may analyze closed trades, setup metadata, confidence breakdowns, profiles, timeframes, sessions, symbols, and market regimes to produce historical performance evidence.

It must not:

- Define CRT rules.
- Change detector behavior.
- Execute trades.
- Override risk controls.
- Influence live alerts or execution until explicitly approved.

When enabled, learning output must be explainable, versioned, validated, and reproducible from stored data.

---

## AI Analysis Layer Extension

ATLAX may support an AI Analysis Layer for advisory analysis, documentation, reporting, education, and natural language analytics.

The AI Analysis Layer is documented in `docs/12_ANALYTICS_ENGINE.md` and governed by `docs/16_SECURITY.md`.

It must remain outside the core trading decision path.

AI may:

- Summarize performance.
- Analyze journal notes.
- Explain anomalies after deterministic triggers.
- Draft reports and documentation.
- Suggest configuration changes for trader review.
- Format answers to analytics questions.

AI must not:

- Define CRT rules.
- Detect patterns.
- Decide entries or exits.
- Generate live confidence scores unless separately approved.
- Modify configuration without approval.
- Manage risk.
- Execute trades.

---

## MCP Security Gateway Extension

ATLAX may use an MCP server as the controlled gateway between AI analysis clients and internal ATLAX data.

The MCP security model is documented in `docs/16_SECURITY.md`.

The MCP gateway must:

- Expose only approved read-only or compute-only tools.
- Validate every request.
- Sanitize every response.
- Rate-limit AI access.
- Audit every tool call.
- Use least-privilege internal credentials.
- Prevent direct database, filesystem, broker, execution, configuration-write, and rulebook-write access.

MCP access does not grant trading authority. It supports advisory AI analysis only.

---

## Required Components

### TradingView Components

- Timeframe-aware CRT detector deployment.
- JSON webhook payloads with timeframe context.
- Configurable alert generation.

### Python Bot Components

- Webhook intake.
- Event normalization.
- Profile router.
- Confluence evaluator.
- Alert deduplication.
- Approval workflow.
- Configuration service integration.
- Audit logging.

### MT5 Components

- Approved trade receiver.
- Profile-aware risk calculation.
- Profile-aware execution settings.
- Trade modification support.
- Trade journal logging.

### Configuration Components

- Canonical settings schema.
- Profile configuration.
- Confluence configuration.
- Alert routing configuration.
- Deduplication configuration.
- Validation and live reload.

### Learning Components

- Historical setup and trade dataset.
- Explainable statistical model.
- Model validator.
- Model registry and versioning.
- Feedback loop from closed trades.
- Confidence explanation output.

### AI Analysis Components

- Advisory analysis interface.
- Prompt and response audit logging.
- Data redaction and permission controls.
- Report generation workflow.
- Trader approval workflow for suggested configuration changes.

### MCP Security Components

- MCP server gateway.
- Tool allowlist.
- Request validator.
- Rate limiter.
- Data sanitizer.
- Audit logger.
- Read-only analytics database credentials.
- Forbidden-action test suite.

---

## Open Questions

These must be answered before implementing profile routing or confluence behavior:

1. Should alerts always require approval, or can any profile auto-execute?
2. Which profiles are active for the first version: scalper, day trader, swing trader, or all three?
3. Should each profile alert on any valid CRT, or only when multiple timeframes align?
4. What is the maximum acceptable alert rate per day per pair and per profile?
5. Should scalper events check H1 or D1 bias, or ignore higher-timeframe bias?
6. Which confluence scoring weights are authoritative?
7. Which document owns non-CRT confluence rules: `docs/rulebooks/CRT_RULEBOOK.md`, `docs/13_CONFIGURATION.md`, or a future strategy rulebook?
8. Is the Learning Engine advisory only, or may it influence alert thresholds after validation?
9. Which document owns the adaptive confidence formula?
10. Which AI provider and data-sharing policy are approved for advisory analysis?
11. Is MCP approved as the only AI data-access path?

Until these are answered, implementation must return `UNKNOWN` for unspecified behavior instead of guessing.

---

## Boundary Reminder

This architecture allows multiple timeframe events to exist at once and be routed by profile.

It does not authorize:

- Inventing CRT rules.
- Hardcoding confluence scoring.
- Returning `BUY` or `SELL` from detector outputs.
- Mixing alerting with execution.
- Executing without risk validation.
- Dropping events from the audit trail.
