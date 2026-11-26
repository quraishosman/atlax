# ATLAX Prop-Firm Account Mode Switch Plan

**Version:** 1.0  
**Status:** Live Prop-Firm enforcement enabled; post-promotion monitoring active  
**Owner:** ATLAX Engineering  
**Related specifications:** `docs/10_RISK_ENGINE.md`, `docs/11_EXECUTION_ENGINE.md`, `docs/13_CONFIGURATION.md`, `docs/16_SECURITY.md`, `docs/42_PROP_FIRM_LOSS_BUDGET_PLAN.md`

## 1. Purpose

Add an administrator-controlled Telegram switch that selects the risk policy
for the MT5 account currently connected to ATLAX:

- `NORMAL_ACCOUNT`
- `PROP_FIRM_SHADOW`
- `PROP_FIRM_ENFORCED`
- `AUTO_ENFORCED`

The switch must make the active policy visible, auditable, and bound to a
specific MT5 account identity. It must not pretend to change the MT5 login.
After the one-time account/profile binding, `AUTO_ENFORCED` reads the active
MT5 login and applies the matched profile without requiring an active/inactive
toggle for every login.

## 2. Critical Boundary

The Telegram switch selects **how ATLAX manages risk**. It does not change
which broker account MetaTrader 5 is logged into.

If the operator needs ATLAX to trade a different account, that account must be
opened in the configured MT5 terminal or in a separately bound MT5 terminal
instance. A mode switch alone must never redirect orders to an unknown account.

The first implementation supports one explicitly bound MT5 account per active
scanner instance. Multi-terminal routing is a later, separate capability.

## 3. Current State

Already available:

- Versioned `PropFirmRuleProfile` models.
- Dynamic loss-budget evaluation based on the selected account snapshot.
- Recovery states and reduced-risk sizing.
- Prop-Firm Telegram control state.
- Admin-only Prop-Firm profile selection.
- Prop-Firm shadow validation and reporting.
- Optional execution protection guard.

Current live configuration remains the normal risk path until a real approved
profile and account binding are configured and the mode is explicitly
promoted.

## 3.1 Implementation Status

The repository implementation now includes:

- Typed MT5 account identity and secret-free account fingerprinting.
- Restart-safe account binding and mode state.
- Optional source-backed profile loading from
  `ATLAX_PROP_FIRM_PROFILE_FILE`, `config/prop_firm_profiles.yaml`, or the
  JSON compatibility path.
- Admin Telegram controls for binding, Normal, Shadow, and Enforced modes.
- Startup identity verification and mismatch logging.
- A scanner-owned Prop-Firm context adapter with dynamic opening and
  high-watermark account references.
- Fail-closed live Risk Engine wiring for Enforced mode.
- Shadow comparison using the same verified runtime context.
- Protection-guard refresh from the exact live loss-budget decision.
- Account-mode audit, status, and operational logging.
- Monotonic account-mode revisions with compare-and-set persistence.
- Explicit Telegram confirmation before `PROP_FIRM_ENFORCED` can be selected.
- Protection-guard clearing on mode changes and before each fresh context
  resolution, preventing stale authorization from surviving a downgrade.
- Operational counters for mode changes, verification failures, unknown
  contexts, blocked entries, and protection-context refreshes.
- A read-only Phase 8 preflight command at
  `scripts/prop_firm_phase8_preflight.py`.

The project lead approved the first rollout for Maven Standard 2-Step, 2K,
Phase 2. The selected source-backed profile is version `1.1.0`, the intended
MT5 account is bound, `account_mode.enabled` is true, and approved budget
fractions are `0.50` and `0.15`. Automatic enforcement is enabled through
`account_mode.auto_enforce_when_bound`; the scanner promotes a matching bound
account to `AUTO_ENFORCED` at startup and blocks mismatches.

## 4. Non-Negotiable Rules

1. The mode is admin-only and every change is audited.
2. Prop-Firm enforcement requires a complete, source-backed, non-stale profile.
3. Prop-Firm enforcement requires a matching MT5 account binding.
4. Missing, stale, contradictory, or mismatched account data returns
   `UNKNOWN` and blocks new entries.
5. Protective reductions on already-open positions remain available only when
   the existing execution guard authorizes them.
6. A loss budget is calculated from the selected profile and live account
   snapshot; no funded balance, loss amount, lot size, or threshold is
   hardcoded.
7. The mode switch must never increase risk or bypass the existing Risk Engine.
8. Telegram text must distinguish account identity, selected policy, and live
   enforcement status.
9. No broker password, API key, payment secret, or raw MT5 credential may be
   persisted in mode state, logs, Telegram, or AI context.
10. The system must fail closed on startup and after a mode or account change
    until the new state has passed validation.

## 5. Proposed Runtime Model

