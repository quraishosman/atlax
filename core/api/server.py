"""
ATLAX API Server.
"""
import hashlib
import hmac
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Depends, Request, Response
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
import jwt
import yaml

from core.analytics.metrics import MetricsCalculator
from core.analytics.journal import SQLiteJournal
from core.settings.approval_config import ApprovalConfig
from core.api.webhooks import router as webhooks_router
from core.api.auth_db import AuthDB
from core.storage.postgres import initialize_storage
from core.storage.factory import (
    get_approval_engine,
    get_audit_log,
    get_journal as get_journal_store,
    get_payment_store,
    get_runtime_state,
    get_subscriber_db,
)
from core.operations.admin_view import build_admin_operations_view
from pydantic import BaseModel

logger = logging.getLogger("atlax.api")

# Initialize Database
initialize_storage()
auth_db = AuthDB()

# Load JWT config
with open("config/atlax.yaml", "r") as f:
    config_data = yaml.safe_load(f)
JWT_SECRET = os.environ.get("ATLAX_DASHBOARD_JWT_SECRET", "").strip()
if not JWT_SECRET:
    raise RuntimeError(
        "ATLAX_DASHBOARD_JWT_SECRET is required. "
        "Dashboard JWT secrets must not be stored in config/atlax.yaml."
    )
ALGORITHM = "HS256"
AUTH_COOKIE_NAME = "atlax_access_token"
AUTH_COOKIE_MAX_AGE_SECONDS = 24 * 60 * 60
ATLAX_ENV = os.environ.get("ATLAX_ENV", "development").strip().lower()
_default_cookie_secure = "false" if ATLAX_ENV in {"development", "dev", "test", "local"} else "true"
AUTH_COOKIE_SECURE = os.environ.get("ATLAX_AUTH_COOKIE_SECURE", _default_cookie_secure).strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
if ATLAX_ENV in {"production", "prod", "staging"} and not AUTH_COOKIE_SECURE:
    raise RuntimeError("ATLAX_AUTH_COOKIE_SECURE must be true outside local development")
AUTH_COOKIE_SAMESITE = os.environ.get("ATLAX_AUTH_COOKIE_SAMESITE", "lax").strip().lower()
if AUTH_COOKIE_SAMESITE not in {"lax", "strict", "none"}:
    raise RuntimeError("ATLAX_AUTH_COOKIE_SAMESITE must be one of: lax, strict, none")
if AUTH_COOKIE_SAMESITE == "none" and not AUTH_COOKIE_SECURE:
    raise RuntimeError("SameSite=None cookies require ATLAX_AUTH_COOKIE_SECURE=true")

MINI_APP_COOKIE_NAME = "atlax_miniapp_session"
MINI_APP_SESSION_MAX_AGE_SECONDS = 15 * 60
try:
    MINI_APP_INIT_DATA_MAX_AGE_SECONDS = int(
        os.environ.get("ATLAX_TELEGRAM_INIT_DATA_MAX_AGE_SECONDS", "900")
    )
except ValueError as exc:
    raise RuntimeError("ATLAX_TELEGRAM_INIT_DATA_MAX_AGE_SECONDS must be an integer") from exc
if MINI_APP_INIT_DATA_MAX_AGE_SECONDS <= 0:
    raise RuntimeError("ATLAX_TELEGRAM_INIT_DATA_MAX_AGE_SECONDS must be positive")

_DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000,"
    "http://127.0.0.1:3000,"
    "http://localhost:5173,"
    "http://127.0.0.1:5173,"
    "http://localhost:4173,"
    "http://127.0.0.1:4173"
)

def _load_cors_origins() -> list[str]:
    raw = os.environ.get("ATLAX_CORS_ORIGINS", _DEFAULT_CORS_ORIGINS)
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    if not origins:
        raise RuntimeError("ATLAX_CORS_ORIGINS must contain at least one origin")
    if any(origin == "*" for origin in origins):
        raise RuntimeError("ATLAX_CORS_ORIGINS must not include wildcard origins")
    return origins

def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        max_age=AUTH_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
        path="/",
    )

def _clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(key=AUTH_COOKIE_NAME, path="/")


def _set_miniapp_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=MINI_APP_COOKIE_NAME,
        value=token,
        max_age=MINI_APP_SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
        path="/",
    )

class SignupRequest(BaseModel):
    username: str
    password: str
    
class ConfigUpdateRequest(BaseModel):
    yaml_content: str


class MiniAppBootstrapRequest(BaseModel):
    init_data: str


class MiniAppPreferencesRequest(BaseModel):
    style: Optional[str] = None
    symbols: Optional[list[str]] = None
    min_confidence: Optional[int] = None
    notifications: Optional[str] = None

# Load approval config
approval_cfg = config_data.get("approval", {})
app_config = ApprovalConfig(
    require_manual=approval_cfg.get("require_manual", True),
    auto_reject_after_seconds=approval_cfg.get("auto_reject_after_seconds", 300)
)
approval_engine = get_approval_engine(app_config)

