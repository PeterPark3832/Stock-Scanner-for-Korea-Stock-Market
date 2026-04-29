"""
Stock Scanner Dashboard v2.1
- ① async→sync: FastAPI thread-pool 실행 (이벤트 루프 블로킹 방지)
- ② mtime 캐시: 파일 변경 시에만 재파싱
- ③ Phase 1 제어: 자동매매 토글 / pause·resume / 포지션 즉시 청산
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
POSITIONS_FILE = os.path.join(BASE_DIR, "positions.json")
HISTORY_FILE   = os.path.join(BASE_DIR, "trade_history.csv")

# ── .env 직접 파싱 (runtime 최신값 보장) ──────────────────────
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

# ── 환경변수 ──────────────────────────────────────────────────
def _kis_base() -> str:
    return ("https://openapi.koreainvestment.com:9443"
            if read_env("KIS_MODE","paper") == "real"
            else "https://openapivts.koreainvestment.com:29443")

DASHBOARD_TOKEN = read_env("DASHBOARD_TOKEN", "scanner2024")

# ── KIS 토큰 캐시 ─────────────────────────────────────────────
_token_cache      = {"token": None, "expires_at": 0}
_token_lock       = threading.Lock()
_file_lock        = threading.Lock()   # 대시보드 내부 스레드용
_cache_lock       = threading.Lock()   # _hist_cache race condition 방지
_POSITIONS_FLOCK  = FileLock(os.path.join(BASE_DIR, "positions.json.lock"),     timeout=5)
_HISTORY_FLOCK    = FileLock(os.path.join(BASE_DIR, "trade_history.csv.lock"),  timeout=5)

def get_kis_token() -> str | None:
    with _token_lock:
        if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
            return _token_cache["token"]
    try:
        r = requests.post(f"{_kis_base()}/oauth2/tokenP",
            json={"grant_type": "client_credentials",
                  "appkey":     read_env("KIS_APP_KEY"),
                  "appsecret":  read_env("KIS_APP_SECRET")}, timeout=10)
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
                     "appkey":        read_env("KIS_APP_KEY"),
                     "appsecret":     read_env("KIS_APP_SECRET"),
                     "tr_id":         "FHKST01010100"},
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
            timeout=5)
        d = r.json()
        if d.get("rt_cd") == "0":
            o = d["output"]
            return {"current": int(o.get("stck_prpr", 0))}
    except Exception:
        pass
    return None

# ── Telegram 발송 ─────────────────────────────────────────────
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

# ── 파일 I/O ──────────────────────────────────────────────────
def load_positions() -> list[dict]:
    with _POSITIONS_FLOCK:
        try:
            with open(POSITIONS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

def save_positions(positions: list[dict]) -> None:
    with _POSITIONS_FLOCK:               # 크로스 프로세스 락
        with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(positions, f, ensure_ascii=False, indent=2)

def append_history(row: dict) -> None:
    fieldnames = ["ticker","name","sector","entry_date","exit_date",
                  "entry_price","exit_price","quantity","pnl_pct",
                  "exit_reason","signal_score","bo_lookback","pullback_depth","auto_traded"]
    exists = os.path.exists(HISTORY_FILE)
    with _HISTORY_FLOCK:                 # 크로스 프로세스 락
        with open(HISTORY_FILE, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            if not exists:
                w.writeheader()
            w.writerow(row)

# ── ② mtime 캐시 ──────────────────────────────────────────────
_hist_cache: dict = {"mtime": -1, "rows": [], "stats": {}, "dates": [], "curve": [], "reasons": {}}

def get_history_cached() -> dict:
    with _cache_lock:                        # ③ race condition 방지
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
            total    = len(rows),
            wins     = len(wins),
            losses   = len(loss),
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
        result.append({**p,
            "current":     cur,
            "pnl_pct":     pnl,
            "progress":    prog,
            "is_trailing": p.get("sl", 0) > p.get("sl_init", p.get("sl", 0)),
            "live_ok":     live is not None,
        })
    return result

# ── ③ 즉시 청산 (Phase 1) ─────────────────────────────────────
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
                     "appkey":        read_env("KIS_APP_KEY"),
                     "appsecret":     read_env("KIS_APP_SECRET"),
                     "tr_id":         tr_id, "custtype": "P"},
            json=body, timeout=15)
        d = r.json()
        if d.get("rt_cd") == "0":
            result.update(success=True, order_no=d.get("output", {}).get("ODNO", ""))
        else:
            result["error"] = d.get("msg1", "주문 실패")
    except Exception as e:
        result["error"] = str(e)
    return result

# ── FastAPI ────────────────────────────────────────────────────
app = FastAPI()

def auth(token: str):
    if token != DASHBOARD_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

# ① async → def (FastAPI가 thread-pool에서 실행)
@app.get("/api/data")
def api_data(token: str = ""):
    auth(token)
    hc        = get_history_cached()
    positions = enrich_positions(load_positions())
    recent    = [{"name": r["name"], "ticker": r["ticker"],
                  "exit_date": r["exit_date"], "exit_reason": r["exit_reason"],
                  "pnl_pct": float(r["pnl_pct"]),
                  "entry_price": int(r.get("entry_price", 0)),
                  "exit_price":  int(r.get("exit_price", 0))}
                 for r in reversed(hc["rows"][-20:])]
    return JSONResponse({
        "now":          datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        "auto_trade":   read_env("AUTO_TRADE", "false").lower() == "true",
        "kis_mode":     "실전투자" if read_env("KIS_MODE","paper") == "real" else "모의투자",
        "trade_amount": int(read_env("TRADE_AMOUNT_PER_STOCK", "200000")),
        "stats":        hc["stats"],
        "positions":    positions,
        "history":      recent,
        "equity_dates": hc["dates"],
        "equity_curve": hc["curve"],
        "reasons":      hc["reasons"],
    })

# ③ 제어 API
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
        cmd_map = {"pause": "🖥️ *대시보드* — 신호 발송 정지", "resume": "🖥️ *대시보드* — 신호 발송 재개"}
        # 공유 flag 파일로 봇에 전달
        flag = os.path.join(BASE_DIR, f"_{action}.flag")
        open(flag, "w").close()
        send_telegram(cmd_map[action])
        return JSONResponse({"ok": True, "msg": f"{action} 명령 전달"})

    return JSONResponse({"ok": False, "msg": "알 수 없는 액션"}, status_code=400)

@app.post("/api/sell/{ticker}")
async def api_sell(ticker: str, request: Request, token: str = ""):
    auth(token)
    body = await request.json()
    qty  = int(body.get("qty", 0))
    name = body.get("name", ticker)

    result = dashboard_sell(ticker, qty, name)
    if not result["success"]:
        return JSONResponse({"ok": False, "msg": result["error"]}, status_code=400)

    # 포지션에서 제거 + 이력 기록
    positions = load_positions()
    p = next((x for x in positions if x["ticker"] == ticker), None)
    if p:
        live  = get_price(ticker)
        epx   = live["current"] if live else int(body.get("entry", 0))
        entry = p.get("entry", 0)
        pnl   = round((epx - entry)/entry*100, 2) if entry else 0
        save_positions([x for x in positions if x["ticker"] != ticker])
        append_history({
            "ticker": ticker, "name": name, "sector": p.get("sector",""),
            "entry_date": p.get("entry_date",""), "exit_date": datetime.now(KST).strftime("%Y-%m-%d"),
            "entry_price": entry, "exit_price": epx, "quantity": p.get("quantity", qty),
            "pnl_pct": pnl, "exit_reason": "MANUAL_SELL",
            "signal_score": p.get("signal_score",""), "bo_lookback": p.get("bo_lookback",""),
            "pullback_depth": p.get("pullback_depth",""), "auto_traded": p.get("auto_traded", False),
        })
        send_telegram(
            f"🖥️ *대시보드 수동 청산*\n"
            f"{name}({ticker}) {qty}주\n"
            f"주문번호: {result['order_no']} | PnL: {pnl:+.2f}%"
        )
    return JSONResponse({"ok": True, "order_no": result["order_no"]})

@app.get("/api/backtest")
def api_backtest(token: str = ""):
    auth(token)
    results = {}
    for fname, label, strategy_kw in [
        ("backtest_results.csv",    "v1",  "A_눌림목"),
        ("backtest_v2_results.csv", "v2",  "A_눌림목v2"),
    ]:
        path = os.path.join(BASE_DIR, fname)
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8-sig") as f:
                rows = [r for r in csv.DictReader(f)
                        if strategy_kw in r.get("strategy", "")]
        except Exception:
            continue
        if not rows:
            continue
        wins = [r for r in rows if float(r["pnl_pct"]) > 0]
        loss = [r for r in rows if float(r["pnl_pct"]) <= 0]
        gw   = sum(float(r["pnl_pct"]) for r in wins)
        gl   = abs(sum(float(r["pnl_pct"]) for r in loss))
        # 에쿼티 커브
        cum, curve, dates = 0.0, [], []
        for r in rows:
            cum += float(r["pnl_pct"])
            curve.append(round(cum, 2))
            dates.append(r["exit_date"][5:] if r.get("exit_date") else "")
        results[label] = dict(
            label    = f"백테스트 {label} ({strategy_kw})",
            total    = len(rows),
            wins     = len(wins),
            losses   = len(loss),
            win_rate = round(len(wins)/len(rows)*100, 1) if rows else 0.0,
            avg_win  = round(gw/len(wins), 2) if wins else 0.0,
            avg_loss = round(-gl/len(loss), 2) if loss else 0.0,
            pf       = round(gw/gl, 2) if gl else 0.0,
            cum_pct  = round(sum(float(r["pnl_pct"]) for r in rows), 2),
            curve    = curve,
            dates    = dates,
        )
    # 실거래 데이터도 함께 반환
    hc = get_history_cached()
    results["live"] = dict(
        label    = "실거래",
        total    = hc["stats"].get("total", 0),
        wins     = hc["stats"].get("wins", 0),
        losses   = hc["stats"].get("losses", 0),
        win_rate = hc["stats"].get("win_rate", 0.0),
        avg_win  = hc["stats"].get("avg_win", 0.0),
        avg_loss = hc["stats"].get("avg_loss", 0.0),
        pf       = hc["stats"].get("pf", 0.0),
        cum_pct  = hc["stats"].get("cum_pct", 0.0),
        curve    = hc["curve"],
        dates    = hc["dates"],
    )
    return JSONResponse(results)


@app.get("/", response_class=HTMLResponse)
def dashboard(token: str = ""):
    auth(token)
    return HTMLResponse(HTML.replace("__TOKEN__", token))

# ── HTML ──────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Scanner v4.6</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  *{-webkit-tap-highlight-color:transparent;box-sizing:border-box}
  body{background:#0d1117;color:#e6edf3;font-family:'Segoe UI',system-ui,sans-serif;overscroll-behavior:none}
  .card{background:#161b22;border:1px solid #21262d;border-radius:14px}
  .tab-btn{flex:1;padding:14px 0 10px;font-size:11px;color:#8b949e;display:flex;flex-direction:column;align-items:center;gap:3px;transition:color .15s;border:none;background:none;cursor:pointer}
  .tab-btn.active{color:#58a6ff}
  .tab-btn svg{width:22px;height:22px}
  .tab-panel{display:none}
  .tab-panel.active{display:block}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
  .pulse{animation:pulse 2s infinite}
  .badge-tp{background:#0d2818;color:#3fb950;border:1px solid #196830}
  .badge-sl{background:#2d0f0f;color:#f85149;border:1px solid #6e1c1c}
  .badge-trail{background:#2d1f00;color:#e3b341;border:1px solid #6e4c00}
  .badge-exp{background:#1c2128;color:#8b949e;border:1px solid #30363d}
  .badge-manual{background:#1a1f35;color:#79c0ff;border:1px solid #1f4470}
  .prog-track{height:6px;background:#21262d;border-radius:99px;position:relative;overflow:visible}
  .prog-fill{height:100%;border-radius:99px;transition:width .4s}
  .prog-dot{position:absolute;top:50%;transform:translate(-50%,-50%);width:12px;height:12px;border-radius:50%;border:2px solid #0d1117}
  ::-webkit-scrollbar{width:3px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:#30363d;border-radius:99px}
  .kpi-num{font-size:28px;font-weight:700;line-height:1;letter-spacing:-.5px}
  .kpi-sub{font-size:11px;color:#8b949e;margin-top:3px}
  .green{color:#3fb950}.red{color:#f85149}.gray{color:#8b949e}.blue{color:#58a6ff}.orange{color:#e3b341}
  /* 토글 스위치 */
  .toggle{position:relative;display:inline-block;width:44px;height:24px}
  .toggle input{opacity:0;width:0;height:0}
  .slider{position:absolute;cursor:pointer;inset:0;background:#30363d;border-radius:24px;transition:.3s}
  .slider:before{position:absolute;content:"";width:18px;height:18px;left:3px;bottom:3px;background:#e6edf3;border-radius:50%;transition:.3s}
  input:checked+.slider{background:#238636}
  input:checked+.slider:before{transform:translateX(20px)}
  /* 버튼 */
  .btn-ctrl{font-size:12px;font-weight:500;padding:6px 14px;border-radius:8px;cursor:pointer;transition:all .15s;border:none}
  .btn-pause{background:#21262d;color:#8b949e;border:1px solid #30363d}
  .btn-pause:active{background:#30363d}
  .btn-sell{background:#2d0f0f;color:#f85149;border:1px solid #6e1c1c;font-size:11px;padding:5px 10px;border-radius:7px;cursor:pointer}
  .btn-sell:active{background:#3d1515}
  .btn-sell:disabled{opacity:.4;cursor:not-allowed}
  /* 토스트 */
  #toast{position:fixed;bottom:80px;left:50%;transform:translateX(-50%);padding:10px 20px;border-radius:10px;font-size:13px;font-weight:500;z-index:100;opacity:0;transition:opacity .3s;pointer-events:none;white-space:nowrap}
</style>
</head>
<body class="pb-20">

<!-- 헤더 -->
<div class="sticky top-0 z-30 px-4 py-3 flex items-center justify-between"
     style="background:rgba(13,17,23,.92);backdrop-filter:blur(12px);border-bottom:1px solid #21262d">
  <div class="flex items-center gap-2">
    <div id="statusDot" class="w-2 h-2 rounded-full bg-gray-500"></div>
    <span class="font-semibold text-sm">Scanner v4.6</span>
    <span id="kisModeBadge" class="text-xs px-2 py-0.5 rounded-full"
          style="background:#1c2128;color:#8b949e;border:1px solid #30363d">—</span>
  </div>
  <div class="flex items-center gap-2">
    <button class="btn-ctrl btn-pause" onclick="loadData()">↻ 새로고침</button>
  </div>
</div>
<p id="updateTime" class="text-center text-xs mt-2 mb-1" style="color:#484f58">—</p>

<!-- 탭 패널 -->
<div class="px-3">

  <!-- ① 개요 -->
  <div id="tab-overview" class="tab-panel active">

    <!-- KPI 4개 -->
    <div class="grid grid-cols-2 gap-2 mb-3">
      <div class="card p-4">
        <p class="kpi-sub">총 거래</p>
        <p class="kpi-num text-white mt-1"><span id="kpiTotal">—</span></p>
        <p class="text-xs mt-1" style="color:#484f58"><span id="kpiWL">—</span></p>
      </div>
      <div class="card p-4">
        <p class="kpi-sub">승률</p>
        <p id="kpiWR" class="kpi-num mt-1">—</p>
        <p class="kpi-sub mt-1">PF <span id="kpiPF">—</span></p>
      </div>
      <div class="card p-4">
        <p class="kpi-sub">평균 수익</p>
        <p id="kpiAvgW" class="kpi-num green mt-1">—</p>
        <p class="kpi-sub mt-1">평균 손실 <span id="kpiAvgL" class="red">—</span></p>
      </div>
      <div class="card p-4">
        <p class="kpi-sub">누적 합산</p>
        <p id="kpiCum" class="kpi-num mt-1">—</p>
        <p class="kpi-sub mt-1">종목당 <span id="kpiAmt">—</span></p>
      </div>
    </div>

    <!-- 봇 제어 카드 -->
    <div class="card p-4 mb-3">
      <p class="text-sm font-semibold mb-3">봇 제어</p>
      <div class="flex items-center justify-between mb-3">
        <div>
          <p class="text-sm text-white font-medium">자동매매</p>
          <p id="atLabel" class="text-xs mt-0.5" style="color:#8b949e">—</p>
        </div>
        <label class="toggle">
          <input type="checkbox" id="atToggle" onchange="toggleAutoTrade(this.checked)">
          <span class="slider"></span>
        </label>
      </div>
      <div class="flex gap-2">
        <button id="btnPause"  class="btn-ctrl btn-pause flex-1" onclick="ctrlAction('pause')">⏸ 신호 정지</button>
        <button id="btnResume" class="btn-ctrl btn-pause flex-1" onclick="ctrlAction('resume')">▶ 신호 재개</button>
      </div>
    </div>

    <!-- 에쿼티 커브 -->
    <div class="card p-4 mb-3">
      <div class="flex items-center justify-between mb-3">
        <p class="text-sm font-semibold">에쿼티 커브</p>
        <p class="text-xs" style="color:#484f58">누적 수익률 (%)</p>
      </div>
      <div style="height:160px"><canvas id="equityChart"></canvas></div>
    </div>

    <!-- 도넛 + 청산 사유 -->
    <div class="grid grid-cols-2 gap-2 mb-3">
      <div class="card p-4 flex flex-col items-center">
        <p class="text-sm font-semibold mb-2">승/패 비율</p>
        <div style="width:100px;height:100px"><canvas id="donutChart"></canvas></div>
        <p id="donutLabel" class="text-xs mt-2" style="color:#8b949e">—</p>
      </div>
      <div class="card p-4">
        <p class="text-sm font-semibold mb-2">청산 사유</p>
        <div id="reasonList" class="space-y-1.5 text-xs"></div>
      </div>
    </div>
  </div>

  <!-- ② 포지션 -->
  <div id="tab-positions" class="tab-panel">
    <div id="positionList" class="space-y-2 mt-1"></div>
  </div>

  <!-- ③ 이력 -->
  <div id="tab-history" class="tab-panel">
    <div id="historyList" class="space-y-1.5 mt-1"></div>
  </div>

  <!-- ④ 백테스트 분석 -->
  <div id="tab-backtest" class="tab-panel">
    <!-- 비교 차트 -->
    <div class="card p-4 mb-3">
      <div class="flex items-center justify-between mb-3">
        <p class="text-sm font-semibold">에쿼티 커브 비교</p>
        <div class="flex gap-2 text-xs">
          <span style="color:#58a6ff">■ 백테스트</span>
          <span style="color:#3fb950">■ 실거래</span>
        </div>
      </div>
      <div style="height:180px"><canvas id="btCompareChart"></canvas></div>
    </div>
    <!-- 지표 비교 테이블 -->
    <div id="btStatsCards" class="space-y-2 mb-3"></div>
    <!-- 해석 카드 -->
    <div id="btInsight" class="card p-4"></div>
  </div>

</div>

<!-- 하단 탭 네비 -->
<nav class="fixed bottom-0 left-0 right-0 flex z-30"
     style="background:rgba(13,17,23,.95);backdrop-filter:blur(12px);border-top:1px solid #21262d">
  <button class="tab-btn active" onclick="switchTab('overview',this)">
    <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.8">
      <path stroke-linecap="round" stroke-linejoin="round"
        d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"/>
    </svg>개요
  </button>
  <button class="tab-btn" onclick="switchTab('positions',this)">
    <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.8">
      <path stroke-linecap="round" stroke-linejoin="round"
        d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/>
    </svg>포지션 <span id="posBadge"></span>
  </button>
  <button class="tab-btn" onclick="switchTab('history',this)">
    <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.8">
      <path stroke-linecap="round" stroke-linejoin="round"
        d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
    </svg>이력
  </button>
  <button class="tab-btn" onclick="switchTab('backtest',this);loadBacktest()">
    <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.8">
      <path stroke-linecap="round" stroke-linejoin="round"
        d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/>
    </svg>분석
  </button>
</nav>

<!-- 토스트 -->
<div id="toast"></div>

<script>
const TOKEN = '__TOKEN__';
let equityChart = null, donutChart = null;

// ── 유틸 ──────────────────────────────────────────────────────
const fmt  = n => Number(n).toLocaleString('ko-KR');
const fmtP = n => (n >= 0 ? '+' : '') + Number(n).toFixed(2) + '%';
const clr  = n => n > 0 ? 'green' : n < 0 ? 'red' : 'gray';

function toast(msg, ok = true) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.style.background = ok ? '#0d2818' : '#2d0f0f';
  el.style.color       = ok ? '#3fb950' : '#f85149';
  el.style.border      = `1px solid ${ok ? '#196830' : '#6e1c1c'}`;
  el.style.opacity = '1';
  setTimeout(() => el.style.opacity = '0', 2800);
}

function badgeHTML(reason) {
  const map = {TP:'badge-tp TP', SL:'badge-sl SL',
               TRAIL_SL:'badge-trail 트레일', EXPIRE:'badge-exp 만료',
               MANUAL_SELL:'badge-manual 수동청산'};
  const [cls, label] = (map[reason] || 'badge-exp ?').split(' ');
  return `<span class="text-xs px-1.5 py-0.5 rounded-full ${cls} font-medium">${label}</span>`;
}

function switchTab(name, btn) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
}

// ── 제어 API ──────────────────────────────────────────────────
async function ctrlAction(action) {
  try {
    const r = await fetch(`/api/control?token=${TOKEN}`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({action})
    });
    const d = await r.json();
    toast(d.msg, d.ok);
  } catch(e) {
    toast('연결 오류', false);
  }
}

async function toggleAutoTrade(on) {
  // ④ 재시작 경고: 장중(9~15:30 KST) 변경 시 알림
  const now = new Date();
  const kst = new Date(now.toLocaleString('en-US', {timeZone:'Asia/Seoul'}));
  const h = kst.getHours(), m = kst.getMinutes();
  const inMarket = (h > 9 || (h === 9 && m >= 0)) && (h < 15 || (h === 15 && m < 30));
  if (inMarket) {
    const ok = confirm(
      `⚠️ 장중 자동매매 ${on?'활성화':'비활성화'}\n\n` +
      `봇이 재시작됩니다. 현재 14:30~15:20 스캔 중이라면\n` +
      `해당 사이클이 중단될 수 있습니다.\n\n계속하시겠습니까?`
    );
    if (!ok) {
      document.getElementById('atToggle').checked = !on;
      return;
    }
  }
  document.getElementById('atToggle').disabled = true;
  try {
    const r = await fetch(`/api/control?token=${TOKEN}`, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({action: on ? 'autotrade_on' : 'autotrade_off'})
    });
    const d = await r.json();
    toast(d.msg, d.ok);
    document.getElementById('atLabel').textContent = on ? '활성화 — 봇 재시작 중' : '비활성화 — 봇 재시작 중';
  } catch(e) {
    toast('연결 오류', false);
    document.getElementById('atToggle').checked = !on;
  } finally {
    setTimeout(() => document.getElementById('atToggle').disabled = false, 4500);
  }
}

async function sellPosition(ticker, qty, name, entry) {
  if (!confirm(`${name} (${ticker})\n${qty}주를 즉시 시장가 청산하시겠습니까?`)) return;
  const btn = document.getElementById(`sell-${ticker}`);
  if (btn) { btn.disabled = true; btn.textContent = '처리 중...'; }
  try {
    const r = await fetch(`/api/sell/${ticker}?token=${TOKEN}`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({qty, name, entry})
    });
    const d = await r.json();
    if (d.ok) {
      toast(`${name} 청산 완료 (${d.order_no})`, true);
      setTimeout(loadData, 1500);
    } else {
      toast(`청산 실패: ${d.msg}`, false);
      if (btn) { btn.disabled = false; btn.textContent = '즉시 청산'; }
    }
  } catch(e) {
    toast('연결 오류', false);
    if (btn) { btn.disabled = false; btn.textContent = '즉시 청산'; }
  }
}

// ── 데이터 로드 ───────────────────────────────────────────────
async function loadData() {
  document.getElementById('updateTime').textContent = '로딩 중…';
  try {
    const d = await (await fetch(`/api/data?token=${TOKEN}`)).json();
    render(d);
    document.getElementById('updateTime').textContent = '업데이트: ' + d.now;
  } catch(e) {
    document.getElementById('updateTime').textContent = '연결 오류';
  }
}

function render(d) {
  // 헤더 & 제어
  const dot = document.getElementById('statusDot');
  dot.className = 'w-2 h-2 rounded-full pulse ' + (d.auto_trade ? 'bg-green-400' : 'bg-yellow-400');
  document.getElementById('kisModeBadge').textContent = d.kis_mode;
  const tog = document.getElementById('atToggle');
  tog.checked = d.auto_trade;
  document.getElementById('atLabel').textContent =
    d.auto_trade ? '활성화 — 신호 발생 시 자동 주문' : '비활성화 — 수동 처리';

  // KPI
  const s = d.stats;
  document.getElementById('kpiTotal').textContent = s.total + '건';
  document.getElementById('kpiWL').textContent    = s.wins + '승 ' + s.losses + '패';
  const wrEl = document.getElementById('kpiWR');
  wrEl.textContent = s.win_rate + '%';
  wrEl.className   = 'kpi-num mt-1 ' + (s.win_rate >= 50 ? 'green' : s.win_rate >= 40 ? 'blue' : 'red');
  document.getElementById('kpiPF').textContent   = s.pf;
  document.getElementById('kpiAvgW').textContent = '+' + s.avg_win + '%';
  document.getElementById('kpiAvgL').textContent = s.avg_loss + '%';
  const cumEl = document.getElementById('kpiCum');
  cumEl.textContent = (s.cum_pct >= 0 ? '+' : '') + s.cum_pct + '%';
  cumEl.className   = 'kpi-num mt-1 ' + clr(s.cum_pct);
  document.getElementById('kpiAmt').textContent = fmt(d.trade_amount) + '원';

  renderEquity(d.equity_dates, d.equity_curve);
  renderDonut(s.wins, s.losses);

  // 청산 사유 (전체 기준)
  const reasonMap = {TP:'🟢 TP', SL:'🔴 SL', TRAIL_SL:'🟠 트레일', EXPIRE:'⚫ 만료', MANUAL_SELL:'🔵 수동'};
  document.getElementById('reasonList').innerHTML =
    Object.entries(d.reasons || {}).sort((a,b)=>b[1]-a[1])
      .map(([k,v]) => `<div class="flex justify-between">
        <span style="color:#8b949e">${reasonMap[k]||k}</span>
        <span class="text-white font-medium">${v}건</span>
      </div>`).join('');

  document.getElementById('posBadge').textContent = d.positions.length ? ` (${d.positions.length})` : '';
  renderPositions(d.positions);
  renderHistory(d.history);
}

// ── 에쿼티 커브 ──────────────────────────────────────────────
function renderEquity(labels, data) {
  const ctx = document.getElementById('equityChart').getContext('2d');
  const last = data.length ? data[data.length-1] : 0;
  const lc   = last >= 0 ? '#3fb950' : '#f85149';
  const grad = ctx.createLinearGradient(0,0,0,160);
  grad.addColorStop(0, last >= 0 ? 'rgba(63,185,80,.25)' : 'rgba(248,81,73,.25)');
  grad.addColorStop(1, 'rgba(13,17,23,0)');
  if (equityChart) equityChart.destroy();
  equityChart = new Chart(ctx, {
    type:'line',
    data:{ labels, datasets:[{data, borderColor:lc, backgroundColor:grad,
            borderWidth:2, pointRadius:0, fill:true, tension:0.3}] },
    options:{responsive:true, maintainAspectRatio:false,
      plugins:{legend:{display:false},
        tooltip:{callbacks:{label:c=>fmtP(c.parsed.y)}}},
      scales:{
        x:{ticks:{color:'#484f58',font:{size:10},maxTicksLimit:6},grid:{display:false}},
        y:{ticks:{color:'#8b949e',font:{size:10},callback:v=>v+'%'},
           grid:{color:'rgba(33,38,45,.8)'}}}}
  });
}

// ── 도넛 ─────────────────────────────────────────────────────
function renderDonut(wins, losses) {
  const ctx = document.getElementById('donutChart').getContext('2d');
  if (donutChart) donutChart.destroy();
  donutChart = new Chart(ctx, {
    type:'doughnut',
    data:{datasets:[{data:[wins,losses],backgroundColor:['#3fb950','#f85149'],
                    borderWidth:0,borderRadius:3}]},
    options:{responsive:true,maintainAspectRatio:false,cutout:'72%',
      plugins:{legend:{display:false}}}
  });
  document.getElementById('donutLabel').textContent = wins+'W / '+losses+'L';
}

// ── 포지션 카드 ───────────────────────────────────────────────
function renderPositions(positions) {
  const el = document.getElementById('positionList');
  if (!positions.length) {
    el.innerHTML = `<div class="card p-6 text-center text-sm" style="color:#484f58">보유 포지션 없음</div>`;
    return;
  }
  el.innerHTML = positions.map(p => {
    const pnl  = p.pnl_pct;
    const pclr = pnl === null ? 'gray' : clr(pnl);
    const prog = p.progress;
    const dc   = prog > 50 ? '#3fb950' : '#f85149';
    const trailTag = p.is_trailing ? `<span class="text-xs px-1.5 py-0.5 rounded-full badge-trail ml-1">트레일</span>` : '';
    const autoTag  = p.auto_traded ? `<span class="text-xs" style="color:#484f58">🤖 자동</span>` : `<span class="text-xs" style="color:#484f58">✋ 수동</span>`;
    const qtyTag   = p.quantity ? `<span class="text-xs" style="color:#484f58">${p.quantity}주</span>` : '';
    const pnlText  = pnl !== null ? `<span class="${pclr} font-bold text-lg">${fmtP(pnl)}</span>` : `<span class="gray text-sm">—</span>`;
    const curText  = p.current ? `<span style="color:#8b949e" class="text-sm">${fmt(p.current)}원</span>` : '';
    const sellable = p.quantity > 0;
    return `
    <div class="card p-4">
      <div class="flex justify-between items-start mb-3">
        <div>
          <div class="flex items-center gap-1">
            <span class="font-semibold text-white">${p.name}</span>${trailTag}
          </div>
          <div class="flex items-center gap-1.5 mt-0.5">
            <span style="color:#484f58" class="text-xs">${p.ticker}</span>
            <span style="color:#484f58" class="text-xs">·</span>
            ${autoTag}
            ${qtyTag ? `<span style="color:#484f58" class="text-xs">·</span>${qtyTag}` : ''}
          </div>
        </div>
        <div class="text-right">${pnlText}${curText ? '<br>'+curText : ''}</div>
      </div>
      <div class="prog-track mb-1">
        <div class="prog-fill" style="width:${prog}%;background:${dc}"></div>
        <div class="prog-dot" style="left:${prog}%;background:${dc}"></div>
      </div>
      <div class="flex justify-between text-xs mt-1.5 mb-3">
        <span class="red">SL ${fmt(p.sl)}</span>
        <span style="color:#484f58">진입 ${fmt(p.entry)}</span>
        <span class="green">TP ${fmt(p.tp)}</span>
      </div>
      <div class="flex items-center justify-between">
        <p class="text-xs" style="color:#484f58">${p.entry_date} 진입</p>
        <button id="sell-${p.ticker}" class="btn-sell" ${sellable?'':'disabled'}
          onclick="sellPosition('${p.ticker}',${p.quantity},'${p.name}',${p.entry})">
          즉시 청산
        </button>
      </div>
    </div>`;
  }).join('');
}

// ── 이력 ──────────────────────────────────────────────────────
function renderHistory(history) {
  const el = document.getElementById('historyList');
  if (!history.length) {
    el.innerHTML = `<div class="card p-6 text-center text-sm" style="color:#484f58">거래 이력 없음</div>`;
    return;
  }
  el.innerHTML = history.map(h => {
    const pc = clr(h.pnl_pct);
    return `
    <div class="card p-3 flex items-center gap-3">
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-1.5 flex-wrap">
          <span class="font-medium text-white text-sm">${h.name}</span>
          ${badgeHTML(h.exit_reason)}
        </div>
        <p class="text-xs mt-0.5" style="color:#484f58">
          ${h.exit_date} · ${fmt(h.entry_price)}→${fmt(h.exit_price)}원
        </p>
      </div>
      <span class="font-bold text-base ${pc} shrink-0">${fmtP(h.pnl_pct)}</span>
    </div>`;
  }).join('');
}

// ── Phase 3: 백테스트 비교 ────────────────────────────────────
let btChart = null, btLoaded = false;

async function loadBacktest() {
  if (btLoaded) return;
  document.getElementById('btStatsCards').innerHTML =
    `<p class="text-center text-sm py-4" style="color:#484f58">로딩 중…</p>`;
  try {
    const d = await (await fetch(`/api/backtest?token=${TOKEN}`)).json();
    renderBacktest(d);
    btLoaded = true;
  } catch(e) {
    document.getElementById('btStatsCards').innerHTML =
      `<p class="text-center text-sm py-4 red">로드 실패</p>`;
  }
}

function renderBacktest(d) {
  // 비교 차트 (백테스트 + 실거래)
  const ctx = document.getElementById('btCompareChart').getContext('2d');
  if (btChart) btChart.destroy();
  const datasets = [];
  const colorMap = {v1:'#58a6ff', v2:'#79c0ff', live:'#3fb950'};
  for (const [key, val] of Object.entries(d)) {
    if (!val.curve?.length) continue;
    datasets.push({
      label: val.label,
      data: val.curve,
      borderColor: colorMap[key] || '#8b949e',
      backgroundColor: 'transparent',
      borderWidth: key === 'live' ? 2.5 : 1.5,
      pointRadius: 0,
      tension: 0.3,
      borderDash: key === 'live' ? [] : [4, 3],
    });
  }
  btChart = new Chart(ctx, {
    type: 'line',
    data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: true, position: 'bottom',
          labels: { color: '#8b949e', font: { size: 11 }, boxWidth: 20 }
        },
        tooltip: { callbacks: { label: c => `${c.dataset.label}: ${fmtP(c.parsed.y)}` }}
      },
      scales: {
        x: { display: false },
        y: { ticks: { color: '#8b949e', font: { size: 10 }, callback: v => v+'%' },
             grid: { color: 'rgba(33,38,45,.8)' }}
      }
    }
  });

  // 지표 비교 카드
  const metrics = [
    {key:'total',    label:'총 거래',   fmt: v => v+'건'},
    {key:'win_rate', label:'승률',      fmt: v => v+'%', clrFn: v => v>=50?'green':v>=40?'blue':'red'},
    {key:'pf',       label:'Profit Factor', fmt: v => v, clrFn: v => v>=1?'green':'red'},
    {key:'avg_win',  label:'평균 수익', fmt: v => '+'+v+'%', clrFn: ()=>'green'},
    {key:'avg_loss', label:'평균 손실', fmt: v => v+'%',     clrFn: ()=>'red'},
    {key:'cum_pct',  label:'누적 합산', fmt: v => (v>=0?'+':'')+v+'%', clrFn: v=>v>=0?'green':'red'},
  ];

  const order   = ['live', 'v1', 'v2'].filter(k => d[k]);
  const headers = order.map(k => d[k].label);

  let html = `<div class="card overflow-hidden">
    <div class="grid text-xs font-semibold py-2 px-3" style="grid-template-columns:1fr ${order.map(()=>'1fr').join(' ')};border-bottom:1px solid #21262d">
      <span style="color:#484f58">지표</span>
      ${headers.map(h=>`<span class="text-center" style="color:#8b949e">${h}</span>`).join('')}
    </div>`;

  for (const m of metrics) {
    html += `<div class="grid text-sm py-2.5 px-3" style="grid-template-columns:1fr ${order.map(()=>'1fr').join(' ')};border-bottom:1px solid #161b22">
      <span style="color:#8b949e">${m.label}</span>
      ${order.map(k => {
        const v = d[k]?.[m.key] ?? '—';
        const cls = m.clrFn ? m.clrFn(v) : '';
        return `<span class="text-center font-medium ${cls}">${v !== '—' ? m.fmt(v) : '—'}</span>`;
      }).join('')}
    </div>`;
  }
  html += '</div>';
  document.getElementById('btStatsCards').innerHTML = html;

  // 해석 인사이트
  const live = d.live, bt = d.v2 || d.v1;
  let insight = '';
  if (live && bt) {
    const pfDiff = ((live.pf - bt.pf) * 100).toFixed(0);
    const wrDiff = (live.win_rate - bt.win_rate).toFixed(1);
    const pfOk = live.pf >= bt.pf;
    const wrOk = live.win_rate >= bt.win_rate;
    insight = `
      <p class="text-sm font-semibold mb-2">📌 전략 드리프트 진단</p>
      <div class="space-y-1.5 text-xs">
        <div class="flex justify-between">
          <span style="color:#8b949e">실거래 PF vs 백테스트</span>
          <span class="${pfOk?'green':'red'} font-medium">${pfOk?'▲ 우수':'▼ 열세'} (${live.pf} vs ${bt.pf})</span>
        </div>
        <div class="flex justify-between">
          <span style="color:#8b949e">실거래 승률 vs 백테스트</span>
          <span class="${wrOk?'green':'red'} font-medium">${wrOk?'▲ 우수':'▼ 열세'} (${live.win_rate}% vs ${bt.win_rate}%)</span>
        </div>
        <p class="mt-2" style="color:#484f58">
          ${live.total < 30
            ? '⚠️ 실거래 샘플('+live.total+'건)이 통계적 유의성(30건) 미달 — 추가 관찰 필요'
            : pfOk && wrOk
              ? '✅ 실거래가 백테스트 대비 우수 — 전략 작동 양호'
              : '⚠️ 실거래 성과가 백테스트 하회 — 시장 변화 또는 파라미터 재검토 권장'}
        </p>
      </div>`;
  } else {
    insight = `<p class="text-sm" style="color:#484f58">백테스트 파일을 찾을 수 없습니다.</p>`;
  }
  document.getElementById('btInsight').innerHTML = insight;
}

loadData();
setInterval(loadData, 60000);
</script>
</body>
</html>"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081)
