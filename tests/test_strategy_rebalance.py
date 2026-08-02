"""Tests for scanner.strategy_rebalance — 모멘텀·목표비중 계산 (FDR 호출 없이 순수 로직 검증)."""
import pandas as pd
import pytest

from scanner.strategy_rebalance import (
    STRATEGIES, DEFAULT_KEY, MANAGED_UNIVERSE, NAMES,
    get_strategy, list_strategies, universe_for,
    _blended_momentum, _w13612_momentum, _compute_dual, _compute_vaa,
    compute_target_weights,
)


def series_with_return(ret: float, length: int = 300) -> pd.Series:
    """모든 룩백(63/126/252)에서 정확히 `ret` 수익률이 나오는 시계열.
    앞부분 100 고정, 마지막 값만 100*(1+ret)."""
    vals = [100.0] * length + [100.0 * (1 + ret)]
    return pd.Series(vals)


class TestRegistry:
    def test_get_strategy_known_key(self):
        assert get_strategy("kr_gem")["name"] == "한국·미국 멀티에셋"

    def test_get_strategy_unknown_falls_back_to_default(self):
        assert get_strategy("no_such_key") is STRATEGIES[DEFAULT_KEY]

    def test_list_strategies_has_all_required_fields(self):
        lst = list_strategies()
        assert len(lst) == len(STRATEGIES)
        for s in lst:
            for field in ("key", "name", "description", "profile", "top_n", "min_seed"):
                assert field in s, f"{s.get('key')}에 {field} 누락"

    def test_universe_for_dual_includes_safe_and_cash(self):
        u = universe_for("kr_gem")
        spec = STRATEGIES["kr_gem"]
        assert set(u) == set(spec["risk"] + [spec["safe"], spec["cash_proxy"]])
        assert len(u) == len(set(u)), "중복 티커 없어야 함"

    def test_universe_for_vaa(self):
        u = universe_for("vaa_kr")
        spec = STRATEGIES["vaa_kr"]
        assert set(u) == set(spec["offensive"] + spec["defensive"])

    def test_managed_universe_covers_every_strategy(self):
        for key in STRATEGIES:
            assert set(universe_for(key)) <= MANAGED_UNIVERSE

    def test_all_universe_tickers_have_display_names(self):
        for tk in MANAGED_UNIVERSE:
            assert tk in NAMES, f"{tk} 표시명 누락"


class TestMomentum:
    def test_blended_none_input(self):
        assert _blended_momentum(None) is None
        assert _blended_momentum(pd.Series([], dtype=float)) is None

    def test_blended_short_series_returns_none(self):
        # 최소 룩백(63일)보다 짧으면 계산 불가
        assert _blended_momentum(pd.Series([100.0] * 50)) is None

    def test_blended_exact_return(self):
        assert _blended_momentum(series_with_return(0.10)) == pytest.approx(0.10)
        assert _blended_momentum(series_with_return(-0.05)) == pytest.approx(-0.05)

    def test_blended_partial_lookbacks(self):
        # 100일 시계열 → 63일 룩백만 사용
        s = pd.Series([100.0] * 100 + [110.0])
        assert _blended_momentum(s) == pytest.approx(0.10)

    def test_w13612_exact_return(self):
        # 전 구간 동일 수익률이면 가중평균도 동일
        assert _w13612_momentum(series_with_return(0.10)) == pytest.approx(0.10)

    def test_w13612_none_and_short(self):
        assert _w13612_momentum(None) is None
        assert _w13612_momentum(pd.Series([100.0] * 10)) is None


class TestComputeDual:
    SPEC = {
        "risk": ["A", "B", "C"], "safe": "SAFE", "cash_proxy": "CASH",
        "top_n": 2, "type": "dual",
    }

    def test_top_n_equal_weight_when_all_beat_cash(self):
        closes = {
            "A": series_with_return(0.20), "B": series_with_return(0.10),
            "C": series_with_return(0.05), "CASH": series_with_return(0.0),
            "SAFE": series_with_return(0.01),
        }
        w = _compute_dual(self.SPEC, closes)
        assert w == {"A": 50.0, "B": 50.0}

    def test_slot_flees_to_safe_when_below_cash_momentum(self):
        closes = {
            "A": series_with_return(0.20), "B": series_with_return(-0.10),
            "C": series_with_return(-0.20), "CASH": series_with_return(0.0),
            "SAFE": series_with_return(0.01),
        }
        w = _compute_dual(self.SPEC, closes)
        # A만 현금 모멘텀 초과 → A 50 + SAFE 50
        assert w == {"A": 50.0, "SAFE": 50.0}

    def test_all_negative_goes_full_safe(self):
        closes = {
            "A": series_with_return(-0.10), "B": series_with_return(-0.20),
            "C": series_with_return(-0.30), "CASH": series_with_return(0.0),
            "SAFE": series_with_return(0.01),
        }
        w = _compute_dual(self.SPEC, closes)
        assert w == {"SAFE": 100.0}

    def test_no_data_returns_empty_not_defensive(self):
        """데이터 장애는 방어 신호가 아니다. 안전자산 100%를 돌려주면
        시세 API가 죽은 날 보유 전량을 팔고 채권으로 갈아탄다."""
        assert _compute_dual(self.SPEC, {}) == {}

    def test_insufficient_data_for_top_n_returns_empty(self):
        """상위 N개를 고를 만큼도 데이터가 없으면 판단 불가."""
        closes = {"A": series_with_return(0.20),   # top_n=2인데 1개만 유효
                  "CASH": series_with_return(0.0), "SAFE": series_with_return(0.01)}
        assert _compute_dual(self.SPEC, closes) == {}

    def test_all_weak_still_flees_to_safe(self):
        """데이터가 충분한데 전 자산이 약세면 '진짜' 방어 신호이므로 안전자산으로 간다."""
        closes = {
            "A": series_with_return(-0.10), "B": series_with_return(-0.20),
            "C": series_with_return(-0.30), "CASH": series_with_return(0.0),
            "SAFE": series_with_return(0.01),
        }
        assert _compute_dual(self.SPEC, closes) == {"SAFE": 100.0}

    def test_weights_always_sum_to_100(self):
        closes = {
            "A": series_with_return(0.15), "B": series_with_return(-0.05),
            "C": series_with_return(0.02), "CASH": series_with_return(0.0),
            "SAFE": series_with_return(0.01),
        }
        w = _compute_dual(self.SPEC, closes)
        assert sum(w.values()) == pytest.approx(100.0)


