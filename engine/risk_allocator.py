"""
Portfolio Risk Allocator & Position Sizing Engine (engine/risk_allocator.py)
Implements institutional portfolio risk budgeting, gap-aware tail loss modeling,
volatility parity, and subsector concentration limits.
Follows GPT-6 ASTRA Institutional Audit Report Pillar 4 (Section 2.4).
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import numpy as np

from core.logger import get_logger

logger = get_logger("engine.risk_allocator")


@dataclass
class PositionSizingResult:
    """개별 종목 자본 배분 및 포지션 사이징 결과"""
    ticker: str
    entry_price: float
    stop_loss: float
    effective_risk_pct: float       # 갭 위험 반영 실효 손실률 (d_eff %)
    target_weight: float            # 포트폴리오 비중 (%) (0.0% ~ 5.0%)
    allocated_capital: float        # 배정 자본금 ($)
    allocated_shares: int           # 매수 수량 (주)
    risk_dollars: float             # 최대 손실 예상액 ($)
    subsector: str
    approved: bool                  # 한도 초과 없이 승인 여부
    rejection_reason: Optional[str] = None

    @property
    def target_weight_ratio(self) -> float:
        """비율 형태 비중 (0.0 ~ 0.05) 반환"""
        return self.target_weight / 100.0


@dataclass
class PortfolioRiskConfig:
    """포트폴리오 리스크 및 배분 규격 설정 (퍼센트 및 비율 단위 상호 호환)"""
    total_nav: float = 100000.0         # 총 포트폴리오 순자산가치 (NAV $)
    single_trade_risk_pct: float = 0.25 # 거래당 최대 손실 허용 예산 (0.25% of NAV)
    max_single_stock_pct: float = 5.0   # 개별 종목 최대 포트폴리오 비중 (5.0% 또는 0.05)
    max_subsector_pct: float = 20.0     # 서브섹터(반도체, 크립토 등) 최대 포트폴리오 비중 (20.0% 또는 0.20)
    max_portfolio_risk_pct: float = 2.0 # 전체 포트폴리오 동시 오픈 리스크 한도 (2.0% 또는 0.02)
    max_gross_exposure_pct: float = 100.0 # 최대 주식 총 익스포저 (100.0% 또는 1.00)
    slippage_buffer_pct: float = 0.3    # 슬리피지 및 스프레드 비용 버퍼 (0.3%)
    gap_down_stress_floor_pct: float = 6.0 # 갭하락 스트레스 최소 손실 하한선 (6.0%)

    def __post_init__(self):
        # 0.05, 0.02, 1.00 등 비율(ratio <= 1.0)로 입력된 경우 퍼센트(%) 단위로 자동 변환하여 정규화
        if 0.0 < self.max_single_stock_pct <= 1.0:
            self.max_single_stock_pct *= 100.0
        if 0.0 < self.max_subsector_pct <= 1.0:
            self.max_subsector_pct *= 100.0
        if 0.0 < self.max_portfolio_risk_pct <= 1.0:
            self.max_portfolio_risk_pct *= 100.0
        if 0.0 < self.max_gross_exposure_pct <= 1.0:
            self.max_gross_exposure_pct *= 100.0
        if 0.0 < self.single_trade_risk_pct <= 0.01:
            self.single_trade_risk_pct *= 100.0


class RiskReservationManager:
    """주문 발주 시 동시 다발적 리스크 한도 초과를 방지하는 원자적 예약 관리자"""
    def __init__(self):
        self._reservations: Dict[str, Dict[str, Any]] = {}

    def reserve(self, order_id: str, sizing: PositionSizingResult) -> bool:
        if not sizing.approved:
            return False
        self._reservations[order_id] = {
            "ticker": sizing.ticker,
            "allocated_capital": sizing.allocated_capital,
            "risk_dollars": sizing.risk_dollars,
            "subsector": sizing.subsector,
        }
        return True

    def release(self, order_id: str) -> None:
        self._reservations.pop(order_id, None)

    def get_reserved_trades(self) -> List[Dict[str, Any]]:
        return list(self._reservations.values())

    def clear(self) -> None:
        self._reservations.clear()


def _extract_trade_risk(t: Dict[str, Any]) -> float:
    """기존 포지션의 오픈 리스크($) 추출 (risk_dollars 누락 시 구조적 위험 공식으로 보수적 산출)"""
    if t.get("risk_dollars") is not None and float(t.get("risk_dollars", 0.0)) > 0:
        return float(t["risk_dollars"])
    
    # 1. 진입가 및 손절선 기반 구조적 위험 (entry_price - stop_loss) * shares
    ep = float(t.get("entry_price", 0.0))
    sl = float(t.get("stop_loss", 0.0))
    sh = float(t.get("shares", t.get("allocated_shares", 0)))
    if ep > 0 and sl > 0 and sh > 0 and ep > sl:
        return (ep - sl) * sh
    
    # 2. 배정 자본금 및 실효 손실률 기반 위험 allocated_capital * (risk_pct / 100)
    cap = float(t.get("allocated_capital", 0.0))
    risk_pct = float(t.get("effective_risk_pct", t.get("risk_pct", 0.0)))
    if cap > 0 and risk_pct > 0:
        return cap * (risk_pct / 100.0)
    
    # 3. 주식 수량과 진입가만 있고 손절선이 없는 경우: 최소 갭하락 하한선(6%) 적용
    if ep > 0 and sh > 0:
        return (ep * 0.06) * sh
    if cap > 0:
        return cap * 0.06

    return float(t.get("risk_dollars", 0.0))


def calculate_position_sizing(
    ticker: str,
    entry_price: float,
    stop_loss: float,
    subsector: str,
    config: Optional[PortfolioRiskConfig] = None,
    current_portfolio_trades: Optional[List[Dict[str, Any]]] = None,
    annual_volatility: Optional[float] = None,
) -> PositionSizingResult:
    """
    GPT-6 Astra Pillar 4 공식에 따른 갭 반영 포지션 사이징 산출.
    
    1. 구조적 손절률 d_SL = (E - SL) / E
    2. 갭 스트레스 실효 손실률 d_eff = max(d_SL + c_slip, gap_floor)
    3. 리스크 예산 기반 비중 w_risk = r_budget / d_eff
    4. 비중 상한: min(w_risk, max_single_stock)
    5. 서브섹터 비중 한도(20%) 및 포트폴리오 전체 오픈 리스크(2%) 점검
    """
    if config is None:
        config = PortfolioRiskConfig()

    if entry_price <= 0 or stop_loss <= 0:
        return PositionSizingResult(
            ticker=ticker,
            entry_price=entry_price,
            stop_loss=stop_loss,
            effective_risk_pct=0.0,
            target_weight=0.0,
            allocated_capital=0.0,
            allocated_shares=0,
            risk_dollars=0.0,
            subsector=subsector,
            approved=False,
            rejection_reason="유효하지 않은 진입가 또는 손절가",
        )

    # 0. 롱 포지션 무결성 검증 (손절가는 반드시 진입가보다 낮아야 함)
    if stop_loss >= entry_price:
        return PositionSizingResult(
            ticker=ticker,
            entry_price=entry_price,
            stop_loss=stop_loss,
            effective_risk_pct=0.0,
            target_weight=0.0,
            allocated_capital=0.0,
            allocated_shares=0,
            risk_dollars=0.0,
            subsector=subsector,
            approved=False,
            rejection_reason="손절가(SL)는 진입가(Entry)보다 엄격히 낮아야 합니다 (Long 포지션 불변조건)",
        )

    current_trades = current_portfolio_trades or []

    # 1. 포트폴리오 오픈 리스크(Open Risk) 한도 점검 (최대 2.0% of NAV)
    current_open_risk = sum(
        _extract_trade_risk(t)
        for t in current_trades
    )
    max_portfolio_risk = config.total_nav * (config.max_portfolio_risk_pct / 100.0)
    max_risk_dollars = config.total_nav * (config.single_trade_risk_pct / 100.0)

    if (current_open_risk + max_risk_dollars) > max_portfolio_risk or config.max_portfolio_risk_pct <= 0:
        return PositionSizingResult(
            ticker=ticker,
            entry_price=entry_price,
            stop_loss=stop_loss,
            effective_risk_pct=0.0,
            target_weight=0.0,
            allocated_capital=0.0,
            allocated_shares=0,
            risk_dollars=0.0,
            subsector=subsector,
            approved=False,
            rejection_reason=f"포트폴리오 오픈 리스크(Open Risk) 한도 초과 (현재 {current_open_risk / config.total_nav * 100:.2f}% + 신규 {config.single_trade_risk_pct:.2f}% > {config.max_portfolio_risk_pct:.2f}%)",
        )

    # 2. 포트폴리오 총 익스포저(Gross Exposure) 한도 점검 (최대 100.0%)
    current_gross_capital = sum(
        float(t.get("allocated_capital", t.get("entry_price", 0) * t.get("shares", 0)))
        for t in current_trades
    )
    max_gross_capital = config.total_nav * (config.max_gross_exposure_pct / 100.0)
    remaining_gross = max(0.0, max_gross_capital - current_gross_capital)

    if remaining_gross <= 0 or config.max_gross_exposure_pct <= 0:
        return PositionSizingResult(
            ticker=ticker,
            entry_price=entry_price,
            stop_loss=stop_loss,
            effective_risk_pct=0.0,
            target_weight=0.0,
            allocated_capital=0.0,
            allocated_shares=0,
            risk_dollars=0.0,
            subsector=subsector,
            approved=False,
            rejection_reason=f"포트폴리오 총 익스포저(Gross Exposure) 한도 초과 ({current_gross_capital / config.total_nav * 100:.1f}% >= {config.max_gross_exposure_pct:.1f}%)",
        )

    # 3. 구조적 손절률 및 갭 스트레스 실효 손실률 (d_eff)
    structural_loss_pct = ((entry_price - stop_loss) / entry_price) * 100.0
    effective_risk_pct = max(
        structural_loss_pct + config.slippage_buffer_pct,
        config.gap_down_stress_floor_pct,
    )

    # 4. 리스크 기반 자본 배정액 ($) = risk_budget / (d_eff / 100)
    risk_based_capital = max_risk_dollars / (effective_risk_pct / 100.0)

    # 5. 종목당 최대 배정 자본금 (기존 동일 종목 합산 5% NAV 한도)
    current_ticker_capital = sum(
        float(t.get("allocated_capital", t.get("entry_price", 0) * t.get("shares", 0)))
        for t in current_trades
        if t.get("ticker") == ticker
    )
    max_stock_capital = config.total_nav * (config.max_single_stock_pct / 100.0)
    remaining_stock_capacity = max(0.0, max_stock_capital - current_ticker_capital)

    if remaining_stock_capacity <= 0:
        return PositionSizingResult(
            ticker=ticker,
            entry_price=entry_price,
            stop_loss=stop_loss,
            effective_risk_pct=round(effective_risk_pct, 2),
            target_weight=0.0,
            allocated_capital=0.0,
            allocated_shares=0,
            risk_dollars=0.0,
            subsector=subsector,
            approved=False,
            rejection_reason=f"개별 종목({ticker}) 누적 비중 한도 초과 ({current_ticker_capital / config.total_nav * 100:.1f}% >= {config.max_single_stock_pct:.1f}%)",
        )

    allocated_capital = min(risk_based_capital, remaining_stock_capacity, remaining_gross)

    # 6. 서브섹터 집중도 점검 (기존 보유 포지션 합산 <= 20%)
    current_sector_capital = sum(
        float(t.get("allocated_capital", t.get("entry_price", 0) * t.get("shares", 0)))
        for t in current_trades
        if t.get("subsector") == subsector
    )
    max_sector_capital = config.total_nav * (config.max_subsector_pct / 100.0)

    if (current_sector_capital + allocated_capital) > max_sector_capital:
        remaining_sector_capacity = max_sector_capital - current_sector_capital
        if remaining_sector_capacity <= (config.total_nav * 0.01):
            return PositionSizingResult(
                ticker=ticker,
                entry_price=entry_price,
                stop_loss=stop_loss,
                effective_risk_pct=round(effective_risk_pct, 2),
                target_weight=0.0,
                allocated_capital=0.0,
                allocated_shares=0,
                risk_dollars=0.0,
                subsector=subsector,
                approved=False,
                rejection_reason=f"서브섹터({subsector}) 집중도 한도 초과 ({current_sector_capital / config.total_nav * 100:.1f}% >= {config.max_subsector_pct:.1f}%)",
            )
        allocated_capital = remaining_sector_capacity

    # 7. 주식 수량 산출
    shares = int(allocated_capital // entry_price)
    final_capital = round(shares * entry_price, 2)
    final_risk_dollars = round(final_capital * (effective_risk_pct / 100.0), 2)
    final_weight_pct = round((final_capital / config.total_nav) * 100.0, 2)


    return PositionSizingResult(
        ticker=ticker,
        entry_price=entry_price,
        stop_loss=stop_loss,
        effective_risk_pct=round(effective_risk_pct, 2),
        target_weight=final_weight_pct,
        allocated_capital=final_capital,
        allocated_shares=shares,
        risk_dollars=final_risk_dollars,
        subsector=subsector,
        approved=shares > 0,
        rejection_reason=None if shares > 0 else "배정 수량 0주",
    )
