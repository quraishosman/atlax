# ATLAX Security Specification

## Controlled AI Data Access Architecture

Version: 1.0  
Status: Proposed / Security Architecture  
Purpose: Define how AI tools may access ATLAX data through a controlled MCP gateway without receiving direct database, filesystem, broker, or execution access.

---

## Core Principle

AI may need data to analyze performance, explain results, or draft reports.

AI must access that data only through controlled, audited, least-privilege interfaces.

AI must never receive:

- Direct database credentials.
- Direct filesystem access.
- Broker credentials.
- Execution permissions.
- Raw SQL access.
- Configuration write access.
- Rulebook write access.

---

## Problem Without A Gateway

An insecure design gives an external AI service direct access to production systems.

```text
AI Provider
  |
  |  direct credentials
  v
Production Database
  - Trade history
  - Configurations
  - Risk parameters
  - Account data
  - Read/write access
```

This is forbidden because it creates:

- No enforceable permission boundary.
- No reliable audit trail.
- No request validation.
- No rate limiting.
- No data sanitization.
- Risk of accidental or malicious modification.
- Risk of exposing account, broker, or execution data.

---

## MCP Gateway Solution

ATLAX may use an MCP server as a controlled gateway between AI analysis tools and internal system data.

```text
AI Provider
  |
  |  approved MCP tools only
  v
MCP Server
  - Authentication
  - Tool allowlist
  - Request validation
  - Type checking
  - Rate limiting
  - Data sanitization
  - Audit logging
  - Timeout enforcement
  |
  |  read-only / compute-only internal tools
  v
ATLAX Data And Analytics Services
```

The MCP server is the permission boundary. Everything not explicitly exposed as an approved MCP tool is forbidden.

---

## Relationship To AI Analysis

The MCP gateway supports the AI advisory analysis workflows described in `docs/12_ANALYTICS_ENGINE.md`.

AI Analysis Layer:

- Summarizes performance.
- Explains historical outcomes.
- Analyzes journals.
- Drafts reports.
- Suggests configuration changes for review.

MCP Security Gateway:

- Controls what data the AI can request.
- Sanitizes responses before data leaves ATLAX.
- Logs every AI access.
- Prevents AI from modifying state.

AI remains advisory. MCP access does not grant trading authority.

---

## Security Architecture

```text
External AI Service
  - Stateless
  - No direct database access
  - No credentials stored for ATLAX internals
  |
  | HTTPS or approved encrypted transport
  v
MCP Server
  - API key authentication
  - Tool allowlist
  - Request schema validation
  - Rate limiting
  - Audit logging
  - Data sanitization
  - Response timeout
  |
  | Internal network or local-only access
  v
ATLAX Database / Analytics Store
  - Read-only MCP database user
  - Isolated permissions
  - No trade execution permissions
  - Query logging
  - Append-only audit log
```

Preferred deployment is local-only or private-network access. Public exposure requires explicit approval and additional security review.

---

## TradingView Webhook Security

Official reference:

- https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/

TradingView webhook intake is an internet-facing boundary when alerts are delivered from TradingView to ATLAX.

Requirements:

- Webhook endpoints must use HTTPS when publicly reachable.
- Webhook bodies must not contain broker credentials, account passwords, API keys, bot tokens, execution credentials, database credentials, or other secrets.
- Webhook authentication must be documented before deployment.
- Webhook authentication material must be configurable, rotatable, and redacted from logs.
- If IP allowlisting is used, the allowlist must be based on the current official TradingView webhook IP list at deployment time.
- Webhook requests must be schema-validated before they enter the event pipeline.
- Webhook requests must be rate-limited.
- Malformed, unauthorized, replayed, duplicate, or oversized webhook requests must fail closed.
- Webhook raw-body storage must follow retention and redaction policy.
- Webhook errors must not disclose secrets, internal paths, stack traces, broker details, or database details.

Webhook access grants detector-event submission only.

Webhook access must not grant:

- Trade execution.
- Risk override.
- Configuration write access.
- Rulebook write access.
- Broker access.
- Database access.
- Filesystem access.

---

