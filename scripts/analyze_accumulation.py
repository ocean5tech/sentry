#!/usr/bin/env python3
"""
验证 主力建仓启动假说.

定义:
  底部     = close < (过去 60 日 high) × 0.85   (距高点 ≥-15%)
  缩量     = (过去 20 日均量) / (过去 60 日均量) < 0.7  (量缩 ≥30%)
  量突变   = T0 vol > (过去 20 日均量) × 2.0
  突破     = T0 close > (过去 20 日 high, 不含今天)

  Sig    = 上述 4 条全满足 (主力建仓 + 启动)

对照:
  Baseline       任意日
  Bottom+Squeeze  底部 + 缩量 (潜伏中, 无启动)
  Burst+Break    量突变 + 突破 (爆发, 不限位置)
  Sig            4 全满足
"""

import argparse
import struct
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.data_loader import load_daily
from core.tdx_loader import list_tdx_symbols


BOTTOM_RATIO = 0.85
SQUEEZE_RATIO = 0.7
VOL_BURST = 2.0
BREAK_WIN = 20
HIGH_WIN = 60
VOL_LONG = 60
VOL_SHORT = 20
FUTURE = 20
MIN_BARS = HIGH_WIN + 10


def board_ok(sym):
    return sym.startswith(("00", "60", "30", "68"))


def is_20cm(sym):
    return sym.startswith(("30", "68"))


