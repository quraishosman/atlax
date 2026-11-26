import pytest
import dataclasses
import sys
import types
from unittest.mock import patch, MagicMock
from types import SimpleNamespace
from decimal import Decimal
from datetime import datetime, timezone, timedelta

from core.models.candle import Candle, CandleSequence
from core.settings.crt_config import load_crt_config
from core.settings.strategy_config import load_strategy_config
from core.settings.confidence_config import load_confidence_config
from core.settings.alert_config import load_alert_config
from core.settings.analytics_config import load_analytics_config
from core.settings.approval_config import load_approval_config
from core.settings.execution_config import load_execution_config
from core.settings.key_level_config import load_key_level_config
from core.settings.market_data_config import load_market_data_config, MarketDataConfig, WatchedInstrument
from core.settings.risk_config import load_risk_config
from core.market_data.key_level_evaluator import KeyLevelEvaluator
from core.market_data.scanner import MarketScanner

@pytest.fixture
def configs():
    crt = load_crt_config("config/atlax.yaml")
    strat = load_strategy_config("config/atlax.yaml")
    conf = load_confidence_config("config/atlax.yaml")
    alert = load_alert_config("config/atlax.yaml")
    market = load_market_data_config("config/atlax.yaml")
    risk = load_risk_config("config/atlax.yaml")
    approval = load_approval_config("config/atlax.yaml")
    execution = load_execution_config("config/atlax.yaml")
    analytics = load_analytics_config("config/atlax.yaml")
    key_level = load_key_level_config("config/atlax.yaml")
    return market, crt, strat, conf, alert, risk, approval, execution, analytics, key_level


@pytest.fixture
def runtime_store(monkeypatch):
    class MemoryRuntime:
        def __init__(self):
            self.values = {}

        def get(self, key, default=None):
            return self.values.get(key, default)

        def set(self, key, value):
            self.values[key] = value

    store = MemoryRuntime()
    monkeypatch.setattr("core.market_data.scanner.get_runtime_state", lambda: store)
    return store

def test_scanner_processes_instruments_with_mock_feed(configs, tmp_path, runtime_store, monkeypatch):
    market_cfg, crt_cfg, strategy_cfg, conf_cfg, alert_cfg, risk_cfg, approval_cfg, execution_cfg, analytics_cfg, key_level_cfg = configs
    analytics_cfg = dataclasses.replace(analytics_cfg, db_path=str(tmp_path / "journal.sqlite"))
    scanner = MarketScanner(
        market_cfg, crt_cfg, strategy_cfg, conf_cfg, alert_cfg,
        risk_cfg, approval_cfg, execution_cfg, analytics_cfg=analytics_cfg,
        key_level_cfg=key_level_cfg,
    )

    # Patch MT5CandleFeed to return a gold-standard bullish fixture
    BASE = datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc)
    def mc(o, h, l, cl, off, dur=4):
        return Candle("EURUSD", "H4", BASE + timedelta(hours=off), BASE + timedelta(hours=off + dur),
            Decimal(o), Decimal(h), Decimal(l), Decimal(cl), True)

    # Realistic EURUSD prices so sweep_distance is in the 3-30 pip ideal range.
    # ATR=0.0050 (50 pips H4).  Sweep closes at 12:00 UTC = NY AM kill zone.
    # Parent:  range=40 pips (ATR ratio=0.80>=0.75), body=27 pips (ratio=0.675>=0.38)
    # Sweep:   depth=12 pips (ATR ratio=0.24>=0.22), close_location=0.40 (ideal)
    #          sweep_distance_pips=12 (ideal 3-30), wick_ratio=0.30 (ideal 0.05-0.50)
    mock_seq = CandleSequence((
        mc("1.0800", "1.0840", "1.0800", "1.0827", 4),   # parent (closes 08:00)
        mc("1.0814", "1.0820", "1.0788", "1.0816", 8),   # sweep (closes 12:00 = NY AM)
    ), "EURUSD", "H4")

    alerts_produced = []
    original_process = scanner._alert_engine.process
    def capture_alert(candidate, score, profile, **kwargs):
        result = original_process(candidate, score, profile, **kwargs)
        if result is not None:
            alerts_produced.append(result)
        return result

    scanner._alert_engine.process = capture_alert

    class OfflineMT5:
        @staticmethod
        def symbol_info_tick(symbol):
            return None

        @staticmethod
        def account_info():
            return None

        @staticmethod
        def positions_get():
            return None

    monkeypatch.setattr("core.market_data.scanner.mt5", OfflineMT5)

    mock_feed = MagicMock()
    mock_feed.get_candles.return_value = mock_seq
    mock_feed.get_atr_from_sequence.return_value = Decimal("0.0050")  # 50 pips H4 EURUSD

    # Only test H4 EURUSD instrument
    h4_instrument = next(i for i in market_cfg.instruments if i.symbol == "EURUSD" and i.timeframe == "H4")

    with patch("core.market_data.scanner.MT5CandleFeed", return_value=mock_feed):
        scanner._process_instrument(h4_instrument)

    assert len(alerts_produced) >= 1, "Expected at least 1 alert for H4 EURUSD (swing_trader)"
    alert = alerts_produced[0]
    assert alert.symbol == "EURUSD"
    assert alert.timeframe == "H4"
    assert alert.direction == "BUY"

