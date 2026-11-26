from types import SimpleNamespace

from core.market_data.scanner import MarketScanner
from core.settings.atr_quality_config import load_atr_quality_config


def _fake_scanner(calls):
    next_prompt_id = 1000

    def send_inline_menu(*args):
        calls.append(("send", args))

    def edit_inline_menu(*args):
        calls.append(("edit", args))

    def send_reply(*args):
        calls.append(("reply", args))

    def send_one_time_prompt(*args, **kwargs):
        nonlocal next_prompt_id
        next_prompt_id += 1
        calls.append(("prompt", args, kwargs))
        return next_prompt_id

    def delete_message(*args):
        calls.append(("delete", args))
        return True

    telegram = SimpleNamespace(
        name="telegram",
        _config=SimpleNamespace(chat_id="999"),
        send_inline_menu=send_inline_menu,
        send_reply=send_reply,
        send_one_time_prompt=send_one_time_prompt,
        delete_message=delete_message,
        mini_app_button=lambda: {"text": "Open ATLAX", "web_app": {"url": "https://signals.example/miniapp"}},
        edit_inline_menu=edit_inline_menu,
    )
    scanner = object.__new__(MarketScanner)
    scanner._admin_audit = SimpleNamespace(record=lambda *args, **kwargs: None)
    scanner._payment_cfg = None
    scanner._alert_engine = SimpleNamespace(
        _channels=[telegram],
        _journal=SimpleNamespace(
            get_entries=lambda: [
                SimpleNamespace(
                    record_id="alert-1",
                    timeframe="H1",
                    profile="swing_trader",
                    confidence_score=82.5,
                    entry_price="1.15",
                    tp_price="1.20",
                    sl_price="1.00",
                )
            ]
        ),
        tracker=SimpleNamespace(
            predictions={
                "alert-1": {
                    "chat_id": "123",
                    "symbol": "EURUSD",
                    "direction": "BUY",
                    "target": 1.2,
                    "midpoint": 1.1,
                    "sl": 1.0,
                    "timestamp": 0,
                    "subscriber_deliveries": [],
                }
            }
        ),
    )
    scanner._strategy_cfg = SimpleNamespace(
        active_profiles=[SimpleNamespace(min_confidence_threshold=70)]
    )
    return scanner, telegram


def _active_db():
    return SimpleNamespace(
        get_subscriber=lambda chat_id: {
            "username": "tester",
            "style": "day_trader",
            "timeframes": ["M15", "H1"],
            "subscription_source": "paid",
            "subscription_expires_at": "2030-01-01T00:00:00+00:00",
        },
        is_registered=lambda chat_id: True,
        get_settings=lambda chat_id: {
            "symbols": [],
            "min_confidence": 70,
            "notifications": "all",
        },
    )


def test_active_user_home_profile_and_signal_terminal_render():
    calls = []
    scanner, telegram = _fake_scanner(calls)
    db = _active_db()

    scanner._show_main_menu("123", telegram, db, "tester")
    home_text, home_keyboard = calls[-1][1][1:]
    assert "Welcome back" in home_text
    assert "Signal Terminal" in home_text
    assert any(
        button.get("callback_data") == "nav:profile"
        for row in home_keyboard
        for button in row
    )
    assert any(
        button.get("web_app", {}).get("url") == "https://signals.example/miniapp"
        for row in home_keyboard
        for button in row
    )

    scanner._show_user_profile("123", telegram, db)
    assert "My ATLAX Profile" in calls[-1][1][1]

    scanner._show_signal_terminal("123", telegram, db)
    signal_text = calls[-1][1][1]
    assert "Signal Terminal" in signal_text
    assert "EURUSD" in signal_text


def test_unregistered_user_receives_onboarding_menu():
    calls = []
    scanner, telegram = _fake_scanner(calls)
    db = SimpleNamespace(
        get_subscriber=lambda chat_id: None,
        is_registered=lambda chat_id: False,
        get_settings=lambda chat_id: {},
    )

    scanner._show_main_menu("123", telegram, db, "new_user")
    welcome_text, welcome_keyboard = calls[-1][1][1:]
    assert "Welcome to ATLAX" in welcome_text
    assert "deterministic, multi-timeframe trading operating system" in welcome_text
    assert "Signal Passport and history" in welcome_text
    assert "does not control a subscriber's broker account" in welcome_text
    button_texts = [
        button.get("text", "")
        for row in welcome_keyboard
        for button in row
    ]
    assert "💳 Subscribe" in button_texts
    assert not any("Start Subscription" in text for text in button_texts)
    assert not any(text.startswith("🚀") for text in button_texts)
    assert any(
        button.get("callback_data") == "menu:subscribe"
        for row in welcome_keyboard
        for button in row
    )
    assert any(
        button.get("web_app", {}).get("url") == "https://signals.example/miniapp"
        for row in welcome_keyboard
        for button in row
    )


