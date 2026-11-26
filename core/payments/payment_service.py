"""
ATLAX payment orchestration.

This service creates payment intents, formats instructions for Telegram,
and applies confirmed payments to the subscriber database.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Optional

from core.alerts.subscriber_db import STYLE_LABELS, STYLE_TIMEFRAMES, SubscriberDB
from core.analytics.google_sheets_reporting import report_payment_event
from core.payments.payment_store import PaymentStore
from core.settings.payment_config import PaymentConfig
from core.storage.factory import get_payment_store, get_subscriber_db

logger = logging.getLogger("atlax.payments.service")


def format_user_datetime(value: object, fallback: str = "UNKNOWN") -> str:
    """Render stored timestamps for Telegram without ISO seconds or offsets."""
    if value in (None, ""):
        return fallback
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value).strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return raw or fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def payment_method_label(method: object) -> str:
    """Return a customer-facing payment rail label."""
    labels = {
        "crypto": "USDC (Solana)",
        "solana": "USDC (Solana)",
        "usdt_ton": "USDT (TON)",
        "usdt_trc20": "USDT (TRC20)",
        "usdc_trc20": "USDC (TRC20)",
        "mpesa": "M-Pesa Lipa Namba",
        "card": "Card Payment",
        "invite_code": "Invite code",
        "paid": "Paid subscription",
    }
    normalized = str(method or "").strip().lower()
    return labels.get(normalized, str(method or "UNKNOWN").replace("_", " ").title())


def subscription_plan_label(plan: object) -> str:
    """Return a customer-facing subscription plan label."""
    labels = {
        "standard": "Standard Plan",
        "premium": "Premium Plan",
    }
    normalized = str(plan or "standard").strip().lower()
    return labels.get(normalized, "Standard Plan")


class PaymentService:
    def __init__(
        self,
        config: PaymentConfig,
        store: Optional[PaymentStore] = None,
        subscriber_db: Optional[SubscriberDB] = None,
    ) -> None:
        self._config = config
        self._store = store or get_payment_store()
        self._subscriber_db = subscriber_db or get_subscriber_db()

    def attach_subscriber_db(self, subscriber_db: SubscriberDB) -> None:
        self._subscriber_db = subscriber_db

    def _report_payment(self, intent: Optional[dict], event_type: str) -> None:
        if not intent:
            return
        try:
            report_payment_event(intent, event_type)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                '{"event":"google_sheets_payment_report_failed",'
                '"reference":"%s","error":"%s"}',
                str(intent.get("reference", ""))[:80],
                str(exc)[:160],
            )

    def get_latest_intent(self, chat_id: str) -> Optional[dict]:
        """Return the latest payment request for a Telegram user."""
        return self._store.get_latest_for_chat(str(chat_id))

    def get_intent_by_reference(self, reference: str) -> Optional[dict]:
        """Return a payment intent by its customer-facing reference."""
        return self._store.get_by_reference(str(reference or "").strip().upper())

    def get_open_intent(
        self,
        chat_id: str,
        method: str,
        subscription_plan: str,
    ) -> Optional[dict]:
        """Reuse an unclosed reference instead of creating duplicate live requests."""
        method = str(method or "").strip().lower()
        plan = str(subscription_plan or "standard").strip().lower()
        for intent in self._store.list_intents(chat_id=str(chat_id)):
            if (
                intent.get("method") == method
                and intent.get("subscription_plan") == plan
                and intent.get("status") in {"awaiting_payment", "submitted"}
            ):
                return intent
        return None

    def is_enabled(self) -> bool:
        return bool(self._config.enabled)

    @staticmethod
    def payment_method_button_label(method: str) -> str:
        labels = {
            "usdt_trc20": "💎 USDT (TRC20)",
            "usdt_ton": "💎 USDT (TON)",
            "usdc_trc20": "💎 USDC (TRC20)",
            "solana": "🪙 USDC (Solana)",
            "crypto": "🪙 Pay with Solana",
            "mpesa": "📱 Pay with M-Pesa",
            "card": "💳 Card Payment",
        }
        return labels.get(method.strip().lower(), payment_method_label(method))

    def _named_crypto_rail(self, method: str) -> Optional[object]:
        method = method.strip().lower()
        if method == "usdt_trc20":
            return self._config.usdt_trc20
        if method == "usdt_ton":
            return self._config.usdt_ton
        if method == "usdc_trc20":
            return self._config.usdc_trc20
        if method == "solana":
            return self._config.solana or self._config.crypto
        return None

    def _available_destination(self, method: str) -> Optional[dict[str, str]]:
        method = method.strip().lower()
        if method in {"crypto", "solana", "usdt_ton", "usdt_trc20", "usdc_trc20"}:
            rail = self._config.crypto if method == "crypto" else self._named_crypto_rail(method)
            if rail is None or not rail.enabled or not rail.wallet_address.strip():
                return None
            return {
                "wallet_address": rail.wallet_address.strip(),
                "network": rail.network.strip(),
                "asset_label": rail.asset_label.strip(),
            }
        if method == "mpesa" and self._config.mpesa.enabled and self._config.mpesa.number.strip():
            amount_tzs = self._config.mpesa.amount_tzs.strip()
            amount_hint = self._config.mpesa.amount_hint.strip()
            return {
                "lipa_number": self._config.mpesa.number.strip(),
                "account_reference": self._config.mpesa.account_reference.strip(),
                "amount_tzs": amount_tzs,
                "amount_hint": amount_hint,
            }
        if method == "card" and self._config.card_enabled and self._config.card_checkout_url.strip():
            return {
                "checkout_url": self._config.card_checkout_url.strip(),
                "provider": "configured card checkout",
            }
        return None

    def enabled_methods(self) -> list[str]:
        """Return payment rails enabled by the operator, even if details are pending."""
        methods: list[str] = []
        if any(
            rail is not None
            for rail in (
                self._config.usdt_ton,
                self._config.usdt_trc20,
                self._config.usdc_trc20,
                self._config.solana,
            )
        ):
            if self._config.usdt_ton and self._config.usdt_ton.enabled:
                methods.append("usdt_ton")
            if self._config.usdt_trc20 and self._config.usdt_trc20.enabled:
                methods.append("usdt_trc20")
            if self._config.usdc_trc20 and self._config.usdc_trc20.enabled:
                methods.append("usdc_trc20")
            if self._config.solana and self._config.solana.enabled:
                methods.append("solana")
        elif self._config.crypto.enabled:
            methods.append("crypto")
        if self._config.mpesa.enabled:
            methods.append("mpesa")
        if self._config.card_enabled:
            methods.append("card")
        return methods

    def available_methods(self) -> list[str]:
        methods: list[str] = []
        for method in self.enabled_methods():
            if self._available_destination(method):
                methods.append(method)
        return methods

    def _resolve_style(self, chat_id: str) -> str:
        sub = self._subscriber_db.get_subscriber(chat_id)
        if sub and sub.get("style") in STYLE_TIMEFRAMES:
            return str(sub.get("style"))
        return self._config.default_style

    def price_for_plan(self, subscription_plan: object) -> Decimal:
        """Return the configured price for a validated subscription plan."""
        plan = str(subscription_plan or "standard").strip().lower()
        if plan == "premium":
            return self._config.premium_subscription_price_usd
        return self._config.subscription_price_usd

    def _human_ts(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def create_payment_intent(
        self,
        chat_id: str,
        username: str,
        method: str,
        subscription_plan: str = "standard",
    ) -> tuple[Optional[dict[str, str]], str]:
        if not self.is_enabled():
            return None, "Payments are not enabled yet."

        method = method.strip().lower()
        if method not in {"crypto", "solana", "usdt_ton", "usdt_trc20", "usdc_trc20", "mpesa", "card"}:
            return None, "Unsupported payment method."

        subscription_plan = str(subscription_plan or "standard").strip().lower()
        if subscription_plan not in {"standard", "premium"}:
            return None, "Unsupported subscription plan."

        destination = self._available_destination(method)
        if destination is None:
            return None, f"{payment_method_label(method)} payments are not configured yet."

        style = self._resolve_style(chat_id)
        intent = self._store.create_intent(
            chat_id=chat_id,
            username=username,
            method=method,
            style=style,
            amount_usd=str(self.price_for_plan(subscription_plan)),
            payment_destination=destination,
            reference_prefix=self._config.payment_reference_prefix,
            support_contact=self._config.support_contact,
            payment_ttl_hours=self._config.payment_ttl_hours,
            note="ATLAX subscription purchase",
            subscription_plan=subscription_plan,
        )
        self._report_payment(intent, "payment_created")
        return intent, self.build_payment_instructions(intent)

    def _build_legacy_subscription_pitch(self, chat_id: str) -> str:
        methods = self.enabled_methods()
        standard_price = self._config.subscription_price_usd
        premium_price = self._config.premium_subscription_price_usd
        days = self._config.subscription_duration_days
        lines = [
            "👋 <b>Welcome to ATLAX AI Signals</b>",
            "",
            f"Standard: <b>${standard_price} USD</b> · Premium: <b>${premium_price} USD</b> for <b>{days} days</b>",
            "",
        ]
        if methods:
            lines.append("Choose a payment method below or use /buy crypto or /buy mpesa.")
        else:
            lines.append("Payment methods are not configured yet. Please contact the admin.")
        lines.append("")
        lines.append("After payment is confirmed, access is activated on Telegram automatically.")
        return "\n".join(lines)

    def build_subscription_pitch(
        self,
        chat_id: str,
        subscription_plan: Optional[str] = None,
    ) -> str:
        methods = self.enabled_methods()
        configured_methods = set(self.available_methods())
        standard_price = escape(str(self._config.subscription_price_usd))
        premium_price = escape(str(self._config.premium_subscription_price_usd))
        days = escape(str(self._config.subscription_duration_days))
        lines = [
            "💳 <b>ATLAX Signal Subscription</b>",
            "",
            "Choose the plan that matches how you want to follow the market. Access lasts for the configured subscription period and is activated only after payment review.",
            "",
            f"🌱 <b>Standard Plan — ${standard_price} / {days} days</b>",
            "• Telegram signal delivery\n• Scalper, Day Trader, and Swing routing\n• Pair selection and Signal Terminal\n• Signal Passport, history, and daily/weekly reports\n• Market context, heatmap, and correlation\n• ATLAX AI: up to 10 questions per day\n• Confidence filter",
            "",
            f"⭐ <b>Premium Plan — ${premium_price} / {days} days</b>",
            "Everything in Standard, plus:\n• A/A+ selective signal route\n• Premium signal label and confluence filter\n• Advanced market analysis\n• High-Quality Only and Key-Level Only filters\n• Priority routing, premium support, and early feature access",
        ]

        if subscription_plan in {"standard", "premium"}:
            lines.extend([
                "",
                f"✅ <b>Selected:</b> {subscription_plan_label(subscription_plan)}",
                "Choose a payment method below. Payment instructions appear before ATLAX creates your reference.",
            ])

        if self._subscriber_db.is_registered(chat_id):
            sub = self._subscriber_db.get_subscriber(chat_id) or {}
            expiry = escape(format_user_datetime(sub.get("subscription_expires_at"), "No expiry set"))
            lines.extend([
                "",
                f"⏳ <b>Current access expires:</b> <code>{expiry}</code>",
                "Renewing while access is active extends your current expiry instead of replacing it.",
            ])

        lines.extend([
            "",
            (
                "Choose a payment method below. ATLAX will show the destination and payment steps first."
                if subscription_plan in {"standard", "premium"}
                else "Choose a plan below. ATLAX will show the available payment methods after your selection."
            ),
            "",
        ])
        if methods:
            for method in methods:
                if method not in configured_methods:
                    lines.append(f"⚠️ <b>{escape(self.payment_method_button_label(method))}:</b> payment details are being configured. Selecting it will explain what is missing.")
                else:
                    lines.append(f"✅ {escape(self.payment_method_button_label(method))} is available.")
        else:
            lines.append("⚠️ Checkout destinations are temporarily unavailable. Please contact support so the payment setup can be completed safely.")

        if self._config.manual_review_required:
            lines.extend([
                "",
                "🛡️ <b>Verification:</b> after payment, submit the transaction ID using the payment reference shown by ATLAX. An administrator verifies the payment before signal access is enabled.",
            ])
        return "\n".join(lines)

    def build_selected_plan_checkout(self, chat_id: str, subscription_plan: str) -> str:
        """Render the compact payment-method screen for an already selected plan."""
        plan = str(subscription_plan or "standard").strip().lower()
        amount = escape(str(self.price_for_plan(plan)))
        expiry = "No active access"
        if self._subscriber_db.is_registered(chat_id):
            sub = self._subscriber_db.get_subscriber(chat_id) or {}
            expiry = format_user_datetime(sub.get("subscription_expires_at"), "No expiry set")
        return (
            f"✅ <b>You selected: {escape(subscription_plan_label(plan))} — ${amount} USD</b>\n\n"
            f"⏳ <b>Current access expires:</b> {escape(expiry)}\n"
            f"♻️ Renewal will extend your access by {self._config.subscription_duration_days} days.\n\n"
            "Choose payment method:"
        )

    def build_payment_method_preview(self, subscription_plan: str, method: str) -> str:
        """Render payment steps without creating a reference or payment intent."""
        plan = str(subscription_plan or "standard").strip().lower()
        method = str(method or "").strip().lower()
        if plan not in {"standard", "premium"}:
            return "⚠️ Please choose a valid subscription plan first."
        destination = self._available_destination(method)
        if destination is None:
            return (
                f"⚠️ <b>{escape(self.payment_method_button_label(method))}</b> is not configured yet.\n\n"
                f"Please contact <code>{escape(self._config.support_contact)}</code> or choose another payment method."
            )

        amount = escape(str(self.price_for_plan(plan)))
        lines = [
            f"💳 <b>Payment Instructions — {escape(payment_method_label(method))}</b>",
            "",
            f"⭐ <b>Plan:</b> {escape(subscription_plan_label(plan))}",
            f"💵 <b>Amount:</b> ${amount} USD",
            "",
        ]
        if method in {"crypto", "solana", "usdt_ton", "usdt_trc20", "usdc_trc20"}:
            lines.extend([
                f"🌐 <b>Network:</b> {escape(str(destination.get('network', '')))}",
                f"🪙 <b>Asset:</b> {escape(str(destination.get('asset_label', '')))}",
                "",
                "Send the exact asset on the exact network to:",
                f"<code>{escape(str(destination.get('wallet_address', '')))}</code>",
                "",
                "⚠️ Sending a different asset or using another network may permanently lose the funds. ATLAX will never request a seed phrase or private key.",
                "",
                "After payment, generate your ATLAX reference and submit the matching transaction signature.",
            ])
        elif method == "mpesa":
            amount_tzs = destination.get("amount_tzs", "") or destination.get("amount_hint", "")
            amount_instruction = (
                f"Enter <b>{escape(str(amount_tzs))} TZS</b>."
                if amount_tzs
                else f"Enter the current TZS equivalent of <b>${amount} USD</b>; confirm today's amount with ATLAX before paying."
            )
            lines.extend([
                "📱 <b>M-Pesa Lipa Namba</b>",
                "🇹🇿 <b>Vodacom M-Pesa</b>",
                "1️⃣ Dial <code>*150*00#</code>.",
                "2️⃣ Select <b>4 — Lipa kwa M-Pesa</b>.",
                "3️⃣ Select <b>1 — Weka Lipa Namba</b>.",
                f"4️⃣ Enter Lipa Namba: <code>{escape(str(destination.get('lipa_number', '')))}</code>",
                f"5️⃣ {amount_instruction} Confirm the merchant details and keep the transaction ID.",
                "",
                "📲 <b>Airtel Money</b>",
                "1️⃣ Dial <code>*150*60#</code>.",
                "2️⃣ Select <b>5 — Lipa bili</b>, then <b>1 — Lipa kwa simu mitandao yote</b>.",
                "3️⃣ Select <b>1 — Lipa kwa Vodacom M-Pesa</b>, then enter the Lipa Namba above.",
                f"4️⃣ {amount_instruction} Confirm and keep the transaction ID.",
                "",
                "🟠 <b>Halotel</b>",
                "Use the Vodacom M-Pesa Lipa Namba above, confirm the merchant details, and keep the transaction SMS.",
                "",
                "After payment, generate your ATLAX reference and submit the M-Pesa transaction ID.",
            ])
        elif method == "card":
            lines.extend([
                "💳 <b>Card Payment</b>",
                "Continue through the configured card checkout page. Do not send card numbers, PINs, or one-time codes to the Telegram bot.",
                f"🔗 <a href=\"{escape(str(destination.get('checkout_url', '')), quote=True)}\">Open secure card checkout</a>",
                "",
                "After the card provider confirms payment, keep the provider receipt and submit the payment reference if ATLAX requests proof.",
            ])
        return "\n".join(lines)

    def build_payment_reference_message(self, intent: dict[str, str]) -> str:
        """Render the compact reference screen after the user requests a reference."""
        reference = escape(str(intent.get("reference", "")))
        minutes = self._config.payment_ttl_hours * 60
        return (
            "🔑 <b>Payment Reference Generated</b>\n\n"
            f"Reference: <code>{reference}</code>\n\n"
            "Use this reference when submitting your payment proof.\n\n"
            f"⏰ <b>Reference expires in:</b> {minutes} minutes"
        )

    def build_payment_instructions(self, intent: dict[str, str]) -> str:
        method = intent.get("method", "")
        reference = intent.get("reference", "")
        plan = str(intent.get("subscription_plan", "standard")).strip().lower()
        amount = intent.get("amount_usd", str(self.price_for_plan(plan)))
        style = intent.get("style", self._config.default_style)
        style_label = STYLE_LABELS.get(style, style)
        support_contact = intent.get("support_contact", self._config.support_contact)

        lines = [
            "🔑 <b>Payment Reference Generated</b>",
            "",
            "Your unique ATLAX payment reference is ready. Complete the payment using the instructions below, then submit the matching proof so the ATLAX team can verify and activate your access.",
            "",
            f"🔖 <b>Payment reference:</b> <code>{reference}</code>",
            f"💵 <b>Amount:</b> ${amount} USD",
            f"⭐ <b>Plan:</b> {escape(subscription_plan_label(plan))}",
            f"⏰ <b>Reference expires in:</b> {self._config.payment_ttl_hours * 60} minutes",
            f"🎯 <b>Routing profile after activation:</b> {escape(str(style_label))}",
            "",
        ]

        if method in {"crypto", "solana", "usdt_ton", "usdt_trc20", "usdc_trc20"}:
            destination = intent["payment_destination"]
            asset_label = escape(str(destination.get("asset_label", "USDC")))
            network = escape(str(destination.get("network", "Solana")))
            rail_label = escape(payment_method_label(method))
            proof_label = "Solana transaction signature" if method in {"crypto", "solana"} else "network transaction ID/hash"
            proof_example = "SOLANA_TRANSACTION_SIGNATURE" if method in {"crypto", "solana"} else "TRANSACTION_ID_OR_HASH"
            lines.extend(
                [
                    f"🪙 <b>Pay with {rail_label}</b>",
                    f"1️⃣ Open your crypto wallet and choose <b>{asset_label}</b>.",
                    f"2️⃣ Select the <b>{network}</b> network. Sending on another network can permanently lose the funds.",
                    f"3️⃣ Send exactly <b>${escape(str(amount))}</b> worth of {asset_label} to:",
                    f"<code>{escape(str(destination['wallet_address']))}</code>",
                    "",
                    "⚠️ Confirm the asset, network, and destination address carefully before sending. ATLAX will never ask for your wallet seed phrase or private key.",
                    "",
                    f"📨 After the transfer is confirmed, submit the {proof_label}:",
                    f"<code>/paid {reference} {proof_example}</code>",
                    "",
                    "The payment reference is used by ATLAX to match your request. It is not a substitute for the transaction proof.",
                ]
            )
        elif method == "mpesa":
            destination = intent["payment_destination"]
            amount_tzs = destination.get("amount_tzs", "") or destination.get("amount_hint", "")
            amount_instruction = (
                f"Enter the amount: <b>{escape(str(amount_tzs))} TZS</b>."
                if amount_tzs
                else f"Enter the current TZS equivalent of <b>${escape(str(amount))} USD</b>; confirm today's amount with ATLAX before paying."
            )
            lines.extend(
                [
                    "📱 <b>Pay with M-Pesa Lipa Namba</b>",
                    "Use the mobile-money menu that matches your network. The ATLAX merchant number and amount are shown below:",
                    "",
                    "🇹🇿 <b>Vodacom M-Pesa</b>",
                    "1️⃣ Dial <code>*150*00#</code>.",
                    "2️⃣ Select <b>4 — Lipa kwa M-Pesa</b>.",
                    "3️⃣ Select <b>1 — Weka Lipa Namba</b>.",
                    "4️⃣ Ingiza Lipa Namba ya ATLAX:",
                    f"<code>{escape(str(destination['lipa_number']))}</code>",
                    f"5️⃣ {amount_instruction}",
                    "6️⃣ Enter your M-Pesa PIN, confirm the merchant details, and keep the confirmation SMS.",
                    "",
                    "📲 <b>Airtel Money</b>",
                    "1️⃣ Dial <code>*150*60#</code>.",
                    "2️⃣ Select <b>5 — Lipa bili</b>.",
                    "3️⃣ Select <b>1 — Lipa kwa simu mitandao yote</b>.",
                    "4️⃣ Select <b>1 — Lipa kwa Vodacom M-Pesa</b>.",
                    "5️⃣ Select <b>2</b> for the merchant/Lipa-number entry, then ingiza the ATLAX Lipa Namba.",
                    f"6️⃣ {amount_instruction} Confirm and keep the transaction ID.",
                    "",
                    "🟠 <b>Halotel</b>",
                    "Use the Halotel mobile-money menu to pay through the Vodacom M-Pesa Lipa Namba shown above. Confirm the merchant name and keep the transaction SMS.",
                    "",
                    f"🧾 ATLAX reference: <code>{escape(str(reference))}</code>",
                    "",
                    "📨 After payment, submit the M-Pesa transaction ID:",
                    f"<code>/paid {reference} MPESA_TRANSACTION_ID</code>",
                    "",
                    "Never share your M-Pesa PIN with ATLAX, support, or anyone else. ATLAX only needs the transaction ID for verification.",
                ]
            )
        elif method == "card":
            destination = intent["payment_destination"]
            lines.extend(
                [
                    "💳 <b>Pay by Card</b>",
                    "Complete payment only through the configured secure checkout page:",
                    f"🔗 <a href=\"{escape(str(destination.get('checkout_url', '')), quote=True)}\">Open card checkout</a>",
                    "",
                    "Never send card details, PINs, or one-time codes in Telegram. Keep the provider receipt for review.",
                ]
            )
        else:
            lines.append("⚠️ Unknown payment method. Please return to the subscription menu and choose an available option.")

        lines.extend(
            [
                "",
                f"🆘 Need help? Contact <code>{escape(str(support_contact))}</code> and include your payment reference, never your PIN or private wallet credentials.",
                f"⏰ Payment request expires at: <code>{escape(format_user_datetime(intent.get('expires_at'), 'UNKNOWN'))}</code>",
            ]
        )
        return "\n".join(lines)

    def _build_legacy_payment_status_message(self, chat_id: str) -> str:
        sub = self._subscriber_db.get_subscriber(chat_id)
        active = self._subscriber_db.is_registered(chat_id)
        latest = self._store.get_latest_for_chat(chat_id)

        if active and sub:
            style = str(sub.get("style", self._config.default_style))
            style_label = STYLE_LABELS.get(style, style)
            timeframes = ", ".join(sub.get("timeframes", [])) or "n/a"
            expiry = sub.get("subscription_expires_at") or "No expiry set"
            method = sub.get("payment_method") or sub.get("subscription_source") or "invite code"
            reference = sub.get("payment_reference") or sub.get("invite_code") or "n/a"
            return (
                "✅ <b>Your ATLAX access is active</b>\n\n"
                f"Style: <b>{style_label}</b>\n"
                f"Timeframes: <b>{timeframes}</b>\n"
                f"Access expires: <code>{expiry}</code>\n"
                f"Payment method: <b>{payment_method_label(method)}</b>\n"
                f"Plan: <b>{subscription_plan_label(sub.get('subscription_plan', 'standard'))}</b>\n"
                f"Reference: <code>{reference}</code>\n\n"
                "Use /style to change routing, /mystyle to review settings, and /renew to extend access."
            )

        if latest:
            reference = latest.get("reference", "n/a")
            status = latest.get("status", "unknown")
            created_at = latest.get("created_at", "unknown")
            expires_at = latest.get("expires_at", "unknown")
            method = latest.get("method", "unknown")
            return (
                "⏳ <b>Your latest payment is not yet active</b>\n\n"
                f"Method: <b>{method}</b>\n"
                f"Reference: <code>{reference}</code>\n"
                f"Status: <b>{status}</b>\n"
                f"Created: <code>{created_at}</code>\n"
                f"Expires: <code>{expires_at}</code>\n\n"
                "If you already paid, send /paid with the reference and transaction id."
            )

        return (
            "⚠️ <b>No active subscription found</b>\n\n"
            "Use /subscribe to get started."
        )

    def _payment_timeline(self, intent: dict) -> str:
        history = intent.get("status_history") or []
        statuses = [
            str(event.get("status", "unknown"))
            for event in history
            if isinstance(event, dict)
        ]
        if not statuses:
            statuses = [str(intent.get("status", "unknown"))]
        return " -> ".join(escape(status.replace("_", " ")) for status in statuses)

    def build_payment_submitted_message(self, intent: dict[str, str]) -> str:
        """Render the customer confirmation after /paid is accepted."""
        plan = str(intent.get("subscription_plan", "standard")).strip().lower()
        amount = escape(str(intent.get("amount_usd", self.price_for_plan(plan))))
        transaction = escape(str(intent.get("submitted_tx_reference", "")))
        reference = escape(str(intent.get("reference", "")))
        submitted_at = format_user_datetime(
            intent.get("submitted_at") or intent.get("updated_at"),
            self._human_ts(),
        )
        return (
            "✅ <b>Payment Submitted</b>\n\n"
            f"Transaction ID: <code>{transaction}</code>\n"
            f"Reference: <code>{reference}</code>\n"
            f"Plan: <b>{escape(subscription_plan_label(plan))} — ${amount}</b>\n"
            f"Time: <code>{escape(submitted_at)}</code>\n\n"
            "Your payment is being verified by an administrator.\n\n"
            "⏱️ Typical verification time: 5–30 minutes\n\n"
            "You will receive a notification when access is activated."
        )

    def build_payment_status_message(self, chat_id: str) -> str:
        sub = self._subscriber_db.get_subscriber(chat_id)
        active = self._subscriber_db.is_registered(chat_id)
        latest = self._store.get_latest_for_chat(chat_id)

        if active and sub:
            style = STYLE_LABELS.get(str(sub.get("style", self._config.default_style)), str(sub.get("style", "")))
            timeframes = ", ".join(sub.get("timeframes", [])) or "n/a"
            expiry = format_user_datetime(sub.get("subscription_expires_at"), "No expiry set")
            method = sub.get("payment_method") or sub.get("subscription_source") or "invite_code"
            reference = sub.get("payment_reference") or sub.get("invite_code") or "n/a"
            lines = [
                "✅ <b>Your ATLAX access is active</b>",
                "",
                f"🎯 <b>Style:</b> {escape(str(style))}",
                f"⭐ <b>Plan:</b> {escape(subscription_plan_label(sub.get('subscription_plan', 'standard')))}",
                f"⏱️ <b>Timeframes:</b> {escape(str(timeframes))}",
                f"⏳ <b>Access expires:</b> <code>{escape(expiry)}</code>",
                f"💳 <b>Payment method:</b> {escape(payment_method_label(method))}",
                f"🔖 <b>Reference:</b> <code>{escape(str(reference))}</code>",
                "",
                "Your profile is ready to receive routed ATLAX alerts. Renewal extends from the current expiry while access remains active.",
            ]
            if latest:
                lines.extend([
                    "",
                    f"🧾 <b>Latest payment:</b> {escape(str(latest.get('status', 'unknown')).replace('_', ' '))}",
                    f"📍 <b>Payment timeline:</b> <code>{self._payment_timeline(latest)}</code>",
                ])
            return "\n".join(lines)

        if latest:
            reference = latest.get("reference", "n/a")
            status = str(latest.get("status", "unknown")).replace("_", " ")
            return (
                "⏳ <b>Your latest payment is awaiting activation</b>\n\n"
                "ATLAX has recorded your payment request, but signal access remains protected until the submitted proof is reviewed.\n\n"
                f"💳 <b>Payment method:</b> {escape(payment_method_label(latest.get('method', 'unknown')))}\n"
                f"⭐ <b>Plan:</b> {escape(subscription_plan_label(latest.get('subscription_plan', 'standard')))}\n"
                f"🔖 <b>Reference:</b> <code>{escape(str(reference))}</code>\n"
                f"📌 <b>Status:</b> {escape(status)}\n"
                f"🕒 <b>Created:</b> <code>{escape(format_user_datetime(latest.get('created_at')))}</code>\n"
                f"⏰ <b>Expires:</b> <code>{escape(format_user_datetime(latest.get('expires_at')))}</code>\n"
                f"📍 <b>Timeline:</b> <code>{self._payment_timeline(latest)}</code>\n\n"
                "If you have already paid, submit <code>/paid REF TRANSACTION_ID</code>."
            )

        return "⚠️ <b>No active subscription found</b>\n\nUse /subscribe to choose a payment method and begin your ATLAX access request."

    def build_payment_history_message(self, chat_id: str) -> str:
        intents = self._store.list_intents(chat_id=chat_id)[:5]
        if not intents:
            return "🧾 <b>Payment History</b>\n\nNo payment requests have been created yet. Your completed and pending payment requests will appear here."

        lines = ["🧾 <b>Payment History</b>", ""]
        for intent in intents:
            status = str(intent.get("status", "unknown")).replace("_", " ")
            lines.extend([
                f"<b>{escape(str(intent.get('reference', 'n/a')))}</b> | {escape(payment_method_label(intent.get('method', 'unknown')))} | <b>{escape(status)}</b>",
                f"Created: <code>{escape(format_user_datetime(intent.get('created_at')))}</code>",
                f"Timeline: <code>{self._payment_timeline(intent)}</code>",
                "",
            ])
        return "\n".join(lines).rstrip()

    def build_admin_review_history_message(self, limit: int = 20) -> str:
        intents = self._store.list_intents()[:max(1, min(int(limit), 50))]
        if not intents:
            return "🧾 <b>Payment Review History</b>\n\nNo payment requests have been recorded."

        lines = [f"🧾 <b>Payment Review History ({len(intents)})</b>", ""]
        for intent in intents:
            status = str(intent.get("status", "unknown")).replace("_", " ")
            lines.append(
                f"<code>{escape(str(intent.get('reference', 'n/a')))}</code> | "
                f"@{escape(str(intent.get('username', 'unknown')))} | "
                f"{escape(payment_method_label(intent.get('method', 'unknown')))} | "
                f"<b>{escape(status)}</b>"
            )
            lines.append(f"Timeline: <code>{self._payment_timeline(intent)}</code>")
        return "\n".join(lines)

    def build_review_message(self, intent: dict[str, str]) -> str:
        style = str(intent.get("style", self._config.default_style))
        style_label = STYLE_LABELS.get(style, style)
        return (
            "🧾 <b>Payment review needed</b>\n\n"
            f"User: <b>@{intent.get('username', 'unknown')}</b>\n"
            f"Chat ID: <code>{intent.get('chat_id', 'n/a')}</code>\n"
            f"Payment method: <b>{payment_method_label(intent.get('method', 'unknown'))}</b>\n"
            f"Plan: <b>{subscription_plan_label(intent.get('subscription_plan', 'standard'))}</b>\n"
            f"Reference: <code>{intent.get('reference', 'n/a')}</code>\n"
            f"Submitted tx: <code>{intent.get('submitted_tx_reference', 'n/a') or 'n/a'}</code>\n"
            f"Amount: <b>${intent.get('amount_usd', '0')}</b>\n"
            f"Style: <b>{style_label}</b>\n"
            f"Status: <b>{intent.get('status', 'unknown')}</b>\n"
            f"Expires: <code>{format_user_datetime(intent.get('expires_at'))}</code>\n"
        )

    def build_activation_message(self, intent: dict[str, str]) -> str:
        sub = self._subscriber_db.get_subscriber(intent.get("chat_id", ""))
        expiry = "No expiry set"
        style_label = STYLE_LABELS.get(intent.get("style", self._config.default_style), intent.get("style", ""))
        timeframes = "n/a"
        if sub:
            expiry = format_user_datetime(sub.get("subscription_expires_at"), expiry)
            timeframes = ", ".join(sub.get("timeframes", [])) or timeframes
        return (
            "✅ <b>Payment approved</b>\n\n"
            f"Your access is active for <b>{timeframes}</b>.\n"
            f"Plan: <b>{subscription_plan_label(intent.get('subscription_plan', 'standard'))}</b>\n"
            f"Style: <b>{style_label}</b>\n"
            f"Expires: <code>{escape(expiry)}</code>\n"
            f"Reference: <code>{intent.get('reference', 'n/a')}</code>\n\n"
            "Choose your trading style below, or use /style anytime to change routing."
        )

    def build_rejection_message(self, intent: dict[str, str], reason: str) -> str:
        reason_text = reason.strip() or "No reason supplied"
        return (
            "❌ <b>Payment rejected</b>\n\n"
            f"Reference: <code>{intent.get('reference', 'n/a')}</code>\n"
            f"Reason: <code>{reason_text}</code>\n\n"
            "If this looks wrong, contact support with the reference above."
        )

    def _legacy_submit_payment(self, reference: str, tx_reference: str = "", note: str = "") -> tuple[Optional[dict[str, str]], str]:
        intent = self._store.mark_submitted(reference, tx_reference=tx_reference, note=note)
        if intent is None:
            return None, "Payment reference not found."
        if intent.get("status") == "confirmed":
            return intent, "Payment was already approved."
        if intent.get("status") == "expired":
            return None, "That payment reference has expired. Please create a new one with /subscribe."
        self._report_payment(intent, "payment_submitted")
        return intent, "Payment proof submitted."

    def _legacy_confirm_payment(self, reference: str, confirmed_by: str, note: str = "") -> tuple[Optional[dict[str, str]], str]:
        intent = self._store.confirm(reference, confirmed_by=confirmed_by, note=note)
        if intent is None:
            return None, "Payment reference not found or already closed."

        style = str(intent.get("style", self._config.default_style))
        if style not in STYLE_TIMEFRAMES:
            style = self._config.default_style

        activated = self._subscriber_db.activate_subscription(
            chat_id=str(intent.get("chat_id", "")),
            username=str(intent.get("username", "")),
            style=style,
            subscription_days=self._config.subscription_duration_days,
            payment_method=str(intent.get("method", "")),
            payment_reference=str(intent.get("reference", "")),
            subscription_source="paid",
            subscription_plan=str(intent.get("subscription_plan", "standard")),
        )
        if not activated:
            return None, "Payment was confirmed, but subscriber activation failed."
        self._report_payment(intent, "payment_confirmed")
        return intent, "Payment confirmed and subscriber activated."

    def _legacy_reject_payment(self, reference: str, rejected_by: str, reason: str = "") -> tuple[Optional[dict[str, str]], str]:
        intent = self._store.reject(reference, rejected_by=rejected_by, reason=reason)
        if intent is None:
            return None, "Payment reference not found or already confirmed."
        self._report_payment(intent, "payment_rejected")
        return intent, "Payment rejected."

    def submit_payment(self, reference: str, tx_reference: str = "", note: str = "") -> tuple[Optional[dict[str, str]], str]:
        tx_reference = str(tx_reference or "").strip()
        if not tx_reference:
            return None, "A transaction ID or hash is required. Use /paid REF TRANSACTION_ID."
        existing = self._store.get_by_reference(reference)
        if existing is None:
            return None, "Payment reference not found."
        status = str(existing.get("status", ""))
        if status == "submitted":
            return existing, "Payment proof was already submitted for this reference."
        if status == "confirmed":
            return existing, "Payment was already approved."
        if status == "expired":
            return None, "That payment reference has expired. Please create a new one with /subscribe."
        if status == "rejected":
            return None, "That payment reference was rejected. Please create a new one with /subscribe."
        duplicate = self._store.get_by_submitted_tx_reference(tx_reference)
        if duplicate and str(duplicate.get("reference", "")).strip().upper() != str(reference).strip().upper():
            return None, "That transaction proof has already been used. Check the reference and try again."
        intent = self._store.mark_submitted(reference, tx_reference=tx_reference, note=note)
        if intent is None:
            return None, "Payment reference is not awaiting payment."
        self._report_payment(intent, "payment_submitted")
        return intent, "Payment proof submitted."

    def confirm_payment(self, reference: str, confirmed_by: str, note: str = "") -> tuple[Optional[dict[str, str]], str]:
        existing = self._store.get_by_reference(reference)
        if existing is None:
            return None, "Payment reference not found."
        status = str(existing.get("status", ""))
        if status in {"rejected", "expired"}:
            return None, f"Payment is already {status}."
        intent = existing if status == "confirmed" else self._store.confirm(
            reference,
            confirmed_by=confirmed_by,
            note=note,
        )
        if intent is None:
            return None, "Payment reference is not awaiting review."

        style = str(intent.get("style", self._config.default_style))
        if style not in STYLE_TIMEFRAMES:
            style = self._config.default_style

        current_subscriber = self._subscriber_db.get_subscriber(str(intent.get("chat_id", "")))
        if (
            status == "confirmed"
            and current_subscriber
            and current_subscriber.get("active")
            and current_subscriber.get("payment_reference") == intent.get("reference")
        ):
            return intent, "Payment was already approved and access is already active."

        activated = self._subscriber_db.activate_subscription(
            chat_id=str(intent.get("chat_id", "")),
            username=str(intent.get("username", "")),
            style=style,
            subscription_days=self._config.subscription_duration_days,
            payment_method=str(intent.get("method", "")),
            payment_reference=str(intent.get("reference", "")),
            subscription_source="paid",
            subscription_plan=str(intent.get("subscription_plan", "standard")),
        )
        if not activated:
            return None, "Payment was confirmed, but subscriber activation failed."
        self._report_payment(intent, "payment_confirmed")
        return intent, "Payment confirmed and subscriber activated."

    def reject_payment(self, reference: str, rejected_by: str, reason: str = "") -> tuple[Optional[dict[str, str]], str]:
        existing = self._store.get_by_reference(reference)
        if existing is None:
            return None, "Payment reference not found."
        status = str(existing.get("status", ""))
        if status == "rejected":
            return existing, "Payment was already rejected."
        if status in {"confirmed", "expired"}:
            return None, f"Payment is already {status}."
        intent = self._store.reject(reference, rejected_by=rejected_by, reason=reason)
        if intent is None:
            return None, "Payment reference is not awaiting review."
        self._report_payment(intent, "payment_rejected")
        return intent, "Payment rejected."

    def void_payment(
        self,
        reference: str,
        voided_by: str,
        reason: str = "",
    ) -> tuple[Optional[dict[str, str]], str]:
        """Invalidate a confirmed payment and remove access tied to it."""
        existing = self._store.get_by_reference(reference)
        if existing is None:
            return None, "Payment reference not found."
        if existing.get("status") == "voided":
            return existing, "Payment was already voided."
        if existing.get("status") != "confirmed":
            return None, "Only a confirmed payment can be voided."

        intent = self._store.void_confirmed(
            reference,
            voided_by=voided_by,
            reason=reason,
        )
        if intent is None:
            return None, "Payment could not be voided."

        chat_id = str(intent.get("chat_id", ""))
        subscriber = self._subscriber_db.get_subscriber(chat_id)
        if subscriber and subscriber.get("payment_reference") == intent.get("reference"):
            self._subscriber_db.deactivate(chat_id)
        self._report_payment(intent, "payment_voided")
        return intent, "Payment voided and linked access deactivated."

    def pending_count(self) -> int:
        return self._store.count_pending()

    def expire_stale_intents(self) -> list[dict]:
        """Expire open payment requests and return only newly expired records."""
        expired = self._store.expire_stale_intents()
        for intent in expired:
            self._report_payment(intent, "payment_expired")
        return expired

    def get_revenue_summary(self, days: int = 30) -> dict:
        """Return sanitized payment counts and confirmed revenue totals."""
        period_days = max(1, min(int(days), 3650))
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=period_days)
        intents = self._store.list_intents()

        status_counts: dict[str, int] = {}
        for intent in intents:
            status = str(intent.get("status", "unknown"))
            status_counts[status] = status_counts.get(status, 0) + 1

        def parse_timestamp(value) -> Optional[datetime]:
            if not value:
                return None
            try:
                parsed = datetime.fromisoformat(str(value))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc)
            except (TypeError, ValueError):
                return None

        def amount(intent: dict) -> Decimal:
            try:
                return Decimal(str(intent.get("amount_usd", "0")))
            except (InvalidOperation, ValueError, TypeError):
                return Decimal("0")

        confirmed = [
            intent
            for intent in intents
            if str(intent.get("status", "")) == "confirmed"
        ]
        recent_confirmed = []
        for intent in confirmed:
            confirmed_at = parse_timestamp(intent.get("confirmed_at"))
            if confirmed_at is not None and confirmed_at >= cutoff:
                recent_confirmed.append(intent)
        method_totals: dict[str, Decimal] = {}
        for intent in recent_confirmed:
            method = str(intent.get("method", "unknown")).lower()
            method_totals[method] = method_totals.get(method, Decimal("0")) + amount(intent)

        return {
            "period_days": period_days,
            "status_counts": status_counts,
            "pending_count": sum(
                status_counts.get(status, 0)
                for status in ("awaiting_payment", "submitted")
            ),
            "period_confirmed_count": len(recent_confirmed),
            "period_gross_usd": sum(
                (amount(intent) for intent in recent_confirmed),
                Decimal("0"),
            ),
            "period_by_method": method_totals,
            "all_time_confirmed_count": len(confirmed),
            "all_time_gross_usd": sum(
                (amount(intent) for intent in confirmed),
                Decimal("0"),
            ),
        }

    def build_admin_revenue_summary_message(self, days: int = 30) -> str:
        summary = self.get_revenue_summary(days=days)
        status_counts = summary["status_counts"]
        method_totals = summary["period_by_method"]
        lines = [
            "\U0001F4CA <b>ATLAX Revenue Summary</b>",
            "",
            f"\U0001F4C6 Window: <b>last {summary['period_days']} days</b>",
            f"\U0001F4B0 Confirmed revenue: <b>${summary['period_gross_usd']:.2f} USD</b>",
            f"\u2705 Confirmed payments: <b>{summary['period_confirmed_count']}</b>",
            f"\u23F3 Open payment requests: <b>{summary['pending_count']}</b>",
            "",
            "<b>Current payment ledger</b>",
            f"Awaiting payment: {status_counts.get('awaiting_payment', 0)}",
            f"Proof submitted: {status_counts.get('submitted', 0)}",
            f"Confirmed: {status_counts.get('confirmed', 0)}",
            f"Rejected: {status_counts.get('rejected', 0)}",
            f"Expired: {status_counts.get('expired', 0)}",
            "",
            f"\U0001F4C8 All-time confirmed: <b>{summary['all_time_confirmed_count']}</b> "
            f"(${summary['all_time_gross_usd']:.2f} USD)",
        ]
        if method_totals:
            lines.extend(["", "<b>Confirmed by rail in window</b>"])
            for method, total in sorted(method_totals.items()):
                lines.append(f"{escape(method.title())}: ${total:.2f} USD")
        return "\n".join(lines)
