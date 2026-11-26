# ATR Quality Overlay Validation Report

Version: 0.1  
Status: ACTIVE - LIVE PREMIUM ENFORCEMENT  
Related plan: `docs/25_ATR_QUALITY_OVERLAY_PLAN.md`  
Overlay configuration: `enabled: true`, `shadow_mode: false`

## Scope

This report records the Phase 7 validation gate for the proposed ATR quality
overlay. It validates classification, routing boundaries, persistence, and
execution isolation. The project lead approved live premium filtering on
2026-08-13.

## Validation Matrix

| Requirement | Evidence | Status |
| --- | --- | --- |
| Decimal ATR ratios and hand-calculated boundaries | `tests/unit/confidence/test_atr_quality.py` | PASS |
| Bullish/bearish sweep symmetry | `tests/unit/confidence/test_atr_quality.py` | PASS |
| Zero or negative ATR fails closed to `UNKNOWN` | `tests/unit/confidence/test_atr_quality.py` | PASS |
| Insufficient ATR candles return zero for upstream `UNKNOWN` handling | `tests/unit/market_data/test_mt5.py` | PASS |
| Standard routing remains unchanged | `tests/unit/alerts/test_atr_routing.py` | PASS |
| Premium accepts `A+` and `A` only | `tests/unit/alerts/test_atr_routing.py`, `test_subscription_entitlements.py` | PASS |
| Premium rejects `B`, `C`, and `UNKNOWN` | `tests/unit/alerts/test_subscription_entitlements.py` | PASS |
| Configuration changes produce a new snapshot ID | `tests/unit/confidence/test_atr_quality.py` | PASS |
| Passport persists ATR metadata when delivery is suppressed | `tests/unit/alerts/test_atr_validation.py`, `test_signal_passport.py` | PASS |
| ATR output cannot become execution instructions | `tests/unit/alerts/test_atr_validation.py` | PASS |

## Results

The focused Phase 7 validation suite passed with **63 tests**. The full
repository suite remains a separate health gate because existing test fixtures
currently include unrelated readonly-journal, scanner-constructor,
analytics-config, and CRT-fixture failures.

## Activation Gate

Live premium enforcement is active. Ongoing operation requires:

1. Project-lead approval of `ATR_QUALITY_OVERLAY.md` and `ADR-010.md`.
2. A clean focused validation run.
3. Shadow-mode logs and Signal Passport records from real market data.
4. Verification that standard delivery and MT5 execution are unchanged.
5. A separate approval to enable premium `A`/`A+` filtering.

No grade is a win-rate or probability guarantee.
