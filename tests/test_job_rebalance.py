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
    # 기본은 빈 장부 — ambient positions.json에 영향받지 않도록 격리
    # (보유 기대 가드를 검증하는 테스트는 개별적으로 재정의한다).
    monkeypatch.setattr(jr, "load_positions", lambda: [])
    # 체결 반영 대기는 실거래용 — 테스트에서 실제로 잠들 이유가 없다.
    monkeypatch.setattr(jr, "ORDER_SETTLE_WAIT", 0)
    # 예수금 조회는 표시 전용 — 기본은 None(폴백은 get_order_possible_cash).
    monkeypatch.setattr(jr, "get_deposit_balance", lambda: None)


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

    def test_zero_price_target_aborts_instead_of_ordering_zero(self, monkeypatch):
        """가격을 못 구하면 수량을 정할 수 없다 → 0주 주문이 아니라 계획 자체를 중단."""
        monkeypatch.setattr(jr, "compute_target_weights", lambda key: [
            {"ticker": "114260", "name": "국고채", "weight": 100.0, "price": 0.0},
        ])
        monkeypatch.setattr(jr, "get_order_possible_cash", lambda t, p: 1_000_000)
        plan = jr.preview_rebalance()
        assert plan["actionable"] is False
        assert plan["rows"] == []

    def test_aborts_when_holdings_query_fails(self, monkeypatch):
        """잔고조회 API 장애(None)면 현재 보유를 0으로 착각해 전량 매도하지 말고 중단."""
        monkeypatch.setattr(jr, "compute_target_weights", lambda key: [
            {"ticker": "069500", "name": "K200", "weight": 100.0, "price": 10_000},
        ])
        monkeypatch.setattr(jr, "get_account_holdings", lambda: None)  # API 장애
        monkeypatch.setattr(jr, "load_positions", lambda: [
            {"ticker": "069500", "strategy": "kr_gem", "quantity": 10},
        ])
        monkeypatch.setattr(jr, "get_order_possible_cash", lambda t, p: 1_000_000)
        plan = jr.preview_rebalance()
        assert plan["actionable"] is False
        assert plan["rows"] == [], "조회 장애인데 매도 행 생성 — 전량 청산 위험"

    def test_proceeds_when_account_genuinely_empty(self, monkeypatch):
        """정상 조회로 빈 계좌([])면 — 신규 계좌든 사용자가 전량 매도했든 — 목표대로
        재진입 매수를 진행한다(장애 None과 구분). positions.json 잔재는 무시."""
        monkeypatch.setattr(jr, "compute_target_weights", lambda key: [
            {"ticker": "069500", "name": "K200", "weight": 100.0, "price": 10_000},
        ])
        monkeypatch.setattr(jr, "get_account_holdings", lambda: [])    # 정상 조회·빈 계좌
        monkeypatch.setattr(jr, "load_positions", lambda: [           # 옛 보유 잔재
            {"ticker": "069500", "strategy": "kr_gem", "quantity": 10},
        ])
        monkeypatch.setattr(jr, "get_order_possible_cash", lambda t, p: 1_000_000)
        monkeypatch.setattr(jr, "get_current_price", lambda tk: {"current": 10_000})
        plan = jr.preview_rebalance()
        assert plan["actionable"] is True and plan["rows"], "빈 계좌 재진입 매수가 막힘"
        assert all(r["diff_qty"] >= 0 for r in plan["rows"]), "빈 계좌인데 매도 행 발생"

    def test_aborts_when_cash_query_fails(self, monkeypatch):
        """주문가능금액 조회 실패(None)면 총자산이 과소계상돼 보유가 전부 소폭 매도로
        잡힌다 → 사이징 근거 없음으로 중단(현 보유 유지)."""
        monkeypatch.setattr(jr, "compute_target_weights", lambda key: [
            {"ticker": "069500", "name": "K200", "weight": 100.0, "price": 10_000},
        ])
        monkeypatch.setattr(jr, "get_account_holdings", lambda: [
            {"ticker": "069500", "name": "K200", "qty": 50, "avg_price": 10_000},
        ])
        monkeypatch.setattr(jr, "load_positions", lambda: [
            {"ticker": "069500", "strategy": "kr_gem", "quantity": 50},
        ])
        monkeypatch.setattr(jr, "get_order_possible_cash", lambda t, p: None)  # 현금 조회 실패
        monkeypatch.setattr(jr, "get_current_price", lambda tk: {"current": 10_000})
        plan = jr.preview_rebalance()
        assert plan["actionable"] is False
        assert plan["rows"] == [], "현금 조회 실패인데 매도 행 생성 — 유령 총자산으로 청산 위험"

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


