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
- Signal Passport history and lifecycle reporting.

## Analytics Failure Semantics

Analytics and MCP query results must distinguish an empty valid result from an
unavailable data source. Aggregate tools return `status: "OK"` only after a
successful storage query. Missing journals, query failures, malformed storage,
or unavailable required context return `status: "UNKNOWN"` with a safe reason;
they must not be represented as zero trades, a zero win rate, or an empty
successful list. Advisory AI must stop or label the affected context as
`UNKNOWN` rather than infer that no activity occurred.

The Telegram Passport history query retains its legacy list return contract for
internal callers but also supports a status-bearing result for user-facing
surfaces. The API approval queue returns HTTP 503 when its storage read fails.
Partial market-context enrichment is marked `UNKNOWN` when any journal row
cannot be enriched, even when other rows were valid.

## Signal-Level Outcome PnL

The Signal Tracker follows alert levels and does not represent a subscriber's
broker account or the execution engine's cash PnL. For a resolved signal it
stores the deterministic price outcome as an R multiple:

```text
risk_distance = abs(entry_price - stop_loss_price)

BUY:  pnl_r = (exit_price - entry_price) / risk_distance
SELL: pnl_r = (entry_price - exit_price) / risk_distance
```

The result is rounded to four decimal places and is recorded with the journal
and Google Sheets feedback payload as `pnl_r`. A signal with a missing,
non-finite, or zero entry-to-stop reference remains unresolved and emits an
`signal_outcome_unknown` event; ATLAX must not substitute a guessed PnL value.
Actual account-currency PnL remains an execution-layer responsibility and is
not exposed through subscriber analytics.

## Execution-Side Cash PnL Reconciliation

When broker economics are available, execution-side cash PnL is calculated from
the actual fill prices and broker tick values:

```text
BUY gross = (exit - entry) / tick_size * tick_value * volume
SELL gross = (entry - exit) / tick_size * tick_value * volume
net PnL = gross + swap + commission + fee
```

For an already closed MT5 position, broker-reported exit-deal `profit`, `swap`,
`commission`, and `fee` values are authoritative. Only matching ATLAX magic,
order, and position context is eligible. Missing or malformed matching exit
deals return `UNKNOWN`; open positions must not be reported as realized PnL.
This cash result is separate from subscriber-facing signal-level `pnl_r`.

Execution attempts are joined to Signal Passport rows by `candidate_id`. The
execution ledger is authoritative for execution status and broker identifiers;
the Signal Passport remains authoritative for the public signal lifecycle and
price-based outcome. A filled execution therefore does not imply a resolved
win, and a signal outcome does not imply that a broker trade was executed.
The execution ledger's private settlement fields are authoritative for broker
cash PnL after matching exit deals are available. They are not included in
subscriber Telegram alerts, ATLAX AI context, or public Google Sheets
feedback.

The execution-quality journal is the operational companion to that ledger. It
keeps broker-fill, position-link, protection-action, circuit-breaker, and
settlement facts separate from setup-quality and subscriber signal outcomes.
Analytics may compare setup quality with execution quality, but it must not
rewrite detector rules, confidence rules, risk settings, or execution policy
from those observations automatically. Missing execution facts remain
`UNKNOWN`.

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

## ATLAX AI Surface

ATLAX AI is a separate advisory surface from the admin execution assistant.
Subscribers receive signals only; ATLAX does not manage their broker account,
balance, PnL, lots, risk settings, entries, exits, or orders.

ATLAX AI may answer:

- Public aggregate ATLAX signal results, including resolved wins, losses, open-signal counts, win rate, pair, profile, and timeframe sample sizes.
- The configured ATLAX heatmap snapshot.
- Upcoming high-impact economic-calendar events from the configured calendar feed.
- Correlation between two configured pairs when enough aligned market history is available.
- General trading education and explanations of documented ATLAX behavior.

ATLAX AI must refuse or return `UNKNOWN` for:

- Account balance, private PnL, lot size, broker details, or execution state.
- Personalized stop-loss, take-profit, position-size, risk, entry, or exit instructions.
- Guaranteed predictions or claims that a future trade will win.
- News or fundamental claims when the configured source is unavailable.
- Any request to place, modify, pause, or manage a trade.

The deterministic flow is:

```text
Subscriber question
  -> subscriber permission and daily-limit check
  -> sanitized MCP analytics and market-context retrieval
  -> configured AI provider
  -> Telegram-safe advisory answer
```

The subscriber context sent to the configured external AI provider contains
only the approved aggregate signal results, replay coverage and outcome
groupings, heatmap values, calendar events, correlation result, and the
reviewed public FAQ. Replay context is diagnostic aggregate data; it must not
be treated as a prediction or causal explanation. It never contains account
credentials, broker data, subscriber identity, private execution-side PnL,
raw replay candles, price levels, signal IDs, recipient IDs, or internal
strategy and implementation details. An unavailable replay report remains
`UNKNOWN`.

ATLAX AI is controlled independently by:

- `analytics.ai_subscriber_enabled`
- `analytics.ai_subscriber_daily_limit`
- `analytics.heatmap_path`
- `analytics.faq_path`

The public FAQ in `docs/20_PUBLIC_FAQ.md` is the subscriber-facing source of
truth for product, access, payment, support, and AI capability questions.

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

### Deterministic Performance Review Compatibility

The local `AdvisoryEngine.analyze_performance` report-object API is a
deterministic compatibility surface for existing callers. It accepts already
calculated `PerformanceMetrics` and returns an `AdvisoryReport`; it does not
call an external provider and cannot alter detection, confidence, risk,
execution, alerts, or configuration. A standalone review is returned only
when the configured `analytics.ai_performance_min_trades` sample size is met.
This setting is a reporting-quality guard, not a trading rule or delivery
threshold.

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