### 5.1 Mode states

```text
NORMAL_ACCOUNT
    Standard ATLAX risk configuration; no Prop-Firm loss-budget context.

PROP_FIRM_SHADOW
    Normal live orders continue, while the validated Prop-Firm policy is
    evaluated beside them and differences are recorded.

PROP_FIRM_ENFORCED
    The validated Prop-Firm policy is supplied to Risk Engine before sizing
    and entry approval.

AUTO_ENFORCED
    The scanner reads the current MT5 account identity on each runtime
    verification, matches it to the approved binding/profile, and applies the
    validated Prop-Firm policy. A missing or mismatched identity blocks new
    entries.

BLOCKED_ACCOUNT_MISMATCH
    No new automatic entries. Protective management may continue through the
    approved Trade Manager and protection guard.
```

`BLOCKED_ACCOUNT_MISMATCH` is an operational safety state, not a selectable
policy. It is entered automatically when an active Prop-Firm binding no longer
matches the connected MT5 account.

### 5.2 Account identity

The validated MT5 identity should contain only non-secret broker metadata:

```yaml
account_binding:
  mt5_login: "<operator supplied login>"
  server: "<operator supplied server>"
  currency: "USD"
  account_class: "PROP_FIRM"
  terminal_instance_id: "<stable local instance identifier>"
  bound_at: "<UTC timestamp>"
  bound_by: "<admin identifier>"
```

The runtime adapter must read the current identity from `mt5.account_info()`
and compare at least login, server, currency, and terminal instance where the
terminal exposes a stable value. Account name and company are display and
diagnostic fields, not the only security boundary.

MT5 does not reliably provide a universal `NORMAL` versus `PROP_FIRM` label.
The account class is therefore an explicit, audited operator binding that is
verified against the login and server, not an inferred broker label.

### 5.3 Prop-Firm context

The runtime provider must construct `PropFirmRiskContext` only after it has:

- Loaded the selected profile by ID and version.
- Validated profile source, freshness, and required loss rules.
- Read the current MT5 account identity.
- Confirmed the identity matches the stored binding.
- Collected current balance, equity, realized PnL, open ATLAX risk, cost
  reserve, and slippage reserve.
- Confirmed the snapshot currency and profile currency match.

No provider result means “allow normally.” It means `UNKNOWN`, and Prop-Firm
new-entry evaluation must block.

## 6. Telegram Experience

The admin menu should show:

```text
🏦 Prop-Firm Account Mode

Mode: 🟢 NORMAL ACCOUNT
MT5 account: 12345678 · Broker-Server
Policy: standard ATLAX risk controls

[🏦 Use Prop-Firm Rules]
[🔗 Bind Current MT5 Account]
[📋 Choose Prop-Firm Profile]
[🧪 Shadow Report]
[🏠 Home]
```

When Prop-Firm mode is active:

```text
🏦 Prop-Firm Account Mode

Mode: ✅ PROP-FIRM ENFORCED
Profile: Example Firm · 2K Program · Phase 1
MT5 account: 12345678 · Broker-Server
Loss buffer: $160.00
Daily buffer: $100.00
Recovery state: NORMAL
Last verified: 2026-09-19 12:30 UTC

[⏸ Pause New Entries]
[🔄 Re-verify Account]
[🟢 Use Normal Rules]
[🏠 Home]
```

The interface must show `PROP-FIRM SHADOW` when live orders are unchanged.
It must never display `PROP-FIRM ENFORCED` until the live scanner has passed
account, profile, and context validation.

Enabling the mode requires confirmation that states:

> This changes the live risk policy for the currently bound MT5 account. It
> does not change the MT5 login. New entries will be blocked if the account,
> profile, or live risk data does not match.

## 7. Implementation Phases

### Phase 0 - Governance and ADR — COMPLETE

- Approve this plan.
- Create an ADR defining the difference between account identity and risk
  policy selection.
- Approve the first supported account identity fields and mismatch policy.
- Confirm whether the first release supports one scanner/terminal instance
  only.

**Exit criteria:** the project lead approves the fail-closed boundary and the
meaning of each runtime mode.

### Phase 1 - Account identity adapter — COMPLETE

- Add a typed, secret-free `MT5AccountIdentity` model.
- Add an adapter that reads `login`, `server`, `currency`, `trade_mode`,
  `balance`, `equity`, and broker/company diagnostics from MT5.
- Normalize values for deterministic comparison.
- Return `UNKNOWN` for unavailable or contradictory fields.
- Add a stable scanner/terminal instance identifier without persisting
  credentials.
- Add tests for account changes, reconnects, missing fields, and currency
  mismatches.

**Exit criteria:** ATLAX can prove which MT5 account is connected without
guessing its account class.

