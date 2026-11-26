from datetime import datetime, timedelta, timezone
from decimal import Decimal

from core.alerts.subscriber_db import SubscriberDB
from core.payments.payment_service import PaymentService
from core.payments.payment_store import PaymentStore
from core.payments.subscription_lifecycle import SubscriptionLifecycleService
from core.logging.audit_log import OperationalAuditLog
from core.settings.payment_config import (
    CryptoPaymentConfig,
    MpesaPaymentConfig,
    PaymentConfig,
)


def _payment_service(tmp_path):
    config = PaymentConfig(
        enabled=True,
        subscription_price_usd=Decimal("15"),
        subscription_duration_days=30,
        default_style="day_trader",
        manual_review_required=True,
        payment_reference_prefix="ATLAX",
        payment_ttl_hours=72,
        support_contact="@support",
        crypto=CryptoPaymentConfig(True, "wallet", "USDC-Test", "USDC"),
        mpesa=MpesaPaymentConfig(True, "0700000000", "ATLAX15", "", "39000"),
    )
    db = SubscriberDB(
        codes_file=str(tmp_path / "invite_codes.json"),
        subs_file=str(tmp_path / "subscribers.json"),
    )
    store = PaymentStore(str(tmp_path / "payment_intents.json"))
    return PaymentService(config, store=store, subscriber_db=db), db


def test_payment_instructions_use_explicit_network_and_local_mpesa_steps(tmp_path):
    service, _ = _payment_service(tmp_path)

    crypto_intent, crypto_message = service.create_payment_intent("111", "crypto_user", "crypto")
    mpesa_intent, mpesa_message = service.create_payment_intent("222", "mpesa_user", "mpesa")

    assert crypto_intent is not None
    assert f"/paid {crypto_intent['reference']} SOLANA_TRANSACTION_SIGNATURE" in crypto_message
    assert "USDC-Test" in crypto_message
    assert "SOLANA_TRANSACTION_SIGNATURE" in crypto_message
    assert "seed phrase" in crypto_message
    assert "wallet" in crypto_message.lower()

    assert mpesa_intent is not None
    assert f"/paid {mpesa_intent['reference']} MPESA_TRANSACTION_ID" in mpesa_message
    assert "*150*00#" in mpesa_message
    assert "Lipa Namba" in mpesa_message
    assert "Weka Lipa Namba" in mpesa_message
    assert "Ingiza Lipa Namba" in mpesa_message
    assert "*150*60#" in mpesa_message
    assert "Airtel Money" in mpesa_message
    assert "Halotel" in mpesa_message
    assert "39000 TZS" in mpesa_message
    assert "MPESA_TRANSACTION_ID" in mpesa_message
    assert "PIN" in mpesa_message


def test_named_payment_rails_preview_then_generate_one_hour_reference(tmp_path):
    config = PaymentConfig(
        enabled=True,
        subscription_price_usd=Decimal("15"),
        premium_subscription_price_usd=Decimal("50"),
        subscription_duration_days=30,
        default_style="day_trader",
        manual_review_required=True,
        payment_reference_prefix="ATLAX",
        payment_ttl_hours=1,
        support_contact="@support",
        crypto=CryptoPaymentConfig(True, "legacy-wallet", "USDC-Solana", "USDC"),
        mpesa=MpesaPaymentConfig(True, "0700000000", "ATLAX15", "39000", "39000"),
        usdt_trc20=CryptoPaymentConfig(True, "usdt-wallet", "TRC20", "USDT"),
        usdc_trc20=CryptoPaymentConfig(True, "usdc-wallet", "TRC20", "USDC"),
        solana=CryptoPaymentConfig(True, "sol-wallet", "Solana", "SOL"),
    )
    db = SubscriberDB(
        codes_file=str(tmp_path / "invite_codes.json"),
        subs_file=str(tmp_path / "subscribers.json"),
    )
    service = PaymentService(
        config,
        store=PaymentStore(str(tmp_path / "payment_intents.json")),
        subscriber_db=db,
    )

    assert service.enabled_methods() == ["usdt_trc20", "usdc_trc20", "solana", "mpesa"]
    preview = service.build_payment_method_preview("premium", "usdt_trc20")
    assert "TRC20" in preview
    assert "usdt-wallet" in preview
    assert "Payment Reference" not in preview
    assert service.get_latest_intent("901") is None

    intent, generated = service.create_payment_intent(
        "901", "named_rail_user", "usdt_trc20", subscription_plan="premium"
    )
    assert intent is not None
    assert intent["reference"].startswith("ATLAX-CR-")
    suffix = intent["reference"].rsplit("-", 1)[-1]
    assert len(suffix) == 6
    assert all(character in "0123456789ABCDEF" for character in suffix)
    assert "60 minutes" in generated
    assert service.get_open_intent("901", "usdt_trc20", "premium")["reference"] == intent["reference"]


