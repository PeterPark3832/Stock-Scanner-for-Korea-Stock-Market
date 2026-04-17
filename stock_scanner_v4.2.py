"""
주식 검색기 v4.2 (3~5일 스윙 전용)
────────────────────────────────────────
v4.1 → v4.2 변경사항 (Phase 1: 신호 품질 향상):
  [신규 1]   공휴일 캘린더: holidays.KR() 적용, is_market_closed() 헬퍼로 일원화
             (기존 weekday() >= 5 단순 체크 → 설·추석·공휴일 포함)
  [신규 2]   시장 컨디션 필터: KOSPI(KS11) MA20 아래면 스크리닝 억제
             (STRATEGY["use_market_filter"] = True)
  [신규 3]   거래대금 필터: 일 거래대금 < 기준 억원 종목 제외
             (STRATEGY["min_turnover"], 기본 10억)
  [신규 4]   RSI 필터: 기준봉 당일 RSI < rsi_min 이면 제외 (자유낙하 종목 차단)
             (STRATEGY["rsi_period"] = 14, STRATEGY["rsi_min"] = 30)
  [신규 5]   가격 범위 필터: 150일 저가 대비 상위 rsi_range_pct 이상 위치 종목만
             (52주 신고가 근접 프록시, STRATEGY["price_range_pct"] = 0.70)
────────────────────────────────────────
v3.2 → v4.0 변경사항 (백테스트 v2-final 파라미터 동기화):
  [동기화 1] bo_body_pct  0.05 → 0.07
  [동기화 2] pullback_shape 0.30 → 0.25
  [신규 1]   MA60 이상 진입 조건 추가 (use_ma60_filter)
  [신규 2]   시총 500억 미만 유니버스 제외 (min_marcap)
  [신규 3]   STRATEGY 파라미터 딕셔너리로 중앙 관리
────────────────────────────────────────
"""

import json
import os
import time
import requests
import schedule
import holidays
import pandas as pd
import FinanceDataReader as fdr
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

KST = ZoneInfo("Asia/Seoul")  # 서버 타임존 무관하게 한국시간 고정

# ==========================================
# 공휴일 캘린더 (한국 법정 공휴일 포함)
# ==========================================
def is_market_closed(dt: datetime) -> bool:
    """
    주말 또는 한국 법정 공휴일이면 True 반환
    dt: KST datetime (또는 date)
    """
    d = dt.date() if hasattr(dt, "date") else dt
    if d.weekday() >= 5:  # 토·일
        return True
    kr_holidays = holidays.KR(years=d.year)
    return d in kr_holidays


# ==========================================
# 포지션 추적 (5 거래일 경과 시 정리 알람)
# ==========================================
POSITIONS_FILE = "positions.json"

def _count_weekdays(start: datetime, end: datetime) -> int:
    """두 날짜 사이의 평일(거래일) 수 계산"""
    days = 0
    cur = start.date() if hasattr(start, "date") else start
    end = end.date() if hasattr(end, "date") else end
    while cur < end:
        if cur.weekday() < 5:
            days += 1
        cur += timedelta(days=1)
    return days

