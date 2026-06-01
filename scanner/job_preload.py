"""14:50 KIS 토큰 캐시 선발급 — 15:20 주문 지연 방지."""
from datetime import datetime

from scanner import state
from scanner.kis import get_kis_access_token
from scanner.calendar import KST, is_market_closed
from scanner.logger import log


def job_preload_kis_token() -> None:
    if is_market_closed(datetime.now(KST)):
        return
    with state._auto_trade_lock:
        do_trade = state._auto_trade_enabled
    if not do_trade:
        return
    token = get_kis_access_token()
    if token:
        log.info("[14:50] KIS 토큰 선발급 완료 — 15:20 주문 지연 방지")
    else:
        log.warning("[14:50] KIS 토큰 선발급 실패 — 15:20 재시도 예정")