def test_payment_plan_is_persisted_and_applied_on_confirmation(tmp_path):
    service, db = _payment_service(tmp_path)

    intent, _ = service.create_payment_intent(
        "555",
        "premium_user",
        "crypto",
        subscription_plan="premium",
    )
    assert intent["subscription_plan"] == "premium"
    assert intent["amount_usd"] == "50"

    confirmed, _ = service.confirm_payment(intent["reference"], confirmed_by="admin-1")

    assert confirmed["subscription_plan"] == "premium"
    assert db.get_subscription_plan("555") == "premium"
    assert db.get_subscriber("555")["subscription_plan"] == "premium"


def test_payment_submission_and_approval_are_idempotent(tmp_path):
    service, db = _payment_service(tmp_path)
    intent, _ = service.create_payment_intent("123", "tester", "crypto")

    submitted, _ = service.submit_payment(intent["reference"], tx_reference="tx-1")
    submitted_again, repeat_message = service.submit_payment(
        intent["reference"], tx_reference="tx-2"
    )
    confirmed, _ = service.confirm_payment(intent["reference"], confirmed_by="admin-1")
    confirmed_again, already_message = service.confirm_payment(
        intent["reference"], confirmed_by="admin-1"
    )

    assert submitted["status"] == "submitted"
    assert submitted_again["submitted_tx_reference"] == "tx-1"
    assert "already" in repeat_message.lower()
    assert confirmed["status"] == "confirmed"
    assert confirmed_again["status"] == "confirmed"
    assert "already active" in already_message.lower()
    assert db.is_registered("123") is True


def test_expired_payment_cannot_be_confirmed_or_activate_access(tmp_path):
    service, db = _payment_service(tmp_path)
    intent, _ = service.create_payment_intent("789", "tester", "crypto")
    store = service._store
    store._intents[intent["intent_id"]]["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    ).isoformat()
    store._save()

    expired = store.get_by_reference(intent["reference"])
    confirmed, message = service.confirm_payment(intent["reference"], confirmed_by="admin-1")

    assert expired["status"] == "expired"
    assert confirmed is None
    assert "expired" in message.lower()
    assert db.is_registered("789") is False


class _FakeTelegram:
    is_enabled = True

    def __init__(self):
        self.messages = []

    def send_reply(self, chat_id, text, message_id=None):
        self.messages.append((str(chat_id), text))
        return True


def test_subscription_lifecycle_reminders_are_idempotent_and_expire_access(tmp_path):
    service, db = _payment_service(tmp_path)
    intent, _ = service.create_payment_intent("321", "subscriber", "crypto")
    activated, _ = service.confirm_payment(intent["reference"], confirmed_by="admin")
    assert activated is not None

    now = datetime.now(timezone.utc)
    subscriber = db.get_subscriber("321")
    subscriber["subscription_expires_at"] = (now + timedelta(hours=60)).isoformat()
    subscriber["subscription_reminders_sent"] = {}
    subscriber["subscription_expiry_notice_sent"] = False
    db._save()

    telegram = _FakeTelegram()
    lifecycle = SubscriptionLifecycleService(
        service,
        db,
        OperationalAuditLog(str(tmp_path / "audit.jsonl")),
        now_fn=lambda: now,
    )

    first = lifecycle.run(telegram)
    second = lifecycle.run(telegram)

    assert first["expiry_reminders_sent"] == 1
    assert second["expiry_reminders_sent"] == 0
    assert len(telegram.messages) == 1
    assert "expires in approximately" in telegram.messages[0][1]

    subscriber = db.get_subscriber("321")
    subscriber["subscription_expires_at"] = (now - timedelta(seconds=1)).isoformat()
    db._save()

    expired = lifecycle.run(telegram)
    repeated = lifecycle.run(telegram)

    assert expired["suspended_subscriptions"] == 1
    assert expired["expiry_notices_sent"] == 1
    assert repeated["suspended_subscriptions"] == 0
    assert repeated["expiry_notices_sent"] == 0
    assert db.is_registered("321") is False
    assert len(telegram.messages) == 2


def test_revenue_summary_counts_confirmed_intents_only(tmp_path):
    service, _ = _payment_service(tmp_path)
    crypto, _ = service.create_payment_intent("401", "crypto_user", "crypto")
    mpesa, _ = service.create_payment_intent("402", "mpesa_user", "mpesa")
    pending, _ = service.create_payment_intent("403", "pending_user", "crypto")

    service.confirm_payment(crypto["reference"], confirmed_by="admin")
    service.submit_payment(mpesa["reference"], tx_reference="mpesa-1")
    service.confirm_payment(mpesa["reference"], confirmed_by="admin")

    summary = service.get_revenue_summary()

    assert summary["period_confirmed_count"] == 2
    assert summary["period_gross_usd"] == Decimal("30")
    assert summary["pending_count"] == 1
    assert summary["all_time_confirmed_count"] == 2
    assert summary["period_by_method"] == {"crypto": Decimal("15"), "mpesa": Decimal("15")}
    assert pending["reference"] not in {
        intent["reference"] for intent in service._store.list_intents(status="confirmed")
    }
