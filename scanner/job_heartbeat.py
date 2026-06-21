"""매일 09:00 Heartbeat — 봇 생존신호·만료 포지션 처리·주간 리포트."""
import csv
import time
from datetime import datetime

from scanner.config import TRADE_HISTORY_FILE, STRATEGY, STRATEGY_MODE
from scanner import state
from scanner.notify import send_telegram, _esc, order_result_tag
from scanner.positions import check_expired_positions, save_positions
from scanner.history import record_trade_history
from scanner.performance import send_weekly_report
from scanner.kis import get_current_price, place_order
from scanner.calendar import KST, is_market_closed, count_weekdays
from scanner.state import _HISTORY_FLOCK
from scanner.logger import log


def _build_position_line(p: dict) -> str:
    entry   = p.get("entry", 0)
    tp      = p.get("tp", 0)
    sl      = p.get("sl", 0)
    sl_init = p.get("sl_init", sl)
    ticker  = p["ticker"]
    name    = p["name"]
    days    = p.get("elapsed_days", 5)
    edate   = p.get("entry_date", "-")
    qty     = p.get("quantity", 0)
    qty_str = f" | {qty}주" if qty > 0 else ""

    live = get_current_price(ticker)
    if live:
        cur       = live["current"]
        pnl_pct   = (cur - entry) / entry * 100 if entry else 0
        trail_tag = f" | Trail SL {sl:,}" if sl > sl_init else ""
        if cur >= tp:
            status = f"✅ TP 달성 ({pnl_pct:+.1f}%) — 익절 완료 확인"
        elif cur <= sl:
            status = f"🔴 SL 도달 ({pnl_pct:+.1f}%) — 손절 확인 필요"
        else:
            emoji  = "📈" if pnl_pct >= 0 else "📉"
            status = f"{emoji} 보유 중 ({pnl_pct:+.1f}%) — 정리 검토"
        return (
            f"• *{_esc(name)}* ({ticker}){qty_str}\n"
            f"  진입 {entry:,}원 → 현재 *{cur:,}원* | {days}일 경과\n"
            f"  TP {tp:,}원 / SL {sl:,}원{trail_tag}\n"
            f"  {status}\n"
        )
    else:
        return (
            f"• *{_esc(name)}* ({ticker}){qty_str}\n"
            f"  진입 {entry:,}원 | {days}일 경과 ({edate} 진입)\n"
            f"  TP {tp:,}원 / SL {sl:,}원 ⚠️ 현재가 조회 실패\n"
        )


