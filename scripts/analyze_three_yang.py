#!/usr/bin/env python3
"""
验证 三根阳线假说.

定义:
  大阳线   = close>open AND ret_t0 ∈ [4%, 6%]
  震荡期   = 过去 40 日净涨跌幅 ∈ [-15%, +15%]
  非连续   = 过去 40 日 (含今天) 中没有相邻两日都是大阳线
  信号 sig = 今天大阳线 AND 震荡 AND 过去 40 日大阳线数 >= 3 AND 非连续

观察:
  T+20 个股收益, 同期 sh000300 收益, 超额收益
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


YANG_LO = 0.04
LOOKBACK = 40
FUTURE = 20
RANGE_LIMIT = 0.15


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
    if n < LOOKBACK + FUTURE + 5:
        return None

    C = df["close"].values.astype(float)
    O = df["open"].values.astype(float)
    V = df["volume"].values.astype(float)

    prev_C = np.concatenate(([np.nan], C[:-1]))
    ret = C / prev_C - 1
    yang = (C > O) & (ret >= YANG_LO)
    yang_arr = yang.astype(np.int8)

    # 过去 LOOKBACK+1 日 (含今天) 的大阳线数
    past_yang = pd.Series(yang_arr).rolling(LOOKBACK + 1, min_periods=1).sum().values
    # 相邻日同时大阳 (今天 AND 昨天)
    adj_yang = (yang_arr & np.concatenate(([0], yang_arr[:-1]))).astype(np.int8)
    past_adj = pd.Series(adj_yang).rolling(LOOKBACK + 1, min_periods=1).sum().values
    # 震荡: 过去 LOOKBACK 日净涨跌幅
    range_ret = np.full(n, np.nan)
    range_ret[LOOKBACK:] = C[LOOKBACK:] / C[:-LOOKBACK] - 1
    in_range = (np.abs(range_ret) <= RANGE_LIMIT)

    fut_ret = np.full(n, np.nan)
    if n > FUTURE:
        fut_ret[:-FUTURE] = C[FUTURE:] / C[:-FUTURE] - 1

    dates_d = df["date"].dt.date.values
    bench_fut = np.full(n, np.nan)
    for j, d in enumerate(dates_d):
        i = sh_idx_map.get(d, -1)
        if 0 <= i < len(sh_fut):
            bench_fut[j] = sh_fut[i]

    sig = yang & in_range & (past_yang >= 3) & (past_adj == 0)

    out = pd.DataFrame({
        "date": df["date"].values,
        "code": sym,
        "is_20cm": is_20cm(sym),
        "yang": yang,
        "in_range": in_range,
        "yang_range": yang & in_range,
        "sig": sig,
        "fut_ret": fut_ret,
        "bench_fut": bench_fut,
    })
    out["excess"] = out["fut_ret"] - out["bench_fut"]
    valid = (~np.isnan(out["fut_ret"])) & (~np.isnan(out["bench_fut"])) & (~np.isnan(range_ret)) & (V > 0)
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
    print(f"扫 {len(syms)} 只  窗口 {args.start}~{args.end}  "
          f"大阳线≥{YANG_LO*100:.0f}%  "
          f"震荡=±{RANGE_LIMIT*100:.0f}%/{LOOKBACK}日  未来={FUTURE}日")

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
    report(df[df["yang"]], "Yang (今天大阳线)")
    report(df[df["yang_range"]], "Yang+震荡 (大阳+震荡期)")
    report(df[df["sig"]], "Sig 3yang (假说信号)")

    print("\n=== 2. 按板别 ===")
    for tag, m in [("主板 10CM", ~df["is_20cm"]), ("创/科 20CM", df["is_20cm"])]:
        sub = df[m]
        print(f"  -- {tag} (n_total={len(sub):,}) --")
        report(sub, "Baseline")
        report(sub[sub["yang"]], "Yang")
        report(sub[sub["yang_range"]], "Yang+震荡")
        report(sub[sub["sig"]], "Sig 3yang")

    print("\n=== 3. 按年度 (Sig vs Yang+震荡) ===")
    sig = df[df["sig"]].copy(); sig["year"] = sig["dt"].dt.year
    yr = df[df["yang_range"]].copy(); yr["year"] = yr["dt"].dt.year
    out = pd.DataFrame({
        "yr_n": yr.groupby("year").size(),
        "yr_excess%": yr.groupby("year")["excess"].mean() * 100,
        "yr_win%": yr.groupby("year").apply(lambda x: (x["excess"] > 0).mean() * 100),
        "sig_n": sig.groupby("year").size(),
        "sig_excess%": sig.groupby("year")["excess"].mean() * 100,
        "sig_win%": sig.groupby("year").apply(lambda x: (x["excess"] > 0).mean() * 100),
    }).fillna(0)
    print(out.to_string(float_format=lambda v: f"{v:.2f}"))

    print("\n=== 4. Sig 信号样本展示 (随机 10 条) ===")
    sig_d = df[df["sig"]].copy()
    if len(sig_d) > 0:
        s = sig_d.sample(min(10, len(sig_d)), random_state=42)
        s["fut_ret%"] = s["fut_ret"] * 100
        s["bench_fut%"] = s["bench_fut"] * 100
        s["excess%"] = s["excess"] * 100
        print(s[["date", "code", "fut_ret%", "bench_fut%", "excess%"]].to_string(index=False, float_format=lambda v: f"{v:.2f}"))


if __name__ == "__main__":
    main()
