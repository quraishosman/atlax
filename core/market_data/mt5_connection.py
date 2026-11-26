"""
ATLAX MT5 Connection Manager.

Manages the lifecycle of the MetaTrader5 Python library connection.
Fail-closed: refuses to proceed if the connection cannot be established.

Authority: docs/11_EXECUTION_ENGINE.md (MT5 platform only)
           docs/04_SYSTEM_DESIGN.md (Market Data Layer responsibility)

Official MT5 Python API references:
    https://www.mql5.com/en/docs/python_metatrader5/mt5initialize_py
    https://www.mql5.com/en/docs/python_metatrader5/mt5shutdown_py
    https://www.mql5.com/en/docs/python_metatrader5/mt5terminalinfo_py

Architectural Boundaries:
    - Market Data Layer ONLY. Never detects patterns.
    - Never executes trades.
    - Connection is shared across the scanner session.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from core.settings.market_data_config import MT5ConnectionConfig

logger = logging.getLogger("atlax.market_data.connection")


class MT5ConnectionError(Exception):
    """Raised when MT5 cannot be initialised or connected."""


class MT5Connection:
    """
    Manages the MetaTrader5 Python library initialisation and shutdown.

    Uses a lazy-import pattern so the rest of ATLAX can be imported
    and tested without MT5 installed. Only the scanner path requires it.

    Fail-closed: initialize() raises MT5ConnectionError if MT5 is
    unavailable or the account cannot be verified.

    Args:
        config: MT5 connection configuration.
    """

    def __init__(self, config: MT5ConnectionConfig) -> None:
        self._config = config
        self._mt5 = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def initialize(self) -> None:
        """
        Initialise the MT5 terminal connection.

        Retries up to config.max_retries times on failure.
        Raises MT5ConnectionError if all attempts fail.

        Authority: docs/11_EXECUTION_ENGINE.md
            "Refuse execution if required configuration is missing or invalid."
        """
        try:
            import MetaTrader5 as mt5
        except ImportError as exc:
            raise MT5ConnectionError(
                "MetaTrader5 Python package is not installed. "
                "Install it with: pip install MetaTrader5"
            ) from exc

        self._mt5 = mt5

        cfg = self._config
        last_error: Optional[str] = None

        for attempt in range(1, cfg.max_retries + 1):
            logger.info(
                '{"event":"mt5_init_attempt","attempt":%d,"max":%d}',
                attempt, cfg.max_retries,
            )

            # Prefer the terminal session that the operator has already
            # authenticated. Passing a stale password to initialize() can
            # force a valid terminal session into an authorization failure.
            if self._attach_to_terminal_session(mt5, cfg):
                return

            # An empty password means terminal-session mode only. Do not
            # turn a failed session attach into an implicit login attempt.
            if not cfg.password:
                last_error = self._last_error_text(mt5)
                if attempt < cfg.max_retries:
                    time.sleep(2.0 * attempt)
                continue

            # Build init kwargs — only pass non-empty values
            init_kwargs: dict = {"timeout": cfg.timeout_ms}
            if cfg.account > 0:
                init_kwargs["login"] = cfg.account
            if cfg.server:
                init_kwargs["server"] = cfg.server
            if cfg.password:
                init_kwargs["password"] = cfg.password  # never logged

            success = mt5.initialize(**init_kwargs)

            if success:
                terminal_info = mt5.terminal_info()
                account_info = mt5.account_info()

                if terminal_info is None:
                    last_error = "MT5 terminal_info returned None after init."
                    mt5.shutdown()
                elif account_info is None:
                    last_error = "MT5 account_info returned None — not logged in."
                    mt5.shutdown()
                else:
                    self._connected = True
                    logger.info(
                        '{"event":"mt5_connected","account":%d,"server":"%s",'
                        '"terminal":"%s","build":%d}',
                        account_info.login,
                        account_info.server,
                        terminal_info.name,
                        terminal_info.build,
                    )
                    return
            else:
                error = mt5.last_error()
                last_error = f"MT5 init failed: code={error[0]}, msg={error[1]}"
                logger.warning(
                    '{"event":"mt5_init_failed","attempt":%d,"error":"%s"}',
                    attempt, last_error,
                )

            if attempt < cfg.max_retries:
                time.sleep(2.0 * attempt)

        raise MT5ConnectionError(
            f"MT5 connection failed after {cfg.max_retries} attempts. "
            f"Last error: {last_error}"
        )

    @staticmethod
    def _last_error_text(mt5: object) -> str:
        error = mt5.last_error()
        return f"MT5 init failed: code={error[0]}, msg={error[1]}"

    @staticmethod
    def _session_matches_config(account_info: object, cfg: MT5ConnectionConfig) -> bool:
        if account_info is None:
            return False
        if cfg.account > 0 and getattr(account_info, "login", None) != cfg.account:
            return False
        if cfg.server and getattr(account_info, "server", None) != cfg.server:
            return False
        return True

    def _attach_to_terminal_session(self, mt5: object, cfg: MT5ConnectionConfig) -> bool:
        """Attach without credentials when a matching MT5 session is active."""
        success = mt5.initialize(timeout=cfg.timeout_ms)
        if not success:
            logger.debug(
                '{"event":"mt5_session_attach_failed","error":"%s"}',
                self._last_error_text(mt5),
            )
            return False

        terminal_info = mt5.terminal_info()
        account_info = mt5.account_info()
        if terminal_info is None or account_info is None:
            mt5.shutdown()
            return False

        if not self._session_matches_config(account_info, cfg):
            logger.warning(
                '{"event":"mt5_session_identity_mismatch","configured_account":%d,'
                '"configured_server":"%s","connected_account":%s,'
                '"connected_server":"%s"}',
                cfg.account,
                cfg.server,
                getattr(account_info, "login", None),
                getattr(account_info, "server", None),
            )
            mt5.shutdown()
            return False

        self._connected = True
        logger.info(
            '{"event":"mt5_connected","auth_mode":"terminal_session",'
            '"account":%d,"server":"%s","terminal":"%s","build":%d}',
            account_info.login,
            account_info.server,
            terminal_info.name,
            terminal_info.build,
        )
        return True

    def shutdown(self) -> None:
        """
        Cleanly shut down the MT5 connection.
        Safe to call even if not connected.
        """
        if self._mt5 is not None and self._connected:
            self._mt5.shutdown()
            self._connected = False
            logger.info('{"event":"mt5_shutdown"}')

    def __enter__(self) -> "MT5Connection":
        self.initialize()
        return self

    def __exit__(self, *_: object) -> None:
        self.shutdown()
