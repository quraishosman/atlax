# CRT Research Log

Version: 0.3  
Status: Research Archive — Rules Approved  
Updated: 2026-07-14  
Approved By: Project Lead, 2026-07-14

---

## Purpose

Track external CRT research separately from the authoritative rulebook.

Nothing in this file is executable rule authority unless promoted into `CRT_RULEBOOK.md` with project lead approval.

---

## Search Log — Round 1 (2026-07-14, Previous Session)

Searches attempted:

- `Candle Range Theory trading Romeo`
- `CRT Candle Range Theory Romeo`
- `Romeo CRT forex`
- `Romeo ICT CRT forex`
- `Candle Range Theory liquidity sweep parent candle`
- `CRT trading parent candle liquidity sweep`
- `site:youtube.com Candle Range Theory`
- `site:youtube.com Romeo CRT trading`
- `Romeo CRT trading forex candle range theory`
- `Romeo forex CRT candle range`
- `Candle Range Theory CRT forex liquidity sweep Romeo`
- `Candle Range Theory trading strategy Romeo forex`
- `candle range theory forex trading`
- `what is candle range theory trading`
- `CRT trading strategy candle range`
- `candle range theory trading parent candle`

Result: No authoritative Romeo source was directly identified.

---

## Search Log — Round 2 (2026-07-14, Current Session)

Searches attempted:

- `Romeo CRT Candle Range Theory YouTube channel forex trading`
- `Candle Range Theory CRT parent candle liquidity sweep explanation forex`
- `"candle range theory" "Romeo" forex trading rules parent candle sweep displacement`
- `Romeo TPT CRT candle range theory YouTube video full explanation AMD accumulation manipulation distribution`
- `CRT candle range theory bullish bearish rules invalidation entry stop loss target complete guide`
- `site:youtube.com "candle range theory" Romeo CRT full course mentorship`
- `CRT candle range theory "mean threshold" "50%" midpoint entry model order block FVG killzone session London New York`
- `CRT candle range theory data requirements closed candle OHLC timeframes supported H4 H1 M15 M5 M1 daily`

---

## Romeo / Romeotpt Identity

**Confirmed**: Romeo is known online as **Romeotpt** (Romeo TPT).

- CRT (Candle Range Theory) is widely attributed to Romeotpt across the SMC/ICT trading community.
- Romeo shares content across Twitter/X, Telegram, and YouTube.
- There is no single centralized official course. His teachings circulate via mentorship clips, community-compiled tutorials, and third-party channel summaries.
- Search terms to find his YouTube content: `"Romeo CRT"`, `"Romeotpt"`, `"Romeo CRT mentorship"`, `"Romeo CRT & TS"` (TS = Turtle Soup).
- His channel features "CRT secrets" episodes, live tape-reading sessions, and mentorship-style content.

**Status**: Romeo is confirmed as the primary originator of CRT. His direct channel or official transcript must be the final authority for rule approval. Community summaries below are `RESEARCH_ONLY`.

---

## Key Research Findings (RESEARCH ONLY — Not Approved Rules)

All findings below are cross-referenced across multiple independent sources (tradingwyckoff.com, writofinance.com, innercircletrader.net, scribd.com, forexalgo-trader.com, arongroups.co, binance.com, grandalgo.com, forexbee.co).

These are consistent community-level descriptions of Romeo's CRT model. They are flagged `PROPOSED` for each relevant section until project lead review and approval.

### Finding 1: CRT Identity and Foundation

- CRT is attributed to **Romeo (Romeotpt)**.
- CRT is a price action methodology rooted in ICT (Inner Circle Trader) concepts.
- CRT applies the **ICT Power of Three / AMD (Accumulation, Manipulation, Distribution)** model to the anatomy of individual candles.
- Every HTF candle is treated as a self-contained micro-cycle of market behavior.
- Sources: scribd.com (multiple CRT documents), writofinance.com, innercircletrader.net

### Finding 2: The Parent Candle (Reference Candle)

- The "parent candle" or "reference candle" is the higher-timeframe candle whose high and low define the CRT range.
- Its high is called the **CRT High (CRH)** and its low the **CRT Low (CRL)**.
- The parent candle is typically selected from a significant zone: HTF liquidity level, order block, supply/demand zone, or a recent swing high/low.
- The parent candle is the immediately preceding candle before the sweep candle.
- Sources: tradingwyckoff.com, writofinance.com, innercircletrader.net, scribd.com

### Finding 3: The Sweep / Manipulation

