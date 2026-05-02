#!/usr/bin/env python3
"""
宏景三角旗 → 挖坑 → 起爆日 回测.

流程:
  1. 检测到三角旗收敛 (四重条件) → 发出警告
  2. 往后最多 LAUNCH_WINDOW 个交易日内寻找"起爆日":
       * 单日涨幅 ≥ LAUNCH_MIN_PCT
       * 成交量 ≥ 前20日均量 × LAUNCH_VOL_MUL
  3. 若找到起爆日 → 起爆日次日开盘买入
  4. 统计 10 / 20 / 80 个交易日后的收益率与胜率
  5. 若在 LAUNCH_WINDOW 内未找到起爆日 → 放弃
"""
import sys, json
import numpy as np
import pandas as pd
from pathlib import Path
from scipy.ndimage import maximum_filter1d, minimum_filter1d

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / "q-seed" / "lib"))

from core.tdx_loader import list_tdx_symbols
from core.data_loader import load_daily
from core.stock_names import get_names
from pennant import (WINDOW_SIZES, MIN_WIN, MAX_WIN,
                     MA20_MIN_SLOPE, MA20_MIN_MONO, LOW_MIN_MONO,
                     APEX_MAX_FUTURE, APEX_MAX_PAST)

# ── 回测参数 ─────────────────────────────────────────────────────────────────
DATA_START      = "2023-01-01"
HOLD_DAYS       = [10, 20, 80]
STEP            = 10              # 三角旗检测步长（交易日）
COOLDOWN        = 22
SWING_WIN       = 2

LAUNCH_WINDOW   = 40              # 三角旗后最多等待 N 天找起爆日（约2个月）
LAUNCH_MIN_PCT  = 0.05            # 起爆日涨幅阈值（5%）
LAUNCH_VOL_MUL  = 1.5             # 起爆日量比（1.5× 20日均量）
MIN_FUTURE      = 85              # 起爆日后至少需要 85 根数据计算 80d 收益


def _fast_local_highs(H, w=SWING_WIN):
    size = 2 * w + 1
    rol  = maximum_filter1d(H, size=size, mode='nearest')
    idx  = np.arange(len(H))
    return np.where((H == rol) & (idx >= w) & (idx < len(H) - w))[0]


def _fast_local_lows(L, w=SWING_WIN):
    size = 2 * w + 1
    rol  = minimum_filter1d(L, size=size, mode='nearest')
    idx  = np.arange(len(L))
    return np.where((L == rol) & (idx >= w) & (idx < len(L) - w))[0]


def _precompute_ma20(C):
    cum = np.concatenate([[0.0], np.cumsum(C)])
    n   = len(C)
    ma  = np.empty(n)
    for i in range(n):
        lo = max(0, i - 19)
        ma[i] = (cum[i+1] - cum[lo]) / (i - lo + 1)
    return ma


def _check_pennant(H, L, C, ma20_slice):
    n  = len(C)
    sh = _fast_local_highs(H)
    sl = _fast_local_lows(L)
    if len(sh) < 2 or len(sl) < 2:
        return False

    coef_h = np.polyfit(sh.astype(float), H[sh], 1)
    coef_l = np.polyfit(sl.astype(float), L[sl], 1)
    s_h, s_l = float(coef_h[0]), float(coef_l[0])
    if s_h >= 0 or s_l <= 0:
        return False

    denom = s_h - s_l
    if abs(denom) < 1e-10:
        return False
    dta = int(round((coef_l[1] - coef_h[1]) / denom - (n - 1)))
    if dta < -APEX_MAX_PAST or dta > APEX_MAX_FUTURE:
        return False

    mid = float(np.mean(C[-5:]))
    if mid <= 0:
        return False
    w0 = (float(np.polyval(coef_h, 0))     - float(np.polyval(coef_l, 0)))     / mid
    wn = (float(np.polyval(coef_h, n - 1)) - float(np.polyval(coef_l, n - 1))) / mid
    if w0 <= 0 or wn <= 0 or wn >= w0:
        return False

    cur    = float(C[-1])
    upper  = float(np.polyval(coef_h, n - 1))
    lower  = float(np.polyval(coef_l, n - 1))
    if cur > upper * 1.08 or cur < lower * 0.92:
        return False

    x = np.arange(n, dtype=float)
    ma_s = float(np.polyfit(x, ma20_slice, 1)[0]) / (float(np.mean(ma20_slice)) + 1e-9) * 100
    ma_m = float(np.mean(np.diff(ma20_slice) >= 0))
    if ma_s <= MA20_MIN_SLOPE or ma_m < MA20_MIN_MONO:
        return False

    if len(sl) < 2:
        return False
    if float(np.mean(np.diff(L[sl]) > 0)) < LOW_MIN_MONO:
        return False

    return True