class TestWaitForCash:
    def test_returns_as_soon_as_cash_available(self, monkeypatch):
        seq = iter([100, 100, 5_000])
        monkeypatch.setattr(jr, "get_order_possible_cash", lambda t, p: next(seq))
        monkeypatch.setattr(jr.time, "sleep", lambda s: None)
        assert jr._wait_for_cash(1_000, before=0, timeout=30, interval=0) >= 1_000

    def test_times_out_without_blocking_forever(self, monkeypatch):
        monkeypatch.setattr(jr, "get_order_possible_cash", lambda t, p: 0)
        monkeypatch.setattr(jr.time, "sleep", lambda s: None)
        clock = iter([0, 1, 2, 3, 99, 99, 99])
        monkeypatch.setattr(jr.time, "time", lambda: next(clock))
        assert jr._wait_for_cash(1_000, before=0, timeout=5, interval=0) == 0

    def test_tolerates_query_failure(self, monkeypatch):
        monkeypatch.setattr(jr, "get_order_possible_cash", lambda t, p: None)
        monkeypatch.setattr(jr.time, "sleep", lambda s: None)
        clock = iter([0, 1, 2, 99, 99, 99])
        monkeypatch.setattr(jr.time, "time", lambda: next(clock))
        assert jr._wait_for_cash(1_000, before=777, timeout=5, interval=0) == 777


class TestDataOutageGuard:
    """회귀 방지: 시세 데이터 장애가 '전량 청산'으로 이어지면 안 된다."""

    HELD = [{"ticker": "069500", "name": "K200", "qty": 100, "avg_price": 10_000},
            {"ticker": "133690", "name": "나스닥100", "qty": 50, "avg_price": 20_000}]

    def test_no_targets_yields_unactionable_plan(self, monkeypatch):
        monkeypatch.setattr(jr, "compute_target_weights", lambda key: [])
        monkeypatch.setattr(jr, "get_account_holdings", lambda: self.HELD)
        monkeypatch.setattr(jr, "get_current_price", lambda tk: {"current": 10_000})
        plan = jr.preview_rebalance()
        assert plan["actionable"] is False
        assert plan["rows"] == [], "빈 목표인데 매도 행이 생성됨 — 전량 청산 위험"

    def test_execute_places_no_orders_on_outage(self, monkeypatch):
        monkeypatch.setattr(jr, "compute_target_weights", lambda key: [])
        monkeypatch.setattr(jr, "get_account_holdings", lambda: self.HELD)
        monkeypatch.setattr(jr, "get_current_price", lambda tk: {"current": 10_000})
        placed, sent = [], []
        monkeypatch.setattr(jr, "place_order", lambda *a, **k: placed.append(a))
        monkeypatch.setattr(jr, "send_telegram", lambda m: sent.append(m))
        saved = []
        monkeypatch.setattr(jr, "save_positions", lambda arr: saved.append(arr))
        res = jr.execute_rebalance()
        assert res["aborted"] is True
        assert placed == [], "장애 상황에서 주문이 나감"
        assert saved == [], "장애 상황에서 포지션이 덮어써짐"
        assert sent and "중단" in sent[0]

    def test_unpriced_target_aborts(self, monkeypatch):
        """목표는 나왔지만 체결가를 못 구하면 수량을 못 정하므로 중단."""
        monkeypatch.setattr(jr, "compute_target_weights", lambda key: [
            {"ticker": "069500", "name": "K200", "weight": 100.0, "price": 0.0},
        ])
        monkeypatch.setattr(jr, "get_account_holdings", lambda: self.HELD)
        monkeypatch.setattr(jr, "get_current_price", lambda tk: None)
        plan = jr.preview_rebalance()
        assert plan["actionable"] is False
        assert "체결가" in plan["reason"]

    def test_normal_plan_stays_actionable(self, monkeypatch):
        monkeypatch.setattr(jr, "compute_target_weights", lambda key: [
            {"ticker": "069500", "name": "K200", "weight": 100.0, "price": 10_000},
        ])
        monkeypatch.setattr(jr, "get_order_possible_cash", lambda t, p: 1_000_000)
        monkeypatch.setattr(jr, "get_current_price", lambda tk: {"current": 10_000})
        plan = jr.preview_rebalance()
        assert plan["actionable"] is True and plan["rows"]


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
        """잔고조회 API 장애(None)면 0원 스냅샷을 남기지 않고 건너뛴다."""
        snap_file = self._patch_snapshot_file(monkeypatch, tmp_path)
        monkeypatch.setattr(jr, "get_account_holdings", lambda: None)  # API 장애
        sent = []
        monkeypatch.setattr(jr, "send_telegram", lambda msg: sent.append(msg))
        result = jr.snapshot_equity()
        assert result is None
        assert sent, "경고 텔레그램 발송"
        import os
        assert not os.path.exists(snap_file), "스냅샷 파일 미기록"

    def test_records_empty_account_after_manual_sell(self, monkeypatch, tmp_path):
        """전량 매도로 빈 계좌([])면 — 장애가 아니므로 — 현금만 있는 스냅샷을 정상 기록."""
        self._patch_snapshot_file(monkeypatch, tmp_path)
        monkeypatch.setattr(jr, "get_account_holdings", lambda: [])    # 정상·빈 계좌
        monkeypatch.setattr(jr, "get_deposit_balance", lambda: 4_290_502)  # 매도대금 포함 예수금
        snap = jr.snapshot_equity()
        assert snap is not None
        assert snap["equity"] == 0 and snap["cash"] == 4_290_502
        assert snap["total"] == 4_290_502, "매도대금 포함 예수금이 총자산에 반영"

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


