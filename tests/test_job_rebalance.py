"""Tests for scanner.job_rebalance — KIS·FDR 호출 없이 리밸런싱 플랜 로직 검증."""
import json
import pytest

import scanner.job_rebalance as jr


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """실수로 실제 API를 때리지 않도록 기본 차단."""
    monkeypatch.setattr(jr, "get_account_holdings", lambda: [])
    monkeypatch.setattr(jr, "get_order_possible_cash", lambda t, p: 0)
    monkeypatch.setattr(jr, "get_current_price", lambda tk: None)
    monkeypatch.setattr(jr, "compute_target_weights", lambda key: [])


class TestTotalValue:
    def test_cash_only(self):
        assert jr._total_value({}, 1_000_000) == 1_000_000

    def test_holdings_use_live_price(self, monkeypatch):
        monkeypatch.setattr(jr, "get_current_price",
                            lambda tk: {"current": 12_000})
        holdings = {"069500": {"ticker": "069500", "name": "K200",
                               "qty": 10, "avg_price": 10_000}}
        assert jr._total_value(holdings, 500_000) == 500_000 + 12_000 * 10

    def test_holdings_fall_back_to_avg_price(self):
        holdings = {"069500": {"ticker": "069500", "name": "K200",
                               "qty": 10, "avg_price": 10_000}}
        assert jr._total_value(holdings, 0) == 100_000


