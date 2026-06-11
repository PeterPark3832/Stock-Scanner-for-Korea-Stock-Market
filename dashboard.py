"""
Stock Scanner Dashboard v5.1
- TeamHub 라이트 테마 (민트 그린 #2ECC88 / 배경 #F2F6FB)
- PC: 220px 고정 사이드바 + 섹션 라우팅
- Mobile: 하단 탭 네비 (5개 섹션)
접속: http://<서버IP>:8081?token=<DASHBOARD_TOKEN>
"""
import csv, json, os, re, subprocess, threading, time, requests
from datetime import datetime
from zoneinfo import ZoneInfo
from filelock import FileLock
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

DASHBOARD_TOKEN  = read_env("DASHBOARD_TOKEN", "scanner2024")
_token_cache     = {"token": None, "expires_at": 0}
_token_lock      = threading.Lock()
_cache_lock      = threading.Lock()
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

def get_price(ticker: str) -> dict | None:
    token = get_kis_token()
    if not token:
        return None
    try:
        r = requests.get(f"{_kis_base()}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers={"Authorization": f"Bearer {token}",
                     "appkey": read_env("KIS_APP_KEY"),
                     "appsecret": read_env("KIS_APP_SECRET"),
                     "tr_id": "FHKST01010100"},
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}, timeout=5)
        d = r.json()
        if d.get("rt_cd") == "0":
            return {"current": int(d["output"].get("stck_prpr", 0))}
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

_hist_cache: dict = {"mtime": -1, "rows": [], "stats": {}, "dates": [], "curve": [], "reasons": {}}

