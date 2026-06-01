# 시스템 아키텍처

## 개요

v5.0에서 2,202줄 단일 파일(`stock_scanner_v4.6.py`)을 `scanner/` 패키지로 분리.  
역할별 모듈 분리로 테스트 용이성·유지보수성 향상.

---

## 패키지 구조

```
scanner/
├── config.py          기준: 변경 불가 상수
├── state.py           기준: 전역 공유 상태
│
├── calendar.py        순수: 장개폐장·영업일 계산
├── analytics.py       순수: RSI·신호점수·시장 필터
├── fdr.py             인프라: FinanceDataReader 래퍼
├── logger.py          인프라: 로거 싱글턴
│
├── notify.py          출력: 텔레그램 메시지 전송
├── positions.py       저장소: positions.json I/O
├── history.py         저장소: trade_history.csv I/O
├── performance.py     분석: PF·드리프트·주간 리포트
│
├── kis.py             외부 API: KIS 토큰·시세·주문·잔고
├── telegram_cmd.py    외부 통신: 커맨드 라우터
├── telegram_poll.py   외부 통신: Long Polling 스레드
│
├── job_heartbeat.py   스케줄 09:00: Heartbeat·만료처리
├── job_monitor.py     스케줄 09:10~15:25: TP/SL 모니터링
├── job_screener.py    스케줄 14:30+15:20: 1차·2차 스크리닝
└── job_preload.py     스케줄 14:50: 토큰 캐시 선발급
```

---

## 의존성 그래프

```
config ──────────────────────────────────────────────────┐
state ◄── config                                         │
logger                                                    │
calendar                                                  │
fdr ◄── logger                                            │
analytics ◄── config, fdr, logger                         │
                                                          ▼
notify ◄── config, logger                        (모든 모듈이 config 사용)
positions ◄── config, state, notify, calendar, logger
history ◄── config, state, calendar, logger
performance ◄── config, notify, history, logger
kis ◄── config, state, notify, logger
telegram_cmd ◄── config, state, notify, positions, history, performance, kis
telegram_poll ◄── config, state, notify, telegram_cmd, logger
job_heartbeat ◄── config, state, notify, positions, history, performance, kis, calendar
job_monitor ◄── config, state, notify, positions, history, kis, calendar
job_screener ◄── config, state, notify, positions, kis, analytics, fdr, calendar
job_preload ◄── config, state, kis, calendar
main.py ◄── (모든 job_*, telegram_poll, 기타)
```

**순환 참조 없음** — 모든 의존 방향이 단방향.

---

## 전역 상태 관리

`scanner/state.py`에 모든 공유 상태를 집중:

```python
_first_screen_cache   # 1차 스크리닝 결과 (14:30→15:20 전달)
_cache_lock           # ↑ 보호 Lock

_kis_token_cache      # KIS OAuth 토큰 + 만료시각
_pause_signals        # 신호 발송 일시정지 플래그
_signals_lock
_tg_update_offset     # 텔레그램 polling offset
_offset_lock
_POSITIONS_FLOCK      # positions.json filelock (크로스 프로세스)
_HISTORY_FLOCK        # trade_history.csv filelock
_shutdown_event       # Graceful shutdown 트리거
_auto_trade_enabled   # 자동매매 런타임 플래그
_auto_trade_lock
```

다른 모듈에서 수정 시:
```python
import scanner.state as state
with state._auto_trade_lock:
    state._auto_trade_enabled = True   # global 키워드 불필요
```

---

## 주요 데이터 흐름

### 스크리닝 파이프라인

```
14:30 job_first_screen()
  └─ FDR 전종목 스캔 (~2,000종목 × 150일 데이터)
  └─ 기준봉·눌림목 조건 필터
  └─ 신호점수 계산
  └─ state._first_screen_cache에 저장 (Lock 보호)
  └─ 텔레그램 1차 결과 발송

14:50 job_preload_kis_token()  ← 15:20 주문 지연 방지

15:20 job_second_screen()
  └─ state._first_screen_cache 읽기 (Lock 보호)
  └─ KIS API 실시간 재검증 (체결강도·지지·캔들·SL 거리)
  └─ 신호점수 min_signal_score 필터
  └─ 섹터 집중도 강제 차단
  └─ AUTO_TRADE=true → _execute_buy_orders() → KIS 시장가 매수
  └─ add_positions() → positions.json 저장
  └─ 텔레그램 최종 신호 발송
```

### 포지션 모니터링

```
09:10 job_morning_sl_check()
  └─ KIS 시가 조회 → SL/HARD_SL 도달 시 즉시 청산

10:00 / 11:30 / 13:00 / 15:25 job_monitor_positions()
  └─ KIS 현재가 조회 → TP/SL/트레일링/하드스탑 체크
  └─ 청산 시 record_trade_history() → trade_history.csv
  └─ save_positions() → positions.json 업데이트
```

### 파일 I/O 안전성

```
positions.json   ─── _POSITIONS_FLOCK (filelock) ─── 봇 + 대시보드 동시 접근 안전
trade_history.csv ─── _HISTORY_FLOCK (filelock)
```

---

## 대시보드 통합

`dashboard.py`는 독립 FastAPI 서비스로, 봇과 파일을 공유:

```
봇 (main.py / stock_scanner_v4.6.py)
  └─ positions.json ──► dashboard.py (폴링 읽기)
  └─ trade_history.csv ──► dashboard.py
  └─ _pause.flag / _resume.flag ◄── dashboard.py (플래그 파일 쓰기)
```

대시보드가 `/pause` 버튼 클릭 시 `_pause.flag` 파일 생성 →  
봇 메인 루프의 `_check_dashboard_flags()`가 1초마다 감지해 처리.

---

## 테스트

```
tests/
├── test_analytics.py   calc_rsi, calc_signal_score (순수함수 — 외부 API 불필요)
├── test_calendar.py    is_market_closed, count_weekdays
├── test_config.py      STRATEGY dict 키·값 범위 검증
└── test_positions.py   positions.json I/O (tmp_path로 실제 파일 격리)
```

```bash
python -m pytest tests/ -v   # 62개 전체 통과
```

외부 API(KIS·텔레그램·FDR)는 테스트에서 호출하지 않아 네트워크 없이 실행 가능.
