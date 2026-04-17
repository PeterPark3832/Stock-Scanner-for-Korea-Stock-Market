# KOSPI/KOSDAQ 스윙 트레이딩 전략 리서치
## 실증 연구 기반 한국 주식시장 단기 전략 정리

**작성일:** 2026-04-01  
**데이터 출처:** 학술 논문, KRX 공시, 실증 연구 (하단 Sources 참조)  
**경고:** 아래 전략들은 학술 문헌 및 트레이딩 커뮤니티에서 문서화된 내용을 정리한 것입니다.  
개별 백테스트 수치(승률, Sharpe 등)는 표본 기간·거래비용·슬리피지에 따라 크게 달라집니다.  
실전 운용 전 반드시 Walk-Forward 검증을 수행하십시오.

---

## 1. 한국 시장의 구조적 특수성 (Edge의 근원)

### 1-1. 가격제한폭 ±30% (2015년 6월 15일 이후)
- 변경 전 ±15%, 변경 후 ±30%: 실현 변동성이 일중 기준 **3.6~9.3% 증가** (실증: arxiv 1805.04728)
- 상한가(+30%) 도달 종목 수가 크게 감소 → 상한가 자체가 강한 신호가 됨
- 하한가 근처에서의 패닉 셀링 → **과매도 반등 기회** 증가
- 가격 제한에 도달하면 "마그넷 효과(magnet effect)": 제한폭 접근 시 거래량·변동성 급증 (Princeton/Xiong 연구)

### 1-2. 개인투자자 비중 & 단기 추세 추종
- 한국 시장은 개인 비중이 높고 기관·외국인 대비 단기 추세 추종 성향이 강함
- 실증: 신규 개인투자자 일평균 회전율 **18.12%**, 일반 개인 **8.03%** (Xiong et al., Daily Momentum paper)
- 개인투자자의 순매수는 **다음 달 수익률과 음의 상관관계** → 역추세 가능성 존재
- 단, 개인 쏠림이 강한 테마주·중소형 KOSDAQ에서는 **단기 모멘텀이 오히려 강화**됨

### 1-3. 공매도 제한의 영향
- 한국은 공매도를 **여러 차례 전면 금지** (2020년 3월, 2023년 11월~2025년 3월)
- 공매도 금지 선언 직후: KOSPI +5.66%, KOSDAQ +7.34% (단일 세션 기준)
- 공매도 제한 환경 = **하방 압력 인위적 약화** → 단기 상승 모멘텀 전략에 유리한 구조
- 2025년 3월 31일 전면 재개 후 환경 변화 주시 필요

### 1-4. 시장 이상현상 복제 연구
- KOSPI·KOSDAQ에서 148개 이상현상 중 통계적 유의(|t-stat| ≥ 1.96) 이상현상: **57개(37.8%)**
- 상대적으로 잘 복제된 팩터: **가치(Value), 거래마찰(Trading Friction), 모멘텀(Momentum)**
- 단, KOSPI 단독에서는 모멘텀 유의성이 낮고 KOSDAQ 포함 전체 시장에서 더 강함
- 출처: Journal of Derivatives and Quantitative Studies, Emerald (2020)

---

## 2. 핵심 실증 연구 요약

### 연구 A: 모멘텀과 반전 효과 (1983~2023, 40년)
**출처:** Momentum and reversal effects in the Korean stock market, Investment Analysts Journal Vol.54 No.4 (2024/2025)

| 구분 | 결과 |
|------|------|
| 개별 종목 모멘텀 전략 | **반전(reversal) 효과 우세** |
| 섹터/업종 모멘텀 전략 | **유의미한 효과 없음** |
| 전통 모멘텀(Jegadeesh-Titman) | 특정 하위 기간에만 유효 |
| 다양한 리스크 팩터 통제 후 | 이상현상 여전히 지속 |

**핵심 시사점:** 한국에서 전통적인 크로스섹션 모멘텀(과거 수익률 상위 종목 매수)은 서구 시장과 달리 장기적으로는 **반전이 우세**. 단기(1~4주) 모멘텀은 다름 — 별도 접근 필요.

### 연구 B: 현저성(Salience) 효과와 모멘텀 (Emerging Markets Finance and Trade, 2022)
- 전통 모멘텀 전략은 한국 시장에서 수익성이 낮고 **장기 반전 손실** 발생
- 그러나 **특이적(idiosyncratic) 모멘텀, 순위(rank) 모멘텀, 부호(sign) 모멘텀**은 안정적 수익
- 거래회전율 높은(=개인 주목도 높은) 종목에서 역모멘텀(negative momentum profit)이 유의미
- 구성 기간에 현저한 수익률 종목을 제외하면 전통 모멘텀도 수익성 회복

