"""Tests for scanner.analytics.get_kospi_condition — mocked FDR."""
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

import scanner.analytics as analytics


def _make_kospi(values: list[float]) -> pd.DataFrame:
    """Return synthetic KOSPI DataFrame with given Close values."""
    return pd.DataFrame(
        {"Close": values},
        index=pd.date_range("2024-01-01", periods=len(values), freq="B"),
    )


def _patched_condition(df: pd.DataFrame):
    """Call get_kospi_condition with fdr mocked to return df."""
    with patch.object(analytics, "fdr", MagicMock()) as mock_fdr:
        mock_fdr.DataReader.return_value = df
        return analytics.get_kospi_condition("2024-01-01")


class TestGetKospiCondition:
    def test_passes_when_all_conditions_met(self):
        # Steady uptrend: all 3 conditions satisfied
        vals = [float(2000 + i) for i in range(25)]
        ok, status = _patched_condition(_make_kospi(vals))
        assert bool(ok) is True
        assert "▲ 양호" in status

    def test_fails_when_below_ma20(self):
        # Sharp drop at the end so close < MA20
        vals = [float(2000 + i) for i in range(24)] + [1900.0]
        ok, _ = _patched_condition(_make_kospi(vals))
        assert bool(ok) is False

    def test_fails_when_ma20_declining(self):
        # Downtrend: MA20 now < MA20 5 days ago
        vals = [float(2100 - i) for i in range(25)]
        ok, _ = _patched_condition(_make_kospi(vals))
        assert bool(ok) is False

    def test_fails_on_weekly_crash(self):
        # Flat then -5% drop in last 5 bars
        flat = [2000.0] * 20
        drop = [1900.0, 1895.0, 1890.0, 1885.0, 1880.0]
        ok, status = _patched_condition(_make_kospi(flat + drop))
        assert bool(ok) is False
        assert "급락" in status

    def test_passes_through_on_empty_data(self):
        ok, status = _patched_condition(pd.DataFrame())
        assert ok is True
        assert "부족" in status

    def test_passes_through_on_exception(self):
        with patch.object(analytics, "fdr", MagicMock()) as mock_fdr:
            mock_fdr.DataReader.side_effect = RuntimeError("network error")
            ok, status = analytics.get_kospi_condition("2024-01-01")
        assert ok is True
        assert "실패" in status
