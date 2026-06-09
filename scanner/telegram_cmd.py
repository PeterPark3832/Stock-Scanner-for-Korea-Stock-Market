"""텔레그램 커맨드 라우터 — /positions /report /pause /resume /autotrade /help."""
import time
from datetime import datetime

from scanner.config import TRADE_AMOUNT_PER_STOCK
from scanner import state
from scanner.notify import send_telegram, _esc
from scanner.positions import load_positions
from scanner.history import load_trade_history
from scanner.performance import calc_performance_stats, format_weekly_report
from scanner.kis import get_current_price, _parse_account
from scanner.calendar import KST
from scanner.logger import log


def _cmd_positions() -> None:
    positions = load_positions()
    now = datetime.now(KST)

    if not positions:
        send_telegram(f"📋 *보유 포지션 없음* ({now.strftime('%m/%d %H:%M')})")
        return

    msg  = f"📋 *보유 포지션 현황* ({now.strftime('%m/%d %H:%M')})\n━━━━━━━━━━━━━━━━━━\n"
    pnls = []

    for p in positions:
        entry = p.get("entry", 0)
        tp    = p.get("tp", 0)
        sl    = p.get("sl", 0)
        qty   = p.get("quantity", 0)
        live  = get_current_price(p["ticker"])

        if live:
            cur     = live["current"]
            pnl_pct = (cur - entry) / entry * 100 if entry else 0
            pnls.append(pnl_pct)
            if   cur >= tp:    status = f"✅ TP 근접 ({pnl_pct:+.1f}%)"
            elif cur <= sl:    status = f"🔴 SL 근접 ({pnl_pct:+.1f}%)"
            elif pnl_pct >= 0: status = f"📈 {pnl_pct:+.1f}%"
            else:              status = f"📉 {pnl_pct:+.1f}%"
            score_str = f" [{p['signal_score']}점]" if p.get("signal_score") is not None else ""
            qty_str   = f" | {qty}주" if qty > 0 else ""
            auto_str  = " 🤖" if p.get("auto_traded") else ""
            msg += (
                f"• *{p['name']}* ({p['ticker']}){score_str}{qty_str}{auto_str}\n"
                f"  진입 {entry:,} → 현재 *{cur:,}* | {status}\n"
                f"  TP {tp:,} / SL {sl:,}\n"
            )
        else:
            msg += f"• *{p['name']}* ({p['ticker']})\n  진입 {entry:,} | ⚠️ 시세 조회 실패\n"
        time.sleep(0.15)

    if pnls:
        avg = sum(pnls) / len(pnls)
        msg += f"━━━━━━━━━━━━━━━━━━\n{'📈' if avg >= 0 else '📉'} 평균 PnL {avg:+.1f}% | {len(positions)}종목"
    send_telegram(msg)


def _cmd_report() -> None:
    df = load_trade_history()
    if df.empty:
        send_telegram("📊 *누적 성과 리포트*\n거래 이력 없음 (trade\\_history.csv 비어있음)")
        return

    stats = calc_performance_stats(df)
    msg   = format_weekly_report(stats, f"전체 누적 {stats['total']}건")

    recent = df.tail(5)
    lines  = []
    for _, row in recent.iterrows():
        emoji = "✅" if row["pnl_pct"] > 0 else "🔴"
        auto  = " 🤖" if str(row.get("auto_traded", "")).lower() == "true" else ""
        lines.append(f"  {emoji} {row['name']} | {row['exit_reason']} | {row['pnl_pct']:+.2f}%{auto}")
    msg += "\n━━━━━━━━━━━━━━━━━━\n📌 *최근 5건*\n" + "\n".join(lines)
    send_telegram(msg)


def _cmd_stats() -> None:
    with state._screen_stats_lock:
        s = dict(state._last_screen_stats)
    if not s:
        send_telegram("📊 *스크리닝 통계 없음*\n14:30 스크리닝 이후 조회 가능합니다.")
        return
    fc = s.get("filter_counts", {})
    top = sorted(fc.items(), key=lambda x: x[1], reverse=True)
    lines = [f"  • {k}: {v:,}건" for k, v in top if v > 0]
    total_filtered = sum(fc.values())
    send_telegram(
        f"📊 *스크리닝 통계* ({s['date']} {s['time']})\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"전체 종목: {s['total']:,}개\n"
        f"후보 통과: {s['candidates']}개\n"
        f"탈락 합계: {total_filtered:,}건\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"*필터별 탈락 현황*\n" + "\n".join(lines)
    )


def handle_command(text: str) -> None:
    parts = text.strip().lower().split()
    cmd   = parts[0]

    if cmd == "/positions":
        _cmd_positions()
    elif cmd == "/stats":
        _cmd_stats()
    elif cmd == "/report":
        _cmd_report()
    elif cmd == "/pause":
        with state._signals_lock:
            state._pause_signals = True
        send_telegram("⏸ *신호 발송 일시정지*\n신규 신호를 억제합니다.\n재개: /resume")
    elif cmd == "/resume":
        with state._signals_lock:
            state._pause_signals = False
        send_telegram("▶️ *신호 발송 재개*\n정상 스캔 신호를 발송합니다.")
    elif cmd == "/autotrade":
        arg = parts[1] if len(parts) > 1 else ""
        if arg == "on":
            with state._auto_trade_lock:
                state._auto_trade_enabled = True
            cano, _ = _parse_account()
            acct_ok = "✅" if cano else "⚠️ 계좌번호 미설정"
            send_telegram(
                f"🤖 *자동매매 ON*\n"
                f"계좌: {acct_ok}\n"
                f"종목당 예산: {TRADE_AMOUNT_PER_STOCK:,}원\n"
                f"다음 15:20 스캔부터 자동 주문 실행됩니다"
            )
        elif arg == "off":
            with state._auto_trade_lock:
                state._auto_trade_enabled = False
            send_telegram("📋 *자동매매 OFF*\n신호는 알림만 발송, 주문은 수동으로 직접 처리하세요")
        else:
            with state._auto_trade_lock:
                status = state._auto_trade_enabled
            send_telegram(
                f"🤖 자동매매 현재 상태: {'*ON*' if status else '*OFF*'}\n"
                f"변경: /autotrade on 또는 /autotrade off"
            )
    elif cmd in ("/help", "/start"):
        send_telegram(
            "📋 *사용 가능한 커맨드*\n\n"
            "/positions — 보유 포지션 실시간 PnL\n"
            "/stats — 최근 스크리닝 필터 통계\n"
            "/report — 누적 성과 리포트\n"
            "/pause — 신규 신호 발송 정지\n"
            "/resume — 신호 발송 재개\n"
            "/autotrade on|off — 자동매매 토글\n"
            "/help — 이 메시지"
        )
    else:
        send_telegram(f"⚠️ 알 수 없는 커맨드: `{cmd}`\n/help 로 목록 확인")
