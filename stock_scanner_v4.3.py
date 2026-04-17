"""
주식 검색기 v4.3 (3~5일 스윙 전용)
────────────────────────────────────────
v4.2 → v4.3 변경사항 (Phase 2: 포지션 추적 고도화):
  [신규 1]   장중 TP/SL 실시간 감지 (job_monitor_positions)
             10:00 / 13:00 KIS 현재가 → TP·SL 도달 즉시 텔레그램 알림
  [신규 2]   트레일링 스탑: 최고가(HWM) 갱신 시 SL 자동 상향
             STRATEGY["trail_pct"] = 0.03 (HWM 대비 -3%)
             원래 SL보다 높아질 때만 적용 (SL 후퇴 없음)
  [신규 3]   실매매 이력 자동 기록 (trade_history.csv)
             TP·SL·만료 청산 시 ticker/날짜/진입가/청산가/PnL/사유 CSV 누적
  [신규 4]   섹터 집중도 경고: 동일 섹터 max_sector_count 초과 시 텔레그램 경고
             (진입 차단이 아닌 경고 — 판단은 사용자에게)
  [구조변경] positions.json: high_water_mark, sl_init, sector 필드 추가
             기존 포지션은 .get() 기본값으로 하위 호환
────────────────────────────────────────
v4.1 → v4.2 변경사항 (Phase 1: 신호 품질 향상):
  [신규 1]   공휴일 캘린더: holidays.KR() 적용
  [신규 2]   시장 컨디션 필터: KOSPI MA20 게이트
  [신규 3]   거래대금 필터 (기본 10억)
  [신규 4]   RSI 필터 (기준봉 RSI < 30 제외)
  [신규 5]   가격 위치 필터 (150일 레인지 상위 70%)
────────────────────────────────────────
"""

import csv
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

KST = ZoneInfo("Asia/Seoul")

# ==========================================
# 파일 경로 상수
# ==========================================
POSITIONS_FILE     = "positions.json"
TRADE_HISTORY_FILE = "trade_history.csv"

# ==========================================
# 공휴일 캘린더
# ==========================================
def is_market_closed(dt: datetime) -> bool:
    """주말 또는 한국 법정 공휴일이면 True"""
    d = dt.date() if hasattr(dt, "date") else dt
    if d.weekday() >= 5:
        return True
    return d in holidays.KR(years=d.year)


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
    # ── Phase 2 신규 파라미터 ────────────────────────────────
    "trail_pct":         0.03,   # 트레일링 스탑: HWM 대비 -3%
    "max_sector_count":  2,      # 동일 섹터 보유 한도 (초과 시 경고)
}


_first_screen_cache: list[dict] = []
_kis_token_cache: dict = {"token": None, "expires_at": 0}


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
# 텔레그램
# ==========================================
def send_telegram(text: str, topic_id: int | None = None) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_IDS:
        print("[WARN] 텔레그램 설정 없음 — .env 확인")
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
            json={"grant_type": "client_credentials",
                  "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET},
            timeout=10,
        )
        res.raise_for_status()
        data = res.json()
        _kis_token_cache["token"] = data["access_token"]
        _kis_token_cache["expires_at"] = now + int(data.get("expires_in", 86400))
        print(f"[KIS] 토큰 발급 완료 (유효: {int(data.get('expires_in',86400)/3600)}시간)")
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
    for attempt in range(1, retries + 1):
        try:
            return fdr.DataReader(ticker, start_date)
        except Exception as e:
            if attempt < retries:
                print(f"  [RETRY {attempt}/{retries}] {ticker}: {e}")
                time.sleep(delay)
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
        print(f"  [WARN] KOSPI 조회 실패: {e} — 시장 필터 통과로 처리")
        return True, f"KOSPI 조회 실패: {e}"


