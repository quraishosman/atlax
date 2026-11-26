# ATLAX Prop-Firm Loss Budget and Recovery Plan

Version: 0.1  
Status: Phase 7 complete; controlled live rollout pending  
Date: 2026-09-19  
Owner: ATLAX Engineering  

Related documents:

- `docs/10_RISK_ENGINE.md`
- `docs/11_EXECUTION_ENGINE.md`
- `docs/13_CONFIGURATION.md`
- `docs/15_LOGGING.md`
- `docs/16_SECURITY.md`
- `docs/41_PROFIT_PROTECTION_PLAN.md`

## 1. Purpose

Add a Telegram-selectable **Prop-Firm Mode** that protects a configured
prop-firm account using its actual loss allowance rather than treating the
headline funded balance as freely riskable capital.

The feature has two responsibilities:

1. Explain the selected prop firm's rules using versioned, source-backed rule
   profiles.
2. Enforce deterministic account-protection controls before ATLAX can open a
   new MT5 position.

This document does not authorize live behavior. Runtime promotion requires the
approval and validation gates described in Section 12.

## 2. Core Principle

The nominal funded balance is not ATLAX's trading-risk budget.

For illustration only, an account with a $2,000 nominal balance and an 8%
maximum-loss rule would calculate:

```text
maximum_loss_allowance = $2,000 × 0.08 = $160
```

The $160 is the account's total loss buffer in that example. It is not a new
balance that may be risked in one trade. These figures must never be hardcoded
in ATLAX. The implementation must read the selected account type, nominal
balance, maximum-loss rule, daily-loss rule, reset method, and currency from
the versioned prop-firm profile and/or the validated broker account snapshot.
It must then calculate the actual loss buffer for that account and use a
smaller configurable risk fraction while reserving room for spread, slippage,
commission, swap, and correlated open exposure.

The example value `$126` is not 8% of `$2,000`; it is approximately 6.3%.
ATLAX must calculate and display the exact source-derived amount rather than
accepting a manually typed approximation.

## 3. Scope and Non-Goals

### In scope

- Prop-firm rule profiles and source tracking.
- Static, trailing, end-of-day, and equity-based loss-limit evaluation.
- Daily loss and overall loss headroom.
- Maximum open-risk and maximum-position checks.
- Deterministic lot-size reduction during drawdown recovery.
- Automatic pause and manual resume after protection events.
- Reuse of the existing profit-protection manager.
- Telegram status and controls.
- Audited rule snapshots attached to each execution decision.

### Not in scope

- Guaranteeing profitable trades.
- Increasing position size to recover a loss.
- AI choosing risk, lot size, entries, or exits.
- Automatically accepting unverified prop-firm rules.
- Scraping an unlimited list of firms without source review.
- Replacing the existing CRT, strategy, confidence, or execution boundaries.

## 4. Prop-Firm Rule Profile

ATLAX must not implement one universal prop-firm rule set. A profile is
selected by firm, program, phase, account type, account size, platform, and
currency. Account size and loss values must be read from the selected profile
or validated account data; they must not be embedded in Python, Telegram
handlers, YAML defaults, or lot-size formulas.

Each profile must contain:

- Firm name and official program name.
- Challenge, verification, funded, or other phase.
- Nominal account balance and currency.
- Maximum overall loss amount or percentage.
- Maximum daily loss amount or percentage.
- Loss calculation basis: balance, equity, or balance plus floating P/L.
- Overall-limit type: static, trailing, end-of-day trailing, or intraday.
- Daily reset timezone and reset rule.
- Maximum position size and instrument limits.
- Maximum open positions and exposure limits where published.
- Minimum trading days.
- Consistency, Best Day, or payout constraints where applicable.
- News, weekend, overnight, copy-trading, and prohibited-strategy rules where
  applicable.
- Official source URLs.
- Source retrieval timestamp.
- Effective date or last verified date.
- Profile version and content hash.
- `UNKNOWN` fields for rules that are not verified.

Official examples show why profiles are required: FTMO publishes maximum daily
loss, maximum loss, minimum trading days, and product-specific consistency
requirements; Topstep publishes futures contract limits, consistency targets,
and program-specific loss rules; FundingPips publishes different loss and
news-policy rules for its products. Rules must therefore be stored per
program, not inferred from the firm's name alone.

Reference sources:

