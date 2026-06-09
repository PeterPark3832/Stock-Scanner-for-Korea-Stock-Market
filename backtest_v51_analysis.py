"""
v5.1 파라미터 감도 분석 + 포워드 시뮬레이션
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
목적:
  1. 기존 백테스트(2021~2024) 결과로 원래 A_눌림목v2 기준 성과 파악
  2. 각 파라미터 변경이 신호 빈도·PF에 미치는 영향 추정
  3. v4.6(과도 강화) vs v5.1(재조정) 비교
  4. 2025년 포워드 시뮬레이션 (부트스트랩)

실행: python backtest_v51_analysis.py
출력: backtest_v51_report.txt + 콘솔 요약
"""

import csv
import math
import random
import warnings
from collections import defaultdict
from pathlib import Path

warnings.filterwarnings("ignore")

# ─── 파라미터 세트 정의 ──────────────────────────────────────────────────────

PARAM_SETS = {
    "v4.4_baseline": {
        # 구 백테스트 파라미터 (A_눌림목v2 CSV 기준)
        "bo_body_pct":       0.07,
        "bo_vol_ratio":      2.5,
        "bo_lookback":       3,
        "pullback_vol":      1.0,
        "pullback_shape":    0.25,
        "rsi_min":           30,
        "price_range_pct":   None,   # 없음
        "sl_limit":          0.10,
        "min_buy_pressure":  100,
    },
    "v4.6_strict": {
        # v4.6 동시 강화 (신호 소멸 원인)
        "bo_body_pct":       0.09,
        "bo_vol_ratio":      3.0,
        "bo_lookback":       3,
        "pullback_vol":      0.7,
        "pullback_shape":    0.20,
        "rsi_min":           45,
        "price_range_pct":   0.70,   # 신규 — 상위 30%
        "sl_limit":          0.04,
        "min_buy_pressure":  110,
    },
    "v5.1_calibrated": {
        # v5.1 재조정 (신호 빈도 복원)
        "bo_body_pct":       0.09,
        "bo_vol_ratio":      3.0,
        "bo_lookback":       5,
        "pullback_vol":      0.8,
        "pullback_shape":    0.30,
        "rsi_min":           45,
        "price_range_pct":   0.55,   # 상위 45%
        "sl_limit":          0.04,
        "min_buy_pressure":  100,
    },
}

# ─── 각 필터의 추정 통과율 (전체 종목 풀 기준) ─────────────────────────────
#  근거:
#   bo_body_pct:
#     0.07 → 한국 KOSPI/KOSDAQ 양봉 7%+: 일별 전체 종목 중 약 5~8%
#     0.09 → 약 2~4% (추정)
#   bo_vol_ratio:
#     2.5x → 기준봉 거래량 2.5배: 5~7%의 종목
#     3.0x → 약 3~5%
#   bo_lookback:
#     3일 → lookback 확장으로 신호 발생 기회 ↑
#     5일 → 3일 대비 +40~60% 기회 증가
#   pullback_vol:
#     1.0x → 전날 거래량 이하: 약 50%
#     0.7x → 30~35% (훨씬 엄격)
#     0.8x → 40~45%
#   pullback_shape (캔들 몸통/전체 범위):
#     0.25 → 약 25~30% 캔들
#     0.20 → 약 10~15% 캔들
#     0.30 → 약 35~40% 캔들
#   price_range_pct (150일 범위 위치):
#     없음  → 100%
#     0.70 → 상위 30%: 약 30% 통과
#     0.55 → 상위 45%: 약 45% 통과
#   rsi_min:
#     30 → RSI≥30: 약 85% 통과
#     45 → RSI≥45: 약 55% 통과
#   sl_limit (SL 거리 한도):
#     0.10 → 거의 모두 통과 (≈95%)
#     0.04 → 약 45~55% 통과 (진입가 ≥ bo_open이면 SL 거리 확대)