def get_history_cached() -> dict:
    with _cache_lock:
        try:
            mtime = os.path.getmtime(HISTORY_FILE)
        except FileNotFoundError:
            return _hist_cache
        if mtime == _hist_cache["mtime"]:
            return _hist_cache
        rows = []
        try:
            with open(HISTORY_FILE, encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        except Exception:
            pass
        wins = [r for r in rows if float(r["pnl_pct"]) > 0]
        loss = [r for r in rows if float(r["pnl_pct"]) <= 0]
        gw   = sum(float(r["pnl_pct"]) for r in wins)
        gl   = abs(sum(float(r["pnl_pct"]) for r in loss))
        stats = dict(
            total    = len(rows), wins = len(wins), losses = len(loss),
            win_rate = round(len(wins)/len(rows)*100, 1) if rows else 0.0,
            avg_win  = round(gw/len(wins), 2) if wins else 0.0,
            avg_loss = round(-gl/len(loss), 2) if loss else 0.0,
            pf       = round(gw/gl, 2) if gl else 0.0,
            cum_pct  = round(sum(float(r["pnl_pct"]) for r in rows), 2),
        )
        cum, dates, curve = 0.0, [], []
        for r in rows:
            cum += float(r["pnl_pct"])
            curve.append(round(cum, 2))
            dates.append(r["exit_date"][5:])
        reasons: dict[str, int] = {}
        for r in rows:
            reasons[r["exit_reason"]] = reasons.get(r["exit_reason"], 0) + 1
        _hist_cache.update(mtime=mtime, rows=rows, stats=stats,
                           dates=dates, curve=curve, reasons=reasons)
        return _hist_cache

def enrich_positions(positions: list[dict]) -> list[dict]:
    result = []
    for p in positions:
        live  = get_price(p["ticker"])
        entry = p.get("entry", 0)
        tp    = p.get("tp", 0)
        sl    = p.get("sl", 0)
        cur   = live["current"] if live else 0
        pnl   = round((cur - entry)/entry*100, 2) if entry and cur else None
        rng   = (tp - sl) if tp > sl else 1
        prog  = max(0, min(100, round((cur - sl)/rng*100))) if cur else 50
        result.append({**p, "current": cur, "pnl_pct": pnl, "progress": prog,
                       "is_trailing": p.get("sl", 0) > p.get("sl_init", p.get("sl", 0)),
                       "live_ok": live is not None})
    return result

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

@app.get("/api/data")
def api_data(token: str = ""):
    auth(token)
    hc        = get_history_cached()
    positions = enrich_positions(load_positions())
    recent    = [{"name": r["name"], "ticker": r["ticker"],
                  "exit_date": r["exit_date"], "exit_reason": r["exit_reason"],
                  "pnl_pct": float(r["pnl_pct"]),
                  "entry_price": int(r.get("entry_price", 0)),
                  "exit_price":  int(r.get("exit_price", 0)),
                  "post_expire_pnl": r.get("post_expire_pnl", "")}
                 for r in reversed(hc["rows"][-20:])]
    return JSONResponse({
        "now":          datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        "auto_trade":   read_env("AUTO_TRADE", "false").lower() == "true",
        "kis_mode":     "실전투자" if read_env("KIS_MODE", "paper") == "real" else "모의투자",
        "trade_amount": int(read_env("TRADE_AMOUNT_PER_STOCK", "200000")),
        "stats":        hc["stats"],
        "positions":    positions,
        "history":      recent,
        "equity_dates": hc["dates"],
        "equity_curve": hc["curve"],
        "reasons":      hc["reasons"],
    })

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
    if action in ("pause", "resume"):
        open(os.path.join(BASE_DIR, f"_{action}.flag"), "w").close()
        send_telegram({"pause": "🖥️ *대시보드* — 신호 발송 정지",
                       "resume": "🖥️ *대시보드* — 신호 발송 재개"}[action])
        return JSONResponse({"ok": True, "msg": f"{action} 명령 전달"})
    return JSONResponse({"ok": False, "msg": "알 수 없는 액션"}, status_code=400)

@app.post("/api/set_trade_amount")
async def api_set_trade_amount(request: Request, token: str = ""):
    auth(token)
    body = await request.json()
    try:
        amount = int(body.get("amount", 0))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "msg": "금액은 숫자여야 합니다"}, status_code=400)
    if amount < 100_000:
        return JSONResponse({"ok": False, "msg": "최소 100,000원 이상 입력하세요"}, status_code=400)
    if amount > 100_000_000:
        return JSONResponse({"ok": False, "msg": "최대 1억원까지 설정 가능합니다"}, status_code=400)
    old = int(read_env("TRADE_AMOUNT_PER_STOCK", "1000000"))
    write_env("TRADE_AMOUNT_PER_STOCK", str(amount))
    subprocess.Popen(["systemctl", "restart", "stock-scanner"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    send_telegram(
        f"🖥️ *대시보드* — 종목당 투자금액 변경\n"
        f"{old:,}원 → {amount:,}원\n봇 재시작 중..."
    )
    return JSONResponse({"ok": True, "msg": f"종목당 {amount:,}원으로 변경 — 봇 재시작 중", "amount": amount})

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

@app.get("/api/backtest")
def api_backtest(token: str = ""):
    auth(token)
    results = {}
    for fname, label, kw in [("backtest_results.csv","v1","A_눌림목"),
                               ("backtest_v2_results.csv","v2","A_눌림목v2")]:
        path = os.path.join(BASE_DIR, fname)
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8-sig") as f:
                rows = [r for r in csv.DictReader(f) if kw in r.get("strategy","")]
        except Exception:
            continue
        if not rows:
            continue
        wins = [r for r in rows if float(r["pnl_pct"]) > 0]
        loss = [r for r in rows if float(r["pnl_pct"]) <= 0]
        gw   = sum(float(r["pnl_pct"]) for r in wins)
        gl   = abs(sum(float(r["pnl_pct"]) for r in loss))
        cum, curve, dates = 0.0, [], []
        for r in rows:
            cum += float(r["pnl_pct"])
            curve.append(round(cum, 2))
            dates.append(r["exit_date"][5:] if r.get("exit_date") else "")
        results[label] = dict(label=f"백테스트 {label} ({kw})", total=len(rows),
            wins=len(wins), losses=len(loss),
            win_rate=round(len(wins)/len(rows)*100,1) if rows else 0.0,
            avg_win=round(gw/len(wins),2) if wins else 0.0,
            avg_loss=round(-gl/len(loss),2) if loss else 0.0,
            pf=round(gw/gl,2) if gl else 0.0,
            cum_pct=round(sum(float(r["pnl_pct"]) for r in rows),2),
            curve=curve, dates=dates)
    hc = get_history_cached()
    results["live"] = dict(label="실거래", **{k: hc["stats"].get(k,0) for k in
        ["total","wins","losses","win_rate","avg_win","avg_loss","pf","cum_pct"]},
        curve=hc["curve"], dates=hc["dates"])
    return JSONResponse(results)

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


@app.get("/api/screening-log")
def api_screening_log(token: str = ""):
    auth(token)
    if not os.path.exists(SCREENING_LOG_FILE):
        return JSONResponse([])
    try:
        with open(SCREENING_LOG_FILE, "r", encoding="utf-8") as f:
            return JSONResponse(json.load(f))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/position/{ticker}/update")
async def api_position_update(ticker: str, request: Request, token: str = ""):
    auth(token)
    body   = await request.json()
    new_tp = int(body.get("tp", 0))
    new_sl = int(body.get("sl", 0))
    if new_tp <= 0 or new_sl <= 0 or new_sl >= new_tp:
        return JSONResponse({"ok": False, "msg": "TP > SL > 0 이어야 합니다"}, status_code=400)
    positions = load_positions()
    p = next((x for x in positions if x["ticker"] == ticker), None)
    if not p:
        return JSONResponse({"ok": False, "msg": "포지션 없음"}, status_code=404)
    old_tp, old_sl = p["tp"], p["sl"]
    p["tp"] = new_tp
    p["sl"] = new_sl
    save_positions(positions)
    send_telegram(
        f"🖥️ *대시보드 TP/SL 수정*\n{p['name']}({ticker})\n"
        f"TP: {old_tp:,} → {new_tp:,}원\nSL: {old_sl:,} → {new_sl:,}원"
    )
    return JSONResponse({"ok": True, "msg": f"{p['name']} TP/SL 수정 완료"})


@app.get("/", response_class=HTMLResponse)
def dashboard(token: str = ""):
    auth(token)
    return HTMLResponse(HTML.replace("__TOKEN__", token))

# ── HTML — TeamHub 라이트 테마 ────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>Scanner v5.1</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root{
  --c-primary:#2ECC88;--c-primary-dk:#25b374;
  --c-bg:#F2F6FB;--c-surface:#fff;
  --c-text:#1A1D23;--c-text2:#6B7280;
  --c-border:#E5E9F0;
  --c-danger:#EF4444;--c-warn:#F59E0B;--c-info:#3B82F6;
  --sidebar-w:220px;
}
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
body{background:var(--c-bg);color:var(--c-text);font-family:'Segoe UI',system-ui,sans-serif;overscroll-behavior:none}

/* Layout */
.layout{display:flex;min-height:100vh}
.sidebar{width:var(--sidebar-w);flex-shrink:0;background:var(--c-surface);border-right:1px solid var(--c-border);display:flex;flex-direction:column;position:fixed;top:0;left:0;bottom:0;z-index:100;overflow-y:auto}
.main{margin-left:var(--sidebar-w);flex:1;padding:24px;max-width:1400px}

/* Sidebar */
.sb-logo{padding:20px 16px 14px;display:flex;align-items:center;gap:10px;border-bottom:1px solid var(--c-border)}
.sb-logo-icon{width:36px;height:36px;background:var(--c-primary);border-radius:10px;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.sb-logo-icon svg{width:20px;height:20px;fill:#fff}
.sb-logo-text{font-size:15px;font-weight:700;letter-spacing:-.3px}
.sb-logo-sub{font-size:11px;color:var(--c-text2)}
.sb-section{padding:16px 14px 6px;font-size:11px;font-weight:600;color:var(--c-text2);text-transform:uppercase;letter-spacing:.6px}
.sb-nav{list-style:none;padding:0 8px}
.sb-nav li a{display:flex;align-items:center;gap:10px;width:100%;padding:9px 10px;border-radius:8px;font-size:13.5px;color:var(--c-text2);text-decoration:none;background:none;border:none;cursor:pointer;transition:all .15s}
.sb-nav li a:hover{background:var(--c-bg);color:var(--c-text)}
.sb-nav li a.active{background:#E8F9F2;color:var(--c-primary);font-weight:600}
.sb-nav li a svg{width:17px;height:17px;flex-shrink:0}
.sb-bottom{margin-top:auto;padding:12px 8px;border-top:1px solid var(--c-border)}
.sb-status{display:flex;align-items:center;gap:8px;padding:9px 10px;border-radius:8px;background:var(--c-bg);font-size:12px}
.sb-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.sb-dot.green{background:#22c55e;box-shadow:0 0 0 3px #dcfce7}
.sb-dot.red{background:#ef4444;box-shadow:0 0 0 3px #fee2e2}

/* Card */
.card{background:var(--c-surface);border-radius:14px;padding:20px;box-shadow:0 1px 4px rgba(0,0,0,.06)}
.card-title{font-size:13px;font-weight:600;color:var(--c-text2);margin-bottom:14px;display:flex;align-items:center;justify-content:space-between}

/* KPI strip */
.kpi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:20px}
.kpi-card{background:var(--c-surface);border-radius:14px;padding:18px 20px;box-shadow:0 1px 4px rgba(0,0,0,.06)}
.kpi-label{font-size:12px;color:var(--c-text2);margin-bottom:6px;font-weight:500}
.kpi-value{font-size:28px;font-weight:700;letter-spacing:-.5px;line-height:1}
.kpi-sub{font-size:11.5px;color:var(--c-text2);margin-top:5px}
.kpi-value.green{color:#16a34a}
.kpi-value.red{color:#dc2626}
.kpi-value.neutral{color:var(--c-text)}

/* Bot card */
.grid-bot{display:grid;grid-template-columns:300px 1fr;gap:16px;margin-bottom:20px}
.bot-card{border-left:4px solid var(--c-primary)}
.bot-avatar{width:48px;height:48px;border-radius:12px;background:linear-gradient(135deg,var(--c-primary),#1a9e66);display:flex;align-items:center;justify-content:center;margin-bottom:12px}
.bot-avatar svg{width:26px;height:26px;fill:#fff}
.bot-name{font-size:16px;font-weight:700;margin-bottom:2px}
.bot-role{font-size:12px;color:var(--c-text2)}
.bot-stats{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:14px}
.bot-stat{background:var(--c-bg);border-radius:8px;padding:10px 12px}
.bot-stat-label{font-size:11px;color:var(--c-text2)}
.bot-stat-value{font-size:14px;font-weight:600;margin-top:2px}

/* Ring charts */
.ring-row{display:grid;grid-template-columns:repeat(7,1fr);gap:10px;margin-bottom:20px}
.ring-card{background:var(--c-surface);border-radius:12px;padding:14px 8px;text-align:center;box-shadow:0 1px 4px rgba(0,0,0,.06)}
.ring-label{font-size:11px;color:var(--c-text2);margin-bottom:8px;font-weight:500}
.ring-canvas-wrap{position:relative;width:72px;height:72px;margin:0 auto}
.ring-center{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;pointer-events:none}
.ring-pct{font-size:15px;font-weight:700;line-height:1}
.ring-count{font-size:10px;color:var(--c-text2);margin-top:1px}

/* Mini calendar */
.cal-month{font-size:14px;font-weight:700;text-align:center;margin-bottom:10px}
.cal-header{display:grid;grid-template-columns:repeat(7,1fr);margin-bottom:3px}
.cal-h{font-size:10px;color:var(--c-text2);text-align:center;font-weight:600;padding:3px 0}
.cal-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:2px}
.cal-d{font-size:12px;text-align:center;padding:5px 0;border-radius:6px}
.cal-d.today{background:var(--c-primary);color:#fff;font-weight:700}
.cal-d.other-month{color:#cbd5e1}
.cal-d.sat{color:#3b82f6}
.cal-d.sun{color:#ef4444}
.schedule-list{margin-top:14px;border-top:1px solid var(--c-border);padding-top:10px}
.schedule-item{display:flex;align-items:flex-start;gap:10px;padding:6px 0;border-bottom:1px solid var(--c-border);font-size:12.5px}
.schedule-item:last-child{border-bottom:none}
.schedule-time{font-weight:700;color:var(--c-primary);min-width:38px;flex-shrink:0}
.schedule-desc{color:var(--c-text2)}

/* Tables */
.tbl-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:13px}
thead th{background:var(--c-bg);color:var(--c-text2);font-weight:600;font-size:11.5px;text-transform:uppercase;letter-spacing:.3px;padding:10px 14px;text-align:left;border-bottom:1px solid var(--c-border);white-space:nowrap}
tbody tr{border-bottom:1px solid var(--c-border);transition:background .12s}
tbody tr:hover{background:#f8fafb}
tbody tr:last-child{border-bottom:none}
td{padding:11px 14px;vertical-align:middle;white-space:nowrap}

/* Progress bar */
.prog-bar{height:5px;border-radius:3px;background:var(--c-border);overflow:hidden;width:80px;display:inline-block;vertical-align:middle}
.prog-fill{height:100%;border-radius:3px;background:var(--c-primary);transition:width .3s}
.prog-fill.danger{background:var(--c-danger)}
.prog-fill.warn{background:var(--c-warn)}

/* Badges */
.badge{display:inline-flex;align-items:center;padding:2px 8px;border-radius:100px;font-size:11.5px;font-weight:600;line-height:1.4}
.badge-green{background:#dcfce7;color:#15803d}
.badge-red{background:#fee2e2;color:#dc2626}
.badge-blue{background:#dbeafe;color:#1d4ed8}
.badge-gray{background:#f1f5f9;color:#64748b}
.badge-yellow{background:#fef3c7;color:#92400e}

/* Buttons */
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 16px;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;border:none;transition:all .15s;white-space:nowrap}
.btn-primary{background:var(--c-primary);color:#fff}
.btn-primary:hover{background:var(--c-primary-dk)}
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
.form-group{margin-bottom:14px}
.form-label{font-size:12px;color:var(--c-text2);font-weight:600;margin-bottom:5px;display:block}
.form-input{width:100%;padding:9px 12px;border:1.5px solid var(--c-border);border-radius:8px;font-size:14px;outline:none;transition:border .15s;background:var(--c-surface);color:var(--c-text)}
.form-input:focus{border-color:var(--c-primary)}

/* Chart */
.chart-wrap{position:relative;height:220px}

/* Section header */
.section-hd{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;flex-wrap:wrap;gap:8px}
.section-title{font-size:16px;font-weight:700}
.section-sub{font-size:12px;color:var(--c-text2)}

/* Sections */
section{display:none}
section.active{display:block}

/* Control strip */
.ctrl-strip{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:12px 18px;background:var(--c-surface);border-radius:12px;box-shadow:0 1px 4px rgba(0,0,0,.06);margin-bottom:20px;font-size:13px}
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
.toast{position:fixed;top:20px;right:20px;background:#1A1D23;color:#fff;padding:12px 18px;border-radius:10px;font-size:13px;z-index:9999;opacity:0;transform:translateY(-8px);transition:all .25s;pointer-events:none;max-width:320px}
.toast.show{opacity:1;transform:translateY(0)}

/* Mobile nav */
.mobile-nav{display:none;position:fixed;bottom:0;left:0;right:0;background:var(--c-surface);border-top:1px solid var(--c-border);z-index:200;padding-bottom:env(safe-area-inset-bottom,0px)}
.mobile-nav-inner{display:flex}
.mnav-btn{flex:1;display:flex;flex-direction:column;align-items:center;gap:3px;padding:10px 0 8px;font-size:10px;color:var(--c-text2);background:none;border:none;cursor:pointer;transition:color .15s}
.mnav-btn svg{width:22px;height:22px}
.mnav-btn.active{color:var(--c-primary)}

/* Refresh timestamp */
.refresh-ts{font-size:11px;color:var(--c-text2)}

/* Responsive — tablet */
@media(max-width:1024px){
  :root{--sidebar-w:64px}
  .sb-logo-text,.sb-logo-sub,.sb-nav li a span,.sb-section,.sb-status span{display:none}
  .sb-logo{justify-content:center;padding:16px 0}
  .sb-nav li a{justify-content:center;padding:10px 0}
  .main{padding:16px}
  .kpi-grid{grid-template-columns:repeat(2,1fr)}
  .ring-row{grid-template-columns:repeat(4,1fr)}
  .grid-bot{grid-template-columns:1fr}
}
/* Responsive — mobile */
@media(max-width:640px){
  .sidebar{display:none}
  .main{margin-left:0;padding:12px;padding-bottom:78px}
  .mobile-nav{display:block}
  .kpi-grid{grid-template-columns:repeat(2,1fr);gap:10px}
  .kpi-value{font-size:24px}
  .ring-row{grid-template-columns:repeat(4,1fr);gap:8px}
  .grid-bot{grid-template-columns:1fr}
  .ctrl-strip{gap:8px;padding:10px 12px}
  .chart-wrap{height:180px}
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
      <div class="sb-logo-sub">눌림목 스윙 봇</div>
    </div>
  </div>

  <div class="sb-section">메인</div>
  <ul class="sb-nav">
    <li><a href="#" class="active" data-sec="overview" onclick="showSection('overview');return false">
      <svg viewBox="0 0 20 20" fill="currentColor"><path d="M2 4a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V4Zm9 0a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2h-3a2 2 0 0 1-2-2V4Zm0 9a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2h-3a2 2 0 0 1-2-2v-3ZM2 13a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-3Z"/></svg>
      <span>개요</span>
    </a></li>
    <li><a href="#" data-sec="positions" onclick="showSection('positions');return false">
      <svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M6 2a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7.414A2 2 0 0 0 15.414 6L12 2.586A2 2 0 0 0 10.586 2H6Zm2 7a1 1 0 0 1 1-1h2a1 1 0 1 1 0 2H9a1 1 0 0 1-1-1Zm1 3a1 1 0 1 0 0 2h2a1 1 0 1 0 0-2H9Z" clip-rule="evenodd"/></svg>
      <span>보유 포지션</span>
    </a></li>
    <li><a href="#" data-sec="history" onclick="showSection('history');return false">
      <svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm1-12a1 1 0 1 0-2 0v4a1 1 0 0 0 .293.707l2.828 2.829a1 1 0 1 0 1.415-1.415L11 9.586V6Z" clip-rule="evenodd"/></svg>
      <span>거래 이력</span>
    </a></li>
    <li><a href="#" data-sec="backtest" onclick="showSection('backtest');return false">
      <svg viewBox="0 0 20 20" fill="currentColor"><path d="M2 11a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-5Zm6-4a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V7Zm6-3a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1h-2a1 1 0 0 1-1-1V4Z"/></svg>
      <span>백테스트</span>
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
  <div class="ctrl-divider"></div>
  <div class="ctrl-item">
    <button class="btn btn-outline btn-sm" onclick="sendPause()">⏸ 정지</button>
    <button class="btn btn-outline btn-sm" onclick="sendResume()">▶ 재개</button>
  </div>
  <div class="ctrl-divider"></div>
  <div class="ctrl-item">
    <span style="color:var(--c-text2)">종목당</span>
    <input type="number" id="tradeAmtInp" class="form-input" style="width:110px;padding:6px 10px;font-size:13px" placeholder="투자금액">
    <button class="btn btn-primary btn-sm" onclick="setTradeAmt()">설정</button>
  </div>
  <div class="ctrl-spacer"></div>
  <span id="kisModeBadge" class="badge badge-blue"></span>
</div>

<!-- ── Overview ── -->
<section id="sec-overview" class="active">

  <!-- KPI -->
  <div class="kpi-grid">
    <div class="kpi-card">
      <div class="kpi-label">총 거래</div>
      <div class="kpi-value neutral" id="kpiTotal">--</div>
      <div class="kpi-sub">승 <span id="kpiWins">-</span> / 패 <span id="kpiLoss">-</span></div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">승률</div>
      <div class="kpi-value" id="kpiWR">--%</div>
      <div class="kpi-sub">손익분기 <span id="kpiBE">--</span>% 이상 필요</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">기대 손익 / 건</div>
      <div class="kpi-value" id="kpiAvg">--%</div>
      <div class="kpi-sub">익 <span id="kpiAW">--</span>% / 손 <span id="kpiAL">--</span>%</div>
    </div>
    <div class="kpi-card">
      <div class="kpi-label">Profit Factor</div>
      <div class="kpi-value" id="kpiPF">--</div>
      <div class="kpi-sub">누적 PnL <span id="kpiCum">--</span>%</div>
    </div>
  </div>

  <!-- Bot status + Calendar -->
  <div class="grid-bot">
    <div class="card bot-card">
      <div class="bot-avatar">
        <svg viewBox="0 0 26 26"><circle cx="13" cy="9" r="5"/><path d="M4 22c0-4.418 4.03-8 9-8s9 3.582 9 8"/></svg>
      </div>
      <div class="bot-name">눌림목 봇</div>
      <div class="bot-role" id="botRoleLabel">시스템 연결 중...</div>
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
          <div class="bot-stat-label">보유 종목</div>
          <div class="bot-stat-value" id="botHolding">-- / 5</div>
        </div>
        <div class="bot-stat">
          <div class="bot-stat-label">종목당 예산</div>
          <div class="bot-stat-value" id="botTradeAmt">--</div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">이달 스캔 일정</div>
      <div id="miniCal"></div>
      <div class="schedule-list">
        <div class="schedule-item"><span class="schedule-time">10:00</span><span class="schedule-desc">포지션 모니터링 (오전)</span></div>
        <div class="schedule-item"><span class="schedule-time">11:30</span><span class="schedule-desc">포지션 모니터링</span></div>
        <div class="schedule-item"><span class="schedule-time">13:00</span><span class="schedule-desc">포지션 모니터링 (오후)</span></div>
        <div class="schedule-item"><span class="schedule-time">14:30</span><span class="schedule-desc">1차 스크리닝 — 기준봉 탐색</span></div>
        <div class="schedule-item"><span class="schedule-time">15:20</span><span class="schedule-desc">2차 확인 + 자동매매 주문 실행</span></div>
      </div>
    </div>
  </div>

  <!-- Exit reason rings -->
  <div class="card" style="margin-bottom:20px">
    <div class="card-title">청산 사유별 분포 <span id="reasonTotal"></span></div>
    <div class="ring-row" id="ringRow">
      <div style="color:var(--c-text2);font-size:13px;padding:8px 0">데이터 로딩 중...</div>
    </div>
  </div>

  <!-- Equity curve -->
  <div class="card" style="margin-bottom:20px">
    <div class="section-hd">
      <div>
        <div class="section-title">누적 손익 곡선</div>
        <div class="section-sub">실거래 누적 PnL (%)</div>
      </div>
      <span class="refresh-ts" id="eqTs"></span>
    </div>
    <div class="chart-wrap"><canvas id="equityChart"></canvas></div>
  </div>

  <!-- Filter breakdown -->
  <div class="card">
    <div class="card-title">필터별 탈락 현황 <span id="filterTs"></span></div>
    <div id="filterBars"><div style="color:var(--c-text2);font-size:13px;padding:8px 0">14:30 스크리닝 이후 데이터가 표시됩니다</div></div>
  </div>

</section>

<!-- ── Positions ── -->
<section id="sec-positions">
  <div class="section-hd">
    <div>
      <div class="section-title">보유 포지션</div>
      <div class="section-sub" id="posTs"></div>
    </div>
  </div>
  <div class="card">
    <div class="tbl-wrap">
      <table>
        <thead><tr>
          <th>종목</th><th>진입가</th><th>현재가</th>
          <th>TP / SL</th><th>진행</th><th>PnL</th>
          <th>신호점수</th><th>구분</th><th>액션</th>
        </tr></thead>
        <tbody id="posTbody">
          <tr><td colspan="9" style="text-align:center;color:var(--c-text2);padding:40px">보유 포지션 없음</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</section>

<!-- ── History ── -->
<section id="sec-history">
  <div class="section-hd">
    <div>
      <div class="section-title">거래 이력</div>
      <div class="section-sub">최근 20건</div>
    </div>
  </div>
  <div class="card">
    <div class="tbl-wrap">
      <table>
        <thead><tr>
          <th>종목</th><th>청산일</th><th>청산 사유</th>
          <th>PnL</th><th>진입가</th><th>청산가</th><th>구분</th>
        </tr></thead>
        <tbody id="histTbody">
          <tr><td colspan="7" style="text-align:center;color:var(--c-text2);padding:40px">이력 없음</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</section>

<!-- ── Backtest ── -->
<section id="sec-backtest">
  <div class="section-hd">
    <div class="section-title">백테스트 비교</div>
    <div class="bt-tabs">
      <button class="bt-tab active" onclick="btSwitch(this,'live')">실거래</button>
      <button class="bt-tab" onclick="btSwitch(this,'v1')">v1</button>
      <button class="bt-tab" onclick="btSwitch(this,'v2')">v2</button>
    </div>
  </div>
  <div class="kpi-grid" id="btKpi" style="margin-bottom:16px"></div>
  <div class="card">
    <div class="card-title">누적 손익 곡선</div>
    <div class="chart-wrap"><canvas id="btChart"></canvas></div>
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
  <button class="mnav-btn" onclick="showSection('positions');mnavSet(this)">
    <svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M6 2a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7.414A2 2 0 0 0 15.414 6L12 2.586A2 2 0 0 0 10.586 2H6Z" clip-rule="evenodd"/></svg>
    포지션
  </button>
  <button class="mnav-btn" onclick="showSection('history');mnavSet(this)">
    <svg viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm1-12a1 1 0 1 0-2 0v4a1 1 0 0 0 .293.707l2.828 2.829a1 1 0 1 0 1.415-1.415L11 9.586V6Z" clip-rule="evenodd"/></svg>
    이력
  </button>
  <button class="mnav-btn" onclick="showSection('backtest');mnavSet(this)">
    <svg viewBox="0 0 20 20" fill="currentColor"><path d="M2 11a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-5Zm6-4a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V7Zm6-3a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1h-2a1 1 0 0 1-1-1V4Z"/></svg>
    백테스트
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
    <h3>📤 수동 청산</h3>
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

<!-- Edit Modal -->
<div class="modal-bg" id="editModal">
  <div class="modal">
    <h3>✏️ TP / SL 수정</h3>
    <div class="form-group">
      <label class="form-label">종목</label>
      <input type="text" id="editName" class="form-input" readonly>
    </div>
    <div class="form-group">
      <label class="form-label">익절가 (TP)</label>
      <input type="number" id="editTp" class="form-input">
    </div>
    <div class="form-group">
      <label class="form-label">손절가 (SL)</label>
      <input type="number" id="editSl" class="form-input">
    </div>
    <div style="display:flex;gap:8px;margin-top:6px">
      <button class="btn btn-primary" style="flex:1" onclick="confirmEdit()">저장</button>
      <button class="btn btn-outline" style="flex:1" onclick="closeModal('editModal')">취소</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const TOKEN = "__TOKEN__";
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);

// ── Section routing ────────────────────────────────────────────
function showSection(sec) {
  $$("section").forEach(s => s.classList.remove("active"));
  const el = document.getElementById("sec-" + sec);
  if (el) el.classList.add("active");
  $$(".sb-nav li a").forEach(a => a.classList.toggle("active", a.dataset.sec === sec));
  if (sec === "logs") loadLogs();
  if (sec === "backtest") loadBacktest();
}
function mnavSet(btn) {
  $$(".mnav-btn").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
}

// ── Toast ──────────────────────────────────────────────────────
function toast(msg, dur=2800) {
  const el = $("#toast");
  el.textContent = msg;
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
    if (d.ok) setTimeout(loadData, 1500);
  }).catch(() => toast("❌ 청산 요청 실패"));
}

function openEdit(ticker, name, tp, sl) {
  _editTicker = ticker;
  $("#editName").value = name + " (" + ticker + ")";
  $("#editTp").value = tp;
  $("#editSl").value = sl;
  $("#editModal").classList.add("open");
}
function confirmEdit() {
  const tp = parseInt($("#editTp").value), sl = parseInt($("#editSl").value);
  if (!tp || !sl || sl >= tp) { toast("TP > SL 이어야 합니다"); return; }
  fetch("/api/position/" + _editTicker + "/update?token=" + TOKEN, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({tp, sl})
  }).then(r => r.json()).then(d => {
    toast(d.ok ? "✅ " + d.msg : "❌ " + d.msg);
    closeModal("editModal");
    if (d.ok) setTimeout(loadData, 1000);
  }).catch(() => toast("❌ 수정 요청 실패"));
}

// ── Controls ───────────────────────────────────────────────────
function toggleAutoTrade(on) {
  fetch("/api/control?token=" + TOKEN, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({action: on ? "autotrade_on" : "autotrade_off"})
  }).then(r => r.json()).then(d => toast(d.ok ? "✅ " + d.msg : "❌ " + d.msg))
    .catch(() => toast("❌ 요청 실패"));
}
function sendPause() {
  fetch("/api/control?token=" + TOKEN, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({action: "pause"})
  }).then(r => r.json()).then(d => toast(d.ok ? "⏸ " + d.msg : "❌ " + d.msg));
}
function sendResume() {
  fetch("/api/control?token=" + TOKEN, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({action: "resume"})
  }).then(r => r.json()).then(d => toast(d.ok ? "▶ " + d.msg : "❌ " + d.msg));
}
function setTradeAmt() {
  const v = parseInt($("#tradeAmtInp").value);
  if (!v || v < 100000) { toast("최소 100,000원 이상"); return; }
  fetch("/api/set_trade_amount?token=" + TOKEN, {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({amount: v})
  }).then(r => r.json()).then(d => toast(d.ok ? "✅ " + d.msg : "❌ " + d.msg));
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
    let cls = "cal-d";
    if (d === today) cls += " today";
    else if (dow === 6) cls += " sat";
    else if (dow === 0) cls += " sun";
    h += `<div class="${cls}">${d}</div>`;
  }
  h += `</div>`;
  $("#miniCal").innerHTML = h;
}

// ── Reason rings ───────────────────────────────────────────────
const REASON_META = {
  TP:          {label:"TP 익절",    color:"#2ECC88"},
  TP1:         {label:"TP1 분할",   color:"#22c55e"},
  SL:          {label:"SL 손절",    color:"#EF4444"},
  HARD_SL:     {label:"하드 SL",   color:"#dc2626"},
  TRAIL_SL:    {label:"트레일 SL", color:"#F59E0B"},
  EXPIRE:      {label:"만료",       color:"#94a3b8"},
  MANUAL_SELL: {label:"수동 청산", color:"#3B82F6"},
};
const ringCharts = {};

function renderRings(reasons) {
  const total = Object.values(reasons).reduce((a, b) => a + b, 0);
  $("#reasonTotal").textContent = total ? `전체 ${total}건` : "";
  const row = $("#ringRow");
  row.innerHTML = "";
  Object.entries(REASON_META).forEach(([k, meta]) => {
    const cnt = reasons[k] || 0;
    const pct = total ? Math.round(cnt / total * 100) : 0;
    const div = document.createElement("div");
    div.className = "ring-card";
    div.innerHTML = `
      <div class="ring-label">${meta.label}</div>
      <div class="ring-canvas-wrap">
        <canvas id="ring_${k}" width="72" height="72"></canvas>
        <div class="ring-center">
          <div class="ring-pct" style="color:${meta.color}">${pct}%</div>
          <div class="ring-count">${cnt}건</div>
        </div>
      </div>`;
    row.appendChild(div);
    if (ringCharts[k]) ringCharts[k].destroy();
    const ctx = document.getElementById("ring_" + k).getContext("2d");
    ringCharts[k] = new Chart(ctx, {
      type: "doughnut",
      data: {datasets: [{
        data: [cnt, Math.max(0, total - cnt)],
        backgroundColor: [meta.color, "#F2F6FB"],
        borderWidth: 0
      }]},
      options: {
        cutout: "72%", responsive: false,
        plugins: {legend: {display: false}, tooltip: {enabled: false}}
      }
    });
  });
}

// ── Equity chart ───────────────────────────────────────────────
let equityChart = null;
function renderEquity(dates, curve) {
  const ctx = $("#equityChart").getContext("2d");
  if (equityChart) equityChart.destroy();
  equityChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: dates,
      datasets: [{
        label: "누적 PnL (%)", data: curve,
        borderColor: "#2ECC88", backgroundColor: "rgba(46,204,136,.1)",
        fill: true, tension: 0.35, pointRadius: 0, borderWidth: 2
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {legend: {display: false}},
      scales: {
        x: {ticks: {maxTicksLimit: 8, font: {size: 11}, color: "#94a3b8"}, grid: {display: false}},
        y: {ticks: {font: {size: 11}, color: "#94a3b8", callback: v => v.toFixed(0) + "%"}, grid: {color: "#f0f4f8"}}
      }
    }
  });
}

// ── KPI ────────────────────────────────────────────────────────
function renderKpi(stats) {
  const {total=0, wins=0, losses=0, win_rate=0, avg_win=0, avg_loss=0, pf=0, cum_pct=0} = stats;
  $("#kpiTotal").textContent = total;
  $("#kpiWins").textContent = wins;
  $("#kpiLoss").textContent = losses;
  const wrEl = $("#kpiWR");
  wrEl.textContent = win_rate.toFixed(1) + "%";
  wrEl.className = "kpi-value " + (win_rate >= 40 ? "green" : "red");
  const avgL = Math.abs(avg_loss);
  const be = avgL && avg_win ? (avgL / (avg_win + avgL) * 100).toFixed(0) : "--";
  $("#kpiBE").textContent = be;
  const ev = total ? ((avg_win * wins + avg_loss * losses) / total) : 0;
  const avgEl = $("#kpiAvg");
  avgEl.textContent = (ev >= 0 ? "+" : "") + ev.toFixed(2) + "%";
  avgEl.className = "kpi-value " + (ev >= 0 ? "green" : "red");
  $("#kpiAW").textContent = "+" + avg_win.toFixed(2) + "%";
  $("#kpiAL").textContent = avg_loss.toFixed(2) + "%";
  const pfEl = $("#kpiPF");
  pfEl.textContent = pf.toFixed(2);
  pfEl.className = "kpi-value " + (pf >= 1 ? "green" : "red");
  $("#kpiCum").textContent = (cum_pct >= 0 ? "+" : "") + cum_pct.toFixed(1);
}

// ── Positions table ────────────────────────────────────────────
const REASON_BADGE = {
  TP:"badge-green", TP1:"badge-green", SL:"badge-red",
  HARD_SL:"badge-red", TRAIL_SL:"badge-yellow",
  EXPIRE:"badge-gray", MANUAL_SELL:"badge-blue"
};
function reasonBadge(r) {
  const label = (REASON_META[r] || {label:r}).label;
  return `<span class="badge ${REASON_BADGE[r]||"badge-gray"}">${label}</span>`;
}

function renderPositions(positions) {
  const tb = $("#posTbody");
  if (!positions || !positions.length) {
    tb.innerHTML = `<tr><td colspan="9" style="text-align:center;color:var(--c-text2);padding:40px">보유 포지션 없음</td></tr>`;
    $("#botHolding").textContent = "0 / 5";
    return;
  }
  $("#botHolding").textContent = positions.length + " / 5";
  tb.innerHTML = positions.map(p => {
    const pnl = p.pnl_pct;
    const pnlStr = pnl !== null ? (pnl >= 0 ? "+" : "") + pnl.toFixed(2) + "%" : "--";
    const pnlSt = pnl === null ? "" : pnl >= 0 ? "color:#16a34a;font-weight:600" : "color:#dc2626;font-weight:600";
    const prog = p.progress || 50;
    const pfill = prog < 20 ? "danger" : prog < 40 ? "warn" : "";
    const trailing = p.is_trailing ? ` <span class="badge badge-yellow" style="font-size:10px">TRAIL</span>` : "";
    const auto = p.auto_traded ? `<span class="badge badge-blue" style="font-size:10px">자동</span>` : `<span class="badge badge-gray" style="font-size:10px">수동</span>`;
    const score = p.signal_score !== undefined && p.signal_score !== null ? p.signal_score + "점" : "--";
    const nm = (p.name || "").replace(/'/g, "\\'");
    return `<tr>
      <td>
        <div style="font-weight:600">${p.name||"--"}</div>
        <div style="font-size:11px;color:var(--c-text2)">${p.ticker}</div>
      </td>
      <td>${(p.entry||0).toLocaleString()}</td>
      <td>
        ${p.current ? p.current.toLocaleString() : "--"}
        ${!p.live_ok ? `<span style="font-size:10px;color:var(--c-warn)" title="시세 조회 실패">⚠</span>` : ""}
      </td>
      <td>
        <div style="font-size:12px">TP <b>${(p.tp||0).toLocaleString()}</b></div>
        <div style="font-size:12px">SL ${(p.sl||0).toLocaleString()}${trailing}</div>
      </td>
      <td>
        <div class="prog-bar"><div class="prog-fill ${pfill}" style="width:${prog}%"></div></div>
        <div style="font-size:10px;color:var(--c-text2);margin-top:2px">${prog}%</div>
      </td>
      <td style="${pnlSt}">${pnlStr}</td>
      <td><span class="badge badge-gray">${score}</span></td>
      <td>${auto}</td>
      <td>
        <div style="display:flex;gap:5px">
          <button class="btn btn-danger btn-sm" onclick="openSell('${p.ticker}','${nm}',${p.quantity||0})">청산</button>
          <button class="btn btn-outline btn-sm" onclick="openEdit('${p.ticker}','${nm}',${p.tp||0},${p.sl||0})">수정</button>
        </div>
      </td>
    </tr>`;
  }).join("");
}

// ── History table ──────────────────────────────────────────────
function renderHistory(history) {
  const tb = $("#histTbody");
  if (!history || !history.length) {
    tb.innerHTML = `<tr><td colspan="7" style="text-align:center;color:var(--c-text2);padding:40px">이력 없음</td></tr>`;
    return;
  }
  tb.innerHTML = history.map(h => {
    const pnl = parseFloat(h.pnl_pct);
    const pnlSt = pnl >= 0 ? "color:#16a34a;font-weight:600" : "color:#dc2626;font-weight:600";
    const auto = h.auto_traded ? `<span class="badge badge-blue" style="font-size:10px">자동</span>` : `<span class="badge badge-gray" style="font-size:10px">수동</span>`;
    return `<tr>
      <td>
        <div style="font-weight:600">${h.name}</div>
        <div style="font-size:11px;color:var(--c-text2)">${h.ticker}</div>
      </td>
      <td style="font-size:12px;color:var(--c-text2)">${h.exit_date||"--"}</td>
      <td>${reasonBadge(h.exit_reason)}</td>
      <td style="${pnlSt}">${(pnl>=0?"+":"")+pnl.toFixed(2)}%</td>
      <td style="font-size:12px">${h.entry_price ? h.entry_price.toLocaleString() : "--"}</td>
      <td style="font-size:12px">${h.exit_price ? h.exit_price.toLocaleString() : "--"}</td>
      <td>${auto}</td>
    </tr>`;
  }).join("");
}

// ── Filter bars ────────────────────────────────────────────────
function renderFilterBars(data) {
  const el = $("#filterBars");
  if (!data || !data.length) {
    el.innerHTML = `<div style="color:var(--c-text2);font-size:13px;padding:8px 0">14:30 스크리닝 이후 데이터가 표시됩니다</div>`;
    return;
  }
  const latest = data[data.length - 1];
  $("#filterTs").textContent = latest.date + " " + latest.time;
  const fc = latest.filter_counts || {};
  const max = Math.max(...Object.values(fc), 1);
  const sorted = Object.entries(fc).sort((a, b) => b[1] - a[1]).filter(([,v]) => v > 0);
  el.innerHTML = sorted.map(([k, v]) => `
    <div class="filter-bar-row">
      <span class="filter-name">${k}</span>
      <div class="filter-bar-bg"><div class="filter-bar-fill" style="width:${Math.round(v/max*100)}%"></div></div>
      <span class="filter-count">${v.toLocaleString()}</span>
    </div>`).join("");
}

// ── Main data load ─────────────────────────────────────────────
function loadData() {
  fetch("/api/data?token=" + TOKEN)
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
      const amt = d.trade_amount || 0;
      $("#botTradeAmt").textContent = amt >= 10000 ? (amt/10000).toFixed(0) + "만원" : amt.toLocaleString() + "원";
      $("#tradeAmtInp").value = amt;
      $("#posTs").textContent = "기준: " + d.now;
      renderKpi(d.stats || {});
      renderPositions(d.positions || []);
      renderHistory(d.history || []);
      renderRings(d.reasons || {});
      renderEquity(d.equity_dates || [], d.equity_curve || []);
      $("#eqTs").textContent = "기준: " + d.now;
    })
    .catch(() => toast("⚠️ 데이터 로드 실패"));
}

// ── Screening log ──────────────────────────────────────────────
function loadScreeningLog() {
  fetch("/api/screening-log?token=" + TOKEN)
    .then(r => r.json())
    .then(data => { if (Array.isArray(data)) renderFilterBars(data); })
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

// ── Backtest ───────────────────────────────────────────────────
let btData = {}, btActive = "live", btChartObj = null;
function loadBacktest() {
  if (Object.keys(btData).length) { renderBt(btActive); return; }
  fetch("/api/backtest?token=" + TOKEN)
    .then(r => r.json())
    .then(d => { btData = d; renderBt(btActive); })
    .catch(() => toast("⚠️ 백테스트 로드 실패"));
}
function btSwitch(btn, key) {
  $$(".bt-tab").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  btActive = key;
  renderBt(key);
}
function renderBt(key) {
  const d = btData[key];
  const kpiEl = $("#btKpi");
  if (!d) {
    kpiEl.innerHTML = `<div style="color:var(--c-text2);padding:16px">데이터 없음 (백테스트 CSV 파일 확인)</div>`;
    return;
  }
  const {total=0, wins=0, losses=0, win_rate=0, pf=0, cum_pct=0} = d;
  kpiEl.innerHTML = `
    <div class="kpi-card"><div class="kpi-label">총 거래</div><div class="kpi-value neutral">${total}</div><div class="kpi-sub">승 ${wins} / 패 ${losses}</div></div>
    <div class="kpi-card"><div class="kpi-label">승률</div><div class="kpi-value ${win_rate>=40?"green":"red"}">${win_rate.toFixed(1)}%</div></div>
    <div class="kpi-card"><div class="kpi-label">Profit Factor</div><div class="kpi-value ${pf>=1?"green":"red"}">${pf.toFixed(2)}</div></div>
    <div class="kpi-card"><div class="kpi-label">누적 PnL</div><div class="kpi-value ${cum_pct>=0?"green":"red"}">${(cum_pct>=0?"+":"")+cum_pct.toFixed(1)}%</div></div>
  `;
  const ctx = $("#btChart").getContext("2d");
  if (btChartObj) btChartObj.destroy();
  btChartObj = new Chart(ctx, {
    type: "line",
    data: {
      labels: d.dates || [],
      datasets: [{
        label: d.label || key, data: d.curve || [],
        borderColor: "#2ECC88", backgroundColor: "rgba(46,204,136,.1)",
        fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {legend: {display: false}},
      scales: {
        x: {ticks: {maxTicksLimit: 8, font:{size:11}, color:"#94a3b8"}, grid:{display:false}},
        y: {ticks: {font:{size:11}, color:"#94a3b8", callback: v => v.toFixed(0)+"%"}, grid:{color:"#f0f4f8"}}
      }
    }
  });
}

// ── Init ───────────────────────────────────────────────────────
renderCalendar();
loadData();
loadScreeningLog();
setInterval(loadData, 60000);
setInterval(loadScreeningLog, 300000);
</script>
</body>
</html>"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081)
