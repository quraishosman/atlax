"""
Signal Validation Tracker.

Monitors live market data to grade ATLAX predictions as Target Hit or Failed.
Stores state in a local JSON file to survive restarts. Tracked for 24 hours.
"""

import time
import logging
from typing import Dict, Any
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from core.analytics.google_sheets import log_trade_to_sheets

logger = logging.getLogger("atlax.analytics.tracker")

class SignalTracker:
    def __init__(
        self,
        storage_path: str = "data/active_predictions.json",
        stats_path: str = "data/daily_stats.json",
        db_path: str = "data/journal.sqlite",
        runtime_store=None,
        journal=None,
        subscriber_db=None,
        google_sheets_webhook_url: str = "",
    ):
        self.storage_path = storage_path
        self.stats_path = stats_path
        self.db_path = db_path
        self._google_sheets_webhook_url = google_sheets_webhook_url
        from core.storage.factory import get_runtime_state
        self._runtime_store = runtime_store or get_runtime_state()
        self._journal = journal
        self._subscriber_db = subscriber_db
        self.predictions: Dict[str, Any] = {}
        self.daily_stats: Dict[str, Any] = {"date": "", "total": 0, "success": 0, "failed": 0}
        self._load()

    def _get_journal(self):
        if self._journal is None:
            from core.storage.factory import get_journal
            self._journal = get_journal(self.db_path)
        return self._journal

    def _get_subscriber_db(self):
        if self._subscriber_db is None:
            from core.storage.factory import get_subscriber_db
            self._subscriber_db = get_subscriber_db()
        return self._subscriber_db

    def _load(self):
        predictions = self._runtime_store.get("signal_tracker.predictions", {})
        daily_stats = self._runtime_store.get("signal_tracker.daily_stats", {})
        self.predictions = predictions if isinstance(predictions, dict) else {}
        if isinstance(daily_stats, dict) and daily_stats:
            self.daily_stats = daily_stats

    def _save(self):
        self._runtime_store.set("signal_tracker.predictions", self.predictions)
        self._runtime_store.set("signal_tracker.daily_stats", self.daily_stats)

    @staticmethod
    def _outcome_feedback(
        data: dict[str, Any],
        final_outcome: str,
        exit_reason: str,
        pnl_r: Decimal | None = None,
    ) -> dict[str, Any]:
        """Build the resolved setup metadata sent to the feedback sheet."""
        metadata = dict(data.get("feedback_metadata") or {})
        if not metadata.get("created_at"):
            try:
                metadata["created_at"] = datetime.fromtimestamp(
                    float(data.get("timestamp", time.time())), timezone.utc
                ).isoformat()
            except (TypeError, ValueError, OSError):
                metadata["created_at"] = ""
        metadata.update(
            {
                "resolved_at": datetime.now(timezone.utc).isoformat(),
                "final_outcome": final_outcome,
                "exit_reason": exit_reason,
                "signal_status": "RESOLVED",
            }
        )
        if pnl_r is not None:
            metadata["pnl_r"] = str(pnl_r)
        return metadata

    @staticmethod
    def _calculate_signal_pnl_r(
        data: dict[str, Any],
        exit_price: Decimal,
    ) -> Decimal | None:
        """Calculate deterministic signal performance in risk units.

        This tracker follows alert levels, not a broker position, so it cannot
        claim account-currency PnL.  R is the signed price movement divided by
        the original entry-to-stop distance.  Missing or invalid references
        return ``None`` so an outcome is not guessed.
        """
        try:
            entry = Decimal(str(data.get("entry_price")))
            stop = Decimal(str(data.get("sl")))
            exit_value = Decimal(str(exit_price))
        except (InvalidOperation, TypeError, ValueError):
            return None

        if not all(value.is_finite() for value in (entry, stop, exit_value)):
            return None
        risk_distance = abs(entry - stop)
        if risk_distance <= 0:
            return None

        direction = str(data.get("direction", "")).upper()
        if direction == "BUY":
            movement = exit_value - entry
        elif direction == "SELL":
            movement = entry - exit_value
        else:
            return None
        return (movement / risk_distance).quantize(Decimal("0.0001"))

    @staticmethod
    def _sheet_notes(data: dict[str, Any], default: str) -> str:
        """Keep Phase 2 H1 evidence in the existing lean notes column."""
        metadata = data.get("feedback_metadata") or {}
        momentum = metadata.get("htf_bias")
        relationship = metadata.get("htf_relationship")
        if not momentum and not relationship:
            return default
        return (
            f"{default}; H1 bias={momentum or 'UNKNOWN'}, "
            f"relationship={relationship or 'UNKNOWN'}, "
            f"run={metadata.get('htf_run_length', 'UNKNOWN')}, "
            f"base={metadata.get('htf_base_score', 'UNKNOWN')}, "
            f"proposed={metadata.get('htf_proposed_score', 'UNKNOWN')}, "
            f"adjustment={metadata.get('htf_adjustment', 'UNKNOWN')}"
        )

    def register_prediction(
        self,
        alert_id: str,
        symbol: str,
        timeframe: str,
        direction: str,
        target: float,
        midpoint: float,
        sl: float,
        entry_price: float,
        telegram_message_id: int,
        chat_id: str,
        candle_open_time: float = 0.0,
        subscriber_deliveries: list = None,  # [{"chat_id": ..., "message_id": ...}]
        feedback_metadata: dict[str, Any] | None = None,
    ):
        """Register a new prediction to monitor."""
        self.predictions[alert_id] = {
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": direction,
            "target": target,
            "midpoint": midpoint,
            "sl": sl,
            "entry_price": entry_price,
            "telegram_message_id": telegram_message_id,
            "chat_id": chat_id,
            "subscriber_deliveries": subscriber_deliveries or [],
            "feedback_metadata": dict(feedback_metadata or {}),
            "timestamp": time.time(),
            "candle_open_time": candle_open_time,
            "midpoint_hit": False,
            "target_hit": False,
            "sl_hit": False
        }
        
        current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.daily_stats.get("date") != current_date:
            self.daily_stats = {"date": current_date, "total": 0, "success": 0, "failed": 0}
        self.daily_stats["total"] += 1
        
        self._save()
        logger.info(f"Registered prediction {alert_id} for {symbol} ({direction})")

    def check_market_data(self, symbol: str, current_high: float, current_low: float, current_candle_open: float = 0.0, telegram_channel=None):
        """
        Check all active predictions for the given symbol against the latest candle high/low.
        If a target or SL is breached, send a Telegram reply via telegram_channel.
        """
        now = time.time()
        to_remove = []
        state_changed = False

        for alert_id, data in self.predictions.items():
            # 1. Check Expiration (24 hours = 86400 seconds)
            if now - data["timestamp"] > 86400:
                try:
                    self._get_journal().mark_signal_expired(alert_id)
                except Exception as exc:
                    logger.warning(
                        '{"event":"signal_passport_expiry_write_failed","record_id":"%s","error":"%s"}',
                        alert_id,
                        str(exc)[:120],
                    )
                to_remove.append(alert_id)
                continue

            if data["symbol"] != symbol:
                continue
                
            # Zero-Tick Delay Fix: Prevent checking the EXACT SAME candle that spawned the signal
            if current_candle_open > 0.0 and current_candle_open <= data.get("candle_open_time", 0.0):
                continue

            direction = data["direction"]
            target    = data["target"]
            midpoint  = data.get("midpoint", target)  # fallback for old entries
            sl        = data["sl"]

            hit_target   = False
            hit_midpoint = False
            hit_sl       = False

            if direction == "BUY":
                if current_low <= sl:
                    hit_sl = True
                else:
                    if current_high >= target:
                        hit_target = True
                    elif current_high >= midpoint:
                        hit_midpoint = True
            elif direction == "SELL":
                if current_high >= sl:
                    hit_sl = True
                else:
                    if current_low <= target:
                        hit_target = True
                    elif current_low <= midpoint:
                        hit_midpoint = True

            # Helper: send update to admin + all subscribers (respects notification settings)
            def _broadcast_update(text, msg_id, pred_data, channel, update_type="sl"):
                if not channel:
                    return
                # Only update the admin message when the initial alert reached
                # the admin feed. Subscriber deliveries are handled below.
                if pred_data.get("telegram_message_id"):
                    channel.send_reply(
                        chat_id=pred_data["chat_id"],
                        text=text,
                        message_id=msg_id
                    )
                # Load subscriber DB once to check notification prefs
                try:
                    _sub_db = self._get_subscriber_db()
                except Exception:
                    _sub_db = None

                for sub in pred_data.get("subscriber_deliveries", []):
                    try:
                        # Check notification mode for this subscriber
                        if _sub_db:
                            settings  = _sub_db.get_settings(str(sub["chat_id"]))
                            notif_mode = settings.get("notifications", "all")
                        else:
                            notif_mode = "all"

                        # signals_only: only gets new signals, skip ALL outcome updates
                        if notif_mode == "signals_only":
                            continue
                        # minimal: gets signals + SL only, skip TP1 and TP2
                        if notif_mode == "minimal" and update_type in ("tp1", "tp2"):
                            continue

                        channel.send_reply(
                            chat_id=str(sub["chat_id"]),
                            text=text,
                            message_id=sub.get("message_id")
                        )
                    except Exception:
                        pass

            # --- SL hit: failed prediction, remove immediately ---
            if hit_sl and not data["sl_hit"]:
                from decimal import Decimal
                exit_price = Decimal(str(data["sl"]))
                pnl_r = self._calculate_signal_pnl_r(data, exit_price)
                if pnl_r is None:
                    logger.error(
                        '{"event":"signal_outcome_unknown","record_id":"%s",'
                        '"reason":"invalid_entry_stop_reference","exit_reason":"sl_hit"}',
                        alert_id,
                    )
                    continue
                data["sl_hit"] = True
                state_changed = True
                to_remove.append(alert_id)
                self.daily_stats["failed"] += 1
                logger.info(f"Prediction {alert_id} hit Stop Loss.")
                # Update journal outcome
                try:
                    self._get_journal().update_signal_outcome(
                        record_id=alert_id,
                        exit_reason="sl_hit",
                        exit_price=exit_price,
                        pnl=pnl_r,
                    )
                    try:
                        log_trade_to_sheets(
                            symbol=data["symbol"],
                            timeframe=data.get("timeframe", "UNKNOWN"),
                            entry_price=data.get("entry_price", 0.0),
                            exit_price=data["sl"],
                            sl=data["sl"],
                            tp=data.get("target", 0.0),
                            pips=None,
                            result="LOSS ❌",
                            notes=self._sheet_notes(data, "Signal tracker stop loss hit"),
                            webhook_url=self._google_sheets_webhook_url,
                            metadata=self._outcome_feedback(
                                data, "LOSS", "sl_hit", pnl_r=pnl_r
                            ),
                        )
                    except Exception:
                        pass
                except Exception as exc:
                    logger.warning(
                        '{"event":"signal_passport_outcome_write_failed","record_id":"%s","error":"%s"}',
                        alert_id,
                        str(exc)[:120],
                    )
                _broadcast_update(
                    (
                        "❌ <b>STOP LOSS HIT</b>\n\n"
                        "Trade closed at invalidation level."
                    ),
                    data["telegram_message_id"],
                    data,
                    telegram_channel,
                    update_type="sl"
                )

            # --- TP2 (Extreme) hit: full success, remove ---
            elif hit_target and not data["target_hit"]:
                from decimal import Decimal
                exit_price = Decimal(str(data["target"]))
                pnl_r = self._calculate_signal_pnl_r(data, exit_price)
                if pnl_r is None:
                    logger.error(
                        '{"event":"signal_outcome_unknown","record_id":"%s",'
                        '"reason":"invalid_entry_stop_reference","exit_reason":"tp2_hit"}',
                        alert_id,
                    )
                    continue
                data["target_hit"] = True
                state_changed = True
                to_remove.append(alert_id)
                self.daily_stats["success"] += 1
                logger.info(f"Prediction {alert_id} hit TP2 (Extreme Target).")
                # Update journal outcome
                try:
                    self._get_journal().update_signal_outcome(
                        record_id=alert_id,
                        exit_reason="tp2_hit",
                        exit_price=exit_price,
                        pnl=pnl_r,
                    )
                    try:
                        log_trade_to_sheets(
                            symbol=data["symbol"],
                            timeframe=data.get("timeframe", "UNKNOWN"),
                            entry_price=data.get("entry_price", 0.0),
                            exit_price=data["target"],
                            sl=data["sl"],
                            tp=data["target"],
                            pips=None,
                            result="WIN ✅",
                            notes=self._sheet_notes(data, "Signal tracker target hit"),
                            webhook_url=self._google_sheets_webhook_url,
                            metadata=self._outcome_feedback(
                                data, "WIN", "tp2_hit", pnl_r=pnl_r
                            ),
                        )
                    except Exception:
                        pass
                except Exception as exc:
                    logger.warning(
                        '{"event":"signal_passport_outcome_write_failed","record_id":"%s","error":"%s"}',
                        alert_id,
                        str(exc)[:120],
                    )
                _broadcast_update(
                    (
                        "🏆 <b>FULL TARGET HIT</b>\n\n"
                        "TP2 successfully reached! ✅"
                    ),
                    data["telegram_message_id"],
                    data,
                    telegram_channel,
                    update_type="tp2"
                )

            # --- TP1 (Midpoint) hit: partial win, keep tracking for TP2 ---
            elif hit_midpoint and not data.get("midpoint_hit", False):
                data["midpoint_hit"] = True
                state_changed = True
                logger.info(f"Prediction {alert_id} hit TP1 (Midpoint).")
                _broadcast_update(
                    (
                        "🪩 <b>PARTIAL PROFIT</b>\n\n"
                        "TP1 (Midpoint) reached. Secure profits! 💰"
                    ),
                    data["telegram_message_id"],
                    data,
                    telegram_channel,
                    update_type="tp1"
                )

        for alert_id in set(to_remove):
            if alert_id in self.predictions:
                del self.predictions[alert_id]
                state_changed = True

        if state_changed:
            self._save()

    def get_daily_report(self) -> dict:
        """Return the current daily stats."""
        return self.daily_stats.copy()

    def reset_daily_report(self):
        """Reset the daily stats for a new day."""
        current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.daily_stats = {"date": current_date, "total": 0, "success": 0, "failed": 0}
        self._save()
