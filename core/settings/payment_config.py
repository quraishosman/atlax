"""
ATLAX Payment Configuration Schema and Loader.

External subscription payments are handled outside Telegram and then
applied to subscriber access after confirmation.

Authority: docs/13_CONFIGURATION.md and docs/16_SECURITY.md
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal

import yaml

_VALID_STYLES = {"scalper", "day_trader", "swing_trader", "all_styles"}


@dataclass(frozen=True)
class CryptoPaymentConfig:
    enabled: bool
    wallet_address: str
    network: str
    asset_label: str


@dataclass(frozen=True)
class MpesaPaymentConfig:
    enabled: bool
    number: str
    account_reference: str
    amount_hint: str
    amount_tzs: str = ""

    @property
    def lipa_number(self) -> str:
        """Return the merchant Lipa kwa M-Pesa number using the legacy field name."""
        return self.number


@dataclass(frozen=True)
class PaymentConfig:
    enabled: bool
    subscription_price_usd: Decimal
    subscription_duration_days: int
    default_style: str
    manual_review_required: bool
    payment_reference_prefix: str
    payment_ttl_hours: int
    support_contact: str
    crypto: CryptoPaymentConfig
    mpesa: MpesaPaymentConfig
    expiry_reminder_hours: tuple[int, ...] = (72, 24)
    premium_subscription_price_usd: Decimal = Decimal("50")
    # Optional named rails keep the legacy ``crypto`` and ``mpesa`` fields
    # compatible with existing deployments and tests.
    usdt_trc20: CryptoPaymentConfig | None = None
    usdc_trc20: CryptoPaymentConfig | None = None
    usdt_ton: CryptoPaymentConfig | None = None
    solana: CryptoPaymentConfig | None = None
    card_enabled: bool = False
    card_checkout_url: str = ""
    automatic_verification_enabled: bool = False
    automatic_verification_shadow_mode: bool = True
    verification_timeout_seconds: int = 15
    verification_clock_skew_seconds: int = 300
    ton_verification_api_url: str = "https://toncenter.com/api/v3/jettons/transfers"
    ton_verification_api_key: str = ""
    ton_usdt_jetton_master: str = ""
    ton_usdt_decimals: int = 6
    solana_verification_rpc_url: str = ""
    solana_token_mint: str = ""
    solana_token_decimals: int = 6

    def __post_init__(self) -> None:
        if self.subscription_price_usd <= 0:
            raise ValueError("subscription_price_usd must be greater than zero")
        if self.premium_subscription_price_usd <= 0:
            raise ValueError("premium_subscription_price_usd must be greater than zero")
        if self.subscription_duration_days <= 0:
            raise ValueError("subscription_duration_days must be greater than zero")
        if self.default_style not in _VALID_STYLES:
            raise ValueError(
                f"default_style must be one of {sorted(_VALID_STYLES)}, got {self.default_style!r}"
            )
        if self.payment_ttl_hours <= 0:
            raise ValueError("payment_ttl_hours must be greater than zero")
        if self.verification_timeout_seconds <= 0:
            raise ValueError("verification_timeout_seconds must be greater than zero")
        if self.verification_clock_skew_seconds < 0:
            raise ValueError("verification_clock_skew_seconds must not be negative")
        if self.ton_usdt_decimals < 0 or self.solana_token_decimals < 0:
            raise ValueError("token decimals must not be negative")
        if not self.payment_reference_prefix.strip():
            raise ValueError("payment_reference_prefix must not be empty")
        try:
            reminder_hours = tuple(int(value) for value in self.expiry_reminder_hours)
        except (TypeError, ValueError) as exc:
            raise ValueError("expiry_reminder_hours must contain integers") from exc
        if any(value <= 0 for value in reminder_hours):
            raise ValueError("expiry_reminder_hours must contain only positive integers")
        if len(set(reminder_hours)) != len(reminder_hours):
            raise ValueError("expiry_reminder_hours must not contain duplicates")
        object.__setattr__(
            self,
            "expiry_reminder_hours",
            tuple(sorted(reminder_hours, reverse=True)),
        )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    if raw.lower() in {"1", "true", "yes", "on"}:
        return True
    if raw.lower() in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _env_decimal(name: str, default: Decimal | int | float | str) -> Decimal:
    raw = os.environ.get(name, "").strip()
    value = raw if raw else default
    try:
        return Decimal(str(value))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"{name} must be a decimal number") from exc


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name, "").strip()
    return raw if raw else str(default).strip()


def _parse_int_list(name: str, source: object, default: tuple[int, ...]) -> tuple[int, ...]:
    """Parse a comma-separated environment value or a YAML list."""
    env_value = os.environ.get(name, "").strip()
    if env_value:
        items = [item.strip() for item in env_value.split(",")]
    elif source is None:
        items = list(default)
    elif isinstance(source, str):
        items = [item.strip() for item in source.split(",")]
    elif isinstance(source, (list, tuple)):
        items = list(source)
    else:
        raise ValueError(f"{name} must be a comma-separated list or YAML list")

    if not items:
        return ()

    values: list[int] = []
    for item in items:
        try:
            value = int(str(item).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must contain only integers") from exc
        if value <= 0:
            raise ValueError(f"{name} must contain only positive integers")
        if value in values:
            raise ValueError(f"{name} must not contain duplicates")
        values.append(value)
    return tuple(sorted(values, reverse=True))


def load_payment_config(config_path: str) -> PaymentConfig:
    """
    Load and validate payment configuration from the YAML config file.

    Secrets are not required for manual wallet or M-Pesa instructions.
    Provider secrets, if added later, must remain environment-only.
    """
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    payments_raw = raw.get("payments", {}) or {}

    try:
        enabled = _env_bool(
            "ATLAX_PAYMENTS_ENABLED",
            bool(payments_raw.get("enabled", False)),
        )
        price_usd = _env_decimal(
            "ATLAX_PAYMENT_PRICE_USD",
            payments_raw.get("subscription_price_usd", 15),
        )
        premium_price_usd = _env_decimal(
            "ATLAX_PREMIUM_PAYMENT_PRICE_USD",
            payments_raw.get("premium_subscription_price_usd", 50),
        )
        duration_days = _env_int(
            "ATLAX_PAYMENT_DURATION_DAYS",
            int(payments_raw.get("subscription_duration_days", 30)),
        )
        default_style = _env_str(
            "ATLAX_PAYMENT_DEFAULT_STYLE",
            payments_raw.get("default_style", "day_trader"),
        )
        manual_review_required = _env_bool(
            "ATLAX_PAYMENT_MANUAL_REVIEW_REQUIRED",
            bool(payments_raw.get("manual_review_required", True)),
        )
        payment_reference_prefix = _env_str(
            "ATLAX_PAYMENT_REFERENCE_PREFIX",
            payments_raw.get("payment_reference_prefix", "ATLAX"),
        )
        payment_ttl_hours = _env_int(
            "ATLAX_PAYMENT_TTL_HOURS",
            int(payments_raw.get("payment_ttl_hours", 1)),
        )
        expiry_reminder_hours = _parse_int_list(
            "ATLAX_SUBSCRIPTION_EXPIRY_REMINDER_HOURS",
            payments_raw.get("expiry_reminder_hours", (72, 24)),
            (72, 24),
        )
        support_contact = _env_str(
            "ATLAX_PAYMENT_SUPPORT_CONTACT",
            payments_raw.get("support_contact", "@ATLAXSupport"),
        )

        crypto_raw = payments_raw.get("crypto", {}) or {}
        crypto = CryptoPaymentConfig(
            enabled=_env_bool(
                "ATLAX_CRYPTO_ENABLED",
                bool(crypto_raw.get("enabled", True)),
            ),
            wallet_address=_env_str(
                "ATLAX_CRYPTO_WALLET_ADDRESS",
                "",
            ),
            network=_env_str(
                "ATLAX_CRYPTO_NETWORK",
                crypto_raw.get("network", "USDC-Solana"),
            ),
            asset_label=_env_str(
                "ATLAX_CRYPTO_ASSET_LABEL",
                crypto_raw.get("asset_label", "USDC"),
            ),
        )

        mpesa_raw = payments_raw.get("mpesa", {}) or {}
        mpesa = MpesaPaymentConfig(
            enabled=_env_bool(
                "ATLAX_MPESA_ENABLED",
                bool(mpesa_raw.get("enabled", True)),
            ),
            number=_env_str(
                "ATLAX_MPESA_LIPA_NUMBER",
                os.environ.get(
                    "ATLAX_MPESA_NUMBER",
                    "",
                ),
            ),
            account_reference=_env_str(
                "ATLAX_MPESA_ACCOUNT_REFERENCE",
                mpesa_raw.get("account_reference", "ATLAX15"),
            ),
            amount_hint=_env_str(
                "ATLAX_MPESA_AMOUNT_HINT",
                mpesa_raw.get("amount_hint", ""),
            ),
            amount_tzs=_env_str(
                "ATLAX_MPESA_AMOUNT_TZS",
                mpesa_raw.get("amount_tzs", ""),
            ),
        )

        rails_raw = payments_raw.get("rails", {}) or {}

        def crypto_rail(
            key: str,
            env_prefix: str,
            default_enabled: bool = False,
            wallet_fallback: str = "",
        ) -> CryptoPaymentConfig:
            rail_raw = rails_raw.get(key, {}) or {}
            return CryptoPaymentConfig(
                enabled=_env_bool(
                    f"{env_prefix}_ENABLED",
                    bool(rail_raw.get("enabled", default_enabled)),
                ),
                # Wallet destinations stay environment-only. The fallback is
                # the legacy environment variable for the Solana migration.
                wallet_address=_env_str(
                    f"{env_prefix}_WALLET_ADDRESS",
                    wallet_fallback,
                ),
                network=_env_str(
                    f"{env_prefix}_NETWORK",
                    rail_raw.get("network", ""),
                ),
                asset_label=_env_str(
                    f"{env_prefix}_ASSET_LABEL",
                    rail_raw.get("asset_label", ""),
                ),
            )

        legacy_wallet = os.environ.get("ATLAX_CRYPTO_WALLET_ADDRESS", "").strip()
        solana = crypto_rail(
            "solana",
            "ATLAX_SOLANA",
            default_enabled=bool(crypto_raw.get("enabled", True)),
            wallet_fallback=legacy_wallet,
        )
        usdt_trc20 = crypto_rail("usdt_trc20", "ATLAX_USDT_TRC20")
        usdc_trc20 = crypto_rail("usdc_trc20", "ATLAX_USDC_TRC20")
        usdt_ton = crypto_rail("usdt_ton", "ATLAX_USDT_TON")
        card_raw = rails_raw.get("card", {}) or {}
        card_enabled = _env_bool(
            "ATLAX_CARD_ENABLED",
            bool(card_raw.get("enabled", False)),
        )
        card_checkout_url = _env_str(
            "ATLAX_CARD_CHECKOUT_URL",
            card_raw.get("checkout_url", ""),
        )

        verification_raw = payments_raw.get("automatic_verification", {}) or {}
        automatic_verification_enabled = _env_bool(
            "ATLAX_AUTOMATIC_VERIFICATION_ENABLED",
            bool(verification_raw.get("enabled", False)),
        )
        automatic_verification_shadow_mode = _env_bool(
            "ATLAX_AUTOMATIC_VERIFICATION_SHADOW_MODE",
            bool(verification_raw.get("shadow_mode", True)),
        )
        verification_timeout_seconds = _env_int(
            "ATLAX_PAYMENT_VERIFICATION_TIMEOUT_SECONDS",
            int(verification_raw.get("timeout_seconds", 15)),
        )
        verification_clock_skew_seconds = _env_int(
            "ATLAX_PAYMENT_VERIFICATION_CLOCK_SKEW_SECONDS",
            int(verification_raw.get("clock_skew_seconds", 300)),
        )
        ton_verification_api_url = _env_str(
            "ATLAX_TON_VERIFICATION_API_URL",
            verification_raw.get(
                "ton_api_url",
                "https://toncenter.com/api/v3/jettons/transfers",
            ),
        )
        # Provider credentials and allowlists remain environment-only.
        ton_verification_api_key = os.environ.get(
            "ATLAX_TON_VERIFICATION_API_KEY", ""
        ).strip()
        ton_usdt_jetton_master = os.environ.get(
            "ATLAX_TON_USDT_JETTON_MASTER", ""
        ).strip()
        ton_usdt_decimals = _env_int(
            "ATLAX_TON_USDT_DECIMALS",
            int(verification_raw.get("ton_usdt_decimals", 6)),
        )
        solana_verification_rpc_url = os.environ.get(
            "ATLAX_SOLANA_VERIFICATION_RPC_URL", ""
        ).strip()
        solana_token_mint = os.environ.get("ATLAX_SOLANA_TOKEN_MINT", "").strip()
        solana_token_decimals = _env_int(
            "ATLAX_SOLANA_TOKEN_DECIMALS",
            int(verification_raw.get("solana_token_decimals", 6)),
        )

        return PaymentConfig(
            enabled=enabled,
            subscription_price_usd=price_usd,
            subscription_duration_days=duration_days,
            default_style=default_style,
            manual_review_required=manual_review_required,
            payment_reference_prefix=payment_reference_prefix,
            payment_ttl_hours=payment_ttl_hours,
            support_contact=support_contact,
            crypto=crypto,
            mpesa=mpesa,
            expiry_reminder_hours=expiry_reminder_hours,
            premium_subscription_price_usd=premium_price_usd,
            usdt_trc20=usdt_trc20,
            usdc_trc20=usdc_trc20,
            usdt_ton=usdt_ton,
            solana=solana,
            card_enabled=card_enabled,
            card_checkout_url=card_checkout_url,
            automatic_verification_enabled=automatic_verification_enabled,
            automatic_verification_shadow_mode=automatic_verification_shadow_mode,
            verification_timeout_seconds=verification_timeout_seconds,
            verification_clock_skew_seconds=verification_clock_skew_seconds,
            ton_verification_api_url=ton_verification_api_url,
            ton_verification_api_key=ton_verification_api_key,
            ton_usdt_jetton_master=ton_usdt_jetton_master,
            ton_usdt_decimals=ton_usdt_decimals,
            solana_verification_rpc_url=solana_verification_rpc_url,
            solana_token_mint=solana_token_mint,
            solana_token_decimals=solana_token_decimals,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Payment configuration validation failed: {exc}") from exc
