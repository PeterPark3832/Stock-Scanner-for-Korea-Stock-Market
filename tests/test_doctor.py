"""배포 점검 — 손실을 유발하는 설정을 실제로 잡아내는지 검증."""
import importlib

import pytest

from scanner import doctor
from scanner.doctor import OK, WARN, FAIL


@pytest.fixture(autouse=True)
def restore_config():
    yield
    import scanner.config as cfg
    importlib.reload(cfg)
    importlib.reload(doctor)


def _reload(monkeypatch, **env):
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, str(v))
    import scanner.config as cfg
    importlib.reload(cfg)
    importlib.reload(doctor)
    return doctor


class TestRebalanceTimeCheck:
    def test_fails_on_thin_liquidity_window(self, monkeypatch):
        d = _reload(monkeypatch, REBALANCE_TIME="09:05")
        c = d.check_rebalance_time()
        assert c.status == FAIL, "09:05는 ETF LP 호가 공백 구간 — 반드시 잡아야 함"
        assert "10:00" in c.fix

    def test_passes_on_liquid_window(self, monkeypatch):
        d = _reload(monkeypatch, REBALANCE_TIME="10:00")
        assert d.check_rebalance_time().status == OK


class TestCashBufferCheck:
    def test_warns_when_no_buffer(self, monkeypatch):
        d = _reload(monkeypatch, REBALANCE_CASH_BUFFER="1.0")
        assert d.check_cash_buffer().status == WARN

    def test_fails_on_absurd_value(self, monkeypatch):
        d = _reload(monkeypatch, REBALANCE_CASH_BUFFER="0.5")
        assert d.check_cash_buffer().status == FAIL

    def test_passes_on_default(self, monkeypatch):
        d = _reload(monkeypatch, REBALANCE_CASH_BUFFER="0.995")
        assert d.check_cash_buffer().status == OK


class TestStrategyCheck:
    def test_fails_on_unknown_strategy(self, monkeypatch):
        d = _reload(monkeypatch, STRATEGY_MODE="rebalance", STRATEGY_KEY="없는전략")
        c = d.check_strategy()
        assert c.status == FAIL
        assert "kr_gem" in c.fix, "사용 가능한 전략 목록을 안내해야 함"

    def test_passes_on_valid_strategy(self, monkeypatch):
        d = _reload(monkeypatch, STRATEGY_MODE="rebalance", STRATEGY_KEY="kr_ensemble")
        c = d.check_strategy()
        assert c.status == OK and "앙상블" in c.detail

    def test_warns_on_legacy_mode(self, monkeypatch):
        d = _reload(monkeypatch, STRATEGY_MODE="breakout")
        assert d.check_strategy().status == WARN


class TestAutoTradeCheck:
    def test_fails_when_enabled_without_account(self, monkeypatch):
        d = _reload(monkeypatch, AUTO_TRADE="true", KIS_ACCOUNT_NO="")
        c = d.check_auto_trade()
        assert c.status == FAIL, "주문이 전부 실패하는 조합"

    def test_warns_when_disabled(self, monkeypatch):
        d = _reload(monkeypatch, AUTO_TRADE="false", KIS_ACCOUNT_NO="50071234-01")
        assert d.check_auto_trade().status == WARN

    def test_passes_when_configured(self, monkeypatch):
        d = _reload(monkeypatch, AUTO_TRADE="true", KIS_ACCOUNT_NO="50071234-01")
        c = d.check_auto_trade()
        assert c.status == OK
        assert "50071234" not in c.detail, "계좌번호 전체가 노출되면 안 됨"


class TestReviewScheduleCheck:
    def test_warns_when_disabled(self, monkeypatch):
        d = _reload(monkeypatch, STRATEGY_REVIEW_DAY="0")
        assert d.check_review_schedule().status == WARN

    def test_passes_when_scheduled(self, monkeypatch):
        d = _reload(monkeypatch, STRATEGY_REVIEW_DAY="25")
        assert d.check_review_schedule().status == OK


class TestTaxExposureCheck:
    def test_flags_heavy_taxable_exposure(self, monkeypatch):
        monkeypatch.setattr(doctor, "__name__", doctor.__name__)
        import scanner.job_strategy_review as sr
        monkeypatch.setattr(sr, "current_tax_profile",
                            lambda: {"taxable_pct": 100.0, "effective_rate": 15.4,
                                     "drag_per_10pct": 1.54})
        c = doctor.check_tax_exposure()
        assert c.status == FAIL and "ISA" in c.fix

    def test_ok_when_tax_free(self, monkeypatch):
        import scanner.job_strategy_review as sr
        monkeypatch.setattr(sr, "current_tax_profile",
                            lambda: {"taxable_pct": 0.0, "effective_rate": 0.0,
                                     "drag_per_10pct": 0.0})
        assert doctor.check_tax_exposure().status == OK


class TestRunAndReport:
    def test_config_only_never_touches_network(self, monkeypatch):
        import scanner.job_rebalance as jr

        def boom(*a, **k):
            raise AssertionError("--no-net 인데 네트워크 호출")

        monkeypatch.setattr(jr, "get_account_holdings", boom)
        checks = doctor.run(include_network=False)
        assert len(checks) == len(doctor.CONFIG_CHECKS)

    def test_one_broken_check_does_not_abort_others(self, monkeypatch):
        def explode():
            raise RuntimeError("boom")

        monkeypatch.setattr(doctor, "CONFIG_CHECKS", [explode, doctor.check_cash_buffer])
        checks = doctor.run(include_network=False)
        assert len(checks) == 2
        assert checks[0].status == WARN

    def test_report_flags_failures(self):
        checks = [doctor.Check("A", FAIL, "문제", "고치세요"),
                  doctor.Check("B", OK, "정상")]
        out = doctor.format_report(checks)
        assert "조치 필요 1건" in out and "고치세요" in out

    def test_report_all_clear(self):
        out = doctor.format_report([doctor.Check("A", OK, "정상")])
        assert "배포 가능" in out

    def test_exit_code_nonzero_on_failure(self, monkeypatch):
        monkeypatch.setattr(doctor, "CONFIG_CHECKS",
                            [lambda: doctor.Check("X", FAIL, "bad")])
        monkeypatch.setattr("sys.argv", ["doctor", "--no-net"])
        assert doctor.main() == 1

    def test_exit_code_zero_when_clean(self, monkeypatch):
        monkeypatch.setattr(doctor, "CONFIG_CHECKS",
                            [lambda: doctor.Check("X", OK, "good")])
        monkeypatch.setattr("sys.argv", ["doctor", "--no-net"])
        assert doctor.main() == 0
