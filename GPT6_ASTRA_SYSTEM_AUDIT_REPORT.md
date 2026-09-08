# Whykoff 정량 전략·아키텍처 감사 보고서

감사일: 2026-09-08 (Asia/Seoul)  
대상 리비전: `794db820302246836e02505256636996f422654a`  
요청서: [GPT6_ASTRA_SYSTEM_AUDIT_PROMPT.md](GPT6_ASTRA_SYSTEM_AUDIT_PROMPT.md)

**판정: 현재 저장소만으로 `wyckoff_v2.0_full_swing`의 실거래 기대값 +1.58%, PF 1.40, 표본외 검증 완료를 인정할 수 없다.** 가장 큰 문제는 파라미터 최적화보다 먼저 해결해야 할 **진입 필터 우회, 백테스트와 추적기의 서로 다른 청산 정책, 비현실적 체결 기록, 중복 실행에 따른 상태 변화**다. 이는 전략에 수익성이 없다는 판정이 아니라, 제시된 성과를 운영 전략의 검증 근거로 사용할 수 없다는 판정이다.

본 작업은 감사·개선 설계다. 운영 코드, DB, GCP, Telegram 및 챔피언 설정은 변경하지 않았다. 아래 수치 제안은 모두 미검증 연구 후보이며, 기존 점수·별점 기준을 바꾸거나 성능 개선을 실증했다는 뜻이 아니다.

검토 근거는 필수 문서 3종, `AGENTS.md`, 스캐너·지표·백테스터·추적기·스케줄러·CLI·수집기·스키마·관련 테스트다. 운영 DB의 597개 원거래, 실제 체결, GCP 실행 로그와 설치 리비전은 확보하지 않았다. 303/306/316개라는 유니버스 수치와 OHLCV 레코드 수는 문서 주장으로 취급한다. 외부 자료는 해당 문장에 직접 연결했으며, 프로젝트별 수식과 제안값은 감사자의 설계다.

## 1. Mathematical & Architectural Flaw Assessment

### 1.1 확정 결함과 심각도

Critical은 거래 자격·체결 손익·챔피언 선택을 잘못 판정하는 문제, High는 성과 검증 또는 운영 신뢰도를 크게 떨어뜨리는 문제, Medium은 계산·해석·유지보수 문제로 분류했다. 파일 위치는 위 리비전 기준이다.

| ID | 심각도 | 발견 사항 및 근거 | 영향·권고 |
|---|---|---|---|
| C1 | Critical | `engine/backtester.py:93`, `services/scheduler.py:112`에서 `strict_filter=False`. 스캐너 `engine/wyckoff_scanner.py:306`은 `strict_filter and is_failed`일 때만 별점 탈락 처리한다. | 낙폭·기울기 등의 필수 조건 탈락 종목도 5성이 되어 진입한다. 진단 점수 반환과 `entry_eligible`을 별도 필드로 분리하고 모든 소비자가 자격을 검사해야 한다. |
| C2 | Critical | `engine/backtester.py:152`부터 TP1 도달 시 전량 종료. TP2는 저장하지만 청산에 사용하지 않는다. 추적기는 TP1 50%·TP2 잔여·40일 모드를 사용한다. | 현재 백테스터로 2단계 운영 전략을 검증할 수 없다. 공통 순수 상태 전이 함수를 라이브·백테스트에서 함께 사용해야 한다. |
| C3 | Critical | `services/trade_tracker.py:343`은 open을 조회하지 않고 `:386`에서 언제나 SL 가격 체결을 기록한다. | TP1 후 E=100, SL=100.5, 하루 전체 가격 88~92인 경우도 100.5에 팔았다고 기록. 가상의 +10.25% 종합 수익을 만든다. 체결 원장 또는 갭·슬리피지 모델이 필요하다. |
| C4 | Critical | 추적기 `:397`의 TP2 분기가 TP1보다 앞서며, 이전 `tp1_hit=False`이면 `:405`에서 전량 +50%를 기록한다. | O=110, L=109, H=151, E=100인 합성 봉은 TP1 50%/TP2 50%의 +35% 대신 +50%로 기록된다. 부분 체결 이벤트를 순차 정산해야 한다. |
| C5 | Critical | `engine/backtester.py:470`은 `auto_promote=False`여도 `is_champion=True`로 INSERT. 최초 챔피언 분기 `:456`도 플래그를 무시한다. | dry-run이 챔피언 행을 생성한다. `get_current_champion()`이 최신 TRUE 행을 선택하므로 실제 선택도 바뀔 수 있다. 비교는 순수 계산으로, 저장과 승격은 별도 트랜잭션으로 분리한다. |
| H1 | High | `WyckoffParams.enable_overhead_gate=False` (`engine/wyckoff_scanner.py:49`); 운영 scheduler는 params를 주입하지 않는다. `main.py:33`의 챔피언 조회는 배너용이다. | 문서상 필수 5% 게이트가 기본 운영 경로에 적용되지 않는다. 켜더라도 C1 때문에 진단 모드에서 자격 차단이 우회된다. 버전별 불변 설정과 해시를 실제 계산에 주입해야 한다. |
| H2 | High | 추적기 `:408`의 `elif high >= tp1`에 이미 TP1인 포지션도 진입하며, 뒤의 `:453` 만료 검사를 건너뛴다. | TP1 이후 high가 계속 TP1 이상이면 41일 이상 OPEN 유지 가능. 만료 검사를 체결 처리 후 독립 실행한다. |
| H3 | High | 추적기 `:359`는 호출마다 보유일 +1. `:343`의 가격 조회에 as-of 상한·거래일·신선도 검사가 없다. | 같은 날 재실행, 휴장일의 오래된 봉, 과거 날짜 요청 모두 잘못 정산될 수 있다. `(trade_id, market_session, event_id)` 멱등성과 명시적 bar timestamp가 필요하다. |
| H4 | High | 백테스터 `:85`의 루프는 마지막 봉을 처리하지 않고, 끝에 남은 포지션도 반환하지 않는다. | 마지막 봉 손절·이익·평가손익 누락. 방향을 단정할 수 없는 표본 선택 왜곡이다. terminal mark-to-market과 미청산 상태를 별도로 보고한다. |
| H5 | High | `all_trades.extend()` (`engine/backtester.py:331`)로 티커별 순서 수집, `:270`에서 각 거래 수익을 전액 복리화한다. | 동시 보유·자본 제약·현금·부분익절·시간 순서가 없는 MDD다. 티커 순서를 바꾸면 MDD도 달라질 수 있다. 날짜별 포트폴리오 NAV로 다시 계산한다. |
| H6 | High | `compare_and_promote` 게이트는 55%/2.0, PROJECT_STATUS는 50%/1.30. 검정·최소 표본수 게이트가 없다. 기본 250봉 중 초기 약 120봉은 준비 구간이다. | '1년 표본외 walk'나 '통계적 우위'를 코드에서 확인할 수 없다. 250봉 기본 실행은 대략 나머지 반년의 신호만 평가한다. 사전 고정된 OOS 절차와 기준이 필요하다. |
| H7 | High | 백테스터 E는 다음 일봉 시가 (`:114`), 추적기 E는 이미 관측한 종가 (`services/trade_tracker.py:213`). 주문·실제 수량·브로커 체결 확인 필드는 검토 경로에 없다. | 현재 구조는 신호/가상 포지션 추적이다. 일봉 고가 터치만으로 '익절 완료'를 사실로 표시할 수 없다. `SIGNALLED / ORDER_PENDING / FILLED`와 모의/실제 원장을 구분한다. |
| H8 | High | `services/scheduler.py:171`은 07:30 실행. 문서 cron은 07:30 일봉 수집, 07:32 매크로, 07:45 `--briefing` 실행이다. | 문서 구성대로라면 수집 완료 전 계산 및 하루 이중 상태 갱신 경로가 있다. GCP에서 실제 동시 활성화 여부는 미검증. 단일 작업 소유자·DB 잠금·수집 완료 watermark를 도입한다. |
| H9 | High | `tests/test_trade_tracker.py:37`은 DB DELETE를 수행하고 테스트 중 update 함수는 모든 OPEN 행을 읽는다. `tests/test_backtester.py`의 검증은 `run_test()`이며 unittest TestCase가 아니다. | 기본 discovery 통과만으로 백테스트 게이트가 검증되지 않는다. 운영 DB를 사용하는 테스트는 일반 포지션까지 변경할 수 있다. 전용 테스트 DB와 범위 제한이 필수다. |
| H10 | High | 검사한 진입 경로에는 요청서의 거래량 50만 주 필터, ADV·스프레드·자금·섹터 한도가 없다. | 유니버스 구성 단계에서 과거에 적용했을 가능성은 있으나 실행 시 보장은 없다. 거래 적격성과 주문 용량 제약을 별도 적용한다. |
| M1 | Medium | 가중치 합은 115, 실제 점수는 `min(100, raw)` (`engine/wyckoff_scanner.py:253`). | 100~115가 같은 점수가 된다. 버전 호환성을 유지하며 `raw_score`, `display_score`, 구성요소를 별도 저장한다. 단순히 115로 나눠 정규화하면 68~78의 의미가 바뀐다. |
| M2 | Medium | POC는 각 봉 전체 거래량을 typical price의 한 bin에 배치 (`engine/indicators.py:196`). 0 거래량이면 종가 POC로 대체. | 실제 가격별 체결 분포가 아니라 OHLCV 근사치다. 시간봉 활용 시에도 한계가 남는다. 데이터 품질 실패를 지지 확인으로 해석하지 않아야 한다. |
| M3 | Medium | MFI의 total_flow=0을 50으로 만든 뒤 neg_flow=0 조건이 다시 100으로 덮는다 (`engine/indicators.py:109`). | 돈의 흐름이 없는 구간이 최대 MFI가 된다. 조건 순서를 수정하고 0/NaN 거래량을 별도 차단한다. 단독으로 MFI 가산점 발생을 보장한다는 뜻은 아니다. |
| M4 | Medium | 시간봉은 KST 변환 후 timezone 없는 문자열 저장 (`collectors/market_collector.py:174,188`); 일봉은 날짜 00:00 저장. 완성 여부 필드가 없다. | 날짜만으로 조인하면 미국 session과 한국 날짜를 혼동하고 미완성 봉을 소비할 수 있다. UTC bar_start/bar_end, NY session_date, available_at을 저장한다. |
| M5 | Medium | `engine/backtester.py:15`는 DB에 직접 의존한다. schema의 OPEN unique는 `(ticker, strategy_type)` 단위다. | 명시된 순수 engine 계층 및 '종목당 하나' 요구와 차이가 있다. DB orchestration을 services로 이동하고 중복 정책을 명확히 한다. |
| M6 | Medium | `scan_snapshots`는 일자·종목·전략 upsert이고, reconfirmed_count는 호출 횟수다. | 하루 여러 관측 기록을 보존하지 않으며 재확인 일수와도 다르다. 최종 일봉 스냅샷과 append-only 실행 이벤트를 분리한다. |
| L1 | Low | ARCHITECTURE에는 구형 `scan_signals` 및 미정의 diagram 노드가 남아 있다. PROJECT_STATUS의 `evaluate_strategy_gate()` 함수는 현재 소스에 없다. | 신규 운영자가 재현 가능한 절차를 얻기 어렵다. 실제 entrypoint·스키마와 문서를 함께 버전 관리한다. |

