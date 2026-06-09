"""Tests for scanner.job_monitor — TP/SL/trail/hard_sl branching logic."""
import pytest
from unittest.mock import MagicMock, patch, call

import scanner.job_monitor as jm


# ─── 공통 픽스처 ──────────────────────────────────────────────────────────────

def _pos(entry=10000, tp=10700, sl=9600, sl_init=None, qty=100,
         hwm=None, trail_activated=False) -> dict:
    return {
        "ticker":           "000001",
        "name":             "테스트종목",
        "sector":           "테스트",
        "entry":            entry,
        "tp":               tp,
        "sl":               sl,
        "sl_init":          sl_init if sl_init is not None else sl,
        "quantity":         qty,
        "high_water_mark":  hwm if hwm is not None else entry,
        "trail_activated":  trail_activated,
        "entry_date":       "2024-01-01",
        "bo_date":          "2024-01-01",
        "bo_open":          9500,
        "signal_score":     60,
        "bo_lookback":      1,
    }


def _live(cur=10000, open_=10000, high=10200, low=9900, vol=50000, bp=110.0):
    return {"current": cur, "open": open_, "high": high,
            "low": low, "volume": vol, "buy_pressure": bp}


PATCHES = [
    "scanner.job_monitor.is_market_closed",
    "scanner.job_monitor.load_positions",
    "scanner.job_monitor.save_positions",
    "scanner.job_monitor.get_current_price",
    "scanner.job_monitor.place_order",
    "scanner.job_monitor.record_trade_history",
    "scanner.job_monitor.send_telegram",
]


def _run_monitor(positions, cur_price, place_order_result=None, auto_trade=False):
    """Run job_monitor_positions with given positions and mocked price."""
    mocks = {}
    with patch("scanner.job_monitor.is_market_closed", return_value=False), \
         patch("scanner.job_monitor.load_positions", return_value=positions), \
         patch("scanner.job_monitor.save_positions") as m_save, \
         patch("scanner.job_monitor.get_current_price", return_value=_live(cur=cur_price)), \
         patch("scanner.job_monitor.place_order",
               return_value=(place_order_result or {"success": True, "order_no": "99"})) as m_order, \
         patch("scanner.job_monitor.record_trade_history") as m_record, \
         patch("scanner.job_monitor.send_telegram"), \
         patch.object(jm.state, "_auto_trade_enabled", auto_trade), \
         patch.object(jm.state, "_auto_trade_lock", MagicMock()):
        jm.job_monitor_positions()
        mocks["save"]   = m_save
        mocks["order"]  = m_order
        mocks["record"] = m_record
    return mocks


# ─── 테스트 ──────────────────────────────────────────────────────────────────