## Allowed MCP Tool Categories

Allowed tools must be read-only or compute-only.

Approved categories:

- Read sanitized trade history.
- Read aggregated performance statistics.
- Read sanitized configuration.
- Read learning model statistics.
- Read anomaly summaries.
- Read MCP audit logs.
- Compute performance comparisons from approved data.
- Generate advisory summaries from approved data.

Allowed tools must not mutate trading state.

---

## Suggested Tool Allowlist

Initial proposed MCP tools:

| Tool | Access | Purpose |
| --- | --- | --- |
| `get_recent_trades` | Read-only | Return sanitized recent trades with capped limit. |
| `get_performance_stats` | Read-only / compute | Return aggregated performance metrics. |
| `get_configuration` | Read-only | Return sanitized settings relevant to analysis. |
| `get_pattern_analysis` | Read-only / compute | Return statistics for approved pattern labels. |
| `get_timeframe_comparison` | Read-only / compute | Compare performance by timeframe. |
| `get_session_analysis` | Read-only / compute | Compare performance by session. |
| `get_market_regime_analysis` | Read-only / compute | Compare performance by documented market regime. |
| `get_pair_comparison` | Read-only / compute | Compare performance by instrument. |
| `get_learning_model_stats` | Read-only | Return model version and readiness statistics. |
| `get_anomalies` | Read-only / compute | Return deterministic anomaly summaries. |
| `get_audit_log` | Read-only | Return MCP access history. |

These names are proposed. Final tool schemas belong in `docs/05_API_SPECIFICATION.md` before implementation.

---

## Forbidden MCP Tools

The MCP server must not expose tools that can:

- Execute trades.
- Place orders.
- Modify open trades.
- Modify configuration.
- Modify rulebooks.
- Modify risk settings.
- Delete trades.
- Delete audit logs.
- Access raw database connections.
- Execute raw SQL supplied by AI.
- Access broker credentials.
- Access account credentials.
- Access exact lot sizes unless explicitly approved for analytics.
- Access internal IDs unless explicitly required and sanitized.
- Read arbitrary files.
- Write files.
- Run shell commands.

If a requested tool could change trading behavior or system state, it is forbidden unless separately documented, reviewed, and approved.

---

## Data Sanitization

All MCP responses must be sanitized before leaving ATLAX.

Trade data may include:

- Public or configured pair symbol.
- Timeframe.
- Direction, only when direction is already documented as a strategy or trade result.
- Entry, stop, and target prices when needed for analytics.
- Entry and close timestamps.
- PnL or R multiple.
- Pattern labels.
- Confidence score and breakdown.
- Session.
- Market regime, if documented.
- Trade status.

Trade data must remove or mask:

- Account IDs.
- Broker account numbers.
- Broker server names.
- API keys.
- Bot tokens.
- Credentials.
- Raw internal database IDs when not needed.
- Exact lot sizes unless explicitly approved.
- Personally identifying trader data unless explicitly approved.

Configuration data may include:

- Enabled profiles.
- Timeframes.
- Confidence thresholds.
- Risk percentages.
- Session filters.
- Alert preferences without secrets.

Configuration data must remove or mask:

- Telegram bot tokens.
- Discord tokens.
- Email credentials.
- Webhook secrets.
- API keys.
- Broker credentials.

---

## Request Validation

Every MCP tool must validate its inputs.

Required validation:

- Type checking.
- Allowed enum values.
- Maximum limits.
- Date range caps.
- Pattern allowlists.
- Timeframe allowlists.
- Pair allowlists from configuration.
- Request timeout.

Example caps:

- `get_recent_trades.limit` must have a documented maximum.
- `get_anomalies.days` must have a documented maximum.
- Audit log reads must have a documented maximum.

Invalid requests must fail closed with:

- Error reason.
- Timestamp.
- Tool name.
- Recovery suggestion.

---

## Rate Limiting

The MCP gateway must rate-limit AI access.

Configuration should support:

- Requests per minute.
- Maximum concurrent requests.
- Request timeout seconds.
- Tool-specific limits for expensive queries.

Rate-limit events must be logged.

---

