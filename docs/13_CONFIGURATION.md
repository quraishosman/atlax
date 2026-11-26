# ATLAX Configuration Specification

## Settings Engine

Version: 1.0  
Status: Active  
Purpose: Define how ATLAX stores, validates, reloads, and applies trader-controlled settings.

---

## Core Rule

Everything must be configurable.

No strategy decision, risk value, threshold, timeframe, session, spread, symbol, notification preference, or execution parameter may be hidden in code.

### Market Schedule

`market_data.schedule` is the authoritative calendar gate for live market
processing. The scanner skips market-data processing, FVG entry checks, and
approved execution consumption when the local weekday in the configured IANA
timezone is not listed in `trading_weekdays`. Telegram command polling remains
available while the market is closed.

```yaml
market_data:
  schedule:
    timezone: UTC
    trading_weekdays: [0, 1, 2, 3, 4]
```

Weekday values use ISO/Python numbering: `0` is Monday and `6` is Sunday.
The default ATLAX configuration is Monday through Friday. This calendar gate
prevents stale, broker-synthetic, or persisted weekend events from becoming
subscriber alerts or execution requests.

Configuration can control how documented rules are applied, but configuration must never invent trading logic. CRT behavior still belongs only in `docs/rulebooks/CRT_RULEBOOK.md`.

### New-Entry Circuit Breaker

The circuit breaker is a deterministic pre-entry safety gate. It does not
modify existing positions and it does not define detector or strategy rules.
The live configuration must explicitly define every gate:

```yaml
circuit_breaker:
  enabled: true
  block_on_mt5_unavailable: true
  block_on_mt5_trading_disabled: true
  block_on_prop_firm_unknown: true
  block_on_loss_limits: true
  block_on_storage_failure: true
  notify_admin_on_transition: true
  block_on_reconciliation_uncertain: true
```

The loss-limit checks reuse the validated `risk` percentages and the
Prop-Firm loss-budget decision. Missing account, profile, MT5, storage, or
budget data returns an unknown/blocking decision when the corresponding gate
is enabled. The decision and reason code are persisted under
`operations.circuit_breaker` and are available to the admin Operations
Snapshot.

`block_on_reconciliation_uncertain` fail-closes new entries when the latest
broker-to-ATLAX trade-state report is `UNKNOWN` or `CONFLICT`. The report is
persisted under `operations.reconciliation`; `MATCHED` is the only state that
confirms the current broker state is fully verifiable for new-entry purposes.

### Reliability Watchdog

The watchdog is an observational health layer. It does not change detector,
confidence, risk, alert, or execution decisions. It persists its latest
component states under `operations.watchdog`, emits bounded heartbeat/failure
events, and deduplicates administrator notifications after the configured
failure threshold.

```yaml
watchdog:
  enabled: true
  heartbeat_interval_seconds: 30
  failure_threshold: 3
  notification_cooldown_seconds: 300
  notification_ttl_seconds: 300
  repeat_notifications: false
  market_data_stale_after_seconds: 180
  telegram_stale_after_seconds: 180
  config_path: config/atlax.yaml
```

The watchdog reports `HEALTHY`, `DEGRADED`, or `UNKNOWN` for the scanner and
instance lock, MT5 IPC, market-data freshness, Telegram polling and delivery,
runtime state, journal, audit, subscriber/payment services, and configuration
file availability. It records each component's last successful observation
when available. Unknown watchdog state never becomes a permissive trading
fallback; existing fail-closed entry controls remain authoritative.
Configuration drift is compared with the fingerprint captured by the current
scanner process. A restart re-baselines the fingerprint after the validated
configuration is loaded; edits made afterward remain `DEGRADED` until another
restart.
By default, an unchanged failure signature produces one administrator
notification. A changed failure signature or recovery followed by a new
failure produces a new notification. Set `repeat_notifications: true` only
when periodic reminders are operationally required; the cooldown then applies.
Watchdog Telegram notifications are temporary: each is deleted after
`notification_ttl_seconds` (300 seconds by default). Only the watchdog's own
bot messages are tracked and removed; trade alerts, menus, prompts, and user
messages remain untouched. The cleanup schedule is persisted so a restarted
scanner can remove an already-expired watchdog notice.

### MT5 Session Attachment

When an MT5 account and server are configured, ATLAX first attaches to the
already authenticated terminal session without sending credentials. It accepts
the session only when the returned account number and exact broker server match
the configured values. This prevents a stale password from logging out a valid
terminal session. `ATLAX_MT5_PASSWORD` is used only as a fallback when no
matching terminal session is available; an empty password is terminal-session
mode only. Any identity mismatch or failed authentication fails closed and
prevents market-data and execution startup.

### Telegram-Selected Trading Account

The administrator may save up to 20 named, secret-free MT5 account targets in
the Telegram **Trading Accounts** menu. A target contains only its label, MT5
login, and exact broker server. Selecting a target does not submit a login to
MT5 and does not change the Prop-Firm risk policy.

