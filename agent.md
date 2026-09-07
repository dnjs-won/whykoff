# AI Agent Development Guidelines (Whykoff Project)

이 문서는 **Whykoff (주식 와이코프 매집 분석 및 자동 브리핑 시스템)** 프로젝트를 개발하고 유지보수할 때 AI 어시스턴트와 개발자가 반드시 따라야 하는 절대 원칙입니다.

---

## 1. 프로젝트 정체성 및 핵심 철학

- **도메인 목적**: 리처드 와이코프(Richard D. Wyckoff)의 축적(Accumulation) 이론 및 다중 타임프레임 컨플루언스(Confluence) 지표를 바탕으로 바닥권 매집 완료 종목을 포착하고, 시장 거시 환경(Macro) 및 AI 뉴스 분석을 결합하여 텔레그램으로 자동 브리핑하는 퀀트/스윙 트레이딩 시스템.
- **설계 철학**:
  1. **단순성과 명확성 (KISS)**: 불필요한 추상화나 과도한 단발성 스크립트 생성을 엄격히 금지하고, 명확한 레이어드 아키텍처를 유지한다.
  2. **재현 가능성과 신뢰성**: 모든 시그널 연산은 완성봉 기준을 원칙으로 하며 지표 리페인팅(Repainting)을 원천 차단한다.
  3. **단일 책임 원칙 (SRP)**: 수집, 분석/엔진, 알림/서비스의 책임을 철저히 분리한다.

---

## 2. 디렉토리 구조 및 역할 규격

프로젝트 루트에 임의의 테스트 스크립트를 마구잡이로 생성하지 마십시오. 모든 코드는 정해진 패키지 안에 위치해야 합니다.

```text
C:\project_k\whykoff\
├── core/                  # 공통 기반 (DB 연결, 로깅, 모델, 설정)
│   ├── config.py          # 환경변수 및 기본 설정
│   ├── database.py        # PostgreSQL 커넥션 풀, 컨텍스트 매니저, DDL
│   ├── logger.py          # 표준 로거 설정
│   └── models.py          # 표준 데이터 모델 (Pydantic / Dataclass)
├── collectors/            # 외부 데이터 수집 (야후 파이낸스, 매크로, 뉴스)
│   ├── market_collector.py# OHLCV 일봉/1시간봉 수집
│   ├── macro_collector.py # VIX, 금리, 환율, 원자재 등 거시지표 수집
│   └── news_collector.py  # 종목별 최근 뉴스 수집
├── engine/                # 순수 분석, 지표 계산, 와이코프 스캐너 엔진
│   ├── indicators.py      # RSI, MACD, MFI, OBV, POC, ATR 연산
│   ├── wyckoff_scanner.py # 바닥 매집(Accumulation Bagger) 핵심 스캐너
│   └── confluence_scanner.py # 3-Track 다이버전스 & 모멘텀 스캐너
├── services/              # 알림, 브리핑, 텔레그램 봇, 스케줄러
│   ├── briefing_service.py# 프리마켓 / 장마감 리포트 조립
│   ├── telegram_bot.py    # 텔레그램 대화형 봇 핸들러
│   ├── llm_evaluator.py   # Gemini AI 종합 리스크 평가 (Synthesizer)
│   ├── llm_service.py     # Gemini API 뉴스 심층 요약
│   └── scheduler.py       # 주기적 배치 실행 관리
├── tests/                 # 단위 및 기능 테스트
├── agent.md               # [현재 파일] AI 개발 가이드라인
├── evaluator_agent.md     # AI 종합 평가 에이전트(리스크 매니저) 지침서
├── ARCHITECTURE.md        # 아키텍처 및 파이프라인 설계서
├── CORE_LOGIC_SPECS.md    # 와이코프 매집 및 서브섹터 스캐닝 명세서
├── schema.sql             # PostgreSQL 통합 DDL 파일
└── main.py                # 시스템 통합 실행 진입점
```

---