**실전 적용:** 거래량·회전율이 극단적으로 높은 "이미 시장이 다 아는" 종목의 모멘텀 추종은 위험. 덜 주목받은 종목의 신호가 더 신뢰도 높음.

### 연구 C: 투자자 주목도와 모멘텀 (Journal of Korean Finance Studies, 2020; Research in International Business and Finance, 2021)
- 투자자 주목도(검색량, 거래량)가 높을수록 **음의 모멘텀 이익** 발생
- 낮은 주목도 종목에서 모멘텀 유의미, 높은 주목도 종목에서는 소멸
- 출처: e-kjfs.org 2020; ScienceDirect 2021

### 연구 D: 단기 평균회귀 존재 확인 (2000년 이후, KOSPI·KOSDAQ 전체)
**출처:** Journal of Derivatives and Quantitative Studies, Emerald (2021)

- 2000년 이후 한국 주식시장에서 **단기 평균회귀 실증 확인** (2000년 이전에는 미확인)
- KOSPI·KOSDAQ 모두 해당
- 거래량 변화 효과를 제거하면 분산비율(VR)이 1에 더 근접 → 거래량이 평균회귀 강도에 영향

### 연구 E: 일중 모멘텀 (한국거래소 유가증권시장, 2012~2014)
**출처:** KAIST 석사 논문, KOASAS

- 일중 수익률의 **40거래일 지속** 모멘텀 현상 확인
- 특히 **개장 초반(09:00~09:30)과 종장 후반(14:30~15:00)** 에 집중
- 시장 시가 방향이 종가까지 일관되게 이어지는 경향

---

## 3. 전략별 실증 분석 및 구현 가이드

---

### 전략 1: 52주 신고가 돌파 모멘텀

#### 학술적 근거
- George, Hwang (2004): 52주 신고가 기반 모멘텀이 전통 모멘텀(Jegadeesh-Titman)보다 우월
- 한국 적용 연구: KOSPI·KOSDAQ 2000~2017 데이터 적용 시 **계절성 조정 후 양의 수익**
- 단, 거래비용 포함 시 대부분 시장에서 유의성 감소 (ScienceDirect, J. International Money and Finance 2011)
- 52주 신고가 전략 수익의 원천이 **가격 수준(price level)의 닻내리기(anchoring) 효과**임을 확인

#### 한국 시장 특수성
- 52주 신고가 = 심리적 저항선 돌파 → 개인투자자 FOMO 유발 → 단기 추가 상승
- 단, 이미 주목받는 종목(고거래회전율)에서는 오히려 단기 반전 위험

#### 구현 규칙 (FinanceDataReader)

```python
import FinanceDataReader as fdr
import pandas as pd

def screen_52w_high_breakout(stock_list: list, lookback_days: int = 252) -> list:
    """
    52주 신고가 돌파 스크리닝
    Entry 조건:
      1. 종가가 최근 252일(52주) 최고가를 당일 돌파
      2. 돌파 당일 거래량 >= 20일 평균 거래량의 2배 (확인 필요)
      3. 시가총액 >= 500억 (유동성 필터)
      4. KOSPI MA20 위에서 거래 중 (시장 게이트)
    Exit:
      - TP: +8~12%
      - SL: -5~7% (52주 신고가 이하 재진입 시)
      - 최대 보유: 10거래일
    """
    results = []
    end = pd.Timestamp.today()
    start = end - pd.Timedelta(days=lookback_days + 30)

    for code in stock_list:
        try:
            df = fdr.DataReader(code, start, end)
            if len(df) < lookback_days:
                continue

            df['52w_high'] = df['Close'].rolling(lookback_days).max().shift(1)
            df['vol_ma20'] = df['Volume'].rolling(20).mean()

            latest = df.iloc[-1]
            prev   = df.iloc[-2]

            # 조건 1: 당일 종가가 이전 252일 최고가 돌파
            breakout = (latest['Close'] > latest['52w_high']) and \
                       (prev['Close'] <= prev['52w_high'])

            # 조건 2: 거래량 확인
            vol_confirm = latest['Volume'] >= latest['vol_ma20'] * 2.0

            if breakout and vol_confirm:
                results.append({
                    'code': code,
                    'close': latest['Close'],
                    '52w_high': latest['52w_high'],
                    'vol_ratio': latest['Volume'] / latest['vol_ma20'],
                    'signal': '52W_BREAKOUT'
                })
        except Exception:
            continue
    return results
```

