"""
ATLAX Economic Calendar.

Fetches high-impact forex economic events from the ForexFactory unofficial
JSON feed — completely free, no API key required.

Authority: docs/12_ANALYTICS_ENGINE.md (market context research)
           docs/16_SECURITY.md (AI receives sanitized context only)

AI boundary: This module provides read-only advisory context.
It must never influence trade entries, exits, or risk decisions.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("atlax.analytics.economic_calendar")

_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
_HIGH_IMPACT_CURRENCIES = {"USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"}
_TIMEOUT = 8  # seconds


def _fetch_calendar() -> list[dict[str, Any]]:
    """
    Fetch this week's economic events from ForexFactory.
    Returns an empty list on any error — never raises.
    """
    try:
        req = urllib.request.Request(
            _CALENDAR_URL,
            headers={"User-Agent": "ATLAX/1.0 (advisory-only)"},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning(
            '{"event":"calendar_fetch_failed","error":"%s"}', str(exc)[:120]
        )
        return []


def get_events_near_time(
    dt: datetime,
    window_minutes: int = 30,
    currencies: list[str] | None = None,
    min_impact: str = "High",
) -> list[dict[str, Any]]:
    """
    Return economic events within `window_minutes` of the given UTC datetime.

    Args:
        dt:              The UTC datetime to check around (e.g., trade close time).
        window_minutes:  How many minutes either side of `dt` to search.
        currencies:      Filter to specific currency codes (e.g. ["GBP", "USD"]).
                         Defaults to all major pairs.
        min_impact:      Minimum impact level. "High" (default) or "Medium".

    Returns:
        List of sanitized event dicts:
        {
            "title": str,
            "currency": str,
            "impact": str,
            "event_time_utc": str,
            "minutes_from_trade": int,
        }

    Never raises. Returns empty list if calendar unavailable.
    """
    events = _fetch_calendar()
    if not events:
        return []

    currencies = currencies or list(_HIGH_IMPACT_CURRENCIES)
    currencies_upper = {c.upper() for c in currencies}

    impact_levels = {"High"}
    if min_impact == "Medium":
        impact_levels.add("Medium")

    window = timedelta(minutes=window_minutes)
    results: list[dict[str, Any]] = []

    for ev in events:
        ev_currency = (ev.get("country") or ev.get("currency") or "").upper()
        ev_impact   = ev.get("impact") or ""
        ev_date_str = ev.get("date") or ""
        ev_time_str = ev.get("time") or ""

        if ev_currency not in currencies_upper:
            continue
        if ev_impact not in impact_levels:
            continue

        # Parse the event timestamp (ForexFactory uses "YYYY-MM-DDTHH:MM:SS-05:00" format)
        try:
            if "T" in ev_date_str:
                event_dt_str = ev_date_str
            else:
                event_dt_str = f"{ev_date_str}T{ev_time_str}" if ev_time_str else ev_date_str

            # Handle timezone offset
            if "Z" in event_dt_str or "+" in event_dt_str[10:] or (
                len(event_dt_str) > 19 and event_dt_str[19] == "-"
            ):
                event_dt = datetime.fromisoformat(event_dt_str)
            else:
                # Assume Eastern Time (ForexFactory default) → UTC+5h standard
                event_dt = datetime.fromisoformat(event_dt_str).replace(
                    tzinfo=timezone.utc
                )

            # Normalise to UTC
            if event_dt.tzinfo is not None:
                event_dt = event_dt.astimezone(timezone.utc)

            delta = abs((event_dt - dt).total_seconds() / 60)
            if delta <= window_minutes:
                minutes_from_trade = int((event_dt - dt).total_seconds() / 60)
                results.append({
                    "title": ev.get("title") or ev.get("name") or "Unknown Event",
                    "currency": ev_currency,
                    "impact": ev_impact,
                    "event_time_utc": event_dt.strftime("%H:%M UTC"),
                    "minutes_from_trade": minutes_from_trade,
                })
        except Exception:
            continue

    # Sort closest events first
    results.sort(key=lambda x: abs(x["minutes_from_trade"]))

    logger.info(
        '{"event":"calendar_events_found","near_dt":"%s","count":%d}',
        dt.isoformat(), len(results),
    )
    return results


def get_todays_high_impact_events(currencies: list[str] | None = None) -> list[dict[str, Any]]:
    """
    Return all high-impact events scheduled for today (UTC).
    Used to provide context in the daily report even if no trades were active.
    """
    now_utc = datetime.now(timezone.utc)
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end   = now_utc.replace(hour=23, minute=59, second=59, microsecond=0)

    events = _fetch_calendar()
    currencies_upper = {c.upper() for c in (currencies or list(_HIGH_IMPACT_CURRENCIES))}
    results: list[dict[str, Any]] = []

    for ev in events:
        ev_currency = (ev.get("country") or ev.get("currency") or "").upper()
        ev_impact   = ev.get("impact") or ""
        ev_date_str = ev.get("date") or ""
        ev_time_str = ev.get("time") or ""

        if ev_currency not in currencies_upper or ev_impact != "High":
            continue

        try:
            ev_dt_str = f"{ev_date_str}T{ev_time_str}" if ev_time_str else ev_date_str
            if "Z" in ev_dt_str or "+" in ev_dt_str[10:] or (
                len(ev_dt_str) > 19 and ev_dt_str[19] == "-"
            ):
                event_dt = datetime.fromisoformat(ev_dt_str)
            else:
                event_dt = datetime.fromisoformat(ev_dt_str).replace(tzinfo=timezone.utc)

            if event_dt.tzinfo is not None:
                event_dt = event_dt.astimezone(timezone.utc)

            if today_start <= event_dt <= today_end:
                results.append({
                    "title": ev.get("title") or ev.get("name") or "Unknown Event",
                    "currency": ev_currency,
                    "impact": ev_impact,
                    "event_time_utc": event_dt.strftime("%H:%M UTC"),
                })
        except Exception:
            continue

    return results


def get_upcoming_high_impact_events(
    hours: int = 24,
    currencies: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return sanitized high-impact events scheduled in the next `hours`."""
    now_utc = datetime.now(timezone.utc)
    horizon = now_utc + timedelta(hours=min(max(int(hours), 1), 72))
    allowed = {item.upper() for item in (currencies or _HIGH_IMPACT_CURRENCIES)}
    results: list[dict[str, Any]] = []

    for event in _fetch_calendar():
        currency = str(event.get("country") or event.get("currency") or "").upper()
        if currency not in allowed or str(event.get("impact") or "") != "High":
            continue
        date_value = event.get("date") or ""
        time_value = event.get("time") or ""
        try:
            value = date_value if "T" in date_value else (
                f"{date_value}T{time_value}" if time_value else date_value
            )
            if "T" in value and (
                "+" in value[10:] or "Z" in value or (len(value) > 19 and value[19] == "-")
            ):
                event_dt = datetime.fromisoformat(value)
            else:
                event_dt = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
            event_dt = event_dt.astimezone(timezone.utc) if event_dt.tzinfo else event_dt.replace(tzinfo=timezone.utc)
            if now_utc <= event_dt <= horizon:
                results.append({
                    "title": event.get("title") or event.get("name") or "Unknown Event",
                    "currency": currency,
                    "impact": "High",
                    "event_time_utc": event_dt.strftime("%Y-%m-%d %H:%M UTC"),
                    "minutes_from_now": int((event_dt - now_utc).total_seconds() / 60),
                })
        except Exception:
            continue

    results.sort(key=lambda item: item["minutes_from_now"])
    logger.info('{"event":"upcoming_calendar_events_found","count":%d}', len(results))
    return results[:50]
