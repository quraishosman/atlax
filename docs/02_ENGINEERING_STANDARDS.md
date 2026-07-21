# ATLAX Engineering Standards

Version: 1.0  
Status: Active  
Purpose: Define engineering rules for maintainable, deterministic ATLAX development.

---

## Priorities

1. Correctness
2. Reliability
3. Maintainability
4. Performance

Never sacrifice correctness for speed.

---

## Core Standards

- Use clean architecture.
- Use SOLID principles.
- Prefer composition over inheritance.
- Use dependency injection.
- Keep coupling low and cohesion high.
- Keep functions short.
- Give every class one responsibility.
- Use names that describe intent.
- Avoid abbreviations.
- Comment why, not what.

---

## Forbidden Patterns

- God objects.
- Massive files.
- Global mutable state.
- Magic numbers.
- Circular dependencies.
- Hidden trading logic.
- Undocumented defaults.
- Silent error handling.
- Detector logic that returns trade commands.

---

## Configuration Standard

All configurable behavior must flow through the configuration service described in `docs/13_CONFIGURATION.md`.

Do not hardcode:

- Risk values.
- Sessions.
- Spreads.
- Risk-reward requirements.
- Timeframes.
- Symbols.
- Thresholds.
- News filters.
- Confidence weights.
- Alert routing behavior.

---

## Documentation Standard

Every module requires:

- Purpose.
- Inputs.
- Outputs.
- Dependencies.
- Example.
- Limitations.
- Logging requirements.
- Error behavior.

---

## AI Agent Rule

If a required rule, parameter, schema, or behavior is missing, return `UNKNOWN` and request clarification.

Do not fill gaps with plausible trading logic.
