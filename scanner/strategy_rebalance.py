"""kr_gem 모멘텀 전략 (SeedNGrow 포팅) — 목표 비중 계산만 담당, 주문 실행 없음.

한국·미국 멀티에셋 모멘텀: KOSPI200·S&P500·나스닥100·금·반도체 ETF 중
3/6/12개월 모멘텀 평균 상위 3개를 동일비중 배분. 절대모멘텀(단기채권 대비)
미달 슬롯은 국고채3년(안전자산)으로 전환. 매월 1일(첫 거래일) 리밸런싱.
"""
from datetime import datetime, timedelta

import pandas as pd

RISK_ASSETS = {
    "069500": "KODEX 200",
    "143850": "TIGER 미국S&P500선물(H)",
    "133690": "TIGER 미국나스닥100",
    "132030": "KODEX 골드선물(H)",
    "091160": "KODEX Fn반도체",
}
SAFE_ASSET = "114260"   # KODEX 국고채3년 — 절대모멘텀 미달 시 도피처
CASH_PROXY = "153130"   # KODEX 단기채권PLUS — 모멘텀 임계값 기준
TOP_N = 3
LOOKBACK_DAYS = (63, 126, 252)  # 3/6/12개월

NAMES = {**RISK_ASSETS, SAFE_ASSET: "KODEX 국고채3년", CASH_PROXY: "KODEX 단기채권PLUS"}
UNIVERSE = list(RISK_ASSETS) + [SAFE_ASSET, CASH_PROXY]


def _close_series(ticker: str, start: str) -> pd.Series | None:
    try:
        import FinanceDataReader as fdr
        df = fdr.DataReader(ticker, start)
    except Exception:
        return None
    if df is None or df.empty or "Close" not in df.columns:
        return None
    s = df["Close"].astype(float)
    s = s[s > 0]
    return s if len(s) else None


def _momentum(series: pd.Series | None) -> float | None:
    """3/6/12개월 수익률 단순 평균. 데이터 부족 시 None."""
    if series is None or len(series) <= max(LOOKBACK_DAYS):
        return None
    last = series.iloc[-1]
    rets = [last / series.iloc[-1 - d] - 1 for d in LOOKBACK_DAYS]
    return sum(rets) / len(rets)


def compute_target_weights() -> list[dict]:
    """반환: [{ticker, name, weight(0~100), price}], weight 합계 100."""
    start = (datetime.now() - timedelta(days=420)).strftime("%Y-%m-%d")
    closes = {tk: _close_series(tk, start) for tk in UNIVERSE}

    momentum = {tk: _momentum(closes[tk]) for tk in RISK_ASSETS}
    momentum = {tk: m for tk, m in momentum.items() if m is not None}
    cash_mom = _momentum(closes.get(CASH_PROXY)) or 0.0

    ranked = sorted(momentum.items(), key=lambda kv: kv[1], reverse=True)[:TOP_N]
    if not ranked:
        ranked = [(SAFE_ASSET, 0.0)]  # 가격 조회 전부 실패 시 안전자산 100%

    slot = 100.0 / len(ranked)
    weights: dict[str, float] = {}
    for tk, mom in ranked:
        dest = tk if mom > cash_mom else SAFE_ASSET
        weights[dest] = weights.get(dest, 0.0) + slot

    total = sum(weights.values())
    if total > 0 and abs(total - 100.0) > 0.01:
        weights = {tk: w / total * 100.0 for tk, w in weights.items()}

    result = []
    for tk, w in weights.items():
        series = closes.get(tk)
        price = float(series.iloc[-1]) if series is not None else 0.0
        result.append({"ticker": tk, "name": NAMES.get(tk, tk), "weight": round(w, 2), "price": price})
    return result
