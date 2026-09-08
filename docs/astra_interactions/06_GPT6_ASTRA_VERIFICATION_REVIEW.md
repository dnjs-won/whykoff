# WHYKOFF-ASTRA-V2-VERIF-001 검증 회신

검토일: 2026-09-08, Asia/Seoul  
대상: [검증 요청서](GPT6_ASTRA_VERIFICATION_REQUEST.md)와 현재 미커밋 작업 트리. 검토 파일 SHA-256 및 재현 값은 [검증 JSON](audits/2026-09-08-verification/verification_evidence.json)에 보존했다.

**판정: 주요 국소 수정은 확인. “잔여 결함·가드레일 100% 완료” 및 GCP P2/P4 Shadow 주문·원장 파일럿의 최종 적합성 서명은 보류한다.** 오프라인 replay와 독립적인 원데이터 관측 준비는 진행 가능하다. 기존 production tracker에 연결한 모의 주문·성과 집계를 승인한다는 뜻은 아니다.

이번 검토는 운영 코드 수정·GCP 배포·Telegram 발송을 수행하지 않았다. 실제 GCP 서비스 구성 및 설치 리비전, 304종목 재백테스트 원장, 온라인 PostgreSQL 테스트 결과는 별도로 확인하지 못했다. 챔피언 변경은 없다.

## 1. 우선순위별 잔여 발견 사항

### [P1] 실행 일자 멱등성이 원본 봉 멱등성을 대체하지 못한다

근거: [가격 조회](services/trade_tracker.py:360), [일자 비교](services/trade_tracker.py:404).

같은 `last_evaluated_date`의 순차 재실행을 `continue`로 막은 것은 유효한 수정이다. 그러나 DB 조회는 여전히 `WHERE ticker=%s ORDER BY datetime DESC LIMIT 1`이며 봉 일시를 반환하지 않고 as-of 상한도 없다. 날짜 역행을 차단하는 `last_evaluated_date >= target_date` 조건도 없다.

재현: E=100, SL=95, TP1=120, O/H/L/C=110/121/99/115인 봉을 2월 3일 평가하면 TP1 후 SL=100.5로 OPEN이다. DB 갱신 상태로 같은 날짜를 반복하면 OPEN을 유지하지만 **동일 가격 봉을 2월 4일 요청에 넣으면 BREAKEVEN_SL로 청산**한다. 또한 last_eval=2월 3일에서 2월 2일 요청을 넣어도 최신 봉으로 다시 평가하고 보유일을 증가시켜 과거 날짜 청산을 기록한다.

휴장일·수집 지연·수집 실패 후 배치 실행에서 여전히 발생할 수 있는 경로다. `scheduler.py`도 수집 실패 시 기존 DB 데이터로 계속 진행한다. 원본 봉의 NY session·완성 여부·available_at·revision을 조회해 처리 키로 사용하고, 오래된 봉/역행 요청은 보류해야 한다. 동시 worker의 동일 stale row 처리도 막도록 행 잠금 또는 version 조건부 갱신과 이벤트 저장을 묶어야 한다.

### [P1] 시간봉 무효화가 영속 상태가 아니어서 과거 ST가 다시 활성화된다

근거: [최신 ATR·고정 슬라이스](engine/hourly_trigger.py:86), [ST 탐색](engine/hourly_trigger.py:142), [후속 봉 무효화](engine/hourly_trigger.py:174).

수정된 코드는 선택한 ST **이후** 저가만 검사한다. Spring 이후 ST 이전에 지지선이 붕괴한 경우는 무시한다. 합성 입력에서 Spring low=98.5, 다음 봉 low=90, 그 다음 저거래량 ST를 주면 기존 Spring으로 `ST_CONFIRMED`를 반환한다.

