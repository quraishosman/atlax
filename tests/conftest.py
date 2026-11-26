from __future__ import annotations

import os


_TEST_SECRET_ENV = {
    "ATLAX_TELEGRAM_BOT_TOKEN": "test-telegram-bot-token",
    "ATLAX_TELEGRAM_CHAT_ID": "123456789",
    "ATLAX_DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/test/test",
    "ATLAX_AI_API_KEY": "test-ai-api-key-012345678901234567890123456789",
    "ATLAX_AI_FALLBACK_API_KEY": "test-ai-fallback-api-key-012345678901234567890123456789",
    "ATLAX_DASHBOARD_JWT_SECRET": "test-dashboard-jwt-secret-012345678901234567890123456789",
    "ATLAX_WEBHOOK_SECRET": "test-webhook-secret-012345678901234567890123456789",
}


for key, value in _TEST_SECRET_ENV.items():
    os.environ.setdefault(key, value)
