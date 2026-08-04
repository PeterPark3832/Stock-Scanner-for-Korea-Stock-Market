"""전략 리뷰 — 실데이터 백테스트를 봇이 직접 돌려 텔레그램으로 보고.

왜 필요한가
  전략 선택은 이 봇의 수익에 가장 크게 작용하는 변수인데, 백테스트를 사람이 기억해서
  수동 실행해야 하면 사실상 검증 없이 운용된다. 이 잡은 매월 리밸런싱 전에 자동으로
  5개 전략을 실데이터로 비교해 결과를 밀어준다.

왜 자동 전환은 하지 않는가
  최근 성과가 좋은 전략으로 갈아타는 것(performance chasing)은 수익을 갉아먹는 대표적
  실수다. 국면이 바뀌면 방금 좋았던 전략이 가장 나쁜 전략이 된다. 그래서 이 잡은
  **보고만** 하고, 교체는 사람이 대시보드에서 명시적으로 결정하게 둔다.
  다만 '현재 전략이 여러 해에 걸쳐 일관되게 열위'인 경우에만 검토 권고를 띄운다.
"""
from datetime import datetime

from scanner.config import STRATEGY_KEY
from scanner.calendar import KST
from scanner.notify import send_telegram
from scanner.logger import log

# 검토 권고 기준 — 근소한 차이로 전략을 바꾸면 매매비용만 늘어난다.
CALMAR_GAP_TRIGGER = 0.40    # 위험조정 성과가 이 비율 이상 뒤처질 때만 언급
MIN_YEARS_FOR_VERDICT = 3.0  # 표본이 이보다 짧으면 판단 보류


def _pad(s: str, width: int) -> str:
    """한글(전각)은 표시폭이 2라 str.ljust로는 표가 어긋난다. 표시폭 기준으로 자르고 채운다."""
    import unicodedata
    w = lambda ch: 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    out, used = [], 0
    for ch in s:
        cw = w(ch)
        if used + cw > width:
            break
        out.append(ch)
        used += cw
    return "".join(out) + " " * (width - used)


def build_review(results: list[dict], current_key: str, tax: dict | None = None) -> str:
    """백테스트 결과 → 텔레그램 메시지. 순수 함수(네트워크 접근 없음).

    tax: tax_profile() 결과. 호출부에서 계산해 넘긴다(여기서 조회하면 테스트가 느려지고
         리포트 생성이 KIS 장애에 묶인다).
    """
    strat = [r for r in results if not r["key"].startswith("bh_")]
    bench = next((r for r in results if r["key"].startswith("bh_")), None)
    if not strat:
        return "⚠️ *전략 리뷰 실패*\n백테스트 결과가 비어 있습니다 (가격 데이터 확인 필요)."

    cur = next((r for r in strat if r["key"] == current_key), None)
    by_calmar = sorted(strat, key=lambda r: r["calmar"], reverse=True)
    best = by_calmar[0]
    span = f"{strat[0]['start'].year}~{strat[0]['end'].year}"
    years = strat[0]["years"]

    lines = [
        f"📊 *전략 리뷰* ({datetime.now(KST).strftime('%Y-%m-%d')})",
        f"백테스트 {span} · {years:.1f}년 · 비용 반영",
        "━━━━━━━━━━━━━━━━━━",
        f"`{_pad('전략', 16)}  CAGR    MDD Calmar`",
    ]
    for r in by_calmar:
        mark = "▶" if r["key"] == current_key else " "
        lines.append(f"`{mark}{_pad(r['name'], 15)}{r['cagr']:6.1f}%{r['mdd']:7.1f}%"
                     f"{r['calmar']:7.2f}`")
    if bench:
        lines.append(f"` {_pad('KOSPI200 보유', 15)}{bench['cagr']:6.1f}%{bench['mdd']:7.1f}%"
                     f"{bench['calmar']:7.2f}`")
    lines.append("━━━━━━━━━━━━━━━━━━")

    # 연도별 일관성 — 특정 해에만 몰린 성과는 신뢰도가 낮다
    if cur is not None:
        pos = sum(1 for v in cur["yearly"] if v == v and v > 0)
        tot = sum(1 for v in cur["yearly"] if v == v)
        lines.append(f"현재 전략 *{cur['name']}*: 연간 플러스 {pos}/{tot}년")

    note = _tax_note(tax)
    if note:
        lines.append(note)

    lines.append(_verdict(cur, best, bench, years))
    lines.append("")
    lines.append("_전략 교체는 대시보드 '리밸런싱 → 전략 변경'에서 직접 하세요._")
    lines.append("_최근 성과만 보고 자주 바꾸면 매매비용만 늘어납니다._")
    return "\n".join(lines)