또한 호출할 때마다 최신 ATR로 과거 Spring/ST 손절선을 다시 계산한다. 재현에서는 ATR_H=2일 때 SL=98, 후속 low=97.9로 `INVALIDATED`였다. 다음 봉이 추가되고 최신 ATR_H=4인 입력에서는 SL을 97.5로 넓혀 **같은 과거 ST가 다시 triggered=True**가 된다. ATR은 합성 series로 주입해 계산 의존성을 분리했으며, 시장 발생 빈도를 측정한 결과는 아니다.

Spring 직전 base·ATR, ST 시각·SL·candidate revision을 확정 시점에 고정해야 한다. 무효화된 candidate revision은 재활성화하지 않고, 새 Spring/새 후보가 있어야 재무장하도록 한다. Spring→ST 전 구간의 구조 붕괴, 후보 3세션·주문 2봉 만료, available_at도 현재 인터페이스에서 관리되지 않는다. 현재 raw-volume `<0.8` 조건은 확인됐지만 RVOL 시간대 보정은 구현되지 않았다.

### [P1] 리스크 상한은 완전한 입력의 단일 호출에만 유효하다

근거: [기존 위험액 집계](engine/risk_allocator.py:100), [노출 집계](engine/risk_allocator.py:123), [누적 name cap](engine/risk_allocator.py:156).

동일 종목 5%, gross 100%, open risk 2%의 **각각 독립적인 정상 입력 거절**은 확인했다. 다만 기존 포지션에 `risk_dollars`가 없으면 0으로 처리한다. NAV=100,000, 기존 400주×100, SL=94이면 구조적 위험만 2,400으로 이미 2%를 넘는데, `risk_dollars`를 생략한 입력에서 신규 4,100달러 배정을 승인한다. 데이터 계약이 없어서 불완전한 포지션을 무위험으로 간주하는 셈이다. 필수 필드 결측은 거절하거나 동일 모델로 보수적으로 재계산해야 한다.

예약도 없다. 현재 modeled risk=1,700인 동일 스냅샷으로 A/B를 각각 호출하면 둘 다 246달러 위험의 주문을 승인하여 합계가 **2,192>2,000**이 된다. 순수 계산 함수 자체가 잠금을 가져야 한다는 뜻은 아니다. 승인·예약을 원자적으로 수행하는 서비스가 필요하며, 현재 검토 범위에는 그 소유자가 없다. 기존 노출도 현재 평가액 대신 `allocated_capital`/진입가에 의존하므로, 시가평가·잔여수량·미체결 예약의 입력 계약이 필요하다.

요청서의 config 값 `0.05/1.00/0.02`와 달리 실제 코드는 퍼센트 **`5.0/100.0/2.0`**을 받고 100으로 나눈다. `target_weight`도 0~0.05라는 주석과 달리 예시에서 **4.1**을 반환한다. GCP adapter를 붙이기 전에 ratio와 percent 단위를 통일해야 한다.

### [P1 — 성과 검증] NAV MDD 결함은 수정되지 않았고 완료 집계에서 빠졌다

근거: [거래별 전액 복리 계산](engine/backtester.py:342), [프로젝트 완료 주장](PROJECT_STATUS.md:142).

이전 보고서의 원래 8건에는 **거래 연쇄 MDD와 NAV의 차이**가 있었다. 이번 요청서의 8건 표는 이를 빼고 DB 오프라인 Skip 처리를 넣었다. 반면 PROJECT_STATUS는 포트폴리오 NAV MDD까지 전수 조치 완료라고 주장한다.

현재도 `equity *= 1 + trade.pnl_pct/100`이다. 초기 NAV=100, cash=90, A/B에 각 5 투자, 동일 시간축의 반대 방향 +10%/-10% 가격 움직임이면 NAV MDD=0%지만 함수는 10%를 반환한다. 기존 원거래의 순서 정렬은 이 문제를 해결하지 않는다. NAV 원장을 구현하기 전에는 해당 지표를 거래 연쇄 drawdown으로 명시하고 “포트폴리오 위험 검증 완료”에서 제외해야 한다. 원자료 수집만 하는 관측기의 시작을 이 지표 하나로 막을 이유는 없으나, NAV 성과·위험 파일럿의 인수 증거로 사용할 수 없다.

