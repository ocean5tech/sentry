#!/usr/bin/env python3
"""
Squeeze 单因子完整实战回测.

信号: 过去 20 日均量 / 过去 60 日均量 < 0.7
入场: T0 close
卖出: T+20 close
成本: 0.35% (双边)
资金: 50 万 / 10 slot × 5 万 / 先到先得
排序: --sort-by code (默认) 或 squeeze (缩得越深越优先)
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


SQUEEZE_RATIO = 0.5   # 缩 50%
VOL_LONG = 60
VOL_SHORT = 20
HOLD = 20
COST = 0.0035
HIGH_WIN = 60
BOTTOM_RATIO = 0.80   # 距 60 日高 ≤ -20%
MA_WIN = 60           # close < MA60


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
    return closes, dates_arr, {d: i for i, d in enumerate(dates_arr)}


def scan_one(sym, sh_idx_map, sh_closes):
    if not board_ok(sym):
        return None
    try:
        df = load_daily(sym)
    except Exception:
        return None
    n = len(df)
    if n < VOL_LONG + HOLD + 5:
        return None

    C = df["close"].values.astype(float)
    H = df["high"].values.astype(float)
    V = df["volume"].values.astype(float)

    v_short = pd.Series(V).shift(1).rolling(VOL_SHORT, min_periods=VOL_SHORT).mean().values
    v_long = pd.Series(V).shift(1).rolling(VOL_LONG, min_periods=VOL_LONG).mean().values
    squeeze_ratio = v_short / np.where(v_long > 0, v_long, np.nan)

    high_60 = pd.Series(H).rolling(HIGH_WIN, min_periods=HIGH_WIN).max().values
    ma_60 = pd.Series(C).rolling(MA_WIN, min_periods=MA_WIN).mean().values

    squeeze_ok = (squeeze_ratio < SQUEEZE_RATIO) & (v_long > 0)
    bottom_ok = C < high_60 * BOTTOM_RATIO
    below_ma = C < ma_60
    sig = squeeze_ok & bottom_ok & below_ma

    fut_close = np.full(n, np.nan)
    if n > HOLD:
        fut_close[:-HOLD] = C[HOLD:]

    dates_d = df["date"].dt.date.values
    bench_fut_ret = np.full(n, np.nan)
    for j, d in enumerate(dates_d):
        i = sh_idx_map.get(d, -1)
        if 0 <= i < len(sh_closes) - HOLD:
            bench_fut_ret[j] = sh_closes[i + HOLD] / sh_closes[i] - 1

    rows = []
    for t in range(n):
        if sig[t] and V[t] > 0 and not np.isnan(fut_close[t]) and not np.isnan(bench_fut_ret[t]) and C[t] > 0:
            rows.append({
                "date": df["date"].iloc[t],
                "code": sym,
                "is_20cm": is_20cm(sym),
                "entry": C[t],
                "exit": fut_close[t],
                "ret_gross": (fut_close[t] - C[t]) / C[t],
                "bench_ret": bench_fut_ret[t],
                "squeeze_ratio": squeeze_ratio[t],
            })
    return pd.DataFrame(rows) if rows else None


def simulate_portfolio(trades, cal, sort_cols, n_slots, slot_amount):
    cal_idx = {d: i for i, d in enumerate(cal)}
    df = trades.copy()
    df["entry_d"] = pd.to_datetime(df["date"]).dt.date
    df["entry_i"] = df["entry_d"].map(cal_idx)
    df = df.dropna(subset=["entry_i"]).copy()
    df["entry_i"] = df["entry_i"].astype(int)
    df["exit_i"] = df["entry_i"] + HOLD
    df = df.sort_values(list(sort_cols), kind="mergesort").reset_index(drop=True)

    slots = [-1] * n_slots
    sel_idx = []
    skipped = 0
    for i, row in enumerate(df.itertuples(index=False)):
        ei = row.entry_i
        free = next((k for k, s in enumerate(slots) if s < ei), None)
        if free is None:
            skipped += 1
            continue
        slots[free] = row.exit_i
        sel_idx.append(i)

    sel = df.iloc[sel_idx].copy()
    sel["pnl"] = sel["ret_net"] * slot_amount
    sel["bench_pnl"] = sel["bench_ret"] * slot_amount
    return sel, skipped, len(df)


def report_overall(trades):
    n = len(trades)
    g = trades["ret_gross"] * 100
    ne = trades["ret_net"] * 100
    bench = trades["bench_ret"] * 100
    excess = (trades["ret_gross"] - trades["bench_ret"]) * 100
    print(f"\n=== 整体 (无资金约束)  n={n:,} ===")
    print(f"  毛: 均 {g.mean():+5.2f}%  中位 {g.median():+5.2f}%  胜率 {(g>0).mean()*100:5.1f}%")
    print(f"  净: 均 {ne.mean():+5.2f}%  中位 {ne.median():+5.2f}%  胜率 {(ne>0).mean()*100:5.1f}%")
    print(f"  大盘同期: 均 {bench.mean():+5.2f}%  中位 {bench.median():+5.2f}%")
    print(f"  超额(毛-大盘): 均 {excess.mean():+5.2f}%  中位 {excess.median():+5.2f}%  赢大盘 {(excess>0).mean()*100:5.1f}%")
    print(f"  分布: >+5% {(g>5).mean()*100:5.1f}%  <-5% {(g<-5).mean()*100:5.1f}%  >+15% {(g>15).mean()*100:5.1f}%  <-15% {(g<-15).mean()*100:5.1f}%")


def report_infinite_slot(trades, cal, cost_offset=0.0):
    """无限 slot 等权组合: 每天活跃仓位等权, 持有 HOLD 日, 求组合日收益, 复利累积."""
    cal_idx = {d: i for i, d in enumerate(cal)}
    df = trades.copy()
    df["entry_d"] = pd.to_datetime(df["date"]).dt.date
    df["entry_i"] = df["entry_d"].map(cal_idx)
    df = df.dropna(subset=["entry_i"]).copy()
    df["entry_i"] = df["entry_i"].astype(int)

    n_days = len(cal)
    sum_ret = np.zeros(n_days + HOLD)
    sum_bench = np.zeros(n_days + HOLD)
    count = np.zeros(n_days + HOLD, dtype=int)

    r_net = df["ret_net"].values
    r_bench = df["bench_ret"].values
    start_idx = df["entry_i"].values
    # 每笔的 daily 复利摊分
    trade_daily = (1 + r_net) ** (1.0 / HOLD) - 1
    bench_daily = (1 + r_bench) ** (1.0 / HOLD) - 1

    for i in range(len(df)):
        s = start_idx[i]
        e = s + HOLD
        if s < 0 or s >= n_days + HOLD:
            continue
        e = min(e, n_days + HOLD)
        sum_ret[s:e] += trade_daily[i]
        sum_bench[s:e] += bench_daily[i]
        count[s:e] += 1

    active = count > 0
    if not active.any():
        print("无限 slot: 无活跃日")
        return

    first = int(np.argmax(active))
    last = int(len(active) - np.argmax(active[::-1]) - 1)
    port_daily = np.where(active, sum_ret / np.maximum(count, 1), 0.0)[first:last + 1]
    bench_daily_arr = np.where(active, sum_bench / np.maximum(count, 1), 0.0)[first:last + 1]

    equity = np.cumprod(1 + port_daily)
    bench_eq = np.cumprod(1 + bench_daily_arr)
    n_active = int(active[first:last + 1].sum())
    n_total = last - first + 1

    years = n_total / 250.0
    total_ret = equity[-1] - 1
    bench_total = bench_eq[-1] - 1
    ann = equity[-1] ** (1 / years) - 1
    bench_ann = bench_eq[-1] ** (1 / years) - 1
    peak = np.maximum.accumulate(equity)
    max_dd = ((equity - peak) / peak).min()
    std_daily = port_daily.std() + 1e-9
    sharpe = port_daily.mean() / std_daily * np.sqrt(250)

    print(f"\n--- 无限 slot 等权组合 (每天全仓等权, 复利) ---")
    print(f"期间 {n_total} 日 ({years:.2f} 年)  活跃仓 {n_active} 日  平均同时持仓 {count[first:last + 1].mean():.0f} 只")
    print(f"策略: 累计 {total_ret*100:+.1f}%  复利年化 {ann*100:+.2f}%")
    print(f"大盘: 累计 {bench_total*100:+.1f}%  复利年化 {bench_ann*100:+.2f}%")
    print(f"超额: 累计 {(total_ret-bench_total)*100:+.1f}%  超额年化 {((1+ann)/(1+bench_ann)-1)*100:+.2f}%")
    print(f"最大回撤 {max_dd*100:+.1f}%  Sharpe ≈{sharpe:.2f}  权益峰值 {equity.max():.3f}x  谷值 {equity.min():.3f}x")

    # 按年度
    cal_dates = cal[first:last + 1]
    years_arr = np.array([d.year for d in cal_dates])
    yr_rows = []
    for y in sorted(set(years_arr)):
        mask = years_arr == y
        port_y = np.prod(1 + port_daily[mask]) - 1
        bench_y = np.prod(1 + bench_daily_arr[mask]) - 1
        avg_pos = count[first:last + 1][mask].mean()
        yr_rows.append((y, mask.sum(), avg_pos, port_y * 100, bench_y * 100, (port_y - bench_y) * 100))
    yr_df = pd.DataFrame(yr_rows, columns=["year", "days", "avg_pos", "port%", "bench%", "excess%"])
    print(yr_df.to_string(index=False, float_format=lambda v: f"{v:.2f}"))


def report_portfolio(sel, skipped, total, slot_amount, n_slots, label):
    capital = slot_amount * n_slots
    print(f"\n--- 资金管理 [{label}]: {n_slots} slot × {slot_amount/10000:.0f}万 = {capital/10000:.0f}万 ---")
    print(f"信号 {total:,}  执行 {len(sel):,}  跳过 {skipped:,} ({skipped/total*100:.1f}%)")
    if sel.empty:
        return

    sel = sel.sort_values("entry_i").reset_index(drop=True)
    total_pnl = sel["pnl"].sum()
    total_bench_pnl = sel["bench_pnl"].sum()
    total_ret = total_pnl / capital
    bench_total = total_bench_pnl / capital
    span_days = (sel["entry_d"].max() - sel["entry_d"].min()).days or 1
    ann = total_ret / span_days * 365 * 100
    bench_ann = bench_total / span_days * 365 * 100
    win = (sel["ret_net"] > 0).mean() * 100
    excess_total = total_ret - bench_total
    print(f"策略: 累计 {total_ret*100:+.1f}%  年化 {ann:+.1f}%  净胜率 {win:.1f}%")
    print(f"大盘: 累计 {bench_total*100:+.1f}%  年化 {bench_ann:+.1f}%")
    print(f"超额: 累计 {excess_total*100:+.1f}%  ({(excess_total/(span_days/365))*100:+.1f}%/年)")

    sel["excess_net"] = sel["ret_net"] - sel["bench_ret"]
    sel["year"] = pd.to_datetime(sel["entry_d"]).dt.year
    yr = sel.groupby("year").agg(
        n=("code", "size"),
        win=("ret_net", lambda x: (x > 0).mean() * 100),
        net_mean=("ret_net", lambda x: x.mean() * 100),
        bench_mean=("bench_ret", lambda x: x.mean() * 100),
    )
    yr["excess_mean"] = sel.groupby("year")["excess_net"].mean() * 100
    yr["pnl"] = sel.groupby("year")["pnl"].sum()
    yr["yoy_ret%"] = yr["pnl"] / capital * 100
    yr["cum_pnl"] = yr["pnl"].cumsum()
    print(yr[["n", "win", "net_mean", "bench_mean", "excess_mean", "pnl", "yoy_ret%", "cum_pnl"]].to_string(
        float_format=lambda v: f"{v:.2f}"))

    daily_pnl = sel.groupby("entry_i")["pnl"].sum()
    equity = capital + daily_pnl.cumsum()
    peak = equity.cummax()
    dd = (equity - peak) / peak
    max_dd = dd.min() * 100
    sharpe_d = daily_pnl.mean() / (daily_pnl.std() + 1e-9) * np.sqrt(250)
    print(f"最大回撤 {max_dd:.1f}%  日 Sharpe ≈{sharpe_d:.2f}  权益峰值 {equity.max():,.0f}  谷值 {equity.min():,.0f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2026-04-21")
    ap.add_argument("--cost", type=float, default=COST)
    ap.add_argument("--slots", type=int, default=10)
    ap.add_argument("--slot-amount", type=float, default=50000)
    args = ap.parse_args()

    sh_closes, sh_dates, sh_idx_map = build_sh300_lookup()
    cal = sorted(sh_dates.tolist())
    syms = list_tdx_symbols()
    print(f"扫 {len(syms)} 只  窗口 {args.start}~{args.end}")
    print(f"参数: 缩量 V{VOL_SHORT}/V{VOL_LONG}<{SQUEEZE_RATIO}  "
          f"距{HIGH_WIN}日高<{BOTTOM_RATIO}  close<MA{MA_WIN}  "
          f"持有 {HOLD} 日  成本 {args.cost*100:.2f}%")

    parts = []
    for i, s in enumerate(syms, 1):
        if i % 1500 == 0:
            print(f"  {i}/{len(syms)}  累计 trades {sum(len(p) for p in parts):,}")
        r = scan_one(s, sh_idx_map, sh_closes)
        if r is not None and len(r):
            parts.append(r)

    trades = pd.concat(parts, ignore_index=True)
    trades = trades[(trades["date"] >= pd.Timestamp(args.start)) & (trades["date"] <= pd.Timestamp(args.end))].reset_index(drop=True)
    trades["ret_net"] = trades["ret_gross"] - args.cost
    print(f"\n过滤后 trades {len(trades):,}")

    report_overall(trades)

    print("\n=== 按板别 (无资金约束) ===")
    for tag, m in [("主板 10CM", ~trades["is_20cm"]), ("创/科 20CM", trades["is_20cm"])]:
        sub = trades[m]
        if len(sub) == 0: continue
        ne = sub["ret_net"] * 100
        bench = sub["bench_ret"] * 100
        excess = (sub["ret_gross"] - sub["bench_ret"]) * 100
        print(f"  {tag}: n={len(sub):,}  净均 {ne.mean():+5.2f}%  大盘均 {bench.mean():+5.2f}%  超额 {excess.mean():+5.2f}%  赢大盘 {(excess>0).mean()*100:5.1f}%")

    # 两种 10 slot 排序对比
    for sort_label, sort_cols in [("按 code 升序 (基线)", ("entry_i", "code")),
                                    ("按缩量比升序 (深缩量优先)", ("entry_i", "squeeze_ratio", "code"))]:
        sel, skipped, total = simulate_portfolio(trades, cal, sort_cols, args.slots, args.slot_amount)
        report_portfolio(sel, skipped, total, args.slot_amount, args.slots, sort_label)

    # 无限 slot 等权组合 (裸信号理论上限)
    report_infinite_slot(trades, cal)


if __name__ == "__main__":
    main()
