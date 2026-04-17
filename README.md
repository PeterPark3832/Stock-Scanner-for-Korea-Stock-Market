# 📈 한국 주식 스윙 눌림목 검색기

KOSPI/KOSDAQ 전 종목을 실시간 스캔해 3-5일 스윙 눌림목 신호를 텔레그램으로 전송하는 봇입니다.  
KIS OpenAPI로 체결강도를 확인하고, 신호 강도를 0-100점으로 점수화해 우선순위를 표시합니다.

## 주요 기능

- **눌림목 자동 탐지** — 돌파 후 눌림 구간 진입 종목 실시간 스캔
- **신호 강도 점수화** — BO 강도(30) + 눌림 품질(40) + 추세 품질(30) = 최대 100점
- **체결강도 필터** — KIS API로 매수/매도 압력 실시간 확인 (100 이하 탈락)
- **포지션 관리** — 진입·청산 자동 추적, 최대 동시 보유 종목 수 제한
- **백테스트** — `backtest_strategies.py` / `backtest_v2.py` 로 전략 검증
- **텔레그램 명령어** — 실시간 포지션/성과 조회 및 스캔 일시정지

## 텔레그램 명령어

| 명령어 | 설명 |
|--------|------|
| `/positions` | 보유 포지션 전종목 실시간 PnL |
| `/report` | 누적 성과 + 최근 5건 거래 |
| `/pause` | 신규 신호 발송 중지 |
| `/resume` | 신규 신호 발송 재개 |
| `/help` | 명령어 목록 안내 |

## 설치 및 실행

```bash
# 1. 패키지 설치
pip install -r requirements.txt

# 2. 환경변수 설정
cp .env.example .env
# .env 파일을 열어 실제 값 입력

# 3. 실행
python stock_scanner_v4.5.py
```

## 환경변수 설정

`.env.example`을 `.env`로 복사 후 아래 항목을 채우세요.

| 변수 | 설명 |
|------|------|
| `TELEGRAM_TOKEN` | 텔레그램 봇 토큰 (@BotFather) |
| `TELEGRAM_CHAT_IDS` | 알림 받을 채팅방 ID (콤마 구분) |
| `TELEGRAM_TOPIC_ID` | 토픽 그룹 스레드 ID (일반 채팅방이면 빈값) |
| `KIS_APP_KEY` | 한국투자증권 OpenAPI App Key |
| `KIS_APP_SECRET` | 한국투자증권 OpenAPI App Secret |
| `KIS_MODE` | `paper`=모의투자, `real`=실전투자 |

## 버전 이력

| 버전 | 주요 변경사항 |
|------|--------------|
| v4.5 | logging 도입, 스레드 안전성 강화, Graceful shutdown |
| v4.4 | 텔레그램 양방향 통신, 운영 안전망, 체결강도 필터, 신호 점수화 |
| v4.3 | 포지션 관리 기능 추가 |
| v4.2 | 기본 스윙 눌림목 탐지 |

## 백테스트 결과

- `KOSPI_KOSDAQ_SwingStrategy_Research.md` — 전략 리서치 문서
- `backtest_results.csv` — v1 전략 백테스트 결과
- `backtest_v2_results.csv` — v2 전략 백테스트 결과

## 주의사항

> 이 프로그램은 투자 참고용이며 수익을 보장하지 않습니다.  
> 실전 투자 전 반드시 모의투자(`KIS_MODE=paper`)로 충분히 검증하세요.
