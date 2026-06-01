"""Tests for scanner.calendar — no external I/O."""
from datetime import datetime
from zoneinfo import ZoneInfo
from scanner.calendar import is_market_closed, count_weekdays, KST


class TestIsMarketClosed:
    def test_saturday_closed(self):
        sat = datetime(2024, 6, 1, 10, 0, tzinfo=KST)   # Saturday
        assert is_market_closed(sat)

    def test_sunday_closed(self):
        sun = datetime(2024, 6, 2, 10, 0, tzinfo=KST)   # Sunday
        assert is_market_closed(sun)

    def test_weekday_before_open_closed(self):
        dt = datetime(2024, 6, 3, 8, 59, tzinfo=KST)    # Monday 08:59
        assert is_market_closed(dt)

    def test_weekday_during_market_open(self):
        dt = datetime(2024, 6, 3, 10, 0, tzinfo=KST)    # Monday 10:00
        assert not is_market_closed(dt)

    def test_weekday_after_close(self):
        dt = datetime(2024, 6, 3, 15, 30, tzinfo=KST)   # Monday 15:30
        assert is_market_closed(dt)

    def test_chuseok_holiday(self):
        # Korean holiday (광복절 2024-08-15 is Thursday)
        dt = datetime(2024, 8, 15, 10, 0, tzinfo=KST)
        assert is_market_closed(dt)


class TestCountWeekdays:
    def test_same_day_zero(self):
        d = datetime(2024, 6, 3)
        assert count_weekdays(d, d) == 0

    def test_mon_to_fri_five_days(self):
        mon = datetime(2024, 6, 3)
        fri = datetime(2024, 6, 7)
        assert count_weekdays(mon, fri) == 4   # Mon→Tue→Wed→Thu = 4 weekdays

    def test_skips_weekends(self):
        fri = datetime(2024, 6, 7)
        mon = datetime(2024, 6, 10)
        assert count_weekdays(fri, mon) == 1   # only Friday counts
