#!/usr/bin/env python3
"""
导出 Wave+Pierce (Sig) 信号列表.
每行: 股票 + 信号触发日 + 每次波浪(起始/波峰/波谷) + T+20/60/120 表现.

输出: logs/wave_sig_list.csv (UTF-8 BOM, Excel 友好)
"""

import struct
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.data_loader import load_daily
from core.tdx_loader import list_tdx_symbols
from core.stock_names import get_names


LAUNCH_RET = 0.05
LAUNCH_VOL_MUL = 2.0
VOL_WIN = 20
PULLBACK_WIN = 15
LOOKBACK = 60
MIN_WAVE = 2
FUT_WINS = (20, 60, 120)
MAX_WAVE = 5
START_DATE = "2021-01-01"

OUT_CSV = ROOT / "logs" / "wave_sig_list.csv"


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


def build_sh300():
    sh = read_index(ROOT / "data" / "tdx" / "sh" / "lday" / "sh000300.day")
    sh = sh.sort_values("date").reset_index(drop=True)
    closes = sh["close"].values.astype(float)
    dates_arr = sh["date"].dt.date.values
    return closes, {d: i for i, d in enumerate(dates_arr)}


def check_wave(launches, min_count):
    if len(launches) < min_count:
        return False
    historical = launches[:-1]
    if len(historical) < min_count - 1:
        return False
    if any(l.get("trough_price") is None for l in historical):
        return False
    peaks = [l["peak_price"] for l in launches]
    for i in range(len(peaks) - 1):
        if peaks[i+1] <= peaks[i]:
            return False
    troughs = [l["trough_price"] for l in historical]
    if len(troughs) >= 2:
        viol = sum(1 for i in range(len(troughs) - 1) if troughs[i+1] <= troughs[i])
        if viol > 1:
            return False
    for l in historical:
        if not (l["trough_price"] < l["launch_open"]):
            return False
    return True


def find_launches(O, H, L, C, V, dates):
    n = len(O)
    prev_C = np.concatenate(([np.nan], C[:-1]))
    ret = C / prev_C - 1
    v_ma = pd.Series(V).shift(1).rolling(VOL_WIN, min_periods=VOL_WIN).mean().values
    is_launch = (ret >= LAUNCH_RET) & (V > v_ma * LAUNCH_VOL_MUL) & (v_ma > 0)

    launches = []
    for t in range(n):
        if is_launch[t]:
            launches.append({"launch_i": t})

    for k, la in enumerate(launches):
        t = la["launch_i"]
        if k + 1 < len(launches):
            next_t = launches[k+1]["launch_i"]
        else:
            next_t = min(t + PULLBACK_WIN + 1, n)

        la["launch_open"] = float(O[t])
        la["launch_date"] = dates[t]

        if next_t > t:
            rng = H[t:next_t]
            pi = t + int(np.argmax(rng))
            la["peak_price"] = float(H[pi])
            la["peak_date"] = dates[pi]
        else:
            la["peak_price"] = float(H[t])
            la["peak_date"] = dates[t]

        if t + 1 < next_t:
            rng = L[t+1:next_t]
            ti = t + 1 + int(np.argmin(rng))
            la["trough_price"] = float(L[ti])
            la["trough_date"] = dates[ti]
        else:
            la["trough_price"] = None
            la["trough_date"] = None

    return launches