#### 예상 성과 (문헌 기반, 한국 시장)
| 지표 | 범위 |
|------|------|
| 승률 | 45~55% (시장 환경 의존) |
| 평균 보유 기간 | 5~15거래일 |
| 거래비용 전 기대수익 | 양(+), 거래비용 후 불확실 |
| 주의 | 고회전율·테마주는 단기 반전 위험 |

---

### 전략 2: 갭 상승 모멘텀 (Gap-Up Momentum)

#### 학술적 근거
- 일중 모멘텀 연구(KAIST): 시가 방향이 장중 지속되는 경향 — 특히 09:00~09:30
- 시가 갭이 클수록 당일 모멘텀 연속성(continuation) 확률 상승 (일반적 실증)
- 단, 과도한 갭(>4~5%)은 당일 내 반전 가능성도 증가

#### 한국 시장 특수성
- 외국인/기관이 전일 외부 이벤트 반응으로 시가 갭 유발
- 개인이 시가 직후 따라붙는 패턴 → 단기 모멘텀 연장
- 갭이 연속 상한가 테마에서 발생하면 추가 연장 가능성 높음

#### 구현 규칙

```python
def screen_gap_up(stock_list: list, min_gap_pct: float = 0.03) -> list:
    """
    갭 상승 모멘텀 스크리닝 (당일 시가 기준)
    Entry 조건:
      1. 시가 / 전일 종가 - 1 >= min_gap_pct (기본 3%)
      2. 갭 발생 후 시가 이상에서 거래 유지 (시가 지지 확인)
      3. 거래량 >= 20일 평균의 1.5배
      4. 갭 크기 <= 15% (과도한 갭 = 단기 반전 위험)
    Exit:
      - 갭 하단(시가) 이탈 시 즉시 손절
      - TP: 갭 크기의 1.5~2배
      - 최대 보유: 당일 or 익일 시가
    주의: 순수 갭 모멘텀은 단기(당일~1일) 전략. 오래 보유할수록 엣지 소멸.
    """
    results = []
    end = pd.Timestamp.today()
    start = end - pd.Timedelta(days=60)

    for code in stock_list:
        try:
            df = fdr.DataReader(code, start, end)
            if len(df) < 21:
                continue

            df['vol_ma20'] = df['Volume'].rolling(20).mean()
            latest = df.iloc[-1]
            prev   = df.iloc[-2]

            gap_pct = (latest['Open'] / prev['Close']) - 1.0
            # 갭 범위 필터: 3~15%
            if not (min_gap_pct <= gap_pct <= 0.15):
                continue

            # 갭 지지 확인: 종가가 시가 이상
            gap_hold = latest['Close'] >= latest['Open']
            vol_ok   = latest['Volume'] >= latest['vol_ma20'] * 1.5

            if gap_hold and vol_ok:
                results.append({
                    'code': code,
                    'gap_pct': round(gap_pct * 100, 2),
                    'open': latest['Open'],
                    'close': latest['Close'],
                    'vol_ratio': latest['Volume'] / latest['vol_ma20'],
                    'signal': 'GAP_UP_MOMENTUM'
                })
        except Exception:
            continue
    return results
```

#### 예상 성과
| 지표 | 범위 |
|------|------|
| 승률 | 55~65% (갭 지지 확인 후 진입 기준) |
| 평균 보유 기간 | 당일~2거래일 |
| 핵심 위험 | 뉴스 소재 소진 시 당일 내 반전 |
| 필터 효과 | 시가 지지 확인이 승률을 크게 개선 |

---

### 전략 3: 상한가 다음날 추종 전략 (상따)

#### 학술적 근거
- 가격제한폭 도달 종목의 다음날 수익률에 대한 패턴:
  - **대형 투자자는 상한가 당일 매수 → 익일 매도** 경향 (가격 제한 연구 문헌)
  - 가격 제한 근접 시 "마그넷 효과"로 상한가 유지 확률 증가
  - 상한가 직후 단기: **연속 상승(continuation)** or **갭하락(reversal)** 양방향 존재
- 서울대 연구(상한가굳히기 매매): 상한가 유지 패턴이 가격 조종 여부와 연관 → 진짜 상한가와 작전 상한가 구별 필요

#### 한국 실전 커뮤니티 정리 (나무위키, 머니투데이, 씽크풀)
**상한가 다음날 시나리오 3가지:**

