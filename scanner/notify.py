"""텔레그램 알림 전송 + 메시지 포맷 유틸리티."""
import time
import requests
from scanner.config import TELEGRAM_TOKEN, TELEGRAM_CHAT_IDS, TELEGRAM_TOPIC_ID
from scanner.logger import log


def _esc(text: str) -> str:
    """Markdown v1 특수문자 이스케이프."""
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


def send_telegram(text: str, topic_id: int | None = None) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_IDS:
        log.warning("[WARN] 텔레그램 설정 없음 — .env 확인")
        return
    _topic_id = topic_id or TELEGRAM_TOPIC_ID
    for chat_id in TELEGRAM_CHAT_IDS:
        try:
            payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
            if _topic_id:
                payload["message_thread_id"] = _topic_id
            res = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data=payload, timeout=10,
            )
            if res.status_code != 200:
                log.error(f"텔레그램 실패 (chat_id={chat_id}): {res.text}")
        except Exception as e:
            log.error(f"텔레그램 예외 (chat_id={chat_id}): {e}")
        time.sleep(0.1)


def order_result_tag(order: dict | None, do_trade: bool) -> str:
    """주문 결과를 텔레그램 메시지용 한 줄 태그로 변환."""
    if not do_trade:
        return "  ⚠️ 자동매매 OFF — 수동 주문 필요"
    if order is None:
        return "  ⚠️ 보유수량 0 — 수동 주문 필요"
    if order.get("success"):
        return f"  🤖 자동매도 완료 (주문번호: {order['order_no']})"
    return f"  🚨 자동매도 실패: {order.get('error', '?')} — 수동 확인 필요"
