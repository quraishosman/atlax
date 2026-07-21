# CRT Source Intake

Version: 0.3  
Status: Active Workflow  
Last Updated: 2026-07-14  
Purpose: Capture CRT source material in a format that can be reviewed and promoted into `CRT_RULEBOOK.md`.

**Approval Note**: All sources captured on 2026-07-14 were reviewed by the project lead and approved on 2026-07-14. Rules derived from these sources are now APPROVED in `CRT_RULEBOOK.md`.

---

## Intake Rule

No source material becomes ATLAX trading logic automatically.

Every source must be captured, interpreted, reviewed, and approved before it can change `CRT_RULEBOOK.md`.

---

## Source Intake Form

The sources below were captured on 2026-07-14 from web and YouTube research. All are `RESEARCH_ONLY` or `PENDING_REVIEW`. None are approved for implementation.

---

### Captured Research Sources (2026-07-14)

```yaml
source_id: CRT-SOURCE-001
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-14
source_type: web_reference
source_title: Candle Range Theory (Explained) – CRT Trading Guide
source_url: https://www.writofinance.com/candle-range-theory-crt/
source_author: Muhammad Aatiq Shah (Writo-Finance)
source_date: 2024-09-13, updated 2026-06-03
captured_date: 2026-07-14
captured_by: AI Research Agent

segments:
  - segment_id: CRT-SOURCE-001-S01
    raw_claim: CRT is a price action methodology that treats each candlestick as a range of price action.
      The high and low of the candle represent the range on smaller timeframes.
    proposed_rule_category: [terminology, data_requirements]
    proposed_rule_text: The parent (reference) candle's high and low define the CRT range. The high is the CRT High (CRH) and the low is the CRT Low (CRL).
    confidence: HIGH (consistent across all sources)
    reviewer_notes: Non-Romeo source. Consistent with CRT community understanding.

  - segment_id: CRT-SOURCE-001-S02
    raw_claim: In a bullish CRT, the next candle sweeps the low of the reference candle. Its close must be above the low of the reference candlestick.
    proposed_rule_category: [bullish_crt, liquidity_sweep, close_requirement]
    proposed_rule_text: Bullish CRT sweep candle - sweep.low < parent.low AND sweep.close > parent.low
    confidence: HIGH (consistent across all sources)
    reviewer_notes: Close-back above swept low is the defining condition.

  - segment_id: CRT-SOURCE-001-S03
    raw_claim: After the liquidity grab, a bullish confirmation candle must close above the high of the candle that performed the liquidity raid.
    proposed_rule_category: [bullish_crt]
    proposed_rule_text: Bullish confirmation - confirmation.close > sweep.high
    confidence: HIGH
    reviewer_notes: 3rd candle closes above sweep candle high signals MSS.

  - segment_id: CRT-SOURCE-001-S04
    raw_claim: In a bearish CRT, the next candle sweeps the high of the reference candle. Its close must be below the high of the reference candlestick.
    proposed_rule_category: [bearish_crt, liquidity_sweep, close_requirement]
    proposed_rule_text: Bearish CRT sweep candle - sweep.high > parent.high AND sweep.close < parent.high
    confidence: HIGH
    reviewer_notes: Mirror of bullish rule.

  - segment_id: CRT-SOURCE-001-S05
    raw_claim: Key timing: 1am, 5am, 9am, 1pm, 3pm, 9pm are key times when liquidity and volatility shift.
    proposed_rule_category: [quality_metadata]
    proposed_rule_text: CRT quality is enhanced when the sweep occurs during session kill zones.
    confidence: MEDIUM
    reviewer_notes: These are session-based quality filters, not binary detection conditions.

  - segment_id: CRT-SOURCE-001-S06
    raw_claim: Timeframe pairings: Monthly->Daily, Daily->H1, H4->M15, H1->M5, M15->M1.
    proposed_rule_category: [data_requirements, multi_timeframe]
    proposed_rule_text: Standard CRT timeframe pairings for parent candle and execution timeframe.
    confidence: HIGH
    reviewer_notes: Consistent with all other sources.
```