def load_positions() -> list[dict]:
    """저장된 포지션 로드"""
    if not os.path.exists(POSITIONS_FILE):
        return []
    try:
        with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_positions(positions: list[dict]) -> None:
    """포지션 저장"""
    try:
        with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(positions, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[ERROR] 포지션 저장 실패: {e}")

def add_positions(stocks: list[dict]) -> None:
    """신규 포착 종목을 포지션 파일에 추가"""
    existing = load_positions()
    existing_tickers = {p["ticker"] for p in existing}
    now_str = datetime.now(KST).strftime("%Y-%m-%d")

    added = 0
    for s in stocks:
        if s["ticker"] not in existing_tickers:
            existing.append({
                "ticker":     s["ticker"],
                "name":       s["name"],
                "entry":      s["entry"],
                "tp":         s["tp"],
                "sl":         s["sl"],
                "entry_date": now_str,
            })
            added += 1

    save_positions(existing)
    print(f"  포지션 기록: {added}개 추가 (누적 {len(existing)}개)")

def check_expired_positions() -> tuple[list[dict], list[dict]]:
    """
    5 거래일 경과 종목 분리
    반환: (만료된 포지션, 유효한 포지션)
    """
    positions = load_positions()
    today = datetime.now(KST)
    expired, active = [], []

    for p in positions:
        try:
            entry_dt = datetime.strptime(p["entry_date"], "%Y-%m-%d")
            elapsed = _count_weekdays(entry_dt, today)
            p["elapsed_days"] = elapsed
            if elapsed >= 5:
                expired.append(p)
            else:
                active.append(p)
        except Exception:
            active.append(p)

    return expired, active


# ==========================================
# 환경변수
# ==========================================
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN")
_raw_ids = os.getenv("TELEGRAM_CHAT_IDS") or os.getenv("TELEGRAM_CHAT_ID") or ""
TELEGRAM_CHAT_IDS = [cid.strip() for cid in _raw_ids.split(",") if cid.strip()]
_raw_topic = os.getenv("TELEGRAM_TOPIC_ID", "").split("#")[0].strip()
TELEGRAM_TOPIC_ID = int(_raw_topic) if _raw_topic.isdigit() else None
KIS_APP_KEY      = os.getenv("KIS_APP_KEY")
KIS_APP_SECRET   = os.getenv("KIS_APP_SECRET")

_KIS_MODE = os.getenv("KIS_MODE", "paper").lower()
KIS_BASE_URL = (
    "https://openapi.koreainvestment.com:9443"
    if _KIS_MODE == "real"
    else "https://openapivts.koreainvestment.com:29443"
)

# ==========================================
# 전략 파라미터 (백테스트 v2-final 검증값)
# ==========================================
STRATEGY = {
    # ── 기존 파라미터 ──────────────────────────────────
    "bo_body_pct":     0.07,   # 기준봉 몸통 상승률 (시가 대비 종가)
    "bo_vol_ratio":    2.5,    # 기준봉 거래량 / 기준봉 이전 Vol20 배수
    "bo_lookback":     3,      # 기준봉 탐색 범위 (최근 N일)
    "pullback_vol":    1.0,    # 눌림목 거래량 <= 기준봉 이전 Vol20 × N배
    "pullback_shape":  0.25,   # 눌림목 몸통 / 전체범위 (도지/음봉)
    "tp_pct":          0.10,   # 익절 목표 +10%
    "sl_buffer":       0.99,   # 손절 = 기준봉 시가 × 0.99
    "sl_limit":        0.10,   # 손절폭 한도 (진입가 대비 10% 초과 시 제외)
    "max_hold_days":   7,      # 최대 보유일
    "use_ma60_filter": True,   # MA60 이상만 진입
    "min_marcap":      50_000_000_000,  # 시총 500억 미만 제외
    # ── Phase 1 신규 파라미터 ──────────────────────────
    "use_market_filter":  True,          # KOSPI MA20 시장 컨디션 필터
    "min_turnover":       1_000_000_000, # 일 거래대금 최소 10억 원
    "rsi_period":         14,            # RSI 계산 기간
    "rsi_min":            30,            # 기준봉 RSI 하한 (이하면 자유낙하로 판단, 제외)
    "use_price_range":    True,          # 150일 가격 범위 위치 필터
    "price_range_pct":    0.70,          # 150일 레인지 기준 상위 70% 위치 이상만
}


_first_screen_cache: list[dict] = []
_kis_token_cache: dict = {"token": None, "expires_at": 0}


# ==========================================
# RSI 계산 (TA-Lib 미사용, 자체 구현)
# ==========================================
def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """
    Wilder 방식 RSI (EWM com=period-1)
    반환: RSI 시리즈 (0~100), 초기 period 행은 NaN
    """
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - 100 / (1 + rs)


# ==========================================
# 텔레그램
# ==========================================
def send_telegram(text: str, topic_id: int | None = None) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_IDS:
        print("[WARN] 텔레그램 설정 없음 — .env 확인")
        return

    _topic_id = topic_id or TELEGRAM_TOPIC_ID

    for chat_id in TELEGRAM_CHAT_IDS:
        try:
            payload = {
                "chat_id":    chat_id,
                "text":       text,
                "parse_mode": "Markdown",
            }
            if _topic_id:
                payload["message_thread_id"] = _topic_id

            res = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                data=payload,
                timeout=10,
            )
            if res.status_code != 200:
                print(f"[ERROR] 텔레그램 실패 (chat_id={chat_id}): {res.text}")
        except Exception as e:
            print(f"[ERROR] 텔레그램 예외 (chat_id={chat_id}): {e}")
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
            json={"grant_type": "client_credentials", "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET},
            timeout=10,
        )
        res.raise_for_status()
        data = res.json()
        _kis_token_cache["token"] = data["access_token"]
        _kis_token_cache["expires_at"] = now + int(data.get("expires_in", 86400))
        print(f"[KIS] 토큰 발급 완료 (유효: {int(data.get('expires_in', 86400) / 3600)}시간)")
        return _kis_token_cache["token"]
    except Exception as e:
        print(f"[ERROR] KIS 토큰 발급 실패: {e}")
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
            "current": int(o["stck_prpr"]),
            "volume":  int(o["acml_vol"]),
            "open":    int(o["stck_oprc"]),
            "high":    int(o["stck_hgpr"]),
            "low":     int(o["stck_lwpr"]),
        }
    except Exception as e:
        print(f"  [ERROR] {ticker} 시세 조회 예외: {e}")
        return None


