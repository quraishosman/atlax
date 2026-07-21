# ATLAX Risk Engine Specification

Version: 1.0  
Status: Draft  
Purpose: Define deterministic risk management boundaries.

---

## Responsibility

The Risk Engine protects the trader from invalid or excessive exposure.

It must be deterministic, configurable, and auditable.

---

## Required Controls

Risk controls must include:

- Risk per trade.
- Maximum daily loss.
- Maximum weekly loss.
- Maximum open trades.
- Maximum trades per day.
- Maximum correlated trades.
- Spread limits.
- News restrictions.
- Profile-specific limits.

---

## Forbidden Behavior

The Risk Engine must not:

- Use AI to choose risk.
- Guess missing risk parameters.
- Allow execution when configuration is invalid.
- Bypass limits because a setup has high confidence.
- Accept hidden defaults.

---

## Failure Mode

Invalid or missing risk configuration must fail closed.

The system must log:

- Reason.
- Timestamp.
- Context.
- Recovery suggestion.
