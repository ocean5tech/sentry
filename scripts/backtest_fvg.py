#!/usr/bin/env python3
"""
FVG二次启动策略回测

Usage:
    python scripts/backtest_fvg.py --start 2021-01-01 --end 2026-04-16
    python scripts/backtest_fvg.py --limit 500                  # 快速测试
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


_m = _load_mytt()
MA, HHV, LLV, CROSS, BARSLAST, COUNT = _m.MA, _m.HHV, _m.LLV, _m.CROSS, _m.BARSLAST, _m.COUNT


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
    """Return {date -> bool} for 大盘OK.  仅要求 MA20 向上."""
    C = sh300["close"].values.astype(float)
    ma20 = MA(C, 20)
    trend = ma20 > _ref_smart(ma20, 5)
    return {d.date(): bool(t) for d, t in zip(sh300["date"], trend)}


def board_ok(symbol: str, name: str) -> bool:
    """板块过滤：保留 00x/60x/30x/688，排除ST"""
    if not symbol.startswith(("00", "60", "30", "68")):
        return False
    if name and ("ST" in name.upper() or "*ST" in name.upper()):
        return False
    return True


def is_20cm(symbol: str) -> bool:
    return symbol.startswith(("30", "68"))


def compute_signals(df: pd.DataFrame, is_cb: bool):
    """Per-day signal array + 低点 array (FVG下沿，用作止损).

    Returns (signal: bool[n], 低点: float[n]).  低点 is the breakout-candle low.
    """
    n = len(df)
    if n < 65:
        return np.zeros(n, dtype=bool), np.full(n, np.nan)

    C = df["close"].values.astype(float)
    H = df["high"].values.astype(float)
    L = df["low"].values.astype(float)
    V = df["volume"].values.astype(float)

    N, M = 20, 5
    前高 = HHV(H, N)
    ma_v = MA(V, M)
    放量 = (V > ma_v * 1.5).astype(np.int8)
    prev_high = _ref_smart(前高, 1).astype(float)
    突破 = (CROSS(C, prev_high) & 放量).astype(np.int8)
    最近突破 = COUNT(突破, 10) > 0

    高点 = _ref_smart(H, BARSLAST(突破))
    低点 = _ref_smart(L, BARSLAST(突破))
    with np.errstate(invalid="ignore", divide="ignore"):
        中线 = (高点 + 低点) / 2
        huicai_th = 0.09 if is_cb else 0.06
        zhengli_th = 1.15 if is_cb else 1.10
        回踩 = (np.abs(C - 中线) / 中线 < huicai_th) & (C > 低点)

    缩量 = V < ma_v * 1.1
    整理 = (HHV(H, 5) / LLV(L, 5)) < zhengli_th
    ma20 = MA(C, 20)
    ma60 = MA(C, 60)
    趋势 = (ma20 > ma60) & (ma20 > _ref_smart(ma20, 5))

    signal = (最近突破 & 回踩 & 缩量 & 整理 & 趋势)
    signal = np.nan_to_num(signal, nan=0).astype(bool)
    return signal, 低点.astype(float)


def backtest_stock(symbol: str, name: str, start: pd.Timestamp, end: pd.Timestamp,
                   dapan_ok: dict) -> list[dict]:
    if not board_ok(symbol, name):
        return []
    try:
        df = load_daily(symbol)
    except Exception:
        return []
    if df.empty:
        return []

    # 截到回测区间（但保留前65天预热期）
    df = df[df["date"] <= end].reset_index(drop=True)
    if len(df) < 65:
        return []

    is_cb = is_20cm(symbol)
    signals, stops = compute_signals(df, is_cb)

    # "无法买入"阈值：次日开盘涨幅 > 限价的 80%
    # 主板限价+10% → +8%算买不到; 20CM +20% → +16%算买不到
    unbuyable_ratio = 1 + (0.20 if is_cb else 0.10) * 0.80

    C = df["close"].values.astype(float)
    O = df["open"].values.astype(float)
    V = df["volume"].values.astype(float)
    dates = df["date"].values

    start_date = start.date()
    trades = []
    n = len(df)
    for i in range(n - 1):
        if not signals[i]:
            continue
        d = pd.Timestamp(dates[i]).date()
        if d < start_date:
            continue
        if not dapan_ok.get(d, False):
            continue

        entry_open = float(O[i + 1])
        # 次日开盘若接近涨停，视作买不到
        if entry_open >= float(C[i]) * unbuyable_ratio:
            continue
        # 次日零成交（停牌）也跳过
        if V[i + 1] == 0:
            continue

        stop = float(stops[i])
        if not np.isfinite(stop):
            continue

        # 止损退出：从 i+1 起第一天 close < stop 即卖出
        closes_after = C[i + 1:]
        below = closes_after < stop
        if below.any():
            j_rel = int(np.argmax(below))
            exit_idx = i + 1 + j_rel
            exited = True
        else:
            exit_idx = n - 1
            exited = False

        exit_price = float(C[exit_idx])
        ret = (exit_price - entry_open) / entry_open
        hold_days = exit_idx - i   # 信号日到退出日的交易日数

        trades.append({
            "code":        symbol,
            "name":        name[:10],
            "signal":      d.isoformat(),
            "entry":       pd.Timestamp(dates[i + 1]).date().isoformat(),
            "entry_price": round(entry_open, 2),
            "stop_price":  round(stop, 2),
            "exit":        pd.Timestamp(dates[exit_idx]).date().isoformat(),
            "exit_price":  round(exit_price, 2),
            "hold_days":   hold_days,
            "ret":         round(ret, 4),
            "exited":      int(exited),
            "is_20cm":     int(is_cb),
        })
    return trades


def report(trades: pd.DataFrame, label: str):
    if trades.empty:
        print(f"\n=== {label}: 无信号 ===")
        return
    print(f"\n=== {label} ===")
    n = len(trades)
    n_exited = int(trades["exited"].sum())
    n_open   = n - n_exited
    print(f"样本数: {n}  (已平仓 {n_exited}  仍持有 {n_open})")

    # 以已平仓样本为主（未平仓的收益未确定）
    s_all = trades["ret"]
    s_ex  = trades.loc[trades["exited"] == 1, "ret"]
    days  = trades["hold_days"]

    def _stats(s, days_s, tag):
        if s.empty:
            return
        win = (s > 0).mean() * 100
        big_win  = (s > 0.10).mean() * 100
        big_loss = (s < -0.05).mean() * 100
        mean = s.mean() * 100
        med  = s.median() * 100
        avg_days = days_s.mean()
        # 按 250 交易日年化（简单近似）
        ann = s.sum() / days_s.sum() * 250 * 100 if days_s.sum() > 0 else 0
        print(f"  [{tag}] 胜率: {win:5.1f}%  大赢(>10%): {big_win:5.1f}%  "
              f"大亏(<-5%): {big_loss:5.1f}%  均值: {mean:+6.2f}%  "
              f"中位: {med:+6.2f}%  均持仓: {avg_days:5.1f}日  日化年化≈{ann:+6.1f}%")

    _stats(s_all, days, "全部")
    if n_exited > 0 and n_open > 0:
        _stats(s_ex, days[trades["exited"] == 1], "已平仓")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end",   default="2026-04-16")
    ap.add_argument("--limit", type=int, default=0, help="只测前N只股票（0=全部）")
    ap.add_argument("--output", default="logs/backtest_fvg.csv")
    args = ap.parse_args()

    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    print(f"回测区间: {args.start} ~ {args.end}")

    # 大盘数据
    sh300_path = Path(__file__).parent.parent / "data" / "tdx" / "sh" / "lday" / "sh000300.day"
    sh300 = read_index_day(sh300_path)
    dapan_ok = compute_dapan_ok(sh300)
    dapan_ok_days = sum(1 for v in dapan_ok.values() if v)
    print(f"沪深300: {len(dapan_ok)} 天中 {dapan_ok_days} 天大盘OK ({dapan_ok_days*100//len(dapan_ok)}%)")

    # 分年度大盘OK统计
    print("\n分年度大盘OK天数:")
    by_year = {}
    for d, v in dapan_ok.items():
        y = d.year
        by_year.setdefault(y, [0, 0])
        by_year[y][0] += 1
        by_year[y][1] += int(v)
    for y in sorted(by_year):
        total, ok = by_year[y]
        if y >= 2021:
            print(f"  {y}: {ok:>3}/{total:>3} 天 ({ok*100//max(total,1):>2}%)")

    # 股票名
    names = get_names()

    # 股票池
    symbols = list_tdx_symbols()
    if args.limit > 0:
        symbols = symbols[:args.limit]
    print(f"扫描 {len(symbols)} 只股票...")

    # 跑回测
    all_trades = []
    for i, sym in enumerate(symbols, 1):
        if i % 500 == 0:
            print(f"  {i}/{len(symbols)}  累计信号: {len(all_trades)}")
        trades = backtest_stock(sym, names.get(sym, ""), start, end, dapan_ok)
        all_trades.extend(trades)

    df = pd.DataFrame(all_trades)
    if df.empty:
        print("无任何信号命中，请检查参数")
        return

    df["signal_dt"] = pd.to_datetime(df["signal"])

    # 写文件
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output, index=False, encoding="utf-8-sig")

    # 全局统计
    report(df, "整体")

    # In-sample vs Out-of-sample
    is_cut = pd.Timestamp("2025-01-01")
    report(df[df["signal_dt"] < is_cut], "In-Sample 2021-2024")
    report(df[df["signal_dt"] >= is_cut], "Out-of-Sample 2025-2026")

    # 按板块
    report(df[df["is_20cm"] == 0], "主板(10%)")
    report(df[df["is_20cm"] == 1], "创/科(20%)")

    # 按年度（按信号年份分组）
    print(f"\n=== 按信号年份汇总 ===")
    df["year"] = df["signal_dt"].dt.year
    yearly = df.groupby("year").agg(
        count=("ret", "size"),
        win_rate=("ret", lambda x: (x > 0).mean() * 100),
        mean_ret=("ret", lambda x: x.mean() * 100),
        med_ret=("ret", lambda x: x.median() * 100),
        avg_days=("hold_days", "mean"),
        pct_open=("exited", lambda x: (1 - x.mean()) * 100),
    )
    print(yearly.to_string(float_format=lambda v: f"{v:.2f}"))

    print(f"\n详细交易记录已保存: {args.output}")


if __name__ == "__main__":
    main()
