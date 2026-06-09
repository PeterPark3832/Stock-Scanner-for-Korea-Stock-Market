"""순수 분석 함수 — RSI, 신호점수, KOSPI 시장 컨디션."""
import pandas as pd
try:
    import FinanceDataReader as fdr
except ImportError:
    fdr = None  # type: ignore[assignment]

from scanner.config import STRATEGY
from scanner.logger import log
from scanner.fdr import fdr_data_reader


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - 100 / (1 + rs)


def calc_signal_score(stock: dict) -> int:
    score = 0.0
    body_pct  = stock.get("bo_body_pct", 9.0)
    score += min(max((body_pct - 9.0) / (20.0 - 9.0) * 15, 0.0), 15.0)
    vol_ratio = stock.get("bo_vol_ratio", 3.0)
    score += min(max((vol_ratio - 3.0) / (8.0 - 3.0) * 15, 0.0), 15.0)
    score += {1: 15, 2: 10, 3: 7, 4: 4, 5: 2}.get(stock.get("bo_lookback", 5), 2)
    vol_dry   = stock.get("vol_dry_ratio", 1.0)
    score += min(max((1.0 - vol_dry) * 15, 0.0), 15.0)
    shape     = stock.get("shape_ratio", 0.25)
    score += min(max((0.25 - shape) / 0.25 * 10, 0.0), 10.0)
    gap = stock.get("ma20_gap", 0.05)
    if 0.0 <= gap <= 0.05:
        score += 15.0
    elif gap < 0.0:
        score += max(15.0 + gap / 0.03 * 15.0, 0.0)
    else:
        score += max(15.0 - (gap - 0.05) / 0.10 * 15.0, 0.0)
    pos = stock.get("price_pos", 0.70)
    score += min(max((pos - 0.70) / (0.95 - 0.70) * 15, 0.0), 15.0)
    return round(min(score, 100.0))


def get_kospi_condition(start_date) -> tuple[bool, str]:
    try:
        kospi = fdr.DataReader("KS11", start_date)
        if kospi.empty or len(kospi) < 25:
            return True, "KOSPI 데이터 부족 — 필터 통과"
        ma20_series = kospi["Close"].rolling(20).mean()
        ma20_now    = ma20_series.iloc[-1]
        ma20_5d_ago = ma20_series.iloc[-6]
        close       = kospi["Close"].iloc[-1]
        close_5d    = kospi["Close"].iloc[-6]
        above_ma20  = close >= ma20_now
        ma20_rising = ma20_now >= ma20_5d_ago
        # 주간 급락 브레이크: 5거래일 수익률 -3% 이하면 강제 차단
        weekly_ret  = (close - close_5d) / close_5d if close_5d else 0
        weekly_ok   = weekly_ret > -0.03
        ok          = above_ma20 and ma20_rising and weekly_ok
        slope       = "↑상승" if ma20_rising else "↓하락"
        weekly_tag  = f" | 주간{weekly_ret*100:+.1f}%{'⚡급락' if not weekly_ok else ''}"
        status = (
            f"KOSPI {close:,.0f} / MA20 {ma20_now:,.0f} {slope}{weekly_tag} "
            f"({'▲ 양호' if ok else '▼ 차단'})"
        )
        return ok, status
    except Exception as e:
        log.warning(f"  KOSPI 조회 실패: {e} — 시장 필터 통과로 처리")
        return True, f"KOSPI 조회 실패: {e}"
