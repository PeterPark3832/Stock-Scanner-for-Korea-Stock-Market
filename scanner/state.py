"""
전역 공유 상태 — 스레드 안전 Lock·Flag·캐시.
모든 모듈이 이 모듈의 속성을 직접 읽고/씁니다 (global 키워드 불필요).
"""
import threading
from filelock import FileLock
from scanner.config import POSITIONS_FILE, TRADE_HISTORY_FILE, _AUTO_TRADE_INIT

# 1차 스크리닝 결과 캐시 (14:30 write / 15:20 read)
_first_screen_cache: list[dict] = []
_cache_lock = threading.Lock()

# KIS OAuth 토큰 캐시
_kis_token_cache: dict = {"token": None, "expires_at": 0}

# 신호 발송 일시정지 플래그
_pause_signals: bool = False
_signals_lock  = threading.Lock()

# 텔레그램 polling offset
_tg_update_offset: int = 0
_offset_lock = threading.Lock()

# 포지션/이력 파일 잠금
_file_lock       = threading.Lock()   # (레거시 — 직접 사용 안 함)
_POSITIONS_FLOCK = FileLock(POSITIONS_FILE     + ".lock", timeout=5)
_HISTORY_FLOCK   = FileLock(TRADE_HISTORY_FILE + ".lock", timeout=5)

# Graceful shutdown
_shutdown_event = threading.Event()

# 자동매매 런타임 플래그
_auto_trade_enabled: bool = _AUTO_TRADE_INIT
_auto_trade_lock = threading.Lock()

# 최근 스크리닝 통계 캐시 (14:30 write / /stats 명령 read)
_last_screen_stats: dict = {}
_screen_stats_lock = threading.Lock()
