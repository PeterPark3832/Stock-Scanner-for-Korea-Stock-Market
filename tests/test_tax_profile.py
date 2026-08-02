"""과세 구조 계산 — 세전 모멘텀만 보는 전략에서 보이지 않던 최대 비용."""
import pytest

from scanner.strategy_rebalance import (
    TAX_RATE_OTHER_ETF, TAX_FREE_TICKERS, is_tax_free, tax_profile,
    STRATEGIES, universe_for,
)


class TestIsTaxFree:
    @pytest.mark.parametrize("tk", ["069500", "229200", "091160"])
    def test_domestic_equity_etf_is_tax_free(self, tk):
        assert is_tax_free(tk)

    @pytest.mark.parametrize("tk", ["133690", "143850", "132030", "114260", "153130"])
    def test_overseas_commodity_bond_etf_is_taxable(self, tk):
        assert not is_tax_free(tk), f"{tk}는 기타 ETF — 매매차익 15.4% 과세"

    def test_domestic_single_stocks_are_tax_free(self):
        for tk in ("005930", "000660", "207940"):
            assert is_tax_free(tk)

    def test_unknown_ticker_treated_as_taxable(self):
        """모르는 종목은 보수적으로 과세로 본다(세부담 과소평가 방지)."""
        assert not is_tax_free("999999")


class TestTaxProfile:
    def test_all_tax_free(self):
        t = tax_profile({"069500": 50.0, "229200": 50.0})
        assert t["taxable_pct"] == 0.0
        assert t["effective_rate"] == 0.0
        assert t["drag_per_10pct"] == 0.0

    def test_all_taxable(self):
        t = tax_profile({"133690": 50.0, "143850": 50.0})
        assert t["taxable_pct"] == 100.0
        assert t["effective_rate"] == pytest.approx(TAX_RATE_OTHER_ETF * 100, abs=0.01)
        # 세전 10% 수익 → 1.54%p 세금
        assert t["drag_per_10pct"] == pytest.approx(1.54, abs=0.01)

    def test_mixed_is_weighted(self):
        t = tax_profile({"069500": 50.0, "133690": 50.0})
        assert t["taxable_pct"] == 50.0
        assert t["drag_per_10pct"] == pytest.approx(0.77, abs=0.01)

    def test_uses_value_weights_not_counts(self):
        """종목 수가 아니라 금액 비중으로 계산해야 한다."""
        t = tax_profile({"069500": 90.0, "133690": 10.0})
        assert t["taxable_pct"] == 10.0

    def test_accepts_arbitrary_totals(self):
        """비중이 아니라 평가금액(원)을 그대로 넣어도 동작."""
        t = tax_profile({"069500": 700_000, "133690": 300_000})
        assert t["taxable_pct"] == 30.0

    def test_empty_is_safe(self):
        t = tax_profile({})
        assert t["taxable_pct"] == 0.0

    def test_zero_values_are_safe(self):
        t = tax_profile({"069500": 0.0, "133690": 0.0})
        assert t["taxable_pct"] == 0.0


class TestUniverseCoverage:
    def test_every_managed_ticker_has_a_tax_classification(self):
        """분류 누락 시 조용히 '과세'로 처리되므로, 의도한 분류인지 명시적으로 확인."""
        classified = set()
        for key in STRATEGIES:
            classified |= set(universe_for(key))
        # 비과세 목록에 없는 티커는 전부 '기타 ETF(과세)'로 의도된 것이어야 한다
        taxable = {tk for tk in classified if tk not in TAX_FREE_TICKERS}
        expected_taxable = {"133690", "143850", "132030", "114260", "153130"}
        assert taxable == expected_taxable, f"분류 미검토 티커: {taxable ^ expected_taxable}"
