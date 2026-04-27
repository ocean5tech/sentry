#!/usr/bin/env python3
"""
A股翻倍主升浪形态统计.

定义:
  起点 T0:  close[T0] = window [T0-60, T0+60] 的最低点 (局部低点)
  峰值 T1:  T0 之后 252 天内的最高点
  翻倍:     close[T1] / close[T0] >= 2.0

分类 T0 前的结构 (回看90日):
  "二次启动": T_break ∈ [T0-90, T0-20] 为20日新高+放量,
              随后回调 >5%, 震荡期波动 <15%
  "一次启动": T0 之前没有显著突破, 直接从局部低点反转
  "其他":     介于之间

Usage:
    python3 scripts/analyze_doublings.py
"""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.data_loader import load_daily
from core.tdx_loader import list_tdx_symbols
from core.stock_names import get_names


def _load_mytt():
    spec = importlib.util.spec_from_file_location(
        "mytt", Path(__file__).parent.parent / "core" / "mytt.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


MA = _load_mytt().MA
HHV = _load_mytt().HHV


def board(symbol: str) -> str:
    if symbol.startswith(("00",)): return "深主板"
    if symbol.startswith(("60",)): return "沪主板"
    if symbol.startswith(("30",)): return "创业板"
    if symbol.startswith(("68",)): return "科创板"
    return "其他"


def find_doublings(df: pd.DataFrame, lookback_pre=90, lookforward=252, gain=2.0):
    """T0 = 突破日 (20日新高+放量), 其后 252 天内 low/close[T0] >= 2.0."""
    C = df["close"].values.astype(float)
    H = df["high"].values.astype(float)
    L = df["low"].values.astype(float)
    V = df["volume"].values.astype(float)
    n = len(C)
    if n < lookback_pre + lookforward + 10:
        return []

    prev_high_20 = pd.Series(H).rolling(20, min_periods=20).max().shift(1).values
    ma_v_5 = pd.Series(V).rolling(5, min_periods=5).mean().values
    prev_ma_v = pd.Series(ma_v_5).shift(1).values

    results = []
    last_t1 = -1
    for t0 in range(lookback_pre, n - lookforward):
        if t0 <= last_t1:
            continue
        if not np.isfinite(prev_high_20[t0]) or not np.isfinite(prev_ma_v[t0]):
            continue
        # 必须是 突破日
        if not (C[t0] > prev_high_20[t0] and V[t0] > prev_ma_v[t0] * 1.5):
            continue
        # 未来 252天内 close 最高日 T1, 用 low[T1] 作为保守峰值
        future_close = C[t0 + 1 : t0 + lookforward + 1]
        future_low   = L[t0 + 1 : t0 + lookforward + 1]
        if future_close.size == 0:
            continue
        t1_rel = int(np.argmax(future_close))
        peak_real = future_low[t1_rel]
        if peak_real / C[t0] < gain:
            continue
        t1 = t0 + 1 + t1_rel
        results.append((t0, t1, peak_real / C[t0]))
        last_t1 = t1
    return results


def classify_pattern(df: pd.DataFrame, t0: int, lookback=90) -> str:
    """判断 T0 启动之前 90 天的结构.

    "二次启动": 有先前突破 + 回调>=5% + T0前20天低震荡(<15%)
    "突破后冲高回落": 有先前突破 + 回调>=5% + T0前20天震荡太大
    "连续上涨": 有先前突破, 但没有回调 >5%
    "首次突破": 90 天内没有先前突破 (T0 是从底部/下降中的首次突破)
    """
    if t0 < lookback + 25:
        return "数据不足"
    C = df["close"].values.astype(float)
    H = df["high"].values.astype(float)
    V = df["volume"].values.astype(float)

    # 在 [T0-90, T0-10] 找早突破
    start, end = t0 - lookback, t0 - 10
    prev_high_20 = pd.Series(H).rolling(20, min_periods=20).max().shift(1).values
    ma_v_5 = pd.Series(V).rolling(5, min_periods=5).mean().values
    prev_ma_v = pd.Series(ma_v_5).shift(1).values

    breaks = []
    for k in range(start, end):
        if not np.isfinite(prev_high_20[k]) or not np.isfinite(prev_ma_v[k]):
            continue
        if C[k] > prev_high_20[k] and V[k] > prev_ma_v[k] * 1.5:
            breaks.append(k)
    if not breaks:
        return "首次突破"

    t_early = breaks[0]  # 最早的早突破
    early_price = C[t_early]

    between = C[t_early + 1 : t0]
    if between.size == 0 or between.min() >= early_price * 0.95:
        return "连续上涨"  # 突破后一路涨, 没有明显回调

    # 有回调, 检查 T0 前 20 天震荡
    recent = C[t0 - 20 : t0]
    if recent.size < 5:
        return "其他"
    recent_range = (recent.max() - recent.min()) / recent.min()
    if recent_range < 0.15:
        return "二次启动"
    return "突破后冲高回落"


def analyze(symbol: str, name: str):
    try:
        df = load_daily(symbol)
    except Exception:
        return []
    if df.empty or len(df) < 400:
        return []

    doublings = find_doublings(df)
    results = []
    for t0, t1, g in doublings:
        pat = classify_pattern(df, t0)
        results.append({
            "code":    symbol,
            "name":    name[:10],
            "board":   board(symbol),
            "t0_date": df["date"].iloc[t0].date().isoformat(),
            "t0_close": round(float(df["close"].iloc[t0]), 2),
            "t1_date": df["date"].iloc[t1].date().isoformat(),
            "t1_low":  round(float(df["low"].iloc[t1]), 2),
            "t1_close": round(float(df["close"].iloc[t1]), 2),
            "gain":    round(g, 2),
            "days_to_peak": t1 - t0,
            "pattern": pat,
        })
    return results


def main():
    names = get_names()
    symbols = list_tdx_symbols()
    print(f"扫描 {len(symbols)} 只股票, 寻找历史上的翻倍主升浪...")

    all_rows = []
    for i, sym in enumerate(symbols, 1):
        if i % 1000 == 0:
            print(f"  {i}/{len(symbols)}  累计翻倍行情: {len(all_rows)}")
        all_rows.extend(analyze(sym, names.get(sym, "")))

    if not all_rows:
        print("没找到翻倍行情")
        return

    df = pd.DataFrame(all_rows)
    out = Path(__file__).parent.parent / "logs" / "doublings.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n总计: {len(df)} 次翻倍主升浪")

    # ===== 启动形态分布 =====
    print("\n=== 启动形态分布 ===")
    pat = df["pattern"].value_counts()
    for p, c in pat.items():
        print(f"  {p:<10}: {c:>5}  ({c*100/len(df):5.1f}%)")

    # ===== 峰值天数分布 (整体) =====
    print("\n=== 从启动到峰值的天数 (整体) ===")
    d = df["days_to_peak"]
    print(f"  均值: {d.mean():.0f} 天    中位: {d.median():.0f} 天")
    print(f"  25%: {d.quantile(0.25):.0f} 天   75%: {d.quantile(0.75):.0f} 天   90%: {d.quantile(0.90):.0f} 天")
    bins = [0, 30, 60, 90, 120, 180, 252]
    labels = ["<30天", "30-60", "60-90", "90-120", "120-180", "180-252"]
    df["days_bin"] = pd.cut(d, bins=bins, labels=labels, include_lowest=True)
    dist = df["days_bin"].value_counts().sort_index()
    for b, c in dist.items():
        print(f"  {b:<10}: {c:>5}  ({c*100/len(df):5.1f}%)")

    # ===== 按启动形态分 =====
    print("\n=== 不同启动形态的天数/涨幅 ===")
    for p in df["pattern"].unique():
        sub = df[df["pattern"] == p]
        if len(sub) < 10:
            continue
        print(f"  {p:<10}  样本={len(sub):>5}  "
              f"峰值中位={sub['days_to_peak'].median():.0f}天  "
              f"峰值均值={sub['days_to_peak'].mean():.0f}天  "
              f"涨幅中位={sub['gain'].median():.1f}x  "
              f"涨幅均值={sub['gain'].mean():.1f}x")

    # ===== 按板块分 =====
    print("\n=== 按板块 ===")
    for b in df["board"].unique():
        sub = df[df["board"] == b]
        if len(sub) < 10:
            continue
        二次 = (sub["pattern"] == "二次启动").sum()
        print(f"  {b:<6} 样本={len(sub):>5}  二次启动占比={二次*100/len(sub):5.1f}%  "
              f"峰值中位={sub['days_to_peak'].median():.0f}天  "
              f"涨幅中位={sub['gain'].median():.1f}x")

    # ===== 按年份分 =====
    print("\n=== 按启动年份 ===")
    df["year"] = pd.to_datetime(df["t0_date"]).dt.year
    yearly = df.groupby("year").agg(
        count=("gain", "size"),
        二次启动占比=("pattern", lambda x: (x == "二次启动").mean() * 100),
        days_med=("days_to_peak", "median"),
        gain_med=("gain", "median"),
    )
    print(yearly.to_string(float_format=lambda v: f"{v:.1f}"))

    # ===== 按涨幅段分 =====
    print("\n=== 按涨幅段 ===")
    df["gain_bin"] = pd.cut(df["gain"],
                            bins=[2.0, 3.0, 5.0, 10.0, 100.0],
                            labels=["2-3倍", "3-5倍", "5-10倍", "10倍+"])
    by_gain = df.groupby("gain_bin", observed=True).agg(
        count=("gain", "size"),
        二次启动占比=("pattern", lambda x: (x == "二次启动").mean() * 100),
        days_med=("days_to_peak", "median"),
        days_mean=("days_to_peak", "mean"),
    )
    print(by_gain.to_string(float_format=lambda v: f"{v:.1f}"))

    print(f"\n详细结果: {out}")


if __name__ == "__main__":
    main()