# ==========================================
# [Phase 2-신규 3] 매매 이력 기록 (CSV 누적)
# ==========================================
def record_trade_history(p: dict, exit_price: int, exit_reason: str) -> None:
    """
    포지션 청산 시 trade_history.csv 에 한 행 추가 (append-only)
    exit_reason: "TP" | "SL" | "TRAIL_SL" | "EXPIRE"
    """
    entry   = p.get("entry", 0)
    pnl_pct = round((exit_price - entry) / entry * 100, 2) if entry else 0.0
    row = {
        "ticker":      p["ticker"],
        "name":        p["name"],
        "sector":      p.get("sector", ""),
        "entry_date":  p.get("entry_date", ""),
        "exit_date":   datetime.now(KST).strftime("%Y-%m-%d"),
        "entry_price": entry,
        "exit_price":  exit_price,
        "pnl_pct":     pnl_pct,
        "exit_reason": exit_reason,
    }
    file_exists = os.path.exists(TRADE_HISTORY_FILE)
    try:
        with open(TRADE_HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(row.keys()))
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
        print(f"  [이력] {row['name']} ({exit_reason}) {pnl_pct:+.2f}% 기록 완료")
    except Exception as e:
        print(f"  [ERROR] 이력 기록 실패 ({p['ticker']}): {e}")


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
    if not os.path.exists(POSITIONS_FILE):
        return []
    try:
        with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_positions(positions: list[dict]) -> None:
    try:
        with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(positions, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[ERROR] 포지션 저장 실패: {e}")

def add_positions(stocks: list[dict]) -> None:
    """
    신규 포착 종목을 포지션 파일에 추가
    [Phase 2] high_water_mark, sl_init, sector 필드 추가
    """
    existing = load_positions()
    existing_tickers = {p["ticker"] for p in existing}
    now_str = datetime.now(KST).strftime("%Y-%m-%d")

    added = 0
    for s in stocks:
        if s["ticker"] not in existing_tickers:
            entry = s["entry"]
            sl    = s["sl"]
            existing.append({
                "ticker":           s["ticker"],
                "name":             s["name"],
                "entry":            entry,
                "tp":               s["tp"],
                "sl":               sl,
                "sl_init":          sl,          # 원래 손절가 (트레일링 후에도 보존)
                "high_water_mark":  entry,       # 최고가 추적 (트레일링 기준)
                "entry_date":       now_str,
                "sector":           s.get("sector", ""),
            })
            added += 1

    save_positions(existing)
    print(f"  포지션 기록: {added}개 추가 (누적 {len(existing)}개)")

def check_expired_positions() -> tuple[list[dict], list[dict]]:
    """5 거래일 경과 종목 분리 → (만료, 활성)"""
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
# [Phase 2-신규 1·2] 장중 포지션 모니터링
# ==========================================
def job_monitor_positions() -> None:
    """
    10:00 / 13:00 장중 실행
    ────────────────────────────────────────
    1) KIS 현재가 조회
    2) 최고가(HWM) 갱신 → 트레일링 SL 자동 상향
       trail_sl = HWM × (1 - trail_pct)  (원래 SL보다 높을 때만 반영)
    3) TP 도달: 즉시 알림 + 이력 기록 + 포지션 제거
    4) SL 도달 (원래 SL 또는 트레일링 SL): 즉시 알림 + 이력 기록 + 제거
    ────────────────────────────────────────
    Note: schedule 은 단일 스레드이므로 14:30/15:20 잡과 race condition 없음
    """
    if is_market_closed(datetime.now(KST)):
        return

    now       = datetime.now(KST)
    positions = load_positions()
    if not positions:
        print(f"[{now.strftime('%H:%M')}] 모니터링: 추적 포지션 없음")
        return

    print(f"\n{'='*50}")
    print(f"[{now.strftime('%H:%M')}] 장중 포지션 모니터링 ({len(positions)}개)")
    print(f"{'='*50}")

    remaining = []
    tp_hit    = []
    sl_hit    = []

    for p in positions:
        ticker = p["ticker"]
        name   = p["name"]
        entry  = p.get("entry", 0)
        tp     = p.get("tp", 0)
        sl     = p.get("sl", 0)
        hwm    = p.get("high_water_mark", entry)  # 기존 포지션 호환: 없으면 entry

        live = get_current_price(ticker)
        if not live:
            print(f"  [SKIP] {name} — API 조회 실패")
            remaining.append(p)
            time.sleep(0.15)
            continue

        cur = live["current"]

        # ── 트레일링 스탑 업데이트 ──────────────────────────────
        hwm_updated = False
        if cur > hwm:
            hwm = cur
            p["high_water_mark"] = hwm
            hwm_updated = True

        trail_sl = int(hwm * (1 - STRATEGY["trail_pct"]))
        if trail_sl > sl:
            # 원래 SL보다 높아진 경우에만 상향 (절대 내리지 않음)
            p["sl"] = trail_sl
            sl = trail_sl

        pnl_pct = (cur - entry) / entry * 100 if entry else 0

        # ── TP / SL 판단 ────────────────────────────────────────
        if cur >= tp:
            record_trade_history(p, cur, "TP")
            tp_hit.append({**p, "cur": cur, "pnl_pct": pnl_pct})
            print(f"  ✅ TP [{name}] {cur:,}원 ({pnl_pct:+.1f}%)")

        elif cur <= sl:
            sl_init = p.get("sl_init", sl)
            reason  = "TRAIL_SL" if sl > sl_init else "SL"
            record_trade_history(p, cur, reason)
            sl_hit.append({**p, "cur": cur, "pnl_pct": pnl_pct, "sl_reason": reason})
            print(f"  🔴 SL [{name}] {cur:,}원 ({pnl_pct:+.1f}%) — {reason}")

        else:
            trail_info = f" | HWM {hwm:,} → Trail SL {sl:,}" if hwm_updated else ""
            print(f"  🔵 [{name}] {cur:,}원 ({pnl_pct:+.1f}%){trail_info}")
            remaining.append(p)

        time.sleep(0.15)

    # 포지션 파일 업데이트 (TP/SL 제거 + HWM/SL 갱신 반영)
    save_positions(remaining)

    # ── 텔레그램 알림 ─────────────────────────────────────────
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
        print(f"  알림 발송: TP {len(tp_hit)}개 SL {len(sl_hit)}개")
    else:
        print(f"  TP/SL 달성 없음 (잔여 {len(remaining)}개)")


# ==========================================
# 스케줄 1 — 14:30 : 1차 스크리닝
# ==========================================
def job_first_screen() -> None:
    """
    FDR 종가 데이터로 눌림목 후보 1차 추출
    [Phase 2] 섹터 정보를 candidate에 추가
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

        # ── 시장 컨디션 게이트 ──────────────────────────────────
        if STRATEGY["use_market_filter"]:
            market_ok, market_status = get_kospi_condition(start_date)
            print(f"  시장 컨디션: {market_status}")
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

        # [Phase 2] 섹터 컬럼 탐색 (FDR 버전마다 이름 다름)
        sec_col = next((c for c in ["Sector", "Industry", "업종", "Ind1", "IndName"] if c in krx.columns), None)
        if sec_col:
            print(f"  섹터 컬럼 감지: '{sec_col}'")
        else:
            print("  섹터 컬럼 없음 — sector 필드 빈 값으로 저장")

        candidates = []
        total = len(krx)
        filter_counts = {
            "데이터부족": 0, "거래대금": 0, "기준봉없음": 0,
            "거래량": 0, "지지": 0, "캔들": 0, "MA": 0, "RSI": 0, "가격위치": 0,
        }

        for i, (_, row) in enumerate(krx.iterrows(), 1):
            ticker  = row["Code"]
            name    = row["Name"]
            sector  = str(row[sec_col]).strip() if sec_col and pd.notna(row[sec_col]) else ""

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

                today_body  = today["Close"] - today["Open"]
                today_range = today["High"] - today["Low"]
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
                    candidates.append({
                        "name":         name,
                        "ticker":       ticker,
                        "sector":       sector,   # [Phase 2]
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
    [Phase 2] 섹터 집중도 경고 추가
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

        if not live:
            sl_price    = int(stock["bo_open"] * STRATEGY["sl_buffer"])
            entry_price = stock["fdr_close"]
            sl_pct      = (entry_price - sl_price) / entry_price
            if sl_pct > STRATEGY["sl_limit"]:
                print(f"  [탈락-FB] {stock['name']} API 실패 + 손절폭 과대 ({sl_pct*100:.1f}%)")
                continue
            verified.append({
                **stock,
                "entry": entry_price,
                "tp":    int(entry_price * (1 + STRATEGY["tp_pct"])),
                "sl":    sl_price,
                "live_vol": None, "live_verified": False,
            })
            print(f"  [통과-FB] {stock['name']} (종가 기준, API 미확인)")
            continue

        cur        = live["current"]
        live_open  = live["open"]
        live_high  = live["high"]
        live_low   = live["low"]
        live_vol   = live["volume"]
        live_range = live_high - live_low

        ok_vol_dry = live_vol <= stock["vol20_before"] * (STRATEGY["pullback_vol"] + 0.2)
        ok_support = cur >= stock["bo_open"]
        ok_shape   = (abs(cur - live_open) / live_range <= STRATEGY["pullback_shape"] + 0.10) if live_range > 0 else False

        if ok_vol_dry and ok_support and ok_shape:
            sl_price = int(stock["bo_open"] * STRATEGY["sl_buffer"])
            tp_price = int(cur * (1 + STRATEGY["tp_pct"]))
            sl_pct   = (cur - sl_price) / cur
            if sl_pct > 0.10:
                print(f"  [탈락] {stock['name']} 손절폭 과대 ({sl_pct*100:.1f}%)")
                continue
            verified.append({
                **stock,
                "entry": cur, "tp": tp_price, "sl": sl_price,
                "sl_pct": round(sl_pct * 100, 1),
                "live_vol": live_vol, "live_verified": True,
            })
            print(f"  [통과] {stock['name']} (현재가={cur:,} | 손절폭={sl_pct*100:.1f}%)")
        else:
            print(f"  [탈락] {stock['name']} vol={ok_vol_dry} support={ok_support} shape={ok_shape}")

        time.sleep(0.1)

    print(f"  최종 통과: {len(verified)}개 / {len(candidates)}개")

    if not verified:
        send_telegram(
            f"📉 *{datetime.now(KST).strftime('%Y-%m-%d')} 스윙 눌림목 없음*\n"
            f"1차 후보 {len(candidates)}개 → 실시간 재검증 전원 탈락"
        )
        return

    # ── [Phase 2-신규 4] 섹터 집중도 경고 ──────────────────────
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
        print(f"  [섹터 경고] {sector_warnings}")

    # ── 텔레그램 발송 ────────────────────────────────────────
    date_str = datetime.now(KST).strftime("%m/%d %H:%M")
    msg = f"🚀 *스윙 눌림목 타점 포착!* ({date_str})\n\n"
    for i, s in enumerate(verified, 1):
        icon    = "✅" if s.get("live_verified") else "⚠️"
        rsi_str = f" | RSI {s['bo_rsi']}" if s.get("bo_rsi") else ""
        sec_str = f" [{s['sector']}]" if s.get("sector") else ""
        msg += f"*{i}. {s['name']}* ({s['ticker']}){sec_str} {icon}\n"
        msg += f"  ▪️ 기준봉: {s['bo_date']} (몸통 +{s['bo_body_pct']}%{rsi_str})\n"
        msg += f"  ▪️ 목표 보유: {STRATEGY['max_hold_days']}일 이내\n"
        msg += f"  ▪️ 진입가: {s['entry']:,}원\n"
        msg += f"  ▪️ 익절(TP): {s['tp']:,}원 (+{int(STRATEGY['tp_pct']*100)}%)\n"
        sl_pct_str = f"{s['sl_pct']}%" if s.get("sl_pct") else "-"
        msg += f"  ▪️ 손절(SL): {s['sl']:,}원 (-{sl_pct_str})\n"
        if s.get("live_verified") and s.get("live_vol"):
            msg += f"  ▪️ 실시간 거래량: {s['live_vol']:,}\n"
        msg += "\n"
    msg += "✅ KIS 실시간 재검증  ⚠️ 종가 기준(API 미확인)"
    send_telegram(msg)
    print("  텔레그램 발송 완료")
    add_positions(verified)


# ==========================================
# Heartbeat — 매일 09:00
# ==========================================
def _build_position_line(p: dict) -> str:
    entry  = p.get("entry", 0)
    tp     = p.get("tp", 0)
    sl     = p.get("sl", 0)
    ticker = p["ticker"]
    name   = p["name"]
    days   = p.get("elapsed_days", 5)
    edate  = p.get("entry_date", "-")
    hwm    = p.get("high_water_mark", entry)
    sl_init = p.get("sl_init", sl)

    live = get_current_price(ticker)
    if live:
        cur     = live["current"]
        pnl_pct = (cur - entry) / entry * 100 if entry else 0
        trail_tag = f" | Trail SL {sl:,}" if sl > sl_init else ""

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
            f"  TP {tp:,}원 / SL {sl:,}원{trail_tag}\n"
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
    매일 09:00:
    1) 봇 생존 신호
    2) 5 거래일 경과 포지션 → 수익률 조회 + trade_history 기록 + 정리 알람
    """
    if is_market_closed(datetime.now(KST)):
        return
    now = datetime.now(KST)

    expired, active = check_expired_positions()

    # [Phase 2] 만료 포지션: 현재가로 이력 기록 후 제거
    if expired:
        for p in expired:
            live = get_current_price(p["ticker"])
            exit_price = live["current"] if live else p.get("entry", 0)
            record_trade_history(p, exit_price, "EXPIRE")
            time.sleep(0.2)
        save_positions(active)
        print(f"  만료 포지션 {len(expired)}개 이력 기록 후 제거 → 잔여 {len(active)}개")

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
        for p in expired:
            msg += _build_position_line(p)
            time.sleep(0.2)
        msg += "\n_이력이 trade\\_history.csv에 저장되었습니다_"

    send_telegram(msg)
    print(f"[{now.strftime('%H:%M')}] Heartbeat 발송 완료 (만료 {len(expired)}개)")


# ==========================================
# 시작 텔레그램 알림
# ==========================================
def send_startup_message() -> None:
    now = datetime.now(KST)
    kis_mode_str    = "🔴 실전투자" if _KIS_MODE == "real" else "🟡 모의투자"
    mf_str          = "활성화" if STRATEGY["use_market_filter"] else "비활성화"
    send_telegram(
        f"✅ *스윙 눌림목 검색기 v4.3 시작* (채팅방 {len(TELEGRAM_CHAT_IDS)}개)\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🕐 시작 시각: {now.strftime('%Y-%m-%d %H:%M')}\n"
        f"🔑 KIS 모드: {kis_mode_str}\n"
        f"📊 시장 필터(KOSPI MA20): {mf_str}\n"
        f"🎯 트레일링 스탑: HWM -{int(STRATEGY['trail_pct']*100)}%\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⏰ 09:00 → Heartbeat + 만료 포지션 정리\n"
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
    if is_market_closed(now):
        print("  휴장일 — catch-up 없음\n")
        return

    hm = now.hour * 60 + now.minute
    T1 = 14 * 60 + 30
    T2 = 15 * 60 + 20
    T3 = 15 * 60 + 30

    if hm < T1:
        print("  14:30 이전 시작 — 스케줄 대기\n")
    elif T1 <= hm < T2:
        print("  14:30~15:20 사이 시작 → 1차 즉시 실행\n")
        send_telegram("⚡ 늦은 시작 감지 → 1차 스크리닝 즉시 시작합니다")
        job_first_screen()
    elif T2 <= hm < T3:
        print("  15:20~15:30 사이 시작 → 1차 + 2차 즉시 순차 실행\n")
        send_telegram("⚡ 늦은 시작 감지 → 1차 + 2차 즉시 순차 실행합니다")
        job_first_screen()
        job_second_screen()
    else:
        print("  15:30 이후 — 당일 skip\n")
        send_telegram(
            f"⏭ *당일 스크리닝 skip*\n"
            f"시작 시각 {now.strftime('%H:%M')} — 장 마감 이후\n"
            f"내일 14:30부터 정상 스케줄 실행"
        )


# ==========================================
# 스케줄러 등록 & 실행
# ==========================================
if __name__ == "__main__":
    schedule.every().day.at("09:00", "Asia/Seoul").do(job_heartbeat)
    schedule.every().day.at("10:00", "Asia/Seoul").do(job_monitor_positions)  # [Phase 2]
    schedule.every().day.at("13:00", "Asia/Seoul").do(job_monitor_positions)  # [Phase 2]
    schedule.every().day.at("14:30", "Asia/Seoul").do(job_first_screen)
    schedule.every().day.at("15:20", "Asia/Seoul").do(job_second_screen)

    print("\n✅ 스윙 눌림목 검색기 v4.3 시작")
    print("  ⏰ 09:00 → Heartbeat + 만료 포지션 이력 기록")
    print("  ⏰ 10:00 → 장중 TP/SL 모니터링 (트레일링 스탑)")
    print("  ⏰ 13:00 → 장중 TP/SL 모니터링 (트레일링 스탑)")
    print("  ⏰ 14:30 → 1차 스크리닝")
    print("  ⏰ 15:20 → 2차 재검증 + 텔레그램")
    print(f"  🔑 KIS 모드: {'실전투자' if _KIS_MODE == 'real' else '모의투자'}")
    print(f"  🎯 트레일링 스탑: HWM -{int(STRATEGY['trail_pct']*100)}%")
    print("  종료: Ctrl+C\n")

    send_startup_message()
    run_catchup()

    while True:
        schedule.run_pending()
        time.sleep(1)
