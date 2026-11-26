"""
ATLAX FVG Entry Watcher.

Holds pending CRT+FVG signals and monitors live price tick-by-tick
(on each scanner cycle) to detect when price retraces into the LTF
Fair Value Gap zone. When price enters the zone, fires the second
"Entry Triggered" alert to all subscribers.

Two-alert flow:
    Alert 1 (CRT Detected):
        Fired immediately when the HTF CRT forms AND an LTF FVG is
        found in the displacement window. Tells traders to wait for
        the pullback into the FVG.

    Alert 2 (FVG Entry Triggered):
        Fired when live price taps into the FVG zone. This is the
        actual entry trigger — the moment price arrives at the level.

State:
    Pending watches are persisted to data/fvg_watches.json so they
    survive a scanner restart. Each watch expires after max_age_seconds
    (configured in atlax.yaml, default 4 hours).

Authority: docs/07_DETECTOR_SPECIFICATION.md (detector boundaries)
           docs/15_LOGGING.md (required logging)
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from core.detectors.fvg.detector import FVGResult

logger = logging.getLogger("atlax.detection.fvg_watcher")

_STATE_FILE = Path("data/fvg_watches.json")


@dataclass
class FVGWatch:
    """
    A single pending FVG watch entry.

    Created when a CRT is detected with an LTF FVG present.
    Expires after max_age_seconds without a price touch.

    Attributes:
        watch_id:       Unique identifier.
        symbol:         Trading instrument.
        htf_timeframe:  The HTF on which the CRT was detected (e.g. "H1").
        ltf_timeframe:  The LTF on which the FVG was found (e.g. "M5").
        direction:      "bullish" or "bearish".
        fvg_high:       Upper boundary of the FVG zone.
        fvg_low:        Lower boundary of the FVG zone.
        created_at:     UTC timestamp when the watch was created.
        expires_at_ts:  Unix timestamp when this watch expires.
        triggered:      True once price has tapped the zone.
        candidate_id:   The CRT candidate_id this FVG belongs to.
        confidence_score: The HTF confidence score for alert 2 display.
        profile:        The trading profile (scalper/day_trader/swing_trader).
        entry_zone_high: HTF entry zone high (for the trade levels display).
        entry_zone_low:  HTF entry zone low.
        invalidation_level: HTF stop loss level.
        midpoint_target: HTF TP1.
        opposite_extreme_target: HTF TP2.
    """
    watch_id: str
    symbol: str
    htf_timeframe: str
    ltf_timeframe: str
    direction: str
    fvg_high: str    # stored as string for JSON serialisation
    fvg_low: str
    created_at: str  # ISO string
    expires_at_ts: float
    triggered: bool
    candidate_id: str
    confidence_score: float
    profile: str
    entry_zone_high: str
    entry_zone_low: str
    invalidation_level: str
    midpoint_target: str
    opposite_extreme_target: str
    parent_at_htf_zone: Optional[bool] = None
    profile_min_confidence: Optional[float] = None
    quality_status: str = "UNKNOWN"
    quality_would_suppress: bool = True


class FVGWatcher:
    """
    Monitors live price against pending FVG zones and fires entry alerts.

    Usage (called from scanner.py on every cycle):
        watcher.add_watch_full(symbol, fvg_result, candidate_id, conf_score, ...)
        triggered = watcher.check_live_price(symbol, timeframe, live_high, live_low)
        for watch in triggered:
            # fire Alert 2 for each triggered watch
    """

    def __init__(self, max_age_seconds: int = 14400, state_store=None) -> None:
        """
        Args:
            max_age_seconds: How long to hold a pending watch before
                             expiring it. Default 4 hours (14400s).
        """
        self._max_age_seconds = max_age_seconds
        self._state_store = state_store
        self._watches: list[FVGWatch] = self._load_state()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_watch_full(
        self,
        symbol: str,
        fvg: FVGResult,
        candidate_id: str,
        confidence_score: float,
        profile: str,
        entry_zone_high: Decimal,
        entry_zone_low: Decimal,
        invalidation_level: Decimal,
        midpoint_target: Decimal,
        opposite_extreme_target: Decimal,
        htf_timeframe: str,
        parent_at_htf_zone: Optional[bool] = None,
        profile_min_confidence: Optional[float] = None,
        quality_status: str = "UNKNOWN",
        quality_would_suppress: bool = True,
    ) -> FVGWatch:
        """
        Register a new pending FVG watch (explicit symbol parameter).

        This is the canonical method. Call this from the scanner.
        """
        now = datetime.now(timezone.utc)
        watch = FVGWatch(
            watch_id=str(uuid.uuid4()),
            symbol=symbol,
            htf_timeframe=htf_timeframe,
            ltf_timeframe=fvg.ltf,
            direction=fvg.direction,
            fvg_high=str(fvg.fvg_high),
            fvg_low=str(fvg.fvg_low),
            created_at=now.isoformat(),
            expires_at_ts=(now.timestamp() + self._max_age_seconds),
            triggered=False,
            candidate_id=candidate_id,
            confidence_score=confidence_score,
            profile=profile,
            entry_zone_high=str(entry_zone_high),
            entry_zone_low=str(entry_zone_low),
            invalidation_level=str(invalidation_level),
            midpoint_target=str(midpoint_target),
            opposite_extreme_target=str(opposite_extreme_target),
            parent_at_htf_zone=parent_at_htf_zone,
            profile_min_confidence=profile_min_confidence,
            quality_status=quality_status,
            quality_would_suppress=quality_would_suppress,
        )
        self._watches.append(watch)
        self._save_state()
        logger.info(
            '{"event":"fvg_watch_added","watch_id":"%s","symbol":"%s",'
            '"htf":"%s","ltf":"%s","direction":"%s","fvg_high":"%s","fvg_low":"%s",'
            '"expires_in_hours":%.1f}',
            watch.watch_id, symbol, htf_timeframe, fvg.ltf,
            fvg.direction, str(fvg.fvg_high), str(fvg.fvg_low),
            self._max_age_seconds / 3600,
        )
        return watch

    def check_live_price(
        self,
        symbol: str,
        live_bid: float,
        live_ask: float,
    ) -> list[FVGWatch]:
        """
        Check if live price has tapped into any pending FVG zones.

        Called on every scanner cycle for every instrument.

        Args:
            symbol:   The instrument symbol.
            live_bid: Current live bid price.
            live_ask: Current live ask price.

        Returns:
            List of FVGWatch entries that have just been triggered.
            These should be used to fire Alert 2 immediately.
        """
        now_ts = datetime.now(timezone.utc).timestamp()
        triggered = []
        still_active = []

        for watch in self._watches:
            # Skip other symbols
            if watch.symbol != symbol:
                still_active.append(watch)
                continue

            # Expire old watches
            if now_ts > watch.expires_at_ts:
                logger.info(
                    '{"event":"fvg_watch_expired","watch_id":"%s","symbol":"%s",'
                    '"direction":"%s"}',
                    watch.watch_id, symbol, watch.direction,
                )
                continue  # Drop it

            # Already triggered — don't re-trigger
            if watch.triggered:
                still_active.append(watch)
                continue

            fvg_high = Decimal(watch.fvg_high)
            fvg_low  = Decimal(watch.fvg_low)
            bid = Decimal(str(live_bid))
            ask = Decimal(str(live_ask))

            tapped = False
            if watch.direction == "bearish":
                # Bearish FVG: zone is ABOVE current price.
                # Triggered when ask price rises INTO the zone.
                if ask >= fvg_low and bid <= fvg_high:
                    tapped = True
            elif watch.direction == "bullish":
                # Bullish FVG: zone is BELOW current price.
                # Triggered when bid price falls INTO the zone.
                if bid <= fvg_high and ask >= fvg_low:
                    tapped = True

            if tapped:
                logger.info(
                    '{"event":"fvg_entry_triggered","watch_id":"%s","symbol":"%s",'
                    '"direction":"%s","fvg_high":"%s","fvg_low":"%s",'
                    '"live_bid":"%.5f","live_ask":"%.5f"}',
                    watch.watch_id, symbol, watch.direction,
                    watch.fvg_high, watch.fvg_low, live_bid, live_ask,
                )
                # Mark as triggered (create new frozen-style object via dict)
                triggered_watch = FVGWatch(
                    watch_id=watch.watch_id,
                    symbol=watch.symbol,
                    htf_timeframe=watch.htf_timeframe,
                    ltf_timeframe=watch.ltf_timeframe,
                    direction=watch.direction,
                    fvg_high=watch.fvg_high,
                    fvg_low=watch.fvg_low,
                    created_at=watch.created_at,
                    expires_at_ts=watch.expires_at_ts,
                    triggered=True,
                    candidate_id=watch.candidate_id,
                    confidence_score=watch.confidence_score,
                    profile=watch.profile,
                    entry_zone_high=watch.entry_zone_high,
                    entry_zone_low=watch.entry_zone_low,
                    invalidation_level=watch.invalidation_level,
                    midpoint_target=watch.midpoint_target,
                    opposite_extreme_target=watch.opposite_extreme_target,
                    parent_at_htf_zone=watch.parent_at_htf_zone,
                    profile_min_confidence=watch.profile_min_confidence,
                    quality_status=watch.quality_status,
                    quality_would_suppress=watch.quality_would_suppress,
                )
                still_active.append(triggered_watch)
                triggered.append(triggered_watch)
            else:
                still_active.append(watch)

        self._watches = still_active
        if triggered:
            self._save_state()

        return triggered

    def active_watch_count(self) -> int:
        """Return the number of currently active (non-expired, non-triggered) watches."""
        now_ts = datetime.now(timezone.utc).timestamp()
        return sum(
            1 for w in self._watches
            if not w.triggered and now_ts <= w.expires_at_ts
        )

    def clear_watches(self, reason: str = "configuration_disabled") -> int:
        """Remove pending entry watches when the deferred flow is disabled."""
        count = len(self._watches)
        if count == 0:
            return 0
        self._watches = []
        self._save_state()
        logger.info(
            '{"event":"fvg_watches_cleared","count":%d,"reason":"%s"}',
            count,
            str(reason)[:120],
        )
        return count

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_state(self) -> None:
        """Persist current watches to disk."""
        data = [asdict(watch) for watch in self._watches]
        if self._state_store is not None:
            self._state_store.set("detector.fvg_watches", data)
            return
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(
                json.dumps(data, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning(
                '{"event":"fvg_watcher_save_failed","reason":"%s"}', str(exc)
            )

    def _load_state(self) -> list[FVGWatch]:
        """Load persisted watches from disk on startup."""
        if self._state_store is not None:
            data = self._state_store.get("detector.fvg_watches", [])
            if isinstance(data, list):
                now_ts = datetime.now(timezone.utc).timestamp()
                return [
                    FVGWatch(**item)
                    for item in data
                    if isinstance(item, dict)
                    and not item.get("triggered")
                    and item.get("expires_at_ts", 0) >= now_ts
                ]
            return []
        try:
            if _STATE_FILE.exists():
                data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
                now_ts = datetime.now(timezone.utc).timestamp()
                watches = []
                for d in data:
                    # Skip already-triggered or expired entries on load
                    if d.get("triggered"):
                        continue
                    if d.get("expires_at_ts", 0) < now_ts:
                        continue
                    watches.append(FVGWatch(**d))
                logger.info(
                    '{"event":"fvg_watches_loaded","count":%d}', len(watches)
                )
                return watches
        except Exception as exc:
            logger.warning(
                '{"event":"fvg_watcher_load_failed","reason":"%s"}', str(exc)
            )
        return []
