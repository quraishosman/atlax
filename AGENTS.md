# ATLAX AI Working Memory

This file is the first-stop instruction set for AI agents working in ATLAX.

ATLAX is not a collection of scripts. It is a deterministic trading operating system.

Before making project changes, read the relevant engineering specifications. For broad work, read them in order:

1. `docs/00_PROJECT_VISION.md`
2. `docs/01_ONBOARDING.md`
3. `docs/02_ENGINEERING_STANDARDS.md`
4. `docs/03_ARCHITECTURE.md`
5. `docs/04_SYSTEM_DESIGN.md`
6. `docs/05_API_SPECIFICATION.md`
7. `docs/06_DATA_MODELS.md`
8. `docs/07_DETECTOR_SPECIFICATION.md`
9. `docs/08_STRATEGY_ENGINE.md`
10. `docs/09_CONFIDENCE_ENGINE.md`
11. `docs/10_RISK_ENGINE.md`
12. `docs/11_EXECUTION_ENGINE.md`
13. `docs/12_ANALYTICS_ENGINE.md`
14. `docs/13_CONFIGURATION.md`
15. `docs/14_TESTING.md`
16. `docs/15_LOGGING.md`
17. `docs/16_SECURITY.md`
18. `docs/17_CONTRIBUTING.md`
19. `docs/rulebooks/CRT_RULEBOOK.md`
20. `docs/rulebooks/CRT_SOURCE_INTAKE.md`
21. `docs/rulebooks/CRT_RULE_APPROVAL.md`
22. `docs/glossary.md`

Also review applicable ADRs in `docs/decisions/`.

If any required document is missing or incomplete, stop, document the ambiguity, and ask for clarification.

## Non-Negotiables

- ATLAX is not a trading strategy.
- Never invent trading rules.
- Never change strategy behavior unless the relevant source document explicitly defines the change.
- Never modify CRT logic without documented rulebook authority.
- Never assume missing parameters.
- Never hardcode strategy decisions, thresholds, risk values, sessions, symbols, timeframes, spreads, RR values, confidence weights, or lot sizes.
- Never mix detection, strategy, alerting, execution, risk, analytics, security, or AI-advisory responsibilities.
- Never bypass risk management.
- Never skip logging for important decisions, errors, configuration changes, alerts, executions, or trade updates.
- When uncertain, return `UNKNOWN` instead of guessing.

## Source Of Truth

The engineering specification defines system behavior.

Primary behavior authorities:

- `docs/rulebooks/CRT_RULEBOOK.md`
- `docs/03_ARCHITECTURE.md`
- `docs/05_API_SPECIFICATION.md`
- `docs/13_CONFIGURATION.md`

Supporting module authorities:

- Detector behavior: `docs/07_DETECTOR_SPECIFICATION.md`
- Strategy behavior: `docs/08_STRATEGY_ENGINE.md`
- Confidence and learning boundaries: `docs/09_CONFIDENCE_ENGINE.md`
- Risk behavior: `docs/10_RISK_ENGINE.md`
- Execution behavior: `docs/11_EXECUTION_ENGINE.md`
- Analytics and AI advisory behavior: `docs/12_ANALYTICS_ENGINE.md`
- Logging behavior: `docs/15_LOGGING.md`
- Security and MCP gateway behavior: `docs/16_SECURITY.md`

If code and documentation disagree, documentation wins.

If documentation is incomplete, stop and request clarification.

## Development Priorities

1. Correctness
2. Reliability
3. Maintainability
4. Performance

Never sacrifice correctness for speed.

## Architecture Boundaries

- Market Data Layer receives market data only.
- Detector Layer identifies patterns only and must never return `BUY` or `SELL`.
- Strategy Engine consumes detector outputs and produces `Trade Candidate`, `No Trade`, or `UNKNOWN`.
- Confidence Engine scores quality using documented and configurable values only.
- Alert Engine produces alerts only and must never execute trades.
- Execution Engine is MT5-only and handles risk, lot size, SL, TP, execution, and trade management.
- Analytics Layer handles journal, statistics, reports, expectancy, win rate, risk metrics, and advisory AI analysis.
- Multi-timeframe profile routing is documented in `docs/03_ARCHITECTURE.md`.
- Multiple timeframe events for the same pair must be preserved with profile and timeframe context.

## Settings Engine

- Configuration behavior is documented in `docs/13_CONFIGURATION.md`.
- Everything configurable must flow through the Settings Engine or an approved configuration service.
- Profile settings, risk limits, confidence thresholds, symbols, timeframes, sessions, spread filters, alert routing, execution preferences, and MCP limits must not be hidden in code.
- Configuration may control documented behavior, but must never define undocumented trading rules.
- Invalid configuration must fail closed, keep the last known valid settings when available, and log the reason.
- Live reload is allowed only after validation succeeds.

## Learning Engine

- Learning/adaptive confidence boundaries are documented in `docs/09_CONFIDENCE_ENGINE.md`.
- Learning is proposed/research unless explicitly promoted by the project lead.
- Learning may analyze historical outcomes, but it must never define CRT rules, change detector behavior, override risk controls, or execute trades.
- Learning output must be explainable, versioned, validated, and reproducible from stored data.
- If learning data is insufficient or validation fails, return `UNKNOWN` or advisory-only output and keep the last approved model.

## AI Advisory Analysis

- AI advisory analysis is documented in `docs/12_ANALYTICS_ENGINE.md` and constrained by `docs/16_SECURITY.md`.
- LLMs may summarize, explain, draft reports, analyze journals, and suggest configuration changes for trader review.
- LLMs must never detect CRTs, define trading rules, decide entries or exits, generate live confidence scores without explicit approval, manage risk, modify configuration without approval, or execute trades.
- AI output is advisory unless a reviewed document explicitly promotes it.
- If required source data is missing, return `UNKNOWN` instead of filling gaps.

## MCP Security Gateway

- MCP security policy is documented in `docs/16_SECURITY.md`.
- AI data access must go through approved, audited, least-privilege tools.
- Do not give AI direct database credentials, filesystem access, broker access, execution access, raw SQL access, or configuration write access.
- MCP tools must be read-only or compute-only unless a reviewed source document explicitly approves otherwise.
- Every MCP request must be validated, sanitized, rate-limited, and audited.
- Forbidden actions must fail closed.

## Completion Standard

A feature is complete only when it:

- Matches documentation.
- Passes tests.
- Has documentation.
- Has logging.
- Has configuration.
- Has no hardcoded values.
- Has no architectural violations.
- Produces deterministic behavior.

Otherwise, it is not complete.
