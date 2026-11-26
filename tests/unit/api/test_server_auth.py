from __future__ import annotations

import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from core.api import server
from core.api.auth_db import AuthDB


class DummyApprovalEngine:
    def get_all_pending(self, profile=None):
        return []

    def approve_trade(self, candidate_id: str):
        return True

    def reject_trade(self, candidate_id: str):
        return True


class DummySubscriberDB:
    def list_subscribers(self):
        return []

    def deactivate(self, chat_id: str):
        return None

    def list_codes(self):
        return []

    def generate_code(self, label: str = "", days_valid: int = 30):
        return "ATLAX-TEST"

    def revoke_code(self, code: str):
        return None


class MiniAppSubscriberDB:
    def __init__(self):
        self.subscriber = {
            "chat_id": "12345",
            "username": "mini_user",
            "style": "day_trader",
            "timeframes": ["M15", "H1"],
            "active": True,
            "subscription_status": "active",
            "subscription_source": "paid",
            "subscription_expires_at": "2030-01-01T00:00:00+00:00",
        }
        self.settings = {
            "symbols": [],
            "min_confidence": None,
            "notifications": "all",
        }

    def get_subscriber(self, chat_id: str):
        return self.subscriber if str(chat_id) == "12345" else None

    def is_registered(self, chat_id: str):
        return str(chat_id) == "12345" and self.subscriber["active"]

    def get_settings(self, chat_id: str):
        return dict(self.settings) if str(chat_id) == "12345" else {}

    def update_style(self, chat_id: str, style: str):
        if not self.is_registered(chat_id):
            return False
        self.subscriber["style"] = style
        self.subscriber["timeframes"] = {
            "scalper": ["M5"],
            "day_trader": ["M15", "H1"],
            "swing_trader": ["H4", "D1"],
            "all_styles": ["M5", "M15", "H1", "H4", "D1"],
        }[style]
        return True

    def update_settings(self, chat_id: str, key: str, value):
        if not self.is_registered(chat_id):
            return False
        self.settings[key] = value
        return True


@pytest.fixture()
def empty_client(tmp_path, monkeypatch):
    auth_db = AuthDB(str(tmp_path / "users.sqlite"))
    monkeypatch.setattr(server, "auth_db", auth_db)
    monkeypatch.setattr(server, "approval_engine", DummyApprovalEngine())
    monkeypatch.setattr(server, "_sub_db", DummySubscriberDB())
    return TestClient(server.app), auth_db


@pytest.fixture()
def authed_client(tmp_path, monkeypatch):
    auth_db = AuthDB(str(tmp_path / "users.sqlite"))
    auth_db.create_user("admin", "adminpass", role="admin")
    auth_db.create_user("analyst", "analystpass", role="user")
    monkeypatch.setattr(server, "auth_db", auth_db)
    monkeypatch.setattr(server, "approval_engine", DummyApprovalEngine())
    monkeypatch.setattr(server, "_sub_db", DummySubscriberDB())
    return TestClient(server.app), auth_db


@pytest.fixture()
def miniapp_client(tmp_path, monkeypatch):
    auth_db = AuthDB(str(tmp_path / "users.sqlite"))
    subscriber_db = MiniAppSubscriberDB()
    monkeypatch.setattr(server, "auth_db", auth_db)
    monkeypatch.setattr(server, "approval_engine", DummyApprovalEngine())
    monkeypatch.setattr(server, "_sub_db", subscriber_db)
    return TestClient(server.app), subscriber_db