def test_language_selector_localizes_new_user_welcome_and_persists_choice():
    calls = []
    scanner, telegram = _fake_scanner(calls)
    selected = {"value": "en"}
    db = SimpleNamespace(
        get_subscriber=lambda chat_id: None,
        is_registered=lambda chat_id: False,
        get_settings=lambda chat_id: {"language": selected["value"]},
        get_language=lambda chat_id: selected["value"],
        set_language=lambda chat_id, language: selected.__setitem__("value", language) or True,
    )

    scanner._show_main_menu("123", telegram, db, "new_user")
    english_text, english_keyboard = calls[-1][1][1:]
    assert "Welcome to ATLAX" in english_text
    assert any(
        button.get("callback_data") == "nav:language"
        for row in english_keyboard
        for button in row
    )

    scanner._handle_menu_callback("nav:language", "123", 77, telegram, "new_user", db)
    language_text, language_keyboard = calls[-1][1][2:4]
    assert "Choose the language" in language_text
    assert any(
        button.get("callback_data") == "set:language:sw:main"
        for row in language_keyboard
        for button in row
    )

    scanner._handle_menu_callback("set:language:sw:main", "123", 77, telegram, "new_user", db)
    assert selected["value"] == "sw"
    swahili_text, _ = calls[-1][1][1:]
    assert "Karibu ATLAX" in swahili_text


def test_language_preference_survives_subscriber_db_reload(tmp_path):
    from core.alerts.subscriber_db import SubscriberDB

    codes_path = tmp_path / "codes.json"
    subscribers_path = tmp_path / "subscribers.json"
    db = SubscriberDB(str(codes_path), str(subscribers_path))
    db.set_language("123", "sw")
    assert db.get_language("123") == "sw"

    assert db.register("123", "tester", "day_trader", invite_code="")
    assert db.get_settings("123")["language"] == "sw"

    reloaded = SubscriberDB(str(codes_path), str(subscribers_path))
    assert reloaded.get_language("123") == "sw"


def test_public_faq_and_support_navigation_are_available():
    calls = []
    scanner, telegram = _fake_scanner(calls)
    scanner._analytics_cfg = SimpleNamespace(faq_path="docs/20_PUBLIC_FAQ.md")

    scanner._show_faq_menu("123", telegram)
    faq_text, faq_keyboard = calls[-1][1][1:]
    assert "Frequently Asked Questions" in faq_text
    assert "WHAT IS ATLAX?" in faq_text
    assert any(
        button.get("callback_data") == "menu:main"
        for row in faq_keyboard
        for button in row
    )

    scanner._show_help_menu("123", telegram)
    help_text, help_keyboard = calls[-1][1][1:]
    assert "@ocrooxx" in help_text
    assert any(
        button.get("callback_data") == "menu:main"
        for row in help_keyboard
        for button in row
    )
    assert any(
        button.get("text") == "🆘 Support"
        and button.get("url") == "https://t.me/ocrooxx"
        for row in help_keyboard
        for button in row
    )


def test_public_faq_shows_ten_numbered_questions_per_page():
    calls = []
    scanner, telegram = _fake_scanner(calls)
    scanner._analytics_cfg = SimpleNamespace(faq_path="docs/20_PUBLIC_FAQ.md")

    scanner._show_faq_menu("123", telegram)
    first_text, first_keyboard = calls[-1][1][1:]
    assert "1️⃣ <b>WHAT IS ATLAX?</b>" in first_text
    assert "🔟 <b>WHAT DOES HIGH-QUALITY ONLY MEAN?</b>" in first_text
    assert "WHY HAVE I NOT RECEIVED A SIGNAL?" not in first_text
    assert "Version:" not in first_text
    assert any(
        button.get("callback_data") == "nav:faq:page:1"
        for row in first_keyboard
        for button in row
    )

    scanner._show_faq_menu("123", telegram, message_id=77, page=1)
    second_text, second_keyboard = calls[-1][1][2:]
    assert "1️⃣ <b>WHY HAVE I NOT RECEIVED A SIGNAL?</b>" in second_text
    assert "1️⃣ <b>WHAT IS ATLAX?</b>" not in second_text
    assert any(
        button.get("callback_data") == "nav:faq:page:0"
        for row in second_keyboard
        for button in row
    )


def test_public_faq_resolves_when_scanner_starts_outside_project_root(monkeypatch, tmp_path):
    from core.analytics.faq_knowledge import load_public_faq

    monkeypatch.chdir(tmp_path)
    content = load_public_faq("docs/20_PUBLIC_FAQ.md")

    assert "## WHAT IS ATLAX?" in content


def test_signal_passport_history_and_weekly_navigation_are_available(tmp_path):
    from decimal import Decimal

    from core.analytics.journal import SQLiteJournal

    calls = []
    scanner, telegram = _fake_scanner(calls)
    scanner._analytics_cfg = SimpleNamespace(db_path=str(tmp_path / "journal.sqlite"))
    journal = SQLiteJournal(scanner._analytics_cfg.db_path)
    journal.add_signal_entry(
        "passport-1",
        "candidate-1",
        "EURUSD",
        "H1",
        "day_trader",
        "BUY",
        82.0,
        {},
        Decimal("1.1"),
        Decimal("1.0"),
        Decimal("1.2"),
    )
    db = _active_db()

    scanner._show_signal_passport_history("123", telegram, db)
    history_text, history_keyboard = calls[-1][1][1:]
    assert "Signal Passport" in history_text
    assert "Created:" in history_text
    assert "passport-1" not in history_text
    assert any(
        button.get("callback_data") == "nav:weekly"
        for row in history_keyboard
        for button in row
    )

    scanner._show_signal_passport_weekly("123", telegram, db)
    weekly_text, weekly_keyboard = calls[-1][1][1:]
    assert "Weekly Signal Report" in weekly_text
    assert any(
        button.get("callback_data") == "nav:history"
        for row in weekly_keyboard
        for button in row
    )


