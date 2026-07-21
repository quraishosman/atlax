# ATLAX Project Vision

Version: 1.0  
Status: Active  
Purpose: Define the mission, scope, and operating philosophy for ATLAX.

---

## Mission

ATLAX is a modular market intelligence platform capable of detecting, validating, scoring, alerting, optionally executing, managing, and analyzing trading opportunities using deterministic rules.

ATLAX is not a trading strategy.

ATLAX is not a collection of scripts.

ATLAX is a deterministic trading operating system.

---

## First Supported Strategy

Version 1 supports:

- Candle Range Theory (CRT)

CRT rules belong only in:

- `docs/rulebooks/CRT_RULEBOOK.md`

If the CRT rulebook is incomplete, implementation must return `UNKNOWN` or stop for clarification.

---

## Product Goals

ATLAX should:

- Detect opportunities.
- Evaluate setup quality.
- Score opportunities.
- Alert the trader.
- Execute trades only when approved and configured.
- Manage trades.
- Log every important event.
- Generate analytics.
- Preserve an audit trail.

Everything else is secondary.

---

## Development Phases

Phase 0: Engineering specification  
Phase 1: Architecture and system design  
Phase 2: Market data layer  
Phase 3: Detection layer  
Phase 4: Strategy engine  
Phase 5: Confidence engine  
Phase 6: Alert system  
Phase 7: Execution  
Phase 8: Trade management  
Phase 9: Analytics  
Phase 10: Optimization

Coding does not begin for a subsystem until its governing specification is sufficient.

---

## Non-Negotiable Principle

Every behavior must originate from documented rulebooks and specifications.

Missing information is resolved by updating documentation, not by inventing code.
