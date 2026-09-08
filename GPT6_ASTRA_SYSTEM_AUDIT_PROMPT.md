# Whykoff Institutional Quant Strategy & Architecture Audit Request
> **To:** GPT-6 ASTRA (Senior Lead Quantitative Researcher & Trading Systems Architect)  
> **From:** Whykoff Development & Engineering Team  
> **Date:** September 2026  
> **Classification:** Quantitative Strategy Audit & Math/Engineering Peer Review

---

## 1. Executive Summary & Strategy Context

You are requested to conduct an exhaustive institutional-grade mathematical and architectural audit of **Whykoff**—a production swing trading system operating in the US Equity Market ($303$ tickers across $14$ granular subsectors including Semiconductor, Enterprise SaaS, Crypto Mining/Ecosystem, Biotech, Nuclear/Power, Cyber Security, etc.).

The system automates the quantitative detection of **Wyckoff Accumulation Phase C/D (Last Point of Support - LPS)**, calculates entry/exit levels, manages trade state machines with partial take-profits, and broadcasts real-time execution signals via Telegram.

### 1.1 Production Champion Benchmark (`wyckoff_v2.0_full_swing`)
- **Universe:** 303 US Mid-to-Large Cap Equities (High liquidity filtered, $V \ge 500\text{k}$)
- **Time Horizon:** Swing Trading (Mean holding period: **15.2 trading days**, approx. 3 weeks)
- **Sample Size:** 597 closed trades (1-year rolling out-of-sample walk)
- **Win Rate:** **50.75%**
- **Profit Factor (PF):** **1.40**
- **Expected Return per Trade (Expectancy):** **+1.58%**
- **Data Layers:** 
  - `ohlcv_daily`: 165,000+ daily candles (active scanning baseline)
  - `ohlcv_1h`: 184,000+ 1-hour candles (infrastructure ready, uncoupled from scanner)

---

## 2. Quantitative Engine & Math Specifications

### 2.1 Top-Down 2-Step Architecture Principle
- **Rule:** Sector capital inflow/outflow (measured via ETF dollar-volume shares: `SOXX`, `IGV`, `WGMI`, `CIBR`, etc.) is strictly separated from individual stock technical scores.
- **Invariant:** **No sector bonus points may be added to individual stock scores.** Stocks must stand on their own idiosyncratic Wyckoff structural merit.

### 2.2 Wyckoff Accumulation Scoring Model (0 ~ 100 pts)
A stock is evaluated over a 120-day lookback window through a 6-step sequential pipeline:

$$\text{Total Score} = S_{\text{box}} + S_{\text{ma20}} + S_{\text{poc}} + S_{\text{mfi}} + S_{\text{obv}} + S_{\text{rsi}} + S_{\text{trigger}} + S_{\text{macd}}$$

1. **Long-Term Markdown Filter:**
   $$\text{Drop Rate} = \frac{P_{\text{current}} - \max(P_{\text{high}, 120d})}{\max(P_{\text{high}, 120d})} \times 100 \le -25.0\%$$
   *(Disqualified immediately if drop $< 25\%$ to prevent buying shallow pullbacks).*

2. **30-Day Base Volatility Compression ($S_{\text{box}} \le 20\text{ pts}$):**
   $$\text{Box Range} = \frac{\max(P_{\text{high}, 30d}) - \min(P_{\text{low}, 30d})}{\min(P_{\text{low}, 30d})} \times 100 \le 20.0\% \implies +20\text{ pts}$$
   *(Hard filter: $> 20\%$ immediately disqualified).*

3. **20-Day Moving Average Slope Flatness ($S_{\text{ma20}} \le 15\text{ pts}$):**
   $$\text{Slope}_{\text{MA20}} = \frac{\text{MA20}_{t} - \text{MA20}_{t-10}}{\text{MA20}_{t-10}} \times 100 \in [-2.0\%, +2.5\%] \implies +15\text{ pts}$$
   *(Protects against falling knives).*