When a target is selected, ATLAX deliberately uses terminal-session mode and
requires the locally running MT5 terminal to report the selected login and
server. The scanner disconnects from its current MT5 session and does not
resume market data or execution until that verification succeeds. It also
checks the selected identity during the live session; changing accounts
directly in MT5 causes ATLAX to disconnect and fail closed until the selected
target is active again.

The operator enters, changes, and stores passwords only in the local MT5
terminal. Telegram, ATLAX runtime state, logs, audit records, YAML, and the
account registry must never receive or persist an MT5 password. The selected
account registry is runtime state rather than tracked configuration so normal
account changes require no YAML or `.env` edit. Before a first selected target
is saved, the existing `market_data.mt5` configuration remains the connection
target.

The Telegram **Delete account** flow permanently removes an unselected saved
identity from the runtime registry after an explicit confirmation. The
currently selected target is protected and must be changed first; this avoids
an accidental fallback to legacy `market_data.mt5` connection settings. The
operation removes only ATLAX's saved login/server label and never deletes the
broker account, closes trades, or removes MT5 terminal credentials.

Some brokers expose a suffix on symbols while ATLAX keeps canonical symbols in
detector, alert, and subscriber records. Set the non-secret
`ATLAX_MT5_SYMBOL_SUFFIX` environment variable when the connected terminal
requires one, for example `m` for `EURUSDm`, `GBPUSDm`, `USDJPYm`, and
`XAUUSDm`. Exact canonical broker symbols always take precedence, and an
unresolved mapping fails closed for market data and execution.

### ATR Snapshot Consistency

The scanner computes ATR from the same closed-candle sequence that it passes
to the detector for a symbol and timeframe. It must not make a second MT5
history request only for ATR, because a transient broker/IPC response could
otherwise produce an invalid zero ATR after valid detector candles were
received. Missing or insufficient closed history remains `UNKNOWN`, and the
approved premium ATR route continues to fail closed.

### Account-Mode Runtime

The account-mode adapter selects the risk policy for the currently connected
MT5 account. It does not change the MT5 login. Prop-Firm enforcement requires
an administrator binding, a typed source-backed profile, and explicit budget
fractions. Missing values fail closed.

`account_mode.max_profile_age_hours` is also the freshness limit for the
source-backed Prop-Firm profile monitor. The monitor records the initial
catalog as a baseline, then requires review if a selected profile's version or
content hash changes. A stale, changed, invalid, missing, or unavailable
profile cannot be used for `AUTO_ENFORCED` or `PROP_FIRM_ENFORCED` runtime
context. The prior approved identity remains available for audit comparison;
the monitor never scrapes or invents undocumented firm rules.

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

The operator-selected mode is persisted separately from this configuration:
`NORMAL_ACCOUNT`, `PROP_FIRM_SHADOW`, `PROP_FIRM_ENFORCED`, or
`AUTO_ENFORCED`. When `auto_enforce_when_bound` is true, a matching bound MT5
account automatically activates `AUTO_ENFORCED`; an unknown or mismatched
account blocks new entries. Prop-Firm mode
blocks new entries when account identity, profile, budget, or snapshot state is
unknown or mismatched. No broker password or secret may be stored in the
binding.

When Prop-Firm recovery is enabled, ATLAX persists the recovery controller
under an account- and profile-scoped runtime key. The first verified
account/profile context creates a `NORMAL` state; subsequent evaluations load
and persist its transition before an entry can proceed. Missing, invalid, or
unpersistable recovery state fails closed. The canonical recovery setting is
`restore_profit_cushion`; older `restore_profit_cushion_fraction` configuration
is accepted only as a compatibility alias and has the same absolute-currency
meaning as the canonical field.

---

## Settings Architecture

```text
+-------------------------------------------------------------+
|                     TRADER SETTINGS UI                      |
|   Web Dashboard / Telegram Bot / Discord Bot / Config File  |
+------------------------------+------------------------------+
                               |
                               v
+-------------------------------------------------------------+
|                 CONFIGURATION DATABASE / FILE               |
|          config.json / config.yaml / database               |
|                                                             |
|  - Trader Profiles: Scalper, Day Trader, Swing Trader       |
|  - Alert Rules                                              |
|  - Risk Management                                          |
|  - Confluence Rules                                         |
|  - Session Filters                                          |
|  - Notification Preferences                                 |
+------------------------------+------------------------------+
                               |
            +------------------+------------------+
            v                  v                  v
      Pine Script         Python Bot            MT5 EA
     TradingView         Alert Router         Execution

All runtime components read from configuration.
```

---

## ATR Quality Overlay

The ATR quality overlay is a downstream quality and premium-routing feature.
It does not define CRT validity, strategy direction, risk, stop loss, take
profit, lot size, or execution behavior. Its approved live configuration uses
`enabled: true` and `shadow_mode: false`; invalid configuration fails closed.