class TestPreviewRebalance:
    def test_target_qty_from_weight_with_cash_buffer(self, monkeypatch):
        monkeypatch.setattr(jr, "compute_target_weights", lambda key: [
            {"ticker": "069500", "name": "K200", "weight": 50.0, "price": 10_000},
            {"ticker": "132030", "name": "GOLD", "weight": 50.0, "price": 20_000},
        ])
        monkeypatch.setattr(jr, "get_order_possible_cash", lambda t, p: 1_000_000)
        monkeypatch.setattr(jr, "REBALANCE_CASH_BUFFER", 0.995)
        plan = jr.preview_rebalance()
        assert plan["total_value"] == 1_000_000
        by_tk = {r["ticker"]: r for r in plan["rows"]}
        # 버퍼 0.995 → 예산 995,000원. 슬롯당 497,500원 → 49주 / 24주(내림).
        # 잔돈 25,000원은 스윕이 더 미달인 132030에 1주 추가 → 25주.
        assert by_tk["069500"]["target_qty"] == 49
        assert by_tk["132030"]["target_qty"] == 25
        assert by_tk["069500"]["diff_qty"] == 49  # 신규 매수
        spent = sum(r["target_qty"] * r["price"] for r in plan["rows"])
        assert spent <= 1_000_000, "주문금액이 총자산 초과"

    def test_buffer_keeps_order_within_cash(self, monkeypatch):
        """전일 종가로 산출한 수량이 갭상승한 시가로도 현금 안에 들어와야 한다."""
        monkeypatch.setattr(jr, "compute_target_weights", lambda key: [
            {"ticker": "069500", "name": "K200", "weight": 100.0, "price": 10_000},
        ])
        monkeypatch.setattr(jr, "get_order_possible_cash", lambda t, p: 1_000_000)
        monkeypatch.setattr(jr, "REBALANCE_CASH_BUFFER", 0.995)
        qty = jr.preview_rebalance()["rows"][0]["target_qty"]
        gap_up_price = 10_000 * 1.004          # 개장 갭 +0.4%
        assert qty * gap_up_price <= 1_000_000, "갭상승 시 주문금액이 현금 초과 → 매수 실패"

    def test_zero_price_target_is_zero_qty(self, monkeypatch):
        monkeypatch.setattr(jr, "compute_target_weights", lambda key: [
            {"ticker": "114260", "name": "국고채", "weight": 100.0, "price": 0.0},
        ])
        monkeypatch.setattr(jr, "get_order_possible_cash", lambda t, p: 1_000_000)
        plan = jr.preview_rebalance()
        assert plan["rows"][0]["target_qty"] == 0

    def test_removed_holding_marked_full_sell(self, monkeypatch):
        monkeypatch.setattr(jr, "compute_target_weights", lambda key: [
            {"ticker": "069500", "name": "K200", "weight": 100.0, "price": 10_000},
        ])
        monkeypatch.setattr(jr, "get_account_holdings", lambda: [
            {"ticker": "069500", "name": "K200", "qty": 5, "avg_price": 9_000},
            {"ticker": "132030", "name": "GOLD", "qty": 3, "avg_price": 20_000},
        ])
        monkeypatch.setattr(jr, "get_order_possible_cash", lambda t, p: 100_000)
        monkeypatch.setattr(jr, "get_current_price",
                            lambda tk: {"current": {"069500": 10_000, "132030": 20_000}[tk]})
        plan = jr.preview_rebalance()
        by_tk = {r["ticker"]: r for r in plan["rows"]}
        # 목표에서 빠진 GOLD → 전량 매도
        assert by_tk["132030"]["diff_qty"] == -3
        assert by_tk["132030"]["target_qty"] == 0
        # 유니버스 밖 종목이 아니라 관리 종목만 계산에 포함됐는지: 총자산 일치
        assert plan["total_value"] == 100_000 + 5 * 10_000 + 3 * 20_000

    def test_sizes_with_live_price_not_stale_fdr_close(self, monkeypatch):
        """수량은 실시간가로 산출해야 한다. 전일 FDR 종가로 잡으면 갭 시 비중이 어긋난다."""
        monkeypatch.setattr(jr, "compute_target_weights", lambda key: [
            {"ticker": "069500", "name": "K200", "weight": 100.0, "price": 10_000},  # 전일 종가
        ])
        monkeypatch.setattr(jr, "get_current_price", lambda tk: {"current": 12_000})  # 갭상승
        monkeypatch.setattr(jr, "get_order_possible_cash", lambda t, p: 1_200_000)
        monkeypatch.setattr(jr, "REBALANCE_CASH_BUFFER", 1.0)
        row = jr.preview_rebalance()["rows"][0]
        assert row["target_qty"] == 100, "전일 종가(1만원)로 120주를 잡으면 현금 초과"
        assert row["price"] == 12_000, "행에도 사이징에 쓴 실시간가가 기록돼야 함"

    def test_falls_back_to_fdr_price_when_live_unavailable(self, monkeypatch):
        monkeypatch.setattr(jr, "compute_target_weights", lambda key: [
            {"ticker": "069500", "name": "K200", "weight": 100.0, "price": 10_000},
        ])
        monkeypatch.setattr(jr, "get_current_price", lambda tk: None)   # 시세 조회 실패
        monkeypatch.setattr(jr, "get_order_possible_cash", lambda t, p: 1_000_000)
        monkeypatch.setattr(jr, "REBALANCE_CASH_BUFFER", 1.0)
        row = jr.preview_rebalance()["rows"][0]
        assert row["target_qty"] == 100

    def test_ignores_non_universe_holdings(self, monkeypatch):
        """눌림목·수동 보유 종목(관리 유니버스 밖)은 리밸런싱 대상에서 제외."""
        monkeypatch.setattr(jr, "get_account_holdings", lambda: [
            {"ticker": "005930", "name": "삼성전자", "qty": 10, "avg_price": 70_000},
            {"ticker": "999999", "name": "잡주", "qty": 100, "avg_price": 1_000},
        ])
        holdings, cash = jr._current_state()
        assert "999999" not in holdings          # 유니버스 밖 → 무시
        assert "005930" in holdings              # kr_leaders 유니버스 포함 종목


