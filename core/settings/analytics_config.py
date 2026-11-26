"""
Analytics Configuration.
Authority: docs/13_CONFIGURATION.md
"""
import os

import yaml
from dataclasses import dataclass


@dataclass(frozen=True)
class AnalyticsConfig:
    enabled: bool = True
    db_path: str = "data/journal.sqlite"
    # Daily report / Core AI
    ai_advisory_enabled: bool = False
    ai_provider: str = "gemini"
    ai_model: str = "gemini-3.5-flash"
    ai_api_key: str = ""
    ai_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    # Fallback AI (Hybrid redundancy)
    ai_fallback_provider: str = "none"
    ai_fallback_model: str = ""
    ai_fallback_api_key: str = ""
    ai_fallback_base_url: str = ""
    
    ai_report_utc_hour: int = 23
    ai_report_utc_minute: int = 55
    # Weekly optimizer
    ai_weekly_report_enabled: bool = True
    ai_weekly_report_utc_day: int = 6    # 0=Monday … 6=Sunday
    ai_weekly_report_utc_hour: int = 22
    # Anomaly detection
    ai_anomaly_detection_enabled: bool = True
    ai_anomaly_win_rate_drop_threshold: float = 20.0  # percentage points below baseline
    ai_anomaly_min_trades: int = 5                    # min trades today before anomaly fires
    ai_performance_min_trades: int = 10                # minimum sample for standalone review
    ai_prop_firm_daily_limit: int = 10
    ai_prop_firm_cooldown_seconds: int = 10
    # Subscriber-facing AI remains advisory and is independently controlled.
    ai_subscriber_enabled: bool = False
    ai_subscriber_daily_limit: int = 10
    ai_subscriber_per_minute_limit: int = 3
    ai_subscriber_cooldown_seconds: int = 10
    ai_subscriber_active_timeout_seconds: int = 120
    ai_subscriber_abuse_strike_limit: int = 3
    ai_subscriber_max_question_chars: int = 600
    heatmap_path: str = "data/heatmap.json"
    faq_path: str = "docs/20_PUBLIC_FAQ.md"
    google_sheets_webhook_url: str = ""
    replay_enabled: bool = True
    replay_context_bars_before_parent: int = 30
    replay_outcome_forward_bars: int = 50
    replay_render_timeout_seconds: int = 5
    replay_image_width: int = 1600
    replay_image_height: int = 900
    replay_image_max_bytes: int = 2 * 1024 * 1024
    replay_cache_dir: str = "data/replay_cache"
    replay_renderer_version: str = "1.0"
    replay_request_cooldown_seconds: int = 10