def scan_one(sym, name, sh_closes, sh_idx_map):
    if not board_ok(sym):
        return []
    try:
        df = load_daily(sym)
    except Exception:
        return []
    n = len(df)
    if n < VOL_WIN + LOOKBACK + 10:
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

    fut = {}
    for k in FUT_WINS:
        a = np.full(n, np.nan)
        if n > k:
            a[:-k] = C[k:] / C[:-k] - 1
        fut[k] = a

    bench = {k: np.full(n, np.nan) for k in FUT_WINS}
    for j, d in enumerate(dates):
        i = sh_idx_map.get(d, -1)
        if i < 0:
            continue
        for k in FUT_WINS:
            if 0 <= i < len(sh_closes) - k:
                bench[k][j] = sh_closes[i + k] / sh_closes[i] - 1

    results = []
    for k, la in enumerate(launches):
        t = la["launch_i"]
        window_start = t - LOOKBACK
        prev = [l for l in launches[:k] if l["launch_i"] >= window_start]
        all_l = prev + [la]
        if not check_wave(all_l, MIN_WAVE):
            continue

        row = {
            "code": sym,
            "name": name,
            "is_20cm": int(is_20cm(sym)),
            "sig_date": la["launch_date"].isoformat(),
            "n_waves": len(all_l),
            "entry": round(float(C[t]), 2),
        }
        for k_fut in FUT_WINS:
            v = fut[k_fut][t]
            bv = bench[k_fut][t]
            row[f"fut_{k_fut}%"] = round(float(v) * 100, 2) if not np.isnan(v) else None
            row[f"bench_{k_fut}%"] = round(float(bv) * 100, 2) if not np.isnan(bv) else None
            if not np.isnan(v) and not np.isnan(bv):
                row[f"excess_{k_fut}%"] = round(float(v - bv) * 100, 2)
            else:
                row[f"excess_{k_fut}%"] = None

        for i in range(MAX_WAVE):
            pref = f"w{i+1}_"
            if i < len(all_l):
                w = all_l[i]
                row[f"{pref}start_d"] = w["launch_date"].isoformat()
                row[f"{pref}start_p"] = round(w["launch_open"], 2)
                row[f"{pref}peak_d"] = w["peak_date"].isoformat() if w.get("peak_date") else ""
                row[f"{pref}peak_p"] = round(w["peak_price"], 2) if w.get("peak_price") is not None else None
                row[f"{pref}trough_d"] = w["trough_date"].isoformat() if w.get("trough_date") else ""
                row[f"{pref}trough_p"] = round(w["trough_price"], 2) if w.get("trough_price") is not None else None
            else:
                for suf in ("start_d", "start_p", "peak_d", "peak_p", "trough_d", "trough_p"):
                    row[f"{pref}{suf}"] = ""
        results.append(row)
    return results


def main():
    sh_closes, sh_idx_map = build_sh300()
    names = get_names()
    syms = list_tdx_symbols()
    print(f"扫 {len(syms)} 只  过滤起始 {START_DATE}")

    all_rows = []
    for i, s in enumerate(syms, 1):
        if i % 1500 == 0:
            print(f"  {i}/{len(syms)}  累计 Sig {len(all_rows):,}")
        rs = scan_one(s, names.get(s, ""), sh_closes, sh_idx_map)
        if rs:
            all_rows.extend(rs)

    df = pd.DataFrame(all_rows)
    df["sig_date_dt"] = pd.to_datetime(df["sig_date"])
    df = df[df["sig_date_dt"] >= pd.Timestamp(START_DATE)].copy()
    df = df.drop(columns=["sig_date_dt"]).sort_values(["sig_date", "code"]).reset_index(drop=True)
    print(f"\n总 Sig {len(df):,}  (2021+ 过滤后)")

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(f"写入 {OUT_CSV} ({OUT_CSV.stat().st_size/1024:.0f} KB)")

    print(f"\n按 excess_60% 分箱:")
    ex = df["excess_60%"].dropna()
    bins = [(-1000, -50), (-50, -20), (-20, 0), (0, 20), (20, 50), (50, 100), (100, 200), (200, 2000)]
    for lo, hi in bins:
        cnt = ((ex >= lo) & (ex < hi)).sum()
        print(f"  [{lo:+5}%, {hi:+5}%): {cnt:6,}  ({cnt/len(ex)*100:5.2f}%)")

    print(f"\nTOP 20 超额 (T+60):")
    top = df.nlargest(20, "excess_60%")[["code", "name", "sig_date", "n_waves", "entry", "fut_60%", "excess_60%"]]
    print(top.to_string(index=False))

    print(f"\nBOT 10 超额 (T+60):")
    bot = df.nsmallest(10, "excess_60%")[["code", "name", "sig_date", "n_waves", "entry", "fut_60%", "excess_60%"]]
    print(bot.to_string(index=False))


if __name__ == "__main__":
    main()
