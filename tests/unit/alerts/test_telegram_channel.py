import json
import io
import urllib.error
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from core.alerts.i18n import localize_text
from core.alerts.telegram_channel import TelegramAlertChannel, _build_message
from core.analytics.faq_knowledge import load_public_faq, render_faq_page
from core.analytics.signal_passport import render_signal_passport_weekly
from core.models.alert_event import AlertEvent


class _Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.payload


def _channel():
    return TelegramAlertChannel(
        SimpleNamespace(
            enabled=True,
            bot_token="test-token",
            chat_id="123",
            parse_mode="HTML",
            timeout_seconds=2,
            retry_attempts=1,
            mini_app_url="https://signals.example/miniapp",
        )
    )


def test_poll_commands_accepts_messages_and_callback_queries(monkeypatch):
    channel = _channel()
    updates = {
        "ok": True,
        "result": [
            {
                "update_id": 10,
                "message": {
                    "message_id": 7,
                    "chat": {"id": 123},
                    "from": {"username": "tester"},
                    "text": "/start@atlax_bot",
                },
            },
            {
                "update_id": 11,
                "callback_query": {
                    "id": "callback-1",
                    "from": {"username": "tester"},
                    "data": "menu:subscribe",
                    "message": {"message_id": 8, "chat": {"id": 123}},
                },
            },
        ],
    }
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: _Response(updates),
    )

    commands = channel.poll_commands()

    assert commands[0]["cmd"] == "/start"
    assert commands[0]["message_id"] == 7
    assert commands[1]["type"] == "callback"
    assert commands[1]["cmd"] == "menu:subscribe"
    assert commands[1]["callback_query_id"] == "callback-1"
    assert channel._last_update_id == 11
    assert channel._last_poll_failed is False


def test_poll_commands_marks_plain_text_as_a_message_for_admin_setup_flows(monkeypatch):
    channel = _channel()
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: _Response(
            {
                "ok": True,
                "result": [
                    {
                        "update_id": 12,
                        "message": {
                            "message_id": 9,
                            "chat": {"id": 123},
                            "from": {"username": "tester"},
                            "text": "Maven 2K",
                        },
                    }
                ],
            }
        ),
    )

    commands = channel.poll_commands()

    assert commands == [
        {
            "cmd": "Maven 2K",
            "chat_id": "123",
            "username": "tester",
            "language_code": "",
            "is_admin": True,
            "message_id": 9,
            "type": "message",
        }
    ]