def scan_stock(df: pd.DataFrame) -> list[dict]:
    if df is None or len(df) < 80:
        return []

    H_all    = df["high"].values.astype(float)
    L_all    = df["low"].values.astype(float)
    C_all    = df["close"].values.astype(float)
    O_all    = df["open"].values.astype(float)
    V_all    = df["volume"].values.astype(float)
    dates    = df["date"].values
    n        = len(df)
    ma20_all = _precompute_ma20(C_all)

    signals    = []
    last_sig_t = -COOLDOWN - 1
    t          = MIN_WIN + 5

    while t < n - LAUNCH_WINDOW - MIN_FUTURE:
        if t - last_sig_t < COOLDOWN:
            t += STEP
            continue

        pennant_hit = False
        for win in WINDOW_SIZES:
            if win < MIN_WIN or win > MAX_WIN:
                continue
            if t + 1 < win:
                continue
            s = t - win + 1
            if _check_pennant(H_all[s:t+1], L_all[s:t+1], C_all[s:t+1], ma20_all[s:t+1]):
                pennant_hit = True
                break

        if not pennant_hit:
            t += STEP
            continue

        # 三角旗发现 → 在接下来 LAUNCH_WINDOW 天内找起爆日
        vol_ma20 = float(np.mean(V_all[max(0, t - 19): t + 1]))
        launch_idx = None
        for fwd in range(1, LAUNCH_WINDOW + 1):
            i = t + fwd
            if i >= n:
                break
            if C_all[i - 1] <= 0:
                continue
            ret = (C_all[i] - C_all[i - 1]) / C_all[i - 1]
            vol_ratio = V_all[i] / vol_ma20 if vol_ma20 > 0 else 0
            if ret >= LAUNCH_MIN_PCT and vol_ratio >= LAUNCH_VOL_MUL:
                launch_idx = i
                break

        last_sig_t = t
        t += COOLDOWN  # 不管有没有起爆，该三角旗冷却

        if launch_idx is None:
            continue  # 2月内无起爆日，放弃

        entry_idx = launch_idx + 1
        if entry_idx >= n:
            continue

        ep = float(O_all[entry_idx])
        if ep <= 0:
            continue

        # 检查是否有足够 future 数据
        if entry_idx + max(HOLD_DAYS) >= n:
            continue

        launch_ret = (C_all[launch_idx] - C_all[launch_idx - 1]) / C_all[launch_idx - 1]
        launch_vol_ratio = float(V_all[launch_idx] / vol_ma20) if vol_ma20 > 0 else 0

        sig = {
            "pennant_date": str(pd.Timestamp(dates[t - COOLDOWN]).date()),
            "launch_date":  str(pd.Timestamp(dates[launch_idx]).date()),
            "entry_date":   str(pd.Timestamp(dates[entry_idx]).date()),
            "entry_price":  round(ep, 2),
            "days_pennant_to_launch": launch_idx - (t - COOLDOWN),
            "launch_ret_pct": round(launch_ret * 100, 1),
            "launch_vol_ratio": round(launch_vol_ratio, 2),
        }
        for h in HOLD_DAYS:
            ei = entry_idx + h
            if ei < n:
                sig[f"ret_{h}d"] = round((float(C_all[ei]) - ep) / ep * 100, 1)
            else:
                sig[f"ret_{h}d"] = None

        signals.append(sig)

    return signals


