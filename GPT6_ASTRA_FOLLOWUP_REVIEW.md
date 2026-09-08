# Whykoff 후속 감사 및 P2–P4 실행 명세

검토일: 2026-09-08 (Asia/Seoul)  
대상: [후속 요청서](GPT6_ASTRA_FOLLOWUP_REQUEST.md)와 현재 미커밋 작업 트리. 파일별 SHA-256은 [재현 결과](audits/2026-09-08-followup/review_evidence.json)에 보존했다.

**판정: P0의 주요 수정은 확인했지만, P1을 검증 완료된 클린 기준선으로 인증할 수 없다. P2 연구 설계와 P4 관측 준비는 진행할 수 있으며, 성과 비교·P3 튜닝·승격에 앞서 아래 측정 결함을 닫아야 한다.**

이번 산출물은 구현 재검토, 오프라인 결함 재현, 통계 계산, 실험 프로토콜 및 Shadow 원장 설계다. 304종목의 원거래·일봉·시간봉 데이터 스냅샷, GCP 설치 리비전, 실시간 quote/fill은 확보하지 않았다. 따라서 40건의 실제 재백테스트나 원격 배포를 완료했다는 뜻이 아니다. 기존 챔피언 `wyckoff_v2.0_full_swing`은 유지하며, 68~78점 5성·85점 이상 2성 및 섹터 가산점 금지 원칙을 그대로 적용한다.

## 1. P0 수정 재검토와 남은 차단 조건

| 항목 | 확인한 개선 | 남은 조건 |
|---|---|---|
| C1 자격 분리 | 실패 게이트를 `entry_eligible=False`로 반환하고 일봉 백테스트·트래커가 검사 | POC +7% 초과와 MA20 기울기 상한 초과는 여전히 가점 미부여이며 hard reject가 아님. 문서의 필수 조건과 구분 필요 |
| C2/C4 분할 회계 | TP1 50%, 잔여 50%, +35% TP2 정산 구현 | 공통 상태 함수가 아닌 두 구현의 복제. 동일 봉에서 TP1 후 새 SL을 언제 활성화하는지 명시하고 동일 이벤트 replay로 검증 필요 |
| C3 갭 손절 | open이 기존 SL 아래면 open 사용 | 비용·유동성·거래정지 미모델링. open 부재 시 high를 쓰는 fallback은 실체결 증거가 아님 |
| C5 dry-run | 게이트 통과·우수 후보 분기에서 `auto_promote` 반영 | **게이트 실패 분기는 입력 객체의 `is_champion=True`를 지우지 않고 저장**. 재사용 객체로 dry-run 오염 재현 |
| H1 상단 공간 | 기본 게이트 True, 가격 위 저항 선택 및 구름 내부 상단 검사 | 구름 결측 시 통과 가능. 설정·데이터 결측 정책 고정 필요. 기본값 변경이 GCP에도 적용됐는지는 미확인 |
| H2 만료 | TP1 분기 밖에서 만료 평가 | 휴장·누락 세션 및 실제 거래일 기준 나이 계산은 별도 미완료 |
| H3 멱등성 | 같은 일자 보유일 이중 가산 방지 | **상태 전이는 재실행된다. 첫 평가 TP1→새 SL, 동일 봉 재평가→본전손절 종료 재현**. `as_of` 상한 없는 최신 봉 조회도 잔존 |
| H4 마지막 봉 | 최종 봉 SL과 terminal MTM 기록 | MTM 평가와 실제 청산을 성과에서 구분하고 부분 체결·현금 원장 필요 |
| H5 MDD | 청산일 기준 거래 정렬 | **여전히 모든 거래를 전액 복리화한다. 날짜별 포트폴리오 NAV MDD가 아님** |
| M3 MFI | 거래량 0의 중립값 수정 | 데이터 유효성 검사는 별도 |

근거: [백테스터](engine/backtester.py), [추적기](services/trade_tracker.py), [스캐너](engine/wyckoff_scanner.py), [감사 테스트](tests/test_audit_remediation.py).

**재현된 H3 예:** E=100, 기존 SL=95, TP1=120, O/H/L/C=110/121/99/115. 첫 실행은 TP1 50%와 SL=100.5를 기록하고 OPEN 유지한다. DB에 갱신된 상태로 같은 세션·같은 봉을 다시 넣으면 low=99를 새 SL에 적용해 CLOSED가 된다. 보유일이 같아도 멱등성이 성립하지 않는다. `(run, trade, source_bar_revision, evaluation_policy)` 단위의 중복 이벤트 차단과 상태·이벤트의 원자적 커밋이 필요하다. 수정 봉은 원본 덮어쓰기 대신 revision/replay 정책으로 처리한다.