### [P2] DB 테스트 Skip 처리가 환경 장애 외의 회귀까지 숨긴다

근거: [tracker setUp/tearDown](tests/test_trade_tracker.py:34), [inspector setUp](tests/test_ticker_inspector.py:20).

`except Exception → SkipTest`이므로 접속 거부 외에도 테이블 누락·SQL 오류·권한 오류·프로그래밍 예외가 skip으로 분류된다. 재현에서 `RuntimeError("schema or code error, not offline")`도 SkipTest가 된다. teardown은 예외를 모두 삼켜 정리 실패도 숨긴다.

이번 **32개 발견, 29개 통과, 3개 skip**은 정확히 재현했다. 32개 전부 실행·검증된 것은 아니다. 명시적인 offline 모드에서만 DB 통합 테스트를 건너뛰고, integration CI에서는 schema/권한/SQL/정리 오류를 실패로 처리해야 한다. 온라인 경로는 여전히 테스트 대상 DB를 강제하지 않고 DELETE와 일반 OPEN 포지션 조회를 수행한다. Skip 추가는 DB 격리를 구현한 것이 아니다.

## 2. 확인된 수정과 요청서의 정확성

오프라인 독립 검증은 유효한 Spring/ST 양성 fixture를 먼저 확인한 뒤 음성·경계 입력을 비교했다. 아래 7개 시나리오 그룹에서 의도한 수정 동작을 확인했다.

| 기존 항목 | 검증 결과 | 범위·문서 정정 |
|---|---|---|
| 동일 봉 TP1 재평가 | 같은 날짜의 커밋된 상태로 재실행하면 OPEN 유지 | `effective_from_next_bar` 플래그가 아니라 last_eval 동일 시 continue |
| C5 실패 객체 flag | 저장 직전 is_champion=False | 요청서의 `dry_run_passed`는 구현에서 찾지 못함; 필수 해결은 확인 |
| 일봉 부적격 시간봉 | entry_eligible=False 또는 sweet_spot=False이면 DISQUALIFIED | `score<68` 직접 비교나 REJECTED_DAILY_SETUP 상태는 없음. 실제 생산자 자격 플래그에 의존 |
| ST 거래량 | 동일 거래량 거절, 50% 거래량 ST 양성 | `<0.8 × raw volume` 구현. 정확히 80%도 거절하는 경계이며 RVOL은 아님 |
| ST 이후 붕괴 | 고정 ATR 하에서 후속 저점 하회 시 INVALIDATED | §1의 후보 재활성화·ST 이전 붕괴는 잔존 |
| SL≥E | 동일/상회 입력 모두 approved=False | ValueError 발생 주장은 잘못됨; 결과 객체로 거절 |
| name/gross/risk caps | 완전한 기존 포지션 입력에서 각 cap을 독립적으로 검사·거절 | 불완전 입력·현재 평가·예약은 §1의 미완료 사항 |

TP1 알림의 “원금 100% 보존” 표현을 본전 보호·갭 위험 문구로 바꾼 것은 확인했다. 그러나 [브리핑의 보유 모드 표시](services/briefing_service.py:178)에는 **“무위험 Free-Ride 모드”**가 남아 있다. 전체 사용자 경로의 표현 수정 완료로 보기는 어렵다.

기존 `review_evidence.py`는 과거 결함이 존재함을 assert하는 역사적 재현 스크립트다. 수정 후에도 그 전체 스크립트가 성공해야 하는 인수 테스트가 아니다. 이번에는 이를 덮어쓰지 않고 새 [검증 스크립트](audits/2026-09-08-verification/verify_remediation.py)를 작성했다. 결과는 **수정 확인 7개 그룹, 잔여 관측 8건**이다. 잔여 8건은 새 검증의 관측 수로, 이전 결함 8개가 전부 그대로라는 뜻이 아니다.

## 3. P2/P4 파일럿 판단과 다음 인수 조건

