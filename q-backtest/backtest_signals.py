#!/usr/bin/env python3
"""
三红V4 + 宏景三角旗 历史回测

三红规则：
  入场：c3确认+c4出现后，回调至c3中值以下 + 缩量≤20日均量50% → T+1开盘买入
  出场：起爆日（≥8%大阳）后第3天收盘
  止损：c3低点（持有期间收盘跌破即止损）
  超时：入场后60天未起爆 → 按持有60天收盘退出

宏景三角旗规则：
  观察：pennant确认后不买
  入场：pennant结束后40交易日内出现放量大阳（≥8% + 量≥1.5倍20日均量）→ T+1开盘
  出场：持有80交易日后收盘
  止损：pennant最后摆动低点（收盘跌破即止损）

设计：O(N)单次扫描，不做滑窗重复计算
"""
import sys, json, importlib.util, time
import numpy as np
from pathlib import Path
from datetime import date

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "q-seed" / "lib"))

import core.tdx_loader as tdx
tdx.set_tdx_dir(str(ROOT / "data" / "tdx"))
from core.data_loader import load_daily
from core.tdx_loader import list_tdx_symbols

def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

tr_mod      = _load("three_red_bars_v4", ROOT / "core/strategies/three_red_bars_v4.py")
pennant_mod = _load("pennant",           ROOT / "q-seed/lib/pennant.py")

# ── 上证指数 MA20 查找表（大盘过滤用）──────────────────
def _build_index_map() -> dict:
    """返回 {datestr: (close, ma20)}，用于入场日大盘过滤."""
    import struct
    path = ROOT / "data" / "tdx" / "sh" / "lday" / "sh000001.day"
    if not path.exists():
        return {}
    data = path.read_bytes()
    fmt  = "<IIIIIfII"
    sz   = struct.calcsize(fmt)
    rows = []
    for off in range(0, len(data) - sz + 1, sz):
        dt, o, h, l, c, *_ = struct.unpack(fmt, data[off: off + sz])
        if 19900101 <= dt <= 30000101:
            rows.append((str(dt), c / 100))
    closes = np.array([c for _, c in rows], dtype=float)
    result = {}
    for i, (d, c) in enumerate(rows):
        ma20 = float(np.mean(closes[max(0, i - 19): i + 1]))
        result[d] = (c, ma20)
    return result

INDEX_MAP = _build_index_map()


def _market_ok(datestr: str) -> bool:
    """入场日大盘过滤：上证指数收盘 > MA20 才允许入场."""
    key = datestr.replace("-", "")
    if key not in INDEX_MAP:
        return True   # 无数据时不过滤
    c, ma20 = INDEX_MAP[key]
    return c > ma20


# ── 参数 ──────────────────────────────────────────────
LAUNCH_PCT           = tr_mod.LAUNCH_PCT       # 0.08
LAUNCH_HOLD          = 3
MAX_HOLD_TR          = 60
PENNANT_WATCH        = 40
PENNANT_BREAKOUT_PCT = 0.08
PENNANT_VOL_MULT     = 1.5
PENNANT_HOLD         = 80
PENNANT_STEP         = 60    # 每60根检测一次（约3个月），覆盖所有最短形态
PENNANT_HIST_BARS    = 600   # 只扫最近600根K线（约2.4年），降低计算量
CUTOFF_BARS          = 90


# ════════════════════════════════════════════════════
# 三红 — 与 three_red_bars_v4.py 同步的回测扫描
# ════════════════════════════════════════════════════
def _calendar_gap(dates, i) -> int:
    if dates is None or i == 0:
        return 1
    try:
        from datetime import datetime
        d1 = datetime.strptime(str(dates[i-1])[:10], '%Y-%m-%d')
        d2 = datetime.strptime(str(dates[i])[:10], '%Y-%m-%d')
        return (d2 - d1).days
    except Exception:
        return 1


