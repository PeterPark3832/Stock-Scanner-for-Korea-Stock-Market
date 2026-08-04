"""자기주도 학습 — 측정·축소보정·신뢰도 게이팅 검증.

핵심 위험: 표본 3~10건에서 단순평균을 쓰면 운 나쁜 체결 한 건이 비용 가정을
왜곡해 전략 선택 전체가 틀어진다. 그 방어가 실제로 작동하는지가 이 테스트의 목적.
"""
import json

import pytest

from scanner import learn


def ev(ts, orders):
    return {"ts": ts, "total_value": 1_000_000, "cash": 0, "holdings": [], "orders": orders}


def buy(tk, planned, fill, qty=10, name=None):
    return {"ticker": tk, "name": name or tk, "side": "buy", "qty": qty,
            "planned_price": planned, "fill_price": fill}


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    p = str(tmp_path / "learning_state.json")
    monkeypatch.setattr(learn, "LEARNING_FILE", p)
    return p


class TestSlippageSamples:
    def test_extracts_buy_orders_with_both_prices(self):
        evs = [ev("2026-01-02", [buy("069500", 10_000, 10_030)])]
        s = learn.slippage_samples(evs)
        assert len(s) == 1
        assert s[0]["slippage"] == pytest.approx(0.003)

    def test_skips_orders_without_fill_price(self):
        """추가 매수는 평단이 섞여 체결가 복원 불가 → 학습에서 제외."""
        evs = [ev("2026-01-02", [{"ticker": "069500", "side": "buy", "qty": 5,
                                  "planned_price": 10_000, "fill_price": None}])]
        assert learn.slippage_samples(evs) == []

    def test_skips_sell_orders(self):
        evs = [ev("2026-01-02", [{"ticker": "069500", "side": "sell", "qty": 5,
                                  "planned_price": 10_000, "fill_price": 9_900}])]
        assert learn.slippage_samples(evs) == []

    def test_skips_zero_or_missing_planned(self):
        evs = [ev("2026-01-02", [buy("069500", 0, 10_000), buy("132030", 10_000, 0)])]
        assert learn.slippage_samples(evs) == []

    def test_negative_slippage_is_kept_as_measured(self):
        """계획보다 싸게 체결된 것도 측정값으로는 그대로 둔다(바닥은 보정 단계에서)."""
        evs = [ev("2026-01-02", [buy("069500", 10_000, 9_950)])]
        assert learn.slippage_samples(evs)[0]["slippage"] == pytest.approx(-0.005)

    def test_handles_malformed_event(self):
        assert learn.slippage_samples([{}, {"orders": None}]) == []


class TestSummarize:
    def test_empty(self):
        s = learn.summarize_slippage([])
        assert s["n"] == 0 and s["median"] is None

    def test_median_resists_single_outlier(self):
        """이상치 1건이 대표값을 끌고 가면 안 된다 — 중앙값을 쓰는 이유.
        (필터 한계 안쪽인 +15%짜리 나쁜 체결. 그보다 크면 데이터 오류로 보고 제외된다)"""
        evs = [ev("t", [buy("A", 10_000, 10_010), buy("B", 10_000, 10_010),
                        buy("C", 10_000, 10_010), buy("D", 10_000, 11_500)])]
        s = learn.summarize_slippage(learn.slippage_samples(evs))
        assert s["n"] == 4, "필터 한계 안쪽 값은 표본에 남아야 함"
        assert s["median"] == pytest.approx(0.001, abs=1e-6)
        assert s["mean"] > s["median"], "평균은 이상치에 끌려간다(대조군)"
        assert s["worst"] == pytest.approx(0.15)

    def test_groups_by_ticker(self):
        evs = [ev("t", [buy("A", 10_000, 10_020), buy("A", 10_000, 10_040),
                        buy("B", 10_000, 10_010)])]
        s = learn.summarize_slippage(learn.slippage_samples(evs))
        assert s["by_ticker"]["A"]["n"] == 2
        assert s["by_ticker"]["A"]["median"] == pytest.approx(0.003)