```yaml
atr_quality_overlay:
  enabled: true
  shadow_mode: false
  version: "1.0"
  method: "SIMPLE_TR_MEAN"
  atr_period: 14
  minimum_premium_grade: "A"
  missing_data_policy: "UNKNOWN"
  parent_a_plus_min_atr_ratio: 0.80
  parent_a_min_atr_ratio: 0.60
  parent_b_min_atr_ratio: 0.45
  sweep_a_plus_min_atr_ratio: 0.25
  sweep_a_min_atr_ratio: 0.20
  sweep_b_min_atr_ratio: 0.15
  advanced_regime_filter_enabled: false
  max_parent_atr_ratio: null
  max_sweep_atr_ratio: null
  volatility_regime_min_ratio: null
  volatility_regime_max_ratio: null
  default_plan: "standard"
  plans:
    standard:
      atr_quality_filter: false
      minimum_grade: null
    premium:
      atr_quality_filter: true
      minimum_grade: "A"
```

The current implementation accepts `SIMPLE_TR_MEAN` only. Wilder's smoothed
ATR remains a separately approved future method. Invalid or incomplete
overlay configuration must fail closed.

## CRT Quality Tightening Shadow Policy

The proposed CRT noise-reduction policy is configured separately from the
approved CRT detector gates. It is evaluated for every directional candidate
and routed only at the subscriber delivery boundary:

```yaml
crt:
  quality_tightening:
    enabled: true
    shadow_mode: true
    version: "1.0"
    min_parent_range_atr_multiple: 0.75
    min_parent_body_ratio: 0.38
    max_sweep_retracement_pct: 0.55
    min_sweep_depth_atr_multiple: 0.22
```

The policy evaluates parent ATR ratio, parent body ratio, external sweep depth,
and sweep retracement. It must never change detector output globally. Standard
subscribers receive valid CRT alerts subject to their own style, pair,
confidence, and notification settings. Subscribers who explicitly enable
`high_quality_only` or Premium Confluence may use the same result as a stricter
delivery filter. See `docs/37_CRT_QUALITY_TIGHTENING.md`.

## Lower-Timeframe FVG Flow

The optional FVG confluence and two-alert watch flow is controlled by a
required top-level `fvg` section. These values are inputs to the detector and
watcher only; they do not define CRT validity, strategy direction, risk, or
execution.

```yaml
fvg:
  enabled: true
  execution_gate_enabled: false
  min_gap_pips: 0.5
  ltf_candle_count: 30
  ltf_atr_period: 14
  watch_max_age_seconds: 14400
```

`min_gap_pips` must be positive, `ltf_candle_count` must be at least 3,
`ltf_atr_period` must be at least 1, and `watch_max_age_seconds` must be
positive. Missing or invalid values fail closed. The scanner does not fall
back to code-defined FVG thresholds, candle windows, ATR periods, or watch
expiry values.

`execution_gate_enabled` controls whether a detected LTF FVG delays the
execution request. The approved live value is `false`: FVG remains a
confidence/confluence input, while a valid, risk-approved CRT is execution
ready immediately. Set it to `true` only for the explicitly approved delayed
FVG-entry workflow, where execution waits for a live price touch of the FVG
zone.

## Webhook Intake Queue

The authenticated TradingView intake persists normalized envelopes in the
durable local queue until the shared PostgreSQL cutover. Queue policy is
operational configuration, not trading logic:

```text
ATLAX_WEBHOOK_STORE_PATH=data/webhook_intake.sqlite
ATLAX_WEBHOOK_MAX_ATTEMPTS=5
ATLAX_WEBHOOK_RETRY_DELAY_SECONDS=30
```

The queue rejects empty paths and non-positive retry limits. Duplicate payloads
are identified by a canonical validated-payload fingerprint and are not
enqueued twice. A downstream consumer must acknowledge an event only after
successful processing; exhausted failures remain in `DEAD_LETTER` for review.

## Nested CRT Routing

Containment-aware nested CRT routing is an approved routing-layer policy. It
does not alter detector definitions, confidence weights, risk, or execution.
The initial relationship suppresses contained M15 repeats and permits only
same-bias M5 events inside an active M15 controller.

```yaml
nested_crt_routing:
  enabled: true
  shadow_mode: false
  relationships:
    - controller_timeframe: M15
      child_timeframes: [M5]
      same_timeframe_policy: suppress_inside
      child_policy: allow_aligned_only
      containment_mode: parent_range_and_child_closes
```

The controller expires on approved mitigation or invalidation, and a later
non-contained M15 event replaces it. Suppressed detector events are retained in
the routing audit ledger; they are not confidence-scored trades.

---

## Configuration Sources

ATLAX may support these configuration interfaces:

- Web dashboard
- Telegram bot settings
- Discord bot settings
- Direct configuration file
- Database-backed configuration

All interfaces must write to the same canonical configuration model.

---

## Configuration File Shape

The canonical configuration may be represented as YAML, JSON, or a database record. The structure below defines expected fields and categories.

Values shown here are template examples unless explicitly approved elsewhere. They are not CRT rules and must not be treated as immutable trading defaults.