### 1.2 68~78점은 수학적으로 최적화가 보장된 LPS 구간인가?

**아니다. 현재는 프로젝트가 정한 휴리스틱 분류다.** 이를 보존하되 '기관 매집 완료', '손절 2~3%', '최적 R:R'는 데이터로 별도 검증해야 한다.

현재 점수 증가분의 최대공약수는 5다. 따라서 실제 엔진의 68~78 구간에는 **70점과 75점만** 있고, 79~84에는 **80점만** 있다. 테스트 fixture의 72·76·78점은 모델이 반환할 수 있는 점수라는 증거가 아니다. 68→69 또는 78→79 변경에서 신호가 같다고 해도 강건성 증거가 되지 않는다. 실제 바뀌는 65/70/75/80/85의 경계와 신호 집합을 비교해야 한다.

기초 60점에 RSI 10점 또는 MFI/OBV 중 하나 15점을 더하면 각각 70·75다. '1~2개 수급 확인'이라는 설명에서 RSI+MFI는 이미 85점이다. 또한 75점이라는 합만으로 기초 세 조건을 모두 충족했다는 사실은 역추론할 수 없다. 각 구성요소를 따로 검사해야 한다.

현재 가격이 박스 저점에서 얼마나 떨어져 있는지와 손절 거리를 직접 측정하자.

\[
z_{box}=\frac{E-B}{H-B},\qquad
d_{SL}=1-\frac{0.975B}{E},\qquad RR_1=\frac{0.20}{d_{SL}}.
\]

5성의 박스 진폭 18%만으로는 E가 B 근처라는 보장이 없다. E=1.18B라면 \(d_{SL}=17.37\%\), \(RR_1\approx1.15\)다. POC가 박스 상단 부근이면 POC 지지도 함께 통과할 수 있다. 반대로 E=B에 가깝다면 고득점이어도 손절 거리가 약 2.5%일 수 있다. 따라서 85점 이상 경고는 유지하되 '이미 10~25% 상승했다'는 문구를 관측값 없이 단정하지 않는다.

POC 허용 범위 역시 문자 설명의 0~7%와 실제 \([-1.5\%,7\%]\)가 다르다. 7%를 넘으면 스캐너는 25점을 주지 않을 뿐 필수 탈락으로 만들지 않는다. MA20 상단 기울기 초과도 가점 미부여다. 이를 모두 hard filter라고 설명한 문서와 실제 정책을 구분해야 한다.

### 1.3 상단 공간과 시간 정렬

요청서는 \(P<CloudTop\)에서 CloudTop까지 거리, PROJECT_STATUS와 코드는 구름 하단 아래에서 \(\min(CloudBottom,MA60)\)까지 거리를 사용한다. 이들은 다른 전략이다. 현재 코드에서는 MA60이 가격 아래라도 min에 포함되어 음수 공간으로 거절하며, **구름 내부 가격도 하단 이상이라는 이유로 예외 처리**된다.

명확한 후보 정책은 다음과 같다. 구름 아래에서는 실제 가격 위의 저항만 선택하고, 구름 내부는 보수적으로 신규 진입 불허, 구름 위는 cloud gate 예외로 하되 다른 위험 검사는 유지한다. 이 수정도 챌린저로 검증해야 한다.

