"""
Whykoff Accumulation Setup Scanner (engine/wyckoff_scanner.py)
Implements Richard Wyckoff's 6-step accumulation detection algorithm and dual evaluation system
(Technical Raw Score 0~100 + Sweet Spot Star Rating 1~5 Stars).

Strictly follows CORE_LOGIC_SPECS.md:
1. Drop >= 25% from 120-day High (Hard Filter)
2. 30-day Box Range <= 20% (+20 pts, Hard Filter)
3. 20-day MA Flattening (-2.0% <= Slope <= +2.5%, +15 pts, Hard Filter for Slope < -2.0%)
4. 120-day Volume Profile POC Support (0% ~ +7%, +25 pts, Hard Filter for Price < POC*0.985)
5. Smart Money Inflow (MFI +15, OBV Golden Cross +15, RSI Healthy Turn +10)
6. Breakout Triggers (Price >= MA5 and MA20 +10, MACD Histogram > 0 or Rising +5)
7. Dual Rating (5-Star Sweet Spot at 68~78 pts vs 2-Star Overextended at 85+ pts)
"""
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any
import pandas as pd
import numpy as np

from core.logger import get_logger
from core.models import WyckoffSetupResult
from engine.indicators import (
    calculate_volume_profile_poc,
    add_all_indicators,
)

logger = get_logger("engine.wyckoff_scanner")