def backtest_three_red(df) -> list[dict]:
    n = len(df)
    if n < 120 + CUTOFF_BARS:
        return []

    C = df["close"].values.astype(float)
    O = df["open"].values.astype(float)
    H = df["high"].values.astype(float)
    L = df["low"].values.astype(float)
    V = df["volume"].values.astype(float)
    try:
        dates = df["date"].dt.strftime("%Y-%m-%d").values
    except Exception:
        dates = np.array([str(i) for i in range(n)])

    big = [i for i in range(1, n)
           if C[i-1] > 0
           and (C[i] - C[i-1]) / C[i-1] >= tr_mod.MIN_CANDLE_PCT
           and C[i] > O[i]]
    if len(big) < 3:
        return []

    seen_c3 = set()
    trades  = []

    for k in range(2, len(big)):
        c3_i = big[k]
        if c3_i in seen_c3 or c3_i > n - CUTOFF_BARS:
            if c3_i > n - CUTOFF_BARS: break
            continue

        # 向前找 c2 / c1
        c2_i = None
        for j in range(k - 1, max(k - 15, -1), -1):
            sp = c3_i - big[j]
            if sp > tr_mod.MAX_SPACING: break
            if sp >= tr_mod.MIN_SPACING: c2_i = big[j]; break
        if c2_i is None: continue

        idx2 = big.index(c2_i)
        c1_i = None
        for j2 in range(idx2 - 1, max(idx2 - 15, -1), -1):
            sp = c2_i - big[j2]
            if sp > tr_mod.MAX_SPACING: break
            if sp >= tr_mod.MIN_SPACING: c1_i = big[j2]; break
        if c1_i is None: continue

        sp1, sp2 = c2_i - c1_i, c3_i - c2_i
        if sp1 > 0 and sp2 > 0 and max(sp1,sp2)/min(sp1,sp2) > tr_mod.MAX_SPACING_RATIO:
            continue

        # 缓慢上升
        bp = [C[i-1] for i in (c1_i, c2_i, c3_i) if i > 0 and C[i-1] > 0]
        if len(bp) < 2 or bp[0] <= 0: continue
        drift = (bp[-1] - bp[0]) / bp[0]
        if drift < 0 or drift > tr_mod.BASE_DRIFT_MAX: continue

        # 框体重叠：min(收) > max(开)
        if min(C[c1_i], C[c2_i], C[c3_i]) <= max(O[c1_i], O[c2_i], O[c3_i]):
            continue

        # 内部清洁：高点≤最高价×1.05，无涨跌幅≥8%（含一字板），停牌复牌除外
        box_top = max(H[c1_i], H[c2_i], H[c3_i]) * (1 + tr_mod.BOX_TOL)
        c123 = {c1_i, c2_i, c3_i}
        dirty = False
        resumption = False
        for ii in range(c1_i + 1, c3_i):
            if ii in c123: resumption = False; continue
            if _calendar_gap(dates, ii) > tr_mod.SUSPENSION_DAYS:
                resumption = True
            is_lim = C[ii-1] > 0 and abs(C[ii]-C[ii-1])/C[ii-1] >= tr_mod.MIN_CANDLE_PCT
            if resumption:
                if is_lim: continue
                else: resumption = False
            if H[ii] > box_top or is_lim: dirty = True; break
        if dirty: continue

        # 洗盘放量
        vol_ma_c1 = float(np.mean(V[max(0,c1_i-19):c1_i+1]))
        vol_ma_c2 = float(np.mean(V[max(0,c2_i-19):c2_i+1]))
        if tr_mod._wash_vol_ratio(V,c1_i,vol_ma_c1) < tr_mod.WASH_VOL_MULT: continue
        if tr_mod._wash_vol_ratio(V,c2_i,vol_ma_c2) < tr_mod.WASH_VOL_MULT: continue

        seen_c3.add(c3_i)
        c3_mid   = (O[c3_i] + C[c3_i]) / 2
        c3_vol   = float(V[c3_i])
        stop_lvl = min(L[c1_i], L[c2_i], L[c3_i])

        # 入场：c3后WATCH_WINDOW天内，阴线 + 收盘≤c3收盘 + 量≤c3量×65%
        c3_close = float(C[c3_i])
        entry_i = None
        for ri in range(c3_i + 1, min(c3_i + tr_mod.WATCH_WINDOW + 1, n - 1)):
            if C[ri] >= O[ri]: continue             # 必须阴线
            if C[ri] > c3_close: continue           # 收盘须≤c3收盘
            if c3_vol > 0 and V[ri] > c3_vol * tr_mod.ENTRY_VOL_RATIO: continue  # 量须≤65%
            entry_i = ri; break
        if entry_i is None or entry_i + 1 >= n - CUTOFF_BARS: continue

        entry_price = float(O[entry_i + 1])
        if entry_price <= 0: continue

        # 大盘过滤：入场日上证指数须在MA20上方
        if not _market_ok(str(dates[entry_i])):
            continue

        # 出场：起爆日（≥8%大阳）后第3天，或MAX_HOLD_TR超时
        launch_i = None
        for li in range(entry_i + 1, min(entry_i + MAX_HOLD_TR + 1, n - 1)):
            if C[li-1] <= 0: continue
            if (C[li]-C[li-1])/C[li-1] >= LAUNCH_PCT and C[li] > O[li]:
                launch_i = li; break

        exit_i    = min(launch_i + LAUNCH_HOLD, n-1) if launch_i else min(entry_i + MAX_HOLD_TR, n-1)
        exit_type = "launch+3" if launch_i else "timeout"

        for si in range(entry_i + 1, exit_i + 1):
            if C[si] < stop_lvl:
                exit_i = si; exit_type = "stop_loss"; break

        ret = (C[exit_i] / entry_price - 1) * 100
        trades.append({
            "entry_date": str(dates[entry_i+1]),
            "c3_date":    str(dates[c3_i]),
            "exit_type":  exit_type,
            "hold_days":  exit_i - entry_i,
            "ret_pct":    round(ret, 2),
            "win":        ret > 0,
        })

    return trades


