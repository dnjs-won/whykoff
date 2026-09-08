"""
Whykoff Single Ticker Diagnostic Inspector (services/ticker_inspector.py)
Enables on-demand inspection of individual stocks:
- Evaluates 6-step Wyckoff accumulation rules, Volume POC support, and Overhead Space Gate.
- Pinpoints EXACT reasons why a stock is NOT captured or filtered out.
- Formats structured reports for CLI and Telegram (/check TICKER).
"""
from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np

from core.database import load_candles_df, get_db_cursor
from core.logger import get_logger
from engine.indicators import add_all_indicators, calculate_volume_profile_poc
from engine.wyckoff_scanner import evaluate_wyckoff_setup, WyckoffParams

logger = get_logger("services.ticker_inspector")


def inspect_single_ticker(
    ticker: str,
    auto_collect: bool = True,
    params: Optional[WyckoffParams] = None,
) -> Dict[str, Any]:
    """
    개별 종목 정밀 진단 및 와이코프 매집 적합성 분석.
    
    Args:
        ticker: 종목 티커 심볼 (예: 'NVDA', 'AAPL', 'TSLA')
        auto_collect: DB 데이터 부족 시 Yahoo Finance에서 즉시 자동 수집 여부 (기본: True)
        params: 와이코프 스캐너 파라미터 (None이면 챔피언 기본값)
        
    Returns:
        Dict[str, Any]: 정밀 진단 결과 딕셔너리
    """
    sym = ticker.strip().upper()
    if params is None:
        params = WyckoffParams()

    # 1. 일봉 데이터 로드 (최소 120봉 확보)
    df = load_candles_df(sym, timeframe="daily", limit=160)

    # 데이터 부족 시 yfinance 자동 수집 시도
    if (df.empty or len(df) < 120) and auto_collect:
        logger.info(f"📥 [{sym}] Insufficient candles in DB ({len(df)} bars). Attempting on-demand collection from Yahoo Finance...")
        try:
            from collectors.market_collector import collect_daily_candles
            collect_daily_candles([sym], period="1y")
            df = load_candles_df(sym, timeframe="daily", limit=160)
        except Exception as e:
            logger.warning(f"[{sym}] On-demand collection failed: {e}")

    if df.empty or len(df) < 120:
        return {
            "status": "ERROR_INSUFFICIENT_DATA",
            "ticker": sym,
            "candle_count": len(df),
            "message": f"현재 DB에 보유한 일봉 데이터가 {len(df)}개로 최소 필요 기준(120봉)에 미달합니다. (유효하지 않은 티커이거나 상장 기간 부족)",
        }

    # 2. 지표 일괄 계산
    df_calc = add_all_indicators(df)
    row = df_calc.iloc[-1]
    prev_row = df_calc.iloc[-2]

    # 기본 주가 정보
    current_price = float(row["close"])
    prev_close = float(prev_row["close"])
    change_pct = ((current_price - prev_close) / prev_close) * 100.0 if prev_close > 0 else 0.0
    latest_date = str(row["datetime"])[:10]
    volume = int(row["volume"])

    # 3. 와이코프 스캐너 비엄격 평가 (점수 및 사유 전수 도출)
    setup = evaluate_wyckoff_setup(
        df_calc,
        ticker=sym,
        as_of_latest=True,
        strict_filter=False,
        params=params,
    )

    if not setup:
        return {
            "status": "ERROR_EVALUATION_FAILED",
            "ticker": sym,
            "message": "종목 분석 과정에서 오류가 발생했습니다.",
        }

    # 4. 세부 지표 값 추출
    sub_120 = df_calc.iloc[-120:]
    sub_30 = df_calc.iloc[-30:]

    high_120d = float(sub_120["high"].max())
    drop_rate_pct = ((current_price - high_120d) / high_120d) * 100.0 if high_120d > 0 else 0.0

    box_high = float(sub_30["high"].max())
    box_low = float(sub_30["low"].min())
    box_range_pct = ((box_high - box_low) / box_low) * 100.0 if box_low > 0 else 0.0

    ma5 = float(row["ma5"]) if pd.notna(row["ma5"]) else 0.0
    ma20 = float(row["ma20"]) if pd.notna(row["ma20"]) else 0.0
    ma60 = float(row["ma60"]) if pd.notna(row.get("ma60")) else 0.0
    slope_ma20 = float(row["ma20_slope_10d"]) if pd.notna(row["ma20_slope_10d"]) else 0.0

    poc = calculate_volume_profile_poc(df_calc, lookback=120, bins=40)
    poc_threshold = poc * params.poc_support_buffer
    poc_dist_pct = ((current_price - poc) / poc) * 100.0 if poc > 0 else 0.0

    mfi = float(row["mfi"]) if pd.notna(row["mfi"]) else 0.0
    mfi_15d = float(row["mfi_15d_ago"]) if pd.notna(row["mfi_15d_ago"]) else 0.0
    obv = float(row["obv"]) if pd.notna(row["obv"]) else 0.0
    obv_ma10 = float(row["obv_ma10"]) if pd.notna(row["obv_ma10"]) else 0.0
    rsi = float(row["rsi"]) if pd.notna(row["rsi"]) else 0.0
    macd_hist = float(row["macd_hist"]) if pd.notna(row["macd_hist"]) else 0.0

    cloud_bottom = float(row["cloud_bottom"]) if pd.notna(row.get("cloud_bottom")) else 0.0
    cloud_top = float(row["cloud_top"]) if pd.notna(row.get("cloud_top")) else 0.0
    overhead_space = setup.overhead_space_pct

    # 5. 미포착 / 감점 / 탈락 원인 정밀 추출 (Failures & Warnings)
    disqualification_reasons = []

    # ① 낙폭 검사
    is_drop_ok = drop_rate_pct <= params.min_drop_rate
    if not is_drop_ok:
        disqualification_reasons.append(
            f"120일 고점(${high_120d:.2f}) 대비 낙폭 미달 (현재 낙폭 {drop_rate_pct:+.1f}% > 기준 {params.min_drop_rate:.1f}% 이하) "
            f"➔ 아직 고점권이거나 가격/기간 조정이 충분하지 않음"
        )

    # ② 30일 박스권 진폭 검사
    is_box_ok = box_range_pct <= params.max_box_range
    if not is_box_ok:
        disqualification_reasons.append(
            f"30일 박스권 진폭 과대 (현재 {box_range_pct:.1f}% > 기준 {params.max_box_range:.1f}% 이하) "
            f"➔ 변동성이 커서 톱질(Whipsaw) 휩소 손절 위험 높음"
        )

    # ③ 20일선 기울기 검사
    is_slope_ok = slope_ma20 >= params.ma20_slope_min
    if not is_slope_ok:
        disqualification_reasons.append(
            f"20일 이동평균선 급락 추세 (10일 기울기 {slope_ma20:.2f}% < 기준 {params.ma20_slope_min:.1f}%) "
            f"➔ '떨어지는 칼날' 하락 추세 미진정"
        )

    # ④ POC 매물대 안착 검사
    is_poc_ok = current_price >= poc_threshold
    if not is_poc_ok:
        disqualification_reasons.append(
            f"120일 볼륨 POC 매물대 하방 갇힘 (현재가 ${current_price:.2f} < POC ${poc:.2f}) "
            f"➔ 머리 위에 강력한 기관 매물벽 저항이 존재함"
        )

    # ⑤ 상단 공간 게이트 검사 (Overhead Space Gate)
    is_overhead_ok = True
    if cloud_bottom > 0 and current_price < cloud_bottom:
        if overhead_space is not None and overhead_space < params.min_overhead_space_pct:
            is_overhead_ok = False
            disqualification_reasons.append(
                f"상단 저항선(구름대/60일선)까지 잔여 공간 부족 (+{overhead_space:.1f}% < 기준 +{params.min_overhead_space_pct:.1f}% 이상) "
                f"➔ 진입 시 즉시 상단 저항에 부딪혀 반락할 위험"
            )

    # ⑥ 5일선 안전벨트
    is_ma5_ok = current_price >= ma5
    if not is_ma5_ok and params.require_ma5_recovery:
        disqualification_reasons.append(
            f"일봉 5선(${ma5:.2f}) 미회복 (현재가 ${current_price:.2f}) "
            f"➔ 단기 바닥 턴어라운드 안전벨트 미체결"
        )

    # ⑦ 점수 미달 또는 과열 경고
    if setup.score < 68.0:
        disqualification_reasons.append(
            f"종합 기술 점수 미달 ({setup.score:.0f}점 < 최소 진입 기준 68.0점) "
            f"➔ 수급(MFI/OBV) 및 캔들 안착 점수 부족"
        )
    elif setup.is_overextended:
        disqualification_reasons.append(
            f"단기 과열 경고 (점수 {setup.score:.0f}점 >= 85점) "
            f"➔ 바닥권 진입 단계를 벗어나 이미 급등한 상태 (추격매수 금지 2성 경고)"
        )

    # 포착 여부 판정
    is_captured = (setup.stars_rating >= 4 or setup.is_sweet_spot) and not setup.is_overextended

    return {
        "status": "SUCCESS",
        "ticker": sym,
        "latest_date": latest_date,
        "current_price": current_price,
        "prev_close": prev_close,
        "change_pct": round(change_pct, 2),
        "volume": volume,
        "score": setup.score,
        "stars_rating": setup.stars_rating,
        "is_sweet_spot": setup.is_sweet_spot,
        "is_overextended": setup.is_overextended,
        "setup_type": setup.setup_type,
        "is_captured": is_captured,
        # 가격대 및 지지저항
        "high_120d": round(high_120d, 2),
        "drop_rate_pct": round(drop_rate_pct, 1),
        "box_high": round(box_high, 2),
        "box_low": round(box_low, 2),
        "box_range_pct": round(box_range_pct, 1),
        "daily_poc": round(poc, 2),
        "poc_dist_pct": round(poc_dist_pct, 1),
        "ma5": round(ma5, 2),
        "ma20": round(ma20, 2),
        "ma60": round(ma60, 2),
        "slope_ma20": round(slope_ma20, 2),
        "cloud_bottom": round(cloud_bottom, 2) if cloud_bottom > 0 else None,
        "cloud_top": round(cloud_top, 2) if cloud_top > 0 else None,
        "overhead_space_pct": round(overhead_space, 1) if overhead_space is not None else None,
        # 수급 지표
        "mfi": round(mfi, 1),
        "mfi_15d": round(mfi_15d, 1),
        "obv_golden": obv > obv_ma10,
        "rsi": round(rsi, 1),
        "macd_hist": round(macd_hist, 3),
        # 타점 가이드
        "stop_loss": setup.stop_loss,
        "tp1": setup.tp1,
        "tp2": setup.tp2,
        "rr_ratio": setup.rr_ratio,
        # 사유 리스트
        "all_reasons": setup.reasons,
        "disqualification_reasons": disqualification_reasons,
    }


