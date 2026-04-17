"""
주식 검색기 v4.5 (3~5일 스윙 전용)
────────────────────────────────────────
v4.4 → v4.5 변경사항 (운영 안정성 강화):
  [개선 1]   logging 모듈 도입: print() → log.info/warning/error
             RotatingFileHandler (10MB × 5개 순환) → scanner.log
  [개선 2]   스레드 안전성: _pause_signals / _tg_update_offset 에
             threading.Lock() 적용 (폴링 스레드 ↔ 메인 스레드 경합 방지)
  [개선 3]   is_market_closed(): 장 시간(09:00~15:30 KST) 외 시간도 True 반환
             → 장 마감 후 잔여 스케줄 job 불필요 실행 방지
  [개선 4]   fdr_data_reader(): 지수 백오프 (1s → 2s → 4s)
             네트워크 일시 장애 시 재시도 성공률 향상
  [개선 5]   Graceful shutdown: SIGINT/SIGTERM 수신 시
             스케줄 루프 정상 종료 (파일 I/O 도중 강제 종료 방지)
  [개선 6]   _file_lock: positions.json 읽기/쓰기에 Lock 적용
             텔레그램 데몬 스레드와 메인 스레드 동시 접근 시 JSONDecodeError 방지
  [개선 7]   FDR 호출 간격 0.05s → 0.1s (IP 차단 위험 감소, 50분 내 여유 충분)
  [개선 8]   텔레그램 Long Polling: ReadTimeout·ConnectionError 예외 분리
             35초 타임아웃(정상 동작)이 로그를 오염시키는 문제 방지
  [개선 9]   FDR 데이터 .copy() 적용 (SettingWithCopyWarning 방지)
────────────────────────────────────────
v4.4 변경사항 (Phase 7: 텔레그램 양방향 통신):
  [신규 1]   _telegram_polling_loop(): 데몬 스레드로 커맨드 수신 (long polling)
             TELEGRAM_CHAT_IDS 등록 채팅방만 처리 (보안)
  [신규 2]   /positions — 보유 포지션 전종목 실시간 PnL 조회
  [신규 3]   /report   — trade_history 기반 누적 성과 + 최근 5건 on-demand
  [신규 4]   /pause·/resume — 신규 신호 발송 토글 (_pause_signals 플래그)
  [신규 5]   /help·/start  — 커맨드 목록 안내
────────────────────────────────────────
v4.4 변경사항 (Phase 6: 운영 안전망):
  [신규 1]   _safe_run() 래퍼: 모든 스케줄 job을 감싸는 최후 방어선
             미처리 예외 발생 시 텔레그램 즉시 알림 (job_heartbeat 등 조용한 크래시 방지)
  [신규 2]   STRATEGY["max_positions"]=5: 동시 보유 종목 수 상한선
             초과 시 signal_score 높은 종목 우선 추가, 나머지 텔레그램 알림
  [신규 3]   positions.json에 signal_score, bo_lookback, pullback_depth 저장
             Phase 4/5에서 계산된 값이 포지션 파일에도 유지됨
────────────────────────────────────────
v4.4 변경사항 (Phase 5: 체결강도 필터 + 눌림 깊이 기록):
  [신규 1]   get_current_price(): buy_pressure(체결강도, cttr) 파싱 추가
             STRATEGY["min_buy_pressure"]=100 (매수/매도 균형점, 이하 탈락)
  [신규 2]   job_second_screen: ok_buy_pressure 필터 추가 (매도 우위 종목 제거)
             pullback_depth = (bo_high - 현재가) / bo_high * 100 계산·저장
  [신규 3]   텔레그램 알림에 체결강도·눌림깊이·bo_lookback 표시
  [신규 4]   trade_history.csv에 pullback_depth 컬럼 추가
────────────────────────────────────────
v4.4 변경사항 (Phase 4: 신호 강도 점수화):
  [신규 1]   calc_signal_score(): 눌림목 신호를 0~100점으로 정량화
             BO 강도(30) + 눌림 품질(40) + 추세 품질(30)
  [신규 2]   2차 검증 후보를 signal_score 내림차순 정렬
             텔레그램 알림에 점수 바 표시 (████████░░ 82점)
  [신규 3]   trade_history.csv에 signal_score, bo_lookback 컬럼 추가
             데이터 축적 후 점수대별 승률 분석 가능
────────────────────────────────────────
v4.3 → v4.4 변경사항 (Phase 3: 백테스트 피드백 루프):
  [신규 1]   주간 성과 리포트: 매주 월요일 09:00 Heartbeat 후 자동 발송
             전주 청산 거래 기준 — 승률, 평균PnL, Sharpe, 청산사유 분류
  [신규 2]   전략 드리프트 감지: 최근 N주 연속 승률 threshold 미달 시 경고
             STRATEGY["drift_weeks"]=3, STRATEGY["drift_winrate_threshold"]=0.40
  [신규 3]   성과 집계 함수 분리 (calc_performance_stats / format_weekly_report)
             데이터와 포맷을 분리해 향후 다른 기간/필터에도 재사용 가능
────────────────────────────────────────
v4.2 → v4.3 변경사항 (Phase 2: 포지션 추적 고도화):
  [신규 1]   장중 TP/SL 실시간 감지 (10:00 / 13:00)
  [신규 2]   트레일링 스탑 (HWM 대비 -3%)
  [신규 3]   매매 이력 자동 기록 (trade_history.csv)
  [신규 4]   섹터 집중도 경고
────────────────────────────────────────
v4.1 → v4.2 변경사항 (Phase 1: 신호 품질 향상):
  [신규 1]   공휴일 캘린더, [신규 2] KOSPI MA20 시장 게이트
  [신규 3]   거래대금 필터, [신규 4] RSI 필터, [신규 5] 가격위치 필터
────────────────────────────────────────
"""

import csv
import json
import logging
import os
import signal
import time
import threading
import requests
from requests.exceptions import ReadTimeout, ConnectionError as RequestsConnectionError
import schedule
import holidays
import pandas as pd
import FinanceDataReader as fdr
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

KST = ZoneInfo("Asia/Seoul")