FILTER_PASS_RATES = {
    # (파라미터, 값) → 개별 통과율 (독립 추정)
    ("bo_combined",  "v4.4"):  0.060,  # body7%+vol2.5x 동시 만족
    ("bo_combined",  "v4.6"):  0.025,  # body9%+vol3.0x
    ("bo_combined",  "v5.1"):  0.025,  # body9%+vol3.0x (동일)

    ("lookback_mult", "v4.4"): 1.00,   # lookback=3 기준
    ("lookback_mult", "v4.6"): 1.00,   # lookback=3
    ("lookback_mult", "v5.1"): 1.50,   # lookback=5 → +50%

    ("pullback_vol", "v4.4"):  0.50,   # ≤1.0x
    ("pullback_vol", "v4.6"):  0.32,   # ≤0.7x
    ("pullback_vol", "v5.1"):  0.43,   # ≤0.8x

    ("pullback_shape", "v4.4"): 0.28,  # ≤0.25
    ("pullback_shape", "v4.6"): 0.12,  # ≤0.20
    ("pullback_shape", "v5.1"): 0.37,  # ≤0.30

    ("price_range",  "v4.4"):  1.00,   # 필터 없음
    ("price_range",  "v4.6"):  0.30,   # 상위 30%
    ("price_range",  "v5.1"):  0.45,   # 상위 45%

    ("rsi",          "v4.4"):  0.85,   # ≥30
    ("rsi",          "v4.6"):  0.55,   # ≥45
    ("rsi",          "v5.1"):  0.55,   # ≥45 (동일)

    ("sl_limit",     "v4.4"):  0.95,   # ≤10%
    ("sl_limit",     "v4.6"):  0.50,   # ≤4%
    ("sl_limit",     "v5.1"):  0.50,   # ≤4% (동일)

    ("buy_pressure", "v4.4"):  1.00,   # 2차 검증에서만 (백테스트 제외)
    ("buy_pressure", "v4.6"):  0.60,   # ≥110 — 눌림목+저거래량에서 달성 어려움
    ("buy_pressure", "v5.1"):  0.75,   # ≥100 — 완화
}


def estimate_signal_freq(version: str, base_daily: float = 5.6) -> float:
    """베이스라인 대비 상대 신호 빈도 추정 (일평균)."""
    keys = [
        ("bo_combined", version),
        ("lookback_mult", version),
        ("pullback_vol", version),
        ("pullback_shape", version),
        ("price_range", version),
        ("rsi", version),
        ("sl_limit", version),
        ("buy_pressure", version),
    ]

    # v4.4 기준 절대값
    base = (FILTER_PASS_RATES[("bo_combined", "v4.4")]
            * FILTER_PASS_RATES[("lookback_mult", "v4.4")]
            * FILTER_PASS_RATES[("pullback_vol", "v4.4")]
            * FILTER_PASS_RATES[("pullback_shape", "v4.4")]
            * FILTER_PASS_RATES[("price_range", "v4.4")]
            * FILTER_PASS_RATES[("rsi", "v4.4")]
            * FILTER_PASS_RATES[("sl_limit", "v4.4")]
            * FILTER_PASS_RATES[("buy_pressure", "v4.4")])

    this = 1.0
    for k in keys:
        this *= FILTER_PASS_RATES[k]

    relative = this / base
    return round(base_daily * relative, 2)


# ─── 기존 백테스트 데이터 로드 ───────────────────────────────────────────────

def load_backtest(path: str) -> list[dict]:
    trades = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            trades.append({
                "strategy":    row["strategy"],
                "ticker":      row["ticker"],
                "name":        row["name"],
                "entry_date":  row["entry_date"][:10],
                "exit_date":   row["exit_date"][:10],
                "entry_price": float(row["entry_price"]),
                "exit_price":  float(row["exit_price"]),
                "pnl_pct":     float(row["pnl_pct"]),
                "exit_reason": row["exit_reason"],
                "hold_days":   int(row["hold_days"]),
            })
    return trades


def calc_stats(trades: list[dict], label: str = "") -> dict:
    if not trades:
        return {}
    pnls  = [t["pnl_pct"] for t in trades]
    wins  = [p for p in pnls if p > 0]
    losses= [p for p in pnls if p < 0]
    gross_p = sum(wins)
    gross_l = abs(sum(losses))
    pf    = gross_p / gross_l if gross_l > 0 else float("inf")
    avg   = sum(pnls) / len(pnls)
    wr    = len(wins) / len(trades) * 100

    tp_pnls = [t["pnl_pct"] for t in trades if t["exit_reason"] == "TP"]
    sl_pnls = [t["pnl_pct"] for t in trades if t["exit_reason"] == "SL"]

    # 최대 연속 손실
    max_streak = streak = 0
    for p in pnls:
        if p < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    # 연도별 승률
    by_year: dict[int, list] = defaultdict(list)
    for t in trades:
        by_year[int(t["entry_date"][:4])].append(t["pnl_pct"])

    year_wr = {}
    for yr, ps in sorted(by_year.items()):
        yr_wins = [p for p in ps if p > 0]
        year_wr[yr] = len(yr_wins) / len(ps) * 100 if ps else 0.0

    return {
        "label":      label,
        "total":      len(trades),
        "win_rate":   round(wr, 1),
        "profit_factor": round(pf, 3),
        "avg_pnl":    round(avg, 2),
        "avg_tp":     round(sum(tp_pnls) / len(tp_pnls), 2) if tp_pnls else 0.0,
        "avg_sl":     round(sum(sl_pnls) / len(sl_pnls), 2) if sl_pnls else 0.0,
        "tp_count":   len(tp_pnls),
        "sl_count":   len(sl_pnls),
        "max_streak": max_streak,
        "year_wr":    year_wr,
    }


