from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Any, Dict

@dataclass
class CandleRecord:
    ticker: str
    datetime: str | datetime
    open: float
    high: float
    low: float
    close: float
    volume: int

@dataclass
class WyckoffSetupResult:
    ticker: str
    score: float                         # 기술 점수 (0~100)
    stars_rating: int                    # 스윗스팟 별점 (1~5성)
    is_sweet_spot: bool                  # 68~78점 바닥 매집 완료 여부
    is_overextended: bool                # 85점 이상 과열 추격 경고
    setup_type: str
    current_price: float
    daily_poc: float
    box_low: float
    box_high: float
    stop_loss: float
    tp1: float
    tp2: float
    rr_ratio: float
    reasons: List[str] = field(default_factory=list)

@dataclass
class ConfluenceSignalResult:
    ticker: str
    score: float
    stars_rating: int
    setup_type: str  # 'GOLDEN', 'PULLBACK', 'REVERSAL'
    current_price: float
    stop_loss: float
    tp1: float
    tp2: float
    rr_ratio: float
    reasons: List[str] = field(default_factory=list)

@dataclass
class ScanSnapshotRecord:
    ticker: str
    scan_date: str
    strategy_type: str
    technical_score: float
    stars_rating: int
    is_sweet_spot: bool
    is_overextended: bool
    current_price: float
    daily_poc: Optional[float]
    stop_loss: float
    tp1: float
    tp2: float
    rr_ratio: float
    reasons: List[str] = field(default_factory=list)
    ai_rating: Optional[str] = None
    ai_verdict: Optional[str] = None

@dataclass
class ActiveTradeRecord:
    trade_id: Optional[int]
    ticker: str
    strategy_type: str
    entry_date: str
    entry_price: float
    stop_loss: float
    tp1: float
    tp2: float
    rr_ratio: float
    status: str = "OPEN"  # OPEN, TP1_HIT, TP2_HIT, SL_HIT, EXPIRED
    current_price: Optional[float] = None
    unrealized_pnl_pct: float = 0.0
    max_favorable_pct: float = 0.0
    max_adverse_pct: float = 0.0
    exit_date: Optional[str] = None
    exit_price: Optional[float] = None
    realized_pnl_pct: Optional[float] = None
    holding_days: int = 0
    close_reason: Optional[str] = None
    reconfirmed_count: int = 1

@dataclass
class PositionMonitoringItem:
    ticker: str
    entry_date: str
    holding_days: int
    current_price: float
    pnl_pct: float
    entry_score: float
    current_score: float
    score_delta: float
    daily_poc: float
    stop_loss: float
    tp1: float
    is_poc_broken: bool
    warning_flag: bool
    warning_comment: Optional[str] = None

@dataclass
class StrategyPerformanceSummary:
    rolling_days: int
    total_closed_trades: int
    wins: int
    losses: int
    win_rate_pct: float
    profit_factor: float
    avg_gain_pct: float
    avg_loss_pct: float
    avg_holding_days: float
    current_open_trades: int

@dataclass
class BenchmarkResult:
    strategy_version: str
    test_start_date: str
    test_end_date: str
    sample_trades_count: int
    win_rate_pct: float
    profit_factor: float
    expectancy_pct: float
    avg_holding_days: float
    max_drawdown_pct: float
    is_champion: bool = False
    params_config: Dict[str, Any] = field(default_factory=dict)

@dataclass
class MacroRecord:
    symbol: str
    trade_date: str
    close: float
    change_pct: Optional[float] = None

@dataclass
class SectorShareRecord:
    symbol: str
    sector_name: str
    trade_date: str
    share_pct: float
    share_delta: float
    change_pct: Optional[float] = None

@dataclass
class NewsRecord:
    symbol: str
    title: str
    summary: str
    published_at: str | datetime
    url: Optional[str] = None
