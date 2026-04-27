"""Layer 2 K 线快照 + 4 档 safety. 复用 q-seed kline_snapshot 思路, 独立实现."""

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class SafetyThresholds:
    red: float = 1.30
    yellow: float = 1.10
    green: float = 0.90
    vwap_window: int = 60


def _vwap(close, volume, n: int) -> float:
    if len(close) == 0:
        return 0.0
    n = min(n, len(close))
    c, v = close[-n:], volume[-n:]
    return float((c * v).sum() / v.sum()) if v.sum() > 0 else float(c.mean())


def _ret(close, n: int) -> float:
    if len(close) <= n:
        return 0.0
    return float(close[-1] / close[-(n+1)] - 1)


def _classify(curr: float, vwap60: float, th: SafetyThresholds) -> str:
    if vwap60 <= 0:
        return "⚪"
    r = curr / vwap60
    if r >= th.red:
        return "🔴"
    if r >= th.yellow:
        return "🟡"
    if r >= th.green:
        return "🟢"
    return "⚪"


def make_snapshot(df: pd.DataFrame, th: SafetyThresholds) -> dict:
    if df is None or df.empty:
        return {}
    C = df["close"].values.astype(float)
    H = df["high"].values.astype(float)
    L = df["low"].values.astype(float)
    V = df["volume"].values.astype(float)
    dates = df["date"].dt.date.values

    curr = float(C[-1])
    v60 = _vwap(C, V, th.vwap_window)
    return {
        "current_price": curr,
        "current_date": dates[-1].isoformat(),
        "vwap20": round(_vwap(C, V, 20), 2),
        "vwap60": round(v60, 2),
        "vwap60_dev": round(curr / v60 - 1, 4) if v60 > 0 else 0.0,
        "vwap120": round(_vwap(C, V, 120), 2),
        "high60": round(float(H[-60:].max()), 2) if len(H) >= 1 else 0,
        "low60": round(float(L[-60:].min()), 2) if len(L) >= 1 else 0,
        "ret5": round(_ret(C, 5), 4),
        "ret20": round(_ret(C, 20), 4),
        "ret60": round(_ret(C, 60), 4),
        "ret120": round(_ret(C, 120), 4),
        "kline_safety": _classify(curr, v60, th),
    }