```yaml
source_id: CRT-SOURCE-002
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-14
source_type: web_reference
source_title: Candle Range Theory — TradingWyckoff
source_url: https://tradingwyckoff.com/candle-range-theory/
source_author: TradingWyckoff (community educational site)
source_date: UNKNOWN
captured_date: 2026-07-14
captured_by: AI Research Agent

segments:
  - segment_id: CRT-SOURCE-002-S01
    raw_claim: For a valid CRT setup, price must reject the breakout and close back inside the parent candle range.
      Many traders require the BODY of the sweep candle to close back inside the range.
    proposed_rule_category: [liquidity_sweep, close_requirement]
    proposed_rule_text: Valid sweep requires body close inside parent range (not just wick).
    confidence: HIGH
    reviewer_notes: Body close-back is the consistent distinguishing rule across sources.

  - segment_id: CRT-SOURCE-002-S02
    raw_claim: A CRT zone is mitigated once price touches the opposite extreme. Do not look for repeat entries in same zone.
    proposed_rule_category: [invalidation]
    proposed_rule_text: CRT zone is exhausted after price delivers to the opposite parent candle extreme.
    confidence: MEDIUM
    reviewer_notes: Mitigation concept is consistent. Needs project lead confirmation.

  - segment_id: CRT-SOURCE-002-S03
    raw_claim: If the setup is invalidated (body closes outside range), it suggests a legitimate breakout rather than manipulation.
    proposed_rule_category: [invalidation]
    proposed_rule_text: Body close outside parent range = breakout = CRT invalidation.
    confidence: HIGH
    reviewer_notes: Consistent across all sources.
```

```yaml
source_id: CRT-SOURCE-003
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-14
source_type: web_reference
source_title: Candle Range Theory — InnercircleTrader.net (community)
source_url: https://innercircletrader.net/candle-range-theory/
source_author: InnercircleTrader.net (community educational site, not ICT official)
source_date: UNKNOWN
captured_date: 2026-07-14
captured_by: AI Research Agent

segments:
  - segment_id: CRT-SOURCE-003-S01
    raw_claim: CRT setups are most effective when the manipulation phase occurs during London and New York session kill zones.
    proposed_rule_category: [quality_metadata]
    proposed_rule_text: London Open and New York AM kill zones increase CRT setup probability.
    confidence: HIGH
    reviewer_notes: Kill zone emphasis is consistent across all CRT sources.

  - segment_id: CRT-SOURCE-003-S02
    raw_claim: The CRH is buy-side liquidity and the CRL is sell-side liquidity.
      Price sweeps these levels to trigger stop-losses.
    proposed_rule_category: [terminology, liquidity_sweep]
    proposed_rule_text: CRH = buy-side liquidity above parent high. CRL = sell-side liquidity below parent low.
    confidence: HIGH
    reviewer_notes: Terminology consistent across all sources.

  - segment_id: CRT-SOURCE-003-S03
    raw_claim: AMD cycle - Accumulation (consolidation inside range), Manipulation (liquidity sweep),
      Distribution (impulsive expansion to opposite extreme).
    proposed_rule_category: [terminology]
    proposed_rule_text: CRT follows the AMD (Accumulation, Manipulation, Distribution) cycle.
    confidence: HIGH
    reviewer_notes: AMD is consistently cited as the theoretical basis of CRT.
```