def test_signal_filters_and_detail_use_stored_metadata():
    calls = []
    scanner, telegram = _fake_scanner(calls)
    db = _active_db()

    scanner._handle_menu_callback(
        "signals:filter:symbol:EURUSD",
        "123",
        77,
        telegram,
        "tester",
        db,
    )
    filtered_text, filtered_keyboard = calls[-1][1][2:4]
    assert "Pair: <b>EURUSD</b>" in filtered_text
    assert "Confidence: <b>any</b>" in filtered_text
    assert any(
        button.get("callback_data") == "signals:detail:alert-1"
        for row in filtered_keyboard
        for button in row
    )
    button_texts = [
        button.get("text", "")
        for row in filtered_keyboard
        for button in row
    ]
    assert any(text.startswith("\U0001F4B1") for text in button_texts)
    assert any(text.startswith("\U0001F7E2") for text in button_texts)
    assert [
        {"text": "\U0001F504 Refresh", "callback_data": "signals:refresh"},
        {"text": "\U0001F464 Profile", "callback_data": "nav:profile"},
        {"text": "\U0001F3E0 Home", "callback_data": "menu:main"},
    ] in filtered_keyboard

    scanner._handle_menu_callback(
        "signals:detail:alert-1",
        "123",
        77,
        telegram,
        "tester",
        db,
    )
    detail_text = calls[-1][1][2]
    assert "Signal Detail" in detail_text
    assert "Timeframe: <b>H1</b>" in detail_text
    assert "Profile: <b>swing_trader</b>" in detail_text
    assert "Confidence: <b>82.5</b>" in detail_text


def test_signal_detail_rechecks_delivery_ownership():
    calls = []
    scanner, telegram = _fake_scanner(calls)
    db = _active_db()

    scanner._handle_menu_callback(
        "signals:detail:alert-1",
        "456",
        77,
        telegram,
        "other_user",
        db,
    )
    detail_text = calls[-1][1][2]
    assert "not available for this account" in detail_text
    assert "EURUSD" not in detail_text


class _MutableSubscriberDB:
    def __init__(self):
        self.subscriber = {
            "username": "tester",
            "style": "day_trader",
            "timeframes": ["M15", "H1"],
            "settings": {"symbols": [], "min_confidence": None, "notifications": "all"},
        }

    def get_subscriber(self, chat_id):
        return self.subscriber

    def is_registered(self, chat_id):
        return True

    def get_settings(self, chat_id):
        return self.subscriber["settings"]

    def update_style(self, chat_id, style):
        from core.alerts.subscriber_db import STYLE_TIMEFRAMES

        self.subscriber["style"] = style
        self.subscriber["timeframes"] = STYLE_TIMEFRAMES[style]
        return True

    def update_settings(self, chat_id, key, value):
        self.subscriber["settings"][key] = value
        return True


def test_style_choice_supports_all_styles_routing():
    calls = []
    scanner, telegram = _fake_scanner(calls)
    db = _MutableSubscriberDB()
    scanner._subscriber_db = db

    scanner._handle_menu_callback("set:style_all", "123", 77, telegram, "tester", db)

    assert db.subscriber["style"] == "all_styles"
    assert db.subscriber["timeframes"] == ["M5", "M15", "H1", "H4", "D1"]
    assert any("Saved: trading style updated." in args[1] for kind, args in calls if kind == "reply")


def test_signal_pair_picker_persists_multiple_selected_pairs():
    calls = []
    scanner, telegram = _fake_scanner(calls)
    db = _MutableSubscriberDB()
    scanner._subscriber_db = db
    scanner._market_cfg = SimpleNamespace(
        instruments=[
            SimpleNamespace(symbol="EURUSD"),
            SimpleNamespace(symbol="GBPUSD"),
            SimpleNamespace(symbol="XAUUSD"),
            SimpleNamespace(symbol="USDJPY"),
        ]
    )

    scanner._handle_menu_callback("signals:pairs", "123", 77, telegram, "tester", db)
    pair_menu = calls[-1][1][2:4]
    assert "Signal Pair Routing" in pair_menu[0]
    assert any(
        button.get("callback_data") == "signals:pairs:toggle:GBPUSD"
        for row in pair_menu[1]
        for button in row
    )

    scanner._handle_menu_callback("signals:pairs:toggle:GBPUSD", "123", 77, telegram, "tester", db)
    scanner._handle_menu_callback("signals:pairs:toggle:XAUUSD", "123", 77, telegram, "tester", db)
    assert db.subscriber["settings"]["symbols"] == ["GBPUSD", "XAUUSD"]

    scanner._handle_menu_callback("signals:pairs:all", "123", 77, telegram, "tester", db)
    assert db.subscriber["settings"]["symbols"] == []