# ==========================================
# FDR 데이터 로딩 (3회 재시도)
# ==========================================
def fdr_data_reader(ticker: str, start_date, retries: int = 3, delay: float = 1.0):
    """FDR 네트워크 오류 시 최대 N회 재시도 후 None 반환"""
    for attempt in range(1, retries + 1):
        try:
            df = fdr.DataReader(ticker, start_date)
            return df
        except Exception as e:
            if attempt < retries:
                print(f"  [RETRY {attempt}/{retries}] {ticker}: {e}")
                time.sleep(delay)
            else:
                raise


# ==========================================
# [Phase 1-신규 2] 시장 컨디션 체크 (KOSPI MA20)
# ==========================================
def get_kospi_condition(start_date) -> tuple[bool, str]:
    """
    KOSPI(KS11) 지수가 MA20 위에 있으면 True 반환
    반환: (시장 양호 여부, 설명 문자열)
    """
    try:
        kospi = fdr.DataReader("KS11", start_date)
        if kospi.empty or len(kospi) < 20:
            return True, "KOSPI 데이터 부족 — 필터 통과"  # 데이터 없으면 통과로 처리
        ma20  = kospi["Close"].rolling(20).mean().iloc[-1]
        close = kospi["Close"].iloc[-1]
        above = close >= ma20
        status = f"KOSPI {close:,.0f} / MA20 {ma20:,.0f} ({'▲ 양호' if above else '▼ 약세'})"
        return above, status
    except Exception as e:
        print(f"  [WARN] KOSPI 조회 실패: {e} — 시장 필터 통과로 처리")
        return True, f"KOSPI 조회 실패: {e}"


