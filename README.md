# Whykoff Stock System (와이코프 퀀트 스윙 및 브리핑 시스템)

리처드 와이코프(Richard D. Wyckoff)의 매집(Accumulation) 이론 및 볼륨 프로파일(POC) 지표를 기반으로 바닥권 매집 완료 종목을 포착하고, 시장 거시 환경(Macro) 및 AI 뉴스 분석을 결합하여 텔레그램으로 자동 브리핑하는 프로덕션 퀀트 트레이딩 시스템입니다.

---

## 📁 디렉토리 구조 및 모듈 현황

```text
C:\project_k\whykoff\
├── core/                         # [완료] 공통 기반 레이어
│   ├── config.py                 # DB, Telegram, Gemini 설정
│   ├── database.py               # PostgreSQL 커넥션 풀, DDL 스키마, 캔들 로더
│   ├── logger.py                 # 일자/레벨 표준 로거
│   ├── models.py                 # 캔들, 시그널, 스냅샷, 벤치마크 데이터 클래스
│   └── migrate_subsectors.py     # 11대 섹터 및 세부 테마(반도체, 광통신, 크립토 등) 분류기
├── collectors/                   # [완료] 외부 데이터 수집 파이프라인
│   ├── market_collector.py       # OHLCV 일봉/1시간봉 배치 수집 (Yahoo Finance)
│   ├── macro_collector.py        # VIX, 금리, 환율 및 19대 섹터/서브섹터 자금 점유율 추적
│   ├── news_collector.py         # 주요 종목 최신 뉴스 RSS 수집
│   └── run_collectors.py         # 수집기 통합 CLI 실행기 (1h, daily, macro, news, all)
├── engine/                       # [완료] 분석 및 퀀트 백테스팅 엔진
│   ├── indicators.py             # RSI, MACD, MFI, OBV, ATR, Volume Profile POC
│   ├── wyckoff_scanner.py        # 와이코프 6단계 매집 및 5성 스윗스팟(LPS) 듀얼 평가 엔진
│   └── backtester.py             # 1년 시계열 벤치마크 검증 게이트 & 챔피언-챌린저 승격기
├── services/                     # [완료] 서비스 및 자동화 파이프라인
│   ├── briefing_service.py       # 장마감 종합 브리핑 조립 (매크로+수급+스윗스팟+성과)
│   ├── telegram_bot.py           # 텔레그램 실시간 대화형 봇 (/scan, /portfolio, /briefing)
│   ├── llm_evaluator.py          # Gemini AI 종합 리스크 평가 에이전트 (CRO Synthesizer)
│   ├── trade_tracker.py          # 포지션 라이프사이클 추적 및 조기경보 엔진
│   └── scheduler.py              # 장마감 정기 배치 파이프라인
├── scripts/                      # [완료] 운영 유틸리티 스크립트
│   └── set_bot_commands.py       # 텔레그램 봇 메뉴 커맨드 등록기 (setMyCommands API)
├── tests/                        # [완료] 단위 및 통합 테스트 (pytest 7/7 Passed)
├── ARCHITECTURE.md               # [설계] 시스템 전체 아키텍처 다이어그램
├── CORE_LOGIC_SPECS.md           # [명세] 와이코프 공식 및 파라미터 규격서
├── schema.sql                    # [DB] PostgreSQL 통합 DDL 파일
└── main.py                       # [엔트리] 통합 CLI 및 데몬 실행 진입점
```

---

## 🚀 CLI 실행 및 주요 커맨드

```bash
# 1. 텔레그램 봇 데몬 실행 (systemd 연동)
python main.py --daemon

# 2. 장마감 종합 브리핑 1회 생성 및 텔레그램 발송
python main.py --briefing

# 3. 데이터 수집 (전체 / 1시간봉 / 일봉 / 매크로)
python main.py --collect all      # 전체 수집
python main.py --collect 1h       # 1시간봉만 수집
python main.py --collect daily    # 일봉만 수집
python main.py --collect macro    # 거시지표 & 19개 섹터 점유율 수집

# 4. 와이코프 매집 스캔
python main.py --scan ALL --telegram       # 전체 시장 스캔 후 텔레그램 전송
python main.py --scan OPTICAL --telegram   # 광통신 테마만 스캔 후 전송
python main.py --scan CRYPTO --telegram    # 암호화폐/채굴 테마만 스캔 후 전송
python main.py --scan QUANTUM --telegram   # 양자컴퓨터 테마만 스캔 후 전송
python main.py --scan SEMI --telegram      # 반도체 테마만 스캔 후 전송
```

---

## ⏰ Crontab 스케줄 가이드

```cron
# 매시간 정각: 1시간봉 수집
0 * * * * /home/wonjungo546/whykoff/venv/bin/python /home/wonjungo546/whykoff/main.py --collect 1h >> /home/wonjungo546/whykoff/collector.log 2>&1

# 화~토 아침 07:30: 일봉 수집
30 7 * * 2-6 /home/wonjungo546/whykoff/venv/bin/python /home/wonjungo546/whykoff/main.py --collect daily >> /home/wonjungo546/whykoff/collector.log 2>&1

# 화~토 아침 07:32: 매크로 & 섹터 수급 수집
32 7 * * 2-6 /home/wonjungo546/whykoff/venv/bin/python /home/wonjungo546/whykoff/main.py --collect macro >> /home/wonjungo546/whykoff/collector.log 2>&1

# 화~토 아침 07:45: 장마감 종합 브리핑 발송
45 7 * * 2-6 /home/wonjungo546/whykoff/venv/bin/python /home/wonjungo546/whykoff/main.py --briefing >> /home/wonjungo546/whykoff/briefing.log 2>&1

# 월~금 밤 22:05: 개장 전 와이코프 매집 스캔 발송
05 22 * * 1-5 /home/wonjungo546/whykoff/venv/bin/python /home/wonjungo546/whykoff/main.py --scan ALL --telegram >> /home/wonjungo546/whykoff/scanner.log 2>&1
```