def format_inspection_telegram(diag: Dict[str, Any]) -> str:
    """개별 종목 정밀 진단 결과 텔레그램 HTML 포맷 변환"""
    if diag.get("status") != "SUCCESS":
        return f"⚠️ <b>[{diag.get('ticker', 'UNKNOWN')}] 진단 실패</b>\n\n• {diag.get('message', '알 수 없는 오류')}"

    ticker = diag["ticker"]
    curr_p = diag["current_price"]
    chg_p = diag["change_pct"]
    sign = "+" if chg_p >= 0 else ""
    date_str = diag["latest_date"]
    score = diag["score"]
    stars = "⭐" * diag["stars_rating"]

    # 판정 배지
    if diag["is_sweet_spot"]:
        badge = "🔥 <b>[5성 LPS 스윗스팟 포착 통과!]</b>"
        summary_verdict = "현재 바닥 매집 완료 후 1차 돌파 직후의 <b>최적의 스윙 진입 적기</b>입니다."
    elif diag["stars_rating"] >= 4:
        badge = "⚡ <b>[4성 마크업 돌파 포착 통과!]</b>"
        summary_verdict = "박스권 상단 돌파 및 상승 추세 안착 신호입니다."
    elif diag["is_overextended"]:
        badge = "⚠️ <b>[2성 단기 과열 경고 - 추격매수 금지!]</b>"
        summary_verdict = "점수 85점 이상으로 이미 단기 급등하여 차익 실현 위험이 높습니다."
    elif diag["stars_rating"] == 3:
        badge = "⏳ <b>[3성 바닥 매집 진행 중]</b>"
        summary_verdict = "에너지를 모으는 중이나 아직 결정적 돌파 트리거가 부족합니다. (관심종목 등록)"
    else:
        badge = "❌ <b>[시스템 미포착 (기준 미달)]</b>"
        summary_verdict = "와이코프 매집 퀀트 필터를 통과하지 못했습니다. (사유 하단 참조)"

    lines = [
        f"🔎 <b>[Whykoff 개별종목 정밀 진단] {ticker}</b>",
        f"📅 기준일자: <code>{date_str}</code> | 현재가: <code>${curr_p:.2f}</code> ({sign}{chg_p:.2f}%)",
        f"{'=' * 32}",
        f"• <b>종합 진단:</b> {badge}",
        f"• <b>기술 점수:</b> <b>{score:.0f}점</b> ({stars})",
        f"• <b>총평:</b> {summary_verdict}\n",
        "📊 <b>[핵심 조건별 충족 상태]</b>",
        f"1. <b>120일 고점 낙폭:</b> <code>{diag['drop_rate_pct']:+.1f}%</code> (고점 <code>${diag['high_120d']:.2f}</code>) {'✅' if diag['drop_rate_pct'] <= -25.0 else '❌'}",
        f"2. <b>30일 박스 진폭:</b> <code>{diag['box_range_pct']:.1f}%</code> (<code>${diag['box_low']:.2f} ~ ${diag['box_high']:.2f}</code>) {'✅' if diag['box_range_pct'] <= 20.0 else '❌'}",
        f"3. <b>20일선 기울기:</b> <code>{diag['slope_ma20']:+.2f}%</code> (평탄화 지지) {'✅' if diag['slope_ma20'] >= -1.5 else '❌'}",
        f"4. <b>120일 볼륨 POC:</b> <code>${diag['daily_poc']:.2f}</code> (이격 <code>{diag['poc_dist_pct']:+.1f}%</code>) {'✅' if curr_p >= diag['daily_poc'] * 0.995 else '❌'}",
    ]

    # 상단 공간 게이트
    if diag.get("cloud_bottom"):
        sp = diag.get("overhead_space_pct")
        sp_str = f"+{sp:.1f}%" if sp is not None else "N/A"
        sp_icon = "✅" if (sp is not None and sp >= 5.0) or curr_p >= diag["cloud_bottom"] else "❌"
        lines.append(f"5. <b>상단 저항 공간:</b> <code>{sp_str}</code> (구름대 <code>${diag['cloud_bottom']:.2f}</code>) {sp_icon}")

    # 수급
    lines.append(f"6. <b>스마트머니 수급:</b> MFI <code>{diag['mfi']:.1f}</code> | OBV <code>{'골든크로스 🟢' if diag['obv_golden'] else '하회 ⚪'}</code> | RSI <code>{diag['rsi']:.1f}</code>\n")

    # 미포착 사유 강조
    if diag["disqualification_reasons"]:
        lines.append("🚫 <b>[시스템에 포착되지 않는 이유]</b>")
        for reason in diag["disqualification_reasons"]:
            lines.append(f"  • {reason}")
        lines.append("")
    else:
        lines.append("🎯 <b>[포착 완료]:</b> 모든 엄격한 와이코프 매집 관문을 통과했습니다!\n")

    # 매매 타점 가이드
    lines.extend([
        "🧭 <b>[참고 타점 가이드]</b>",
        f"• <b>손절선 (SL):</b> <code>${diag['stop_loss']:.2f}</code> (박스 하단 -2.5% 버퍼)",
        f"• <b>1차 목표가 (TP1):</b> <code>${diag['tp1']:.2f}</code> (+20.0%)",
        f"• <b>2차 목표가 (TP2):</b> <code>${diag['tp2']:.2f}</code> (+50.0%)",
        f"• <b>손익비 (R:R):</b> <code>{diag['rr_ratio']:.2f}:1</code>",
    ])

    return "\n".join(lines)