- [FTMO Trading Objectives](https://ftmo.com/en/trading-objectives/)
- [Topstep Trading Combine Parameters](https://help.topstep.com/en/articles/8284197-trading-combine-parameters)
- [Topstep Daily Loss Limit](https://help.topstep.com/en/articles/10490293-daily-loss-limit-in-the-trading-combine-and-express-funded-account)
- [FundingPips Zero rules](https://help.fundingpips.com/hc/en-us/articles/34502157694865-FundingPips-Zero)

If a required rule is missing, stale, contradictory, or ambiguous, the
profile evaluator returns `UNKNOWN` and blocks new automatic entries.

## 5. Loss-Budget Model

### 5.1 Account equity

When the prop-firm rule uses equity, ATLAX must calculate:

```text
equity = balance + floating_profit_loss + swap + commission + fees
```

The broker snapshot must be fresh and symbol metadata must be valid. A missing
or stale equity component is not replaced with zero. The evaluator must use
the account type and rule profile currently selected for that account rather
than assuming that the visible balance is the loss budget.

### 5.2 Authoritative account floor

The profile adapter calculates the authoritative account floor according to the
selected firm's rule:

```text
static_floor       = initial_balance - maximum_loss_amount
trailing_floor     = high_watermark - maximum_loss_amount
eod_trailing_floor = source_defined_eod_reference - maximum_loss_amount
```

The adapter must never substitute one rule type for another. The account
balance, maximum-loss amount, daily-loss amount, and reference equity must be
resolved at runtime from the active account snapshot and profile. If a profile
uses a percentage, ATLAX calculates the amount from the observed or
profile-approved account basis; if it uses an absolute amount, ATLAX uses that
amount directly. No fixed `$2,000`, `8%`, `$160`, or `$126` value is a runtime
default.

### 5.3 Remaining overall buffer

```text
remaining_overall_buffer = max(0, current_equity - authoritative_account_floor)
```

### 5.4 Remaining daily buffer

The daily evaluator uses the source-defined reset timezone and reference value:

```text
daily_floor = daily_reference - maximum_daily_loss_amount
remaining_daily_buffer = max(0, current_equity - daily_floor)
```

If the selected firm has no daily-loss rule, the value is `NOT_APPLICABLE`,
not an invented zero. The evaluator must always inspect both the maximum-loss
buffer and the daily-loss buffer when both exist, then trade against the
smaller remaining allowance.

### 5.5 New-trade budget

Before an order is approved:

```text
open_risk = sum(max_loss_at_stop(position) for open ATLAX positions)

available_budget = min(
    remaining_overall_buffer,
    remaining_daily_buffer when applicable,
    configured_max_open_risk_budget
) - open_risk - configured_cost_reserve

allowed_trade_risk = min(
    configured_strategy_risk,
    available_budget × new_trade_buffer_fraction
)
```

If `available_budget` is zero, negative, stale, or `UNKNOWN`, no new order may
be submitted. The active account type controls which limits participate in the
`min(...)` calculation; ATLAX must not apply daily-loss, trailing-loss, or
consistency rules that are not present in that account's profile.

### 5.6 Lot size

The existing broker-metadata-first risk formula remains authoritative:

```text
value_per_price_unit = trade_tick_value / trade_tick_size
cash_risk_per_lot = stop_distance_price × value_per_price_unit
raw_lot_size = allowed_trade_risk / cash_risk_per_lot
```

The result is then constrained by broker minimum, maximum, and volume step. A
volume that rounds below the broker minimum must be rejected, not enlarged to
fit the minimum.

## 6. Drawdown Recovery Mode

The recovery feature reduces risk after losses. It must never increase lot size
to recover a loss.

### 6.1 State machine

```text
NORMAL
  -> CAUTION
  -> RECOVERY
  -> PAUSED
  -> RULE_VIOLATION
```

Transitions are deterministic and logged.

- `NORMAL`: normal configured risk, subject to current prop-firm headroom.
- `CAUTION`: reduced risk after the configured drawdown or loss-streak trigger.
- `RECOVERY`: further reduced risk while recovering the approved drawdown.
- `PAUSED`: no new trades; existing protection remains active.
- `RULE_VIOLATION`: no automatic trading and manual review required.

### 6.2 Recovery sizing

```text
recovery_risk = normal_risk × recovery_risk_multiplier
allowed_trade_risk = min(recovery_risk, remaining_budget constraints)
```

The multiplier, activation drawdown, consecutive-loss trigger, minimum risk,
and restoration conditions must be configurable. They must not be hidden in
the lot-size code.

### 6.3 Restoration

Normal risk may return only when all configured restoration conditions are met,
such as:

- The prior drawdown has been recovered.
- The account remains below all firm limits.
- Required stabilization outcomes have completed.
- No stale or contradictory broker data exists.

The recovery target is not a command to keep trading until the loss is
replaced. ATLAX must be allowed to remain paused indefinitely.

## 7. Profit Protection Integration

Prop-Firm Mode must reuse `docs/41_PROFIT_PROTECTION_PLAN.md` and the existing
Trade Manager. It must not create a second exit manager.

The integration must support:

- Breakeven plus validated transaction costs.
- Positive-R protection after the configured activation threshold.
- MFE and giveback tracking.
- Closed-candle momentum confirmation.
- TP1 partial profit where the actual fill is favorable and profitable.
- Protective SL tightening without moving the stop backward.
- Preservation of the approved TP2 unless a separately approved close action
  is enabled.

Every profit-protection action must be checked against the remaining prop-firm
loss budget and recorded with the active rule-profile version.

## 8. Telegram Experience

Add a subscriber/admin execution control:

```text
🏦 Prop Firm Mode
```

Required screens:

- Enable or disable Prop-Firm Mode.
- Choose firm, program, phase, account size, and currency.
- View current rule-profile version and last verification date.
- View nominal balance separately from ATLAX risk capital.
- View maximum loss allowance and remaining overall buffer.
- View remaining daily buffer when applicable.
- View current recovery state and risk multiplier.
- View why a trade was blocked.
- Pause new trading.
- Resume only after an authorized confirmation.
- View the active protection status and last risk event.

User-facing wording must say **loss budget** or **remaining protection buffer**;
it must not imply that the bot can guarantee a passed challenge.

## 9. ATLAX AI Boundary

ATLAX AI may:

- Explain the selected official rule profile.
- Show the source and verification date.
- Explain why an order was blocked or resized.
- Summarize drawdown, daily loss, and consistency status.
- Identify that a rule is unknown or potentially stale.

ATLAX AI must not:

- Invent a prop-firm rule.
- Override a hard risk block.
- Change the selected profile without approval.
- Increase risk after losses.
- Promise that a trade will recover a loss.
- Place, modify, or close trades by natural-language request.

## 10. Configuration Design

The following is a planning shape only. Values require project-lead approval
before being added to the live configuration:

```yaml
prop_firm_mode:
  enabled: false
  shadow_mode: true
  selected_profile_id: ''
  rule_profile_max_age_hours: 168
  stale_profile_policy: BLOCK_NEW_TRADES
  require_manual_resume_after_pause: true
  loss_budget:
    use_equity_when_required: true
    configured_max_open_risk_fraction: null
    configured_new_trade_buffer_fraction: null
    configured_cost_reserve: null
    transaction_cost_reserve: null
    slippage_reserve: null
  recovery:
    enabled: true
    caution_drawdown_fraction: null
    recovery_drawdown_fraction: null
    consecutive_loss_trigger: null
    caution_risk_multiplier: null
    recovery_risk_multiplier: null
    minimum_risk_multiplier: null
    restore_after_drawdown_recovered: true
    restore_profit_cushion: null
    stabilization_closed_trades: null
  profit_protection:
    inherit_existing: true
```

Null values are deliberate placeholders. The Settings Engine must reject a
live profile with missing required risk values rather than applying defaults.

## 11. Storage and Audit Requirements

Persist:

- Rule profile ID, version, source URLs, content hash, and verification time.
- Selected account phase and nominal balance.
- Authoritative floor and its calculation method.
- Daily reference, daily floor, and reset timezone.
- Current equity and remaining buffers.
- Open-risk calculation and broker metadata snapshot.
- Recovery state, multiplier, trigger, and restoration event.
- Every blocked, resized, paused, resumed, or approved order decision.
- The complete configuration snapshot used for each decision.

No API keys, broker passwords, or payment secrets may enter the profile or
AI prompt data.

## 12. Implementation Phases

### Phase 0 - Governance and source intake

- Approve the loss-budget principle.
- Select the first supported firms and exact programs.
- Record official rule URLs and verification dates.
- Define which rules are enforceable versus advisory.
- Create an ADR for Prop-Firm Mode.

Exit criteria: approved profile worksheets with no unresolved required fields.

### Phase 1 - Domain models and profile validation

- Add typed rule-profile and account-snapshot models.
- Implement source version, age, and completeness validation.
- Return `UNKNOWN` for missing or contradictory rules.
- Add unit tests for static, trailing, end-of-day, and equity-based limits.

Implementation status: **COMPLETE**.

Implemented in:

- `core/models/prop_firm.py`
- `core/risk/prop_firm_validation.py`
- `tests/unit/risk/test_prop_firm_phase1.py`

The Phase 1 models are not connected to MT5, lot sizing, Telegram, or live
execution. Invalid profiles fail validation, stale sources return `UNKNOWN`,
and missing account reference values return `UNKNOWN` without guessing.

Verification: 45 focused risk and Phase 1 tests passed.

Exit criteria: complete. Invalid profiles fail closed before any later runtime
integration can reach MT5.

### Phase 2 - Loss-budget evaluator

- Calculate equity using broker data.
- Calculate daily and overall floors.
- Calculate remaining buffers and current open risk.
- Produce an auditable `RiskBudgetDecision`.
- Integrate with the existing Risk Engine before lot sizing.

Implementation status: **COMPLETE**.

Implemented in:

- `core/risk/loss_budget.py`
- `core/risk/risk_engine.py`
- `core/models/prop_firm.py`
- `core/models/risk.py`
- `tests/unit/risk/test_loss_budget.py`
- `tests/unit/risk/test_risk_engine.py`

The integration is opt-in through `PropFirmRiskContext`. It calculates the
overall and daily buffers, subtracts open risk and cost reserve, caps the
existing broker-metadata-first lot-size calculation, and persists the budget
decision inside `RiskEvaluation`. No live scanner caller supplies this context
yet, so existing runtime behavior remains unchanged.

Verification: 67 focused risk, profile-validation, approval, and storage tests
passed.

Exit criteria: complete. When the context is supplied, no order can bypass the
loss-budget evaluator before lot sizing.

### Phase 3 - Risk sizing and recovery mode

- Apply the remaining-budget cap to lot sizing.
- Implement NORMAL, CAUTION, RECOVERY, PAUSED, and RULE_VIOLATION states.
- Reduce risk after configured loss or drawdown triggers.
- Prevent risk increases during recovery.
- Require explicit resume after hard pauses.

Implementation status: **COMPLETE**.

Implemented in:

- `core/settings/prop_firm_config.py`
- `core/models/prop_firm.py`
- `core/risk/recovery.py`
- `core/risk/loss_budget.py`
- `tests/unit/risk/test_prop_firm_recovery.py`
- `tests/unit/risk/test_loss_budget.py`

Recovery drawdown is measured against the selected profile's calculated
maximum-loss amount, not the nominal account balance. CAUTION and RECOVERY
apply validated multipliers no greater than `1`; the loss-budget cap remains
the upper bound. A missing recovery state or high-watermark equity returns
`UNKNOWN` and blocks a new trade. PAUSED requires an authorized resume, while
RULE_VIOLATION remains blocked for manual review. Restoration requires the
configured equity cushion and stabilization outcomes; ATLAX never increases
risk to replace a loss.

The integration remains opt-in through `PropFirmRiskContext`. No scanner or
live MT5 caller supplies the context yet, so current live behavior is
unchanged until a later approved rollout phase.

Verification: focused recovery and loss-budget tests pass, including state
transitions, serialization, reduced-risk sizing, missing-data blocking, and
hard-limit handling.

Exit criteria: deterministic replay tests prove that a loss cannot increase
the next lot size.

### Phase 4 - Profit-protection integration

- Connect Prop-Firm Mode to the current MFE, breakeven, and momentum manager.
- Verify TP2 preservation and no-backward-stop behavior.
- Verify all modifications remain within broker and prop-firm constraints.
- Add cost and slippage accounting to the budget.

Implementation status: **COMPLETE**.

Implemented in:

- `core/risk/prop_firm_protection.py`
- `core/risk/loss_budget.py`
- `core/models/prop_firm.py`
- `core/execution/trade_manager.py`
- `tests/unit/risk/test_prop_firm_protection.py`
- `tests/unit/risk/test_loss_budget.py`
- `tests/unit/execution/test_trade_manager.py`

The existing Trade Manager remains the only component that can request MT5
stop or partial-close operations. When an optional
`PropFirmProtectionGuard` is supplied, it authorizes only fresh, auditable,
risk-reducing actions: protective breakeven, profit-lock, closed-candle
momentum stop tightening, and broker-confirmed profitable partial close.
Backward stop movement, stale or invalid profile snapshots, unknown profit, and
unknown action types fail closed. Protective actions remain available after a
hard limit breach because they reduce exposure; new entries remain blocked by
the loss-budget evaluator.

`PropFirmRiskContext` now accepts separate transaction-cost and slippage
reserves. Both are included in the available-budget calculation and persisted
in `LossBudgetDecision`, so the account's remaining loss capital is not
overstated. Existing MFE, breakeven-cost, TP1, TP2, closed-candle momentum,
state persistence, and no-backward-stop behavior are reused rather than
duplicated.

The guard and context are opt-in integration points. No live scanner caller
supplies a Prop-Firm profile or guard yet; current live trading behavior is
unchanged until the later rollout phases are explicitly approved.

Exit criteria: protection actions are idempotent, logged, and restart-safe.

### Phase 5 - Telegram controls

- Add the Prop-Firm Mode button and profile selection flow.
- Add risk-budget status and block explanations.
- Add pause/resume controls with authorization checks.
- Keep sensitive account credentials out of Telegram messages.

Implementation status: **COMPLETE**.

Implemented in:

- `core/risk/prop_firm_controls.py`
- `core/market_data/scanner.py`
- `tests/unit/risk/test_prop_firm_controls.py`
- `tests/unit/alerts/test_telegram_experience.py`

The admin-only Telegram surface is available from Risk Management through
**🏦 Prop-Firm Mode**. It persists the selected profile ID and version, the
enable/disable state, and the manual pause state in the runtime store. Profile
buttons use server-side catalog indexes; the callback cannot select an
arbitrary profile identifier. Enabling is rejected unless the selected
profile is present in the server-side, source-backed catalog. Sensitive
credentials and secrets are never rendered.

The screen displays the selected profile, profile version, overall and daily
protection buffers when a validated runtime snapshot is available, recovery
state, and risk multiplier. Pause and resume controls are shown only while
Prop-Firm Mode is enabled. The current pause action is a persisted control
intent and does not change the scanner's unrelated global pause state.

This phase intentionally does **not** connect the control state to live MT5
entry decisions. Until the approved profile catalog and later runtime wiring
are supplied, Prop-Firm Mode remains disabled or reports that enforcement is
pending. This preserves fail-closed behavior while the remaining validation
phases are completed.

Exit criteria: a user can identify the active profile and current protection
state without seeing internal implementation details.

### Phase 6 - ATLAX AI advisory integration

- Add read-only rule-profile and risk-budget tools.
- Restrict answers to the selected profile and current snapshot.
- Include source URL and verification date in rule explanations.
- Rate-limit and audit all requests.

Implementation status: **COMPLETE**.

Implemented in:

- `core/analytics/mcp_server.py`
- `core/analytics/ai_advisory.py`
- `core/market_data/scanner.py`
- `tests/unit/analytics/test_prop_firm_ai.py`

The MCP gateway now exposes sanitized, read-only Prop-Firm rule-profile and
protection-budget views. The profile view requires a complete source-backed
profile with official URLs, version, content hash, and retrieval timestamp;
incomplete or unverified profiles return `UNKNOWN`. The budget view uses a
strict field allowlist and excludes balances, credentials, broker data, and
other unapproved runtime fields.

Only the admin AI path receives this context, and only when the question is
explicitly about a Prop-Firm, funded-account, drawdown, loss budget, daily
loss, recovery, or related rule. Subscriber ATLAX AI remains isolated from
Prop-Firm profiles and live protection buffers. The admin AI prompt requires
source URL and verification date when available, treats `UNKNOWN` as
unavailable, and cannot change configuration, override a block, increase
risk, or execute trades.

The admin AI menu includes **🏦 Prop-Firm Rules**. Every context request and
tool result is logged without storing the question or sensitive values.

Exit criteria: AI cannot change configuration or override a risk decision.

### Phase 7 - Shadow validation

- Run the evaluator beside the current system without changing orders.
- Compare current sizing with loss-budget sizing.
- Simulate loss streaks, gaps, slippage, disconnects, stale data, and resets.
- Verify each prop-firm rule using account-history fixtures.
- Review false blocks and false allows with the project lead.

Implementation status: **COMPLETE**.

Implemented in:

- `core/risk/prop_firm_shadow.py`
- `core/market_data/scanner.py`
- `tests/unit/risk/test_prop_firm_shadow.py`

The shadow validator runs beside the existing Risk Engine evaluation when an
approved context provider supplies a validated `PropFirmRiskContext`. It
re-evaluates the same candidate and account snapshot with the Prop-Firm
budget, then records `would_block`, `would_resize`, `would_allow`,
`would_increase`, `no_change`, and `UNKNOWN` outcomes. It never replaces the
live `RiskEvaluation`, changes its lot size, blocks an order, or calls MT5.

Aggregate counters and the latest 50 sanitized samples are persisted under
`prop_firm.shadow_validation`. The admin can review them through **🏦
Prop-Firm Mode → 🧪 Shadow Report** or `/prop_firm_shadow`. The report contains
decision categories and public candidate context only; it does not display
account credentials or broker secrets.

If no context provider is configured, the validator is inert and reports
`NOT_CONFIGURED`. A provider error, missing context, invalid context, stale
profile, or unknown budget is recorded as `UNKNOWN`; it is never converted to
an allow decision. This keeps the current deployment unchanged until an
approved source-backed profile and account-context adapter are supplied.

The fixture suite covers reduced sizing, unknown context, and inert startup.
Additional shadow evidence must be collected from the configured account
before Phase 8 live promotion.

Exit criteria: all known rule scenarios produce the expected decision and no
unknown state allows a new trade.

### Phase 8 - Controlled live rollout

- Enable hard pre-trade blocking first.
- Enable reduced sizing only after shadow evidence is approved.
- Start with demo or minimum permitted broker volume.
- Require manual resume after every protection pause.
- Monitor `prop_rule_unknown`, `loss_budget_blocked`,
  `recovery_mode_entered`, `risk_size_reduced`, and `prop_limit_breached`.

Exit criteria: signed go-live approval, clean monitoring, and rollback tested.

Current rollout record: the project lead approved live Prop-Firm enforcement
for Maven Standard 2-Step, 2K, Phase 2 before a real Shadow sample set was
accumulated. This exception is fail-closed in the runtime: an unavailable or
unknown account context blocks a new entry. Post-promotion monitoring and
rollback readiness remain mandatory.

### Phase 9 - Maintenance and rule refresh

- Re-verify official sources on a configured schedule.
- Version every profile change.
- Retire profiles when a firm changes or removes a program.
- Notify the operator when a selected profile becomes stale.
- Never silently update live risk rules without an auditable version change.

## 13. Acceptance Criteria

The feature is complete only when:

- The selected prop-firm profile is source-backed and versioned.
- Nominal funded balance and ATLAX loss budget are displayed separately.
- Account balance and loss values are derived dynamically from the active
  account snapshot and selected profile; no example values are hardcoded.
- Daily and overall limits use the correct source-defined calculation method.
- Floating losses and trading costs are included where required.
- Lot size never increases because of a prior loss.
- Unknown or stale rule data blocks new automatic trades.
- A hard loss limit pauses trading and requires authorized resume.
- Profit protection preserves approved stops and targets.
- Every decision is logged and restart-safe.
- Focused unit, integration, replay, and failure-mode tests pass.
- Live promotion is explicitly approved and reversible.

## 14. Open Decisions

Before coding begins, the project lead must approve:

1. The first prop firms and exact programs supported.
2. The official source and refresh interval for each profile.
3. Whether Prop-Firm Mode can control the existing automated MT5 account or
   only a separately identified prop account.
4. The internal safety reserve and maximum new-trade budget fraction.
5. Drawdown and consecutive-loss thresholds for CAUTION and RECOVERY.
6. The recovery multiplier and restoration conditions.
7. The behavior when the prop firm changes rules during an open trade.
8. Whether an administrator or account owner may resume after a pause.
9. The minimum broker volume policy when the calculated lot is below minimum.
10. The shadow-validation duration and evidence required for live promotion.