def test_key_level_filter_preference_persists_from_settings_menu():
    calls = []
    scanner, telegram = _fake_scanner(calls)
    db = _MutableSubscriberDB()
    scanner._subscriber_db = db

    scanner._handle_menu_callback("set:menu_key_level", "123", 77, telegram, "tester", db)
    menu_text, keyboard = calls[-1][1][2:4]
    assert "Key-Level Filter" in menu_text
    assert any(
        button.get("callback_data") == "set:key_level_on"
        for row in keyboard
        for button in row
    )

    scanner._handle_menu_callback("set:key_level_on", "123", 77, telegram, "tester", db)

    assert db.subscriber["settings"]["key_level_only"] is True
    assert any("key-level setups only" in args[1] for kind, args in calls if kind == "reply")


def test_high_quality_filter_preference_persists_from_settings_menu():
    calls = []
    scanner, telegram = _fake_scanner(calls)
    db = _MutableSubscriberDB()
    scanner._subscriber_db = db

    scanner._handle_menu_callback("set:menu_quality", "123", 77, telegram, "tester", db)
    menu_text, keyboard = calls[-1][1][2:4]
    assert "Quality Filter" in menu_text
    assert any(
        button.get("callback_data") == "set:quality_on"
        for row in keyboard
        for button in row
    )

    scanner._handle_menu_callback("set:quality_on", "123", 77, telegram, "tester", db)

    assert db.subscriber["settings"]["high_quality_only"] is True
    assert any("high-quality setups only" in args[1] for kind, args in calls if kind == "reply")


def test_premium_confluence_filter_preference_persists_from_settings_menu():
    calls = []
    scanner, telegram = _fake_scanner(calls)
    db = _MutableSubscriberDB()
    scanner._subscriber_db = db
    scanner._atr_quality_cfg = load_atr_quality_config("config/atlax.yaml")
    db.subscriber.update({
        "subscription_plan": "premium",
        "active": True,
        "subscription_status": "active",
        "subscription_expires_at": "2030-01-01T00:00:00+00:00",
    })

    scanner._handle_menu_callback("set:menu_confluence", "123", 77, telegram, "tester", db)
    menu_text, keyboard = calls[-1][1][2:4]
    assert "Premium Confluence" in menu_text
    assert any(
        button.get("callback_data") == "set:confluence_on"
        for row in keyboard
        for button in row
    )

    scanner._handle_menu_callback("set:confluence_on", "123", 77, telegram, "tester", db)

    assert db.subscriber["settings"]["confluence_only"] is True
    assert any("premium confluence setups only" in args[1] for kind, args in calls if kind == "reply")


def test_standard_user_cannot_open_or_enable_premium_confluence():
    calls = []
    scanner, telegram = _fake_scanner(calls)
    db = _MutableSubscriberDB()
    scanner._subscriber_db = db
    scanner._atr_quality_cfg = load_atr_quality_config("config/atlax.yaml")
    db.subscriber.update({
        "subscription_plan": "standard",
        "active": True,
        "subscription_status": "active",
        "subscription_expires_at": "2030-01-01T00:00:00+00:00",
    })

    scanner._show_settings_home("123", 77, telegram)
    settings_text, settings_keyboard = calls[-1][1][2:4]
    assert "Premium Plan Only" in settings_text
    assert not any(
        button.get("callback_data") == "set:menu_confluence"
        for row in settings_keyboard
        for button in row
    )

    scanner._handle_menu_callback("set:menu_confluence", "123", 77, telegram, "tester", db)
    assert "active Premium Plan" in calls[-1][1][2]

    scanner._handle_menu_callback("set:confluence_on", "123", 77, telegram, "tester", db)
    assert db.subscriber["settings"].get("confluence_only") is not True


def _payment_service(tmp_path):
    from decimal import Decimal

    from core.alerts.subscriber_db import SubscriberDB
    from core.payments.payment_service import PaymentService
    from core.payments.payment_store import PaymentStore
    from core.settings.payment_config import CryptoPaymentConfig, MpesaPaymentConfig, PaymentConfig

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
        mpesa=MpesaPaymentConfig(True, "0700000000", "ATLAX15", "KSh equivalent"),
    )
    db = SubscriberDB(
        codes_file=str(tmp_path / "invite_codes.json"),
        subs_file=str(tmp_path / "subscribers.json"),
    )
    store = PaymentStore(str(tmp_path / "payment_intents.json"))
    return PaymentService(config, store=store, subscriber_db=db), db