```yaml
source_id: CRT-SOURCE-004
status: APPROVED
approved_by: Project Lead
approved_date: 2026-07-14
source_type: web_reference
source_title: Scribd CRT community documents (multiple Romeo CRT study guides)
source_url: https://www.scribd.com (multiple documents)
source_author: Various Romeo CRT community contributors
source_date: UNKNOWN
captured_date: 2026-07-14
captured_by: AI Research Agent

segments:
  - segment_id: CRT-SOURCE-004-S01
    raw_claim: CRT 3-candle rule - range candle, liquidation candle, confirmation candle.
    proposed_rule_category: [crt_candle, bullish_crt, bearish_crt]
    proposed_rule_text: CRT is a 3-candle structure: parent, sweep, confirmation.
    confidence: HIGH
    reviewer_notes: 3-candle structure is consistent across all sources.

  - segment_id: CRT-SOURCE-004-S02
    raw_claim: Conservative entry: wait for 3rd candle to close inside the range after the sweep.
      Aggressive entry: drop to LTF on the sweep for MSS entry.
    proposed_rule_category: [example]
    proposed_rule_text: Two entry models: HTF confirmation close vs LTF MSS entry.
    confidence: HIGH
    reviewer_notes: Both entry models are consistent across sources. Strategy Engine responsibility, not detector.

  - segment_id: CRT-SOURCE-004-S03
    raw_claim: Stop loss is placed above/below the sweep high or low (beyond the wick extreme).
      Take profit targets: 50% midpoint and opposite extreme.
    proposed_rule_category: [example]
    proposed_rule_text: SL beyond sweep wick. TP1 at 50% midpoint. TP2 at opposite CRT extreme.
    confidence: HIGH
    reviewer_notes: Strategy Engine and Risk Engine responsibility, not detector.
```

```yaml
source_id: CRT-SOURCE-ROMEO-001
status: PENDING_SOURCE
source_type: youtube_video
source_title: Romeo (Romeotpt) primary YouTube content — NOT YET LOCATED
source_url: UNKNOWN — search YouTube for: Romeotpt, "Romeo CRT", "Romeo CRT & TS", "Romeo CRT mentorship"
source_author: Romeo (Romeotpt)
source_date: UNKNOWN
captured_date: 2026-07-14
captured_by: AI Research Agent

segments:
  - segment_id: CRT-SOURCE-ROMEO-001-S01
    raw_claim: UNKNOWN — primary Romeo source not yet located
    proposed_rule_category: []
    proposed_rule_text: UNKNOWN
    confidence: UNKNOWN
    reviewer_notes: This is the required primary source. All PROPOSED rules need validation
      against direct Romeo video content with timestamps before they can be APPROVED.
      Project lead must provide Romeo YouTube video links for final rule approval.
```

---

## Captured Generic Sources

These records are reference-only. They may support neutral OHLC and candlestick vocabulary, but they do not define CRT behavior.

```yaml
source_id: CRT-SOURCE-GEN-001
status: REFERENCE_ONLY
source_type: web_reference
source_title: Candlestick chart
source_url: https://en.wikipedia.org/wiki/Candlestick_chart
source_author: Wikipedia contributors
source_date: UNKNOWN
captured_date: 2026-07-14
captured_by: Codex
segments:
  - segment_id: CRT-SOURCE-GEN-001-S01
    raw_claim: Candlestick charts represent open, high, low, and close data for a time interval.
    proposed_rule_category:
      - terminology
      - data_requirements
    proposed_rule_text: Generic candlestick data contains open, high, low, and close values.
    confidence: REFERENCE_ONLY
    reviewer_notes: Generic OHLC vocabulary only. Not CRT authority.
  - segment_id: CRT-SOURCE-GEN-001-S02
    raw_claim: Candle range is the difference between the high and low of the candle.
    proposed_rule_category:
      - terminology
    proposed_rule_text: Candle range may be described as high minus low.
    confidence: REFERENCE_ONLY
    reviewer_notes: Generic range vocabulary only. Not CRT authority.
```

```yaml
source_id: CRT-SOURCE-GEN-002
status: REFERENCE_ONLY
source_type: web_reference
source_title: Understanding Basic Candlestick Charts
source_url: https://www.investopedia.com/trading/candlestick-charting-what-is-it/
source_author: Investopedia
source_date: UNKNOWN
captured_date: 2026-07-14
captured_by: Codex
segments:
  - segment_id: CRT-SOURCE-GEN-002-S01
    raw_claim: Candlesticks commonly show a real body and shadows or wicks.
    proposed_rule_category:
      - terminology
    proposed_rule_text: Body, shadow, and wick may be used as generic candlestick terms.
    confidence: REFERENCE_ONLY
    reviewer_notes: Generic candlestick vocabulary only. Not CRT authority.
  - segment_id: CRT-SOURCE-GEN-002-S02
    raw_claim: Bullish and bearish candle color is based on the relationship between open and close.
    proposed_rule_category:
      - terminology
    proposed_rule_text: Bullish candle and bearish candle may describe generic open-close candle direction.
    confidence: REFERENCE_ONLY
    reviewer_notes: This does not define Bullish CRT or Bearish CRT.
```

