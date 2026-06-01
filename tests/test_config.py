"""Tests for scanner.config — validates STRATEGY dict structure."""
import pytest
from scanner.config import STRATEGY


REQUIRED_KEYS = [
    "bo_body_pct", "bo_vol_ratio", "bo_lookback",
    "pullback_vol", "pullback_shape",
    "tp_pct", "tp1_pct", "sl_buffer", "sl_limit", "max_hold_days",
    "use_ma60_filter", "min_marcap", "use_market_filter", "min_turnover",
    "rsi_period", "rsi_min",
    "use_price_range", "price_range_pct",
    "trail_pct", "trail_activate_pct",
    "max_sector_count", "drift_winrate_threshold", "drift_weeks",
    "min_buy_pressure", "max_positions", "min_signal_score", "hard_stop_pct",
]


@pytest.mark.parametrize("key", REQUIRED_KEYS)
def test_strategy_has_key(key):
    assert key in STRATEGY, f"STRATEGY missing key: {key}"


class TestStrategyValues:
    def test_tp_pct_positive_and_reasonable(self):
        assert 0 < STRATEGY["tp_pct"] <= 0.20

    def test_sl_buffer_less_than_one(self):
        assert STRATEGY["sl_buffer"] < 1.0

    def test_sl_limit_less_than_tp(self):
        assert STRATEGY["sl_limit"] < STRATEGY["tp_pct"]

    def test_hard_stop_le_sl_limit(self):
        assert STRATEGY["hard_stop_pct"] <= STRATEGY["sl_limit"] + 0.02

    def test_max_positions_positive(self):
        assert STRATEGY["max_positions"] > 0

    def test_trail_activate_lt_tp(self):
        assert STRATEGY["trail_activate_pct"] < STRATEGY["tp_pct"]

    def test_breakeven_winrate_achievable(self):
        tp  = STRATEGY["tp_pct"]
        sl  = STRATEGY["sl_limit"]
        rr  = tp / sl
        be  = sl / (tp + sl)
        assert be < 0.60, f"손익분기 승률 {be:.1%} 너무 높음 (R:R={rr:.2f})"

    def test_bo_body_pct_at_least_5pct(self):
        assert STRATEGY["bo_body_pct"] >= 0.05