\[
\mathcal R_t=\{CloudBottom_t,MA60_t\}\cap(E_t,\infty),\qquad
R_t^*=\min\mathcal R_t.
\]

집합이 비어 있으면 `no_known_resistance`로 기록한다. cloud가 NaN인 경우와 구분하여, 필요한 데이터가 없으면 자격을 보류한다. `shift(+26)`으로 현재 위치의 구름을 계산하는 기존 구현은 과거 값 참조이며, 그 자체를 미래 참조 결함으로 볼 근거는 없다. 미래로 이동한 도식의 좌표와 정보가 알려진 시점을 혼동하지 않아야 한다.

### 1.4 실제 실행한 검증

[오프라인 재현 스크립트](audits/2026-09-08/reproduce_findings.py)는 DB 연결을 차단하고 합성 OHLCV·mock cursor로 **현재 결함을 재현하는 10개 assertion**을 실행했다. 모두 재현 성공했다. 이는 정상 동작 테스트 10개 통과를 뜻하지 않는다.

| 재현 | 관측 결과 |
|---|---|
| 낙폭 미달·75점 | strict 모드 1성, 운영과 같은 diagnostic 모드 5성 |
| overhead=True·공간 2% | strict 모드 None, diagnostic 모드 5성 |
| 백테스트 TP1 | 1일차 +20% 전량 청산 |
| 마지막 봉의 갭 손절 | 거래 목록에 기록되지 않음 |
| TP1 이후 하루 전체 가격 88~92 | SL 100.5 체결, 종합 +10.25% 기록 |
| TP1 미기록 상태에서 TP2 도달 | +50% 기록; 고정 목표 분할 모델이면 +35% |
| TP1 이후 41일차, high 125 | OPEN 유지 |
| 동일 session 두 번 실행 | 보유일 2→3 |
| `auto_promote=False` | 저장 객체에 `is_champion=True` |
| 자금 유입/유출 모두 0 | MFI=100 |

필수 명령 **`python -m unittest discover tests`**도 실행했다. 결과는 **14개 발견, 11개 통과, DB 의존 3개 오류**다. 테스트의 운영 데이터 갱신을 피하기 위해 해당 프로세스에 `DB_HOST=127.0.0.1`, `DB_PORT=1`, 전용 감사 DB 이름을 지정했다. 사용 가능한 별도 PostgreSQL fixture를 설정하지 않았으므로 이 3개는 환경 제한이며, 제품 로직 실패로 세지 않았다. 로그: [unittest_discover.log](audits/2026-09-08/unittest_discover.log). 기존 문서의 '7/7 suites'는 이번 결과와 다른 집계이며 재확인되지 않았다.

## 2. Direct Mathematical Formulations & Improvements

### 2.1 Pillar 1 — 통계적 강건성과 과최적화

**현재 성과의 산술적 해석.** PF는 평균 이익/평균 손실 비율과 다르다. 동일한 거래 표본·동일한 금액 가중, 손익 0 거래 영향이 미미하다고 가정하면:

\[
p=0.5075,\quad PF=\frac{p\bar W}{(1-p)\bar L}=1.40,
\quad b=\frac{\bar W}{\bar L}=PF\frac{1-p}{p}\approx1.3586.
\]

\[
\bar L=\frac{0.0158}{(PF-1)(1-p)}\approx8.02\%,
\qquad \bar W=b\bar L\approx10.90\%.
\]

이는 **반올림된 주장 수치의 역산**이며 실제 평균 손익을 재측정한 결과가 아니다. 실제 분포·수수료 포함 여부·가중 기준을 원장으로 확인해야 한다. 스캐너의 목표/손절 비율 3~5와 실현 payoff ratio 약 1.36은 다른 값이다.

597개가 독립 Bernoulli라고 가정한 승률의 95% Wilson 구간은 약 **46.75~54.74%**다. 승률 50% 초과가 확정되지 않지만, payoff가 비대칭이므로 이것만으로 기대값 0을 결론 낼 수도 없다. 기대값 CI는 실제 수익 분포가 필요하다.

동일 날짜·섹터의 거래는 독립이 아니다. 동일 크기 m 군집 내 상관 \(\rho\)를 가정한 예시 유효 표본수는:

\[
N_{eff}\approx\frac{N}{1+(m-1)\rho}.
\]

m=10, rho=0.5라면 약 109개다. 이는 실제 추정치가 아닌 군집 의존성의 예시다. 거래를 독립적으로 bootstrap하지 말고 **모든 티커를 함께 묶은 날짜 블록**으로 일별 포트폴리오 수익을 재표집한다. 20/40/60 거래일 블록 길이 민감도를 보고, 거래 단위 평균도 신호일 군집을 보존해 재계산한다.

**검증 프로토콜 제안:**

1. 현 소스와 명세를 일치시킨 `corrected_baseline`을 먼저 만든다. 기존 결과와 달라진 것은 버그 수정 영향으로 기록한다.
2. 역사적 시점별 유니버스·상장폐지·상장일·기업행동·배당·정보 available_at을 고정한다. 현재 활성 303개로 과거 전체를 평가하면 생존자/선택 편향 위험이 있다. 수집기의 `auto_adjust=False`만으로 split 처리 유무를 단정하지 말고 공급자 의미와 실제 split 사례를 대조한다.
3. 가능한 경우 24개월 학습 / 3개월 내부 검증 / 다음 3개월 OOS, 3개월씩 이동한다. **신호 생성용 최소 120봉 준비 기간을 성과 측정 기간과 별도로 확보**한다. 현재 문서의 약 1년만으로 이 검증은 불가능하므로 과거 데이터가 더 필요하다.
4. 라벨 구간 \([entry,exit]\)이 검증 구간과 겹치는 학습 샘플을 제거한다. 40거래일 최대 보유 정책을 기준으로 경계 완충을 잡고, 양방향 CV라면 검증 이후 embargo도 둔다. strictly forward 학습에는 이미 미래 자료를 넣지 않는다. 120일 과거 지표 공유 자체는 미래 누설이 아니다.
5. 최종 6~12개월 holdout은 모든 선택이 끝난 후 한 번 평가한다. 결과를 보고 다시 튜닝하면 새 표본외 구간 또는 forward shadow 기간이 필요하다.
6. 실험 장부에 실패 모델·실행 정책·타임프레임·후보값까지 기록한다. 총 실험 수와 전체 후보 성과 행렬 없이 '선택 편향 보정'을 주장하지 않는다. PBO/CSCV는 추가 진단으로 사용하고 시간순 OOS를 대체하지 않는다. 반복 선택의 위험과 PBO 접근은 [Bailey 외, The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)에 근거한다.
7. 날짜 블록별 \(\Delta R_d=R_{challenger,d}-R_{baseline,d}\)의 평균 CI, net PF, MDD, turnover, 노출·집중·용량을 함께 보고한다. 독립 거래수 50개 또는 승률 55%만으로 승격하지 않는다.

**점수 밴드의 동적 사용:** 5성 정의 68~78과 85+ 경고는 그대로 유지한다. 점수를 VIX로 더하거나 섹터 점수를 합산하지 않는다. 대신 5성 내부에서 비용 차감 조건부 기대값과 실행 자격을 달리한다.

