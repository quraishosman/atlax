"""Immutable domain models for Phase 1 Prop-Firm Mode.

Authority: docs/10_RISK_ENGINE.md
           docs/13_CONFIGURATION.md
           docs/42_PROP_FIRM_LOSS_BUDGET_PLAN.md

These models deliberately do not contain lot-sizing or MT5 calls. A later
phase may consume their validated snapshots through the Risk Engine.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class LossLimitMode(str, Enum):
    """How the reference floor for a loss rule is maintained."""

    STATIC = "STATIC"
    TRAILING = "TRAILING"
    END_OF_DAY_TRAILING = "END_OF_DAY_TRAILING"


class AccountReferenceBasis(str, Enum):
    """Account value used as the reference for a percentage limit."""

    INITIAL_BALANCE = "INITIAL_BALANCE"
    OPENING_BALANCE = "OPENING_BALANCE"
    OPENING_EQUITY = "OPENING_EQUITY"
    HIGH_WATERMARK_BALANCE = "HIGH_WATERMARK_BALANCE"
    HIGH_WATERMARK_EQUITY = "HIGH_WATERMARK_EQUITY"


class ValidationStatus(str, Enum):
    """Validation outcome used by fail-closed callers."""

    VALID = "VALID"
    UNKNOWN = "UNKNOWN"
    INVALID = "INVALID"


class RecoveryMode(str, Enum):
    """Deterministic prop-firm risk-recovery state."""

    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    RECOVERY = "RECOVERY"
    PAUSED = "PAUSED"
    RULE_VIOLATION = "RULE_VIOLATION"


def _require_decimal(value: Optional[Decimal], field_name: str) -> None:
    if value is None:
        return
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field_name} must be a finite Decimal")


def _require_aware_datetime(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    if value.utcoffset() is None:
        raise ValueError(f"{field_name} must have a valid UTC offset")


def _validate_timezone(value: Optional[str], field_name: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty IANA timezone")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a valid IANA timezone") from exc


@dataclass(frozen=True)
class LossBudgetDecision:
    """Auditable result of a prop-firm loss-budget evaluation."""

    status: ValidationStatus
    profile_id: str
    profile_version: str
    overall_floor: Optional[Decimal]
    daily_floor: Optional[Decimal]
    remaining_overall_buffer: Optional[Decimal]
    remaining_daily_buffer: Optional[Decimal]
    open_risk: Optional[Decimal]
    cost_reserve: Optional[Decimal]
    max_open_risk_budget: Optional[Decimal]
    available_budget: Optional[Decimal]
    allowed_new_trade_risk: Optional[Decimal]
    blocked: bool
    reason: str
    evaluated_at: datetime
    recovery_mode: Optional[str] = None
    risk_multiplier: Optional[Decimal] = None
    transaction_cost_reserve: Optional[Decimal] = None
    slippage_reserve: Optional[Decimal] = None
    recovery_state: Optional["PropFirmRecoveryState"] = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ValidationStatus):
            raise ValueError("LossBudgetDecision.status must be ValidationStatus")
        if not self.profile_id or not self.profile_version:
            raise ValueError("LossBudgetDecision profile identity is required")
        if not self.reason:
            raise ValueError("LossBudgetDecision.reason is required")
        for field_name in (
            "overall_floor",
            "daily_floor",
            "remaining_overall_buffer",
            "remaining_daily_buffer",
            "open_risk",
            "cost_reserve",
            "max_open_risk_budget",
            "available_budget",
            "allowed_new_trade_risk",
            "risk_multiplier",
            "transaction_cost_reserve",
            "slippage_reserve",
        ):
            _require_decimal(getattr(self, field_name), field_name)
        if self.recovery_state is not None and not isinstance(
            self.recovery_state, PropFirmRecoveryState
        ):
            raise ValueError("recovery_state must be PropFirmRecoveryState")
        _require_aware_datetime(self.evaluated_at, "evaluated_at")

    def to_dict(self) -> dict:
        """Serialize without leaking Enum or datetime implementation details."""
        data = asdict(self)
        data["status"] = self.status.value
        for field_name in (
            "overall_floor",
            "daily_floor",
            "remaining_overall_buffer",
            "remaining_daily_buffer",
            "open_risk",
            "cost_reserve",
            "max_open_risk_budget",
            "available_budget",
            "allowed_new_trade_risk",
        ):
            value = getattr(self, field_name)
            data[field_name] = str(value) if value is not None else None
        data["evaluated_at"] = self.evaluated_at.isoformat()
        data["recovery_state"] = (
            self.recovery_state.to_dict()
            if self.recovery_state is not None
            else None
        )
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "LossBudgetDecision":
        """Hydrate persisted decisions without applying a fallback."""
        raw_status = str(data.get("status", ValidationStatus.UNKNOWN.value))
        try:
            status = ValidationStatus(raw_status)
        except ValueError:
            status = ValidationStatus.UNKNOWN

        def decimal_or_none(value):
            return Decimal(str(value)) if value is not None else None

        evaluated_at = datetime.fromisoformat(str(data["evaluated_at"]))
        if evaluated_at.tzinfo is None:
            evaluated_at = evaluated_at.replace(tzinfo=timezone.utc)
        return cls(
            status=status,
            profile_id=str(data["profile_id"]),
            profile_version=str(data["profile_version"]),
            overall_floor=decimal_or_none(data.get("overall_floor")),
            daily_floor=decimal_or_none(data.get("daily_floor")),
            remaining_overall_buffer=decimal_or_none(
                data.get("remaining_overall_buffer")
            ),
            remaining_daily_buffer=decimal_or_none(
                data.get("remaining_daily_buffer")
            ),
            open_risk=decimal_or_none(data.get("open_risk")),
            cost_reserve=decimal_or_none(data.get("cost_reserve")),
            max_open_risk_budget=decimal_or_none(
                data.get("max_open_risk_budget")
            ),
            available_budget=decimal_or_none(data.get("available_budget")),
            allowed_new_trade_risk=decimal_or_none(
                data.get("allowed_new_trade_risk")
            ),
            blocked=bool(data.get("blocked", True)),
            reason=str(data.get("reason", "UNKNOWN")),
            evaluated_at=evaluated_at,
            recovery_mode=(
                str(data["recovery_mode"])
                if data.get("recovery_mode") is not None
                else None
            ),
            risk_multiplier=decimal_or_none(data.get("risk_multiplier")),
            transaction_cost_reserve=decimal_or_none(
                data.get("transaction_cost_reserve")
            ),
            slippage_reserve=decimal_or_none(data.get("slippage_reserve")),
            recovery_state=(
                PropFirmRecoveryState.from_dict(data["recovery_state"])
                if isinstance(data.get("recovery_state"), dict)
                else None
            ),
        )


@dataclass(frozen=True)
class PropFirmRecoveryState:
    """Persistable state for the prop-firm recovery controller."""

    mode: RecoveryMode = RecoveryMode.NORMAL
    loss_streak: int = 0
    recovery_reference_equity: Optional[Decimal] = None
    stabilization_closed_trades: int = 0
    manual_resume_required: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.mode, RecoveryMode):
            raise ValueError("PropFirmRecoveryState.mode must be RecoveryMode")
        if not isinstance(self.loss_streak, int) or self.loss_streak < 0:
            raise ValueError("loss_streak must be a non-negative integer")
        if not isinstance(self.stabilization_closed_trades, int) or self.stabilization_closed_trades < 0:
            raise ValueError(
                "stabilization_closed_trades must be a non-negative integer"
            )
        _require_decimal(
            self.recovery_reference_equity,
            "recovery_reference_equity",
        )
        if self.recovery_reference_equity is not None and self.recovery_reference_equity < 0:
            raise ValueError("recovery_reference_equity must not be negative")
        if not isinstance(self.manual_resume_required, bool):
            raise ValueError("manual_resume_required must be a boolean")

    def to_dict(self) -> dict:
        return {
            "mode": self.mode.value,
            "loss_streak": self.loss_streak,
            "recovery_reference_equity": (
                str(self.recovery_reference_equity)
                if self.recovery_reference_equity is not None
                else None
            ),
            "stabilization_closed_trades": self.stabilization_closed_trades,
            "manual_resume_required": self.manual_resume_required,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PropFirmRecoveryState":
        try:
            mode = RecoveryMode(str(data.get("mode", RecoveryMode.NORMAL.value)))
        except ValueError:
            mode = RecoveryMode.RULE_VIOLATION
        raw_reference = data.get("recovery_reference_equity")
        return cls(
            mode=mode,
            loss_streak=int(data.get("loss_streak", 0)),
            recovery_reference_equity=(
                Decimal(str(raw_reference)) if raw_reference is not None else None
            ),
            stabilization_closed_trades=int(
                data.get("stabilization_closed_trades", 0)
            ),
            manual_resume_required=bool(data.get("manual_resume_required", False)),
        )


@dataclass(frozen=True)
class LossLimitSpec:
    """One source-defined loss limit.

    Exactly one of ``amount`` or ``percentage`` must be supplied. Percentage
    rules also identify the account reference used by the source rule. The
    evaluator must not infer a reference from the firm name.
    """

    mode: LossLimitMode
    basis: AccountReferenceBasis
    amount: Optional[Decimal] = None
    percentage: Optional[Decimal] = None
    reset_timezone: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, LossLimitMode):
            raise ValueError("LossLimitSpec.mode must be a LossLimitMode")
        if not isinstance(self.basis, AccountReferenceBasis):
            raise ValueError(
                "LossLimitSpec.basis must be an AccountReferenceBasis"
            )
        _require_decimal(self.amount, "LossLimitSpec.amount")
        _require_decimal(self.percentage, "LossLimitSpec.percentage")
        if (self.amount is None) == (self.percentage is None):
            raise ValueError(
                "LossLimitSpec requires exactly one of amount or percentage"
            )
        if self.amount is not None and self.amount <= 0:
            raise ValueError("LossLimitSpec.amount must be positive")
        if self.percentage is not None and not (0 < self.percentage <= 100):
            raise ValueError(
                "LossLimitSpec.percentage must be greater than 0 and at most 100"
            )
        _validate_timezone(self.reset_timezone, "LossLimitSpec.reset_timezone")
        if self.mode == LossLimitMode.END_OF_DAY_TRAILING:
            if self.reset_timezone is None:
                raise ValueError(
                    "END_OF_DAY_TRAILING requires reset_timezone"
                )
            if self.basis not in {
                AccountReferenceBasis.OPENING_BALANCE,
                AccountReferenceBasis.OPENING_EQUITY,
            }:
                raise ValueError(
                    "END_OF_DAY_TRAILING requires an opening account basis"
                )


@dataclass(frozen=True)
class PropFirmRuleProfile:
    """Versioned rules for one firm, program, phase, and account type."""

    profile_id: str
    firm_name: str
    program_name: str
    phase: str
    account_type: str
    currency: str
    overall_loss: LossLimitSpec
    daily_loss: Optional[LossLimitSpec]
    source_urls: tuple[str, ...]
    source_retrieved_at: datetime
    version: str
    content_hash: str
    nominal_balance: Optional[Decimal] = None
    effective_at: Optional[datetime] = None
    max_position_size: Optional[Decimal] = None
    minimum_trading_days: Optional[int] = None

    def __post_init__(self) -> None:
        for field_name in (
            "profile_id",
            "firm_name",
            "program_name",
            "phase",
            "account_type",
            "version",
            "content_hash",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if not isinstance(self.currency, str) or len(self.currency) != 3:
            raise ValueError("currency must be a three-letter code")
        if self.currency != self.currency.upper() or not self.currency.isalpha():
            raise ValueError("currency must be an uppercase alphabetic code")
        if not isinstance(self.overall_loss, LossLimitSpec):
            raise ValueError("overall_loss must be a LossLimitSpec")
        if self.daily_loss is not None and not isinstance(
            self.daily_loss, LossLimitSpec
        ):
            raise ValueError("daily_loss must be a LossLimitSpec or None")
        if not isinstance(self.source_urls, tuple) or not self.source_urls:
            raise ValueError("source_urls must contain at least one URL")
        _require_aware_datetime(self.source_retrieved_at, "source_retrieved_at")
        if self.effective_at is not None:
            _require_aware_datetime(self.effective_at, "effective_at")
        _require_decimal(self.nominal_balance, "nominal_balance")
        _require_decimal(self.max_position_size, "max_position_size")
        if self.nominal_balance is not None and self.nominal_balance <= 0:
            raise ValueError("nominal_balance must be positive")
        if self.max_position_size is not None and self.max_position_size <= 0:
            raise ValueError("max_position_size must be positive")
        if self.minimum_trading_days is not None and self.minimum_trading_days < 0:
            raise ValueError("minimum_trading_days must not be negative")


@dataclass(frozen=True)
class PropFirmAccountSnapshot:
    """Validated account data needed for later loss-budget evaluation."""

    profile_id: str
    account_type: str
    currency: str
    balance: Decimal
    equity: Decimal
    snapshot_at: datetime
    initial_balance: Optional[Decimal] = None
    opening_balance: Optional[Decimal] = None
    opening_equity: Optional[Decimal] = None
    high_watermark_balance: Optional[Decimal] = None
    high_watermark_equity: Optional[Decimal] = None

    def __post_init__(self) -> None:
        for field_name in ("profile_id", "account_type"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if not isinstance(self.currency, str) or len(self.currency) != 3:
            raise ValueError("currency must be a three-letter code")
        if self.currency != self.currency.upper() or not self.currency.isalpha():
            raise ValueError("currency must be an uppercase alphabetic code")
        for field_name in (
            "balance",
            "equity",
            "initial_balance",
            "opening_balance",
            "opening_equity",
            "high_watermark_balance",
            "high_watermark_equity",
        ):
            _require_decimal(getattr(self, field_name), field_name)
        if self.balance < 0 or self.equity < 0:
            raise ValueError("balance and equity must not be negative")
        _require_aware_datetime(self.snapshot_at, "snapshot_at")
