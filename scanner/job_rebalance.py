"""kr_gem 월간 리밸런싱 잡 — 목표비중 계산 → 실제 계좌 비교 → 매도/매수 실행."""
import json
import os
import time
from datetime import datetime

from scanner.strategy_rebalance import compute_target_weights, RISK_ASSETS, SAFE_ASSET
from scanner.kis import get_account_holdings, get_order_possible_cash, get_current_price, place_order
from scanner.positions import load_positions, save_positions
from scanner.config import REBALANCE_LOG_FILE
from scanner.state import _POSITIONS_FLOCK
from scanner.calendar import KST
from scanner.notify import send_telegram
from scanner.logger import log

_UNIVERSE = set(RISK_ASSETS) | {SAFE_ASSET}


def _current_state() -> tuple[dict[str, dict], int]:
    """kr_gem 유니버스에 속한 보유 종목과 가용 현금 조회. 그 외(눌림목) 보유 종목은 무시."""
    holdings = {h["ticker"]: h for h in get_account_holdings() if h["ticker"] in _UNIVERSE}
    cash = get_order_possible_cash("", 0) or 0
    return holdings, cash


def _total_value(holdings: dict[str, dict], cash: int) -> int:
    total = cash
    for tk, h in holdings.items():
        price_info = get_current_price(tk)
        price = price_info["current"] if price_info else h["avg_price"]
        total += price * h["qty"]
    return total


def preview_rebalance() -> dict:
    """주문 없이 목표 비중·현재 비중·필요 주문 수량만 계산."""
    targets = compute_target_weights()
    holdings, cash = _current_state()
    total = _total_value(holdings, cash)

    rows = []
    target_tickers = set()
    for t in targets:
        tk, price = t["ticker"], t["price"]
        target_tickers.add(tk)
        cur_qty       = holdings.get(tk, {}).get("qty", 0)
        target_dollar = total * t["weight"] / 100.0
        target_qty    = int(target_dollar // price) if price > 0 else 0
        rows.append({
            **t, "current_qty": cur_qty, "target_qty": target_qty,
            "diff_qty": target_qty - cur_qty,
        })

    # 목표비중에서 빠졌지만 여전히 보유 중인 kr_gem 종목 → 전량 매도 대상
    for tk, h in holdings.items():
        if tk not in target_tickers:
            rows.append({
                "ticker": tk, "name": h["name"], "weight": 0.0, "price": 0.0,
                "current_qty": h["qty"], "target_qty": 0, "diff_qty": -h["qty"],
            })

    return {
        "total_value": total, "cash": cash, "rows": rows,
        "computed_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
    }


def execute_rebalance() -> dict:
    """실제 매도→매수 주문 실행 + positions.json 갱신 + 이력 기록 + 텔레그램 리포트."""
    plan  = preview_rebalance()
    sells = [r for r in plan["rows"] if r["diff_qty"] < 0]
    buys  = [r for r in plan["rows"] if r["diff_qty"] > 0]

    # 매도 실현손익 계산용: 교체 전 kr_gem 포지션의 평단
    old_entry = {p["ticker"]: p.get("entry", 0)
                 for p in load_positions() if p.get("strategy") == "kr_gem"}

    results = []
    for r in sells:
        res = place_order(r["ticker"], "sell", -r["diff_qty"], r["name"])
        live  = get_current_price(r["ticker"])
        price = live["current"] if live else (r["price"] or old_entry.get(r["ticker"], 0))
        entry = old_entry.get(r["ticker"], 0)
        pnl   = round((price - entry) / entry * 100, 2) if entry else None
        results.append({"ticker": r["ticker"], "name": r["name"], "side": "sell",
                        "qty": -r["diff_qty"], "price": price, "pnl_pct": pnl, **res})

    if sells:
        time.sleep(3)  # 매도 체결 대기 — 현금 확보 후 매수

    for r in buys:
        res = place_order(r["ticker"], "buy", r["diff_qty"], r["name"])
        live  = get_current_price(r["ticker"])
        price = live["current"] if live else r["price"]
        results.append({"ticker": r["ticker"], "name": r["name"], "side": "buy",
                        "qty": r["diff_qty"], "price": price, "pnl_pct": None, **res})

    _save_rebalance_positions(plan)
    _record_rebalance_log(plan, results)

    ok = sum(1 for r in results if r["success"])
    lines = "\n".join(
        f"  {'✅' if r['success'] else '❌'} {('매수' if r['side']=='buy' else '매도')} "
        f"{r['name']}({r['ticker']}) {r['qty']}주" + (f" — {r['error']}" if not r["success"] else "")
        for r in results
    )
    send_telegram(
        f"🔄 *kr_gem 월간 리밸런싱 실행 완료* ({ok}/{len(results)} 성공)\n"
        f"총자산: {plan['total_value']:,}원 | 현금: {plan['cash']:,}원\n"
        f"{lines or '  (주문 변경 없음)'}"
    )
    log.info(f"[리밸런싱] {ok}/{len(results)} 주문 성공 (총자산 {plan['total_value']:,}원)")
    return {"plan": plan, "orders": results}


def _record_rebalance_log(plan: dict, results: list[dict]) -> None:
    """리밸런싱 이벤트 1건을 rebalance_log.json에 append (성공 주문만 기록)."""
    holdings = [{"ticker": r["ticker"], "name": r["name"], "weight": r["weight"],
                 "qty": r["target_qty"], "value": int(r["target_qty"] * r["price"])}
                for r in plan["rows"] if r["target_qty"] > 0]
    orders = [{"ticker": r["ticker"], "name": r["name"], "side": r["side"],
               "qty": r["qty"], "price": r.get("price", 0), "pnl_pct": r.get("pnl_pct")}
              for r in results if r.get("success")]
    event = {
        "ts":          datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        "total_value": plan["total_value"],
        "cash":        plan["cash"],
        "holdings":    holdings,
        "orders":      orders,
    }
    with _POSITIONS_FLOCK:
        try:
            events = []
            if os.path.exists(REBALANCE_LOG_FILE):
                with open(REBALANCE_LOG_FILE, "r", encoding="utf-8") as f:
                    events = json.load(f)
            events.append(event)
            with open(REBALANCE_LOG_FILE, "w", encoding="utf-8") as f:
                json.dump(events, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.error(f"[리밸런싱] 이력 기록 실패: {e}")


def _save_rebalance_positions(plan: dict) -> None:
    """positions.json에서 strategy=='kr_gem' 레코드를 최신 목표 보유로 교체."""
    now_str  = datetime.now(KST).strftime("%Y-%m-%d")
    existing = [p for p in load_positions() if p.get("strategy") != "kr_gem"]
    for r in plan["rows"]:
        if r["target_qty"] <= 0:
            continue
        existing.append({
            "ticker":          r["ticker"],
            "name":            r["name"],
            "entry":           r["price"],
            "tp":              0,
            "sl":              0,
            "sl_init":         0,
            "high_water_mark": r["price"],
            "entry_date":      now_str,
            "sector":          "ETF",
            "signal_score":    None,
            "bo_lookback":     None,
            "pullback_depth":  None,
            "quantity":        r["target_qty"],
            "auto_traded":     True,
            "strategy":        "kr_gem",
            "target_weight":   r["weight"],
        })
    save_positions(existing)