def test_inline_menu_and_callback_answer_use_telegram_api(monkeypatch):
    channel = _channel()
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return _Response({"ok": True, "result": {"message_id": 42}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    assert channel.send_inline_menu("123", "Home", [[{"text": "Subscribe", "callback_data": "menu:subscribe"}]]) == 42
    assert channel.edit_inline_menu("123", 42, "Updated", []) is True
    assert channel.answer_callback_query("callback-1") is True

    assert len(requests) == 3
    assert "/sendMessage" in requests[0].full_url
    assert "/editMessageText" in requests[1].full_url
    assert "/answerCallbackQuery" in requests[2].full_url


def test_one_time_prompt_and_deletion_use_telegram_api(monkeypatch):
    channel = _channel()
    calls = []
    channel._api_request = lambda method, payload: calls.append((method, payload)) or {
        "ok": True,
        "result": {"message_id": 42},
    }

    prompt_id = channel.send_one_time_prompt("123", "Reply with your account label.")

    assert prompt_id == 42
    assert channel.delete_message("123", prompt_id) is True
    assert calls == [
        (
            "sendMessage",
            {
                "chat_id": "123",
                "text": "Reply with your account label.",
                "parse_mode": "HTML",
            },
        ),
        ("deleteMessage", {"chat_id": "123", "message_id": 42}),
    ]
    assert channel.delete_message("123", None) is False


def test_outbound_menus_and_replies_use_saved_subscriber_language():
    requests = []

    class _LanguageDB:
        def get_language(self, chat_id):
            return "sw"

    channel = TelegramAlertChannel(
        SimpleNamespace(
            enabled=True,
            bot_token="test-token",
            chat_id="123",
            parse_mode="HTML",
            timeout_seconds=2,
            retry_attempts=1,
            mini_app_url="",
        ),
        subscriber_db=_LanguageDB(),
    )
    channel._api_request = lambda method, payload: requests.append((method, payload)) or {
        "ok": True,
        "result": {"message_id": 42},
    }

    channel.send_inline_menu(
        "456",
        "💳 <b>ATLAX Signal Subscription</b>",
        [[{"text": "💳 Card Payment", "callback_data": "pay:card:standard"}]],
    )
    channel.send_reply("456", "✅ <b>Payment Reference Generated</b>")

    menu_payload = requests[0][1]
    assert "Usajili wa Ishara za ATLAX" in menu_payload["text"]
    assert "Malipo kwa Kadi" in menu_payload["reply_markup"]
    assert "Rejea ya Malipo Imetengenezwa" in requests[1][1]["text"]


def test_faq_and_passport_report_copy_use_saved_language():
    faq = load_public_faq("docs/20_PUBLIC_FAQ.md")
    localized_faq_pages = [
        localize_text(render_faq_page(faq, page=page, page_size=10)[0], "sw")
        for page in range(3)
    ]
    assert "ATLAX NI NINI?" in localized_faq_pages[0]
    assert "atlax ni huduma ya taarifa za soko" in localized_faq_pages[0]
    assert "WHAT IS ATLAX?" not in localized_faq_pages[0]
    assert all("use <code>" not in page for page in localized_faq_pages)
    assert all("education. subscriber" not in page for page in localized_faq_pages)

    report = render_signal_passport_weekly(
        {
            "status": "OK",
            "total_signals": 5,
            "open_signals": 1,
            "resolved_signals": 4,
            "unknown_outcomes": 0,
            "by_pair": {
                "EURUSD": {"total_signals": 5, "wins": 2, "losses": 2, "win_rate_pct": 50.0},
            },
            "by_style": {},
        }
    )
    localized_report = localize_text(report, "sw")
    assert "Utendaji kwa" in localized_report
    assert "Kwa Jozi" in localized_report
    assert "5 ishara · 2 ushindi · 2 hasara" in localized_report


def test_identical_inline_menu_edit_is_idempotent(monkeypatch):
    channel = _channel()

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(
                b'{"ok":false,"error_code":400,'
                b'"description":"Bad Request: message is not modified"}'
            ),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    assert channel.edit_inline_menu("123", 42, "Home", []) is True


def test_inline_menu_edit_preserves_real_telegram_error(monkeypatch):
    channel = _channel()

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(
                b'{"ok":false,"error_code":400,'
                b'"description":"Bad Request: message to edit not found"}'
            ),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    assert channel.edit_inline_menu("123", 42, "Home", []) is False


def test_mini_app_button_uses_configured_url():
    assert _channel().mini_app_button() == {
        "text": "Open ATLAX",
        "web_app": {"url": "https://signals.example/miniapp"},
    }


def test_alert_targets_use_the_same_trophy_marker():
    alert = AlertEvent(
        alert_id="alert-1",
        candidate_id="candidate-1",
        symbol="EURUSD",
        timeframe="H1",
        profile="day_trader",
        direction="BUY",
        classification="bullish_crt",
        confidence_score=92.0,
        confidence_breakdown={},
        missing_factors=(),
        crt_high=Decimal("1.10500"),
        crt_low=Decimal("1.10000"),
        entry_zone_high=Decimal("1.10400"),
        entry_zone_low=Decimal("1.10250"),
        invalidation_level=Decimal("1.09800"),
        midpoint_target=Decimal("1.10700"),
        opposite_extreme_target=Decimal("1.11000"),
        explanation="test alert",
        created_at=datetime.now(timezone.utc),
    )

    message = _build_message(alert)

    assert message.count("🏆") == 2
    assert "🩩" not in message


def test_fvg_broadcast_methods_send_admin_and_eligible_subscriber(monkeypatch):
    channel = _channel()

    class _Subscribers:
        def get_subscribers_for_timeframe(self, timeframe, symbol=None):
            assert timeframe == "M15"
            assert symbol == "EURUSD"
            return [{
                "chat_id": "456",
                "subscription_plan": "premium",
                "active": True,
                "settings": {"min_confidence": 50},
            }]

    channel._subscriber_db = _Subscribers()
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return _Response({"ok": True, "result": {"message_id": len(requests)}})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    delivered = channel.broadcast_fvg_watch(
        symbol="EURUSD",
        htf_timeframe="M15",
        ltf_timeframe="M5",
        direction="bullish",
        fvg_high="1.10500",
        fvg_low="1.10300",
        fvg_midpoint="1.10400",
        invalidation_level="1.10000",
        midpoint_target="1.10800",
        opposite_extreme_target="1.11200",
        confidence_score=82,
        profile="day_trader",
        candidate_id="candidate-1234",
        parent_at_htf_zone=True,
        profile_min_confidence=60,
    )

    assert delivered == [2]
    assert len(requests) == 2
    assert b"FVG+ENTRY+WATCH" in requests[0].data
    assert b"PREMIUM+SIGNAL" in requests[1].data
