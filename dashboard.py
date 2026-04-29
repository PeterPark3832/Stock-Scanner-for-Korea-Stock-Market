"""
Stock Scanner Dashboard v2.0
FastAPI + Tailwind CSS + Chart.js — 모바일 최적화 프리미엄 재설계
접속: http://<서버IP>:8081?token=<DASHBOARD_TOKEN>
"""
import csv, json, os, time, threading, requests
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

load_dotenv()

KST            = ZoneInfo("Asia/Seoul")
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
POSITIONS_FILE = os.path.join(BASE_DIR, "positions.json")
HISTORY_FILE   = os.path.join(BASE_DIR, "trade_history.csv")

KIS_APP_KEY    = os.getenv("KIS_APP_KEY", "")
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "")
KIS_MODE       = os.getenv("KIS_MODE", "paper")
KIS_BASE_URL   = ("https://openapi.koreainvestment.com:9443"
                  if KIS_MODE == "real" else
                  "https://openapivts.koreainvestment.com:29443")
DASHBOARD_TOKEN    = os.getenv("DASHBOARD_TOKEN", "scanner2024")
AUTO_TRADE         = os.getenv("AUTO_TRADE", "false").lower() == "true"
TRADE_AMOUNT       = int(os.getenv("TRADE_AMOUNT_PER_STOCK", "200000"))

_token_cache: dict = {"token": None, "expires_at": 0}
_token_lock = threading.Lock()
app = FastAPI()

# ── KIS ───────────────────────────────────────────────────────
def get_token() -> str | None:
    with _token_lock:
        if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
            return _token_cache["token"]
    try:
        r = requests.post(f"{KIS_BASE_URL}/oauth2/tokenP",
            json={"grant_type":"client_credentials",
                  "appkey":KIS_APP_KEY,"appsecret":KIS_APP_SECRET}, timeout=10)
        t = r.json().get("access_token")
        if t:
            with _token_lock:
                _token_cache.update({"token":t,"expires_at":time.time()+86400})
        return t
    except Exception:
        return None

def get_price(ticker: str) -> dict | None:
    token = get_token()
    if not token:
        return None
    try:
        r = requests.get(f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers={"Authorization":f"Bearer {token}","appkey":KIS_APP_KEY,
                     "appsecret":KIS_APP_SECRET,"tr_id":"FHKST01010100"},
            params={"FID_COND_MRKT_DIV_CODE":"J","FID_INPUT_ISCD":ticker}, timeout=5)
        d = r.json()
        if d.get("rt_cd") == "0":
            o = d["output"]
            return {"current": int(o.get("stck_prpr",0)),
                    "change_pct": float(o.get("prdy_ctrt",0))}
    except Exception:
        pass
    return None

