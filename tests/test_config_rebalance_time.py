"""REBALANCE_TIME 저유동성 구간 경고 — 개장 직후 ETF 시장가 체결 방지."""
import importlib

import pytest


def _reload_with(monkeypatch, value):
    monkeypatch.setenv("REBALANCE_TIME", value)
    import scanner.config as cfg
    importlib.reload(cfg)
    return cfg


@pytest.fixture(autouse=True)
def restore_config():
    yield
    import scanner.config as cfg
    importlib.reload(cfg)


class TestRebalanceTimeWarning:
    @pytest.mark.parametrize("t", ["09:00", "09:01", "09:05", "09:10"])
    def test_warns_inside_thin_liquidity_window(self, monkeypatch, t):
        cfg = _reload_with(monkeypatch, t)
        w = cfg.rebalance_time_warning()
        assert w is not None and "LP" in w, f"{t}는 경고 대상이어야 함"

    @pytest.mark.parametrize("t", ["08:59", "09:11", "10:00", "13:30", "15:00"])
    def test_silent_outside_window(self, monkeypatch, t):
        cfg = _reload_with(monkeypatch, t)
        assert cfg.rebalance_time_warning() is None, f"{t}는 경고 대상이 아님"

    def test_flags_malformed_value(self, monkeypatch):
        cfg = _reload_with(monkeypatch, "아홉시")
        w = cfg.rebalance_time_warning()
        assert w is not None and "형식" in w

    def test_default_is_outside_thin_window(self, monkeypatch):
        monkeypatch.delenv("REBALANCE_TIME", raising=False)
        import scanner.config as cfg
        importlib.reload(cfg)
        assert cfg.REBALANCE_TIME == "10:00"
        assert cfg.rebalance_time_warning() is None, "기본값이 저유동성 구간이면 안 됨"