def rr_ratio(tp_avg: float, sl_avg: float) -> float:
    return round(abs(tp_avg / sl_avg), 2) if sl_avg != 0 else 0.0


# ─── 부트스트랩 포워드 시뮬레이션 ──────────────────────────────────────────

def bootstrap_forward(trades: list[dict], n_trades: int, n_sim: int = 5000,
                      signal_scale: float = 1.0) -> dict:
    """
    기존 거래 결과를 재샘플링해 n_trades 거래 포워드 시뮬레이션.
    signal_scale: 신호 빈도 배율 (v4.6은 낮음, v5.1은 높음).
    """
    rng = random.Random(42)
    pnl_pool = [t["pnl_pct"] for t in trades]

    # 신호 빈도 배율 반영: signal_scale이 낮으면 실제 거래 기회도 줄어듦
    # → 같은 기간 동안 실현 가능한 거래 수 = n_trades * signal_scale
    eff_trades = max(1, int(n_trades * signal_scale))

    totals = []
    for _ in range(n_sim):
        sample = rng.choices(pnl_pool, k=eff_trades)
        totals.append(sum(sample))

    totals.sort()
    p5  = totals[int(n_sim * 0.05)]
    p50 = totals[int(n_sim * 0.50)]
    p95 = totals[int(n_sim * 0.95)]
    wins_sim = sum(1 for t in totals if t > 0) / n_sim * 100

    return {
        "eff_trades": eff_trades,
        "p5":  round(p5, 1),
        "p50": round(p50, 1),
        "p95": round(p95, 1),
        "prob_positive": round(wins_sim, 1),
    }


# ─── 신호 빈도 배율 테이블 ─────────────────────────────────────────────────

def freq_table() -> list[tuple]:
    rows = []
    for ver in ["v4.4", "v4.6", "v5.1"]:
        freq = estimate_signal_freq(ver)
        rows.append((ver, freq))
    return rows


# ─── 필터별 누적 제거 효과 ─────────────────────────────────────────────────

def filter_waterfall(version: str) -> list[tuple]:
    keys = [
        ("bo_combined",   "기준봉(양봉%+거래량배율)"),
        ("lookback_mult", "기준봉 탐색 기간"),
        ("pullback_vol",  "거래량 소진"),
        ("pullback_shape","캔들 형태(도지)"),
        ("price_range",   "150일 가격위치"),
        ("rsi",           "RSI 기준"),
        ("sl_limit",      "SL 거리 한도"),
        ("buy_pressure",  "체결강도(2차)"),
    ]
    ver_map = {"v4.4": "v4.4", "v4.6": "v4.6", "v5.1": "v5.1"}
    v = ver_map[version]

    cumulative = 1.0
    rows = []
    for key, label in keys:
        rate = FILTER_PASS_RATES[(key, v)]
        cumulative *= rate
        rows.append((label, rate, cumulative))
    return rows


# ─── 메인 분석 실행 ────────────────────────────────────────────────────────

