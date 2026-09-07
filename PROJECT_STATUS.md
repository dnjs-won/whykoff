# Whykoff Stock System - Project Status & Maintenance Handover Guide
**Last Updated:** 2026-09-08 (Production Ready)  
**Author/Pair:** Antigravity AI & wonjungo546  
**Repository:** `C:\project_k\whykoff` (Branch: `main`)  
**Remote Host:** GCP Linux Server (`wonjungo546@gcp-machine:~/whykoff`)

---

## 1. 프로젝트 개요 및 현재 상태

본 시스템은 리처드 와이코프(Richard D. Wyckoff) 매집(Accumulation) 이론 및 볼륨 프로파일(POC) 지지선 기반의 퀀트 스윙 트레이딩 및 일일 자동 브리핑 시스템입니다.

- **현재 상태:** ✅ **Production Ready (배포 및 전수 검증 완료)**
- **단위 테스트:** 7/7 Test Suites Passed (100% 정상)
- **DB 데이터 무결성:**
  - `ohlcv_daily`: 306개 종목, 165,000+ 레코드
  - `ohlcv_1h`: 304개 종목, 184,000+ 레코드
  - `macro_indicators` & `sector_liquidity_shares`: 19대 섹터/서브섹터 수급 추적 정상

---

## 2. 챔피언 전략 명세 (`wyckoff_v2.0_full_swing`)

303개 전 종목 유니버스 대상 1년 시계열 전수 백테스트를 통과하고 승격된 공식 챔피언 전략입니다.

| 지표 항목 | 벤치마크 기준 | 백테스트 실측 성과 | 판정 |
| :--- | :--- | :--- | :--- |
| **샘플 거래 수** | $\ge 50$건 | **597건** | 통과 (통계적 신뢰도 확보) |
| **승률 (Win Rate)** | $\ge 50.0\%$ | **50.75%** | 통과 |
| **손익비 (Profit Factor)** | $\ge 1.30$ | **1.40** | 통과 |
| **기대값 (Expectancy)** | $> 0.0\%$ | **+1.58% / 거래** | 통과 |
| **평균 보유일 (Holding)** | 20영업일 이내 | **15.2영업일 (~3주)** | 적정 스윙 사이클 |

### 핵심 매매 규칙 및 파라미터 Rationale
1. **낙폭 및 박스권 필터:** 120일 최고가 대비 낙폭 $\le -25\%$, 30일 박스권 진폭 $\le 20\%$ 종목만 대상.
2. **볼륨 지지선(POC):** 120일 볼륨 프로파일 최대 매물대(POC) 위 $0\% \sim +7\%$ 안착 확인.
3. **5성 LPS 스윗스팟 (68~78점):**
   - 85점 이상 초과열 종목은 추격매수 금지(2-Star 경고).
   - 바닥을 다지고 첫 매집 돌파를 시작하는 68~78점 구간을 5성 최고 타점으로 평가.
4. **손절 버퍼 (`sl_buffer_pct = 0.025`):**
   - 단순 박스 최저점 손절 시 발생하는 휩소(Shakeout 꼬리) 방지를 위해 2.5% 버퍼 적용.
5. **목표가 설정:**
   - 1차 익절(TP1): $+20\%$ (또는 손익비 $\ge 2.5:1$)
   - 2차 익절(TP2): $+50\%$
   - 최대 보유 기간: 20영업일 타임아웃 청산

---

## 3. 세부 테마 및 14대 서브섹터 분류 체계

`core/migrate_subsectors.py`를 통해 316개 티커를 14개 고유 서브섹터로 정밀 분류 완료:

| 서브섹터 코드 | 대표 ETF / 지표 | 주요 편입 종목 (총 316개) |
| :--- | :--- | :--- |
| `SEMICONDUCTOR` | `SOXX`, `SMH` | NVDA, TSM, ASML, AVGO, AMD, QCOM, MU 등 |
| `OPTICAL` | `IYZ` | AAOI, LITE, COHR, FN, CIEN, LUMN 등 |
| `CRYPTO` | `WGMI` | MSTR, MARA, CLSK, RIOT, COIN, CIFR 등 |
| `QUANTUM` | `QTUM` | IONQ, RGTI, QBTS, QUBT 등 |
| `CYBERSECURITY` | `CIBR` | CRWD, PANW, FTNT, ZS, NET, S, TENB 등 |
| `AI_SOFTWARE` | `IGV` | PLTR, CRM, SNOW, NOW, PATH, ADBE 등 |
| `AI_HARDWARE` | `BOTZ` | VRT, SMCI, ANET, DELL, HPE 등 |
| `NUCLEAR` | `URA` | CCJ, OKLO, SMR, NNE, CEG, VST, TLN 등 |
| `DEFENSE` | `XLI` / `ITA` | LMT, RTX, NOC, GD, KTOS, RKLB, ASTS 등 |
| `BIOTECH` | `XBI` | ARGX, BIIB, VRTX, REGN, ALNY, MRNA 등 |
| `FINTECH` | `XLF` / `FINX` | PYPL, SQ, SOFI, HOOD, AFRM, UPST 등 |
| `EV` | `XLY` / `IDRV` | TSLA, RIVN, LCID, NIO, XPEV 등 |
| `LITHIUM` | `XLB` / `LIT` | ALB, SQM, LTHM, LAC, PLL 등 |
| `DATA_CENTER` | `XLRE` | EQIX, DLR, AMT, CCI 등 |

