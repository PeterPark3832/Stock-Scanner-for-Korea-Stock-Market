"""SeedNGrow KR 전략 5종 포팅 — 목표 비중 계산만 담당(주문 실행 없음).

4종(asset_momentum·gem·growth·leaders)은 동일 듀얼모멘텀 엔진:
  3/6/12개월 수익률 평균 → 상위 TOP_N 동일비중 → 절대모멘텀(단기채권 대비) 미달 슬롯은
  국고채3년(안전자산)으로 도피.
vaa_kr은 13612W(21/63/126/252 가중 12/4/2/1) + breadth 카나리아:
  공격 자산군 음수 모멘텀 개수로 캐시비중 CF 산출 → 공격 상위 TOP_N + 방어 1개.

모두 월간(첫 거래일) 리밸런싱. SeedNGrow는 yfinance '.KS' suffix를 쓰지만 여기서는
FinanceDataReader용 bare code(예: '069500')를 사용한다.
"""
from datetime import datetime, timedelta

import pandas as pd

# ── 표시명 (bare code → 한글명) ─────────────────────────────────────
NAMES = {
    "069500": "KOSPI200 (KODEX 200)",
    "229200": "코스닥150 (KODEX)",
    "133690": "미국 나스닥100 (TIGER)",
    "143850": "미국 S&P500 (TIGER, H)",
    "132030": "금 (KODEX 골드선물H)",
    "091160": "반도체 (KODEX)",
    "114260": "국고채 3년 (안전자산)",
    "153130": "단기채권 (현금성)",
    "005930": "삼성전자", "000660": "SK하이닉스", "005380": "현대차",
    "035420": "NAVER", "051910": "LG화학", "006400": "삼성SDI",
    "207940": "삼성바이오로직스", "068270": "셀트리온", "105560": "KB금융",
    "012330": "현대모비스",
}

# ── 전략 레지스트리 ─────────────────────────────────────────────────
STRATEGIES: dict[str, dict] = {
    "kr_asset_momentum": {
        "name": "한국 자산배분 모멘텀", "profile": "방어",
        "description": "KOSPI200·미국S&P·금·코스닥150 중 모멘텀 상위 3개로 분산, 약세 시 국고채 도피",
        "type": "dual", "risk": ["069500", "143850", "132030", "229200"],
        "safe": "114260", "cash_proxy": "153130", "top_n": 3, "min_seed": 1_000_000,
    },
    "kr_gem": {
        "name": "한국·미국 멀티에셋", "profile": "밸런스",
        "description": "KOSPI200·미국S&P·나스닥100·금·반도체 중 모멘텀 상위 3개로 분산, 약세 시 국고채 도피",
        "type": "dual", "risk": ["069500", "143850", "133690", "132030", "091160"],
        "safe": "114260", "cash_proxy": "153130", "top_n": 3, "min_seed": 1_000_000,
    },
    "kr_growth": {
        "name": "한국·미국 성장주", "profile": "공격",
        "description": "코스닥150·KOSPI200·나스닥100·미국S&P·반도체 중 모멘텀 상위 3개 집중, 약세 시 국고채 도피",
        "type": "dual", "risk": ["229200", "069500", "133690", "143850", "091160"],
        "safe": "114260", "cash_proxy": "153130", "top_n": 3, "min_seed": 1_000_000,
    },
    "kr_leaders": {
        "name": "한국 주도주", "profile": "공격",
        "description": "한국 대형 주도주(삼성전자·SK하이닉스 등) 중 모멘텀 상위 4개 집중, 약세 시 국고채 도피",
        "type": "dual",
        "risk": ["005930", "000660", "005380", "035420", "051910",
                 "006400", "207940", "068270", "105560", "012330"],
        "safe": "114260", "cash_proxy": "153130", "top_n": 4, "min_seed": 50_000_000,
    },
    "vaa_kr": {
        "name": "한국형 VAA 카나리아", "profile": "방어",
        "description": "정통 VAA(13612W+breadth) — 공격군(KOSPI200·S&P500·나스닥100·금) 위험 감지 시 국고채·단기채권 전량 도피, 무난하면 상위 2개 집중",
        "type": "vaa", "offensive": ["069500", "143850", "133690", "132030"],
        "defensive": ["114260", "153130"], "breadth_break": 1, "top_n": 2, "min_seed": 1_000_000,
    },
}

DEFAULT_KEY = "kr_gem"
_BLEND_LOOKBACKS = (63, 126, 252)          # 3/6/12개월
_W13612 = {21: 12.0, 63: 4.0, 126: 2.0, 252: 1.0}


def get_strategy(key: str) -> dict:
    """전략 스펙 반환. 미존재 시 기본(kr_gem) 폴백."""
    return STRATEGIES.get(key, STRATEGIES[DEFAULT_KEY])


