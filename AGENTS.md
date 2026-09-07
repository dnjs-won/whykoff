# Universal AI Agent Guidelines for Whykoff Project

> **MANDATORY INSTRUCTION FOR ALL AI AGENTS:**  
> Before performing any task, planning, or editing in this repository, you MUST first read and adhere to:
> - [`PROJECT_STATUS.md`](file:///C:/project_k/whykoff/PROJECT_STATUS.md): Current Champion Strategy (`wyckoff_v2.0_full_swing`), GCP production setup, and maintenance handover context.
> - [`CORE_LOGIC_SPECS.md`](file:///C:/project_k/whykoff/CORE_LOGIC_SPECS.md): Wyckoff quantitative formulas, 5-Star LPS Sweet Spot (68~78 pts), and trade state machine.
> - [`ARCHITECTURE.md`](file:///C:/project_k/whykoff/ARCHITECTURE.md): Layered architecture constraints.

---

## Key Invariants
1. Champion Strategy: `wyckoff_v2.0_full_swing` (Win Rate 50.75%, PF 1.40, Expectancy +1.58%).
2. Do not add sector bonus points into individual stock scores (Top-down 2-step principle).
3. 68~78 pts is the 5-Star LPS Sweet Spot. 85+ pts is Overextended (2-Star Warning).
4. Always verify edits with `python -m unittest discover tests`.