class TestComputeVaa:
    SPEC = {
        "offensive": ["O1", "O2", "O3"], "defensive": ["D1", "D2"],
        "breadth_break": 1, "top_n": 2, "type": "vaa",
    }

    def test_all_positive_picks_top_n(self):
        closes = {
            "O1": series_with_return(0.20), "O2": series_with_return(0.10),
            "O3": series_with_return(0.05),
            "D1": series_with_return(0.02), "D2": series_with_return(0.01),
        }
        w = _compute_vaa(self.SPEC, closes)
        assert w == {"O1": 50.0, "O2": 50.0}

    def test_one_negative_triggers_full_defense(self):
        # breadth_break=1: 음수 1개면 cf=1 → 방어자산 100%
        closes = {
            "O1": series_with_return(0.20), "O2": series_with_return(0.10),
            "O3": series_with_return(-0.05),
            "D1": series_with_return(0.02), "D2": series_with_return(0.05),
        }
        w = _compute_vaa(self.SPEC, closes)
        assert w == {"D2": 100.0}  # 방어군 중 모멘텀 최고

    def test_missing_offensive_data_counts_as_breach(self):
        closes = {
            "O1": series_with_return(0.20), "O2": series_with_return(0.10),
            # O3 데이터 없음 → breach로 집계
            "D1": series_with_return(0.02), "D2": series_with_return(0.01),
        }
        w = _compute_vaa(self.SPEC, closes)
        assert w == {"D1": 100.0}

    def test_no_offensive_data_returns_empty(self):
        assert _compute_vaa(self.SPEC, {}) == {}


class TestComputeTargetWeights:
    def test_returns_normalized_rows_with_prices(self, monkeypatch):
        import scanner.strategy_rebalance as sr
        data = {
            "069500": series_with_return(0.20), "143850": series_with_return(0.15),
            "133690": series_with_return(0.10), "132030": series_with_return(-0.05),
            "091160": series_with_return(-0.10),
            "114260": series_with_return(0.01), "153130": series_with_return(0.0),
        }
        monkeypatch.setattr(sr, "_close_series", lambda tk, start: data.get(tk))
        rows = sr.compute_target_weights("kr_gem")
        assert rows, "목표비중이 비면 안 됨"
        assert sum(r["weight"] for r in rows) == pytest.approx(100.0, abs=0.1)
        for r in rows:
            assert r["price"] > 0
            assert r["name"]  # 표시명 존재
        # 상위 3개(069500·143850·133690)가 각각 ≈33.3%
        tickers = {r["ticker"] for r in rows}
        assert tickers == {"069500", "143850", "133690"}

    def test_no_data_yields_no_targets(self, monkeypatch):
        """FDR 전면 실패 시 빈 목표를 돌려줘야 한다(호출부가 리밸런싱을 중단)."""
        import scanner.strategy_rebalance as sr
        monkeypatch.setattr(sr, "_close_series", lambda tk, start: None)
        assert sr.compute_target_weights("kr_gem") == []
        assert sr.compute_target_weights("vaa_kr") == []

    def test_vaa_partial_data_outage_returns_empty(self, monkeypatch):
        """공격군 절반도 못 읽으면 breadth 판정을 신뢰할 수 없다."""
        import scanner.strategy_rebalance as sr
        spec = STRATEGIES["vaa_kr"]
        only_one = {spec["offensive"][0]: series_with_return(0.10)}
        only_one.update({tk: series_with_return(0.02) for tk in spec["defensive"]})
        monkeypatch.setattr(sr, "_close_series", lambda tk, start: only_one.get(tk))
        assert sr.compute_target_weights("vaa_kr") == []

    def test_vaa_key_uses_vaa_engine(self, monkeypatch):
        import scanner.strategy_rebalance as sr
        spec = STRATEGIES["vaa_kr"]
        data = {tk: series_with_return(0.10) for tk in spec["offensive"]}
        data.update({tk: series_with_return(0.02) for tk in spec["defensive"]})
        monkeypatch.setattr(sr, "_close_series", lambda tk, start: data.get(tk))
        rows = sr.compute_target_weights("vaa_kr")
        # 전 종목 양수 → 공격 상위 top_n(2)개 50/50
        assert len(rows) == 2
        assert sum(r["weight"] for r in rows) == pytest.approx(100.0, abs=0.1)
        assert all(r["ticker"] in spec["offensive"] for r in rows)
