"""
Stock Scanner Dashboard v5.1
- TeamHub 라이트 테마 (민트 그린 #2ECC88 / 배경 #F2F6FB)
- PC: 220px 고정 사이드바 + 섹션 라우팅
- Mobile: 하단 탭 네비 (5개 섹션)
접속: http://<서버IP>:8081?token=<DASHBOARD_TOKEN>
"""
import csv, json, os, re, subprocess, threading, time, requests
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from filelock import FileLock
import holidays as _holidays
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

KST      = ZoneInfo("Asia/Seoul")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(BASE_DIR, ".env")
POSITIONS_FILE    = os.path.join(BASE_DIR, "positions.json")
HISTORY_FILE      = os.path.join(BASE_DIR, "trade_history.csv")
SCREENING_LOG_FILE = os.path.join(BASE_DIR, "screening_log.json")

def read_env(key: str, default: str = "") -> str:
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == key:
                    return v.strip()
    except Exception:
        pass
    return os.getenv(key, default)

def write_env(key: str, value: str) -> None:
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        pattern = rf"^{re.escape(key)}=.*$"
        new_line = f"{key}={value}"
        if re.search(pattern, content, re.MULTILINE):
            content = re.sub(pattern, new_line, content, flags=re.MULTILINE)
        else:
            content += f"\n{new_line}"
        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        raise RuntimeError(f".env 쓰기 실패: {e}")

def _kis_base() -> str:
    return ("https://openapi.koreainvestment.com:9443"
            if read_env("KIS_MODE", "paper") == "real"
            else "https://openapivts.koreainvestment.com:29443")

DASHBOARD_TOKEN  = read_env("DASHBOARD_TOKEN", "")
if not DASHBOARD_TOKEN or len(DASHBOARD_TOKEN) < 20:
    raise RuntimeError("DASHBOARD_TOKEN must be set in .env (minimum 20 characters)")
_token_cache     = {"token": None, "expires_at": 0}
_token_lock      = threading.Lock()
_POSITIONS_FLOCK = FileLock(os.path.join(BASE_DIR, "positions.json.lock"),    timeout=5)
_HISTORY_FLOCK   = FileLock(os.path.join(BASE_DIR, "trade_history.csv.lock"), timeout=5)

def get_kis_token() -> str | None:
    with _token_lock:
        if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
            return _token_cache["token"]
    try:
        r = requests.post(f"{_kis_base()}/oauth2/tokenP",
            json={"grant_type": "client_credentials",
                  "appkey": read_env("KIS_APP_KEY"),
                  "appsecret": read_env("KIS_APP_SECRET")}, timeout=10)
        t = r.json().get("access_token")
        if t:
            with _token_lock:
                _token_cache.update({"token": t, "expires_at": time.time() + 86400})
        return t
    except Exception:
        return None

_price_cache: dict[str, tuple] = {}   # ticker -> (expires_at, result)
_PRICE_TTL = 20                       # 초 — 한 새로고침 주기 내 중복 KIS 시세 호출 방지

def get_price(ticker: str) -> dict | None:
    hit = _price_cache.get(ticker)
    if hit and time.time() < hit[0]:
        return hit[1]
    token = get_kis_token()
    if not token:
        return None
    result = None
    try:
        r = requests.get(f"{_kis_base()}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers={"Authorization": f"Bearer {token}",
                     "appkey": read_env("KIS_APP_KEY"),
                     "appsecret": read_env("KIS_APP_SECRET"),
                     "tr_id": "FHKST01010100"},
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}, timeout=5)
        d = r.json()
        if d.get("rt_cd") == "0":
            result = {"current": int(d["output"].get("stck_prpr", 0))}
    except Exception:
        pass
    if result is not None:
        _price_cache[ticker] = (time.time() + _PRICE_TTL, result)
    return result

_cash_cache: dict = {"expires_at": 0, "value": None}
def get_order_possible_cash() -> int | None:
    if _cash_cache["value"] is not None and time.time() < _cash_cache["expires_at"]:
        return _cash_cache["value"]
    token = get_kis_token()
    if not token:
        return None
    acno = read_env("KIS_ACCOUNT_NO", "").replace("-", "").replace(" ", "")
    if len(acno) < 10:
        return None
    cano, acnt = acno[:8], acno[8:10]
    tr_id = "TTTC8908R" if read_env("KIS_MODE", "paper") == "real" else "VTTC8908R"
    try:
        r = requests.get(f"{_kis_base()}/uapi/domestic-stock/v1/trading/inquire-psbl-order",
            headers={"content-type": "application/json",
                     "authorization": f"Bearer {token}",
                     "appkey": read_env("KIS_APP_KEY"),
                     "appsecret": read_env("KIS_APP_SECRET"),
                     "tr_id": tr_id, "custtype": "P"},
            params={"CANO": cano, "ACNT_PRDT_CD": acnt, "PDNO": "",
                    "ORD_UNPR": "0", "ORD_DVSN": "01",
                    "CMA_EVLU_AMT_ICLD_YN": "Y", "OVRS_ICLD_YN": "N"}, timeout=5)
        d = r.json()
        if d.get("rt_cd") == "0":
            val = int(d["output"].get("ord_psbl_cash", 0))
            _cash_cache.update(expires_at=time.time() + _PRICE_TTL, value=val)
            return val
    except Exception:
        pass
    return None

def send_telegram(text: str) -> None:
    token    = read_env("TELEGRAM_TOKEN")
    chat_ids = [c.strip() for c in read_env("TELEGRAM_CHAT_IDS").split(",") if c.strip()]
    topic_id = read_env("TELEGRAM_TOPIC_ID")
    if not token or not chat_ids:
        return
    for cid in chat_ids:
        try:
            payload = {"chat_id": cid, "text": text, "parse_mode": "Markdown"}
            if topic_id:
                payload["message_thread_id"] = int(topic_id)
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data=payload, timeout=5)
        except Exception:
            pass

