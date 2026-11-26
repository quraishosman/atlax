"""Read a secret-free identity snapshot from the connected MT5 terminal."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from core.models.account_mode import MT5AccountIdentity


def _value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def read_mt5_account_identity(
    mt5: Any,
    *,
    terminal_instance_id: Optional[str] = None,
    observed_at: Optional[datetime] = None,
) -> MT5AccountIdentity:
    """Read and validate the current MT5 account and terminal metadata."""

    account_info = mt5.account_info()
    if account_info is None:
        raise RuntimeError("MT5 account_info is unavailable")
    terminal_info = mt5.terminal_info()
    login = _value(account_info, "login")
    server = str(_value(account_info, "server", "") or "").strip()
    currency = str(_value(account_info, "currency", "") or "").strip().upper()
    try:
        login = int(login)
    except (TypeError, ValueError):
        raise ValueError("MT5 account login is invalid") from None
    if not server or len(currency) != 3:
        raise ValueError("MT5 account server or currency is unavailable")

    build = _value(terminal_info, "build") if terminal_info is not None else None
    try:
        build = int(build) if build is not None else None
    except (TypeError, ValueError):
        build = None

    current_time = observed_at or datetime.now(timezone.utc)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    return MT5AccountIdentity(
        login=login,
        server=server,
        currency=currency,
        trade_mode=(str(_value(account_info, "trade_mode")) if _value(account_info, "trade_mode") is not None else None),
        terminal_name=(str(_value(terminal_info, "name")) if _value(terminal_info, "name") is not None else None),
        terminal_build=build,
        terminal_instance_id=(str(terminal_instance_id).strip() if terminal_instance_id else None),
        observed_at=current_time,
    )
