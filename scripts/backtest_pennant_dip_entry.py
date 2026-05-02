#!/usr/bin/env python3
"""
宏景三角旗 → 挖坑缩量日买入 → 等待10%起爆 回测.

流程:
  1. 检测到三角旗收敛 → 发出警告
  2. 收敛后 DIP_WINDOW 天内找"缩量下跌日"（量比<0.8×均量 且当日收跌）→ 买入
  3. 买入后 LAUNCH_WINDOW 天内观察是否出现起爆日（≥10%+放量≥1.5×均量）
  4. 以买入日次日开盘为入场价，统计：
       - 10/20/80 天后收益率与胜率
       - 40天内起爆日出现率
  5. 无缩量下跌日 → 放弃该信号

注意：无 look-ahead bias，买入完全依赖当时可见信息。
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

# ── 参数 ──────────────────────────────────────────────────────────────────────
DATA_START      = "2023-01-01"
HOLD_DAYS       = [10, 20, 80]
STEP            = 10
COOLDOWN        = 22
SWING_WIN       = 2

DIP_WINDOW      = 30         # 收敛后最多等N天找缩量下跌日
DIP_VOL_RATIO   = 0.80       # 量比 < 此值视为缩量
LAUNCH_WINDOW   = 40         # 买入后最多等N天观察起爆日
LAUNCH_PCT      = 0.10       # 起爆日阈值：≥10% 涨幅
LAUNCH_VOL_MUL  = 1.5        # 起爆日量比：≥1.5×均量
MIN_FUTURE      = 85


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

    cur   = float(C[-1])
    upper = float(np.polyval(coef_h, n - 1))
    lower = float(np.polyval(coef_l, n - 1))
    if cur > upper * 1.08 or cur < lower * 0.92:
        return False

    x    = np.arange(n, dtype=float)
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

    while t < n - DIP_WINDOW - MIN_FUTURE:
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

        # 三角旗检测到 → 在接下来 DIP_WINDOW 天找缩量下跌日
        vol_ma20 = float(np.mean(V_all[max(0, t - 19): t + 1]))
        dip_entry_idx = None

        for fwd in range(1, DIP_WINDOW + 1):
            i = t + fwd
            if i >= n:
                break
            # 缩量：量比 < DIP_VOL_RATIO
            vol_ok = vol_ma20 > 0 and V_all[i] / vol_ma20 < DIP_VOL_RATIO
            # 下跌日
            down_ok = C_all[i] < C_all[i - 1]
            if vol_ok and down_ok:
                dip_entry_idx = i + 1  # 次日开盘买入
                break

        last_sig_t = t
        t += COOLDOWN

        if dip_entry_idx is None or dip_entry_idx >= n:
            continue

        ep = float(O_all[dip_entry_idx])
        if ep <= 0:
            continue
        if dip_entry_idx + max(HOLD_DAYS) >= n:
            continue

        # 买入后 LAUNCH_WINDOW 天内是否出现起爆日
        launch_found = False
        launch_days  = None
        for fwd in range(1, LAUNCH_WINDOW + 1):
            li = dip_entry_idx + fwd
            if li >= n or C_all[li - 1] <= 0:
                break
            ret = (C_all[li] - C_all[li - 1]) / C_all[li - 1]
            vol_ratio = V_all[li] / vol_ma20 if vol_ma20 > 0 else 0
            if ret >= LAUNCH_PCT and vol_ratio >= LAUNCH_VOL_MUL:
                launch_found = True
                launch_days  = fwd
                break

        sig = {
            "pennant_date": str(pd.Timestamp(dates[t - COOLDOWN]).date()),
            "dip_date":     str(pd.Timestamp(dates[dip_entry_idx - 1]).date()),
            "entry_date":   str(pd.Timestamp(dates[dip_entry_idx]).date()),
            "entry_price":  round(ep, 2),
            "days_pennant_to_dip": dip_entry_idx - 1 - (t - COOLDOWN),
            "launch_found": launch_found,
            "launch_days":  launch_days,
        }
        for h in HOLD_DAYS:
            ei = dip_entry_idx + h
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

    print(f"[pennant_dip] {total} 只股票  起: {DATA_START}", flush=True)
    print(f"  买入: 收敛后缩量下跌日  起爆确认: ≥{LAUNCH_PCT*100:.0f}%+{LAUNCH_VOL_MUL}×量", flush=True)

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

    print(f"\n[pennant_dip] 完成. {len(all_sigs)} 个信号 / "
          f"{len(set(s['code'] for s in all_sigs))} 只股票", flush=True)

    # 起爆率
    launched = [s for s in all_sigs if s.get("launch_found")]
    print(f"  买入后40天内出现≥10%起爆: {len(launched)} ({len(launched)/max(len(all_sigs),1)*100:.1f}%)", flush=True)
    if launched:
        d = [s["launch_days"] for s in launched if s.get("launch_days")]
        print(f"  起爆间隔: 中位数={np.median(d):.0f}天  P25={np.percentile(d,25):.0f}  P75={np.percentile(d,75):.0f}", flush=True)

    out = ROOT / "logs" / "backtest_pennant_dip_entry.jsonl"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for s in all_sigs:
            f.write(json.dumps(s, ensure_ascii=False, default=str) + "\n")

    star    = [s for s in all_sigs if s["code"].startswith(("688","300","301"))]
    w_launch = launched
    summary = {
        "total":      len(all_sigs),
        "launched_n": len(launched),
        "all":        {f"hold_{h}d": stats([s.get(f"ret_{h}d") for s in all_sigs]) for h in HOLD_DAYS},
        "with_launch":{f"hold_{h}d": stats([s.get(f"ret_{h}d") for s in launched]) for h in HOLD_DAYS},
        "star":       {f"hold_{h}d": stats([s.get(f"ret_{h}d") for s in star])     for h in HOLD_DAYS},
    }
    with open(ROOT / "logs" / "backtest_pennant_dip_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 65)
    print("宏景三角旗 → 缩量挖坑买入 回测报告")
    print("=" * 65)
    print(f"总信号: {summary['total']}  其中后续有起爆: {summary['launched_n']}")

    for label, key in [("全部买入信号", "all"),
                        (f"有≥10%起爆 (n={len(launched)})", "with_launch"),
                        ("科创+创业板", "star")]:
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