def _signed_init_data(*, chat_id: int = 12345, auth_date: int | None = None) -> str:
    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAHtest-query",
        "user": json.dumps(
            {"id": chat_id, "first_name": "Mini", "username": "mini_user"},
            separators=(",", ":"),
        ),
    }
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret_key = hmac.new(
        b"WebAppData",
        server.os.environ["ATLAX_TELEGRAM_BOT_TOKEN"].encode("utf-8"),
        hashlib.sha256,
    ).digest()
    signature = hmac.new(secret_key, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": signature})


def test_miniapp_bootstrap_issues_separate_cookie_and_scopes_identity(miniapp_client):
    client, _ = miniapp_client

    response = client.post(
        "/api/miniapp/bootstrap",
        json={"init_data": _signed_init_data()},
    )

    assert response.status_code == 200, response.text
    assert client.cookies.get(server.MINI_APP_COOKIE_NAME)
    assert response.json()["user"]["telegram_id"] == "12345"
    assert response.json()["access_active"] is True

    profile = client.get("/api/miniapp/me")
    assert profile.status_code == 200
    assert profile.json()["user"]["telegram_id"] == "12345"


@pytest.mark.parametrize(
    "init_data",
    [
        "auth_date=bad&user=%7B%22id%22%3A12345%7D&hash=not-a-signature",
        _signed_init_data(auth_date=1),
    ],
)
def test_miniapp_bootstrap_rejects_invalid_or_stale_init_data(miniapp_client, init_data):
    client, _ = miniapp_client

    response = client.post("/api/miniapp/bootstrap", json={"init_data": init_data})

    assert response.status_code == 401
    assert client.cookies.get(server.MINI_APP_COOKIE_NAME) is None


def test_dashboard_cookie_cannot_authenticate_miniapp_routes(authed_client):
    client, _ = authed_client
    _login(client, "admin", "adminpass")

    response = client.get("/api/miniapp/me")

    assert response.status_code == 401
    assert response.json()["detail"] == "Mini App session required"


def test_miniapp_signals_only_return_owned_deliveries(miniapp_client, monkeypatch):
    client, _ = miniapp_client

    class FakeTracker:
        def __init__(self):
            self.predictions = {
                "owned": {
                    "chat_id": "999",
                    "symbol": "EURUSD",
                    "direction": "BUY",
                    "target": 1.2,
                    "midpoint": 1.1,
                    "sl": 1.0,
                    "timestamp": time.time(),
                    "subscriber_deliveries": [{"chat_id": "12345", "message_id": 1}],
                },
                "not-owned": {
                    "chat_id": "999",
                    "symbol": "GBPUSD",
                    "direction": "SELL",
                    "target": 1.2,
                    "midpoint": 1.1,
                    "sl": 1.0,
                    "timestamp": time.time(),
                    "subscriber_deliveries": [{"chat_id": "54321", "message_id": 2}],
                },
            }

        def get_daily_report(self):
            return {"total": 2, "success": 1, "failed": 0}

    class FakeJournal:
        def __init__(self, _path):
            pass

        def get_entries(self):
            return [
                SimpleNamespace(
                    record_id="owned",
                    timeframe="H1",
                    profile="day_trader",
                    confidence_score=84,
                    entry_price="1.15",
                ),
                SimpleNamespace(
                    record_id="not-owned",
                    timeframe="H1",
                    profile="day_trader",
                    confidence_score=84,
                    entry_price="1.25",
                ),
            ]

    import core.analytics.signal_tracker as signal_tracker

    monkeypatch.setattr(signal_tracker, "SignalTracker", FakeTracker)
    monkeypatch.setattr(server, "get_journal_store", FakeJournal)
    client.post("/api/miniapp/bootstrap", json={"init_data": _signed_init_data()})

    response = client.get("/api/miniapp/signals")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["signals"]] == ["owned"]
    assert "subscriber_deliveries" not in response.text


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/api/auth/login",
        data={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "access_token" not in payload
    assert payload["status"] == "success"
    assert client.cookies.get(server.AUTH_COOKIE_NAME)
    return payload["user"]["username"]


def test_bootstrap_signup_creates_only_initial_admin(empty_client):
    client, auth_db = empty_client

    response = client.post("/api/auth/signup", json={"username": "owner", "password": "strongpass"})
    assert response.status_code == 200
    assert response.json() == {"status": "success"}

    user_row = auth_db.get_user("owner")
    assert user_row is not None
    assert user_row["role"] == "admin"

    second = client.post("/api/auth/signup", json={"username": "intruder", "password": "strongpass"})
    assert second.status_code == 403
    assert second.json()["detail"] == "System already initialized"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/config", None),
        ("POST", "/api/config", {"yaml_content": "dashboard:\n  enabled: true\n"}),
        ("GET", "/api/admin/operations", None),
        ("GET", "/api/admin/overview", None),
        ("GET", "/api/approval/pending", None),
        ("POST", "/api/approval/approve/candidate-1", None),
        ("POST", "/api/approval/reject/candidate-1", None),
        ("GET", "/api/subscribers", None),
        ("DELETE", "/api/subscribers/12345", None),
        ("GET", "/api/invite-codes", None),
        ("POST", "/api/invite-codes/generate", {"days_valid": 30, "label": "test"}),
        ("DELETE", "/api/invite-codes/ATLAX-TEST", None),
    ],
)
def test_regular_user_cannot_access_admin_routes(authed_client, method, path, body):
    client, _ = authed_client
    _login(client, "analyst", "analystpass")

    response = client.request(
        method,
        path,
        json=body,
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "Admin privileges required"


def test_admin_can_access_admin_routes(authed_client):
    client, _ = authed_client
    _login(client, "admin", "adminpass")

    me_response = client.get("/api/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["user"]["username"] == "admin"

    response = client.get("/api/config")

    assert response.status_code == 200
    assert "yaml" in response.json()


def test_admin_operations_view_is_protected_and_secret_free(authed_client, monkeypatch):
    client, _ = authed_client

    class Runtime:
        def get(self, key, default=None):
            return {
                "operations.snapshot": {
                    "observed_at": "2026-09-20T08:00:00+00:00",
                    "account_mode": {
                        "mode": "AUTO_ENFORCED",
                        "status": "VERIFIED",
                    },
                    "health": {"mt5": "CONNECTED"},
                    "account": {"login": 123, "server": "Broker"},
                },
                "operations.watchdog": {
                    "status": "HEALTHY",
                    "components": {"mt5_ipc": {"status": "HEALTHY"}},
                },
            }.get(key, default)

    class Journal:
        def list_execution_quality_events(self, limit=20):
            return [{
                "event_id": "evt-1",
                "event_type": "EXECUTION_RESULT",
                "symbol": "EURUSD",
                "status": "FILLED",
                "metadata": {"token": "must-not-leak"},
            }]

    class Audit:
        def recent(self, limit=20):
            return [{"timestamp": "2026-09-20T08:00:00+00:00", "action": "view"}]

    monkeypatch.setattr(server, "get_runtime_state", lambda: Runtime())
    monkeypatch.setattr(server, "get_journal_store", lambda path: Journal())
    monkeypatch.setattr(server, "get_audit_log", lambda: Audit())

    assert client.get("/api/admin/operations").status_code == 401
    _login(client, "admin", "adminpass")
    response = client.get("/api/admin/operations")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"]["label"] == "Auto-Enforced"
    assert payload["execution_quality"][0]["symbol"] == "EURUSD"
    assert "metadata" not in payload["execution_quality"][0]
    assert "must-not-leak" not in response.text


def test_admin_overview_is_protected_and_bounded(authed_client, monkeypatch):
    client, _ = authed_client
    monkeypatch.setattr(
        server,
        "_build_admin_overview",
        lambda: {
            "schema_version": 1,
            "metrics": {"total_trades": 0},
            "billing": {"recent": []},
            "operations": {"execution_quality": []},
        },
    )

    assert client.get("/api/admin/overview").status_code == 401
    _login(client, "admin", "adminpass")
    response = client.get("/api/admin/overview")

    assert response.status_code == 200
    assert response.json()["schema_version"] == 1
    assert "submitted_tx_reference" not in response.text
    assert "payment_destination" not in response.text


def test_admin_sees_approval_storage_failure(authed_client):
    client, _ = authed_client
    _login(client, "admin", "adminpass")

    class FailingApprovalEngine:
        def get_all_pending(self, profile=None):
            raise RuntimeError("approval store offline")

    server.approval_engine = FailingApprovalEngine()
    response = client.get("/api/approval/pending")

    assert response.status_code == 503
    assert response.json()["detail"] == "Approval queue is temporarily unavailable"


def test_config_update_rejects_secret_fields(authed_client):
    client, _ = authed_client
    _login(client, "admin", "adminpass")

    response = client.post(
        "/api/config",
        json={
            "yaml_content": (
                "dashboard:\n"
                "  enabled: true\n"
                "  jwt_secret: should-not-be-here\n"
            )
        },
    )

    assert response.status_code == 400
    assert "environment variables only" in response.json()["detail"]
