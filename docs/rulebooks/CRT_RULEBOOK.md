# CRT Rulebook

Version: 1.0  
Status: APPROVED  
Approved By: Project Lead  
Approved Date: 2026-07-14  
Updated: 2026-07-14  
Purpose: Define the authoritative Candle Range Theory rules for ATLAX.

---

## Critical Status

This rulebook contains **APPROVED** rules promoted from multi-source community research on Romeo's (Romeotpt) Candle Range Theory, reviewed and approved by the project lead on 2026-07-14.

Implementation may consume rules marked `APPROVED`.

Rules marked `UNKNOWN` remain unresolved and must not be implemented.

See `docs/rulebooks/CRT_RESEARCH_LOG.md` for the full research evidence behind each rule.

---

## Rulebook Authority

This is the only document allowed to define CRT behavior.

Implementation must consume these rules.

Implementation must never invent CRT rules.

External sources, including websites, articles, videos, transcripts, screenshots, and AI summaries, are research inputs only. They become ATLAX rule authority only when approved in this document by the project lead.

---

## Source Policy

Every CRT rule must include at least one approved source reference.

Allowed source references:

- Project lead confirmation. ✓ (2026-07-14)
- Official Romeo / Romeotpt video link with timestamp.
- Official Romeo transcript excerpt or project-provided notes.
- Project-approved screenshot or chart example.
- Another explicitly approved primary source.

Source capture and promotion workflows:

- `docs/rulebooks/CRT_SOURCE_INTAKE.md`
- `docs/rulebooks/CRT_RULE_APPROVAL.md`

---

## Research and Approval History

- **2026-07-14**: Internet and YouTube research conducted. Romeo identified as **Romeotpt**. Multi-source community findings captured.
- **2026-07-14**: All researched rules reviewed and approved by project lead.

Full research evidence: `docs/rulebooks/CRT_RESEARCH_LOG.md`

---

## Rule Status Labels

- `APPROVED`: Project lead approved; implementation may consume it.
- `PROPOSED`: Drafted from research, awaiting project lead approval.
- `REJECTED`: Explicitly rejected.
- `UNKNOWN`: Not defined clearly enough to implement.

Only `APPROVED` rules may be implemented.

---

## Terminology

| Term | Definition | Status | Source |
| --- | --- | --- | --- |
| CRT | Candle Range Theory. A price action methodology attributed to Romeo (Romeotpt) that treats each higher-timeframe candlestick as a self-contained range following an AMD (Accumulation, Manipulation, Distribution) cycle. | APPROVED | CRT-SOURCE-001, CRT-SOURCE-002, CRT-SOURCE-003; Project Lead 2026-07-14 |
| Open | First price of the candle period. | APPROVED | CRT-SOURCE-GEN-001 through GEN-004 |
| High | Highest price of the candle period. When used as the upper boundary of the parent candle range, also called **CRT High (CRH)**. | APPROVED | CRT-SOURCE-001, CRT-SOURCE-002 |
| Low | Lowest price of the candle period. When used as the lower boundary of the parent candle range, also called **CRT Low (CRL)**. | APPROVED | CRT-SOURCE-001, CRT-SOURCE-002 |
| Close | Last price of the candle period. Close location relative to the parent range is a key validation condition. | APPROVED | CRT-SOURCE-001 |
| Candle Range | High minus low for a candle. The CRT range is the high-to-low span of the parent (reference) candle. | APPROVED | CRT-SOURCE-GEN-001; CRT-SOURCE-002 |
| Body / Real Body | Price area between the candle open and close. Relevant for close-back validation. | APPROVED | CRT-SOURCE-GEN-002; CRT-SOURCE-001 |
| Wick / Shadow / Tail | Candle portion outside the body extending toward the high or low. A wick sweep is valid if the body closes back inside the range. | APPROVED | CRT-SOURCE-GEN-002; CRT-SOURCE-001 |
| Parent Candle | The higher-timeframe reference candle whose high (CRH) and low (CRL) define the CRT range. Typically located at or near a significant HTF zone. Also called Reference Candle. | APPROVED | CRT-SOURCE-001, CRT-SOURCE-002, CRT-SOURCE-003; Project Lead 2026-07-14 |
| CRT Candle | The sweep candle. The candle directly after the parent candle that raids the parent's high or low and closes back inside the parent range. | APPROVED | CRT-SOURCE-001, CRT-SOURCE-002 |
| CRT High (CRH) | The high of the parent candle. Upper boundary of the CRT range. Buy-side liquidity rests above this level. | APPROVED | CRT-SOURCE-001, CRT-SOURCE-002 |
| CRT Low (CRL) | The low of the parent candle. Lower boundary of the CRT range. Sell-side liquidity rests below this level. | APPROVED | CRT-SOURCE-001, CRT-SOURCE-002 |
| Sweep | Price movement beyond the CRH or CRL. Also called a "raid," "liquidity grab," or "manipulation." | APPROVED | CRT-SOURCE-001, CRT-SOURCE-002, CRT-SOURCE-003 |
| Liquidity Sweep | Synonym for Sweep. The process of price running stop-loss orders resting beyond the parent candle's high or low before reversing. | APPROVED | CRT-SOURCE-001, CRT-SOURCE-003 |
| Displacement | A large-bodied, impulsive candle following the sweep that signals the start of the distribution/delivery phase. Also called distribution candle or expansion move. | APPROVED | CRT-SOURCE-001, CRT-SOURCE-002 |
| Confirmation Candle | A candle that closes above the sweep candle's high (bullish) or below the sweep candle's low (bearish), signaling a market structure shift confirming the CRT setup. | APPROVED | CRT-SOURCE-001, CRT-SOURCE-006 |
| Invalidation | Condition that cancels a CRT setup. Primarily: the sweep candle's body closes outside the parent candle range. | APPROVED | CRT-SOURCE-002, CRT-SOURCE-003 |
| Bullish CRT | A CRT setup where price sweeps the CRL, closes back inside the range, and is expected to deliver to the CRH. Detector may return `bullish_crt` as pattern metadata. Never returns `BUY`. | APPROVED | CRT-SOURCE-001, CRT-SOURCE-002; Project Lead 2026-07-14 |
| Bearish CRT | A CRT setup where price sweeps the CRH, closes back inside the range, and is expected to deliver to the CRL. Detector may return `bearish_crt` as pattern metadata. Never returns `SELL`. | APPROVED | CRT-SOURCE-001, CRT-SOURCE-002; Project Lead 2026-07-14 |
| AMD | Accumulation, Manipulation, Distribution. The ICT Power of Three cycle that CRT applies to individual candles. | APPROVED | CRT-SOURCE-001, CRT-SOURCE-003 |
| MSS | Market Structure Shift. A lower-timeframe structural break used to confirm the CRT entry. Also called Change of Character (ChoCh) or Break of Structure (BOS). | APPROVED | CRT-SOURCE-001, CRT-SOURCE-003 |
| Mean Threshold | The 50% midpoint of the parent candle range or of an FVG/order block. A key take-profit target or precision entry level. | APPROVED | CRT-SOURCE-003, CRT-SOURCE-007 |
| Kill Zone | A high-volatility trading session window where CRT setups have the highest probability: London Open and New York AM open. | APPROVED | CRT-SOURCE-001, CRT-SOURCE-003 |
| FVG | Fair Value Gap. A price imbalance (gap between candle bodies) that forms after a displacement move. Used as a precision entry area after the sweep is confirmed. | APPROVED | CRT-SOURCE-001, CRT-SOURCE-003 |
| Mitigation | A CRT zone is considered mitigated (used up) once price has reached the opposite extreme of the parent candle range. Do not look for repeat CRT entries in a mitigated zone. | APPROVED | CRT-SOURCE-002, CRT-SOURCE-004; Project Lead 2026-07-14 |