@dataclass
class WyckoffParams:
    """와이코프 6단계 매집 전략 파라미터 규격"""
    min_drop_rate: float = -25.0         # 조건 1: 120일 고점 대비 최소 낙폭 (%)
    max_box_range: float = 20.0         # 조건 2: 30일 박스권 최대 진폭 (%)
    ma20_slope_min: float = -2.0        # 조건 3: 20일선 최소 기울기 (%)
    ma20_slope_max: float = 2.5         # 조건 3: 20일선 최대 기울기 (%)
    poc_support_buffer: float = 0.985   # 조건 4: POC 하방 지지 여유율 (POC * 0.985)
    poc_distance_max: float = 7.0       # 조건 4: POC 상방 최대 허용 이격 (%)
    mfi_threshold: float = 45.0         # 조건 5: MFI 유입 기준치
    rsi_min: float = 42.0               # 조건 5: RSI 정상 턴 하한
    rsi_max: float = 62.0               # 조건 5: RSI 정상 턴 상한
    tp1_pct: float = 0.20               # 1차 목표가 비율 (+20%)
    tp2_pct: float = 0.50               # 2차 목표가 비율 (+50%)
    sl_buffer_pct: float = 0.025        # 박스 저점 대비 SL 버퍼 (2.5% - 노이즈 털림 방지)
    sweet_spot_min_score: float = 68.0  # 5성 스윗스팟 최소 점수
    sweet_spot_max_score: float = 78.0  # 5성 스윗스팟 최대 점수
    sweet_spot_max_box: float = 18.0    # 5성 스윗스팟 박스권 진폭 상한
    overextended_score: float = 85.0    # 2성 과열권 경고 점수

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def evaluate_wyckoff_setup(
    df: pd.DataFrame,
    ticker: str = "UNKNOWN",
    as_of_latest: bool = True,
    strict_filter: bool = True,
    params: Optional[WyckoffParams] = None,
) -> Optional[WyckoffSetupResult]:
    """
    단일 종목의 일봉 OHLCV 데이터를 받아 와이코프 6단계 매집 알고리즘 및 스윗스팟 별점을 평가.
    
    Args:
        df: 최소 120봉 이상의 OHLCV 일봉 DataFrame (필수 컬럼: datetime, open, high, low, close, volume)
        ticker: 종목 티커 심볼
        as_of_latest: True면 마지막 완성봉(iloc[-1]) 기준. False면 직전 봉(iloc[-2]) 기준.
        strict_filter: True인 경우 필수 게이트(하락폭, 박스권, 20선, POC 지지) 탈락 시 즉시 탈락(1성) 처리.
        params: 전략 파라미터 객체 (None이면 기본 Champion 파라미터 사용)
        
    Returns:
        Optional[WyckoffSetupResult]: 매집 분석 결과 객체
    """
    if params is None:
        params = WyckoffParams()

    # 0. 가드 클로즈 (Guard Clause)
    if df is None or df.empty:
        logger.warning(f"[{ticker}] Empty DataFrame received.")
        return None

    required_cols = {"open", "high", "low", "close", "volume"}
    if not required_cols.issubset(df.columns):
        logger.error(f"[{ticker}] Missing required columns: {required_cols - set(df.columns)}")
        return None

    # 최소 120봉 데이터 필요
    min_required_len = 120
    if len(df) < min_required_len:
        logger.info(f"[{ticker}] Insufficient candle history: {len(df)} < {min_required_len}")
        return None

    # 지표 산출 (이미 지표 컬럼이 있으면 재계산 생략하여 성능 극대화)
    if "ma20_slope_10d" not in df.columns or "rsi" not in df.columns:
        df_calc = add_all_indicators(df)
    else:
        df_calc = df

    # 분석 대상 기준봉 인덱스 결정 (완성봉 리페인팅 방지)
    offset = 0 if as_of_latest else 1
    end_idx = len(df_calc) - offset
    if end_idx < min_required_len:
        return None

    row = df_calc.iloc[end_idx - 1]
    prev_row = df_calc.iloc[end_idx - 2]

    current_price = float(row["close"])
    if current_price <= 0:
        logger.warning(f"[{ticker}] Invalid current price: {current_price}")
        return None

    # 분석용 서브셋 슬라이싱 (끝 인덱스: end_idx)
    sub_120 = df_calc.iloc[max(0, end_idx - 120) : end_idx]
    sub_30 = df_calc.iloc[max(0, end_idx - 30) : end_idx]
    poc_df = df_calc.iloc[:end_idx]

    score = 0.0
    reasons: List[str] = []
    is_failed = False

    # ----------------------------------------------------
    # [조건 1] 장기 하락 및 기간 조정 (Base Building)
    # 최근 120영업일 내 최고가 대비 현재가 낙폭 <= params.min_drop_rate (-25.0%)
    # ----------------------------------------------------
    high_120d = float(sub_120["high"].max())
    drop_rate = ((current_price - high_120d) / high_120d) * 100.0 if high_120d > 0 else 0.0

    if drop_rate > params.min_drop_rate:
        is_failed = True
        reasons.append(f"조건 1 탈락: 120일 고점 대비 하락폭 미달 (낙폭 {drop_rate:.1f}%, 기준 {params.min_drop_rate:.1f}% 이하)")
    else:
        reasons.append(f"조건 1 통과: 120일 고점 대비 충분한 기간 조정 (낙폭 {drop_rate:.1f}%)")

    # ----------------------------------------------------
    # [조건 2] 30일 박스권 에너지 수렴
    # 최근 30영업일 진폭 <= params.max_box_range (20.0%) -> +20점
    # ----------------------------------------------------
    box_high = float(sub_30["high"].max())
    box_low = float(sub_30["low"].min())
    box_range = ((box_high - box_low) / box_low * 100.0) if box_low > 0 else 999.0

    if box_range <= params.max_box_range:
        score += 20.0
        reasons.append(f"조건 2 통과 (+20점): 30일 박스권 에너지 수렴 (진폭 {box_range:.1f}%)")
    else:
        is_failed = True
        reasons.append(f"조건 2 탈락: 30일 박스권 변동성 과대 (진폭 {box_range:.1f}%, 기준 {params.max_box_range:.1f}% 이하)")

    # ----------------------------------------------------
    # [조건 3] 일봉 20일 이동평균선 평탄화 (Flat Check)
    # params.ma20_slope_min <= Slope <= params.ma20_slope_max -> +15점
    # Slope < params.ma20_slope_min 시 탈락 (떨어지는 칼날 차단)
    # ----------------------------------------------------
    slope_ma20 = float(row["ma20_slope_10d"]) if pd.notna(row["ma20_slope_10d"]) else -99.0

    if slope_ma20 < params.ma20_slope_min:
        is_failed = True
        reasons.append(f"조건 3 탈락: 20일선 급락 추세 (10일 기울기 {slope_ma20:.2f}%, 기준 {params.ma20_slope_min:.1f}% 이상)")
    elif params.ma20_slope_min <= slope_ma20 <= params.ma20_slope_max:
        score += 15.0
        reasons.append(f"조건 3 통과 (+15점): 20일선 평탄화 지지 형성 (기울기 {slope_ma20:.2f}%)")
    else:
        # 기울기가 상한을 초과하는 경우
        reasons.append(f"조건 3 보류 (+0점): 20일선 상승 기울기 (기울기 {slope_ma20:.2f}%, 평탄화 구간 초과)")

    # ----------------------------------------------------
    # [조건 4] 볼륨 프로파일 최대 매물대 (POC) 지지 안착
    # 판정: Current Price >= POC * params.poc_support_buffer AND 이격 <= params.poc_distance_max -> +25점
    # ----------------------------------------------------
    daily_poc = calculate_volume_profile_poc(poc_df, lookback=120, bins=40)
    poc_support_threshold = daily_poc * params.poc_support_buffer
    poc_distance_pct = ((current_price - daily_poc) / daily_poc * 100.0) if daily_poc > 0 else 0.0

    if current_price < poc_support_threshold:
        is_failed = True
        reasons.append(f"조건 4 탈락: POC 매물대 저항 하방 갇힘 (현재가 ${current_price:.2f} < POC ${daily_poc:.2f})")
    elif poc_distance_pct <= params.poc_distance_max:
        score += 25.0
        reasons.append(f"조건 4 통과 (+25점): 120일 POC 매물대 지지판 안착 (POC ${daily_poc:.2f}, 이격 {poc_distance_pct:+.1f}%)")
    else:
        reasons.append(f"조건 4 보류 (+0점): POC 대비 단기 과이격 (POC ${daily_poc:.2f}, 이격 {poc_distance_pct:+.1f}%, 기준 +{params.poc_distance_max:.1f}% 이내)")

    # ----------------------------------------------------
    # [조건 5] 스마트머니 수급 지표 (MFI, OBV, RSI)
    # 1. MFI >= params.mfi_threshold 및 15일 전 대비 상승: +15점
    # 2. OBV > OBV 10MA (골든크로스): +15점
    # 3. params.rsi_min <= RSI <= params.rsi_max: +10점
    # ----------------------------------------------------
    mfi_now = float(row["mfi"]) if pd.notna(row["mfi"]) else 0.0
    mfi_15d = float(row["mfi_15d_ago"]) if pd.notna(row["mfi_15d_ago"]) else 0.0
    if mfi_now >= params.mfi_threshold and mfi_now > mfi_15d:
        score += 15.0
        reasons.append(f"조건 5-1 통과 (+15점): MFI 스마트머니 자금 유입 (MFI {mfi_now:.1f} > 15일전 {mfi_15d:.1f})")
    else:
        reasons.append(f"조건 5-1 미충족 (+0점): MFI 유입 신호 부재 (현재 {mfi_now:.1f}, 15일전 {mfi_15d:.1f})")

    obv_now = float(row["obv"]) if pd.notna(row["obv"]) else 0.0
    obv_ma_now = float(row["obv_ma10"]) if pd.notna(row["obv_ma10"]) else 0.0
    if obv_now > obv_ma_now:
        score += 15.0
        reasons.append(f"조건 5-2 통과 (+15점): OBV 10일 이평 상회 골든크로스")
    else:
        reasons.append(f"조건 5-2 미충족 (+0점): OBV 10일 이평 하회")

    rsi_now = float(row["rsi"]) if pd.notna(row["rsi"]) else 0.0
    if params.rsi_min <= rsi_now <= params.rsi_max:
        score += 10.0
        reasons.append(f"조건 5-3 통과 (+10점): RSI 바닥권 건전한 턴 (RSI {rsi_now:.1f})")
    else:
        reasons.append(f"조건 5-3 미충족 (+0점): RSI 구간 이탈 (RSI {rsi_now:.1f}, 기준 {params.rsi_min:.1f}~{params.rsi_max:.1f})")

    # ----------------------------------------------------
    # [조건 6] 진입 트리거 (Trigger)
    # 1. Current Price >= MA5 and Current Price >= MA20: +10점
    # 2. MACD Histogram > 0 또는 직전 봉 대비 상승: +5점
    # ----------------------------------------------------
    ma5_val = float(row["ma5"]) if pd.notna(row["ma5"]) else 0.0
    ma20_val = float(row["ma20"]) if pd.notna(row["ma20"]) else 0.0
    if current_price >= ma5_val and current_price >= ma20_val:
        score += 10.0
        reasons.append(f"조건 6-1 통과 (+10점): 단기 이평선(5선/20선) 동시 상회 안착")
    else:
        reasons.append(f"조건 6-1 미충족 (+0점): 5선(${ma5_val:.2f}) 또는 20선(${ma20_val:.2f}) 하회")

    macd_hist_now = float(row["macd_hist"]) if pd.notna(row["macd_hist"]) else 0.0
    macd_hist_prev = float(prev_row["macd_hist"]) if pd.notna(prev_row["macd_hist"]) else 0.0
    if macd_hist_now > 0 or macd_hist_now > macd_hist_prev:
        score += 5.0
        reasons.append(f"조건 6-2 통과 (+5점): MACD 히스토그램 모멘텀 양전/상승 ({macd_hist_now:+.3f})")
    else:
        reasons.append(f"조건 6-2 미충족 (+0점): MACD 히스토그램 둔화 ({macd_hist_now:+.3f})")

    # 총점 상한 클리핑 (최대 100.0)
    technical_score = min(100.0, float(score))

    # ----------------------------------------------------
    # [별점 및 스윗스팟 판정]
    # ----------------------------------------------------
    is_sweet_spot = False
    is_overextended = False
    stars_rating = 1
    setup_type = "WYCKOFF_WEAK_SETUP"

    if strict_filter and is_failed:
        stars_rating = 1
        setup_type = "WYCKOFF_FILTER_REJECTED"
    else:
        if (
            params.sweet_spot_min_score <= technical_score <= params.sweet_spot_max_score
            and current_price >= poc_support_threshold
            and box_range <= params.sweet_spot_max_box
        ):
            stars_rating = 5
            is_sweet_spot = True
            setup_type = "WYCKOFF_SWEET_SPOT_LPS"
        elif 79.0 <= technical_score < params.overextended_score and current_price >= ma20_val:
            stars_rating = 4
            setup_type = "WYCKOFF_MARKUP_BREAKOUT"
        elif technical_score >= params.overextended_score:
            stars_rating = 2
            is_overextended = True
            setup_type = "WYCKOFF_OVEREXTENDED"
        elif 60.0 <= technical_score < params.sweet_spot_min_score:
            stars_rating = 3
            setup_type = "WYCKOFF_BASE_BUILDING"
        else:
            stars_rating = 1
            setup_type = "WYCKOFF_LOW_SCORE"

    # ----------------------------------------------------
    # 타점 및 SL / TP / 손익비(RR Ratio) 계산
    # ----------------------------------------------------
    stop_loss = round(box_low * (1.0 - params.sl_buffer_pct), 2)
    if stop_loss >= current_price or stop_loss <= 0:
        stop_loss = round(current_price * 0.95, 2)

    tp1 = round(current_price * (1.0 + params.tp1_pct), 2)
    tp2 = round(current_price * (1.0 + params.tp2_pct), 2)

    risk = current_price - stop_loss
    reward = tp1 - current_price
    rr_ratio = round(reward / risk, 2) if risk > 0 else 0.0

    return WyckoffSetupResult(
        ticker=ticker,
        score=technical_score,
        stars_rating=stars_rating,
        is_sweet_spot=is_sweet_spot,
        is_overextended=is_overextended,
        setup_type=setup_type,
        current_price=round(current_price, 2),
        daily_poc=round(daily_poc, 2),
        box_low=round(box_low, 2),
        box_high=round(box_high, 2),
        stop_loss=round(stop_loss, 2),
        tp1=round(tp1, 2),
        tp2=round(tp2, 2),
        rr_ratio=rr_ratio,
        reasons=reasons,
    )