## Audit Logging

Every MCP request must be logged.

Audit log fields:

- Timestamp.
- Caller identity.
- Tool name.
- Sanitized parameters.
- Result summary.
- Request status.
- Error reason, if any.
- Duration.
- Model or AI provider identifier when available.

Audit logs must not store secrets.

Audit logs should be append-only and retained according to configuration.

---

## Database Access

MCP database access must use least privilege.

Requirements:

- Separate database user for MCP.
- Read-only access for analytics tables.
- Append-only access for MCP audit logs, if logs are stored in database.
- No write access to trades, execution, configuration, rulebooks, or risk settings.
- No administrative database permissions.
- Parameterized queries only.
- No raw SQL passthrough.

---

## Configuration Example

```yaml
MCP_SERVER:
  enabled: true
  url: "localhost:8000"
  auth_key: "${MCP_AUTH_KEY}"

  rate_limit:
    requests_per_minute: 100
    max_concurrent: 5
    request_timeout_seconds: 5

  audit:
    enabled: true
    log_path: "logs/mcp_audit.log"
    retention_days: 90

  available_tools:
    - get_recent_trades
    - get_performance_stats
    - get_configuration
    - get_pattern_analysis
    - get_timeframe_comparison
    - get_session_analysis
    - get_market_regime_analysis
    - get_pair_comparison
    - get_learning_model_stats
    - get_anomalies
    - get_audit_log

  forbidden_tools:
    - modify_configuration
    - execute_trade
    - place_order
    - modify_trade
    - delete_trades
    - access_raw_database
    - execute_sql
    - modify_rules
    - access_credentials
```

This is a configuration shape, not final schema authority. Final schema must be documented in `docs/13_CONFIGURATION.md` or `docs/05_API_SPECIFICATION.md` before implementation.

---

## AI Provider Integration

AI provider integration must use only approved MCP tools.

The system prompt for any AI analysis client must state:

- AI can read only through approved MCP tools.
- AI cannot modify trades, configuration, rulebooks, or risk settings.
- AI cannot execute trades.
- AI can suggest changes only for trader review.
- All requests are audited.

The AI client must not receive any fallback path to direct credentials.

---

## Deployment Phases

### Phase 1: MCP Server

- Define tool schemas.
- Implement authentication.
- Implement request validation.
- Implement data sanitization.
- Implement audit logging.
- Use a read-only database account.

### Phase 2: AI Analysis Integration

- Connect approved AI analysis clients through MCP.
- Test allowed tool calls.
- Test forbidden actions.
- Verify audit logs.
- Verify sanitized output.

### Phase 3: Monitoring

- Monitor access patterns.
- Review audit logs.
- Verify rate limits.
- Rotate MCP credentials.
- Review tool allowlist regularly.

---

## Testing Requirements

Before MCP is enabled, tests must prove:

- Forbidden tools are unavailable.
- Invalid parameters fail closed.
- Raw SQL cannot be executed.
- Secrets are redacted.
- Large requests are capped.
- Rate limits apply.
- Audit logs are written.
- Read-only database credentials cannot mutate state.
- AI cannot access execution or broker APIs through MCP.

---

## Open Questions

These must be answered before implementation:

1. Is MCP approved as the only AI data-access path?
2. Which AI providers may call the MCP server?
3. Should the MCP server be local-only, private-network, or remotely accessible?
4. What data fields are approved for AI analysis?
5. Are exact prices, PnL, and lot sizes allowed in AI responses?
6. What authentication mechanism is required?
7. What rate limits are acceptable?
8. Where should audit logs be stored?
9. How long should MCP audit logs be retained?
10. Who reviews MCP audit logs?
11. Should any MCP tool ever be allowed to draft a configuration patch without applying it?

Until these are answered, MCP remains a proposed security architecture and must not be treated as implemented behavior.

---

## Final Policy

MCP is a gate, not a privilege escalation path.

AI can only see what MCP deliberately exposes.

AI can only analyze sanitized data.

AI cannot modify ATLAX.

AI cannot execute trades.

AI cannot bypass the rulebook, configuration, risk controls, or audit trail.