| 시나리오 | 발생 조건 | 다음날 패턴 |
|----------|-----------|------------|
| 연속 상한가(연상) | 재료 강도 높음, 거래량 폭발 | 시초가 강세, 추가 상승 |
| 갭 상승 후 반전 | 재료 단발성, 작전성 의심 | 시초가 고가 후 하락 |
| 갭 하락 출발 | 재료 소진, 시장 무관심 | 손실 구간 |

**성공률을 높이는 필터 (커뮤니티 실증):**
1. 재료의 지속성: 단일 이벤트 < 구조적 테마 (2차전지, AI, 반도체 등)
2. 상한가 달성 시각: 장 초반 < 장 중반 < **종가까지 상한가 유지** (강도 순)
3. 거래량 크기: 상한가 당일 거래량이 10일 평균의 **5배 이상** = 신호 강도 상
4. 시장 환경: 테마 장세·개인 주도 장세에서 유효 / 기관 주도 침체 장세에서는 불리
5. 2015년 이후 ±30% 제한폭: 상한가 종목 수 현저 감소 → **각 상한가의 신뢰도 상승**

#### 구현 규칙

```python
def screen_upper_limit_followup(stock_list: list) -> list:
    """
    상한가 다음날 추종 전략 스크리닝
    
    Entry 조건 (전일 상한가 종목):
      1. 전일 종가 >= 전전일 종가 * 1.28 (≈ 상한가, 30% 제한에서 실질적 상한가)
      2. 전일 거래량 >= 10일 평균 거래량의 5배
      3. 전일 종가 = 전일 고가 (종가 상한가 유지 확인)
      4. 시가총액 300억~5000억 (소형~중형: 상한가 이후 이동 가능한 유동성)
      5. 당일 시초가 >= 전일 종가 * 0.98 (갭하락 미발생 확인 후 진입)
    
    Entry 시점: 장 시작 후 10~15분 관망 후 시초가 지지 확인 진입
    Exit:
      - TP: +10~15% (재료 강도에 따라)  
      - SL: 전일 종가(=상한가) 이하 이탈 시 (-5% 이내)
      - 최대 보유: 3거래일
    """
    results = []
    end = pd.Timestamp.today()
    start = end - pd.Timedelta(days=30)

    for code in stock_list:
        try:
            df = fdr.DataReader(code, start, end)
            if len(df) < 12:
                continue

            df['vol_ma10'] = df['Volume'].rolling(10).mean()

            # 전일 기준 (iloc[-2]가 전일, iloc[-1]이 오늘)
            prev   = df.iloc[-2]
            prev2  = df.iloc[-3]
            today  = df.iloc[-1]

            # 조건 1: 전일 상한가 (≥ +28%, ±30% 제한 환경)
            upper_limit = prev['Close'] >= prev2['Close'] * 1.28

            # 조건 2: 전일 거래량 급증
            vol_surge = prev['Volume'] >= prev['vol_ma10'] * 5.0

            # 조건 3: 전일 종가 = 고가 (장 마감까지 상한가 유지)
            closed_at_high = prev['Close'] >= prev['High'] * 0.995

            # 조건 4: 당일 갭하락 없음
            no_gap_down = today['Open'] >= prev['Close'] * 0.98

            if upper_limit and vol_surge and closed_at_high and no_gap_down:
                results.append({
                    'code': code,
                    'prev_close': prev['Close'],
                    'today_open': today['Open'],
                    'gap_pct': round((today['Open'] / prev['Close'] - 1) * 100, 2),
                    'vol_ratio_prev': prev['Volume'] / prev['vol_ma10'],
                    'signal': 'UPPER_LIMIT_FOLLOWUP'
                })
        except Exception:
            continue
    return results
```

#### 예상 성과 (커뮤니티 실증 + 문헌 종합)
| 지표 | 값 |
|------|-----|
| 조건 없이 상한가 다음날 매수 | 승률 ~40~45% (반전 위험 큼) |
| 위 5개 필터 적용 후 | 승률 **55~65%** (추정, 재료 강도 의존) |
| 최대 위험 | 작전성 상한가: 갭 상승 시초가 후 급락 |
| 2015년 이후 | 상한가 종목 수 감소로 신호당 품질 향상 |

**중요 주의:** 단순 "상한가 다음날 매수" 규칙의 승률 수치는 공식 퀀트 백테스트로 확인된 수치가 없습니다. 위 추정치는 조건 조합 효과에 대한 합리적 추론입니다. 반드시 본인이 Walk-Forward 백테스트로 확인하십시오.

---

### 전략 4: 단기 과매도 반등 (KOSDAQ 평균회귀)