def stats(vals, cap=200.0):
    vs = [v for v in vals if v is not None and abs(v) < cap]
    if not vs:
        return {}
    return {
        "n":        len(vs),
        "mean":     round(float(np.mean(vs)), 2),
        "median":   round(float(np.median(vs)), 2),
        "win_rate": round(sum(v > 0 for v in vs) / len(vs) * 100, 1),
        "p25":      round(float(np.percentile(vs, 25)), 2),
        "p75":      round(float(np.percentile(vs, 75)), 2),
        "p90":      round(float(np.percentile(vs, 90)), 2),
    }


def main():
    names = get_names()
    syms  = list_tdx_symbols()
    total = len(syms)
    all_sigs: list[dict] = []

    print(f"[backtest_pennant_launch] {total} 只股票  起: {DATA_START}", flush=True)
    print(f"  起爆条件: ≥{LAUNCH_MIN_PCT*100:.0f}% 涨幅 + ≥{LAUNCH_VOL_MUL}×均量  等待窗口: {LAUNCH_WINDOW}天", flush=True)

    for i, code in enumerate(syms):
        if i % 1000 == 0:
            print(f"  {i}/{total}  ({len(all_sigs)} 信号)", flush=True)

        df = load_daily(code)
        if df is None or len(df) == 0:
            continue
        df = df[df["date"] >= pd.Timestamp(DATA_START)].reset_index(drop=True)
        sigs = scan_stock(df)
        name = names.get(code, "")
        for s in sigs:
            s["code"] = code
            s["name"] = name
        all_sigs.extend(sigs)

    print(f"\n[backtest_pennant_launch] 完成. 共 {len(all_sigs)} 个起爆信号 / "
          f"{len(set(s['code'] for s in all_sigs))} 只股票", flush=True)

    # 挖坑时长分布
    dtl = [s["days_pennant_to_launch"] for s in all_sigs]
    if dtl:
        print(f"  三角旗→起爆 间隔: 中位数={np.median(dtl):.0f}天  "
              f"P25={np.percentile(dtl,25):.0f}  P75={np.percentile(dtl,75):.0f}", flush=True)

    # 保存
    out = ROOT / "logs" / "backtest_pennant_launch.jsonl"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for s in all_sigs:
            f.write(json.dumps(s, ensure_ascii=False, default=str) + "\n")

    # 分组
    star = [s for s in all_sigs if s["code"].startswith(("688", "300", "301"))]
    quick = [s for s in all_sigs if s.get("days_pennant_to_launch", 99) <= 20]

    summary = {
        "total":    len(all_sigs),
        "stocks":   len(set(s["code"] for s in all_sigs)),
        "star_n":   len(star),
        "quick_n":  len(quick),
        "all":   {f"hold_{h}d": stats([s.get(f"ret_{h}d") for s in all_sigs]) for h in HOLD_DAYS},
        "star":  {f"hold_{h}d": stats([s.get(f"ret_{h}d") for s in star])     for h in HOLD_DAYS},
        "quick": {f"hold_{h}d": stats([s.get(f"ret_{h}d") for s in quick])    for h in HOLD_DAYS},
    }

    sout = ROOT / "logs" / "backtest_pennant_launch_summary.json"
    with open(sout, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 65)
    print("三角旗→起爆日 回测报告")
    print("=" * 65)
    print(f"总起爆信号: {summary['total']}  科创+创业: {summary['star_n']}  "
          f"快速起爆(≤20天): {summary['quick_n']}")

    for label, key in [("全部", "all"), ("科创+创业板", "star"), ("快速起爆≤20天", "quick")]:
        g  = summary[key]
        n0 = g.get(f"hold_{HOLD_DAYS[0]}d", {}).get("n", 0)
        print(f"\n── {label} (n={n0}) ──")
        for h in HOLD_DAYS:
            st = g.get(f"hold_{h}d", {})
            if st:
                print(f"  {h:2d}天: n={st['n']:5d} | 胜率={st['win_rate']:5.1f}% | "
                      f"均值={st['mean']:+6.2f}% | 中位数={st['median']:+6.2f}% | "
                      f"P25={st['p25']:+6.2f}% P75={st['p75']:+6.2f}% P90={st['p90']:+6.2f}%")
    print()


if __name__ == "__main__":
    main()
