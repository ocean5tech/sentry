#!/usr/bin/env python3
"""
强势股回调反弹策略回测（均值回归）

入场:
  1. MA60 > MA120 且 MA60 20日上升  (中期多头)
  2. 昨日 close < MA10                (近期回调)
  3. 今日 close > open                (阳线)
  4. 今日 close > 昨日 close          (涨了)
  5. 今日 close > MA10                (收复均线)
  6. 沪深300 MA20 20日上升            (大盘多头)
  7. 非ST, 上市>125日

出场（先到先走）:
  - 止盈: close >= entry * 1.03
  - 止损: close < entry_low * 0.98
  - 超时: 持仓满5交易日

Usage:
    python3 scripts/backtest_bounce.py
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
    trend = ma20 > _ref_smart(ma20, 20)
    return {d.date(): bool(t) for d, t in zip(sh300["date"], trend)}


def board_ok(symbol: str, name: str) -> bool:
    if not symbol.startswith(("00", "60", "30", "68")):
        return False
    if name and "ST" in name.upper():
        return False
    return True


def is_20cm(symbol: str) -> bool:
    return symbol.startswith(("30", "68"))


def compute_signals(df: pd.DataFrame) -> np.ndarray:
    n = len(df)
    if n < 125:
        return np.zeros(n, dtype=bool)

    C = df["close"].values.astype(float)
    O = df["open"].values.astype(float)

    ma10 = MA(C, 10)
    ma60 = MA(C, 60)
    ma120 = MA(C, 120)

    mid_up = (ma60 > ma120) & (ma60 > _ref_smart(ma60, 20))
    prev_below = _ref_smart(C, 1) < _ref_smart(ma10, 1)
    today_up = (C > O) & (C > _ref_smart(C, 1)) & (C > ma10)

    signal = mid_up & prev_below & today_up
    return np.nan_to_num(signal, nan=0).astype(bool)


def backtest_stock(symbol, name, start, end, dapan_ok):
    if not board_ok(symbol, name):
        return []
    try:
        df = load_daily(symbol)
    except Exception:
        return []
    if df.empty:
        return []
    df = df[df["date"] <= end].reset_index(drop=True)
    if len(df) < 130:
        return []

    signals = compute_signals(df)

    C = df["close"].values.astype(float)
    O = df["open"].values.astype(float)
    L = df["low"].values.astype(float)
    V = df["volume"].values.astype(float)
    dates = df["date"].values

    is_cb = is_20cm(symbol)
    unbuyable_ratio = 1 + (0.20 if is_cb else 0.10) * 0.80

    start_date = start.date()
    trades = []
    n = len(df)
    MAX_HOLD = 5

    for i in range(n - 1):
        if not signals[i]:
            continue
        d = pd.Timestamp(dates[i]).date()
        if d < start_date:
            continue
        if not dapan_ok.get(d, False):
            continue

        entry_open = float(O[i + 1])
        entry_low  = float(L[i])
        if entry_open <= 0 or V[i + 1] == 0:
            continue
        if entry_open >= C[i] * unbuyable_ratio:
            continue

        target = entry_open * 1.03
        stop   = entry_low * 0.98

        exit_idx, exit_price, reason = None, None, None
        for j in range(i + 1, min(i + 1 + MAX_HOLD, n)):
            c_j = C[j]
            if c_j >= target:
                exit_idx, exit_price, reason = j, c_j, "target"
                break
            if c_j < stop:
                exit_idx, exit_price, reason = j, c_j, "stop"
                break
        if exit_idx is None:
            exit_idx = min(i + MAX_HOLD, n - 1)
            exit_price = C[exit_idx]
            reason = "time"

        ret_gross = (exit_price - entry_open) / entry_open
        trades.append({
            "code":   symbol,
            "name":   name[:10],
            "signal": d.isoformat(),
            "entry":  pd.Timestamp(dates[i + 1]).date().isoformat(),
            "entry_price": round(entry_open, 2),
            "stop":   round(stop, 2),
            "target": round(target, 2),
            "exit":   pd.Timestamp(dates[exit_idx]).date().isoformat(),
            "exit_price": round(float(exit_price), 2),
            "hold_days":  exit_idx - i,
            "ret_gross": round(ret_gross, 4),
            "reason": reason,
            "is_20cm": int(is_cb),
        })
    return trades


def report(trades: pd.DataFrame, label: str):
    if trades.empty:
        print(f"\n=== {label}: 无信号 ===")
        return
    days = trades["hold_days"]
    print(f"\n=== {label} ===")
    for tag, col in (("毛利", "ret_gross"), ("净利", "ret_net")):
        if col not in trades.columns:
            continue
        s = trades[col]
        win   = (s > 0).mean() * 100
        mean  = s.mean() * 100
        med   = s.median() * 100
        big_w = (s > 0.05).mean() * 100
        big_l = (s < -0.05).mean() * 100
        ann = s.sum() / days.sum() * 250 * 100 if days.sum() > 0 else 0
        print(f"  [{tag}] 样本: {len(trades):>6}  胜率: {win:5.1f}%  "
              f"大赢>5%: {big_w:5.1f}%  大亏<-5%: {big_l:5.1f}%  "
              f"均值: {mean:+6.2f}%  中位: {med:+6.2f}%  年化≈{ann:+6.1f}%")
    print(f"  均持仓: {days.mean():.1f}日", end="  ")
    r = trades["reason"].value_counts(normalize=True) * 100
    print("退出:", "  ".join(f"{k}={v:.1f}%" for k, v in r.items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end",   default="2026-04-16")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--output", default="logs/backtest_bounce.csv")
    ap.add_argument("--cost", type=float, default=0.0022,
                    help="双边交易成本（默认0.22%：佣金0.05%+印花税0.05%+过户费+滑点0.1%）")
    args = ap.parse_args()

    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    print(f"回测区间: {args.start} ~ {args.end}")

    sh300_path = Path(__file__).parent.parent / "data" / "tdx" / "sh" / "lday" / "sh000300.day"
    sh300 = read_index_day(sh300_path)
    dapan_ok = compute_dapan_ok(sh300)
    n_ok = sum(1 for v in dapan_ok.values() if v)
    print(f"大盘OK: {n_ok}/{len(dapan_ok)} 天 ({n_ok*100//len(dapan_ok)}%)")

    names = get_names()
    symbols = list_tdx_symbols()
    if args.limit > 0:
        symbols = symbols[:args.limit]
    print(f"扫描 {len(symbols)} 只股票...")

    all_trades = []
    for i, sym in enumerate(symbols, 1):
        if i % 1000 == 0:
            print(f"  {i}/{len(symbols)}  累计信号: {len(all_trades)}")
        trades = backtest_stock(sym, names.get(sym, ""), start, end, dapan_ok)
        all_trades.extend(trades)

    df = pd.DataFrame(all_trades)
    if df.empty:
        print("无信号命中")
        return

    df["ret_net"] = df["ret_gross"] - args.cost
    df["ret_net"] = df["ret_net"].round(4)
    print(f"\n交易成本: 双边 {args.cost*100:.2f}%  (毛利 - 成本 = 净利)")

    df["signal_dt"] = pd.to_datetime(df["signal"])
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output, index=False, encoding="utf-8-sig")

    report(df, "整体")
    is_cut = pd.Timestamp("2025-01-01")
    report(df[df["signal_dt"] < is_cut], "In-Sample 2021-2024")
    report(df[df["signal_dt"] >= is_cut], "Out-of-Sample 2025-2026")
    report(df[df["is_20cm"] == 0], "主板")
    report(df[df["is_20cm"] == 1], "创/科")

    print(f"\n=== 按年度（毛利 / 净利）===")
    df["year"] = df["signal_dt"].dt.year
    yearly = df.groupby("year").agg(
        count=("ret_gross", "size"),
        win_net=("ret_net", lambda x: (x > 0).mean() * 100),
        gross_mean=("ret_gross", lambda x: x.mean() * 100),
        net_mean=("ret_net", lambda x: x.mean() * 100),
        avg_days=("hold_days", "mean"),
    )
    print(yearly.to_string(float_format=lambda v: f"{v:.2f}"))
    print(f"\n详细交易记录: {args.output}")


if __name__ == "__main__":
    main()
