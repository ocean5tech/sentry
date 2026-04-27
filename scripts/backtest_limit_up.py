#!/usr/bin/env python3
"""
超短抓涨停板策略 完整回测.

信号 (T0 日 14:45 前后判定, 用当日 close 近似):
  1. T0 涨停 (主板 ret>=9.5%, 创/科 ret>=19.5%)
  2. T0 非一字板 (O==C 且 H==L 即排除)
  3. 上市 >= 130 日, 非 ST
  4. (可选) 沪深300 MA20 向上 -- 大盘过滤

买入 (T+1 开盘):
  - 跳过 T+1 一字板: O>=涨停价 且 H==L
  - 跳过高开过大: T+1 O > T0 close * (1+entry_prem)  默认 5%

持有与卖出 (持有窗口 3 交易日, 一字板顺延):
  - 每日盘中 if H >= entry*1.09 -> 当日卖出 @ entry*1.09
  - 持仓期一字板 (H==L 且当日涨停) -> 卖不出, 不消耗持有天数
  - 3 个非一字板交易日内未触发 -> 最后一天收盘清仓

资金管理:
  - 50 万本金, 10 slot, 每 slot 5 万, 先到先得 (code 排序)

Usage:
    python3 scripts/backtest_limit_up.py
"""

import argparse
import importlib.util
import struct
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.data_loader import load_daily
from core.tdx_loader import list_tdx_symbols
from core.stock_names import get_names
from core.formula_engine import _ref_smart


