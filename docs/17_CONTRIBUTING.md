# ATLAX Contributing Guide

Version: 1.0  
Status: Active  
Purpose: Define contribution expectations for human and AI contributors.

---

## Before Coding

Read the engineering specification in order, starting with `docs/00_PROJECT_VISION.md`.

Do not write code for behavior that is not documented.

---

## Pull Request Questions

Every PR must answer:

- What changed?
- Why?
- Which rulebook or specification section is affected?
- Any architectural changes?
- Any breaking changes?
- Tests included?
- Documentation updated?
- Any remaining `UNKNOWN` behavior?

---

## Contribution Rules

- Preserve layer boundaries.
- Keep behavior deterministic.
- Add configuration for configurable behavior.
- Add logging for important events.
- Add tests for implemented behavior.
- Update documentation before or with code.
- Request clarification when documentation is incomplete.

---

## Forbidden Contributions

- Undocumented trading rules.
- Hidden defaults.
- Detector code that executes trades.
- Risk bypasses.
- AI-driven live trading decisions.
- Direct AI access to internal systems.
