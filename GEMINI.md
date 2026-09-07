# Antigravity & Gemini AI Development Rules for Whykoff

> **CRITICAL MANDATORY STARTUP INSTRUCTION:**  
> When starting any conversation, task, or maintenance in this repository, you MUST immediately read and align with the following core documents before planning or executing any changes:
> 1. [`PROJECT_STATUS.md`](file:///C:/project_k/whykoff/PROJECT_STATUS.md): System state, production GCP setup, current Champion Strategy (`wyckoff_v2.0_full_swing`), benchmark metrics, and handover context.
> 2. [`CORE_LOGIC_SPECS.md`](file:///C:/project_k/whykoff/CORE_LOGIC_SPECS.md): Wyckoff 6-step accumulation math, 5-Star LPS Sweet Spot (68~78 pts), POC volume support, SL/TP rules.
> 3. [`agent.md`](file:///C:/project_k/whykoff/agent.md) & [`ARCHITECTURE.md`](file:///C:/project_k/whykoff/ARCHITECTURE.md): Strict layered architecture (core, collectors, engine, services) and governance.

---

## 1. System Identity & Current Production State
- **Project:** Whykoff Stock System (와이코프 매집 퀀트 스윙 트레이딩 & 텔레그램 데몬)
- **Champion Strategy:** `wyckoff_v2.0_full_swing`
  - Universe: 303 US Stocks (14 Granular Subsectors: Semi, Optical, Crypto, Quantum, Cyber, AI, etc.)
  - Metrics: 597 Trades, Win Rate **50.75%**, Profit Factor **1.40**, Expectancy **+1.58% / trade**
  - Hold Time: Avg 15.2 trading days (~3 weeks)
- **Database:** PostgreSQL (`stock_db`) with ~350,000 OHLCV daily & 1h candles
- **Production Server:** GCP Linux (`wonjungo546@gcp-machine:~/whykoff`), running `systemd` service `whykoff.service` and Crontab jobs.

---

## 2. Mandatory Coding & Architecture Rules

1. **Top-Down 2-Step Score Separation (Never Violate):**
   - Sector capital inflow/outflow is tracked separately via ETF dollar volume shares (e.g. `SOXX`, `IYZ`, `WGMI`, `QTUM`).
   - **DO NOT** add sector bonus points directly into individual stock technical scores. Stock charts must stand on their own Wyckoff merit (68~78 pts Sweet Spot).
2. **LPS Sweet Spot (5-Star) vs Overextended (2-Star):**
   - Stocks breaking out of accumulation with score 68~78 pts and POC volume support are 5-Star LPS sweet spots.
   - Stocks with score $\ge 85$ pts are **Overextended (추격매수 금지 2-Star 경고)**.
3. **Wick Shakeout Defense:**
   - Stop Loss always includes a 2.5% buffer (`sl_buffer_pct = 0.025`) below the box low to protect against institutional wick shakeouts.
4. **Idempotence & Resource Safety:**
   - Always use `get_db_cursor()` context manager in `core/database.py` to prevent PostgreSQL connection leaks.
   - All batch collections use `ON CONFLICT DO UPDATE`.
5. **Quality Verification Gate:**
   - After ANY code edit, you MUST execute `python -m unittest discover tests` and verify that all tests pass before completing your response.
   - No mock/dummy/hardcoded values in production code paths.