4. **120-Day Point of Control (POC) Volume Shelf ($S_{\text{poc}} \le 25\text{ pts}$):**
   - 120-day price range divided into 40 equal histogram bins weighted by volume.
   - Price must sit within support threshold:
     $$P_{\text{current}} \ge \text{POC} \times 0.985 \quad \text{and} \quad \frac{P_{\text{current}} - \text{POC}}{\text{POC}} \le +7.0\% \implies +25\text{ pts}$$

5. **Smart Money Flow Divergence ($S_{\text{flow}} \le 40\text{ pts}$):**
   - $\text{MFI}(14) \ge 45.0 \text{ and } \text{MFI}_{t} > \text{MFI}_{t-15} \implies +15\text{ pts}$
   - $\text{OBV} > \text{SMA}(\text{OBV}, 10) \implies +15\text{ pts}$
   - $42.0 \le \text{RSI}(14) \le 62.0 \implies +10\text{ pts}$

6. **Price & Trend Momentum Triggers ($S_{\text{trigger}} \le 15\text{ pts}$):**
   - $P_{\text{current}} \ge \text{MA5} \text{ and } P_{\text{current}} \ge \text{MA20} \implies +10\text{ pts}$
   - $\text{MACD Histogram} > 0 \text{ or increasing} \implies +5\text{ pts}$

### 2.3 The 5-Star LPS Sweet Spot vs. 2-Star Overextension Dichotomy
The scoring system rejects monotonic linearity ("higher score $\ne$ better trade"):
- **Base Accumulation Skeleton = 60 pts** ($S_{\text{box}} + S_{\text{ma20}} + S_{\text{poc}}$).
- **⭐⭐⭐⭐⭐ 5-Star LPS Sweet Spot ($68 \sim 78\text{ pts}$):**
  - Base skeleton + 1~2 early money flow confirmations.
  - Represents Wyckoff Phase C/D: institutional accumulation complete, public retail volume absent, price hugging the box floor.
  - **Risk/Reward ratio is maximized (1:3 to 1:5)** because Stop Loss distance is minimal ($2 \sim 3\%$).
- **⭐⭐ 2-Star Overextended Warning ($\ge 85\text{ pts}$):**
  - All indicators maxed out; price has already surged $10 \sim 25\%$ off the low.
  - Stop Loss distance explodes to $-12 \sim -18\%$, breaking the R:R structure. **Strictly flagged as "Do Not Chase".**

### 2.4 Overhead Space Gate (Ichimoku Cloud Clearance)
To prevent buying directly beneath major multi-month institutional resistance:
- Calculated via Ichimoku Kinko Hyo: Tenkan 9, Kijun 26, Senkou Span A/B shifted 26 periods ahead.
- $\text{Cloud Top} = \max(\text{Senkou A}, \text{Senkou B})$
- If $P_{\text{current}} < \text{Cloud Top}$:
  $$\text{Overhead Space} = \frac{\text{Cloud Top} - P_{\text{current}}}{P_{\text{current}}} \times 100$$
  If $\text{Overhead Space} < +5.0\%$, the setup is **rejected/disqualified** due to unfavorable risk/reward buffer before heavy supply overhead.

### 2.5 Two-Stage Trade Lifecycle & Execution State Machine
- **Entry Price ($E$):** Day $t$ close upon signal generation.
- **Stop Loss ($SL$):** Box Low with institutional wick buffer:
  $$SL = \text{Box Low}_{30d} \times (1 - 0.025)$$
- **Target 1 ($TP1$):** $+20.0\%$ (Initial markup ceiling).
- **Target 2 ($TP2$):** $+50.0\%$ (Full bagger trend ride).
- **Default Timeout:** 20 trading days.
- **Two-Stage Partial Exit Mechanism:**
  1. If $P_{\text{high}} \ge TP1$:
     - **Partial Exit:** 50% position closed at $+20.0\%$.
     - **Breakeven Adjustment:** $SL$ raised to $E \times 1.005$ (+0.5% profit buffer to eliminate downside risk).
     - **Dynamic Timeout Extension:** Holding limit extended from $20\text{ days} \to 40\text{ days}$ (**Free-Ride Mode**).
  2. If $P_{\text{high}} \ge TP2$: Remaining 50% closed at $+50.0\%$.
  3. If $P_{\text{low}} \le SL$: Position closed at stop loss.
  4. If $t > \text{Max Days}$: Position closed at market close (timeout).