#### 학술적 근거
- **실증 확인:** 2000년 이후 한국 주식시장(KOSPI·KOSDAQ)에서 단기 평균회귀 존재 (Emerald, JDQS 2021)
- 거래량 급증 동반 하락 후 평균회귀 강도가 더 강함 (거래량 효과 존재)
- KOSDAQ 소형주에서 더 뚜렷: 개인투자자 패닉 셀링이 과매도를 심화

#### 구현 규칙

```python
def screen_oversold_bounce(stock_list: list) -> list:
    """
    단기 과매도 반등 스크리닝 (평균회귀)
    
    Entry 조건:
      1. RSI(14) < 30 (과매도)
      2. 최근 3~5일 누적 하락 >= -15% (충분한 낙폭)
      3. 당일 종가 > 당일 시가 (하락 중 양봉 — 반전 캔들)
      4. 거래량 >= 5일 평균의 1.5배 (패닉 셀링 확인)
      5. 52주 신저가 근방이 아닐 것 (구조적 하락 종목 제외)
         → 현재가 >= 52주 최저가 * 1.15
      6. 시가총액 >= 500억 (유동성)
    
    Exit:
      - TP: RSI 50 회복 or +8%
      - SL: 진입가 -5%
      - 최대 보유: 5거래일
    """
    results = []
    end = pd.Timestamp.today()
    start = end - pd.Timedelta(days=120)

    for code in stock_list:
        try:
            df = fdr.DataReader(code, start, end)
            if len(df) < 60:
                continue

            # RSI 계산 (Wilder)
            delta = df['Close'].diff()
            gain  = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
            loss  = (-delta).clip(lower=0).ewm(com=13, adjust=False).mean()
            rsi   = 100 - (100 / (1 + gain / loss))

            df['RSI'] = rsi
            df['vol_ma5']  = df['Volume'].rolling(5).mean()
            df['52w_low']  = df['Low'].rolling(252).min()

            latest = df.iloc[-1]

            # 3일 누적 수익률
            cum_ret_3d = (df['Close'].iloc[-1] / df['Close'].iloc[-4]) - 1.0

            cond_rsi      = latest['RSI'] < 30
            cond_drop     = cum_ret_3d <= -0.15
            cond_reversal = latest['Close'] > latest['Open']  # 양봉
            cond_vol      = latest['Volume'] >= latest['vol_ma5'] * 1.5
            cond_not_52wl = latest['Close'] >= latest['52w_low'] * 1.15

            if all([cond_rsi, cond_drop, cond_reversal, cond_vol, cond_not_52wl]):
                results.append({
                    'code': code,
                    'rsi': round(latest['RSI'], 1),
                    'cum_ret_3d': round(cum_ret_3d * 100, 2),
                    'close': latest['Close'],
                    'vol_ratio': latest['Volume'] / latest['vol_ma5'],
                    'signal': 'OVERSOLD_BOUNCE'
                })
        except Exception:
            continue
    return results
```

#### 예상 성과 (문헌 기반)
| 지표 | 값 |
|------|-----|
| 평균회귀 존재 여부 | 확인됨 (2000년 이후) |
| 승률 (조건 조합) | 55~60% 추정 |
| 평균 보유 기간 | 3~7거래일 |
| 핵심 위험 | 구조적 하락 종목 — 필터 5번이 중요 |
| 거래비용 민감도 | 중간 (보유 기간이 짧아 슬리피지 비중 높음) |

---

### 전략 5: 거래량 급증 신고가 (Volume-Confirmed Breakout)

#### 학술적 근거
- 거래량 확인을 동반한 가격 돌파가 거래량 없는 돌파보다 지속성(continuation) 높음 — 일반적 실증 사실
- 한국: 신고가 기법과 볼린저밴드 돌파 기법이 "저항돌파"라는 공통점으로 수익성 상대적 우수 (국내 퀀트 커뮤니티)
- 거래량 급증은 기관·외국인 개입의 간접 신호 → 개인 팔로우 유입 → 단기 모멘텀 연장

#### 기존 stock_scanner_v4.5와의 관계
현재 스캐너(v4.5)의 핵심 전략이 바로 이 패턴입니다:
- `bo_vol_ratio: 2.5` (돌파 당일 거래량 >= 20일 평균의 2.5배)
- `bo_body_pct: 0.07` (양봉 실체 >= 7%)
- 눌림목 진입 + 체결강도 확인

#### 추가 개선 포인트 (연구 기반)

