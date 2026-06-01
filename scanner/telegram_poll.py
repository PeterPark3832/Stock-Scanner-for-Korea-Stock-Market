"""텔레그램 Long Polling 스레드."""
import requests
from requests.exceptions import ReadTimeout, ConnectionError as RequestsConnectionError

from scanner.config import TELEGRAM_TOKEN, TELEGRAM_CHAT_IDS
from scanner import state
from scanner.telegram_cmd import handle_command
from scanner.logger import log


def telegram_polling_loop() -> None:
    if not TELEGRAM_TOKEN:
        log.info("[polling] 텔레그램 토큰 없음 — 폴링 비활성화")
        return

    allowed_ids = {str(c) for c in TELEGRAM_CHAT_IDS}
    log.info("[polling] 텔레그램 커맨드 폴링 시작")

    while not state._shutdown_event.is_set():
        try:
            with state._offset_lock:
                offset = state._tg_update_offset

            res = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={
                    "offset":          offset,
                    "timeout":         30,
                    "allowed_updates": ["message"],
                },
                timeout=35,
            )

            if res.status_code != 200:
                state._shutdown_event.wait(timeout=5)
                continue

            for update in res.json().get("result", []):
                with state._offset_lock:
                    state._tg_update_offset = update["update_id"] + 1
                msg_obj = update.get("message", {})
                chat_id = str(msg_obj.get("chat", {}).get("id", ""))
                text    = msg_obj.get("text", "")

                if chat_id not in allowed_ids or not text.startswith("/"):
                    continue

                log.info(f"[polling] 커맨드: {text!r} (chat={chat_id})")
                handle_command(text)

        except ReadTimeout:
            continue
        except RequestsConnectionError as e:
            log.warning(f"[polling] 네트워크 연결 오류: {e}")
            state._shutdown_event.wait(timeout=5)
        except Exception as e:
            log.warning(f"[polling] 예외: {e}")
            state._shutdown_event.wait(timeout=5)