class TestJobMonitorPositions:

    def test_no_positions_returns_early(self):
        with patch("scanner.job_monitor.is_market_closed", return_value=False), \
             patch("scanner.job_monitor.load_positions", return_value=[]), \
             patch("scanner.job_monitor.get_current_price") as m_price, \
             patch("scanner.job_monitor.save_positions"), \
             patch.object(jm.state, "_auto_trade_lock", MagicMock()):
            jm.job_monitor_positions()
        m_price.assert_not_called()

    def test_market_closed_returns_early(self):
        with patch("scanner.job_monitor.is_market_closed", return_value=True), \
             patch("scanner.job_monitor.load_positions") as m_load, \
             patch.object(jm.state, "_auto_trade_lock", MagicMock()):
            jm.job_monitor_positions()
        m_load.assert_not_called()

    def test_tp_hit_records_and_removes_position(self):
        pos = [_pos(entry=10000, tp=10700, sl=9600)]
        mocks = _run_monitor(pos, cur_price=10700, auto_trade=False)
        # record_trade_history must be called with "TP"
        mocks["record"].assert_called_once()
        args = mocks["record"].call_args[0]
        assert args[2] == "TP"
        # position must be removed (saved list is empty)
        saved = mocks["save"].call_args[0][0]
        assert saved == []

    def test_sl_hit_records_sl_and_removes(self):
        pos = [_pos(entry=10000, tp=10700, sl=9600, sl_init=9600)]
        mocks = _run_monitor(pos, cur_price=9600, auto_trade=False)
        mocks["record"].assert_called_once()
        args = mocks["record"].call_args[0]
        assert args[2] == "SL"
        saved = mocks["save"].call_args[0][0]
        assert saved == []

    def test_hard_sl_hit_records_hard_sl(self):
        # entry=10000, hard_stop=5% → hard_stop_sl=9500; cur=9490 ≤ 9500
        pos = [_pos(entry=10000, tp=10700, sl=9600, sl_init=9600, trail_activated=False)]
        mocks = _run_monitor(pos, cur_price=9490, auto_trade=False)
        mocks["record"].assert_called_once()
        reason = mocks["record"].call_args[0][2]
        assert reason == "HARD_SL"

    def test_trail_sl_hit_records_trail_sl(self):
        # sl > sl_init → TRAIL_SL
        # sl_init=9600, sl raised to 9900 (trail already active), cur=9850 ≤ 9900
        pos = [_pos(entry=10000, tp=10700, sl=9900, sl_init=9600,
                    hwm=10400, trail_activated=True)]
        mocks = _run_monitor(pos, cur_price=9850, auto_trade=False)
        mocks["record"].assert_called_once()
        reason = mocks["record"].call_args[0][2]
        assert reason == "TRAIL_SL"

    def test_api_failure_keeps_position(self):
        pos = [_pos()]
        with patch("scanner.job_monitor.is_market_closed", return_value=False), \
             patch("scanner.job_monitor.load_positions", return_value=pos), \
             patch("scanner.job_monitor.save_positions") as m_save, \
             patch("scanner.job_monitor.get_current_price", return_value=None), \
             patch("scanner.job_monitor.record_trade_history") as m_record, \
             patch("scanner.job_monitor.send_telegram"), \
             patch.object(jm.state, "_auto_trade_enabled", False), \
             patch.object(jm.state, "_auto_trade_lock", MagicMock()):
            jm.job_monitor_positions()
        m_record.assert_not_called()
        saved = m_save.call_args[0][0]
        assert len(saved) == 1  # position kept

    def test_trailing_activates_at_threshold(self):
        # entry=10000, trail_activate_pct=0.03 → activate at +3% = 10300
        # cur=10310 → +3.1% → should activate trailing
        pos = [_pos(entry=10000, tp=10700, sl=9600, sl_init=9600,
                    hwm=10310, trail_activated=False)]
        with patch("scanner.job_monitor.is_market_closed", return_value=False), \
             patch("scanner.job_monitor.load_positions", return_value=pos), \
             patch("scanner.job_monitor.save_positions") as m_save, \
             patch("scanner.job_monitor.get_current_price", return_value=_live(cur=10310)), \
             patch("scanner.job_monitor.place_order"), \
             patch("scanner.job_monitor.record_trade_history"), \
             patch("scanner.job_monitor.send_telegram"), \
             patch.object(jm.state, "_auto_trade_enabled", False), \
             patch.object(jm.state, "_auto_trade_lock", MagicMock()):
            jm.job_monitor_positions()
        saved_positions = m_save.call_args[0][0]
        # The position should remain (no TP/SL hit), but trail_activated=True
        assert len(saved_positions) == 1
        assert saved_positions[0]["trail_activated"] is True

    def test_order_placed_on_tp_when_auto_trade_on(self):
        pos = [_pos(entry=10000, tp=10700, sl=9600, qty=100)]
        mocks = _run_monitor(pos, cur_price=10700, auto_trade=True)
        mocks["order"].assert_called_once_with("000001", "sell", 100, "테스트종목")

    def test_no_order_when_auto_trade_off(self):
        pos = [_pos(entry=10000, tp=10700, sl=9600, qty=100)]
        mocks = _run_monitor(pos, cur_price=10700, auto_trade=False)
        mocks["order"].assert_not_called()


class TestJobMorningSLCheck:

    def test_sl_triggered_at_gap_open(self):
        pos = [_pos(entry=10000, tp=10700, sl=9600, sl_init=9600)]
        with patch("scanner.job_monitor.is_market_closed", return_value=False), \
             patch("scanner.job_monitor.load_positions", return_value=pos), \
             patch("scanner.job_monitor.save_positions") as m_save, \
             patch("scanner.job_monitor.get_current_price", return_value=_live(cur=9500)), \
             patch("scanner.job_monitor.place_order", return_value={"success": True}), \
             patch("scanner.job_monitor.record_trade_history") as m_record, \
             patch("scanner.job_monitor.send_telegram"), \
             patch.object(jm.state, "_auto_trade_enabled", False), \
             patch.object(jm.state, "_auto_trade_lock", MagicMock()):
            jm.job_morning_sl_check()
        m_record.assert_called_once()
        saved = m_save.call_args[0][0]
        assert saved == []

    def test_no_sl_when_price_above(self):
        pos = [_pos(entry=10000, tp=10700, sl=9600)]
        with patch("scanner.job_monitor.is_market_closed", return_value=False), \
             patch("scanner.job_monitor.load_positions", return_value=pos), \
             patch("scanner.job_monitor.save_positions") as m_save, \
             patch("scanner.job_monitor.get_current_price", return_value=_live(cur=10100)), \
             patch("scanner.job_monitor.record_trade_history") as m_record, \
             patch("scanner.job_monitor.send_telegram"), \
             patch.object(jm.state, "_auto_trade_enabled", False), \
             patch.object(jm.state, "_auto_trade_lock", MagicMock()):
            jm.job_morning_sl_check()
        m_record.assert_not_called()
        saved = m_save.call_args[0][0]
        assert len(saved) == 1