def test_payment_submission_and_approval_are_idempotent(tmp_path):
    service, db = _payment_service(tmp_path)
    intent, _ = service.create_payment_intent("123", "tester", "crypto")

    submitted, first_message = service.submit_payment(intent["reference"], tx_reference="tx-1")
    submitted_again, second_message = service.submit_payment(intent["reference"], tx_reference="tx-2")
    assert submitted["status"] == "submitted"
    assert submitted_again["submitted_tx_reference"] == "tx-1"
    assert "submitted" in first_message.lower()
    assert "already" in second_message.lower()

    confirmed, confirmation_message = service.confirm_payment(intent["reference"], confirmed_by="admin-1")
    confirmed_again, repeat_message = service.confirm_payment(intent["reference"], confirmed_by="admin-1")
    assert confirmed["status"] == "confirmed"
    assert confirmed_again["status"] == "confirmed"
    assert db.is_registered("123") is True
    assert "activated" in confirmation_message.lower()
    assert "already active" in repeat_message.lower()
    assert [event["status"] for event in confirmed["status_history"]] == [
        "awaiting_payment", "submitted", "confirmed"
    ]


def test_subscription_checkout_uses_explicit_rails_and_reference_bound_proof(tmp_path):
    calls = []
    scanner, telegram = _fake_scanner(calls)
    service, db = _payment_service(tmp_path)
    scanner._payment_cfg = service._config
    scanner._payment_service = service
    scanner._subscription_lifecycle = None
    scanner._admin_audit = SimpleNamespace(record=lambda *args, **kwargs: None)

    scanner._show_subscription_menu("123", telegram, db)
    _, _, subscription_keyboard = calls[-1][1]
    labels = [
        button["text"]
        for row in subscription_keyboard
        for button in row
        if "text" in button
    ]
    assert "🌱 Standard Plan" in labels
    assert "⭐ Premium Plan" in labels

    scanner._handle_menu_callback(
        "subscribe:plan:premium",
        "123",
        77,
        telegram,
        "tester",
        db,
    )
    _, _, selected_text, selected_keyboard = calls[-1][1]
    assert "You selected: Premium Plan — $50 USD" in selected_text
    assert "Your ATLAX access is active" not in selected_text
    assert "Latest payment" not in selected_text
    assert "Choose payment method" in selected_text
    selected_labels = [
        button["text"]
        for row in selected_keyboard
        for button in row
        if "text" in button
    ]
    assert "🪙 Pay with Solana" in selected_labels
    assert "📱 Pay with M-Pesa" in selected_labels
    assert any(
        button.get("callback_data") == "pay:crypto:premium"
        for row in selected_keyboard
        for button in row
    )
    assert db.is_registered("123") is False

    scanner._start_payment_flow(
        "123", "tester", "crypto", telegram, db, subscription_plan="premium"
    )
    _, payment_text, payment_keyboard = calls[-1][1]
    assert "Payment Reference Generated" in payment_text
    reference = next(
        button["callback_data"].split(":", 3)[3]
        for row in payment_keyboard
        for button in row
        if button.get("text") == "📤 Submit Payment Proof"
    )
    assert reference in payment_text

    scanner._handle_menu_callback(
        f"payment:proof:crypto:{reference}",
        "123",
        77,
        telegram,
        "tester",
        db,
    )
    _, _, proof_text, _ = calls[-1][1]
    assert reference in proof_text
    assert f"/paid {reference} SOLANA_TRANSACTION_SIGNATURE" in proof_text


def test_subscription_menu_keeps_enabled_rails_visible_when_destination_is_pending(tmp_path):
    calls = []
    scanner, telegram = _fake_scanner(calls)
    service, db = _payment_service(tmp_path)
    from core.settings.payment_config import CryptoPaymentConfig, MpesaPaymentConfig

    pending_config = service._config.__class__(
        enabled=True,
        subscription_price_usd=service._config.subscription_price_usd,
        subscription_duration_days=service._config.subscription_duration_days,
        default_style=service._config.default_style,
        manual_review_required=True,
        payment_reference_prefix=service._config.payment_reference_prefix,
        payment_ttl_hours=service._config.payment_ttl_hours,
        support_contact=service._config.support_contact,
        crypto=CryptoPaymentConfig(True, "", "USDC-Solana", "USDC"),
        mpesa=MpesaPaymentConfig(True, "", "ATLAX15", "", ""),
    )
    scanner._payment_cfg = pending_config
    scanner._payment_service = service.__class__(pending_config, store=service._store, subscriber_db=db)
    scanner._subscription_lifecycle = None
    scanner._admin_audit = SimpleNamespace(record=lambda *args, **kwargs: None)

    scanner._show_subscription_menu("123", telegram, db, selected_plan="standard")
    labels = [button.get("text") for row in calls[-1][1][2] for button in row]
    assert "🪙 Pay with Solana" in labels
    assert "📱 Pay with M-Pesa" in labels


def test_rejection_is_terminal_and_history_is_rendered(tmp_path):
    service, _ = _payment_service(tmp_path)
    intent, _ = service.create_payment_intent("456", "tester", "mpesa")

    rejected, message = service.reject_payment(intent["reference"], rejected_by="admin-1", reason="Reference did not match")
    rejected_again, repeat_message = service.reject_payment(intent["reference"], rejected_by="admin-1")
    confirmed, confirm_message = service.confirm_payment(intent["reference"], confirmed_by="admin-1")

    assert rejected["status"] == "rejected"
    assert rejected_again["status"] == "rejected"
    assert "rejected" in message.lower()
    assert "already" in repeat_message.lower()
    assert confirmed is None
    assert "already rejected" in confirm_message.lower()

    history = service.build_payment_history_message("456")
    assert intent["reference"] in history
    assert "awaiting payment" in history
    assert "rejected" in history


