"""
스윙 눌림목 검색기 v5.0 — 모듈화 리팩토링
  scanner/ 패키지 기반으로 역할별 분리.
  이 파일은 스케줄러 설정 + 메인 루프만 담당.
"""
import os
import signal
import threading

import holidays
import schedule

from scanner.logger import log, setup_logger
from scanner.config import (
    STRATEGY, KIS_ACCOUNT_NO, TRADE_AMOUNT_PER_STOCK,
    _KIS_MODE, _AUTO_TRADE_INIT, TELEGRAM_CHAT_IDS,
)
from scanner import state
from scanner.notify import send_telegram
from scanner.calendar import KST, is_market_closed
from scanner.kis import sync_kis_holdings

from scanner.job_heartbeat   import job_heartbeat
from scanner.job_monitor     import job_monitor_positions, job_morning_sl_check
from scanner.job_screener    import job_first_screen, job_second_screen
from scanner.job_preload     import job_preload_kis_token
from scanner.telegram_poll   import telegram_polling_loop

from datetime import datetime


def _safe_run(fn, label: str) -> None:
    try:
        fn()
    except Exception as e:
        err = f"[ERROR] {label} 예외: {e}"
        log.error(err)
        send_telegram(f"🚨 *{label} 오류 — 즉시 확인 필요*\n```{err[:300]}```")


def _check_dashboard_flags() -> None:
    _BASE = os.path.dirname(os.path.abspath(__file__))
    for flag, new_state, label in [
        (os.path.join(_BASE, "_pause.flag"),  True,  "⏸ 신호 정지 (대시보드 명령)"),
        (os.path.join(_BASE, "_resume.flag"), False, "▶️ 신호 재개 (대시보드 명령)"),
    ]:
        if os.path.exists(flag):
            try:
                os.remove(flag)
            except OSError:
                pass
            with state._signals_lock:
                state._pause_signals = new_state
            log.info(f"[대시보드] {label}")
            send_telegram(f"{'⏸' if new_state else '▶️'} *{label}*")


def _handle_shutdown(signum, frame) -> None:
    log.info(f"[shutdown] 신호 수신 ({signum}) — 안전 종료 중...")
    state._shutdown_event.set()


def send_startup_message() -> None:
    now          = datetime.now(KST)
    kis_mode_str = "🔴 실전투자" if _KIS_MODE == "real" else "🟡 모의투자"
    mf_str       = "활성화" if STRATEGY["use_market_filter"] else "비활성화"
    at_str       = (f"🤖 자동매매 ON (종목당 {TRADE_AMOUNT_PER_STOCK:,}원)"
                    if state._auto_trade_enabled else "📋 자동매매 OFF (수동 모드)")
    acct_str     = f"계좌: {KIS_ACCOUNT_NO}" if KIS_ACCOUNT_NO else "⚠️ KIS_ACCOUNT_NO 미설정"
    send_telegram(
        f"✅ *스윙 눌림목 검색기 v5.0 시작* (채팅방 {len(TELEGRAM_CHAT_IDS)}개)\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🕐 시작 시각: {now.strftime('%Y-%m-%d %H:%M')}\n"
        f"🔑 KIS 모드: {kis_mode_str} | {acct_str}\n"
        f"{at_str}\n"
        f"📊 시장 필터(KOSPI MA20+기울기): {mf_str}\n"
        f"🎯 TP {int(STRATEGY['tp_pct']*100)}% | SL bo시가×{STRATEGY['sl_buffer']} | "
        f"트레일링 -{int(STRATEGY['trail_pct']*100)}%(+{int(STRATEGY.get('trail_activate_pct',0)*100)}% 활성) | "
        f"하드스탑 -{int(STRATEGY.get('hard_stop_pct',0)*100)}%\n"
        f"🔍 기준봉 {int(STRATEGY['bo_body_pct']*100)}%↑ / {STRATEGY['bo_vol_ratio']}x↑ | "
        f"RSI≥{STRATEGY['rsi_min']} | 눌림볼 ≤{STRATEGY['pullback_vol']}x\n"
        f"🏆 신호점수 최소 {STRATEGY.get('min_signal_score', 0)}점 | 체결강도≥{STRATEGY['min_buy_pressure']}\n"
        f"📈 드리프트 감지: {STRATEGY['drift_weeks']}주 연속 승률 "
        f"{int(STRATEGY['drift_winrate_threshold']*100)}% 미달 시 경고\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⏰ 09:00 → Heartbeat + 만료 포지션 정리 (월: 주간 리포트)\n"
        f"⏰ 09:10 → 갭오픈 SL 체크\n"
        f"⏰ 10:00 / 11:30 / 13:00 → 장중 TP/SL 모니터링\n"
        f"⏰ 14:30 → 1차 스크리닝\n"
        f"⏰ 14:50 → KIS 토큰 선발급\n"
        f"⏰ 15:20 → 2차 재검증 + 발송 + {'자동매수' if state._auto_trade_enabled else '수동매수'}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"/autotrade on·off 로 자동매매 토글 가능"
    )