class TestLeftoverCashSweep:
    def _rows(self, specs):
        return [{"ticker": tk, "name": tk, "weight": w, "price": p,
                 "current_qty": 0, "target_qty": int(0), "diff_qty": 0}
                for tk, w, p in specs]

    def test_never_exceeds_budget(self):
        rows = self._rows([("A", 50.0, 30_000), ("B", 50.0, 7_000)])
        budget = 1_000_000
        for r in rows:
            r["target_qty"] = int(budget * r["weight"] / 100 // r["price"])
        jr._sweep_leftover_cash(rows, budget)
        assert sum(r["target_qty"] * r["price"] for r in rows) <= budget

    def test_reduces_idle_cash(self):
        rows = self._rows([("A", 50.0, 30_000), ("B", 50.0, 7_000)])
        budget = 1_000_000
        for r in rows:
            r["target_qty"] = int(budget * r["weight"] / 100 // r["price"])
        before = budget - sum(r["target_qty"] * r["price"] for r in rows)
        jr._sweep_leftover_cash(rows, budget)
        after = budget - sum(r["target_qty"] * r["price"] for r in rows)
        assert after < before, "잔돈이 그대로 방치됨"

    def test_keeps_diff_qty_consistent(self):
        rows = self._rows([("A", 100.0, 9_000)])
        rows[0]["current_qty"] = 5
        rows[0]["target_qty"] = int(100_000 // 9_000)
        rows[0]["diff_qty"] = rows[0]["target_qty"] - 5
        jr._sweep_leftover_cash(rows, 100_000)
        assert rows[0]["diff_qty"] == rows[0]["target_qty"] - rows[0]["current_qty"]

    def test_skips_zero_price_rows(self):
        rows = self._rows([("A", 100.0, 0)])
        jr._sweep_leftover_cash(rows, 1_000_000)
        assert rows[0]["target_qty"] == 0, "가격 0인 종목에 수량을 배정하면 안 됨"

    def test_does_not_buy_when_unaffordable(self):
        """잔돈이 1주 값에 못 미치면 담지 않는다."""
        rows = self._rows([("A", 100.0, 90_000)])
        rows[0]["target_qty"] = 1          # 90,000 배정, 잔돈 10,000
        jr._sweep_leftover_cash(rows, 100_000)
        assert rows[0]["target_qty"] == 1

    def test_does_not_buy_when_all_slots_full(self):
        """모든 슬롯이 목표를 채웠으면 잔돈이 남아도 추가 매수하지 않는다."""
        rows = self._rows([("A", 100.0, 1_000)])
        rows[0]["target_qty"] = 100        # 100,000 = 목표 전액
        jr._sweep_leftover_cash(rows, 100_000)
        assert rows[0]["target_qty"] == 100


class TestReconcilePositions:
    PLAN = {"rows": [
        {"ticker": "069500", "name": "K200", "weight": 100.0, "price": 10_000,
         "current_qty": 0, "target_qty": 50, "diff_qty": 50},
        {"ticker": "132030", "name": "GOLD", "weight": 0.0, "price": 0.0,
         "current_qty": 3, "target_qty": 0, "diff_qty": -3},
    ]}

    @pytest.fixture
    def saved(self, monkeypatch):
        box = {}
        monkeypatch.setattr(jr, "load_positions", lambda: [
            {"ticker": "005930", "name": "삼성전자", "strategy": None},      # 눌림목(태그 없음)
            {"ticker": "132030", "name": "GOLD", "strategy": "kr_gem",
             "entry": 19_000, "entry_date": "2026-01-05"},                   # 옛 관리 종목
        ])
        monkeypatch.setattr(jr, "save_positions", lambda arr: box.update(v=arr))
        return box

    def test_uses_actual_broker_holdings(self, saved, monkeypatch):
        """잔고 조회가 되면 실제 보유 수량·매입평균가를 기록한다."""
        monkeypatch.setattr(jr, "get_account_holdings", lambda: [
            {"ticker": "069500", "name": "K200", "qty": 48, "avg_price": 10_150},
        ])
        results = [{"ticker": "069500", "side": "buy", "qty": 50,
                    "price": 10_100, "success": True}]
        jr._reconcile_positions(self.PLAN, results)
        arr = saved["v"]
        tickers = [p["ticker"] for p in arr]
        assert "005930" in tickers, "비관리(눌림목) 포지션은 보존"
        assert "132030" not in tickers, "매도된 옛 종목 제거"
        rec = next(p for p in arr if p["ticker"] == "069500")
        assert rec["quantity"] == 48, "목표 50이 아니라 실제 체결 48주를 기록해야 함"
        assert rec["entry"] == 10_150, "실제 매입평균가를 기록해야 함(FDR 종가 아님)"
        assert rec["strategy"] == jr.STRATEGY_KEY
        assert rec["target_weight"] == 100.0

    def test_failed_buy_is_not_recorded_as_held(self, saved, monkeypatch):
        """핵심 회귀: 매수 실패 시 보유한 것처럼 남으면 안 된다."""
        monkeypatch.setattr(jr, "get_account_holdings", lambda: [])   # 잔고 재조회 실패
        results = [{"ticker": "069500", "side": "buy", "qty": 50, "price": 10_100,
                    "success": False, "error": "주문가능금액 부족"},
                   {"ticker": "132030", "side": "sell", "qty": 3, "price": 20_000,
                    "success": True}]
        jr._reconcile_positions(self.PLAN, results)
        held = {p["ticker"] for p in saved["v"]}
        assert "069500" not in held, "실패한 매수가 보유로 기록됨 — 다음 달 주문·손익 왜곡"
        assert "132030" not in held, "성공한 매도는 보유에서 제거"
        assert "005930" in held

    def test_partial_fill_fallback_uses_successful_orders(self, saved, monkeypatch):
        monkeypatch.setattr(jr, "get_account_holdings", lambda: [])
        results = [{"ticker": "069500", "side": "buy", "qty": 50, "price": 10_100,
                    "success": True},
                   {"ticker": "132030", "side": "sell", "qty": 3, "price": 20_000,
                    "success": False, "error": "장 종료"}]
        jr._reconcile_positions(self.PLAN, results)
        by_tk = {p["ticker"]: p for p in saved["v"]}
        assert by_tk["069500"]["quantity"] == 50
        assert by_tk["069500"]["entry"] == 10_100, "체결가 기준 기록"
        # 매도 실패 → 3주 그대로 보유 중이어야 함
        assert by_tk["132030"]["quantity"] == 3

    def test_entry_date_preserved_for_continuing_holding(self, saved, monkeypatch):
        """계속 보유 중인 종목의 최초 진입일은 유지되어야 한다(보유기간 왜곡 방지)."""
        monkeypatch.setattr(jr, "get_account_holdings", lambda: [
            {"ticker": "132030", "name": "GOLD", "qty": 3, "avg_price": 19_000},
        ])
        jr._reconcile_positions(self.PLAN, [])
        rec = next(p for p in saved["v"] if p["ticker"] == "132030")
        assert rec["entry_date"] == "2026-01-05"


class TestSnapshotEquity:
    def _patch_snapshot_file(self, monkeypatch, tmp_path):
        snap_file = str(tmp_path / "equity_snapshots.json")
        monkeypatch.setattr(jr, "EQUITY_SNAPSHOT_FILE", snap_file)
        return snap_file

    def test_skips_when_holdings_query_fails(self, monkeypatch, tmp_path):
        """positions.json엔 보유가 있는데 KIS 잔고가 비면 0원 스냅샷 방지."""
        snap_file = self._patch_snapshot_file(monkeypatch, tmp_path)
        monkeypatch.setattr(jr, "load_positions", lambda: [
            {"ticker": "069500", "strategy": "kr_gem", "quantity": 10},
        ])
        sent = []
        monkeypatch.setattr(jr, "send_telegram", lambda msg: sent.append(msg))
        result = jr.snapshot_equity()
        assert result is None
        assert sent, "경고 텔레그램 발송"
        import os
        assert not os.path.exists(snap_file), "스냅샷 파일 미기록"

    def test_records_and_dedupes_today(self, monkeypatch, tmp_path):
        snap_file = self._patch_snapshot_file(monkeypatch, tmp_path)
        monkeypatch.setattr(jr, "load_positions", lambda: [])
        monkeypatch.setattr(jr, "get_order_possible_cash", lambda t, p: 500_000)
        first = jr.snapshot_equity()
        assert first is not None and first["total"] == 500_000

        monkeypatch.setattr(jr, "get_order_possible_cash", lambda t, p: 600_000)
        second = jr.snapshot_equity()
        assert second["total"] == 600_000

        with open(snap_file, encoding="utf-8") as f:
            arr = json.load(f)
        assert len(arr) == 1, "같은 날짜는 교체(dedupe)"
        assert arr[0]["total"] == 600_000
