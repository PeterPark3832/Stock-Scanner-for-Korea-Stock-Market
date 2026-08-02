"""backtest_rebalance 엔진 무결성 검증 — 합성 가격으로 회계·look-ahead·정수주수 확인.

백테스트가 틀리면 잘못된 전략을 고르게 되므로, 엔진 자체를 먼저 검증한다.
"""
import pandas as pd
import pytest

import backtest_rebalance as bt
from scanner.strategy_rebalance import STRATEGIES, universe_for


def make_prices(tickers, n=900, trend=None, start="2019-01-01"):
    """영업일 기준 합성 OHLC. trend={ticker: 일간수익률}."""
    idx = pd.bdate_range(start=start, periods=n)
    trend = trend or {}
    out = {}
    for tk in tickers:
        g = trend.get(tk, 0.0)
        close = [100.0 * (1 + g) ** i for i in range(n)]
        out[tk] = pd.DataFrame({"Close": close, "Open": close}, index=idx)
    return out


class TestFirstTradingDays:
    def test_one_per_month_and_ascending(self):
        idx = pd.bdate_range("2020-01-01", periods=300)
        days = bt.first_trading_days(idx, idx[0])
        months = [(d.year, d.month) for d in days]
        assert len(months) == len(set(months)), "월당 1개"
        assert days == sorted(days)

    def test_respects_start(self):
        idx = pd.bdate_range("2020-01-01", periods=300)
        cut = idx[100]
        assert all(d >= cut for d in bt.first_trading_days(idx, cut))


class TestNoLookAhead:
    def test_weights_ignore_asof_day_close(self):
        """리밸런싱 당일 종가를 조작해도 목표 비중이 변하면 안 된다(= 미래 정보 미사용)."""
        spec = STRATEGIES["kr_gem"]
        tks = universe_for("kr_gem")
        prices = make_prices(tks, trend={tks[0]: 0.001})
        asof = prices[tks[0]].index[400]

        w_before = bt.target_weights_asof(spec, prices, tks, asof)

        spiked = {tk: df.copy() for tk, df in prices.items()}
        for tk, df in spiked.items():          # 당일 종가를 10배로 왜곡
            df.loc[asof, "Close"] = df.loc[asof, "Close"] * 10
        w_after = bt.target_weights_asof(spec, spiked, tks, asof)

        assert w_before == w_after, "당일 종가가 판단에 새어 들어감(look-ahead)"

    def test_weights_sum_to_100(self):
        spec = STRATEGIES["kr_gem"]
        tks = universe_for("kr_gem")
        prices = make_prices(tks, trend={tks[0]: 0.0008, tks[1]: 0.0004})
        w = bt.target_weights_asof(spec, prices, tks, prices[tks[0]].index[500])
        assert sum(w.values()) == pytest.approx(100.0, abs=0.01)


