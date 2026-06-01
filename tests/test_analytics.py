"""Tests for scanner.analytics — pure functions, no external API."""
import pytest
import pandas as pd
from scanner.analytics import calc_rsi, calc_signal_score


class TestCalcRsi:
    def test_length_matches_input(self):
        prices = pd.Series([100.0 + i for i in range(30)])
        result = calc_rsi(prices, period=14)
        assert len(result) == len(prices)

    def test_uptrend_gives_high_rsi(self):
        # Strong uptrend: +2 most steps, -0.5 every 5th step → RSI should be high
        vals = []
        v = 100.0
        for i in range(30):
            v += -0.5 if i % 5 == 4 else 2.0
            vals.append(v)
        rsi = calc_rsi(pd.Series(vals), period=14)
        assert rsi.iloc[-1] > 70

    def test_downtrend_gives_low_rsi(self):
        prices = pd.Series([float(30 - i) for i in range(30)])
        rsi = calc_rsi(prices, period=14)
        assert rsi.iloc[-1] < 30

    def test_flat_prices_gives_nan_or_50(self):
        prices = pd.Series([100.0] * 30)
        rsi = calc_rsi(prices, period=14)
        # flat → avg_loss=0 → rs=nan → rsi=nan or 100
        last = rsi.iloc[-1]
        assert pd.isna(last) or last == pytest.approx(100, abs=1)


class TestCalcSignalScore:
    def _base_stock(self, **overrides) -> dict:
        base = {
            "bo_body_pct":   9.0,
            "bo_vol_ratio":  3.0,
            "bo_lookback":   3,
            "vol_dry_ratio": 0.5,
            "shape_ratio":   0.10,
            "ma20_gap":      0.02,
            "price_pos":     0.80,
        }
        base.update(overrides)
        return base

    def test_score_in_range(self):
        s = calc_signal_score(self._base_stock())
        assert 0 <= s <= 100

    def test_higher_body_pct_increases_score(self):
        low  = calc_signal_score(self._base_stock(bo_body_pct=9.0))
        high = calc_signal_score(self._base_stock(bo_body_pct=15.0))
        assert high > low

    def test_lower_vol_dry_increases_score(self):
        wet  = calc_signal_score(self._base_stock(vol_dry_ratio=0.9))
        dry  = calc_signal_score(self._base_stock(vol_dry_ratio=0.1))
        assert dry > wet

    def test_lookback_1_beats_lookback_3(self):
        s1 = calc_signal_score(self._base_stock(bo_lookback=1))
        s3 = calc_signal_score(self._base_stock(bo_lookback=3))
        assert s1 > s3

    def test_perfect_stock_scores_near_100(self):
        perfect = {
            "bo_body_pct":   20.0,
            "bo_vol_ratio":  8.0,
            "bo_lookback":   1,
            "vol_dry_ratio": 0.0,
            "shape_ratio":   0.0,
            "ma20_gap":      0.02,
            "price_pos":     0.95,
        }
        assert calc_signal_score(perfect) >= 90

    def test_returns_int(self):
        s = calc_signal_score(self._base_stock())
        assert isinstance(s, int)