\[
a_t=ATR_{14,t}/C_t,\quad
u_t=\widehat F_{\{a_{t-252},\ldots,a_{t-1}\}}(a_t),\quad
v_t=\widehat F_{\{VIX_{t-252},\ldots,VIX_{t-1}\}}(VIX_t).
\]

VIX도 의사결정 당시 확정된 관측만 쓴다. 분위수는 최근 252거래일 이전 자료로 계산하고 학습·평가에서 동일한 시점 규칙을 적용한다.

\[
x_t=(1_{S=70},1_{S=75},u_t,v_t,z_{box},d_{SL},
(C_t-POC_t)/ATR_t,\text{liquidity}),
\]
\[
\widehat\mu_t=\widehat p(x_t)\widehat W(x_t)
-(1-\widehat p(x_t))\widehat L(x_t)-\widehat c_t,
\quad eligible=G_t\land star5_t\land
\operatorname{LCB}_{95\%}(\widehat\mu_t)>0.
\]

G는 모든 필수·데이터·위험 조건이다. 소표본에서는 복잡한 모델보다 2~3개 volatility regime의 shrinkage 추정부터 시작한다. 위 CI 자체도 군집 의존성과 모델 선택을 반영해야 한다. 표본이 부족하면 학습된 동적 밴드라는 이름을 붙이지 않고 기존 자격과 보수적 사이징으로 남긴다. 점수 구간 자체를 이동시키는 모델은 현재 불변조건과 다르므로 본 제안에서 채택하지 않는다.

**고정값의 취약성:** SL 2.5%는 거래 한 건의 손실과 빈도에 직접 작용하고, overhead 5%는 ATR/위험 대비 의미가 크게 변한다. 25% 낙폭은 변동성이 큰 종목을 구조적으로 더 선택하며 20% 박스는 sector·regime에 따라 지나치게 느슨하거나 엄격해진다. 이는 기계적 민감도 예상 순서이지 데이터에서 측정한 순위는 아니다.

별도 챌린저용 ATR 정규화 후보는 다음과 같다. 여기서 모든 비율은 소수 단위이며 `clip(x,l,h)=min(max(x,l),h)`다.

\[
D_{min,t}=clip(k_D a_t,0.15,0.40),\quad
BoxMax_t=clip(k_B a_t,0.12,0.25),
\]
\[
SL_D=B_t-k_S ATR_{D,t},\qquad
R_t^*-E\ge\max(k_O ATR_{D,t},\ k_R(E-SL_D)).
\]

앵커 후보는 \(k_D=8, k_B=6.7, k_S=0.5, k_O=2, k_R=1\). ATR/가격=3%라면 낙폭 약24%, 박스 약20.1%로 현 정책 근처다. MA 기울기는 \((MA20_t-MA20_{t-10})/ATR_t\)도 병렬 기록한다. 변동성 급등이 자동으로 넓은 박스를 허용하게 되는 부작용이 있으므로 ATR 상위 분위에서는 gross 축소/거래 보류와 함께 평가해야 한다. 기존 hard threshold를 이 값으로 즉시 교체하지 않는다.

### 2.2 Pillar 2 — Daily + 1H 역할 분리

**일봉은 전략 자격, 시간봉은 주문 타이밍과 무효화 가격**을 담당한다. 시간봉 가산점을 일봉 점수에 합치지 않는다. 검증할 기본 가설은 '추가 확인의 대기 비용과 놓친 상승을 감수해도, 체결·손절 품질이 개선되는가'다. 시간봉을 쓰면 슬리피지나 whipsaw가 반드시 감소한다고 전제하지 않는다.

일봉 마감 t에 확정한 후보와 \(POC_D,B_D,ATR_D,S_D\)를 보존한다. 다음 session부터 최대 3개 거래일 동안 시간봉 트리거를 기다린다. 일봉을 재평가하려면 매일 마감 시 **새 버전의 후보 이벤트**로 갱신하고 이전 주문과 연결한다.

시간봉 h 이전 완성 20봉의 저점 \(B_H=\min L\), 직전 ATR14 \(A_H\) 및 10 session POC_H를 고정한다. 10일을 무조건 70개 동등한 봉으로 간주하지 말고 session과 실제 거래시간으로 선정한다. 마지막 단축 봉과 조기 폐장 봉은 정상 완성 봉이며 미완성 진행 봉과 다르다.

Spring 후보 s:

\[
0.1A_H\le B_H-L_s\le0.5A_H,\quad C_s>B_H,
\quad |C_s-POC_D|\le1.0ATR_D.
\]

Secondary Test j는 s 이후 1~6개 완성 봉 안에서:

\[
L_j\ge L_s,\quad |L_j-B_H|\le0.5A_H,\quad C_j\ge B_H,
\quad RVOL_j\le0.8RVOL_s.
\]

\(RVOL_h=(V_h/\text{실제 거래분})/\operatorname{median}(\text{과거 20 session 동일 시간대 분당 거래량})\)으로 장초/장말 계절성과 짧은 봉 길이를 보정한다. ST 확정 다음부터 \(H_j+tick\) buy-stop-limit 주문을 활성화하고, 2봉 미체결 시 취소한다. 매수 limit는 trigger+0.1A_H를 초기 후보로 둔다. 갭으로 limit를 넘으면 주문 미체결이다. OHLCV만으로 limit 체결 대기열을 확정할 수 없으므로 보수적 체결 모델·미체결률을 함께 보고한다.

초기 손절 후보:

\[
SL_H=\min(L_s,L_j)-0.25A_H,
\quad d_{struct}=(E-SL_H)/E.
\]

진입 후 SL_H를 먼저 적용하는 것은 '일봉 논리가 무효화되기 전에 시간봉 실행 가설이 실패해 청산한다'는 새 정책이다. 손절 후 임의로 SL_D까지 넓히지 않는다. 초기 ATR 노이즈 하한 \(E-SL_H\ge1.5A_H\), 최대 위험 8%, 아래 갭 위험 사이징을 함께 검사한다. 지나치게 좁으면 가격을 억지로 조절하기보다 후보를 보류한다. 일봉 SL 유지형과 시간봉 SL형을 별개 실험으로 비교한다.

POC_H는 실제 volume-at-price가 있으면 이를 우선 사용한다. OHLCV만 있다면 각 봉 범위와 bin의 겹치는 길이 비례로 거래량을 분산하는 대안:

\[
V_b=\sum_h V_h\frac{|[L_h,H_h]\cap bin_b|}{H_h-L_h}.
\]

H=L인 봉은 해당 가격 bin에 전량 배치한다. 이 역시 균일 체결 가정이지 실제 체결 복원은 아니다. 기존 typical-price 방식과 bin 20/40/80, 5/10/20 session 민감도를 별도 비교하고 POC_H reclaim을 선택적 확인으로만 추가한다. 거래량 배분·POC·지표는 미래 봉을 추가해도 과거 결과가 변하지 않는 prefix 검사를 통과해야 한다.

### 2.3 Pillar 3 — 갭, 부분익절, 체결 비용

**'Free-Ride'의 정확한 손익:** 최초 수량 기준 50%를 1.2E에 팔고 잔여를 F에 팔았다면 비용 전 수익률은:

\[
r_{total}=0.5(1.2-1)+0.5(F/E-1)=0.1+0.5(F/E-1).
\]