---

## 4. 운영 환경 (GCP Production) 및 명령어 레퍼런스

### 4.1 리눅스 서비스 (systemd)
- 서비스 파일: `/etc/systemd/system/whykoff.service`
- 실행 계정: `wonjungo546`
- 실행 명령: `/home/wonjungo546/whykoff/venv/bin/python main.py --daemon`
- 관리 명령어:
  ```bash
  sudo systemctl status whykoff
  sudo systemctl restart whykoff
  journalctl -u whykoff -f
  ```

### 4.2 크론탭 (Crontab) 스케줄
```cron
# 매시간 정각: 1시간봉 수집
0 * * * * /home/wonjungo546/whykoff/venv/bin/python /home/wonjungo546/whykoff/main.py --collect 1h >> /home/wonjungo546/whykoff/collector.log 2>&1

# 화~토 아침 07:30: 일봉 수집
30 7 * * 2-6 /home/wonjungo546/whykoff/venv/bin/python /home/wonjungo546/whykoff/main.py --collect daily >> /home/wonjungo546/whykoff/collector.log 2>&1

# 화~토 아침 07:32: 매크로 & 19대 섹터 자금유입 점유율 수집
32 7 * * 2-6 /home/wonjungo546/whykoff/venv/bin/python /home/wonjungo546/whykoff/main.py --collect macro >> /home/wonjungo546/whykoff/collector.log 2>&1

# 화~토 아침 07:45: 장후마감 종합 브리핑 텔레그램 발송
45 7 * * 2-6 /home/wonjungo546/whykoff/venv/bin/python /home/wonjungo546/whykoff/main.py --briefing >> /home/wonjungo546/whykoff/briefing.log 2>&1

# 월~금 밤 22:05: 미국 개장 전 와이코프 매집 스캔 발송
05 22 * * 1-5 /home/wonjungo546/whykoff/venv/bin/python /home/wonjungo546/whykoff/main.py --scan ALL --telegram >> /home/wonjungo546/whykoff/scanner.log 2>&1
```

### 4.3 텔레그램 봇 메뉴 커맨드
텔레그램 BotFather 및 `setMyCommands` API로 자동 등록됨:
- `/scan`: 전체 시장 와이코프 매집 스캔 (또는 `/scan OPTICAL`, `/scan CRYPTO` 등)
- `/portfolio`: 현재 보유 포지션 수익률 및 POC 지지 이탈 조기경보 현황
- `/briefing`: 오늘 장마감 종합 브리핑 즉시 조회
- `/help`: 사용 가이드 및 서브섹터 목록

---

## 5. 미래 AI 세션 및 유지보수자를 위한 인수인계 팁

새로운 AI 세션이나 다른 개발자가 유지보수를 시작할 때, 다음 지침을 숙지하십시오:

1. **컨텍스트 복원 방법:**
   - 대화 세션이 새로 열리더라도 이 문서(`PROJECT_STATUS.md`)와 `CORE_LOGIC_SPECS.md`, `README.md`를 먼저 읽으면 현재 시스템의 모든 설계 결정과 상태를 100% 파악할 수 있습니다.
2. **코드 변경 시 필수 절차:**
   - 임의의 로직 수정 후 반드시 `python -m unittest discover tests`를 실행하여 7개 테스트 케이스의 통과 여부를 검증하십시오.
3. **새로운 종목 추가 시:**
   - `tickers` 테이블에 INSERT 후, `python -c "from collectors.market_collector import collect_single_ticker_history; collect_single_ticker_history('TICKER', '2y')"`를 실행하여 초기 500봉을 적재하십시오.
4. **전략 튜닝 및 게이트 검증 시:**
   - 파라미터 수정 후 `python -c "from engine.backtester import evaluate_strategy_gate; evaluate_strategy_gate()"`를 실행하여 기존 챔피언(`wyckoff_v2.0_full_swing`)의 기대값(+1.58%)을 넘어서는지 확인하십시오.