### Phase 2 - Persisted mode and binding state — COMPLETE

- Add a validated `AccountModeState` model.
- Persist selected mode, bound identity, selected profile ID/version, actor,
  timestamps, verification status, and block reason.
- Add compare-and-set updates so two Telegram callbacks cannot race.
- Make startup load the state and default to `NORMAL_ACCOUNT` only when no
  Prop-Firm binding exists; an invalid existing Prop-Firm state must block,
  not silently downgrade.
- Redact account identifiers appropriately in ordinary logs while retaining
  enough audit data for reconciliation.

**Exit criteria:** mode and binding survive restart and every transition is
auditable.

### Phase 3 - Telegram binding and confirmation flow — COMPLETE

- Add admin-only buttons for `Normal`, `Prop-Firm Shadow`, and
  `Prop-Firm Enforced`.
- Add `Bind Current MT5 Account` and `Re-verify Account`.
- Require a selected, validated profile before Prop-Firm modes can be chosen.
- Require explicit confirmation before enabling enforcement.
- Display the exact connected login/server and selected profile.
- Add pause and rollback to normal mode.
- Reject callbacks from non-admin users and audit every accepted or rejected
  action.

**Exit criteria:** the operator can see exactly which account and policy are
selected before live enforcement is possible.

### Phase 4 - Runtime context provider — COMPLETE

- Implement the scanner-owned provider that builds `PropFirmRiskContext` from
  the validated mode state and current MT5 snapshot.
- Reuse existing profile validation, loss-budget, recovery, open-risk,
  transaction-cost, and slippage calculations.
- Refresh the context at the configured cadence and before every new entry.
- Reject stale snapshots and stale profiles.
- Persist a sanitized context fingerprint and verification timestamp.
- Add tests for all mismatch and missing-data cases.

**Exit criteria:** a valid context is produced only for the bound account and
selected profile.

### Phase 5 - Live Risk Engine wiring — COMPLETE, DISABLED BY CONFIGURATION

- Pass the verified `PropFirmRiskContext` to the live `RiskEngine.evaluate`
  call before lot-size calculation.
- Keep `NORMAL_ACCOUNT` behavior unchanged.
- In `PROP_FIRM_SHADOW`, run the existing shadow validator and leave live
  orders unchanged.
- In `PROP_FIRM_ENFORCED`, block or resize according to the validated budget.
- In `BLOCKED_ACCOUNT_MISMATCH`, block new entries with a clear reason.
- Attach mode, account fingerprint, profile ID/version, budget decision, and
  recovery state to the persisted risk evaluation.
- Preserve existing approval, execution, and idempotency boundaries.

**Exit criteria:** no new Prop-Firm entry can bypass the loss-budget evaluator,
and normal mode has no unintended behavior change.

Implementation note: the scanner now resolves the verified MT5 identity,
selected source-backed profile, broker-valued open ATLAX stop risk, and live
account snapshot before the risk decision. `PROP_FIRM_SHADOW` records the
comparison beside the normal risk result; `PROP_FIRM_ENFORCED` blocks the new
entry when that context is missing or not verified. The runtime adapter is
constructed only when `account_mode.enabled` is true, so the repository's
current configuration remains unchanged and safe for normal-account operation.

### Phase 6 - Execution and trade-management integration — COMPLETE

- Pass the active mode and validated profile snapshot to the existing
  `PropFirmProtectionGuard`.
- Permit only approved risk-reducing actions for open positions.
- Preserve TP2 and no-backward-stop behavior.
- Keep protective actions available after an entry block when the guard has
  valid data.
- Block unsupported full-close or unapproved action types.
- Add reconciliation tests for restarts, account disconnects, and ambiguous
  MT5 responses.

**Exit criteria:** account-mode enforcement cannot turn trade management into a
second execution or risk system.

### Phase 7 - Observability and operator verification — COMPLETE

- Add structured events:
  - `account_identity_read`
  - `account_binding_verified`
  - `account_binding_mismatch`
  - `trading_mode_changed`
  - `prop_firm_context_ready`
  - `prop_firm_context_unknown`
  - `prop_firm_enforcement_blocked`
  - `prop_firm_entry_resized`
- Add counters for mode changes, verification failures, unknown contexts,
  blocked entries, resized entries, protective actions, and protection-context
  refreshes. Resized-entry counts come from the uncapped versus budget-capped
  risk calculation, while protective-action counts are recorded only after a
  broker-confirmed stop or close operation.
- Add an admin status view showing the current mode and last verification.
- Ensure logs never contain MT5 passwords, payment secrets, or raw context
  payloads.

**Exit criteria:** an operator can prove from Telegram and logs whether the
system is normal, shadow, enforced, or blocked.

### Phase 8 - Shadow validation and promotion — PROMOTED WITH FAIL-CLOSED MONITORING