def load_positions() -> list[dict]:
    with _POSITIONS_FLOCK:
        try:
            with open(POSITIONS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

def save_positions(positions: list[dict]) -> None:
    with _POSITIONS_FLOCK:
        with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(positions, f, ensure_ascii=False, indent=2)

def append_history(row: dict) -> None:
    fieldnames = ["ticker","name","sector","entry_date","exit_date",
                  "entry_price","exit_price","quantity","pnl_pct",
                  "exit_reason","signal_score","bo_lookback","pullback_depth","auto_traded",
                  "post_expire_pnl"]
    exists = os.path.exists(HISTORY_FILE)
    with _HISTORY_FLOCK:
        with open(HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            if not exists:
                w.writeheader()
            w.writerow(row)

def dashboard_sell(ticker: str, qty: int, name: str) -> dict:
    result = {"success": False, "order_no": "", "error": ""}
    if qty <= 0:
        result["error"] = "수량 0"
        return result
    token = get_kis_token()
    if not token:
        result["error"] = "KIS 토큰 발급 실패"
        return result
    kis_mode = read_env("KIS_MODE", "paper")
    tr_id    = "TTTC0801U" if kis_mode == "real" else "VTTC0801U"
    acno     = read_env("KIS_ACCOUNT_NO", "").replace("-", "").replace(" ", "")
    cano, acnt = (acno[:8], acno[8:10]) if len(acno) >= 10 else (acno, "01")
    body = {"CANO": cano, "ACNT_PRDT_CD": acnt, "PDNO": ticker,
            "ORD_DVSN": "01", "ORD_QTY": str(qty), "ORD_UNPR": "0"}
    try:
        r = requests.post(f"{_kis_base()}/uapi/domestic-stock/v1/trading/order-cash",
            headers={"content-type": "application/json",
                     "authorization": f"Bearer {token}",
                     "appkey": read_env("KIS_APP_KEY"),
                     "appsecret": read_env("KIS_APP_SECRET"),
                     "tr_id": tr_id, "custtype": "P"},
            json=body, timeout=15)
        d = r.json()
        if d.get("rt_cd") == "0":
            result.update(success=True, order_no=d.get("output", {}).get("ODNO", ""))
        else:
            result["error"] = d.get("msg1", "주문 실패")
    except Exception as e:
        result["error"] = str(e)
    return result

app = FastAPI()

def auth(token: str):
    if token != DASHBOARD_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

@app.post("/api/control")
async def api_control(request: Request, token: str = ""):
    auth(token)
    body   = await request.json()
    action = body.get("action", "")
    if action in ("autotrade_on", "autotrade_off"):
        value = "true" if action == "autotrade_on" else "false"
        write_env("AUTO_TRADE", value)
        subprocess.Popen(["systemctl", "restart", "stock-scanner"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        label = "ON" if value == "true" else "OFF"
        send_telegram(f"🖥️ *대시보드* — 자동매매 {label} 변경\n봇 재시작 중...")
        return JSONResponse({"ok": True, "msg": f"자동매매 {label} — 봇 재시작 중"})
    return JSONResponse({"ok": False, "msg": "알 수 없는 액션"}, status_code=400)

@app.post("/api/sell/{ticker}")
async def api_sell(ticker: str, request: Request, token: str = ""):
    auth(token)
    body   = await request.json()
    qty    = int(body.get("qty", 0))
    name   = body.get("name", ticker)
    result = dashboard_sell(ticker, qty, name)
    if not result["success"]:
        return JSONResponse({"ok": False, "msg": result["error"]}, status_code=400)
    positions = load_positions()
    p = next((x for x in positions if x["ticker"] == ticker), None)
    if p:
        live  = get_price(ticker)
        epx   = live["current"] if live else int(body.get("entry", 0))
        entry = p.get("entry", 0)
        pnl   = round((epx - entry)/entry*100, 2) if entry else 0
        save_positions([x for x in positions if x["ticker"] != ticker])
        append_history({"ticker": ticker, "name": name, "sector": p.get("sector",""),
            "entry_date": p.get("entry_date",""), "exit_date": datetime.now(KST).strftime("%Y-%m-%d"),
            "entry_price": entry, "exit_price": epx, "quantity": p.get("quantity", qty),
            "pnl_pct": pnl, "exit_reason": "MANUAL_SELL",
            "signal_score": p.get("signal_score",""), "bo_lookback": p.get("bo_lookback",""),
            "pullback_depth": p.get("pullback_depth",""), "auto_traded": p.get("auto_traded", False)})
        send_telegram(f"🖥️ *대시보드 수동 청산*\n{name}({ticker}) {qty}주\n"
                      f"주문번호: {result['order_no']} | PnL: {pnl:+.2f}%")
    return JSONResponse({"ok": True, "order_no": result["order_no"]})

def _portfolio_snapshot() -> dict:
    """현재 봇 관리 보유 평가 — 총평가금액·현금·보유종목·현재배분·매입원가 대비 평가손익.
    전략 태그(kr_gem/kr_growth 등)와 무관하게 리밸런싱이 보유한 종목 전체를 합산한다."""
    from scanner.strategy_rebalance import STRATEGIES
    positions = [p for p in load_positions() if p.get("strategy") in STRATEGIES]
    holdings, equity, cost_basis = [], 0, 0
    for p in positions:
        live  = get_price(p["ticker"])
        price = live["current"] if live else p.get("entry", 0)
        qty   = p.get("quantity", 0)
        entry = p.get("entry", 0)
        val   = price * qty
        equity += val
        cost_basis += entry * qty
        pnl_pct = round((price - entry) / entry * 100, 2) if entry else None
        holdings.append({"ticker": p["ticker"], "name": p.get("name", p["ticker"]),
                         "qty": qty, "price": price, "value": val, "entry": entry,
                         "pnl_pct": pnl_pct, "target_weight": p.get("target_weight", 0)})
    cash  = get_order_possible_cash() or 0
    total = equity + cash
    for h in holdings:
        h["current_weight"] = round(h["value"] / total * 100, 1) if total else 0
    last_rebalance = max((p.get("entry_date", "") for p in positions), default="")
    unrealized = round((equity - cost_basis) / cost_basis * 100, 2) if cost_basis else 0.0
    return {"holdings": holdings, "equity": equity, "cash": cash, "total": total,
            "cost_basis": cost_basis, "unrealized_pnl": unrealized,
            "cash_weight": round(cash / total * 100, 1) if total else 0,
            "count": len(holdings), "last_rebalance": last_rebalance}

def _rebalance_events() -> list[dict]:
    path = os.path.join(BASE_DIR, "rebalance_log.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def _equity_snapshots() -> list[dict]:
    path = os.path.join(BASE_DIR, "equity_snapshots.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

_bench_cache = {"key": None, "at": 0, "series": None}
def _benchmark_series(start: str):
    """KOSPI200(069500) 일별 종가 시계열 {date: close}. FDR, 1시간 캐시."""
    now = time.time()
    if _bench_cache["series"] is not None and _bench_cache["key"] == start and now - _bench_cache["at"] < 3600:
        return _bench_cache["series"]
    series = {}
    try:
        import FinanceDataReader as fdr
        df = fdr.DataReader("069500", start)
        if df is not None and not df.empty and "Close" in df.columns:
            series = {d.strftime("%Y-%m-%d"): float(v) for d, v in df["Close"].items() if v == v and v > 0}
    except Exception:
        series = {}
    _bench_cache.update(key=start, at=now, series=series)
    return series

def _first_trading_day(y: int, m: int) -> date:
    d = date(y, m, 1)
    kr = _holidays.KR(years=y)
    while d.weekday() >= 5 or d in kr:
        d += timedelta(days=1)
    return d

def _next_rebalance_date() -> date:
    today = datetime.now(KST).date()
    ft = _first_trading_day(today.year, today.month)
    if ft <= today:
        ny, nm = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        ft = _first_trading_day(ny, nm)
    return ft

@app.get("/api/portfolio")
def api_portfolio(token: str = ""):
    auth(token)
    snap = _portfolio_snapshot()
    ret = snap["unrealized_pnl"]   # 매입원가 대비 평가손익(입금 영향 없음)
    nxt = _next_rebalance_date()
    d_day = (nxt - datetime.now(KST).date()).days
    from scanner.strategy_rebalance import get_strategy
    skey = read_env("STRATEGY_KEY", "kr_gem")
    return JSONResponse({
        "now":          datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        "auto_trade":   read_env("AUTO_TRADE", "false").lower() == "true",
        "kis_mode":     "실전투자" if read_env("KIS_MODE", "paper") == "real" else "모의투자",
        "next_rebalance": nxt.strftime("%Y-%m-%d"), "d_day": d_day,
        "strategy": skey, "strategy_name": get_strategy(skey)["name"],
        **snap, "total_return": ret,
    })

_targets_cache: dict = {}   # key -> (expires_at, targets) — 표시용. 실거래(job_rebalance)는 항상 신규 계산.
_TARGETS_TTL = 600          # 초 — FDR 일별 데이터라 자주 갱신 불필요
def _cached_targets(key: str) -> list[dict]:
    hit = _targets_cache.get(key)
    if hit and time.time() < hit[0]:
        return hit[1]
    from scanner.strategy_rebalance import compute_target_weights
    targets = compute_target_weights(key)
    if targets:
        _targets_cache[key] = (time.time() + _TARGETS_TTL, targets)
    return targets

@app.get("/api/strategies")
def api_strategies(token: str = ""):
    auth(token)
    from scanner.strategy_rebalance import list_strategies
    return JSONResponse({"current": read_env("STRATEGY_KEY", "kr_gem"),
                         "strategies": list_strategies()})

@app.post("/api/strategy")
async def api_set_strategy(request: Request, token: str = ""):
    auth(token)
    from scanner.strategy_rebalance import STRATEGIES, get_strategy
    body = await request.json()
    key  = (body.get("key") or "").strip()
    if key not in STRATEGIES:
        return JSONResponse({"ok": False, "msg": "알 수 없는 전략입니다"}, status_code=400)
    old = read_env("STRATEGY_KEY", "kr_gem")
    write_env("STRATEGY_KEY", key)
    subprocess.Popen(["systemctl", "restart", "stock-scanner"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    name = get_strategy(key)["name"]
    send_telegram(f"🖥️ *대시보드* — 리밸런싱 전략 변경\n{old} → *{key}* ({name})\n"
                  f"봇 재시작 중 · 다음 리밸런싱부터 적용")
    return JSONResponse({"ok": True, "msg": f"전략을 '{name}'(으)로 변경 — 봇 재시작 중", "key": key})

@app.get("/api/rebalance")
def api_rebalance(token: str = ""):
    auth(token)
    try:
        targets = _cached_targets(read_env("STRATEGY_KEY", "kr_gem"))
    except Exception as e:
        return JSONResponse({"ok": False, "msg": f"전략 계산 실패: {e}"}, status_code=500)
    snap = _portfolio_snapshot()
    held = {h["ticker"]: h for h in snap["holdings"]}
    rows = []
    target_tickers = set()
    for t in targets:
        target_tickers.add(t["ticker"])
        h = held.get(t["ticker"])
        cur_qty   = h["qty"] if h else 0
        cur_price = h["price"] if h else (get_price(t["ticker"]) or {}).get("current", 0)
        cur_w     = h["current_weight"] if h else 0
        tgt_qty   = int(snap["total"] * t["weight"] / 100 // t["price"]) if t["price"] else 0
        rows.append({**t, "current_qty": cur_qty, "current_price": cur_price,
                     "current_weight": cur_w, "target_qty": tgt_qty,
                     "diff_qty": tgt_qty - cur_qty})
    # 목표에서 빠졌지만 보유 중 → 전량 매도 표시
    for tk, h in held.items():
        if tk not in target_tickers:
            rows.append({"ticker": tk, "name": h["name"], "weight": 0.0,
                         "price": h["price"], "current_qty": h["qty"],
                         "current_price": h["price"], "current_weight": h["current_weight"],
                         "target_qty": 0, "diff_qty": -h["qty"]})
    # 최소 필요금액: 목표 각 종목을 비중만큼 사서 1주 이상 담으려면 필요한 총자산
    #   (총자산 × weight% ≥ price  →  총자산 ≥ price × 100/weight). 가장 큰 값이 기준.
    need = [t["price"] * 100 / t["weight"] for t in targets if t.get("weight") and t.get("price")]
    min_required = int(max(need)) if need else 0
    return JSONResponse({"ok": True, "rows": rows, "total_value": snap["total"],
                         "cash": snap["cash"], "last_rebalance": snap["last_rebalance"],
                         "min_required": min_required})

@app.get("/api/rebalance/history")
def api_rebalance_history(token: str = ""):
    auth(token)
    events = _rebalance_events()
    snaps  = _equity_snapshots()
    # 일별 평가금액 포인트(스냅샷). 오늘은 09:00 스냅샷이 장중 매매/입금 전이라 실시간 값으로 덮어씀.
    points = {s["date"]: s.get("total", 0) for s in snaps if s.get("date")}
    today  = datetime.now(KST).strftime("%Y-%m-%d")
    live_total = _portfolio_snapshot()["total"]
    if live_total:
        points[today] = live_total
    full_dates = sorted(points)
    totals = [points[d] for d in full_dates]
    base = totals[0] if totals else 0
    # 벤치마크(KOSPI200) — 시작 평가금액과 동일 금액을 KOSPI200에 넣었다고 가정해 원화로 환산
    bench_value = []
    if full_dates and base:
        bs = _benchmark_series(full_dates[0])
        if bs:
            keys = sorted(bs)
            def closest(d):
                prev = [k for k in keys if k <= d]
                return bs[prev[-1]] if prev else None
            raw = [closest(d) for d in full_dates]
            b0 = next((x for x in raw if x), None)
            if b0:
                bench_value = [int(base * x / b0) if x else None for x in raw]
    labels = [d[5:] for d in full_dates]  # MM-DD
    exec_dates = [e["ts"][:10] for e in events if e.get("ts")]
    return JSONResponse({"events": list(reversed(events)),
                         "dates": labels, "value_curve": totals,
                         "benchmark_value": bench_value, "exec_dates": exec_dates})

@app.post("/api/rebalance/execute")
def api_rebalance_execute(token: str = ""):
    auth(token)
    open(os.path.join(BASE_DIR, "_rebalance_now.flag"), "w").close()
    send_telegram("🖥️ *대시보드* — 수동 리밸런싱 요청 전달\n실행 중인 봇이 곧 처리합니다")
    return JSONResponse({"ok": True, "msg": "리밸런싱 요청 전달 — 1분 내 처리됩니다"})

@app.get("/api/logs")
def api_logs(token: str = "", lines: int = 100):
    auth(token)
    log_file = os.path.join(BASE_DIR, "scanner.log")
    if not os.path.exists(log_file):
        return JSONResponse({"lines": [], "error": "scanner.log 없음"})
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        recent = [l.rstrip() for l in all_lines[-min(lines, len(all_lines)):]]
        return JSONResponse({"lines": recent})
    except Exception as e:
        return JSONResponse({"lines": [], "error": str(e)})


@app.get("/", response_class=HTMLResponse)
def dashboard(token: str = ""):
    auth(token)
    import holidays as _hol
    now = datetime.now()
    kr = _hol.KR(years=[now.year, now.year + 1])
    hols = {str(d): name for d, name in sorted(kr.items())}
    return HTMLResponse(
        HTML.replace("__TOKEN__", token)
            .replace("__HOLIDAYS__", json.dumps(hols, ensure_ascii=False))
    )

# ── HTML — TeamHub 라이트 테마 ────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Scanner v5.1</title>
<link rel="preconnect" href="https://api.fontshare.com" crossorigin>
<link href="https://api.fontshare.com/v2/css?f[]=satoshi@400,500,700,900&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root{
  --c-primary:#00C805;--c-primary-dk:#00A843;
  --c-up:#00A843;--c-up-bright:#00C805;--c-down:#F0463A;
  --c-bg:#FAFAFA;--c-surface:#fff;--c-surface2:#F6F7F8;
  --c-text:#0A0B0D;--c-text2:#6F7780;
  --c-border:#EAECEF;
  --c-danger:#F0463A;--c-warn:#F59E0B;--c-info:#3B82F6;
  --sidebar-w:220px;
  /* dark sidebar tokens */
  --sb-bg:#0A0B0D;--sb-border:rgba(255,255,255,.07);
  --sb-text:#9AA0A6;--sb-text-hover:#F1F3F5;
  --sb-active-bg:rgba(255,255,255,.08);
}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
html{overflow-x:hidden}
body{background:var(--c-bg);color:var(--c-text);font-family:'Satoshi','Inter',system-ui,sans-serif;overscroll-behavior:none;-webkit-font-smoothing:antialiased;overflow-x:hidden;max-width:100vw}

/* Layout */
.layout{display:flex;min-height:100vh}
.sidebar{width:var(--sidebar-w);flex-shrink:0;background:var(--sb-bg);border-right:1px solid var(--sb-border);display:flex;flex-direction:column;position:fixed;top:0;left:0;bottom:0;z-index:100;overflow-y:auto}
.main{margin-left:var(--sidebar-w);flex:1;padding:28px 28px 28px;max-width:1400px}

/* Sidebar */
.sb-logo{padding:20px 16px 16px;display:flex;align-items:center;gap:10px;border-bottom:1px solid var(--sb-border)}
.sb-logo-icon{width:34px;height:34px;background:var(--c-primary);border-radius:9px;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.sb-logo-icon svg{width:18px;height:18px;fill:#fff}
.sb-logo-text{font-size:14px;font-weight:700;letter-spacing:-.2px;color:#F1F5F9}
.sb-logo-sub{font-size:11px;color:var(--sb-text)}
.sb-section{padding:20px 14px 6px;font-size:10px;font-weight:600;color:rgba(255,255,255,.28);text-transform:uppercase;letter-spacing:.8px}
.sb-nav{list-style:none;padding:0 8px}
.sb-nav li a{display:flex;align-items:center;gap:10px;width:100%;padding:9px 10px;border-radius:8px;font-size:13px;color:var(--sb-text);text-decoration:none;background:none;border:none;cursor:pointer;transition:all .15s}
.sb-nav li a:hover{background:rgba(255,255,255,.07);color:var(--sb-text-hover)}
.sb-nav li a.active{background:var(--sb-active-bg);color:#fff;font-weight:600}
.sb-nav li a.active svg{color:var(--c-primary)}
.sb-nav li a svg{width:16px;height:16px;flex-shrink:0;transition:color .15s}
.sb-bottom{margin-top:auto;padding:12px 8px;border-top:1px solid var(--sb-border)}
.sb-status{display:flex;align-items:center;gap:8px;padding:9px 10px;border-radius:8px;background:rgba(255,255,255,.05);font-size:12px}
.sb-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.sb-dot.green{background:#22c55e;box-shadow:0 0 0 3px rgba(34,197,94,.2)}
.sb-dot.red{background:#ef4444;box-shadow:0 0 0 3px rgba(239,68,68,.2)}
.sb-status span{color:var(--sb-text)}

/* Card */
.card{background:var(--c-surface);border-radius:16px;padding:22px;box-shadow:none;border:1px solid var(--c-border)}
.card-title{font-size:11px;font-weight:700;color:var(--c-text2);margin-bottom:16px;display:flex;align-items:center;justify-content:space-between;text-transform:uppercase;letter-spacing:.5px}

/* KPI strip (rebalance tab) */
.kpi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:20px}
.kpi-card{background:var(--c-surface);border-radius:14px;padding:18px 18px 16px;border:1px solid var(--c-border)}
.kpi-label{font-size:11px;color:var(--c-text2);font-weight:500;letter-spacing:.2px;margin-bottom:9px}
.kpi-value{font-size:27px;font-weight:700;letter-spacing:-.6px;line-height:1;font-variant-numeric:tabular-nums}
.kpi-sub{font-size:12px;color:var(--c-text2);margin-top:7px;min-width:0}
.kpi-value.green{color:var(--c-up)}
.kpi-value.red{color:var(--c-down)}
.kpi-value.neutral{color:var(--c-text)}

/* Hero — Robinhood-style portfolio header */
.hero{padding:6px 2px 22px}
.hero-label{font-size:13px;color:var(--c-text2);font-weight:500;margin-bottom:9px}
.hero-value{font-size:46px;font-weight:900;letter-spacing:-1.6px;line-height:1;font-variant-numeric:tabular-nums;color:var(--c-text)}
.hero-delta{display:inline-flex;align-items:center;gap:7px;margin-top:14px;font-size:16px;font-weight:700;font-variant-numeric:tabular-nums}
.hero-delta.up{color:var(--c-up)}
.hero-delta.down{color:var(--c-down)}
.hero-arrow{display:inline-flex;align-items:center}
.hero-delta-sub{font-size:13px;font-weight:500;color:var(--c-text2)}
.hero-meta{display:flex;align-items:center;gap:12px;margin-top:14px;font-size:13px;color:var(--c-text2)}
.hero-meta b{color:var(--c-text);font-weight:600;font-variant-numeric:tabular-nums}
.dot-sep{width:3px;height:3px;border-radius:50%;background:var(--c-border)}

/* Compact secondary stat row */
.stat-row{display:grid;grid-template-columns:repeat(3,1fr);border:1px solid var(--c-border);border-radius:14px;overflow:hidden;margin-bottom:20px;background:var(--c-surface)}
.stat{padding:16px 18px;border-right:1px solid var(--c-border)}
.stat:last-child{border-right:none}
.stat-label{font-size:11px;color:var(--c-text2);font-weight:500;margin-bottom:6px}
.stat-num{font-size:19px;font-weight:700;letter-spacing:-.3px;font-variant-numeric:tabular-nums;color:var(--c-text)}
@media(max-width:640px){
  .hero-value{font-size:38px;letter-spacing:-1.2px}
  .stat{padding:13px 11px}
  .stat-num{font-size:16px}
  .hero-meta{flex-wrap:wrap;gap:8px}
}

/* Bot card */
.grid-bot{display:grid;grid-template-columns:280px 1fr;gap:16px;margin-bottom:20px}
.bot-card{border-left:3px solid var(--c-primary)}
.bot-header{display:flex;align-items:center;gap:12px;margin-bottom:14px}
.bot-avatar{width:40px;height:40px;border-radius:10px;background:linear-gradient(135deg,var(--c-primary),#1a9e66);display:flex;align-items:center;justify-content:center;flex-shrink:0}
.bot-avatar svg{width:22px;height:22px;fill:#fff}
.bot-name{font-size:15px;font-weight:700;margin-bottom:2px}
.bot-role{font-size:11.5px;color:var(--c-text2)}
.bot-stats{display:flex;flex-wrap:wrap;gap:6px}
.bot-stat{background:var(--c-bg);border-radius:8px;padding:7px 12px;display:flex;align-items:center;gap:7px;border:1px solid var(--c-border)}
.bot-stat-label{font-size:11px;color:var(--c-text2)}
.bot-stat-value{font-size:13px;font-weight:600}

/* Reason bar chart */
.reason-bar-list{display:flex;flex-direction:column;gap:8px}
.reason-bar-row{display:grid;grid-template-columns:88px 1fr 64px;align-items:center;gap:10px}
.reason-bar-label{font-size:12px;color:var(--c-text2);font-weight:500;white-space:nowrap;text-align:right}
.reason-bar-track{background:var(--c-border);border-radius:4px;height:8px;overflow:hidden}
.reason-bar-fill{height:100%;border-radius:4px;transition:width .4s ease}
.reason-bar-meta{font-size:11.5px;color:var(--c-text2);white-space:nowrap;text-align:right;font-variant-numeric:tabular-nums}
.reason-bar-pct{font-weight:700;color:var(--c-text)}
/* 자산 배분: 이름·% 윗줄, 막대 아랫줄 (긴 종목명도 겹치지 않게) */
#allocRow{gap:14px}
.alloc-row{display:flex;flex-direction:column;gap:5px}
.alloc-top{display:flex;justify-content:space-between;align-items:baseline;gap:10px}
.alloc-name{font-size:12.5px;color:var(--c-text);font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.alloc-pct{font-size:12.5px;color:var(--c-text);font-weight:700;white-space:nowrap;font-variant-numeric:tabular-nums}
.alloc-track{background:var(--c-border);border-radius:4px;height:8px;overflow:hidden}
.alloc-fill{height:100%;border-radius:4px;transition:width .4s ease}
@media(max-width:640px){
  .reason-bar-row{grid-template-columns:72px 1fr 56px}
  .reason-bar-label{font-size:11px}
}

/* Mini calendar */
.cal-month{font-size:14px;font-weight:700;text-align:center;margin-bottom:10px}
.cal-header{display:grid;grid-template-columns:repeat(7,1fr);margin-bottom:3px}
.cal-h{font-size:10px;color:var(--c-text2);text-align:center;font-weight:600;padding:3px 0}
.cal-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:2px}
.cal-d{font-size:12px;text-align:center;padding:4px 0 3px;border-radius:6px;cursor:default;line-height:1.2}
.cal-d.today{background:var(--c-primary);color:#fff;font-weight:700}
.cal-d.other-month{color:#cbd5e1}
.cal-d.sat{color:#3b82f6}
.cal-d.sun{color:#ef4444}
.cal-d.holiday{color:#ef4444}
.cal-d.today.holiday,.cal-d.today.sat,.cal-d.today.sun{color:#fff}
.hol-dot{display:block;width:3px;height:3px;background:#ef4444;border-radius:50%;margin:1px auto 0}
.cal-d.today .hol-dot{background:rgba(255,255,255,.8)}
.cal-d.sat .hol-dot{background:#3b82f6}
.rebal-dot{display:block;width:3px;height:3px;background:var(--c-primary);border-radius:50%;margin:1px auto 0}
.cal-d.rebal-exec{background:rgba(0,200,5,.10)}
.cal-d.rebal-next{box-shadow:inset 0 0 0 1.5px var(--c-primary);font-weight:700;border-radius:6px}
.cal-d.today .rebal-dot{background:rgba(255,255,255,.9)}
.schedule-list{margin-top:14px;border-top:1px solid var(--c-border);padding-top:10px}
.schedule-item{display:flex;align-items:flex-start;gap:10px;padding:6px 0;border-bottom:1px solid var(--c-border);font-size:12.5px}
.schedule-item:last-child{border-bottom:none}
.schedule-time{font-weight:700;color:var(--c-primary);min-width:38px;flex-shrink:0}
.schedule-desc{color:var(--c-text2)}

/* Tables */
.tbl-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:13px}
thead th{background:var(--c-bg);color:var(--c-text2);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.5px;padding:10px 14px;text-align:left;border-bottom:2px solid var(--c-border);white-space:nowrap}
tbody tr{border-bottom:1px solid var(--c-border);transition:background .12s}
tbody tr:nth-child(even){background:var(--c-surface2)}
tbody tr:hover{background:rgba(16,185,129,.04)}
tbody tr:last-child{border-bottom:none}
td{padding:11px 14px;vertical-align:middle;white-space:nowrap}
.tbl-name{max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.date-short{display:none}

/* Progress bar */
.prog-bar{height:5px;border-radius:3px;background:var(--c-border);overflow:hidden;width:80px;display:inline-block;vertical-align:middle}
.prog-fill{height:100%;border-radius:3px;background:var(--c-primary);transition:width .3s}
.prog-fill.danger{background:var(--c-danger)}
.prog-fill.warn{background:var(--c-warn)}

/* Badges */
.badge{display:inline-flex;align-items:center;padding:2px 8px;border-radius:6px;font-size:11.5px;font-weight:600;line-height:1.4;white-space:nowrap}
.badge-green{background:#dcfce7;color:#15803d}
.badge-red{background:#fee2e2;color:#dc2626}
.badge-blue{background:#dbeafe;color:#1d4ed8}
.badge-gray{background:#f1f5f9;color:#64748b}
.badge-yellow{background:#fef3c7;color:#92400e}

/* Buttons */
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 16px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;border:none;transition:all .15s;white-space:nowrap}
.btn-primary{background:var(--c-primary);color:#04210A;font-weight:700}
.btn-primary:hover{background:var(--c-primary-dk);color:#fff}
.btn-outline{background:transparent;border:1.5px solid var(--c-border);color:var(--c-text)}
.btn-outline:hover{background:var(--c-bg)}
.btn-danger{background:#fee2e2;color:#dc2626;border:1.5px solid #fecaca}
.btn-danger:hover{background:#fecaca}
.btn-sm{padding:5px 11px;font-size:12px}

/* Toggle switch */
.toggle{position:relative;display:inline-block;width:44px;height:24px;vertical-align:middle}
.toggle input{opacity:0;width:0;height:0}
.slider{position:absolute;cursor:pointer;inset:0;background:#e2e8f0;border-radius:24px;transition:.25s}
.slider:before{position:absolute;content:"";height:18px;width:18px;left:3px;bottom:3px;background:#fff;border-radius:50%;transition:.25s;box-shadow:0 1px 3px rgba(0,0,0,.2)}
input:checked+.slider{background:var(--c-primary)}
input:checked+.slider:before{transform:translateX(20px)}

/* Modal */
.modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:999;display:none;align-items:center;justify-content:center;padding:16px}
.modal-bg.open{display:flex}
.modal{background:var(--c-surface);border-radius:18px;padding:28px;width:340px;max-width:100%}
.modal h3{font-size:16px;font-weight:700;margin-bottom:18px}
.strat-opt{border:1px solid var(--c-border);border-radius:10px;padding:12px 14px;cursor:pointer;transition:border-color .12s,background .12s}
.strat-opt:hover{border-color:var(--c-primary)}
.strat-opt.sel{border-color:var(--c-primary);background:rgba(0,200,5,.06);box-shadow:inset 0 0 0 1px var(--c-primary)}
.form-group{margin-bottom:14px}
.form-label{font-size:12px;color:var(--c-text2);font-weight:600;margin-bottom:5px;display:block}
.form-input{width:100%;padding:9px 12px;border:1.5px solid var(--c-border);border-radius:8px;font-size:14px;outline:none;transition:border .15s;background:var(--c-surface);color:var(--c-text)}
.form-input:focus{border-color:var(--c-primary)}

/* Chart */
.chart-wrap{position:relative;height:220px}

/* Section header */
.section-hd{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;flex-wrap:wrap;gap:8px}
.section-title{font-size:17px;font-weight:700;letter-spacing:-.2px}
.section-sub{font-size:12px;color:var(--c-text2);margin-top:2px}

/* Sections */
section{display:none}
section.active{display:block}

/* Control strip */
.ctrl-strip{display:flex;align-items:center;gap:0;flex-wrap:wrap;padding:0;background:var(--c-surface);border-radius:12px;box-shadow:0 1px 2px rgba(0,0,0,.04);border:1px solid var(--c-border);margin-bottom:24px;font-size:13px;overflow:hidden}
.ctrl-group{display:flex;align-items:center;gap:10px;padding:10px 16px}
.ctrl-group-state{flex:1;gap:12px}
.ctrl-group-action{background:var(--c-bg);border-left:1px solid var(--c-border);gap:8px}
.ctrl-divider{width:1px;height:22px;background:var(--c-border);flex-shrink:0}
.ctrl-item{display:flex;align-items:center;gap:7px}
.ctrl-spacer{flex:1}

/* Filter bars */
.filter-bar-row{display:flex;align-items:center;gap:10px;padding:7px 0;border-bottom:1px solid var(--c-border);font-size:12.5px}
.filter-bar-row:last-child{border-bottom:none}
.filter-name{min-width:130px;color:var(--c-text2);flex-shrink:0}
.filter-bar-bg{flex:1;height:7px;background:var(--c-bg);border-radius:4px;overflow:hidden}
.filter-bar-fill{height:100%;border-radius:4px;background:var(--c-primary);transition:width .4s}
.filter-count{min-width:40px;text-align:right;font-weight:600;color:var(--c-text)}

/* Log viewer */
#logBox{background:#1e2330;border-radius:10px;padding:14px;font-family:'Courier New',monospace;font-size:12px;height:380px;overflow-y:auto;white-space:pre-wrap;word-break:break-all;color:#a8b4cb;line-height:1.5}

/* Backtest tabs */
.bt-tabs{display:flex;gap:6px}
.bt-tab{padding:6px 14px;border-radius:7px;font-size:12.5px;font-weight:600;color:var(--c-text2);cursor:pointer;border:1.5px solid var(--c-border);background:none;transition:all .15s}
.bt-tab.active{border-color:var(--c-primary);color:var(--c-primary);background:#E8F9F2}

/* Toast */
.toast{position:fixed;top:20px;right:20px;background:#0A0B0D;color:#fff;padding:12px 16px;border-radius:12px;font-size:13px;font-weight:500;z-index:9999;opacity:0;transform:translateY(-8px);transition:all .25s;pointer-events:none;max-width:320px;border-left:3px solid var(--c-text2)}
.toast[data-type=ok]{border-left-color:var(--c-up-bright)}
.toast[data-type=err]{border-left-color:var(--c-down)}
.toast[data-type=warn]{border-left-color:var(--c-warn)}
.toast.show{opacity:1;transform:translateY(0)}

/* Mobile nav */
.mobile-nav{display:none;position:fixed;bottom:0;left:0;right:0;background:var(--c-surface);border-top:1px solid var(--c-border);z-index:200;padding-bottom:env(safe-area-inset-bottom,0px)}
.mobile-nav-inner{display:flex}
.mnav-btn{flex:1;display:flex;flex-direction:column;align-items:center;gap:3px;padding:10px 0 8px;font-size:10px;color:var(--c-text2);background:none;border:none;cursor:pointer;transition:color .15s}
.mnav-btn svg{width:22px;height:22px}
.mnav-btn.active{color:var(--c-primary);box-shadow:inset 0 2px 0 var(--c-primary)}

/* Refresh timestamp */
.refresh-ts{font-size:11px;color:var(--c-text2)}

/* Responsive — tablet */
@media(max-width:1024px){
  :root{--sidebar-w:60px}
  .sb-logo-text,.sb-logo-sub,.sb-nav li a span,.sb-section,.sb-status span{display:none}
  .sb-logo{justify-content:center;padding:16px 0}
  .sb-nav li a{justify-content:center;padding:12px 0;border-radius:10px}
  .sb-status{justify-content:center;padding:10px 0}
  .sb-dot{width:8px;height:8px}
  .main{padding:20px}
  .kpi-grid{grid-template-columns:repeat(2,1fr)}
  .grid-bot{grid-template-columns:1fr}
}
/* Responsive — mobile */
@media(max-width:640px){
  .sidebar{display:none}
  .main{margin-left:0;padding:12px;padding-bottom:78px}
  .mobile-nav{display:block}
  .kpi-grid{grid-template-columns:repeat(2,1fr);gap:10px}
  .kpi-card{padding:16px 16px 14px;border-radius:14px}
  .kpi-value{font-size:24px;letter-spacing:-.4px}
  .kpi-label{font-size:9.5px;margin-bottom:7px;letter-spacing:.5px}
  .grid-bot{grid-template-columns:1fr}
  .ctrl-strip{font-size:12px;flex-direction:column;gap:0}
  .ctrl-group{width:100%;box-sizing:border-box;padding:8px 10px;gap:6px}
  .ctrl-group-action{border-left:none;border-top:1px solid var(--c-border)}
  .ctrl-strip .ctrl-divider{display:none}
  .chart-wrap{height:180px}
  .btn-sm{padding:4px 8px;font-size:11.5px}
  .kpi-sub{white-space:normal;overflow:visible;text-overflow:unset;font-size:10.5px}
  .kpi-value{font-size:20px}
  .date-short{display:inline}
  .date-full{display:none}
  .tbl-name{max-width:100px}
  /* 리밸런싱 테이블: 현재 비중(3)·현재가(4) 숨김 — 종목·목표비중·보유수량·필요매매만 표시 */
  #sec-rebalance thead th:nth-child(3),
  #sec-rebalance thead th:nth-child(4),
  #rebalTbody td:nth-child(3),
  #rebalTbody td:nth-child(4){display:none}
  /* td 텍스트 줄바꿈 허용 + 패딩 축소 */
  td{white-space:normal;padding:9px 8px;font-size:12px}
  thead th{padding:8px 8px;font-size:10.5px}
}
</style>
</head>
<body>
<div class="layout">

<!-- ── Sidebar ── -->
<aside class="sidebar">
  <div class="sb-logo">
    <div class="sb-logo-icon">
      <svg viewBox="0 0 20 20"><path d="M10 2a8 8 0 1 0 0 16A8 8 0 0 0 10 2Zm1 5a1 1 0 1 0-2 0v3.586l-1.707 1.707a1 1 0 0 0 1.414 1.414L11 12.414l1.293 1.293a1 1 0 0 0 1.414-1.414L12 11V7a1 1 0 0 0-1-1Z"/></svg>
    </div>
    <div>
      <div class="sb-logo-text">Scanner v5.1</div>
      <div class="sb-logo-sub">kr_gem 멀티에셋</div>
    </div>
  </div>

  <div class="sb-section">메인</div>
  <ul class="sb-nav">
    <li><a href="#" class="active" data-sec="overview" onclick="showSection('overview');return false">
      <svg viewBox="0 0 20 20" fill="currentColor"><path d="M2 4a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V4Zm9 0a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2h-3a2 2 0 0 1-2-2V4Zm0 9a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2h-3a2 2 0 0 1-2-2v-3ZM2 13a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-3Z"/></svg>
      <span>개요</span>
    </a></li>
    <li><a href="#" data-sec="rebalance" onclick="showSection('rebalance');return false">
      <svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M15.312 11.424a5.5 5.5 0 0 1-9.201 2.466l-.312-.311h2.433a.75.75 0 0 0 0-1.5H3.989a.75.75 0 0 0-.75.75v4.242a.75.75 0 0 0 1.5 0v-2.43l.31.31a7 7 0 0 0 11.712-3.138.75.75 0 0 0-1.449-.39Zm1.23-3.723a.75.75 0 0 0 .219-.53V2.929a.75.75 0 0 0-1.5 0V5.36l-.31-.31A7 7 0 0 0 3.239 8.188a.75.75 0 0 0 1.448.389A5.5 5.5 0 0 1 13.89 6.11l.311.31h-2.432a.75.75 0 0 0 0 1.5h4.243a.75.75 0 0 0 .53-.219Z" clip-rule="evenodd"/></svg>
      <span>리밸런싱</span>
    </a></li>
    <li><a href="#" data-sec="history" onclick="showSection('history');return false">
      <svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm1-12a1 1 0 1 0-2 0v4a1 1 0 0 0 .293.707l2.828 2.829a1 1 0 1 0 1.415-1.415L11 9.586V6Z" clip-rule="evenodd"/></svg>
      <span>리밸런싱 내역</span>
    </a></li>
    <li><a href="#" data-sec="logs" onclick="showSection('logs');return false">
      <svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M2 5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5Zm3.293 1.293a1 1 0 0 1 1.414 0l3 3a1 1 0 0 1 0 1.414l-3 3a1 1 0 0 1-1.414-1.414L7.586 10 5.293 7.707a1 1 0 0 1 0-1.414ZM11 12a1 1 0 1 0 0 2h3a1 1 0 1 0 0-2h-3Z" clip-rule="evenodd"/></svg>
      <span>시스템 로그</span>
    </a></li>
  </ul>

  <div class="sb-bottom">
    <div class="sb-status">
      <div class="sb-dot green" id="sbDot"></div>
      <span id="sbStatusLabel" style="font-size:12px;color:var(--c-text2)">시스템 정상</span>
    </div>
  </div>
</aside>

<!-- ── Main ── -->
<main class="main">

<!-- Control strip -->
<div class="ctrl-strip">
  <!-- 상태 영역 -->
  <div class="ctrl-group ctrl-group-state">
    <div class="ctrl-item" style="gap:5px">
      <svg width="14" height="14" viewBox="0 0 20 20" fill="var(--c-text2)"><path fill-rule="evenodd" d="M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm1-12a1 1 0 1 0-2 0v4a1 1 0 0 0 .293.707l2.828 2.829a1 1 0 1 0 1.415-1.415L11 9.586V6Z" clip-rule="evenodd"/></svg>
      <span id="nowTs" style="font-size:12px;color:var(--c-text2)">--</span>
    </div>
    <div class="ctrl-divider"></div>
    <div class="ctrl-item">
      <span style="color:var(--c-text2)">자동매매</span>
      <label class="toggle"><input type="checkbox" id="atToggle" onchange="toggleAutoTrade(this.checked)"><span class="slider"></span></label>
      <span id="atLabel" style="font-size:12px;font-weight:700">OFF</span>
    </div>
    <div class="ctrl-spacer"></div>
    <span id="kisModeBadge" class="badge badge-blue"></span>
  </div>
</div>

<!-- ── Overview ── -->
<section id="sec-overview" class="active">

  <!-- Hero: 총 평가금액 -->
  <div class="hero">
    <div class="hero-label">총 평가금액</div>
    <div class="hero-value" id="kpiTotal">--</div>
    <div class="hero-delta up" id="kpiDelta">
      <span class="hero-arrow" id="kpiRetArrow"></span>
      <span id="kpiRet">--%</span>
      <span class="hero-delta-sub">매입원가 대비 평가손익</span>
    </div>
    <div class="hero-meta">
      <span>주식 <b id="kpiEquity">--</b></span>
      <span class="dot-sep"></span>
      <span>현금 <b id="kpiCash">--</b></span>
    </div>
  </div>

  <!-- Portfolio chart -->
  <div class="card" style="margin-bottom:20px;padding:18px 20px">
    <div class="section-hd" style="margin-bottom:6px">
      <div class="card-title" style="margin-bottom:0">평가금액 추이</div>
      <span class="refresh-ts" id="eqTs"></span>
    </div>
    <div class="chart-wrap" style="height:260px"><canvas id="equityChart"></canvas></div>
  </div>

  <!-- Secondary stats -->
  <div class="stat-row">
    <div class="stat"><div class="stat-label">보유 종목</div><div class="stat-num" id="kpiCount">--</div></div>
    <div class="stat"><div class="stat-label">현금 비중</div><div class="stat-num" id="kpiCashW">--%</div></div>
    <div class="stat"><div class="stat-label">자동매매</div><div class="stat-num" id="kpiAuto">--</div></div>
  </div>

  <!-- Holdings allocation -->
  <div class="card" style="margin-bottom:20px">
    <div class="card-title">현재 자산 배분 <span id="allocTotal"></span></div>
    <div class="reason-bar-list" id="allocRow">
      <div style="color:var(--c-text2);font-size:13px;padding:8px 0">데이터 로딩 중...</div>
    </div>
  </div>

  <!-- Bot status + Calendar (secondary) -->
  <div class="grid-bot">
    <div class="card bot-card">
      <div class="bot-header">
        <div class="bot-avatar">
          <svg viewBox="0 0 26 26"><circle cx="13" cy="9" r="5"/><path d="M4 22c0-4.418 4.03-8 9-8s9 3.582 9 8"/></svg>
        </div>
        <div>
          <div class="bot-name">kr_gem 리밸런싱 봇</div>
          <div class="bot-role" id="botRoleLabel">시스템 연결 중...</div>
        </div>
      </div>
      <div class="bot-stats">
        <div class="bot-stat">
          <div class="bot-stat-label">자동매매</div>
          <div class="bot-stat-value" id="autoTradeStatus">--</div>
        </div>
        <div class="bot-stat">
          <div class="bot-stat-label">KIS 모드</div>
          <div class="bot-stat-value" id="kisModeStatus">--</div>
        </div>
        <div class="bot-stat">
          <div class="bot-stat-label">보유</div>
          <div class="bot-stat-value" id="botHolding">--</div>
        </div>
        <div class="bot-stat">
          <div class="bot-stat-label">전략</div>
          <div class="bot-stat-value" id="botStrategy">--</div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="section-hd" style="margin-bottom:10px">
        <div class="card-title" style="margin-bottom:0">이달 리밸런싱 일정</div>
        <span id="ddayBadge" class="badge badge-green" style="display:none"></span>
      </div>
      <div id="miniCal"></div>
      <div class="schedule-list">
        <div class="schedule-item"><span class="schedule-time">매월 1일</span><span class="schedule-desc">첫 거래일 09:05 자동 리밸런싱</span></div>
        <div class="schedule-item"><span class="schedule-time">수시</span><span class="schedule-desc">대시보드 '리밸런싱' 탭에서 수동 실행</span></div>
        <div class="schedule-item"><span class="schedule-time">09:00</span><span class="schedule-desc">Heartbeat — 봇 생존 확인</span></div>
      </div>
    </div>
  </div>

</section>

<!-- ── Rebalance ── -->
<section id="sec-rebalance">
  <div class="section-hd">
    <div>
      <div class="section-title">리밸런싱</div>
      <div class="section-sub" id="rebalTs">마지막 리밸런싱: --</div>
    </div>
    <button class="btn btn-primary btn-sm" onclick="confirmRebalance()">지금 리밸런싱 실행</button>
  </div>
  <!-- Strategy banner -->
  <div class="card" style="margin-bottom:16px;display:flex;align-items:center;gap:14px">
    <div style="flex:1;min-width:0">
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <span style="font-weight:700;font-size:15px" id="stratName">전략 로딩 중...</span>
        <span class="badge badge-blue" id="stratProfile" style="display:none"></span>
      </div>
      <div style="font-size:12.5px;color:var(--c-text2);margin-top:4px" id="stratDesc"></div>
    </div>
    <button class="btn btn-outline btn-sm" onclick="openStrategyModal()">전략 변경</button>
  </div>
  <div class="kpi-grid" style="margin-bottom:16px">
    <div class="kpi-card"><div class="kpi-label">총 평가금액</div><div class="kpi-value neutral" id="rbTotal">--</div></div>
    <div class="kpi-card"><div class="kpi-label">주문가능 현금</div><div class="kpi-value neutral" id="rbCash">--</div></div>
    <div class="kpi-card"><div class="kpi-label">최소 필요금액</div><div class="kpi-value neutral" id="rbMin">--</div><div class="kpi-sub">3종목 각 1주 이상 매수 기준</div></div>
  </div>
  <div id="rbWarn" style="display:none;margin-bottom:16px;padding:12px 14px;border-radius:10px;background:#FEF3C7;border:1px solid #FCD34D;color:#92400E;font-size:13px"></div>
  <div class="card">
    <div class="tbl-wrap">
      <table>
        <thead><tr>
          <th>종목</th><th>목표 비중</th><th>현재 비중</th><th>현재가</th><th>보유 수량</th><th>필요 매매</th>
        </tr></thead>
        <tbody id="rebalTbody">
          <tr><td colspan="6" style="text-align:center;color:var(--c-text2);padding:40px">로딩 중...</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</section>

<!-- ── Rebalance history ── -->
<section id="sec-history">
  <div class="section-hd">
    <div>
      <div class="section-title">리밸런싱 내역</div>
      <div class="section-sub">실행된 리밸런싱 이벤트별 매매 내역</div>
    </div>
  </div>
  <div id="rbHistList">
    <div class="card"><div style="color:var(--c-text2);font-size:13px;padding:8px 0">리밸런싱 실행 이력이 없습니다</div></div>
  </div>
</section>

<!-- ── Logs ── -->
<section id="sec-logs">
  <div class="section-hd">
    <div class="section-title">시스템 로그</div>
    <div style="display:flex;gap:8px;align-items:center">
      <select id="logLines" class="form-input" style="width:90px;padding:6px 10px;font-size:12px">
        <option value="50">50줄</option>
        <option value="100" selected>100줄</option>
        <option value="200">200줄</option>
      </select>
      <button class="btn btn-outline btn-sm" onclick="loadLogs()">새로고침</button>
    </div>
  </div>
  <div id="logBox">(로그 로딩 중...)</div>
</section>

</main>
</div>

<!-- Mobile nav -->
<nav class="mobile-nav">
<div class="mobile-nav-inner">
  <button class="mnav-btn active" onclick="showSection('overview');mnavSet(this)">
    <svg viewBox="0 0 20 20" fill="currentColor"><path d="M2 4a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V4Zm9 0a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2h-3a2 2 0 0 1-2-2V4Zm0 9a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2h-3a2 2 0 0 1-2-2v-3ZM2 13a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-3Z"/></svg>
    개요
  </button>
  <button class="mnav-btn" onclick="showSection('rebalance');mnavSet(this)">
    <svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M15.312 11.424a5.5 5.5 0 0 1-9.201 2.466l-.312-.311h2.433a.75.75 0 0 0 0-1.5H3.989a.75.75 0 0 0-.75.75v4.242a.75.75 0 0 0 1.5 0v-2.43l.31.31a7 7 0 0 0 11.712-3.138.75.75 0 0 0-1.449-.39Zm1.23-3.723a.75.75 0 0 0 .219-.53V2.929a.75.75 0 0 0-1.5 0V5.36l-.31-.31A7 7 0 0 0 3.239 8.188a.75.75 0 0 0 1.448.389A5.5 5.5 0 0 1 13.89 6.11l.311.31h-2.432a.75.75 0 0 0 0 1.5h4.243a.75.75 0 0 0 .53-.219Z" clip-rule="evenodd"/></svg>
    리밸런싱
  </button>
  <button class="mnav-btn" onclick="showSection('history');mnavSet(this)">
    <svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm1-12a1 1 0 1 0-2 0v4a1 1 0 0 0 .293.707l2.828 2.829a1 1 0 1 0 1.415-1.415L11 9.586V6Z" clip-rule="evenodd"/></svg>
    내역
  </button>
  <button class="mnav-btn" onclick="showSection('logs');mnavSet(this)">
    <svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M2 5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5Z" clip-rule="evenodd"/></svg>
    로그
  </button>
</div>
</nav>

<!-- Sell Modal -->
<div class="modal-bg" id="sellModal">
  <div class="modal">
    <h3 style="display:flex;align-items:center;gap:8px"><svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 16.5v.75A.75.75 0 0 0 3.75 18h12.5a.75.75 0 0 0 .75-.75v-.75M10 3v10.5m0 0-3-3m3 3 3-3"/></svg>수동 청산</h3>
    <div class="form-group">
      <label class="form-label">종목</label>
      <input type="text" id="sellName" class="form-input" readonly>
    </div>
    <div class="form-group">
      <label class="form-label">수량 <span style="font-weight:400">(보유: <span id="sellQtyHeld"></span>주)</span></label>
      <input type="number" id="sellQty" class="form-input" min="1">
    </div>
    <div style="display:flex;gap:8px;margin-top:6px">
      <button class="btn btn-danger" style="flex:1" onclick="confirmSell()">청산 실행</button>
      <button class="btn btn-outline" style="flex:1" onclick="closeModal('sellModal')">취소</button>
    </div>
  </div>
</div>

<!-- Strategy Modal -->
<div class="modal-bg" id="strategyModal">
  <div class="modal" style="max-width:520px">
    <h3 style="display:flex;align-items:center;gap:8px"><svg width="18" height="18" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h14M3 10h14M3 14h9"/></svg>리밸런싱 전략 선택</h3>
    <div style="font-size:12px;color:var(--c-text2);margin:-4px 0 12px">선택한 전략은 다음 리밸런싱(월간 자동 또는 수동 실행)부터 적용됩니다. 변경 시 봇이 재시작됩니다.</div>
    <div id="strategyList" style="display:flex;flex-direction:column;gap:8px;max-height:50vh;overflow-y:auto"></div>
    <div style="display:flex;gap:8px;margin-top:14px">
      <button class="btn btn-primary" style="flex:1" onclick="applyStrategy()">적용</button>
      <button class="btn btn-outline" style="flex:1" onclick="closeModal('strategyModal')">취소</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const TOKEN = "__TOKEN__";
const KR_HOLIDAYS = __HOLIDAYS__;
let REBAL_EXEC = new Set();   // 과거 리밸런싱 실행일 (YYYY-MM-DD)
let NEXT_REBAL = "";          // 다음 리밸런싱 예정일 (YYYY-MM-DD)
let STRATEGIES_CACHE = [];    // 전략 목록
let CUR_STRATEGY = "";        // 현재 활성 전략 key
let SEL_STRATEGY = "";        // 모달에서 선택 중인 전략 key
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);

// ── Section routing ────────────────────────────────────────────
function showSection(sec) {
  $$("section").forEach(s => s.classList.remove("active"));
  const el = document.getElementById("sec-" + sec);
  if (el) el.classList.add("active");
  $$(".sb-nav li a").forEach(a => a.classList.toggle("active", a.dataset.sec === sec));
  if (sec === "logs") loadLogs();
  if (sec === "rebalance") loadRebalance();
  if (sec === "history") loadRebalanceHistory();
}

// ── Rebalance ──────────────────────────────────────────────────
const won = v => (v || 0).toLocaleString() + "원";
// ── Strategy ───────────────────────────────────────────────────
const PROFILE_BADGE = {"방어":"badge-blue","밸런스":"badge-green","공격":"badge-red"};
function loadStrategies() {
  return fetch("/api/strategies?token=" + TOKEN).then(r => r.json()).then(d => {
    STRATEGIES_CACHE = d.strategies || [];
    CUR_STRATEGY = d.current || "";
    const s = STRATEGIES_CACHE.find(x => x.key === CUR_STRATEGY);
    if (s) {
      $("#stratName").textContent = s.name;
      $("#stratDesc").textContent = s.description;
      const pb = $("#stratProfile");
      pb.textContent = s.profile; pb.className = "badge " + (PROFILE_BADGE[s.profile] || "badge-gray");
      pb.style.display = "inline-block";
    }
  }).catch(() => {});
}
function openStrategyModal() {
  const render = () => {
    SEL_STRATEGY = CUR_STRATEGY;
    $("#strategyList").innerHTML = STRATEGIES_CACHE.map(s => {
      const seed = s.min_seed >= 10000 ? (s.min_seed/10000).toLocaleString() + "만원" : s.min_seed + "원";
      return `<div class="strat-opt" data-key="${s.key}" onclick="selectStrategyCard('${s.key}')">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <span style="font-weight:700">${s.name}</span>
          <span class="badge ${PROFILE_BADGE[s.profile]||'badge-gray'}" style="font-size:10px">${s.profile}</span>
          <span style="font-size:11px;color:var(--c-text2)">상위 ${s.top_n}종목 · 권장 ${seed}+</span>
        </div>
        <div style="font-size:12px;color:var(--c-text2);margin-top:4px">${s.description}</div>
      </div>`;
    }).join("");
    selectStrategyCard(CUR_STRATEGY);
    $("#strategyModal").classList.add("open");
  };
  if (STRATEGIES_CACHE.length) render(); else loadStrategies().then(render);
}
function selectStrategyCard(key) {
  SEL_STRATEGY = key;
  $$("#strategyList .strat-opt").forEach(el => el.classList.toggle("sel", el.dataset.key === key));
}
function applyStrategy() {
  if (!SEL_STRATEGY) return;
  if (SEL_STRATEGY === CUR_STRATEGY) { toast("이미 적용 중인 전략입니다"); closeModal("strategyModal"); return; }
  const s = STRATEGIES_CACHE.find(x => x.key === SEL_STRATEGY) || {};
  if (!confirm(`리밸런싱 전략을 '${s.name}'(으)로 변경할까요?\n봇이 재시작되며, 다음 리밸런싱부터 적용됩니다.`)) return;
  fetch("/api/strategy?token=" + TOKEN, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({key: SEL_STRATEGY})
  }).then(r => r.json()).then(d => {
    toast(d.ok ? "✅ " + d.msg : "❌ " + d.msg);
    closeModal("strategyModal");
    if (d.ok) setTimeout(() => { loadStrategies(); loadRebalance(); loadPortfolio(); }, 2500);
  }).catch(() => toast("❌ 전략 변경 실패"));
}

function loadRebalance() {
  loadStrategies();
  fetch("/api/rebalance?token=" + TOKEN).then(r => r.json()).then(d => {
    if (!d.ok) { toast("❌ " + d.msg); $("#rebalTbody").innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--c-text2);padding:40px">목표 비중 계산 실패 — 잠시 후 다시 시도하세요</td></tr>`; return; }
    $("#rebalTs").textContent = "마지막 리밸런싱: " + (d.last_rebalance || "없음");
    $("#rbTotal").textContent = won(d.total_value);
    $("#rbCash").textContent  = won(d.cash);
    $("#rbMin").textContent   = won(d.min_required);
    const warn = $("#rbWarn");
    if (d.min_required && d.total_value < d.min_required) {
      const short = d.min_required - d.total_value;
      warn.style.display = "block";
      warn.innerHTML = `<svg width="15" height="15" viewBox="0 0 20 20" fill="currentColor" style="vertical-align:-2px;margin-right:5px"><path fill-rule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.515 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495ZM10 5a.75.75 0 0 1 .75.75v3.5a.75.75 0 0 1-1.5 0v-3.5A.75.75 0 0 1 10 5Zm0 9a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z" clip-rule="evenodd"/></svg>현재 평가금액(${won(d.total_value)})이 최소 필요금액(${won(d.min_required)})보다 적어 일부 종목을 1주도 매수할 수 없습니다. 약 <b>${won(short)}</b> 추가 입금이 필요합니다.`;
    } else {
      warn.style.display = "none";
    }
    $("#rebalTbody").innerHTML = d.rows.length ? d.rows.map(row => {
      const diff = row.diff_qty || 0;
      const diffStr = diff === 0 ? '<span style="color:var(--c-text2)">유지</span>'
        : diff > 0 ? `<span style="color:#16a34a;font-weight:600">+${diff} 매수</span>`
        : `<span style="color:#dc2626;font-weight:600">${diff} 매도</span>`;
      return `<tr>
        <td><div style="font-weight:600">${row.name}</div><div style="font-size:11px;color:var(--c-text2)">${row.ticker}</div></td>
        <td><b>${(row.weight||0).toFixed(1)}%</b></td>
        <td>${(row.current_weight||0).toFixed(1)}%</td>
        <td>${(row.current_price||row.price||0).toLocaleString()}</td>
        <td>${row.current_qty||0}주</td>
        <td>${diffStr}</td>
      </tr>`;
    }).join("") : `<tr><td colspan="6" style="text-align:center;color:var(--c-text2);padding:40px">목표 종목 없음</td></tr>`;
  }).catch(() => toast("❌ 리밸런싱 데이터 조회 실패"));
}
function confirmRebalance() {
  if (!confirm("지금 kr_gem 리밸런싱을 실행할까요?\n실행 중인 봇이 매도/매수 주문을 즉시 전송합니다.")) return;
  fetch("/api/rebalance/execute?token=" + TOKEN, {method: "POST"}).then(r => r.json()).then(d => {
    toast(d.ok ? "✅ " + d.msg : "❌ " + d.msg);
  }).catch(() => toast("❌ 리밸런싱 요청 실패"));
}
function loadRebalanceHistory() {
  fetch("/api/rebalance/history?token=" + TOKEN).then(r => r.json()).then(d => {
    const box = $("#rbHistList");
    if (!d.events || !d.events.length) {
      box.innerHTML = `<div class="card"><div style="color:var(--c-text2);font-size:13px;padding:8px 0">리밸런싱 실행 이력이 없습니다</div></div>`;
      return;
    }
    box.innerHTML = d.events.map(ev => {
      const orders = (ev.orders||[]).map(o => {
        const buy = o.side === "buy";
        const badge = buy ? '<span class="badge badge-green" style="font-size:10px">매수</span>'
                          : '<span class="badge badge-red" style="font-size:10px">매도</span>';
        const pnl = (!buy && o.pnl_pct != null)
          ? ` <span style="${o.pnl_pct>=0?'color:#16a34a':'color:#dc2626'};font-size:11px">(${o.pnl_pct>=0?'+':''}${o.pnl_pct}%)</span>` : "";
        return `<div style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:13px">
          ${badge}<span style="flex:1">${o.name} <span style="color:var(--c-text2);font-size:11px">${o.ticker}</span></span>
          <span>${o.qty}주 @ ${(o.price||0).toLocaleString()}${pnl}</span></div>`;
      }).join("") || '<div style="color:var(--c-text2);font-size:12px;padding:4px 0">주문 변경 없음</div>';
      return `<div class="card" style="margin-bottom:14px">
        <div class="section-hd" style="margin-bottom:10px">
          <div class="card-title">${ev.ts}</div>
          <span style="font-size:12px;color:var(--c-text2)">평가금액 ${won(ev.total_value)}</span>
        </div>${orders}</div>`;
    }).join("");
  }).catch(() => {});
}
function mnavSet(btn) {
  $$(".mnav-btn").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
}

// ── Toast ──────────────────────────────────────────────────────
function toast(msg, dur=2800) {
  const el = $("#toast");
  let type = "info";
  if (/^[✅▶]/.test(msg)) type = "ok";
  else if (/^[❌]/.test(msg)) type = "err";
  else if (/^[⚠⏸]/.test(msg)) type = "warn";
  el.textContent = msg.replace(/^[✅❌⚠▶⏸️\s]+/, "");
  el.dataset.type = type;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), dur);
}

// ── Modals ─────────────────────────────────────────────────────
function closeModal(id) { $("#" + id).classList.remove("open"); }
let _sellTicker = "", _editTicker = "";

function openSell(ticker, name, qty) {
  _sellTicker = ticker;
  $("#sellName").value = name + " (" + ticker + ")";
  $("#sellQtyHeld").textContent = qty;
  $("#sellQty").value = qty;
  $("#sellModal").classList.add("open");
}
function confirmSell() {
  const qty = parseInt($("#sellQty").value);
  if (!qty || qty < 1) { toast("수량을 입력하세요"); return; }
  fetch("/api/sell/" + _sellTicker + "?token=" + TOKEN, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({qty, name: $("#sellName").value.split(" (")[0]})
  }).then(r => r.json()).then(d => {
    toast(d.ok ? "✅ 청산 완료 — 주문번호: " + d.order_no : "❌ " + d.msg);
    closeModal("sellModal");
    if (d.ok) setTimeout(loadPortfolio, 1500);
  }).catch(() => toast("❌ 청산 요청 실패"));
}

// ── Controls ───────────────────────────────────────────────────
function toggleAutoTrade(on) {
  fetch("/api/control?token=" + TOKEN, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({action: on ? "autotrade_on" : "autotrade_off"})
  }).then(r => r.json()).then(d => toast(d.ok ? "✅ " + d.msg : "❌ " + d.msg))
    .catch(() => toast("❌ 요청 실패"));
}

// ── Mini calendar ──────────────────────────────────────────────
function renderCalendar() {
  const now = new Date(), y = now.getFullYear(), m = now.getMonth(), today = now.getDate();
  const firstDow = new Date(y, m, 1).getDay();
  const daysInMonth = new Date(y, m + 1, 0).getDate();
  const mn = ["1월","2월","3월","4월","5월","6월","7월","8월","9월","10월","11월","12월"];
  let h = `<div class="cal-month">${y}년 ${mn[m]}</div>`;
  h += `<div class="cal-header">`;
  ["일","월","화","수","목","금","토"].forEach(d => { h += `<div class="cal-h">${d}</div>`; });
  h += `</div><div class="cal-grid">`;
  for (let i = 0; i < firstDow; i++) h += `<div class="cal-d other-month"></div>`;
  for (let d = 1; d <= daysInMonth; d++) {
    const dow = (firstDow + d - 1) % 7;
    const dateStr = `${y}-${String(m+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    const holName = KR_HOLIDAYS[dateStr];
    const isExec = REBAL_EXEC.has(dateStr);
    const isNext = dateStr === NEXT_REBAL;
    let cls = "cal-d";
    if (d === today) cls += " today";
    if (dow === 0) cls += " sun";
    else if (dow === 6) cls += " sat";
    if (holName && dow > 0 && dow < 6) cls += " holiday";
    if (isExec) cls += " rebal-exec";
    if (isNext) cls += " rebal-next";
    const dot = isExec ? `<span class="rebal-dot"></span>` : (holName ? `<span class="hol-dot"></span>` : "");
    const titles = [holName, isExec ? "리밸런싱 실행" : null, isNext ? "다음 리밸런싱 예정" : null].filter(Boolean);
    const title = titles.length ? ` title="${titles.join(' · ')}"` : "";
    h += `<div class="${cls}"${title}>${d}${dot}</div>`;
  }
  h += `</div>`;
  $("#miniCal").innerHTML = h;
}

// ── Allocation bar chart ───────────────────────────────────────
const ALLOC_COLORS = ["#00C805","#0A0B0D","#00A843","#6F7780","#9AA0A6","#C9CDD2"];
function renderAllocation(holdings, cashWeight) {
  const row = $("#allocRow");
  const items = (holdings || []).map(h => ({label: h.name, pct: h.current_weight || 0}));
  if (cashWeight > 0) items.push({label: "현금", pct: cashWeight});
  $("#allocTotal").textContent = items.length ? `${items.length}개 자산` : "";
  if (!items.length) {
    row.innerHTML = '<div style="color:var(--c-text2);font-size:13px;padding:8px 0">보유 자산 없음 — 리밸런싱 실행 전</div>';
    return;
  }
  items.sort((a, b) => b.pct - a.pct);
  row.innerHTML = items.map((it, i) => `
    <div class="alloc-row">
      <div class="alloc-top">
        <span class="alloc-name" title="${it.label}">${it.label}</span>
        <span class="alloc-pct">${it.pct.toFixed(1)}%</span>
      </div>
      <div class="alloc-track">
        <div class="alloc-fill" style="width:${Math.min(100,it.pct)}%;background:${it.label==='현금'?'#94a3b8':ALLOC_COLORS[i%ALLOC_COLORS.length]}"></div>
      </div>
    </div>`).join("");
}

// ── 평가금액 추이 (원) ─────────────────────────────────────────
let equityChart = null;
const wonShort = v => v >= 1e8 ? (v/1e8).toFixed(2)+"억" : v >= 1e4 ? Math.round(v/1e4).toLocaleString()+"만" : (v||0).toLocaleString();
function renderReturnCurve(dates, curve, benchmark) {
  const canvas = $("#equityChart");
  const ctx = canvas.getContext("2d");
  if (equityChart) equityChart.destroy();
  const grad = ctx.createLinearGradient(0, 0, 0, canvas.clientHeight || 220);
  grad.addColorStop(0, "rgba(0,200,5,.16)");
  grad.addColorStop(1, "rgba(0,0,0,0)");
  const hasBench = benchmark && benchmark.some(v => v != null);
  const datasets = [{
    label: "내 평가금액", data: curve,
    borderColor: "#00C805", backgroundColor: grad,
    fill: true, tension: 0.25, pointRadius: dates.length <= 12 ? 2.5 : 0, pointHoverRadius: 4,
    pointHoverBackgroundColor: "#00C805", pointHoverBorderColor: "#fff", borderWidth: 2
  }];
  if (hasBench) datasets.push({
    label: "KOSPI200 (동일 시작금액)", data: benchmark,
    borderColor: "#9AA0A6", backgroundColor: "transparent",
    fill: false, tension: 0.25, pointRadius: 0, pointHoverRadius: 4,
    borderWidth: 1.5, borderDash: [5, 4], spanGaps: true
  });
  equityChart = new Chart(ctx, {
    type: "line",
    data: { labels: dates, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: {mode: "index", intersect: false},
      plugins: {
        legend: {display: hasBench, position: "top", align: "end",
                 labels: {boxWidth: 12, boxHeight: 2, font: {size: 11}, color: "#6F7780", usePointStyle: false}},
        tooltip: {callbacks: {label: item => " " + item.dataset.label + ": " + (item.raw==null?"-":item.raw.toLocaleString()+"원")}}
      },
      scales: {
        x: {ticks: {maxTicksLimit: 8, font: {size: 11}, color: "#94a3b8"}, grid: {display: false}},
        y: {ticks: {font: {size: 11}, color: "#94a3b8", callback: v => wonShort(v)}, grid: {color: "#f0f4f8"}}
      }
    }
  });
}

// ── Portfolio (overview) ───────────────────────────────────────
function loadPortfolio() {
  fetch("/api/portfolio?token=" + TOKEN)
    .then(r => { if (!r.ok) throw r; return r.json(); })
    .then(d => {
      $("#nowTs").textContent = d.now;
      const at = !!d.auto_trade;
      $("#atToggle").checked = at;
      $("#atLabel").textContent = at ? "ON" : "OFF";
      $("#atLabel").style.color = at ? "#16a34a" : "#94a3b8";
      $("#kisModeBadge").textContent = d.kis_mode;
      $("#kisModeStatus").textContent = d.kis_mode === "실전투자" ? "실전" : "모의";
      $("#botRoleLabel").textContent = d.kis_mode + " 운용 중";
      $("#autoTradeStatus").textContent = at ? "ON" : "OFF";
      $("#autoTradeStatus").style.color = at ? "#16a34a" : "#94a3b8";
      $("#botHolding").textContent = d.count + "종목";
      if (d.strategy_name) $("#botStrategy").textContent = d.strategy_name;

      $("#kpiTotal").textContent  = won(d.total);
      $("#kpiEquity").textContent = won(d.equity);
      $("#kpiCash").textContent   = won(d.cash);
      const ret = d.total_return || 0;
      const upRet = ret >= 0;
      $("#kpiRet").textContent = (upRet ? "+" : "") + ret.toFixed(2) + "%";
      const delta = $("#kpiDelta");
      if (delta) delta.className = "hero-delta " + (upRet ? "up" : "down");
      const arrow = $("#kpiRetArrow");
      if (arrow) arrow.innerHTML = upRet
        ? '<svg width="14" height="14" viewBox="0 0 20 20" fill="currentColor"><path d="M10 3.5 16 11h-4v5.5H8V11H4l6-7.5Z"/></svg>'
        : '<svg width="14" height="14" viewBox="0 0 20 20" fill="currentColor"><path d="M10 16.5 4 9h4V3.5h4V9h4l-6 7.5Z"/></svg>';
      $("#kpiCount").textContent = d.count;
      $("#kpiCashW").textContent = (d.cash_weight || 0).toFixed(1) + "%";
      const ka = $("#kpiAuto");
      if (ka) { ka.textContent = at ? "ON" : "OFF"; ka.style.color = at ? "var(--c-up)" : "var(--c-text2)"; }

      renderAllocation(d.holdings || [], d.cash_weight || 0);

      // 다음 리밸런싱 D-day 배지 + 캘린더 하이라이트
      if (d.next_rebalance) {
        NEXT_REBAL = d.next_rebalance;
        const badge = $("#ddayBadge");
        if (badge) {
          const dd = d.d_day;
          const md = d.next_rebalance.slice(5).replace("-", "/");
          badge.textContent = (dd === 0 ? "오늘 리밸런싱" : "D-" + dd) + " · " + md;
          badge.style.display = "inline-block";
        }
        renderCalendar();
      }
    })
    .catch(() => toast("⚠️ 데이터 로드 실패"));

  fetch("/api/rebalance/history?token=" + TOKEN)
    .then(r => r.json())
    .then(d => {
      renderReturnCurve(d.dates || [], d.value_curve || [], d.benchmark_value || []);
      const n = (d.dates ? d.dates.length : 0);
      $("#eqTs").textContent = n ? n + "일 추이" : "데이터 없음";
      REBAL_EXEC = new Set(d.exec_dates || []);
      renderCalendar();
    })
    .catch(() => {});
}

// ── Logs ───────────────────────────────────────────────────────
function loadLogs() {
  const n = $("#logLines").value || 100;
  fetch("/api/logs?token=" + TOKEN + "&lines=" + n)
    .then(r => r.json())
    .then(d => {
      const box = $("#logBox");
      box.textContent = (d.lines || []).join("\n") || "(로그 없음)";
      box.scrollTop = box.scrollHeight;
    })
    .catch(() => { $("#logBox").textContent = "로그 로드 실패"; });
}

// ── Init ───────────────────────────────────────────────────────
renderCalendar();
loadPortfolio();
loadRebalance();
setInterval(loadPortfolio, 60000);
</script>
</body>
</html>"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081)