class TestFillPriceInstrumentation:
    """학습 재료 계측 — 부정확한 체결가로 학습하면 비용 추정이 오히려 나빠진다."""

    def test_records_fill_for_new_position(self):
        results = [{"ticker": "069500", "side": "buy", "qty": 10,
                    "qty_before": 0, "success": True}]
        jr._attach_fill_prices(results, {"069500": {"qty": 10, "avg_price": 10_150}})
        assert results[0]["fill_price"] == 10_150

    def test_skips_add_on_buy_where_average_is_blended(self):
        """기존 보유에 추가 매수하면 매입평균가가 섞여 체결가를 복원할 수 없다."""
        results = [{"ticker": "069500", "side": "buy", "qty": 10,
                    "qty_before": 5, "success": True}]
        jr._attach_fill_prices(results, {"069500": {"qty": 15, "avg_price": 10_100}})
        assert "fill_price" not in results[0]

    def test_skips_when_quantity_mismatch(self):
        """부분 체결이면 평균가가 주문 수량과 안 맞는다 → 기록하지 않음."""
        results = [{"ticker": "069500", "side": "buy", "qty": 10,
                    "qty_before": 0, "success": True}]
        jr._attach_fill_prices(results, {"069500": {"qty": 7, "avg_price": 10_150}})
        assert "fill_price" not in results[0]

    def test_skips_failed_and_sell_orders(self):
        results = [{"ticker": "069500", "side": "buy", "qty": 10,
                    "qty_before": 0, "success": False},
                   {"ticker": "132030", "side": "sell", "qty": 3,
                    "qty_before": 3, "success": True}]
        jr._attach_fill_prices(results, {"069500": {"qty": 10, "avg_price": 10_150},
                                         "132030": {"qty": 0, "avg_price": 20_000}})
        assert all("fill_price" not in r for r in results)

    def test_empty_snapshot_is_safe(self):
        results = [{"ticker": "069500", "side": "buy", "qty": 10,
                    "qty_before": 0, "success": True}]
        jr._attach_fill_prices(results, {})
        assert "fill_price" not in results[0]

    def test_log_carries_planned_and_fill(self, monkeypatch, tmp_path):
        """리밸런싱 로그에 계획가·체결가가 남아야 학습이 가능하다."""
        import json as _json
        log_file = str(tmp_path / "rebalance_log.json")
        monkeypatch.setattr(jr, "REBALANCE_LOG_FILE", log_file)
        plan = {"total_value": 1_000_000, "cash": 0, "rows": [
            {"ticker": "069500", "name": "K200", "weight": 100.0,
             "price": 10_000, "target_qty": 10}]}
        results = [{"ticker": "069500", "name": "K200", "side": "buy", "qty": 10,
                    "price": 10_150, "planned_price": 10_000, "fill_price": 10_150,
                    "pnl_pct": None, "success": True}]
        jr._record_rebalance_log(plan, results)
        with open(log_file, encoding="utf-8") as f:
            order = _json.load(f)[0]["orders"][0]
        assert order["planned_price"] == 10_000 and order["fill_price"] == 10_150

    def test_reconcile_reuses_shared_snapshot(self, monkeypatch):
        """계측과 동기화가 같은 스냅샷을 써야 한다 — 재조회하면 상태가 어긋난다."""
        calls = []
        monkeypatch.setattr(jr, "get_account_holdings",
                            lambda: calls.append(1) or [])
        monkeypatch.setattr(jr, "save_positions", lambda arr: None)
        plan = {"rows": [{"ticker": "069500", "name": "K200", "weight": 100.0,
                          "price": 10_000, "current_qty": 0, "target_qty": 10,
                          "diff_qty": 10}]}
        snap = {"069500": {"ticker": "069500", "name": "K200", "qty": 10,
                           "avg_price": 10_150}}
        jr._reconcile_positions(plan, [], snap)
        assert calls == [], "스냅샷을 넘겼는데 잔고를 다시 조회함"
