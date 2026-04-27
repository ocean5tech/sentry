#!/usr/bin/env python3
"""
验证假说 B: 上升波浪 + 每阶洗盘刺破支撑 → 主升浪.

定义:
  Launch (启动日):
    ret >= +5% AND V > 前 20 日均量 × LAUNCH_VOL_MUL
    记录 (launch_i, launch_open, launch_high)  launch_high = 启动日 H

  Pullback (本次启动后的回踩):
    从 launch+1 到 min(下次 launch, launch+PULLBACK_WIN) 之间的最低 low
    pierce = (pullback_low < launch_open) 即假跌破启动日开盘

  Wave (波浪, 当前为第 K 次 launch 时判定):
    过去 LOOKBACK 日内 ≥ MIN_WAVE_COUNT 次 launch
    higher_highs: launch_high 严格递增
    higher_lows:  历史 pullback_low 整体递增 (允许 ≤1 次例外)
    historical pullbacks 都 pierced (每次都洗过 launch_open)

对照:
  Baseline : 任意日
  Launch   : 单独 launch
  Wave_NoP : wave 条件但不要求 pierce
  Sig      : wave + pierce  (假说 B)

观察: 触发后 T+20 / T+60 / T+120 日收益, 超额大盘.
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


LAUNCH_RET = 0.05
LAUNCH_VOL_MUL = 2.0
VOL_WIN = 20
PULLBACK_WIN = 15
LOOKBACK = 60
MIN_WAVE = 2   # 过去 60 日至少 N 次 launch
FUT_WINS = (20, 60, 120)


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
    return closes, dates_arr, idx_map


def check_wave(launches, require_pierce, min_count):
    """launches: list of dicts with keys launch_i, launch_open, launch_high, pullback_low (None 若未完成)
       最后一个是当前 launch, pullback_low 可能为 None.
       历史 launches (前 N-1 个) 必须有 pullback_low."""
    if len(launches) < min_count:
        return False
    historical = launches[:-1]
    if len(historical) < min_count - 1:
        return False
    if any(l["pullback_low"] is None for l in historical):
        return False
    # higher highs
    highs = [l["launch_high"] for l in launches]
    for i in range(len(highs) - 1):
        if highs[i+1] <= highs[i]:
            return False
    # higher lows (允许 1 次例外)
    lows = [l["pullback_low"] for l in historical]
    if len(lows) >= 2:
        violations = sum(1 for i in range(len(lows) - 1) if lows[i+1] <= lows[i])
        if violations > 1:
            return False
    # pierce: 每次历史 pullback 都击穿该次的 launch_open
    if require_pierce:
        for l in historical:
            if not (l["pullback_low"] < l["launch_open"]):
                return False
    return True


def analyze_one(sym, sh_closes, sh_idx_map):
    if not board_ok(sym):
        return None
    try:
        df = load_daily(sym)
    except Exception:
        return None
    n = len(df)
    if n < VOL_WIN + LOOKBACK + max(FUT_WINS) + 5:
        return None

    O = df["open"].values.astype(float)
    H = df["high"].values.astype(float)
    L = df["low"].values.astype(float)
    C = df["close"].values.astype(float)
    V = df["volume"].values.astype(float)

    prev_C = np.concatenate(([np.nan], C[:-1]))
    ret = C / prev_C - 1
    v_ma = pd.Series(V).shift(1).rolling(VOL_WIN, min_periods=VOL_WIN).mean().values
    is_launch = (ret >= LAUNCH_RET) & (V > v_ma * LAUNCH_VOL_MUL) & (v_ma > 0)

    # 预计算: 每个位置 t 往后 PULLBACK_WIN 日内的最低 low (T+1..T+PULLBACK_WIN)
    pull_low = np.full(n, np.nan)
    for t in range(n):
        end = min(t + 1 + PULLBACK_WIN, n)
        if t + 1 < n:
            pull_low[t] = L[t+1:end].min()

    # 未来 K 日收益
    fut = {}
    for k in FUT_WINS:
        a = np.full(n, np.nan)
        if n > k:
            a[:-k] = C[k:] / C[:-k] - 1
        fut[k] = a

    # 大盘 K 日收益
    dates_d = df["date"].dt.date.values
    bench = {k: np.full(n, np.nan) for k in FUT_WINS}
    for j, d in enumerate(dates_d):
        i = sh_idx_map.get(d, -1)
        if i < 0: continue
        for k in FUT_WINS:
            if 0 <= i < len(sh_closes) - k:
                bench[k][j] = sh_closes[i + k] / sh_closes[i] - 1

    # 按时间扫,维护最近 LOOKBACK 日的 launches
    records = []
    for t in range(n):
        if not is_launch[t]:
            continue
        # 当前 launch
        cur = {
            "launch_i": t,
            "launch_open": O[t],
            "launch_high": H[t],
            "pullback_low": None,  # 还没完成
        }
        # 取过去 LOOKBACK 内的所有 launches (含今天, 但今天的 pullback 还没有)
        # 从 t-LOOKBACK 到 t
        window_start = max(0, t - LOOKBACK)
        # 对 window 内每个 launch 位置,算它的 pullback_low: 从它 +1 到 下一个 launch (或 PULLBACK_WIN)
        # 更简单: 直接用 pull_low[j] (T+1..T+15 内的 low)
        prev_launches = []
        for j in range(window_start, t):
            if is_launch[j]:
                prev_launches.append({
                    "launch_i": j,
                    "launch_open": O[j],
                    "launch_high": H[j],
                    "pullback_low": pull_low[j],
                })
        all_launches = prev_launches + [cur]

        wave_nop = check_wave(all_launches, require_pierce=False, min_count=MIN_WAVE)
        wave_p = check_wave(all_launches, require_pierce=True, min_count=MIN_WAVE)

        row = {
            "date": df["date"].iloc[t],
            "code": sym,
            "is_20cm": is_20cm(sym),
            "entry": C[t],
            "is_launch": True,
            "is_wave_nop": wave_nop,
            "is_wave_p": wave_p,
            "n_prev_launches": len(prev_launches),
        }
        for k in FUT_WINS:
            row[f"fut_{k}"] = fut[k][t]
            row[f"bench_{k}"] = bench[k][t]
            row[f"excess_{k}"] = fut[k][t] - bench[k][t]
        records.append(row)
    if not records:
        return None
    return pd.DataFrame(records)


def baseline_stats(syms, sh_closes, sh_idx_map, args):
    """快速采样 baseline: 每只取随机若干日计算 T+20/60/120."""
    import random
    random.seed(42)
    parts = []
    for s in syms:
        if not board_ok(s): continue
        try:
            df = load_daily(s)
        except Exception: continue
        n = len(df)
        if n < max(FUT_WINS) + 60:
            continue
        C = df["close"].values.astype(float)
        V = df["volume"].values.astype(float)
        dates_d = df["date"].dt.date.values
        pool = [t for t in range(60, n - max(FUT_WINS)) if V[t] > 0]
        if not pool: continue
        samp = random.sample(pool, min(20, len(pool)))
        for t in samp:
            d = dates_d[t]
            i = sh_idx_map.get(d, -1)
            if i < 0: continue
            row = {"date": df["date"].iloc[t], "code": s, "is_20cm": is_20cm(s)}
            for k in FUT_WINS:
                if 0 <= i < len(sh_closes) - k and n > t + k:
                    row[f"fut_{k}"] = C[t + k] / C[t] - 1
                    row[f"bench_{k}"] = sh_closes[i + k] / sh_closes[i] - 1
                    row[f"excess_{k}"] = row[f"fut_{k}"] - row[f"bench_{k}"]
            parts.append(row)
    return pd.DataFrame(parts)


def report(d, label):
    n = len(d)
    if n == 0:
        print(f"  {label:28}  n=0")
        return
    parts = [f"n={n:>8,}"]
    for k in FUT_WINS:
        col = f"excess_{k}"
        if col not in d.columns or d[col].isna().all():
            continue
        sub = d[~d[col].isna()]
        ex = sub[col] * 100
        p_big = (sub[col] > 0.50).mean() * 100  # 主升浪 (超额+50%+)
        win = (sub[col] > 0).mean() * 100
        parts.append(f"T+{k}: 超额{ex.mean():+5.2f}%/中位{ex.median():+5.2f}% 赢{win:4.1f}% ≥+50% {p_big:4.1f}%")
    print(f"  {label:28}  " + "  ".join(parts))


def main():
    global MIN_WAVE
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2026-04-21")
    ap.add_argument("--min-wave", type=int, default=MIN_WAVE)
    args = ap.parse_args()
    MIN_WAVE = args.min_wave

    sh_closes, sh_dates, sh_idx_map = build_sh300_lookup()
    syms = list_tdx_symbols()
    print(f"扫 {len(syms)} 只  窗口 {args.start}~{args.end}")
    print(f"参数: 启动 ret>={LAUNCH_RET*100:.0f}% V>{LAUNCH_VOL_MUL}×MA{VOL_WIN}  "
          f"回踩窗 {PULLBACK_WIN}日  波浪窗 {LOOKBACK}日  最少 {MIN_WAVE} 次launch")

    parts = []
    for i, s in enumerate(syms, 1):
        if i % 1500 == 0:
            print(f"  {i}/{len(syms)}  累计 launches {sum(len(p) for p in parts):,}")
        r = analyze_one(s, sh_closes, sh_idx_map)
        if r is not None and len(r):
            parts.append(r)

    df = pd.concat(parts, ignore_index=True)
    df["dt"] = pd.to_datetime(df["date"])
    df = df[(df["dt"] >= pd.Timestamp(args.start)) & (df["dt"] <= pd.Timestamp(args.end))].reset_index(drop=True)
    print(f"\nLaunch 触发 {len(df):,}  其中 Wave(NoP) {int(df['is_wave_nop'].sum()):,}  Sig(Wave+Pierce) {int(df['is_wave_p'].sum()):,}")

    # Baseline 采样
    print("\n采样 Baseline (每只 20 个随机日)...")
    base = baseline_stats(syms, sh_closes, sh_idx_map, args)
    base["dt"] = pd.to_datetime(base["date"])
    base = base[(base["dt"] >= pd.Timestamp(args.start)) & (base["dt"] <= pd.Timestamp(args.end))].reset_index(drop=True)

    print("\n=== 整体对比 ===")
    report(base, "Baseline (随机)")
    report(df, "Launch (单独)")
    report(df[df["is_wave_nop"]], "Wave_NoP (波浪,未必洗)")
    report(df[df["is_wave_p"]], "Sig: Wave+Pierce")

    print("\n=== 按板别 ===")
    for tag, m in [("主板 10CM", ~df["is_20cm"]), ("创/科 20CM", df["is_20cm"])]:
        sub = df[m]
        sub_b = base[base["is_20cm"] == (tag == "创/科 20CM")]
        print(f"  -- {tag} --")
        report(sub_b, "Baseline")
        report(sub, "Launch")
        report(sub[sub["is_wave_nop"]], "Wave_NoP")
        report(sub[sub["is_wave_p"]], "Sig")

    print("\n=== 按年度 (Sig vs Launch T+60 超额) ===")
    sig = df[df["is_wave_p"]].copy(); sig["year"] = sig["dt"].dt.year
    lau = df.copy(); lau["year"] = lau["dt"].dt.year
    yr = pd.DataFrame({
        "Launch_n": lau.groupby("year").size(),
        "Launch_T60超额%": lau.groupby("year")["excess_60"].mean() * 100,
        "Sig_n": sig.groupby("year").size(),
        "Sig_T60超额%": sig.groupby("year")["excess_60"].mean() * 100,
        "Sig_T60≥+50%比例": sig.groupby("year")["excess_60"].apply(lambda x: (x > 0.50).mean() * 100),
    }).fillna(0)
    print(yr.to_string(float_format=lambda v: f"{v:.2f}"))

    print("\n=== Sig 信号样本 (T+60 超额降序前 10) ===")
    sig_d = df[df["is_wave_p"]].copy()
    if len(sig_d) > 0:
        s = sig_d.nlargest(10, "excess_60")[["date", "code", "n_prev_launches", "entry", "fut_60", "bench_60", "excess_60"]]
        s["fut_60%"] = s["fut_60"] * 100
        s["bench_60%"] = s["bench_60"] * 100
        s["excess_60%"] = s["excess_60"] * 100
        print(s[["date", "code", "n_prev_launches", "entry", "fut_60%", "bench_60%", "excess_60%"]].to_string(index=False, float_format=lambda v: f"{v:.2f}"))

        print("\n=== Sig 信号样本 (T+60 超额升序前 10, 反面) ===")
        s2 = sig_d.nsmallest(10, "excess_60")[["date", "code", "n_prev_launches", "entry", "fut_60", "bench_60", "excess_60"]]
        s2["fut_60%"] = s2["fut_60"] * 100
        s2["bench_60%"] = s2["bench_60"] * 100
        s2["excess_60%"] = s2["excess_60"] * 100
        print(s2[["date", "code", "n_prev_launches", "entry", "fut_60%", "bench_60%", "excess_60%"]].to_string(index=False, float_format=lambda v: f"{v:.2f}"))


if __name__ == "__main__":
    main()