```yaml
GENERAL:
  trader_name: "John Doe"
  timezone: "America/New_York"
  account_currency: "USD"
  account_balance: 10000
  auto_restart_on_error: true

PROFILES_ENABLED:
  SCALPER: true
  DAY_TRADER: true
  SWING_TRADER: true

SCALPER_PROFILE:
  enabled: true
  timeframes: ["M5"]
  min_confidence: 60
  max_confidence: 100

  ALERT_RULES:
    alert_all_timeframes: true
    require_confluence: false
    min_aligned_timeframes: 1

  ENTRY:
    auto_execute: false
    entry_model: "MARKET"

  RISK_MANAGEMENT:
    risk_per_trade: 0.5
    max_daily_loss: 2.0
    max_weekly_loss: 5.0
    max_trades_per_day: 10
    max_open_trades: 2
    max_correlated_trades: 1

  STOP_LOSS:
    method: "BELOW_CRT"
    fixed_pips: null
    atr_multiplier: 1.5
    max_sl_pips: 15

  TAKE_PROFIT:
    method: "FIXED_RR"
    fixed_rr: 1.0

  SESSION_FILTER:
    enabled: true
    allowed_sessions: ["LONDON_OPEN", "NEW_YORK_OPEN"]

  NEWS_FILTER:
    enabled: true
    minutes_before_news: 30
    minutes_after_news: 30

  SPREAD_FILTER:
    max_spread_pips: 2.0

DAY_TRADER_PROFILE:
  enabled: true
  timeframes: ["M15", "H1"]
  min_confidence: 70
  max_confidence: 100

  ALERT_RULES:
    alert_all_timeframes: false
    require_confluence: true
    min_aligned_timeframes: 2

  ENTRY:
    auto_execute: false
    entry_model: "LIMIT"

  RISK_MANAGEMENT:
    risk_per_trade: 1.0
    max_daily_loss: 3.0
    max_weekly_loss: 8.0
    max_trades_per_day: 5
    max_open_trades: 2
    max_correlated_trades: 1

  STOP_LOSS:
    method: "BELOW_LIQUIDITY"
    max_sl_pips: 40

  TAKE_PROFIT:
    method: "FIXED_RR"
    fixed_rr: 2.0

  SESSION_FILTER:
    enabled: true
    allowed_sessions: ["LONDON", "NEW_YORK"]

  NEWS_FILTER:
    enabled: true
    minutes_before_news: 60
    minutes_after_news: 60

  SPREAD_FILTER:
    max_spread_pips: 1.5

SWING_TRADER_PROFILE:
  enabled: true
  timeframes: ["H4", "D1"]
  min_confidence: 80
  max_confidence: 100

  ALERT_RULES:
    alert_all_timeframes: false
    require_confluence: true
    min_aligned_timeframes: 3
    require_htf_bias: true

  ENTRY:
    auto_execute: false
    entry_model: "MARKET"

  RISK_MANAGEMENT:
    risk_per_trade: 0.5
    max_daily_loss: 1.5
    max_weekly_loss: 5.0
    max_trades_per_day: 3
    max_open_trades: 2
    max_correlated_trades: 1

  STOP_LOSS:
    method: "BELOW_CRT"
    max_sl_pips: 100

  TAKE_PROFIT:
    method: "FIXED_RR"
    fixed_rr: 3.0

  SESSION_FILTER:
    enabled: false

  NEWS_FILTER:
    enabled: true
    minutes_before_news: 240
    minutes_after_news: 120

  SPREAD_FILTER:
    max_spread_pips: 3.0

  HTF_BIAS:
    enabled: true
    bias_timeframe: "D1"
    bias_requirement: "MUST_MATCH"

ALERT_DEDUPLICATION:
  enabled: true
  min_seconds_between_same_pair: 120
  group_same_direction: true

ALERT_ROUTING:
  notification_method: "TELEGRAM"
  telegram_bot_token: "YOUR_TOKEN"
  telegram_chat_id: "YOUR_CHAT_ID"
  show_confidence_score: true
  show_entry_details: true
  show_risk_reward: true
  show_recommended_profile: true

PATTERN_DETECTION_RULES:
  CRT:
    enabled: true
    quality_threshold: "MEDIUM"

  LIQUIDITY_SWEEP:
    enabled: true
    min_sweep_pips: 5
    max_sweep_pips: 50

  MARKET_STRUCTURE:
    enabled: true

  FAIR_VALUE_GAP:
    enabled: true
    min_fvg_pips: 10

  SESSION_TIMING:
    enabled: true

MONITORING:
  pairs_to_monitor:
    - "EURUSD"
    - "GBPUSD"
    - "AUDUSD"
    - "NZDUSD"

  excluded_pairs: []

  timeframes_to_scan:
    - "M5"
    - "M15"
    - "M30"
    - "H1"
    - "H4"
    - "D1"

LOGGING:
  log_every_setup: true
  log_rejected_setups: true
  log_execution: true
  log_level: "INFO"
  max_log_days: 30
  screenshot_on_alert: true
```

---

## Platform Integration Settings

TradingView webhook and MT5 execution behavior require explicit configuration.

TradingView-related settings should include:

- Webhook endpoint URL.
- Webhook authentication method.
- Webhook secret reference, stored outside committed files.
- Expected content type.
- Expected schema version.
- Enabled symbols.
- Enabled timeframes.
- Script version.
- Settings version.
- Alert regeneration status.
- Webhook timeout handling policy.
- Raw-payload retention policy.