## 3. 레이어별 엄격한 코딩 규칙

### [A. `core/` (기반 레이어)]
- 비즈니스 로직(지표 계산, 매매 전략)을 포함하지 않는다.
- DB 연결은 반드시 `core/database.py`의 `get_db_cursor()` 또는 컨텍스트 매니저를 통해 관리하고, 작업 후 커넥션 누수가 없도록 보장한다.
- 설정값은 `core/config.py`의 `settings` 객체로부터만 참조하며, 코드 내에 토큰/비밀번호/IP를 하드코딩하지 않는다.

### [B. `collectors/` (수집 레이어)]
- 외부 API(yfinance, web 등)로부터 데이터를 수집하여 표준 데이터프레임/모델로 정제한 뒤 DB에 안전하게 Upsert(ON CONFLICT) 저장한다.
- 수집기 내에 매수/매도 판별이나 지표 스코어링 같은 분석 로직을 섞지 않는다.
- 예외 발생 시 전체 프로세스가 중단되지 않도록 종목별/청크별 try-except 격리를 적용한다.

### [C. `engine/` (분석 및 와이코프 엔진 레이어)]
- **순수 함수 지향**: 가급적 DB에 직접 쿼리하지 않고, 입력으로 `pd.DataFrame` 또는 도메인 모델을 받아 연산 결과를 반환한다.
- **백테스트 게이트 필수**: 지표 가중치나 기준값을 변경할 때 임의로 코드를 수정하지 말 것. 반드시 `engine/backtester.py`를 통해 승률 55%+, 손익비 2.0+, 기대값 우위가 검증된 Challenger 모델만 승격(Champion)시킨다.
- **지표 점수 vs 스윗스팟 별점(⭐) 2원화**: 단순 기술점수 외에 68~78점 바닥 매집 완료도(스윗스팟 5성)와 85점 이상 과열 추격 경고(2성)를 반드시 함께 산출한다.
- 지표 계산 시 `iloc[-1]`(진행 중인 봉)과 `iloc[-2]`(직전 완성봉)의 구분을 명확히 표기하여 리페인팅을 원천 차단한다.

### [D. `services/` (서비스 및 사용자 대면 레이어)]
- **스냅샷과 포지션 추적의 철저한 분리**:
  - 일일 스캔 결과는 `scan_snapshots`에 무조건 기록하여 점수 빌드업 히스토리를 보존한다.
  - 실제 매매 정산은 `active_trades` 테이블을 통해 관리하며, 이미 `status = 'OPEN'`인 종목은 신규 포지션을 중복 생성하지 않고 기존 포지션을 업데이트한다.
- 텔레그램 메시지 포맷팅 시 HTML 태그는 `<b>`, `<i>`, `<code>`, `<pre>`만 허용되며, `<br>`, `<p>` 및 파싱 에러를 유발하는 특수문자 처리를 전담한다.
- Gemini LLM 호출은 `services/llm_evaluator.py` 및 `services/llm_service.py`를 통해서만 수행하며, `evaluator_agent.md`의 프롬프트 규격을 엄격히 준수한다.

---

## 4. 코드 스타일 및 품질 표준

1. **Python 3.10+ 표준 준수**: `int | None`, `tuple[pd.DataFrame, pd.DataFrame]` 등 최신 타입 힌트를 명시한다.
2. **명시적 로깅**: `print()` 대신 표준 `logging.getLogger(__name__)`을 사용하며, [INFO], [WARNING], [ERROR] 레벨을 적절히 구분한다.
3. **단발성 파일 생성 금지**: 테스트 코드는 루트에 `test_xxx.py`로 만들지 말고 반드시 `tests/test_xxx.py`에 생성하거나 `pytest` 규격을 따른다.
4. **결측치 및 0값 방어**: 주가 데이터(OHLCV) 처리 시 0원, 음수, NaN, 빈 데이터프레임에 대한 가드 클로즈(Guard Clause)를 항상 최우선 배치한다.
