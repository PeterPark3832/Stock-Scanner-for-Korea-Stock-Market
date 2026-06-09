"""
전략 파라미터·환경변수·상수 — 다른 모듈이 변경해선 안 됨.
"""
import os
from dotenv import load_dotenv

load_dotenv()

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project root

POSITIONS_FILE     = os.path.join(_BASE_DIR, "positions.json")
TRADE_HISTORY_FILE = os.path.join(_BASE_DIR, "trade_history.csv")
SCREENING_LOG_FILE = os.path.join(_BASE_DIR, "screening_log.json")

TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN")
_raw_ids          = os.getenv("TELEGRAM_CHAT_IDS") or os.getenv("TELEGRAM_CHAT_ID") or ""
TELEGRAM_CHAT_IDS = [cid.strip() for cid in _raw_ids.split(",") if cid.strip()]
_raw_topic        = os.getenv("TELEGRAM_TOPIC_ID", "").split("#")[0].strip()
TELEGRAM_TOPIC_ID = int(_raw_topic) if _raw_topic.isdigit() else None

KIS_APP_KEY    = os.getenv("KIS_APP_KEY")
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET")
KIS_ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO", "")
TRADE_AMOUNT_PER_STOCK = int(os.getenv("TRADE_AMOUNT_PER_STOCK", "1000000"))
_AUTO_TRADE_INIT = os.getenv("AUTO_TRADE", "false").lower() == "true"

_KIS_MODE = os.getenv("KIS_MODE", "paper").lower()
KIS_BASE_URL = (
    "https://openapi.koreainvestment.com:9443"
    if _KIS_MODE == "real"
    else "https://openapivts.koreainvestment.com:29443"
)

_TR_BUY  = "TTTC0802U" if _KIS_MODE == "real" else "VTTC0802U"
_TR_SELL = "TTTC0801U" if _KIS_MODE == "real" else "VTTC0801U"
_TR_BAL  = "TTTC8908R" if _KIS_MODE == "real" else "VTTC8908R"

STRATEGY: dict = {
    # ── 기준봉 ────────────────────────────────────────────────────
    "bo_body_pct":               0.09,
    "bo_vol_ratio":              3.0,
    "bo_lookback":               5,     # 3→5: 기준봉 탐색 기간 확장 (신호 빈도 개선)
    # ── 눌림목 ────────────────────────────────────────────────────
    "pullback_vol":              0.8,   # 0.7→0.8: 거래량 감소 기준 완화
    "pullback_shape":            0.30,  # 0.20→0.30: 도지 기준 완화 (캔들 10~15%만 통과 → 병목)
    # ── TP/SL ─────────────────────────────────────────────────────
    "tp_pct":                    0.07,   # 백테스트 검증값 (max TP +7.46%)
    "tp1_pct":                   0.00,   # 비활성화: TP=7% 에서 분할 익절 시 PF 하락
    "sl_buffer":                 0.99,
    "sl_limit":                  0.04,   # 진입가 대비 SL 거리 상한 (10%→4%)
    "max_hold_days":             7,
    # ── 필터 ──────────────────────────────────────────────────────
    "use_ma60_filter":           True,
    "min_marcap":                50_000_000_000,
    "use_market_filter":         True,
    "min_turnover":              1_000_000_000,
    "rsi_period":                14,
    "rsi_min":                   45,
    "use_price_range":           True,
    "price_range_pct":           0.55,  # 0.70→0.55: 상위 30% → 상위 45% (가장 큰 병목 해소)
    # ── 트레일링 ──────────────────────────────────────────────────
    "trail_pct":                 0.05,
    "trail_activate_pct":        0.03,   # +3% 수익 이후 트레일링 개시
    # ── 리스크 관리 ───────────────────────────────────────────────
    "max_sector_count":          2,
    "drift_winrate_threshold":   0.35,
    "drift_weeks":               3,
    "min_buy_pressure":          100,   # 110→100: 체결강도 기준 원복 (눌림목과 고강도 매수세 상충)
    "max_positions":             5,
    "min_signal_score":          40,
    "hard_stop_pct":             0.05,   # 7%→5% (09:10 갭SL 체크 병행)
}
