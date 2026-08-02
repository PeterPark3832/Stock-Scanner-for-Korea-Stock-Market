"""전략 리뷰 리포트 로직 — 판정이 보수적인지(성과 추종 방지) 검증."""
import pandas as pd
import pytest

from scanner.job_strategy_review import build_review, _verdict, CALMAR_GAP_TRIGGER


def mk(key, name, cagr, mdd, calmar, years=6.0, yearly=None, bh=False):
    idx = pd.to_datetime(["2018-12-31", "2019-12-31", "2020-12-31"])
    vals = yearly if yearly is not None else [5.0, 6.0, 7.0]
    return {
        "key": f"bh_{key}" if bh else key, "name": name, "profile": "밸런스",
        "start": pd.Timestamp("2016-01-04"), "end": pd.Timestamp("2025-12-30"),
        "years": years, "cagr": cagr, "mdd": mdd, "calmar": calmar,
        "sharpe": 1.0, "win_rate": 55.0, "total_ret": cagr * years,
        "final": 0, "vol": 12.0, "sortino": 1.2, "fees": 0, "turnover": 0,
        "n_orders": 0, "n_rebal": 0, "yearly": pd.Series(vals, index=idx),
        "curve": pd.Series([1.0]),
    }


class TestVerdict:
    BEST = mk("kr_growth", "성장주", 14.0, -20.0, 0.70)

    def test_holds_when_current_is_best(self):
        cur = self.BEST
        assert "유지 권장" in _verdict(cur, self.BEST, None, 6.0)

    def test_holds_when_gap_is_small(self):
        """근소한 열위로는 교체를 권하지 않는다 — 잦은 교체는 비용만 늘린다."""
        cur = mk("kr_gem", "멀티에셋", 13.0, -21.0, 0.62)   # gap ≈ 11%
        v = _verdict(cur, self.BEST, None, 6.0)
        assert "유지 권장" in v
        assert "검토" not in v

    def test_flags_only_on_material_gap(self):
        cur = mk("kr_gem", "멀티에셋", 6.0, -30.0, 0.20)     # gap ≈ 71%
        v = _verdict(cur, self.BEST, None, 6.0)
        assert "검토 권고" in v
        assert "성장주" in v

    def test_gap_threshold_boundary(self):
        just_under = mk("x", "x", 10.0, -20.0, self.BEST["calmar"] * (1 - CALMAR_GAP_TRIGGER + 0.05))
        assert "유지 권장" in _verdict(just_under, self.BEST, None, 6.0)

    def test_defers_when_sample_too_short(self):
        cur = mk("kr_gem", "멀티에셋", 3.0, -40.0, 0.05)
        assert "판단 보류" in _verdict(cur, self.BEST, None, 1.5)

    def test_warns_when_worse_than_benchmark(self):
        cur = mk("kr_gem", "멀티에셋", 4.0, -30.0, 0.13)
        bench = mk("069500", "KOSPI200", 9.0, -25.0, 0.36, bh=True)
        v = _verdict(cur, self.BEST, bench, 6.0)
        assert "KOSPI200 단순 보유보다 못합니다" in v

    def test_handles_missing_current_strategy(self):
        assert "STRATEGY_KEY 확인" in _verdict(None, self.BEST, None, 6.0)


class TestBuildReview:
    def test_marks_current_strategy(self):
        results = [mk("kr_gem", "멀티에셋", 10.0, -20.0, 0.50),
                   mk("kr_growth", "성장주", 14.0, -20.0, 0.70)]
        msg = build_review(results, "kr_gem")
        assert "▶" in msg
        cur_line = next(l for l in msg.splitlines() if "▶" in l)
        assert "멀티에셋" in cur_line

    def test_sorted_by_calmar_desc(self):
        results = [mk("a", "AAA", 5.0, -20.0, 0.25),
                   mk("b", "BBB", 9.0, -18.0, 0.50),
                   mk("c", "CCC", 7.0, -15.0, 0.47)]
        msg = build_review(results, "a")
        order = [n for n in ("BBB", "CCC", "AAA") if n in msg]
        pos = [msg.index(n) for n in order]
        assert pos == sorted(pos), "Calmar 내림차순 정렬 아님"

    def test_includes_benchmark_row(self):
        results = [mk("kr_gem", "멀티에셋", 10.0, -20.0, 0.50),
                   mk("069500", "KOSPI200", 8.0, -25.0, 0.32, bh=True)]
        assert "KOSPI200" in build_review(results, "kr_gem")

    def test_reports_yearly_consistency(self):
        results = [mk("kr_gem", "멀티에셋", 10.0, -20.0, 0.50, yearly=[5.0, -3.0, 8.0])]
        msg = build_review(results, "kr_gem")
        assert "연간 플러스 2/3년" in msg

    def test_always_warns_against_frequent_switching(self):
        results = [mk("kr_gem", "멀티에셋", 10.0, -20.0, 0.50)]
        msg = build_review(results, "kr_gem")
        assert "자주 바꾸면" in msg, "성과 추종 경고가 빠지면 안 됨"

    def test_empty_results_is_graceful(self):
        assert "실패" in build_review([], "kr_gem")

    def test_is_pure_no_network(self, monkeypatch):
        """리포트 생성이 KIS 조회에 묶이면 안 된다(느려지고 장애에 취약해짐)."""
        import scanner.job_rebalance as jr

        def boom(*a, **k):
            raise AssertionError("build_review가 네트워크를 호출함")

        monkeypatch.setattr(jr, "get_account_holdings", boom)
        monkeypatch.setattr(jr, "get_current_price", boom)
        build_review([mk("kr_gem", "멀티에셋", 10.0, -20.0, 0.50)], "kr_gem")


class TestTaxNote:
    RES = [mk("kr_gem", "멀티에셋", 10.0, -20.0, 0.50)]

    def test_omitted_when_unknown(self):
        assert "세금" not in build_review(self.RES, "kr_gem", None)

    def test_shows_drag_when_taxable(self):
        tax = {"taxable_pct": 66.7, "effective_rate": 10.3, "drag_per_10pct": 1.03}
        msg = build_review(self.RES, "kr_gem", tax)
        assert "세금" in msg and "1.0%p" in msg
        assert "ISA" in msg, "절세 계좌 안내가 있어야 실제 조치로 이어진다"

    def test_notes_when_fully_tax_free(self):
        tax = {"taxable_pct": 0.0, "effective_rate": 0.0, "drag_per_10pct": 0.0}
        assert "비과세" in build_review(self.RES, "kr_gem", tax)

    def test_message_fits_telegram_limit(self):
        results = [mk(f"s{i}", f"전략{i}" * 3, 10.0 + i, -20.0, 0.5) for i in range(5)]
        results.append(mk("069500", "KOSPI200", 8.0, -25.0, 0.32, bh=True))
        assert len(build_review(results, "s0")) < 4096
