"""
ATLAX Base Strategy Contract.

Abstract base class that every ATLAX strategy must implement.
Enforces the single-responsibility boundary: strategies consume
DetectorOutput and produce StrategyDecision only.

Authority: docs/08_STRATEGY_ENGINE.md
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.models.detector_output import DetectorOutput
from core.models.trade_candidate import StrategyDecision
from core.settings.strategy_config import ProfileConfig, StrategyConfig


class BaseStrategy(ABC):
    """
    Abstract contract for all ATLAX strategy implementations.

    Every strategy must:
    - Accept a DetectorOutput as its primary input.
    - Produce a StrategyDecision (trade_candidate | no_trade | UNKNOWN).
    - Never talk directly to MT5, TradingView, or any broker.
    - Never return execution instructions (lot_size, risk_percentage, etc.).
    - Never invent trading rules not documented in a rulebook.

    Authority: docs/08_STRATEGY_ENGINE.md
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique name of this strategy."""

    @abstractmethod
    def evaluate(
        self,
        detector_output: DetectorOutput,
        profile: ProfileConfig,
        config: StrategyConfig,
    ) -> StrategyDecision:
        """
        Evaluate a detector output and return a strategy decision.

        Args:
            detector_output: The output from a detector's detect() call.
            profile: The trader profile context for this evaluation.
            config: The active strategy configuration.

        Returns:
            StrategyDecision with outcome trade_candidate | no_trade | UNKNOWN.
        """
