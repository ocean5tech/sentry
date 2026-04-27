#!/usr/bin/env python3
"""
验证假说: 破冰首板效应.

  过去 N 日 (默认 60≈3 个月) 无涨停, 突然出现涨停 (Ice_LU) ->
  未来 K 日 (默认 10) 再次涨停的概率, 是否高于普通涨停 (All_LU) 与基线 (任意日).

输出:
  - Baseline / All_LU / Ice_LU 三组的 p_future_lu (未来 K 日内有涨停)
  - 按板别拆分
  - 按 ice_n 敏感性 (30/60/90/120)
  - 按 future_k 分布 (T+1..T+10 各日涨停的概率)
  - 按年度
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.data_loader import load_daily
from core.tdx_loader import list_tdx_symbols


def board_ok(sym):
    return sym.startswith(("00", "60", "30", "68"))


def is_20cm(sym):
    return sym.startswith(("30", "68"))


def analyze_one(sym, ice_n_max=120, future_k_max=10):
    if not board_ok(sym):
        return None
    try:
        df = load_daily(sym)
    except Exception:
        return None
    n = len(df)
    if n < ice_n_max + future_k_max + 5:
        return None

    cb = is_20cm(sym)
    lu_thresh = 0.195 if cb else 0.095

    C = df["close"].values.astype(float)
    O = df["open"].values.astype(float)
    H = df["high"].values.astype(float)
    L = df["low"].values.astype(float)
    V = df["volume"].values.astype(float)

    prev_C = np.concatenate(([np.nan], C[:-1]))
    ret = C / prev_C - 1
    is_lu = (ret >= lu_thresh)
    is_ow = is_lu & (O == C) & (H == L)

    lu_arr = is_lu.astype(np.int8)

    # 过去 N 日涨停次数 (不含今天)
    past = {}
    for n_back in (30, 60, 90, 120):
        past[n_back] = pd.Series(lu_arr).shift(1).rolling(n_back, min_periods=n_back).sum().values

    # 未来 K 日涨停次数 (T+1..T+K), 以及 T+i 当日是否涨停
    cum_lu = np.concatenate(([0], np.cumsum(lu_arr)))
    future_count = {}
    for k in (5, 10, 20):
        fc = np.full(n, np.nan)
        for t in range(n):
            if t + k < n:
                fc[t] = cum_lu[t + k + 1] - cum_lu[t + 1]
        future_count[k] = fc
    # T+1..T+10 各日是否涨停
    future_at = {}
    for i in range(1, 11):
        a = np.full(n, np.nan)
        if n > i:
            a[:n - i] = lu_arr[i:]
        future_at[i] = a

    out = pd.DataFrame({
        "date": df["date"].values,
        "code": sym,
        "is_20cm": cb,
        "is_lu": is_lu,
        "is_ow": is_ow,
        "past_30": past[30],
        "past_60": past[60],
        "past_90": past[90],
        "past_120": past[120],
        "fut_5": future_count[5],
        "fut_10": future_count[10],
        "fut_20": future_count[20],
    })
    for i in range(1, 11):
        out[f"f_at_{i}"] = future_at[i]

    valid = (V > 0) & (~np.isnan(out["fut_10"])) & (~np.isnan(out["past_120"]))
    return out[valid].reset_index(drop=True)


def p_future(d, k):
    """返回未来 k 日内至少出现 1 次涨停的占比 (%)."""
    col = f"fut_{k}"
    return (d[col] >= 1).mean() * 100


def report_block(d, label, k_list=(5, 10, 20)):
    n = len(d)
    if n == 0:
        print(f"  {label:34}  n=0")
        return
    parts = [f"n={n:>9,}"]
    for k in k_list:
        p = p_future(d, k)
        parts.append(f"K={k}日内涨停 {p:5.2f}%")
    avg = d["fut_10"].mean()
    parts.append(f"K=10日均涨停数 {avg:.3f}")
    print(f"  {label:34}  " + "  ".join(parts))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2026-04-21")
    args = ap.parse_args()

    syms = list_tdx_symbols()
    print(f"扫 {len(syms)} 只 (board_ok 过滤后参与)  窗口 {args.start} ~ {args.end}")

    parts = []
    for i, s in enumerate(syms, 1):
        if i % 1500 == 0:
            print(f"  {i}/{len(syms)}  累计 {sum(len(p) for p in parts):,}")
        r = analyze_one(s)
        if r is not None and len(r):
            parts.append(r)

    df = pd.concat(parts, ignore_index=True)
    df["dt"] = pd.to_datetime(df["date"])
    df = df[(df["dt"] >= pd.Timestamp(args.start)) & (df["dt"] <= pd.Timestamp(args.end))].reset_index(drop=True)
    print(f"\n过滤后总样本 {len(df):,}")

    # 排除一字板 (实战不可买)
    not_ow = ~df["is_ow"]

    print("\n=== 1. 整体三组对比 ===")
    report_block(df, "Baseline (任意日)")
    report_block(df[df["is_lu"] & not_ow], "All_LU (任意涨停, 排除一字)")
    report_block(df[df["is_lu"] & not_ow & (df["past_60"] == 0)], "Ice_LU [60日无涨停] (排除一字)")

    print("\n=== 2. 按 ice_n 敏感性 (Ice_LU 排除一字) ===")
    for n_back in (30, 60, 90, 120):
        sub = df[df["is_lu"] & not_ow & (df[f"past_{n_back}"] == 0)]
        report_block(sub, f"Ice_LU [{n_back}日无涨停]")

    print("\n=== 3. 按板别 ===")
    for tag, m in [("主板 10CM", ~df["is_20cm"]), ("创/科 20CM", df["is_20cm"])]:
        sub_d = df[m]
        print(f"\n  -- {tag} (n_total={len(sub_d):,}) --")
        report_block(sub_d, "Baseline")
        report_block(sub_d[sub_d["is_lu"] & ~sub_d["is_ow"]], "All_LU")
        report_block(sub_d[sub_d["is_lu"] & ~sub_d["is_ow"] & (sub_d["past_60"] == 0)], "Ice_LU [60]")

    print("\n=== 4. 未来 T+i 各日涨停概率 (Ice_LU vs All_LU) ===")
    ice = df[df["is_lu"] & not_ow & (df["past_60"] == 0)]
    allu = df[df["is_lu"] & not_ow]
    base = df
    print(f"  {'T+i':>5}  {'Baseline':>10}  {'All_LU':>10}  {'Ice_LU':>10}  {'Lift(I/A)':>10}")
    for i in range(1, 11):
        col = f"f_at_{i}"
        b = base[col].mean() * 100
        a = allu[col].mean() * 100
        ic = ice[col].mean() * 100
        lift = ic / a if a > 0 else 0
        print(f"  T+{i:<3}  {b:>9.2f}%  {a:>9.2f}%  {ic:>9.2f}%  {lift:>9.2f}x")

    print("\n=== 5. 按年度 (Ice_LU vs All_LU 未来10日内涨停%) ===")
    ice = ice.copy(); ice["year"] = ice["dt"].dt.year
    allu = allu.copy(); allu["year"] = allu["dt"].dt.year
    yr = pd.DataFrame({
        "All_n": allu.groupby("year").size(),
        "All_p10": allu.groupby("year").apply(lambda x: (x["fut_10"] >= 1).mean() * 100),
        "Ice_n": ice.groupby("year").size(),
        "Ice_p10": ice.groupby("year").apply(lambda x: (x["fut_10"] >= 1).mean() * 100),
    })
    yr["Lift"] = yr["Ice_p10"] / yr["All_p10"]
    print(yr.to_string(float_format=lambda v: f"{v:.2f}"))


if __name__ == "__main__":
    main()
