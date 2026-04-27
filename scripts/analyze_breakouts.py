#!/usr/bin/env python3
"""
统计 A 股所有"放量突破"后的结局分布.

突破定义: close > HHV(high, 20)[昨] 且 volume > MA(volume, 5)[昨] * 1.5

对每个突破日 T0, 看其后 252 天内的表现:
  - peak_gain  = max(close[T0+1..]) / close[T0] - 1
  - max_dd     = min(low[T0+1..T0+20]) / close[T0] - 1   (前20日最大回撤)
  - days_to_peak

分类 (按 peak_gain):
  主升浪:    >= 100%
  大波段:    50%~100%
  中波段:    20%~50%
  小波段:    5%~20%
  假启动:    < 5% (且最大回撤 < -4% 视为快速失败)

输出:
  1. baseline: 无过滤
  2. filtered: 加上中期多头 + 相对强度 + 大盘过滤后

Usage:
    python3 scripts/analyze_breakouts.py
"""

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


def classify(peak_gain: float, dd: float) -> str:
    if peak_gain >= 1.0:
        return "主升浪"
    if peak_gain >= 0.5:
        return "大波段"
    if peak_gain >= 0.2:
        return "中波段"
    if peak_gain >= 0.05:
        return "小波段"
    if dd <= -0.04:
        return "假启动_快速失败"
    return "假启动_阴跌"


def board_ok(symbol: str) -> bool:
    return symbol.startswith(("00", "60", "30", "68"))


def analyze_stock(symbol: str, sh300_close_by_date: dict,
                  sh300_gain60_by_date: dict, sh300_ma20_up: dict,
                  lookforward=252):
    if not board_ok(symbol):
        return []
    try:
        df = load_daily(symbol)
    except Exception:
        return []
    if df.empty or len(df) < 130 + lookforward:
        return []

    C = df["close"].values.astype(float)
    H = df["high"].values.astype(float)
    L = df["low"].values.astype(float)
    V = df["volume"].values.astype(float)
    n = len(C)

    prev_high_20 = pd.Series(H).rolling(20, min_periods=20).max().shift(1).values
    ma_v_5 = pd.Series(V).rolling(5, min_periods=5).mean().values
    prev_ma_v = pd.Series(ma_v_5).shift(1).values
    ma60 = MA(C, 60)
    ma120 = MA(C, 120)

    results = []
    for t0 in range(130, n - lookforward):
        if not np.isfinite(prev_high_20[t0]) or not np.isfinite(prev_ma_v[t0]):
            continue
        if not (C[t0] > prev_high_20[t0] and V[t0] > prev_ma_v[t0] * 1.5):
            continue

        entry = C[t0]
        future_C = C[t0 + 1 : t0 + lookforward + 1]
        future_L = L[t0 + 1 : t0 + lookforward + 1]
        if future_C.size == 0:
            continue

        peak_gain = float(future_C.max() / entry - 1)
        # 前 20 交易日的最大回撤
        short_window_L = future_L[:20] if future_L.size >= 20 else future_L
        short_dd = float(short_window_L.min() / entry - 1)
        t1_rel = int(np.argmax(future_C))
        days_to_peak = t1_rel + 1

        # 过滤条件
        mid_up = (np.isfinite(ma60[t0]) and np.isfinite(ma120[t0])
                  and ma60[t0] > ma120[t0])
        # 个股60日涨幅
        stock_g60 = C[t0] / C[t0 - 60] - 1 if t0 >= 60 else np.nan
        d = df["date"].iloc[t0].date()
        sh_g60 = sh300_gain60_by_date.get(d, np.nan)
        rel_strong = (np.isfinite(stock_g60) and np.isfinite(sh_g60)
                      and stock_g60 > sh_g60)
        vol_strong = V[t0] > prev_ma_v[t0] * 2.0
        mkt_up = sh300_ma20_up.get(d, False)
        # 距离52周高点
        high_52w = H[max(0, t0 - 252) : t0].max() if t0 > 0 else np.nan
        close_to_high = (np.isfinite(high_52w)
                         and entry > high_52w * 0.85)

        results.append({
            "cat": classify(peak_gain, short_dd),
            "peak_gain": peak_gain,
            "short_dd":  short_dd,
            "days_to_peak": days_to_peak,
            "f_mid_up":   int(bool(mid_up)),
            "f_rel_strong": int(bool(rel_strong)),
            "f_vol_strong": int(bool(vol_strong)),
            "f_mkt_up":   int(bool(mkt_up)),
            "f_near_high": int(bool(close_to_high)),
        })
    return results