```python
def screen_volume_breakout_new_high(stock_list: list, high_period: int = 60) -> list:
    """
    거래량 급증 + 신고가 돌파 (60일 고점 기준)
    
    현재 v4.5의 단순 돌파와의 차이:
    - 60일 신고가를 돌파하되, 거래량 = 평균의 3배 이상 (더 엄격)
    - 최근 10일 중 거래량 급증이 2회 이상 없었을 것 (기관 지속 개입 vs 단발 펌핑 구분)
    - 거래대금 >= 30억 (유동성)
    
    Entry: 돌파 당일 종가 또는 다음날 눌림목
    Exit:
      - TP: +10~15%
      - SL: 돌파 고점 -3%
      - 최대 보유: 10거래일
    """
    results = []
    end = pd.Timestamp.today()
    start = end - pd.Timedelta(days=high_period + 30)

    for code in stock_list:
        try:
            df = fdr.DataReader(code, start, end)
            if len(df) < high_period + 10:
                continue

            df['vol_ma20']  = df['Volume'].rolling(20).mean()
            df['turnover']  = df['Close'] * df['Volume']
            df['60d_high']  = df['High'].rolling(high_period).max().shift(1)
            df['vol_spike'] = df['Volume'] >= df['vol_ma20'] * 2.5

            latest = df.iloc[-1]
            recent = df.iloc[-11:-1]  # 최근 10일

            breakout    = latest['Close'] > latest['60d_high']
            vol_confirm = latest['Volume'] >= latest['vol_ma20'] * 3.0
            turnover_ok = latest['turnover'] >= 3_000_000_000

            # 최근 10일 중 거래량 스파이크가 1회 이하 (단발 펌핑 방지)
            prior_spikes = recent['vol_spike'].sum()
            not_pumped   = prior_spikes <= 1

            if breakout and vol_confirm and turnover_ok and not_pumped:
                results.append({
                    'code': code,
                    'close': latest['Close'],
                    '60d_high': latest['60d_high'],
                    'vol_ratio': latest['Volume'] / latest['vol_ma20'],
                    'prior_spikes': prior_spikes,
                    'signal': 'VOL_BREAKOUT_NEW_HIGH'
                })
        except Exception:
            continue
    return results
```

#### 예상 성과
| 지표 | 값 |
|------|-----|
| 승률 | 50~60% (거래량 필터 강도에 따라) |
| 평균 보유 기간 | 5~10거래일 |
| 최대 위험 | 단발성 이슈 펌핑 후 즉각 반전 |
| 개선 팁 | 눌림목 진입이 당일 돌파 진입보다 리스크 대비 수익 우수 |

---

## 4. 공통 필터 및 리스크 관리

### 4-1. 시장 게이트 (Market Filter)
```python
# KOSPI MA20 위에 있을 때만 진입 (v4.5에도 구현됨)
kospi = fdr.DataReader('KS11', start, end)
kospi['MA20'] = kospi['Close'].rolling(20).mean()
market_ok = kospi['Close'].iloc[-1] > kospi['MA20'].iloc[-1]
```

### 4-2. 거래비용 현실화
- 한국 주식 매매 수수료: 약 **0.015~0.025%** (증권사별 상이)
- 증권거래세: **0.18%** (2024년 기준, 농어촌특별세 포함)
- 슬리피지: 소형주 **0.1~0.3%**, 대형주 **0.02~0.05%**
- **왕복 총 비용: 약 0.5~1.0%** → 순기대수익에서 차감 필수

### 4-3. 포지션 사이징
- Kelly 기준의 절반(Half-Kelly) 사용 권장
- 종목당 최대 포트폴리오 20% (v4.5: max_positions=5)
- 섹터 집중 제한 (v4.5: max_sector_count=2)

### 4-4. 공매도 환경 모니터링
- 2025년 3월 31일 공매도 전면 재개 → 모멘텀 전략에 역풍 가능
- 공매도 과열 종목 지정 여부 확인 (KRX 일별 공시)

---

## 5. Walk-Forward 검증 방법론

### 5-1. 핵심 원칙
한국 시장 이상현상 연구에서 반복 강조: **인샘플 최적화 수익 != 아웃샘플 실전 수익**

특히 KOSDAQ 소형주는:
- 생존편향(Survivorship Bias): 상장폐지 종목 제외 시 수익률 과장
- 거래비용 과소 추정: 소형주 슬리피지 무시
- 과적합(Overfitting): 파라미터가 많을수록 과거 최적화 함정

### 5-2. 권장 Walk-Forward 구조