class TestShrink:
    def test_no_samples_returns_prior(self):
        assert learn.shrink(None, 0, 0.0015) == 0.0015
        assert learn.shrink(0.01, 0, 0.0015) == 0.0015

    def test_pulls_measurement_toward_prior(self):
        """측정 0.5%, 표본 6건 → 기본 0.15% 쪽으로 당겨져야 한다."""
        v = learn.shrink(0.005, 6, 0.0015, k=12)
        assert 0.0015 < v < 0.005
        assert v == pytest.approx((6 * 0.005 + 12 * 0.0015) / 18)

    def test_converges_to_measurement_with_many_samples(self):
        far = learn.shrink(0.005, 500, 0.0015, k=12)
        assert far == pytest.approx(0.005, abs=1e-4)

    def test_monotonic_in_sample_size(self):
        """표본이 늘수록 측정값에 단조 수렴해야 한다."""
        vals = [learn.shrink(0.005, n, 0.0015, k=12) for n in (1, 5, 20, 100)]
        assert vals == sorted(vals)


class TestCalibrate:
    def _summary(self, n, median):
        return {"n": n, "median": median, "mean": median, "worst": median, "by_ticker": {}}

    def test_below_threshold_keeps_prior(self):
        c = learn.calibrate(self._summary(learn.MIN_SAMPLES_TO_APPLY - 1, 0.01))
        assert c["applied"] is False
        assert c["slippage"] == learn.PRIOR_SLIPPAGE, "표본 부족인데 가정을 바꿈"

    def test_at_threshold_applies(self):
        c = learn.calibrate(self._summary(learn.MIN_SAMPLES_TO_APPLY, 0.01))
        assert c["applied"] is True
        assert c["slippage"] > learn.PRIOR_SLIPPAGE

    def test_negative_measurement_floored_at_zero(self):
        """유리한 체결이 이어져도 비용을 음수로 잡으면 백테스트가 낙관 왜곡된다."""
        c = learn.calibrate(self._summary(20, -0.01))
        assert c["slippage"] >= 0
        assert c["measured_median"] == 0.0

    def test_commission_is_not_learned(self):
        c = learn.calibrate(self._summary(50, 0.02))
        assert c["commission"] == learn.PRIOR_COMMISSION

    def test_empty_summary_is_safe(self):
        c = learn.calibrate({"n": 0, "median": None, "by_ticker": {}})
        assert c["slippage"] == learn.PRIOR_SLIPPAGE and c["applied"] is False


class TestStateRoundTrip:
    def test_learned_costs_defaults_without_state(self, state_file):
        assert learn.learned_costs() == (learn.PRIOR_SLIPPAGE, learn.PRIOR_COMMISSION)

    def test_run_learning_persists_and_reloads(self, state_file):
        evs = [ev("2026-01-02", [buy(f"T{i}", 10_000, 10_030) for i in range(8)])]
        learn.run_learning(evs)
        slip, comm = learn.learned_costs()
        assert slip > learn.PRIOR_SLIPPAGE
        assert comm == learn.PRIOR_COMMISSION
        with open(state_file, encoding="utf-8") as f:
            assert json.load(f)["costs"]["applied"] is True

    def test_corrupt_state_falls_back_to_prior(self, state_file):
        with open(state_file, "w", encoding="utf-8") as f:
            f.write("{{broken")
        assert learn.learned_costs() == (learn.PRIOR_SLIPPAGE, learn.PRIOR_COMMISSION)

    def test_garbage_values_rejected(self, state_file):
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump({"costs": {"slippage": "많이", "commission": -5}}, f)
        assert learn.learned_costs() == (learn.PRIOR_SLIPPAGE, learn.PRIOR_COMMISSION)

    def test_persist_false_does_not_write(self, state_file):
        import os
        learn.run_learning([ev("t", [buy("A", 10_000, 10_030)])], persist=False)
        assert not os.path.exists(state_file)


