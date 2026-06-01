"""포지션 파일 I/O — positions.json 읽기·쓰기·추가·만료 체크."""
import json
import os
from datetime import datetime

from scanner.config import POSITIONS_FILE, STRATEGY
from scanner.state import _POSITIONS_FLOCK
from scanner.calendar import KST, count_weekdays
from scanner.notify import send_telegram
from scanner.logger import log


def load_positions() -> list[dict]:
    with _POSITIONS_FLOCK:
        if not os.path.exists(POSITIONS_FILE):
            return []
        try:
            with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log.error(f"포지션 로드 실패 (파일 손상 의심): {e}")
            return []


def save_positions(positions: list[dict]) -> None:
    with _POSITIONS_FLOCK:
        try:
            with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
                json.dump(positions, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.error(f"포지션 저장 실패: {e}")


def add_positions(stocks: list[dict]) -> None:
    """신규 포착 종목을 포지션 파일에 추가."""
    existing         = load_positions()
    existing_tickers = {p["ticker"] for p in existing}
    now_str  = datetime.now(KST).strftime("%Y-%m-%d")
    cap      = STRATEGY["max_positions"]
    slots    = cap - len(existing)

    candidates = [s for s in stocks if s["ticker"] not in existing_tickers]
    candidates.sort(key=lambda s: s.get("signal_score", 0), reverse=True)
    to_add  = candidates[:max(slots, 0)]
    skipped = candidates[max(slots, 0):]

    for s in to_add:
        entry   = s["entry"]
        sl      = s["sl"]
        tp1_pct = STRATEGY.get("tp1_pct", 0)
        existing.append({
            "ticker":          s["ticker"],
            "name":            s["name"],
            "entry":           entry,
            "tp":              s["tp"],
            "tp1":             int(entry * (1 + tp1_pct)) if tp1_pct else 0,
            "tp1_taken":       False,
            "sl":              sl,
            "sl_init":         sl,
            "high_water_mark": entry,
            "entry_date":      now_str,
            "sector":          s.get("sector", ""),
            "signal_score":    s.get("signal_score"),
            "bo_lookback":     s.get("bo_lookback"),
            "pullback_depth":  s.get("pullback_depth"),
            "quantity":        s.get("quantity", 0),
            "auto_traded":     s.get("auto_traded", False),
            "sizing_factor":   s.get("sizing_factor", 1.0),
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
            elapsed  = count_weekdays(entry_dt, today)
            p["elapsed_days"] = elapsed
            if elapsed >= STRATEGY["max_hold_days"]:
                expired.append(p)
            else:
                active.append(p)
        except Exception:
            active.append(p)
    return expired, active
