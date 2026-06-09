"""Tests for scanner.performance — calc_performance_stats / check_winrate_drift."""
import pandas as pd
import pytest

from scanner.performance import calc_performance_stats, check_winrate_drift, format_weekly_report


def _df(pnls, reasons=None, exit_dates=None):
    n = len(pnls)
    if reasons is None:
        reasons = ["TP" if p > 0 else "SL" for p in pnls]
    if exit_dates is None:
        exit_dates = [pd.Timestamp("2024-01-01")] * n
    return pd.DataFrame({
        "pnl_pct":     pnls,
        "exit_reason": reasons,
        "exit_date":   pd.to_datetime(exit_dates),
    })


class TestCalcPerformanceStats:
    def test_empty_returns_zeros(self):
        s = calc_performance_stats(pd.DataFrame())
        assert s["total"] == 0
        assert s["win_rate"] == 0.0
        assert s["avg_pnl"] == 0.0

    def test_all_wins(self):
        s = calc_performance_stats(_df([5.0, 7.0, 6.0]))
        assert s["total"] == 3
        assert s["wins"] == 3
        assert s["win_rate"] == pytest.approx(1.0)
        assert s["avg_pnl"] == pytest.approx(6.0)

    def test_all_losses(self):
        s = calc_performance_stats(_df([-3.0, -4.0]))
        assert s["wins"] == 0
        assert s["win_rate"] == 0.0
        assert s["avg_pnl"] < 0

    def test_mixed_win_rate(self):
        s = calc_performance_stats(_df([7.0, -3.0, -3.0, -3.0]))
        assert s["win_rate"] == pytest.approx(0.25)

    def test_by_reason_counts(self):
        s = calc_performance_stats(_df([7.0, -3.0, -3.0], reasons=["TP", "SL", "SL"]))
        assert s["by_reason"]["TP"] == 1
        assert s["by_reason"]["SL"] == 2

    def test_sharpe_zero_for_single_trade(self):
        # std=0 → sharpe must return 0.0
        s = calc_performance_stats(_df([5.0]))
        assert s["sharpe"] == 0.0

    def test_sharpe_positive_for_volatile_wins(self):
        s = calc_performance_stats(_df([6.0, 7.5, 5.0, 8.0, 6.5]))
        assert s["sharpe"] > 0


class TestCheckWinrateDrift:
    def _week_df(self, win_counts, loss_counts, start_week_monday="2024-01-01"):
        """Build a DataFrame with data spread across consecutive ISO weeks."""
        rows = []
        day = pd.Timestamp(start_week_monday)
        for wins, losses in zip(win_counts, loss_counts):
            for _ in range(wins):
                rows.append({"pnl_pct": 7.0, "exit_reason": "TP", "exit_date": day})
            for _ in range(losses):
                rows.append({"pnl_pct": -3.0, "exit_reason": "SL", "exit_date": day})
            day += pd.Timedelta(days=7)
        df = pd.DataFrame(rows)
        df["exit_date"] = pd.to_datetime(df["exit_date"])
        return df

    def test_none_when_empty(self):
        assert check_winrate_drift(pd.DataFrame()) is None

    def test_none_when_insufficient_weeks(self):
        # Only 2 weeks of data → can't trigger 3-week drift
        df = self._week_df([0, 0], [4, 4])
        assert check_winrate_drift(df) is None

    def test_none_when_winning(self):
        # 3 weeks all above threshold (50% >> 35%)
        df = self._week_df([2, 2, 2], [2, 2, 2])
        assert check_winrate_drift(df) is None

    def test_triggers_after_three_losing_weeks(self):
        # 3 weeks, 0% win rate each week → must trigger
        df = self._week_df([0, 0, 0], [4, 4, 4])
        msg = check_winrate_drift(df)
        assert msg is not None
        assert "드리프트" in msg

    def test_no_trigger_if_only_last_two_bad(self):
        # Week 1 is good (50%), weeks 2+3 bad → only 2 consecutive bad weeks
        df = self._week_df([2, 0, 0], [2, 4, 4])
        assert check_winrate_drift(df) is None

    def test_message_contains_week_labels(self):
        df = self._week_df([0, 0, 0], [4, 4, 4])
        msg = check_winrate_drift(df)
        assert "%" in msg


class TestFormatWeeklyReport:
    def test_empty_stats_message(self):
        stats = {"total": 0, "wins": 0, "win_rate": 0.0,
                 "avg_pnl": 0.0, "sharpe": 0.0, "by_reason": {}}
        msg = format_weekly_report(stats, "01/01~01/07")
        assert "없음" in msg

    def test_format_with_data(self):
        stats = {"total": 4, "wins": 2, "win_rate": 0.5,
                 "avg_pnl": 1.5, "sharpe": 0.8,
                 "by_reason": {"TP": 2, "SL": 2}}
        msg = format_weekly_report(stats, "01/01~01/07")
        assert "50.0%" in msg
        assert "TP" in msg
