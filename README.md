# 📈 한국 주식 자동매매 봇 v5.1

KIS OpenAPI 기반 알고리즘 트레이딩 봇. 두 가지 전략 모드를 지원합니다.

| 모드 | 설명 | 기본값 |
|------|------|:------:|
| **`rebalance`** | ETF 듀얼모멘텀·VAA **월간 리밸런싱** (SeedNGrow KR 5종 전략) | ✅ |
| `breakout` | KOSPI/KOSDAQ 전 종목 **스윙 눌림목** 스캔 + TP/SL 자동매매 (레거시) | |

> ⚠️ 이 프로그램은 투자 참고용이며 수익을 보장하지 않습니다.
> 실전 투자 전 반드시 모의투자(`KIS_MODE=paper`)로 충분히 검증하세요.

---

## 핵심 기능

### 리밸런싱 모드 (기본)

| 기능 | 설명 |
|------|------|
| **전략 5종** | 자산배분 모멘텀 / 멀티에셋(kr_gem) / 성장주 / 주도주 / VAA 카나리아 |
| **듀얼 모멘텀** | 3·6·12개월 수익률 상위 종목 동일비중, 약세 시 국고채 도피 |
| **VAA 13612W** | 가중 모멘텀 + breadth 카나리아 — 위험 감지 시 방어자산 전량 도피 |
| **월간 자동 실행** | 매월 첫 거래일 09:05 목표비중 계산 → 매도/매수 자동 주문 |
| **성과 추적** | 장마감 평가금액 스냅샷, 입출금 보정 TWR, KOSPI200 알파 비교 |
| **웹 대시보드** | 포트폴리오·리밸런싱 미리보기/실행·입출금 기록·전략 변경 (`dashboard.py`) |

### 눌림목 모드 (레거시, `STRATEGY_MODE=breakout`)

| 기능 | 설명 |
|------|------|
| **눌림목 탐지** | 돌파봉 후 거래량·캔들 눌림 구간 자동 스캔 (14:30) + 15:20 재검증 |
| **자동 매매** | 신호점수 비례 사이징 매수, TP/SL/트레일링/하드스탑/만료 자동 청산 |
| **리스크 제한** | 섹터 집중 차단, 주간 급락 브레이크, 09:10 갭SL 조기 체크 |

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
# 봇 실행
python main.py

