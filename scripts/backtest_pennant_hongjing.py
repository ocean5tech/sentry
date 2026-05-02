#!/usr/bin/env python3
"""
宏景三角旗策略 历史 Walk-Forward 回测（快速版）.

优化:
  - 只扫描近3年数据 (DATA_START 以后)，避免几十年历史数据拖累
  - 用 scipy.ndimage / numpy 向量化 rolling-max/min 替代 Python 循环
  - STEP=10 天检测一次，有信号跳过 win 天
  - 4条检测条件全满足才记录信号

入场: 信号日次日开盘  退出: 持仓结束日收盘
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

HOLD_DAYS  = [5, 10, 20]
DATA_START = "2023-01-01"     # 只用近3年数据
MIN_HIST   = 80               # 至少80根K线
MIN_FUTURE = 25               # 信号日后需25根计算收益
COOLDOWN   = 22               # 同股票两次信号最少间隔
STEP       = 10               # 每10天检测一次（有信号跳过win天）
SWING_WIN  = 2                # 与 pennant.py 一致


def _fast_local_highs(H: np.ndarray, w: int = SWING_WIN) -> np.ndarray:
    """numpy 向量化 rolling max."""
    size = 2 * w + 1
    rol_max = maximum_filter1d(H, size=size, mode='nearest')
    return np.where((H == rol_max) & (np.arange(len(H)) >= w) &
                    (np.arange(len(H)) < len(H) - w))[0]


def _fast_local_lows(L: np.ndarray, w: int = SWING_WIN) -> np.ndarray:
    size = 2 * w + 1
    rol_min = minimum_filter1d(L, size=size, mode='nearest')
    return np.where((L == rol_min) & (np.arange(len(L)) >= w) &
                    (np.arange(len(L)) < len(L) - w))[0]


def _precompute_ma20(C: np.ndarray) -> np.ndarray:
    cum = np.concatenate([[0.0], np.cumsum(C)])
    n   = len(C)
    ma  = np.empty(n)
    for i in range(n):
        lo = max(0, i - 19)
        ma[i] = (cum[i + 1] - cum[lo]) / (i - lo + 1)
    return ma


def _check(H, L, C, ma20_slice) -> dict | None:
    n  = len(C)
    sh = _fast_local_highs(H)
    sl = _fast_local_lows(L)
    if len(sh) < 2 or len(sl) < 2:
        return None

    x = np.arange(n, dtype=float)
    coef_h = np.polyfit(sh.astype(float), H[sh], 1)
    coef_l = np.polyfit(sl.astype(float), L[sl], 1)
    slope_h, slope_l = float(coef_h[0]), float(coef_l[0])
    if slope_h >= 0 or slope_l <= 0:
        return None

    denom = slope_h - slope_l
    if abs(denom) < 1e-10:
        return None
    apex_x  = (coef_l[1] - coef_h[1]) / denom
    dta     = int(round(apex_x - (n - 1)))
    if dta < -APEX_MAX_PAST or dta > APEX_MAX_FUTURE:
        return None

    mid = float(np.mean(C[-5:]))
    if mid <= 0:
        return None
    w0 = (float(np.polyval(coef_h, 0))      - float(np.polyval(coef_l, 0)))      / mid
    wn = (float(np.polyval(coef_h, n - 1))  - float(np.polyval(coef_l, n - 1)))  / mid
    if w0 <= 0 or wn <= 0 or wn >= w0:
        return None

    cur    = float(C[-1])
    upper  = float(np.polyval(coef_h, n - 1))
    lower  = float(np.polyval(coef_l, n - 1))
    if cur > upper * 1.08 or cur < lower * 0.92:
        return None

    # MA20 稳健上升
    ma_slope = float(np.polyfit(x, ma20_slice, 1)[0])
    ma_slope_pct = ma_slope / (float(np.mean(ma20_slice)) + 1e-9) * 100
    ma_mono = float(np.mean(np.diff(ma20_slice) >= 0))
    if ma_slope_pct <= MA20_MIN_SLOPE or ma_mono < MA20_MIN_MONO:
        return None

    # 低点单调递增
    if len(sl) < 2:
        return None
    low_mono = float(np.mean(np.diff(L[sl]) > 0))
    if low_mono < LOW_MIN_MONO:
        return None

    return {
        "days_to_apex": dta,
        "compression":  round(wn / w0, 3),
        "ma20_slope":   round(ma_slope_pct, 3),
        "ma20_mono":    round(ma_mono, 2),
        "low_mono":     round(low_mono, 2),
    }


def scan_stock(df: pd.DataFrame) -> list[dict]:
    if df is None or len(df) < MIN_HIST:
        return []

    H_all    = df["high"].values.astype(float)
    L_all    = df["low"].values.astype(float)
    C_all    = df["close"].values.astype(float)
    O_all    = df["open"].values.astype(float)
    dates    = df["date"].values
    n        = len(df)
    ma20_all = _precompute_ma20(C_all)

    signals    = []
    last_sig_t = -COOLDOWN - 1
    t          = MIN_WIN + 5

    while t < n - MIN_FUTURE:
        if t - last_sig_t < COOLDOWN:
            t += STEP
            continue

        hit = None
        for win in WINDOW_SIZES:
            if win < MIN_WIN or win > MAX_WIN:
                continue
            if t + 1 < win:
                continue
            s   = t - win + 1
            res = _check(H_all[s:t+1], L_all[s:t+1], C_all[s:t+1], ma20_all[s:t+1])
            if res is not None:
                hit = (win, res)
                break

        if hit is None:
            t += STEP
            continue

        win, res = hit
        entry_idx = t + 1
        if entry_idx >= n:
            t += STEP
            continue
        ep = float(O_all[entry_idx])
        if ep <= 0:
            t += STEP
            continue

        sig = {
            "trigger_date": str(pd.Timestamp(dates[t]).date()),
            "entry_date":   str(pd.Timestamp(dates[entry_idx]).date()),
            "entry_price":  round(ep, 2),
            "win_bars":     win,
            **res,
        }
        for h in HOLD_DAYS:
            ei = entry_idx + h
            sig[f"ret_{h}d"] = (
                round((float(C_all[ei]) - ep) / ep * 100, 1) if ei < n else None
            )
        signals.append(sig)
        last_sig_t = t
        t += win

    return signals


def stats(vals, cap=150.0):
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
    }


def main():
    names = get_names()
    syms  = list_tdx_symbols()
    total = len(syms)
    all_sigs: list[dict] = []

    print(f"[backtest_pennant] 扫描 {total} 只股票  "
          f"(数据起 {DATA_START}, 步长 STEP={STEP}天)...", flush=True)

    for i, code in enumerate(syms):
        if i % 500 == 0:
            print(f"  {i}/{total}  ({len(all_sigs)} 信号)", flush=True)

        df = load_daily(code)
        if df is None or len(df) == 0:
            continue

        # 只取近3年数据
        df = df[df["date"] >= pd.Timestamp(DATA_START)].reset_index(drop=True)
        sigs = scan_stock(df)
        name = names.get(code, "")
        for s in sigs:
            s["code"] = code
            s["name"] = name
        all_sigs.extend(sigs)

    print(f"[backtest_pennant] 完成. {len(all_sigs)} 个信号 / "
          f"{len(set(s['code'] for s in all_sigs))} 只股票", flush=True)

    out = ROOT / "logs" / "backtest_pennant_hongjing.jsonl"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for s in all_sigs:
            f.write(json.dumps(s, ensure_ascii=False, default=str) + "\n")
    print(f"[backtest_pennant] → {out}", flush=True)

    # ── 分组 ──────────────────────────────────────────────────────────────
    near  = [s for s in all_sigs if abs(s.get("days_to_apex", 99)) <= 15]
    tight = [s for s in all_sigs if s.get("compression", 1) < 0.4]
    star  = [s for s in all_sigs if s["code"].startswith(("688", "300", "301"))]

    summary = {
        "total":          len(all_sigs),
        "stocks":         len(set(s["code"] for s in all_sigs)),
        "near_apex_n":    len(near),
        "tight_n":        len(tight),
        "star_chinext_n": len(star),
        "all":   {f"hold_{h}d": stats([s.get(f"ret_{h}d") for s in all_sigs]) for h in HOLD_DAYS},
        "near":  {f"hold_{h}d": stats([s.get(f"ret_{h}d") for s in near])     for h in HOLD_DAYS},
        "tight": {f"hold_{h}d": stats([s.get(f"ret_{h}d") for s in tight])    for h in HOLD_DAYS},
        "star":  {f"hold_{h}d": stats([s.get(f"ret_{h}d") for s in star])     for h in HOLD_DAYS},
    }

    sout = ROOT / "logs" / "backtest_pennant_hongjing_summary.json"
    with open(sout, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # ── 打印 ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 65)
    print("宏景三角旗 Walk-Forward 回测报告  (近3年, 四重条件)")
    print("=" * 65)
    print(f"总信号: {summary['total']}  来自 {summary['stocks']} 只股票")
    print(f"近起爆(apex≤15d): {summary['near_apex_n']}  "
          f"高压缩(<40%): {summary['tight_n']}  "
          f"科创+创业: {summary['star_chinext_n']}")

    for label, key in [("全部信号", "all"),
                        ("近起爆 apex≤15d", "near"),
                        ("高压缩 compression<40%", "tight"),
                        ("科创+创业板", "star")]:
        g  = summary[key]
        n0 = g.get(f"hold_{HOLD_DAYS[0]}d", {}).get("n", 0)
        print(f"\n── {label} (n={n0}) ──")
        for h in HOLD_DAYS:
            st = g.get(f"hold_{h}d", {})
            if st:
                print(f"  {h:2d}天: n={st['n']:5d} | 胜率={st['win_rate']:5.1f}% | "
                      f"均值={st['mean']:+6.2f}% | 中位数={st['median']:+6.2f}% | "
                      f"P25={st['p25']:+6.2f}% P75={st['p75']:+6.2f}%")
    print()


if __name__ == "__main__":
    main()
