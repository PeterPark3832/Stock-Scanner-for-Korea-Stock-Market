"""
한국 주식 전략 비교 백테스트 v2 (신전략 + 유니버스 개선)
══════════════════════════════════════════════════════════
v1 실패 원인 분석:
  - 유니버스 문제: 시총 상위 200 대형주 → 상따/갭/RSI 필터 신호 극소
  - C_갭모멘텀: 갭 다음날 진입 → 되돌림 이후 진입, 당일 SL 60%
  - D_과매도반등: RSI ≤ 25 대형주에서 거의 발생 안 함
  - A_눌림목: 시장 필터 없음 → 2022 하락장 승률 8%

v2 개선:
  유니버스 → KOSDAQ 전종목 + KOSPI 중소형 (시총 50B~3T 이하)
             중소형에서 모멘텀·상따·갭 전략이 학술적으로 효과 확인

신규 전략 2종:
  E. 52주신고가  : 252일 고가 돌파 + 거래량 1.5x → 단기 모멘텀 추종
                  (Investment Analysts Journal 2024 한국 시장 검증)
  F. 거래량급증  : N일 신고가 + 거래량 3x 폭발 → 수급 이탈 순간 진입

기존 전략 수정:
  A_눌림목_v2   : KOSPI MA20 시장 필터 추가 + TP 8% (달성 현실화)
  B_상따_v2     : 유니버스 교체만 (KOSDAQ 중소형에서 자주 발생)

검증 방식: 파라미터 고정 + 연도별 안정성 (2021~2024)
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
BACKTEST_START  = "2021-01-01"
BACKTEST_END    = "2024-12-31"

# ── 유니버스 v2: 중소형 위주 ────────────────────────────
UNIVERSE_MIN_MARCAP = 50_000_000_000     # 시총 500억 이상
UNIVERSE_MAX_MARCAP = 3_000_000_000_000  # 시총 3조 이하 (대형주 제외)
UNIVERSE_MAX        = 400                # 최대 400종목
UNIVERSE_MARKET     = ["KOSDAQ", "KOSPI"] # 전체 포함

COMMISSION   = 0.005   # 왕복 거래비용 0.5%
SLIPPAGE     = 0.001   # 슬리피지 0.1%
MIN_TURNOVER = 500_000_000   # 최소 거래대금 5억 (중소형 기준 완화)
CACHE_DIR    = "data_cache"
FORCE_REFRESH = False

# ── 전략 고정 파라미터 ──────────────────────────────────
STRATEGY_CONFIGS = {
    "A_눌림목v2": {
        "bo_body_pct":  0.07,   # 기준봉 몸통 7%+
        "bo_vol_ratio": 2.5,    # 기준봉 거래량 2.5x
        "bo_lookback":  3,      # 최대 3일 전
        "tp_pct":       0.08,   # TP +8% (v1의 10%에서 현실화)
        "sl_pct":       0.03,   # SL -3%
        "max_hold":     7,
        "use_market_filter": True,  # KOSPI MA20 시장 필터
    },
    "B_상따v2": {
        "limit_up_pct": 0.25,   # 전날 +25% 이상 (중소형 기준 완화)
        "tp_pct":       0.07,
        "sl_pct":       0.04,
        "max_hold":     3,
    },
    "E_52주신고가": {
        "lookback_days": 252,   # 52주 = 252 거래일
        "vol_ratio":     1.5,   # 거래량 MA20 대비 1.5x
        "confirm_bull":  0.02,  # 종가/시가 ≥ 2% (강한 돌파 확인)
        "tp_pct":        0.08,  # TP +8%
        "sl_pct":        0.04,  # SL -4%
        "max_hold":      10,    # 최대 10 거래일
    },
    "F_거래량신고가": {
        "high_days":    20,     # N일 신고가 (20일)
        "vol_ratio":    3.0,    # 거래량 MA20 대비 3x (급등)
        "ma20_above":   True,   # MA20 위 조건
        "tp_pct":       0.06,   # TP +6%
        "sl_pct":       0.025,  # SL -2.5%
        "max_hold":     5,
    },
}

# KOSPI 지수 데이터 (시장 필터용 캐시)
_kospi_cache: pd.DataFrame | None = None


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
    pnl_pct:     float
    exit_reason: str
    hold_days:   int


@dataclass
class PerfStats:
    strategy:      str
    period:        str
    total:         int
    wins:          int
    win_rate:      float
    avg_pnl:       float
    sharpe:        float
    max_dd:        float
    profit_factor: float
    trades: list[Trade] = field(default_factory=list)


# ══════════════════════════════════════════════════════════
# 유틸리티
# ══════════════════════════════════════════════════════════
def _cache_path(ticker: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{ticker}.pkl")


def load_stock_data(ticker: str, start: str) -> pd.DataFrame | None:
    path = _cache_path(ticker)
    if not FORCE_REFRESH and os.path.exists(path):
        with open(path, "rb") as f:
            df = pickle.load(f)
        if not df.empty and str(df.index[0].date()) <= start:
            return df
    for attempt in range(3):
        try:
            df = fdr.DataReader(ticker, start)
            if df is None or df.empty:
                return None
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


def get_kospi_ma20(date_str: str) -> bool | None:
    """해당 날짜의 KOSPI가 MA20 위에 있으면 True (시장 필터)"""
    global _kospi_cache
    if _kospi_cache is None:
        try:
            df = fdr.DataReader("KS11", BACKTEST_START)
            df.columns = [c.capitalize() for c in df.columns]
            df["MA20"] = df["Close"].rolling(20).mean()
            _kospi_cache = df
        except Exception:
            return True  # 조회 실패 시 통과
    dt = pd.Timestamp(date_str)
    row = _kospi_cache[_kospi_cache.index <= dt]
    if row.empty or pd.isna(row.iloc[-1]["MA20"]):
        return True
    last = row.iloc[-1]
    return bool(last["Close"] >= last["MA20"])


def calc_rsi(close: pd.Series, period: int = 10) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    loss  = (-delta).clip(lower=0).ewm(com=period - 1, min_periods=period).mean()
    rs    = gain / loss.replace(0, float("nan"))
    return 100 - 100 / (1 + rs)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["MA20"]  = df["Close"].rolling(20).mean()
    df["MA60"]  = df["Close"].rolling(60).mean()
    df["Vol20"] = df["Volume"].rolling(20).mean()
    return df


# ══════════════════════════════════════════════════════════
# 신호 생성 함수
# ══════════════════════════════════════════════════════════
def signals_pullback_v2(df: pd.DataFrame, cfg: dict) -> list[int]:
    """A_눌림목v2 — 시장 필터(KOSPI MA20) 추가"""
    signals = []
    bo_body = cfg["bo_body_pct"]
    bo_vol  = cfg["bo_vol_ratio"]
    use_mf  = cfg.get("use_market_filter", True)

    for i in range(60, len(df) - 1):
        today = df.iloc[i]
        if pd.isna(today["MA20"]) or pd.isna(today["Vol20"]):
            continue
        if today["Close"] * today["Volume"] < MIN_TURNOVER:
            continue
        if today["Close"] < today["MA20"]:
            continue

        # 시장 필터
        if use_mf:
            date_str = str(df.index[i].date())
            if not get_kospi_ma20(date_str):
                continue

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
                vol20_pre    = df["Vol20"].iloc[bi - 1] if bi > 0 else vol20_at_bo
                today_range  = today["High"] - today["Low"]
                today_body   = abs(today["Close"] - today["Open"])
                cond_vol_dry = today["Volume"] <= (vol20_pre * 1.0) if vol20_pre > 0 else True
                cond_support = today["Close"] >= bo["Open"]
                cond_shape   = (today_body / today_range <= 0.25) if today_range > 0 else False
                if cond_vol_dry and cond_support and cond_shape:
                    signals.append(i)
                    break
    return signals


def signals_sangtta_v2(df: pd.DataFrame, cfg: dict) -> list[int]:
    """B_상따v2 — 중소형 유니버스 + +25% 기준"""
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
        change_pct = prev["Close"] / prev2["Close"] - 1
        if change_pct < limit_pct:
            continue
        if prev["Volume"] < prev["Vol20"] * 0.5:
            continue
        gap_open = today["Open"] / prev["Close"] - 1
        if abs(gap_open) > 0.10:   # 갭 ±10% 이내 (중소형 기준 완화)
            continue
        signals.append(i)
    return signals


def signals_52w_high(df: pd.DataFrame, cfg: dict) -> list[int]:
    """
    E_52주신고가 돌파
    - 252일 최고가를 오늘 종가가 처음 돌파
    - 최근 2일은 52주 최고가 미만이었음 (신규 돌파만 포착)
    - 거래량 MA20 대비 1.5x+
    - 종가/시가 >= 1.02 (강한 양봉)
    """
    signals = []
    lb      = cfg["lookback_days"]
    vr      = cfg["vol_ratio"]
    bull    = cfg["confirm_bull"]

    for i in range(lb + 1, len(df) - 1):
        today = df.iloc[i]
        if pd.isna(today["Vol20"]):
            continue
        if today["Close"] * today["Volume"] < MIN_TURNOVER:
            continue

        # 52주 최고가 (오늘 제외)
        window_high = df["High"].iloc[i - lb: i].max()

        # 신규 돌파 확인 (오늘만 돌파, 어제·그제는 미만)
        if today["Close"] <= window_high:
            continue
        prev1_close = df.iloc[i - 1]["Close"]
        prev2_close = df.iloc[i - 2]["Close"]
        if prev1_close >= window_high or prev2_close >= window_high:
            continue  # 이미 돌파 중이었으면 제외

        # 거래량 확인
        if today["Volume"] < today["Vol20"] * vr:
            continue

        # 강한 양봉 확인
        if today["Open"] <= 0:
            continue
        body_pct = today["Close"] / today["Open"] - 1
        if body_pct < bull:
            continue

        signals.append(i)
    return signals


def signals_vol_new_high(df: pd.DataFrame, cfg: dict) -> list[int]:
    """
    F_거래량급증 N일신고가
    - 오늘 종가가 20일 최고가 돌파
    - 거래량 MA20 대비 3x 이상 폭발
    - MA20 위 종목 한정
    - 전날 대비 종가 상승 (연속 상승 배제)
    """
    signals = []
    hd = cfg["high_days"]
    vr = cfg["vol_ratio"]

    for i in range(hd + 20, len(df) - 1):
        today = df.iloc[i]
        prev  = df.iloc[i - 1]
        if pd.isna(today["MA20"]) or pd.isna(today["Vol20"]):
            continue
        if today["Close"] * today["Volume"] < MIN_TURNOVER:
            continue

        # MA20 위
        if cfg.get("ma20_above", True) and today["Close"] < today["MA20"]:
            continue

        # N일 최고가 돌파 (당일 제외)
        window_high = df["High"].iloc[i - hd: i].max()
        if today["Close"] <= window_high:
            continue

        # 거래량 3x 폭발
        if today["Volume"] < today["Vol20"] * vr:
            continue

        # 전날 대비 상승 (추가 확인)
        if today["Close"] <= prev["Close"]:
            continue

        signals.append(i)
    return signals


# ══════════════════════════════════════════════════════════
# 백테스트 엔진 (v1과 동일)
# ══════════════════════════════════════════════════════════
def simulate_trade(
    df: pd.DataFrame, signal_idx: int, cfg: dict,
    strategy_name: str, ticker: str, name: str
) -> Trade | None:
    entry_idx = signal_idx + 1
    if entry_idx >= len(df):
        return None

    entry_bar   = df.iloc[entry_idx]
    raw_entry   = entry_bar["Open"]
    entry_price = raw_entry * (1 + SLIPPAGE)
    if entry_price <= 0:
        return None

    tp_price = entry_price * (1 + cfg["tp_pct"])
    sl_price = entry_price * (1 - cfg["sl_pct"])
    max_hold = cfg["max_hold"]
    entry_date = str(df.index[entry_idx].date())

    exit_price = exit_reason = exit_date = None

    for j in range(entry_idx, min(entry_idx + max_hold, len(df))):
        bar = df.iloc[j]
        if j == entry_idx and bar["Open"] <= sl_price:
            exit_price  = bar["Open"] * (1 - SLIPPAGE)
            exit_reason = "SL"
            exit_date   = str(df.index[j].date())
            break
        if bar["Low"] <= sl_price:
            exit_price  = sl_price * (1 - SLIPPAGE)
            exit_reason = "SL"
            exit_date   = str(df.index[j].date())
            break
        if bar["High"] >= tp_price:
            exit_price  = tp_price * (1 - SLIPPAGE)
            exit_reason = "TP"
            exit_date   = str(df.index[j].date())
            break

    if exit_price is None:
        exit_idx    = min(entry_idx + max_hold - 1, len(df) - 1)
        exit_price  = df.iloc[exit_idx]["Close"] * (1 - SLIPPAGE)
        exit_reason = "EXPIRE"
        exit_date   = str(df.index[exit_idx].date())

    net_entry = raw_entry * (1 + COMMISSION / 2)
    net_exit  = exit_price * (1 - COMMISSION / 2)
    pnl_pct   = round((net_exit - net_entry) / net_entry * 100, 3)

    hold_days = (
        datetime.strptime(exit_date, "%Y-%m-%d")
        - datetime.strptime(entry_date, "%Y-%m-%d")
    ).days

    return Trade(
        strategy=strategy_name, ticker=ticker, name=name,
        entry_date=entry_date, exit_date=exit_date,
        entry_price=round(raw_entry, 0), exit_price=round(net_exit, 0),
        pnl_pct=pnl_pct, exit_reason=exit_reason, hold_days=hold_days,
    )


def backtest_one_stock(
    df: pd.DataFrame, signal_fn: Callable, cfg: dict,
    strategy_name: str, ticker: str, name: str, start: str, end: str
) -> list[Trade]:
    if df is None or df.empty or len(df) < 100:
        return []
    df_period = df[(df.index >= start) & (df.index <= end)].copy()
    if len(df_period) < 100:
        return []

    df_ind = add_indicators(df_period)
    signal_indices = signal_fn(df_ind, cfg)

    trades = []
    last_exit_idx = -1
    for sig_idx in signal_indices:
        if sig_idx <= last_exit_idx:
            continue
        trade = simulate_trade(df_ind, sig_idx, cfg, strategy_name, ticker, name)
        if trade:
            trades.append(trade)
            exit_dt = datetime.strptime(trade.exit_date, "%Y-%m-%d")
            future = df_ind.index[df_ind.index > exit_dt]
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
    cum      = np.cumsum(pnls)
    rm       = np.maximum.accumulate(cum)
    max_dd   = round(float((cum - rm).min()), 2)
    gw       = sum(p for p in pnls if p > 0) or 0.0
    gl       = abs(sum(p for p in pnls if p < 0)) or 1e-9
    pf       = round(gw / gl, 2)
    return PerfStats(strategy, period, total, wins, win_rate, avg_pnl, sharpe, max_dd, pf, trades)


# ══════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════
SIGNAL_FNS = {
    "A_눌림목v2":    signals_pullback_v2,
    "B_상따v2":      signals_sangtta_v2,
    "E_52주신고가":  signals_52w_high,
    "F_거래량신고가": signals_vol_new_high,
}
YEARS = ["2021", "2022", "2023", "2024"]


def run_backtest() -> dict[str, list[Trade]]:
    print("\n" + "═"*62)
    print("  한국 주식 전략 비교 백테스트 v2")
    print(f"  기간: {BACKTEST_START} ~ {BACKTEST_END}")
    print(f"  유니버스: 시총 {UNIVERSE_MIN_MARCAP//100_000_000}억~"
          f"{UNIVERSE_MAX_MARCAP//100_000_000:,}억 (중소형) 최대 {UNIVERSE_MAX}종목")
    print(f"  거래비용: 왕복 {COMMISSION*100:.1f}% | 슬리피지: {SLIPPAGE*100:.1f}%")
    print("═"*62)

    # ── 유니버스 ─────────────────────────────────────────
    print("\n[1/3] 유니버스 구성 중...")
    try:
        krx = fdr.StockListing("KRX")
        krx = krx[krx["Market"].isin(UNIVERSE_MARKET)].copy()
        cap_col  = next((c for c in ["Marcap","MarCap","marcap","시가총액"] if c in krx.columns), None)
        name_col = next((c for c in ["Name","종목명"] if c in krx.columns), "Name")
        code_col = next((c for c in ["Code","Symbol"] if c in krx.columns), "Code")

        if cap_col:
            krx[cap_col] = pd.to_numeric(krx[cap_col], errors="coerce").fillna(0)
            krx = krx[
                (krx[cap_col] >= UNIVERSE_MIN_MARCAP) &
                (krx[cap_col] <= UNIVERSE_MAX_MARCAP)
            ]
            # 시총 중간 구간 중 거래 활성도 높은 종목 우선
            krx = krx.nlargest(UNIVERSE_MAX, cap_col)

        universe = [(row[code_col], row[name_col]) for _, row in krx.iterrows()]
        print(f"  → 유니버스: {len(universe)}종목 "
              f"(KOSPI {krx[krx['Market']=='KOSPI'].shape[0]}종목 / "
              f"KOSDAQ {krx[krx['Market']=='KOSDAQ'].shape[0]}종목)")
    except Exception as e:
        print(f"  [ERROR] 유니버스 로드 실패: {e}")
        return {}

    # KOSPI 지수 미리 로드 (시장 필터용)
    print("  시장 필터용 KOSPI 지수 로드...")
    get_kospi_ma20(BACKTEST_START)

    # ── 데이터 다운로드 ─────────────────────────────────
    print(f"\n[2/3] 주가 데이터 로드 중 (캐시 활용)...")
    stock_data: dict[str, tuple[str, pd.DataFrame]] = {}
    failed = 0
    for idx, (ticker, name) in enumerate(universe, 1):
        if idx % 100 == 0:
            print(f"  {idx}/{len(universe)} 완료...")
        df = load_stock_data(ticker, BACKTEST_START)
        if df is not None and len(df) >= 100:
            stock_data[ticker] = (name, df)
        else:
            failed += 1
        time.sleep(0.02)
    print(f"  → 로드 완료: {len(stock_data)}종목 | 실패: {failed}종목")

    # ── 전략별 백테스트 ─────────────────────────────────
    print(f"\n[3/3] 전략 백테스트 실행 중...")
    all_trades: dict[str, list[Trade]] = {n: [] for n in STRATEGY_CONFIGS}

    for strat_name, cfg in STRATEGY_CONFIGS.items():
        signal_fn = SIGNAL_FNS[strat_name]
        print(f"\n  [{strat_name}] 실행 중...", end="", flush=True)
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
    PASS_CRITERIA = {
        "승률≥50%":  lambda st: st.win_rate >= 50,
        "PnL≥+0.5%": lambda st: st.avg_pnl >= 0.5,
        "Sharpe≥0.3": lambda st: st.sharpe >= 0.30,
        "PF≥1.2":    lambda st: st.profit_factor >= 1.2,
    }

    print("\n\n" + "═"*70)
    print("  백테스트 v2 결과 요약")
    print("═"*70)

    # 전체 기간
    hdr = f"{'전략':<16} {'거래수':>6} {'승률':>7} {'평균PnL':>9} {'Sharpe':>8} {'MaxDD':>8} {'PF':>6}"
    print(f"\n■ 전체 기간 ({BACKTEST_START[:4]}~{BACKTEST_END[:4]})")
    print("-"*70)
    print(hdr)
    print("-"*70)
    summary: dict[str, PerfStats] = {}
    for sn, trades in all_trades.items():
        st = calc_stats(trades, sn, "전체")
        summary[sn] = st
        if st.total == 0:
            print(f"  {sn:<14} {'거래 없음':>50}")
            continue
        print(
            f"  {sn:<14} "
            f"{st.total:>6,} "
            f"{st.win_rate:>6.1f}% "
            f"{st.avg_pnl:>+8.2f}% "
            f"{st.sharpe:>8.2f} "
            f"{st.max_dd:>+7.2f}% "
            f"{st.profit_factor:>6.2f}"
        )

    # 연도별 안정성
    print(f"\n■ 연도별 승률 / 평균PnL")
    print("-"*70)
    for sn, trades in all_trades.items():
        print(f"\n  [{sn}]")
        header = f"    {'':6}"
        for yr in YEARS:
            header += f"  {yr:>12}"
        print(header)
        for metric_label, mfn in [
            ("승률%",   lambda st: f"{st.win_rate:>5.1f}%"),
            ("평균PnL", lambda st: f"{st.avg_pnl:>+6.2f}%"),
            ("거래수",  lambda st: f"{st.total:>5,}건"),
        ]:
            row = f"    {metric_label:6}"
            for yr in YEARS:
                yr_trades = [t for t in trades if t.entry_date.startswith(yr)]
                st_yr = calc_stats(yr_trades, sn, yr)
                row += f"  {mfn(st_yr):>12}" if st_yr.total > 0 else f"  {'—':>12}"
            print(row)

    # 청산 사유
    print(f"\n■ 청산 사유 분포")
    print("-"*70)
    for sn, trades in all_trades.items():
        if not trades:
            continue
        total  = len(trades)
        tp_cnt = sum(1 for t in trades if t.exit_reason == "TP")
        sl_cnt = sum(1 for t in trades if t.exit_reason == "SL")
        ex_cnt = sum(1 for t in trades if t.exit_reason == "EXPIRE")
        avg_h  = round(sum(t.hold_days for t in trades) / total, 1)
        tp_avg = round(sum(t.pnl_pct for t in trades if t.exit_reason=="TP") / tp_cnt, 2) if tp_cnt else 0
        sl_avg = round(sum(t.pnl_pct for t in trades if t.exit_reason=="SL") / sl_cnt, 2) if sl_cnt else 0
        print(
            f"  {sn:<16} "
            f"TP {tp_cnt/total*100:.0f}%(avg{tp_avg:+.2f}%) | "
            f"SL {sl_cnt/total*100:.0f}%(avg{sl_avg:+.2f}%) | "
            f"만기 {ex_cnt/total*100:.0f}% | "
            f"평균보유 {avg_h}일"
        )

    # 평가 기준 체크
    print(f"\n■ 통과 기준 체크 (과최적화 방지)")
    print("-"*70)
    crit_keys = list(PASS_CRITERIA.keys())
    print(f"  {'전략':<16}  " + "  ".join(f"{k:10}" for k in crit_keys) + "  연도일관성")
    print("-"*70)
    recommendations = []
    for sn, st in summary.items():
        if st.total == 0:
            continue
        checks = [("✅" if PASS_CRITERIA[k](st) else "❌") for k in crit_keys]
        yr_consistent = all(
            calc_stats([t for t in all_trades[sn] if t.entry_date.startswith(yr)], sn, yr).win_rate >= 45
            for yr in YEARS
            if any(t.entry_date.startswith(yr) for t in all_trades[sn])
        )
        checks.append("✅" if yr_consistent else "❌")
        pass_count = checks.count("✅")
        flag = " ← 추천" if pass_count >= 4 else (f" ({pass_count}/5)" if pass_count >= 3 else "")
        print(f"  {sn:<16}  " + "  ".join(f"{c:10}" for c in checks) + flag)
        if pass_count >= 4:
            recommendations.append(sn)

    # CSV 저장
    rows = []
    for trades in all_trades.values():
        for t in trades:
            rows.append({
                "strategy": t.strategy, "ticker": t.ticker, "name": t.name,
                "entry_date": t.entry_date, "exit_date": t.exit_date,
                "entry_price": t.entry_price, "exit_price": t.exit_price,
                "pnl_pct": t.pnl_pct, "exit_reason": t.exit_reason, "hold_days": t.hold_days,
            })
    if rows:
        out_path = "backtest_v2_results.csv"
        pd.DataFrame(rows).to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"\n✅ 거래 내역 저장: {out_path} ({len(rows):,}건)")

    # 최종 결론
    print("\n" + "═"*70)
    if recommendations:
        print(f"  통과 전략: {', '.join(recommendations)}")
        print("  → stock_scanner v4.5 통합 권장")
    else:
        print("  통과 전략 없음 — 파라미터 재검토 또는 추가 전략 필요")
        print("  참고: 거래수가 적은 전략은 샘플 부족으로 판단 보류 권장")
    print("═"*70 + "\n")


if __name__ == "__main__":
    t0 = time.time()
    all_trades = run_backtest()
    if all_trades:
        print_results(all_trades)
    print(f"  총 소요 시간: {(time.time()-t0)/60:.1f}분")
