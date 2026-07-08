"""
백테스트 엔진 (backtesting.py 래퍼)
====================================
전략 실행 → 성과지표(metrics) + 매매기록(markers) 반환.
markers 는 Lightweight Charts 의 setMarkers() 에 바로 넣는 포맷.
"""
import math

import pandas as pd
from backtesting import Backtest


def _num(x):
    """numpy/nan/inf 를 JSON 안전한 값으로."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, 4)


def run_backtest(df: pd.DataFrame, strat_cls, params: dict,
                 cash: float = 10_000_000, commission: float = 0.0005):
    if df.empty or len(df) < 30:
        raise ValueError("백테스트에 필요한 데이터가 부족합니다.")

    bt = Backtest(
        df, strat_cls,
        cash=cash, commission=commission,
        exclusive_orders=True,   # 반대 신호 시 기존 포지션 정리
        trade_on_close=False,
    )
    stats = bt.run(**(params or {}))

    # ---- 매매기록 → 마커 ----
    markers = []
    trades = stats["_trades"]
    for _, t in trades.iterrows():
        long = t["Size"] > 0
        markers.append({
            "time": pd.Timestamp(t["EntryTime"]).strftime("%Y-%m-%d"),
            "position": "belowBar" if long else "aboveBar",
            "color": "#2962FF",
            "shape": "arrowUp" if long else "arrowDown",
            "text": f"진입 {t['EntryPrice']:.0f}",
        })
        win = t["PnL"] >= 0
        markers.append({
            "time": pd.Timestamp(t["ExitTime"]).strftime("%Y-%m-%d"),
            "position": "aboveBar" if long else "belowBar",
            "color": "#26a69a" if win else "#ef5350",
            "shape": "arrowDown" if long else "arrowUp",
            "text": f"청산 {t['ExitPrice']:.0f} ({t['PnL']:+.0f})",
        })
    markers.sort(key=lambda m: m["time"])

    # ---- 성과지표 ----
    metrics = {
        "return_pct": _num(stats.get("Return [%]")),
        "buyhold_pct": _num(stats.get("Buy & Hold Return [%]")),
        "cagr_pct": _num(stats.get("CAGR [%]")),
        "winrate_pct": _num(stats.get("Win Rate [%]")),
        "mdd_pct": _num(stats.get("Max. Drawdown [%]")),
        "sharpe": _num(stats.get("Sharpe Ratio")),
        "sortino": _num(stats.get("Sortino Ratio")),
        "profit_factor": _num(stats.get("Profit Factor")),
        "num_trades": int(stats.get("# Trades", 0)),
        "exposure_pct": _num(stats.get("Exposure Time [%]")),
        "start": str(stats.get("Start", "")).split(" ")[0],
        "end": str(stats.get("End", "")).split(" ")[0],
    }

    # ---- 자본곡선(선택: 차트 하단 표시용) ----
    eq = stats["_equity_curve"]["Equity"]
    equity = [
        {"time": idx.strftime("%Y-%m-%d"), "value": _num(v)}
        for idx, v in eq.items()
    ]

    return {"metrics": metrics, "markers": markers, "equity": equity}
