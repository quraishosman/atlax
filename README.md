# ATLAX

ATLAX is a deterministic trading operating system for professional market intelligence.

It is not a collection of scripts, and it is not a trading strategy. ATLAX detects, validates, scores, alerts, optionally executes, manages, logs, and analyzes trading opportunities using documented rules and typed interfaces.

The first supported strategy is Candle Range Theory (CRT). CRT behavior is not implemented until `docs/rulebooks/CRT_RULEBOOK.md` defines it.

## Start Here

Before writing code, read the engineering specification in order:

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

Architecture decisions are recorded in `docs/decisions/`.

## Immutable Principle

Every contributor must understand this before making changes:

> ATLAX is not a collection of scripts. It is a deterministic trading operating system.

Every decision must be explainable, auditable, repeatable, and traceable to a documented specification or rulebook.

If documentation is missing, stop and request clarification.