def current_tax_profile() -> dict | None:
    """현재 보유의 과세 구조를 조회한다(네트워크 접근 — 호출부에서만 사용)."""
    try:
        from scanner.job_rebalance import _current_state, _live_prices
        from scanner.strategy_rebalance import tax_profile
        holdings, _ = _current_state()
        if not holdings:
            return None
        live = _live_prices(list(holdings))
        vals = {tk: (live.get(tk) or h.get("avg_price", 0)) * h.get("qty", 0)
                for tk, h in holdings.items()}
        if sum(vals.values()) <= 0:
            return None
        return tax_profile(vals)
    except Exception:
        return None


def _tax_note(t: dict | None) -> str | None:
    """현재 보유의 과세 구조 — 전략이 세전 모멘텀만 보므로 세후 손실이 안 보인다.

    수수료·스프레드(연 0.x%)보다 세금(차익의 15.4%)이 훨씬 큰 비용인데도 어디에도
    표시되지 않아, 계좌 종류를 바꾸면 얻을 수 있는 큰 이득이 방치된다.
    """
    if not t:
        return None
    if t["taxable_pct"] <= 0:
        return "\n🟢 현재 보유는 전액 매매차익 비과세 구성입니다."
    return (f"\n💸 *세금* 과세 대상 보유 {t['taxable_pct']:.0f}% "
            f"(실효 {t['effective_rate']:.1f}%)\n"
            f"세전 10% 수익 시 약 *{t['drag_per_10pct']:.1f}%p*가 세금으로 나갑니다.\n"
            f"→ 해외지수·금·채권 ETF는 차익의 15.4% 과세(국내주식형은 비과세). "
            f"ISA·연금저축 계좌를 쓰면 상당 부분 줄일 수 있습니다.")


def _verdict(cur: dict | None, best: dict, bench: dict | None, years: float) -> str:
    if years < MIN_YEARS_FOR_VERDICT:
        return f"\n⏸ 표본 {years:.1f}년 — 판단 보류 (최소 {MIN_YEARS_FOR_VERDICT:.0f}년 필요)"
    if cur is None:
        return "\n⚠️ 현재 전략이 백테스트에 없습니다 — STRATEGY_KEY 확인 필요"
    if bench and cur["cagr"] <= bench["cagr"] and cur["calmar"] <= bench["calmar"]:
        return ("\n🔴 현재 전략이 *KOSPI200 단순 보유보다 못합니다*. "
                "전략 교체 또는 인덱스 보유를 검토하세요.")
    if cur["key"] == best["key"]:
        return "\n✅ 현재 전략이 위험조정 성과 1위 — 유지 권장"
    gap = (best["calmar"] - cur["calmar"]) / abs(best["calmar"]) if best["calmar"] else 0
    if gap >= CALMAR_GAP_TRIGGER:
        return (f"\n🟡 *{best['name']}*가 위험조정 기준 우위 "
                f"(Calmar {best['calmar']:.2f} vs {cur['calmar']:.2f}). 검토 권고 — "
                "단, 연도별 성과가 고른지 함께 확인하세요.")
    return "\n✅ 1위와 유의한 차이 없음 — 유지 권장 (잦은 교체는 비용만 증가)"