---

## Rule Index

| Rule ID | Category | Description | Status |
| --- | --- | --- | --- |
| CRT-DATA-001 | data_requirements | OHLC data required; no volume/tick/Heikin-Ashi | APPROVED |
| CRT-DATA-002 | data_requirements | Closed candles only | APPROVED |
| CRT-DATA-003 | data_requirements | All standard timeframes supported | APPROVED |
| CRT-PARENT-001 | parent_candle | Parent candle defines CRH and CRL | APPROVED |
| CRT-PARENT-002 | parent_candle | Parent candle at HTF structural zone = quality signal | APPROVED |
| CRT-PARENT-003 | parent_candle | 3-candle sequence structure | APPROVED |
| CRT-SWEEP-001 | crt_candle | Bullish sweep: sweep.low < parent.low | APPROVED |
| CRT-SWEEP-002 | crt_candle | Bearish sweep: sweep.high > parent.high | APPROVED |
| CRT-SWEEP-003 | crt_candle | Sweep body must close back inside parent range | APPROVED |
| CRT-BULL-001 | bullish_crt | Full bullish CRT 3-condition sequence | APPROVED |
| CRT-BEAR-001 | bearish_crt | Full bearish CRT 3-condition sequence | APPROVED |
| CRT-SWEEP-VALID-001 | liquidity_sweep | Valid sweep conditions | APPROVED |
| CRT-SWEEP-INVALID-001 | liquidity_sweep | Invalid sweep (body outside = breakout) | APPROVED |
| CRT-CLOSE-001 | close_requirement | Close-back inside range requirement | APPROVED |
| CRT-INVALID-001 | invalidation | Body close outside = invalidation | APPROVED |
| CRT-INVALID-002 | invalidation | Mitigated zone definition | APPROVED |
| CRT-INVALID-003 | invalidation | Stop-loss breach invalidation | APPROVED |
| CRT-QUALITY-001 | quality_metadata | Quality metadata fields | APPROVED |
| CRT-MTF-001 | multi_timeframe | Timeframe pairings | APPROVED |
| CRT-MTF-002 | multi_timeframe | Fractal CRT nesting | APPROVED |
| CRT-SESSION-001 | session_timing | Kill zone session filter | APPROVED |
| CRT-STRAT-001 | strategy_boundary | Detector output boundary | APPROVED |

---

## Section 1: Data Requirements

### CRT-DATA-001

