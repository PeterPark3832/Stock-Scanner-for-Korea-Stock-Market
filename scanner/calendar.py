"""장 개폐장 판단·영업일 계산."""
import holidays
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def is_market_closed(dt: datetime) -> bool:
    d = dt.date() if hasattr(dt, "date") else dt
    if d.weekday() >= 5:
        return True
    if d in holidays.KR(years=d.year):
        return True
    if hasattr(dt, "hour"):
        h, m = dt.hour, dt.minute
        if (h, m) < (9, 0) or (h, m) >= (15, 30):
            return True
    return False


def is_first_trading_day_of_month(dt: datetime) -> bool:
    """dt가 해당 월의 첫 영업일(주말·공휴일 제외)인지 여부."""
    d = dt.date() if hasattr(dt, "date") else dt
    if d.weekday() >= 5 or d in holidays.KR(years=d.year):
        return False
    probe = d.replace(day=1)
    while probe.weekday() >= 5 or probe in holidays.KR(years=probe.year):
        probe += timedelta(days=1)
    return probe == d


def count_weekdays(start: datetime, end: datetime) -> int:
    cur = start.date() if hasattr(start, "date") else start
    end = end.date() if hasattr(end, "date") else end
    days = 0
    while cur < end:
        if cur.weekday() < 5:
            days += 1
        cur += timedelta(days=1)
    return days