MT5-related settings should include:

- Execution enabled flag.
- Manual approval required flag.
- Broker symbol mapping.
- Maximum slippage or deviation.
- Filling policy.
- Order time policy.
- Magic number or ATLAX execution identifier.
- Comment template with sanitized candidate reference.
- Pre-flight order-check requirement.
- Execution retry policy.
- Position reconciliation policy.
- Positive-profit breakeven and partial-profit management policy.

These settings configure platform integration only. They do not define CRT detection, strategy direction, confidence scoring, or risk limits.

With `execution.move_sl_to_breakeven: true`, the Trade Manager protects an
open position after the broker reports positive live profit; TP1 is not the
breakeven trigger. TP1 partial close is separately guarded against an actual
market fill that makes the configured midpoint unfavorable or still losing.

MFE/profit-lock protection is configured separately. The repository's live
configuration below enables protective stop tightening after the approved
promotion; `TIGHTEN_STOP` is the only live action.

```yaml
profit_protection:
  enabled: true
  shadow_mode: false
  activation_r: 0.25
  minimum_lock_r: 0.05
  maximum_giveback_r: 0.20
  confirmation_bars: 2
  momentum_loss_enabled: true
  momentum_shadow_mode: false
  momentum_source_timeframe: H1
  minimum_current_profit_r: 0.05
  momentum_action: TIGHTEN_STOP
  close_remaining_on_momentum_loss: false
  include_transaction_cost_buffer: true
  trailing_stop_enabled: true
  trailing_activation_r: 0.25
  trailing_distance_r: 0.20
  trailing_step_r: 0.05
  target_proximity_fraction: 0.80
```

`activation_r`, `minimum_lock_r`, and `maximum_giveback_r` control the
normalized-R lock calculation described in the Execution Engine
specification. With `trailing_stop_enabled: true`, the live manager begins
ratcheting after `trailing_activation_r` and keeps the stop
`trailing_distance_r` behind the best favorable excursion, advancing only by
`trailing_step_r` or more. With `shadow_mode: false`, each successful trail or
qualifying MFE giveback submits a protective SL tightening, preserves TP2, and
never moves an existing stop backward. A failed broker modification does not
advance the persisted lock, so it is retried. Phase 3 uses only closed candles from `momentum_source_timeframe`;
the configured number of consecutive opposing candles, positive current-R
floor, and MFE giveback must all qualify. With
`momentum_shadow_mode: false`, the approved `TIGHTEN_STOP` action submits the
same protective SL update. Partial and full close actions remain fail-closed
until separately approved and tested.

`target_proximity_fraction` is the required fraction of the favorable path
from the actual fill to TP2 before trailing or giveback stop tightening can
apply. TP1 is never used as this reference; it remains only the separately
guarded partial-close target. The value must be within `[0, 1]`.

When `include_transaction_cost_buffer` is true, ATLAX uses available broker
commission, swap, and fee data plus MT5 tick economics to place breakeven
above entry for BUY positions or below entry for SELL positions. If the broker
does not expose valid cost data, ATLAX logs `breakeven_cost_buffer_unknown`
and falls back to the actual entry price rather than inventing a cost.
Missing or invalid values fail closed through the Settings Engine.

---

## Payment Settings

ATLAX subscription payments are external and must be configurable.

Payment settings should include:

- Subscription price in USD.
- Separate Premium subscription price in USD.
- Subscription duration in days.
- Default routing style for new activations.
- Crypto wallet destination details.
- Crypto network and asset labels.
- Named crypto rails may be configured independently for USDT on TON through a
  Web3 wallet address and Solana (SOL).
- M-Pesa merchant Lipa Namba and account reference.
- Optional M-Pesa subscription amount in TZS; when omitted, the customer uses
  the current TZS equivalent of the selected USD plan. Exchange rates must be
  supplied by the approved operator and must not be invented by code.
- Payment intent lifetime before expiry.
- Pre-expiry reminder windows in hours.
- Support contact for failed or ambiguous payments.
- Manual review toggle for proof-based confirmation.
- Optional secure card checkout URL. Card intents are unavailable until a
  provider checkout URL is configured.

The production plan prices are configured as Standard `$15 USD` and Premium
`$50 USD` for the configured subscription duration. Use
`ATLAX_PAYMENT_PRICE_USD` and `ATLAX_PREMIUM_PAYMENT_PRICE_USD` for
environment-level overrides.

Payment confirmation must activate subscriber access only after a reviewed confirmation path succeeds. Payment configuration must not hardcode payout targets or silently invent payment rail behavior.

Subscription lifecycle settings include payments.expiry_reminder_hours,
defaulting to [72, 24], and the comma-separated environment override
ATLAX_SUBSCRIPTION_EXPIRY_REMINDER_HOURS.

Payment references default to a one-hour lifetime (`ATLAX_PAYMENT_TTL_HOURS=1`)
so abandoned checkout attempts do not remain in the review queue. An operator
may increase or decrease this positive whole-hour setting when the approved
payment policy changes.