F=1.005E일 때 +10.25%, F=0.90E이면 +5%, F=0.80E이면 0%, F=0이면 **-40%**다. '갭 -10%'는 기준 가격을 명시해야 한다. SL=1.005E에서 10% 갭이면 F=0.9045E, 종합 +5.225%이며 원금 손실이 반드시 생기는 것은 아니다. 다만 잔여 물량은 여전히 위험하고 현재 평가자산 대비 낙폭도 발생한다.

TP1 50% 매도 대금은 최초 원금의 60%일 뿐이다. 잔여가 0이 되어도 최초 원금을 회수하려면 비용 전 최소 \(\alpha\ge1/1.2=83.33\%\)를 그 가격에 팔아야 한다. 이는 현재 50% 전략을 바꾸자는 권고가 아니라 '100% 원금 보호' 설명의 수학적 반례다.

SL은 체결 보장이 아니라 주문 trigger다. Stop-limit는 가격을 제한하지만 미체결 위험을 남긴다. [SEC/Investor.gov의 Stop Orders 설명](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-15)이 이 구분을 명시한다. 이 시스템이 실제 주문을 내지 않고 알림만 보낸다면 사용자의 반응 지연까지 있어 가상의 stop-market 체결도 그대로 실적이라 부를 수 없다.

**재현 가능한 체결 정책:**

\[
F_{sell,stop}=\begin{cases}
O_h(1-s_h),&O_h\le SL_{h^-},\\
SL_{h^-}(1-s_h),&O_h>SL_{h^-},\ L_h\le SL_{h^-}.
\end{cases}
\]

이는 거래가 가능한 시장의 단순 모형이다. 거래정지·유동성 부재에서는 fill을 보류하고 다음 실행 가능 이벤트를 기다린다. TP는 사전에 제출된 실제 limit 주문인지, 봉 마감 후 알림인지에 맞춰 모델링한다. 목표를 갭 상향 돌파한 시가의 가격 개선도 주문 종류와 증거 없이 자동 인정하지 않는다.

TP1 신규 도달 봉에서 SL을 올린 뒤 그 봉 전체 low에 소급 적용하면 시간 순서 오류다. 분봉/체결 timestamp로 순서를 확인하거나 OHLC→OLHC의 두 경로를 평가해 비관/낙관 범위를 보고한다. 같은 봉에서 기존 SL과 TP가 모두 닿은 경우도 open에서 먼저 확정된 이벤트를 처리한 후 잔여 경로 불확실성을 판단한다. 단순 '무조건 SL 우선'은 보수적 참고 모델이지 모든 실제 경로의 복원은 아니다.

**갭 손실 모형과 사이징:** 의사결정 시점까지의 \(G_i=O_i/C_{i-1}-1\)를 이용하되, 기업행동을 가격 급락과 구분한다. 예시 손절 돌파 손실 함수는 \(X_i=\max(d_{SL,i},-G_i)+c_i\). 이는 내일 open의 하락률과 현재 기준 손절 거리 중 큰 쪽을 반영하는 스트레스 근사이며 실제 거래 상태별 cash-flow simulation과 함께 사용한다.

\[
ES_q(X)=E[X\mid X\text{가 상위 }(1-q)\text{ 손실 꼬리}],
\quad d_{eff,i}=\max(d_{SL,i}+\hat c_i, ES_{97.5\%}(X_i)).
\]

252일의 97.5% 꼬리는 약 6개 관측에 불과하다. 종목 자체 꼬리만 믿지 말고 과거 수년·동종 유동성군의 shrinkage·동일 날짜 공동 gap scenario를 사용한다. earnings/비실적, 고변동/저변동 상태를 나눠도 표본 부족이 더 커진다는 점을 기록한다. -10/-20/-40% gap, 거래정지 후 재개를 추가 스트레스로 검사한다. 이는 확률 추정치가 아닌 스트레스 시나리오다.

TP1 이후에도 잔여 수량을 현재가로 평가한 \(w^{rem}_{i,t}=Q^{rem}_{i,t}P_{i,t}/NAV_t\)로 포트폴리오 위험을 다시 계산한다. 과거 실현익으로 해당 종목의 현재 gap 위험을 0으로 만들지 않는다.

**비용과 용량:** 50만 주 거래량만으로 유동성 적합성을 판단할 수 없다. 가격 $2와 $200은 거래대금이 100배 차이 난다. 스프레드는 quote 데이터로 측정해야 하며 OHLC 범위를 bid-ask spread로 치환하지 않는다.

\[
ADV^{\$}_{20}=\frac1{20}\sum_{d=t-19}^{t} C_dV_d,\quad
c_{leg}\approx\frac{spread}{2mid}+fee+Y\sigma_D\sqrt{Q/ADV^{shares}_{20}}.
\]