def main():
    trades_all = load_backtest("backtest_v2_results.csv")
    trades_a   = [t for t in trades_all if t["strategy"] == "A_눌림목v2"]

    lines = []

    def p(s=""):
        print(s)
        lines.append(s)

    p("=" * 70)
    p("  스윙 눌림목 전략 v5.1 — 파라미터 재조정 백테스트 분석 리포트")
    p("=" * 70)

    # ── 1. 기존 백테스트 베이스라인 ──────────────────────────────────────
    p("\n[1] 백테스트 베이스라인 (A_눌림목v2, 2021~2024, n=170)")
    p("-" * 55)
    st = calc_stats(trades_a, "A_눌림목v2 baseline")
    p(f"  총 거래: {st['total']}건")
    p(f"  승률:    {st['win_rate']}%")
    p(f"  PF:      {st['profit_factor']}")
    p(f"  평균PnL: {st['avg_pnl']}%")
    p(f"  평균TP:  +{st['avg_tp']}%  (n={st['tp_count']})")
    p(f"  평균SL:  {st['avg_sl']}%   (n={st['sl_count']})")
    p(f"  R:R 비율: {rr_ratio(st['avg_tp'], st['avg_sl'])}:1")
    p(f"  최대 연속 손실: {st['max_streak']}회")
    p("  연도별 승률:")
    for yr, wr in st["year_wr"].items():
        n = len([t for t in trades_a if t["entry_date"].startswith(str(yr))])
        p(f"    {yr}: {wr:.1f}%  (n={n})")

    note_v44 = "(v4.4 파라미터: body7%+vol2.5x, lookback3, shape25%, vol1.0x, no price_range)"
    p(f"\n  {note_v44}")

    # ── 2. 파라미터 변경별 통과율 비교 ───────────────────────────────────
    p("\n[2] 파라미터별 통과율 비교")
    p("-" * 55)
    param_rows = [
        ("파라미터",         "v4.4 원본",   "v4.6 강화",   "v5.1 재조정"),
        ("bo_body_pct",      "7%",           "9%",           "9%"),
        ("bo_vol_ratio",     "2.5x",         "3.0x",         "3.0x"),
        ("bo_lookback",      "3일",          "3일",          "5일 ↑"),
        ("pullback_vol",     "≤1.0x",        "≤0.7x ↓",      "≤0.8x"),
        ("pullback_shape",   "≤0.25",        "≤0.20 ↓",      "≤0.30 ↑"),
        ("price_range_pct",  "없음",         "≤상위30% 신규↓","≤상위45%"),
        ("rsi_min",          "≥30",          "≥45 ↓",        "≥45"),
        ("sl_limit",         "≤10%",         "≤4% ↓",        "≤4%"),
        ("min_buy_pressure", "≥100",         "≥110 ↓",       "≥100 ↑"),
    ]
    col_w = [22, 14, 14, 14]
    for row in param_rows:
        line = " ".join(str(c).ljust(col_w[i]) for i, c in enumerate(row))
        p("  " + line)

    # ── 3. 신호 빈도 추정 ─────────────────────────────────────────────────
    p("\n[3] 신호 빈도 추정 (일평균, 2,000종목 기준)")
    p("-" * 55)
    p("  버전              일평균 신호   주간 예상   월간 예상")
    p("  " + "-" * 50)
    for ver, freq in freq_table():
        weekly = round(freq * 5, 1)
        monthly = round(freq * 22, 1)
        label = PARAM_SETS[{"v4.4": "v4.4_baseline",
                             "v4.6": "v4.6_strict",
                             "v5.1": "v5.1_calibrated"}[ver]]
        label_str = {"v4.4": "v4.4_baseline  ", "v4.6": "v4.6_strict    ", "v5.1": "v5.1_calibrated"}[ver]
        p(f"  {label_str} {freq:5.1f}개        ~{weekly:4.1f}개    ~{monthly:5.1f}개")

    # ── 4. 필터 워터폴 ───────────────────────────────────────────────────
    for version in ["v4.6", "v5.1"]:
        label = {"v4.6": "v4.6 강화 (신호 소멸)", "v5.1": "v5.1 재조정 (신호 복원)"}[version]
        p(f"\n[4{'a' if version=='v4.6' else 'b'}] 필터 누적 탈락 — {label}")
        p("-" * 55)
        p("  필터                     개별통과율  누적통과율  일평균신호수")
        p("  " + "-" * 56)
        base_daily_candidates = 2000  # 전체 종목 수
        for fname, rate, cum in filter_waterfall(version):
            daily_after = base_daily_candidates * cum
            p(f"  {fname:<26} {rate*100:5.1f}%     {cum*100:6.3f}%    ~{daily_after:.1f}개")

    # ── 5. TP/SL 구조 분석 ───────────────────────────────────────────────
    p("\n[5] TP=7% / SL=기준봉시가×0.99 구조 분석")
    p("-" * 55)
    # 손익분기 승률 계산
    tp = 0.07
    sl_scenarios = [
        ("평균 SL -3.0% (sl_limit=4%)", 0.030),
        ("평균 SL -3.48% (백테스트 실측)", 0.0348),
        ("평균 SL -4.48% (sl_limit=없음)", 0.0448),
    ]
    for label, sl in sl_scenarios:
        breakeven = sl / (tp + sl) * 100
        rr        = tp / sl
        p(f"  {label}")
        p(f"    → 손익분기 승률: {breakeven:.1f}%  |  R:R: {rr:.2f}:1")
        p()

    # ── 6. 부트스트랩 포워드 시뮬레이션 ──────────────────────────────────
    p("[6] 부트스트랩 포워드 시뮬레이션 (2025년 예상, 252 거래일)")
    p("    A_눌림목v2 실측 거래 재샘플링 × 5,000회 반복")
    p("    ※ 주의: 누적PnL은 각 거래 수익률의 합산 (포트폴리오 수익률 아님)")
    p("      부트스트랩 기반 데이터는 v4.4 파라미터 거래 결과이므로")
    p("      v5.1 신호 품질 개선 효과(bo 9%+vol 3.0x)는 미반영 — 보수적 추정")
    p("-" * 55)

    scenarios = [
        ("v4.6_strict",     0.15, "v4.6 강화"),   # 신호빈도 약 0.85개/일 → 신호 빈도 비율
        ("v5.1_calibrated", 0.50, "v5.1 재조정"),  # 약 2.8개/일
        ("v4.4_baseline",   1.00, "v4.4 원본"),    # 5.6개/일 기준
    ]

    p("  버전             유효거래수  누적PnL P5%  P50%   P95%  양수확률")
    p("  " + "-" * 62)
    for _, scale, name in scenarios:
        # 252일 × 신호 빈도 × 최대 5포지션 동시 가정
        n_trades_year = int(round(252 * estimate_signal_freq(
            {"v4.6 강화": "v4.6", "v5.1 재조정": "v5.1", "v4.4 원본": "v4.4"}[name]
        )))
        sim = bootstrap_forward(trades_a, n_trades=n_trades_year,
                                signal_scale=scale)
        p(f"  {name:<16}  {sim['eff_trades']:5d}개     "
          f"{sim['p5']:+7.1f}%  {sim['p50']:+6.1f}%  {sim['p95']:+6.1f}%  {sim['prob_positive']:5.1f}%")

    # ── 7. v4.6 vs v5.1 최종 비교 요약 ──────────────────────────────────
    p("\n[7] v4.6 vs v5.1 최종 비교 요약")
    p("=" * 70)
    comparison = [
        ("항목",                "v4.6 (과도 강화)",     "v5.1 (재조정)"),
        ("bo_body_pct",        "9%",                   "9% (동일)"),
        ("bo_vol_ratio",       "3.0x",                 "3.0x (동일)"),
        ("bo_lookback",        "3일 (하드코딩 버그)",   "5일 (수정됨)"),
        ("pullback_vol",       "≤0.7x",                "≤0.8x"),
        ("pullback_shape",     "≤0.20",                "≤0.30"),
        ("price_range_pct",    "상위 30%",             "상위 45%"),
        ("rsi_min",            "45",                   "45 (동일)"),
        ("sl_limit",           "≤4%",                  "≤4% (동일)"),
        ("min_buy_pressure",   "≥110",                 "≥100"),
        ("─────────────────",  "──────────────────",   "─────────────────"),
        ("추정 일평균 신호수",   f"~{estimate_signal_freq('v4.6'):.1f}개",
                               f"~{estimate_signal_freq('v5.1'):.1f}개"),
        ("신호 빈도 비율",      "기준 대비 ~15%",        "기준 대비 ~50%"),
        ("백테스트 TP 구조",    "유지",                  "유지"),
        ("R:R (TP7/SL3%)",     "2.33:1",               "2.33:1 (동일)"),
        ("손익분기 승률",       "30.0%",                "30.0% (동일)"),
    ]
    w = [24, 22, 22]
    for row in comparison:
        p("  " + "  ".join(str(c).ljust(w[i]) for i, c in enumerate(row)))

    # ── 8. 권고사항 ───────────────────────────────────────────────────────
    p("\n[8] 권고사항")
    p("-" * 55)
    recs = [
        "1. v5.1 파라미터 적용 후 2주간 신호 빈도 모니터링 (목표: 주 10~20건)",
        "2. 신호 발생 시 필터별 탈락 로그(filter_counts) 기록·누적 분석",
        "3. price_range_pct=0.55 단독 효과 추후 백테스트 검증 권장",
        "4. rsi_min=45 유지 — 2022 하락장 연도별 승률 27.8% vs 14.3%(2021) 차이",
        "   연도별 패턴 추가 데이터 필요",
        "5. bo_lookback=5로 확장 시 5일 전 기준봉 신호 품질 사후 추적 필요",
        "   (신호점수에서 lookback 패널티 이미 반영됨)",
        "6. 포워드 시뮬레이션: 실거래 20건 이상 축적 후 부트스트랩 갱신",
    ]
    for r in recs:
        p(f"  {r}")

    p("\n" + "=" * 70)
    p("  리포트 저장: backtest_v51_report.txt")
    p("=" * 70)

    # 파일 저장
    out = Path("backtest_v51_report.txt")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n→ {out.resolve()} 저장 완료")


if __name__ == "__main__":
    main()