# ==========================================
# 로거 설정 (파일 순환 + 콘솔 동시 출력)
# ==========================================
def _setup_logger() -> logging.Logger:
    logger = logging.getLogger("scanner")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # 콘솔 핸들러
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    # 파일 핸들러 (10MB × 5개 순환)
    fh = RotatingFileHandler(
        "scanner.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger


log = _setup_logger()

# ==========================================
# 파일 경로 상수
# ==========================================
POSITIONS_FILE     = "positions.json"
TRADE_HISTORY_FILE = "trade_history.csv"

# ==========================================
# 공휴일 캘린더
# ==========================================
def is_market_closed(dt: datetime) -> bool:
    """
    다음 중 하나면 True:
    - 주말 (토·일)
    - 한국 법정 공휴일
    - 장 시간 외 (datetime 객체일 때만 검사, 09:00~15:30 KST)
    """
    d = dt.date() if hasattr(dt, "date") else dt
    if d.weekday() >= 5:
        return True
    if d in holidays.KR(years=d.year):
        return True
    # datetime 객체일 때만 장 시간 체크
    if hasattr(dt, "hour"):
        h, m = dt.hour, dt.minute
        if (h, m) < (9, 0) or (h, m) >= (15, 30):
            return True
    return False


# ==========================================
# 환경변수
# ==========================================
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN")
_raw_ids = os.getenv("TELEGRAM_CHAT_IDS") or os.getenv("TELEGRAM_CHAT_ID") or ""
TELEGRAM_CHAT_IDS = [cid.strip() for cid in _raw_ids.split(",") if cid.strip()]
_raw_topic = os.getenv("TELEGRAM_TOPIC_ID", "").split("#")[0].strip()
TELEGRAM_TOPIC_ID = int(_raw_topic) if _raw_topic.isdigit() else None
KIS_APP_KEY       = os.getenv("KIS_APP_KEY")
KIS_APP_SECRET    = os.getenv("KIS_APP_SECRET")

_KIS_MODE = os.getenv("KIS_MODE", "paper").lower()
KIS_BASE_URL = (
    "https://openapi.koreainvestment.com:9443"
    if _KIS_MODE == "real"
    else "https://openapivts.koreainvestment.com:29443"
)

# ==========================================
# 전략 파라미터
# ==========================================
STRATEGY = {
    # ── 기존 파라미터 ──────────────────────────────────────
    "bo_body_pct":       0.07,
    "bo_vol_ratio":      2.5,
    "bo_lookback":       3,
    "pullback_vol":      1.0,
    "pullback_shape":    0.25,
    "tp_pct":            0.10,
    "sl_buffer":         0.99,
    "sl_limit":          0.10,
    "max_hold_days":     7,
    "use_ma60_filter":   True,
    "min_marcap":        50_000_000_000,
    # ── Phase 1 파라미터 ────────────────────────────────────
    "use_market_filter": True,
    "min_turnover":      1_000_000_000,
    "rsi_period":        14,
    "rsi_min":           30,
    "use_price_range":   True,
    "price_range_pct":   0.70,
    # ── Phase 2 파라미터 ────────────────────────────────────
    "trail_pct":         0.03,
    "max_sector_count":  2,
    # ── Phase 3 신규 파라미터 ────────────────────────────────
    "drift_winrate_threshold": 0.40,   # 주간 승률 하한 (이하면 드리프트 후보)
    "drift_weeks":             3,      # 연속 N주 미달 시 경고 발송
    # ── Phase 5 파라미터 ────────────────────────────────────
    "min_buy_pressure":        100,    # 체결강도 하한 (100 = 매수/매도 균형점)
    # ── Phase 6 파라미터 ────────────────────────────────────
    "max_positions":           5,      # 동시 보유 최대 종목 수
}


_first_screen_cache: list[dict] = []
_kis_token_cache: dict = {"token": None, "expires_at": 0}

# 스레드 안전 전역 상태 (Lock으로 보호)
_pause_signals: bool = False        # /pause · /resume 으로 토글
_signals_lock = threading.Lock()

_tg_update_offset: int = 0          # getUpdates 중복 수신 방지용 offset
_offset_lock = threading.Lock()

_file_lock = threading.Lock()       # positions.json 동시 읽기/쓰기 방지

# Graceful shutdown 이벤트
_shutdown_event = threading.Event()


# ==========================================
# RSI 계산 (Wilder, TA-Lib 미사용)
# ==========================================
def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - 100 / (1 + rs)


# ==========================================
# [Phase 4] 신호 품질 점수 (0~100)
# ==========================================
def calc_signal_score(stock: dict) -> int:
    """
    눌림목 신호 품질을 0~100점으로 정량화
    ────────────────────────────────────────
    구성            항목            만점
    BO 강도  (30)   bo_body_pct     15   7%→0pt, 20%→15pt
                    bo_vol_ratio    15   2.5x→0pt, 8x→15pt
    눌림 품질(40)   bo_lookback     15   1일→15, 2일→10, 3일→5
                    vol_dry_ratio   15   0배→15pt, 1.0배→0pt
                    shape_ratio     10   0→10pt, 0.25→0pt
    추세 품질(30)   ma20_gap        15   0~5%=15pt, 벗어날수록 감소
                    price_pos       15   70%→0pt, 95%→15pt
    """
    score = 0.0

    # ── BO 강도 (30점) ──────────────────────────────────
    body_pct   = stock.get("bo_body_pct", 7.0)          # 단위: % (예: 8.5)
    score += min(max((body_pct - 7.0) / (20.0 - 7.0) * 15, 0.0), 15.0)

    vol_ratio  = stock.get("bo_vol_ratio", 2.5)
    score += min(max((vol_ratio - 2.5) / (8.0 - 2.5) * 15, 0.0), 15.0)

    # ── 눌림 품질 (40점) ────────────────────────────────
    score += {1: 15, 2: 10, 3: 5}.get(stock.get("bo_lookback", 3), 5)

    vol_dry    = stock.get("vol_dry_ratio", 1.0)         # 낮을수록 좋음
    score += min(max((1.0 - vol_dry) * 15, 0.0), 15.0)

    shape      = stock.get("shape_ratio", 0.25)          # 낮을수록 좋음
    score += min(max((0.25 - shape) / 0.25 * 10, 0.0), 10.0)

    # ── 추세 품질 (30점) ────────────────────────────────
    gap = stock.get("ma20_gap", 0.05)                    # (close-MA20)/MA20
    if 0.0 <= gap <= 0.05:
        score += 15.0
    elif gap < 0.0:                                      # MA20 하회 시 감점
        score += max(15.0 + gap / 0.03 * 15.0, 0.0)
    else:                                                # 5~15% 구간 선형 감소
        score += max(15.0 - (gap - 0.05) / 0.10 * 15.0, 0.0)

    pos = stock.get("price_pos", 0.70)                   # 150일 범위 내 위치
    score += min(max((pos - 0.70) / (0.95 - 0.70) * 15, 0.0), 15.0)

    return round(min(score, 100.0))


# ==========================================
# 텔레그램
# ==========================================
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


# ==========================================
# KIS OAuth 토큰 (캐시)
# ==========================================
def get_kis_access_token() -> str | None:
    now = time.time()
    if _kis_token_cache["token"] and now < _kis_token_cache["expires_at"] - 60:
        return _kis_token_cache["token"]
    try:
        res = requests.post(
            f"{KIS_BASE_URL}/oauth2/tokenP",
            json={"grant_type": "client_credentials",
                  "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET},
            timeout=10,
        )
        res.raise_for_status()
        data = res.json()
        _kis_token_cache["token"] = data["access_token"]
        _kis_token_cache["expires_at"] = now + int(data.get("expires_in", 86400))
        log.info(f"[KIS] 토큰 발급 완료 (유효: {int(data.get('expires_in', 86400) / 3600)}시간)")
        return _kis_token_cache["token"]
    except Exception as e:
        log.error(f"KIS 토큰 발급 실패: {e}")
        return None


# ==========================================
# KIS 현재가 조회
# ==========================================
def get_current_price(ticker: str) -> dict | None:
    token = get_kis_access_token()
    if not token:
        return None
    try:
        res = requests.get(
            f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": KIS_APP_KEY,
                "appsecret": KIS_APP_SECRET,
                "tr_id": "FHKST01010100",
            },
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
            timeout=10,
        )
        res.raise_for_status()
        data = res.json()
        if data.get("rt_cd") != "0":
            return None
        o = data["output"]
        return {
            "current":      int(o["stck_prpr"]),
            "volume":       int(o["acml_vol"]),
            "open":         int(o["stck_oprc"]),
            "high":         int(o["stck_hgpr"]),
            "low":          int(o["stck_lwpr"]),
            "buy_pressure": float(o.get("cttr", 100) or 100),  # 체결강도 (매수/매도 * 100)
        }
    except Exception as e:
        log.error(f"  {ticker} 시세 조회 예외: {e}")
        return None


# ==========================================
# FDR 데이터 로딩 (지수 백오프 재시도)
# ==========================================
def fdr_data_reader(ticker: str, start_date, retries: int = 3, delay: float = 1.0):
    """
    FDR DataReader 호출 — 실패 시 지수 백오프로 재시도
    delay 기본 1초: 1회 → 1s, 2회 → 2s, 3회 → 4s
    """
    for attempt in range(1, retries + 1):
        try:
            return fdr.DataReader(ticker, start_date)
        except Exception as e:
            if attempt < retries:
                wait = delay * (2 ** (attempt - 1))
                log.warning(f"  [RETRY {attempt}/{retries}] {ticker}: {e} (재시도 {wait:.0f}초 후)")
                time.sleep(wait)
            else:
                raise


# ==========================================
# 시장 컨디션 체크 (KOSPI MA20)
# ==========================================
def get_kospi_condition(start_date) -> tuple[bool, str]:
    try:
        kospi = fdr.DataReader("KS11", start_date)
        if kospi.empty or len(kospi) < 20:
            return True, "KOSPI 데이터 부족 — 필터 통과"
        ma20  = kospi["Close"].rolling(20).mean().iloc[-1]
        close = kospi["Close"].iloc[-1]
        above = close >= ma20
        status = f"KOSPI {close:,.0f} / MA20 {ma20:,.0f} ({'▲ 양호' if above else '▼ 약세'})"
        return above, status
    except Exception as e:
        log.warning(f"  KOSPI 조회 실패: {e} — 시장 필터 통과로 처리")
        return True, f"KOSPI 조회 실패: {e}"


# ==========================================
# 매매 이력 기록 (CSV 누적 append)
# ==========================================
def record_trade_history(p: dict, exit_price: int, exit_reason: str) -> None:
    """
    exit_reason: "TP" | "SL" | "TRAIL_SL" | "EXPIRE"
    trade_history.csv 에 한 행 추가 (append-only)
    """
    entry   = p.get("entry", 0)
    pnl_pct = round((exit_price - entry) / entry * 100, 2) if entry else 0.0
    row = {
        "ticker":        p["ticker"],
        "name":          p["name"],
        "sector":        p.get("sector", ""),
        "entry_date":    p.get("entry_date", ""),
        "exit_date":     datetime.now(KST).strftime("%Y-%m-%d"),
        "entry_price":   entry,
        "exit_price":    exit_price,
        "pnl_pct":       pnl_pct,
        "exit_reason":   exit_reason,
        "signal_score":   p.get("signal_score", ""),
        "bo_lookback":    p.get("bo_lookback", ""),
        "pullback_depth": p.get("pullback_depth", ""),
    }
    file_exists = os.path.exists(TRADE_HISTORY_FILE)
    try:
        with open(TRADE_HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(row.keys()))
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
        log.info(f"  [이력] {row['name']} ({exit_reason}) {pnl_pct:+.2f}% 기록 완료")
    except Exception as e:
        log.error(f"  이력 기록 실패 ({p['ticker']}): {e}")


# ==========================================
# [Phase 3] 성과 집계 함수군
# ==========================================
def load_trade_history() -> pd.DataFrame:
    """
    trade_history.csv 로드 → DataFrame 반환
    파일 없거나 비어있으면 빈 DataFrame 반환
    """
    if not os.path.exists(TRADE_HISTORY_FILE):
        return pd.DataFrame()
    try:
        df = pd.read_csv(TRADE_HISTORY_FILE, encoding="utf-8",
                         parse_dates=["exit_date", "entry_date"])
        return df if not df.empty else pd.DataFrame()
    except Exception as e:
        log.warning(f"  이력 파일 로드 실패: {e}")
        return pd.DataFrame()


def calc_performance_stats(df: pd.DataFrame) -> dict:
    """
    거래 DataFrame → 성과 통계 dict 반환
    ─────────────────────────────────────
    반환 키:
      total     : 총 거래 수
      wins      : 수익 거래 수 (pnl_pct > 0)
      win_rate  : 승률 (0.0~1.0)
      avg_pnl   : 평균 PnL (%)
      sharpe    : 거래별 Sharpe (mean/std, n<2 이면 0.0)
      by_reason : exit_reason 별 건수 dict
    """
    if df.empty:
        return {
            "total": 0, "wins": 0, "win_rate": 0.0,
            "avg_pnl": 0.0, "sharpe": 0.0, "by_reason": {},
        }
    total    = len(df)
    wins     = int((df["pnl_pct"] > 0).sum())
    win_rate = round(wins / total, 3) if total > 0 else 0.0
    avg_pnl  = round(float(df["pnl_pct"].mean()), 2)
    std_pnl  = float(df["pnl_pct"].std())
    sharpe   = round(avg_pnl / std_pnl, 2) if total >= 2 and std_pnl > 0 else 0.0
    by_reason = df["exit_reason"].value_counts().to_dict()
    return {
        "total": total, "wins": wins, "win_rate": win_rate,
        "avg_pnl": avg_pnl, "sharpe": sharpe, "by_reason": by_reason,
    }


def format_weekly_report(stats: dict, week_label: str) -> str:
    """성과 dict → 텔레그램용 주간 리포트 문자열"""
    if stats["total"] == 0:
        return (
            f"📊 *주간 성과 리포트* ({week_label})\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"해당 주 청산 거래 없음"
        )

    REASON_LABELS = {
        "TP":       "TP 익절",
        "SL":       "SL 손절",
        "TRAIL_SL": "트레일 손절",
        "EXPIRE":   "기간만료",
    }
    reason_parts = [
        f"{REASON_LABELS.get(r, r)} {c}건"
        for r, c in sorted(stats["by_reason"].items())
    ]

    win_rate_pct = round(stats["win_rate"] * 100, 1)
    win_emoji    = "🟢" if win_rate_pct >= 50 else "🔴"
    pnl_emoji    = "📈" if stats["avg_pnl"] >= 0 else "📉"

    return (
        f"📊 *주간 성과 리포트* ({week_label})\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{win_emoji} 승률: {win_rate_pct}%  ({stats['wins']}/{stats['total']}건)\n"
        f"{pnl_emoji} 평균 PnL: {stats['avg_pnl']:+.2f}%\n"
        f"📐 Sharpe: {stats['sharpe']:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"청산 사유: {' | '.join(reason_parts)}"
    )


def check_winrate_drift(df: pd.DataFrame) -> str | None:
    """
    전체 이력에서 최근 drift_weeks 주 연속으로 승률이
    drift_winrate_threshold 미만이면 경고 문자열 반환, 아니면 None
    ─────────────────────────────────────────────────────
    · ISO 주 기준 집계 (연도 포함, 연말 경계 안전)
    · 데이터 부족(< drift_weeks 주) → None (경고 억제)
    """
    threshold = STRATEGY["drift_winrate_threshold"]
    n_weeks   = STRATEGY["drift_weeks"]

    if df.empty:
        return None

    df = df.copy()
    iso = df["exit_date"].dt.isocalendar()
    df["year_week"] = iso.year.astype(int) * 100 + iso.week.astype(int)

    def _calc_wr(g):
        return (g > 0).sum() / len(g) if len(g) > 0 else 0.0

    weekly_wr = (
        df.groupby("year_week")["pnl_pct"]
        .apply(_calc_wr)
        .sort_index()
    )

    if len(weekly_wr) < n_weeks:
        return None  # 데이터 부족 — 조용히 통과

    recent = weekly_wr.iloc[-n_weeks:]
    if not (recent < threshold).all():
        return None  # 연속 미달 아님

    wr_strs   = " / ".join(f"{r*100:.0f}%" for r in recent)
    weeks_str = " / ".join(str(w) for w in recent.index)
    return (
        f"🚨 *전략 드리프트 경고*\n"
        f"최근 {n_weeks}주 연속 승률 {threshold*100:.0f}% 미달\n"
        f"주간 승률: {wr_strs}\n"
        f"({weeks_str})\n"
        f"STRATEGY 파라미터 재검토 권장"
    )


def _send_weekly_report(now: datetime) -> None:
    """
    매주 월요일 job_heartbeat 에서 호출
    ─────────────────────────────────────
    1) 전주(월~일) 청산 거래 성과 집계 → 텔레그램 발송
    2) 전체 이력으로 드리프트 감지 → 경고 있으면 별도 발송
    """
    df = load_trade_history()

    # ── 전주 범위 계산 ──────────────────────────────────
    # now = 이번 월요일 (09:00 Heartbeat)
    prev_monday = (now - timedelta(days=7)).date()
    prev_sunday = (now - timedelta(days=1)).date()
    week_label  = f"{prev_monday.strftime('%m/%d')}~{prev_sunday.strftime('%m/%d')}"

    if not df.empty:
        week_df = df[
            (df["exit_date"].dt.date >= prev_monday) &
            (df["exit_date"].dt.date <= prev_sunday)
        ]
    else:
        week_df = pd.DataFrame()

    # ── 주간 성과 리포트 ────────────────────────────────
    stats      = calc_performance_stats(week_df)
    report_msg = format_weekly_report(stats, week_label)
    send_telegram(report_msg)
    log.info(f"  [주간 리포트] {week_label}: {stats['total']}건 | 승률 {stats['win_rate']*100:.0f}%")

    # ── 드리프트 감지 (전체 이력 기준, 별도 메시지) ────────
    drift_msg = check_winrate_drift(df)
    if drift_msg:
        send_telegram(drift_msg)
        log.info("  [드리프트 경고] 발송 완료")


# ==========================================
# 포지션 파일 I/O
# ==========================================
def _count_weekdays(start: datetime, end: datetime) -> int:
    days = 0
    cur = start.date() if hasattr(start, "date") else start
    end = end.date() if hasattr(end, "date") else end
    while cur < end:
        if cur.weekday() < 5:
            days += 1
        cur += timedelta(days=1)
    return days

def load_positions() -> list[dict]:
    with _file_lock:
        if not os.path.exists(POSITIONS_FILE):
            return []
        try:
            with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.error(f"포지션 로드 실패 (파일 손상 의심): {e}")
            return []

def save_positions(positions: list[dict]) -> None:
    with _file_lock:
        try:
            with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(positions, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.error(f"포지션 저장 실패: {e}")

def add_positions(stocks: list[dict]) -> None:
    """
    신규 포착 종목을 포지션 파일에 추가
    - Phase 2: high_water_mark, sl_init, sector
    - Phase 4/5: signal_score, bo_lookback, pullback_depth
    - Phase 6: max_positions 한도 적용 (초과 시 고점수 우선, 텔레그램 알림)
    """
    existing = load_positions()
    existing_tickers = {p["ticker"] for p in existing}
    now_str  = datetime.now(KST).strftime("%Y-%m-%d")
    cap      = STRATEGY["max_positions"]
    slots    = cap - len(existing)

    candidates = [s for s in stocks if s["ticker"] not in existing_tickers]
    candidates.sort(key=lambda s: s.get("signal_score", 0), reverse=True)
    to_add  = candidates[:max(slots, 0)]
    skipped = candidates[max(slots, 0):]

    for s in to_add:
        entry = s["entry"]
        sl    = s["sl"]
        existing.append({
            "ticker":          s["ticker"],
            "name":            s["name"],
            "entry":           entry,
            "tp":              s["tp"],
            "sl":              sl,
            "sl_init":         sl,
            "high_water_mark": entry,
            "entry_date":      now_str,
            "sector":          s.get("sector", ""),
            "signal_score":    s.get("signal_score"),
            "bo_lookback":     s.get("bo_lookback"),
            "pullback_depth":  s.get("pullback_depth"),
        })

    save_positions(existing)
    log.info(f"  포지션 기록: {len(to_add)}개 추가 (누적 {len(existing)}개 / 최대 {cap}개)")

    if skipped:
        names = ", ".join(s["name"] for s in skipped)
        send_telegram(
            f"⛔ *포지션 한도({cap}개) 초과 — 미추가 종목*\n"
            f"{names}\n"
            f"현재 {len(existing)}개 보유 중 | 포지션 정리 후 재스캔 필요"
        )
        log.warning(f"  [SKIP] 한도 초과 {len(skipped)}개 미추가: {names}")

def check_expired_positions() -> tuple[list[dict], list[dict]]:
    positions = load_positions()
    today     = datetime.now(KST)
    expired, active = [], []
    for p in positions:
        try:
            entry_dt = datetime.strptime(p["entry_date"], "%Y-%m-%d")
            elapsed  = _count_weekdays(entry_dt, today)
            p["elapsed_days"] = elapsed
            if elapsed >= 5:
                expired.append(p)
            else:
                active.append(p)
        except Exception:
            active.append(p)
    return expired, active


# ==========================================
# 장중 포지션 모니터링 (Phase 2)
# ==========================================
def job_monitor_positions() -> None:
    """
    10:00 / 13:00 장중 실행
    - HWM 갱신 → 트레일링 SL 상향
    - TP/SL 도달 → 즉시 알림 + 이력 기록 + 포지션 제거
    """
    if is_market_closed(datetime.now(KST)):
        return

    now       = datetime.now(KST)
    positions = load_positions()
    if not positions:
        log.info(f"[{now.strftime('%H:%M')}] 모니터링: 추적 포지션 없음")
        return

    log.info(f"\n{'='*50}")
    log.info(f"[{now.strftime('%H:%M')}] 장중 포지션 모니터링 ({len(positions)}개)")
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

        trail_sl = int(hwm * (1 - STRATEGY["trail_pct"]))
        if trail_sl > sl:
            p["sl"] = trail_sl
            sl = trail_sl

        pnl_pct = (cur - entry) / entry * 100 if entry else 0

        if cur >= tp:
            record_trade_history(p, cur, "TP")
            tp_hit.append({**p, "cur": cur, "pnl_pct": pnl_pct})
            log.info(f"  ✅ TP [{name}] {cur:,}원 ({pnl_pct:+.1f}%)")

        elif cur <= sl:
            sl_init = p.get("sl_init", sl)
            reason  = "TRAIL_SL" if sl > sl_init else "SL"
            record_trade_history(p, cur, reason)
            sl_hit.append({**p, "cur": cur, "pnl_pct": pnl_pct, "sl_reason": reason})
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
            msg += (
                f"✅ *TP 달성* — {h['name']} ({h['ticker']})\n"
                f"  진입 {h['entry']:,}원 → 현재 *{h['cur']:,}원* ({h['pnl_pct']:+.1f}%)\n"
                f"  TP {h['tp']:,}원 도달 확인\n\n"
            )
        for h in sl_hit:
            tag = " 〔트레일링〕" if h.get("sl_reason") == "TRAIL_SL" else ""
            msg += (
                f"🔴 *SL 도달{tag}* — {h['name']} ({h['ticker']})\n"
                f"  진입 {h['entry']:,}원 → 현재 *{h['cur']:,}원* ({h['pnl_pct']:+.1f}%)\n"
                f"  SL {h['sl']:,}원 | 즉시 확인 필요\n\n"
            )
        msg += f"_잔여 추적: {len(remaining)}개_"
        send_telegram(msg)
        log.info(f"  알림 발송: TP {len(tp_hit)}개 SL {len(sl_hit)}개")
    else:
        log.info(f"  TP/SL 달성 없음 (잔여 {len(remaining)}개)")


# ==========================================
# 스케줄 1 — 14:30 : 1차 스크리닝
# ==========================================
def job_first_screen() -> None:
    """
    FDR 종가 데이터로 눌림목 후보 1차 추출 (Phase 1·2 필터 포함)
    """
    global _first_screen_cache
    now = datetime.now(KST)

    if is_market_closed(now):
        return

    log.info(f"\n{'='*50}")
    log.info(f"[14:30] 1차 스윙 눌림목 스크리닝 시작")
    log.info(f"{'='*50}")

    try:
        start_date = (now - timedelta(days=150)).replace(tzinfo=None)

        if STRATEGY["use_market_filter"]:
            market_ok, market_status = get_kospi_condition(start_date)
            log.info(f"  시장 컨디션: {market_status}")
            if not market_ok:
                send_telegram(
                    f"📉 *시장 약세 — 스크리닝 억제*\n"
                    f"{now.strftime('%Y-%m-%d')}\n"
                    f"{market_status}\n"
                    f"KOSPI MA20 회복 전까지 신규 진입 보류"
                )
                _first_screen_cache = []
                return
        else:
            log.info("  시장 컨디션 필터: 비활성화")

        krx = fdr.StockListing("KRX")
        krx = krx[krx["Market"].isin(["KOSPI", "KOSDAQ"])].copy()

        cap_col = next((c for c in ["Marcap","MarCap","marcap","시가총액"] if c in krx.columns), None)
        if cap_col:
            before = len(krx)
            krx[cap_col] = pd.to_numeric(krx[cap_col], errors="coerce").fillna(0)
            krx = krx[krx[cap_col] >= STRATEGY["min_marcap"]]
            log.info(f"  시총 {STRATEGY['min_marcap']//100_000_000}억 미만 제외: {before}개 → {len(krx)}개")

        sec_col = next((c for c in ["Sector","Industry","업종","Ind1","IndName"] if c in krx.columns), None)
        if sec_col:
            log.info(f"  섹터 컬럼 감지: '{sec_col}'")

        candidates = []
        total = len(krx)
        filter_counts = {
            "데이터부족": 0, "거래대금": 0, "기준봉없음": 0,
            "거래량": 0, "지지": 0, "캔들": 0, "MA": 0, "RSI": 0, "가격위치": 0,
        }

        for i, (_, row) in enumerate(krx.iterrows(), 1):
            ticker = row["Code"]
            name   = row["Name"]
            sector = str(row[sec_col]).strip() if sec_col and pd.notna(row[sec_col]) else ""

            if i % 300 == 0:
                log.info(f"  진행: {i}/{total} | 후보: {len(candidates)}개")

            try:
                _raw = fdr_data_reader(ticker, start_date)
                if _raw is None or _raw.empty:
                    continue
                if len(_raw) < 60:
                    filter_counts["데이터부족"] += 1
                    continue
                df = _raw.copy()   # SettingWithCopyWarning 방지

                df["MA20"]  = df["Close"].rolling(20).mean()
                df["MA60"]  = df["Close"].rolling(60).mean()
                df["Vol20"] = df["Volume"].rolling(20).mean()
                df["RSI"]   = calc_rsi(df["Close"], STRATEGY["rsi_period"])

                today    = df.iloc[-1]
                turnover = today["Close"] * today["Volume"]

                if turnover < STRATEGY["min_turnover"]:
                    filter_counts["거래대금"] += 1
                    continue

                if STRATEGY["use_price_range"]:
                    low_150   = df["Close"].min()
                    high_150  = df["Close"].max()
                    range_150 = high_150 - low_150
                    if range_150 > 0 and today["Close"] < low_150 + range_150 * STRATEGY["price_range_pct"]:
                        filter_counts["가격위치"] += 1
                        continue

                bo_candle    = None
                bo_date      = None
                bo_rsi       = None
                vol20_before = None

                for lookback in [1, 2, 3]:
                    bo_idx = -(lookback + 1)
                    if abs(bo_idx) > len(df) or abs(bo_idx - 1) > len(df):
                        continue
                    curr        = df.iloc[bo_idx]
                    vol20_at_bo = df["Vol20"].iloc[bo_idx]
                    bo_is_bull   = curr["Close"] > curr["Open"]
                    bo_body_pct  = curr["Close"] / curr["Open"] - 1
                    bo_vol_ratio = curr["Volume"] / vol20_at_bo if vol20_at_bo > 0 else 0
                    if bo_is_bull and bo_body_pct >= STRATEGY["bo_body_pct"] and bo_vol_ratio >= STRATEGY["bo_vol_ratio"]:
                        bo_candle    = curr
                        bo_date      = df.index[bo_idx].strftime("%Y-%m-%d")
                        bo_rsi       = df["RSI"].iloc[bo_idx]
                        vol20_before = df["Vol20"].iloc[bo_idx - 1] if abs(bo_idx - 1) <= len(df) else vol20_at_bo
                        break

                if bo_candle is None or vol20_before is None or vol20_before <= 0:
                    filter_counts["기준봉없음"] += 1
                    continue

                if pd.notna(bo_rsi) and bo_rsi < STRATEGY["rsi_min"]:
                    filter_counts["RSI"] += 1
                    continue

                today_body   = today["Close"] - today["Open"]
                today_range  = today["High"] - today["Low"]
                cond_vol_dry = today["Volume"] <= vol20_before * STRATEGY["pullback_vol"]
                cond_support = today["Close"] >= bo_candle["Open"]
                cond_shape   = (abs(today_body) / today_range <= STRATEGY["pullback_shape"]) if today_range > 0 else False
                cond_ma20    = today["Close"] >= today["MA20"]
                cond_ma60    = (today["Close"] >= today["MA60"]) if (STRATEGY["use_ma60_filter"] and pd.notna(today["MA60"])) else True

                if not cond_vol_dry: filter_counts["거래량"] += 1
                elif not cond_support: filter_counts["지지"] += 1
                elif not cond_shape: filter_counts["캔들"] += 1
                elif not (cond_ma20 and cond_ma60): filter_counts["MA"] += 1

                if cond_vol_dry and cond_support and cond_shape and cond_ma20 and cond_ma60:
                    _low      = df["Close"].min()
                    _high     = df["Close"].max()
                    _ma20     = today["MA20"]
                    _vol_dry  = today["Volume"] / vol20_before if vol20_before > 0 else 1.0
                    _shape    = abs(today_body) / today_range if today_range > 0 else 0.5
                    _ma20_gap = (today["Close"] - _ma20) / _ma20 if pd.notna(_ma20) and _ma20 > 0 else 0.05
                    _price_pos = (today["Close"] - _low) / (_high - _low) if _high > _low else 0.70

                    cand = {
                        "name":          name,
                        "ticker":        ticker,
                        "sector":        sector,
                        "bo_date":       bo_date,
                        "bo_open":       int(bo_candle["Open"]),
                        "bo_high":       int(bo_candle["High"]),
                        "bo_body_pct":   round(bo_body_pct * 100, 1),
                        "bo_vol_ratio":  round(bo_vol_ratio, 2),
                        "bo_lookback":   lookback,
                        "bo_rsi":        round(bo_rsi, 1) if pd.notna(bo_rsi) else None,
                        "vol20_before":  int(vol20_before),
                        "vol_dry_ratio": round(_vol_dry, 3),
                        "shape_ratio":   round(_shape, 3),
                        "ma20_gap":      round(_ma20_gap, 4),
                        "price_pos":     round(_price_pos, 3),
                        "fdr_close":     int(today["Close"]),
                        "turnover":      int(turnover),
                    }
                    cand["signal_score"] = calc_signal_score(cand)
                    candidates.append(cand)

                time.sleep(0.1)   # IP 차단 방지: 0.05 → 0.1 (초당 ~10회)

            except Exception as e:
                log.warning(f"  [SKIP] {ticker} {name}: {e}")

        _first_screen_cache = candidates
        log.info(f"\n[14:30] 1차 완료: {len(candidates)}개 눌림목 후보 저장")
        log.info(f"  필터 탈락 현황: {filter_counts}")
        log.info("  → 15:20 KIS 실시간 재검증 예정\n")

        if candidates:
            names = ", ".join(s["name"] for s in candidates[:10])
            more  = f" 외 {len(candidates) - 10}개" if len(candidates) > 10 else ""
            send_telegram(
                f"🔍 *1차 스윙 스크리닝 완료* ({now.strftime('%Y-%m-%d')})\n"
                f"눌림목 후보 {len(candidates)}개: {names}{more}\n"
                f"⏰ 15:20 실시간 재검증 예정"
            )

    except Exception as e:
        err = f"[ERROR] 1차 스크리닝 예외: {e}"
        log.error(err)
        send_telegram(f"🚨 *1차 스크리닝 오류*\n```{err}```")


# ==========================================
# 스케줄 2 — 15:20 : 2차 실시간 검증 + 발송
# ==========================================
def job_second_screen() -> None:
    """KIS 실시간 시세로 1차 후보 재검증 + 섹터 집중도 경고"""
    if is_market_closed(datetime.now(KST)):
        return

    log.info(f"\n{'='*50}")
    log.info(f"[15:20] 2차 실시간 검증 시작")
    log.info(f"{'='*50}")

    if not _first_screen_cache:
        send_telegram("⚠️ 1차 후보군 없음 (14:30 스크리닝 실행 여부 확인)")
        return

    candidates = _first_screen_cache
    log.info(f"  대상: {len(candidates)}개 종목")
    verified = []

    for stock in candidates:
        live = get_current_price(stock["ticker"])

        if not live:
            sl_price    = int(stock["bo_open"] * STRATEGY["sl_buffer"])
            entry_price = stock["fdr_close"]
            sl_pct      = (entry_price - sl_price) / entry_price
            if sl_pct > STRATEGY["sl_limit"]:
                log.info(f"  [탈락-FB] {stock['name']} API 실패 + 손절폭 과대 ({sl_pct*100:.1f}%)")
                continue
            verified.append({
                **stock,
                "entry": entry_price,
                "tp":    int(entry_price * (1 + STRATEGY["tp_pct"])),
                "sl":    sl_price,
                "live_vol": None, "live_verified": False,
            })
            log.info(f"  [통과-FB] {stock['name']} (종가 기준, API 미확인)")
            continue

        cur              = live["current"]
        live_open        = live["open"]
        live_high        = live["high"]
        live_low         = live["low"]
        live_vol         = live["volume"]
        live_buy_pressure = live["buy_pressure"]
        live_range       = live_high - live_low

        ok_vol_dry      = live_vol <= stock["vol20_before"] * (STRATEGY["pullback_vol"] + 0.2)
        ok_support      = cur >= stock["bo_open"]
        ok_shape        = (abs(cur - live_open) / live_range <= STRATEGY["pullback_shape"] + 0.10) if live_range > 0 else False
        ok_buy_pressure = live_buy_pressure >= STRATEGY["min_buy_pressure"]

        if ok_vol_dry and ok_support and ok_shape and ok_buy_pressure:
            sl_price       = int(stock["bo_open"] * STRATEGY["sl_buffer"])
            tp_price       = int(cur * (1 + STRATEGY["tp_pct"]))
            sl_pct         = (cur - sl_price) / cur
            if sl_pct > 0.10:
                log.info(f"  [탈락] {stock['name']} 손절폭 과대 ({sl_pct*100:.1f}%)")
                continue
            bo_high        = stock.get("bo_high", stock["bo_open"])
            pullback_depth = round((bo_high - cur) / bo_high * 100, 1) if bo_high > 0 else 0.0
            verified.append({
                **stock,
                "entry":          cur,
                "tp":             tp_price,
                "sl":             sl_price,
                "sl_pct":         round(sl_pct * 100, 1),
                "live_vol":       live_vol,
                "live_verified":  True,
                "buy_pressure":   round(live_buy_pressure, 1),
                "pullback_depth": pullback_depth,
            })
            log.info(f"  [통과] {stock['name']} (현재가={cur:,} | 손절폭={sl_pct*100:.1f}% | 체결강도={live_buy_pressure:.0f} | 눌림깊이={pullback_depth:.1f}%)")
        else:
            log.info(f"  [탈락] {stock['name']} vol={ok_vol_dry} support={ok_support} shape={ok_shape} buy_pressure={ok_buy_pressure}({live_buy_pressure:.0f})")

        time.sleep(0.1)

    log.info(f"  최종 통과: {len(verified)}개 / {len(candidates)}개")
    verified.sort(key=lambda s: s.get("signal_score", 0), reverse=True)

    if not verified:
        send_telegram(
            f"📉 *{datetime.now(KST).strftime('%Y-%m-%d')} 스윙 눌림목 없음*\n"
            f"1차 후보 {len(candidates)}개 → 실시간 재검증 전원 탈락"
        )
        return

    # 섹터 집중도 경고
    sector_counts: dict[str, list[str]] = {}
    for s in verified:
        sec = s.get("sector") or "미분류"
        sector_counts.setdefault(sec, []).append(s["name"])
    sector_warnings = {
        sec: names for sec, names in sector_counts.items()
        if sec != "미분류" and len(names) > STRATEGY["max_sector_count"]
    }
    if sector_warnings:
        warn_lines = "\n".join(
            f"  ▸ {sec}: {', '.join(names)} ({len(names)}개)"
            for sec, names in sector_warnings.items()
        )
        send_telegram(
            f"⚠️ *섹터 집중 경고* — {datetime.now(KST).strftime('%m/%d')}\n"
            f"{warn_lines}\n"
            f"동일 섹터 {STRATEGY['max_sector_count']}개 초과 — 분산 여부 판단 필요"
        )

    date_str = datetime.now(KST).strftime("%m/%d %H:%M")
    msg = f"🚀 *스윙 눌림목 타점 포착!* ({date_str})\n\n"
    for i, s in enumerate(verified, 1):
        icon      = "✅" if s.get("live_verified") else "⚠️"
        rsi_str   = f" | RSI {s['bo_rsi']}" if s.get("bo_rsi") else ""
        sec_str   = f" [{s['sector']}]" if s.get("sector") else ""
        score     = s.get("signal_score", 0)
        filled    = round(score / 10)
        score_bar = "█" * filled + "░" * (10 - filled)
        bp_str    = f"{s['buy_pressure']:.0f}" if s.get("buy_pressure") is not None else "-"
        pd_str    = f"{s['pullback_depth']:.1f}%" if s.get("pullback_depth") is not None else "-"
        msg += f"*{i}. {s['name']}* ({s['ticker']}){sec_str} {icon}\n"
        msg += f"  ▪️ 신호점수: {score_bar} {score}점\n"
        msg += f"  ▪️ 기준봉: {s['bo_date']} +{s['bo_body_pct']}% / {s.get('bo_vol_ratio', '?')}x{rsi_str}\n"
        msg += f"  ▪️ 체결강도: {bp_str} | 눌림깊이: {pd_str} ({s.get('bo_lookback', '?')}일 경과)\n"
        msg += f"  ▪️ 목표 보유: {STRATEGY['max_hold_days']}일 이내\n"
        msg += f"  ▪️ 진입가: {s['entry']:,}원\n"
        msg += f"  ▪️ 익절(TP): {s['tp']:,}원 (+{int(STRATEGY['tp_pct']*100)}%)\n"
        sl_pct_str = f"{s['sl_pct']}%" if s.get("sl_pct") else "-"
        msg += f"  ▪️ 손절(SL): {s['sl']:,}원 (-{sl_pct_str})\n"
        if s.get("live_verified") and s.get("live_vol"):
            msg += f"  ▪️ 실시간 거래량: {s['live_vol']:,}\n"
        msg += "\n"
    msg += "✅ KIS 실시간 재검증  ⚠️ 종가 기준(API 미확인)"

    with _signals_lock:
        paused = _pause_signals

    if paused:
        send_telegram(
            f"⏸ *신호 억제 중* — {len(verified)}개 신호 발생\n"
            f"재개하려면 /resume 전송"
        )
        log.info(f"  [PAUSE] 신호 {len(verified)}개 억제됨 (_pause_signals=True)")
    else:
        send_telegram(msg)
        log.info("  텔레그램 발송 완료")
        add_positions(verified)


# ==========================================
# Heartbeat — 매일 09:00
# ==========================================
def _build_position_line(p: dict) -> str:
    entry   = p.get("entry", 0)
    tp      = p.get("tp", 0)
    sl      = p.get("sl", 0)
    sl_init = p.get("sl_init", sl)
    ticker  = p["ticker"]
    name    = p["name"]
    days    = p.get("elapsed_days", 5)
    edate   = p.get("entry_date", "-")

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
            f"• *{name}* ({ticker})\n"
            f"  진입 {entry:,}원 → 현재 *{cur:,}원* | {days}일 경과\n"
            f"  TP {tp:,}원 / SL {sl:,}원{trail_tag}\n"
            f"  {status}\n"
        )
    else:
        return (
            f"• *{name}* ({ticker})\n"
            f"  진입 {entry:,}원 | {days}일 경과 ({edate} 진입)\n"
            f"  TP {tp:,}원 / SL {sl:,}원 ⚠️ 현재가 조회 실패\n"
        )


def job_heartbeat() -> None:
    """
    매일 09:00:
    1) 봇 생존 신호
    2) 5 거래일 경과 포지션 → 이력 기록 + 정리 알람
    3) [Phase 3] 월요일에만 주간 성과 리포트 + 드리프트 감지 발송
    """
    if is_market_closed(datetime.now(KST)):
        return
    now = datetime.now(KST)

    expired, active = check_expired_positions()

    expired_lines = []
    if expired:
        for p in expired:
            live = get_current_price(p["ticker"])
            exit_price = live["current"] if live else p.get("entry", 0)
            record_trade_history(p, exit_price, "EXPIRE")

            entry     = p.get("entry", 0)
            pnl_pct   = (exit_price - entry) / entry * 100 if entry else 0
            days      = p.get("elapsed_days", 5)
            tp        = p.get("tp", 0)
            sl        = p.get("sl", 0)
            sl_init   = p.get("sl_init", sl)
            trail_tag = f" | Trail SL {sl:,}" if sl > sl_init else ""
            api_warn  = "" if live else " ⚠️ 현재가 조회 실패"
            expired_lines.append(
                f"• *{p['name']}* ({p['ticker']})\n"
                f"  진입 {entry:,}원 → 최종 *{exit_price:,}원* ({pnl_pct:+.1f}%) | {days}일 경과\n"
                f"  TP {tp:,}원 / SL {sl:,}원{trail_tag}\n"
                f"  ⏰ 기간 만료 — 정리 검토{api_warn}\n"
            )
            time.sleep(0.2)
        save_positions(active)
        log.info(f"  만료 포지션 {len(expired)}개 이력 기록 후 제거 → 잔여 {len(active)}개")

    total_tracking = len(active) + len(expired)

    msg = (
        f"💚 *봇 정상 작동 중* ({now.strftime('%Y-%m-%d %H:%M')})\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⏰ 10:00 / 13:00 → 장중 TP/SL 모니터링\n"
        f"⏰ 14:30 → 1차 스크리닝\n"
        f"⏰ 15:20 → 2차 재검증 + 발송\n"
        f"🔑 KIS 모드: {'실전투자' if _KIS_MODE == 'real' else '모의투자'}\n"
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

    # ── [Phase 3] 월요일: 주간 성과 리포트 + 드리프트 감지 ────
    if now.weekday() == 0:
        log.info("  [월요일] 주간 성과 리포트 생성 중...")
        _send_weekly_report(now)


# ==========================================
# 시작 텔레그램 알림
# ==========================================
def send_startup_message() -> None:
    now          = datetime.now(KST)
    kis_mode_str = "🔴 실전투자" if _KIS_MODE == "real" else "🟡 모의투자"
    mf_str       = "활성화" if STRATEGY["use_market_filter"] else "비활성화"
    send_telegram(
        f"✅ *스윙 눌림목 검색기 v4.5 시작* (채팅방 {len(TELEGRAM_CHAT_IDS)}개)\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🕐 시작 시각: {now.strftime('%Y-%m-%d %H:%M')}\n"
        f"🔑 KIS 모드: {kis_mode_str}\n"
        f"📊 시장 필터(KOSPI MA20): {mf_str}\n"
        f"🎯 트레일링 스탑: HWM -{int(STRATEGY['trail_pct']*100)}%\n"
        f"📈 드리프트 감지: {STRATEGY['drift_weeks']}주 연속 승률 {int(STRATEGY['drift_winrate_threshold']*100)}% 미달 시 경고\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⏰ 09:00 → Heartbeat + 만료 포지션 정리 (월: 주간 리포트)\n"
        f"⏰ 10:00 / 13:00 → 장중 TP/SL 모니터링\n"
        f"⏰ 14:30 → 1차 스크리닝\n"
        f"⏰ 15:20 → 2차 재검증 + 발송\n"
        f"━━━━━━━━━━━━━━━━━━"
    )


# ==========================================
# 시작 시각별 catch-up
# ==========================================
def run_catchup() -> None:
    now = datetime.now(KST)
    d = now.date()

    # 휴장일 체크 (날짜 기준만 — 시간 체크는 run_catchup이 자체적으로 처리)
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


# ==========================================
# [Phase 7] 텔레그램 커맨드 핸들러
# ==========================================
def _cmd_positions() -> None:
    """/positions — 보유 포지션 실시간 PnL"""
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
        live  = get_current_price(p["ticker"])

        if live:
            cur     = live["current"]
            pnl_pct = (cur - entry) / entry * 100 if entry else 0
            pnls.append(pnl_pct)
            if   cur >= tp:        status = f"✅ TP 근접 ({pnl_pct:+.1f}%)"
            elif cur <= sl:        status = f"🔴 SL 근접 ({pnl_pct:+.1f}%)"
            elif pnl_pct >= 0:     status = f"📈 {pnl_pct:+.1f}%"
            else:                  status = f"📉 {pnl_pct:+.1f}%"
            score_str = f" [{p['signal_score']}점]" if p.get("signal_score") is not None else ""
            msg += (
                f"• *{p['name']}* ({p['ticker']}){score_str}\n"
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
    """/report — 누적 성과 리포트 on-demand"""
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
        lines.append(f"  {emoji} {row['name']} | {row['exit_reason']} | {row['pnl_pct']:+.2f}%")
    msg += "\n━━━━━━━━━━━━━━━━━━\n📌 *최근 5건*\n" + "\n".join(lines)
    send_telegram(msg)


def _handle_command(text: str) -> None:
    """텔레그램 커맨드 라우팅"""
    global _pause_signals
    cmd = text.strip().split()[0].lower()

    if cmd == "/positions":
        _cmd_positions()
    elif cmd == "/report":
        _cmd_report()
    elif cmd == "/pause":
        with _signals_lock:
            _pause_signals = True
        send_telegram("⏸ *신호 발송 일시정지*\n신규 신호를 억제합니다.\n재개: /resume")
    elif cmd == "/resume":
        with _signals_lock:
            _pause_signals = False
        send_telegram("▶️ *신호 발송 재개*\n정상 스캔 신호를 발송합니다.")
    elif cmd in ("/help", "/start"):
        send_telegram(
            "📋 *사용 가능한 커맨드*\n\n"
            "/positions — 보유 포지션 실시간 PnL\n"
            "/report — 누적 성과 리포트\n"
            "/pause — 신규 신호 발송 정지\n"
            "/resume — 신호 발송 재개\n"
            "/help — 이 메시지"
        )
    else:
        send_telegram(f"⚠️ 알 수 없는 커맨드: `{cmd}`\n/help 로 목록 확인")


def _telegram_polling_loop() -> None:
    """
    별도 데몬 스레드에서 실행 — Telegram getUpdates long polling
    ─────────────────────────────────────────────────────────────
    · timeout=30: 최대 30초 대기 후 재폴링 (서버 부하 최소화)
    · allowed_ids: TELEGRAM_CHAT_IDS 등록 채팅방만 처리 (보안)
    · daemon=True: 메인 스레드 종료 시 자동 종료
    · _offset_lock: _tg_update_offset 스레드 안전 읽기/쓰기
    """
    global _tg_update_offset

    if not TELEGRAM_TOKEN:
        log.info("[polling] 텔레그램 토큰 없음 — 폴링 비활성화")
        return

    allowed_ids = {str(c) for c in TELEGRAM_CHAT_IDS}
    log.info("[polling] 텔레그램 커맨드 폴링 시작")

    while not _shutdown_event.is_set():
        try:
            with _offset_lock:
                offset = _tg_update_offset

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
                _shutdown_event.wait(timeout=5)
                continue

            for update in res.json().get("result", []):
                with _offset_lock:
                    _tg_update_offset = update["update_id"] + 1
                msg_obj = update.get("message", {})
                chat_id = str(msg_obj.get("chat", {}).get("id", ""))
                text    = msg_obj.get("text", "")

                if chat_id not in allowed_ids or not text.startswith("/"):
                    continue

                log.info(f"[polling] 커맨드: {text!r} (chat={chat_id})")
                _handle_command(text)

        except ReadTimeout:
            # Long Polling 정상 동작 (35초 대기 후 응답 없음) — 재폴링
            continue
        except RequestsConnectionError as e:
            log.warning(f"[polling] 네트워크 연결 오류: {e}")
            _shutdown_event.wait(timeout=5)
        except Exception as e:
            log.warning(f"[polling] 예외: {e}")
            _shutdown_event.wait(timeout=5)


# ==========================================
# [Phase 6] 안전 실행 래퍼
# ==========================================
def _safe_run(fn, label: str) -> None:
    """
    스케줄 job을 감싸는 안전 실행 래퍼
    미처리 예외 발생 시 콘솔 출력 + 텔레그램 즉시 알림
    ─────────────────────────────────────────────────
    job_first_screen은 내부 try/except를 유지하되
    이 래퍼가 최후 방어선 역할을 함
    """
    try:
        fn()
    except Exception as e:
        err = f"[ERROR] {label} 예외: {e}"
        log.error(err)
        send_telegram(f"🚨 *{label} 오류 — 즉시 확인 필요*\n```{err[:300]}```")


# ==========================================
# Graceful shutdown 핸들러
# ==========================================
def _handle_shutdown(signum, frame) -> None:
    log.info(f"[shutdown] 신호 수신 ({signum}) — 안전 종료 중...")
    _shutdown_event.set()


# ==========================================
# 스케줄러 등록 & 실행
# ==========================================
if __name__ == "__main__":
    # 종료 시그널 등록 (Windows는 SIGTERM 미지원)
    signal.signal(signal.SIGINT, _handle_shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_shutdown)

    schedule.every().day.at("09:00", "Asia/Seoul").do(lambda: _safe_run(job_heartbeat,          "Heartbeat"))
    schedule.every().day.at("10:00", "Asia/Seoul").do(lambda: _safe_run(job_monitor_positions,  "장중 모니터링(10시)"))
    schedule.every().day.at("13:00", "Asia/Seoul").do(lambda: _safe_run(job_monitor_positions,  "장중 모니터링(13시)"))
    schedule.every().day.at("14:30", "Asia/Seoul").do(lambda: _safe_run(job_first_screen,       "1차 스크리닝"))
    schedule.every().day.at("15:20", "Asia/Seoul").do(lambda: _safe_run(job_second_screen,      "2차 검증"))

    log.info("\n✅ 스윙 눌림목 검색기 v4.5 시작")
    log.info("  ⏰ 09:00 → Heartbeat + 만료 이력 기록 (월요일: 주간 리포트)")
    log.info("  ⏰ 10:00 → 장중 TP/SL 모니터링")
    log.info("  ⏰ 13:00 → 장중 TP/SL 모니터링")
    log.info("  ⏰ 14:30 → 1차 스크리닝")
    log.info("  ⏰ 15:20 → 2차 재검증 + 텔레그램")
    log.info(f"  🔑 KIS 모드: {'실전투자' if _KIS_MODE == 'real' else '모의투자'}")
    log.info(f"  🎯 트레일링 스탑: HWM -{int(STRATEGY['trail_pct']*100)}%")
    log.info(f"  📊 드리프트 감지: {STRATEGY['drift_weeks']}주 연속 승률 {int(STRATEGY['drift_winrate_threshold']*100)}% 미달")
    log.info("  종료: Ctrl+C\n")

    threading.Thread(
        target=_telegram_polling_loop, daemon=True, name="tg-polling"
    ).start()

    send_startup_message()
    run_catchup()

    while not _shutdown_event.is_set():
        schedule.run_pending()
        _shutdown_event.wait(timeout=1)

    log.info("[shutdown] 봇 종료 완료")
