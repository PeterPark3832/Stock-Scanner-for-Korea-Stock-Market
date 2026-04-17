"""
한국 주식 전략 비교 백테스트 (Walk-Forward 안정성 검증)
══════════════════════════════════════════════════════════
비교 전략 4종 (파라미터 고정 — 과최적화 방지):
  A. 눌림목 스윙  : 기준봉 + 눌림 진입 (stock_scanner_v4.5 현행 전략)
  B. 상따          : 상한가(+28%↑) 다음날 시가 진입
  C. 갭 모멘텀     : 갭 상승(+3%↑) + 거래량 급증 다음날 진입
  D. 과매도 반등   : RSI(10) ≤ 25 + 볼린저 하단 이탈 다음날 진입

검증 방식:
  - 파라미터 최적화 없이 고정값 사용 (과최적화 방지)
  - 연도별 성과 분리 출력 (2021 / 2022 / 2023 / 2024)
  - 전략 간 비교 요약 테이블

실행:
  python backtest_strategies.py

캐시:
  FDR 다운로드 결과를 data_cache/ 에 pkl 로 저장
  재실행 시 캐시 사용 (FORCE_REFRESH=True 로 강제 재다운로드)

주의:
  - 거래비용 왕복 0.5% 반영
  - 슬리피지 0.1% 반영
  - 신호 당일 다음날 시가 진입 (look-ahead 방지)
  - TP/SL 은 당일 고가/저가로 시뮬레이션
══════════════════════════════════════════════════════════
"""

import os
import pickle
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

import FinanceDataReader as fdr
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ══════════════════════════════════════════════════════════
# 설정
# ══════════════════════════════════════════════════════════
BACKTEST_START   = "2021-01-01"
BACKTEST_END     = "2024-12-31"
UNIVERSE_MARCAP  = 300_000_000_000   # 시총 3000억 이상
UNIVERSE_MAX     = 200               # 시총 상위 200종목 (속도 조절)
COMMISSION       = 0.005             # 왕복 거래비용 0.5%
SLIPPAGE         = 0.001             # 슬리피지 0.1%
MIN_TURNOVER     = 1_000_000_000     # 최소 거래대금 10억
CACHE_DIR        = "data_cache"
FORCE_REFRESH    = False             # True: 캐시 무시하고 재다운로드

# ── 전략별 고정 파라미터 (최적화 금지) ─────────────────────
STRATEGY_CONFIGS = {
    "A_눌림목": {
        "bo_body_pct":   0.07,   # 기준봉 몸통 7%+
        "bo_vol_ratio":  2.5,    # 기준봉 거래량 MA20 대비 2.5x
        "bo_lookback":   3,      # 기준봉 최대 3일 전
        "tp_pct":        0.10,   # TP +10%
        "sl_pct":        0.03,   # SL -3% (기준봉 시가 기준)
        "max_hold":      7,      # 최대 보유 7 거래일
    },
    "B_상따": {
        "limit_up_pct":  0.28,   # 전날 +28% 이상 (상한가 근접)
        "tp_pct":        0.07,   # TP +7%
        "sl_pct":        0.04,   # SL -4%
        "max_hold":      3,      # 최대 3 거래일
    },
    "C_갭모멘텀": {
        "gap_pct":       0.03,   # 갭 상승 3%+
        "vol_ratio":     2.0,    # 거래량 MA20 대비 2x
        "close_above_open": True,# 양봉 마감 조건
        "tp_pct":        0.06,   # TP +6%
        "sl_pct":        0.03,   # SL -3%
        "max_hold":      3,      # 최대 3 거래일
    },
    "D_과매도반등": {
        "rsi_period":    10,     # RSI 기간
        "rsi_threshold": 25,     # RSI 25 이하
        "bb_period":     20,     # 볼린저 밴드 기간
        "bb_std":        2.0,    # 볼린저 밴드 표준편차
        "tp_pct":        0.08,   # TP +8%
        "sl_pct":        0.04,   # SL -4%
        "max_hold":      7,      # 최대 7 거래일
    },
}