class TestReport:
    def test_no_samples_message(self, state_file):
        r = learn.run_learning([], persist=False)
        msg = learn.build_report(r)
        assert "표본 없음" in msg and "기본값" in msg

    def test_too_few_samples_withholds_numbers(self, state_file):
        r = learn.run_learning([ev("t", [buy("A", 10_000, 10_030)])], persist=False)
        msg = learn.build_report(r)
        assert "너무 적" in msg
        assert "중앙값" not in msg, "표본 부족인데 수치를 단정함"

    def test_reports_numbers_and_hold_when_between_thresholds(self, state_file):
        orders = [buy(f"T{i}", 10_000, 10_030) for i in range(learn.MIN_SAMPLES_TO_REPORT)]
        r = learn.run_learning([ev("t", orders)], persist=False)
        msg = learn.build_report(r)
        assert "중앙값" in msg
        assert "자동 보정 보류" in msg

    def test_reports_applied_calibration(self, state_file):
        orders = [buy(f"T{i}", 10_000, 10_030) for i in range(10)]
        r = learn.run_learning([ev("t", orders)], persist=False)
        msg = learn.build_report(r)
        assert "자동 보정" in msg and "축소보정" in msg

    def test_advice_escalates_with_slippage(self, state_file):
        mild = learn.run_learning([ev("t", [buy(f"T{i}", 10_000, 10_005) for i in range(8)])],
                                  persist=False)
        bad = learn.run_learning([ev("t", [buy(f"T{i}", 10_000, 10_500) for i in range(8)])],
                                 persist=False)
        assert "🟢" in learn.build_report(mild)
        assert "🔴" in learn.build_report(bad)
        assert "REBALANCE_TIME" in learn.build_report(bad)

    def test_flags_illiquid_tickers(self, state_file):
        orders = [buy("BAD", 10_000, 10_080), buy("BAD", 10_000, 10_090)] + \
                 [buy(f"T{i}", 10_000, 10_005) for i in range(6)]
        r = learn.run_learning([ev("t", orders)], persist=False)
        msg = learn.build_report(r)
        assert "BAD" in msg

    def test_message_fits_telegram_limit(self, state_file):
        orders = [buy(f"T{i%9}", 10_000, 10_030 + i) for i in range(60)]
        r = learn.run_learning([ev("t", orders)], persist=False)
        assert len(learn.build_report(r)) < 4096


class TestLoadEvents:
    def test_missing_file(self, tmp_path):
        assert learn.load_events(str(tmp_path / "nope.json")) == []

    def test_corrupt_file(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{{", encoding="utf-8")
        assert learn.load_events(str(p)) == []

    def test_non_list_json(self, tmp_path):
        p = tmp_path / "obj.json"
        p.write_text('{"a":1}', encoding="utf-8")
        assert learn.load_events(str(p)) == []


class TestOutlierRejection:
    """표본이 적을수록 데이터 오류 1건이 비용 가정을 통째로 왜곡한다."""

    def test_rejects_implausible_slippage(self):
        evs = [ev("t", [buy("BAD", 10_000, 1_000_000)])]   # 100배 — 데이터 오류
        assert learn.slippage_samples(evs) == []

    def test_rejects_implausible_negative(self):
        evs = [ev("t", [buy("BAD", 10_000, 100)])]         # -99%
        assert learn.slippage_samples(evs) == []

    def test_keeps_boundary_value(self):
        evs = [ev("t", [buy("OK", 10_000, int(10_000 * (1 + learn.MAX_PLAUSIBLE_SLIPPAGE)))])]
        assert len(learn.slippage_samples(evs)) == 1

    def test_outlier_does_not_corrupt_calibration(self, state_file):
        """오류 1건이 섞여도 보정값이 정상 범위를 유지해야 한다."""
        good = [buy(f"T{i}", 10_000, 10_030) for i in range(8)]
        r = learn.run_learning([ev("t", good + [buy("BAD", 10_000, 5_000_000)])],
                               persist=False)
        assert r["summary"]["n"] == 8, "이상치가 표본에 포함됨"
        assert r["costs"]["slippage"] < 0.01, "이상치가 비용 가정을 오염시킴"