---

## 3. Targeted Audit Questionnaire for GPT-6 ASTRA

As Senior Lead Quantitative Researcher, please deliver a rigorous, uncompromising critique across the following four audit pillars:

### Pillar 1: Statistical Robustness & Overfitting Detection
1. **Sweet Spot Interval ($68 \sim 78\text{ pts}$):** Does this specific point band exhibit curve-fitting or data snooping bias over the 2024–2026 US tech-heavy swing market? How would you mathematically parameterize this threshold dynamically based on rolling market regime volatility (e.g., ATR percentile or VIX regime)?
2. **Fixed Thresholds:** The system uses fixed constants: $\text{Drop} \ge 25\%$, $\text{Box Range} \le 20\%$, $\text{Overhead Gate} \ge 5\%$, $SL \text{ buffer} = 2.5\%$. Which of these are most susceptible to parameter fragility? What dynamic adaptations (e.g., standard deviation / ATR multiples) would harden them against regime shifts?

### Pillar 2: Multi-Timeframe Integration (Daily + 1H Candles)
1. The system currently stores $184,000+$ 1-hour candles (`ohlcv_1h`) across all 303 tickers, but the scanner runs strictly on daily candles (`ohlcv_daily`).
2. **Algorithmic MTF Blueprint:** How should we mathematically couple `ohlcv_1h` into the execution layer?
   - Can we use 1-hour Volume Profile / Spring / Wyckoff Secondary Test (ST) confirmations to reduce entry slippage and narrow the initial stop loss without getting whipsawed?
   - Please provide a concrete, programmatic logic flow for a 2-Tier Daily-Macro + Hourly-Trigger model.

### Pillar 3: Execution Friction, Gap Risk, & Breakeven Slippage
1. **Free-Ride Gap Risk:** Upon reaching $TP1$, the system raises $SL$ to $E \times 1.005$. In real-world overnight sessions (e.g., negative earnings surprises, geopolitical macro shocks), stocks can gap down $-10\%$ through the $E \times 1.005$ barrier.
   - How should our position sizing and post-$TP1$ tracking mathematically account for overnight gap probabilities and execution slippage?
2. **Liquidity Friction:** Across 303 mid-cap tickers, how does market impact affect the $+1.58\%$ per-trade expectancy? What turnover or bid-ask spread filters should be enforced?

### Pillar 4: Portfolio Correlation & Dynamic Capital Allocation
1. **Sector Co-movement Clustered Signals:** During sudden market-wide sector rotations (e.g., semiconductor or crypto relief rallies), $10 \sim 15$ tickers in the same subsector may trigger 5-Star LPS Sweet Spot ratings on the same day.
   - What correlation penalty or subsector allocation cap formula should be integrated to prevent portfolio variance explosion?
2. **Position Sizing:** The current backtester assumes fixed unit bet sizing. What dynamic sizing model (Fractional Kelly, Volatility Parity, or Cornish-Fisher VaR-based) best complements our Win Rate ($50.75\%$) and Profit Factor ($1.40$)?

---

## 4. Required Output Deliverable Format

Please structure your audit report into:
1. **Mathematical & Architectural Flaw Assessment** (Severity: Critical / High / Medium / Low)
2. **Direct Mathematical Formulations & Improvements** (LaTeX equations for dynamic thresholds, MTF trigger, and sizing)
3. **Multi-Timeframe (Daily $\to$ 1H) Algorithmic Blueprint** (Step-by-step pseudo-code or Python logic)
4. **Concrete Parameter Recommendations** (Ready for backtesting validation)

*Avoid generic trading generalities. Provide rigorous, quantitative, and production-executable insights.*
