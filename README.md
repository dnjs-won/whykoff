# Whykoff Stock System (와이코프 주식 분석 및 브리핑 시스템)

리처드 와이코프(Richard D. Wyckoff)의 매집(Accumulation) 이론 및 다중 타임프레임 컨플루언스(Confluence) 지표를 기반으로 바닥권 매집 완료 종목을 포착하고, 시장 거시 환경(Macro) 및 AI 뉴스 분석을 결합하여 텔레그램으로 자동 브리핑하는 퀀트/스윙 트레이딩 시스템입니다.

---

## 📁 디렉토리 구조 및 상태

```text
C:\project_k\whykoff\
├── core/                         # [완료] 공통 기반 레이어
│   ├── config.py                 # DB, Telegram, Gemini 설정 (기존 설정 100% 호환)
│   ├── database.py               # PostgreSQL 커넥션 풀, 컨텍스트 매니저, DDL 스키마
│   ├── logger.py                 # 표준 로거
│   └── models.py                 # 데이터 모델 (캔들, 시그널, 매크로, 뉴스)
├── collectors/                   # [대기] 외부 데이터 수집 파이프라인
│   ├── market_collector.py       # OHLCV 일봉/1시간봉 배치 수집
│   ├── macro_collector.py        # VIX, 채권금리, 달러지수 등 수집
│   └── news_collector.py         # 실시간 뉴스 수집
├── engine/                       # [대기] 분석 및 와이코프 알고리즘 엔진
│   ├── indicators.py             # RSI, MACD, MFI, OBV, ATR, Volume POC
│   ├── wyckoff_scanner.py        # 바닥 매집 완료 배거(Bagger) 스캐너
│   └── confluence_scanner.py    # 3-Track 다이버전스 & 모멘텀 스캐너
├── services/                     # [대기] 사용자 대면 서비스
│   ├── briefing_service.py       # 프리마켓 / 장마감 브리핑 생성기
│   ├── telegram_bot.py           # 텔레그램 대화형 봇 핸들러
│   ├── llm_evaluator.py          # Gemini AI 종합 리스크 평가 (Synthesizer)
│   ├── llm_service.py            # Gemini AI 뉴스 심층 분석기
│   └── scheduler.py              # 일일 주기적 실행 스케줄러
├── tests/                        # [대기] 단위 및 통합 테스트
├── agent.md                      # [핵심] AI 에이전트 개발 행동 강령 및 코딩 규칙
├── evaluator_agent.md            # [핵심] AI 종합 평가 에이전트(리스크 매니저) 가이드
├── ARCHITECTURE.md               # [설계] 시스템 아키텍처 다이어그램
├── CORE_LOGIC_SPECS.md           # [명세] 와이코프 매집 공식, POC 지표, 서브섹터 체계
├── schema.sql                    # [DB] PostgreSQL 통합 DDL 파일
└── README.md                     # [안내] 프로젝트 개요 및 로드맵
```

---

## 🚀 다음 구축 단계 (Next Steps)

새 프로젝트 창에서 AI와 함께 다음 순서대로 모듈을 1개씩 구현하고 단위 테스트를 거치며 조립해 나가면 됩니다.

1. **1단계: DB 스키마 확인 (`schema.sql` 기반)**
   - `python test.py` (기존 DB 연결 및 스모크 테스트)
   - `python -c "from core.database import init_database_tables; init_database_tables()"` 실행
2. **2단계: `engine/indicators.py` 및 `engine/wyckoff_scanner.py` 구현**
   - `CORE_LOGIC_SPECS.md`에 명시된 6단계 와이코프 매집 알고리즘을 순수 함수 형태로 구현
   - 서브섹터(SOXX, IGV 등) 유니버스 필터링 연동
3. **3단계: `services/llm_evaluator.py` 구현 (AI 종합 평가)**
   - `evaluator_agent.md`를 바탕으로 스캔 결과 + 서브섹터 + 매크로 + 뉴스를 융합하는 리스크 매니저 봇 완성
4. **4단계: `collectors/` 및 `services/briefing_service.py`, `telegram_bot.py` 조립**
   - 기존 봇 토큰 및 Chat ID를 연동하여 시그널 알림 및 브리핑 발송
