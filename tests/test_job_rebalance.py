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
    def test_target_qty_from_weight(self, monkeypatch):
        monkeypatch.setattr(jr, "compute_target_weights", lambda key: [
            {"ticker": "069500", "name": "K200", "weight": 50.0, "price": 10_000},
            {"ticker": "132030", "name": "GOLD", "weight": 50.0, "price": 20_000},
        ])
        monkeypatch.setattr(jr, "get_order_possible_cash", lambda t, p: 1_000_000)
        plan = jr.preview_rebalance()
        assert plan["total_value"] == 1_000_000
        by_tk = {r["ticker"]: r for r in plan["rows"]}
        # 50만원/1만원 = 50주, 50만원/2만원 = 25주
        assert by_tk["069500"]["target_qty"] == 50
        assert by_tk["132030"]["target_qty"] == 25
        assert by_tk["069500"]["diff_qty"] == 50  # 신규 매수

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

    def test_ignores_non_universe_holdings(self, monkeypatch):
        """눌림목·수동 보유 종목(관리 유니버스 밖)은 리밸런싱 대상에서 제외."""
        monkeypatch.setattr(jr, "get_account_holdings", lambda: [
            {"ticker": "005930", "name": "삼성전자", "qty": 10, "avg_price": 70_000},
            {"ticker": "999999", "name": "잡주", "qty": 100, "avg_price": 1_000},
        ])
        holdings, cash = jr._current_state()
        assert "999999" not in holdings          # 유니버스 밖 → 무시
        assert "005930" in holdings              # kr_leaders 유니버스 포함 종목


class TestSaveRebalancePositions:
    def test_replaces_managed_keeps_others(self, monkeypatch, tmp_path):
        saved = {}
        monkeypatch.setattr(jr, "load_positions", lambda: [
            {"ticker": "005930", "name": "삼성전자", "strategy": None},      # 눌림목(태그 없음)
            {"ticker": "132030", "name": "GOLD", "strategy": "kr_gem"},      # 옛 관리 종목
        ])
        monkeypatch.setattr(jr, "save_positions", lambda arr: saved.update(arr={"v": arr}))
        plan = {"rows": [
            {"ticker": "069500", "name": "K200", "weight": 100.0, "price": 10_000,
             "current_qty": 0, "target_qty": 50, "diff_qty": 50},
            {"ticker": "132030", "name": "GOLD", "weight": 0.0, "price": 0.0,
             "current_qty": 3, "target_qty": 0, "diff_qty": -3},
        ]}
        jr._save_rebalance_positions(plan)
        arr = saved["arr"]["v"]
        tickers = [p["ticker"] for p in arr]
        assert "005930" in tickers, "비관리(눌림목) 포지션은 보존"
        assert "069500" in tickers, "신규 목표 보유 추가"
        assert "132030" not in tickers, "옛 관리 종목은 교체 제거"
        new = next(p for p in arr if p["ticker"] == "069500")
        assert new["quantity"] == 50
        assert new["strategy"] == jr.STRATEGY_KEY
        assert new["target_weight"] == 100.0


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
