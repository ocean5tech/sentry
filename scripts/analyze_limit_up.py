#!/usr/bin/env python3
"""
反向研究: 次日触及 +10% 的前一日特征.

目标: high[T+1] / close[T0] >= 1.10
  - 主板: 次日涨停或接近涨停
  - 20CM: 次日达到 +10% 位置

对 T0 收盘时能观察到的特征, 计算 Lift, 找出高预测力组合.

Usage:
    python3 scripts/analyze_limit_up.py
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
from core.formula_engine import _ref_smart


def _load_mytt():
    spec = importlib.util.spec_from_file_location(
        "mytt", Path(__file__).parent.parent / "core" / "mytt.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


MA = _load_mytt().MA
TARGET = 0.10   # 次日 +10%
MIN_BARS = 130


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


def compute_sh300(sh300: pd.DataFrame):
    C = sh300["close"].values.astype(float)
    ma20 = MA(C, 20)
    mkt_up = ma20 > _ref_smart(ma20, 5)
    dates = sh300["date"].dt.date.values
    return {d: bool(u) for d, u in zip(dates, mkt_up)}


def board_ok(symbol: str) -> bool:
    return symbol.startswith(("00", "60", "30", "68"))


def is_20cm(symbol: str) -> bool:
    return symbol.startswith(("30", "68"))


def build_features(df: pd.DataFrame, mkt_up: dict, cb: bool):
    n = len(df)
    if n < MIN_BARS:
        return None

    C = df["close"].values.astype(float)
    O = df["open"].values.astype(float)
    H = df["high"].values.astype(float)
    L = df["low"].values.astype(float)
    V = df["volume"].values.astype(float)

    prev_C = np.concatenate(([np.nan], C[:-1]))
    # 次日 +10% target
    next_H = np.concatenate((H[1:], [np.nan]))
    next_C = np.concatenate((C[1:], [np.nan]))
    winner = (next_H / C >= 1 + TARGET).astype(np.int8)

    # T0 当日特征
    ret_t0 = C / prev_C - 1
    body_ratio = (C - O) / O            # 阳线/阴线强度
    close_at_high = (C >= H * 0.995).astype(np.int8)   # 收盘近最高
    up_day = (C > O).astype(np.int8)

    # 涨停判定
    limit_thresh = 0.195 if cb else 0.095
    is_limit_up = (ret_t0 >= limit_thresh).astype(np.int8)

    # 历史涨停
    lu_series = pd.Series(is_limit_up)
    lu_5d = lu_series.rolling(5, min_periods=1).sum().values   # 5日内涨停次数
    lu_10d = lu_series.rolling(10, min_periods=1).sum().values
    # 连板 (今天涨停 且 昨天涨停)
    prev_lu = np.concatenate(([0], is_limit_up[:-1]))
    consecutive_lu = ((is_limit_up == 1) & (prev_lu == 1)).astype(np.int8)
    # 首板 (今天涨停 且 过去10日都没涨停过)
    first_lu = ((is_limit_up == 1) & (lu_10d <= 1)).astype(np.int8)

    # 成交量
    ma_v5 = MA(V, 5)
    prev_ma_v5 = _ref_smart(ma_v5, 1).astype(float)
    vol_ratio = V / prev_ma_v5

    # 均线
    ma5 = MA(C, 5)
    ma10 = MA(C, 10)
    ma20 = MA(C, 20)
    ma60 = MA(C, 60)
    ma120 = MA(C, 120)

    # 历史涨幅
    ret5 = np.full(n, np.nan); ret5[5:] = C[5:] / C[:-5] - 1
    ret20 = np.full(n, np.nan); ret20[20:] = C[20:] / C[:-20] - 1

    # 52周高点
    high_252 = pd.Series(H).rolling(252, min_periods=60).max().values
    dist_high = C / high_252 - 1

    # 20/60 日新高
    prev_high_20 = pd.Series(H).rolling(20, min_periods=20).max().shift(1).values
    new_high_20 = (C > prev_high_20).astype(np.int8)
    prev_high_60 = pd.Series(H).rolling(60, min_periods=60).max().shift(1).values
    new_high_60 = (C > prev_high_60).astype(np.int8)

    # 连阳
    up_series = pd.Series(up_day)
    consec_3up = (up_series.rolling(3, min_periods=3).sum() == 3).astype(np.int8).values

    # 大盘
    dates = df["date"].dt.date.values
    mkt_up_arr = np.array([mkt_up.get(d, False) for d in dates], dtype=np.int8)

    features = {
        "T0涨幅>3%":      (ret_t0 > 0.03).astype(np.int8),
        "T0涨幅>5%":      (ret_t0 > 0.05).astype(np.int8),
        "T0涨幅>7%":      (ret_t0 > 0.07).astype(np.int8),
        "T0涨停":         is_limit_up,
        "T0首板":         first_lu,
        "T0连板":         consecutive_lu,
        "5日内涨停过":      (lu_5d >= 1).astype(np.int8),
        "5日内涨停≥2次":    (lu_5d >= 2).astype(np.int8),
        "10日内涨停过":     (lu_10d >= 1).astype(np.int8),
        "收盘近最高":       close_at_high,
        "阳线实体>3%":     (body_ratio > 0.03).astype(np.int8),
        "阳线实体>5%":     (body_ratio > 0.05).astype(np.int8),
        "连3阳":          consec_3up,
        "20日新高":        new_high_20,
        "60日新高":        new_high_60,
        "放量2x":         (vol_ratio > 2).astype(np.int8),
        "放量3x":         (vol_ratio > 3).astype(np.int8),
        "放量5x":         (vol_ratio > 5).astype(np.int8),
        "三线多头":        ((ma20 > ma60) & (ma60 > ma120)).astype(np.int8),
        "5>10>20":       ((ma5 > ma10) & (ma10 > ma20)).astype(np.int8),
        "距高点<5%":       (dist_high > -0.05).astype(np.int8),
        "距高点<10%":      (dist_high > -0.10).astype(np.int8),
        "5日涨>10%":      (ret5 > 0.10).astype(np.int8),
        "20日涨>20%":     (ret20 > 0.20).astype(np.int8),
        "大盘上升":        mkt_up_arr,
    }

    valid = ~np.isnan(next_H) & ~np.isnan(ret_t0) & ~np.isnan(ma120) & ~np.isnan(ret20)
    # 停牌日不算
    valid &= (V > 0)
    # 排除 T0 一字板(涨停且开盘=收盘 ≥ 涨停价,实盘买不到)
    is_one_word = (is_limit_up == 1) & (O == C) & (H == L)
    valid &= ~is_one_word

    df_feat = pd.DataFrame({k: v[valid] for k, v in features.items()})
    df_feat["winner"] = winner[valid]
    return df_feat


def main():
    symbols = list_tdx_symbols()
    print(f"扫描 {len(symbols)} 只股票...")

    sh300_path = Path(__file__).parent.parent / "data" / "tdx" / "sh" / "lday" / "sh000300.day"
    sh300 = read_index_day(sh300_path)
    mkt_up = compute_sh300(sh300)

    all_feat = []
    for i, sym in enumerate(symbols, 1):
        if i % 1500 == 0:
            total = sum(len(f) for f in all_feat)
            print(f"  {i}/{len(symbols)}  累计: {total:,}")
        if not board_ok(sym):
            continue
        try:
            df = load_daily(sym)
        except Exception:
            continue
        if df.empty:
            continue
        f = build_features(df, mkt_up, is_20cm(sym))
        if f is not None and len(f):
            all_feat.append(f)

    if not all_feat:
        print("无数据")
        return

    data = pd.concat(all_feat, ignore_index=True)
    print(f"\n总样本: {len(data):,}")

    base = data["winner"].mean()
    print(f"\n基础胜率: {base*100:.2f}%  (任一日买, 次日触及+10%)")

    print("\n========== 单特征效果 (按 Lift 排序) ==========")
    print(f"{'特征':<20} {'触发占比':>10} {'胜率':>8} {'Lift':>7}")
    print("-" * 60)
    rs = []
    for col in data.columns:
        if col == "winner":
            continue
        tr = data[col].mean()
        if tr == 0 or tr == 1:
            continue
        wr = data.loc[data[col] == 1, "winner"].mean()
        lift = wr / base
        rs.append((col, tr, wr, lift))
    rs.sort(key=lambda x: -x[3])
    for col, tr, wr, lift in rs:
        print(f"  {col:<18} {tr*100:>9.2f}% {wr*100:>7.1f}% {lift:>6.2f}x")

    print("\n========== 组合过滤 ==========")
    combos = [
        ["T0涨停"],
        ["T0首板"],
        ["T0连板"],
        ["T0涨停", "5日内涨停过"],
        ["T0涨停", "收盘近最高"],
        ["T0涨停", "首板"] if False else ["T0首板", "放量3x"],
        ["T0涨停", "5日内涨停≥2次"],
        ["T0涨幅>7%", "收盘近最高", "放量2x"],
        ["T0涨停", "收盘近最高", "放量2x"],
        ["T0涨停", "收盘近最高", "20日新高"],
        ["T0涨停", "收盘近最高", "5日内涨停过"],
        ["T0首板", "收盘近最高", "放量3x"],
        ["T0连板", "收盘近最高"],
        ["T0涨停", "三线多头", "大盘上升"],
        ["T0涨幅>5%", "收盘近最高", "20日新高", "放量2x"],
    ]
    print(f"{'组合':<60} {'触发占比':>10} {'胜率':>8} {'Lift':>7}")
    print("-" * 90)
    for combo in combos:
        mask = np.ones(len(data), dtype=bool)
        for c in combo:
            if c not in data.columns:
                mask = None
                break
            mask &= (data[c] == 1)
        if mask is None or mask.sum() < 100:
            continue
        tr = mask.mean()
        wr = data.loc[mask, "winner"].mean()
        lift = wr / base
        label = " + ".join(combo)
        print(f"  {label:<58} {tr*100:>9.3f}% {wr*100:>7.1f}% {lift:>6.2f}x  (n={mask.sum():,})")


if __name__ == "__main__":
    main()
