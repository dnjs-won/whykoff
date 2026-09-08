"""
Multi-Timeframe 1-Hour Trigger Engine (engine/hourly_trigger.py)
Implements Richard Wyckoff Phase C/D 1-Hour Spring and Secondary Test (ST) confirmations.
Follows GPT-6 ASTRA Institutional Audit Report Pillar 2 (Section 2.2):
1. Daily candle validates macro Wyckoff setup eligibility (5-Star LPS Sweet Spot).
2. 1-Hour candle monitors for Spring (shakeout below 20-bar base) and Secondary Test (ST).
3. Constructs precise Stop-Limit entry orders and structural narrow stop loss (SL_H)
   to protect downside without premature whipsaw.
"""
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
import pandas as pd
import numpy as np

from core.logger import get_logger
from core.models import WyckoffSetupResult
from engine.indicators import calculate_atr

logger = get_logger("engine.hourly_trigger")

# Spring/ST 후보의 최초 손절선 및 무효화 상태를 영속 보존하는 레지스트리 (ATR 변동 재무장 방지)
_CANDIDATE_REGISTRY: Dict[Any, Dict[str, Any]] = {}


def clear_hourly_candidate_registry() -> None:
    """후보 레지스트리 초기화 (테스트 및 세션 리셋용)"""
    _CANDIDATE_REGISTRY.clear()


@dataclass
class HourlyTriggerResult:
    """1시간봉 타점 분석 결과 규격"""
    ticker: str
    triggered: bool                         # 주문 발주 조건 충족 여부 (ST 확정)
    state: str                              # 'ARMED', 'SPRING_DETECTED', 'ST_CONFIRMED', 'INVALID'
    hourly_close: float
    buy_stop: Optional[float] = None        # 진입 트리거 가격 (ST High + tick)
    buy_limit: Optional[float] = None       # 슬리피지 방지 매수 한도가 (Buy Stop + 0.1 * A_H)
    hourly_stop_loss: Optional[float] = None # 1시간봉 구조적 손절가 (min(Spring_L, ST_L) - 0.25 * A_H)
    hourly_risk_pct: Optional[float] = None # 진입가 대비 1시간봉 손절 거리 (%)
    spring_low: Optional[float] = None
    st_low: Optional[float] = None
    reasons: List[str] = field(default_factory=list)


