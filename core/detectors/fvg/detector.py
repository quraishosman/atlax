"""
ATLAX Lower-Timeframe Fair Value Gap (LTF FVG) Detector.

Detects Fair Value Gaps on a lower timeframe (LTF) candle sequence
that were created during the sweep/displacement leg of a CRT setup.

An FVG is a 3-candle imbalance:
  - Bullish FVG: candle[i-1].high < candle[i+1].low
    (a gap between C1's high and C3's low — price swept DOWN creating
     an imbalance; price will retrace UP through this FVG)
  - Bearish FVG: candle[i-1].low > candle[i+1].high
    (a gap between C3's high and C1's low — price swept UP creating
     an imbalance; price will retrace DOWN through this FVG)

Usage in ATLAX:
    When a CRT is detected on the HTF (e.g. H1), the scanner fetches
    the LTF candles from the same window (e.g. M5) and runs this detector
    to find the imbalance left by the displacement move.

    The FVG zone is then stored as a pending watch entry. When live price
    retraces into the FVG zone, a second "entry triggered" alert fires.

Authority: docs/07_DETECTOR_SPECIFICATION.md (detector boundaries)
           docs/09_CONFIDENCE_ENGINE.md (fvg_present_after_sweep factor)

Architectural Boundaries:
    - Detector ONLY. No trade decisions, no execution.
    - Returns FVGResult with zone bounds if found, or None if not found.
    - Stateless. All inputs provided by the scanner.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from core.models.candle import Candle, CandleSequence

logger = logging.getLogger("atlax.detection.fvg")


@dataclass(frozen=True)
class FVGResult:
    """
    The result of an LTF FVG detection scan.

    Attributes:
        found:      True if a valid FVG was found in the LTF window.
        direction:  "bullish" | "bearish" — matches the CRT direction.
        fvg_high:   Upper boundary of the FVG zone.
        fvg_low:    Lower boundary of the FVG zone.
        formed_at:  Open time of the middle (displacement) candle.
        ltf:        The lower timeframe used for detection (e.g. "M5").
    """
    found: bool
    direction: str
    fvg_high: Optional[Decimal]
    fvg_low: Optional[Decimal]
    formed_at: Optional[datetime]
    ltf: str

    @property
    def midpoint(self) -> Optional[Decimal]:
        """The midpoint (50%) of the FVG zone."""
        if self.fvg_high is not None and self.fvg_low is not None:
            return (self.fvg_high + self.fvg_low) / Decimal("2")
        return None


class LTFFVGDetector:
    """
    Detects Fair Value Gaps on a lower-timeframe candle sequence.

    The scanner provides the LTF candle window that corresponds to
    the time window of the CRT sweep/confirmation candles on the HTF.
    This detector scans those LTF candles for a 3-candle FVG pattern
    matching the expected CRT direction.

    Args:
        min_fvg_pips: Minimum gap size in pips. FVGs smaller than this
                      are ignored (noise filter). Configurable.
        pip_size:     Pip size for this instrument (e.g. 0.0001 for EURUSD).
    """

    def __init__(self, min_fvg_pips: float = 0.5, pip_size: float = 0.0001) -> None:
        self._min_fvg_pips = Decimal(str(min_fvg_pips))
        self._pip_size = Decimal(str(pip_size))

    def detect(
        self,
        ltf_sequence: CandleSequence,
        crt_direction: str,
    ) -> FVGResult:
        """
        Scan the LTF candle sequence for a Fair Value Gap.

        Scans from newest to oldest (most recent FVG takes priority)
        and returns the first (most recent) valid FVG matching the
        expected direction.

        Args:
            ltf_sequence:   LTF candles for the sweep window (oldest first).
            crt_direction:  "bullish" or "bearish" — the CRT classification.

        Returns:
            FVGResult with found=True and zone bounds if an FVG was found,
            otherwise FVGResult with found=False.
        """
        candles = ltf_sequence.candles
        ltf = ltf_sequence.timeframe

        if len(candles) < 3:
            logger.debug(
                '{"event":"fvg_insufficient_data","symbol":"%s","ltf":"%s","count":%d}',
                ltf_sequence.symbol, ltf, len(candles),
            )
            return FVGResult(
                found=False, direction=crt_direction,
                fvg_high=None, fvg_low=None,
                formed_at=None, ltf=ltf,
            )

        min_gap = self._min_fvg_pips * self._pip_size

        # Scan newest-to-oldest to return the most recent FVG
        for i in range(len(candles) - 1, 1, -1):
            c1 = candles[i - 2]  # oldest of the 3
            c2 = candles[i - 1]  # middle (displacement candle)
            c3 = candles[i]      # newest of the 3

            if crt_direction == "bearish":
                # Bearish CRT → displacement candle swept UP
                # Bearish FVG: C1.low > C3.high  (gap above between C3 and C1)
                # Price will retrace UP into this gap before dropping
                gap = c1.low - c3.high
                if gap >= min_gap:
                    fvg_high = c1.low
                    fvg_low = c3.high
                    logger.info(
                        '{"event":"fvg_detected","symbol":"%s","ltf":"%s",'
                        '"direction":"bearish","fvg_high":"%s","fvg_low":"%s",'
                        '"gap_pips":"%.1f","formed_at":"%s"}',
                        ltf_sequence.symbol, ltf, str(fvg_high), str(fvg_low),
                        float(gap / self._pip_size),
                        c2.open_time.isoformat(),
                    )
                    return FVGResult(
                        found=True,
                        direction="bearish",
                        fvg_high=fvg_high,
                        fvg_low=fvg_low,
                        formed_at=c2.open_time,
                        ltf=ltf,
                    )

            elif crt_direction == "bullish":
                # Bullish CRT → displacement candle swept DOWN
                # Bullish FVG: C1.high < C3.low  (gap below between C1 and C3)
                # Price will retrace DOWN into this gap before rising
                gap = c3.low - c1.high
                if gap >= min_gap:
                    fvg_high = c3.low
                    fvg_low = c1.high
                    logger.info(
                        '{"event":"fvg_detected","symbol":"%s","ltf":"%s",'
                        '"direction":"bullish","fvg_high":"%s","fvg_low":"%s",'
                        '"gap_pips":"%.1f","formed_at":"%s"}',
                        ltf_sequence.symbol, ltf, str(fvg_high), str(fvg_low),
                        float(gap / self._pip_size),
                        c2.open_time.isoformat(),
                    )
                    return FVGResult(
                        found=True,
                        direction="bullish",
                        fvg_high=fvg_high,
                        fvg_low=fvg_low,
                        formed_at=c2.open_time,
                        ltf=ltf,
                    )

        logger.debug(
            '{"event":"fvg_not_found","symbol":"%s","ltf":"%s","direction":"%s",'
            '"candles_scanned":%d}',
            ltf_sequence.symbol, ltf, crt_direction, len(candles),
        )
        return FVGResult(
            found=False, direction=crt_direction,
            fvg_high=None, fvg_low=None,
            formed_at=None, ltf=ltf,
        )