**재현된 H5 예:** 초기 NAV=100, 현금=90, 동시 보유 A/B에 각각 5를 투자한다. 두 가격이 같은 시간축에서 각각 +10%/-10%로 선형 이동하면 NAV=100, MDD=0%다. 현 함수는 거래 +10% 뒤 -10%를 전체 자산에 복리 적용하여 MDD=10%를 반환한다. 시간 정렬만으로 자본 배분과 중간 평가손실을 복원할 수 없다.

추가로 H7(신호 종가 진입과 익일 시가 진입 불일치), H8(daemon/cron 작업 소유권), H9(DB 테스트 격리), H10(체결 용량)이 이번 수정 목록만으로 닫히지 않는다. 트래커의 “원금 100% 보존·손실 위험 전혀 없음” 문구는 요청서의 TTD -2.83% 사례와도 모순된다. Shadow에서는 `모의 TP1 체결 / 보호 stop 상향 / 갭 손실 가능`으로 표시해야 한다. Stop 가격은 보장된 체결 가격이 아니며 stop-limit는 미체결될 수 있다. [SEC Investor Bulletin](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-15).

### 1.1 신규 모듈은 연구 프로토타입 단계

| 계약 | 요청서·설명 | 실제 구현 및 판정 |
|---|---|---|
| Spring 깊이 | 0.1~0.5 ATR_H | 0.05~0.6 ATR_H, 종가도 `>` 대신 `>=` |
| 일봉 POC 근접 | 원 감사 1.0 ATR_D | 1.5 ATR_D |
| ST 거래량 | 요청서 70% 이하 / docstring RVOL 80% | **원거래량 100% 이하**; 시간대 정규화 없음, 동일 거래량도 확정 |
| 일봉 자격 | 적격 5성 후보만 | 함수 내부 자격·별점 검사 없음. 부적격 1성도 triggered=True 재현 |
| Spring 기준 보존 | 해당 Spring 직전 20봉·당시 ATR | 호출 끝 기준 이전 7봉 제외 base 및 최신 ATR. 후보·Spring·ST 시각/만료 상태 보존 없음 |
| ST 무효화 | 지지 붕괴·후보 만료 시 취소 | 이전 ST 후 종가가 SL 밑으로 붕괴해도 과거 ST를 재사용해 True 반환 |
| 갭 위험 | `max(d_SL, 1.5 ATR_pct)` | **`max(d_SL + 0.3%p, 6%)`**. ATR 인자 없음. `annual_volatility`는 미사용 |
| 포트폴리오 한도 | name 5%, sector 20%, open risk 2%, gross 100% | 신규 배정 name·sector만 검사. 기존 동일 종목 누적·미체결 예약·gross·open risk 미적용 |
| 비중 단위 | `target_weight` 주석 0~0.05 | 실제 0~5의 퍼센트 반환. 예시 4.1을 비율로 소비하면 100배 오류 |
| 입력 검증 | 신규 long 구조적 SL<E | SL>E도 승인. NaN/inf, NAV/설정 유효성 및 보유 가격 평가 계약 필요 |

근거: [hourly_trigger.py](engine/hourly_trigger.py), [risk_allocator.py](engine/risk_allocator.py). 현재 `main.py`, `services/`, `collectors/`에서 두 모듈을 호출하는 경로는 검색되지 않았다. 따라서 “탑재”는 파일·테스트 존재를 뜻하며 운영 연결 완료를 입증하지 않는다. 시간봉 테스트는 ARMED도 허용하고 triggered일 때만 가격을 검사하므로 Spring/ST 검출 성공을 증명하지 않는다.

## 2. 요청 1 — 40건의 통계적 해석

요청서의 21승/40건을 독립·동일확률 Bernoulli로 가정한 계산이다. 시장·섹터·동시 보유 의존성은 이 가정을 약화한다.

