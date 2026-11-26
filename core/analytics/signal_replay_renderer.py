"""Deterministic, customer-safe Signal Replay chart rendering.

The renderer consumes only a validated replay snapshot. It never fetches
market data, reruns a detector, recalculates confidence, or changes a signal.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from core.models.signal_replay import (
    ReplayCandleRole,
    ReplayLifecycleStatus,
    ReplayOutcome,
    ReplaySnapshot,
)


logger = logging.getLogger("atlax.analytics.signal_replay_renderer")


class ReplayRenderError(RuntimeError):
    """A typed, non-sensitive failure while creating a replay image."""

    def __init__(self, reason: str) -> None:
        self.reason = str(reason or "render_failed")[:120]
        super().__init__(self.reason)


@dataclass(frozen=True)
class ReplayRenderConfig:
    """Validated renderer settings supplied by the Settings Engine."""

    width: int = 1600
    height: int = 900
    max_bytes: int = 2 * 1024 * 1024
    cache_dir: str = "data/replay_cache"
    renderer_version: str = "1.0"
    timeout_seconds: int = 5

    def __post_init__(self) -> None:
        if not 640 <= int(self.width) <= 2400:
            raise ValueError("replay renderer width must be between 640 and 2400")
        if not 360 <= int(self.height) <= 1600:
            raise ValueError("replay renderer height must be between 360 and 1600")
        if int(self.max_bytes) < 100_000:
            raise ValueError("replay renderer max_bytes must be >= 100000")
        if not str(self.cache_dir).strip():
            raise ValueError("replay renderer cache_dir is required")
        if not str(self.renderer_version).strip():
            raise ValueError("replay renderer version is required")
        if int(self.timeout_seconds) < 1:
            raise ValueError("replay renderer timeout_seconds must be >= 1")


@dataclass(frozen=True)
class ReplayRenderResult:
    """PNG bytes plus the public metadata needed by Telegram delivery."""

    image_bytes: bytes
    cache_key: str
    data_as_of: datetime
    outcome_label: str


def _utc_label(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _public_outcome_label(snapshot: ReplaySnapshot) -> str:
    if snapshot.lifecycle_status is ReplayLifecycleStatus.RESOLVED:
        return snapshot.final_outcome.value
    if snapshot.lifecycle_status is ReplayLifecycleStatus.EXPIRED:
        return "EXPIRED / UNKNOWN"
    return "UNKNOWN"


def build_replay_caption(snapshot: ReplaySnapshot) -> str:
    """Build a short subscriber-facing caption without internal rule names."""
    from html import escape

    direction = str(snapshot.rule_context.get("direction", "UNKNOWN")).upper()
    direction = direction if direction in {"BUY", "SELL"} else "UNKNOWN"
    outcome = _public_outcome_label(snapshot)
    last_candle = (
        snapshot.outcome_candles[-1].candle
        if snapshot.outcome_candles
        else snapshot.formation_candles[-1].candle
    )
    return (
        "📼 <b>ATLAX Signal Replay</b>\n"
        f"💎 <b>{escape(snapshot.symbol)}</b> · {escape(snapshot.source_timeframe)} · "
        f"{escape(direction)}\n"
        f"📌 Outcome: <b>{escape(outcome)}</b>\n"
        f"🕒 Data as of: <code>{_utc_label(last_candle.close_time)}</code>\n"
        "This replay shows the closed market data available to ATLAX at formation."
    )


class ReplayRenderer:
    """Render and cache one deterministic PNG for a validated snapshot."""

    def __init__(self, config: ReplayRenderConfig | None = None) -> None:
        self._config = config or ReplayRenderConfig()

    def render(self, snapshot: ReplaySnapshot) -> ReplayRenderResult:
        if not isinstance(snapshot, ReplaySnapshot):
            raise ReplayRenderError("invalid_snapshot")
        if snapshot.availability.value != "AVAILABLE":
            raise ReplayRenderError("snapshot_unavailable")

        cache_key = self._cache_key(snapshot)
        cached = self._read_cache(cache_key)
        if cached is not None:
            return ReplayRenderResult(
                image_bytes=cached,
                cache_key=cache_key,
                data_as_of=self._data_as_of(snapshot),
                outcome_label=_public_outcome_label(snapshot),
            )

        try:
            self._configure_matplotlib()
        except ImportError as exc:
            logger.error(
                '{"event":"signal_replay_render_failed","reason":"renderer_dependency_missing"}'
            )
            raise ReplayRenderError("renderer_dependency_missing") from exc
        except Exception as exc:  # noqa: BLE001 - typed failure protects delivery
            logger.error(
                '{"event":"signal_replay_render_failed","reason":"renderer_setup_failed","error":"%s"}',
                str(exc)[:160],
            )
            raise ReplayRenderError("renderer_setup_failed") from exc

        started = time.monotonic()
        try:
            image_bytes = self._render_png(snapshot)
        except ReplayRenderError:
            raise
        except ImportError as exc:
            logger.error(
                '{"event":"signal_replay_render_failed","reason":"renderer_dependency_missing"}'
            )
            raise ReplayRenderError("renderer_dependency_missing") from exc
        except Exception as exc:  # noqa: BLE001 - typed failure protects delivery
            logger.error(
                '{"event":"signal_replay_render_failed","reason":"render_exception","error":"%s"}',
                str(exc)[:160],
            )
            raise ReplayRenderError("render_exception") from exc

        elapsed = time.monotonic() - started
        if elapsed > int(self._config.timeout_seconds):
            logger.warning(
                '{"event":"signal_replay_render_failed","reason":"render_timeout","elapsed_seconds":%.3f}',
                elapsed,
            )
            raise ReplayRenderError("render_timeout")
        if not image_bytes or not image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ReplayRenderError("invalid_png")
        if len(image_bytes) > int(self._config.max_bytes):
            raise ReplayRenderError("image_too_large")
        self._write_cache(cache_key, image_bytes)
        logger.info(
            '{"event":"signal_replay_render_succeeded","cache_key":"%s","bytes":%d,"elapsed_seconds":%.3f}',
            cache_key,
            len(image_bytes),
            elapsed,
        )
        return ReplayRenderResult(
            image_bytes=image_bytes,
            cache_key=cache_key,
            data_as_of=self._data_as_of(snapshot),
            outcome_label=_public_outcome_label(snapshot),
        )

    def _cache_key(self, snapshot: ReplaySnapshot) -> str:
        material = "|".join(
            (
                snapshot.snapshot_hash,
                str(self._config.renderer_version),
                str(self._config.width),
                str(self._config.height),
            )
        ).encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def _cache_path(self, cache_key: str) -> Path:
        return Path(self._config.cache_dir) / f"{cache_key}.png"

    def _read_cache(self, cache_key: str) -> bytes | None:
        path = self._cache_path(cache_key)
        try:
            data = path.read_bytes()
        except (FileNotFoundError, OSError):
            return None
        if (
            not data.startswith(b"\x89PNG\r\n\x1a\n")
            or len(data) > int(self._config.max_bytes)
        ):
            return None
        return data

    def _write_cache(self, cache_key: str, data: bytes) -> None:
        path = self._cache_path(cache_key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(f".{os.getpid()}.tmp")
            temporary.write_bytes(data)
            os.replace(temporary, path)
        except OSError as exc:
            logger.warning(
                '{"event":"signal_replay_cache_write_failed","reason":"%s"}',
                str(exc)[:120],
            )

    @staticmethod
    def _data_as_of(snapshot: ReplaySnapshot) -> datetime:
        if snapshot.outcome_candles:
            return snapshot.outcome_candles[-1].candle.close_time
        return snapshot.formation_candles[-1].candle.close_time

    def _configure_matplotlib(self):
        if not os.environ.get("MPLCONFIGDIR"):
            mpl_config_dir = Path(self._config.cache_dir) / ".mplconfig"
            try:
                mpl_config_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                mpl_config_dir = Path(tempfile.gettempdir()) / "atlax-mplconfig"
                mpl_config_dir.mkdir(parents=True, exist_ok=True)
            os.environ["MPLCONFIGDIR"] = str(mpl_config_dir)

        import matplotlib

        matplotlib.use("Agg")
        from matplotlib import pyplot  # noqa: F401 - warm the renderer before timing
        return matplotlib

    def _render_png(self, snapshot: ReplaySnapshot) -> bytes:
        self._configure_matplotlib()
        from matplotlib import pyplot as plt
        from matplotlib.patches import Rectangle

        candles = list(snapshot.formation_candles) + list(snapshot.outcome_candles)
        if not candles:
            raise ReplayRenderError("no_candles")

        width_inches = int(self._config.width) / 100
        height_inches = int(self._config.height) / 100
        fig, axis = plt.subplots(figsize=(width_inches, height_inches), dpi=100)
        try:
            fig.patch.set_facecolor("#111827")
            axis.set_facecolor("#111827")
            x_values = list(range(len(candles)))
            lows = [float(item.candle.low) for item in candles]
            highs = [float(item.candle.high) for item in candles]
            price_span = max(max(highs) - min(lows), 1e-9)
            role_edges = {
                ReplayCandleRole.PARENT: "#f6c453",
                ReplayCandleRole.SWEEP: "#38bdf8",
                ReplayCandleRole.CONFIRMATION: "#c084fc",
            }

            for index, item in enumerate(candles):
                candle = item.candle
                open_price = float(candle.open)
                close_price = float(candle.close)
                high_price = float(candle.high)
                low_price = float(candle.low)
                bullish = close_price >= open_price
                body_color = "#22c55e" if bullish else "#ef4444"
                edge_color = role_edges.get(item.role, body_color)
                axis.vlines(index, low_price, high_price, color=body_color, linewidth=1.2)
                body_low = min(open_price, close_price)
                body_height = max(abs(close_price - open_price), price_span * 0.002)
                axis.add_patch(
                    Rectangle(
                        (index - 0.30, body_low),
                        0.60,
                        body_height,
                        facecolor=body_color,
                        edgecolor=edge_color,
                        linewidth=2.2 if item.role in role_edges else 0.8,
                        alpha=0.95,
                    )
                )
                role_label = {
                    ReplayCandleRole.PARENT: "P",
                    ReplayCandleRole.SWEEP: "S",
                    ReplayCandleRole.CONFIRMATION: "C",
                }.get(item.role)
                if role_label:
                    axis.text(
                        index,
                        high_price + price_span * 0.018,
                        role_label,
                        color=edge_color,
                        fontsize=10,
                        weight="bold",
                        ha="center",
                        va="bottom",
                    )

            parent_x = snapshot.parent_index
            confirmation_x = snapshot.confirmation_index
            range_left = parent_x - 0.55
            range_right = confirmation_x + 0.55
            axis.add_patch(
                Rectangle(
                    (range_left, float(snapshot.parent_low)),
                    range_right - range_left,
                    float(snapshot.parent_high - snapshot.parent_low),
                    facecolor="#f6c453",
                    edgecolor="#f6c453",
                    linewidth=1.4,
                    linestyle="--",
                    alpha=0.08,
                )
            )
            axis.hlines(
                [float(snapshot.parent_high), float(snapshot.parent_low)],
                range_left,
                range_right,
                colors="#f6c453",
                linestyles="--",
                linewidth=1.3,
            )
            axis.text(
                range_left + 0.05,
                float(snapshot.parent_high) + price_span * 0.012,
                f"P HIGH {snapshot.parent_high}",
                color="#f6c453",
                fontsize=9,
                weight="bold",
            )
            axis.text(
                range_left + 0.05,
                float(snapshot.parent_low) - price_span * 0.028,
                f"P LOW {snapshot.parent_low}",
                color="#f6c453",
                fontsize=9,
                weight="bold",
            )
            axis.axvline(
                confirmation_x + 0.55,
                color="#94a3b8",
                linestyle=":",
                linewidth=1.0,
            )
            axis.text(
                confirmation_x + 0.65,
                max(highs),
                "FORMATION CUTOFF",
                color="#cbd5e1",
                fontsize=8,
                rotation=90,
                va="top",
            )
            axis.text(
                range_right - 0.05,
                min(lows) - price_span * 0.055,
                "3-CANDLE CRT",
                color="#f6c453",
                fontsize=8,
                weight="bold",
                ha="right",
            )

            levels = {
                "ENTRY HIGH": _decimal(snapshot.rule_context.get("entry_zone_high")),
                "ENTRY LOW": _decimal(snapshot.rule_context.get("entry_zone_low")),
                "STOP": _decimal(snapshot.rule_context.get("invalidation_level")),
                "TP1": _decimal(snapshot.rule_context.get("midpoint_target")),
                "TP2": _decimal(snapshot.rule_context.get("opposite_extreme_target")),
            }
            line_colors = {
                "ENTRY HIGH": "#60a5fa",
                "ENTRY LOW": "#60a5fa",
                "STOP": "#fb7185",
                "TP1": "#a3e635",
                "TP2": "#facc15",
            }
            entry_low = levels["ENTRY LOW"]
            entry_high = levels["ENTRY HIGH"]
            if entry_low is not None and entry_high is not None:
                lower, upper = sorted((float(entry_low), float(entry_high)))
                axis.fill_between(
                    [-0.5, len(candles) - 0.5],
                    lower,
                    upper,
                    color="#60a5fa",
                    alpha=0.10,
                )
                axis.text(
                    -0.40,
                    (lower + upper) / 2,
                    "ENTRY",
                    color="#60a5fa",
                    fontsize=8,
                    weight="bold",
                    va="center",
                )
            for label, level in levels.items():
                if level is None:
                    continue
                axis.axhline(
                    float(level),
                    color=line_colors[label],
                    linestyle="-" if label.startswith("ENTRY") else "-.",
                    linewidth=1.0,
                    alpha=0.85,
                )
                axis.text(
                    len(candles) - 0.35,
                    float(level),
                    f" {label}",
                    color=line_colors[label],
                    fontsize=8,
                    va="center",
                    ha="right",
                    weight="bold",
                )

            if snapshot.outcome_candles:
                outcome_index = len(candles) - 1
                if snapshot.outcome_at is not None:
                    candidates = [
                        (abs(item.candle.close_time - snapshot.outcome_at), index)
                        for index, item in enumerate(candles)
                        if index >= len(snapshot.formation_candles)
                    ]
                    if candidates:
                        outcome_index = min(candidates)[1]
                outcome_color = {
                    ReplayOutcome.WIN: "#4ade80",
                    ReplayOutcome.LOSS: "#fb7185",
                }.get(snapshot.final_outcome, "#facc15")
                axis.scatter(
                    [outcome_index],
                    [highs[outcome_index] + price_span * 0.04],
                    s=70,
                    marker="*",
                    color=outcome_color,
                    zorder=6,
                )

            direction = str(snapshot.rule_context.get("direction", "UNKNOWN")).upper()
            direction = direction if direction in {"BUY", "SELL"} else "UNKNOWN"
            outcome_label = _public_outcome_label(snapshot)
            axis.set_title(
                f"ATLAX SIGNAL REPLAY  |  {snapshot.symbol} {snapshot.source_timeframe}  |  {direction}  |  {outcome_label}\n"
                f"Data as of {_utc_label(self._data_as_of(snapshot))}",
                color="#f8fafc",
                fontsize=15,
                loc="left",
                pad=14,
                weight="bold",
            )
            axis.set_xlim(-1, len(candles))
            axis.set_ylim(min(lows) - price_span * 0.08, max(highs) + price_span * 0.10)
            axis.set_xticks(x_values[:: max(1, len(x_values) // 10)])
            axis.set_xticklabels(
                [candles[index].candle.close_time.strftime("%m-%d\n%H:%M") for index in x_values[:: max(1, len(x_values) // 10)]],
                color="#cbd5e1",
                fontsize=8,
            )
            axis.tick_params(axis="y", colors="#cbd5e1", labelsize=9)
            axis.grid(axis="y", color="#334155", alpha=0.55, linewidth=0.7)
            for spine in axis.spines.values():
                spine.set_color("#475569")
            fig.subplots_adjust(left=0.07, right=0.97, top=0.86, bottom=0.12)
            output = io.BytesIO()
            fig.savefig(output, format="png", dpi=100)
            return output.getvalue()
        finally:
            plt.close(fig)