class TestAccounting:
    def test_flat_market_zero_cost_preserves_capital(self):
        """가격 불변·비용 0이면 자본은 보존(정수주수로 인한 현금 잔여만 존재)."""
        prices = make_prices(sorted(bt.MANAGED_UNIVERSE))
        r = bt.run_backtest("kr_gem", prices, "2020-01-01", 10_000_000,
                            commission=0.0, slippage=0.0)
        assert r is not None
        assert r["final"] == pytest.approx(10_000_000, rel=0.01), "무비용·무변동인데 자본 변동"

    def test_costs_strictly_reduce_capital(self):
        prices = make_prices(sorted(bt.MANAGED_UNIVERSE))
        free = bt.run_backtest("kr_gem", prices, "2020-01-01", 10_000_000, 0.0, 0.0)
        paid = bt.run_backtest("kr_gem", prices, "2020-01-01", 10_000_000, 0.001, 0.002)
        assert paid["final"] < free["final"], "비용을 물렸는데 자본이 줄지 않음"
        assert paid["fees"] > 0

    def test_equity_curve_is_finite_and_non_negative(self):
        prices = make_prices(sorted(bt.MANAGED_UNIVERSE), trend={"069500": -0.001})
        r = bt.run_backtest("kr_gem", prices, "2020-01-01", 5_000_000, 0.00015, 0.0015)
        assert r is not None
        assert (r["curve"] >= 0).all(), "포트폴리오 가치 음수 — 현금 초과 매수"
        assert r["curve"].notna().all()

    def test_uptrend_produces_gain(self):
        """위험자산이 꾸준히 오르면 듀얼모멘텀은 이를 담아 수익을 내야 한다."""
        tks = universe_for("kr_gem")
        risk = STRATEGIES["kr_gem"]["risk"][0]
        prices = make_prices(sorted(bt.MANAGED_UNIVERSE), trend={risk: 0.0008})
        r = bt.run_backtest("kr_gem", prices, "2020-01-01", 10_000_000, 0.00015, 0.0015)
        assert r is not None
        assert r["cagr"] > 0, "상승장에서 손실 — 엔진 이상"

    def test_metrics_shape(self):
        prices = make_prices(sorted(bt.MANAGED_UNIVERSE), trend={"069500": 0.0005})
        r = bt.run_backtest("kr_gem", prices, "2020-01-01", 10_000_000, 0.00015, 0.0015)
        for k in ("cagr", "mdd", "sharpe", "calmar", "win_rate", "fees", "n_orders"):
            assert k in r
        assert r["mdd"] <= 0, "MDD는 0 이하"
        assert r["n_rebal"] > 0


class TestExecTiming:
    def _prices_open_below_close(self, tickers, n=900):
        """매일 시가 < 종가 (개장 직후 매수가 유리한 국면)."""
        idx = pd.bdate_range("2019-01-01", periods=n)
        out = {}
        for tk in tickers:
            close = [100.0 * (1.0005 ** i) for i in range(n)]
            open_ = [c * 0.99 for c in close]
            out[tk] = pd.DataFrame({"Close": close, "Open": open_}, index=idx)
        return out

    def test_open_vs_close_execution_differs(self):
        prices = self._prices_open_below_close(sorted(bt.MANAGED_UNIVERSE))
        o = bt.run_backtest("kr_gem", prices, "2020-01-01", 10_000_000, 0.0, 0.0, "open")
        c = bt.run_backtest("kr_gem", prices, "2020-01-01", 10_000_000, 0.0, 0.0, "close")
        assert o and c
        assert o["cagr"] != c["cagr"], "체결 시점이 성과에 반영되지 않음"
        # 시가가 종가보다 1% 싸므로 시가 매수(현행)가 이 국면에선 유리해야 함
        assert o["cagr"] > c["cagr"]

    def test_invalid_exec_defaults_to_close_column(self):
        prices = self._prices_open_below_close(sorted(bt.MANAGED_UNIVERSE))
        r = bt.run_backtest("kr_gem", prices, "2020-01-01", 10_000_000, 0.0, 0.0, "close")
        assert r is not None


class TestBuyAndHold:
    def test_tracks_underlying_return(self):
        prices = make_prices(["069500"], n=600, trend={"069500": 0.001})
        idx = prices["069500"].index
        r = bt.buy_and_hold(prices, "069500", idx[0], idx[-1], 10_000_000, 0.0, 0.0)
        assert r is not None
        underlying = prices["069500"]["Close"].iloc[-1] / prices["069500"]["Open"].iloc[0] - 1
        # 정수 주수로 인한 현금 잔여만큼만 하회
        assert r["total_ret"] / 100 == pytest.approx(underlying, rel=0.02)


class TestReport:
    def test_renders_without_error(self):
        prices = make_prices(sorted(bt.MANAGED_UNIVERSE), trend={"069500": 0.0005})
        results = []
        for key in ("kr_gem", "vaa_kr"):
            r = bt.run_backtest(key, prices, "2020-01-01", 10_000_000, 0.00015, 0.0015)
            if r:
                results.append(r)
        assert results
        txt = bt.fmt_report(results, 10_000_000, 0.00015, 0.0015)
        assert "종합 성과" in txt and "연도별" in txt and "판단" in txt
