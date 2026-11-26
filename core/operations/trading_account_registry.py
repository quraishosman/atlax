"""Secret-free saved MT5 account selection for the single-terminal operator flow."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


TRADING_ACCOUNT_REGISTRY_KEY = "trading_accounts.registry"
_MAX_ACCOUNTS = 20


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


@dataclass(frozen=True)
class TradingAccountProfile:
    """A named, non-secret MT5 identity that an operator may select."""

    profile_id: str
    label: str
    login: int
    server: str
    created_at: datetime
    created_by: str

    def __post_init__(self) -> None:
        if not self.profile_id or len(self.profile_id) > 32:
            raise ValueError("profile_id must be 1-32 characters")
        if not self.label or len(self.label) > 48:
            raise ValueError("label must be 1-48 characters")
        if not isinstance(self.login, int) or self.login <= 0:
            raise ValueError("login must be a positive integer")
        if not self.server or len(self.server) > 128:
            raise ValueError("server must be 1-128 characters")
        if not self.created_by or len(self.created_by) > 80:
            raise ValueError("created_by must be 1-80 characters")
        _aware(self.created_at, "created_at")

    @property
    def identity_key(self) -> tuple[int, str]:
        return self.login, self.server.strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "label": self.label,
            "login": self.login,
            "server": self.server,
            "created_at": self.created_at.astimezone(timezone.utc).isoformat(),
            "created_by": self.created_by,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "TradingAccountProfile":
        if not isinstance(raw, dict):
            raise ValueError("trading account profile must be a mapping")
        created_at = datetime.fromisoformat(str(raw["created_at"]))
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return cls(
            profile_id=str(raw["profile_id"]),
            label=str(raw["label"]).strip(),
            login=int(raw["login"]),
            server=str(raw["server"]).strip(),
            created_at=created_at,
            created_by=str(raw["created_by"]),
        )


@dataclass(frozen=True)
class TradingAccountRegistry:
    """Restart-safe, secret-free account list and active expected identity."""

    revision: int = 0
    accounts: tuple[TradingAccountProfile, ...] = ()
    active_profile_id: str = ""
    updated_at: datetime | None = None
    updated_by: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("revision must be a non-negative integer")
        if len(self.accounts) > _MAX_ACCOUNTS:
            raise ValueError(f"at most {_MAX_ACCOUNTS} accounts may be saved")
        profile_ids = [account.profile_id for account in self.accounts]
        if len(set(profile_ids)) != len(profile_ids):
            raise ValueError("saved account profile IDs must be unique")
        identities = [account.identity_key for account in self.accounts]
        if len(set(identities)) != len(identities):
            raise ValueError("saved MT5 login/server identities must be unique")
        if self.active_profile_id and self.active_profile_id not in profile_ids:
            raise ValueError("active_profile_id must reference a saved account")
        if self.updated_at is not None:
            _aware(self.updated_at, "updated_at")
        if len(self.updated_by) > 80:
            raise ValueError("updated_by must be at most 80 characters")

    @property
    def active_account(self) -> TradingAccountProfile | None:
        return next(
            (account for account in self.accounts if account.profile_id == self.active_profile_id),
            None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "accounts": [account.to_dict() for account in self.accounts],
            "active_profile_id": self.active_profile_id,
            "updated_at": (
                self.updated_at.astimezone(timezone.utc).isoformat()
                if self.updated_at
                else None
            ),
            "updated_by": self.updated_by[:80],
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "TradingAccountRegistry":
        if not raw:
            return cls()
        if not isinstance(raw, dict):
            raise ValueError("trading account registry must be a mapping")
        updated_at = raw.get("updated_at")
        parsed_updated_at = None
        if updated_at:
            parsed_updated_at = datetime.fromisoformat(str(updated_at))
            if parsed_updated_at.tzinfo is None:
                parsed_updated_at = parsed_updated_at.replace(tzinfo=timezone.utc)
        accounts = tuple(TradingAccountProfile.from_dict(item) for item in raw.get("accounts", ()))
        return cls(
            revision=int(raw.get("revision", 0) or 0),
            accounts=accounts,
            active_profile_id=str(raw.get("active_profile_id") or ""),
            updated_at=parsed_updated_at,
            updated_by=str(raw.get("updated_by") or "")[:80],
        )


def load_trading_account_registry(runtime_store: Any) -> TradingAccountRegistry:
    """Load the registry; invalid persisted state is never silently trusted."""

    raw = runtime_store.get(TRADING_ACCOUNT_REGISTRY_KEY, {})
    return TradingAccountRegistry.from_dict(raw)


def _save(runtime_store: Any, registry: TradingAccountRegistry, *, actor: str) -> TradingAccountRegistry:
    updated = replace(
        registry,
        revision=registry.revision + 1,
        updated_at=datetime.now(timezone.utc),
        updated_by=str(actor).strip()[:80],
    )
    runtime_store.set(TRADING_ACCOUNT_REGISTRY_KEY, updated.to_dict())
    return updated


def add_trading_account(
    runtime_store: Any,
    *,
    label: str,
    login: int,
    server: str,
    actor: str,
) -> TradingAccountRegistry:
    """Save one non-secret MT5 identity without changing an existing target."""

    current = load_trading_account_registry(runtime_store)
    account = TradingAccountProfile(
        profile_id=uuid4().hex[:16],
        label=str(label).strip(),
        login=int(login),
        server=str(server).strip(),
        created_at=datetime.now(timezone.utc),
        created_by=str(actor).strip()[:80],
    )
    if any(saved.identity_key == account.identity_key for saved in current.accounts):
        raise ValueError("that MT5 login and server are already saved")
    if len(current.accounts) >= _MAX_ACCOUNTS:
        raise ValueError(f"at most {_MAX_ACCOUNTS} accounts may be saved")
    return _save(
        runtime_store,
        replace(
            current,
            accounts=(*current.accounts, account),
            active_profile_id=current.active_profile_id or account.profile_id,
        ),
        actor=actor,
    )


def select_trading_account(
    runtime_store: Any,
    *,
    profile_id: str,
    actor: str,
) -> TradingAccountRegistry:
    """Set the expected terminal identity; verification still occurs at MT5 connect."""

    current = load_trading_account_registry(runtime_store)
    selected_id = str(profile_id).strip()
    if not any(account.profile_id == selected_id for account in current.accounts):
        raise ValueError("saved account was not found")
    return _save(
        runtime_store,
        replace(current, active_profile_id=selected_id),
        actor=actor,
    )


def delete_trading_account(
    runtime_store: Any,
    *,
    profile_id: str,
    actor: str,
) -> TradingAccountRegistry:
    """Permanently remove an unselected saved MT5 identity.

    The active target is intentionally protected. Operators must select a
    different saved account before deleting the current target; this prevents
    account deletion from making the scanner fall back to legacy YAML
    connection settings unexpectedly.
    """

    current = load_trading_account_registry(runtime_store)
    selected_id = str(profile_id).strip()
    account = next(
        (saved for saved in current.accounts if saved.profile_id == selected_id),
        None,
    )
    if account is None:
        raise ValueError("saved account was not found")
    if account.profile_id == current.active_profile_id:
        raise ValueError(
            "the selected account cannot be deleted; select another account first"
        )

    return _save(
        runtime_store,
        replace(
            current,
            accounts=tuple(
                saved for saved in current.accounts
                if saved.profile_id != selected_id
            ),
        ),
        actor=actor,
    )
