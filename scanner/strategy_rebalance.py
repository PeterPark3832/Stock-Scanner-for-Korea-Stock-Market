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
    "130680": "원유 (TIGER 원유선물)",
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
    "kr_global_alt": {
        "name": "글로벌 분산 + 대체자산", "profile": "밸런스",
        "description": "KOSPI200·코스닥150·나스닥100·S&P500에 금·원유를 더해 자산 간 상관을 "
                       "낮춘 조합. 단기(1/3/6개월) 모멘텀 상위 3개, 약세 시 국고채 도피",
        "type": "dual",
        "risk": ["069500", "229200", "133690", "143850", "132030", "130680"],
        "safe": "114260", "cash_proxy": "153130", "top_n": 3,
        # 3/6/12개월보다 1/3/6개월이 전 유니버스·양 검증구간에서 우위였다(백테스트).
        "lookbacks": (21, 63, 126),
        "min_seed": 1_000_000,
    },
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
    "kr_ensemble": {
        "name": "멀티전략 앙상블", "profile": "밸런스",
        "description": "듀얼모멘텀 3종(자산배분·멀티에셋·성장주)의 목표 비중을 평균 — "
                       "짧은 표본으로 한 전략을 고르는 위험을 분산",
        "type": "ensemble", "members": ["kr_asset_momentum", "kr_gem", "kr_growth"],
        "top_n": 3, "min_seed": 3_000_000,
    },
    "vaa_kr": {
        "name": "한국형 VAA 카나리아", "profile": "방어",
        "description": "정통 VAA(13612W+breadth) — 공격군(KOSPI200·S&P500·나스닥100·금) 위험 감지 시 국고채·단기채권 전량 도피, 무난하면 상위 2개 집중",
        "type": "vaa", "offensive": ["069500", "143850", "133690", "132030"],
        "defensive": ["114260", "153130"], "breadth_break": 1, "top_n": 2, "min_seed": 1_000_000,
    },
}

# ── 과세 구분 (일반 위탁계좌 기준) ──────────────────────────────────
# 국내 '주식형' ETF: 매매차익 비과세.
# 그 외 국내상장 ETF(해외지수·원자재·채권): 매매차익에 배당소득세 15.4% (보유기간과세).
# → 세전 수익률이 같아도 세후 수익은 크게 갈린다. 전략은 세전 모멘텀만 보므로
#   과세 비중이 높은 구성이 나오면 실제 손에 쥐는 돈이 줄어든다.
# 주의: ISA·연금저축 계좌는 과세 체계가 다르고, 세법은 바뀔 수 있다(추정치로만 사용).
TAX_RATE_OTHER_ETF = 0.154
TAX_FREE_TICKERS = {
    "069500",  # KODEX 200 — 국내주식형
    "229200",  # KODEX 코스닥150 — 국내주식형
    "091160",  # KODEX 반도체 — 국내주식형
    # 개별 국내주식(kr_leaders)은 대주주가 아니면 양도차익 비과세
    "005930", "000660", "005380", "035420", "051910",
    "006400", "207940", "068270", "105560", "012330",
}


def is_tax_free(ticker: str) -> bool:
    """매매차익 비과세 대상인지 (일반 위탁계좌 기준)."""
    return ticker in TAX_FREE_TICKERS