The lifecycle worker uses UTC and runs with the scanner poll cycle. An empty
YAML list disables pre-expiry reminders. Expiry suspension remains active even
when market scanning is paused.

## Shared Storage Settings

ATLAX supports a local development backend and a PostgreSQL migration target.
The PostgreSQL storage contract is defined in docs/23_POSTGRES_STORAGE.md.

- ATLAX_STORAGE_BACKEND: local or postgres.
- ATLAX_DATABASE_URL: complete PostgreSQL connection URL; environment only.
- ATLAX_DATABASE_SSLMODE: TLS mode for PostgreSQL connections.
- ATLAX_DATABASE_CONNECT_TIMEOUT_SECONDS: connection timeout.

The local backend remains the default until every business repository has been
migrated and verified. Invalid PostgreSQL configuration must fail closed.

---

## Trader Profiles

### Operational Timeframe Policy

M1 is retired from active ATLAX runtime use because its alert stream is too
noisy. It must not be added to the active instrument list, profile routes,
Telegram menus, public timeframe filters, MT5 feed construction, or webhook
intake without an explicit operational review. Historical rulebook references
are retained as research context and do not authorize current delivery.

ATLAX supports configurable trader profiles.

### Scalper Profile

Default intent:

- Monitor M5.
- Allow lower minimum confidence than longer-term profiles.
- Support frequent alerts.
- Default to manual approval for execution.
- Use strict daily trade and daily loss controls.

### Day Trader Profile

Default intent:

- Monitor M15 and H1.
- Require stronger confidence.
- Require multi-timeframe confluence.
- Default to manual approval for execution.
- Use stricter alert filtering than the scalper profile.

### Swing Trader Profile

Default intent:

- Monitor H4 and D1.
- Require the highest confidence.
- Require strict confluence.
- Allow HTF bias requirements.
- Default to manual approval for execution.

These profile defaults are configurable settings, not hardcoded strategy behavior.

---

## Trader Configuration Interfaces

### Runtime Environment

Secrets and deployment-specific authentication settings must be provided through environment variables, never committed configuration files.

The Telegram Mini App uses:

- `ATLAX_TELEGRAM_BOT_TOKEN` for server-side `WebApp.initData` signature validation.
- ATLAX_TELEGRAM_MINI_APP_URL for the public HTTPS URL used by the inline Open ATLAX button and persistent Telegram chat menu button.
- `ATLAX_TELEGRAM_INIT_DATA_MAX_AGE_SECONDS` for the accepted age of a signed bootstrap payload. The default is `900` seconds.
- `ATLAX_AUTH_COOKIE_SECURE=true` in HTTPS deployments so dashboard and Mini App cookies are sent only over TLS.

Invalid or missing security settings must fail closed and be logged without exposing secret values.

ATLAX AI provider credentials are environment-only:

- `ATLAX_AI_API_KEY` is the primary provider credential. The current primary
  provider is configured as Gemini in `config/atlax.yaml`.
- `ATLAX_AI_FALLBACK_API_KEY` is the fallback provider credential. The current
  fallback provider is configured as Groq in `config/atlax.yaml`.
The configured model must be available to the provider account. If the
primary provider is unavailable, ATLAX uses the configured fallback model;
an unavailable fallback model leaves the answer unavailable and is logged as
a provider failure. Verify both model IDs against the provider account before
restarting the scanner.

The native scanner loads `.env` before reading analytics configuration. Docker
Compose passes the same file through `env_file`. Empty provider credentials
leave external AI calls unavailable; ATLAX must not use a guessed credential or
send secrets in a prompt.

### Web Dashboard

The dashboard is the preferred user experience.

Expected controls include:

- General settings
- Account balance
- Timezone
- Profile enablement
- Profile confidence thresholds
- Profile risk settings
- Profile timeframes
- Auto-execution toggle
- Confluence requirements
- Maximum daily loss
- Alert routing settings
- Subscriber key-level-only delivery preference
- Pairs to monitor
- Save, reset, and export actions

### Telegram Bot Settings

The Telegram bot may expose commands such as:

```text
/settings
/set_scalper_risk 1.0
/enable_day_trader
/disable_confluence
```

Commands must update canonical configuration immediately and must pass validation before saving.

### Direct Config File

Traders may edit the configuration file directly.

The application must validate the edited configuration before applying it.

---

## System Usage

### TradingView

TradingView is responsible for detector-side detection, visualization, and alert event generation.

Any TradingView-provided detector score must be documented as detector metadata and must not replace the downstream Confidence Engine.

TradingView settings must be supplied through generated inputs or synchronized configuration. TradingView must not execute trades.

### Python Bot

The Python bot is responsible for alert routing and orchestration.

It must:

- Read the canonical configuration.
- Reload settings when changes occur.
- Reject alerts that do not satisfy configured profile requirements.
- Respect configured risk and alert routing settings.
- Log accepted and rejected alerts with reasons.

### MT5 EA

The MT5 EA is responsible for execution and trade management only.

It must:

- Read execution and risk configuration.
- Calculate lot size from configuration.
- Enforce stop-loss, take-profit, and trade-management settings.
- Refuse execution when configuration is invalid or risk limits are exceeded.
- Never perform complex detection.

---

## Settings Validation

Settings must be validated before they are applied.

Required validation includes:

- `risk_per_trade` must not exceed `max_daily_loss`.
- Every configured timeframe must be recognized.
- `min_aligned_timeframes` must not exceed the number of configured profile timeframes.
- Confidence values must be between 0 and 100.
- Maximum values must not be lower than minimum values.
- Required credentials must exist before a notification method is enabled.
- Risk and execution settings must be present before auto-execution can be enabled.
- Unknown setting keys should be rejected unless explicitly supported by a versioned migration.

Invalid configuration must fail closed:

- Do not apply the new settings.
- Keep the last known valid settings when available.
- Log the validation failure.
- Return a clear recovery suggestion.

---

## Adaptive Non-Gold Lot Targets

The Risk Engine's approved daily-loss lot ladder is configured under `risk`:

```yaml
risk:
  adaptive_lot_sizing:
    enabled: true
    excluded_symbol_prefixes: [XAU]
    normal_lot: 1.00
    caution_daily_loss_pct: 1.00
    caution_lot: 0.80
    recovery_daily_loss_pct: 2.00
    recovery_lot: 0.50
```

For every non-excluded symbol, the engine uses 1.00 lot below 1% realized
daily loss, 0.80 at or above 1%, and 0.50 at or above 2%. The fixed target is
not permission to exceed the broker-derived risk-safe lot. A trade is blocked
when the target cannot be safely supported by its stop-loss, broker volume
constraints, or active risk budget. `XAU`-prefixed gold symbols retain normal
broker-derived sizing.

## Real-Time Settings Updates

The system should support live settings updates without requiring a full restart.

### H1 Momentum Confidence

The optional `confidence.htf_momentum` section controls the H1 evidence path:

```yaml
confidence:
  htf_momentum:
    enabled: false
    shadow_mode: true
    controller_timeframe: H1
    child_timeframes: [M5, M15]
    lookback_candles: 6
    strong_run_candles: 2
    aligned_adjustment: 0
    opposing_adjustment: 0
    sideways_adjustment: 0
    unknown_adjustment: 0
    run_bonus_per_extra_candle: 0
    max_run_bonus: 0
```

`shadow_mode` records H1 bias, relationship, run length, and the proposed
counterfactual score without changing live delivery or execution. Promotion
requires the documented ADR approval and non-zero numeric values supplied by
configuration.

Default behavior:

- Watch configuration changes.
- Reload after a detected change.
- Validate before applying.
- Apply only valid configuration.
- Log the reload result.

The proposed file watcher interval is 5 seconds.

---

## Configurable Settings Summary

| Setting | Type | Options |
| --- | --- | --- |
| Profile Enabled | Checkbox | Scalper, Day Trader, Swing Trader |
| Timeframes | Multi-select | M5, M15, M30, H1, H4, D1 |
| Min Confidence | Number | 0-100 |
| Auto Execute | Checkbox | Yes / No, manual approval by default |
| Require Confluence | Checkbox | Yes / No |
| Risk Per Trade | Percent | 0.1-5 |
| Max Daily Loss | Percent | 1-10 |
| Max Trades Per Day | Number | 1-20 |
| Stop Loss Method | Select | Below CRT, Below Liquidity, ATR, Fixed Pips |
| Take Profit Method | Select | Fixed RR, Opposite CRT, Liquidity |
| Session Filter | Multi-select | London, New York, Asian, configured sessions |
| News Filter | Minutes | Before and after news |
| Spread Filter | Pips | Maximum spread allowed |
| HTF Bias | Checkbox | Require alignment with configured higher timeframe |

### Controlled Automatic Execution

`execution.require_alert_eligibility` is a fail-closed execution guard:

- `true`: an MT5 entry requires the alert route to report a successful
  Telegram delivery. Suppressed, failed, unavailable, and log-only alerts do
  not create executable requests.
- `false`: execution may proceed after the normal Risk Engine and Approval
  Engine checks even when Telegram delivery is not eligible. This is intended
  only for a separately approved operational mode and must be documented.

The guard applies to ordinary alerts and to both alert stages of the deferred
FVG workflow. It does not change per-subscriber routing thresholds; it only
prevents an unannounced live entry.

`approval.require_manual` controls the final handoff into the MT5 execution
consumer:

- `true`: risk-approved requests remain `PENDING` until an authorized admin
  approves them.
- `false`: risk-approved requests enter the approved queue automatically.

Setting it to `false` is a live-trading deployment decision. It does not
disable risk limits, order preflight, broker validation, execution logging, or
post-send reconciliation. MT5 Algo Trading must also be enabled in the
terminal, and the change requires a scanner restart.

---

## Implementation Requirements

- Configuration access must go through a dedicated settings/configuration service.
- Runtime services must not read raw files directly if a configuration service exists.
- All settings must have a documented schema.
- All settings must have validation rules.
- All setting changes must be logged.
- Sensitive values, such as bot tokens and credentials, must not be committed.
- Defaults may exist as documented templates, but runtime behavior must remain overrideable.
- Configuration must be versioned to support migrations.