app = FastAPI(title="ATLAX Dashboard API")
app.include_router(webhooks_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_load_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Accept", "Authorization", "Content-Type", "X-Requested-With"],
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

def _extract_token(
    request: Request,
    bearer_token: Optional[str],
) -> Optional[str]:
    if bearer_token:
        return bearer_token
    cookie_token = request.cookies.get(AUTH_COOKIE_NAME, "").strip()
    return cookie_token or None


def verify_token(
    request: Request,
    bearer_token: Optional[str] = Depends(oauth2_scheme),
):
    try:
        token = _extract_token(request, bearer_token)
        if not token:
            raise HTTPException(status_code=401, detail="Not authenticated")
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[ALGORITHM],
            issuer="atlax-dashboard",
        )
        subject = payload.get("sub")
        if not subject or payload.get("typ") != "dashboard":
            raise HTTPException(status_code=401, detail="Invalid token")
        return subject
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def _validate_telegram_init_data(raw_init_data: str) -> dict[str, Any]:
    """Validate Telegram's signed Mini App payload before trusting its user id."""
    bot_token = os.environ.get("ATLAX_TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        raise HTTPException(status_code=503, detail="Telegram Mini App is not configured")

    if not raw_init_data or len(raw_init_data) > 8192:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data")

    try:
        pairs = parse_qsl(raw_init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid Telegram init data") from exc

    fields: dict[str, str] = {}
    for key, value in pairs:
        if key in fields:
            raise HTTPException(status_code=401, detail="Invalid Telegram init data")
        fields[key] = value

    provided_hash = fields.pop("hash", "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", provided_hash):
        raise HTTPException(status_code=401, detail="Invalid Telegram init data signature")

    try:
        auth_date = int(fields.get("auth_date", ""))
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid Telegram auth date") from exc

    now = int(time.time())
    if auth_date > now + 60 or now - auth_date > MINI_APP_INIT_DATA_MAX_AGE_SECONDS:
        raise HTTPException(status_code=401, detail="Telegram init data has expired")

    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(fields.items())
    )
    secret_key = hmac.new(
        b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256
    ).digest()
    expected_hash = hmac.new(
        secret_key, data_check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected_hash, provided_hash):
        raise HTTPException(status_code=401, detail="Invalid Telegram init data signature")

    try:
        telegram_user = json.loads(fields.get("user", ""))
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=401, detail="Telegram user data is missing") from exc
    if not isinstance(telegram_user, dict) or not telegram_user.get("id"):
        raise HTTPException(status_code=401, detail="Telegram user data is invalid")

    try:
        telegram_id = str(int(telegram_user["id"]))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Telegram user id is invalid") from exc

    return {
        "id": telegram_id,
        "username": str(telegram_user.get("username", ""))[:64],
        "first_name": str(telegram_user.get("first_name", ""))[:128],
        "last_name": str(telegram_user.get("last_name", ""))[:128],
        "language_code": str(telegram_user.get("language_code", ""))[:16],
        "allows_write_to_pm": bool(telegram_user.get("allows_write_to_pm", False)),
    }


def _issue_miniapp_session(telegram_user: dict[str, Any]) -> str:
    now = int(time.time())
    display_name = " ".join(
        part for part in (telegram_user.get("first_name", ""), telegram_user.get("last_name", ""))
        if part
    ).strip()
    return jwt.encode(
        {
            "sub": str(telegram_user["id"]),
            "typ": "miniapp",
            "iss": "atlax-miniapp",
            "iat": now,
            "exp": now + MINI_APP_SESSION_MAX_AGE_SECONDS,
            "username": telegram_user.get("username", ""),
            "display_name": display_name,
        },
        JWT_SECRET,
        algorithm=ALGORITHM,
    )


def verify_miniapp_session(request: Request) -> dict[str, Any]:
    token = request.cookies.get(MINI_APP_COOKIE_NAME, "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Mini App session required")
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[ALGORITHM],
            issuer="atlax-miniapp",
        )
        if payload.get("typ") != "miniapp" or not payload.get("sub"):
            raise HTTPException(status_code=401, detail="Invalid Mini App session")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Mini App session expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid Mini App session")

