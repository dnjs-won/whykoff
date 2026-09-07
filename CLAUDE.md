# Claude & Cursor AI Guidelines for Whykoff

> **MANDATORY STARTUP INSTRUCTION:**  
> Before making changes or answering queries in this repository, read:
> 1. `PROJECT_STATUS.md` (System state, Champion strategy wyckoff_v2.0_full_swing, GCP production info)
> 2. `CORE_LOGIC_SPECS.md` (Wyckoff 6-step accumulation, 5-Star LPS Sweet Spot, SL/TP rules)
> 3. `ARCHITECTURE.md` (Layered structure)

Always run `python -m unittest discover tests` after modifying code.
Do not blend sector scores into individual stock momentum scores.
LPS Sweet spot is 68-78 pts (5-Star). 85+ pts is Overextended (2-Star warning).
Stop Loss buffer is 2.5% below box low (`sl_buffer_pct = 0.025`).
