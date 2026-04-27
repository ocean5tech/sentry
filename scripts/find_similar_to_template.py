#!/usr/bin/env python3
"""
找当前形态最像 "宏景科技 (301396) 主升浪前" 的活跃候选股.

模板: 宏景 3 次 Sig 触发点 (2024-09-30, 2025-12-09, 2026-01-12) 的特征向量平均.
方法: z-score 标准化 + 欧氏距离 KNN.
候选: 最近 RECENT_DAYS 天内触发的 Sig.
输出: TOP 100 最相似候选 → CSV.
"""

import struct
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from core.data_loader import load_daily
from core.tdx_loader import list_tdx_symbols
from core.stock_names import get_names
from train_wave_model import (
    build_features, find_launches, check_wave,
    board_ok, is_20cm,
    LAUNCH_RET, LAUNCH_VOL_MUL, VOL_WIN, PULLBACK_WIN, LOOKBACK, MIN_WAVE,
)


TEMPLATES = {
    "hongjing": {
        "code": "301396",
        "name": "宏景科技",
        "dates": ["2024-09-30", "2025-12-09", "2026-01-12"],
        "mode": "strict",
        "out_name": "similar_to_hongjing.csv",
    },
    "litong": {
        "code": "603629",
        "name": "利通电子",
        "dates": ["2025-12-10", "2026-01-14", "2026-01-27", "2026-02-02"],
        "mode": "loose",
        "out_name": "similar_to_litong.csv",
    },
}

RECENT_DAYS = 60
TOP_N = 100
SPIKE_WIN = 5
SPIKE_SKIP = 5
MIN_TRIANGLE_LEN = 9
PRICE_CENTER = 80


def build_features_strict(historical, trigger, C, H, L, n, entry_close, sig_date, name, code):
    """严格版: 在原 build_features 基础上加 spike/triangle/price_zone 特征."""
    feats = build_features(historical, trigger, entry_close, sig_date, name, code)

    # 1. 尖头度: 每个 historical wave 的 launch 后 SPIKE_WIN 日内有一天 close < launch close
    spikes = []
    for w in historical:
        t = w["launch_i"]
        if t + SPIKE_WIN < n:
            after_min = float(np.min(C[t+1:t+1+SPIKE_WIN]))
            spike = (after_min < C[t])
        else:
            spike = False
        spikes.append(int(spike))
    trig_t = trigger["launch_i"]
    trig_close_at_high = (entry_close >= trigger["launch_high_day"] * 0.97)
    feats["spike_ratio"] = float(sum(spikes) / max(len(spikes), 1)) if spikes else 0.0
    feats["all_spike"] = int(all(spikes)) if spikes else 0
    feats["spike_count"] = int(sum(spikes))
    feats["trig_close_at_high"] = int(trig_close_at_high)

    # 2. 反喇叭口: 最近一次 historical launch + SPIKE_SKIP 到 trigger 之间分 3 段, 振幅递减
    if historical:
        win_start = historical[-1]["launch_i"] + SPIKE_SKIP
    else:
        win_start = trig_t - 20
    win_end = trig_t
    win_len = win_end - win_start
    if win_len >= MIN_TRIANGLE_LEN:
        seg_size = win_len // 3
        amps = []
        for k in range(3):
            s = win_start + k * seg_size
            e = win_start + (k + 1) * seg_size if k < 2 else win_end
            if e > s:
                sh = float(np.max(H[s:e]))
                sl = float(np.min(L[s:e]))
                sm = (sh + sl) / 2
                amps.append((sh - sl) / sm if sm > 0 else 0.0)
        if len(amps) == 3:
            feats["triangle_strict"] = int(amps[0] > amps[1] > amps[2])
            feats["amp_shrinkage"] = (amps[0] - amps[2]) / amps[0] if amps[0] > 0 else 0.0
            feats["amp_seg1"] = amps[0]
            feats["amp_seg2"] = amps[1]
            feats["amp_seg3"] = amps[2]
        else:
            for k_ in ("triangle_strict", "amp_shrinkage", "amp_seg1", "amp_seg2", "amp_seg3"):
                feats[k_] = 0.0
    else:
        for k_ in ("triangle_strict", "amp_shrinkage", "amp_seg1", "amp_seg2", "amp_seg3"):
            feats[k_] = 0.0

    # 3. 入场价位 (60-100 区间)
    feats["in_60_100"] = int(60 <= entry_close <= 100)
    feats["log_entry_dist_80"] = float(np.log(entry_close / PRICE_CENTER)) if entry_close > 0 else 0.0

    return feats