def require_admin(user: str = Depends(verify_token)):
    user_row = auth_db.get_user(user)
    if not user_row or user_row["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user


_FORBIDDEN_SECRET_PATHS = (
    ("alerts", "telegram", "bot_token"),
    ("alerts", "telegram", "chat_id"),
    ("alerts", "discord", "webhook_url"),
    ("analytics", "ai_api_key"),
    ("analytics", "ai_fallback_api_key"),
    ("analytics", "google_sheets_webhook_url"),
    ("dashboard", "jwt_secret"),
    ("dashboard", "username"),
    ("dashboard", "password"),
    ("security", "webhook_secret"),
    ("market_data", "mt5", "password"),
    ("payments", "crypto", "wallet_address"),
    ("payments", "mpesa", "lipa_number"),
)


def _get_nested_value(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _set_nested_value(data: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current: Any = data
    for key in path[:-1]:
        if not isinstance(current, dict) or key not in current:
            return
        current = current[key]
    if isinstance(current, dict) and path:
        current[path[-1]] = value


def _redact_config(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy safe to send to an administrator-facing API client."""
    redacted = json.loads(json.dumps(data))
    for path in _FORBIDDEN_SECRET_PATHS:
        if _get_nested_value(redacted, path) is not None:
            _set_nested_value(redacted, path, "")
    return redacted


def _contains_forbidden_secret_values(raw_config: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    for path in _FORBIDDEN_SECRET_PATHS:
        value = _get_nested_value(raw_config, path)
        if isinstance(value, str) and value.strip():
            violations.append(".".join(path))
    return violations


def _validate_settings_document(parsed: dict[str, Any]) -> None:
    """Validate a complete YAML document through the existing Settings Engine."""
    if not isinstance(parsed, dict):
        raise ValueError("Configuration root must be a YAML mapping.")

    expected_sections = set(config_data.keys())
    unknown = sorted(set(parsed) - expected_sections)
    missing = sorted(expected_sections - set(parsed))
    if unknown:
        raise ValueError(f"Unknown configuration sections: {', '.join(unknown)}")
    if missing:
        raise ValueError(
            "A complete configuration document is required; missing sections: "
            + ", ".join(missing)
        )

    from core.settings.alert_config import load_alert_config
    from core.settings.analytics_config import load_analytics_config
    from core.settings.approval_config import load_approval_config
    from core.settings.atr_quality_config import load_atr_quality_config
    from core.settings.confidence_config import load_confidence_config
    from core.settings.crt_config import load_crt_config
    from core.settings.execution_config import load_execution_config
    from core.settings.fvg_config import load_fvg_config
    from core.settings.htf_momentum_config import load_htf_momentum_config
    from core.settings.key_level_config import load_key_level_config
    from core.settings.market_data_config import load_market_data_config
    from core.settings.nested_crt_config import load_nested_crt_routing_config
    from core.settings.payment_config import load_payment_config
    from core.settings.profit_protection_config import load_profit_protection_config
    from core.settings.risk_config import load_risk_config
    from core.settings.strategy_config import load_strategy_config

    loader_names = (
        load_alert_config,
        load_analytics_config,
        load_approval_config,
        load_atr_quality_config,
        load_confidence_config,
        load_crt_config,
        load_execution_config,
        load_fvg_config,
        load_htf_momentum_config,
        load_key_level_config,
        load_market_data_config,
        load_nested_crt_routing_config,
        load_payment_config,
        load_profit_protection_config,
        load_risk_config,
        load_strategy_config,
    )
    config_path = Path("config/atlax.yaml")
    temporary_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(config_path.parent),
            prefix=f".{config_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            yaml.safe_dump(parsed, handle, sort_keys=False, allow_unicode=True)
            temporary_path = handle.name
        for loader in loader_names:
            loader(temporary_path)
    finally:
        if temporary_path:
            try:
                Path(temporary_path).unlink(missing_ok=True)
            except OSError:
                logger.warning('{"event":"config_temp_cleanup_failed"}')


def _write_config_atomically(raw_yaml: str) -> None:
    """Write validated config without leaving a secret-bearing backup."""
    config_path = Path("config/atlax.yaml")
    current = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    backup_path = config_path.with_suffix(config_path.suffix + ".bak")
    backup_path.write_text(
        yaml.safe_dump(_redact_config(current), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    temporary_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(config_path.parent),
            prefix=f".{config_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(raw_yaml)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = handle.name
        os.replace(temporary_path, config_path)
        temporary_path = None
    finally:
        if temporary_path:
            Path(temporary_path).unlink(missing_ok=True)

@app.post("/api/auth/login")
def login(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
):
    user = auth_db.get_user(form_data.username)
    if not user or not auth_db.verify_password(form_data.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
        
    expire = datetime.now(timezone.utc) + timedelta(hours=24)
    token = jwt.encode(
        {
            "sub": form_data.username,
            "typ": "dashboard",
            "iss": "atlax-dashboard",
            "iat": datetime.now(timezone.utc),
            "exp": expire,
        },
        JWT_SECRET, 
        algorithm=ALGORITHM
    )
    _set_auth_cookie(response, token)
    return {
        "status": "success",
        "user": {
            "username": user["username"],
            "role": user["role"],
        },
    }

@app.get("/api/auth/me")
def auth_me(user: str = Depends(verify_token)):
    user_row = auth_db.get_user(user)
    if not user_row:
        raise HTTPException(status_code=401, detail="Invalid token")
    return {
        "user": {
            "username": user_row["username"],
            "role": user_row["role"],
        }
    }

@app.post("/api/auth/logout")
def logout(response: Response):
    _clear_auth_cookie(response)
    return {"status": "success"}

@app.post("/api/auth/signup")
def signup(req: SignupRequest):
    # Bootstrap is an atomic database operation; a check-then-create race must
    # never allow two concurrent public requests to become administrators.
    result = auth_db.create_initial_admin(req.username, req.password)
    if result == "created":
        return {"status": "success"}
    if result == "initialized":
        raise HTTPException(status_code=403, detail="System already initialized")
    raise HTTPException(status_code=400, detail="Username already exists")

@app.get("/api/config")
def get_config(admin_user: str = Depends(require_admin)):
    try:
        parsed = yaml.safe_load(Path("config/atlax.yaml").read_text(encoding="utf-8")) or {}
        return {"yaml": yaml.safe_dump(_redact_config(parsed), sort_keys=False, allow_unicode=True)}
    except Exception as e:
        logger.exception('{"event":"config_read_failed"}')
        raise HTTPException(status_code=503, detail="Configuration is temporarily unavailable") from e

@app.post("/api/config")
def update_config(req: ConfigUpdateRequest, admin_user: str = Depends(require_admin)):
    try:
        # Validate that it's actually valid YAML before saving
        parsed = yaml.safe_load(req.yaml_content)
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="Configuration root must be a YAML mapping.")

        forbidden = _contains_forbidden_secret_values(parsed)
        if forbidden:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Secret-bearing fields must be configured via environment variables only: "
                    + ", ".join(forbidden)
                ),
            )

        _validate_settings_document(parsed)
        _write_config_atomically(req.yaml_content)
        get_audit_log().record(
            admin_user,
            "dashboard_config_update",
            result="success",
            metadata={
                "sections": sorted(parsed),
                "content_sha256": hashlib.sha256(req.yaml_content.encode("utf-8")).hexdigest()[:16],
                "restart_required": True,
            },
        )
        return {"status": "success", "restart_required": True}
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML formatting: {e}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/journal")
def get_journal(profile: Optional[str] = None, admin_user: str = Depends(require_admin)):
    try:
        journal = get_journal_store("data/journal.sqlite")
        entries = journal.get_entries()
        if profile and profile != "Global View":
            entries = [e for e in entries if e.profile == profile]
        return [e.__dict__ for e in entries]
    except Exception as e:
        logger.exception('{"event":"journal_read_failed"}')
        raise HTTPException(status_code=503, detail="Journal is temporarily unavailable") from e

@app.get("/api/metrics")
def get_metrics(profile: Optional[str] = None, admin_user: str = Depends(require_admin)):
    try:
        journal = get_journal_store("data/journal.sqlite")
        entries = journal.get_entries()
        if profile and profile != "Global View":
            entries = [e for e in entries if e.profile == profile]
        metrics = MetricsCalculator.calculate(entries)
        return metrics.__dict__
    except Exception as e:
        logger.exception('{"event":"metrics_read_failed"}')
        raise HTTPException(status_code=503, detail="Metrics are temporarily unavailable") from e

@app.get("/api/heatmap")
def get_heatmap(admin_user: str = Depends(require_admin)):
    try:
        return get_runtime_state().get("heatmap.state", {})
    except Exception as e:
        logger.exception('{"event":"heatmap_read_failed"}')
        raise HTTPException(status_code=503, detail="Heatmap is temporarily unavailable") from e


@app.get("/api/admin/operations")
def get_admin_operations(limit: int = 20, admin_user: str = Depends(require_admin)):
    """Return bounded, secret-free operations data for the admin dashboard."""

    if limit < 1 or limit > 50:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 50")
    try:
        view = build_admin_operations_view(
            get_runtime_state(),
            get_journal_store("data/journal.sqlite"),
            get_audit_log(),
            limit=limit,
        )
        return view
    except Exception as exc:  # noqa: BLE001 - admin view must fail closed
        logger.exception('{"event":"admin_operations_read_failed"}')
        raise HTTPException(
            status_code=503,
            detail="Operations data is temporarily unavailable",
        ) from exc


def _safe_payment_view(intent: Any) -> dict[str, Any]:
    """Return payment fields suitable for an authenticated admin dashboard.

    Wallet destinations and submitted payment proofs are deliberately omitted.
    Those values are not needed for an operational summary and should remain in
    the payment workflow's narrower, audited surfaces.
    """

    if not isinstance(intent, dict):
        return {}
    return {
        "reference": str(intent.get("reference", "")),
        "username": str(intent.get("username", "")),
        "method": str(intent.get("method", "")),
        "plan": str(intent.get("subscription_plan", "standard")),
        "amount_usd": str(intent.get("amount_usd", "")),
        "status": str(intent.get("status", "")),
        "created_at": intent.get("created_at"),
        "confirmed_at": intent.get("confirmed_at"),
        "expires_at": intent.get("expires_at"),
    }


def _safe_prop_firm_view(runtime: Any) -> dict[str, Any]:
    """Return the operator-facing prop-firm posture without account fingerprints."""

    control = runtime.get("prop_firm.control", {}) or {}
    snapshot = runtime.get("prop_firm.snapshot", {}) or {}
    if not isinstance(control, dict):
        control = {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    return {
        "enabled": bool(control.get("enabled", False)),
        "paused": bool(control.get("paused", False)),
        "selected_profile_id": control.get("selected_profile_id") or snapshot.get("profile_id"),
        "selected_profile_version": control.get("selected_profile_version") or snapshot.get("profile_version"),
        "status": snapshot.get("status", "UNKNOWN"),
        "login": snapshot.get("login"),
        "server": snapshot.get("server"),
        "currency": snapshot.get("currency"),
        "balance": snapshot.get("balance"),
        "equity": snapshot.get("equity"),
        "remaining_overall_buffer": snapshot.get("remaining_overall_buffer"),
        "remaining_daily_buffer": snapshot.get("remaining_daily_buffer"),
        "recovery_mode": snapshot.get("recovery_mode", "UNKNOWN"),
        "risk_multiplier": snapshot.get("risk_multiplier"),
        "observed_at": snapshot.get("observed_at"),
    }


def _build_admin_overview() -> dict[str, Any]:
    """Build the bounded read-only data contract for the command center."""

    runtime = get_runtime_state()
    journal = get_journal_store("data/journal.sqlite")
    audit = get_audit_log()
    entries = journal.get_entries()
    metrics = MetricsCalculator.calculate(entries).__dict__

    try:
        operations = build_admin_operations_view(runtime, journal, audit, limit=8)
    except Exception:
        logger.exception('{"event":"admin_overview_operations_failed"}')
        operations = {
            "snapshot_available": False,
            "mode": {"label": "Unknown", "status": "UNKNOWN"},
            "watchdog": {"status": "UNKNOWN", "consecutive_failures": 0, "components": {}},
            "circuit_breaker": {"state": "UNKNOWN"},
            "reconciliation": {"status": "UNKNOWN"},
            "execution_quality": [],
            "audit": [],
        }

    subscribers = _sub_db.list_subscribers()
    now = datetime.now(timezone.utc)
    active_subscribers = 0
    expiring_subscribers = 0
    plan_counts = {"standard": 0, "premium": 0}
    for item in subscribers:
        if not isinstance(item, dict):
            continue
        is_active = bool(item.get("active")) and str(item.get("subscription_status", "active")) == "active"
        if is_active:
            active_subscribers += 1
            plan = str(item.get("subscription_plan", "standard")).strip().lower()
            if plan in plan_counts:
                plan_counts[plan] += 1
            expires_at = item.get("subscription_expires_at")
            try:
                expiry = datetime.fromisoformat(str(expires_at)) if expires_at else None
                if expiry and expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if expiry and now <= expiry.astimezone(timezone.utc) <= now + timedelta(days=7):
                    expiring_subscribers += 1
            except (TypeError, ValueError):
                pass

    intents = get_payment_store().list_intents()
    payment_counts = {status: 0 for status in ("awaiting_payment", "submitted", "confirmed", "rejected", "expired", "voided")}
    confirmed_revenue = 0.0
    for intent in intents:
        status = str(intent.get("status", "unknown"))
        if status in payment_counts:
            payment_counts[status] += 1
        if status == "confirmed":
            try:
                confirmed_revenue += float(intent.get("amount_usd", 0) or 0)
            except (TypeError, ValueError):
                pass

    try:
        pending_approvals = len(approval_engine.get_all_pending(None))
    except Exception:
        logger.exception('{"event":"admin_overview_approval_read_failed"}')
        pending_approvals = None

    snapshot = operations.get("snapshot") if isinstance(operations.get("snapshot"), dict) else {}
    account = snapshot.get("account") if isinstance(snapshot.get("account"), dict) else {}
    loss_budget = snapshot.get("loss_budget") if isinstance(snapshot.get("loss_budget"), dict) else {}
    profile = snapshot.get("profile") if isinstance(snapshot.get("profile"), dict) else {}

    return {
        "schema_version": 1,
        "generated_at": now.isoformat(),
        "metrics": metrics,
        "market": {
            "heatmap": runtime.get("heatmap.state", {}) or {},
            "correlation": runtime.get("correlation.state", {}) or {},
            "daily_stats": runtime.get("signal_tracker.daily_stats", {}) or {},
        },
        "approvals": {"pending_count": pending_approvals},
        "prop_firm": _safe_prop_firm_view(runtime),
        "access": {
            "total_subscribers": len(subscribers),
            "active_subscribers": active_subscribers,
            "expiring_within_7_days": expiring_subscribers,
            "plan_counts": plan_counts,
        },
        "billing": {
            "counts": payment_counts,
            "confirmed_revenue_usd": round(confirmed_revenue, 2),
            "recent": [_safe_payment_view(item) for item in intents[:8]],
        },
        "operations": {
            "snapshot_available": bool(operations.get("snapshot_available")),
            "observed_at": operations.get("observed_at"),
            "mode": operations.get("mode", {}),
            "watchdog": operations.get("watchdog", {}),
            "circuit_breaker": operations.get("circuit_breaker", {}),
            "reconciliation": operations.get("reconciliation", {}),
            "account": {
                key: account.get(key)
                for key in ("login", "server", "currency", "balance", "equity", "profit")
                if key in account
            },
            "loss_budget": {
                key: loss_budget.get(key)
                for key in ("remaining_overall_buffer", "remaining_daily_buffer", "status")
                if key in loss_budget
            },
            "profile": {
                key: profile.get(key)
                for key in ("profile_id", "account_type", "binding_status")
                if key in profile
            },
            "execution_quality": operations.get("execution_quality", []),
            "audit": operations.get("audit", []),
        },
    }


@app.get("/api/admin/overview")
def get_admin_overview(admin_user: str = Depends(require_admin)):
    """Return the bounded read-only command-center payload for administrators."""

    try:
        return _build_admin_overview()
    except Exception as exc:  # noqa: BLE001 - dashboard must fail closed
        logger.exception('{"event":"admin_overview_read_failed"}')
        raise HTTPException(
            status_code=503,
            detail="Dashboard overview is temporarily unavailable",
        ) from exc

@app.get("/api/approval/pending")
def get_pending_approvals(profile: Optional[str] = None, admin_user: str = Depends(require_admin)):
    try:
        pending = approval_engine.get_all_pending(profile)
        # Format similar to what the frontend expects
        formatted = []
        for p in pending:
            formatted.append({
                "candidate_id": p.request.candidate.candidate_id,
                "symbol": p.request.candidate.symbol,
                "timeframe": p.request.candidate.timeframe,
                "profile": p.request.candidate.profile,
                "direction": p.request.candidate.direction,
                "lot_size": f"{p.request.risk_evaluation.lot_size:.2f}",
                "entry_price": f"{p.request.candidate.entry_model.reference_price:.5f}",
                "sl_price": f"{p.request.candidate.invalidation_model.invalidation_level:.5f}",
                "tp_price": f"{p.request.candidate.target_model.midpoint_target:.5f}" if p.request.candidate.target_model else "None",
                "status": p.status.value,
                "created_at": p.created_at.isoformat()
            })
        return formatted
    except Exception as e:
        logger.exception('{"event":"approval_pending_read_failed"}')
        raise HTTPException(
            status_code=503,
            detail="Approval queue is temporarily unavailable",
        ) from e

@app.post("/api/approval/approve/{candidate_id}")
def approve_trade(candidate_id: str, admin_user: str = Depends(require_admin)):
    res = approval_engine.approve_trade(candidate_id)
    if res:
        return {"status": "success", "action": "approved", "candidate_id": candidate_id}
    raise HTTPException(status_code=404, detail="Candidate not found or already processed")

@app.post("/api/approval/reject/{candidate_id}")
def reject_trade(candidate_id: str, admin_user: str = Depends(require_admin)):
    res = approval_engine.reject_trade(candidate_id)
    if res:
        return {"status": "success", "action": "rejected", "candidate_id": candidate_id}
    raise HTTPException(status_code=404, detail="Candidate not found or already processed")

# ---- Subscriber & Invite Code Admin Endpoints ----

from core.alerts.subscriber_db import STYLE_TIMEFRAMES

_sub_db = get_subscriber_db()

class GenerateCodeRequest(BaseModel):
    days_valid: int = 30
    label: str = ""


def _miniapp_profile_payload(chat_id: str) -> dict[str, Any]:
    subscriber = _sub_db.get_subscriber(chat_id) or {}
    settings = _sub_db.get_settings(chat_id)
    style = str(subscriber.get("style", "day_trader"))
    timeframes = subscriber.get("timeframes") or STYLE_TIMEFRAMES.get(style, [])
    symbols = settings.get("symbols", [])
    if not isinstance(symbols, list):
        symbols = []
    min_confidence = settings.get("min_confidence")
    if min_confidence is not None:
        try:
            min_confidence = int(min_confidence)
        except (TypeError, ValueError):
            min_confidence = None
    return {
        "style": style,
        "timeframes": [str(item) for item in timeframes],
        "symbols": [str(item) for item in symbols[:12]],
        "min_confidence": min_confidence,
        "notifications": str(settings.get("notifications", "all")),
        "expires_at": subscriber.get("subscription_expires_at"),
        "subscription_source": str(subscriber.get("subscription_source", "")),
        "subscription_status": str(subscriber.get("subscription_status", "inactive")),
        "active": _sub_db.is_registered(chat_id),
    }


def _miniapp_identity(session: dict[str, Any]) -> dict[str, str]:
    return {
        "telegram_id": str(session.get("sub", "")),
        "username": str(session.get("username", "")),
        "display_name": str(session.get("display_name", "")),
    }


def _public_symbol(value: str, *, field_name: str = "symbol") -> str:
    normalized = str(value).strip().upper()
    if not re.fullmatch(r"[A-Z0-9._-]{1,20}", normalized):
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}")
    return normalized


@app.post("/api/miniapp/bootstrap")
def miniapp_bootstrap(req: MiniAppBootstrapRequest, response: Response):
    try:
        telegram_user = _validate_telegram_init_data(req.init_data.strip())
    except HTTPException as exc:
        logger.warning(
            "miniapp_bootstrap_rejected status=%s reason=%s",
            exc.status_code,
            str(exc.detail)[:120],
        )
        raise
    chat_id = str(telegram_user["id"])
    _set_miniapp_cookie(response, _issue_miniapp_session(telegram_user))
    return {
        "status": "success",
        "user": {
            "telegram_id": chat_id,
            "username": telegram_user.get("username", ""),
            "display_name": " ".join(
                part for part in (
                    telegram_user.get("first_name", ""),
                    telegram_user.get("last_name", ""),
                )
                if part
            ).strip(),
        },
        "access_active": _sub_db.is_registered(chat_id),
        "profile": _miniapp_profile_payload(chat_id),
    }


@app.get("/api/miniapp/me")
def miniapp_me(session: dict[str, Any] = Depends(verify_miniapp_session)):
    chat_id = str(session["sub"])
    return {
        "user": _miniapp_identity(session),
        "access_active": _sub_db.is_registered(chat_id),
        "profile": _miniapp_profile_payload(chat_id),
    }


@app.get("/api/miniapp/signals")
def miniapp_signals(
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    min_confidence: Optional[int] = None,
    limit: int = 20,
    session: dict[str, Any] = Depends(verify_miniapp_session),
):
    if limit < 1 or limit > 50:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 50")
    if min_confidence is not None and not 0 <= min_confidence <= 100:
        raise HTTPException(status_code=400, detail="min_confidence must be between 0 and 100")
    normalized_symbol = _public_symbol(symbol) if symbol else None
    normalized_timeframe = _public_symbol(timeframe, field_name="timeframe") if timeframe else None

    chat_id = str(session["sub"])
    access_active = _sub_db.is_registered(chat_id)
    profile = _miniapp_profile_payload(chat_id)
    effective_min_confidence = (
        min_confidence if min_confidence is not None else profile.get("min_confidence")
    )
    if not access_active:
        return {
            "access_active": False,
            "signals": [],
            "analytics": {"active": 0, "tracked_today": 0, "successful_today": 0, "failed_today": 0},
            "filters": {"symbol": normalized_symbol, "timeframe": normalized_timeframe,
                        "min_confidence": effective_min_confidence},
        }

    try:
        from core.analytics.signal_tracker import SignalTracker

        tracker = SignalTracker()
        predictions = tracker.predictions
    except Exception:
        predictions = {}
        tracker = None

    journal_by_id: dict[str, Any] = {}
    if os.path.exists("data/journal.sqlite"):
        try:
            journal_by_id = {
                str(entry.record_id): entry
                for entry in get_journal_store("data/journal.sqlite").get_entries()
                if getattr(entry, "record_id", None)
            }
        except Exception:
            journal_by_id = {}

    visible: list[dict[str, Any]] = []
    for record_id, data in predictions.items():
        delivery_ids = {str(data.get("chat_id", ""))}
        delivery_ids.update(
            str(item.get("chat_id"))
            for item in (data.get("subscriber_deliveries", []) or [])
            if isinstance(item, dict) and item.get("chat_id") is not None
        )
        if chat_id not in delivery_ids:
            continue

        journal_entry = journal_by_id.get(str(record_id))
        confidence = getattr(journal_entry, "confidence_score", None)
        if confidence is not None:
            try:
                confidence = round(float(confidence), 2)
            except (TypeError, ValueError):
                confidence = None
        if effective_min_confidence is not None and (
            confidence is None or confidence < effective_min_confidence
        ):
            continue
        current_symbol = str(data.get("symbol", "")).upper()
        current_timeframe = str(getattr(journal_entry, "timeframe", ""))
        if normalized_symbol and current_symbol != normalized_symbol:
            continue
        if normalized_timeframe and current_timeframe.upper() != normalized_timeframe:
            continue
        try:
            timestamp = datetime.fromtimestamp(float(data.get("timestamp", 0)), timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError):
            timestamp = ""

        visible.append({
            "id": str(record_id),
            "symbol": current_symbol,
            "timeframe": current_timeframe,
            "profile": str(getattr(journal_entry, "profile", "")),
            "direction": str(data.get("direction", "UNKNOWN")),
            "confidence": confidence,
            "entry_price": str(getattr(journal_entry, "entry_price", "")),
            "midpoint": str(data.get("midpoint", "")),
            "target": str(data.get("target", "")),
            "invalidation": str(data.get("sl", "")),
            "timestamp": timestamp,
            "status": "active",
        })

    visible.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
    daily = tracker.get_daily_report() if tracker else {}
    return {
        "access_active": True,
        "signals": visible[:limit],
        "analytics": {
            "active": len(visible),
            "tracked_today": int(daily.get("total", 0) or 0),
            "successful_today": int(daily.get("success", 0) or 0),
            "failed_today": int(daily.get("failed", 0) or 0),
        },
        "filters": {"symbol": normalized_symbol, "timeframe": normalized_timeframe,
                    "min_confidence": effective_min_confidence},
    }


@app.get("/api/miniapp/payments")
def miniapp_payments(session: dict[str, Any] = Depends(verify_miniapp_session)):
    items = []
    for intent in get_payment_store().list_intents(chat_id=str(session["sub"]))[:10]:
        items.append({
            "reference": str(intent.get("reference", "")),
            "method": str(intent.get("method", "")),
            "amount_usd": str(intent.get("amount_usd", "")),
            "status": str(intent.get("status", "")),
            "created_at": intent.get("created_at"),
            "updated_at": intent.get("updated_at"),
            "expires_at": intent.get("expires_at"),
            "status_history": [
                {"status": str(event.get("status", "")), "at": event.get("at")}
                for event in (intent.get("status_history", []) or [])
                if isinstance(event, dict)
            ],
        })
    return {"payments": items}


@app.patch("/api/miniapp/preferences")
def miniapp_preferences(
    req: MiniAppPreferencesRequest,
    session: dict[str, Any] = Depends(verify_miniapp_session),
):
    from core.alerts.subscriber_db import STYLE_TIMEFRAMES

    chat_id = str(session["sub"])
    if not _sub_db.is_registered(chat_id):
        raise HTTPException(status_code=403, detail="Active subscription required")

    fields_set = getattr(req, "model_fields_set", getattr(req, "__fields_set__", set()))
    changed: dict[str, Any] = {}
    if "style" in fields_set and req.style is not None:
        style = req.style.strip().lower()
        if style not in STYLE_TIMEFRAMES:
            raise HTTPException(status_code=400, detail="Invalid trading style")
        if not _sub_db.update_style(chat_id, style):
            raise HTTPException(status_code=404, detail="Subscriber profile not found")
        changed["style"] = style
    if "symbols" in fields_set:
        symbols = req.symbols or []
        if not isinstance(symbols, list) or len(symbols) > 12:
            raise HTTPException(status_code=400, detail="Up to 12 symbols are supported")
        normalized_symbols = [_public_symbol(item, field_name="symbol") for item in symbols]
        if not _sub_db.update_settings(chat_id, "symbols", normalized_symbols):
            raise HTTPException(status_code=404, detail="Subscriber profile not found")
        changed["symbols"] = normalized_symbols
    if "min_confidence" in fields_set:
        if req.min_confidence is not None and not 0 <= req.min_confidence <= 100:
            raise HTTPException(status_code=400, detail="min_confidence must be between 0 and 100")
        if not _sub_db.update_settings(chat_id, "min_confidence", req.min_confidence):
            raise HTTPException(status_code=404, detail="Subscriber profile not found")
        changed["min_confidence"] = req.min_confidence
    if "notifications" in fields_set and req.notifications is not None:
        notifications = req.notifications.strip().lower()
        if notifications not in {"all", "signals_only", "minimal"}:
            raise HTTPException(status_code=400, detail="Invalid notification mode")
        if not _sub_db.update_settings(chat_id, "notifications", notifications):
            raise HTTPException(status_code=404, detail="Subscriber profile not found")
        changed["notifications"] = notifications

    get_audit_log().record(
        chat_id,
        "miniapp_preferences_update",
        target_chat_id=chat_id,
        metadata={"changed": changed},
    )
    return {"status": "success", "profile": _miniapp_profile_payload(chat_id)}

@app.get("/api/subscribers")
def list_subscribers(admin_user: str = Depends(require_admin)):
    return _sub_db.list_subscribers()

@app.delete("/api/subscribers/{chat_id}")
def deactivate_subscriber(chat_id: str, admin_user: str = Depends(require_admin)):
    _sub_db.deactivate(chat_id)
    return {"status": "deactivated", "chat_id": chat_id}

@app.get("/api/invite-codes")
def list_codes(admin_user: str = Depends(require_admin)):
    return _sub_db.list_codes()

@app.post("/api/invite-codes/generate")
def generate_code(req: GenerateCodeRequest, admin_user: str = Depends(require_admin)):
    code = _sub_db.generate_code(label=req.label, days_valid=req.days_valid)
    return {"code": code, "days_valid": req.days_valid, "label": req.label}

@app.delete("/api/invite-codes/{code}")
def revoke_code(code: str, admin_user: str = Depends(require_admin)):
    _sub_db.revoke_code(code)
    return {"status": "revoked", "code": code}