# ==========================================
# 스케줄 1 — 14:30 : 1차 스크리닝
# ==========================================
def job_first_screen() -> None:
    """
    FDR 종가 데이터로 눌림목 후보 1차 추출
    ────────────────────────────────────────
    조건 1 (기준봉): 최근 1~3일 내, 시가 대비 종가 7% 이상 상승 양봉
                     + 기준봉 이전 20일 평균 거래량의 2.5배 이상
    조건 2 (눌림목): 당일 거래량 < 기준봉 이전 20일 평균의 1.0배
    조건 3 (지지):   당일 종가 >= 기준봉 시가
    조건 4 (캔들):   몸통 크기 <= 전체 범위의 25%
    조건 5 (추세):   당일 종가 >= MA20 및 MA60
    ── Phase 1 신규 조건 ──────────────────
    조건 6 (시장):   KOSPI MA20 이상일 때만 스크리닝 진행
    조건 7 (거래대금): 당일 거래대금 >= 10억
    조건 8 (RSI):    기준봉 당일 RSI >= 30 (자유낙하 종목 제외)
    조건 9 (가격위치): 150일 레인지 상위 70% 이상
    """
    global _first_screen_cache
    now = datetime.now(KST)

    if is_market_closed(now):
        return

    print(f"\n{'='*50}")
    print(f"[14:30] 1차 스윙 눌림목 스크리닝 시작")
    print(f"{'='*50}")

    try:
        start_date = (now - timedelta(days=150)).replace(tzinfo=None)

        # ── [Phase 1-신규 2] 시장 컨디션 필터 (게이트) ─────────────
        if STRATEGY["use_market_filter"]:
            market_ok, market_status = get_kospi_condition(start_date)
            print(f"  시장 컨디션: {market_status}")
            if not market_ok:
                msg = (
                    f"📉 *시장 약세 — 스크리닝 억제*\n"
                    f"{now.strftime('%Y-%m-%d')}\n"
                    f"{market_status}\n"
                    f"KOSPI MA20 회복 전까지 신규 진입 보류"
                )
                print(f"  → 시장 필터 차단, 스크리닝 중단")
                send_telegram(msg)
                _first_screen_cache = []
                return
        else:
            print("  시장 컨디션 필터: 비활성화")

        krx = fdr.StockListing("KRX")
        krx = krx[krx["Market"].isin(["KOSPI", "KOSDAQ"])].copy()

        # 시총 필터
        cap_col = next((c for c in ["Marcap","MarCap","marcap","시가총액"] if c in krx.columns), None)
        if cap_col:
            before = len(krx)
            krx[cap_col] = pd.to_numeric(krx[cap_col], errors="coerce").fillna(0)
            krx = krx[krx[cap_col] >= STRATEGY["min_marcap"]]
            print(f"  시총 {STRATEGY['min_marcap']//100_000_000}억 미만 제외: {before}개 → {len(krx)}개")

        candidates = []
        total = len(krx)

        # 필터별 탈락 카운터
        filter_counts = {
            "데이터부족": 0, "거래대금": 0, "기준봉없음": 0,
            "거래량": 0, "지지": 0, "캔들": 0, "MA": 0, "RSI": 0, "가격위치": 0,
        }

        for i, (_, row) in enumerate(krx.iterrows(), 1):
            ticker, name = row["Code"], row["Name"]

            if i % 300 == 0:
                print(f"  진행: {i}/{total} | 후보: {len(candidates)}개")

            try:
                df = fdr_data_reader(ticker, start_date)
                if df is None or df.empty:
                    continue
                if len(df) < 60:
                    filter_counts["데이터부족"] += 1
                    continue

                df["MA20"]  = df["Close"].rolling(20).mean()
                df["MA60"]  = df["Close"].rolling(60).mean()
                df["Vol20"] = df["Volume"].rolling(20).mean()
                df["RSI"]   = calc_rsi(df["Close"], STRATEGY["rsi_period"])  # [Phase 1-신규 4]

                today = df.iloc[-1]

                # ── [Phase 1-신규 3] 거래대금 필터 ─────────────────────
                turnover = today["Close"] * today["Volume"]
                if turnover < STRATEGY["min_turnover"]:
                    filter_counts["거래대금"] += 1
                    continue

                # ── [Phase 1-신규 5] 가격 위치 필터 (150일 레인지 상위 70%) ──
                if STRATEGY["use_price_range"]:
                    low_150  = df["Close"].min()
                    high_150 = df["Close"].max()
                    range_150 = high_150 - low_150
                    if range_150 > 0:
                        cond_range = today["Close"] >= low_150 + range_150 * STRATEGY["price_range_pct"]
                    else:
                        cond_range = True
                    if not cond_range:
                        filter_counts["가격위치"] += 1
                        continue

                # ── 기준봉 탐색 (1=어제, 2=그제, 3=그그제) ─────────────
                bo_candle    = None
                bo_date      = None
                bo_rsi       = None
                vol20_before = None

                for lookback in [1, 2, 3]:
                    bo_idx   = -(lookback + 1)
                    prev_idx = -(lookback + 2)

                    if abs(bo_idx) > len(df) or abs(prev_idx) > len(df):
                        continue

                    curr        = df.iloc[bo_idx]
                    vol20_at_bo = df["Vol20"].iloc[bo_idx]

                    bo_is_bull   = curr["Close"] > curr["Open"]
                    bo_body_pct  = curr["Close"] / curr["Open"] - 1
                    bo_vol_ratio = curr["Volume"] / vol20_at_bo if vol20_at_bo > 0 else 0

                    if bo_is_bull and bo_body_pct >= STRATEGY["bo_body_pct"] and bo_vol_ratio >= STRATEGY["bo_vol_ratio"]:
                        bo_candle    = curr
                        bo_date      = df.index[bo_idx].strftime("%Y-%m-%d")
                        bo_rsi       = df["RSI"].iloc[bo_idx]  # [Phase 1-신규 4]
                        vol20_before = df["Vol20"].iloc[bo_idx - 1] if abs(bo_idx - 1) <= len(df) else vol20_at_bo
                        break

                if bo_candle is None or vol20_before is None or vol20_before <= 0:
                    filter_counts["기준봉없음"] += 1
                    continue

                # ── [Phase 1-신규 4] RSI 필터: 기준봉 당일 RSI < rsi_min 제외 ──
                if pd.notna(bo_rsi) and bo_rsi < STRATEGY["rsi_min"]:
                    filter_counts["RSI"] += 1
                    continue

                # ── 오늘(눌림목) 조건 ──────────────────────────────────
                today_body  = today["Close"] - today["Open"]
                today_range = today["High"] - today["Low"]

                cond_vol_dry = today["Volume"] <= vol20_before * STRATEGY["pullback_vol"]
                cond_support = today["Close"] >= bo_candle["Open"]

                if today_range > 0:
                    cond_shape = abs(today_body) / today_range <= STRATEGY["pullback_shape"]
                else:
                    cond_shape = False

                cond_ma20 = today["Close"] >= today["MA20"]

                if STRATEGY["use_ma60_filter"] and pd.notna(today["MA60"]):
                    cond_ma60 = today["Close"] >= today["MA60"]
                else:
                    cond_ma60 = True

                # 탈락 이유 추적
                if not cond_vol_dry:
                    filter_counts["거래량"] += 1
                elif not cond_support:
                    filter_counts["지지"] += 1
                elif not cond_shape:
                    filter_counts["캔들"] += 1
                elif not (cond_ma20 and cond_ma60):
                    filter_counts["MA"] += 1

                if cond_vol_dry and cond_support and cond_shape and cond_ma20 and cond_ma60:
                    candidates.append({
                        "name":         name,
                        "ticker":       ticker,
                        "bo_date":      bo_date,
                        "bo_open":      int(bo_candle["Open"]),
                        "bo_body_pct":  round(bo_body_pct * 100, 1),
                        "bo_rsi":       round(bo_rsi, 1) if pd.notna(bo_rsi) else None,
                        "vol20_before": int(vol20_before),
                        "fdr_close":    int(today["Close"]),
                        "turnover":     int(turnover),
                    })

                time.sleep(0.05)

            except Exception as e:
                print(f"  [SKIP] {ticker} {name}: {e}")

        _first_screen_cache = candidates
        print(f"\n[14:30] 1차 완료: {len(candidates)}개 눌림목 후보 저장")
        print(f"  필터 탈락 현황: {filter_counts}")
        print("  → 15:20 KIS 실시간 재검증 예정\n")

        if candidates:
            names = ", ".join(s["name"] for s in candidates[:10])
            more  = f" 외 {len(candidates) - 10}개" if len(candidates) > 10 else ""
            send_telegram(
                f"🔍 *1차 스윙 스크리닝 완료* ({now.strftime('%Y-%m-%d')})\n"
                f"눌림목 후보 {len(candidates)}개: {names}{more}\n"
                f"⏰ 15:20 실시간 재검증 예정"
            )
        else:
            print("  후보 없음")

    except Exception as e:
        err = f"[ERROR] 1차 스크리닝 예외: {e}"
        print(err)
        send_telegram(f"🚨 *1차 스크리닝 오류*\n```{err}```")


