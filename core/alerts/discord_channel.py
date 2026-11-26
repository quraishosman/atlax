"""
Discord Alert Channel for ATLAX.

Sends formatted ATLAX alerts to a Discord Webhook as rich embeds.
"""

from __future__ import annotations

import logging
import urllib.request
import urllib.error
import json
from datetime import datetime, timezone
from typing import Dict, Any

from core.models.alert_event import AlertEvent
from core.alerts.base import BaseAlertChannel

logger = logging.getLogger("atlax.alerts.discord")


class DiscordChannel(BaseAlertChannel):
    """
    Delivers ATLAX alerts to a Discord Webhook.
    """

    def __init__(self, webhook_url: str):
        """
        Initialize the Discord channel.
        
        Args:
            webhook_url: The Discord Webhook URL.
        """
        self.webhook_url = webhook_url
        self._is_enabled = bool(webhook_url and webhook_url.strip())

    @property
    def name(self) -> str:
        return "discord"

    @property
    def is_enabled(self) -> bool:
        return self._is_enabled

    def send(self, alert: AlertEvent) -> bool:
        """
        Send the alert to Discord.
        """
        if not self.is_enabled:
            return False

        payload = self._format_discord_embed(alert)
        
        try:
            req = urllib.request.Request(
                self.webhook_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "ATLAX-AlertEngine/1.0"
                }
            )
            with urllib.request.urlopen(req, timeout=10.0) as response:
                if response.status in (200, 204):
                    logger.info(f"DiscordChannel: Successfully sent alert {alert.alert_id}")
                    return True
                else:
                    logger.error(f"DiscordChannel: Unexpected status code {response.status}")
                    return False
                    
        except urllib.error.URLError as e:
            logger.error(f"DiscordChannel: Failed to connect to Discord webhook - {e}")
            return False
        except Exception as e:
            logger.error(f"DiscordChannel: Unexpected error sending alert - {e}")
            return False

    def _format_discord_embed(self, alert: AlertEvent) -> Dict[str, Any]:
        """
        Format the ATLAX AlertEvent into a Discord rich embed.
        """
        color = 0x00FF00 if alert.direction == "BUY" else 0xFF0000
        
        # Build confidence breakdown string
        breakdown_str = "\\n".join([f"• **{k}**: {v}" for k, v in alert.confidence_breakdown.items()])
        if not breakdown_str:
            breakdown_str = "No breakdown available."
            
        missing_str = ", ".join(alert.missing_factors) if alert.missing_factors else "None"
        
        embed = {
            "title": f"🚨 ATLAX ALERT | {alert.direction} {alert.symbol} {alert.timeframe}",
            "description": alert.explanation,
            "color": color,
            "fields": [
                {
                    "name": "📊 Profile & Context",
                    "value": f"**Profile:** {alert.profile}\\n**Pattern:** {alert.classification}\\n**Score:** {alert.confidence_score:.1f}/100",
                    "inline": False
                },
                {
                    "name": "🎯 Entry Zone",
                    "value": f"High: `{alert.entry_zone_high}`\\nLow: `{alert.entry_zone_low}`",
                    "inline": True
                },
                {
                    "name": "🛑 Invalid / TP",
                    "value": f"SL Ref: `{alert.invalidation_level}`\\nTP: `{alert.midpoint_target}`",
                    "inline": True
                },
                {
                    "name": "🔍 Confidence Breakdown",
                    "value": breakdown_str,
                    "inline": False
                },
                {
                    "name": "⚠️ Missing Factors",
                    "value": missing_str,
                    "inline": False
                }
            ],
            "footer": {
                "text": f"ATLAX Execution Engine | ID: {alert.candidate_id[-8:]}"
            },
            "timestamp": alert.created_at.isoformat() if alert.created_at else datetime.now(timezone.utc).isoformat()
        }

        return {
            "username": "ATLAX OS",
            "embeds": [embed]
        }