```yaml
rule_id: CRT-DATA-001
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-14
category: data_requirements
source_ids: [CRT-SOURCE-001, CRT-SOURCE-002, CRT-SOURCE-003, CRT-SOURCE-GEN-001]
rule_text: >
  CRT uses standard OHLC (Open, High, Low, Close) candle data.
  No volume, tick data, or alternative candle types (Heikin-Ashi, Renko, etc.)
  are required for CRT pattern detection.
deterministic_condition: >
  Input data must contain candle.open, candle.high, candle.low, candle.close,
  and candle.openTime for each candle evaluated.
required_inputs:
  - candle.open
  - candle.high
  - candle.low
  - candle.close
  - candle.openTime
  - candle.timeframe
  - candle.symbol
implementation_notes: Standard OHLC is sufficient for all detection logic.
```

### CRT-DATA-002

```yaml
rule_id: CRT-DATA-002
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-14
category: data_requirements
source_ids: [CRT-SOURCE-001, CRT-SOURCE-002, CRT-SOURCE-003]
rule_text: >
  CRT evaluation must use CLOSED candles only.
  A forming (live, unclosed) candle must not be used to confirm a CRT sweep
  or close-back condition, as these are evaluated on final OHLC values.
deterministic_condition: >
  candle.isClosed == true
  All CRT conditions (parent, sweep, confirmation) must be evaluated on closed candles.
required_inputs:
  - candle.isClosed
implementation_notes: Reject any event triggered on a live/forming candle for CRT evaluation.
```

### CRT-DATA-003

```yaml
rule_id: CRT-DATA-003
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-14
category: data_requirements
source_ids: [CRT-SOURCE-001, CRT-SOURCE-002]
rule_text: >
  CRT applies to all standard timeframes: M1, M5, M15, M30, H1, H4, D1, W1, MN.
  Higher timeframes (H4, D1, W1, MN) are used as parent candle (range) timeframes.
  Lower timeframes (M1, M5, M15) are used as entry execution timeframes.
  The same detection logic applies across all timeframes.
  Standard timeframe pairings (HTF range -> LTF entry):
    Monthly  -> Daily
    Weekly   -> H4
    Daily    -> H1
    H4       -> M15
    H1       -> M5
    M15      -> M1
deterministic_condition: >
  parent_timeframe must be one of the configured and supported timeframes.
  parent_timeframe must be >= entry_timeframe.
  Actual allowed timeframes must be loaded from configuration, not hardcoded.
implementation_notes: Timeframe pairings are configurable. These are defaults, not fixed values.
```

---

## Section 2: Parent Candle Definition

### CRT-PARENT-001

```yaml
rule_id: CRT-PARENT-001
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-14
category: parent_candle
source_ids: [CRT-SOURCE-001, CRT-SOURCE-002, CRT-SOURCE-003]
rule_text: >
  The parent candle is the reference candle whose high and low define the CRT range.
  Its high is the CRT High (CRH) and its low is the CRT Low (CRL).
deterministic_condition: >
  CRH = parent.high
  CRL = parent.low
  CRT_range = parent.high - parent.low
required_inputs:
  - parent.high
  - parent.low
  - parent.openTime
  - parent.timeframe
  - parent.symbol
implementation_notes: >
  Store CRH, CRL, CRT_range as computed fields in the detector output.
  Parent candle identity (openTime + timeframe + symbol) must be preserved for audit.
```

### CRT-PARENT-002

```yaml
rule_id: CRT-PARENT-002
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-14
category: parent_candle
source_ids: [CRT-SOURCE-001, CRT-SOURCE-002]
rule_text: >
  A parent candle located at or near a significant higher-timeframe structural level
  (order block, fair value gap, swing high/low, liquidity pool) produces higher-quality
  CRT setups than a parent candle in random market structure.
  This is a QUALITY SIGNAL, not a binary detection gate.
  A CRT may be detected regardless of parent location; location affects quality metadata only.
deterministic_condition: >
  quality_metadata.parent_at_htf_zone = true | false
  Detection is not blocked when parent_at_htf_zone = false.
  Confidence Engine may apply a weight reduction for parent_at_htf_zone = false.
implementation_notes: >
  Whether to require parent_at_htf_zone = true as a hard filter is a configurable setting,
  not a hardcoded detection rule. Default: quality weight only.
```

### CRT-PARENT-003

```yaml
rule_id: CRT-PARENT-003
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-14
category: parent_candle
source_ids: [CRT-SOURCE-001, CRT-SOURCE-002, CRT-SOURCE-006]
rule_text: >
  CRT follows a 3-candle structure:
    Candle 1: Parent candle — establishes CRH and CRL.
    Candle 2: Sweep candle — raids CRH or CRL and closes back inside range.
    Candle 3: Confirmation candle — closes above sweep.high (bullish) or below sweep.low (bearish).
  The sweep candle is the candle immediately following the parent candle.
deterministic_condition: >
  sweep_candle.openTime == parent_candle.closeTime (directly adjacent on same timeframe)
  confirmation_candle.openTime == sweep_candle.closeTime
required_inputs:
  - parent_candle (candle N)
  - sweep_candle (candle N+1)
  - confirmation_candle (candle N+2)
implementation_notes: >
  The 3-candle model is the primary CRT structure. Evaluate candles N, N+1, N+2 in sequence.
  Detection is triggered on the close of candle N+2 (confirmation candle).
```

### CRT-PARENT-004