---

## Boundary Reminder

This document defines configurable knobs and settings flow.

It does not define CRT detection rules.

It does not authorize hardcoded strategy behavior.

It does not replace `docs/rulebooks/CRT_RULEBOOK.md`.
Subscriber routing styles are:

| Style | Routed timeframes |
| --- | --- |
| `scalper` | `M5` |
| `day_trader` | `M15`, `H1` |
| `swing_trader` | `H4`, `D1` |
| `all_styles` | `M5`, `M15`, `H1`, `H4`, `D1` |

`all_styles` is a subscriber delivery option. It does not create a new strategy profile or change detector behavior.

## Admin Advisory Review Settings

```yaml
analytics:
  ai_advisory_enabled: true
  ai_performance_min_trades: 10
```

`ai_performance_min_trades` is the minimum number of resolved journal
outcomes required before the standalone advisory performance review reports
an observed edge. It is a reporting-quality guard only; it does not suppress
signals, change confidence, or alter execution.

## ATLAX AI Settings

```yaml
analytics:
  ai_prop_firm_daily_limit: 10
  ai_prop_firm_cooldown_seconds: 10
  ai_subscriber_enabled: true
  ai_subscriber_daily_limit: 10
  ai_subscriber_per_minute_limit: 3
  ai_subscriber_cooldown_seconds: 10
  ai_subscriber_active_timeout_seconds: 120
  ai_subscriber_abuse_strike_limit: 3
  ai_subscriber_max_question_chars: 600
  google_sheets_webhook_url: ''
  heatmap_path: data/heatmap.json
  faq_path: docs/20_PUBLIC_FAQ.md
  replay_enabled: true
  replay_context_bars_before_parent: 30
  replay_outcome_forward_bars: 50
  replay_render_timeout_seconds: 5
  replay_image_width: 1600
  replay_image_height: 900
  replay_image_max_bytes: 2097152
  replay_cache_dir: data/replay_cache
  replay_renderer_version: "1.0"
  replay_request_cooldown_seconds: 10
```

`ai_subscriber_enabled` is independent from the admin advisory reports.
`ai_prop_firm_daily_limit` and `ai_prop_firm_cooldown_seconds` cap admin
requests that load the selected Prop-Firm profile and protection snapshot for
the external AI provider. A rate-limited request is answered without sending
that Prop-Firm context. These controls do not change trading risk or signal
routing.
`ai_subscriber_daily_limit` caps provider calls per Telegram chat per UTC day.
`ai_subscriber_per_minute_limit` and `ai_subscriber_cooldown_seconds` protect
provider and scanner capacity. `ai_subscriber_active_timeout_seconds` clears
a stale in-flight marker after a process interruption. The abuse strike limit
controls automatic ATLAX AI blocking; it does not deactivate signals.
`ai_subscriber_max_question_chars` limits untrusted prompt size.
The heatmap path is a configured internal output location, not user input.
`faq_path` points to the reviewed public FAQ used by the subscriber interface
and ATLAX AI. It must point only to public product information and must
not contain credentials, private analytics, internal rulebooks, or source code.
`replay_enabled` controls optional analytics snapshot capture and does not
disable live signals when false. The replay context and forward windows are
presentation/storage limits only; they do not change detector input or tracker
expiry behavior. The renderer settings bound the static PNG dimensions, file
size, render time, derived cache location, and renderer cache version. The
request cooldown applies per Telegram chat. Replay images are derived data;
the validated MT5 snapshot remains authoritative.

## Automatic Payment Verification

Payment verification is disabled by default in the repository template. When
enabled, it remains in shadow mode until an explicit operator change disables
shadow mode:

```yaml
payments:
  automatic_verification:
    enabled: false
    shadow_mode: true
    timeout_seconds: 15
    clock_skew_seconds: 300
    ton_api_url: https://toncenter.com/api/v3/jettons/transfers
    ton_usdt_decimals: 6
    solana_token_decimals: 6
```

Provider credentials, token allowlists, RPC URLs, and token mints are
environment-only. Only a verified, exact-amount crypto transfer can be
auto-confirmed, and only when `shadow_mode` is explicitly false. TON USDT is
checked against the configured Jetton master and destination wallet. Solana
requires a configured RPC endpoint, token mint, destination wallet, and token
decimals. It also requires a parsed `transfer` or `transferChecked` instruction
whose destination token account resolves through the transaction account keys
to a token account owned by the configured ATLAX wallet; a matching balance
change without that explicit transfer is rejected. M-Pesa is manual review.
Provider outages, missing configuration,
unfinalized transactions, and mismatches fail closed and remain pending for
review. When automatic verification is enabled, administrator approval cannot
override an unverified crypto proof; shadow mode delays automatic activation
but does not make crypto verification optional.

The transaction timestamp must be at or after the payment reference creation
time, allowing only the configured clock skew. This prevents a real but old
on-chain transfer from being reused for a new checkout reference.