def test_operational_audit_is_append_only_and_redacts_sensitive_metadata(tmp_path):
    from core.logging.audit_log import OperationalAuditLog

    audit = OperationalAuditLog(str(tmp_path / "admin_audit.jsonl"))
    audit.record(
        "999",
        "payment_approve",
        target_chat_id="123",
        metadata={"reference": "ATLAX-CR-ABC123", "token": "should-not-appear"},
    )
    audit.record("999", "subscriber_deactivate", target_chat_id="123")

    events = audit.recent(10)
    assert [event["action"] for event in events] == [
        "subscriber_deactivate",
        "payment_approve",
    ]
    assert events[1]["metadata"]["token"] == "[REDACTED]"
    assert "should-not-appear" not in (tmp_path / "admin_audit.jsonl").read_text(encoding="utf-8")


def test_non_admin_cannot_enter_any_admin_callback_namespace():
    calls = []
    scanner, telegram = _fake_scanner(calls)
    telegram.send_reply = lambda *args: calls.append(("reply", args))
    scanner._admin_audit = SimpleNamespace(record=lambda *args, **kwargs: None)
    db = _active_db()

    for callback in (
        "admin:home",
        "admin:accounts",
        "admin:payments",
        "admin:subscribers",
        "admin:health",
        "admin:execution_quality",
        "admin:audit",
        "admin:sub:view:123",
        "admin:sub:confirm_deactivate:123",
        "admin:sub:deactivate:123",
    ):
        calls.clear()
        scanner._handle_menu_callback(callback, "123", 77, telegram, "tester", db)
        assert calls and calls[-1][0] == "reply", callback
        assert "admin only" in calls[-1][1][1].lower()


def test_admin_trading_account_wizard_persists_no_password_and_selects_explicitly():
    class RuntimeStore:
        def __init__(self):
            self.values = {}

        def get(self, key, default=None):
            return self.values.get(key, default)

        def set(self, key, value):
            self.values[key] = value

    calls = []
    scanner, telegram = _fake_scanner(calls)
    scanner._runtime_store = RuntimeStore()

    scanner._handle_trading_account_callback("admin:accounts:add", "999", 77, telegram)
    assert "Add Trading Account" in calls[-1][1][2]
    assert "password" in calls[-1][1][2].lower()

    assert scanner._consume_trading_account_draft_input("Maven 2K", "999", True, telegram)
    assert scanner._consume_trading_account_draft_input("10422092", "999", True, telegram)
    assert scanner._consume_trading_account_draft_input("MavenTrade-Server", "999", True, telegram)

    assert [entry[1][1] for entry in calls if entry[0] == "delete"] == [77, 1001, 1002]

    registry = scanner._runtime_store.get("trading_accounts.registry")
    assert registry["accounts"][0]["label"] == "Maven 2K"
    assert registry["accounts"][0]["login"] == 10422092
    assert "password" not in str(registry).lower()

    # Saving an additional account leaves the current target untouched.
    from core.operations.trading_account_registry import add_trading_account

    second = add_trading_account(
        scanner._runtime_store,
        label="Demo",
        login=20422092,
        server="MavenTrade-Demo",
        actor="telegram:999",
    )
    scanner._handle_trading_account_callback(
        f"admin:accounts:select:{second.accounts[1].profile_id}",
        "999",
        77,
        telegram,
    )

    assert scanner._runtime_store.get("trading_accounts.registry")["active_profile_id"] == second.accounts[1].profile_id
    assert any(
        entry[0] == "reply" and "Account selected" in entry[1][1]
        for entry in calls
    )
    assert any(entry[0] == "delete" and entry[1][1] == 77 for entry in calls)


def test_admin_can_confirm_permanent_deletion_of_unselected_account():
    class RuntimeStore:
        def __init__(self):
            self.values = {}

        def get(self, key, default=None):
            return self.values.get(key, default)

        def set(self, key, value):
            self.values[key] = value

    from core.operations.trading_account_registry import (
        add_trading_account,
        select_trading_account,
    )

    calls = []
    scanner, telegram = _fake_scanner(calls)
    scanner._runtime_store = RuntimeStore()
    first = add_trading_account(
        scanner._runtime_store,
        label="Old Demo",
        login=123456,
        server="Broker-Demo",
        actor="telegram:999",
    )
    second = add_trading_account(
        scanner._runtime_store,
        label="Live Account",
        login=654321,
        server="Broker-Live",
        actor="telegram:999",
    )
    select_trading_account(
        scanner._runtime_store,
        profile_id=second.accounts[1].profile_id,
        actor="telegram:999",
    )

    scanner._handle_trading_account_callback("admin:accounts:delete", "999", 77, telegram)
    delete_menu = calls[-1][1]
    assert "Delete Trading Account" in delete_menu[2]
    assert any(
        button.get("callback_data") == f"admin:accounts:delete:{first.accounts[0].profile_id}"
        for row in delete_menu[3]
        for button in row
    )

    scanner._handle_trading_account_callback(
        f"admin:accounts:delete:{first.accounts[0].profile_id}",
        "999",
        77,
        telegram,
    )
    assert "Permanent deletion" in calls[-1][1][2]

    scanner._handle_trading_account_callback(
        f"admin:accounts:delete_confirm:{first.accounts[0].profile_id}",
        "999",
        77,
        telegram,
    )
    registry = scanner._runtime_store.get("trading_accounts.registry")
    assert [account["label"] for account in registry["accounts"]] == ["Live Account"]
    assert registry["active_profile_id"] == second.accounts[1].profile_id