# ==========================================
# 스케줄 2 — 15:20 : 2차 실시간 검증 + 발송
# ==========================================
def job_second_screen() -> None:
    """
    KIS 실시간 시세로 1차 후보 재검증
    ────────────────────────────────────────
    재검증 조건:
      - 실시간 거래량 < 기준봉 이전 20일 평균 × 1.2
      - 현재가 >= 기준봉 시가 (지지 유지)
      - 몸통 비율 <= 0.35 (도지/음봉 유지)
    """
    if is_market_closed(datetime.now(KST)):
        return

    print(f"\n{'='*50}")
    print(f"[15:20] 2차 실시간 검증 시작")
    print(f"{'='*50}")

    if not _first_screen_cache:
        msg = "⚠️ 1차 후보군 없음 (14:30 스크리닝 실행 여부 확인)"
        print(msg)
        send_telegram(msg)
        return

    candidates = _first_screen_cache
    print(f"  대상: {len(candidates)}개 종목")
    verified = []

    for stock in candidates:
        live = get_current_price(stock["ticker"])

        # ── Fallback: KIS API 실패 시 1차 종가 기준으로 대체 ──
        if not live:
            sl_price    = int(stock["bo_open"] * STRATEGY["sl_buffer"])
            entry_price = stock["fdr_close"]
            sl_pct      = (entry_price - sl_price) / entry_price

            if sl_pct > STRATEGY["sl_limit"]:
                print(f"  [탈락-FB] {stock['name']} API 실패 + 손절폭 과대 ({sl_pct*100:.1f}%)")
                continue

            verified.append({
                **stock,
                "entry":         entry_price,
                "tp":            int(entry_price * (1 + STRATEGY["tp_pct"])),
                "sl":            sl_price,
                "live_vol":      None,
                "live_verified": False,
            })
            print(f"  [통과-FB] {stock['name']} (종가 기준, API 미확인)")
            continue

        cur       = live["current"]
        live_open = live["open"]
        live_high = live["high"]
        live_low  = live["low"]
        live_vol  = live["volume"]
        live_range = live_high - live_low

        ok_vol_dry = live_vol <= stock["vol20_before"] * (STRATEGY["pullback_vol"] + 0.2)
        ok_support = cur >= stock["bo_open"]

        if live_range > 0:
            body_ratio = abs(cur - live_open) / live_range
            ok_shape   = body_ratio <= STRATEGY["pullback_shape"] + 0.10
        else:
            ok_shape = False

        if ok_vol_dry and ok_support and ok_shape:
            sl_price = int(stock["bo_open"] * STRATEGY["sl_buffer"])
            tp_price = int(cur * (1 + STRATEGY["tp_pct"]))

            sl_pct = (cur - sl_price) / cur
            if sl_pct > 0.10:
                print(f"  [탈락] {stock['name']} 손절폭 과대 "
                      f"({sl_pct*100:.1f}% — 진입가={cur:,} SL={sl_price:,})")
                continue

            verified.append({
                **stock,
                "entry":         cur,
                "tp":            tp_price,
                "sl":            sl_price,
                "sl_pct":        round(sl_pct * 100, 1),
                "live_vol":      live_vol,
                "live_verified": True,
            })
            print(f"  [통과] {stock['name']} (현재가={cur:,} | 손절폭={sl_pct*100:.1f}%)")
        else:
            print(f"  [탈락] {stock['name']} "
                  f"vol={ok_vol_dry} support={ok_support} shape={ok_shape}")

        time.sleep(0.1)

    print(f"  최종 통과: {len(verified)}개 / {len(candidates)}개")

    if verified:
        date_str = datetime.now(KST).strftime("%m/%d %H:%M")
        msg = f"🚀 *스윙 눌림목 타점 포착!* ({date_str})\n\n"
        for i, s in enumerate(verified, 1):
            icon = "✅" if s.get("live_verified") else "⚠️"
            rsi_str = f" | RSI {s['bo_rsi']}" if s.get("bo_rsi") else ""
            msg += f"*{i}. {s['name']}* ({s['ticker']}) {icon}\n"
            msg += f"  ▪️ 기준봉: {s['bo_date']} (몸통 +{s['bo_body_pct']}%{rsi_str})\n"
            msg += f"  ▪️ 목표 보유: {STRATEGY['max_hold_days']}일 이내\n"
            msg += f"  ▪️ 진입가: {s['entry']:,}원\n"
            tp_pct_display = int(STRATEGY["tp_pct"] * 100)
            msg += f"  ▪️ 익절(TP): {s['tp']:,}원 (+{tp_pct_display}%)\n"
            sl_pct_str = f"{s['sl_pct']}%" if s.get("sl_pct") else "-"
            msg += f"  ▪️ 손절(SL): {s['sl']:,}원 (-{sl_pct_str}, 기준봉 시가 이탈)\n"
            if s.get("live_verified") and s.get("live_vol"):
                msg += f"  ▪️ 실시간 거래량: {s['live_vol']:,}\n"
            msg += "\n"
        msg += "✅ KIS 실시간 재검증  ⚠️ 종가 기준(API 미확인)"
        send_telegram(msg)
        print(f"  텔레그램 발송 완료")
        add_positions(verified)
    else:
        send_telegram(
            f"📉 *{datetime.now(KST).strftime('%Y-%m-%d')} 스윙 눌림목 없음*\n"
            f"1차 후보 {len(candidates)}개 → 실시간 재검증 전원 탈락"
        )


