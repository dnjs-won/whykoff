"""
Whykoff Technical Indicators Module (engine/indicators.py)
Pure functional implementations of RSI, MACD, MFI, OBV, ATR, and Volume Profile POC.
Designed to be deterministic, free of repainting, and guarded against division-by-zero and missing data.
"""
from typing import Tuple
import numpy as np
import pandas as pd

from core.logger import get_logger

logger = get_logger("engine.indicators")


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    RSI (Relative Strength Index) 계산 (Wilder's Smoothing 방식)
    
    Args:
        series: 종가(Close) 시리즈
        period: 계산 기간 (기본 14)
        
    Returns:
        pd.Series: 0~100 범위의 RSI 값
    """
    if series.empty or len(series) < period:
        return pd.Series(np.nan, index=series.index)

    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    # Wilder's exponential smoothing (alpha = 1 / period)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))

    # 극단값 처리: avg_loss가 0이면 RSI=100, avg_gain이 0이면 RSI=0
    rsi = rsi.where(avg_loss != 0.0, 100.0)
    rsi = rsi.where(avg_gain != 0.0, 0.0)

    return rsi


def calculate_macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    MACD (Moving Average Convergence Divergence) 계산
    
    Args:
        series: 종가(Close) 시리즈
        fast: 단기 EMA 기간 (기본 12)
        slow: 장기 EMA 기간 (기본 26)
        signal: 시그널 EMA 기간 (기본 9)
        
    Returns:
        Tuple[pd.Series, pd.Series, pd.Series]: (macd_line, signal_line, histogram)
    """
    if series.empty or len(series) < slow:
        nan_series = pd.Series(np.nan, index=series.index)
        return nan_series, nan_series, nan_series

    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()

    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line

    return macd_line, signal_line, histogram


def calculate_mfi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    MFI (Money Flow Index, 자금 흐름 지수) 계산
    
    Args:
        df: high, low, close, volume 컬럼을 포함하는 DataFrame
        period: 계산 기간 (기본 14)
        
    Returns:
        pd.Series: 0~100 범위의 MFI 값
    """
    required_cols = {"high", "low", "close", "volume"}
    if not required_cols.issubset(df.columns) or len(df) < period + 1:
        return pd.Series(np.nan, index=df.index)

    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    raw_money_flow = typical_price * df["volume"]

    tp_diff = typical_price.diff()
    positive_flow = np.where(tp_diff > 0, raw_money_flow, 0.0)
    negative_flow = np.where(tp_diff < 0, raw_money_flow, 0.0)

    pos_mf = pd.Series(positive_flow, index=df.index).rolling(window=period).sum()
    neg_mf = pd.Series(negative_flow, index=df.index).rolling(window=period).sum()

    # MFI = 100 * (pos_mf / (pos_mf + neg_mf))
    total_flow = pos_mf + neg_mf
    mfi = 100.0 * (pos_mf / total_flow.replace(0.0, np.nan))

    # 분모 0 보정 (자금 유입과 유출이 모두 0이면 중립 50.0, 유출만 0이면 100.0)
    mfi = mfi.where(total_flow != 0.0, 50.0)
    mfi = mfi.where(neg_mf != 0.0, 100.0)

    return mfi


def calculate_obv(df: pd.DataFrame, ma_window: int = 10) -> Tuple[pd.Series, pd.Series]:
    """
    OBV (On-Balance Volume) 및 OBV 이동평균 계산
    
    Args:
        df: close, volume 컬럼을 포함하는 DataFrame
        ma_window: OBV 이동평균 기간 (기본 10)
        
    Returns:
        Tuple[pd.Series, pd.Series]: (obv, obv_ma)
    """
    if "close" not in df.columns or "volume" not in df.columns or df.empty:
        nan_series = pd.Series(np.nan, index=df.index)
        return nan_series, nan_series

    close_diff = df["close"].diff()
    direction = np.where(close_diff > 0, 1, np.where(close_diff < 0, -1, 0))
    # 첫 봉은 변동 없으므로 0 처리
    if len(direction) > 0:
        direction[0] = 0

    obv = (pd.Series(direction, index=df.index) * df["volume"]).cumsum()
    obv_ma = obv.rolling(window=ma_window).mean()

    return obv, obv_ma


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    ATR (Average True Range) 계산 (Wilder's Smoothing 방식)
    
    Args:
        df: high, low, close 컬럼을 포함하는 DataFrame
        period: 계산 기간 (기본 14)
        
    Returns:
        pd.Series: ATR 변동성 수치
    """
    required_cols = {"high", "low", "close"}
    if not required_cols.issubset(df.columns) or len(df) < period:
        return pd.Series(np.nan, index=df.index)

    prev_close = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - prev_close).abs()
    tr3 = (df["low"] - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    return atr


def calculate_volume_profile_poc(
    df: pd.DataFrame,
    lookback: int = 120,
    bins: int = 40,
) -> float:
    """
    최근 lookback(기본 120일) 거래일간의 볼륨 프로파일 최대 매물대(Point of Control, POC) 가격 산출.
    CORE_LOGIC_SPECS.md 규격: 최근 120일간의 가격 범위를 40개 구간으로 분할하고 누적 거래량이 가장 큰 구간의 중심가격을 POC로 정의.
    
    Args:
        df: high, low, close, volume 컬럼을 포함하는 DataFrame
        lookback: 분석 캔들 개수 (기본 120)
        bins: 가격 분할 구간 수 (기본 40)
        
    Returns:
        float: 최대 거래량 매물대(POC) 중심 가격
    """
    if df.empty:
        return 0.0

    sub_df = df.iloc[-lookback:] if len(df) >= lookback else df
    low_min = float(sub_df["low"].min())
    high_max = float(sub_df["high"].max())

    if low_min <= 0 or high_max <= low_min or sub_df["volume"].sum() <= 0:
        return float(sub_df["close"].iloc[-1])

    # 각 봉의 대표 가격 (Typical Price)
    typical_prices = (sub_df["high"] + sub_df["low"] + sub_df["close"]) / 3.0
    volumes = sub_df["volume"].values

    # 40개 구간(Bin) 생성
    bin_edges = np.linspace(low_min, high_max, bins + 1)
    hist, _ = np.histogram(typical_prices.values, bins=bin_edges, weights=volumes)

    max_bin_idx = int(np.argmax(hist))
    poc_price = (bin_edges[max_bin_idx] + bin_edges[max_bin_idx + 1]) / 2.0

    return round(float(poc_price), 4)


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    입력 DataFrame의 복사본을 생성하고 와이코프 및 컨플루언스 분석에 필요한 모든 기술적 지표 컬럼을 순수 함수로 추가.
    
    추가되는 컬럼:
    - ma5, ma20, ma50, ma120, ma200: 단순 이동평균선
    - ma20_slope_10d: 10일 전 대비 20일선 기울기 (%)
    - rsi: RSI (14)
    - macd, macd_signal, macd_hist: MACD (12, 26, 9)
    - mfi: Money Flow Index (14)
    - mfi_15d_ago: 15일 전 MFI (수급 턴 판별용)
    - obv, obv_ma10: OBV 및 10일 이동평균
    - atr: Average True Range (14)
    
    Args:
        df: OHLCV DataFrame
        
    Returns:
        pd.DataFrame: 지표가 계산되어 추가된 신규 DataFrame
    """
    if df.empty:
        return df.copy()

    res = df.copy()

    # 1. 이동평균선 (SMA)
    for window in [5, 20, 50, 120, 200]:
        res[f"ma{window}"] = res["close"].rolling(window=window).mean()

    # 2. 20일선 10일 대비 기울기 (%)
    # Slope = (MA20_today - MA20_10d_ago) / MA20_10d_ago * 100
    ma20_10d_ago = res["ma20"].shift(10)
    res["ma20_slope_10d"] = ((res["ma20"] - ma20_10d_ago) / ma20_10d_ago.replace(0.0, np.nan)) * 100.0

    # 3. RSI (14)
    res["rsi"] = calculate_rsi(res["close"], period=14)

    # 4. MACD (12, 26, 9)
    res["macd"], res["macd_signal"], res["macd_hist"] = calculate_macd(res["close"])

    # 5. MFI (14) 및 15일 전 MFI
    res["mfi"] = calculate_mfi(res, period=14)
    res["mfi_15d_ago"] = res["mfi"].shift(15)

    # 6. OBV 및 OBV 10일 MA
    res["obv"], res["obv_ma10"] = calculate_obv(res, ma_window=10)

    # 7. ATR (14)
    res["atr"] = calculate_atr(res, period=14)

    return res
