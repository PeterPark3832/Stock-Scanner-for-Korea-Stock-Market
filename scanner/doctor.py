"""배포 점검 — 수익을 갉아먹는 설정 오류를 기동 전에 잡는다.

왜 필요한가
  이 봇에서 돈이 새는 대부분은 코드 버그가 아니라 '설정이 조용히 잘못된 상태'다.
  예전 .env에 REBALANCE_TIME=09:05가 남아 스프레드를 매달 물거나, 자본이 전략
  최소요구액에 못 미쳐 목표 종목을 못 담거나, 과세 비중이 높은 줄 모른 채 운용된다.
  전부 증상이 눈에 안 보여서 방치되는 것들이라, 한 번에 점검해 알려준다.

사용:
    python -m scanner.doctor            # 전체 점검
    python -m scanner.doctor --no-net   # 네트워크 없이 설정만
"""
from __future__ import annotations

OK, WARN, FAIL = "OK", "WARN", "FAIL"
_MARK = {OK: "✅", WARN: "🟡", FAIL: "🔴"}


class Check:
    __slots__ = ("name", "status", "detail", "fix")

    def __init__(self, name: str, status: str, detail: str, fix: str = ""):
        self.name, self.status, self.detail, self.fix = name, status, detail, fix

    def __repr__(self) -> str:
        return f"Check({self.name}, {self.status})"


# ── 설정 점검 (네트워크 불필요) ────────────────────────────────────
def check_strategy() -> Check:
    from scanner.config import STRATEGY_MODE, STRATEGY_KEY
    from scanner.strategy_rebalance import STRATEGIES
    if STRATEGY_MODE not in ("rebalance", "breakout"):
        return Check("전략 모드", FAIL, f"알 수 없는 STRATEGY_MODE={STRATEGY_MODE!r}",
                     "rebalance 또는 breakout 으로 설정하세요")
    if STRATEGY_MODE != "rebalance":
        return Check("전략 모드", WARN, "breakout(눌림목) 레거시 모드로 운용 중",
                     "리밸런싱을 쓰려면 STRATEGY_MODE=rebalance")
    if STRATEGY_KEY not in STRATEGIES:
        return Check("전략 선택", FAIL, f"STRATEGY_KEY={STRATEGY_KEY!r} 는 없는 전략 "
                     f"(기본값 kr_gem으로 폴백됨)",
                     f"사용 가능: {', '.join(STRATEGIES)}")
    return Check("전략 선택", OK, f"{STRATEGIES[STRATEGY_KEY]['name']} ({STRATEGY_KEY})")


def check_rebalance_time() -> Check:
    from scanner.config import REBALANCE_TIME, rebalance_time_warning
    w = rebalance_time_warning()
    if w:
        return Check("체결 시각", FAIL, w,
                     "REBALANCE_TIME=10:00 (ETF LP 호가가 회복된 시간대)")
    return Check("체결 시각", OK, f"{REBALANCE_TIME} — 유동성 확보 시간대")


def check_cash_buffer() -> Check:
    from scanner.config import REBALANCE_CASH_BUFFER as b
    if not 0.90 <= b <= 1.0:
        return Check("매수 버퍼", FAIL, f"REBALANCE_CASH_BUFFER={b} 는 비정상 범위",
                     "0.99~0.995 권장")
    if b == 1.0:
        return Check("매수 버퍼", WARN, "버퍼 없음 — 체결가 변동 시 매수 전량 실패 가능",
                     "REBALANCE_CASH_BUFFER=0.995")
    return Check("매수 버퍼", OK, f"{b} (여유 {(1-b)*100:.1f}%)")