- Bind the intended MT5 account in shadow mode.
- Collect real evaluations with normal versus Prop-Firm decisions.
- Compare blocks, resizes, allowed trades, and unknown states.
- Test reconnects, daily reset boundaries, profile staleness, equity drawdown,
  open risk, and account switching.
- Obtain explicit project-lead approval for live enforcement.
- Promote only after rollback to normal mode is tested.

**Exit criteria:** signed go-live approval, clean shadow evidence, verified
rollback, and no unresolved `UNKNOWN` path that could allow a new trade.

Current implementation status: the scanner is running with live enforcement.
The project lead explicitly approved promotion before a real Shadow sample set
was accumulated. This is a documented operational exception. The runtime
remains fail-closed: missing, stale, mismatched, or otherwise `UNKNOWN`
account context blocks new entries rather than falling back to normal risk.

The current read-only preflight should report no setup blockers. Monitoring
continues for verification failures, blocked entries, resized entries, and
unknown contexts after promotion:

- `account_mode.enabled` is true.
- `max_open_risk_fraction` is `0.50`.
- `new_trade_buffer_fraction` is `0.15`.
- The MT5 account binding and Maven profile selection are persisted.

## 8. Configuration Shape

The runtime uses the following validated settings shape. Binding and selected
profile state are persisted in the runtime store after an administrator binds
and selects them; credentials and raw MT5 secrets are never stored here.

```yaml
account_mode:
  enabled: false
  auto_enforce_when_bound: false
  context_max_age_seconds: 30
  max_profile_age_hours: 168
  max_open_risk_fraction: null
  new_trade_buffer_fraction: null
  cost_reserve: 0
  transaction_cost_reserve: 0
  slippage_reserve: 0
  terminal_instance_id: null
```

The configuration must reject an enabled Prop-Firm mode with missing binding,
profile, or required account fields. It must not silently use a default
profile or interpret a missing `account_class` as Prop-Firm.

## 9. Test Matrix

Required tests include:

- Normal mode uses existing risk behavior.
- Prop-Firm shadow mode does not change live order decisions.
- Prop-Firm enforced mode blocks when the overall buffer is exhausted.
- Prop-Firm enforced mode uses the smaller daily or overall buffer.
- Losses reduce or preserve risk; they never increase the next lot size.
- Current MT5 login mismatch blocks new entries.
- Current MT5 server mismatch blocks new entries.
- Currency mismatch blocks new entries.
- Profile ID/version mismatch blocks new entries.
- Stale account snapshot blocks new entries.
- MT5 disconnect blocks new entries and logs `UNKNOWN`.
- Restart preserves mode and binding state.
- Duplicate Telegram callbacks cannot create conflicting transitions.
- Non-admin users cannot change mode or binding.
- Protective stop tightening remains governed by the existing guard.
- No secret appears in persisted state, logs, Telegram, or AI context.
- Switching from Prop-Firm to Normal requires audit and status refresh.

## 10. Rollback Plan

Rollback must be an explicit admin action or an automatic fail-closed response
to a binding/profile mismatch:

1. Stop new entries.
2. Preserve current positions and approved protective management.
3. Record the reason and active mode.
4. Switch to `NORMAL_ACCOUNT` only after the operator confirms that normal
   account risk controls are intended for the currently connected account.
5. Re-verify the MT5 identity before resuming entries.

Changing to normal mode is not a substitute for fixing a wrong MT5 login. The
operator must see the connected account identity before resuming.

## 11. Completion Standard

The implementation is complete through Phase 7 when:

- The active mode and bound MT5 account are visible to the administrator.
- The Telegram switch cannot change the MT5 account silently.
- Prop-Firm enforcement reaches the live Risk Engine before sizing.
- Mismatched or unknown account state blocks new entries.
- Normal, shadow, enforced, and blocked states are distinct in code, logs,
  and Telegram.
- Every decision is restart-safe and auditable.
- Tests, configuration documentation, logging documentation, and an ADR are
  updated.
- The repository tests and documentation are updated.

Live promotion is a separate Phase 8 gate and additionally requires an
approved source-backed profile, a verified account binding, real shadow
comparisons, explicit project-lead approval, and tested rollback evidence.

## 12. Open Decisions

Before implementation, the project lead must confirm:

1. The exact MT5 login and server to bind for the first Prop-Firm rollout.
2. Whether one scanner instance is sufficient or multiple MT5 terminals are
   required.
3. The approved profile catalog and profile refresh policy.
4. Whether ordinary-account mode may be selected while a Prop-Firm binding is
   stored, or whether the binding must first be removed.
5. The maximum allowed account-context age before new entries are blocked.
6. Whether a mismatch should automatically pause the scanner or only block new
   entries while Telegram remains available.
