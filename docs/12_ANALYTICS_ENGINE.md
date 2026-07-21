# ATLAX Analytics Engine Specification

Version: 1.0  
Status: Draft  
Purpose: Define journaling, reporting, performance analytics, and advisory AI analysis boundaries.

---

## Responsibility

The Analytics Engine records what happened and explains performance.

It must support:

- Trade journal.
- Statistics.
- Performance reports.
- Screenshots.
- Monthly reports.
- Expectancy.
- Win rate.
- Risk metrics.
- Profile-level analytics.
- Timeframe-level analytics.

---

## Required Analytics Dimensions

Analytics must track:

- Profile.
- Instrument.
- Timeframe.
- Strategy.
- Candidate ID.
- Detector event IDs.
- Confidence score.
- Confidence breakdown.
- Confluence state.
- Risk settings.
- Execution status.
- Trade outcome.

---

## AI Advisory Analysis

AI may be used for advisory analytics only.

Allowed AI uses:

- Performance analysis.
- Trade journal analysis.
- Anomaly explanation after deterministic triggers.
- Market context summaries with sources.
- Configuration suggestions for trader review.
- Educational explanations.
- Backtesting report drafting.
- Documentation drafting.
- Natural language analytics answers.

AI must not:

- Detect CRTs.
- Define rules.
- Decide entries or exits.
- Manage risk.
- Modify configuration without approval.
- Execute trades.
- Generate live confidence scores without explicit approval.

AI data access is governed by `docs/16_SECURITY.md`.

---

## AI Advisory Use Cases

AI may support the following advisory workflows.

Performance analysis:

- Identify common traits in losing setups.
- Compare profile, timeframe, session, symbol, and pattern performance.
- Suggest areas for trader review using provided statistics.

Trade journal analysis:

- Categorize trader notes.
- Identify repeated execution or psychology mistakes.
- Summarize behavior patterns.

Anomaly explanation:

- Explain unusual performance shifts after deterministic anomaly triggers.
- Compare recent performance against historical baselines.
- Suggest possible investigation paths.

Market context research:

- Summarize relevant economic or news context.
- Cite sources when external facts are used.
- Return `UNKNOWN` when reliable context is unavailable.

Configuration suggestions:

- Suggest configuration changes for trader review only.
- Include statistical evidence and sample sizes.
- Identify exact configuration fields affected.
- Never apply changes automatically.

Education and documentation:

- Explain existing documented concepts.
- Draft reports and documentation.
- Answer natural language analytics questions from deterministic query results.

---

## AI Advisory Restrictions

AI advisory output must:

- Distinguish source data from interpretation.
- Include sample sizes for performance claims.
- Avoid unsupported causality claims.
- Avoid inventing missing rows, fields, metrics, or trading rules.
- Be logged where allowed by security policy.
- Use sanitized data only.

AI advisory output must not become source-of-truth trading logic unless reviewed and promoted through the engineering specification.

---

## Natural Language Analytics

Natural language analytics must use deterministic data retrieval first.

Flow:

```text
Trader question
  ->
Deterministic query planning
  ->
Permissioned data retrieval
  ->
AI formatting and explanation
  ->
Advisory answer
```

The AI formats and explains the result. It does not invent data.

---

## AI Advisory Open Questions

These must be answered before implementation:

1. Which AI provider, if any, is approved?
2. What data may be sent to an external AI service?
3. Must AI prompts and responses be stored for audit?
4. Are weekly AI performance reviews part of version 1?
5. What minimum trade count is required before AI performance analysis is useful?
6. Can AI draft configuration changes, or only describe them?
7. Who approves AI-suggested configuration changes?
8. Should market context use web search, economic-calendar APIs, or manually supplied data?