def list_strategies() -> list[dict]:
    return [{"key": k, "name": s["name"], "description": s["description"],
             "profile": s["profile"], "top_n": s["top_n"], "min_seed": s["min_seed"]}
            for k, s in STRATEGIES.items()]


def universe_for(key: str) -> list[str]:
    """해당 전략이 가격을 조회/보유하는 티커 목록."""
    s = get_strategy(key)
    if s["type"] == "vaa":
        return list(dict.fromkeys(s["offensive"] + s["defensive"]))
    return list(dict.fromkeys(s["risk"] + [s["safe"], s["cash_proxy"]]))


# 5개 전략 전체에서 봇이 관리(매수/매도)하는 티커 합집합 — 전략 전환 시 옛 보유 청산용.
MANAGED_UNIVERSE = set()
for _s in STRATEGIES.values():
    if _s["type"] == "vaa":
        MANAGED_UNIVERSE.update(_s["offensive"] + _s["defensive"])
    else:
        MANAGED_UNIVERSE.update(_s["risk"] + [_s["safe"], _s["cash_proxy"]])


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


def _blended_momentum(series: pd.Series | None) -> float | None:
    """3/6/12개월 수익률 단순 평균. 가용 구간이 하나도 없으면 None."""
    if series is None or len(series) == 0:
        return None
    rets = [float(series.iloc[-1] / series.iloc[-1 - d] - 1)
            for d in _BLEND_LOOKBACKS if len(series) > d]
    return sum(rets) / len(rets) if rets else None


def _w13612_momentum(series: pd.Series | None) -> float | None:
    """13612W 모멘텀 — 가용 룩백만으로 가중평균(부호 보존)."""
    if series is None or len(series) == 0:
        return None
    num = wsum = 0.0
    for d, w in _W13612.items():
        if len(series) > d:
            num += w * float(series.iloc[-1] / series.iloc[-1 - d] - 1)
            wsum += w
    return num / wsum if wsum else None


def _compute_dual(spec: dict, closes: dict) -> dict[str, float]:
    risk, safe, cash, topn = spec["risk"], spec["safe"], spec["cash_proxy"], spec["top_n"]
    cash_mom = _blended_momentum(closes.get(cash)) or 0.0
    ranked = sorted(
        [(tk, m) for tk in risk if (m := _blended_momentum(closes.get(tk))) is not None],
        key=lambda x: x[1], reverse=True,
    )[:topn]
    if not ranked:
        return {safe: 100.0}
    slot = 100.0 / len(ranked)
    weights: dict[str, float] = {}
    for tk, mom in ranked:
        dest = tk if mom > cash_mom else safe
        weights[dest] = weights.get(dest, 0.0) + slot
    return weights


def _compute_vaa(spec: dict, closes: dict) -> dict[str, float]:
    off, deff = spec["offensive"], spec["defensive"]
    B, topn = spec["breadth_break"], spec["top_n"]
    off_scores = {tk: m for tk in off if (m := _w13612_momentum(closes.get(tk))) is not None}
    if not off_scores:
        return {}
    b = sum(1 for v in off_scores.values() if v <= 0) + (len(off) - len(off_scores))
    cf = min(1.0, b / B)
    weights: dict[str, float] = {}
    if cf < 1.0:
        positive = sorted([(tk, v) for tk, v in off_scores.items() if v > 0],
                          key=lambda x: x[1], reverse=True)[:topn]
        if positive:
            each = (1.0 - cf) * 100.0 / len(positive)
            for tk, _ in positive:
                weights[tk] = weights.get(tk, 0.0) + each
    if cf > 0.0:
        def_scores = {tk: m for tk in deff if (m := _w13612_momentum(closes.get(tk))) is not None}
        if def_scores:
            best = max(def_scores, key=def_scores.get)
            weights[best] = weights.get(best, 0.0) + cf * 100.0
    return weights


def compute_target_weights(key: str = DEFAULT_KEY) -> list[dict]:
    """반환: [{ticker, name, weight(0~100), price}], weight 합계 ≈100."""
    spec = get_strategy(key)
    start = (datetime.now() - timedelta(days=430)).strftime("%Y-%m-%d")
    closes = {tk: _close_series(tk, start) for tk in universe_for(key)}

    weights = _compute_vaa(spec, closes) if spec["type"] == "vaa" else _compute_dual(spec, closes)
    if not weights:
        return []

    total = sum(weights.values())
    if total > 0 and abs(total - 100.0) > 0.01:
        weights = {tk: w / total * 100.0 for tk, w in weights.items()}

    result = []
    for tk, w in weights.items():
        if w <= 0:
            continue
        series = closes.get(tk)
        price = float(series.iloc[-1]) if series is not None else 0.0
        result.append({"ticker": tk, "name": NAMES.get(tk, tk), "weight": round(w, 2), "price": price})
    return result