```yaml
source_id: CRT-SOURCE-GEN-003
status: REFERENCE_ONLY
source_type: web_reference
source_title: Candlestick pattern
source_url: https://en.wikipedia.org/wiki/Candlestick_pattern
source_author: Wikipedia contributors
source_date: UNKNOWN
captured_date: 2026-07-14
captured_by: Codex
segments:
  - segment_id: CRT-SOURCE-GEN-003-S01
    raw_claim: Candlestick pattern recognition can be subjective unless predefined rules are used.
    proposed_rule_category:
      - detector_boundary
      - testing
    proposed_rule_text: ATLAX must define CRT recognition with deterministic rules before implementation.
    confidence: REFERENCE_ONLY
    reviewer_notes: Supports the engineering requirement for explicit rules. Does not define CRT.
```

```yaml
source_id: CRT-SOURCE-GEN-004
status: REFERENCE_ONLY
source_type: web_reference
source_title: Price action trading
source_url: https://en.wikipedia.org/wiki/Price_action_trading
source_author: Wikipedia contributors
source_date: UNKNOWN
captured_date: 2026-07-14
captured_by: Codex
segments:
  - segment_id: CRT-SOURCE-GEN-004-S01
    raw_claim: Bars and candlesticks include open, close, high, low, body, and tail concepts.
    proposed_rule_category:
      - terminology
      - data_requirements
    proposed_rule_text: ATLAX may use open, close, high, low, body, and tail as generic market-data terms.
    confidence: REFERENCE_ONLY
    reviewer_notes: Generic price-action terminology only. Not CRT authority.
```

```yaml
source_id: CRT-SOURCE-GEN-005
status: REFERENCE_ONLY
source_type: web_reference
source_title: Average true range
source_url: https://en.wikipedia.org/wiki/Average_true_range
source_author: Wikipedia contributors
source_date: UNKNOWN
captured_date: 2026-07-14
captured_by: Codex
segments:
  - segment_id: CRT-SOURCE-GEN-005-S01
    raw_claim: Range is commonly described using high and low values, while true range also considers previous close.
    proposed_rule_category:
      - terminology
      - quality_metadata
    proposed_rule_text: Range and volatility concepts may be documented separately from CRT behavior.
    confidence: REFERENCE_ONLY
    reviewer_notes: ATR is not CRT. It must not be used as a CRT quality rule without approval.
```

---

## Promotion Checklist

Before promoting any source claim into `CRT_RULEBOOK.md`, confirm:

- The source is identified.
- The timestamp or location is recorded.
- The claim is written as an objective rule.
- The rule has measurable inputs.
- The rule has deterministic pass/fail behavior.
- Ambiguous language is resolved.
- Examples or chart evidence are attached where possible.
- Edge cases are identified.
- The project lead approves the final rule.

---

## Interpretation Rules

When converting source material into a rule:

- Prefer exact language from the source.
- Convert visual examples into OHLC conditions only after approval.
- Do not assume what the teacher meant.
- Do not merge separate ideas into one rule unless the source does.
- Preserve ambiguity as `UNKNOWN`.
- Record disagreements between sources.

---

## Output Format For Approved Rules

Approved rules should be promoted into `CRT_RULEBOOK.md` using this format:

```yaml
rule_id: CRT-RULE-001
status: APPROVED
category: UNKNOWN
source_id: CRT-SOURCE-001
source_reference: UNKNOWN
rule_text: UNKNOWN
deterministic_condition: UNKNOWN
required_inputs:
  - UNKNOWN
pass_examples:
  - UNKNOWN
fail_examples:
  - UNKNOWN
edge_cases:
  - UNKNOWN
implementation_notes: UNKNOWN
```

---

## Source Priority

When sources conflict:

1. Project lead approved interpretation wins.
2. Direct Romeo source with timestamp wins over summaries.
3. Full transcript wins over short paraphrase.
4. Chart example with OHLC data wins over visual-only example.
5. Unverifiable claims remain `UNKNOWN`.
