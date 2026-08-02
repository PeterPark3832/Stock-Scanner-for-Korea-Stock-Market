"""기동 시 전략 리뷰 실행 판정 — 첫 배포 후 한 달을 기다리지 않게 하되 재시작 스팸은 막는다."""
from datetime import datetime, timedelta

import pytest

import scanner.job_strategy_review as sr
from scanner.calendar import KST


@pytest.fixture
def stamp(tmp_path, monkeypatch):
    p = str(tmp_path / "review_last.json")
    monkeypatch.setattr(sr, "_stamp_path", lambda: p)
    return p


class TestShouldRunStartupReview:
    def test_runs_when_never_executed(self, stamp):
        assert sr.should_run_startup_review() is True, "첫 배포에는 즉시 리포트가 나가야 함"

    def test_skips_right_after_a_run(self, stamp):
        sr._record_review()
        assert sr.should_run_startup_review() is False, "재시작마다 돌리면 스팸·FDR 낭비"

    def test_runs_again_after_interval(self, stamp):
        sr._record_review()
        future = datetime.now(KST) + timedelta(days=25)
        assert sr.should_run_startup_review(min_days=20, now=future) is True

    def test_respects_custom_interval(self, stamp):
        sr._record_review()
        soon = datetime.now(KST) + timedelta(days=5)
        assert sr.should_run_startup_review(min_days=20, now=soon) is False
        assert sr.should_run_startup_review(min_days=3, now=soon) is True

    def test_corrupt_stamp_is_treated_as_never_run(self, stamp):
        with open(stamp, "w", encoding="utf-8") as f:
            f.write("{{ broken")
        assert sr.should_run_startup_review() is True, "손상된 기록 때문에 리뷰가 영영 안 나가면 안 됨"

    def test_missing_ts_field_is_treated_as_never_run(self, stamp):
        with open(stamp, "w", encoding="utf-8") as f:
            f.write('{"other": 1}')
        assert sr.should_run_startup_review() is True


class TestRecordReview:
    def test_round_trip(self, stamp):
        assert sr.last_review_at() is None
        sr._record_review()
        got = sr.last_review_at()
        assert got is not None
        assert abs((datetime.now(KST) - got).total_seconds()) < 60

    def test_unwritable_path_does_not_raise(self, monkeypatch):
        """기록 실패가 리뷰 발송 자체를 깨뜨리면 안 된다."""
        monkeypatch.setattr(sr, "_stamp_path", lambda: "/nonexistent-dir/x.json")
        sr._record_review()   # 예외 없이 통과해야 함
