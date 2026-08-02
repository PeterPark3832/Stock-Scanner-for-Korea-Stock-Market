"""리밸런싱 전략 5종 백테스트 — 실전 로직 그대로, 수수료·슬리피지 반영.

핵심 설계
  1) 전략 계산은 scanner.strategy_rebalance 의 _dispatch(_compute_dual/_compute_vaa/_compute_ensemble)를 **그대로 호출**한다.
     백테스트용으로 로직을 재구현하지 않으므로, 여기서 나온 성과는 실제 봇이 낼 성과와 같다.
  2) Look-ahead 차단: D일 리밸런싱 판단에는 D일 **이전** 종가까지만 쓴다(D일 종가는 장중에 모름).
     체결은 D일 시가에 슬리피지를 얹어 처리한다(봇이 09:05 시장가로 주문하므로).
  3) 정수 주수·현금 잔여·수수료를 실제와 동일하게 반영한다.

사용법 (FDR 접근 가능한 운영 서버에서):
    python backtest_rebalance.py                    # 전 전략 비교
    python backtest_rebalance.py --seed 10000000    # 시드 1천만원
    python backtest_rebalance.py --start 2018-01-01
    python backtest_rebalance.py --slippage 0.002   # 슬리피지 0.2% 가정

출력: 콘솔 요약 + backtest_rebalance_report.txt
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

import pandas as pd

from scanner.strategy_rebalance import (
    STRATEGIES, MANAGED_UNIVERSE, NAMES, universe_for, _dispatch,
)

# ── 실거래 비용 가정 ────────────────────────────────────────────────
# 국내 ETF: 증권거래세 면제. 위탁수수료는 증권사별 0.0036~0.015%.
# 슬리피지는 09:05 시장가(개장 직후 스프레드 확대) 기준 보수적 가정.
DEFAULT_COMMISSION = 0.00015   # 0.015% (편도)
DEFAULT_SLIPPAGE   = 0.0015    # 0.15% (편도) — 개장 직후 시장가
CASH_BUFFER        = 0.995     # 수수료·호가 변동 대비 매수 여력 여유

TRADING_DAYS = 252


def fetch_prices(tickers: list[str], start: str) -> dict[str, pd.DataFrame]:
    """FDR 일별 OHLC. {ticker: DataFrame[Open, Close]}"""
    import FinanceDataReader as fdr
    out: dict[str, pd.DataFrame] = {}
    for tk in tickers:
        try:
            df = fdr.DataReader(tk, start)
        except Exception as e:
            print(f"  ! {tk} 조회 실패: {e}", file=sys.stderr)
            continue
        if df is None or df.empty or "Close" not in df.columns:
            print(f"  ! {tk} 데이터 없음", file=sys.stderr)
            continue
        cols = {}
        cols["Close"] = df["Close"].astype(float)
        cols["Open"] = df["Open"].astype(float) if "Open" in df.columns else df["Close"].astype(float)
        d = pd.DataFrame(cols)
        d = d[(d["Close"] > 0) & (d["Open"] > 0)]
        if len(d):
            out[tk] = d
            print(f"  · {tk} {NAMES.get(tk, '')[:22]:24s} {len(d):5d}건  "
                  f"{d.index[0].date()} ~ {d.index[-1].date()}")
    return out


def first_trading_days(index: pd.DatetimeIndex, start: pd.Timestamp) -> list[pd.Timestamp]:
    """각 월의 첫 거래일 목록 (봇의 리밸런싱 시점과 동일)."""
    days = [d for d in index if d >= start]
    seen, out = set(), []
    for d in days:
        key = (d.year, d.month)
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def target_weights_asof(spec: dict, prices: dict[str, pd.DataFrame],
                        tickers: list[str], asof: pd.Timestamp) -> dict[str, float]:
    """asof 시점 목표 비중 — asof '이전' 종가만 사용해 look-ahead를 차단한다.
    프로덕션 디스패처(_dispatch)를 그대로 호출한다."""
    closes = {}
    for tk in tickers:
        df = prices.get(tk)
        if df is None:
            continue
        s = df["Close"].loc[df.index < asof]
        if len(s):
            closes[tk] = s
    if not closes:
        return {}
    w = _dispatch(spec, closes)
    total = sum(w.values())
    if total > 0 and abs(total - 100.0) > 0.01:
        w = {tk: v / total * 100.0 for tk, v in w.items()}
    return w


def run_backtest(key: str, prices: dict[str, pd.DataFrame], start: str, seed: int,
                 commission: float, slippage: float, exec_at: str = "open") -> dict | None:
    """월간 리밸런싱 시뮬레이션.

    exec_at="open"  : 리밸런싱일 시가 체결 (현행 봇 09:05 시장가)
    exec_at="close" : 리밸런싱일 종가 체결 (장중 늦은 시간 집행 근사)
    두 결과의 차이가 곧 '개장 직후 집행'의 비용이다.
    """
    exec_col = "Open" if exec_at == "open" else "Close"
    spec = STRATEGIES[key]
    tickers = [t for t in universe_for(key) if t in prices]
    if not tickers:
        return None

    # 공통 거래일 인덱스 (전략 유니버스 기준)
    idx = None
    for tk in tickers:
        idx = prices[tk].index if idx is None else idx.union(prices[tk].index)
    idx = idx.sort_values()

    # 모멘텀 워밍업: 유니버스 전 종목이 252거래일 이상 확보된 이후부터 시작
    ready = []
    for tk in tickers:
        s = prices[tk]
        if len(s) > TRADING_DAYS:
            ready.append(s.index[TRADING_DAYS])
    if not ready:
        return None
    warm = max(ready)
    req_start = pd.Timestamp(start)
    sim_start = max(warm, req_start)

    rebal_days = first_trading_days(idx, sim_start)
    if len(rebal_days) < 6:
        return None

    cash = float(seed)
    holdings: dict[str, int] = {}
    equity_curve: list[tuple[pd.Timestamp, float]] = []
    fees_paid = 0.0
    turnover_krw = 0.0
    n_orders = 0
    rebal_set = set(rebal_days)

    def price_on(tk: str, day: pd.Timestamp, col: str) -> float | None:
        df = prices.get(tk)
        if df is None:
            return None
        if day in df.index:
            return float(df.at[day, col])
        prev = df.index[df.index <= day]
        return float(df.at[prev[-1], "Close"]) if len(prev) else None

    sim_days = [d for d in idx if d >= sim_start]
    for day in sim_days:
        if day in rebal_set:
            w = target_weights_asof(spec, prices, tickers, day)
            if w:
                # 체결가 = 당일 시가 (매수는 +슬리피지, 매도는 -슬리피지)
                execp = {tk: price_on(tk, day, exec_col) for tk in set(list(w) + list(holdings))}
                execp = {k: v for k, v in execp.items() if v}

                total = cash + sum(holdings.get(tk, 0) * execp.get(tk, 0) for tk in holdings)
                target_qty = {}
                for tk, weight in w.items():
                    p = execp.get(tk)
                    if not p:
                        continue
                    buy_p = p * (1 + slippage)
                    target_qty[tk] = int(total * CASH_BUFFER * weight / 100.0 // buy_p)

                # 매도 먼저 (현금 확보)
                for tk, qty in list(holdings.items()):
                    tgt = target_qty.get(tk, 0)
                    if qty > tgt:
                        sell_qty = qty - tgt
                        p = execp.get(tk)
                        if not p:
                            continue
                        gross = sell_qty * p * (1 - slippage)
                        fee = gross * commission
                        cash += gross - fee
                        fees_paid += fee
                        turnover_krw += gross
                        n_orders += 1
                        holdings[tk] = tgt
                        if holdings[tk] <= 0:
                            del holdings[tk]
                # 매수
                for tk, tgt in target_qty.items():
                    cur = holdings.get(tk, 0)
                    if tgt > cur:
                        buy_qty = tgt - cur
                        p = execp.get(tk)
                        if not p:
                            continue
                        buy_p = p * (1 + slippage)
                        cost = buy_qty * buy_p
                        fee = cost * commission
                        while buy_qty > 0 and cost + fee > cash:   # 현금 부족 시 축소
                            buy_qty -= 1
                            cost = buy_qty * buy_p
                            fee = cost * commission
                        if buy_qty <= 0:
                            continue
                        cash -= cost + fee
                        fees_paid += fee
                        turnover_krw += cost
                        n_orders += 1
                        holdings[tk] = cur + buy_qty

        mv = cash + sum(q * (price_on(tk, day, "Close") or 0) for tk, q in holdings.items())
        equity_curve.append((day, mv))

    if len(equity_curve) < 30:
        return None

    ser = pd.Series([v for _, v in equity_curve], index=[d for d, _ in equity_curve])
    return _metrics(key, spec, ser, seed, fees_paid, turnover_krw, n_orders, len(rebal_days))


def _metrics(key: str, spec: dict, ser: pd.Series, seed: int,
             fees: float, turnover: float, n_orders: int, n_rebal: int) -> dict:
    final = float(ser.iloc[-1])
    years = max((ser.index[-1] - ser.index[0]).days / 365.25, 1e-9)
    total_ret = final / seed - 1
    cagr = (final / seed) ** (1 / years) - 1 if final > 0 else -1.0

    dd = ser / ser.cummax() - 1
    mdd = float(dd.min())

    rets = ser.pct_change().dropna()
    ann_vol = float(rets.std() * (TRADING_DAYS ** 0.5)) if len(rets) > 1 else 0.0
    sharpe = (cagr / ann_vol) if ann_vol > 0 else 0.0
    downside = rets[rets < 0]
    dvol = float(downside.std() * (TRADING_DAYS ** 0.5)) if len(downside) > 1 else 0.0
    sortino = (cagr / dvol) if dvol > 0 else 0.0
    calmar = (cagr / abs(mdd)) if mdd < 0 else 0.0

    monthly = ser.resample("ME").last().pct_change().dropna()
    win_rate = float((monthly > 0).mean() * 100) if len(monthly) else 0.0
    yearly = (ser.resample("YE").last() / ser.resample("YE").first() - 1) * 100

    return {
        "key": key, "name": spec["name"], "profile": spec["profile"],
        "start": ser.index[0], "end": ser.index[-1], "years": years,
        "final": final, "total_ret": total_ret * 100, "cagr": cagr * 100,
        "mdd": mdd * 100, "vol": ann_vol * 100, "sharpe": sharpe,
        "sortino": sortino, "calmar": calmar, "win_rate": win_rate,
        "fees": fees, "turnover": turnover, "n_orders": n_orders, "n_rebal": n_rebal,
        "yearly": yearly, "curve": ser,
    }


def buy_and_hold(prices: dict[str, pd.DataFrame], ticker: str, start: pd.Timestamp,
                 end: pd.Timestamp, seed: int, commission: float, slippage: float) -> dict | None:
    df = prices.get(ticker)
    if df is None:
        return None
    d = df.loc[(df.index >= start) & (df.index <= end)]
    if len(d) < 30:
        return None
    p0 = float(d["Open"].iloc[0]) * (1 + slippage)
    qty = int(seed * CASH_BUFFER // p0)
    cash = seed - qty * p0 * (1 + commission)
    ser = d["Close"] * qty + cash
    spec = {"name": f"Buy&Hold {NAMES.get(ticker, ticker)}", "profile": "벤치마크", "type": "bh"}
    return _metrics(f"bh_{ticker}", spec, ser, seed, qty * p0 * commission, qty * p0, 1, 0)


def fmt_report(results: list[dict], seed: int, commission: float, slippage: float,
               timing: list | None = None) -> str:
    L = []
    A = L.append
    A("=" * 100)
    A("리밸런싱 전략 백테스트 — 실전 로직(_dispatch) 그대로, 비용 반영")
    A("=" * 100)
    A(f"시드 {seed:,}원 | 수수료 {commission*100:.3f}%/편도 | 슬리피지 {slippage*100:.2f}%/편도 "
      f"| 매수여력 {CASH_BUFFER*100:.1f}%")
    A(f"체결 가정: 리밸런싱일 시가(봇 09:05 시장가) | 판단 데이터: 전일 종가까지 (look-ahead 차단)")
    A(f"생성: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    A("")
    A("[ 종합 성과 ]")
    A("-" * 100)
    A(f"{'전략':26s} {'성향':6s} {'기간':11s} {'CAGR':>8s} {'MDD':>8s} {'Sharpe':>7s} "
      f"{'Calmar':>7s} {'월승률':>7s} {'누적':>10s}")
    A("-" * 100)
    for r in results:
        period = f"{r['start'].year}~{r['end'].year}"
        A(f"{r['name'][:25]:26s} {r['profile'][:5]:6s} {period:11s} "
          f"{r['cagr']:7.2f}% {r['mdd']:7.1f}% {r['sharpe']:7.2f} "
          f"{r['calmar']:7.2f} {r['win_rate']:6.1f}% {r['total_ret']:9.1f}%")
    A("-" * 100)
    A("")

    A("[ 연도별 수익률 (%) ]")
    A("-" * 100)
    years = sorted({y.year for r in results for y in r["yearly"].index})
    A(f"{'전략':26s} " + " ".join(f"{y:>8d}" for y in years))
    for r in results:
        row = {y.year: v for y, v in r["yearly"].items()}
        A(f"{r['name'][:25]:26s} " + " ".join(
            f"{row[y]:7.1f}%" if y in row and row[y] == row[y] else f"{'-':>8s}" for y in years))
    A("-" * 100)
    A("")

    A("[ 거래 비용 ]")
    A("-" * 100)
    A(f"{'전략':26s} {'리밸런싱':>9s} {'주문':>7s} {'회전액':>15s} {'수수료':>12s} {'비용/CAGR잠식':>13s}")
    for r in results:
        if r["key"].startswith("bh_"):
            continue
        drag = r["fees"] / seed / max(r["years"], 1e-9) * 100
        A(f"{r['name'][:25]:26s} {r['n_rebal']:9d} {r['n_orders']:7d} "
          f"{r['turnover']:14,.0f}원 {r['fees']:11,.0f}원 {drag:12.2f}%p")
    A("-" * 100)
    A("")

    if timing:
        A("[ 집행 시점 비교 — 개장 직후(09:05) 체결의 비용 ]")
        A("-" * 100)
        A(f"{'전략':26s} {'시가체결(현행)':>14s} {'종가체결':>12s} {'차이':>10s}")
        for name, o, c, d in timing:
            A(f"{name[:25]:26s} {o:13.2f}% {c:11.2f}% {d:+9.2f}%p")
        avg = sum(d for *_, d in timing) / len(timing)
        A("-" * 100)
        A(f"평균 차이 {avg:+.2f}%p/년 — 양수면 장중 늦은 집행이 유리(REBALANCE_TIME 조정 검토).")
        A("주의: 이 비교는 시가/종가 '가격차'만 반영하며 호가 스프레드는 --slippage 가정에 포함.")
        A("")

    strat = [r for r in results if not r["key"].startswith("bh_")]
    bh = [r for r in results if r["key"].startswith("bh_")]
    if strat:
        best_cagr = max(strat, key=lambda r: r["cagr"])
        best_risk = max(strat, key=lambda r: r["calmar"])
        A("[ 판단 ]")
        A("-" * 100)
        A(f"· 절대수익 최고 : {best_cagr['name']} (CAGR {best_cagr['cagr']:.2f}%, MDD {best_cagr['mdd']:.1f}%)")
        A(f"· 위험조정 최고 : {best_risk['name']} (Calmar {best_risk['calmar']:.2f}, "
          f"CAGR {best_risk['cagr']:.2f}%, MDD {best_risk['mdd']:.1f}%)")
        if bh:
            b = bh[0]
            A(f"· 벤치마크      : {b['name']} (CAGR {b['cagr']:.2f}%, MDD {b['mdd']:.1f}%)")
            beat = [r['name'] for r in strat if r['cagr'] > b['cagr']]
            A(f"· 벤치마크 초과 : {', '.join(beat) if beat else '없음 — 단순 보유가 더 나음'}")
        A("")
        A("⚠️ 해석 주의")
        A("   · 표본 기간이 짧고(ETF 상장일 제약) 국면이 제한적이라 CAGR 1~2%p 차이는 노이즈일 수 있음.")
        A("   · 연도별 수익률이 고르게 양호한 전략이 과최적화 위험이 낮음 — 총수익만 보고 고르지 말 것.")
        A("   · 슬리피지 가정에 성과가 민감하면(--slippage 로 재실행) 체결 개선이 전략 교체보다 우선.")
        A("-" * 100)
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="리밸런싱 전략 5종 백테스트")
    ap.add_argument("--seed", type=int, default=10_000_000, help="시드 자본 (원)")
    ap.add_argument("--start", default="2016-01-01", help="시뮬레이션 시작일")
    ap.add_argument("--commission", type=float, default=DEFAULT_COMMISSION)
    ap.add_argument("--slippage", type=float, default=DEFAULT_SLIPPAGE)
    ap.add_argument("--exec-at", dest="exec_at", choices=("open", "close"), default="open",
                    help="체결 시점: open=현행 09:05 / close=장중 늦은 집행 근사")
    ap.add_argument("--out", default="backtest_rebalance_report.txt")
    args = ap.parse_args()

    fetch_from = (pd.Timestamp(args.start) - pd.Timedelta(days=700)).strftime("%Y-%m-%d")
    print(f"[1/3] 가격 데이터 수집 (from {fetch_from})")
    prices = fetch_prices(sorted(MANAGED_UNIVERSE), fetch_from)
    if not prices:
        print("데이터 수집 실패 — FDR 접근을 확인하세요", file=sys.stderr)
        return 1

    print(f"\n[2/3] 전략별 시뮬레이션 (시드 {args.seed:,}원)")
    results = []
    for key in STRATEGIES:
        r = run_backtest(key, prices, args.start, args.seed, args.commission,
                         args.slippage, args.exec_at)
        if r:
            results.append(r)
            print(f"  · {r['name'][:24]:26s} CAGR {r['cagr']:6.2f}%  MDD {r['mdd']:6.1f}%")
        else:
            print(f"  ! {STRATEGIES[key]['name']}: 데이터 부족 — 건너뜀")

    # 집행 시점 비교 — 개장 직후(09:05) 체결이 얼마나 비싼지 실측
    timing = []
    for key in STRATEGIES:
        a = run_backtest(key, prices, args.start, args.seed, args.commission, args.slippage, "open")
        b = run_backtest(key, prices, args.start, args.seed, args.commission, args.slippage, "close")
        if a and b:
            timing.append((a["name"], a["cagr"], b["cagr"], b["cagr"] - a["cagr"]))

    if results:
        lo = min(r["start"] for r in results)
        hi = max(r["end"] for r in results)
        b = buy_and_hold(prices, "069500", lo, hi, args.seed, args.commission, args.slippage)
        if b:
            results.append(b)

    print("\n[3/3] 리포트 생성")
    report = fmt_report(results, args.seed, args.commission, args.slippage, timing)
    print("\n" + report)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report + "\n")
    print(f"\n저장: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
