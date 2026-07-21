# ATLAX Strategy Engine Specification

Version: 1.0  
Status: Draft  
Purpose: Define how ATLAX converts detector outputs into trade candidates.

---

## Responsibility

The Strategy Engine consumes detector outputs and produces one of:

- `Trade Candidate`
- `No Trade`
- `UNKNOWN`

It must not receive raw market data directly if detector outputs are required for the decision.

It must not talk directly to TradingView or MT5.

---

## Inputs

Allowed inputs:

- Detector results.
- Configuration snapshot.
- Profile settings.
- Session state.
- News filter state.
- Spread state.
- Risk availability state.

---

## Outputs

A `Trade Candidate` must include:

- Candidate ID.
- Strategy name.
- Source detector event IDs.
- Symbol.
- Timeframe.
- Profile.
- Direction only when authorized by the rulebook and strategy spec.
- Entry model.
- Invalidation model.
- Confidence inputs.
- Explanation.

---

## Rule Authority

Strategy behavior must be documented before implementation.

If the strategy requires CRT-specific behavior not yet defined in `docs/rulebooks/CRT_RULEBOOK.md`, return `UNKNOWN`.
