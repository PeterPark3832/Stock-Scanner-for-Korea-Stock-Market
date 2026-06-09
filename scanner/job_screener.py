"""14:30 1차 스크리닝 + 15:20 2차 실시간 검증 + 자동매수."""
import time
from datetime import datetime, timedelta

import pandas as pd

try:
    import FinanceDataReader as fdr
except ImportError:
    fdr = None  # type: ignore[assignment]

from scanner.config import STRATEGY, TRADE_AMOUNT_PER_STOCK
from scanner import state
from scanner.notify import send_telegram, _esc
from scanner.positions import load_positions, add_positions
from scanner.kis import (
    get_current_price, get_order_possible_cash, place_order, calc_order_qty,
)
from scanner.analytics import calc_rsi, calc_signal_score, get_kospi_condition
from scanner.fdr import fdr_data_reader
from scanner.calendar import KST, is_market_closed
from scanner.logger import log


def job_first_screen() -> None:
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
                with state._cache_lock:
                    state._first_screen_cache = []
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
                df = _raw.copy()

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

                for lookback in range(1, STRATEGY["bo_lookback"] + 1):
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

                time.sleep(0.1)

            except Exception as e:
                log.warning(f"  [SKIP] {ticker} {name}: {e}")

        with state._cache_lock:
            state._first_screen_cache = candidates
        log.info(f"\n[14:30] 1차 완료: {len(candidates)}개 눌림목 후보 저장")
        log.info(f"  필터 탈락 현황: {filter_counts}")
        log.info("  → 15:20 KIS 실시간 재검증 예정\n")

        # 필터 탈락 상위 3개 공통 포맷
        top_filters    = sorted(filter_counts.items(), key=lambda x: x[1], reverse=True)
        filter_summary = " | ".join(f"{k} {v}건" for k, v in top_filters[:3] if v > 0)

        if candidates:
            names = ", ".join(s["name"] for s in candidates[:10])
            more  = f" 외 {len(candidates) - 10}개" if len(candidates) > 10 else ""
            send_telegram(
                f"🔍 *1차 스윙 스크리닝 완료* ({now.strftime('%Y-%m-%d')})\n"
                f"눌림목 후보 {len(candidates)}개: {names}{more}\n"
                f"🔻 주요 탈락: {filter_summary}\n"
                f"⏰ 15:20 실시간 재검증 예정"
            )
        else:
            send_telegram(
                f"📭 *1차 스윙 스크리닝 — 후보 없음* ({now.strftime('%Y-%m-%d')})\n"
                f"🔻 주요 탈락: {filter_summary}"
            )

    except Exception as e:
        err = f"[ERROR] 1차 스크리닝 예외: {e}"
        log.error(err)
        send_telegram(f"🚨 *1차 스크리닝 오류*\n```{err}```")


def job_second_screen() -> None:
    if is_market_closed(datetime.now(KST)):
        return

    log.info(f"\n{'='*50}")
    log.info(f"[15:20] 2차 실시간 검증 시작")
    log.info(f"{'='*50}")

    with state._auto_trade_lock:
        do_trade = state._auto_trade_enabled

    with state._cache_lock:
        if not state._first_screen_cache:
            send_telegram("⚠️ 1차 후보군 없음 (14:30 스크리닝 실행 여부 확인)")
            return
        candidates = list(state._first_screen_cache)
    log.info(f"  대상: {len(candidates)}개 종목 | 자동매매: {'ON' if do_trade else 'OFF'}")
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

        cur               = live["current"]
        live_open         = live["open"]
        live_high         = live["high"]
        live_low          = live["low"]
        live_vol          = live["volume"]
        live_buy_pressure = live["buy_pressure"]
        live_range        = live_high - live_low

        ok_vol_dry      = live_vol <= stock["vol20_before"] * (STRATEGY["pullback_vol"] + 0.2)
        ok_support      = cur >= stock["bo_open"]
        ok_shape        = (abs(cur - live_open) / live_range <= STRATEGY["pullback_shape"] + 0.10) if live_range > 0 else False
        ok_buy_pressure = live_buy_pressure >= STRATEGY["min_buy_pressure"]

        if ok_vol_dry and ok_support and ok_shape and ok_buy_pressure:
            sl_price       = int(stock["bo_open"] * STRATEGY["sl_buffer"])
            tp_price       = int(cur * (1 + STRATEGY["tp_pct"]))
            sl_pct         = (cur - sl_price) / cur
            if sl_pct > STRATEGY["sl_limit"]:
                log.info(f"  [탈락] {stock['name']} 손절폭 과대 ({sl_pct*100:.1f}% > {STRATEGY['sl_limit']*100:.0f}%)")
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

    min_score = STRATEGY.get("min_signal_score", 0)
    if min_score > 0:
        before_filter = len(verified)
        verified = [s for s in verified if s.get("signal_score", 0) >= min_score]
        dropped = before_filter - len(verified)
        if dropped:
            log.info(f"  신호점수 {min_score}점 미달 제외: {dropped}개")

    if not verified:
        send_telegram(
            f"📉 *{datetime.now(KST).strftime('%Y-%m-%d')} 스윙 눌림목 없음*\n"
            f"1차 후보 {len(candidates)}개 → 실시간 재검증 전원 탈락"
        )
        return

    # 섹터 집중도 강제 차단
    max_sec = STRATEGY["max_sector_count"]
    sector_buckets: dict[str, list[dict]] = {}
    for s in verified:
        sec = s.get("sector") or "미분류"
        sector_buckets.setdefault(sec, []).append(s)

    allowed: list[dict] = []
    blocked: list[dict] = []
    for sec, bucket in sector_buckets.items():
        if sec != "미분류" and len(bucket) > max_sec:
            bucket.sort(key=lambda x: x.get("signal_score", 0), reverse=True)
            allowed.extend(bucket[:max_sec])
            blocked.extend(bucket[max_sec:])
        else:
            allowed.extend(bucket)

    if blocked:
        blocked_names = ", ".join(s["name"] for s in blocked)
        send_telegram(
            f"⛔ *섹터 한도 초과 — 자동매수 차단* ({datetime.now(KST).strftime('%m/%d')})\n"
            f"차단: {blocked_names}\n"
            f"동일 섹터 {max_sec}개 초과 → 신호점수 하위 종목 제외"
        )
        log.info(f"  섹터 강제 차단: {blocked_names}")
        verified = allowed

    with state._signals_lock:
        paused = state._pause_signals

    if do_trade and not paused:
        _execute_buy_orders(verified)

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
        order_str = ""
        if do_trade and not paused:
            if s.get("auto_traded"):
                order_str = f"\n  🤖 자동매수 완료 — {s.get('quantity', 0)}주 (주문번호: {s.get('order_no', '-')})"
            else:
                order_str = f"\n  ⚠️ 자동매수 실패 — {s.get('order_error', '수량 0 또는 잔고 부족')} | 수동 매수 필요"
        msg += f"*{i}. {_esc(s['name'])}* ({s['ticker']}){sec_str} {icon}\n"
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
        msg += order_str + "\n"
    trade_tag = "🤖 자동매매 ON" if do_trade else "📋 수동매매 모드"
    msg += f"✅ KIS 실시간 재검증  ⚠️ 종가 기준(API 미확인)\n{trade_tag}"

    if paused:
        send_telegram(
            f"⏸ *신호 억제 중* — {len(verified)}개 신호 발생\n"
            f"재개하려면 /resume 전송"
        )
        log.info(f"  [PAUSE] 신호 {len(verified)}개 억제됨")
    else:
        send_telegram(msg)
        log.info("  텔레그램 발송 완료")
        to_register = [s for s in verified if not do_trade or s.get("quantity", 0) > 0]
        skipped = len(verified) - len(to_register)
        if skipped:
            log.info(f"  [포지션 미등록] 매수 실패 {skipped}개 종목 제외")
        add_positions(to_register)


