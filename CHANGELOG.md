# 변경 이력

## v5.0 — 모듈화 리팩토링

### 신규
- `scanner/` 패키지: 2,202줄 단일 파일 → 19개 모듈로 분리
- `main.py`: 스케줄러 전용 진입점 (모든 로직은 scanner/ 내부)
- `tests/`: pytest 62개 테스트 (analytics·calendar·positions·config)
- `conftest.py`: 프로젝트 루트 pytest 설정

### 모듈 역할 분리
| 모듈 | 분리 내용 |
|------|----------|
| `scanner/config.py` | STRATEGY + 환경변수 단일 출처 |
| `scanner/state.py` | 전역 Lock·Flag·캐시 집중 관리 |
| `scanner/analytics.py` | 순수 함수 (RSI·신호점수·KOSPI 필터) |
| `scanner/kis.py` | KIS API 클라이언트 |
| `scanner/job_*.py` | 스케줄 작업별 독립 모듈 |

---

## v4.6 — KIS 자동매매 연동

### 신규
- **자동매매 엔진**: `AUTO_TRADE=true` 시 2차 검증 통과 → 시장가 매수 자동 실행
- **자동 청산**: TP/SL/TRAIL_SL/HARD_SL/EXPIRE 도달 시 시장가 매도 자동 실행
- **`/autotrade on|off`** 명령어: 런타임 자동매매 토글
- **`KIS_ACCOUNT_NO`** 환경변수: 계좌번호 설정
- **`TRADE_AMOUNT_PER_STOCK`** 환경변수: 종목당 최대 투자금액
- **신호점수 비례 사이징**: 40~59점 ×0.6 / 60~79점 ×1.0 / 80점↑ ×1.5
- **09:10 갭오픈 SL 체크** (`job_morning_sl_check`): 진입 익일 갭손실 조기 차단
- **14:50 KIS 토큰 선발급** (`job_preload_kis_token`): 15:20 주문 지연 방지
- **EXPIRE 사후 PnL 추적** (`post_expire_pnl`): 만료 5영업일 후 가격 자동 기록

### 파라미터 변경
| 파라미터 | 이전 | 이후 | 근거 |
|---------|------|------|------|
| `tp_pct` | 0.13 | **0.07** | 백테스트 TP max +7.46% — 13%는 TP 미발동으로 PF 붕괴 |
| `tp1_pct` | 0.04 | **0.00** | TP=7%에서 분할 익절 시 PF 하락 |
| `sl_limit` | 0.10 | **0.04** | R:R 2.03:1 유지, SL 거리 4% 초과 탈락 |
| `hard_stop_pct` | 0.07 | **0.05** | 09:10 갭체크 추가로 7% 불필요 |
| `trail_activate_pct` | 없음 | **0.03** | 즉시 트레일링 → 손실 확대 문제 해소 |
| `bo_body_pct` | 0.07 | **0.09** | 신호 품질 향상 |
| `bo_vol_ratio` | 2.5 | **3.0** | 신호 품질 향상 |
| `rsi_min` | 30 | **45** | RSI<45는 하락 추세 가능성 |
| `pullback_vol` | 1.0 | **0.7** | 거래량 더 확실히 소진된 눌림목만 허용 |
| `min_buy_pressure` | 100 | **110** | 체결강도 기준 강화 |
| `max_hold_days` | 10 | **7** | EXPIRE 평균 7.0일, 10일 불필요 |

### 버그 수정
- **`sl_limit` 하드코딩 버그**: `job_second_screen()`에서 `0.10` 하드코딩 → `STRATEGY["sl_limit"]` 참조
- **트레일링 즉시 발동 버그**: `hwm=entry`로 초기화되어 `trail_sl=entry×0.95` 즉시 SL 역할 → `trail_activate_pct` 도입으로 해소
- **섹터 경고 미차단 버그**: 섹터 집중 시 텔레그램 경고만 발송 → 신호점수 하위 종목 자동 제외로 강화
- **`_first_screen_cache` 레이스 컨디션**: 14:30 write / 15:20 read 동시 접근 무보호 → `_cache_lock` 추가

---

## v4.5 — 운영 안정성 강화

- **logging 도입**: `print()` → `logging.info/warning/error`, RotatingFileHandler
- **스레드 안전성**: `_pause_signals`, `_tg_update_offset`에 `threading.Lock()` 적용
- **`is_market_closed()`**: 장 시간 외 시간대 처리 추가
- **FDR 지수 백오프**: 1s → 2s → 4s 재시도
- **Graceful shutdown**: SIGINT/SIGTERM 수신 시 스케줄 루프 정상 종료
- **`_file_lock`**: positions.json 읽기/쓰기 Lock 적용
- **FDR 호출 간격**: 0.05s → 0.1s (IP 차단 위험 감소)

---

## v4.4 — 양방향 텔레그램 + 신호 품질

- **텔레그램 Long Polling**: `/positions`, `/report`, `/pause`, `/resume` 명령어
- **체결강도 필터**: KIS API 실시간 확인 (≥100)
- **신호 점수화**: 0~100점 종합 평가
- **Phase 3 성과 추적**: 주간 리포트, 드리프트 감지, Sharpe 비율
- **KOSPI MA20 필터**: 시장 약세 시 스크리닝 억제

---

## v4.3 — 포지션 관리

- `positions.json`: 포지션 파일 영속성
- TP/SL 장중 모니터링 (10:00 / 13:00)
- 트레일링 스탑: HWM × (1 - trail_pct)
- EXPIRE: 기간 만료 포지션 정리 알람

---

## v4.2 — 기본 스윙 눌림목 탐지

- 1차 스크리닝 (14:30): FDR 전종목 스캔
- 2차 검증 (15:20): KIS 실시간 재검증
- 텔레그램 단방향 알림 발송