def test_selected_account_terminal_guard_rejects_a_manual_mt5_switch(monkeypatch):
    class RuntimeStore:
        def __init__(self):
            self.values = {}

        def get(self, key, default=None):
            return self.values.get(key, default)

        def set(self, key, value):
            self.values[key] = value

    from core.operations.trading_account_registry import add_trading_account
    import core.market_data.scanner as scanner_module

    scanner = object.__new__(MarketScanner)
    scanner._runtime_store = RuntimeStore()
    add_trading_account(
        scanner._runtime_store,
        label="Primary",
        login=10422092,
        server="MavenTrade-Server",
        actor="telegram:999",
    )

    monkeypatch.setattr(
        scanner_module,
        "mt5",
        SimpleNamespace(
            account_info=lambda: SimpleNamespace(
                login=20422092,
                server="MavenTrade-Server",
            )
        ),
    )

    assert scanner._selected_account_matches_terminal() is False


def test_selected_account_disables_the_environment_password_fallback():
    class RuntimeStore:
        def __init__(self):
            self.values = {}

        def get(self, key, default=None):
            return self.values.get(key, default)

        def set(self, key, value):
            self.values[key] = value

    from core.operations.trading_account_registry import add_trading_account
    from core.settings.market_data_config import MT5ConnectionConfig

    scanner = object.__new__(MarketScanner)
    scanner._runtime_store = RuntimeStore()
    scanner._market_cfg = SimpleNamespace(
        mt5=MT5ConnectionConfig(
            account=1,
            server="Old-Server",
            password="stale-environment-secret",
            timeout_ms=60000,
            max_retries=3,
        )
    )
    add_trading_account(
        scanner._runtime_store,
        label="Selected",
        login=10422092,
        server="MavenTrade-Server",
        actor="telegram:999",
    )

    selected = scanner._active_mt5_config()

    assert selected.account == 10422092
    assert selected.server == "MavenTrade-Server"
    assert selected.password == ""


def test_watchdog_notification_is_scheduled_and_deleted_after_configured_ttl():
    class RuntimeStore:
        def __init__(self):
            self.values = {}

        def get(self, key, default=None):
            return self.values.get(key, default)

        def set(self, key, value):
            self.values[key] = value

    calls = []
    scanner, telegram = _fake_scanner(calls)
    scanner._runtime_store = RuntimeStore()
    scanner._watchdog = SimpleNamespace(
        _config=SimpleNamespace(notification_ttl_seconds=300)
    )

    scanner._send_temporary_watchdog_notification(
        telegram,
        "999",
        "Watchdog status is degraded.",
    )

    records = scanner._runtime_store.get(scanner._WATCHDOG_EXPIRING_MESSAGES_KEY)
    assert len(records) == 1
    assert records[0]["chat_id"] == "999"
    assert records[0]["message_id"] == 1001
    assert "Watchdog status" not in str(records[0])

    records[0]["expires_at"] = "2000-01-01T00:00:00+00:00"
    scanner._runtime_store.set(scanner._WATCHDOG_EXPIRING_MESSAGES_KEY, records)
    scanner._purge_expired_watchdog_messages(telegram)

    assert ("delete", ("999", 1001)) in calls
    assert scanner._runtime_store.get(scanner._WATCHDOG_EXPIRING_MESSAGES_KEY) == []


def test_admin_execution_quality_view_is_audited_and_refreshable():
    calls = []
    scanner, telegram = _fake_scanner(calls)
    scanner._journal = SimpleNamespace(
        list_execution_quality_events=lambda limit=12: [{
            "event_id": "evt-1",
            "event_type": "EXECUTION_RESULT",
            "symbol": "EURUSD",
            "timeframe": "M15",
            "direction": "BUY",
            "status": "FILLED",
            "action": "order_send",
            "occurred_at": "2026-09-20T08:00:00+00:00",
            "reason": "accepted",
            "metadata": {"secret": "must-not-render"},
        }]
    )
    scanner._handle_menu_callback("admin:execution_quality", "999", 77, telegram, "admin", _active_db())

    assert calls[-1][0] == "edit"
    text = calls[-1][1][2]
    keyboard = calls[-1][1][3]
    assert "Execution Quality" in text
    assert "EURUSD" in text
    assert "must-not-render" not in text
    assert any(
        button.get("callback_data") == "admin:execution_quality"
        for row in keyboard
        for button in row
    )


