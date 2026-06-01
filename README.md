# 📈 한국 주식 스윙 눌림목 검색기 v5.0

KOSPI/KOSDAQ 전 종목을 매일 자동 스캔해 **3~7일 스윙 눌림목 신호**를 텔레그램으로 전송하고,  
KIS OpenAPI로 **시장가 매수·매도를 자동 실행**하는 알고리즘 트레이딩 봇입니다.

> ⚠️ 이 프로그램은 투자 참고용이며 수익을 보장하지 않습니다.  
> 실전 투자 전 반드시 모의투자(`KIS_MODE=paper`)로 충분히 검증하세요.

---

## 핵심 기능

| 기능 | 설명 |
|------|------|
| **눌림목 탐지** | 돌파봉 후 거래량·캔들 눌림 구간 자동 스캔 (14:30) |
| **실시간 재검증** | KIS API로 체결강도·지지 재확인 후 최종 진입 결정 (15:20) |
| **신호 점수화** | BO 강도·눌림 품질·추세·위치 0–100점 종합 평가 |
| **자동 매매** | 검증 통과 → 시장가 매수, TP/SL 도달 → 시장가 매도 자동 실행 |
| **포지션 관리** | TP/SL/트레일링/하드스탑/기간만료 자동 청산 |
| **갭 방어** | 09:10 단일가 직후 SL 조기 체크 |
| **리스크 제한** | 섹터 집중 차단, 주간 급락 브레이크, 신호점수 비례 사이징 |
| **성과 추적** | 주간 리포트·드리프트 경고·EXPIRE 사후 PnL 추적 |
| **웹 대시보드** | FastAPI 기반 실시간 포지션·이력 모니터링 (`dashboard.py`) |

---

## 빠른 시작

### 1. 패키지 설치

```bash
pip install -r requirements.txt
```

### 2. 환경변수 설정

```bash
cp .env.example .env
# .env 파일을 열어 실제 값 입력
```

### 3. 실행

```bash
# 봇 실행 (v5.0 모듈화 버전)
python main.py

# 또는 단일 파일 버전 (하위 호환)
python stock_scanner_v4.6.py

# 대시보드 실행 (별도 터미널)
uvicorn dashboard:app --host 0.0.0.0 --port 8081
```

### 4. 테스트

```bash
python -m pytest tests/ -v
# 62개 테스트 전체 통과 확인
```

---

## 환경변수

`.env.example`을 `.env`로 복사 후 아래 항목을 채우세요.

### 텔레그램

| 변수 | 필수 | 설명 |
|------|:----:|------|
| `TELEGRAM_TOKEN` | ✅ | 봇 토큰 (@BotFather) |
| `TELEGRAM_CHAT_IDS` | ✅ | 알림 받을 채팅방 ID (콤마 구분) |
| `TELEGRAM_TOPIC_ID` | | 토픽 그룹 스레드 ID (일반 채팅방이면 빈값) |

### KIS API

| 변수 | 필수 | 설명 |
|------|:----:|------|
| `KIS_APP_KEY` | ✅ | 한국투자증권 OpenAPI App Key |
| `KIS_APP_SECRET` | ✅ | 한국투자증권 OpenAPI App Secret |
| `KIS_MODE` | ✅ | `paper`=모의투자 / `real`=실전투자 |
| `KIS_ACCOUNT_NO` | | 계좌번호 (예: `50071234-01`) — 자동매매 필수 |

### 자동매매

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `AUTO_TRADE` | `false` | `true` 로 설정 시 자동매매 활성화 |
| `TRADE_AMOUNT_PER_STOCK` | `1000000` | 종목당 최대 투자금액 (원) |

> 신호점수 비례 사이징: 40~59점 × 0.6배 / 60~79점 × 1.0배 / 80점↑ × 1.5배

### 대시보드

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `DASHBOARD_TOKEN` | `scanner2024` | 대시보드 접속 토큰 |

---

## 텔레그램 명령어

| 명령어 | 설명 |
|--------|------|
| `/positions` | 보유 포지션 전종목 실시간 PnL |
| `/report` | 누적 성과 + 최근 5건 거래 |
| `/autotrade on` | 자동매매 활성화 |
| `/autotrade off` | 자동매매 비활성화 (알림만 발송) |
| `/pause` | 신규 신호 발송 중지 |
| `/resume` | 신규 신호 발송 재개 |
| `/help` | 명령어 목록 안내 |

---

## 스케줄

