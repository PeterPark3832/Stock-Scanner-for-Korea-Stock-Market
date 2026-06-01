"""장중 포지션 모니터링 — TP/SL/트레일링/갭오픈 체크."""
import time
from datetime import datetime

from scanner.config import STRATEGY
from scanner import state
from scanner.notify import send_telegram, _esc, order_result_tag
from scanner.positions import load_positions, save_positions
from scanner.history import record_trade_history
from scanner.kis import get_current_price, place_order
from scanner.calendar import KST, is_market_closed
from scanner.logger import log


def job_monitor_positions() -> None:
    if is_market_closed(datetime.now(KST)):
        return

    now       = datetime.now(KST)
    positions = load_positions()
    if not positions:
        log.info(f"[{now.strftime('%H:%M')}] 모니터링: 추적 포지션 없음")
        return

    with state._auto_trade_lock:
        do_trade = state._auto_trade_enabled

    log.info(f"\n{'='*50}")
    log.info(f"[{now.strftime('%H:%M')}] 장중 포지션 모니터링 ({len(positions)}개) | 자동매매: {'ON' if do_trade else 'OFF'}")
    log.info(f"{'='*50}")

    remaining = []
    tp_hit    = []
    sl_hit    = []

    for p in positions:
        ticker = p["ticker"]
        name   = p["name"]
        entry  = p.get("entry", 0)
        tp     = p.get("tp", 0)
        sl     = p.get("sl", 0)
        hwm    = p.get("high_water_mark", entry)
        qty    = p.get("quantity", 0)

        live = get_current_price(ticker)
        if not live:
            log.warning(f"  [SKIP] {name} — API 조회 실패")
            remaining.append(p)
            time.sleep(0.15)
            continue

        cur = live["current"]

        hwm_updated = False
        if cur > hwm:
            hwm = cur
            p["high_water_mark"] = hwm
            hwm_updated = True

        pnl_pct = (cur - entry) / entry * 100 if entry else 0

        trail_activate = STRATEGY.get("trail_activate_pct", 0.0)
        if pnl_pct >= trail_activate * 100 or p.get("trail_activated"):
            trail_sl = int(hwm * (1 - STRATEGY["trail_pct"]))
            if trail_sl > sl:
                p["sl"] = trail_sl
                sl = trail_sl
            if not p.get("trail_activated") and pnl_pct >= trail_activate * 100:
                p["trail_activated"] = True
                log.info(f"  [트레일ON] {name} +{pnl_pct:.1f}% 도달 → 트레일링 스탑 개시")

        hard_stop_pct = STRATEGY.get("hard_stop_pct", 0.0)
        hard_stop_sl  = int(entry * (1 - hard_stop_pct)) if hard_stop_pct else 0
        if hard_stop_sl and hard_stop_sl > sl:
            p["sl"] = hard_stop_sl
            sl = hard_stop_sl

        # TP1 분할 익절 (tp1_pct > 0 인 경우에만 활성)
        tp1 = p.get("tp1", 0)
        if tp1 and not p.get("tp1_taken") and cur >= tp1 and cur < tp:
            half_qty = qty // 2
            p["tp1_taken"] = True
            if do_trade and half_qty > 0:
                r1 = place_order(ticker, "sell", half_qty, name)
                if (r1 or {}).get("success"):
                    p["quantity"] = qty - half_qty
                    qty = p["quantity"]
                    record_trade_history({**p, "quantity": half_qty}, cur, "TP1")
                    log.info(f"  🟡 TP1 [{name}] {cur:,}원 ({pnl_pct:+.1f}%) — {half_qty}주 절반 익절")
                    send_telegram(
                        f"🟡 *TP1 절반 익절* — {_esc(name)} ({ticker})\n"
                        f"  {cur:,}원 ({pnl_pct:+.1f}%)\n"
                        f"  {half_qty}주 청산 | 잔여 {p['quantity']}주 계속 보유\n"
                        f"  TP2 = {tp:,}원 (+{int(STRATEGY['tp_pct']*100)}%)"
                    )
                else:
                    log.warning(f"  [TP1실패] {name} — 절반매도 주문 실패, 계속 추적")
            else:
                log.info(f"  🟡 TP1 [{name}] {cur:,}원 ({pnl_pct:+.1f}%) — 수동 절반 익절 권고")
            remaining.append(p)
            time.sleep(0.15)
            continue

        if cur >= tp:
            order_result = None
            if do_trade and qty > 0:
                order_result = place_order(ticker, "sell", qty, name)
            if do_trade and qty > 0 and not (order_result or {}).get("success"):
                log.error(f"  [매도실패-유지] {name} TP 도달했으나 주문 실패 — 포지션 계속 추적")
                remaining.append(p)
            else:
                record_trade_history(p, cur, "TP")
                tp_hit.append({**p, "cur": cur, "pnl_pct": pnl_pct, "order": order_result})
                log.info(f"  ✅ TP [{name}] {cur:,}원 ({pnl_pct:+.1f}%)")

        elif cur <= sl:
            sl_init = p.get("sl_init", sl)
            if hard_stop_sl and cur <= hard_stop_sl and not p.get("trail_activated"):
                reason = "HARD_SL"
            elif sl > sl_init:
                reason = "TRAIL_SL"
            else:
                reason = "SL"
            order_result = None
            if do_trade and qty > 0:
                order_result = place_order(ticker, "sell", qty, name)
            if do_trade and qty > 0 and not (order_result or {}).get("success"):
                log.error(f"  [매도실패-유지] {name} SL 도달했으나 주문 실패 — 포지션 계속 추적")
                remaining.append(p)
            else:
                record_trade_history(p, cur, reason)
                sl_hit.append({**p, "cur": cur, "pnl_pct": pnl_pct, "sl_reason": reason, "order": order_result})
                log.info(f"  🔴 SL [{name}] {cur:,}원 ({pnl_pct:+.1f}%) — {reason}")

        else:
            trail_info = f" | HWM {hwm:,} → Trail SL {sl:,}" if hwm_updated else ""
            log.info(f"  🔵 [{name}] {cur:,}원 ({pnl_pct:+.1f}%){trail_info}")
            remaining.append(p)

        time.sleep(0.15)

    save_positions(remaining)

    if tp_hit or sl_hit:
        ts  = now.strftime("%m/%d %H:%M")
        msg = f"⚡ *장중 포지션 알림* ({ts})\n\n"
        for h in tp_hit:
            tag = order_result_tag(h.get("order"), do_trade)
            msg += (
                f"✅ *TP 달성* — {_esc(h['name'])} ({h['ticker']})\n"
                f"  진입 {h['entry']:,}원 → 현재 *{h['cur']:,}원* ({h['pnl_pct']:+.1f}%)\n"
                f"  TP {h['tp']:,}원 도달 확인{tag}\n\n"
            )
        for h in sl_hit:
            tag       = " 〔트레일링〕" if h.get("sl_reason") == "TRAIL_SL" else ""
            otag      = order_result_tag(h.get("order"), do_trade)
            msg += (
                f"🔴 *SL 도달{tag}* — {_esc(h['name'])} ({h['ticker']})\n"
                f"  진입 {h['entry']:,}원 → 현재 *{h['cur']:,}원* ({h['pnl_pct']:+.1f}%)\n"
                f"  SL {h['sl']:,}원{otag}\n\n"
            )
        msg += f"잔여 추적: {len(remaining)}개"
        send_telegram(msg)
        log.info(f"  알림 발송: TP {len(tp_hit)}개 SL {len(sl_hit)}개")
    else:
        log.info(f"  TP/SL 달성 없음 (잔여 {len(remaining)}개)")