def _update_post_expire_pnl(now: datetime) -> None:
    """EXPIRE 청산 후 5거래일 경과 시 post_expire_pnl 컬럼을 채운다."""
    import os
    if not os.path.exists(TRADE_HISTORY_FILE):
        return
    try:
        with _HISTORY_FLOCK:
            with open(TRADE_HISTORY_FILE, "r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
    except Exception:
        return

    updated = 0
    for row in rows:
        if row.get("exit_reason") != "EXPIRE":
            continue
        if row.get("post_expire_pnl"):
            continue
        exit_dt = datetime.strptime(row["exit_date"], "%Y-%m-%d")
        if count_weekdays(exit_dt, now) < 5:
            continue
        live = get_current_price(row["ticker"])
        if not live:
            continue
        entry = float(row["entry_price"]) if row.get("entry_price") else 0
        if not entry:
            continue
        post_pnl = round((live["current"] - entry) / entry * 100, 2)
        row["post_expire_pnl"] = post_pnl
        updated += 1
        time.sleep(0.1)

    if updated:
        fieldnames = list(rows[0].keys()) if rows else []
        try:
            with _HISTORY_FLOCK:
                with open(TRADE_HISTORY_FILE, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)
            log.info(f"  [EXPIRE 사후추적] {updated}건 post_expire_pnl 기록 완료")
        except Exception as e:
            log.error(f"  EXPIRE 사후추적 기록 실패: {e}")


def _heartbeat_rebalance(now: datetime) -> None:
    """kr_gem 리밸런싱 모드: 일별 평가금액 스냅샷 + 경량 생존신호.
    (눌림목 만료/TP/SL 매도 로직은 적용 안 함 — 월간 리밸런싱까지 보유 유지)"""
    from scanner.config import _KIS_MODE
    from scanner.job_rebalance import snapshot_equity
    snap = snapshot_equity()
    with state._auto_trade_lock:
        do_trade = state._auto_trade_enabled
    total = snap["total"] if snap else 0
    equity = snap["equity"] if snap else 0
    cash = snap["cash"] if snap else 0
    send_telegram(
        f"💚 *kr_gem 봇 정상 작동 중* ({now.strftime('%Y-%m-%d %H:%M')})\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 평가금액: {total:,}원 (주식 {equity:,} + 현금 {cash:,})\n"
        f"🔑 KIS 모드: {'실전투자' if _KIS_MODE == 'real' else '모의투자'}\n"
        f"{'🤖 자동 리밸런싱 ON' if do_trade else '📋 수동 모드'}\n"
        f"📅 다음 리밸런싱: 매월 첫 거래일 09:05"
    )
    log.info(f"[{now.strftime('%H:%M')}] Heartbeat(rebalance) — 평가금액 {total:,}원 스냅샷")
    if now.weekday() == 0:
        send_weekly_report(now)


def job_heartbeat() -> None:
    if is_market_closed(datetime.now(KST)):
        return
    now = datetime.now(KST)

    if STRATEGY_MODE == "rebalance":
        _heartbeat_rebalance(now)
        return

    with state._auto_trade_lock:
        do_trade = state._auto_trade_enabled

    expired, active = check_expired_positions()

    expired_lines = []
    if expired:
        for p in expired:
            live       = get_current_price(p["ticker"])
            exit_price = live["current"] if live else p.get("entry", 0)

            order_result = None
            qty = p.get("quantity", 0)
            if do_trade and qty > 0:
                order_result = place_order(p["ticker"], "sell", qty, p["name"])

            if do_trade and qty > 0 and not (order_result or {}).get("success"):
                log.error(f"  [매도실패-유지] {p['name']} EXPIRE 주문 실패 — 포지션 유지")
                active.append(p)
                continue

            record_trade_history(p, exit_price, "EXPIRE")

            entry     = p.get("entry", 0)
            pnl_pct   = (exit_price - entry) / entry * 100 if entry else 0
            days      = p.get("elapsed_days", 5)
            tp        = p.get("tp", 0)
            sl        = p.get("sl", 0)
            sl_init   = p.get("sl_init", sl)
            trail_tag = f" | Trail SL {sl:,}" if sl > sl_init else ""
            api_warn  = "" if live else " ⚠️ 현재가 조회 실패"
            otag      = order_result_tag(order_result, do_trade)
            expired_lines.append(
                f"• *{_esc(p['name'])}* ({p['ticker']})\n"
                f"  진입 {entry:,}원 → 최종 *{exit_price:,}원* ({pnl_pct:+.1f}%) | {days}일 경과\n"
                f"  TP {tp:,}원 / SL {sl:,}원{trail_tag}\n"
                f"  ⏰ 기간 만료 — 정리 검토{api_warn}\n"
                f"  {otag.strip()}\n"
            )
            time.sleep(0.2)
        save_positions(active)
        log.info(f"  만료 포지션 {len(expired)}개 이력 기록 후 제거 → 잔여 {len(active)}개")

    from scanner.config import _KIS_MODE
    total_tracking = len(active) + len(expired)
    trade_mode_str = "🤖 자동매매 ON" if do_trade else "📋 수동매매 모드"

    msg = (
        f"💚 *봇 정상 작동 중* ({now.strftime('%Y-%m-%d %H:%M')})\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⏰ 10:00 / 13:00 → 장중 TP/SL 모니터링\n"
        f"⏰ 14:30 → 1차 스크리닝\n"
        f"⏰ 15:20 → 2차 재검증 + 발송\n"
        f"🔑 KIS 모드: {'실전투자' if _KIS_MODE == 'real' else '모의투자'}\n"
        f"{trade_mode_str}\n"
        f"📋 추적 포지션: {total_tracking}개"
        + (f" (정리 대상 {len(expired)}개)" if expired else "")
    )

    if expired:
        msg += f"\n\n⏰ *5 거래일 경과 — 포지션 정리 검토*\n━━━━━━━━━━━━━━━━━━\n"
        for line in expired_lines:
            msg += line
        msg += "\n_이력이 trade\\_history.csv에 저장되었습니다_"

    send_telegram(msg)
    log.info(f"[{now.strftime('%H:%M')}] Heartbeat 발송 완료 (만료 {len(expired)}개)")

    if now.weekday() == 0:
        log.info("  [월요일] 주간 성과 리포트 생성 중...")
        send_weekly_report(now)

    _update_post_expire_pnl(now)