| 계산 | 결과 | 해석 |
|---|---:|---|
| 승률 | 52.50% | 점추정 |
| 95% Wilson 구간 | **37.50~67.06%** | 50%를 충분히 포함 |
| H0:p=50%, 양측 정확 이항 검정 | p=0.8746 | 승률 50% 초과 증거로 부족 |
| n=40, 실제 p=52.5%, 양측 α=5% 정확 검정력 | **4.82%** | 작은 차이를 검출하기 어려움; 이산 검정 특성 포함 |
| 평균익 10.89%, 평균손실 7.75%의 산술 재계산 | Exp=2.036%, PF=1.5531 | 반올림된 보고값끼리는 일관됨 |
| 동일 payoff 가정의 비용 전 손익분기 승률 | 41.58% | WR>50%는 수익성의 필요조건이 아님 |

Wilson 계산은 [NIST 신뢰구간 공식](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm)을 적용했다. 계산 코드와 원시 숫자는 [review_evidence.py](audits/2026-09-08-followup/review_evidence.py), [JSON](audits/2026-09-08-followup/review_evidence.json)에 있다. 이 p-value는 투자 수익성이 없다는 검정 결과가 아니다.

양측 α=5%, power=80%, H0:p=0.5의 정규근사 표본 계획은

`n ≈ ceil((z_.975√(.5×.5) + z_.8√(p1(1-p1)))² / (p1-.5)²)`

