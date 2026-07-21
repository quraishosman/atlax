# ATLAX Testing Specification

Version: 1.0  
Status: Draft  
Purpose: Define testing expectations before ATLAX behavior is considered complete.

---

## Testing Rule

A feature is not complete until tests prove it matches documentation.

Tests must not encode undocumented trading rules.

---

## Required Test Types

- Unit tests for deterministic modules.
- Contract tests for API payloads.
- Configuration validation tests.
- Detector rule tests after rulebooks are defined.
- Strategy engine decision tests.
- Confidence breakdown tests.
- Risk fail-closed tests.
- Execution request validation tests.
- Logging tests.
- Security tests.
- Regression tests for bug fixes.

---

## Unknown Behavior

If documentation is incomplete, tests should assert that the system returns `UNKNOWN` or fails closed.

Do not write tests that normalize guessed behavior.