def read_index(path):
    raw = open(path, "rb").read()
    rows = []
    for i in range(len(raw) // 32):
        d, o, h, l, c, amt, vol, _ = struct.unpack("<IIIIIfII", raw[i*32:(i+1)*32])
        if c:
            rows.append({"date": pd.Timestamp(str(d)), "close": c / 100})
    return pd.DataFrame(rows)


def build_sh300_lookup():
    sh = read_index(ROOT / "data" / "tdx" / "sh" / "lday" / "sh000300.day")
    sh = sh.sort_values("date").reset_index(drop=True)
    closes = sh["close"].values.astype(float)
    dates_arr = sh["date"].dt.date.values
    idx_map = {d: i for i, d in enumerate(dates_arr)}
    fut = np.full(len(closes), np.nan)
    if len(closes) > FUTURE:
        fut[:-FUTURE] = closes[FUTURE:] / closes[:-FUTURE] - 1
    return idx_map, fut


def analyze_one(sym, sh_idx_map, sh_fut):
    if not board_ok(sym):
        return None
    try:
        df = load_daily(sym)
    except Exception:
        return None
    n = len(df)
    if n < MIN_BARS + FUTURE:
        return None

    C = df["close"].values.astype(float)
    H = df["high"].values.astype(float)
    V = df["volume"].values.astype(float)

    # 1. 底部: close < 60 日 high × 0.85 (60 日 high 含今天)
    high_60 = pd.Series(H).rolling(HIGH_WIN, min_periods=HIGH_WIN).max().values
    bottom = C < high_60 * BOTTOM_RATIO

    # 2. 缩量: 20 日均量 / 60 日均量 < 0.7 (用 T-1 为止的数据, shift 1)
    v_short = pd.Series(V).shift(1).rolling(VOL_SHORT, min_periods=VOL_SHORT).mean().values
    v_long = pd.Series(V).shift(1).rolling(VOL_LONG, min_periods=VOL_LONG).mean().values
    squeeze = (v_short < v_long * SQUEEZE_RATIO) & (v_long > 0)

    # 3. 量突变: T0 V > 20 日均量 × 2
    burst = V > v_short * VOL_BURST

    # 4. 突破: T0 close > 过去 20 日 high (T-20..T-1, 不含今天)
    high_20_prev = pd.Series(H).shift(1).rolling(BREAK_WIN, min_periods=BREAK_WIN).max().values
    breakout = C > high_20_prev

    sig = bottom & squeeze & burst & breakout

    fut_ret = np.full(n, np.nan)
    if n > FUTURE:
        fut_ret[:-FUTURE] = C[FUTURE:] / C[:-FUTURE] - 1

    dates_d = df["date"].dt.date.values
    bench_fut = np.full(n, np.nan)
    for j, d in enumerate(dates_d):
        i = sh_idx_map.get(d, -1)
        if 0 <= i < len(sh_fut):
            bench_fut[j] = sh_fut[i]

    out = pd.DataFrame({
        "date": df["date"].values,
        "code": sym,
        "is_20cm": is_20cm(sym),
        "bottom": bottom,
        "squeeze": squeeze,
        "burst": burst,
        "breakout": breakout,
        "bottom_squeeze": bottom & squeeze,
        "burst_break": burst & breakout,
        "sig": sig,
        "fut_ret": fut_ret,
        "bench_fut": bench_fut,
    })
    out["excess"] = out["fut_ret"] - out["bench_fut"]
    valid = (~np.isnan(out["fut_ret"])) & (~np.isnan(out["bench_fut"])) & (~np.isnan(high_60)) & (~np.isnan(v_long)) & (V > 0)
    return out[valid].reset_index(drop=True)


def report(d, label):
    n = len(d)
    if n == 0:
        print(f"  {label:30}  n=0")
        return
    fut = d["fut_ret"] * 100
    bench = d["bench_fut"] * 100
    ex = d["excess"] * 100
    win = (d["excess"] > 0).mean() * 100
    big_win = (d["excess"] > 0.05).mean() * 100
    big_lose = (d["excess"] < -0.05).mean() * 100
    print(f"  {label:30}  n={n:>9,}  "
          f"个股 {fut.mean():+5.2f}%  大盘 {bench.mean():+5.2f}%  "
          f"超额 {ex.mean():+5.2f}%(中位 {ex.median():+5.2f}%)  "
          f"赢大盘 {win:5.1f}%  超 5% {big_win:5.1f}%  跌 5% {big_lose:5.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2026-04-21")
    args = ap.parse_args()

    sh_idx_map, sh_fut = build_sh300_lookup()
    syms = list_tdx_symbols()
    print(f"扫 {len(syms)} 只  窗口 {args.start}~{args.end}")
    print(f"参数: 底部<{BOTTOM_RATIO}×{HIGH_WIN}日高  "
          f"缩量={VOL_SHORT}/{VOL_LONG}日<{SQUEEZE_RATIO}  "
          f"放量×{VOL_BURST}  突破{BREAK_WIN}日高  未来{FUTURE}日")

    parts = []
    for i, s in enumerate(syms, 1):
        if i % 1500 == 0:
            print(f"  {i}/{len(syms)}  累计 {sum(len(p) for p in parts):,}")
        r = analyze_one(s, sh_idx_map, sh_fut)
        if r is not None and len(r):
            parts.append(r)

    df = pd.concat(parts, ignore_index=True)
    df["dt"] = pd.to_datetime(df["date"])
    df = df[(df["dt"] >= pd.Timestamp(args.start)) & (df["dt"] <= pd.Timestamp(args.end))].reset_index(drop=True)
    print(f"\n过滤后总样本 {len(df):,}")

    print("\n=== 1. 整体四组对比 ===")
    report(df, "Baseline (任意日)")
    report(df[df["bottom_squeeze"]], "Bottom+Squeeze (潜伏中)")
    report(df[df["burst_break"]], "Burst+Break (爆发不限位置)")
    report(df[df["sig"]], "Sig 主力建仓启动 (4 全满足)")

    print("\n=== 2. 单条件拆解 (看每个特征单独的超额) ===")
    for col, lab in [("bottom", "Bottom"), ("squeeze", "Squeeze"),
                      ("burst", "Burst"), ("breakout", "Breakout")]:
        report(df[df[col]], f"单因子: {lab}")

    print("\n=== 3. 按板别 ===")
    for tag, m in [("主板 10CM", ~df["is_20cm"]), ("创/科 20CM", df["is_20cm"])]:
        sub = df[m]
        print(f"  -- {tag} (n_total={len(sub):,}) --")
        report(sub, "Baseline")
        report(sub[sub["bottom_squeeze"]], "Bottom+Squeeze")
        report(sub[sub["burst_break"]], "Burst+Break")
        report(sub[sub["sig"]], "Sig (4 全满足)")

    print("\n=== 4. 按年度 (Sig vs Burst+Break) ===")
    sig = df[df["sig"]].copy(); sig["year"] = sig["dt"].dt.year
    bb = df[df["burst_break"]].copy(); bb["year"] = bb["dt"].dt.year
    yr = pd.DataFrame({
        "BB_n": bb.groupby("year").size(),
        "BB_excess%": bb.groupby("year")["excess"].mean() * 100,
        "BB_win%": bb.groupby("year").apply(lambda x: (x["excess"] > 0).mean() * 100),
        "Sig_n": sig.groupby("year").size(),
        "Sig_excess%": sig.groupby("year")["excess"].mean() * 100,
        "Sig_win%": sig.groupby("year").apply(lambda x: (x["excess"] > 0).mean() * 100),
    }).fillna(0)
    print(yr.to_string(float_format=lambda v: f"{v:.2f}"))

    print("\n=== 5. Sig 信号样本展示 (随机 10 条) ===")
    sig_d = df[df["sig"]].copy()
    if len(sig_d) > 0:
        s = sig_d.sample(min(10, len(sig_d)), random_state=42)
        s["fut_ret%"] = s["fut_ret"] * 100
        s["bench_fut%"] = s["bench_fut"] * 100
        s["excess%"] = s["excess"] * 100
        print(s[["date", "code", "fut_ret%", "bench_fut%", "excess%"]].to_string(index=False, float_format=lambda v: f"{v:.2f}"))


if __name__ == "__main__":
    main()
