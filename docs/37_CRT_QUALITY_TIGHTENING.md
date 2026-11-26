# CRT Quality Tightening

Version: 1.0  
Status: GLOBAL DETECTOR GATE DISABLED - SUBSCRIBER FILTER LIVE  
Date: 2026-08-15  
Owner: ATLAX Engineering

## Purpose

Measure whether stricter CRT quality requirements reduce weak alerts while
preserving the current approved detector and subscriber delivery behavior.
The desired volume of approximately 15 signals per day is a measurement goal,
not a hardcoded daily quota.

## Shadow Configuration

The policy is configured under `crt.quality_tightening`:

```yaml
quality_tightening:
  enabled: true
  shadow_mode: true
  version: "1.0"
  min_parent_range_atr_multiple: 0.75
  min_parent_body_ratio: 0.38
  max_sweep_retracement_pct: 0.55
  min_sweep_depth_atr_multiple: 0.22
```

These values are evaluated and logged but cannot reject a detector output,
change confidence, suppress standard Telegram delivery, or change execution.
The separate subscriber preference below may use the same counterfactual result
for an explicit opt-in delivery filter. `shadow_mode` records the governance
mode of the audit evaluation; it is not a global delivery switch.

## Subscriber Quality Preference

Subscribers may opt into `high_quality_only` from the Telegram settings menu
or with `/settings quality on`. The preference is disabled by default.

Subscribers may also enable the single combined `confluence_only` preference
from **Premium Confluence** or with `/settings confluence on`. This master
route requires both quality approval and a major higher-timeframe key-level
match for the same setup.

- Standard subscribers continue to receive the existing valid alert route.
- High-Quality Only subscribers receive a candidate only when its quality
  evaluation is `VALID` and `would_suppress` is false.
- Premium Confluence subscribers additionally require
  `parent_at_htf_zone == true` on that same candidate.
- Missing quality metadata and `UNKNOWN` evaluations are excluded from the
  stricter subscriber route.
- The preference applies to immediate CRT alerts and both FVG watch and FVG
  entry alerts.

This is a subscriber delivery preference, not global live enforcement. ATLAX
keeps the detector and standard delivery behavior unchanged while collecting
comparison data. A global promotion of this policy is intentionally not
supported because it would make one quality choice suppress alerts for users
with different plans and settings.

## Mathematical Rules

For a parent candle `p`, sweep candle `s`, and ATR `A`:

```text
P = (p.high - p.low) / A
B = abs(p.close - p.open) / (p.high - p.low)
```

External sweep depth is direction-specific:

```text
Bullish depth = p.low - s.low
Bearish depth = s.high - p.high
S = external sweep depth / A
```

A candidate is marked `would_suppress` when any of these conditions is true:

```text
P < 0.75
B < 0.38
S < 0.22
sweep retracement exceeds 0.55 of the parent range
```

Exact threshold equality passes. Missing or invalid ATR produces `UNKNOWN`
and never counts as a suppression.

## Observability

Each directional candidate receives a structured
`crt_quality_tightening_evaluated` event with:

- policy version and mode;
- symbol and timeframe;
- evaluation status and direction;
- parent ATR ratio, parent body ratio, sweep-depth ATR ratio;
- retracement ratio;
- stable reason codes;
- current detector result and counterfactual `would_suppress` result.

The persisted operations ledger reports:

- `evaluated`;
- `unknown`;
- `would_suppress`;
- counts by reason.

## Promotion Gate

Keep shadow mode enabled for 3-5 trading days. Before live enforcement,
compare by symbol, timeframe, trading style, and plan:

1. current signal volume versus counterfactual volume;
2. outcomes of candidates marked `would_suppress` versus retained candidates;
3. premium and standard delivery impact;
4. nested CRT routing impact;
5. missing-data and UNKNOWN rates.

Any future change to this boundary requires an explicit architecture decision
and updated subscriber-routing tests. Do not use
`crt.quality_tightening.shadow_mode` as a global subscriber filter.