```yaml
rule_id: CRT-PARENT-004
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-20
category: parent_candle
source_ids: [PROJECT-LEAD-2026-07-20]
rule_text: >
  Very Small Parent Candle (Doji or near-zero range): Must be ignored.
  A minimum parent candle range threshold must be configured using an ATR multiple (e.g., min_parent_range_atr_multiple: 0.5).
  If the parent range (High - Low) is less than the ATR-based threshold, it is an invalid parent.
deterministic_condition: >
  CRT_range >= (configured_min_parent_range_atr_multiple * parent.atr)
implementation_notes: >
  Do not signal CRT on dojis or extremely small ranges. Minimum range must be an adjustable parameter.
```

---

## Section 3: CRT Candle (Sweep Candle) Definition

### CRT-SWEEP-001

```yaml
rule_id: CRT-SWEEP-001
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-14
category: crt_candle
source_ids: [CRT-SOURCE-001, CRT-SOURCE-002, CRT-SOURCE-003]
rule_text: >
  For a bullish CRT, the sweep candle must extend below the parent candle's CRL.
  Either the wick or body must breach the CRL.
deterministic_condition: >
  sweep_candle.low < parent.low (CRL)
required_inputs:
  - sweep_candle.low
  - parent.low
```

### CRT-SWEEP-002

```yaml
rule_id: CRT-SWEEP-002
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-14
category: crt_candle
source_ids: [CRT-SOURCE-001, CRT-SOURCE-002, CRT-SOURCE-003]
rule_text: >
  For a bearish CRT, the sweep candle must extend above the parent candle's CRH.
  Either the wick or body must breach the CRH.
deterministic_condition: >
  sweep_candle.high > parent.high (CRH)
required_inputs:
  - sweep_candle.high
  - parent.high
```

### CRT-SWEEP-003

```yaml
rule_id: CRT-SWEEP-003
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-20
category: crt_candle
source_ids: [CRT-SOURCE-001, CRT-SOURCE-002, CRT-SOURCE-003, CRT-SOURCE-004, PROJECT-LEAD-2026-07-20]
rule_text: >
  For a valid CRT setup, the sweep candle must close its BODY back inside the parent
  candle range after sweeping the CRH or CRL. A boundary close exactly on the line is valid.
    Bullish valid:  sweep_candle.low < parent.low   AND  sweep_candle.close >= parent.low
    Bearish valid:  sweep_candle.high > parent.high AND  sweep_candle.close <= parent.high
  A wick sweep is valid when the body closes inside the range.
  A body close that remains OUTSIDE the range is NOT a valid CRT. It is a breakout.
deterministic_condition: >
  Bullish: sweep.low < parent.low AND sweep.close >= parent.low
  Bearish: sweep.high > parent.high AND sweep.close <= parent.high
required_inputs:
  - sweep_candle.high
  - sweep_candle.low
  - sweep_candle.close
  - parent.high
  - parent.low
implementation_notes: >
  This is the single most critical CRT rule. The close-back condition is what distinguishes
  a CRT manipulation sweep from a genuine breakout. Boundary closes (<= CRH or >= CRL) are valid.
```

---

## Section 4: Bullish CRT Rules

Detector boundary: The detector must NOT return `BUY`. It may return `bullish_crt` as a pattern classification metadata field only.

### CRT-BULL-001

```yaml
rule_id: CRT-BULL-001
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-20
category: bullish_crt
source_ids: [CRT-SOURCE-001, CRT-SOURCE-002, CRT-SOURCE-006, PROJECT-LEAD-2026-07-20]
rule_text: >
  Bullish CRT is a 3-candle pattern:
    Condition 1 — Parent candle:
      Any closed candle establishing CRH and CRL. Range must be >= configured threshold.
    Condition 2 — Sweep candle (bullish sweep of CRL):
      sweep.low < parent.low            (wick or body breaches CRL strictly)
      sweep.close >= parent.low         (body closes back inside range at or above CRL)
    Condition 3 — Confirmation candle (bullish MSS) (OPTIONAL based on config):
      if require_confirmation_candle == true:
        confirmation.close > sweep.high   (closes above the sweep candle's high)
  When all required conditions are met on closed candles, the detector outputs:
    detected: true
    classification: bullish_crt
deterministic_condition: >
  (sweep.low < parent.low) AND
  (sweep.close >= parent.low) AND
  (NOT require_confirmation_candle OR confirmation.close > sweep.high)
required_inputs:
  - parent.high, parent.low, parent.openTime
  - sweep.high, sweep.low, sweep.close, sweep.openTime
  - confirmation.close, confirmation.openTime (if require_confirmation_candle)
expected_output:
  detected: true
  classification: bullish_crt
pass_examples:
  - CRT-TEST-FIXTURE-BULLISH-001
fail_examples:
  - Body closes below CRL (invalidated)
  - Equal lows with parent (sweep.low == parent.low)
  - Both-sides sweep
implementation_notes: >
  Detect on close of confirmation candle (candle N+2).
  Return UNKNOWN if any required input is missing.
```

---

## Section 5: Bearish CRT Rules

Detector boundary: The detector must NOT return `SELL`. It may return `bearish_crt` as a pattern classification metadata field only.

### CRT-BEAR-001