# ==========================================
# Heartbeat — 매일 09:00 정상 작동 확인
# ==========================================
def _build_position_line(p: dict) -> str:
    entry  = p.get("entry", 0)
    tp     = p.get("tp", 0)
    sl     = p.get("sl", 0)
    ticker = p["ticker"]
    name   = p["name"]
    days   = p.get("elapsed_days", 5)
    edate  = p.get("entry_date", "-")

    live = get_current_price(ticker)
    if live:
        cur     = live["current"]
        pnl_pct = (cur - entry) / entry * 100 if entry else 0

        if cur >= tp:
            status = f"✅ TP 달성 ({pnl_pct:+.1f}%) — 익절 완료 확인"
        elif cur <= sl:
            status = f"🔴 SL 도달 ({pnl_pct:+.1f}%) — 손절 확인 필요"
        else:
            emoji  = "📈" if pnl_pct >= 0 else "📉"
            status = f"{emoji} 보유 중 ({pnl_pct:+.1f}%) — 정리 검토"

        line = (
            f"• *{name}* ({ticker})\n"
            f"  진입 {entry:,}원 → 현재 *{cur:,}원* | {days}일 경과\n"
            f"  TP {tp:,}원 / SL {sl:,}원\n"
            f"  {status}\n"
        )
    else:
        line = (
            f"• *{name}* ({ticker})\n"
            f"  진입 {entry:,}원 | {days}일 경과 ({edate} 진입)\n"
            f"  TP {tp:,}원 / SL {sl:,}원 ⚠️ 현재가 조회 실패\n"
        )
    return line


