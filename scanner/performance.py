"""성과 집계 — PF·승률·드리프트 감지·주간 리포트."""
from datetime import timedelta

import pandas as pd

from scanner.config import STRATEGY
from scanner.notify import send_telegram
from scanner.history import load_trade_history
from scanner.logger import log


def calc_performance_stats(df: pd.DataFrame) -> dict:
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
    if stats["total"] == 0:
        return (
            f"📊 *주간 성과 리포트* ({week_label})\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"해당 주 청산 거래 없음"
        )
    REASON_LABELS = {
        "TP":       "TP 익절",
        "TP1":      "TP1 절반익절",
        "SL":       "SL 손절",
        "TRAIL_SL": "트레일 손절",
        "HARD_SL":  "하드스탑",
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
        return None
    recent = weekly_wr.iloc[-n_weeks:]
    if not (recent < threshold).all():
        return None
    wr_strs   = " / ".join(f"{r*100:.0f}%" for r in recent)
    weeks_str = " / ".join(str(w) for w in recent.index)
    return (
        f"🚨 *전략 드리프트 경고*\n"
        f"최근 {n_weeks}주 연속 승률 {threshold*100:.0f}% 미달\n"
        f"주간 승률: {wr_strs}\n"
        f"({weeks_str})\n"
        f"STRATEGY 파라미터 재검토 권장"
    )


def send_weekly_report(now) -> None:
    df          = load_trade_history()
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
    stats      = calc_performance_stats(week_df)
    report_msg = format_weekly_report(stats, week_label)
    send_telegram(report_msg)
    log.info(f"  [주간 리포트] {week_label}: {stats['total']}건 | 승률 {stats['win_rate']*100:.0f}%")
    drift_msg = check_winrate_drift(df)
    if drift_msg:
        send_telegram(drift_msg)
        log.info("  [드리프트 경고] 발송 완료")