# ══════════════════════════════════════════════════════════
# 데이터 클래스
# ══════════════════════════════════════════════════════════
@dataclass
class Trade:
    strategy:    str
    ticker:      str
    name:        str
    entry_date:  str
    exit_date:   str
    entry_price: float
    exit_price:  float
    pnl_pct:     float           # 거래비용·슬리피지 차감 후
    exit_reason: str             # TP | SL | EXPIRE
    hold_days:   int


@dataclass
class PerfStats:
    strategy:    str
    period:      str
    total:       int
    wins:        int
    win_rate:    float
    avg_pnl:     float
    sharpe:      float
    max_dd:      float
    profit_factor: float
    trades:      list[Trade] = field(default_factory=list)


# ══════════════════════════════════════════════════════════
# 유틸리티
# ══════════════════════════════════════════════════════════
def _cache_path(ticker: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{ticker}.pkl")


def load_stock_data(ticker: str, start: str, end: str) -> pd.DataFrame | None:
    path = _cache_path(ticker)
    if not FORCE_REFRESH and os.path.exists(path):
        with open(path, "rb") as f:
            df = pickle.load(f)
        # 범위 확인 — 캐시가 더 짧으면 재다운로드
        if not df.empty and str(df.index[0].date()) <= start and str(df.index[-1].date()) >= end[:10]:
            return df

    for attempt in range(3):
        try:
            df = fdr.DataReader(ticker, start)
            if df is None or df.empty:
                return None
            # 컬럼 정규화
            df.columns = [c.capitalize() for c in df.columns]
            if "Close" not in df.columns:
                return None
            df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
            with open(path, "wb") as f:
                pickle.dump(df, f)
            return df
        except Exception:
            if attempt < 2:
                time.sleep(1.5 * (2 ** attempt))
    return None


def calc_rsi(close: pd.Series, period: int = 10) -> pd.Series:
    delta    = close.diff()
    gain     = delta.clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    loss     = (-delta).clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    rs = gain / loss.replace(0, float("nan"))
    return 100 - 100 / (1 + rs)


def add_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    df = df.copy()
    df["MA20"]   = df["Close"].rolling(20).mean()
    df["MA60"]   = df["Close"].rolling(60).mean()
    df["Vol20"]  = df["Volume"].rolling(20).mean()
    df["RSI10"]  = calc_rsi(df["Close"], cfg.get("rsi_period", 10))
    bb_per       = cfg.get("bb_period", 20)
    bb_std_mul   = cfg.get("bb_std", 2.0)
    df["BB_mid"] = df["Close"].rolling(bb_per).mean()
    df["BB_std"] = df["Close"].rolling(bb_per).std()
    df["BB_low"] = df["BB_mid"] - bb_std_mul * df["BB_std"]
    return df


# ══════════════════════════════════════════════════════════
# 신호 생성 함수군 (returns list of signal row indices)
# ══════════════════════════════════════════════════════════
def signals_pullback(df: pd.DataFrame, cfg: dict) -> list[int]:
    """
    A. 눌림목 스윙
    · 1~3 거래일 전 기준봉: 몸통 7%+, 거래량 2.5x MA20
    · 당일: 저거래량 + 기준봉 시가 이상 지지 + 좁은 캔들 + MA20 위
    """
    signals = []
    bo_body = cfg["bo_body_pct"]
    bo_vol  = cfg["bo_vol_ratio"]

    for i in range(60, len(df) - 1):      # i = 신호 감지일
        today = df.iloc[i]
        if pd.isna(today["MA20"]) or pd.isna(today["Vol20"]):
            continue
        # 거래대금 필터
        if today["Close"] * today["Volume"] < MIN_TURNOVER:
            continue
        # MA20 위 (추세 확인)
        if today["Close"] < today["MA20"]:
            continue

        # 1~3일 전 기준봉 탐색
        found = False
        for lookback in range(1, cfg["bo_lookback"] + 1):
            bi = i - lookback
            if bi < 20:
                break
            bo = df.iloc[bi]
            vol20_at_bo = df["Vol20"].iloc[bi]
            if vol20_at_bo <= 0 or pd.isna(vol20_at_bo):
                continue
            body_pct  = bo["Close"] / bo["Open"] - 1
            vol_ratio = bo["Volume"] / vol20_at_bo
            if bo["Close"] > bo["Open"] and body_pct >= bo_body and vol_ratio >= bo_vol:
                # 눌림 조건
                vol20_pre = df["Vol20"].iloc[bi - 1] if bi > 0 else vol20_at_bo
                cond_vol_dry = today["Volume"] <= (vol20_pre * 1.0) if vol20_pre > 0 else True
                cond_support = today["Close"] >= bo["Open"]
                today_range  = today["High"] - today["Low"]
                today_body   = abs(today["Close"] - today["Open"])
                cond_shape   = (today_body / today_range <= 0.25) if today_range > 0 else False
                if cond_vol_dry and cond_support and cond_shape:
                    found = True
                    break
        if found:
            signals.append(i)
    return signals


def signals_sangtta(df: pd.DataFrame, cfg: dict) -> list[int]:
    """
    B. 상따 (상한가 다음날)
    · 전날: 종가/전전날종가 >= 1.28 (상한가 근접)
    · 추가: 거래량 MA20 이상, 당일 시가 전날 종가 ± 5% 이내 (갭 폭등 방지)
    """
    signals = []
    limit_pct = cfg["limit_up_pct"]

    for i in range(20, len(df) - 1):
        today = df.iloc[i]
        prev  = df.iloc[i - 1]
        prev2 = df.iloc[i - 2]

        if pd.isna(prev["Vol20"]):
            continue
        if today["Close"] * today["Volume"] < MIN_TURNOVER:
            continue

        # 전날이 상한가
        change_pct = prev["Close"] / prev2["Close"] - 1
        if change_pct < limit_pct:
            continue
        # 거래량 확인 (상한가인데 거래량 없으면 의심)
        if prev["Volume"] < prev["Vol20"] * 0.5:
            continue
        # 과도한 갭 방지: 당일 시가가 전날 종가 대비 ±8% 이내
        gap_open = today["Open"] / prev["Close"] - 1
        if abs(gap_open) > 0.08:
            continue

        signals.append(i)
    return signals


def signals_gap_momentum(df: pd.DataFrame, cfg: dict) -> list[int]:
    """
    C. 갭 상승 모멘텀
    · 신호일: 시가/전날종가 >= 3% (갭 상승)
    · 신호일: 거래량 >= MA20 * 2
    · 신호일: 종가 >= 시가 (양봉 마감)
    · 다음날 시가 진입
    """
    signals = []
    gap_pct  = cfg["gap_pct"]
    vol_mult = cfg["vol_ratio"]

    for i in range(20, len(df) - 1):
        today = df.iloc[i]
        prev  = df.iloc[i - 1]

        if pd.isna(today["Vol20"]):
            continue
        if today["Close"] * today["Volume"] < MIN_TURNOVER:
            continue

        gap = today["Open"] / prev["Close"] - 1
        if gap < gap_pct:
            continue
        if today["Volume"] < today["Vol20"] * vol_mult:
            continue
        if cfg.get("close_above_open", True) and today["Close"] < today["Open"]:
            continue

        signals.append(i)
    return signals


def signals_mean_reversion(df: pd.DataFrame, cfg: dict) -> list[int]:
    """
    D. 과매도 반등 (단기 평균회귀)
    · RSI(10) <= 25
    · 종가 <= 볼린저 하단
    · MA60 위 (중기 추세 유지 종목 한정 — 급락 종목 제외)
    · 거래량 MA20 이상 (관심 존재 확인)
    """
    signals = []
    rsi_thr  = cfg["rsi_threshold"]

    for i in range(60, len(df) - 1):
        today = df.iloc[i]
        if pd.isna(today["RSI10"]) or pd.isna(today["BB_low"]) or pd.isna(today["MA60"]):
            continue
        if today["Close"] * today["Volume"] < MIN_TURNOVER:
            continue

        if today["RSI10"] > rsi_thr:
            continue
        if today["Close"] > today["BB_low"]:
            continue
        if today["Close"] < today["MA60"]:   # 중기 추세 위에서만
            continue
        if today["Volume"] < today["Vol20"] * 1.0:
            continue

        signals.append(i)
    return signals


# ══════════════════════════════════════════════════════════
# 백테스트 엔진
# ══════════════════════════════════════════════════════════
def simulate_trade(
    df: pd.DataFrame,
    signal_idx: int,
    cfg: dict,
    strategy_name: str,
    ticker: str,
    name: str,
) -> Trade | None:
    """
    신호일 다음날 시가 진입 → TP/SL/만기 청산 시뮬레이션
    intraday 최악 가정: 당일 저가가 SL 이하이면 SL 먼저 처리
    """
    entry_idx = signal_idx + 1
    if entry_idx >= len(df):
        return None

    entry_bar   = df.iloc[entry_idx]
    raw_entry   = entry_bar["Open"]
    entry_price = raw_entry * (1 + SLIPPAGE)   # 슬리피지 반영

    if entry_price <= 0:
        return None

    tp_price = entry_price * (1 + cfg["tp_pct"])
    sl_price = entry_price * (1 - cfg["sl_pct"])
    max_hold = cfg["max_hold"]

    entry_date = str(df.index[entry_idx].date())
    exit_price  = None
    exit_reason = None
    exit_date   = None

    for j in range(entry_idx, min(entry_idx + max_hold, len(df))):
        bar = df.iloc[j]

        # 첫날 시가가 갭 다운으로 SL 하회하면 시가 청산
        if j == entry_idx and bar["Open"] <= sl_price:
            exit_price  = bar["Open"] * (1 - SLIPPAGE)
            exit_reason = "SL"
            exit_date   = str(df.index[j].date())
            break

        # SL 먼저 (보수적)
        if bar["Low"] <= sl_price:
            exit_price  = sl_price * (1 - SLIPPAGE)
            exit_reason = "SL"
            exit_date   = str(df.index[j].date())
            break

        # TP
        if bar["High"] >= tp_price:
            exit_price  = tp_price * (1 - SLIPPAGE)
            exit_reason = "TP"
            exit_date   = str(df.index[j].date())
            break

    # 만기 청산
    if exit_price is None:
        exit_idx    = min(entry_idx + max_hold - 1, len(df) - 1)
        exit_price  = df.iloc[exit_idx]["Close"] * (1 - SLIPPAGE)
        exit_reason = "EXPIRE"
        exit_date   = str(df.index[exit_idx].date())

    # 거래비용 차감
    net_entry = raw_entry * (1 + COMMISSION / 2)
    net_exit  = exit_price * (1 - COMMISSION / 2)
    pnl_pct   = round((net_exit - net_entry) / net_entry * 100, 3)

    hold_days = (
        datetime.strptime(exit_date, "%Y-%m-%d")
        - datetime.strptime(entry_date, "%Y-%m-%d")
    ).days

    return Trade(
        strategy    = strategy_name,
        ticker      = ticker,
        name        = name,
        entry_date  = entry_date,
        exit_date   = exit_date,
        entry_price = round(raw_entry, 0),
        exit_price  = round(net_exit, 0),
        pnl_pct     = pnl_pct,
        exit_reason = exit_reason,
        hold_days   = hold_days,
    )


def backtest_one_stock(
    df: pd.DataFrame,
    signal_fn: Callable,
    cfg: dict,
    strategy_name: str,
    ticker: str,
    name: str,
    start: str,
    end: str,
) -> list[Trade]:
    """단일 종목에 대한 전략 백테스트"""
    if df is None or df.empty or len(df) < 80:
        return []

    df_period = df[(df.index >= start) & (df.index <= end)].copy()
    if len(df_period) < 80:
        return []

    df_ind = add_indicators(df_period, cfg)
    signal_indices = signal_fn(df_ind, cfg)

    trades = []
    last_exit_idx = -1  # 중복 진입 방지 (같은 종목 연속 신호)

    for sig_idx in signal_indices:
        if sig_idx <= last_exit_idx:
            continue
        trade = simulate_trade(df_ind, sig_idx, cfg, strategy_name, ticker, name)
        if trade:
            trades.append(trade)
            # 다음 진입은 청산 이후부터
            exit_date = datetime.strptime(trade.exit_date, "%Y-%m-%d")
            # DataFrame 인덱스에서 exit 이후 위치 찾기
            future = df_ind.index[df_ind.index > exit_date]
            last_exit_idx = df_ind.index.get_loc(future[0]) - 1 if len(future) > 0 else sig_idx + cfg["max_hold"]

    return trades


# ══════════════════════════════════════════════════════════
# 성과 계산
# ══════════════════════════════════════════════════════════
def calc_stats(trades: list[Trade], strategy: str, period: str) -> PerfStats:
    if not trades:
        return PerfStats(strategy, period, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, [])

    pnls     = [t.pnl_pct for t in trades]
    total    = len(pnls)
    wins     = sum(1 for p in pnls if p > 0)
    win_rate = round(wins / total * 100, 1)
    avg_pnl  = round(float(np.mean(pnls)), 2)
    std_pnl  = float(np.std(pnls))
    sharpe   = round(avg_pnl / std_pnl, 2) if std_pnl > 0 else 0.0

    # 최대 낙폭 (누적 PnL 기준)
    cum = np.cumsum(pnls)
    roll_max = np.maximum.accumulate(cum)
    drawdowns = cum - roll_max
    max_dd   = round(float(drawdowns.min()), 2)

    # Profit Factor
    gross_win  = sum(p for p in pnls if p > 0) or 0.0
    gross_loss = abs(sum(p for p in pnls if p < 0)) or 1e-9
    pf = round(gross_win / gross_loss, 2)

    return PerfStats(strategy, period, total, wins, win_rate, avg_pnl, sharpe, max_dd, pf, trades)


# ══════════════════════════════════════════════════════════
# 메인 실행
# ══════════════════════════════════════════════════════════
SIGNAL_FNS = {
    "A_눌림목":  signals_pullback,
    "B_상따":    signals_sangtta,
    "C_갭모멘텀": signals_gap_momentum,
    "D_과매도반등": signals_mean_reversion,
}

YEARS = ["2021", "2022", "2023", "2024"]


def run_backtest() -> dict[str, list[Trade]]:
    print("\n" + "═"*60)
    print("  한국 주식 전략 비교 백테스트")
    print(f"  기간: {BACKTEST_START} ~ {BACKTEST_END}")
    print(f"  유니버스: 시총 {UNIVERSE_MARCAP//100_000_000}억+ 상위 {UNIVERSE_MAX}종목")
    print(f"  거래비용: 왕복 {COMMISSION*100:.1f}% | 슬리피지: {SLIPPAGE*100:.1f}%")
    print("═"*60)

    # ── 유니버스 구성 ─────────────────────────────────────
    print("\n[1/3] 유니버스 다운로드 중...")
    try:
        krx = fdr.StockListing("KRX")
        krx = krx[krx["Market"].isin(["KOSPI", "KOSDAQ"])].copy()
        cap_col = next((c for c in ["Marcap","MarCap","marcap","시가총액"] if c in krx.columns), None)
        name_col = next((c for c in ["Name","종목명","name"] if c in krx.columns), "Name")
        code_col = next((c for c in ["Code","Symbol","종목코드"] if c in krx.columns), "Code")

        if cap_col:
            krx[cap_col] = pd.to_numeric(krx[cap_col], errors="coerce").fillna(0)
            krx = krx[krx[cap_col] >= UNIVERSE_MARCAP].nlargest(UNIVERSE_MAX, cap_col)
        else:
            krx = krx.head(UNIVERSE_MAX)

        universe = [(row[code_col], row[name_col]) for _, row in krx.iterrows()]
        print(f"  → 유니버스: {len(universe)}종목")
    except Exception as e:
        print(f"  [ERROR] 유니버스 로드 실패: {e}")
        return {}

    # ── 데이터 다운로드 ──────────────────────────────────
    print(f"\n[2/3] 주가 데이터 로드 중 (캐시 활용)...")
    stock_data: dict[str, tuple[str, pd.DataFrame]] = {}
    failed = 0
    for idx, (ticker, name) in enumerate(universe, 1):
        if idx % 50 == 0:
            print(f"  {idx}/{len(universe)} 완료...")
        df = load_stock_data(ticker, BACKTEST_START, BACKTEST_END)
        if df is not None and len(df) >= 80:
            stock_data[ticker] = (name, df)
        else:
            failed += 1
        time.sleep(0.03)
    print(f"  → 로드 완료: {len(stock_data)}종목 | 실패: {failed}종목")

    # ── 전략별 백테스트 ──────────────────────────────────
    print(f"\n[3/3] 전략 백테스트 실행 중...")
    all_trades: dict[str, list[Trade]] = {name: [] for name in STRATEGY_CONFIGS}

    for strat_name, cfg in STRATEGY_CONFIGS.items():
        signal_fn = SIGNAL_FNS[strat_name]
        print(f"\n  {strat_name} 실행 중...", end="", flush=True)
        count = 0
        for ticker, (name, df) in stock_data.items():
            trades = backtest_one_stock(
                df, signal_fn, cfg, strat_name, ticker, name,
                BACKTEST_START, BACKTEST_END
            )
            all_trades[strat_name].extend(trades)
            count += len(trades)
        print(f" → {count}건 거래")

    return all_trades


def print_results(all_trades: dict[str, list[Trade]]) -> None:
    print("\n\n" + "═"*70)
    print("  백테스트 결과 요약")
    print("═"*70)

    # ── 전체 기간 요약 ──────────────────────────────────
    header = f"{'전략':<14} {'거래수':>6} {'승률':>7} {'평균PnL':>9} {'Sharpe':>8} {'MaxDD':>8} {'PF':>6}"
    print(f"\n■ 전체 기간 ({BACKTEST_START[:4]}~{BACKTEST_END[:4]})")
    print("-"*70)
    print(header)
    print("-"*70)

    summary_stats: dict[str, PerfStats] = {}
    for strat_name, trades in all_trades.items():
        st = calc_stats(trades, strat_name, "전체")
        summary_stats[strat_name] = st
        if st.total == 0:
            print(f"  {strat_name:<12} {'거래 없음':>50}")
            continue
        print(
            f"  {strat_name:<12} "
            f"{st.total:>6,} "
            f"{st.win_rate:>6.1f}% "
            f"{st.avg_pnl:>+8.2f}% "
            f"{st.sharpe:>8.2f} "
            f"{st.max_dd:>+7.2f}% "
            f"{st.profit_factor:>6.2f}"
        )

    # ── 연도별 안정성 검증 ──────────────────────────────
    print(f"\n■ 연도별 승률 / 평균PnL (과최적화 방지 — 일관성 확인)")
    print("-"*70)

    for strat_name, trades in all_trades.items():
        print(f"\n  [{strat_name}]")
        year_line = f"    {'연도':<6}"
        for year in YEARS:
            year_line += f" {year:>14}"
        print(year_line)
        print(f"    {'-'*6}" + f" {'-'*14}" * len(YEARS))

        for metric_label, metric_fn in [
            ("승률%",    lambda st: f"{st.win_rate:>6.1f}%"),
            ("평균PnL",  lambda st: f"{st.avg_pnl:>+7.2f}%"),
            ("거래수",   lambda st: f"{st.total:>6,}건"),
        ]:
            row = f"    {metric_label:<6}"
            for year in YEARS:
                year_trades = [t for t in trades if t.entry_date.startswith(year)]
                st_yr = calc_stats(year_trades, strat_name, year)
                if st_yr.total == 0:
                    row += f" {'—':>14}"
                else:
                    row += f" {metric_fn(st_yr):>14}"
            print(row)

    # ── 청산 사유 분포 ──────────────────────────────────
    print(f"\n■ 청산 사유 분포")
    print("-"*70)
    for strat_name, trades in all_trades.items():
        if not trades:
            continue
        total  = len(trades)
        tp_cnt = sum(1 for t in trades if t.exit_reason == "TP")
        sl_cnt = sum(1 for t in trades if t.exit_reason == "SL")
        ex_cnt = sum(1 for t in trades if t.exit_reason == "EXPIRE")
        avg_hold = round(sum(t.hold_days for t in trades) / total, 1)
        print(
            f"  {strat_name:<14} "
            f"TP {tp_cnt/total*100:.0f}% | "
            f"SL {sl_cnt/total*100:.0f}% | "
            f"만기 {ex_cnt/total*100:.0f}% | "
            f"평균보유 {avg_hold}일"
        )

    # ── 상위 10 / 하위 10 거래 ──────────────────────────
    print(f"\n■ 전략별 상위 10 거래 (PnL 기준)")
    print("-"*70)
    for strat_name, trades in all_trades.items():
        if not trades:
            continue
        top10 = sorted(trades, key=lambda t: t.pnl_pct, reverse=True)[:10]
        print(f"\n  [{strat_name}]")
        for t in top10:
            print(f"    {t.entry_date}  {t.name:<12} {t.ticker}  {t.pnl_pct:>+7.2f}%  {t.exit_reason}  {t.hold_days}일")

    # ── CSV 저장 ─────────────────────────────────────────
    all_trade_rows = []
    for trades in all_trades.values():
        for t in trades:
            all_trade_rows.append({
                "strategy":    t.strategy,
                "ticker":      t.ticker,
                "name":        t.name,
                "entry_date":  t.entry_date,
                "exit_date":   t.exit_date,
                "entry_price": t.entry_price,
                "exit_price":  t.exit_price,
                "pnl_pct":     t.pnl_pct,
                "exit_reason": t.exit_reason,
                "hold_days":   t.hold_days,
            })

    if all_trade_rows:
        out_path = "backtest_results.csv"
        pd.DataFrame(all_trade_rows).to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"\n✅ 전체 거래 내역 저장: {out_path} ({len(all_trade_rows):,}건)")

    # ── 최종 추천 ─────────────────────────────────────────
    print("\n" + "═"*70)
    print("  전략 평가 기준 (과최적화 방지 체크리스트)")
    print("═"*70)
    criteria = [
        ("승률",       "≥ 50%",    lambda st: st.win_rate >= 50),
        ("평균 PnL",   "≥ +0.5%",  lambda st: st.avg_pnl >= 0.5),
        ("Sharpe",     "≥ 0.30",   lambda st: st.sharpe >= 0.30),
        ("Profit Factor", "≥ 1.2", lambda st: st.profit_factor >= 1.2),
        ("연도 일관성", "4년 모두 승률 ≥ 45%", None),
    ]
    print(f"\n  {'전략':<14}", end="")
    for label, threshold, _ in criteria:
        print(f"  {label}({threshold})", end="")
    print()
    print("-"*70)

    for strat_name, st in summary_stats.items():
        if st.total == 0:
            continue
        year_consistent = all(
            calc_stats([t for t in all_trades[strat_name] if t.entry_date.startswith(yr)],
                       strat_name, yr).win_rate >= 45
            for yr in YEARS
            if any(t.entry_date.startswith(yr) for t in all_trades[strat_name])
        )
        checks = []
        for label, threshold, fn in criteria:
            if fn is None:
                checks.append("✅" if year_consistent else "❌")
            else:
                checks.append("✅" if fn(st) else "❌")
        print(f"  {strat_name:<14}  " + "       ".join(checks))

    print("\n  ※ ✅ 4개 이상 달성 전략을 stock_scanner 에 통합 권장")
    print("  ※ 거래비용(0.5%)·슬리피지(0.1%) 반영 결과입니다")
    print("═"*70 + "\n")


if __name__ == "__main__":
    t0 = time.time()
    all_trades = run_backtest()
    if all_trades:
        print_results(all_trades)
    print(f"  총 소요 시간: {(time.time()-t0)/60:.1f}분")
