#!/usr/bin/env python3
"""
反向研究: 先定义目标, 再找"成功前都长什么样".

目标: low[T1]/close[T0] >= 1.30, T1-T0 <= 20 交易日

对每日 T0 计算候选特征, 对比"赢家日"vs"非赢家日"分布,
用 Lift 衡量特征的预测力.

Usage:
    python3 scripts/analyze_target.py
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
FORWARD = 20          # 未来 20 交易日
TARGET = 0.30         # 30%+ 涨幅
MIN_BARS = 150        # 需要至少 150 天历史


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
    ma20_up = ma20 > _ref_smart(ma20, 5)
    g60 = np.full_like(C, np.nan)
    g60[60:] = C[60:] / C[:-60] - 1
    dates = sh300["date"].dt.date.values
    return (
        {d: bool(u) for d, u in zip(dates, ma20_up)},
        {d: float(g) if np.isfinite(g) else np.nan for d, g in zip(dates, g60)},
    )


def build_features(df: pd.DataFrame, g60_sh300: dict, mkt_up: dict):
    """返回 (features DataFrame, is_winner array)."""
    n = len(df)
    if n < MIN_BARS:
        return None, None

    C = df["close"].values.astype(float)
    H = df["high"].values.astype(float)
    L = df["low"].values.astype(float)
    V = df["volume"].values.astype(float)

    # 目标: 未来 20 天内 max(low) / close >= 1.30
    future_max_low = np.full(n, np.nan)
    for t in range(n - FORWARD):
        future_max_low[t] = np.max(L[t + 1 : t + FORWARD + 1])
    is_winner = (future_max_low / C >= 1 + TARGET).astype(np.int8)

    # 特征
    ma5   = MA(C, 5)
    ma10  = MA(C, 10)
    ma20  = MA(C, 20)
    ma60  = MA(C, 60)
    ma120 = MA(C, 120)
    ma_v5 = MA(V, 5)
    ma_v20 = MA(V, 20)

    ret5  = np.full(n, np.nan); ret5[5:]   = C[5:]/C[:-5] - 1
    ret20 = np.full(n, np.nan); ret20[20:] = C[20:]/C[:-20] - 1
    ret60 = np.full(n, np.nan); ret60[60:] = C[60:]/C[:-60] - 1

    # 52周高点 rolling
    high_252 = pd.Series(H).rolling(252, min_periods=60).max().values
    dist_high = C / high_252 - 1

    # 20日新高
    prev_high_20 = pd.Series(H).rolling(20, min_periods=20).max().shift(1).values
    new_high_20 = (C > prev_high_20).astype(np.int8)

    # ATR / close (波动率)
    tr = np.maximum.reduce([H - L,
                            np.abs(H - np.concatenate(([np.nan], C[:-1]))),
                            np.abs(L - np.concatenate(([np.nan], C[:-1])))])
    atr20 = pd.Series(tr).rolling(20, min_periods=20).mean().values
    vol_ratio_atr = atr20 / C

    # MACD 简化 (EMA12>EMA26)
    ema12 = pd.Series(C).ewm(span=12, adjust=False).mean().values
    ema26 = pd.Series(C).ewm(span=26, adjust=False).mean().values
    macd_bull = (ema12 > ema26).astype(np.int8)

    # 大盘数据对齐
    dates = df["date"].dt.date.values
    mkt_up_arr = np.array([mkt_up.get(d, False) for d in dates], dtype=np.int8)
    sh_g60 = np.array([g60_sh300.get(d, np.nan) for d in dates])

    # 相对强度
    rel_strong = (ret60 > sh_g60).astype(np.int8)

    features = {
        "三线多头":     ((ma20 > ma60) & (ma60 > ma120)).astype(np.int8),
        "5>10":        (ma5 > ma10).astype(np.int8),
        "收盘>MA20":   (C > ma20).astype(np.int8),
        "20日新高":     new_high_20,
        "放量1.5x":    (V > ma_v5 * 1.5).astype(np.int8),
        "放量2x":      (V > ma_v5 * 2.0).astype(np.int8),
        "放量3x":      (V > ma_v5 * 3.0).astype(np.int8),
        "MACD多头":    macd_bull,
        "大盘上升":     mkt_up_arr,
        "相对强势":     rel_strong,
        "距高点<10%":  (dist_high > -0.10).astype(np.int8),
        "距高点<15%":  (dist_high > -0.15).astype(np.int8),
        "5日涨>5%":    (ret5 > 0.05).astype(np.int8),
        "5日涨>10%":   (ret5 > 0.10).astype(np.int8),
        "20日涨>0":    (ret20 > 0).astype(np.int8),
        "60日涨>0":    (ret60 > 0).astype(np.int8),
        "低波动(ATR<3%)": (vol_ratio_atr < 0.03).astype(np.int8),
        "高波动(ATR>5%)": (vol_ratio_atr > 0.05).astype(np.int8),
    }

    # 有效样本掩码
    valid = ~np.isnan(future_max_low)
    valid &= ~np.isnan(ma120)
    valid &= ~np.isnan(ret60)

    df_feat = pd.DataFrame({k: v[valid] for k, v in features.items()})
    df_feat["winner"] = is_winner[valid]
    return df_feat, None


def main():
    symbols = list_tdx_symbols()
    print(f"扫描 {len(symbols)} 只股票...")

    sh300_path = Path(__file__).parent.parent / "data" / "tdx" / "sh" / "lday" / "sh000300.day"
    sh300 = read_index_day(sh300_path)
    mkt_up, g60_map = compute_sh300(sh300)

    all_feat = []
    for i, sym in enumerate(symbols, 1):
        if i % 1000 == 0:
            total_rows = sum(len(f) for f in all_feat)
            print(f"  {i}/{len(symbols)}  累计样本: {total_rows:,}")
        try:
            df = load_daily(sym)
        except Exception:
            continue
        if df.empty:
            continue
        feat, _ = build_features(df, g60_map, mkt_up)
        if feat is not None and len(feat):
            all_feat.append(feat)

    if not all_feat:
        print("无数据")
        return

    data = pd.concat(all_feat, ignore_index=True)
    print(f"\n总样本: {len(data):,}")

    base_rate = data["winner"].mean()
    print(f"\n基础胜率 (任一日): {base_rate*100:.2f}%")
    print(f"  → 意思是: 随机选一天买入, 未来20日最高 low/close 达到 1.30 的概率 {base_rate*100:.2f}%")

    # ===== 单特征分析 =====
    print("\n========== 单特征效果 (按 Lift 排序) ==========")
    print(f"{'特征':<18} {'触发占比':>8} {'命中胜率':>8} {'Lift':>6}  {'说明'}")
    print("-" * 78)

    feat_cols = [c for c in data.columns if c != "winner"]
    single_results = []
    for col in feat_cols:
        trigger_rate = data[col].mean()
        if trigger_rate == 0 or trigger_rate == 1:
            continue
        win_rate = data.loc[data[col] == 1, "winner"].mean()
        lift = win_rate / base_rate if base_rate > 0 else 0
        single_results.append((col, trigger_rate, win_rate, lift))

    single_results.sort(key=lambda x: -x[3])
    for col, tr, wr, lift in single_results:
        print(f"  {col:<16} {tr*100:>7.1f}% {wr*100:>7.1f}% {lift:>6.2f}x")

    # ===== 组合过滤 =====
    print("\n========== 组合过滤 ==========")
    combos = [
        ["三线多头"],
        ["三线多头", "相对强势"],
        ["三线多头", "相对强势", "大盘上升"],
        ["三线多头", "相对强势", "20日新高"],
        ["三线多头", "相对强势", "放量2x"],
        ["三线多头", "相对强势", "放量2x", "大盘上升"],
        ["三线多头", "相对强势", "20日新高", "放量2x"],
        ["三线多头", "相对强势", "20日新高", "放量2x", "大盘上升"],
        ["三线多头", "相对强势", "距高点<15%", "放量2x"],
        ["三线多头", "相对强势", "20日新高", "放量3x", "大盘上升"],
        ["三线多头", "相对强势", "5日涨>5%", "放量2x"],
    ]
    print(f"{'组合':<60} {'触发占比':>8} {'胜率':>7} {'Lift':>6}")
    print("-" * 90)
    for combo in combos:
        mask = np.ones(len(data), dtype=bool)
        for c in combo:
            mask &= (data[c] == 1)
        tr = mask.mean()
        if mask.sum() == 0:
            continue
        wr = data.loc[mask, "winner"].mean()
        lift = wr / base_rate
        label = " + ".join(combo)
        print(f"  {label:<58} {tr*100:>7.2f}% {wr*100:>6.1f}% {lift:>6.2f}x")


if __name__ == "__main__":
    main()
