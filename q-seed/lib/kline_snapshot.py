"""
K 线快照: 给每个候选股算 vwap / ret / amplitude / kline_safety 字段.
为 dashboard 直接使用而设计 (不需要重调 akshare).
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class SafetyThresholds:
    """kline_safety 4 档阈值, 从 config.kline_safety.thresholds 加载."""
    red: float = 1.30      # >= VWAP60 × red → 🔴
    yellow: float = 1.10   # 1.10 ~ red → 🟡
    green: float = 0.90    # 0.90 ~ yellow → 🟢
    # < green → ⚪
    vwap_window: int = 60

    @classmethod
    def from_dict(cls, ks_cfg: dict) -> "SafetyThresholds":
        th = ks_cfg.get("thresholds", {})
        return cls(
            red=th.get("red", 1.30),
            yellow=th.get("yellow", 1.10),
            green=th.get("green", 0.90),
            vwap_window=ks_cfg.get("vwap_window", 60),
        )


def _safe_pct(numer: float, denom: float) -> float:
    if denom is None or denom == 0 or np.isnan(denom):
        return 0.0
    return float(numer / denom - 1)


def _vwap(close: np.ndarray, volume: np.ndarray, n: int) -> float:
    """末 N 日 VWAP. 若数据不够 N 日, 用全部."""
    if len(close) == 0:
        return 0.0
    n = min(n, len(close))
    c = close[-n:]
    v = volume[-n:]
    if v.sum() <= 0:
        return float(c.mean())
    return float((c * v).sum() / v.sum())


def _classify_safety(curr: float, vwap60: float, th: SafetyThresholds) -> str:
    if vwap60 <= 0:
        return "⚪"
    ratio = curr / vwap60
    if ratio >= th.red:
        return "🔴"
    elif ratio >= th.yellow:
        return "🟡"
    elif ratio >= th.green:
        return "🟢"
    else:
        return "⚪"


def make_snapshot(df: pd.DataFrame, th: SafetyThresholds) -> dict:
    """对一只股票的日线 df 生成 K 线快照字典 (写入输出 schema 的 'kline' 段)."""
    if df is None or df.empty:
        return {}
    C = df["close"].values.astype(float)
    H = df["high"].values.astype(float)
    L = df["low"].values.astype(float)
    V = df["volume"].values.astype(float)
    dates = df["date"].dt.date.values

    curr = float(C[-1])
    vwap20 = _vwap(C, V, 20)
    vwap60 = _vwap(C, V, th.vwap_window)
    vwap120 = _vwap(C, V, 120)

    def _last_n_high(n): return float(H[-n:].max()) if len(H) >= 1 else 0.0
    def _last_n_low(n): return float(L[-n:].min()) if len(L) >= 1 else 0.0

    def _ret(n):
        if len(C) <= n:
            return 0.0
        return _safe_pct(C[-1], C[-(n+1)])

    snap = {
        "current_price": curr,
        "current_date": dates[-1].isoformat(),
        "vwap20": round(vwap20, 2),
        "vwap20_dev": round(_safe_pct(curr, vwap20), 4),
        "vwap60": round(vwap60, 2),
        "vwap60_dev": round(_safe_pct(curr, vwap60), 4),
        "vwap120": round(vwap120, 2),
        "vwap120_dev": round(_safe_pct(curr, vwap120), 4),
        "high60": round(_last_n_high(60), 2),
        "low60": round(_last_n_low(60), 2),
        "high120": round(_last_n_high(120), 2),
        "low120": round(_last_n_low(120), 2),
        "ret5": round(_ret(5), 4),
        "ret20": round(_ret(20), 4),
        "ret60": round(_ret(60), 4),
        "ret120": round(_ret(120), 4),
        "amplitude_5d": round(
            float((H[-5:].max() - L[-5:].min()) / L[-5:].min())
            if len(L) >= 5 and L[-5:].min() > 0 else 0.0,
            4
        ),
        "volume_ratio_5d_20d": round(
            float(V[-5:].mean() / V[-20:].mean())
            if len(V) >= 20 and V[-20:].mean() > 0 else 0.0,
            2
        ),
        "kline_safety": _classify_safety(curr, vwap60, th),
    }
    return snap