def test_scanner_skips_instruments_on_error(configs, tmp_path, runtime_store):
    market_cfg, crt_cfg, strategy_cfg, conf_cfg, alert_cfg, risk_cfg, approval_cfg, execution_cfg, analytics_cfg, key_level_cfg = configs
    analytics_cfg = dataclasses.replace(analytics_cfg, db_path=str(tmp_path / "journal.sqlite"))
    
    error_instrument = WatchedInstrument("INVALID", "H4", 50, 14, 0.0001)
    bad_cfg = MarketDataConfig(
        enabled=True,
        poll_interval_seconds=60,
        mt5=market_cfg.mt5,
        instruments=(error_instrument,) + market_cfg.instruments[:1]
    )
    bad_scanner = MarketScanner(
        bad_cfg, crt_cfg, strategy_cfg, conf_cfg, alert_cfg,
        risk_cfg, approval_cfg, execution_cfg, analytics_cfg=analytics_cfg,
        key_level_cfg=key_level_cfg,
    )

    with patch("core.market_data.scanner.MT5CandleFeed") as MockFeed:
        instance = MockFeed.return_value
        def side_effect(*a, **kw):
            raise RuntimeError("MT5 returned no data for INVALID H4")
        instance.get_candles.side_effect = side_effect
        
        # This should not raise an exception, isolating the error
        bad_scanner._run_cycle()


def test_key_level_evaluation_uses_closed_sources_and_caches_parent(monkeypatch):
    """Key-Level Only routing must receive deterministic parent evidence."""
    start = datetime(2026, 8, 10, tzinfo=timezone.utc)

    def candle(timeframe, opened, high, low, close, duration_hours):
        return Candle(
            "EURUSD",
            timeframe,
            opened,
            opened + timedelta(hours=duration_hours),
            Decimal(high),
            Decimal(high),
            Decimal(low),
            Decimal(close),
            True,
        )

    parent_history = tuple(
        candle(
            "H1",
            start + timedelta(hours=index),
            "1.1005",
            "1.0995",
            "1.1000",
            1,
        )
        for index in range(16)
    )
    parent = parent_history[-1]
    sequence = CandleSequence(parent_history, "EURUSD", "H1")
    source_sequences = {
        "D1": CandleSequence(
            (
                candle("D1", start - timedelta(days=3), "1.1010", "1.0900", "1.0950", 24),
            ),
            "EURUSD",
            "D1",
        ),
        "W1": CandleSequence(
            (
                candle("W1", start - timedelta(days=14), "1.1200", "1.0800", "1.1000", 168),
            ),
            "EURUSD",
            "W1",
        ),
        "H4": CandleSequence(
            (
                candle("H4", start - timedelta(hours=16), "1.1100", "1.0900", "1.1000", 4),
            ),
            "EURUSD",
            "H4",
        ),
    }
    config = load_key_level_config("config/atlax.yaml")
    scanner = object.__new__(MarketScanner)
    scanner._key_level_cfg = config
    scanner._key_level_evaluator = KeyLevelEvaluator(config)
    scanner._key_level_evaluations = {}
    scanner._key_level_shadow_ledger = None
    scanner._market_cfg = SimpleNamespace(instruments=())
    instrument = WatchedInstrument("EURUSD", "H1", 50, 14, 0.0001)
    fetches = []

    class Feed:
        def __init__(self, source_instrument):
            self._instrument = source_instrument

        def get_candles(self, include_live=False):
            assert include_live is False
            fetches.append(self._instrument.timeframe)
            return source_sequences[self._instrument.timeframe]

    monkeypatch.setattr("core.market_data.scanner.MT5CandleFeed", Feed)

    first = scanner._key_level_evaluation_for_parent(instrument, sequence, parent)
    second = scanner._key_level_evaluation_for_parent(instrument, sequence, parent)

    assert first is second
    assert first.status == "VALID"
    assert first.parent_at_htf_zone is True
    assert any(match.level_type == "PREVIOUS_DAY_HIGH" for match in first.matched_levels)
    assert sorted(fetches) == ["D1", "H4", "W1"]