REVIEW_STAMP_FILE = "review_last.json"


def _stamp_path() -> str:
    import os
    from scanner.config import _BASE_DIR
    return os.path.join(_BASE_DIR, REVIEW_STAMP_FILE)


def last_review_at() -> datetime | None:
    import json
    import os
    p = _stamp_path()
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            ts = json.load(f).get("ts", "")
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
    except Exception:
        return None


def _record_review() -> None:
    import json
    try:
        with open(_stamp_path(), "w", encoding="utf-8") as f:
            json.dump({"ts": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")}, f)
    except Exception as e:
        log.warning(f"[전략리뷰] 실행 기록 실패: {e}")


def should_run_startup_review(min_days: int = 20, now: datetime | None = None) -> bool:
    """기동 시 리뷰를 돌릴지. 한 번도 안 돌렸거나 마지막 실행이 오래됐으면 True.

    첫 배포 후 최대 한 달을 기다려야 첫 리포트를 받는 것은 늦다. 반대로 재시작마다
    돌리면 스팸이 되고 FDR 호출도 낭비다 — 그래서 최근 실행 이력으로 가른다.
    """
    last = last_review_at()
    if last is None:
        return True
    return ((now or datetime.now(KST)) - last).days >= min_days


def job_strategy_review(seed: int | None = None) -> str | None:
    """실데이터 백테스트 실행 후 텔레그램 발송. 서버(FDR 접근 가능)에서만 동작."""
    try:
        import backtest_rebalance as bt
        from scanner.strategy_rebalance import STRATEGIES, MANAGED_UNIVERSE
    except Exception as e:
        log.error(f"[전략리뷰] 모듈 로드 실패: {e}")
        return None

    if seed is None:
        seed = _current_equity() or 10_000_000

    # 학습된 비용을 매번 새로 읽는다. backtest 모듈 상수는 import 시점 값이라,
    # 장기 실행 중인 봇에서는 학습이 갱신돼도 재시작 전까지 반영되지 않는다.
    from scanner.learn import learned_costs
    slippage, commission = learned_costs()
    log.info(f"[전략리뷰] 백테스트 시작 (시드 {seed:,}원, "
             f"슬리피지 {slippage*100:.3f}% · 수수료 {commission*100:.3f}%)")
    try:
        prices = bt.fetch_prices(sorted(MANAGED_UNIVERSE), "2014-01-01")
    except Exception as e:
        log.error(f"[전략리뷰] 가격 수집 실패: {e}")
        send_telegram(f"⚠️ *전략 리뷰 실패*\n가격 데이터 수집 오류: `{str(e)[:150]}`")
        return None
    if not prices:
        send_telegram("⚠️ *전략 리뷰 실패*\n가격 데이터를 가져오지 못했습니다.")
        return None

    results = []
    for key in STRATEGIES:
        try:
            r = bt.run_backtest(key, prices, "2016-01-01", seed,
                                commission, slippage)
        except Exception as e:
            log.warning(f"[전략리뷰] {key} 실패: {e}")
            continue
        if r:
            results.append(r)
    if results:
        b = bt.buy_and_hold(prices, "069500", min(r["start"] for r in results),
                            max(r["end"] for r in results), seed,
                            commission, slippage)
        if b:
            results.append(b)

    msg = build_review(results, STRATEGY_KEY, current_tax_profile())
    send_telegram(msg)
    _record_review()
    log.info("[전략리뷰] 발송 완료")
    return msg


def _current_equity() -> int | None:
    """실제 평가금액으로 백테스트해야 정수 주수 제약이 현실과 같아진다."""
    try:
        from scanner.job_rebalance import _current_state, _total_value
        holdings, cash = _current_state()
        total = _total_value(holdings, cash)
        return total if total > 0 else None
    except Exception:
        return None