def _execute_buy_orders(verified: list[dict]) -> None:
    available = get_order_possible_cash("", 0)
    if available is None:
        log.warning("  [자동매수] 잔고 조회 실패 — 개별 주문 시도")
        available = TRADE_AMOUNT_PER_STOCK * len(verified)

    log.info(f"  [자동매수] 주문 가능 현금: {available:,}원 | 종목당 예산: {TRADE_AMOUNT_PER_STOCK:,}원")

    current_positions = load_positions()
    existing_tickers  = {p["ticker"] for p in current_positions}
    open_slots        = max(0, STRATEGY["max_positions"] - len(current_positions))
    remaining_cash    = available
    bought_count      = 0

    if open_slots == 0:
        log.warning("  [자동매수 스킵 전체] 포지션 한도 도달 — 매수 없음")
        for s in verified:
            s["quantity"]    = 0
            s["auto_traded"] = False
            s["order_error"] = "포지션 한도 초과"
        return

    for s in verified:
        if s["ticker"] in existing_tickers:
            log.info(f"  [자동매수 스킵] {s['name']} — 이미 포지션 보유 중")
            s["quantity"]    = 0
            s["auto_traded"] = False
            s["order_error"] = "이미 보유 중"
            continue

        if bought_count >= open_slots:
            log.info(f"  [자동매수 스킵] {s['name']} — 포지션 슬롯 소진 ({open_slots}개 한도)")
            s["quantity"]    = 0
            s["auto_traded"] = False
            s["order_error"] = "포지션 한도 초과"
            continue

        entry = s["entry"]
        score = s.get("signal_score") or 0
        if score >= 80:
            sizing_factor = 1.5
        elif score >= 60:
            sizing_factor = 1.0
        else:
            sizing_factor = 0.6
        s["sizing_factor"] = sizing_factor
        budget = min(int(TRADE_AMOUNT_PER_STOCK * sizing_factor), remaining_cash)
        qty = calc_order_qty(entry, budget)

        if qty <= 0:
            log.info(f"  [자동매수 스킵] {s['name']} — 1주 가격({entry:,}원)이 예산({TRADE_AMOUNT_PER_STOCK:,}원) 초과 (잔여현금: {remaining_cash:,}원)")
            s["quantity"]    = 0
            s["auto_traded"] = False
            s["order_error"] = f"잔여현금 {remaining_cash:,}원 부족"
            continue

        result = place_order(s["ticker"], "buy", qty, s["name"])
        s["quantity"]    = qty if result["success"] else 0
        s["auto_traded"] = result["success"]
        s["order_no"]    = result.get("order_no", "")
        s["order_error"] = result.get("error", "")

        if result["success"]:
            cost = qty * entry
            remaining_cash -= cost
            bought_count   += 1
            log.info(f"  [자동매수 완료] {s['name']} {qty}주 × {entry:,}원 = {cost:,}원")
        else:
            log.error(f"  [자동매수 실패] {s['name']}: {result['error']}")

        time.sleep(0.3)