def job_heartbeat() -> None:
    """
    매일 오전 9시:
      1) 봇 생존 신호
      2) 5 거래일 경과 포지션 → KIS 현재가 조회 후 수익률 포함 정리 알람
    """
    if is_market_closed(datetime.now(KST)):
        return
    now = datetime.now(KST)

    expired, active = check_expired_positions()

    if expired:
        save_positions(active)
        print(f"  만료 포지션 {len(expired)}개 제거 → 잔여 {len(active)}개")

    total_tracking = len(active) + len(expired)

    msg = (
        f"💚 *봇 정상 작동 중* ({now.strftime('%Y-%m-%d %H:%M')})\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⏰ 14:30 → 1차 스크리닝 예정\n"
        f"⏰ 15:20 → 2차 재검증 + 발송 예정\n"
        f"🔑 KIS 모드: {'실전투자' if _KIS_MODE == 'real' else '모의투자'}\n"
        f"📋 추적 포지션: {total_tracking}개"
        + (f" (정리 대상 {len(expired)}개)" if expired else "")
    )

    if expired:
        msg += f"\n\n⏰ *5 거래일 경과 — 포지션 정리 검토*"
        msg += f"\n━━━━━━━━━━━━━━━━━━\n"
        for p in expired:
            msg += _build_position_line(p)
            time.sleep(0.2)
        msg += "\n_TP 달성 ✅ 는 수익 확정, SL 도달 🔴 은 손절 확인_"

    send_telegram(msg)
    print(f"[{now.strftime('%H:%M')}] Heartbeat 발송 완료 (만료 {len(expired)}개)")