# 대시보드 실행 (별도 터미널)
uvicorn dashboard:app --host 0.0.0.0 --port 8081
# 접속: http://<서버IP>:8081?token=<DASHBOARD_TOKEN>
```

### 4. 테스트

```bash
python -m pytest tests/ -q
# 130개 테스트 전체 통과 확인
```

---

## 환경변수

`.env.example`을 `.env`로 복사 후 아래 항목을 채우세요.

### 전략

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `STRATEGY_MODE` | `rebalance` | `rebalance`=월간 리밸런싱 / `breakout`=눌림목 |
| `STRATEGY_KEY` | `kr_gem` | 리밸런싱 전략: `kr_asset_momentum` `kr_gem` `kr_growth` `kr_leaders` `vaa_kr` |
| `REBALANCE_TIME` | `09:05` | 첫 거래일 자동 리밸런싱 실행 시각 |

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

### 자동매매 / 대시보드

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `AUTO_TRADE` | `false` | `true` 로 설정 시 자동매매 활성화 |
| `TRADE_AMOUNT_PER_STOCK` | `1000000` | (눌림목) 종목당 최대 투자금액 (원) |
| `DASHBOARD_TOKEN` | (없음) | **필수, 20자 이상** — 미설정 시 대시보드가 기동을 거부합니다 |

---

## 스케줄

### 리밸런싱 모드 (기본)

| 시각 | 작업 |
|------|------|
| 09:00 | Heartbeat — 평가금액·생존신호 (월요일: 주간 리포트) |
| 09:05 | 매월 첫 거래일에만 자동 리밸런싱 실행 (`REBALANCE_TIME`) |
| 15:40 | 장마감 평가금액 스냅샷 (TWR·그래프용) |
| 수시 | 대시보드 '리밸런싱' 탭에서 수동 실행 가능 |

### 눌림목 모드

| 시각 | 작업 |
|------|------|
| 09:00 | Heartbeat + 만료 포지션 정리 |
| 09:10 | 갭오픈 SL 조기 체크 |
| 10:00 / 11:30 / 13:00 | 장중 TP/SL 모니터링 |
| 14:30 | 1차 스크리닝 (FDR 전종목 스캔) |
| 14:50 | KIS 토큰 캐시 선발급 |
| 15:20 | 2차 재검증 + 신호 발송 + 자동매수 |
| 15:25 | 장마감 직전 TP/SL 최종 체크 |

---

## 텔레그램 명령어

| 명령어 | 설명 |
|--------|------|
| `/positions` | 보유 포지션 전종목 실시간 PnL |
| `/report` | 누적 성과 + 최근 5건 거래 |
| `/stats` | 최근 스크리닝 필터 통계 (눌림목 모드) |
| `/autotrade on·off` | 자동매매 토글 |
| `/pause` `/resume` | 신규 신호 발송 중지/재개 (눌림목 모드) |
| `/help` | 명령어 목록 안내 |

---

## 리밸런싱 전략 5종

| 키 | 이름 | 성향 | 유니버스 | TOP-N |
|----|------|------|----------|:-----:|
| `kr_asset_momentum` | 한국 자산배분 모멘텀 | 방어 | KOSPI200·미국S&P·금·코스닥150 | 3 |
| `kr_gem` | 한국·미국 멀티에셋 | 밸런스 | KOSPI200·S&P·나스닥100·금·반도체 | 3 |
| `kr_growth` | 한국·미국 성장주 | 공격 | 코스닥150·KOSPI200·나스닥100·S&P·반도체 | 3 |
| `kr_leaders` | 한국 주도주 | 공격 | 삼성전자·SK하이닉스 등 대형주 10종 | 4 |
| `vaa_kr` | 한국형 VAA 카나리아 | 방어 | 공격 4종 + 방어 2종 (13612W) | 2 |

- 듀얼 모멘텀 4종: 3/6/12개월 수익률 평균 상위 TOP-N 동일비중, 단기채권 모멘텀 미달 슬롯은 국고채 3년으로 도피
- VAA: 공격군 음수 모멘텀 감지 시 방어자산(국고채·단기채권) 전량 도피
- 전략 변경: 대시보드 '리밸런싱' 탭 → 전략 변경 (다음 리밸런싱부터 적용)

눌림목 전략 파라미터 근거 → [`STRATEGY.md`](STRATEGY.md)

---

## 프로젝트 구조

```
.
├── main.py                    # 진입점 — STRATEGY_MODE 분기 스케줄러
├── dashboard.py               # FastAPI 웹 대시보드 (포트 8081)
├── scanner/                   # 핵심 패키지
│   ├── config.py              # 전략 파라미터 + 환경변수
│   ├── strategy_rebalance.py  # 리밸런싱 전략 5종 — 목표비중 계산
│   ├── job_rebalance.py       # 월간 리밸런싱 실행·평가금액 스냅샷
│   ├── job_heartbeat.py       # 09:00 생존신호
│   ├── job_screener.py        # (눌림목) 1차·2차 스크리닝
│   ├── job_monitor.py         # (눌림목) TP/SL·갭오픈 체크
│   ├── job_preload.py         # (눌림목) 토큰 선발급
│   ├── kis.py                 # KIS API 클라이언트 (시세·잔고·주문)
│   ├── analytics.py           # RSI·신호점수·KOSPI 필터
│   ├── calendar.py            # 개장일 판단·첫 거래일 계산
│   ├── positions.py           # positions.json I/O
│   ├── history.py             # trade_history.csv I/O
│   ├── performance.py         # PF·주간 리포트·드리프트 감지
│   ├── notify.py              # 텔레그램 전송
│   ├── telegram_cmd.py        # 커맨드 라우터
│   ├── telegram_poll.py       # Long Polling 스레드
│   ├── state.py               # 전역 Lock·Flag·캐시
│   └── logger.py              # 로깅 설정
├── tests/                     # pytest 테스트 (130개)
├── backtest_strategies.py     # (눌림목) 백테스트 도구
└── UI_CHECKLIST.md            # 대시보드 UI 점검 체크리스트
```

런타임 생성 파일(모두 .gitignore): `positions.json` `trade_history.csv`
`rebalance_log.json` `equity_snapshots.json` `cash_flows.json` `scanner.log`

전체 아키텍처 → [`ARCHITECTURE.md`](ARCHITECTURE.md)

---

## 버전 이력

| 버전 | 주요 변경사항 |
|------|--------------|
| **v5.1** | **리밸런싱 모드 신설(기본)** — SeedNGrow KR 5종 전략, 월간 자동 실행, TWR·KOSPI 알파, Robinhood 스타일 대시보드, 테스트 130개 |
| **v5.0** | `scanner/` 패키지 분리 (모듈화), `main.py` 진입점 |
| **v4.6** | KIS 자동매매 (매수·매도), 09:10 갭SL, 신호점수 사이징, 섹터 강제 차단 |
| **v4.5** | logging 도입, 스레드 안전성, Graceful shutdown |
| **v4.4** | 텔레그램 양방향, 체결강도 필터, 신호 점수화 |
| **v4.3** | 포지션 관리 추가 |
| **v4.2** | 기본 스윙 눌림목 탐지 |

상세 변경 이력 → [`CHANGELOG.md`](CHANGELOG.md)