def _fvg_profile_scanner(dispatcher):
    scanner = object.__new__(MarketScanner)
    candidate = SimpleNamespace(
        candidate_id="candidate-h1-fvg",
        symbol="EURUSD",
        timeframe="H1",
        direction="BUY",
        confidence_inputs={},
        invalidation_model=SimpleNamespace(invalidation_level=Decimal("1.09")),
        target_model=SimpleNamespace(
            midpoint_target=Decimal("1.11"),
            opposite_extreme_target=Decimal("1.12"),
        ),
    )
    scanner._strategy = SimpleNamespace(
        evaluate=lambda *args, **kwargs: SimpleNamespace(
            is_trade=True,
            candidate=candidate,
        )
    )
    scanner._strategy_cfg = SimpleNamespace()
    scanner._conf_engine = SimpleNamespace(
        score=lambda value: SimpleNamespace(
            final_score=80.0,
            missing_factors=(),
        )
    )
    scanner._htf_momentum_evaluator = None
    scanner._htf_momentum_cfg = None
    scanner._key_level_cfg = None
    scanner._fvg_cfg = SimpleNamespace(
        enabled=True,
        execution_gate_enabled=True,
    )
    scanner._fvg_watcher = object()
    scanner._alert_engine = SimpleNamespace(
        _channels=[dispatcher],
        register_fvg_prediction=lambda *args, **kwargs: None,
        process=lambda *args, **kwargs: None,
    )
    return scanner


def _fvg_inputs():
    return {
        "det_output": SimpleNamespace(timestamp=datetime.now(timezone.utc)),
        "profile": SimpleNamespace(name="day_trader", min_confidence_threshold=44.0),
        "instrument": SimpleNamespace(symbol="EURUSD", timeframe="H1", pip_size=0.0001),
        "fvg_present": True,
        "fvg_result": SimpleNamespace(
            found=True,
            midpoint=Decimal("1.10"),
            fvg_low=Decimal("1.095"),
            fvg_high=Decimal("1.105"),
            direction="bullish",
        ),
        "ltf_tf": "M5",
        "parent_at_htf_zone": True,
        "quality_tightening": SimpleNamespace(
            status="VALID",
            would_suppress=False,
        ),
    }


def test_fvg_dispatch_receives_key_level_context(monkeypatch):
    dispatched = []

    class Telegram:
        name = "telegram"
        is_enabled = True

        def broadcast_fvg_watch(self, **kwargs):
            dispatched.append(kwargs)

    scanner = _fvg_profile_scanner(Telegram())
    monkeypatch.setattr("core.market_data.scanner.mt5", None)
    scanner._process_profile(**_fvg_inputs())

    assert dispatched[0]["htf_timeframe"] == "H1"
    assert dispatched[0]["parent_at_htf_zone"] is True
    assert dispatched[0]["profile_min_confidence"] == 44.0
    assert dispatched[0]["quality_status"] == "VALID"
    assert dispatched[0]["quality_would_suppress"] is False


def test_missing_fvg_dispatcher_preserves_normal_alert(monkeypatch):
    processed = []

    class Telegram:
        name = "telegram"
        is_enabled = True

    scanner = _fvg_profile_scanner(Telegram())
    scanner._alert_engine.process = lambda *args, **kwargs: processed.append(args)
    monkeypatch.setattr("core.market_data.scanner.mt5", None)
    scanner._process_profile(**_fvg_inputs())

    assert len(processed) == 1