def check_dashboard_token() -> Check:
    from scanner.config import _BASE_DIR  # noqa: F401  (경로 기준만 사용)
    import os
    tok = os.getenv("DASHBOARD_TOKEN", "")
    if not tok:
        try:
            with open(os.path.join(_BASE_DIR, ".env"), encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("DASHBOARD_TOKEN="):
                        tok = line.split("=", 1)[1].strip()
                        break
        except Exception:
            pass
    if not tok:
        return Check("대시보드 토큰", FAIL, "미설정 — 대시보드가 기동을 거부합니다",
                     'python -c "import secrets;print(secrets.token_urlsafe(24))" 로 생성')
    if len(tok) < 20:
        return Check("대시보드 토큰", FAIL, f"{len(tok)}자 — 20자 미만은 거부됩니다",
                     "더 긴 토큰으로 교체하세요")
    return Check("대시보드 토큰", OK, f"{len(tok)}자")


def check_auto_trade() -> Check:
    from scanner.config import KIS_ACCOUNT_NO, _AUTO_TRADE_INIT
    if _AUTO_TRADE_INIT and not KIS_ACCOUNT_NO:
        return Check("자동매매", FAIL, "AUTO_TRADE=true 인데 KIS_ACCOUNT_NO 미설정 "
                     "— 주문이 전부 실패합니다", "KIS_ACCOUNT_NO를 .env에 입력")
    if not _AUTO_TRADE_INIT:
        return Check("자동매매", WARN, "OFF — 리밸런싱 신호만 알리고 주문은 하지 않습니다",
                     "실제 운용하려면 AUTO_TRADE=true")
    return Check("자동매매", OK, f"ON (계좌 {KIS_ACCOUNT_NO[:4]}****)")


def check_telegram() -> Check:
    from scanner.config import TELEGRAM_TOKEN, TELEGRAM_CHAT_IDS
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_IDS:
        return Check("텔레그램", WARN, "미설정 — 리밸런싱·전략 리뷰 알림을 못 받습니다",
                     "TELEGRAM_TOKEN / TELEGRAM_CHAT_IDS 설정")
    return Check("텔레그램", OK, f"채팅방 {len(TELEGRAM_CHAT_IDS)}개")


def check_review_schedule() -> Check:
    from scanner.config import STRATEGY_REVIEW_DAY, STRATEGY_REVIEW_TIME
    if not STRATEGY_REVIEW_DAY:
        return Check("전략 리뷰", WARN, "자동 발송 꺼짐 — 전략 적합성을 검증할 수 없습니다",
                     "STRATEGY_REVIEW_DAY=25")
    return Check("전략 리뷰", OK, f"매월 {STRATEGY_REVIEW_DAY}일 {STRATEGY_REVIEW_TIME} "
                 f"+ 기동 직후 1회")


# ── 실계좌/시세 점검 (네트워크 필요) ───────────────────────────────
def check_market_data() -> Check:
    from scanner.config import STRATEGY_KEY
    try:
        from scanner.strategy_rebalance import compute_target_weights
        targets = compute_target_weights(STRATEGY_KEY)
    except Exception as e:
        return Check("시세 데이터", FAIL, f"조회 예외: {str(e)[:80]}",
                     "FDR 접근(네트워크·차단 여부) 확인")
    if not targets:
        return Check("시세 데이터", FAIL, "목표 비중을 산출하지 못했습니다 "
                     "(리밸런싱은 자동 중단됩니다)", "FDR 접근을 확인하세요")
    names = ", ".join(f"{t['name'][:12]} {t['weight']:.0f}%" for t in targets)
    return Check("시세 데이터", OK, f"목표 {len(targets)}종목 — {names}")


def check_capital() -> Check:
    from scanner.config import STRATEGY_KEY, REBALANCE_CASH_BUFFER
    from scanner.strategy_rebalance import compute_target_weights, get_strategy
    try:
        from scanner.job_rebalance import _current_state, _total_value, _live_prices
        targets = compute_target_weights(STRATEGY_KEY)
        holdings, cash = _current_state()
        holdings = holdings or {}   # 잔고조회 실패(None) 안전 처리
        live = _live_prices([t["ticker"] for t in targets] + list(holdings))
        total = _total_value(holdings, cash, live)
    except Exception as e:
        return Check("자본 적정성", WARN, f"계좌 조회 실패: {str(e)[:60]}",
                     "KIS 자격증명·계좌번호 확인")
    if not targets or total <= 0:
        return Check("자본 적정성", WARN, "평가금액 또는 목표를 확인할 수 없습니다")
    need = [(live.get(t["ticker"]) or t["price"]) * 100 / t["weight"] / REBALANCE_CASH_BUFFER
            for t in targets if t.get("weight") and (live.get(t["ticker"]) or t.get("price"))]
    min_req = int(max(need)) if need else 0
    spec = get_strategy(STRATEGY_KEY)
    if min_req and total < min_req:
        return Check("자본 적정성", FAIL,
                     f"평가금액 {total:,}원 < 최소 필요 {min_req:,}원 — "
                     f"일부 목표 종목을 1주도 담을 수 없어 비중이 크게 어긋납니다",
                     f"{min_req - total:,}원 추가 입금하거나, 더 저가 구성 전략으로 변경 "
                     f"(권장 시드 {spec['min_seed']:,}원)")
    return Check("자본 적정성", OK, f"평가금액 {total:,}원 (최소 필요 {min_req:,}원)")


def check_tax_exposure() -> Check:
    try:
        from scanner.job_strategy_review import current_tax_profile
        t = current_tax_profile()
    except Exception:
        t = None
    if not t:
        return Check("세금 노출", WARN, "보유가 없거나 조회 실패 — 리밸런싱 후 재점검")
    if t["taxable_pct"] <= 0:
        return Check("세금 노출", OK, "전액 매매차익 비과세 구성")
    status = FAIL if t["taxable_pct"] >= 80 else WARN
    return Check("세금 노출", status,
                 f"과세 대상 {t['taxable_pct']:.0f}% — 세전 10% 수익 시 "
                 f"약 {t['drag_per_10pct']:.1f}%p가 세금",
                 "ISA·연금저축 계좌 활용 검토 (일반 위탁계좌는 기타 ETF 차익 15.4% 과세)")


CONFIG_CHECKS = [check_strategy, check_rebalance_time, check_cash_buffer,
                 check_dashboard_token, check_auto_trade, check_telegram,
                 check_review_schedule]
NETWORK_CHECKS = [check_market_data, check_capital, check_tax_exposure]


def run(include_network: bool = True) -> list[Check]:
    checks = list(CONFIG_CHECKS) + (NETWORK_CHECKS if include_network else [])
    out = []
    for fn in checks:
        try:
            out.append(fn())
        except Exception as e:  # 점검 하나가 죽어도 나머지는 계속
            out.append(Check(fn.__name__, WARN, f"점검 실패: {str(e)[:80]}"))
    return out


def format_report(checks: list[Check]) -> str:
    lines = ["", "=" * 68, " 배포 점검 — 수익에 영향을 주는 설정", "=" * 68]
    for c in checks:
        lines.append(f"{_MARK[c.status]} {c.name:12s} {c.detail}")
        if c.fix and c.status != OK:
            lines.append(f"   └ 조치: {c.fix}")
    n_fail = sum(1 for c in checks if c.status == FAIL)
    n_warn = sum(1 for c in checks if c.status == WARN)
    lines.append("-" * 68)
    if n_fail:
        lines.append(f"🔴 조치 필요 {n_fail}건 · 확인 권장 {n_warn}건 "
                     f"— 지금 배포하면 손실이 발생할 수 있습니다")
    elif n_warn:
        lines.append(f"🟡 확인 권장 {n_warn}건 — 치명적 문제는 없습니다")
    else:
        lines.append("✅ 전 항목 통과 — 배포 가능")
    lines.append("=" * 68)
    return "\n".join(lines)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="배포 점검")
    ap.add_argument("--no-net", action="store_true", help="네트워크 점검 생략")
    args = ap.parse_args()
    checks = run(include_network=not args.no_net)
    print(format_report(checks))
    return 1 if any(c.status == FAIL for c in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