def load_analytics_config(path: str) -> AnalyticsConfig:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not data or "analytics" not in data:
        raise ValueError("Missing 'analytics' configuration section.")

    sec = data["analytics"]
    enabled       = bool(sec.get("enabled", True))
    db_path       = str(sec.get("db_path", "data/journal.sqlite"))
    ai_enabled    = bool(sec.get("ai_advisory_enabled", False))
    ai_provider   = str(sec.get("ai_provider", "gemini"))
    ai_model      = str(sec.get("ai_model", "gemini-3.5-flash"))
    ai_api_key    = os.environ.get("ATLAX_AI_API_KEY", "").strip()
    ai_base_url   = str(sec.get("ai_base_url", "https://generativelanguage.googleapis.com/v1beta/openai/"))
    
    ai_fallback_provider = str(sec.get("ai_fallback_provider", "none"))
    ai_fallback_model    = str(sec.get("ai_fallback_model", ""))
    ai_fallback_api_key  = os.environ.get("ATLAX_AI_FALLBACK_API_KEY", "").strip()
    ai_fallback_base_url = str(sec.get("ai_fallback_base_url", ""))
    
    ai_report_utc_hour   = int(sec.get("ai_report_utc_hour", 23))
    ai_report_utc_minute = int(sec.get("ai_report_utc_minute", 55))

    ai_weekly_enabled  = bool(sec.get("ai_weekly_report_enabled", True))
    ai_weekly_day      = int(sec.get("ai_weekly_report_utc_day", 6))   # Sunday
    ai_weekly_hour     = int(sec.get("ai_weekly_report_utc_hour", 22))

    ai_anomaly_enabled   = bool(sec.get("ai_anomaly_detection_enabled", True))
    ai_anomaly_threshold = float(sec.get("ai_anomaly_win_rate_drop_threshold", 20.0))
    ai_anomaly_min       = int(sec.get("ai_anomaly_min_trades", 5))
    ai_performance_min   = int(sec.get("ai_performance_min_trades", 10))
    ai_prop_firm_daily_limit = int(sec.get("ai_prop_firm_daily_limit", 10))
    ai_prop_firm_cooldown_seconds = int(sec.get("ai_prop_firm_cooldown_seconds", 10))
    ai_subscriber_enabled = bool(sec.get("ai_subscriber_enabled", False))
    ai_subscriber_daily_limit = int(sec.get("ai_subscriber_daily_limit", 10))
    ai_subscriber_per_minute_limit = int(sec.get("ai_subscriber_per_minute_limit", 3))
    ai_subscriber_cooldown_seconds = int(sec.get("ai_subscriber_cooldown_seconds", 10))
    ai_subscriber_active_timeout_seconds = int(sec.get("ai_subscriber_active_timeout_seconds", 120))
    ai_subscriber_abuse_strike_limit = int(sec.get("ai_subscriber_abuse_strike_limit", 3))
    ai_subscriber_max_question_chars = int(sec.get("ai_subscriber_max_question_chars", 600))
    heatmap_path = str(sec.get("heatmap_path", "data/heatmap.json")).strip()
    faq_path = str(sec.get("faq_path", "docs/20_PUBLIC_FAQ.md")).strip()
    replay_enabled = bool(sec.get("replay_enabled", True))
    replay_context_bars_before_parent = int(
        sec.get("replay_context_bars_before_parent", 30)
    )
    replay_outcome_forward_bars = int(
        sec.get("replay_outcome_forward_bars", 50)
    )
    replay_render_timeout_seconds = int(sec.get("replay_render_timeout_seconds", 5))
    replay_image_width = int(sec.get("replay_image_width", 1600))
    replay_image_height = int(sec.get("replay_image_height", 900))
    replay_image_max_bytes = int(
        sec.get("replay_image_max_bytes", 2 * 1024 * 1024)
    )
    replay_cache_dir = str(sec.get("replay_cache_dir", "data/replay_cache")).strip()
    replay_renderer_version = str(sec.get("replay_renderer_version", "1.0")).strip()
    replay_request_cooldown_seconds = int(
        sec.get("replay_request_cooldown_seconds", 10)
    )
    # Apps Script endpoints are deployment secrets/configuration, never YAML
    # values. An empty environment value is a deliberate fail-closed disable.
    google_sheets_webhook_url = os.environ.get(
        "ATLAX_GOOGLE_SHEETS_WEBHOOK_URL", ""
    ).strip()

    # Validation
    if not db_path.endswith(".sqlite") and not db_path.endswith(".db"):
        raise ValueError("db_path must be a SQLite file ending in .sqlite or .db")

    if ai_enabled and ai_provider not in ("deepseek", "openai", "ollama", "gemini"):
        raise ValueError(f"Unsupported ai_provider: {ai_provider}. Use deepseek, openai, ollama, or gemini.")

    if not (0 <= ai_report_utc_hour <= 23):
        raise ValueError("ai_report_utc_hour must be 0-23")
    if not (0 <= ai_report_utc_minute <= 59):
        raise ValueError("ai_report_utc_minute must be 0-59")
    if not (0 <= ai_weekly_day <= 6):
        raise ValueError("ai_weekly_report_utc_day must be 0-6 (Monday-Sunday)")
    if not (0 <= ai_weekly_hour <= 23):
        raise ValueError("ai_weekly_report_utc_hour must be 0-23")
    if ai_anomaly_threshold <= 0:
        raise ValueError("ai_anomaly_win_rate_drop_threshold must be > 0")
    if ai_anomaly_min < 1:
        raise ValueError("ai_anomaly_min_trades must be >= 1")
    if ai_performance_min < 1:
        raise ValueError("ai_performance_min_trades must be >= 1")
    if ai_prop_firm_daily_limit < 1:
        raise ValueError("ai_prop_firm_daily_limit must be >= 1")
    if ai_prop_firm_cooldown_seconds < 0:
        raise ValueError("ai_prop_firm_cooldown_seconds must be >= 0")
    if ai_subscriber_daily_limit < 1:
        raise ValueError("ai_subscriber_daily_limit must be >= 1")
    if ai_subscriber_per_minute_limit < 1:
        raise ValueError("ai_subscriber_per_minute_limit must be >= 1")
    if ai_subscriber_cooldown_seconds < 0:
        raise ValueError("ai_subscriber_cooldown_seconds must be >= 0")
    if ai_subscriber_active_timeout_seconds < 1:
        raise ValueError("ai_subscriber_active_timeout_seconds must be >= 1")
    if ai_subscriber_abuse_strike_limit < 1:
        raise ValueError("ai_subscriber_abuse_strike_limit must be >= 1")
    if ai_subscriber_max_question_chars < 1:
        raise ValueError("ai_subscriber_max_question_chars must be >= 1")
    if not heatmap_path:
        raise ValueError("heatmap_path must not be empty")
    if not faq_path:
        raise ValueError("faq_path must not be empty")
    if replay_context_bars_before_parent < 0:
        raise ValueError("replay_context_bars_before_parent must be >= 0")
    if replay_outcome_forward_bars < 0:
        raise ValueError("replay_outcome_forward_bars must be >= 0")
    if replay_render_timeout_seconds < 1:
        raise ValueError("replay_render_timeout_seconds must be >= 1")
    if replay_image_width < 640 or replay_image_width > 2400:
        raise ValueError("replay_image_width must be between 640 and 2400")
    if replay_image_height < 360 or replay_image_height > 1600:
        raise ValueError("replay_image_height must be between 360 and 1600")
    if replay_image_max_bytes < 100_000:
        raise ValueError("replay_image_max_bytes must be >= 100000")
    if not replay_cache_dir:
        raise ValueError("replay_cache_dir must not be empty")
    if not replay_renderer_version:
        raise ValueError("replay_renderer_version must not be empty")
    if replay_request_cooldown_seconds < 0:
        raise ValueError("replay_request_cooldown_seconds must be >= 0")

    return AnalyticsConfig(
        enabled=enabled,
        db_path=db_path,
        ai_advisory_enabled=ai_enabled,
        ai_provider=ai_provider,
        ai_model=ai_model,
        ai_api_key=ai_api_key,
        ai_base_url=ai_base_url,
        ai_fallback_provider=ai_fallback_provider,
        ai_fallback_model=ai_fallback_model,
        ai_fallback_api_key=ai_fallback_api_key,
        ai_fallback_base_url=ai_fallback_base_url,
        ai_report_utc_hour=ai_report_utc_hour,
        ai_report_utc_minute=ai_report_utc_minute,
        ai_weekly_report_enabled=ai_weekly_enabled,
        ai_weekly_report_utc_day=ai_weekly_day,
        ai_weekly_report_utc_hour=ai_weekly_hour,
        ai_anomaly_detection_enabled=ai_anomaly_enabled,
        ai_anomaly_win_rate_drop_threshold=ai_anomaly_threshold,
        ai_anomaly_min_trades=ai_anomaly_min,
        ai_performance_min_trades=ai_performance_min,
        ai_prop_firm_daily_limit=ai_prop_firm_daily_limit,
        ai_prop_firm_cooldown_seconds=ai_prop_firm_cooldown_seconds,
        ai_subscriber_enabled=ai_subscriber_enabled,
        ai_subscriber_daily_limit=ai_subscriber_daily_limit,
        ai_subscriber_per_minute_limit=ai_subscriber_per_minute_limit,
        ai_subscriber_cooldown_seconds=ai_subscriber_cooldown_seconds,
        ai_subscriber_active_timeout_seconds=ai_subscriber_active_timeout_seconds,
        ai_subscriber_abuse_strike_limit=ai_subscriber_abuse_strike_limit,
        ai_subscriber_max_question_chars=ai_subscriber_max_question_chars,
        heatmap_path=heatmap_path,
        faq_path=faq_path,
        google_sheets_webhook_url=google_sheets_webhook_url,
        replay_enabled=replay_enabled,
        replay_context_bars_before_parent=replay_context_bars_before_parent,
        replay_outcome_forward_bars=replay_outcome_forward_bars,
        replay_render_timeout_seconds=replay_render_timeout_seconds,
        replay_image_width=replay_image_width,
        replay_image_height=replay_image_height,
        replay_image_max_bytes=replay_image_max_bytes,
        replay_cache_dir=replay_cache_dir,
        replay_renderer_version=replay_renderer_version,
        replay_request_cooldown_seconds=replay_request_cooldown_seconds,
    )
