"""KIS (한국투자증권) API 클라이언트."""
import time
import requests

from scanner.config import (
    KIS_APP_KEY, KIS_APP_SECRET, KIS_BASE_URL, KIS_ACCOUNT_NO,
    TRADE_AMOUNT_PER_STOCK, _KIS_MODE, STRATEGY,
    _TR_BUY, _TR_SELL, _TR_BAL,
)
from scanner import state
from scanner.notify import send_telegram
from scanner.logger import log


def _parse_account() -> tuple[str, str]:
    acno = KIS_ACCOUNT_NO.replace("-", "").replace(" ", "")
    if len(acno) < 10:
        return acno, "01"
    return acno[:8], acno[8:10]


def get_kis_access_token() -> str | None:
    now = time.time()
    if state._kis_token_cache["token"] and now < state._kis_token_cache["expires_at"] - 60:
        return state._kis_token_cache["token"]
    try:
        res = requests.post(
            f"{KIS_BASE_URL}/oauth2/tokenP",
            json={"grant_type": "client_credentials",
                  "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET},
            timeout=10,
        )
        res.raise_for_status()
        data = res.json()
        state._kis_token_cache["token"]      = data["access_token"]
        state._kis_token_cache["expires_at"] = now + int(data.get("expires_in", 86400))
        log.info(f"[KIS] 토큰 발급 완료 (유효: {int(data.get('expires_in', 86400) / 3600)}시간)")
        return state._kis_token_cache["token"]
    except Exception as e:
        log.error(f"KIS 토큰 발급 실패: {e}")
        return None


def get_current_price(ticker: str) -> dict | None:
    token = get_kis_access_token()
    if not token:
        return None
    try:
        res = requests.get(
            f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers={
                "content-type":  "application/json",
                "authorization": f"Bearer {token}",
                "appkey":        KIS_APP_KEY,
                "appsecret":     KIS_APP_SECRET,
                "tr_id":         "FHKST01010100",
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
            "buy_pressure": float(o.get("cttr", 100) or 100),
        }
    except Exception as e:
        log.error(f"  {ticker} 시세 조회 예외: {e}")
        return None


def get_kis_hashkey(body: dict) -> str | None:
    try:
        res = requests.post(
            f"{KIS_BASE_URL}/uapi/hashkey",
            headers={
                "content-type": "application/json",
                "appkey": KIS_APP_KEY,
                "appsecret": KIS_APP_SECRET,
            },
            json=body,
            timeout=10,
        )
        res.raise_for_status()
        return res.json().get("HASH")
    except Exception as e:
        log.warning(f"hashkey 발급 실패 (주문 계속 시도): {e}")
        return None


def get_order_possible_cash(ticker: str, price: int) -> int | None:
    token = get_kis_access_token()
    if not token:
        return None
    cano, acnt_prdt = _parse_account()
    if not cano:
        log.error("KIS_ACCOUNT_NO 미설정 — 주문 불가")
        return None
    try:
        res = requests.get(
            f"{KIS_BASE_URL}/uapi/domestic-stock/v1/trading/inquire-psbl-order",
            headers={
                "content-type":  "application/json",
                "authorization": f"Bearer {token}",
                "appkey":        KIS_APP_KEY,
                "appsecret":     KIS_APP_SECRET,
                "tr_id":         _TR_BAL,
                "custtype":      "P",
            },
            params={
                "CANO":                  cano,
                "ACNT_PRDT_CD":          acnt_prdt,
                "PDNO":                  ticker,
                "ORD_UNPR":              str(price),
                "ORD_DVSN":              "01",
                "CMA_EVLU_AMT_ICLD_YN":  "Y",
                "OVRS_ICLD_YN":          "N",
            },
            timeout=10,
        )
        res.raise_for_status()
        data = res.json()
        if data.get("rt_cd") != "0":
            log.error(f"잔고 조회 실패: {data.get('msg1', '')}")
            return None
        return int(data["output"].get("ord_psbl_cash", 0))
    except Exception as e:
        log.error(f"잔고 조회 예외 ({ticker}): {e}")
        return None


def place_order(ticker: str, side: str, qty: int, name: str = "") -> dict:
    result = {"success": False, "order_no": "", "qty": qty, "error": ""}

    if qty <= 0:
        result["error"] = "수량 0 — 주문 스킵"
        return result

    token = get_kis_access_token()
    if not token:
        result["error"] = "토큰 발급 실패"
        return result

    cano, acnt_prdt = _parse_account()
    if not cano:
        result["error"] = "KIS_ACCOUNT_NO 미설정"
        send_telegram(f"🚨 *자동매매 오류* — 계좌번호 미설정\nKIS_ACCOUNT_NO를 .env에 입력하세요")
        return result

    tr_id = _TR_BUY if side == "buy" else _TR_SELL
    body = {
        "CANO":         cano,
        "ACNT_PRDT_CD": acnt_prdt,
        "PDNO":         ticker,
        "ORD_DVSN":     "01",
        "ORD_QTY":      str(qty),
        "ORD_UNPR":     "0",
    }

    hashkey = get_kis_hashkey(body)
    headers = {
        "content-type":  "application/json",
        "authorization": f"Bearer {token}",
        "appkey":        KIS_APP_KEY,
        "appsecret":     KIS_APP_SECRET,
        "tr_id":         tr_id,
        "custtype":      "P",
    }
    if hashkey:
        headers["hashkey"] = hashkey

    side_kor = "매수" if side == "buy" else "매도"
    label    = f"{name}({ticker})" if name else ticker

    try:
        res = requests.post(
            f"{KIS_BASE_URL}/uapi/domestic-stock/v1/trading/order-cash",
            headers=headers, json=body, timeout=15,
        )
        res.raise_for_status()
        data = res.json()

        if data.get("rt_cd") == "0":
            order_no = data.get("output", {}).get("ODNO", "")
            result.update({"success": True, "order_no": order_no})
            log.info(f"  [주문완료] {side_kor} {label} {qty}주 | 주문번호: {order_no}")
        else:
            msg1 = data.get("msg1", "알 수 없는 오류")
            result["error"] = msg1
            log.error(f"  [주문실패] {side_kor} {label}: {msg1}")
            send_telegram(
                f"🚨 *KIS 주문 실패* — {side_kor} {label}\n"
                f"사유: {msg1}\n수동 {side_kor} 필요"
            )
    except Exception as e:
        result["error"] = str(e)
        log.error(f"  [주문예외] {side_kor} {label}: {e}")
        send_telegram(
            f"🚨 *KIS 주문 예외* — {side_kor} {label}\n"
            f"`{str(e)[:200]}`\n수동 확인 필요"
        )

    return result


def calc_order_qty(price: int, budget: int) -> int:
    if price <= 0 or budget <= 0:
        return 0
    return budget // price


def sync_kis_holdings() -> int:
    """KIS 실제 보유 종목을 positions.json에 동기화."""
    # import here to avoid circular: positions imports notify which imports config
    from scanner.positions import load_positions, save_positions

    token = get_kis_access_token()
    if not token:
        log.warning("[KIS 동기화] 토큰 발급 실패 — 건너뜀")
        return 0
    cano, acnt_prdt = _parse_account()
    if not cano:
        log.warning("[KIS 동기화] KIS_ACCOUNT_NO 미설정 — 건너뜀")
        return 0

    tr_id = "TTTC8434R" if _KIS_MODE == "real" else "VTTC8434R"
    base  = ("https://openapi.koreainvestment.com:9443"
             if _KIS_MODE == "real"
             else "https://openapivts.koreainvestment.com:29443")
    try:
        res = requests.get(
            f"{base}/uapi/domestic-stock/v1/trading/inquire-balance",
            headers={
                "Authorization": f"Bearer {token}",
                "appkey":        KIS_APP_KEY,
                "appsecret":     KIS_APP_SECRET,
                "tr_id":         tr_id,
                "Content-Type":  "application/json; charset=utf-8",
            },
            params={
                "CANO":                  cano,
                "ACNT_PRDT_CD":          acnt_prdt,
                "AFHR_FLPR_YN":          "N",
                "OFL_YN":                "",
                "INQR_DVSN":             "02",
                "UNPR_DVSN":             "01",
                "FUND_STTL_ICLD_YN":     "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN":             "00",
                "CTX_AREA_FK100":        "",
                "CTX_AREA_NK100":        "",
            },
            timeout=10,
        )
        res.raise_for_status()
        data = res.json()
    except Exception as e:
        log.error(f"[KIS 동기화] API 호출 실패: {e}")
        return 0

    if data.get("rt_cd") != "0":
        log.warning(f"[KIS 동기화] API 오류: {data.get('msg1', '')}")
        return 0

    holdings = data.get("output1", [])
    if not holdings:
        log.info("[KIS 동기화] 보유 종목 없음")
        return 0

    from scanner.calendar import KST
    from datetime import datetime as _dt

    existing   = load_positions()
    exist_set  = {p["ticker"] for p in existing}
    now_str    = _dt.now(KST).strftime("%Y-%m-%d")
    added      = 0

    for h in holdings:
        ticker = h.get("pdno", "").strip()
        qty    = int(h.get("hldg_qty", "0"))
        if not ticker or qty <= 0:
            continue
        if ticker in exist_set:
            continue

        avg_price = int(float(h.get("pchs_avg_pric", "0")))
        name      = h.get("prdt_name", ticker)
        if avg_price <= 0:
            continue

        tp = int(avg_price * (1 + STRATEGY["tp_pct"]))
        sl = int(avg_price * 0.95)
        existing.append({
            "ticker":          ticker,
            "name":            name,
            "entry":           avg_price,
            "tp":              tp,
            "sl":              sl,
            "sl_init":         sl,
            "high_water_mark": avg_price,
            "entry_date":      now_str,
            "sector":          "",
            "signal_score":    None,
            "bo_lookback":     None,
            "pullback_depth":  None,
            "quantity":        qty,
            "auto_traded":     False,
        })
        exist_set.add(ticker)
        added += 1
        log.info(f"  [KIS 동기화] {name}({ticker}) {qty}주 @ {avg_price:,}원 → 포지션 추가")

    if added:
        save_positions(existing)
        send_telegram(
            f"🔄 *KIS 보유 종목 동기화 완료*\n"
            f"새로 등록: {added}개 종목\n"
            f"(수동 매수 종목 — TP/SL 기본값 적용)"
        )
    return added
