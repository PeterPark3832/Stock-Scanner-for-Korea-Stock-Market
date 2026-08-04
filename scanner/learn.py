"""자기주도 학습 — 봇이 자기 체결 결과에서 비용을 배우고 판단을 교정한다.

무엇을 배우는가
  백테스트는 슬리피지 0.15%·수수료 0.015%를 '가정'한다. 이 가정이 틀리면 전략
  순위와 기대수익이 통째로 틀어진다. 이 모듈은 실제 주문의 계획가 대비 체결가를
  측정해 그 가정을 실측으로 대체한다.

왜 축소추정(shrinkage)인가
  표본이 3~10건 수준이면 단순 평균은 우연에 크게 흔들린다. 한 번 운 나쁜 체결로
  슬리피지를 0.5%로 잡으면 백테스트가 전부 왜곡된다. 그래서 측정값을 기본 가정
  쪽으로 끌어당겨(prior와 가중평균) 표본이 쌓일수록 측정값에 수렴하게 한다.
      보정값 = (n·측정 + k·기본) / (n + k)
  표본이 0이면 기본값 그대로, 표본이 많아지면 측정값에 가까워진다.

자동 적용 범위
  비용 가정만 자동 갱신한다(측정값이므로). 전략 교체·체결 시각 변경은 판단이
  필요하므로 권고만 하고 사람이 결정한다.
"""
from __future__ import annotations

import json
import os
import statistics
from datetime import datetime

from scanner.config import REBALANCE_LOG_FILE, _BASE_DIR
from scanner.calendar import KST
from scanner.logger import log

LEARNING_FILE = os.path.join(_BASE_DIR, "learning_state.json")

# 기본 가정 (백테스트 초기값과 동일해야 한다)
PRIOR_SLIPPAGE = 0.0015      # 0.15% 편도
PRIOR_COMMISSION = 0.00015   # 0.015% 편도

# 축소추정 강도 — 기본값에 표본 k건만큼의 무게를 준다.
# k=12: 측정 12건이 쌓여야 기본값과 5:5, 그전까지는 기본값 쪽에 무게.
SHRINK_K = 12

# 신뢰도 구간 (주문 표본 수 기준)
MIN_SAMPLES_TO_REPORT = 3    # 이보다 적으면 수치를 말하지 않는다
MIN_SAMPLES_TO_APPLY = 6     # 이보다 적으면 자동 보정하지 않는다

# 이상치 차단 — ETF 시장가 체결에서 ±20%는 슬리피지가 아니라 데이터 오류다
# (계획가 기록 누락, 액면분할, 잘못된 매입평균가 등). 표본이 적을수록 이런 한 건이
# 비용 가정을 통째로 왜곡하므로 학습 대상에서 제외한다.
MAX_PLAUSIBLE_SLIPPAGE = 0.20


# ── 측정 ────────────────────────────────────────────────────────────
def load_events(path: str | None = None) -> list[dict]:
    p = path or REBALANCE_LOG_FILE
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        log.warning(f"[학습] 리밸런싱 로그 읽기 실패: {e}")
        return []


def slippage_samples(events: list[dict]) -> list[dict]:
    """계획가·체결가가 모두 있는 매수 주문에서 슬리피지(비율)를 뽑는다.

    매수는 불리한 체결이 '더 비싸게' 나타나므로 (체결-계획)/계획 이 양수면 손해.
    체결가가 없는 주문(추가 매수 등)은 값을 복원할 수 없어 제외한다.
    """
    out = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        ts = ev.get("ts", "")
        # .get(k, []) 는 키가 있고 값이 None이면 None을 돌려준다 — 로그가 조금만
        # 깨져도 학습 잡 전체가 죽으므로 `or []` 로 받는다.
        for o in ev.get("orders") or []:
            if not isinstance(o, dict) or o.get("side") != "buy":
                continue
            planned = o.get("planned_price") or 0
            fill = o.get("fill_price") or 0
            if planned <= 0 or fill <= 0:
                continue
            slip = (fill - planned) / planned
            if abs(slip) > MAX_PLAUSIBLE_SLIPPAGE:
                log.warning(f"[학습] 이상치 제외 — {o.get('ticker','')} "
                            f"계획 {planned:,} → 체결 {fill:,} ({slip*100:+.1f}%)")
                continue
            out.append({
                "ts": ts, "ticker": o.get("ticker", ""), "name": o.get("name", ""),
                "planned": planned, "fill": fill, "slippage": slip,
                "qty": o.get("qty", 0),
            })
    return out