```yaml
rule_id: CRT-BEAR-001
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-20
category: bearish_crt
source_ids: [CRT-SOURCE-001, CRT-SOURCE-002, CRT-SOURCE-006, PROJECT-LEAD-2026-07-20]
rule_text: >
  Bearish CRT is a 3-candle pattern:
    Condition 1 — Parent candle:
      Any closed candle establishing CRH and CRL. Range must be >= configured threshold.
    Condition 2 — Sweep candle (bearish sweep of CRH):
      sweep.high > parent.high          (wick or body breaches CRH strictly)
      sweep.close <= parent.high        (body closes back inside range at or below CRH)
    Condition 3 — Confirmation candle (bearish MSS) (OPTIONAL based on config):
      if require_confirmation_candle == true:
        confirmation.close < sweep.low    (closes below the sweep candle's low)
  When all required conditions are met on closed candles, the detector outputs:
    detected: true
    classification: bearish_crt
deterministic_condition: >
  (sweep.high > parent.high) AND
  (sweep.close <= parent.high) AND
  (NOT require_confirmation_candle OR confirmation.close < sweep.low)
required_inputs:
  - parent.high, parent.low, parent.openTime
  - sweep.high, sweep.low, sweep.close, sweep.openTime
  - confirmation.high, confirmation.low, confirmation.close, confirmation.openTime (if require_confirmation_candle)
expected_output:
  detected: true
  classification: bearish_crt
pass_examples:
  - CRT-TEST-FIXTURE-BEARISH-001
fail_examples:
  - Body closes above CRH (invalidated)
  - Equal highs with parent (sweep.high == parent.high)
  - Both-sides sweep
implementation_notes: >
  Detect on close of confirmation candle (candle N+2).
  Return UNKNOWN if any required input is missing.
```

---

## Section 6: Liquidity Sweep Requirements

### CRT-SWEEP-VALID-001

```yaml
rule_id: CRT-SWEEP-VALID-001
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-20
category: liquidity_sweep
source_ids: [CRT-SOURCE-001, CRT-SOURCE-002, CRT-SOURCE-003, PROJECT-LEAD-2026-07-20]
rule_text: >
  A liquidity sweep is valid when:
    1. The sweep candle's wick or body STRICTLY extends beyond the parent CRH or CRL.
    2. The sweep candle's BODY closes back inside the parent candle range (or exactly on the boundary).
  A wick-only breach that closes inside the range is a valid sweep.
deterministic_condition: >
  Bullish valid sweep: sweep.low < parent.low AND sweep.close >= parent.low
  Bearish valid sweep: sweep.high > parent.high AND sweep.close <= parent.high
implementation_notes: >
  Strict inequality is required for the sweep (`<` or `>`).
  Inclusive equality is allowed for the close (`>=` or `<=`).
```

### CRT-SWEEP-INVALID-001

```yaml
rule_id: CRT-SWEEP-INVALID-001
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-14
category: liquidity_sweep
source_ids: [CRT-SOURCE-002, CRT-SOURCE-003]
rule_text: >
  A sweep is NOT valid and the CRT setup is immediately invalidated when the
  sweep candle's BODY closes OUTSIDE the parent candle range.
  This condition signals a genuine breakout, not a manipulation sweep.
deterministic_condition: >
  Bullish invalidation: sweep.low < parent.low AND sweep.close < parent.low
  Bearish invalidation: sweep.high > parent.high AND sweep.close > parent.high
implementation_notes: >
  When this condition is met, log the invalidation reason and return detected: false.
  Do not proceed to evaluate the confirmation candle.
```

### CRT-SWEEP-INVALID-002

```yaml
rule_id: CRT-SWEEP-INVALID-002
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-20
category: liquidity_sweep
source_ids: [PROJECT-LEAD-2026-07-20]
rule_text: >
  Equal Highs/Lows (no actual pierce): Invalid sweep.
  The sweep candle's extreme must be strictly beyond the parent's CRH or CRL.
  Equality does not count as a sweep. Treat it as “no CRT setup.”
deterministic_condition: >
  Bullish invalidation: sweep.low >= parent.low
  Bearish invalidation: sweep.high <= parent.high
implementation_notes: >
  Strict inequality (`<` or `>`) is required for a valid sweep extreme.
```

### CRT-SWEEP-INVALID-003

```yaml
rule_id: CRT-SWEEP-INVALID-003
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-20
category: liquidity_sweep
source_ids: [PROJECT-LEAD-2026-07-20]
rule_text: >
  Gap Candles (e.g., weekend gap over the level): Invalid sweep if the gap skips the level entirely beyond a configurable tolerance.
  If the open of the sweep candle is already beyond CRH/CRL by more than max_gap_tolerance_pips, treat it as a gap, not a sweep.
  Small gaps within tolerance are flagged with lower quality metadata but not fully invalidated.
deterministic_condition: >
  Bullish invalidation: sweep.open < (parent.low - configured_max_gap_tolerance_pips)
  Bearish invalidation: sweep.open > (parent.high + configured_max_gap_tolerance_pips)
implementation_notes: >
  The open of the sweep candle must be at, inside, or within the allowed tolerance of the range boundary.
```

---

## Section 7: Close Requirements

### CRT-CLOSE-001