- A **liquidity sweep** (also called a "raid" or manipulation) is the central trigger in CRT.
- Price moves beyond the parent candle's high or low to trigger stop-loss orders.
- This is referred to as "smart money" taking liquidity from retail stop-losses.
- The sweep can be wick-only or body-close beyond the level.
- **Close-back requirement**: The sweep candle must ideally **close back inside** the parent candle's range for the setup to be valid.
- A body close that remains **outside** the range is generally considered a breakout (not CRT manipulation) and invalidates the setup.
- Sources: tradingwyckoff.com, scribd.com, innercircletrader.net, forexalgo-trader.com

### Finding 4: Bullish CRT Rules

Sequence (Bullish):
1. Identify the reference candle at or near a significant HTF zone.
2. The next candle **sweeps the low** of the reference candle (sell-side liquidity raid).
3. The sweep candle closes **back above the low** of the reference candle (closes inside range).
4. Confirmation: A subsequent candle closes **above the high** of the sweep candle, signaling a market structure shift.
5. Price is then expected to deliver to the range high (CRH) or beyond.

Pattern classification metadata: `bullish_crt`  
Detector must NOT return `BUY`. It may return `bullish_crt` as pattern classification metadata only.

Sources: writofinance.com, tradingwyckoff.com, scribd.com, grandalgo.com

### Finding 5: Bearish CRT Rules

Sequence (Bearish):
1. Identify the reference candle at or near a significant HTF zone.
2. The next candle **sweeps the high** of the reference candle (buy-side liquidity raid).
3. The sweep candle closes **back below the high** of the reference candle (closes inside range).
4. Confirmation: A subsequent candle closes **below the low** of the sweep candle, signaling a market structure shift.
5. Price is then expected to deliver to the range low (CRL) or beyond.

Pattern classification metadata: `bearish_crt`  
Detector must NOT return `SELL`. It may return `bearish_crt` as pattern classification metadata only.

Sources: writofinance.com, tradingwyckoff.com, scribd.com, grandalgo.com

### Finding 6: Three-Candle Model

Multiple sources describe CRT as a **3-candle structure**:

| Candle | Role |
| --- | --- |
| Candle 1 | Reference / Parent — sets the range |
| Candle 2 | Sweep / Manipulation — raids a level |
| Candle 3 | Confirmation — shifts market structure |

This is consistently described across independent sources.

Sources: arongroups.co, tradingwyckoff.com, scribd.com

### Finding 7: Invalidation

- A CRT setup is **invalidated** when the sweep candle closes its **body outside the parent candle range** (suggesting a genuine breakout, not manipulation).
- A wick-only sweep that closes back inside the range is a valid sweep.
- A body close beyond the swept level is invalidation.
- Once the CRT zone is "mitigated" (price has delivered to the opposite extreme), it is used up.
- Sources: tradingwyckoff.com, scribd.com, innercircletrader.net

### Finding 8: Entry Models

Two primary entry techniques are described:

**Entry Model 1 — Candlestick Confirmation (HTF)**:
- Enter after the confirmation candle closes above the sweep candle high (bullish) or below the sweep candle low (bearish).
- This is a simple, HTF-level entry.

**Entry Model 2 — LTF Market Structure Shift (MSS)** (Most preferred by practitioners):
- After the sweep is confirmed, drop to a lower timeframe (M1–M15).
- Wait for an ICT-style Market Structure Shift (MSS) / Break of Structure (BOS) on the LTF.
- Enter at the retest of the displaced level or at a Fair Value Gap (FVG) that formed after the sweep.
- The 50% midpoint (Mean Threshold) of the FVG or order block is a common precision entry level.

Sources: writofinance.com, innercircletrader.net, scribd.com, arongroups.co

### Finding 9: Stop Loss and Take Profit

**Stop Loss**:
- Placed just beyond the extreme of the sweep (above the wick high for bearish, below the wick low for bullish).

**Take Profit / Targets**:
- Primary: The 50% midpoint (mean threshold) of the parent candle range.
- Final: The opposite extreme of the parent candle range (CRH for bullish, CRL for bearish).
- Extended: External liquidity beyond the range.
- Minimum R:R cited: 1:2 in multiple sources.

Sources: grandalgo.com, scribd.com, forexbee.co

### Finding 10: Timeframe Relationships

CRT is multi-timeframe by design. Confirmed pairings across sources:

| Parent Candle (HTF Range) | Entry Timeframe (LTF) |
| --- | --- |
| Monthly | Daily |
| Weekly | 4-Hour (H4) |
| Daily (D1) | 1-Hour (H1) |
| 4-Hour (H4) | 15-Minute (M15) |
| 1-Hour (H1) | 5-Minute (M5) |
| 15-Minute (M15) | 1-Minute (M1) |