def test_prop_firm_controls_are_admin_only_and_persist_selection():
    class RuntimeStore:
        def __init__(self):
            self.values = {}

        def get(self, key, default=None):
            return self.values.get(key, default)

        def set(self, key, value):
            self.values[key] = value

    calls = []
    scanner, telegram = _fake_scanner(calls)
    scanner._runtime_store = RuntimeStore()
    scanner._prop_firm_profiles = ({
        "profile_id": "example-funded-usd",
        "version": "2026-09-19.1",
        "firm_name": "Example Firm",
        "program_name": "Example Program",
        "phase": "FUNDED",
        "account_type": "STANDARD",
        "currency": "USD",
    },)
    db = _active_db()

    scanner._handle_menu_callback("propfirm:menu", "123", 77, telegram, "tester", db)
    assert calls[-1][0] == "reply"
    assert "admin only" in calls[-1][1][1].lower()

    calls.clear()
    scanner._handle_menu_callback("propfirm:profiles", "999", 77, telegram, "admin", db)
    assert calls[-1][0] == "edit"
    assert "Example Firm" in calls[-1][1][2]

    scanner._handle_menu_callback("propfirm:profile:0", "999", 77, telegram, "admin", db)
    scanner._handle_menu_callback("propfirm:enable", "999", 77, telegram, "admin", db)
    scanner._handle_menu_callback("propfirm:pause", "999", 77, telegram, "admin", db)

    state = scanner._runtime_store.get("prop_firm.control")
    assert state["enabled"] is True
    assert state["paused"] is True
    assert state["selected_profile_id"] == "example-funded-usd"
    assert any("Enabled" in entry[1][2] for entry in calls if entry[0] == "edit")


def test_prop_firm_enable_without_verified_profile_stays_disabled():
    calls = []
    scanner, telegram = _fake_scanner(calls)
    class RuntimeStore:
        def __init__(self):
            self.values = {}

        def get(self, key, default=None):
            return self.values.get(key, default)

        def set(self, key, value):
            self.values[key] = value

    scanner._runtime_store = RuntimeStore()
    scanner._prop_firm_profiles = ()
    db = _active_db()

    scanner._handle_menu_callback("propfirm:enable", "999", 77, telegram, "admin", db)

    state = scanner._runtime_store.get("prop_firm.control", {})
    assert state.get("enabled", False) is False
    assert any(entry[0] == "reply" and "Cannot enable" in entry[1][1] for entry in calls)


def test_prop_firm_shadow_uses_canonical_profile_selection():
    calls = []
    scanner, telegram = _fake_scanner(calls)

    class RuntimeStore:
        def __init__(self):
            self.values = {
                "account_mode.state": {
                    "selected_profile_id": "example-funded-usd",
                    "selected_profile_version": "2026-09-19.1",
                    "mode": "AUTO_ENFORCED",
                    "status": "VERIFIED",
                },
                "prop_firm.control": {"enabled": True, "paused": False},
            }

        def get(self, key, default=None):
            return self.values.get(key, default)

        def set(self, key, value):
            self.values[key] = value

    scanner._runtime_store = RuntimeStore()
    scanner._prop_firm_profiles = ({
        "profile_id": "example-funded-usd",
        "version": "2026-09-19.1",
        "firm_name": "Example Firm",
        "program_name": "Example Program",
        "phase": "FUNDED",
        "account_type": "STANDARD",
        "currency": "USD",
    },)
    db = _active_db()

    scanner._handle_menu_callback("propfirm:enable", "999", 77, telegram, "admin", db)

    assert not any(
        entry[0] == "reply" and "without a selected" in entry[1][1]
        for entry in calls
    )


def test_admin_ai_menu_exposes_prop_firm_rules_button():
    calls = []
    scanner, telegram = _fake_scanner(calls)

    scanner._handle_menu_callback("menu:ai", "999", 77, telegram, "admin", _active_db())

    assert calls[-1][0] == "edit"
    assert any(
        button.get("callback_data") == "ai:prop_firm"
        for row in calls[-1][1][3]
        for button in row
    )


def test_subscriber_ai_callback_deletes_temporary_thinking_message():
    calls = []
    scanner, telegram = _fake_scanner(calls)
    scanner._market_cfg = SimpleNamespace(
        instruments=[SimpleNamespace(symbol="EURUSD"), SimpleNamespace(symbol="GBPUSD")]
    )
    scanner._advisory_engine = SimpleNamespace(
        answer_subscriber_question=lambda question, chat_id: "🤖 <b>AI answer</b>"
    )

    scanner._handle_subscriber_ai_callback(
        "subai:news",
        "123",
        77,
        telegram,
        _active_db(),
    )

    assert ("prompt", ("123", "Thinking..."), {"reply_to_message_id": None}) in calls
    assert ("delete", ("123", 1001)) in calls
    assert any(
        entry[0] == "edit" and entry[1][2] == "🤖 <b>AI answer</b>"
        for entry in calls
    )