# ════════════════════════════════════════════════════
# 宏景三角旗 — 每PENNANT_STEP个交易日检测一次
# ════════════════════════════════════════════════════
def backtest_pennant(df) -> list[dict]:
    n = len(df)
    if n < 200 + CUTOFF_BARS:
        return []

    C = df["close"].values.astype(float)
    O = df["open"].values.astype(float)
    V = df["volume"].values.astype(float)
    try:
        dates = df["date"].dt.strftime("%Y-%m-%d").values
    except Exception:
        dates = np.array([str(i) for i in range(n)])

    seen = set()
    trades = []

    # 只扫最近 PENNANT_HIST_BARS 根（减少重复历史计算）
    scan_start = max(200, n - PENNANT_HIST_BARS)

    for t in range(scan_start, n - CUTOFF_BARS, PENNANT_STEP):
        try:
            p = pennant_mod.detect_pennant(df.iloc[:t])
        except Exception:
            continue
        if not p.get("detected"):
            continue

        key = (p.get("pennant_start"), p.get("pennant_end"))
        if key in seen:
            continue
        seen.add(key)

        sl_list  = p.get("swing_lows") or []
        stop_lvl = float(sl_list[-1]["price"]) if sl_list else 0.0
        pen_end  = t - 1

        # ── 找起爆日：pennant结束后PENNANT_WATCH天内 ──
        entry_i = None
        for bi in range(pen_end + 1, min(pen_end + PENNANT_WATCH + 1, n - 1)):
            if C[bi-1] <= 0:
                continue
            vol_ma = float(np.mean(V[max(0, bi - 20): bi]))
            if vol_ma <= 0:
                continue
            ret = (C[bi] - C[bi-1]) / C[bi-1]
            if ret >= PENNANT_BREAKOUT_PCT and C[bi] > O[bi] and V[bi] / vol_ma >= PENNANT_VOL_MULT:
                entry_i = bi; break

        if entry_i is None or entry_i + 1 >= n - CUTOFF_BARS:
            continue

        entry_price = float(O[entry_i + 1])
        if entry_price <= 0:
            continue

        exit_i    = min(entry_i + PENNANT_HOLD, n - 1)
        exit_type = "hold80"

        if stop_lvl > 0:
            for si in range(entry_i + 1, exit_i + 1):
                if C[si] < stop_lvl:
                    exit_i    = si
                    exit_type = "stop_loss"
                    break

        ret = (C[exit_i] / entry_price - 1) * 100
        trades.append({
            "entry_date":    str(dates[entry_i + 1]),
            "pennant_start": p.get("pennant_start"),
            "pennant_end":   p.get("pennant_end"),
            "exit_type":     exit_type,
            "hold_days":     exit_i - entry_i,
            "ret_pct":       round(ret, 2),
            "win":           ret > 0,
        })

    return trades


