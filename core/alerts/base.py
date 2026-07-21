"""
ATLAX Base Alert Channel Contract.

Abstract base class that every alert delivery channel must implement.
Enforces the single-responsibility boundary: channels send alerts only.

Authority: docs/03_ARCHITECTURE.md
    "Alert Engine: Sends alerts only. Never executes trades."
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.models.alert_event import AlertEvent


class BaseAlertChannel(ABC):
    """
    Abstract contract for all ATLAX alert delivery channels.

    Every channel must:
    - Accept an AlertEvent.
    - Deliver it to a specific destination (log, Telegram, etc.).
    - Return True on success, False on failure.
    - Never execute trades.
    - Never modify the AlertEvent.
    - Log its own delivery success/failure.

    Authority: docs/03_ARCHITECTURE.md
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique name of this channel (e.g., 'log', 'telegram')."""

    @property
    @abstractmethod
    def is_enabled(self) -> bool:
        """Whether this channel is currently active."""

    @abstractmethod
    def send(self, alert: AlertEvent) -> bool:
        """
        Deliver an alert to this channel.

        Args:
            alert: The fully formed AlertEvent to deliver.

        Returns:
            True if delivery succeeded, False if it failed gracefully.
            Must never raise — failures are logged and returned as False.
        """
