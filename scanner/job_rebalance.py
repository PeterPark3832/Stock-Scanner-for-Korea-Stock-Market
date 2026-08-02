"""kr_gem 월간 리밸런싱 잡 — 목표비중 계산 → 실제 계좌 비교 → 매도/매수 실행."""
import json
import os
import time
from datetime import datetime

from scanner.strategy_rebalance import compute_target_weights, get_strategy, MANAGED_UNIVERSE
from scanner.kis import get_account_holdings, get_order_possible_cash, get_current_price, place_order
from scanner.positions import load_positions, save_positions
from scanner.config import (
    REBALANCE_LOG_FILE, EQUITY_SNAPSHOT_FILE, STRATEGY_KEY, REBALANCE_CASH_BUFFER,
)
from scanner.state import _POSITIONS_FLOCK
from scanner.calendar import KST
from scanner.notify import send_telegram
from scanner.logger import log


def _current_state() -> tuple[dict[str, dict], int]:
    """봇 관리 유니버스(5전략 합집합)에 속한 보유 종목과 가용 현금 조회.
    전략 전환 시 옛 전략 종목도 잡혀 목표=0으로 매도된다. 그 외(눌림목·수동 종목)는 무시."""
    holdings = {h["ticker"]: h for h in get_account_holdings() if h["ticker"] in MANAGED_UNIVERSE}
    cash = get_order_possible_cash("", 0) or 0
    return holdings, cash


def _live_prices(tickers) -> dict[str, int]:
    """티커별 실시간 현재가를 한 번씩만 조회 (중복 KIS 호출 방지)."""
    out: dict[str, int] = {}
    for tk in dict.fromkeys(tickers):
        info = get_current_price(tk)
        if info and info.get("current"):
            out[tk] = info["current"]
    return out


def _total_value(holdings: dict[str, dict], cash: int,
                 prices: dict[str, int] | None = None) -> int:
    total = cash
    for tk, h in holdings.items():
        price = (prices or {}).get(tk)
        if not price:
            info = get_current_price(tk)
            price = info["current"] if info else h["avg_price"]
        total += price * h["qty"]
    return total


def _sweep_leftover_cash(rows: list[dict], budget: float) -> None:
    """정수 주수로 남은 잔돈을 가장 미달된 종목에 추가 배정한다(제자리 수정).

    각 슬롯을 내림하면 자투리 현금이 매달 놀게 되고, 소액 계좌일수록 비중이
    목표에서 크게 벗어난다. 예산(=총자산×버퍼) 안에서만 채우므로 주문 실패 위험은 없다.
    """
    spent = sum(r["target_qty"] * r["price"] for r in rows if r["price"] > 0)
    leftover = budget - spent
    for _ in range(100):                      # 안전 상한
        cands = [r for r in rows if r["price"] > 0 and r["price"] <= leftover]
        if not cands:
            break
        # 목표금액 대비 가장 덜 채워진 슬롯부터. 아직 미달인 슬롯이 있으면 채운다
        # (유휴 현금 드래그가 소폭 비중 초과보다 비용이 크다).
        deficit = lambda r: budget * r["weight"] / 100.0 - r["target_qty"] * r["price"]
        pick = max(cands, key=deficit)
        if deficit(pick) <= 0:
            break                             # 모든 슬롯이 목표 충족 — 초과 매수 금지
        pick["target_qty"] += 1
        pick["diff_qty"] = pick["target_qty"] - pick["current_qty"]
        leftover -= pick["price"]


