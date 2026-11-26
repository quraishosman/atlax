"""Account identity and runtime risk-mode contracts for ATLAX."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class TradingAccountMode(str, Enum):
    """Risk policy selected for the currently connected MT5 account."""

    NORMAL_ACCOUNT = "NORMAL_ACCOUNT"
    PROP_FIRM_SHADOW = "PROP_FIRM_SHADOW"
    PROP_FIRM_ENFORCED = "PROP_FIRM_ENFORCED"
    AUTO_ENFORCED = "AUTO_ENFORCED"


class AccountModeStatus(str, Enum):
    """Verification status of the selected mode and account binding."""

    NOT_REQUIRED = "NOT_REQUIRED"
    VERIFIED = "VERIFIED"
    UNKNOWN = "UNKNOWN"
    MISMATCH = "MISMATCH"
    BLOCKED = "BLOCKED"


def _aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


@dataclass(frozen=True)
class MT5AccountIdentity:
    """Secret-free identity read from the connected MetaTrader terminal."""

    login: int
    server: str
    currency: str
    trade_mode: Optional[str]
    terminal_name: Optional[str]
    terminal_build: Optional[int]
    terminal_instance_id: Optional[str]
    observed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.login, int) or self.login <= 0:
            raise ValueError("login must be a positive integer")
        if not isinstance(self.server, str) or not self.server.strip():
            raise ValueError("server must be a non-empty string")
        if not isinstance(self.currency, str) or len(self.currency) != 3:
            raise ValueError("currency must be a three-letter code")
        if self.currency != self.currency.upper() or not self.currency.isalpha():
            raise ValueError("currency must be uppercase alphabetic")
        if self.terminal_build is not None and self.terminal_build < 0:
            raise ValueError("terminal_build must not be negative")
        _aware(self.observed_at, "observed_at")

    @property
    def fingerprint(self) -> str:
        """Stable non-secret identifier for audit correlation."""
        raw = "|".join(
            (
                str(self.login),
                self.server.strip(),
                self.currency,
                self.terminal_instance_id or "",
            )
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:20]

    def to_dict(self) -> dict[str, Any]:
        return {
            "login": self.login,
            "server": self.server,
            "currency": self.currency,
            "trade_mode": self.trade_mode,
            "terminal_name": self.terminal_name,
            "terminal_build": self.terminal_build,
            "terminal_instance_id": self.terminal_instance_id,
            "fingerprint": self.fingerprint,
            "observed_at": self.observed_at.astimezone(timezone.utc).isoformat(),
        }


@dataclass(frozen=True)
class AccountBinding:
    """Operator-approved binding between a mode and one MT5 account."""

    login: int
    server: str
    currency: str
    account_class: str
    terminal_instance_id: Optional[str]
    bound_at: datetime
    bound_by: str

    def __post_init__(self) -> None:
        if not isinstance(self.login, int) or self.login <= 0:
            raise ValueError("binding login must be a positive integer")
        if not isinstance(self.server, str) or not self.server.strip():
            raise ValueError("binding server must be non-empty")
        if not isinstance(self.currency, str) or len(self.currency) != 3:
            raise ValueError("binding currency must be a three-letter code")
        if self.currency != self.currency.upper() or not self.currency.isalpha():
            raise ValueError("binding currency must be uppercase alphabetic")
        if self.account_class != "PROP_FIRM":
            raise ValueError("account_class must be PROP_FIRM")
        if not isinstance(self.bound_by, str) or not self.bound_by.strip():
            raise ValueError("bound_by must be non-empty")
        _aware(self.bound_at, "bound_at")

    def matches(self, identity: MT5AccountIdentity) -> bool:
        return (
            self.login == identity.login
            and self.server.strip() == identity.server.strip()
            and self.currency == identity.currency
            and (
                not self.terminal_instance_id
                or self.terminal_instance_id == identity.terminal_instance_id
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "login": self.login,
            "server": self.server,
            "currency": self.currency,
            "account_class": self.account_class,
            "terminal_instance_id": self.terminal_instance_id,
            "bound_at": self.bound_at.astimezone(timezone.utc).isoformat(),
            "bound_by": self.bound_by,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "AccountBinding":
        if not isinstance(raw, dict):
            raise ValueError("account binding must be a mapping")
        bound_at = datetime.fromisoformat(str(raw["bound_at"]))
        if bound_at.tzinfo is None:
            bound_at = bound_at.replace(tzinfo=timezone.utc)
        return cls(
            login=int(raw["login"]),
            server=str(raw["server"]),
            currency=str(raw["currency"]).upper(),
            account_class=str(raw["account_class"]),
            terminal_instance_id=(
                str(raw["terminal_instance_id"])
                if raw.get("terminal_instance_id")
                else None
            ),
            bound_at=bound_at,
            bound_by=str(raw["bound_by"]),
        )


@dataclass(frozen=True)
class AccountModeState:
    """Restart-safe mode, binding, profile, and verification state."""

    revision: int = 0
    mode: TradingAccountMode = TradingAccountMode.NORMAL_ACCOUNT
    binding: Optional[AccountBinding] = None
    selected_profile_id: str = ""
    selected_profile_version: str = ""
    status: AccountModeStatus = AccountModeStatus.NOT_REQUIRED
    status_reason: str = ""
    last_verified_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    updated_by: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("revision must be a non-negative integer")
        if not isinstance(self.mode, TradingAccountMode):
            raise ValueError("mode must be TradingAccountMode")
        if not isinstance(self.status, AccountModeStatus):
            raise ValueError("status must be AccountModeStatus")
        if self.last_verified_at is not None:
            _aware(self.last_verified_at, "last_verified_at")
        if self.updated_at is not None:
            _aware(self.updated_at, "updated_at")
        for field_name in (
            "selected_profile_id",
            "selected_profile_version",
            "status_reason",
            "updated_by",
        ):
            if not isinstance(getattr(self, field_name), str):
                raise ValueError(f"{field_name} must be a string")

    @property
    def requires_prop_firm_context(self) -> bool:
        return self.mode in {
            TradingAccountMode.PROP_FIRM_SHADOW,
            TradingAccountMode.PROP_FIRM_ENFORCED,
            TradingAccountMode.AUTO_ENFORCED,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "mode": self.mode.value,
            "binding": self.binding.to_dict() if self.binding else None,
            "selected_profile_id": self.selected_profile_id,
            "selected_profile_version": self.selected_profile_version,
            "status": self.status.value,
            "status_reason": self.status_reason[:240],
            "last_verified_at": (
                self.last_verified_at.astimezone(timezone.utc).isoformat()
                if self.last_verified_at
                else None
            ),
            "updated_at": (
                self.updated_at.astimezone(timezone.utc).isoformat()
                if self.updated_at
                else None
            ),
            "updated_by": self.updated_by[:80],
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "AccountModeState":
        if not isinstance(raw, dict) or not raw:
            return cls()

        def _enum(enum_type, value, default):
            try:
                return enum_type(str(value))
            except (TypeError, ValueError):
                return default

        def _datetime(value):
            if not value:
                return None
            parsed = datetime.fromisoformat(str(value))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

        binding = None
        if raw.get("binding") is not None:
            binding = AccountBinding.from_dict(raw["binding"])
        return cls(
            revision=max(0, int(raw.get("revision", 0) or 0)),
            mode=_enum(TradingAccountMode, raw.get("mode"), TradingAccountMode.NORMAL_ACCOUNT),
            binding=binding,
            selected_profile_id=str(raw.get("selected_profile_id") or ""),
            selected_profile_version=str(raw.get("selected_profile_version") or ""),
            status=_enum(AccountModeStatus, raw.get("status"), AccountModeStatus.UNKNOWN),
            status_reason=str(raw.get("status_reason") or "")[:240],
            last_verified_at=_datetime(raw.get("last_verified_at")),
            updated_at=_datetime(raw.get("updated_at")),
            updated_by=str(raw.get("updated_by") or "")[:80],
        )