이며 p1=52.5%는 3,138건, 55%는 783건, 60%는 194건이다. 연속성 보정 없는 독립 표본 근사로, 정확 검정의 보장 표본수나 거래 목표가 아니다. 평균 기대값·paired 차이의 검정력은 해당 분산 및 날짜 블록 의존성으로 별도 계획한다. [NIST 표본수 설계](https://www.itl.nist.gov/div898/handbook/prc/section2/prc242.htm).

**PF·기대값의 실제 95% CI는 집계값만으로 산출할 수 없다.** 수익률 분산·꼬리·거래 시점이 빠져 있다. 승률 CI에 고정 payoff를 끼워 넣어 기대값 CI라고 부르지 않는다. 원장을 얻으면 전체 날짜축을 블록으로 함께 재표집하고, 동일 entry-session의 모든 티커를 묶어 거래별 Exp/PF를 재계산한다. 포트폴리오 순수익은 같은 블록으로 NAV에서 계산한다. 20/40/60세션 길이, seed·반복수 10,000을 기록하고 손실 없는 bootstrap 표본은 PF=무한대/정의불가 빈도를 따로 보고한다. 현재 40건에서는 블록 수가 적어 CI 자체도 불안정할 수 있다.

32.51%는 현재 코드에 의하면 **거래 연쇄 복리 drawdown**이다. 81.33% 대비 “실제 MDD 방어 48.8%p”로 해석하지 않는다. `NAV_t = Cash_t + Σ Q_i,t × Mark_i,t`, `MDD=max_t(1-NAV_t/max_{u≤t}NAV_u)`로 재측정해야 한다. 비용·TP1 현금·동시 보유·미실현손익·기업행동을 반영한다.

597→40 감소는 같은 시점별 신호를 조인해 탈락 사유를 세기 전까지 “550건 이상 phantom”, “노이즈 100% 제거”로 입증되지 않는다. 기간·유니버스·기본값·진입 정책이 달라졌을 수 있다. 13건 최근 부분집합과 전체 40건은 겹치므로 최근 레짐 개선의 독립 증거도 아니다. 1~9건 섹터별 승률은 기술통계로만 제시한다.

### 표본 확장 순서

1. **시계열 확장 우선:** 가능하면 5년 이상, 하락·급등·고변동 레짐 포함. 현재 종목 목록만 과거에 투영하지 않고 당시 편입·상장·상장폐지·기업행동을 확보한다. 자료가 없으면 survivor-only 연구로 명시한다.
2. **유니버스 확대 병행:** 과거 시점의 유동성·거래가능성 규칙으로 선정한다. 성과가 좋았던 종목·섹터를 사후 추가하지 않는다. 동일 날짜 상관종목 추가는 독립 표본 증가와 다르다.
3. 준비 120봉은 성과 기간 밖에 확보한다. `limit=500`은 종목별 500개 최신 행이며 공통 평가기간이나 500개 완전 세션을 보장하지 않는다. listing/결측/중복/최종 완성 시각을 manifest에 기록한다.
4. 40건을 늘리기 위해 hard filter를 완화하거나 파라미터를 이동하지 않는다. 그 경우 새 챌린저 실험으로 등록한다.

## 3. 요청 2 — P2 시간봉 paired 검증 파이프라인

### 데이터·인과성 계약

- 한 번 생성한 `candidate_id=(ticker, signal_session, strategy_version, params_hash)` 집합을 모든 arm에 제공한다. 적격 5성만 사용하며 탈락·미체결 후보도 남긴다.
- 일봉 완성 뒤 당시 알 수 있었던 POC_D, ATR_D, box, 점수 구성, 저항, 자격, available_at을 고정한다. 이후 업데이트는 새 candidate revision과 이전 주문 취소 이벤트로 처리한다.
- 시간봉은 `bar_start_utc`, `bar_end_utc`, `available_at`, NY `session_date`, finalized, source_revision을 갖춘다. 현재 KST naive 저장값은 collector의 변환 규칙을 확인한 뒤 변환한다. naive 값을 UTC로 단순 지정하지 않는다.
- 미국 거래소 달력으로 DST·휴일·조기폐장과 마지막 30분 봉을 처리한다. RTH/extended-hours 정책을 고정한다. quote 부재·미완성·중복·결측은 quality reject이며 0 spread로 채우지 않는다.
- Spring 후보마다 **그 Spring 직전 20봉** base와 당시 ATR_H를 보존한다. ST는 이후 1~6봉, 후보는 다음 3개 NY세션, 주문은 ST 확정 이후 2봉까지만 유효하다. 세션 종료 carry 여부는 실험 설정으로 고정한다.
- 거래량은 과거 20세션 같은 시간대 분당 거래량 중앙값으로 정규화한 RVOL을 사용한다. 초기 한 정책을 `Spring 0.1~0.5 ATR_H, POC 거리≤1 ATR_D, ST RVOL≤0.8 Spring RVOL`로 사전 등록한다. 요청서의 0.7은 별도 후보이며 현재 코드 1.0과 섞지 않는다. 값들은 원 감사의 연구 후보이며 검증된 최적값이 아니다.

### 실험 arm

| Arm | 진입 | SL | 배분 | 비교 목적 |
|---|---|---|---|---|
| A | 동일 일봉 후보, 다음 실행 가능한 정규장 시가 | 일봉 구조적 SL | 공통 기준 배분 | 수정 일봉 기준선 |
| B | 동일 후보의 H1 ST 뒤 buy-stop-limit | A와 같은 일봉 SL | A와 동일 명목 배분 | 확인 대기·체결 정책 효과 |
| C | B | H1 구조적 SL | A/B와 동일 명목 배분 | 손절 폭 변경 효과 |
| D | C | C | gap-aware 위험 배분·누적 한도 | 배분 변경 효과 |

먼저 후보 단위 단위명목 연구로 A/B/C를 비교하고, 그 다음 모든 arm에 동일 현금·명목 상한·예약 규칙을 적용한 독립 포트폴리오 replay를 수행한다. D만 위험예산 사이징으로 바꾼다. 각 arm의 TP1/TP2는 **해당 arm의 실제 모의 평균 진입가** ×1.2/1.5, 50% 분할, 20→40세션을 유지한다. C의 짧은 SL을 핑계로 A/B/C 수량을 동시에 늘리지 않는다.

### 이벤트 순서와 체결 모델

1. 각 시간의 기존 주문부터 시장 이벤트로 match한다. 이후 완성봉 분석으로 신규 intent를 만든다. ST를 만든 봉 고가로 그 주문을 소급 체결하지 않는다.
2. `active_after=decision_at+declared_latency` 이후 quote만 체결 후보로 사용한다. available_at이 다음 시가보다 늦으면 그 시가는 실행 불가다. 지연 0/1/5분 스트레스를 별도 기록한다.
3. buy-stop=ST high+tick, buy-limit=stop+0.1 ATR_H. 갭으로 limit 위에 열리면 open 체결을 주지 않는다. 이후 limit 이하로 되돌아왔는지는 실제 quote 또는 명시적 OHLC 가정으로 평가한다.
4. OHLC에서 stop-trigger와 limit 재진입, TP1과 새 SL, SL과 TP2가 같은 봉이면 순서를 알 수 없다. 가능한 경로별 보수/낙관 결과와 ambiguous 비율을 모두 기록한다. 더 짧은 봉·quote가 없으면 유리한 경로만 선택하지 않는다.
5. 매수 비용은 ask/중간값 기준, 매도는 bid/중간값 기준으로 분리한다. quote 없는 기간에는 비용 가정 5/10/20/40 bps per side 및 2배 비용을 **민감도 시나리오**로만 사용한다. 진입 지연의 가격 변화와 주문 제출 뒤 체결 슬리피지를 구분한다.
6. 주문 수량은 당시 known ADV·현금·보유 및 예약을 사용한다. 미래 봉 volume으로 주문량을 정하지 않는다. 사후 체결량 cap은 가능하나 구체적 모델과 부분체결·잔량 취소를 원장에 남긴다.
7. 보호 stop 변경은 TP1의 목표 수량 체결 확인 후 활성화한다. 순서가 확정된 fill stream을 기준으로 백테스트·Shadow에서 같은 순수 전이 함수를 호출한다. 만료 청산은 사전 MOC 정책 또는 다음 실행 이벤트 정책을 고정한다.

### paired 지표와 채택 판단

주요 추정량은 모든 원후보에서의 `Δ_i = net_pnl_B_or_C,i / fixed_reference_capital - net_pnl_A,i / fixed_reference_capital`이다. 미체결은 후보 명목 손익 0 및 미체결 사유로 남긴다. 공통 데이터 결측 후보는 모든 arm에서 같은 규칙으로 제외하고 coverage 표에 센다. 성공적으로 체결된 교집합만의 비교는 보조 분석이다.

포트폴리오에서는 공통 날짜의 `Δr_t=r_challenger,t-r_A,t`를 날짜 블록 paired bootstrap한다. 후보 단위 delta와 포트폴리오 delta는 서로 다른 estimand다. 현금 대기와 자본 경쟁으로 A와 D의 후보별 단순 합이 NAV 차이와 같지 않을 수 있다.

필수 결과: 후보수/무신호/취소/만료/미체결/체결률, 후보 전체 net expectancy, realized/net PF, 실제 SL 거리 및 ATR 단위 거리, 손절률·갭 손실, MAE/MFE, 지연시간, implementation shortfall, NAV MDD·tail loss·turnover·peak gross·peak reserved risk. SL 거리 감소만으로 개선 판정을 내리지 않는다.

사전 채택 제안: 순수익 paired CI 하한>0, 비용 2배·지연 스트레스에서 위험 예산 준수, net PF>1, arm별 동일 회계 재현. 낮은 검정력은 “보류”이며 승률·PF 임계값을 사후 낮추지 않는다. 최초 통계적 주 비교는 C−A 하나로 정하고 나머지는 기여 분석으로 표시한다.

## 4. 요청 3 — P3 ATR 적응형 밴드 실험

단위는 비율 소수이며 `a_t=ATR_D,t / P_t`, B_t는 box low, R*_t는 가격 상방 최인접 저항이다. 신호 마감에 계산하고 후보에 고정한다.

```
D_min,t = clip(k_D * a_t, 0.15, 0.40)
drop_fraction <= -D_min,t
BoxMax_t = clip(k_B * a_t, 0.12, 0.25)
box_fraction <= BoxMax_t
SL_D = B_t - k_S * ATR_D,t
R*_t - E_ref >= max(k_O * ATR_D,t, k_R * (E_ref - SL_D))
```

E_ref는 의사결정 때 알려진 진입 참조가다. 실제 진입가가 달라지면 당시 알 수 있는 quote/limit로 risk 재검사하고 sizing을 줄이거나 취소한다. 미래 실제 fill 가격을 과거 신호 생성에 사용하지 않는다. SL≤0, SL≥진입가, ATR 결측, 저항 데이터 결측은 명시적 reject다. 저항 후보 없음은 결측과 구분한다.

| 실험 가족 | 앵커 | 사전 제한 후보 | 고정하는 것 |
|---|---:|---|---|
| D 낙폭 | k_D=8 | 6/8/10 | 박스·SL·overhead·별점 |
| B 박스 | k_B=6.7 | 5/6.7/8 | 낙폭·SL·overhead·별점 |
| S SL | k_S=0.5 | 0.25/0.5/0.75 | 진입 후보 집합·목표·holding |
| O 공간 | k_O=2, k_R=1 | 1.5/2/2.5 | 나머지 가족·별점 |

각 가족을 수정 기준선에 대해 독립 비교하는 **12개 후보와 기준선 1개**부터 시작한다. 가족별 우승자를 연결해 반복 튜닝하면 그것도 추가 선택이므로 실험 횟수에 포함한다. 결합 모델은 내부 검증 후 사전 등록한 1개만 추가한다. 비용 스트레스 등 모든 재실행도 run_id와 purpose를 기록한다. 후보 전체 성과행렬·실패 결과를 보존하고 다중 비교는 Holm 등 사전 고정 방법으로 다룬다. 반복 탐색과 선택 편향의 근거는 [Bailey 외, The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)이며, 여기의 후보 수·워크플로우는 프로젝트용 설계 제안이다.

특히 현재 5성에는 box≤18%가 별도로 들어간다. 이 조건을 유지하면 18%보다 느슨한 동적 box gate가 5성 후보를 늘리지 못하는 구간이 있다. 이를 숨기지 말고 실제 바뀐 후보 집합 수를 보고한다. 68~78·85+ 및 기존 5성 박스 기준은 이 실험에서 유지하며, 별점 변경은 범위 밖이다. 점수 합이 5점 단위이므로 실제 70/75/80/85 셀을 비교한다.

권고 시간 분리: 24개월 학습 + 3개월 내부 검증 + 다음 3개월 OOS, 3개월 이동. 학습 라벨 `[entry,exit]`이 검증/OOS와 겹치면 purge한다. 최대 보유 40세션을 경계 점검에 사용하고 미래를 포함하는 CV에는 embargo를 추가한다. 최종 6~12개월 holdout은 모든 선택 후 한 번만 연다. 기존 40건의 기간은 이미 결과를 봤으므로 untouched holdout이 아니다. 충분한 기간이 없으면 P3 연구 설계만 고정하고 forward 표본을 축적한다.

ATR 급등이 box를 자동 완화하고 SL을 넓히는 효과, 종목 가격대·섹터·레짐·상장기간별 후보 변화를 확인한다. 전체 최고값보다 인접 후보의 net 성과 안정성을 보되 동일 신호 집합은 독립 강건성 증거로 세지 않는다. 역산된 “좋은 값”을 기존 champion에 바로 주입하지 않는다.

## 5. 요청 4 — P4 Shadow 원장·텔레메트리 계약

**30~60일은 관측 기간이며 자동 승격 조건이 아니다.** 날짜 정의는 달력일로 기록하고 실제 NY세션 수를 함께 보고한다. 처음에는 quote·후보 관측과 replay부터 시작하고 주문 브로커 호출 경로가 없는 별도 Shadow 프로세스로 구성한다. Yahoo OHLCV만으로 NBBO·queue·실제 체결 슬리피지는 관측할 수 없다. quote 제공자·coverage·timestamp 의미·지연을 명시한 adapter가 선행되어야 한다. 모의 fill은 실제 broker fill과 다른 `execution_kind`다.

별도 PostgreSQL DB 또는 `shadow` schema 및 전용 역할을 사용한다. 역할은 Shadow 이벤트 INSERT, projection SELECT/UPDATE만 허용하고 `active_trades`, `strategy_benchmarks` 쓰기 권한은 주지 않는다. 원본 events/quotes/fills는 append-only, 잘못된 기록은 correction event로 수정한다. 아래 설계는 아직 배포되지 않았다.

| 테이블 | 핵심 컬럼·고유키 | 목적 |
|---|---|---|
| `shadow_runs` | run_id UUID PK, mode=SHADOW, code_sha, params_hash, data_manifest_hash, execution_model_version, seed, config JSONB, started_at/stopped_at timestamptz | 재현 가능한 실행 단위 |
| `shadow_candidates` | candidate_id UUID PK, run_id FK, ticker, signal_session DATE, revision INT, available_at, expires_at, assessment JSONB; UNIQUE(run_id,ticker,signal_session,revision) | 점수·자격·게이트와 원후보 집합 |
| `shadow_quotes` | quote_id UUID PK, provider, instrument, source_event_id, source_at/received_at timestamptz, bid/ask NUMERIC(20,8), sizes, venue, coverage, quality_flags; UNIQUE(provider,instrument,source_event_id) | 체결 모델 입력, stale/crossed/locked 구분 |
| `shadow_orders` | order_id UUID PK, candidate_id FK, arm, revision, side, type, qty NUMERIC(20,8), stop/limit, active_after/expires_at, status, reserved_cash/risk, version; UNIQUE(candidate_id,arm,revision) | 주문 intent·잔량·예약 projection |
| `shadow_events` | event_id UUID PK, run_id FK, stream_id, sequence BIGINT, event_type, source_at/received_at/processed_at, session_date, idempotency_key, payload JSONB, payload_sha; UNIQUE(run_id,idempotency_key), UNIQUE(run_id,stream_id,sequence) | 원본 순차 상태 전이 |
| `shadow_fills` | fill_id UUID PK, order_id FK, event_id FK UNIQUE, quote_id FK nullable, source_fill_id, execution_kind, qty, price, fee, filled_at, ambiguity/model_flags; UNIQUE(order_id,source_fill_id) | 부분체결·비용 현금 원장 |
| `shadow_positions` | run_id, arm, ticker 복합 PK, initial_qty/remaining_qty, avg_entry, realized_cash_pnl, current_stop, tp1_filled_qty, entry_session, max_sessions, last_event_id, version | 이벤트로 재생성 가능한 projection |
| `shadow_nav_marks` | run_id, arm, marked_at 복합 PK, session_date, cash, market_value, nav, gross, reserved_cash/risk, open_risk, fees, stale_mark_count, mark_manifest_hash | 실현·미실현 포함 NAV |
| `shadow_outbox` | outbox_id UUID PK, event_id FK, channel, payload, attempts, next_retry_at, delivered_at; UNIQUE(event_id,channel) | commit 후 전달, 재시도 |

시간은 UTC timestamptz, 거래일은 NY DATE, 금액·수량은 numeric, 비중은 **ratio 0~1**, bps는 별도 접미사를 사용한다. qty>0, price>0, fee≥0, expiry>active_after, sequence≥1 등의 CHECK와 run/candidate/order의 일치 FK를 migration에 넣는다. quote는 불량 데이터도 quality_flags로 보존하되 체결 입력에서는 거절한다. JSONB에도 schema_version을 넣고 알려지지 않은 버전은 격리한다.

신규 주문 시 포트폴리오/arm 행을 잠그고 현금·기존 보유의 현재 평가액·미체결 예약을 합쳐 name 5%, sector 20%, gross 100%, modeled open risk 2%를 검사한다. 주문 intent+예약+event를 한 트랜잭션으로 저장한다. fill 처리도 event 중복 확인→수량/현금/수수료/예약/position 갱신→outbox를 원자적으로 커밋한다. 정정·부분 취소·수량 반올림을 포함한 현금 보존 invariant가 필요하다. gap 손실이 모델 위험액을 초과할 수 있으므로 `risk_dollars`를 최대 보장 손실이라고 부르지 않는다.

### JSONL 로깅 예시 및 운영 규칙

```json
{"schema_version":1,"mode":"SHADOW","execution_kind":"SIMULATED","run_id":"<uuid>","event_id":"<uuid>","candidate_id":"<uuid>","order_id":"<uuid>","stream_id":"C:AUDIT","sequence":7,"event_type":"ORDER_EXPIRED","source_at":"2026-09-08T16:30:00Z","received_at":"2026-09-08T16:30:00.080Z","processed_at":"2026-09-08T16:30:00.084Z","session_date":"2026-09-08","ticker":"AUDIT","arm":"C","reason_code":"NO_EXECUTABLE_QUOTE_BEFORE_EXPIRY","params_hash":"<sha256>","idempotency_key":"<stable-key>","trace_id":"<uuid>"}
```

이는 형식 예시이며 관측된 이벤트가 아니다. UTC wall clock과 별도로 monotonic clock으로 내부 처리 지연을 측정한다. 토큰·DB 비밀번호·인증 헤더를 로그에 넣지 않는다. event_id를 로그·DB·outbox에 공통 기록한다.

- 별도 `whykoff-shadow.service` 한 개가 Shadow 작업을 소유한다. 현 production daemon/cron과 스케줄을 중복 등록하지 않는다. worker 재시작은 checkpoint 이후 원본 이벤트를 순서대로 재생하며 중복 키는 상태 변화 없이 처리한다.
- 초기 관측안: RTH 30초 heartbeat, 90초 누락 알람, 주문 판단 quote age≤2초. 공급자가 이를 충족하지 못하면 더 느린 관측 모드로 명시하고 실행 가능 fill 판단을 중지한다. 이 값들은 공급자 SLA에 맞춰 사전 고정할 운영 후보다.
- p50/p95/p99 source→receive/receive→decision/decision→match 지연, quote stale·결측률, 봉 완성 지연, 후보수, 미체결/부분체결/취소율, ambiguity 비율, 중복 이벤트, reconciliation 차이, 최대 노출·예약 위반을 기록한다.
- 매일 NAV/잔고/수량 원장 대사와 heartbeat 보고서를 파일로 만든다. 실시간 quote 검증 비용, simulated shortfall, 실거래 fill shortfall을 같은 열에 합치지 않는다.
- 로컬 JSONL은 용량 제한·회전, 원본 이벤트/설정/hash는 최소 실험 전체 60일과 재검토 기간을 포괄하도록 보존한다. 제안: 일일 압축 archive 1년, quote 원본 90일 이상. 원격 버킷 전송은 계정·보관정책 설정 후 별도 배포 작업이다.
- Telegram은 `[SHADOW/모의]` 표기와 독립 채널을 사용한다. 이번 검토에서는 발송하지 않았다. outbox 외부 전달의 exactly-once를 가정하지 않으며 전달 ID·재시도 중복을 기록한다.

### 종료·인수 게이트

필수 전이: 미체결 만료, 부분 진입/취소, TP1 50%→stop 변경, TP2, 갭 SL, 20/40세션 만료, 동일 봉/동일 fill 중복, 재시작·commit 실패, 동시 두 주문 예약, stale quote, 휴일·DST·조기폐장. 자연 관측되지 않은 드문 이벤트는 fault injection/replay로 검증하고 실관측 수와 분리한다.

하루라도 cash/qty/NAV 대사 불일치나 cap 초과·미래 데이터 사용·모의/실제 혼동이 생기면 신규 intent를 중지하고 원본 수집·진단만 계속한다. 재생 결과 해시와 projection 일치, 중복의 순효과 0, 모든 필수 전이 통과가 운영 인수 조건이다. 통계 인수는 별도로 cost 표본·paired 순수익 CI 및 충분한 독립 세션이 필요하다. 30~60일 종료 시 표본이 부족하면 “관측 연장”이며 champion 승격하지 않는다.

## 6. 구현 순서와 이번 검증 결과

| 순서 | 구체적 구현 단위 | 완료 증거 |
|---|---|---|
| 1 | services에 as-of repository·event dedupe·트랜잭션, engine에 공통 순수 lifecycle, dry-run flag 초기화 | 동일 봉 재실행/시간 역행/동일 fill/commit rollback 테스트 |
| 2 | 후보·부분 fill·현금·NAV 원장, 실행/가격 정책 freeze | 40건 원거래 대조, 손익 합계/NAV 재현, costs 분리 |
| 3 | 시간봉 상태 보존·자격·RVOL·만료, risk 전체 cap·예약·단위 검증 | 미래 봉 append 불변, 확정 양성/음성 fixtures, 동시 주문 한도 |
| 4 | 공통 기간/PIT universe 기반 A–D replay service | candidate ledger, arm fills, daily NAV, paired 결과·coverage |
| 5 | P3 사전등록 및 장기 데이터 확장 | 후보 전체 run registry, purge 기록, untouched holdout |
| 6 | 별도 Shadow schema/worker/quote adapter/outbox | 제한 권한, 재시작 대사, telemetry, 30~60일 보고 |

`core/`는 모델/계약, `collectors/`는 원데이터 수집, `engine/`은 DB 없는 계산, `services/`는 조회·저장·예약·replay orchestration을 맡는다. 기존 `engine/backtester.py`의 DB orchestration은 이동 대상이다. 운영 배포나 실제 주문 연동은 이 설계서의 완료 결과가 아니다.

필수 명령 `python -m unittest discover tests` 실행 결과: **25개 발견, 22개 통과, DB 의존 3개 오류**. 프로세스의 DB_HOST=127.0.0.1, DB_PORT=1, 전용 감사 DB 이름으로 격리했다. 일부 기존 테스트가 DELETE 및 일반 OPEN 평가 경로를 사용하므로 운영 DB로 실행하지 않았다. 오류는 DB fixture 미제공에 따른 연결 거부이며, 전체 25개 통과를 확인했다고 보고하지 않는다. [전체 로그](audits/2026-09-08-followup/unittest_discover.log).

오프라인 재현은 **8건 모두 확인**했다: 부적격 일봉 트리거, 동일 거래량 ST, 붕괴 후 과거 ST 재사용, gross/risk/누적 name cap 미적용, SL>E 승인, 동일 봉 재평가 청산, 실패 게이트 dry-run flag 잔존, 거래연쇄 MDD와 NAV의 차이. 이는 정상 동작 테스트 통과가 아닌 **현재 결함을 입증하는 assertion 성공**이다.

재현 명령: `python audits/2026-09-08-followup/review_evidence.py`. DB 연결은 mock으로 명시 차단되며 [결과 JSON](audits/2026-09-08-followup/review_evidence.json)에 통계·관측값·검토 파일 hash를 남긴다. 원본 요청서 및 기존 운영 코드 수정본은 보존했다.