def preview_rebalance() -> dict:
    """주문 없이 목표 비중·현재 비중·필요 주문 수량만 계산 (활성 전략 기준)."""
    targets = compute_target_weights(STRATEGY_KEY)
    holdings, cash = _current_state()
    # 평가금액과 주문수량을 같은 가격 기준으로 맞춘다. FDR 종가(전일)로 수량을 잡으면
    # 갭 발생 시 비중이 어긋나고 매수가 실패한다 → 실시간가 우선, 실패 시 FDR 종가.
    live  = _live_prices([t["ticker"] for t in targets] + list(holdings))
    total = _total_value(holdings, cash, live)

    # 데이터 장애 방어 — 목표를 못 구했거나 가격이 없으면 '실행 불가' 계획을 돌려준다.
    # 빈 목표로 그대로 진행하면 보유 전량이 매도 대상이 되어, 전략 신호가 아니라
    # 시세 API 장애 때문에 시장을 이탈하게 된다.
    unpriced = [t["ticker"] for t in targets if not (live.get(t["ticker"]) or t.get("price", 0))]
    if not targets or unpriced:
        reason = ("목표 비중 산출 실패 (가격 데이터 부족)" if not targets
                  else f"체결가 조회 실패: {', '.join(unpriced)}")
        log.error(f"[리밸런싱] 실행 불가 — {reason}")
        return {"total_value": total, "cash": cash, "rows": [],
                "actionable": False, "reason": reason,
                "computed_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")}

    rows = []
    target_tickers = set()
    for t in targets:
        tk = t["ticker"]
        price = live.get(tk) or t["price"]
        target_tickers.add(tk)
        cur_qty       = holdings.get(tk, {}).get("qty", 0)
        # 버퍼 적용: 조회가와 실제 체결가(시장가) 사이 변동으로 주문가능금액을 넘겨
        # 매수 전체가 실패하는 것을 막는다.
        target_dollar = total * REBALANCE_CASH_BUFFER * t["weight"] / 100.0
        target_qty    = int(target_dollar // price) if price > 0 else 0
        rows.append({
            **t, "price": price, "current_qty": cur_qty, "target_qty": target_qty,
            "diff_qty": target_qty - cur_qty,
        })

    _sweep_leftover_cash(rows, total * REBALANCE_CASH_BUFFER)

    # 목표비중에서 빠졌지만 여전히 보유 중인 관리 종목 → 전량 매도 대상
    for tk, h in holdings.items():
        if tk not in target_tickers:
            rows.append({
                "ticker": tk, "name": h["name"], "weight": 0.0,
                "price": live.get(tk) or h.get("avg_price", 0),
                "current_qty": h["qty"], "target_qty": 0, "diff_qty": -h["qty"],
            })

    return {
        "total_value": total, "cash": cash, "rows": rows,
        "actionable": True, "reason": "",
        "computed_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
    }


def execute_rebalance() -> dict:
    """실제 매도→매수 주문 실행 + positions.json 갱신 + 이력 기록 + 텔레그램 리포트."""
    plan = preview_rebalance()
    if not plan.get("actionable", True):
        # 주문을 내지 않고 현 보유를 유지한다. 다음 달(또는 수동 실행) 때 재시도.
        send_telegram(
            "⚠️ *리밸런싱 중단 — 주문 없음*\n"
            f"사유: {plan.get('reason', '알 수 없음')}\n"
            "현재 보유를 그대로 유지했습니다. 데이터 복구 후 대시보드에서 수동 실행하세요."
        )
        log.error(f"[리밸런싱] 중단 — {plan.get('reason')}")
        return {"plan": plan, "orders": [], "aborted": True}

    sells = [r for r in plan["rows"] if r["diff_qty"] < 0]
    buys  = [r for r in plan["rows"] if r["diff_qty"] > 0]

    # 매도 실현손익 계산용: 교체 전 리밸런싱 관리 포지션(전략 무관)의 평단
    from scanner.strategy_rebalance import STRATEGIES
    old_entry = {p["ticker"]: p.get("entry", 0)
                 for p in load_positions() if p.get("strategy") in STRATEGIES}

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

    _reconcile_positions(plan, results)
    _record_rebalance_log(plan, results)

    ok = sum(1 for r in results if r["success"])
    lines = "\n".join(
        f"  {'✅' if r['success'] else '❌'} {('매수' if r['side']=='buy' else '매도')} "
        f"{r['name']}({r['ticker']}) {r['qty']}주" + (f" — {r['error']}" if not r["success"] else "")
        for r in results
    )
    strat_name = get_strategy(STRATEGY_KEY)["name"]
    send_telegram(
        f"🔄 *{strat_name} 리밸런싱 실행 완료* ({ok}/{len(results)} 성공)\n"
        f"총자산: {plan['total_value']:,}원 | 현금: {plan['cash']:,}원\n"
        f"{lines or '  (주문 변경 없음)'}"
    )
    log.info(f"[리밸런싱] {ok}/{len(results)} 주문 성공 (총자산 {plan['total_value']:,}원)")
    return {"plan": plan, "orders": results}


def snapshot_equity() -> dict | None:
    """오늘 포트폴리오 평가금액을 equity_snapshots.json에 기록 (하루 1건, 날짜 dedupe).

    KIS 잔고 API가 간헐적 500을 뱉으면 보유가 빈 리스트로 와서 equity=0 스냅샷이
    남고 그래프가 바닥으로 꺾인다. positions.json 기준 보유가 있는데 조회 결과가
    비어 있으면 조회 실패로 보고 기록을 건너뛴다."""
    from scanner.strategy_rebalance import STRATEGIES
    holdings, cash = _current_state()

    expected = [p for p in load_positions()
                if p.get("strategy") in STRATEGIES and p.get("quantity", 0) > 0]
    if expected and not holdings:
        log.error(f"[스냅샷] 보유 {len(expected)}종목 예상되나 KIS 잔고조회 결과 없음 "
                  f"— 조회 실패로 판단, 스냅샷 기록 건너뜀")
        send_telegram(
            "⚠️ *평가금액 스냅샷 건너뜀*\n"
            f"positions.json 상 {len(expected)}종목 보유 중이나 KIS 잔고조회가 비어 있습니다.\n"
            "API 일시 오류로 판단해 잘못된 0원 기록을 방지했습니다."
        )
        return None

    total  = _total_value(holdings, cash)
    equity = total - cash
    today  = datetime.now(KST).strftime("%Y-%m-%d")
    snap = {"date": today, "total": total, "equity": equity, "cash": cash}
    with _POSITIONS_FLOCK:
        try:
            arr = []
            if os.path.exists(EQUITY_SNAPSHOT_FILE):
                with open(EQUITY_SNAPSHOT_FILE, "r", encoding="utf-8") as f:
                    arr = json.load(f)
            arr = [s for s in arr if s.get("date") != today]  # 오늘분 교체
            arr.append(snap)
            arr.sort(key=lambda s: s["date"])
            with open(EQUITY_SNAPSHOT_FILE, "w", encoding="utf-8") as f:
                json.dump(arr, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.error(f"[스냅샷] 평가금액 기록 실패: {e}")
            return None
    return snap


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


def _reconcile_positions(plan: dict, results: list[dict]) -> None:
    """주문 후 실제 체결 결과로 positions.json을 맞춘다.

    KIS 잔고를 다시 조회해 **실제 보유 수량과 매입평균가**로 기록한다. 목표(plan)를
    그대로 저장하면 주문이 실패해도 보유한 것처럼 남아, 다음 달 주문 수량과 실현손익이
    모두 어긋난다. 잔고 재조회가 실패하면 성공한 주문만 반영해 보수적으로 기록한다.
    """
    from scanner.strategy_rebalance import STRATEGIES, MANAGED_UNIVERSE
    now_str  = datetime.now(KST).strftime("%Y-%m-%d")
    keep     = [p for p in load_positions() if p.get("strategy") not in STRATEGIES]
    weights  = {r["ticker"]: r.get("weight", 0.0) for r in plan["rows"]}
    prev     = {p["ticker"]: p for p in load_positions() if p.get("strategy") in STRATEGIES}

    live = {h["ticker"]: h for h in get_account_holdings() if h["ticker"] in MANAGED_UNIVERSE}
    if live:
        for tk, h in live.items():
            keep.append(_position_record(
                tk, h.get("name") or tk, h.get("avg_price", 0), h.get("qty", 0),
                weights.get(tk, 0.0),
                prev.get(tk, {}).get("entry_date", now_str) if tk in prev else now_str,
            ))
        save_positions(keep)
        log.info(f"[리밸런싱] 실제 잔고로 포지션 동기화 — {len(live)}종목")
        return

    # 잔고 재조회 실패 → 성공한 주문만 반영 (실패 주문을 보유로 남기지 않는다)
    filled: dict[str, int] = {}
    for r in results:
        if not r.get("success"):
            continue
        d = r["qty"] if r["side"] == "buy" else -r["qty"]
        filled[r["ticker"]] = filled.get(r["ticker"], 0) + d
    fill_price = {r["ticker"]: r.get("price", 0) for r in results if r.get("success")}
    log.warning("[리밸런싱] 잔고 재조회 실패 — 성공 주문 기준으로 포지션 기록")

    for r in plan["rows"]:
        tk  = r["ticker"]
        qty = r["current_qty"] + filled.get(tk, 0)
        if qty <= 0:
            continue
        entry = fill_price.get(tk) or prev.get(tk, {}).get("entry") or r["price"]
        keep.append(_position_record(
            tk, r["name"], entry, qty, r.get("weight", 0.0),
            prev.get(tk, {}).get("entry_date", now_str) if tk in prev else now_str,
        ))
    save_positions(keep)


def _position_record(ticker: str, name: str, entry, qty: int,
                     weight: float, entry_date: str) -> dict:
    """리밸런싱 보유 1건의 positions.json 레코드. TP/SL은 리밸런싱 전략에서 미사용(0)."""
    entry = int(entry or 0)
    return {
        "ticker":          ticker,
        "name":            name,
        "entry":           entry,
        "tp":              0,
        "sl":              0,
        "sl_init":         0,
        "high_water_mark": entry,
        "entry_date":      entry_date,
        "sector":          "ETF",
        "signal_score":    None,
        "bo_lookback":     None,
        "pullback_depth":  None,
        "quantity":        int(qty),
        "auto_traded":     True,
        "strategy":        STRATEGY_KEY,
        "target_weight":   weight,
    }