def summarize_slippage(samples: list[dict]) -> dict:
    """표본 요약. 중앙값을 대표값으로 쓴다(이상 체결 1건에 끌려가지 않도록)."""
    n = len(samples)
    if n == 0:
        return {"n": 0, "median": None, "mean": None, "worst": None, "by_ticker": {}}
    vals = [s["slippage"] for s in samples]
    by_ticker: dict[str, list[float]] = {}
    for s in samples:
        by_ticker.setdefault(s["ticker"], []).append(s["slippage"])
    return {
        "n": n,
        "median": statistics.median(vals),
        "mean": sum(vals) / n,
        "worst": max(vals),
        "by_ticker": {tk: {"n": len(v), "median": statistics.median(v)}
                      for tk, v in by_ticker.items()},
    }


# ── 보정 (축소추정) ─────────────────────────────────────────────────
def shrink(measured: float | None, n: int, prior: float, k: int = SHRINK_K) -> float:
    """측정값을 기본값 쪽으로 끌어당긴다. n=0이면 기본값, n이 커지면 측정값에 수렴."""
    if measured is None or n <= 0:
        return prior
    return (n * measured + k * prior) / (n + k)


def calibrate(summary: dict) -> dict:
    """실측 요약 → 백테스트에 쓸 비용 가정.

    음수 슬리피지(계획보다 싸게 체결)는 0으로 바닥을 깐다. 유리한 체결이 우연히
    이어졌다고 비용을 0으로 잡으면 백테스트가 낙관적으로 왜곡된다.
    """
    n = summary.get("n", 0)
    measured = summary.get("median")
    if measured is not None:
        measured = max(0.0, measured)
    applied = n >= MIN_SAMPLES_TO_APPLY
    value = shrink(measured, n, PRIOR_SLIPPAGE) if applied else PRIOR_SLIPPAGE
    return {
        "slippage": round(value, 6),
        "commission": PRIOR_COMMISSION,   # 수수료는 계약상 고정 — 측정 대상 아님
        "n": n,
        "applied": applied,
        "measured_median": round(measured, 6) if measured is not None else None,
        "prior": PRIOR_SLIPPAGE,
        "updated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
    }


# ── 상태 저장 ───────────────────────────────────────────────────────
def load_state(path: str | None = None) -> dict:
    p = path or LEARNING_FILE
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception as e:
        log.warning(f"[학습] 상태 읽기 실패: {e}")
        return {}


def save_state(state: dict, path: str | None = None) -> bool:
    p = path or LEARNING_FILE
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        log.warning(f"[학습] 상태 저장 실패: {e}")
        return False


def learned_costs() -> tuple[float, float]:
    """백테스트가 쓸 (슬리피지, 수수료). 학습 이력이 없으면 기본 가정."""
    c = load_state().get("costs") or {}
    slip = c.get("slippage")
    comm = c.get("commission")
    slip = slip if isinstance(slip, (int, float)) and slip >= 0 else PRIOR_SLIPPAGE
    comm = comm if isinstance(comm, (int, float)) and comm >= 0 else PRIOR_COMMISSION
    return float(slip), float(comm)


def run_learning(events: list[dict] | None = None, persist: bool = True) -> dict:
    """측정 → 보정 → 저장. 결과 요약을 돌려준다."""
    evs = load_events() if events is None else events
    samples = slippage_samples(evs)
    summary = summarize_slippage(samples)
    costs = calibrate(summary)
    state = load_state()
    prev = (state.get("costs") or {}).get("slippage")
    state["costs"] = costs
    state["summary"] = {k: v for k, v in summary.items() if k != "by_ticker"}
    state["by_ticker"] = summary["by_ticker"]
    if persist:
        save_state(state)
    if costs["applied"]:
        log.info(f"[학습] 슬리피지 보정 {PRIOR_SLIPPAGE*100:.3f}% → "
                 f"{costs['slippage']*100:.3f}% (표본 {costs['n']}건)")
    return {"summary": summary, "costs": costs, "previous_slippage": prev,
            "samples": samples}