def job_morning_sl_check() -> None:
    """09:10 갭오픈 SL 조기 체크 — 진입 익일 갭손실 방어."""
    now = datetime.now(KST)
    if is_market_closed(now):
        return

    positions = load_positions()
    if not positions:
        return

    with state._auto_trade_lock:
        do_trade = state._auto_trade_enabled

    log.info(f"\n{'='*50}")
    log.info(f"[09:10] 갭오픈 SL 체크 ({len(positions)}개 포지션)")
    log.info(f"{'='*50}")

    remaining = []
    sl_hit    = []

    for p in positions:
        ticker = p["ticker"]
        name   = p["name"]
        entry  = p.get("entry", 0)
        sl     = p.get("sl", 0)
        qty    = p.get("quantity", 0)

        live = get_current_price(ticker)
        if not live:
            remaining.append(p)
            time.sleep(0.15)
            continue

        cur     = live["current"]
        pnl_pct = (cur - entry) / entry * 100 if entry else 0

        hard_stop_pct = STRATEGY.get("hard_stop_pct", 0.0)
        hard_stop_sl  = int(entry * (1 - hard_stop_pct)) if hard_stop_pct else 0
        effective_sl  = max(sl, hard_stop_sl) if hard_stop_sl else sl

        if cur <= effective_sl:
            sl_init = p.get("sl_init", sl)
            if hard_stop_sl and cur <= hard_stop_sl and not p.get("trail_activated"):
                reason = "HARD_SL"
            elif sl > sl_init:
                reason = "TRAIL_SL"
            else:
                reason = "SL"
            order_result = None
            if do_trade and qty > 0:
                order_result = place_order(ticker, "sell", qty, name)
            if do_trade and qty > 0 and not (order_result or {}).get("success"):
                log.error(f"  [갭SL 매도실패] {name} — 수동 확인 필요")
                remaining.append(p)
            else:
                record_trade_history(p, cur, reason)
                sl_hit.append({**p, "cur": cur, "pnl_pct": pnl_pct, "sl_reason": reason, "order": order_result})
                log.info(f"  🔴 갭SL [{name}] {cur:,}원 ({pnl_pct:+.1f}%) — {reason}")
        else:
            log.info(f"  🔵 [{name}] {cur:,}원 ({pnl_pct:+.1f}%) — SL {effective_sl:,}원 유지")
            remaining.append(p)

        time.sleep(0.15)

    save_positions(remaining)

    if sl_hit:
        ts  = now.strftime("%m/%d %H:%M")
        msg = f"🌅 *갭오픈 SL 체크* ({ts})\n\n"
        for h in sl_hit:
            tag  = " 〔하드스탑〕" if h.get("sl_reason") == "HARD_SL" else (" 〔트레일〕" if h.get("sl_reason") == "TRAIL_SL" else "")
            otag = order_result_tag(h.get("order"), do_trade)
            msg += (
                f"🔴 *갭손실 조기차단{tag}* — {_esc(h['name'])}\n"
                f"  진입 {h['entry']:,}원 → 갭오픈 *{h['cur']:,}원* ({h['pnl_pct']:+.1f}%)\n"
                f"  {otag}\n\n"
            )
        msg += f"잔여 {len(remaining)}개 포지션 계속 추적"
        send_telegram(msg)
        log.info(f"  갭SL 차단: {len(sl_hit)}개")