# ── 데이터 ────────────────────────────────────────────────────
def load_positions() -> list[dict]:
    try:
        with open(POSITIONS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def load_history() -> list[dict]:
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []

def calc_stats(rows):
    if not rows:
        return dict(total=0,wins=0,losses=0,win_rate=0.0,
                    avg_win=0.0,avg_loss=0.0,pf=0.0,cum_pct=0.0)
    wins = [r for r in rows if float(r["pnl_pct"]) > 0]
    loss = [r for r in rows if float(r["pnl_pct"]) <= 0]
    gw   = sum(float(r["pnl_pct"]) for r in wins)
    gl   = abs(sum(float(r["pnl_pct"]) for r in loss))
    return dict(
        total    = len(rows),
        wins     = len(wins),
        losses   = len(loss),
        win_rate = round(len(wins)/len(rows)*100, 1),
        avg_win  = round(gw/len(wins),2) if wins else 0.0,
        avg_loss = round(-gl/len(loss),2) if loss else 0.0,
        pf       = round(gw/gl,2) if gl else 0.0,
        cum_pct  = round(sum(float(r["pnl_pct"]) for r in rows),2),
    )

def enrich_positions(positions):
    result = []
    for p in positions:
        live  = get_price(p["ticker"])
        entry = p.get("entry", 0)
        tp    = p.get("tp", 0)
        sl    = p.get("sl", 0)
        cur   = live["current"] if live else 0
        pnl   = round((cur - entry)/entry*100, 2) if entry and cur else None

        # TP/SL 진행바: SL~TP 범위에서 현재가 위치 (0~100%)
        rng   = tp - sl if tp > sl else 1
        prog  = max(0, min(100, round((cur - sl)/rng*100))) if cur else 50

        result.append({**p,
            "current":     cur,
            "pnl_pct":     pnl,
            "progress":    prog,
            "is_trailing": p.get("sl",0) > p.get("sl_init", p.get("sl",0)),
            "live_ok":     live is not None,
        })
    return result

# ── API 엔드포인트 ─────────────────────────────────────────────
def auth(token: str):
    if token != DASHBOARD_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

@app.get("/api/data")
async def api_data(token: str = ""):
    auth(token)
    history   = load_history()
    positions = enrich_positions(load_positions())
    stats     = calc_stats(history)

    # 에쿼티 커브 (누적 수익률)
    cumulative, cum = [], 0.0
    equity_dates    = []
    for r in history:
        cum += float(r["pnl_pct"])
        cumulative.append(round(cum, 2))
        equity_dates.append(r["exit_date"][5:])  # MM-DD

    # 최근 거래 이력 (역순 20건)
    recent = []
    for r in reversed(history[-20:]):
        recent.append({
            "name":       r["name"],
            "ticker":     r["ticker"],
            "exit_date":  r["exit_date"],
            "exit_reason":r["exit_reason"],
            "pnl_pct":    float(r["pnl_pct"]),
            "entry_price":int(r.get("entry_price",0)),
            "exit_price": int(r.get("exit_price",0)),
        })

    return JSONResponse({
        "now":         datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        "auto_trade":  AUTO_TRADE,
        "kis_mode":    "실전투자" if KIS_MODE=="real" else "모의투자",
        "trade_amount":TRADE_AMOUNT,
        "stats":       stats,
        "positions":   positions,
        "history":     recent,
        "equity_dates":equity_dates,
        "equity_curve":cumulative,
    })

@app.get("/", response_class=HTMLResponse)
async def dashboard(token: str = ""):
    auth(token)
    return HTMLResponse(HTML.replace("__TOKEN__", token))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081)

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
  * { -webkit-tap-highlight-color: transparent; box-sizing: border-box; }
  body { background:#0d1117; color:#e6edf3; font-family:'Segoe UI',system-ui,sans-serif; overscroll-behavior:none; }

  /* 카드 */
  .card { background:#161b22; border:1px solid #21262d; border-radius:14px; }
  .card-sm { background:#1c2128; border:1px solid #30363d; border-radius:10px; }

  /* 탭 */
  .tab-btn { flex:1; padding:14px 0 10px; font-size:11px; color:#8b949e;
             display:flex; flex-direction:column; align-items:center; gap:3px;
             transition:color .15s; border:none; background:none; cursor:pointer; }
  .tab-btn.active { color:#58a6ff; }
  .tab-btn svg { width:22px; height:22px; }
  .tab-panel { display:none; }
  .tab-panel.active { display:block; }

  /* 펄스 */
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
  .pulse { animation:pulse 2s infinite; }

  /* 뱃지 */
  .badge-tp   { background:#0d2818; color:#3fb950; border:1px solid #196830; }
  .badge-sl   { background:#2d0f0f; color:#f85149; border:1px solid #6e1c1c; }
  .badge-trail{ background:#2d1f00; color:#e3b341; border:1px solid #6e4c00; }
  .badge-exp  { background:#1c2128; color:#8b949e; border:1px solid #30363d; }

  /* 진행바 트랙 */
  .prog-track { height:6px; background:#21262d; border-radius:99px; position:relative; overflow:visible; }
  .prog-fill  { height:100%; border-radius:99px; transition:width .4s; }
  .prog-dot   { position:absolute; top:50%; transform:translate(-50%,-50%);
                width:12px; height:12px; border-radius:50%; border:2px solid #0d1117; }

  /* 스크롤바 */
  ::-webkit-scrollbar { width:3px; }
  ::-webkit-scrollbar-track { background:transparent; }
  ::-webkit-scrollbar-thumb { background:#30363d; border-radius:99px; }

  /* 새로고침 버튼 */
  .refresh-btn { background:#21262d; border:1px solid #30363d; border-radius:8px;
                 padding:6px 14px; font-size:12px; color:#8b949e; cursor:pointer; }
  .refresh-btn:active { background:#30363d; }

  /* KPI 숫자 */
  .kpi-num { font-size:28px; font-weight:700; line-height:1; letter-spacing:-0.5px; }
  .kpi-sub { font-size:11px; color:#8b949e; margin-top:3px; }
  .green { color:#3fb950; } .red { color:#f85149; } .gray { color:#8b949e; } .blue { color:#58a6ff; }
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
    <span id="atBadge" class="text-xs px-2 py-0.5 rounded-full"
          style="background:#1c2128;color:#8b949e;border:1px solid #30363d">—</span>
    <button class="refresh-btn" onclick="loadData()">↻</button>
  </div>
</div>

<!-- 업데이트 시간 -->
<p id="updateTime" class="text-center text-xs mt-2 mb-1" style="color:#484f58">—</p>

<!-- 탭 패널 ───────────────────────────────────────── -->
<div class="px-3">

  <!-- ① 개요 탭 -->
  <div id="tab-overview" class="tab-panel active">

    <!-- KPI 카드 4개 -->
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

    <!-- 에쿼티 커브 -->
    <div class="card p-4 mb-3">
      <div class="flex items-center justify-between mb-3">
        <p class="text-sm font-semibold">에쿼티 커브</p>
        <p class="text-xs" style="color:#484f58">누적 수익률 (%)</p>
      </div>
      <div style="height:160px">
        <canvas id="equityChart"></canvas>
      </div>
    </div>

    <!-- 도넛 + 최근 거래 -->
    <div class="grid grid-cols-2 gap-2 mb-3">
      <div class="card p-4 flex flex-col items-center">
        <p class="text-sm font-semibold mb-2">승/패 비율</p>
        <div style="width:100px;height:100px">
          <canvas id="donutChart"></canvas>
        </div>
        <p id="donutLabel" class="text-xs mt-2" style="color:#8b949e">—</p>
      </div>
      <div class="card p-4">
        <p class="text-sm font-semibold mb-2">청산 사유</p>
        <div id="reasonList" class="space-y-1.5 text-xs"></div>
      </div>
    </div>

  </div>

  <!-- ② 포지션 탭 -->
  <div id="tab-positions" class="tab-panel">
    <div id="positionList" class="space-y-2 mt-1"></div>
  </div>

  <!-- ③ 이력 탭 -->
  <div id="tab-history" class="tab-panel">
    <div id="historyList" class="space-y-1.5 mt-1"></div>
  </div>

</div><!-- /px-3 -->

<!-- 하단 탭 네비게이션 -->
<nav class="fixed bottom-0 left-0 right-0 flex z-30"
     style="background:rgba(13,17,23,.95);backdrop-filter:blur(12px);border-top:1px solid #21262d">
  <button class="tab-btn active" onclick="switchTab('overview',this)" id="nav-overview">
    <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.8">
      <path stroke-linecap="round" stroke-linejoin="round"
            d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"/>
    </svg>개요
  </button>
  <button class="tab-btn" onclick="switchTab('positions',this)" id="nav-positions">
    <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.8">
      <path stroke-linecap="round" stroke-linejoin="round"
            d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/>
    </svg>포지션 <span id="posBadge"></span>
  </button>
  <button class="tab-btn" onclick="switchTab('history',this)" id="nav-history">
    <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.8">
      <path stroke-linecap="round" stroke-linejoin="round"
            d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
    </svg>이력
  </button>
</nav>

<script>
const TOKEN = '__TOKEN__';
let equityChart = null, donutChart = null;

// ── 탭 전환 ───────────────────────────────────────────────────
function switchTab(name, btn) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
}

// ── 포맷 헬퍼 ────────────────────────────────────────────────
const fmt  = n => Number(n).toLocaleString('ko-KR');
const fmtP = n => (n >= 0 ? '+' : '') + Number(n).toFixed(2) + '%';
const clr  = n => n > 0 ? 'green' : n < 0 ? 'red' : 'gray';

function badgeHTML(reason) {
  const map = { TP:'badge-tp TP', SL:'badge-sl SL',
                TRAIL_SL:'badge-trail 트레일', EXPIRE:'badge-exp 만료' };
  const [cls, label] = (map[reason] || 'badge-exp ?').split(' ');
  return `<span class="text-xs px-1.5 py-0.5 rounded-full ${cls} font-medium">${label}</span>`;
}

// ── 데이터 로드 ───────────────────────────────────────────────
async function loadData() {
  document.getElementById('updateTime').textContent = '로딩 중…';
  try {
    const res  = await fetch(`/api/data?token=${TOKEN}`);
    const data = await res.json();
    render(data);
    document.getElementById('updateTime').textContent = '업데이트: ' + data.now;
  } catch(e) {
    document.getElementById('updateTime').textContent = '연결 오류';
  }
}

function render(d) {
  // 헤더
  const dot = document.getElementById('statusDot');
  dot.className = 'w-2 h-2 rounded-full pulse ' + (d.auto_trade ? 'bg-green-400' : 'bg-yellow-400');
  document.getElementById('kisModeBadge').textContent = d.kis_mode;
  const atBadge = document.getElementById('atBadge');
  atBadge.textContent = d.auto_trade ? '🤖 자동매매 ON' : '📋 수동모드';
  atBadge.style.cssText = d.auto_trade
    ? 'background:#0d2818;color:#3fb950;border:1px solid #196830'
    : 'background:#1c2128;color:#8b949e;border:1px solid #30363d';

  // KPI
  const s = d.stats;
  document.getElementById('kpiTotal').textContent = s.total + '건';
  document.getElementById('kpiWL').textContent    = s.wins + '승 ' + s.losses + '패';
  const wrEl = document.getElementById('kpiWR');
  wrEl.textContent = s.win_rate + '%';
  wrEl.className   = 'kpi-num mt-1 ' + (s.win_rate >= 50 ? 'green' : s.win_rate >= 40 ? 'blue' : 'red');
  document.getElementById('kpiPF').textContent    = s.pf;
  document.getElementById('kpiAvgW').textContent  = '+' + s.avg_win + '%';
  document.getElementById('kpiAvgL').textContent  = s.avg_loss + '%';
  const cumEl = document.getElementById('kpiCum');
  cumEl.textContent = (s.cum_pct >= 0 ? '+' : '') + s.cum_pct + '%';
  cumEl.className   = 'kpi-num mt-1 ' + clr(s.cum_pct);
  document.getElementById('kpiAmt').textContent = fmt(d.trade_amount) + '원';

  // 에쿼티 커브
  renderEquity(d.equity_dates, d.equity_curve);

  // 도넛
  renderDonut(s.wins, s.losses);

  // 청산 사유
  const reasons = {};
  d.history.forEach(h => { reasons[h.exit_reason] = (reasons[h.exit_reason]||0)+1; });
  // 전체 이력 기준으로 다시 계산 필요 - history는 최근 20건이므로 stats에서
  const reasonEl = document.getElementById('reasonList');
  const allReasons = {};
  d.history.forEach(h => { allReasons[h.exit_reason] = (allReasons[h.exit_reason]||0)+1; });
  const reasonMap = { TP:'🟢 TP', SL:'🔴 SL', TRAIL_SL:'🟠 트레일', EXPIRE:'⚫ 만료' };
  reasonEl.innerHTML = Object.entries(allReasons)
    .sort((a,b)=>b[1]-a[1])
    .map(([k,v])=>`<div class="flex justify-between">
      <span style="color:#8b949e">${reasonMap[k]||k}</span>
      <span class="text-white font-medium">${v}건</span>
    </div>`).join('');

  // 포지션 뱃지
  document.getElementById('posBadge').textContent =
    d.positions.length ? ` (${d.positions.length})` : '';

  // 포지션 카드
  renderPositions(d.positions);

  // 이력
  renderHistory(d.history);
}

// ── 에쿼티 커브 ───────────────────────────────────────────────
function renderEquity(labels, data) {
  const ctx = document.getElementById('equityChart').getContext('2d');
  const lastVal = data.length ? data[data.length-1] : 0;
  const lineColor = lastVal >= 0 ? '#3fb950' : '#f85149';
  const grad = ctx.createLinearGradient(0, 0, 0, 160);
  grad.addColorStop(0, lastVal >= 0 ? 'rgba(63,185,80,.25)' : 'rgba(248,81,73,.25)');
  grad.addColorStop(1, 'rgba(13,17,23,0)');

  if (equityChart) equityChart.destroy();
  equityChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        data, borderColor: lineColor, backgroundColor: grad,
        borderWidth: 2, pointRadius: 0, fill: true, tension: 0.3,
      }]
    },
    options: {
      responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{display:false},
        tooltip:{ callbacks:{ label: c => fmtP(c.parsed.y) } }
      },
      scales:{
        x:{ ticks:{color:'#484f58',font:{size:10},maxTicksLimit:6}, grid:{display:false} },
        y:{ ticks:{color:'#8b949e',font:{size:10}, callback:v=>v+'%'},
            grid:{color:'rgba(33,38,45,.8)'} }
      }
    }
  });
}

// ── 도넛 차트 ─────────────────────────────────────────────────
function renderDonut(wins, losses) {
  const ctx = document.getElementById('donutChart').getContext('2d');
  if (donutChart) donutChart.destroy();
  donutChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      datasets:[{
        data: [wins, losses],
        backgroundColor:['#3fb950','#f85149'],
        borderWidth:0, borderRadius:3,
      }]
    },
    options:{
      responsive:true, maintainAspectRatio:false, cutout:'72%',
      plugins:{ legend:{display:false},
        tooltip:{ callbacks:{ label:c => c.label+': '+c.parsed } }
      }
    }
  });
  const wr = wins+losses > 0 ? Math.round(wins/(wins+losses)*100) : 0;
  document.getElementById('donutLabel').textContent = `${wins}W / ${losses}L`;
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
    const dotColor = prog > 50 ? '#3fb950' : '#f85149';
    const fillColor = prog > 50 ? '#3fb950' : '#f85149';
    const trailTag = p.is_trailing
      ? `<span class="text-xs px-1.5 py-0.5 rounded-full badge-trail ml-1">트레일</span>` : '';
    const autoTag = p.auto_traded
      ? `<span class="text-xs" style="color:#484f58">🤖 자동</span>`
      : `<span class="text-xs" style="color:#484f58">✋ 수동</span>`;
    const qtyTag = p.quantity
      ? `<span class="text-xs" style="color:#484f58">${p.quantity}주</span>` : '';
    const pnlText = pnl !== null ? `<span class="${pclr} font-bold text-lg">${fmtP(pnl)}</span>` : `<span class="gray text-sm">조회실패</span>`;
    const curText = p.current ? `<span style="color:#8b949e" class="text-sm">${fmt(p.current)}원</span>` : '';
    return `
    <div class="card p-4">
      <div class="flex justify-between items-start mb-3">
        <div>
          <div class="flex items-center gap-1">
            <span class="font-semibold text-white">${p.name}</span>${trailTag}
          </div>
          <div class="flex items-center gap-2 mt-0.5">
            <span style="color:#484f58" class="text-xs">${p.ticker}</span>
            <span style="color:#484f58" class="text-xs">·</span>
            ${autoTag}
            ${qtyTag ? `<span style="color:#484f58" class="text-xs">·</span>${qtyTag}` : ''}
          </div>
        </div>
        <div class="text-right">
          ${pnlText}
          ${curText}
        </div>
      </div>

      <!-- TP/SL 진행바 -->
      <div class="prog-track mb-1">
        <div class="prog-fill" style="width:${prog}%;background:${fillColor}"></div>
        <div class="prog-dot" style="left:${prog}%;background:${dotColor}"></div>
      </div>
      <div class="flex justify-between text-xs mt-1.5">
        <span class="red">SL ${fmt(p.sl)}</span>
        <span style="color:#484f58">진입 ${fmt(p.entry)}</span>
        <span class="green">TP ${fmt(p.tp)}</span>
      </div>

      <!-- 진입일 -->
      <p class="text-xs mt-2" style="color:#484f58">${p.entry_date} 진입</p>
    </div>`;
  }).join('');
}

// ── 거래 이력 ─────────────────────────────────────────────────
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

// ── 초기화 & 자동 갱신 ───────────────────────────────────────
loadData();
setInterval(loadData, 60000);
</script>
</body>
</html>"""
