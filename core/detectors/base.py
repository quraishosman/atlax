"""
ATLAX Base Detector Abstract Class.

Defines the contract that every ATLAX detector must implement.
All detectors are stateless, deterministic, and classification-only.

Authority Documents:
    - docs/07_DETECTOR_SPECIFICATION.md (detector responsibilities)
    - docs/03_ARCHITECTURE.md (layer boundaries)

Architectural Boundaries:
    - Detectors identify patterns. They do NOT make trade decisions.
    - Detectors must never return BUY, SELL, EXECUTE, lot_size,
      risk_percentage, stop_loss, or take_profit.
    - Each detector is independent — no detector may know another
      detector exists. The Strategy Engine is the integration point.
    - Detectors are stateless: no instance variables carry state
      between detect() calls.
    - Detectors are deterministic: same input always produces
      same output.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.models.candle import CandleSequence
from core.models.detector_output import DetectorOutput


class BaseDetector(ABC):
    """
    Abstract base class for all ATLAX detectors.

    Every detector must implement the detect() method, which receives
    a CandleSequence and returns a DetectorOutput.

    Subclass Contract:
        1. detect() must be stateless — no instance variables carry
           state between calls.
        2. detect() must be deterministic — same CandleSequence always
           produces the same DetectorOutput.
        3. detect() must never return classification values like BUY,
           SELL, EXECUTE, LONG, or SHORT.
        4. detect() must return classification="UNKNOWN" if input data
           is insufficient for evaluation.
        5. detect() must not access external state (network, files,
           databases) during evaluation.

    Authority:
        docs/07_DETECTOR_SPECIFICATION.md
        docs/03_ARCHITECTURE.md
    """

    @abstractmethod
    def detect(self, sequence: CandleSequence) -> DetectorOutput:
        """
        Evaluate a candle sequence for a specific pattern.

        Args:
            sequence: An immutable, ordered sequence of candles
                      for a single symbol and timeframe.

        Returns:
            A DetectorOutput describing whether the pattern was
            detected, the classification, the reason, and any
            quality metadata for downstream engines.

        Contract:
            - Must be stateless and deterministic.
            - Must never return BUY or SELL.
            - Must return classification=UNKNOWN if data is insufficient.
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """
        The unique name of this detector (e.g., 'CRTDetector').

        Used in DetectorOutput.detector and in logging.
        """
        ...