Sources: writofinance.com, scribd.com, tradingwyckoff.com

### Finding 11: Session and Kill Zone Requirements

- CRT setups are highest probability when the manipulation (sweep) phase occurs during key session kill zones.
- Key sessions: **London Open** and **New York Open** (AM session).
- Key timing anchors (UTC/GMT reference — exact timezone must be confirmed for ATLAX config):
  - 1am: Asian/London overlap — manipulation or breakout expected
  - 5am: London opening — high volatility transition
  - 9am: London fully active — significant price movement
  - 1pm: London/New York overlap — high volatility
  - 3pm: New York fully active — volatility spikes
  - 9pm: New York close — final daily movement
- Asian session: Low volatility, accumulation phase.
- London session: Manipulation / liquidity sweep.
- New York session: Distribution / expansion.

Sources: writofinance.com, innercircletrader.net

### Finding 12: Data Requirements

- CRT uses standard OHLC candle data (Open, High, Low, Close).
- **Closed candles** are the primary evaluation basis. Forming (live) candles are generally not used for setup confirmation.
- Standard candlestick data is sufficient. No volume, tick data, or alternative candle types (Heikin-Ashi, Renko) are mentioned in community sources.
- Timeframes: M1 through Monthly. All standard timeframes are described as valid parent candle timeframes.

### Finding 13: Multi-Timeframe Nesting

- CRT is fractal. Lower-timeframe CRTs can form within higher-timeframe CRT ranges.
- Example: A 4H CRT range may contain multiple H1 CRTs inside it.
- HTF CRT provides narrative context; LTF CRT provides entry timing.
- Sources: writofinance.com, multiple

### Finding 14: Quality and Context Filters

CRT quality is improved by:
- Setup occurring at a significant HTF level (order block, FVG, weekly/daily liquidity pool, swing high/low).
- Setup occurring during a kill zone session.
- Setup aligning with the higher-timeframe trend or institutional order flow.
- Presence of an FVG or order block in the entry area after the sweep.

CRT quality is reduced by:
- Trading every candle sweep without context.
- Setup occurring during low-volatility or off-session hours.
- Body close outside the parent range (invalidation).

---

## Source URLs Captured For Intake

| Source ID | Type | URL | Status |
| --- | --- | --- | --- |
| CRT-SOURCE-001 | Web article | https://www.writofinance.com/candle-range-theory-crt/ | RESEARCH_ONLY |
| CRT-SOURCE-002 | Web article (community) | tradingwyckoff.com/candle-range-theory/ | RESEARCH_ONLY |
| CRT-SOURCE-003 | Web article (community) | innercircletrader.net/candle-range-theory/ | RESEARCH_ONLY |
| CRT-SOURCE-004 | Document | scribd.com (multiple CRT documents by Romeo community) | RESEARCH_ONLY |
| CRT-SOURCE-005 | Web article | forexalgo-trader.com | RESEARCH_ONLY |
| CRT-SOURCE-006 | Web article | arongroups.co | RESEARCH_ONLY |
| CRT-SOURCE-007 | Web article | binance.com/en/square/post | RESEARCH_ONLY |

---

## Required Next Action

1. **Project lead must review** Findings 2–14 above and confirm or reject each against personal knowledge of Romeo's teachings.
2. Any finding that project lead confirms should be promoted to `PROPOSED` → `APPROVED` in `CRT_RULEBOOK.md`.
3. The direct Romeo YouTube channel (Romeotpt) must be found and linked as the primary source authority for final rule approval.
4. Search on YouTube: `"Romeotpt"` or `"Romeo CRT"` or `"Romeo CRT & TS"` to find primary video sources.
5. Once a direct video link with timestamp is obtained, promote to `CRT_SOURCE_INTAKE.md` with `PENDING_REVIEW` status for project lead sign-off.

---

## Generic Candlestick References

These sources support generic OHLC terminology only. They do not define CRT.

- `CRT-SOURCE-GEN-001`: Wikipedia, `Candlestick chart`: https://en.wikipedia.org/wiki/Candlestick_chart
- `CRT-SOURCE-GEN-002`: Investopedia, `Understanding Basic Candlestick Charts`: https://www.investopedia.com/trading/candlestick-charting-what-is-it/
- `CRT-SOURCE-GEN-003`: Wikipedia, `Candlestick pattern`: https://en.wikipedia.org/wiki/Candlestick_pattern
- `CRT-SOURCE-GEN-004`: Wikipedia, `Price action trading`: https://en.wikipedia.org/wiki/Price_action_trading
- `CRT-SOURCE-GEN-005`: Wikipedia, `Average true range`: https://en.wikipedia.org/wiki/Average_true_range