def run_catchup() -> None:
    now = datetime.now(KST)
    d = now.date()

    if d.weekday() >= 5 or d in holidays.KR(years=d.year):
        log.info("  휴장일 — catch-up 없음\n")
        return

    hm = now.hour * 60 + now.minute
    T1 = 14 * 60 + 30
    T2 = 15 * 60 + 20
    T3 = 15 * 60 + 30

    if hm < T1:
        log.info("  14:30 이전 시작 — 스케줄 대기\n")
    elif T1 <= hm < T2:
        log.info("  14:30~15:20 사이 시작 → 1차 즉시 실행\n")
        send_telegram("⚡ 늦은 시작 감지 → 1차 스크리닝 즉시 시작합니다")
        job_first_screen()
    elif T2 <= hm < T3:
        log.info("  15:20~15:30 사이 시작 → 1차 + 2차 즉시 순차 실행\n")
        send_telegram("⚡ 늦은 시작 감지 → 1차 + 2차 즉시 순차 실행합니다")
        job_first_screen()
        job_second_screen()
    else:
        log.info("  15:30 이후 — 당일 skip\n")
        send_telegram(
            f"⏭ *당일 스크리닝 skip*\n"
            f"시작 시각 {now.strftime('%H:%M')} — 장 마감 이후\n"
            f"내일 14:30부터 정상 스케줄 실행"
        )


if __name__ == "__main__":
    setup_logger()

    signal.signal(signal.SIGINT, _handle_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_shutdown)

    if not KIS_ACCOUNT_NO and state._auto_trade_enabled:
        log.warning("⚠️  AUTO_TRADE=true 이지만 KIS_ACCOUNT_NO 미설정 — 자동매매 비활성화")
        with state._auto_trade_lock:
            state._auto_trade_enabled = False
        send_telegram(
            "⚠️ *자동매매 비활성화*\n"
            "AUTO_TRADE=true 로 설정되었으나 KIS_ACCOUNT_NO가 비어 있습니다.\n"
            ".env 파일에 계좌번호를 입력 후 서비스를 재시작하세요.\n"
            "예: KIS_ACCOUNT_NO=50071234-01"
        )

    schedule.every().day.at("09:00", "Asia/Seoul").do(lambda: _safe_run(job_heartbeat,           "Heartbeat"))
    schedule.every().day.at("09:10", "Asia/Seoul").do(lambda: _safe_run(job_morning_sl_check,  "갭오픈 SL 체크"))
    schedule.every().day.at("10:00", "Asia/Seoul").do(lambda: _safe_run(job_monitor_positions,  "장중 모니터링(10시)"))
    schedule.every().day.at("11:30", "Asia/Seoul").do(lambda: _safe_run(job_monitor_positions,  "장중 모니터링(11:30)"))
    schedule.every().day.at("13:00", "Asia/Seoul").do(lambda: _safe_run(job_monitor_positions,  "장중 모니터링(13시)"))
    schedule.every().day.at("14:30", "Asia/Seoul").do(lambda: _safe_run(job_first_screen,        "1차 스크리닝"))
    schedule.every().day.at("14:50", "Asia/Seoul").do(lambda: _safe_run(job_preload_kis_token,  "KIS 토큰 선발급"))
    schedule.every().day.at("15:20", "Asia/Seoul").do(lambda: _safe_run(job_second_screen,      "2차 검증"))
    schedule.every().day.at("15:25", "Asia/Seoul").do(lambda: _safe_run(job_monitor_positions,  "장마감 모니터링(15:25)"))

    log.info("\n✅ 스윙 눌림목 검색기 v5.0 시작 (모듈화)")
    log.info(f"  🔑 KIS 모드: {'실전투자' if _KIS_MODE == 'real' else '모의투자'}")
    log.info(f"  🤖 자동매매: {'ON (종목당 ' + str(TRADE_AMOUNT_PER_STOCK) + '원)' if state._auto_trade_enabled else 'OFF'}")
    log.info("  종료: Ctrl+C\n")

    threading.Thread(
        target=telegram_polling_loop, daemon=True, name="tg-polling"
    ).start()

    send_startup_message()

    if KIS_ACCOUNT_NO:
        log.info("[KIS 동기화] 실제 보유 종목 조회 중...")
        n = sync_kis_holdings()
        if n == 0:
            log.info("[KIS 동기화] 신규 추가 없음")

    run_catchup()

    while not state._shutdown_event.is_set():
        schedule.run_pending()
        _check_dashboard_flags()
        state._shutdown_event.wait(timeout=1)

    log.info("[shutdown] 봇 종료 완료")