# ==========================================
# 시작 텔레그램 알림
# ==========================================
def send_startup_message() -> None:
    now = datetime.now(KST)
    kis_mode_str = "🔴 실전투자" if _KIS_MODE == "real" else "🟡 모의투자"
    market_filter_str = "활성화" if STRATEGY["use_market_filter"] else "비활성화"
    send_telegram(
        f"✅ *스윙 눌림목 검색기 v4.2 시작* (채팅방 {len(TELEGRAM_CHAT_IDS)}개)\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🕐 시작 시각: {now.strftime('%Y-%m-%d %H:%M')}\n"
        f"🔑 KIS 모드: {kis_mode_str}\n"
        f"📊 시장 필터(KOSPI MA20): {market_filter_str}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⏰ 14:30 → 1차 스크리닝 (기준봉 + 눌림목 탐색)\n"
        f"⏰ 15:20 → 2차 재검증 (KIS 실시간) + 발송\n"
        f"━━━━━━━━━━━━━━━━━━"
    )


# ==========================================
# 시작 시각별 당일 catch-up
# ==========================================
def run_catchup() -> None:
    """
    늦게 시작해도 당일 스크리닝 즉시 실행
    ─────────────────────────────────────
    시작 시각          즉시 실행
    ─────────────────────────────────────
    ~ 14:29           없음 (14:30 스케줄 대기)
    14:30 ~ 15:19     1차만 즉시 실행 (15:20 스케줄 대기)
    15:20 ~ 15:29     1차 + 2차 즉시 순차 실행
    15:30 ~           skip — 내일 14:30부터 정상 실행
    ─────────────────────────────────────
    주말/공휴일에는 catch-up 없음
    """
    now = datetime.now(KST)

    if is_market_closed(now):
        print("  휴장일 — catch-up 없음\n")
        return

    hm = now.hour * 60 + now.minute

    T1 = 14 * 60 + 30  # 14:30
    T2 = 15 * 60 + 20  # 15:20
    T3 = 15 * 60 + 30  # 15:30

    if hm < T1:
        print("  14:30 이전 시작 — 스케줄 대기\n")

    elif T1 <= hm < T2:
        print("  14:30~15:20 사이 시작 → 1차 즉시 실행\n")
        send_telegram("⚡ 늦은 시작 감지 → 1차 스크리닝 즉시 시작합니다")
        job_first_screen()

    elif T2 <= hm < T3:
        print("  15:20~15:30 사이 시작 → 1차 + 2차 즉시 순차 실행\n")
        send_telegram("⚡ 늦은 시작 감지 → 1차 + 2차 스크리닝 즉시 순차 실행합니다")
        job_first_screen()
        job_second_screen()

    else:
        print("  15:30 이후 시작 — 당일 skip, 내일 14:30 정상 실행\n")
        send_telegram(
            f"⏭ *당일 스크리닝 skip*\n"
            f"시작 시각 {now.strftime('%H:%M')} — 장 마감 이후\n"
            f"내일 14:30부터 정상 스케줄로 실행됩니다"
        )


# ==========================================
# 스케줄러 등록 & 실행
# ==========================================
if __name__ == "__main__":
    schedule.every().day.at("09:00", "Asia/Seoul").do(job_heartbeat)
    schedule.every().day.at("14:30", "Asia/Seoul").do(job_first_screen)
    schedule.every().day.at("15:20", "Asia/Seoul").do(job_second_screen)

    print("\n✅ 스윙 눌림목 검색기 v4.2 시작")
    print("  ⏰ 09:00 → Heartbeat (봇 생존 신호)")
    print("  ⏰ 14:30 → 1차 스크리닝 (기준봉 탐색 + 눌림목 필터)")
    print("  ⏰ 15:20 → 2차 재검증 (KIS 실시간) + 텔레그램")
    print(f"  🔑 KIS 모드: {'실전투자' if _KIS_MODE == 'real' else '모의투자'}")
    print(f"  📊 시장 필터(KOSPI MA20): {'활성화' if STRATEGY['use_market_filter'] else '비활성화'}")
    print("  종료: Ctrl+C\n")

    send_startup_message()
    run_catchup()

    while True:
        schedule.run_pending()
        time.sleep(1)