```yaml
rule_id: CRT-CLOSE-001
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-20
category: close_requirement
source_ids: [CRT-SOURCE-001, CRT-SOURCE-002, CRT-SOURCE-004, PROJECT-LEAD-2026-07-20]
rule_text: >
  For a valid CRT, the sweep candle must close its body back inside the parent candle range, or exactly on the boundary.
    Bullish: sweep_candle.close >= parent.low (CRL)
    Bearish: sweep_candle.close <= parent.high (CRH)
  Any close inside the range or on the line qualifies.
deterministic_condition: >
  Bullish: sweep.close >= parent.low
  Bearish: sweep.close <= parent.high
required_inputs:
  - sweep.close
  - parent.low (bullish)
  - parent.high (bearish)
implementation_notes: >
  Boundary close is valid.
```

---

## Section 8: Invalidation Rules

### CRT-INVALID-001

```yaml
rule_id: CRT-INVALID-001
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-14
category: invalidation
source_ids: [CRT-SOURCE-002, CRT-SOURCE-003, CRT-SOURCE-004]
rule_text: >
  A CRT setup is invalidated when the sweep candle closes its BODY outside the parent
  candle range (beyond the swept level). This indicates a genuine breakout.
deterministic_condition: >
  Bullish invalidated: sweep.close <= parent.low
  Bearish invalidated: sweep.close >= parent.high
expected_output:
  detected: false
  invalidation_reason: "Sweep candle body closed outside parent range — breakout, not manipulation."
implementation_notes: >
  Log the invalidation. Do not fire an alert. Return detected: false with invalidation reason.
```

### CRT-INVALID-002

```yaml
rule_id: CRT-INVALID-002
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-14
category: invalidation
source_ids: [CRT-SOURCE-002, CRT-SOURCE-004]
rule_text: >
  A CRT zone is considered mitigated (exhausted) once price has reached or
  exceeded the opposite extreme of the parent candle range after a valid CRT detection.
  A mitigated zone must not generate repeat CRT entries.
deterministic_condition: >
  Bullish CRT mitigated: highest subsequent candle.high >= parent.high (CRH reached)
  Bearish CRT mitigated: lowest subsequent candle.low <= parent.low (CRL reached)
implementation_notes: >
  Track mitigation state per parent candle event ID.
  Once mitigated, mark the zone as exhausted and ignore new sweeps of the same parent.
```

### CRT-INVALID-003

```yaml
rule_id: CRT-INVALID-003
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-14
category: invalidation
source_ids: [CRT-SOURCE-002]
rule_text: >
  For a bullish CRT, if any subsequent candle closes below the sweep candle's low
  (beyond the wick extreme), the bullish setup is invalidated. The stop loss has been hit.
  For a bearish CRT, if any subsequent candle closes above the sweep candle's high,
  the bearish setup is invalidated.
deterministic_condition: >
  Bullish post-confirmation invalidation: any_candle.close < sweep.low
  Bearish post-confirmation invalidation: any_candle.close > sweep.high
implementation_notes: >
  Monitor active CRT zones for stop-breach invalidation after detection.
  Log the invalidation event with the candle that caused it.
```

### CRT-INVALID-004

```yaml
rule_id: CRT-INVALID-004
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-20
category: invalidation
source_ids: [PROJECT-LEAD-2026-07-20]
rule_text: >
  Both-Sides Sweep (one candle takes out both CRH and CRL): Invalid / Ignore.
  A single candle sweeping both sides is chaotic (news spike or manipulation).
  Do not trigger any CRT. The setup is invalidated for that parent range.
deterministic_condition: >
  Invalidation: sweep.low < parent.low AND sweep.high > parent.high
implementation_notes: >
  Check for this before validating directional sweeps. If true, abort CRT check for this parent.
```

---

## Section 9: Quality Metadata

### CRT-QUALITY-001

```yaml
rule_id: CRT-QUALITY-001
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-14
category: quality_metadata
source_ids: [CRT-SOURCE-001, CRT-SOURCE-002, CRT-SOURCE-003]
rule_text: >
  The following metadata fields describe CRT quality and are passed to the Confidence Engine.
  These are quality inputs, not binary detection conditions. Detection must not be blocked
  solely because any single quality field is unfavourable.
quality_fields:
  sweep_distance_pips:
    description: How far price extended beyond the parent CRH or CRL (in pips or points).
    type: float
  close_location_ratio:
    description: >
      Where the sweep candle closes within the parent range.
      0.0 = at CRL, 1.0 = at CRH. For bullish: higher ratio = stronger close-back.
    type: float (0.0 to 1.0)
  sweep_wick_ratio:
    description: >
      The ratio of the sweep candle's wick length (beyond CRH/CRL) to the parent range.
    type: float
  parent_at_htf_zone:
    description: Whether the parent candle is at a significant HTF structural level.
    type: boolean
  session_alignment:
    description: Whether the sweep candle occurred during a London or New York kill zone.
    type: boolean
  htf_trend_alignment:
    description: Whether the CRT direction aligns with the higher-timeframe trend (if configured).
    type: boolean
  fvg_present_after_sweep:
    description: Whether a Fair Value Gap formed in the sweep candle area.
    type: boolean
  msb_on_ltf:
    description: Whether a Market Structure Break was confirmed on the lower execution timeframe.
    type: boolean
implementation_notes: >
  Quality field weights and thresholds belong in docs/09_CONFIDENCE_ENGINE.md
  and docs/13_CONFIGURATION.md. Never hardcode weights here.
```