def evaluate_hourly_trigger(
    hourly_df: pd.DataFrame,
    daily_setup: WyckoffSetupResult,
    daily_atr: float,
    tick_size: float = 0.01,
    lookback_base: int = 20,
) -> HourlyTriggerResult:
    """
    일봉 5성 스윗스팟 후보 종목의 1시간봉 데이터를 분석하여
    Wyckoff Spring 및 Secondary Test(ST) 타점을 정량 판정.

    Pillar 2 수학적 판정 수식:
    1. 20봉 저점 B_H = min(low[-20:])
    2. Spring s:
       - 0.1 * A_H <= B_H - L_s <= 0.5 * A_H
       - C_s > B_H (꼬리 빼고 즉시 복구)
       - |C_s - POC_D| <= 1.0 * ATR_D (일봉 POC 지지권 내)
    3. Secondary Test j (Spring 이후 1~6봉 이내):
       - L_j >= L_s
       - |L_j - B_H| <= 0.5 * A_H
       - C_j >= B_H
       - RVOL_j <= 0.8 * RVOL_s (거래량 감소 확인)
    4. 주문 레벨:
       - buy_stop = H_j + tick
       - buy_limit = H_j + tick + 0.1 * A_H
       - SL_H = min(L_s, L_j) - 0.25 * A_H
    """
    ticker = daily_setup.ticker
    # 0. 일봉 5성 스윗스팟 적격성 검증 (부적격 일봉은 시간봉 트리거 원천 배제)
    if not getattr(daily_setup, "entry_eligible", True) or not getattr(daily_setup, "is_sweet_spot", False):
        return HourlyTriggerResult(
            ticker=ticker,
            triggered=False,
            state="DISQUALIFIED",
            hourly_close=float(hourly_df["close"].iloc[-1]) if hourly_df is not None and not hourly_df.empty else 0.0,
            reasons=[f"일봉 매집 상태 부적격: 5성 스윗스팟 아님 (점수: {daily_setup.score}, stars: {daily_setup.stars_rating}, eligible: {getattr(daily_setup, 'entry_eligible', True)})"],
        )

    if hourly_df is None or len(hourly_df) < (lookback_base + 10):
        return HourlyTriggerResult(
            ticker=ticker,
            triggered=False,
            state="INVALID",
            hourly_close=float(hourly_df["close"].iloc[-1]) if hourly_df is not None and not hourly_df.empty else 0.0,
            reasons=["1시간봉 데이터 부족 (최소 30봉 필요)"],
        )

    # 1. 지표 산출
    atr_series = calculate_atr(hourly_df, period=14)
    a_h = float(atr_series.iloc[-1]) if pd.notna(atr_series.iloc[-1]) and atr_series.iloc[-1] > 0 else (float(hourly_df["close"].iloc[-1]) * 0.01)

    curr_close = float(hourly_df["close"].iloc[-1])
    reasons: List[str] = []

    # 2. 최근 lookback_base(20)봉 기준 박스 저점 B_H 탐색
    # 직전 7봉을 제외한 이전 20봉의 베이스 저점
    base_window = hourly_df.iloc[-(lookback_base + 7) : -7] if len(hourly_df) >= (lookback_base + 7) else hourly_df.iloc[:-7]
    b_h = float(base_window["low"].min())

    # 3. 최근 7봉 내에서 Spring s 탐색
    recent_bars = hourly_df.iloc[-7:]
    spring_idx = None
    spring_bar = None

    for idx, (bar_idx, bar) in enumerate(recent_bars.iterrows()):
        l_s = float(bar["low"])
        c_s = float(bar["close"])
        shakeout_depth = b_h - l_s

        # Spring 조건: 0.05 A_H <= B_H - L_s <= 0.6 A_H, C_s >= B_H, |C_s - POC_D| <= 1.5 * ATR_D
        if 0.05 * a_h <= shakeout_depth <= 0.6 * a_h and c_s >= b_h:
            if abs(c_s - daily_setup.daily_poc) <= 1.5 * daily_atr:
                spring_idx = idx
                spring_bar = bar
                reasons.append(
                    f"1H Spring 포착: 베이스 저점 ${b_h:.2f} 하향 이탈 후 복구 (저점 ${l_s:.2f}, 종가 ${c_s:.2f})"
                )
                break

    if spring_idx is None:
        return HourlyTriggerResult(
            ticker=ticker,
            triggered=False,
            state="ARMED",
            hourly_close=curr_close,
            reasons=["1시간봉 Spring 미발생 (베이스 저점 수렴 대기 중)"],
        )

    # 4. Spring 이후 Secondary Test (ST) 탐색 (Spring 이후 1~6봉)
    post_spring_bars = recent_bars.iloc[spring_idx + 1 :]
    if post_spring_bars.empty:
        return HourlyTriggerResult(
            ticker=ticker,
            triggered=False,
            state="SPRING_DETECTED",
            hourly_close=curr_close,
            spring_low=float(spring_bar["low"]),
            reasons=reasons + ["Spring 발생 후 Secondary Test(ST) 진행 대기 중"],
        )

    st_bar = None
    spring_low = float(spring_bar["low"])
    spring_vol = float(spring_bar["volume"]) if "volume" in spring_bar and float(spring_bar["volume"]) > 0 else 1.0

    intervening_breakdown = False
    for _, bar in post_spring_bars.iterrows():
        l_j = float(bar["low"])
        c_j = float(bar["close"])
        vol_j = float(bar["volume"]) if "volume" in bar and float(bar["volume"]) > 0 else 1.0

        # Spring 저점 붕괴 검사: Spring 이후 ST 형성 전이라도 Spring 저점을 하향 이탈하면 Spring 구조 붕괴
        if l_j < spring_low:
            intervening_breakdown = True
            break

        # ST 조건: L_j >= L_s, |L_j - B_H| <= 0.6 * A_H, C_j >= B_H, RVOL_j < 0.8 * RVOL_s (거래량 20% 이상 수축 필수)
        if l_j >= spring_low and abs(l_j - b_h) <= 0.6 * a_h and c_j >= b_h:
            if vol_j < 0.8 * spring_vol:
                st_bar = bar
                reasons.append(
                    f"1H ST 확정: Spring 저점(${spring_low:.2f}) 상회 지지 (ST 저점 ${l_j:.2f}, 거래량 {vol_j:.0f} < {spring_vol*0.8:.0f} 수렴)"
                )
                break

    if intervening_breakdown:
        return HourlyTriggerResult(
            ticker=ticker,
            triggered=False,
            state="INVALIDATED",
            hourly_close=curr_close,
            spring_low=spring_low,
            reasons=reasons + ["Spring 이후 ST 형성 전 지지선 붕괴(Spring 저점 하회) 발생으로 무효화"],
        )

    if st_bar is None:
        return HourlyTriggerResult(
            ticker=ticker,
            triggered=False,
            state="SPRING_DETECTED",
            hourly_close=curr_close,
            spring_low=spring_low,
            reasons=reasons + ["ST 지지 확인 대기 중"],
        )

    # 5. 타점 및 Stop-Limit 레벨 계산 (후보 키 고정 및 과거 무효화 영구 유지)
    spring_dt = str(spring_bar["datetime"]) if "datetime" in spring_bar else str(spring_bar.name)
    st_dt = str(st_bar["datetime"]) if "datetime" in st_bar else str(st_bar.name)
    candidate_key = (ticker, spring_dt, st_dt)

    # 과거에 이미 무효화된 후보인 경우: ATR 확장에 의해 손절선이 이동하더라도 재활성화 영구 차단
    if candidate_key in _CANDIDATE_REGISTRY and _CANDIDATE_REGISTRY[candidate_key].get("state") == "INVALIDATED":
        cached_sl = _CANDIDATE_REGISTRY[candidate_key].get("sl_h", 0.0)
        return HourlyTriggerResult(
            ticker=ticker,
            triggered=False,
            state="INVALIDATED",
            hourly_close=curr_close,
            hourly_stop_loss=cached_sl,
            spring_low=spring_low,
            st_low=float(st_bar["low"]),
            reasons=reasons + ["기존 무효화된 후보: 과거 손절선 이탈 이력으로 영구 무효화 (ATR 변동 재무장 불가)"],
        )

    st_high = float(st_bar["high"])
    st_low = float(st_bar["low"])

    # ST 확정 시점의 ATR 고정 (후속 봉의 ATR 변동에 영향받지 않음)
    st_loc = st_bar.name
    if st_loc in atr_series.index and pd.notna(atr_series.loc[st_loc]) and float(atr_series.loc[st_loc]) > 0:
        a_h_at_st = float(atr_series.loc[st_loc])
    else:
        a_h_at_st = a_h

    if candidate_key in _CANDIDATE_REGISTRY and "sl_h" in _CANDIDATE_REGISTRY[candidate_key]:
        sl_h = _CANDIDATE_REGISTRY[candidate_key]["sl_h"]
        buy_stop = _CANDIDATE_REGISTRY[candidate_key]["buy_stop"]
        buy_limit = _CANDIDATE_REGISTRY[candidate_key]["buy_limit"]
    else:
        buy_stop = round(st_high + tick_size, 2)
        buy_limit = round(buy_stop + 0.1 * a_h_at_st, 2)
        sl_h = round(min(spring_low, st_low) - 0.25 * a_h_at_st, 2)
        _CANDIDATE_REGISTRY[candidate_key] = {
            "sl_h": sl_h,
            "buy_stop": buy_stop,
            "buy_limit": buy_limit,
            "state": "ST_CONFIRMED",
        }

    # ST 확정 이후 현재가까지의 봉들 중 SL_H를 하향 이탈한 적이 있거나 현재가가 SL 아래면 즉시 영구 무효화
    st_idx_in_post = post_spring_bars.index.get_loc(st_bar.name)
    subsequent_bars = post_spring_bars.iloc[st_idx_in_post + 1 :]
    if curr_close < sl_h or (not subsequent_bars.empty and float(subsequent_bars["low"].min()) < sl_h):
        _CANDIDATE_REGISTRY[candidate_key]["state"] = "INVALIDATED"
        reasons.append(f"ST 지지 붕괴: 현재가(${curr_close:.2f}) 또는 후속 저점이 손절선(${sl_h:.2f}) 하회하여 트리거 무효화")
        return HourlyTriggerResult(
            ticker=ticker,
            triggered=False,
            state="INVALIDATED",
            hourly_close=curr_close,
            hourly_stop_loss=sl_h,
            spring_low=spring_low,
            st_low=st_low,
            reasons=reasons,
        )

    risk_pct = round(((buy_stop - sl_h) / buy_stop) * 100.0, 2) if buy_stop > 0 else 0.0

    if risk_pct > 8.0:
        reasons.append(f"위험 한도 초과: 1시간봉 손절 거리 {risk_pct:.1f}% > 8.0% 상한")
        return HourlyTriggerResult(
            ticker=ticker,
            triggered=False,
            state="INVALID",
            hourly_close=curr_close,
            buy_stop=buy_stop,
            buy_limit=buy_limit,
            hourly_stop_loss=sl_h,
            hourly_risk_pct=risk_pct,
            spring_low=spring_low,
            st_low=st_low,
            reasons=reasons,
        )

    return HourlyTriggerResult(
        ticker=ticker,
        triggered=True,
        state="ST_CONFIRMED",
        hourly_close=curr_close,
        buy_stop=buy_stop,
        buy_limit=buy_limit,
        hourly_stop_loss=sl_h,
        hourly_risk_pct=risk_pct,
        spring_low=spring_low,
        st_low=st_low,
        reasons=reasons,
    )