def tax_profile(weights: dict[str, float]) -> dict:
    """목표/보유 비중의 과세 구조. weights는 {ticker: 비중(0~100)}.

    반환: taxable_pct(과세 비중), effective_rate(비중가중 실효세율),
          drag_per_10pct(세전 10% 수익 시 세금으로 나가는 %p)
    """
    total = sum(weights.values())
    if total <= 0:
        return {"taxable_pct": 0.0, "effective_rate": 0.0, "drag_per_10pct": 0.0}
    taxable = sum(w for tk, w in weights.items() if not is_tax_free(tk))
    taxable_pct = taxable / total * 100
    eff = taxable_pct / 100 * TAX_RATE_OTHER_ETF
    return {
        "taxable_pct": round(taxable_pct, 1),
        "effective_rate": round(eff * 100, 2),
        "drag_per_10pct": round(10.0 * eff, 2),
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
    if s["type"] == "ensemble":
        out: list[str] = []
        for m in s["members"]:
            out.extend(universe_for(m))
        return list(dict.fromkeys(out))
    if s["type"] == "vaa":
        return list(dict.fromkeys(s["offensive"] + s["defensive"]))
    return list(dict.fromkeys(s["risk"] + [s["safe"], s["cash_proxy"]]))


# 전 전략에서 봇이 관리(매수/매도)하는 티커 합집합 — 전략 전환 시 옛 보유 청산용.
MANAGED_UNIVERSE = {tk for _k in STRATEGIES for tk in universe_for(_k)}


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


def _blended_momentum(series: pd.Series | None,
                      lookbacks: tuple[int, ...] | None = None) -> float | None:
    """룩백 구간 수익률의 단순 평균. 가용 구간이 하나도 없으면 None.

    lookbacks를 주면 그 기간을, 없으면 기본 3/6/12개월(_BLEND_LOOKBACKS)을 쓴다.
    전략마다 모멘텀 속도가 달라야 하는 경우가 있어 스펙에서 주입할 수 있게 열어둔다."""
    if series is None or len(series) == 0:
        return None
    lbs = lookbacks or _BLEND_LOOKBACKS
    rets = [float(series.iloc[-1] / series.iloc[-1 - d] - 1)
            for d in lbs if len(series) > d]
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
    lb = spec.get("lookbacks")          # 전략별 모멘텀 기간 (없으면 기본 3/6/12)
    cash_mom = _blended_momentum(closes.get(cash), lb) or 0.0
    scored = [(tk, m) for tk in risk
              if (m := _blended_momentum(closes.get(tk), lb)) is not None]
    # '데이터 장애'와 '전 자산 약세'를 구분한다. 상위 N개를 고를 만큼도 모멘텀을 못 구하면
    # 방어 신호가 아니라 판단 불가다. 여기서 안전자산 100%를 돌려주면 시세 API가 죽은 날
    # 보유 전량을 팔고 채권으로 갈아타게 된다(전략이 아니라 장애에 의한 시장 이탈).
    if len(scored) < min(topn, len(risk)):
        return {}
    ranked = sorted(scored, key=lambda x: x[1], reverse=True)[:topn]
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
    # 데이터 장애 방어: 공격군 절반도 모멘텀을 못 구하면 breadth 판정 자체를 신뢰할 수 없다.
    # (VAA는 결측을 위험 신호로 취급하므로, 장애 시 방어자산 전량 이동이 일어난다)
    if len(off_scores) < max(1, (len(off) + 1) // 2):
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


def _dispatch(spec: dict, closes: dict) -> dict[str, float]:
    """전략 타입별 비중 계산 진입점 (백테스트도 이 함수를 쓴다)."""
    if spec["type"] == "ensemble":
        return _compute_ensemble(spec, closes)
    if spec["type"] == "vaa":
        return _compute_vaa(spec, closes)
    return _compute_dual(spec, closes)


def _compute_ensemble(spec: dict, closes: dict) -> dict[str, float]:
    """구성 전략들의 목표 비중을 평균한다.

    표본이 짧으면(ETF 상장일 제약) 백테스트 1등이 실제 1등이라는 보장이 없다.
    여러 전략의 합의 비중을 담으면 '가장 나쁜 전략을 고를' 위험이 사라지고,
    전략들이 공통으로 지목한 자산에 자연히 비중이 실린다.
    """
    parts = []
    for key in spec["members"]:
        sub = get_strategy(key)
        w = _compute_vaa(sub, closes) if sub["type"] == "vaa" else _compute_dual(sub, closes)
        if w:
            parts.append(w)
    # 데이터 장애 방어 — 과반이 계산되지 않으면 합의를 신뢰할 수 없다
    if len(parts) < max(1, (len(spec["members"]) + 1) // 2):
        return {}
    merged: dict[str, float] = {}
    for w in parts:
        for tk, v in w.items():
            merged[tk] = merged.get(tk, 0.0) + v / len(parts)
    return merged


def compute_target_weights(key: str = DEFAULT_KEY) -> list[dict]:
    """반환: [{ticker, name, weight(0~100), price}], weight 합계 ≈100."""
    from scanner.logger import log
    spec = get_strategy(key)
    # 최장 룩백 252거래일(≈1년)을 항상 확보해야 한다. 거래일은 연 ~245일이므로
    # 430일(≈288거래일)은 여유가 36일뿐이라, 휴장·데이터 결손이 조금만 겹쳐도
    # 12개월 모멘텀이 조용히 빠진 채 다른 전략으로 매매하게 된다. 550일로 여유 확보.
    start = (datetime.now() - timedelta(days=550)).strftime("%Y-%m-%d")
    closes = {tk: _close_series(tk, start) for tk in universe_for(key)}

    max_lb = (max(_W13612) if spec["type"] == "vaa"
              else max(spec.get("lookbacks") or _BLEND_LOOKBACKS))
    for tk, s in closes.items():
        if s is None:
            log.warning(f"[전략] {tk} 가격 데이터 없음 — 후보에서 제외")
        elif len(s) <= max_lb:
            log.warning(f"[전략] {tk} 데이터 {len(s)}건 (<{max_lb + 1}) — "
                        f"최장 모멘텀 구간 누락, 짧은 룩백만으로 판단됨")

    weights = _dispatch(spec, closes)
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
