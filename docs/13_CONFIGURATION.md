# ATLAX Configuration Specification

## Settings Engine

Version: 1.0  
Status: Active  
Purpose: Define how ATLAX stores, validates, reloads, and applies trader-controlled settings.

---

## Core Rule

Everything must be configurable.

No strategy decision, risk value, threshold, timeframe, session, spread, symbol, notification preference, or execution parameter may be hidden in code.

Configuration can control how documented rules are applied, but configuration must never invent trading logic. CRT behavior still belongs only in `docs/rulebooks/CRT_RULEBOOK.md`.

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
  timeframes: ["M1", "M5", "M15"]
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
  timeframes: ["M15", "M30", "H1"]
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
  timeframes: ["H1", "H4", "D1"]
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
    - "M1"
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

These settings configure platform integration only. They do not define CRT detection, strategy direction, confidence scoring, or risk limits.

---

## Trader Profiles

ATLAX supports configurable trader profiles.

### Scalper Profile

Default intent:

- Monitor M1, M5, and M15.
- Allow lower minimum confidence than longer-term profiles.
- Support frequent alerts.
- Default to manual approval for execution.
- Use strict daily trade and daily loss controls.

### Day Trader Profile

Default intent:

- Monitor M15, M30, and H1.
- Require stronger confidence.
- Require multi-timeframe confluence.
- Default to manual approval for execution.
- Use stricter alert filtering than the scalper profile.

### Swing Trader Profile

Default intent:

- Monitor H1, H4, and D1.
- Require the highest confidence.
- Require strict confluence.
- Allow HTF bias requirements.
- Default to manual approval for execution.

These profile defaults are configurable settings, not hardcoded strategy behavior.

---

## Trader Configuration Interfaces

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

## Real-Time Settings Updates

The system should support live settings updates without requiring a full restart.

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
| Timeframes | Multi-select | M1, M5, M15, M30, H1, H4, D1 |
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
