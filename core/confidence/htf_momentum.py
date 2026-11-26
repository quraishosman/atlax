"""Deterministic closed-candle higher-timeframe momentum evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from core.models.candle import CandleSequence
from core.models.trade_candidate import Direction
from core.settings.htf_momentum_config import HTFMomentumConfig


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class HTFMomentumEvaluation:
    symbol: str
    controller_timeframe: str
    event_timestamp: datetime
    bias: str
    relationship: str
    status: str
    run_length: int
    strong_run: bool
    eligible_candles: int
    source_close_times: tuple[str, ...]
    reason: str

    def adjustment(self, config: HTFMomentumConfig) -> float:
        """Return the configured counterfactual adjustment for this result."""
        if self.relationship == "ALIGNED":
            run_bonus = min(
                max(self.run_length - 1, 0) * config.run_bonus_per_extra_candle,
                config.max_run_bonus,
            )
            return config.aligned_adjustment + run_bonus
        if self.relationship == "OPPOSING":
            return config.opposing_adjustment
        if self.relationship == "SIDEWAYS":
            return config.sideways_adjustment
        return config.unknown_adjustment

    def to_dict(self) -> dict:
        return {
            "controller_timeframe": self.controller_timeframe,
            "event_timestamp": self.event_timestamp.isoformat(),
            "bias": self.bias,
            "relationship": self.relationship,
            "status": self.status,
            "run_length": self.run_length,
            "strong_run": self.strong_run,
            "eligible_candles": self.eligible_candles,
            "source_close_times": list(self.source_close_times),
            "reason": self.reason,
        }


class HTFMomentumEvaluator:
    """Evaluate H1 direction without using forming or future candles."""

    def __init__(self, config: HTFMomentumConfig) -> None:
        self._config = config

    def evaluate(
        self,
        sequence: Optional[CandleSequence],
        *,
        event_timestamp: datetime,
        direction: str,
    ) -> HTFMomentumEvaluation:
        event_time = _utc_naive(event_timestamp)
        if sequence is None:
            return self._unknown(
                event_time,
                direction,
                "H1 sequence unavailable",
                symbol="UNKNOWN",
            )

        eligible = [
            candle for candle in sequence.candles
            if candle.is_closed and _utc_naive(candle.close_time) <= event_time
        ][-self._config.lookback_candles:]
        if not eligible:
            return self._unknown(
                event_time,
                direction,
                "No closed H1 candle before event",
                symbol=sequence.symbol,
            )

        latest = eligible[-1]
        if latest.close == latest.open:
            return self._unknown(
                event_time,
                direction,
                "Latest H1 candle is neutral",
                symbol=sequence.symbol,
            )

        bias = "BULLISH" if latest.is_bullish else "BEARISH"
        run_length = 0
        for candle in reversed(eligible):
            if bias == "BULLISH" and candle.is_bullish:
                run_length += 1
            elif bias == "BEARISH" and candle.is_bearish:
                run_length += 1
            else:
                break

        candidate_direction = str(direction or "").upper()
        if candidate_direction == Direction.BUY:
            relationship = "ALIGNED" if bias == "BULLISH" else "OPPOSING"
        elif candidate_direction == Direction.SELL:
            relationship = "ALIGNED" if bias == "BEARISH" else "OPPOSING"
        else:
            return self._unknown(
                event_time,
                direction,
                "Candidate direction is unknown",
                symbol=sequence.symbol,
            )

        close_times = tuple(
            _utc_naive(candle.close_time).isoformat() for candle in eligible[-run_length:]
        )
        return HTFMomentumEvaluation(
            symbol=sequence.symbol,
            controller_timeframe=sequence.timeframe,
            event_timestamp=event_time,
            bias=bias,
            relationship=relationship,
            status="VALID",
            run_length=run_length,
            strong_run=run_length >= self._config.strong_run_candles,
            eligible_candles=len(eligible),
            source_close_times=close_times,
            reason=(
                f"Latest closed {sequence.timeframe} candle is {bias.lower()}; "
                f"consecutive run length={run_length}."
            ),
        )

    def _unknown(
        self,
        event_time: datetime,
        direction: str,
        reason: str,
        *,
        symbol: str,
    ) -> HTFMomentumEvaluation:
        return HTFMomentumEvaluation(
            symbol=symbol,
            controller_timeframe=self._config.controller_timeframe,
            event_timestamp=event_time,
            bias="UNKNOWN",
            relationship="UNKNOWN",
            status="UNKNOWN",
            run_length=0,
            strong_run=False,
            eligible_candles=0,
            source_close_times=(),
            reason=reason,
        )


def proposed_score(base_score: float, evaluation: HTFMomentumEvaluation, config: HTFMomentumConfig) -> float:
    """Return a bounded counterfactual score without mutating the base score."""
    adjustment = evaluation.adjustment(config)
    return round(max(0.0, min(100.0, float(base_score) + adjustment)), 2)