# ════════════════════════════════════════════════════
# 统计
# ════════════════════════════════════════════════════
def summarize(trades: list[dict], label: str) -> dict:
    if not trades:
        print(f"\n{label}: 无交易")
        return {}
    rets  = np.array([t["ret_pct"] for t in trades])
    exits = {}
    for t in trades:
        exits[t["exit_type"]] = exits.get(t["exit_type"], 0) + 1
    n = len(trades)
    print(f"\n{'='*55}")
    print(f"{label}  N={n}")
    print(f"  胜率     : {np.mean(rets>0):.1%}")
    print(f"  中位收益 : {np.median(rets):+.1f}%")
    print(f"  均值收益 : {np.mean(rets):+.1f}%")
    print(f"  P25/P75  : {np.percentile(rets,25):+.1f}% / {np.percentile(rets,75):+.1f}%")
    print(f"  最大亏损 : {np.min(rets):+.1f}%   最大盈利: {np.max(rets):+.1f}%")
    print(f"  平均持仓 : {np.mean([t['hold_days'] for t in trades]):.0f}天")
    print(f"  出场分布 : {exits}")
    return {
        "n": n,
        "win_rate":    round(float(np.mean(rets > 0)), 3),
        "median_ret":  round(float(np.median(rets)), 2),
        "mean_ret":    round(float(np.mean(rets)), 2),
        "p25":         round(float(np.percentile(rets, 25)), 2),
        "p75":         round(float(np.percentile(rets, 75)), 2),
        "max_loss":    round(float(np.min(rets)), 2),
        "max_gain":    round(float(np.max(rets)), 2),
        "avg_hold":    round(float(np.mean([t["hold_days"] for t in trades])), 1),
        "exit_breakdown": exits,
    }


# ════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════
def main():
    symbols = list_tdx_symbols()
    print(f"共 {len(symbols)} 只，开始扫描...", flush=True)
    print(f"三红: 缩量≤50% c3中值, 起爆+{LAUNCH_HOLD}天出, 止损=c3低点", flush=True)
    print(f"三角旗: 大阳≥8% 量≥1.5x, 持{PENNANT_HOLD}天, 止损=下沿", flush=True)

    all_tr, all_pen = [], []
    t0 = time.time()
    errors = 0

    for i, sym in enumerate(symbols):
        if i % 500 == 0:
            print(f"  {i}/{len(symbols)}  三红={len(all_tr)} 三角旗={len(all_pen)}  {time.time()-t0:.0f}s", flush=True)
        try:
            df = load_daily(sym)
        except Exception:
            errors += 1; continue
        if df is None:
            continue

        all_tr.extend(backtest_three_red(df))
        all_pen.extend(backtest_pennant(df))

    elapsed = time.time() - t0
    print(f"\n扫描完成: {elapsed:.0f}s  errors={errors}", flush=True)
    print(f"三红={len(all_tr)}  三角旗={len(all_pen)}", flush=True)

    stats = {
        "generated":  str(date.today()),
        "n_stocks":   len(symbols),
        "elapsed_s":  round(elapsed),
        "params": {
            "three_red": {"vol_shrink": VOL_SHRINK_ENTRY, "launch_pct": LAUNCH_PCT,
                          "launch_hold": LAUNCH_HOLD, "max_hold": MAX_HOLD_TR},
            "pennant":   {"breakout_pct": PENNANT_BREAKOUT_PCT, "vol_mult": PENNANT_VOL_MULT,
                          "hold_days": PENNANT_HOLD, "watch_window": PENNANT_WATCH},
        },
        "three_red": summarize(all_tr,  "三红 V4"),
        "pennant":   summarize(all_pen, "宏景三角旗"),
    }

    out = ROOT / "q-backtest" / "logs" / f"backtest_signals_{date.today().strftime('%Y%m%d')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已保存: {out}", flush=True)


if __name__ == "__main__":
    main()