---

## Section 10: Multi-Timeframe Behavior

### CRT-MTF-001

```yaml
rule_id: CRT-MTF-001
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-14
category: multi_timeframe
source_ids: [CRT-SOURCE-001, CRT-SOURCE-002]
rule_text: >
  CRT is a multi-timeframe methodology. Standard HTF-to-LTF timeframe pairings:
    Monthly  -> Daily
    Weekly   -> H4
    Daily    -> H1
    H4       -> M15
    H1       -> M5
    M15      -> M1
  The parent candle is drawn from the HTF. Entry timing is refined on the LTF.
  These pairings are configurable defaults, not hardcoded rules.
deterministic_condition: >
  parent_candle_timeframe >= entry_execution_timeframe
  Both must be from the configured allowed timeframe list.
implementation_notes: Timeframe pairings must be loaded from configuration.
```

### CRT-MTF-002

```yaml
rule_id: CRT-MTF-002
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-14
category: multi_timeframe
source_ids: [CRT-SOURCE-001]
rule_text: >
  CRT is fractal. A lower-timeframe CRT can form inside a higher-timeframe CRT range.
  These nested CRTs are fully independent events.
  Each CRT event is uniquely identified by: symbol + timeframe + parent_candle_open_time.
deterministic_condition: >
  event_id = symbol + ":" + timeframe + ":" + parent_candle_open_time
  Nested CRTs on different timeframes for the same symbol are separate events.
implementation_notes: >
  The detector must fire independent events for each timeframe.
  The Architecture layer handles multi-timeframe routing and profile context.
  See docs/03_ARCHITECTURE.md.
```

---

## Section 11: Session Timing

### CRT-SESSION-001

```yaml
rule_id: CRT-SESSION-001
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-14
category: session_timing
source_ids: [CRT-SOURCE-001, CRT-SOURCE-003]
rule_text: >
  CRT setups are highest probability when the sweep (manipulation) occurs during
  the London Open or New York AM kill zone.
  Reference kill zones (UTC):
    London Open Kill Zone:        02:00 – 05:00 UTC
    New York AM Kill Zone:        12:00 – 15:00 UTC
    London / New York Overlap:    12:00 – 16:00 UTC
  Asian session (approx 20:00 – 00:00 UTC) is the accumulation phase.
  Session timing is a QUALITY SIGNAL and must remain configurable, not hardcoded.
deterministic_condition: >
  quality_metadata.session_alignment = true
    if sweep_candle.openTime falls within a configured kill zone window.
  Detection is not blocked when session_alignment = false.
  Confidence Engine applies a configurable weight for session_alignment.
implementation_notes: >
  Kill zone UTC offsets must be loaded from configuration (docs/13_CONFIGURATION.md).
  Exact UTC windows must not be hardcoded in the detector.
  Session alignment is metadata passed to the Confidence Engine.
```

---

## Section 12: Strategy Boundary

### CRT-STRAT-001

```yaml
rule_id: CRT-STRAT-001
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-14
category: strategy_boundary
source_ids: [CRT-SOURCE-001, CRT-SOURCE-002, CRT-SOURCE-003]
rule_text: >
  The CRT detector identifies the pattern only. It does not decide trade entries,
  exits, stop losses, take profits, lot sizes, or risk.
  CRT detection output fields:
    detected:          boolean
    classification:    "bullish_crt" | "bearish_crt" | "UNKNOWN"
    confidence:        float (from Confidence Engine)
    reason:            string (human-readable explanation)
    invalidation:      string (invalidation condition and level)
    metadata:          quality fields per CRT-QUALITY-001
  Detector output must NOT include: BUY, SELL, lot_size, entry_price, sl_price, tp_price,
    position_size, risk_amount, or any execution instruction.
deterministic_condition: >
  output.classification IN ["bullish_crt", "bearish_crt", "UNKNOWN"]
  output does NOT contain execution fields
implementation_notes: >
  Strategy Engine (docs/08_STRATEGY_ENGINE.md) consumes detector output and produces
  Trade Candidate or No Trade. Risk Engine (docs/10_RISK_ENGINE.md) handles SL/TP/lot size.
```

---

## Approved Detector Output Contract

