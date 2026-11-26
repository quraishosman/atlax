import json
from types import SimpleNamespace

from core.alerts.telegram_channel import TelegramAlertChannel


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps({"ok": True, "result": {"message_id": 77}}).encode()


def test_send_signal_replay_uses_multipart_photo(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _Response()

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    config = SimpleNamespace(
        enabled=True,
        bot_token="token-not-logged",
        chat_id="123",
        parse_mode="HTML",
        timeout_seconds=4,
    )
    channel = TelegramAlertChannel(config)

    assert channel.send_signal_replay("987", b"\x89PNG\r\n\x1a\nimage", "caption") is True
    request, timeout = requests[0]
    body = request.data
    assert timeout == 4
    assert request.full_url.endswith("/sendPhoto")
    assert b'name="chat_id"' in body
    assert b'name="caption"' in body
    assert b'name="photo"' in body
    assert b"image" in body
    assert b"token-not-logged" not in body