# ── 리포트 ──────────────────────────────────────────────────────────
def build_report(result: dict) -> str:
    """텔레그램용 학습 리포트. 표본이 적으면 수치를 단정하지 않는다."""
    s, c = result["summary"], result["costs"]
    n = s["n"]
    L = ["🧠 *자기주도 학습 리포트*",
         f"({datetime.now(KST).strftime('%Y-%m-%d')})", "━━━━━━━━━━━━━━━━━━"]

    if n == 0:
        L.append("측정 표본 없음 — 리밸런싱 매수가 실행되면 체결 품질을 학습합니다.")
        L.append(f"현재 비용 가정: 슬리피지 {c['slippage']*100:.3f}% (기본값)")
        return "\n".join(L)

    if n < MIN_SAMPLES_TO_REPORT:
        L.append(f"표본 {n}건 — 수치를 말하기엔 너무 적습니다 (최소 {MIN_SAMPLES_TO_REPORT}건).")
        L.append("계속 기록 중이며, 표본이 쌓이면 비용 가정을 자동 보정합니다.")
        return "\n".join(L)

    med = s["median"] * 100
    L.append(f"체결 표본 *{n}건* · 슬리피지 중앙값 *{med:+.3f}%*")
    L.append(f"최악 {s['worst']*100:+.3f}% · 평균 {s['mean']*100:+.3f}%")

    if s["by_ticker"]:
        L.append("")
        L.append("*종목별 (표본 2건 이상)*")
        rows = [(tk, v) for tk, v in s["by_ticker"].items() if v["n"] >= 2]
        rows.sort(key=lambda x: -x[1]["median"])
        for tk, v in rows[:5]:
            L.append(f"  {tk} {v['median']*100:+.3f}% ({v['n']}건)")
        if not rows:
            L.append("  (종목당 2건 이상 쌓이면 표시됩니다)")

    L.append("")
    if c["applied"]:
        prev = result.get("previous_slippage")
        arrow = (f"{prev*100:.3f}% → " if isinstance(prev, (int, float)) else "")
        L.append(f"✅ 비용 가정 자동 보정: 슬리피지 {arrow}*{c['slippage']*100:.3f}%*")
        L.append(f"_측정 {c['measured_median']*100:.3f}% 를 기본값 "
                 f"{c['prior']*100:.3f}% 쪽으로 축소보정 (표본 {n}건)_")
    else:
        L.append(f"⏸ 자동 보정 보류 — 표본 {n}건 (기준 {MIN_SAMPLES_TO_APPLY}건)")
        L.append(f"현재 가정 유지: 슬리피지 {c['slippage']*100:.3f}%")

    L.append("")
    L.extend(_advice(s, c))
    return "\n".join(L)


def _advice(summary: dict, costs: dict) -> list[str]:
    """측정에서 나오는 권고 — 실행은 사람이 결정한다."""
    n, med = summary["n"], summary["median"]
    if n < MIN_SAMPLES_TO_REPORT or med is None:
        return []
    out = ["*권고*"]
    if med > 0.004:
        out.append("🔴 슬리피지가 0.4%를 넘습니다. 체결 시각을 더 늦추거나(REBALANCE_TIME) "
                   "거래대금이 큰 ETF 위주 전략을 검토하세요.")
    elif med > 0.002:
        out.append("🟡 슬리피지 0.2%↑ — 개장 직후 체결이 아닌지 REBALANCE_TIME을 확인하세요.")
    else:
        out.append("🟢 체결 품질 양호 — 현재 체결 시각을 유지하세요.")
    worst = [(tk, v) for tk, v in summary["by_ticker"].items()
             if v["n"] >= 2 and v["median"] > 0.005]
    if worst:
        names = ", ".join(tk for tk, _ in worst)
        out.append(f"⚠️ 유독 불리하게 체결되는 종목: {names} — 호가가 얇을 수 있습니다.")
    return out


def job_learning_review() -> str:
    """학습 1회전 실행 후 텔레그램 발송."""
    from scanner.notify import send_telegram
    result = run_learning()
    msg = build_report(result)
    send_telegram(msg)
    log.info(f"[학습] 리포트 발송 (표본 {result['summary']['n']}건)")
    return msg