`main.py`, `services/`, `collectors/`, `schema.sql` 검색에서 두 신규 엔진을 사용하는 운영 연결 경로나 Shadow 전용 주문·체결·NAV 원장 구현을 확인하지 못했다. 따라서 **모듈의 국소 검증을 GCP 병렬 파일럿의 적합성 검증으로 확대할 수 없다.** 요청서는 파일럿 승인 요청이며, 실제 원격 배포 완료의 증거도 제공하지 않는다.

| 범위 | 검토 의견 |
|---|---|
| 로컬 합성 데이터·오프라인 replay | 진행 가능. 실패·무효화 사례를 계속 포함 |
| 별도 원데이터·후보 관측 | 설계 준비 가능. 원장 쓰기 격리·데이터 시각 검증을 갖춘 관측기 코드 검토 후 연결 |
| GCP Shadow 주문 intent·모의 fill·risk/NAV 집계 | **최종 서명 보류**. 아래 통합 증거 필요 |
| 실제 주문·자본 투입·champion 승격 | 이번 검토 범위 밖이며 승인하지 않음 |

필요한 다음 변경은 ① 봉 identity/as-of·역행 차단·원자적 dedupe, ② Spring/ST 상태 및 가격 고정·만료, ③ 필수 risk 입력 계약·시가평가·주문 예약 트랜잭션, ④ production DB와 분리된 Shadow event/fill/cash/NAV 원장·제한 권한·worker, ⑤ 전용 DB의 integration CI다. 각 변경은 정상·중복·restart·rollback·동시 주문 fixture와 함께 검증한다. quote 수집만 하는 관측 단계와 모의 주문 실행 단계를 별도 feature flag로 나눈다.

**ATR 1.5 vs 2.0:** 현재 allocator에는 ATR 입력이 없고 `max(d_SL+0.3%p, 6%)` 모델이다. 먼저 `ATR_D / decision_price`의 단위와 as-of를 명시한 별도 챌린저 계약을 만들고, 같은 후보·fill stream에서 k=1.5/2.0만 바꿔 비교해야 한다. 신호·SL·배분을 함께 바꾸지 않고, 40건을 보고 유리한 계수를 선택하지 않는다. 자세한 사전등록·paired/holdout 절차는 [기존 후속 명세](GPT6_ASTRA_FOLLOWUP_REVIEW.md)를 따른다.

**NAV 수집:** 주문/부분체결별 현금·수수료·잔여수량·동일 시각 mark를 기록하여 `NAV_t=Cash_t+ΣQ_i,t×Mark_i,t`를 구성한다. TP1 현금, 미체결 예약, terminal 평가, stale mark를 분리한다. 일별 NAV MDD와 장중 NAV MDD의 관측 주기를 명시하고, 30~60일 충족 자체를 성과 통과 조건으로 사용하지 않는다.

## 4. 재현 및 제한

- 필수 명령: `python -m unittest discover tests` → **OK (skipped=3), 32개 발견**. DB_HOST=127.0.0.1, DB_PORT=1, 감사용 DB 이름을 해당 프로세스에 설정했다. [로그](audits/2026-09-08-verification/unittest_discover.log).
- 독립 검증: `python audits/2026-09-08-verification/verify_remediation.py` → 수정 확인 7개 그룹·잔여 관측 8건. 실제 DB 연결은 mock으로 금지한다. [코드](audits/2026-09-08-verification/verify_remediation.py), [결과](audits/2026-09-08-verification/verification_evidence.json).
- 304종목/500봉의 40건·PF1.55·Exp2.04%는 요청서의 재보고 수치이며 이번 실행에서 독립 재측정한 값이 아니다. 온라인 32개 전체 통과, 원격 로그/서비스 상태도 미확인이다.

최종 회신: **수정 작업은 실질적인 진전이 있으나 완료 범위가 과장되어 있다. 국소 수정 확인으로 한정해 접수하며, 위 통합 결함과 측정 증거를 보완한 뒤 GCP Shadow 파일럿을 재검토한다.**
