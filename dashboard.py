"""
Stock Scanner Dashboard v1.0
FastAPI + Tailwind CSS + Chart.js — 모바일 최적화
접속: http://<서버IP>:8080?token=<DASHBOARD_TOKEN>
"""
import csv
import json
import os
import time
import threading
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse

load_dotenv()

KST = ZoneInfo("Asia/Seoul")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
POSITIONS_FILE    = os.path.join(BASE_DIR, "positions.json")
TRADE_HISTORY_FILE = os.path.join(BASE_DIR, "trade_history.csv")

KIS_APP_KEY    = os.getenv("KIS_APP_KEY", "")
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "")
KIS_MODE       = os.getenv("KIS_MODE", "paper")
KIS_BASE_URL   = "https://openapi.koreainvestment.com:9443" if KIS_MODE == "real" else "https://openapivts.koreainvestment.com:29443"
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "scanner2024")

_token_cache: dict = {"token": None, "expires_at": 0}
_token_lock = threading.Lock()

app = FastAPI()


# ── KIS 헬퍼 ──────────────────────────────────────────────────
def get_token() -> str | None:
    with _token_lock:
        if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
            return _token_cache["token"]
    try:
        res = requests.post(
            f"{KIS_BASE_URL}/oauth2/tokenP",
            json={"grant_type": "client_credentials", "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET},
            timeout=10,
        )
        data = res.json()
        token = data.get("access_token")
        if token:
            with _token_lock:
                _token_cache["token"] = token
                _token_cache["expires_at"] = time.time() + 86400
        return token
    except Exception:
        return None


def get_price(ticker: str) -> dict | None:
    token = get_token()
    if not token:
        return None
    try:
        tr_id = "FHKST01010100" if KIS_MODE == "real" else "FHKST01010100"
        res = requests.get(
            f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers={"Authorization": f"Bearer {token}", "appkey": KIS_APP_KEY,
                     "appsecret": KIS_APP_SECRET, "tr_id": tr_id},
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
            timeout=5,
        )
        d = res.json()
        if d.get("rt_cd") == "0":
            o = d["output"]
            return {"current": int(o.get("stck_prpr", 0)), "change_pct": float(o.get("prdy_ctrt", 0))}
    except Exception:
        pass
    return None


# ── 데이터 로더 ────────────────────────────────────────────────
def load_positions() -> list[dict]:
    if not os.path.exists(POSITIONS_FILE):
        return []
    try:
        with open(POSITIONS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def load_history() -> list[dict]:
    if not os.path.exists(TRADE_HISTORY_FILE):
        return []
    try:
        with open(TRADE_HISTORY_FILE, encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def calc_stats(rows: list[dict]) -> dict:
    if not rows:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                "avg_win": 0, "avg_loss": 0, "pf": 0, "cum_pct": 0}
    wins  = [r for r in rows if float(r["pnl_pct"]) > 0]
    loss  = [r for r in rows if float(r["pnl_pct"]) <= 0]
    gw = sum(float(r["pnl_pct"]) for r in wins)
    gl = abs(sum(float(r["pnl_pct"]) for r in loss))
    return {
        "total":    len(rows),
        "wins":     len(wins),
        "losses":   len(loss),
        "win_rate": round(len(wins) / len(rows) * 100, 1),
        "avg_win":  round(gw / len(wins), 2) if wins else 0,
        "avg_loss": round(-gl / len(loss), 2) if loss else 0,
        "pf":       round(gw / gl, 2) if gl else 0,
        "cum_pct":  round(sum(float(r["pnl_pct"]) for r in rows), 2),
    }


def build_positions_with_live(positions: list[dict]) -> list[dict]:
    result = []
    for p in positions:
        live = get_price(p["ticker"])
        entry = p.get("entry", 0)
        cur = live["current"] if live else 0
        pnl = round((cur - entry) / entry * 100, 2) if entry and cur else None
        trail_sl = p.get("sl", 0)
        sl_init  = p.get("sl_init", trail_sl)
        result.append({
            **p,
            "current":    cur,
            "pnl_pct":    pnl,
            "is_trailing": trail_sl > sl_init,
            "live_ok":    live is not None,
        })
    return result


# ── HTML ───────────────────────────────────────────────────────
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
<meta http-equiv="refresh" content="60">
<title>Scanner Dashboard</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  body { background:#0f1117; color:#e2e8f0; font-family:'Segoe UI',system-ui,sans-serif; }
  .card { background:#1a1f2e; border:1px solid #2d3748; border-radius:12px; }
  .pill-green { background:#064e3b; color:#6ee7b7; }
  .pill-red   { background:#450a0a; color:#fca5a5; }
  .pill-gray  { background:#1e293b; color:#94a3b8; }
  .up   { color:#34d399; }
  .down { color:#f87171; }
  .flat { color:#94a3b8; }
  ::-webkit-scrollbar { width:4px; } ::-webkit-scrollbar-track { background:#0f1117; }
  ::-webkit-scrollbar-thumb { background:#2d3748; border-radius:2px; }
</style>
</head>
<body class="min-h-screen p-3 pb-6">

<!-- 헤더 -->
<div class="flex items-center justify-between mb-4">
  <div>
    <h1 class="text-lg font-bold text-white">📈 Scanner v4.6</h1>
    <p class="text-xs text-slate-400">{{ now }}</p>
  </div>
  <div class="text-right">
    <span class="text-xs px-2 py-1 rounded-full {{ 'pill-green' if auto_trade else 'pill-gray' }}">
      {{ '🤖 자동매매 ON' if auto_trade else '📋 수동모드' }}
    </span>
    <p class="text-xs text-slate-500 mt-1">{{ kis_mode }}</p>
  </div>
</div>

<!-- 성과 요약 카드 -->
<div class="grid grid-cols-2 gap-2 mb-4">
  <div class="card p-3">
    <p class="text-xs text-slate-400 mb-1">총 거래</p>
    <p class="text-2xl font-bold text-white">{{ stats.total }}<span class="text-sm text-slate-400">건</span></p>
    <p class="text-xs text-slate-400 mt-1">{{ stats.wins }}승 {{ stats.losses }}패</p>
  </div>
  <div class="card p-3">
    <p class="text-xs text-slate-400 mb-1">승률</p>
    <p class="text-2xl font-bold {{ 'up' if stats.win_rate >= 50 else 'down' }}">{{ stats.win_rate }}<span class="text-sm">%</span></p>
    <p class="text-xs text-slate-400 mt-1">PF {{ stats.pf }}</p>
  </div>
  <div class="card p-3">
    <p class="text-xs text-slate-400 mb-1">평균 수익</p>
    <p class="text-2xl font-bold up">+{{ stats.avg_win }}<span class="text-sm">%</span></p>
    <p class="text-xs text-slate-400 mt-1">수익 거래 기준</p>
  </div>
  <div class="card p-3">
    <p class="text-xs text-slate-400 mb-1">누적 합산</p>
    <p class="text-2xl font-bold {{ 'up' if stats.cum_pct >= 0 else 'down' }}">{{ '+' if stats.cum_pct >= 0 else '' }}{{ stats.cum_pct }}<span class="text-sm">%</span></p>
    <p class="text-xs text-slate-400 mt-1">평균손실 {{ stats.avg_loss }}%</p>
  </div>
</div>

<!-- P&L 차트 -->
{% if chart_labels %}
<div class="card p-3 mb-4">
  <p class="text-sm font-semibold text-slate-300 mb-3">📊 최근 거래 P&L</p>
  <div style="height:160px">
    <canvas id="pnlChart"></canvas>
  </div>
</div>
{% endif %}

<!-- 현재 포지션 -->
<div class="mb-4">
  <p class="text-sm font-semibold text-slate-300 mb-2">📋 현재 포지션 ({{ positions|length }}개)</p>
  {% if positions %}
    {% for p in positions %}
    <div class="card p-3 mb-2">
      <div class="flex justify-between items-start mb-2">
        <div>
          <p class="font-semibold text-white text-sm">{{ p.name }}</p>
          <p class="text-xs text-slate-500">{{ p.ticker }} · {{ p.entry_date }}</p>
        </div>
        <div class="text-right">
          {% if p.pnl_pct is not none %}
            <p class="font-bold text-base {{ 'up' if p.pnl_pct > 0 else ('down' if p.pnl_pct < 0 else 'flat') }}">
              {{ '+' if p.pnl_pct > 0 else '' }}{{ p.pnl_pct }}%
            </p>
            <p class="text-xs text-slate-400">{{ '{:,}'.format(p.current) }}원</p>
          {% else %}
            <p class="text-xs text-slate-500">시세 조회 실패</p>
          {% endif %}
        </div>
      </div>
      <div class="grid grid-cols-3 gap-1 text-xs">
        <div class="text-center bg-slate-800 rounded p-1">
          <p class="text-slate-500">진입가</p>
          <p class="text-slate-200">{{ '{:,}'.format(p.entry) }}</p>
        </div>
        <div class="text-center bg-slate-800 rounded p-1">
          <p class="text-slate-500">목표(TP)</p>
          <p class="text-green-400">{{ '{:,}'.format(p.tp) }}</p>
        </div>
        <div class="text-center bg-slate-800 rounded p-1">
          <p class="text-slate-500">{{ '손절★' if p.is_trailing else '손절' }}</p>
          <p class="text-red-400">{{ '{:,}'.format(p.sl) }}</p>
        </div>
      </div>
      {% if p.quantity %}
      <p class="text-xs text-slate-500 mt-1">{{ p.quantity }}주 · {{ '🤖 자동매수' if p.auto_traded else '✋ 수동' }}</p>
      {% endif %}
    </div>
    {% endfor %}
  {% else %}
    <div class="card p-4 text-center text-slate-500 text-sm">보유 포지션 없음</div>
  {% endif %}
</div>

<!-- 최근 거래 이력 -->
<div>
  <p class="text-sm font-semibold text-slate-300 mb-2">📜 최근 거래 이력</p>
  <div class="card overflow-hidden">
    {% if history %}
      {% for h in history %}
      <div class="flex justify-between items-center px-3 py-2 {{ 'border-t border-slate-800' if not loop.first else '' }}">
        <div>
          <p class="text-sm text-white font-medium">{{ h.name }}</p>
          <p class="text-xs text-slate-500">{{ h.exit_date }} · {{ h.exit_reason }}</p>
        </div>
        <p class="font-bold text-sm {{ 'up' if float(h.pnl_pct) > 0 else 'down' }}">
          {{ '+' if float(h.pnl_pct) > 0 else '' }}{{ h.pnl_pct }}%
        </p>
      </div>
      {% endfor %}
    {% else %}
      <div class="p-4 text-center text-slate-500 text-sm">거래 이력 없음</div>
    {% endif %}
  </div>
</div>

<!-- 새로고침 안내 -->
<p class="text-center text-xs text-slate-600 mt-4">60초 자동 새로고침</p>

{% if chart_labels %}
<script>
const ctx = document.getElementById('pnlChart').getContext('2d');
const labels = {{ chart_labels | tojson }};
const data   = {{ chart_data | tojson }};
const colors = data.map(v => v >= 0 ? 'rgba(52,211,153,0.8)' : 'rgba(248,113,113,0.8)');
new Chart(ctx, {
  type: 'bar',
  data: {
    labels,
    datasets: [{
      data,
      backgroundColor: colors,
      borderRadius: 3,
      borderSkipped: false,
    }]
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { display: false },
      y: {
        ticks: { color:'#94a3b8', font:{size:10},
          callback: v => v + '%'
        },
        grid: { color:'rgba(45,55,72,0.5)' }
      }
    }
  }
});
</script>
{% endif %}

</body>
</html>"""


# ── 라우터 ─────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, token: str = ""):
    if token != DASHBOARD_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

    from jinja2 import Environment
    env = Environment(autoescape=True)
    env.filters["tojson"] = json.dumps

    positions_raw = load_positions()
    history_raw   = load_history()
    stats         = calc_stats(history_raw)

    # 현재가 조회 (KIS)
    positions_live = build_positions_with_live(positions_raw)

    # 최근 20건 역순
    recent_history = list(reversed(history_raw[-20:]))

    # 차트 데이터 (최근 30건)
    chart_rows   = history_raw[-30:]
    chart_labels = [r["name"][:4] for r in chart_rows]
    chart_data   = [float(r["pnl_pct"]) for r in chart_rows]

    auto_trade = os.getenv("AUTO_TRADE", "false").lower() == "true"
    kis_mode   = "🔴 실전투자" if KIS_MODE == "real" else "🟡 모의투자"
    now_str    = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

    template = env.from_string(HTML_TEMPLATE)
    html = template.render(
        now=now_str,
        auto_trade=auto_trade,
        kis_mode=kis_mode,
        stats=stats,
        positions=positions_live,
        history=recent_history,
        chart_labels=chart_labels,
        chart_data=chart_data,
    )
    return HTMLResponse(content=html)


@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now(KST).isoformat()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