square-root impact는 연구 문헌의 경험적 출발점이며 모든 주문·시장에 고정 계수로 성립하는 법칙으로 쓰지 않는다. 특히 종가 대비 peak impact와 평균 fill cost의 계수는 다르다. Y는 실제 implementation shortfall로 보정해야 한다. [Said, Market Impact: Empirical Evidence, Theory and Practice](https://arxiv.org/abs/2205.07385).

진입 Q0와 청산 분율 qj, 각 fill Fj/E 기준으로 최초 notional 대비 정확히 집계하면:

\[
r_{net}=\sum_jq_j(F_j/E-1)-c_{entry}
-\sum_jq_j(F_j/E)c_{exit,j},\qquad\sum_jq_j=1.
\]

수수료를 금액으로 원장에 저장하면 이 근사의 단위 혼동을 피할 수 있다. 이미 fill에 반영된 spread·impact를 다시 빼지 않는다. entry 100%, TP1 50%, 최종 50%의 세 주문을 모두 전량 비용으로 계산하지도 않는다.

| 최초 notional 대비 총 왕복 비용 가정 | 주장된 gross +1.58%에서 단순 차감 |
|---|---|
| 10 bps | +1.48% |
| 30 bps | +1.28% |
| 60 bps | +0.98% |
| 100 bps | +0.58% |
| 158 bps | 0.00% |

표는 주문 규모·가격 경로·미체결·선택 효과가 고정된 산술 예시이며 **수정 전략의 net 실적 예측이 아니다**. 버그 수정과 체결 정책 변경은 거래 집합과 gross 수익 자체를 바꾼다.

### 2.4 Pillar 4 — 상관관계와 자본 배분

섹터·상관관계 정보는 **후보 선택·포트폴리오 비중 제약에만** 사용한다. stock score는 그대로 둔다. ETF 거래대금 점유율은 방향 없는 거래 활동 비중이며 순자금 유입과 동치가 아니다. 실제 순유입은 primary-market creation/redemption 등 다른 자료가 필요하다. 현재 지표는 'ETF 거래 활동 점유율'로 해석하는 편이 정확하다. ETF 2차 거래와 설정·환매의 차이는 [ICI ETF 구조 설명](https://www.ici.org/faqs/faqs_etfs)에 근거한다. SOXX/SMH처럼 기초 보유 종목이 겹치는 ETF를 독립 섹터로 중복 집계하지 않는다.

동일 변동성·동일 비중 n종목의 평균 상관이 rho라면:

\[
Var(R_p)=\sigma^2\left(\rho+\frac{1-\rho}{n}\right),\quad
n_{div}=\frac{n}{1+(n-1)\rho}.
\]

n=15, rho=0.7이면 독립 자산 약 1.39개 수준이다. 종목 수만 늘려서는 sector rotation 동시 손실을 제어할 수 없다.

60/120일의 정렬된 일별 수익으로 covariance를 추정하고 수축한다:

\[
\widehat\Sigma=(1-\delta)\Sigma_{sample}+\delta F,
\quad0\le\delta\le1.
\]

F는 양의 준정부호 factor/constant-correlation target으로 구성한다. 데이터 부족의 raw pairwise covariance나 임의 상관값 덮어쓰기로 비정부호 행렬을 만들지 않는다. 수축 접근의 근거는 [Ledoit·Wolf의 covariance 연구](https://www.ledoit.net/)이며, 아래 60/120일과 제약값은 이 프로젝트의 검증 후보다. 위기 구간의 공동 수익 scenario도 별도로 평가한다.

**권고 사이징은 gap-aware 위험 예산 + 역변동성 배분 + 집중도 제약**이다. 처음부터 추정 Kelly로 확대하지 않는다. NAV=A, 거래별 자본 손실 예산 r, gross 목표 G라면:

\[
w_{risk,i}=\frac{r_i}{d_{eff,i}},\quad
w_{vol,i}=G\frac{1/\sigma_i}{\sum_j1/\sigma_j},\quad
w_i^{(0)}=\min(w_{risk,i},w_{vol,i},w_{name,max}).
\]

기존 보유와 미체결 예약 주문을 합쳐 다음 제약을 검사한다:

\[
0\le w_i\le0.05,\quad \sum_iw_i\le1.0,\quad
\sum_{i\in sector\ s}w_i\le0.20,\quad
\sqrt{252w^T\widehat\Sigma w}\le0.12.
\]

rolling corr>0.75의 종목 연결로 만든 cluster에도 20% 상한을 둔다. 종목이 반도체·AI·데이터센터처럼 여러 factor에 노출되면 공식 subsector 한 개만으로 제한하지 않는다. 신규 주문으로 제약을 초과하면 크기를 줄이거나 거절하고, 기존 포지션이 이미 제약을 초과하면 별도의 감축 정책을 적용한다.

\[
RC_s=\frac{\sum_{i\in s}w_i(\widehat\Sigma w)_i}{w^T\widehat\Sigma w},
\quad RC_s\le0.30\ \text{(검증 후보)},\qquad
\sum_iw_i d_{eff,i}\le0.02.
\]

RC는 해당 모델의 분산 기여도이며 현금 비중이나 gap ES와 같은 값이 아니다. 독립 종목 ES의 합은 공동 꼬리손실을 복원하지 않으므로 추가로 날짜가 같은 gap scenario의 포트폴리오 손실 \(L_k(w)\)에 대해:

\[
\min_z\left[z+\frac1{(1-q)K}\sum_{k=1}^K\max(L_k(w)-z,0)\right]\le L_{budget}
\]

를 검사한다. q=97.5%, 최초 L_budget=2% NAV를 후보로 둔다. 표본·분포가 부족하면 이것을 통계적 보장으로 해석하지 않는다.

수량은 \(Q_i=\lfloor A w_i/E_i\rfloor\)이며 ADV·시간봉 예상 용량·현금 여유를 다시 검사한다. 예컨대 A=$100,000, r=0.25%, 구조적 stop=4%, gap 포함 d_eff=8%이면 risk 기준 notional은 $3,125다. gap을 빼고 4%만 사용한 $6,250보다 작다. TP1 이후 가용 현금 증가와 잔여 위험을 원장에 각각 반영한다.

Kelly를 진단으로만 계산하면 이항 위험 단위에서:

\[
f^*_{risk}=p-\frac{1-p}{b}\approx14.5\%.
\]

이는 '계좌의 14.5%를 주식에 투자'라는 뜻이 아니다. 고정 손실 8.02%의 단순 이항 모형으로 바꾸면 notional Kelly는 약 181%가 되어 오히려 잘못된 공격적 권고를 만들 수 있다. 현재의 다중 청산·갭·상관·불확실한 기대값은 그 이항 가정을 만족하지 않는다. fractional Kelly는 OOS 순수익 분포가 확보된 뒤 \(\max_f E[\log(1+fR)]\)를 비용·drawdown 제약으로 추정해 **이미 정한 위험 상한 아래에서만** 비교한다. Kelly와 drawdown 제약의 구분은 [Busseti·Ryu·Boyd, Risk-Constrained Kelly Gambling](https://web.stanford.edu/~boyd/papers/kelly.html)을 참고한다.

Cornish–Fisher VaR는 597개의 상관된 거래에서 skew/kurtosis를 안정적으로 추정하기 어렵고 손절 불연속·거래정지의 꼬리 근사도 약하다. 초기 sizing의 기준으로 삼지 않고, historical/stress ES를 우선 비교한다.

## 3. Multi-Timeframe Algorithmic Blueprint

### 3.1 계층 및 데이터 계약

```text
collectors: daily/1H/quote/calendar/corporate-action 데이터와 available_at 수집
core.models: Candle, SetupAssessment, Candidate, OrderIntent, Fill, TradeState
engine: score + eligibility / hourly trigger / fill-driven lifecycle / risk 계산
services: 데이터 as-of 조회, 작업 잠금, 저장 트랜잭션, 실행/모의 adapter, 알림
```

신규 모델은 `SetupAssessment(score, stars, entry_eligible, failed_gates,
signal_session, signal_time, data_watermark, strategy_version, params_hash)`를 반환한다. 필터 탈락이어도 진단 점수는 남기되 **score/stars만으로 주문을 생성하지 않는다**. 기존 champion의 68~78·85+ 및 섹터 점수 분리 원칙은 유지한다.

`TradeState`에 최초/잔여 수량, 평균 실제 진입가, 누적 실현 현금손익, 수수료, TP1 체결 수량, 현재 SL, 최초 entry_session, 최대 session 수, last_processed_event/version을 둔다. TP1 터치와 TP1 50% 체결 완료는 다른 이벤트다. stop 수정 및 40일 연장은 지정된 50% 체결이 확인되었을 때 적용한다. partial fill 중의 잔여 주문·보호 stop 수량도 실제 보유 수량과 일치시킨다.

일자별 수익은 \(NAV_d=Cash_d+\sum_iQ_{i,d}P_{i,d}\)로 계산한다. TP1 현금 유입은 해당 일자에 포함하고, 잔여 평가손익·배당·기업행동·비용을 함께 반영한다. trade return과 portfolio return을 따로 보고한다.

### 3.2 이벤트 순서를 고정한 의사코드

아래는 구현 계약을 설명하는 Python형 의사코드다. `calendar`, `repository`, `execution_model` 등의 adapter는 아직 저장소에 구현되어 있지 않다. 그대로 실행 가능한 코드라고 주장하지 않는다.

```python
def on_daily_finalized(session, available_at):
    with repository.session_lock("daily", session, strategy.version):
        if repository.processed("daily", session, strategy.version):
            return
        data = repository.daily_history_ending(session, available_at=available_at)
        require_complete_session_and_corporate_action_consistency(data)
        for ticker, bars in data.items():
            assessment = assess_daily(bars, params=strategy.frozen_params)
            repository.save_assessment(assessment)  # 탈락 이유 포함
            if not assessment.entry_eligible:
                cancel_pending_candidate(ticker, reason="daily_invalid")
                continue
            if assessment.stars != 5:  # MTF challenger의 사전 고정 범위
                continue
            if portfolio.has_position_or_reserved_order(ticker):
                continue
            repository.arm_candidate(
                assessment, expires_session=calendar.add_sessions(session, 3),
                active_after=available_at,  # 다음 사용 가능한 실제 시장 이벤트
            )
        repository.mark_processed("daily", session, strategy.version)


def on_closed_hour(bar, received_at):
    assert bar.finalized and bar.available_at <= received_at
    # bar.start/end는 UTC, session은 거래소 달력 기준; 단축 봉도 허용
    for candidate in repository.active_candidates(bar.ticker):
        if bar.start < candidate.active_after:
            continue
        if not data_is_fresh_and_aligned(bar, candidate):
            suspend(candidate)
            continue
        if candidate.expired(bar.session) or daily_thesis_broken(candidate, bar):
            cancel(candidate)
            continue
        prefix = repository.hourly_prefix(bar.ticker, through=bar.end,
                                         available_at=received_at)
        if candidate.state == "ARMED":
            detect_and_freeze_spring(candidate, prefix)  # 2.2의 Spring 수식
        elif candidate.state == "SPRING":
            st = detect_secondary_test(candidate, prefix)  # 이후 1~6봉
            if st:
                intent = make_stop_limit_intent(
                    buy_stop=st.high + tick_size,
                    buy_limit=st.high + tick_size + 0.1 * candidate.hourly_atr,
                    protective_stop=min(candidate.spring_low, st.low)
                                    - 0.25 * candidate.hourly_atr,
                    active_after=received_at, expiry_bars=2,
                )
                # 현재까지의 정보로 용량 예측. 미래 봉의 open/volume 사용 금지.
                approved = risk.reserve(intent, portfolio, quotes_before(received_at))
                if approved:
                    repository.save_order_intent_and_outbox(approved)


def on_market_event(event):
    # 기존 주문만 체결 가능. 이 봉 마감에 만든 주문을 이 봉 고가로 체결하지 않음.
    fills = execution_model.match_active_orders(event)
    for fill in fills:
        with repository.trade_lock(fill.trade_id):
            if repository.seen_fill(fill.unique_id):
                continue
            state = repository.load_trade(fill.trade_id)
            next_state, intents = engine.apply_fill(state, fill)
            # 첫 진입 실제 평균 fill 기준 TP1=1.2E, TP2=1.5E.
            # TP1 누적 50% 체결 확인 후 stop=max(old_stop,1.005E), max_sessions=40.
            # 수수료/실현 현금/잔여량은 각 fill마다 정확히 갱신.
            repository.atomic_save(next_state, fill, intents, notification_outbox=True)


def on_session_close(session, close_event):
    for trade in portfolio.open_trades():
        # 단순 함수 호출 횟수가 아닌 고유한 거래소 session으로 계산.
        age = calendar.sessions_in_position(trade.entry_session, session)
        if age >= trade.max_sessions and trade.remaining_qty > 0:
            # TP1 가격 위에 있어도 만료를 독립 검사.
            # 종가 체결을 가정하려면 사전에 제출된 MOC 및 주문 마감시간을 준수.
            # 종가 이후에야 결정했다면 다음 실행 가능 이벤트로 넘김.
            schedule_timeout_exit_under_declared_policy(trade, close_event)
    repository.mark_nav(session, cash=portfolio.cash,
                        positions=portfolio.remaining_positions)
```

실시간 구현에서는 bar close 이후 수신 지연 때문에 바로 다음 bar open보다 주문이 늦을 수 있다. 이상적 '다음 시가'와 실제 available_at 이후 첫 실행 가능 quote 정책을 구분하고 0/1/5분 지연 민감도를 검사한다. 실시간 브로커 연동이 없는 현재 시스템에서는 같은 이벤트 모델로 **모의체결**만 기록하고 Telegram은 signal/상태 알림 역할로 둔다.

### 3.3 운영 무결성 및 필수 인수 조건

| 항목 | 인수 조건 |
|---|---|
| 동일 신호 자격 | strict/diagnostic 출력 형태와 무관하게 entry_eligible이 동일 |
| 미래 정보 차단 | 미래 봉 추가 전후의 과거 점수·후보·주문이 동일; bar.available_at 준수 |
| 상태 머신 일치 | 같은 fill stream을 replay하면 백테스트·운영 adapter의 수량·현금·종료 사유가 동일 |
| 경계 체결 | 갭 SL, TP1+TP2 동일 봉, 기존/새 SL 구분, 부분 fill, 거래정지, 미체결 취소 |
| 만료·재시작 | TP1 이후에도 만료 작동; 같은 session 반복 실행이 무효; 누락 session 순차 복구 |
| 자본·동시성 | 미체결 주문 위험 예약 포함; 여러 티커 동시 주문에서도 잔고·sector cap 불초과 |
| 저장·알림 | version 비교/행 잠금·unique id; DB commit 후 outbox 전달, 재전송 dedupe |
| 달력 | 미국 휴일·DST·조기 폐장·KST 익일 관계의 고정 fixture |
| 기업행동 | split 전후 가격·수량·SL·TP가 일관되며 배당을 현금흐름에 반영 |
| 성과 원장 | 마지막 미청산 mark, 날짜별 NAV, 비용, 원장 합계와 dashboard 일치 |

추적기의 현 SELECT→INSERT 경로는 unique index로 중복 행은 막아도 경쟁 시 트랜잭션 오류가 날 수 있고, reconfirmed_count의 read-modify-write는 증가 손실이 가능하다. `INSERT ... ON CONFLICT`, 원자 증가, 작업 잠금을 실제 정책에 맞춰 적용한다. active uniqueness를 ticker 전체로 할지 전략별로 할지는 실계좌 vs shadow ledger를 분리한 뒤 결정한다.

## 4. Concrete Parameter Recommendations

### 4.1 고정 기준과 실험 후보

아래 값은 **백테스트 출발점**이다. 수익성·안전성·시장 용량의 실증 추정치가 아니며, 사용자 계좌의 최적 비중도 아니다. champion과 기존 5성/2성 체계는 유지한다. 운영 자금 규모와 broker latency가 달라지면 특히 용량·spread 기준을 다시 산정해야 한다.

| 계층 | 기준/첫 후보 | 제한된 민감도 실험 | 채택 시 확인할 것 |
|---|---|---|---|
| 점수·별점 | 현 clipping과 68~78/85+ 유지 | 70·75 실제 셀의 성과, 65/80/85는 연구 대조군만 | 구성요소별 자격·box 위치·실제 stop 거리 |
| 일봉 낙폭 | champion -25% | ATR형 kD=6/8/10, clip 15~40% | beta·생존자 편향·regime별 후보수 |
| 일봉 박스 | 30일·20%, 5성 18% | kB=5/6.7/8, clip 12~25% | 고변동 종목만 유리하게 느슨해지는지 |
| 초기 SL | champion B×0.975 | B−kS×ATR_D; kS=0.25/0.5/0.75 | stopout, MAE, gap 포함 net return |
| overhead | 수정 baseline의 문서 정책을 명확히 고정 | 실제 overhead 집합; kO=1.5/2/2.5, kR=1 | cloud 내부/MA60 아래/결측 경계 |
| 일봉 준비/관측 | 최소 120봉, 성과 구간 외 별도 확보 | 150/250봉 입력에서 EWM warmup 차이 | 실시간·백테스트 indicator parity |
| H1 Spring base | 이전 완성 20봉 | 10/20/30봉 | 대기시간·놓친 진입·거짓 Spring |
| H1 Spring 깊이 | 0.1~0.5 ATR_H | 최대 0.25/0.5/0.75 ATR_H | 일봉 후보 자격 유지 여부 |
| H1 ST | Spring 이후 1~6봉, RVOL 비율≤0.8 | 3/6봉, 비율 0.6/0.8/1.0 | 시간대 거래량 보정·confirmation 비용 |
| H1 주문 | 다음 이벤트부터 stop-limit, limit 여유0.1 ATR_H | 0.05/0.1/0.2 ATR_H; 만료2봉 | touch가 아닌 실행 가능 fill·미체결률 |
| H1 보호 SL | min(Spring low, ST low)−0.25 ATR_H | 버퍼0.15/0.25/0.5 ATR_H | risk 폭 최소1.5 ATR_H, 상한8% |
| 일봉 후보 유효 기간 | 3 NY session | 1/3/5 session | 장기 대기와 재무장 횟수 |
| 목표·보유 | 20%/50%, 50% 분할, 20→40 session | 초기에는 그대로 고정 | 체결 정책 차이와 전략 변경 분리 |
| ADV | 과거20일 평균 거래대금≥$20m | $10m/$20m/$50m | 실행 notional별 net 성과와 제외율 |
| Spread | 의사결정 quote spread/mid≤20 bps | 10/20/30 bps | quote 없음은 '0 spread'가 아니라 확인 불가 |
| 주문 참여율 | 주문량≤과거 ADV 주식수의0.5%, 예상 시간대 거래량의2% | 0.1/0.5/1% ADV | 두 한도 중 작은 값; 미래 volume을 주문 sizing에 사용 금지 |
| 이벤트 | 알려진 실적발표 전후 신규 진입 금지 후보 | 다음2 session 이내 발표면 보류, 발표 후1 session 대기 | 발표시각 PIT; 기존 보유의 축소는 별도 전략 실험 |
| 개별 손실 예산 | NAV 0.25% | 0.10/0.25/0.50% | gap-aware d_eff, 동시 신규 주문 예약 |
| 집중 한도 | 종목5%, sector/cluster20% | sector15/20/25% | 중복 factor 노출, sector RC 30% 참고 상한 |
| 전체 위험 | gross≤100%, 연율 vol≤12%, open risk≤2% NAV | vol8/12/15% | 공분산·joint gap ES·현금 제약 |
| regime | ATR/VIX 분위 상위20%에서 신규 risk 예산 절반 | 축소0/50/100% 대조 | threshold 임의 이동 없이 노출만 조정 |
| 비용 스트레스 | quote 기반 비용과 체결 impact 추정 | Y=0.5/1/2 참고, 비용1배/2배 | Y를 실측 계수로 가장하지 않음 |
| gap 스트레스 | 공동 날짜 scenario + tail ES | -10/-20/-40%, 거래정지 후 재개 | 원금 기준 수익과 현재 NAV drawdown 별도 |

위 표 전체의 Cartesian product를 탐색하지 않는다. 후보가 늘어날수록 선택 편향이 커진다. 사전에 소수 가설·대조군·채택 기준을 정한다.

### 4.2 구현·검증 우선순위

1. **P0 — 측정 기반 복구:** C1 자격 필드, C2/C3/C4 공통 fill 원장·부분 청산, C5 승격 분리, H2/H3/H4 만료·멱등·마지막 봉 처리를 수정한다. 현재 챔피언 기록은 원본 그대로 보관하고 수정 기준의 별도 재측정 결과를 만든다.
2. **P1 — 성과 재현:** 버전·파라미터·데이터 hash와 597개 원거래를 대조한다. 정상화된 일봉 기준을 우선 재실행하며 quote 부재 시 비용별 시나리오와 미검증 항목을 명시한다. 포트폴리오 NAV로 MDD를 다시 계산한다.
3. **P2 — 시간봉 비교:** (A) 수정 일봉 + 다음 실행 가능 시가, (B) 동일 일봉 후보 + H1 트리거 + 일봉 SL, (C) B + H1 SL, (D) C + gap/correlation sizing을 순차 비교한다. 동일 기간·PIT universe·거래가능 조건을 사용한다.
4. **P3 — 적응형 임계값:** 비용·fill·portfolio 기준이 안정된 뒤 ATR형 낙폭/박스/overhead를 한 가족씩 비교한다. 점수 밴드 변경과 동시에 하지 않는다.
5. **P4 — shadow 검증:** 실주문과 분리된 모의 원장에서 중복·미체결·지연·오류 복구를 관찰한다. 고정 일수만 채웠다고 통과시키지 말고 필수 상태 전이와 실제 체결 비용 표본을 확보한다.

### 4.3 권고 연구 게이트와 남은 증거

새 연구 게이트는 기존 55%/2.0 또는 50%/1.30 기준을 조용히 대체하는 코드가 아니라 별도 제안이다. 승인된 champion policy와 이름·버전·수치를 분리해 관리해야 한다.

| 검증 항목 | 권고 통과 판단 |
|---|---|
| 계산 무결성 | 3.3의 필수 인수 시나리오 전부 통과, 라이브/백테스트 replay 일치 |
| net 기대값 | 군집/블록 의존성 반영 95% CI 하한>0; 부족한 검정력은 '미확정' |
| baseline 대비 | paired 일별 net 성과 차이와 비용·위험 tradeoff가 사전 목표를 충족 |
| 손익·위험 | net PF>1을 최소 필요조건으로, CI·MDD·tail ES·capital capacity도 함께 심사 |
| 안정성 | 인접 **실제 신호 집합**에서도 붕괴하지 않음, 특정 한 sector/regime에만 전부 의존하지 않음 |
| 비용 여유 | 2배 비용과 지연·gap stress에서 사전 risk budget 준수; 결과 나쁜 경우 원인 및 한도 재설정 |
| 선택 편향 | 모든 시도 기록, 최종 holdout 재사용 금지, 표본외 날짜별 원장 제공 |

추가로 필요한 증거는 (a) benchmark run_id·코드/params/data hash와 597개 거래·부분체결 원장, (b) NY session 기준 PIT daily/1H·기업행동·상장폐지 자료, (c) quote/주문/실제 fill과 latency, (d) GCP 설치 리비전·서비스/cron·수집 완료 시간, (e) 명시적으로 격리된 PostgreSQL 테스트 fixture다. 이 자료 없이 새로운 승률·PF·최적 threshold·정확한 gap 확률을 제시하면 추정치를 실측값으로 오인하게 된다.

현재 완료한 산출물은 코드 근거가 있는 결함 평가, 10건의 오프라인 재현, 필수 테스트 실행 결과, 네 감사 축의 수식·MTF 설계·제한된 백테스트 후보표다. 운영 적합성 재인증과 후보 전략의 성과 검증은 위 증거와 구현 수정 후 수행할 후속 작업이다.