def test_fvg_does_not_delay_the_normal_alert_when_execution_gate_is_disabled(monkeypatch):
    processed = []

    class Telegram:
        name = "telegram"
        is_enabled = True

    scanner = _fvg_profile_scanner(Telegram())
    scanner._fvg_cfg = SimpleNamespace(execution_gate_enabled=False)
    scanner._alert_engine.process = lambda *args, **kwargs: processed.append(
        (args, kwargs)
    )
    monkeypatch.setattr("core.market_data.scanner.mt5", None)

    scanner._process_profile(**_fvg_inputs())

    assert len(processed) == 1
    assert processed[0][1]["quality_tightening"].status == "VALID"


def test_suppressed_telegram_alert_blocks_live_execution(monkeypatch):
    """A suppressed alert must stop before risk approval or MT5 execution."""
    class Telegram:
        name = "telegram"
        is_enabled = True

    scanner = _fvg_profile_scanner(Telegram())
    scanner._fvg_cfg = SimpleNamespace(enabled=False, execution_gate_enabled=False)
    scanner._execution_cfg = SimpleNamespace(require_alert_eligibility=True)
    scanner._alert_engine.process = lambda *args, **kwargs: None
    risk_calls = []
    scanner._risk_engine = SimpleNamespace(
        evaluate=lambda *args, **kwargs: risk_calls.append((args, kwargs))
    )
    monkeypatch.setattr("core.market_data.scanner.mt5", None)

    scanner._process_profile(**_fvg_inputs())

    assert risk_calls == []


def test_scanner_reconciles_closed_execution_from_matching_exit_deal(monkeypatch):
    class Journal:
        def __init__(self):
            self.settled = None

        def list_unsettled_executions(self):
            return [{
                "execution_record_id": "execution-1",
                "candidate_id": "candidate-1",
                "symbol": "EURUSD",
                "execution_status": "FILLED",
                "mt5_order_id": 9,
                "mt5_deal_id": 10,
                "mt5_position_id": 88,
                "attempted_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            }]

        def record_execution_realization(self, record_id, reconciliation, realized_at):
            self.settled = (record_id, reconciliation, realized_at)
            return True

    fake_mt5 = types.ModuleType("MetaTrader5")
    fake_mt5.positions_get = lambda magic: []
    fake_mt5.history_deals_get = lambda start, end: [types.SimpleNamespace(
        magic=123,
        position_id=88,
        order=999,
        entry=1,
        profit=40,
        swap=-2,
        commission=-1,
        fee=0,
    )]
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)

    scanner = object.__new__(MarketScanner)
    scanner._journal = Journal()
    scanner._execution_cfg = types.SimpleNamespace(magic_number=123)

    scanner._reconcile_closed_executions()

    record_id, reconciliation, _ = scanner._journal.settled
    assert record_id == "execution-1"
    assert reconciliation.status == "OK"
    assert reconciliation.net_pnl == Decimal("37")
    assert reconciliation.deals_count == 1


def test_scanner_registers_management_watch_for_string_direction(monkeypatch):
    """Live TradeCandidates use string directions and must retain initial risk."""
    from core.models.execution import ExecutionStatus

    captured = {}
    fake_mt5 = types.ModuleType("MetaTrader5")
    fake_mt5.positions_get = lambda **_kwargs: [types.SimpleNamespace(
        ticket=101,
        identifier=101,
        price_open=Decimal("157.272"),
    )]
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)

    scanner = object.__new__(MarketScanner)
    scanner._execution_cfg = types.SimpleNamespace(magic_number=20260101)
    scanner._journal = types.SimpleNamespace(link_execution_position=lambda *_args: True)
    scanner._record_position_link_quality = lambda *_args: None
    scanner._trade_manager = types.SimpleNamespace(
        register=lambda **kwargs: captured.update(kwargs)
    )
    record = types.SimpleNamespace(
        status=ExecutionStatus.FILLED,
        record_id="execution-1",
    )
    request = types.SimpleNamespace(
        candidate=types.SimpleNamespace(
            candidate_id="candidate-1",
            symbol="USDJPY",
            direction="SELL",
            timeframe="M5",
            profile="scalper",
            invalidation_model=types.SimpleNamespace(
                invalidation_level=Decimal("157.302")
            ),
            target_model=types.SimpleNamespace(
                midpoint_target=Decimal("157.240"),
                opposite_extreme_target=Decimal("157.208"),
            ),
        ),
        risk_evaluation=types.SimpleNamespace(lot_size=Decimal("0.29")),
    )

    scanner._try_register_partial_watch(record, request)

    assert captured["direction"] == "SELL"
    assert captured["original_stop"] == Decimal("157.302")
    assert captured["ticket"] == 101
