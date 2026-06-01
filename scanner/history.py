"""매매 이력 파일 I/O — trade_history.csv 쓰기·읽기."""
import csv
import os
from datetime import datetime

import pandas as pd

from scanner.config import TRADE_HISTORY_FILE
from scanner.state import _HISTORY_FLOCK
from scanner.calendar import KST
from scanner.logger import log


def record_trade_history(p: dict, exit_price: int, exit_reason: str) -> None:
    entry   = p.get("entry", 0)
    pnl_pct = round((exit_price - entry) / entry * 100, 2) if entry else 0.0
    row = {
        "ticker":           p["ticker"],
        "name":             p["name"],
        "sector":           p.get("sector", ""),
        "entry_date":       p.get("entry_date", ""),
        "exit_date":        datetime.now(KST).strftime("%Y-%m-%d"),
        "entry_price":      entry,
        "exit_price":       exit_price,
        "quantity":         p.get("quantity", 0),
        "pnl_pct":          pnl_pct,
        "exit_reason":      exit_reason,
        "signal_score":     p.get("signal_score", ""),
        "bo_lookback":      p.get("bo_lookback", ""),
        "pullback_depth":   p.get("pullback_depth", ""),
        "auto_traded":      p.get("auto_traded", False),
        "post_expire_pnl":  "",
    }
    file_exists = os.path.exists(TRADE_HISTORY_FILE)
    try:
        with _HISTORY_FLOCK:
            with open(TRADE_HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(row.keys()))
                if not file_exists:
                    writer.writeheader()
                writer.writerow(row)
        log.info(f"  [이력] {row['name']} ({exit_reason}) {pnl_pct:+.2f}% 기록 완료")
    except Exception as e:
        log.error(f"  이력 기록 실패 ({p['ticker']}): {e}")


def load_trade_history() -> pd.DataFrame:
    if not os.path.exists(TRADE_HISTORY_FILE):
        return pd.DataFrame()
    try:
        df = pd.read_csv(TRADE_HISTORY_FILE, encoding="utf-8",
                         parse_dates=["exit_date", "entry_date"])
        return df if not df.empty else pd.DataFrame()
    except Exception as e:
        log.warning(f"  이력 파일 로드 실패: {e}")
        return pd.DataFrame()