def scan_one_features(sym, name, mode="strict"):
    """扫一只股票, 返回所有 Sig 触发点的特征 (不要求 fut_60).
    mode: 'strict' (HH+Pierce 严格, 宏景型) 或 'loose' (各允许 1 次例外, 利通型)
    """
    if not board_ok(sym):
        return []
    try:
        df = load_daily(sym)
    except Exception:
        return []
    n = len(df)
    if n < VOL_WIN + LOOKBACK + 5:
        return []

    O = df["open"].values.astype(float)
    H = df["high"].values.astype(float)
    L = df["low"].values.astype(float)
    C = df["close"].values.astype(float)
    V = df["volume"].values.astype(float)
    dates = df["date"].dt.date.values

    launches = find_launches(O, H, L, C, V, dates)
    if len(launches) < MIN_WAVE:
        return []

    rows = []
    for k, la in enumerate(launches):
        t = la["launch_i"]
        window_start = t - LOOKBACK
        prev = [l for l in launches[:k] if l["launch_i"] >= window_start]
        all_l = prev + [la]
        if not check_wave(all_l, MIN_WAVE, mode=mode):
            continue
        feats = build_features_strict(prev, la, C, H, L, n, float(C[t]), la["launch_date"], name, sym)
        row = {
            "code": sym,
            "name": name,
            "sig_date": la["launch_date"].isoformat(),
            "entry": float(C[t]),
            "is_20cm": int(is_20cm(sym)),
            **feats,
        }
        rows.append(row)
    return rows


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", choices=list(TEMPLATES.keys()), default="hongjing",
                    help="hongjing (短期波浪+严格) 或 litong (W型+loose)")
    args = ap.parse_args()

    tpl_cfg = TEMPLATES[args.template]
    TEMPLATE_CODE = tpl_cfg["code"]
    TEMPLATE_NAME = tpl_cfg["name"]
    TEMPLATE_DATES = tpl_cfg["dates"]
    MODE = tpl_cfg["mode"]
    OUT_CSV = ROOT / "logs" / tpl_cfg["out_name"]

    names = get_names()
    syms = list_tdx_symbols()
    print(f"模板: {TEMPLATE_NAME} ({TEMPLATE_CODE})  模式: {MODE}  目标: {OUT_CSV.name}")
    print(f"扫 {len(syms)} 只 → 收集所有 Sig 特征")

    all_rows = []
    for i, s in enumerate(syms, 1):
        if i % 1500 == 0:
            print(f"  {i}/{len(syms)}  累计 {len(all_rows):,}")
        all_rows.extend(scan_one_features(s, names.get(s, ""), mode=MODE))

    df = pd.DataFrame(all_rows)
    df["sig_date_dt"] = pd.to_datetime(df["sig_date"])
    print(f"\n总 Sig {len(df):,}")

    tpl = df[(df["code"] == TEMPLATE_CODE) & (df["sig_date"].isin(TEMPLATE_DATES))]
    print(f"\n模板 ({TEMPLATE_NAME} {TEMPLATE_CODE}): 找到 {len(tpl)} / {len(TEMPLATE_DATES)} 个匹配:")
    print(tpl[["code", "name", "sig_date", "entry"]].to_string(index=False))
    if len(tpl) == 0:
        print("ERROR: 找不到模板, 退出")
        return

    feat_cols = [c for c in df.columns if c not in
                 ("code", "name", "sig_date", "sig_date_dt", "entry")]

    X = df[feat_cols].values.astype(float)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    mu = X.mean(axis=0)
    sigma = X.std(axis=0) + 1e-9
    Xn = (X - mu) / sigma

    tpl_X = tpl[feat_cols].values.astype(float)
    tpl_X = np.nan_to_num(tpl_X, nan=0.0, posinf=0.0, neginf=0.0)
    tpl_n = (tpl_X - mu) / sigma
    q = tpl_n.mean(axis=0)

    dist = np.sqrt(((Xn - q) ** 2).sum(axis=1))
    df["dist"] = dist

    latest = df["sig_date_dt"].max()
    cutoff = latest - pd.Timedelta(days=RECENT_DAYS)
    recent = df[df["sig_date_dt"] >= cutoff].copy()
    recent = recent.sort_values("dist").reset_index(drop=True)
    print(f"\n最近 {RECENT_DAYS} 天 (>= {cutoff.date()}) 内触发 Sig: {len(recent):,}")

    recent_unique = recent.drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)
    print(f"去重 (每个 code 保留最近一次): {len(recent_unique):,}")

    print(f"\n=== TOP {TOP_N} 最像 {TEMPLATE_NAME} 模板的活跃候选 ===")
    cols_show = ["code", "name", "sig_date", "entry", "is_20cm", "n_waves",
                 "total_gain_from_w1", "amp_mean", "pierce_mean", "total_span_days", "dist"]
    print(recent_unique[cols_show].head(TOP_N).to_string(index=False, float_format=lambda v: f"{v:.3f}"))

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    recent_unique.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n已写入 {OUT_CSV}  ({len(recent_unique):,} 行)")

    print(f"\n--- Sanity check: {TEMPLATE_NAME} 自身在最近 {RECENT_DAYS} 天的距离 ---")
    print(recent[recent["code"] == TEMPLATE_CODE][["sig_date", "entry", "dist"]].to_string(index=False))


if __name__ == "__main__":
    main()