| 시각 | 작업 |
|------|------|
| 09:00 | Heartbeat + 만료 포지션 정리 (월요일: 주간 리포트) |
| 09:10 | 갭오픈 SL 조기 체크 |
| 10:00 | 장중 TP/SL 모니터링 |
| 11:30 | 장중 TP/SL 모니터링 |
| 13:00 | 장중 TP/SL 모니터링 |
| 14:30 | 1차 스크리닝 (FDR 전종목 스캔) |
| 14:50 | KIS 토큰 캐시 선발급 |
| 15:20 | 2차 실시간 재검증 + 신호 발송 + 자동매수 |
| 15:25 | 장마감 직전 TP/SL 최종 체크 |

---

## 전략 요약

| 파라미터 | 값 | 근거 |
|---------|-----|------|
| TP | +7% | 백테스트 5,015건 TP max +7.46% |
| SL | bo_open × 0.99 | 기준봉 시가 하단 |
| SL 거리 한도 | ≤ 4% | R:R 2.03:1 유지 |
| 하드스탑 | -5% | 갭손실 최대 방어선 |
| 트레일링 | +3% 이후 HWM -5% | 초기 즉시 발동 방지 |
| 기준봉 | 양봉 9%↑ + 거래량 3배↑ + RSI≥45 | |
| 눌림목 | 거래량 0.7배↓ + 도지형 20%↓ | |
| 최대 보유 | 5종목 (섹터당 2종목) | MDD 관리 |
| 시장 필터 | KOSPI MA20 상승 + 주간 -3% 브레이크 | |

자세한 파라미터 근거 → [`STRATEGY.md`](STRATEGY.md)

---

## 프로젝트 구조

```
.
├── main.py                    # 진입점 (v5.0 모듈화)
├── stock_scanner_v4.6.py      # 진입점 (하위 호환 단일 파일)
├── dashboard.py               # FastAPI 웹 대시보드
├── scanner/                   # 핵심 패키지 (v5.0)
│   ├── config.py              # STRATEGY + 환경변수
│   ├── state.py               # 전역 Lock·Flag·캐시
│   ├── analytics.py           # RSI·신호점수·KOSPI 필터
│   ├── calendar.py            # 장개폐장 판단·영업일 계산
│   ├── positions.py           # positions.json I/O
│   ├── history.py             # trade_history.csv I/O
│   ├── performance.py         # PF·주간 리포트·드리프트 감지
│   ├── notify.py              # 텔레그램 메시지 전송
│   ├── kis.py                 # KIS API 클라이언트
│   ├── telegram_cmd.py        # 커맨드 라우터
│   ├── telegram_poll.py       # Long Polling 스레드
│   ├── job_heartbeat.py       # 09:00 작업
│   ├── job_monitor.py         # TP/SL·갭오픈 체크
│   ├── job_screener.py        # 1차·2차 스크리닝
│   └── job_preload.py         # 토큰 선발급
├── tests/                     # pytest 테스트 (62개)
├── backtest_strategies.py     # 백테스트 도구
├── backtest_v2.py             # 백테스트 v2
├── positions.json             # 현재 포지션 (런타임 생성)
└── trade_history.csv          # 매매 이력 (런타임 생성)
```

전체 아키텍처 → [`ARCHITECTURE.md`](ARCHITECTURE.md)

---

## 백테스트 요약 (2021~2024, 5,015건)

| 지표 | v4.6 기준 |
|------|-----------|
| 승률 | 22.5% |
| 평균 TP | +6.08% |
| 평균 SL | -3.26% |
| R:R | 2.03:1 |
| 손익분기 승률 | 33.0% |
| 최대 연속 손실 | 22회 |

전략 리서치 상세 → [`KOSPI_KOSDAQ_SwingStrategy_Research.md`](KOSPI_KOSDAQ_SwingStrategy_Research.md)

---

## 버전 이력

| 버전 | 주요 변경사항 |
|------|--------------|
| **v5.0** | `scanner/` 패키지 분리 (모듈화), `main.py` 진입점, pytest 62개 |
| **v4.6** | KIS 자동매매 (매수·매도), 09:10 갭SL, 신호점수 사이징, 섹터 강제 차단 |
| **v4.5** | logging 도입, 스레드 안전성, Graceful shutdown |
| **v4.4** | 텔레그램 양방향, 체결강도 필터, 신호 점수화 |
| **v4.3** | 포지션 관리 추가 |
| **v4.2** | 기본 스윙 눌림목 탐지 |

상세 변경 이력 → [`CHANGELOG.md`](CHANGELOG.md)