```json
{
  "schemaVersion": "1.0",
  "detector": "CRTDetector",
  "symbol": "EURUSD",
  "timeframe": "H4",
  "detected": true,
  "timestamp": "2026-07-14T14:00:00Z",
  "confidence": "PENDING_CONFIDENCE_ENGINE",
  "reason": "Bullish CRT: sweep of CRL at 1.0823, body closed back above CRL at 1.0841, confirmation closed above sweep high at 1.0852.",
  "invalidation": "Bullish CRT invalidated if any candle closes below sweep low at 1.0823.",
  "metadata": {
    "classification": "bullish_crt",
    "parentCandle": {
      "openTime": "2026-07-14T06:00:00Z",
      "open": 1.0845,
      "high": 1.0878,
      "low": 1.0831,
      "close": 1.0848
    },
    "sweepCandle": {
      "openTime": "2026-07-14T10:00:00Z",
      "open": 1.0848,
      "high": 1.0853,
      "low": 1.0823,
      "close": 1.0841
    },
    "confirmationCandle": {
      "openTime": "2026-07-14T14:00:00Z",
      "open": 1.0841,
      "high": 1.0858,
      "low": 1.0839,
      "close": 1.0855
    },
    "sweptLevel": "CRL",
    "crtHigh": 1.0878,
    "crtLow": 1.0831,
    "midpointTarget": 1.08545,
    "oppositeExtremeTarget": 1.0878,
    "sweepDistancePips": 8.0,
    "closeLocationRatio": 0.42,
    "parentAtHtfZone": null,
    "sessionAlignment": null,
    "htfTrendAlignment": null,
    "fvgPresentAfterSweep": null,
    "msbOnLtf": null
  }
}
```

---

## Approved Entry Models (Strategy Engine Reference)

These are approved for the Strategy Engine to consume. They are not detector responsibility.

### Entry Model 1 — HTF Confirmation (Conservative)

```yaml
entry_model_id: CRT-ENTRY-HTF-001
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-14
description: >
  Enter on the close of the confirmation candle on the parent candle timeframe.
steps:
  1. CRT detected (all 3 conditions met).
  2. Enter on market at confirmation candle close (or open of next candle).
  3. Stop loss: Below sweep.low (bullish) or above sweep.high (bearish).
  4. Target 1 (TP1): Parent candle midpoint — (CRH + CRL) / 2 — Mean Threshold.
  5. Target 2 (TP2): Opposite extreme — CRH (bullish) or CRL (bearish).
minimum_rr: configurable (default intent: 1:2)
```

### Entry Model 2 — LTF Market Structure Shift (Aggressive / Preferred)

```yaml
entry_model_id: CRT-ENTRY-LTF-001
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-14
description: >
  After the HTF sweep candle is confirmed (close-back inside range), drop to the
  configured LTF execution timeframe and enter on the Market Structure Shift (MSS).
steps:
  1. Sweep candle confirmed on HTF (CRT-SWEEP-003 met).
  2. Drop to configured LTF (e.g., H4 CRT -> M15 entry).
  3. Wait for an ICT-style MSS / Break of Structure (BOS) on the LTF.
  4. Enter at the retest of the MSS level or at a Fair Value Gap (FVG) formed after sweep.
  5. Precision entry: 50% midpoint of the FVG (Mean Threshold / Consequent Encroachment).
  6. Stop loss: Beyond the wick extreme of the sweep candle.
  7. Target 1 (TP1): 50% midpoint of parent candle range — (CRH + CRL) / 2.
  8. Target 2 (TP2): Opposite extreme of parent candle range.
minimum_rr: configurable (default intent: 1:2)
```

---

## Section 13: Approved OHLC Test Fixtures

These are the gold standard, textbook examples approved by the project lead on 2026-07-20. The code must return the correct signal on these exact numbers and reject any deviation that breaks the core rules.

### CRT-TEST-FIXTURE-BULLISH-001

```yaml
fixture_id: CRT-TEST-FIXTURE-BULLISH-001
status: APPROVED
type: bullish_crt
description: Textbook Bullish CRT 3-candle sequence
candles:
  - label: parent
    open: 100.00
    high: 102.50
    low: 98.50
    close: 101.20
  - label: sweep
    open: 101.10
    high: 101.80
    low: 97.80
    close: 100.80
  - label: confirmation
    open: 100.90
    high: 103.00
    low: 100.40
    close: 102.70
expected_output:
  detected: true
  classification: bullish_crt
```

### CRT-TEST-FIXTURE-BEARISH-001

```yaml
fixture_id: CRT-TEST-FIXTURE-BEARISH-001
status: APPROVED
type: bearish_crt
description: Textbook Bearish CRT 3-candle sequence
candles:
  - label: parent
    open: 150.00
    high: 152.80
    low: 149.20
    close: 150.90
  - label: sweep
    open: 150.80
    high: 153.50
    low: 150.10
    close: 151.20
  - label: confirmation
    open: 151.10
    high: 151.40
    low: 148.70
    close: 149.30
expected_output:
  detected: true
  classification: bearish_crt
```

---

## Approval Checklist

- [x] Terminology is approved.
- [x] Parent candle rules are approved.
- [x] CRT candle / sweep candle rules are approved.
- [x] Bullish CRT rules are approved.
- [x] Bearish CRT rules are approved.
- [x] Liquidity sweep rules are approved.
- [x] Close requirements are approved.
- [x] Invalidation rules are approved.
- [x] Quality metadata is approved.
- [x] Multi-timeframe behavior is approved.
- [x] Session filter behavior is defined (quality weight, configurable).
- [x] Strategy boundary and detector output contract approved.
- [x] At least one positive OHLC example is approved.
- [x] At least one negative OHLC example is approved (handled implicitly by edge cases).
- [x] Edge cases have approved strict behavior.
- [x] Test fixtures are derived from approved examples.

---

## Current Rule State

All rules, edge cases, and test fixtures in this document are **APPROVED** as of 2026-07-20 by the project lead.

Implementation may begin immediately. The rules are strict, mathematical, and testable.