```
학습 기간 (In-Sample): 24개월
검증 기간 (Out-of-Sample): 6개월
슬라이딩 윈도우: 6개월씩 전진

예시:
  IS: 2018-01 ~ 2019-12 → OOS: 2020-01 ~ 2020-06
  IS: 2018-07 ~ 2020-06 → OOS: 2020-07 ~ 2020-12
  IS: 2019-01 ~ 2020-12 → OOS: 2021-01 ~ 2021-06
  ... 반복
```

### 5-3. 체크리스트
- [ ] 2020년 코로나 구간 포함 여부 (극단적 변동성)
- [ ] 2023년 11월 공매도 금지 전후 성과 분리 비교
- [ ] 2015년 가격제한폭 변경 전후 파라미터 차이 확인
- [ ] 상장폐지 종목 데이터 포함 (생존편향 제거)
- [ ] 인샘플 최적 파라미터와 OOS 최적 파라미터 괴리 측정

### 5-4. Python Walk-Forward 뼈대

```python
import pandas as pd

def walk_forward_test(
    signal_func,       # 스크리닝 함수
    all_data: dict,    # {code: DataFrame}
    start: str,
    end: str,
    is_months: int = 24,
    oos_months: int = 6,
    tp_pct: float = 0.10,
    sl_pct: float = 0.05,
    hold_days: int = 10,
):
    """
    단순 Walk-Forward 프레임워크
    실제 구현 시 파라미터 그리드 서치를 IS 기간에 적용 후
    최적 파라미터를 OOS에 고정 적용
    """
    results = []
    total_start = pd.Timestamp(start)
    total_end   = pd.Timestamp(end)
    
    cursor = total_start + pd.DateOffset(months=is_months)
    
    while cursor + pd.DateOffset(months=oos_months) <= total_end:
        is_start  = cursor - pd.DateOffset(months=is_months)
        is_end    = cursor
        oos_start = cursor
        oos_end   = cursor + pd.DateOffset(months=oos_months)

        # IS 기간: 파라미터 최적화 (생략, 여기서는 고정 파라미터 사용)
        # OOS 기간: 전략 실행
        oos_trades = simulate_trades(
            signal_func, all_data,
            oos_start.strftime('%Y-%m-%d'),
            oos_end.strftime('%Y-%m-%d'),
            tp_pct, sl_pct, hold_days
        )
        results.extend(oos_trades)
        cursor += pd.DateOffset(months=oos_months)
    
    return pd.DataFrame(results)


def simulate_trades(signal_func, all_data, start, end, tp_pct, sl_pct, hold_days):
    """개별 OOS 기간 시뮬레이션 (슬리피지·세금 포함)"""
    trades = []
    COST = 0.005  # 왕복 거래비용 0.5%
    dates = pd.date_range(start, end, freq='B')  # 영업일

    for date in dates:
        # 해당 날짜까지의 데이터로 신호 생성
        stock_codes = list(all_data.keys())
        signals = signal_func(stock_codes)  # 실제 구현에서는 날짜 필터 필요

        for sig in signals:
            code = sig['code']
            if code not in all_data:
                continue
            df = all_data[code]
            entry_date = date
            entry_price = sig.get('close', df.loc[entry_date, 'Close']) if entry_date in df.index else None
            if entry_price is None:
                continue

            # 보유 기간 동안 TP/SL 확인
            exit_price = None
            exit_reason = 'MAX_HOLD'
            future = df.loc[entry_date:].iloc[1:hold_days+1]

            for _, row in future.iterrows():
                if row['High'] >= entry_price * (1 + tp_pct):
                    exit_price = entry_price * (1 + tp_pct)
                    exit_reason = 'TP'
                    break
                if row['Low'] <= entry_price * (1 - sl_pct):
                    exit_price = entry_price * (1 - sl_pct)
                    exit_reason = 'SL'
                    break
            else:
                exit_price = future['Close'].iloc[-1] if len(future) > 0 else entry_price

            ret = (exit_price / entry_price - 1) - COST
            trades.append({
                'code': code,
                'entry': entry_date,
                'entry_price': entry_price,
                'exit_price': exit_price,
                'return': ret,
                'exit_reason': exit_reason,
                'signal': sig.get('signal', '')
            })
    return trades
```

---

## 6. 전략별 핵심 요약 비교표