def compute_sh300(sh300: pd.DataFrame):
    C = sh300["close"].values.astype(float)
    ma20 = MA(C, 20)
    ma20_up = ma20 > _ref_smart(ma20, 5)
    g60 = np.full_like(C, np.nan)
    g60[60:] = C[60:] / C[:-60] - 1
    dates = sh300["date"].dt.date.values
    return (
        {d: bool(u) for d, u in zip(dates, ma20_up)},
        {d: float(g) if np.isfinite(g) else np.nan for d, g in zip(dates, g60)},
    )


def pct(df: pd.DataFrame, label: str):
    n = len(df)
    print(f"\n--- {label}  (n={n}) ---")
    if n == 0:
        return
    dist = df["cat"].value_counts()
    order = ["主升浪", "大波段", "中波段", "小波段", "假启动_快速失败", "假启动_阴跌"]
    for k in order:
        c = dist.get(k, 0)
        print(f"  {k:<14}  {c:>6}  ({c*100/n:5.1f}%)")
    s = df["peak_gain"]
    print(f"  峰值涨幅 均值: {s.mean()*100:+.1f}%  中位: {s.median()*100:+.1f}%")
    print(f"  前20日回撤 中位: {df['short_dd'].median()*100:+.1f}%  均值: {df['short_dd'].mean()*100:+.1f}%")
    print(f"  期望 E[ret]: {s.mean()*100:+.2f}%   (所有突破无差别持有到峰值)")


def main():
    names = get_names()
    symbols = list_tdx_symbols()

    sh300_path = Path(__file__).parent.parent / "data" / "tdx" / "sh" / "lday" / "sh000300.day"
    sh300 = read_index_day(sh300_path)
    ma20_up_map, g60_map = compute_sh300(sh300)

    print(f"扫描 {len(symbols)} 只股票的所有突破事件...")
    all_rows = []
    for i, sym in enumerate(symbols, 1):
        if i % 1000 == 0:
            print(f"  {i}/{len(symbols)}  累计突破: {len(all_rows)}")
        all_rows.extend(analyze_stock(sym, None, g60_map, ma20_up_map))

    if not all_rows:
        print("无数据")
        return

    df = pd.DataFrame(all_rows)
    out = Path(__file__).parent.parent / "logs" / "breakouts.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n总突破事件: {len(df)}")

    # ===== Baseline: 无过滤 =====
    pct(df, "Baseline (所有突破)")

    # ===== 单一过滤器 =====
    print("\n========== 单一过滤器效果 ==========")
    pct(df[df.f_mid_up == 1],    "① 个股MA60>MA120")
    pct(df[df.f_rel_strong == 1], "② 个股60日强于沪深300")
    pct(df[df.f_vol_strong == 1], "③ 放量≥2x (非1.5x)")
    pct(df[df.f_mkt_up == 1],    "④ 大盘MA20上升")
    pct(df[df.f_near_high == 1], "⑤ 距52周高点<15%")

    # ===== 组合过滤 =====
    print("\n========== 组合过滤 ==========")
    combo2 = df[(df.f_mid_up == 1) & (df.f_rel_strong == 1)]
    pct(combo2, "①+② 中期多头+相对强势")

    combo3 = combo2[combo2.f_mkt_up == 1]
    pct(combo3, "①+②+④ +大盘配合")

    combo4 = combo3[combo3.f_vol_strong == 1]
    pct(combo4, "①+②+④+③ +放量2x")

    combo5 = combo4[combo4.f_near_high == 1]
    pct(combo5, "五过滤全满足")

    print(f"\n详细记录: {out}")


if __name__ == "__main__":
    main()
