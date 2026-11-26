from datetime import datetime, timezone
from types import SimpleNamespace

from core.operations.reconciliation import reconcile_trade_state


NOW = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)


def _record(**overrides):
    record = {
        "execution_record_id": "exec-1",
        "candidate_id": "candidate-1",
        "execution_status": "FILLED",
        "mt5_position_id": 77,
        "mt5_order_id": 88,
        "mt5_deal_id": 99,
        "mt5_request": {
            "symbol": "EURUSD",
            "volume": "0.10",
            "order_type": "ORDER_TYPE_BUY",
            "sl": "1.09000",
            "tp": "1.11000",
        },
    }
    record.update(overrides)
    return record


def _position(**overrides):
    position = {
        "identifier": 77,
        "ticket": 77,
        "magic": 20260101,
        "symbol": "EURUSD",
        "type": 0,
        "volume": 0.10,
        "sl": 1.09,
        "tp": 1.11,
    }
    position.update(overrides)
    return SimpleNamespace(**position)


def test_reconciliation_matches_owned_position():
    report = reconcile_trade_state(
        [_record()],
        [_position()],
        [],
        magic_number=20260101,
        observed_at=NOW,
    )
    assert report.status == "MATCHED"
    assert report.blocks_new_entries is False
    assert report.matched == 1


def test_reconciliation_blocks_on_position_conflict():
    report = reconcile_trade_state(
        [_record()],
        [_position(volume=0.20)],
        [],
        magic_number=20260101,
        observed_at=NOW,
    )
    assert report.status == "CONFLICT"
    assert report.blocks_new_entries is True
    assert report.conflicts == 1


def test_reconciliation_marks_unmatched_atlax_position_as_missing_from_mt5():
    report = reconcile_trade_state(
        [_record()],
        [],
        [],
        magic_number=20260101,
        observed_at=NOW,
    )
    assert report.status == "MISSING_FROM_MT5"
    assert report.missing_from_mt5 == 1


def test_reconciliation_marks_untracked_atlax_magic_position():
    report = reconcile_trade_state(
        [],
        [_position()],
        [],
        magic_number=20260101,
        observed_at=NOW,
    )
    assert report.status == "MISSING_FROM_ATLAX"
    assert report.missing_from_atlax == 1


def test_reconciliation_does_not_claim_ownership_without_magic():
    report = reconcile_trade_state(
        [],
        [_position(magic=None)],
        [],
        magic_number=20260101,
        observed_at=NOW,
    )
    assert report.status == "UNKNOWN"
    assert report.blocks_new_entries is True