| 전략 | 근거 강도 | 예상 승률 | 보유 기간 | 주요 위험 | 한국 특수성 |
|------|-----------|-----------|-----------|-----------|-------------|
| 52주 신고가 돌파 | 강 (학술 복수) | 45~55% | 5~15일 | 고회전 종목 반전 | 가격제한 → 돌파 심리적 의미 강 |
| 갭 상승 모멘텀 | 중 (일중 모멘텀) | 55~65% | 1~2일 | 재료 소진 즉각 반전 | 개인 추종 → 단기 지속 |
| 상한가 추종 (상따) | 중 (커뮤니티+일부 학술) | 55~65% (필터 후) | 1~3일 | 작전성 상한가 | ±30% 제한 → 상한가 희소성 증가 |
| 과매도 반등 | 강 (학술 확인) | 55~60% | 3~7일 | 구조적 하락 종목 | KOSDAQ 개인 패닉 → 과매도 심화 |
| 거래량 신고가 | 중 (실전 커뮤니티) | 50~60% | 5~10일 | 단발 펌핑 | 기관 개입 신호 역할 |

**승률 주의:** 위 수치는 문헌 및 합리적 추론 기반 추정치입니다. 공식적으로 검증된 Out-of-Sample 수치는 아닙니다. 거래비용·슬리피지 차감 후 실질 수익성은 반드시 직접 검증이 필요합니다.

---

## 7. 현재 stock_scanner_v4.5와의 연관성

현재 스캐너가 구현하는 전략은 **전략 5 (거래량 신고가) + 눌림목 진입**의 조합입니다:
- `bo_vol_ratio: 2.5` → 거래량 확인 필터
- `bo_body_pct: 0.07` → 강한 양봉 실체 요건
- `pullback_shape: 0.25` → 눌림목 품질 필터
- `min_buy_pressure: 100` → 체결강도 필터
- `calc_signal_score()` → 0~100점 정량화

**추가할 수 있는 전략 모듈:**
- 위 전략 1~4의 `screen_*()` 함수들을 `job_first_screen()` 유사 구조로 추가
- 각 신호 유형을 `signal` 필드에 구분하여 Telegram 알림에 표시
- `trade_history.csv`에 `signal_type` 컬럼 추가 → 전략별 승률 분리 분석

---

## Sources

- [Market Intraday Momentum with New Measures for Trading Cost: KOSPI (MDPI, 2022)](https://www.mdpi.com/1911-8074/15/11/523)
- [Market anomalies in the Korean stock market (Emerald/JDQS)](https://www.emerald.com/jdqs/article/28/2/3/206237/Market-anomalies-in-the-Korean-stock-market)
- [The Momentum Strategies and Salience: Evidence from the Korean Stock Market (T&F, 2022)](https://www.tandfonline.com/doi/abs/10.1080/1540496X.2022.2034615)
- [Investor attention, firm-specific characteristic, and momentum: Korean stock market (ScienceDirect, 2021)](https://www.sciencedirect.com/science/article/abs/pii/S0275531921000258)
- [Momentum and reversal effects in the Korean stock market (Investment Analysts Journal, 2024/2025)](https://www.tandfonline.com/doi/full/10.1080/10293523.2024.2448054)
- [The short-term mean reversion of stock price and the change in trading volume (Emerald, 2021)](https://www.emerald.com/insight/content/doi/10.1108/JDQS-01-2021-0003/full/html)
- [The 52-week high momentum strategy in international stock markets (ScienceDirect, 2011)](https://www.sciencedirect.com/science/article/abs/pii/S0261560610001099)
- [Daily Momentum and New Investors in Emerging Stock Markets (Xiong et al., Princeton)](https://wxiong.mycpanel.princeton.edu/papers/DailyMomentum.pdf)
- [Effects of a Price Limit Change on Market Stability at the KRX (arXiv, 2018)](https://arxiv.org/pdf/1805.04728)
- [Daily Price Limits and Destructive Market Behavior (Princeton/Xiong)](https://www.princeton.edu/~wxiong/papers/PriceLimit.pdf)
- [Dynamic and Static Volatility Interruptions: Korean Stock Markets (MDPI, 2022)](https://www.mdpi.com/1911-8074/15/3/105)
- [Contrarian strategies and investor overreaction under price limits (Springer)](https://link.springer.com/article/10.1007/s12197-009-9075-5)
- [상따 나무위키](https://namu.wiki/w/%EC%83%81%EB%94%B0)
- [상한가굳히기 실증분석 (서울대 SNU-Space)](https://s-space.snu.ac.kr/handle/10371/134659)
- [한국 주식시장 융합적 모멘텀 투자전략 (KCI, 2015)](https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId=ART002022444)
- [FinanceDataReader GitHub](https://github.com/FinanceData/FinanceDataReader)
- [From Ban to Boom: South Korea Short Selling (S&P Global, 2025)](https://www.spglobal.com/market-intelligence/en/news-insights/research/2025/04/from-ban-to-boom-how-south-korea-learned-to-love-short-selling)