def format_inspection_cli(diag: Dict[str, Any]) -> str:
    """개별 종목 정밀 진단 결과 CLI 텍스트 포맷 변환"""
    if diag.get("status") != "SUCCESS":
        return f"[!] [{diag.get('ticker', 'UNKNOWN')}] 진단 실패: {diag.get('message')}"

    ticker = diag["ticker"]
    curr_p = diag["current_price"]
    chg_p = diag["change_pct"]
    sign = "+" if chg_p >= 0 else ""
    date_str = diag["latest_date"]
    score = diag["score"]
    stars = "★" * diag["stars_rating"] + "☆" * (5 - diag["stars_rating"])

    lines = [
        "=" * 70,
        f"  🔎 [Whykoff 개별종목 정밀 진단 보고서] {ticker}",
        "=" * 70,
        f"  • 기준일자: {date_str}  |  현재가: ${curr_p:.2f} ({sign}{chg_p:.2f}%)",
        f"  • 기술 점수: {score:.0f}점 / 100점 ({stars})",
        f"  • 포착 판정: {'[포착 통과]' if diag['is_captured'] else '[미포착 (기준 미달)]'}",
        "-" * 70,
        "  [1] 6대 와이코프 조건 판정 현황:",
        f"    - 조건 1 (120일 낙폭): {diag['drop_rate_pct']:+.1f}% (고점 ${diag['high_120d']:.2f}) {'[PASS]' if diag['drop_rate_pct'] <= -25.0 else '[FAIL]'}",
        f"    - 조건 2 (30일 박스진폭): {diag['box_range_pct']:.1f}% (${diag['box_low']:.2f} ~ ${diag['box_high']:.2f}) {'[PASS]' if diag['box_range_pct'] <= 20.0 else '[FAIL]'}",
        f"    - 조건 3 (20일선 기울기): {diag['slope_ma20']:+.2f}% {'[PASS]' if diag['slope_ma20'] >= -1.5 else '[FAIL]'}",
        f"    - 조건 4 (120일 볼륨 POC): ${diag['daily_poc']:.2f} (이격 {diag['poc_dist_pct']:+.1f}%) {'[PASS]' if curr_p >= diag['daily_poc'] * 0.995 else '[FAIL]'}",
    ]

    if diag.get("cloud_bottom"):
        sp = diag.get("overhead_space_pct")
        sp_str = f"+{sp:.1f}%" if sp is not None else "N/A"
        sp_status = "[PASS]" if (sp is not None and sp >= 5.0) or curr_p >= diag["cloud_bottom"] else "[BLOCKED]"
        lines.append(f"    - 상단 공간 게이트: 잔여 공간 {sp_str} (구름대하단 ${diag['cloud_bottom']:.2f}) {sp_status}")

    lines.append(
        f"    - 수급 지표: MFI {diag['mfi']:.1f} | OBV {'GoldenCross' if diag['obv_golden'] else 'Under'} | RSI {diag['rsi']:.1f}"
    )

    lines.append("-" * 70)
    if diag["disqualification_reasons"]:
        lines.append("  [2] 시스템에 포착되지 않는 구체적 이유:")
        for r in diag["disqualification_reasons"]:
            lines.append(f"    ❌ {r}")
    else:
        lines.append("  [2] 포착 상태: 모든 엄격한 와이코프 매집 게이트를 완벽히 통과했습니다!")

    lines.extend([
        "-" * 70,
        "  [3] 타점 및 리스크 관리 가이드:",
        f"    • 손절선 (SL): ${diag['stop_loss']:.2f} (박스 하단 -2.5% 버퍼)",
        f"    • 1차 목표가 (TP1): ${diag['tp1']:.2f} (+20.0%)",
        f"    • 2차 목표가 (TP2): ${diag['tp2']:.2f} (+50.0%)",
        f"    • 손익비 (R:R Ratio): {diag['rr_ratio']:.2f}:1",
        "=" * 70,
    ])

    return "\n".join(lines)