def _load_mytt():
    spec = importlib.util.spec_from_file_location(
        "mytt", Path(__file__).parent.parent / "core" / "mytt.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


MA = _load_mytt().MA

MAX_HOLD = 3         # 持有非一字板交易日数
HARD_CAP = 10        # 顺延硬上限 (避免死循环)
TARGET_RET = 0.09    # 目标 +9%


def read_index_day(path: Path) -> pd.DataFrame:
    with open(path, "rb") as f:
        data = f.read()
    n = len(data) // 32
    rows = []
    for i in range(n):
        d, o, h, l, c, amt, vol, _ = struct.unpack("<IIIIIfII", data[i*32:(i+1)*32])
        if c == 0:
            continue
        rows.append({"date": pd.Timestamp(str(d)), "close": c / 100})
    return pd.DataFrame(rows)


def compute_dapan_ok(sh300: pd.DataFrame) -> dict:
    C = sh300["close"].values.astype(float)
    ma20 = MA(C, 20)
    trend = ma20 > _ref_smart(ma20, 5)
    return {d.date(): bool(t) for d, t in zip(sh300["date"], trend)}


def board_ok(symbol: str, name: str) -> bool:
    if not symbol.startswith(("00", "60", "30", "68")):
        return False
    if name and "ST" in name.upper():
        return False
    return True


def is_20cm(symbol: str) -> bool:
    return symbol.startswith(("30", "68"))


def backtest_stock(symbol, name, start, end, dapan_ok, use_dapan, entry_prem):
    if not board_ok(symbol, name):
        return []
    try:
        df = load_daily(symbol)
    except Exception:
        return []
    if df.empty:
        return []
    df = df[df["date"] <= end].reset_index(drop=True)
    n = len(df)
    if n < 130:
        return []

    C = df["close"].values.astype(float)
    O = df["open"].values.astype(float)
    H = df["high"].values.astype(float)
    L = df["low"].values.astype(float)
    V = df["volume"].values.astype(float)
    dates = df["date"].values

    cb = is_20cm(symbol)
    lu_thresh = 0.195 if cb else 0.095
    real_limit = 0.20 if cb else 0.10

    prev_C = np.concatenate(([np.nan], C[:-1]))
    ret_t0 = C / prev_C - 1
    is_lu = ret_t0 >= lu_thresh
    is_ow = is_lu & (O == C) & (H == L)

    start_date = start.date()
    trades = []

    for t0 in range(130, n - 1):
        if not is_lu[t0] or is_ow[t0]:
            continue
        d0 = pd.Timestamp(dates[t0]).date()
        if d0 < start_date:
            continue
        if V[t0] == 0:
            continue
        if use_dapan and not dapan_ok.get(d0, False):
            continue

        t_buy = t0 + 1
        if V[t_buy] == 0:
            continue
        t0_close = float(C[t0])
        buy_o = float(O[t_buy])
        buy_h = float(H[t_buy])
        buy_l = float(L[t_buy])
        limit_price = round(t0_close * (1 + real_limit), 2)

        if buy_h == buy_l and buy_o >= limit_price - 1e-6:
            continue
        if buy_o > t0_close * (1 + entry_prem):
            continue
        if buy_o <= 0:
            continue

        entry = buy_o
        target = entry * (1 + TARGET_RET)

        exit_idx, exit_price, reason = None, None, None
        days_held = 0
        steps = 0
        j = t_buy
        while j < n and steps < HARD_CAP:
            dh = float(H[j]); dl = float(L[j])
            pc = float(C[j - 1])
            ret_c = dh / pc - 1 if pc > 0 else 0
            is_ow_j = (dh == dl) and (ret_c >= lu_thresh)

            if j > t_buy and is_ow_j:
                j += 1; steps += 1
                continue

            if dh >= target - 1e-6:
                exit_idx = j
                exit_price = target
                reason = "target"
                break

            days_held += 1
            if days_held >= MAX_HOLD:
                exit_idx = j
                exit_price = float(C[j])
                reason = "timeout"
                break
            j += 1; steps += 1

        if exit_idx is None:
            exit_idx = min(j, n - 1)
            exit_price = float(C[exit_idx])
            reason = "timeout"

        ret_gross = (exit_price - entry) / entry
        trades.append({
            "code": symbol,
            "name": name[:10],
            "signal": pd.Timestamp(dates[t0]).date().isoformat(),
            "entry_date": pd.Timestamp(dates[t_buy]).date().isoformat(),
            "entry": round(entry, 2),
            "exit_date": pd.Timestamp(dates[exit_idx]).date().isoformat(),
            "exit_price": round(exit_price, 2),
            "hold_days": exit_idx - t_buy + 1,
            "ret_gross": round(ret_gross, 4),
            "reason": reason,
            "is_20cm": int(cb),
        })
    return trades


def simulate_portfolio(trades_df, n_slots=10, cost=0.0022):
    df = trades_df.sort_values(["entry_date", "code"]).reset_index(drop=True)
    slots = [pd.Timestamp("1900-01-01").date()] * n_slots
    selected = []
    skipped = 0
    for _, t in df.iterrows():
        ed = pd.to_datetime(t["entry_date"]).date()
        xd = pd.to_datetime(t["exit_date"]).date()
        free = None
        for i, s in enumerate(slots):
            if s < ed:
                free = i
                break
        if free is None:
            skipped += 1
            continue
        slots[free] = xd
        selected.append(t)
    sel = pd.DataFrame(selected)
    if sel.empty:
        return sel, skipped
    sel["ret_net"] = sel["ret_gross"] - cost
    return sel, skipped


def report(trades: pd.DataFrame, label: str, cost: float):
    if trades.empty:
        print(f"\n=== {label}: 无信号 ===")
        return
    days = trades["hold_days"]
    net = trades["ret_gross"] - cost
    print(f"\n=== {label}  (n={len(trades):,}) ===")
    for tag, s in (("毛利", trades["ret_gross"]), ("净利", net)):
        win = (s > 0).mean() * 100
        mean = s.mean() * 100
        med = s.median() * 100
        big_w = (s > 0.05).mean() * 100
        big_l = (s < -0.05).mean() * 100
        ann = s.sum() / days.sum() * 250 * 100 if days.sum() > 0 else 0
        print(f"  [{tag}] 胜率 {win:5.1f}%  >5% {big_w:5.1f}%  <-5% {big_l:5.1f}%  "
              f"均值 {mean:+6.2f}%  中位 {med:+6.2f}%  年化≈{ann:+6.1f}%")
    print(f"  均持仓 {days.mean():.1f}日", end="  ")
    r = trades["reason"].value_counts(normalize=True) * 100
    print("退出:", "  ".join(f"{k}={v:.1f}%" for k, v in r.items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2026-04-21")
    ap.add_argument("--cost", type=float, default=0.0022)
    ap.add_argument("--no-dapan", action="store_true")
    ap.add_argument("--entry-prem", type=float, default=0.05)
    ap.add_argument("--output", default="logs/backtest_limit_up.csv")
    args = ap.parse_args()

    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    print(f"回测 {args.start} ~ {args.end}  双边成本 {args.cost*100:.2f}%  "
          f"高开容忍 {args.entry_prem*100:.1f}%")

    sh300_path = Path(__file__).parent.parent / "data" / "tdx" / "sh" / "lday" / "sh000300.day"
    sh300 = read_index_day(sh300_path)
    dapan_ok = compute_dapan_ok(sh300)
    use_dapan = not args.no_dapan
    if use_dapan:
        n_ok = sum(1 for v in dapan_ok.values() if v)
        print(f"大盘过滤: {n_ok}/{len(dapan_ok)} 天OK ({n_ok*100//len(dapan_ok)}%)")

    names = get_names()
    symbols = list_tdx_symbols()
    print(f"扫描 {len(symbols)} 只...")

    all_trades = []
    for i, sym in enumerate(symbols, 1):
        if i % 1500 == 0:
            print(f"  {i}/{len(symbols)}  累计 {len(all_trades):,}")
        all_trades.extend(backtest_stock(
            sym, names.get(sym, ""), start, end, dapan_ok,
            use_dapan, args.entry_prem))

    if not all_trades:
        print("无信号")
        return

    df = pd.DataFrame(all_trades)
    df["signal_dt"] = pd.to_datetime(df["signal"])
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output, index=False, encoding="utf-8-sig")

    report(df, "整体 (无资金约束)", args.cost)
    is_cut = pd.Timestamp("2025-01-01")
    report(df[df["signal_dt"] < is_cut], "In-Sample 2021-2024", args.cost)
    report(df[df["signal_dt"] >= is_cut], "Out-of-Sample 2025+", args.cost)
    report(df[df["is_20cm"] == 0], "主板 (10CM)", args.cost)
    report(df[df["is_20cm"] == 1], "创/科 (20CM)", args.cost)

    print("\n=== 按年度 (所有信号) ===")
    df["year"] = df["signal_dt"].dt.year
    yearly = df.groupby("year").agg(
        count=("ret_gross", "size"),
        win=("ret_gross", lambda x: (x > 0).mean() * 100),
        gross_mean=("ret_gross", lambda x: x.mean() * 100),
    )
    yearly["net_mean"] = yearly["gross_mean"] - args.cost * 100
    print(yearly.to_string(float_format=lambda v: f"{v:.2f}"))

    print("\n=== 资金管理: 10 slot × 5 万 = 50 万 ===")
    sel, skipped = simulate_portfolio(df, n_slots=10, cost=args.cost)
    if sel.empty:
        return
    print(f"信号总数 {len(df):,}  执行 {len(sel):,}  跳过(无slot) {skipped:,}")
    sel = sel.copy()
    sel["pnl"] = sel["ret_net"] * 50000
    sel["entry_dt"] = pd.to_datetime(sel["entry_date"])
    span_days = (sel["entry_dt"].max() - sel["entry_dt"].min()).days or 1
    total_pnl = sel["pnl"].sum()
    total_ret = total_pnl / 500000
    ann = total_ret / span_days * 365 * 100
    win_sel = (sel["ret_net"] > 0).mean() * 100
    print(f"组合总盈亏 {total_pnl:+,.0f}  组合总收益 {total_ret*100:+.1f}%  "
          f"年化 {ann:+.1f}%  净胜率 {win_sel:.1f}%")

    sel["year"] = sel["entry_dt"].dt.year
    y2 = sel.groupby("year").agg(
        count=("code", "size"),
        win=("ret_net", lambda x: (x > 0).mean() * 100),
        net_mean=("ret_net", lambda x: x.mean() * 100),
        pnl=("pnl", "sum"),
    )
    y2["cum_pnl"] = y2["pnl"].cumsum()
    print(y2.to_string(float_format=lambda v: f"{v:.1f}"))
    print(f"\n明细: {args.output}")


if __name__ == "__main__":
    main()
