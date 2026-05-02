#!/usr/bin/env python3
"""
三根红棍 V3: 价格止损版.

改进: 止损从"等N天"改为"跌破三根阳线区间下沿立即清仓"
  止损价 = min(三根阳线的最低价)
  目标价 = 第4根阳线的最高价

流程:
  1. 3根箱体大阳 (基准价偏移≤10%)
  2. 第4根大阳出现（在触发后30天内）
  3. 第4根后等缩量回调至第3根阳线中值附近（±3%，量比≤0.8）→ 买入次日开盘
  4. 止损: 日内最低价跌破 stop = min(low[c1], low[c2], low[c3]) → 按 stop 价清仓
  5. 目标: 日内最高价达到 tgt = high[fourth] → 按 tgt 价清仓
  6. 最长持仓 MAX_HOLD 天强制平仓

统计:
  - 漏斗: 信号 → 有第4根 → 有回调 → 实际入场 → 达目标 / 止损 / 超时
  - 达目标: 胜率, 平均持仓天数, 收益分布
  - 止损: 亏损分布
  - 超时: 按收盘价计算损益
"""
import sys, json
import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.tdx_loader import list_tdx_symbols
from core.data_loader import load_daily
from core.stock_names import get_names

MIN_CANDLE_PCT    = 0.08
MIN_SPACING       = 5
MAX_SPACING       = 25
MAX_SPACING_RATIO = 3.0
BASE_DRIFT_MAX    = 0.10
LOOKBACK          = 100

FOURTH_WINDOW     = 30
RETRACE_WINDOW    = 30
MIDPOINT_TOL      = 0.03
VOL_SHRINK        = 0.80
MAX_HOLD          = 20    # 最长持仓天数，超时按收盘平仓


def _find_big_candles(C, O):
    return [i for i in range(1, len(C))
            if C[i-1] > 0
            and (C[i] - C[i-1]) / C[i-1] >= MIN_CANDLE_PCT
            and C[i] > O[i]]


def find_signals(df: pd.DataFrame) -> list[dict]:
    if df is None or len(df) < 60:
        return []

    C  = df["close"].values.astype(float)
    O  = df["open"].values.astype(float)
    H  = df["high"].values.astype(float)
    L  = df["low"].values.astype(float)
    V  = df["volume"].values.astype(float)
    dt = df["date"].values
    n  = len(df)

    big_idx = _find_big_candles(C, O)
    if len(big_idx) < 3:
        return []

    signals  = []
    seen_trig = set()

    for end in range(2, len(big_idx)):
        chain = [big_idx[end]]
        for j in range(end - 1, -1, -1):
            sp = chain[-1] - big_idx[j]
            if sp > MAX_SPACING:
                break
            if sp >= MIN_SPACING:
                chain.append(big_idx[j])
        chain.reverse()
        if len(chain) < 3:
            continue
        spacings = [chain[k+1] - chain[k] for k in range(len(chain) - 1)]
        if max(spacings) / min(spacings) > MAX_SPACING_RATIO:
            continue

        c3 = chain[-3:]
        trig = c3[-1]
        if trig in seen_trig:
            continue

        bp = [C[i-1] for i in c3 if i > 0 and C[i-1] > 0]
        if len(bp) >= 2 and bp[0] > 0:
            if abs(bp[-1] - bp[0]) / bp[0] > BASE_DRIFT_MAX:
                continue

        seen_trig.add(trig)

        # 三根阳线的关键价位
        c3_mid  = (float(O[c3[2]]) + float(C[c3[2]])) / 2   # 第3根中值
        stop    = float(min(L[c3[0]], L[c3[1]], L[c3[2]]))    # 三根最低价 = 止损价

        # 找第4根
        fourth_idx  = None
        fourth_high = None
        c3_set = set(c3)
        for fi in range(trig + 1, min(trig + FOURTH_WINDOW + 1, n)):
            if fi in c3_set:
                continue
            if C[fi-1] <= 0:
                continue
            if (C[fi] - C[fi-1]) / C[fi-1] >= MIN_CANDLE_PCT and C[fi] > O[fi]:
                fourth_idx  = fi
                fourth_high = float(H[fi])
                break

        if fourth_idx is None:
            continue

        # 第4根后找缩量回调点
        vol_ma20 = float(np.mean(V[max(0, trig - 19): trig + 1]))
        retrace_entry_idx   = None
        retrace_entry_price = None

        for ri in range(fourth_idx + 1, min(fourth_idx + RETRACE_WINDOW + 1, n)):
            if C[ri] > c3_mid * (1 + MIDPOINT_TOL):
                continue
            vol_ok = vol_ma20 > 0 and V[ri] / vol_ma20 <= VOL_SHRINK
            if not vol_ok:
                continue
            ei = ri + 1
            if ei >= n:
                break
            ep = float(O[ei])
            if ep <= 0:
                continue
            retrace_entry_idx   = ei
            retrace_entry_price = ep
            break

        sig = {
            "trig_date":   str(pd.Timestamp(dt[trig]).date()),
            "c3_mid":      round(c3_mid, 2),
            "stop_price":  round(stop, 2),
            "fourth_date": str(pd.Timestamp(dt[fourth_idx]).date()),
            "fourth_high": round(fourth_high, 2),
            "has_retrace": retrace_entry_idx is not None,
        }

        if retrace_entry_idx is None:
            signals.append(sig)
            continue

        ep  = retrace_entry_price
        tgt = fourth_high

        if tgt <= ep or ep <= stop:
            # 入场价已超目标 or 入场价已在止损下方 → 跳过
            sig["skip_reason"] = "invalid_prices"
            signals.append(sig)
            continue

        sig["entry_date"]    = str(pd.Timestamp(dt[retrace_entry_idx]).date())
        sig["entry_price"]   = round(ep, 2)
        sig["target_pct"]    = round((tgt - ep) / ep * 100, 1)
        sig["stop_pct"]      = round((stop - ep) / ep * 100, 1)

        # 日内跟踪（使用 high/low 判断触发顺序：同一天若两者都触发，先假设先触碰较近的）
        outcome  = "timeout"
        exit_px  = None
        hold_days = 0

        for day in range(retrace_entry_idx, min(retrace_entry_idx + MAX_HOLD + 1, n)):
            d_high = float(H[day])
            d_low  = float(L[day])
            hold_days = day - retrace_entry_idx

            hit_tgt  = d_high >= tgt
            hit_stop = d_low  <= stop

            if hit_tgt and hit_stop:
                # 同天双触发：判断谁先到——用开盘价相对位置估计
                d_open = float(O[day])
                if abs(d_open - stop) <= abs(d_open - tgt):
                    outcome = "stop"
                    exit_px = stop
                else:
                    outcome = "win"
                    exit_px = tgt
                break
            elif hit_tgt:
                outcome = "win"
                exit_px = tgt
                break
            elif hit_stop:
                outcome = "stop"
                exit_px = stop
                break

        if outcome == "timeout":
            ei_t = retrace_entry_idx + MAX_HOLD
            exit_px = float(C[min(ei_t, n-1)])
            hold_days = MAX_HOLD

        sig["outcome"]    = outcome
        sig["hold_days"]  = hold_days
        sig["exit_price"] = round(exit_px, 2) if exit_px else None
        sig["return_pct"] = round((exit_px - ep) / ep * 100, 1) if exit_px and ep > 0 else None

        signals.append(sig)

    return signals


def stats(vals, cap=500.0):
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

    print(f"[3rb_v3] 扫描 {total} 只股票 (价格止损版)...", flush=True)
    print(f"  止损: 跌破三根最低价  目标: 第4根高点  最长: {MAX_HOLD}天", flush=True)

    for i, code in enumerate(syms):
        if i % 1000 == 0:
            print(f"  {i}/{total}  ({len(all_sigs)} 样本)", flush=True)
        df   = load_daily(code)
        sigs = find_signals(df)
        name = names.get(code, "")
        for s in sigs:
            s["code"] = code
            s["name"] = name
        all_sigs.extend(sigs)

    # 漏斗
    with_fourth  = [s for s in all_sigs if s.get("fourth_high")]
    with_retrace = [s for s in all_sigs if s.get("has_retrace")]
    with_entry   = [s for s in all_sigs if s.get("entry_price") is not None
                    and s.get("skip_reason") is None and "outcome" in s]
    wins    = [s for s in with_entry if s.get("outcome") == "win"]
    stops   = [s for s in with_entry if s.get("outcome") == "stop"]
    timeout = [s for s in with_entry if s.get("outcome") == "timeout"]

    print(f"\n── 漏斗分析 ──")
    print(f"  3根箱体信号:       {len(all_sigs)}")
    print(f"  其中有第4根大阳:   {len(with_fourth)} ({len(with_fourth)/max(len(all_sigs),1)*100:.1f}%)")
    print(f"  其中有缩量回调:    {len(with_retrace)} ({len(with_retrace)/max(len(with_fourth),1)*100:.1f}% of 有第4根)")
    print(f"  实际入场:          {len(with_entry)}")
    print(f"  ✅ 达到目标:       {len(wins)} ({len(wins)/max(len(with_entry),1)*100:.1f}%)")
    print(f"  ❌ 止损:           {len(stops)} ({len(stops)/max(len(with_entry),1)*100:.1f}%)")
    print(f"  ⏰ 超时{MAX_HOLD}天:       {len(timeout)} ({len(timeout)/max(len(with_entry),1)*100:.1f}%)")

    # 达到目标
    if wins:
        win_rets  = [s["return_pct"] for s in wins if s.get("return_pct")]
        win_days  = [s["hold_days"]  for s in wins if s.get("hold_days") is not None]
        tgt_pcts  = [s["target_pct"] for s in wins if s.get("target_pct")]
        print(f"\n── ✅ 达目标 ({len(wins)} 次) ──")
        print(f"  平均持仓天数: {np.mean(win_days):.1f}天  中位数: {np.median(win_days):.1f}天")
        if win_rets:
            st = stats(win_rets)
            print(f"  收益: 均值={st['mean']:+.2f}%  中位数={st['median']:+.2f}%  "
                  f"P25={st['p25']:+.2f}%  P75={st['p75']:+.2f}%  P90={st['p90']:+.2f}%")
        if tgt_pcts:
            print(f"  目标收益率分布: 中位数={np.median(tgt_pcts):+.2f}%  "
                  f"P25={np.percentile(tgt_pcts,25):+.2f}%  P75={np.percentile(tgt_pcts,75):+.2f}%")

    # 止损
    if stops:
        stop_rets = [s["return_pct"] for s in stops if s.get("return_pct")]
        stop_days = [s["hold_days"]  for s in stops if s.get("hold_days") is not None]
        stop_pcts = [s["stop_pct"]   for s in stops if s.get("stop_pct")]
        print(f"\n── ❌ 止损 ({len(stops)} 次) ──")
        print(f"  平均持仓天数: {np.mean(stop_days):.1f}天  中位数: {np.median(stop_days):.1f}天")
        if stop_rets:
            print(f"  亏损: 均值={np.mean(stop_rets):+.2f}%  中位数={np.median(stop_rets):+.2f}%")
        if stop_pcts:
            print(f"  止损幅度: 均值={np.mean(stop_pcts):+.2f}%  中位数={np.median(stop_pcts):+.2f}%")

    # 超时
    if timeout:
        to_rets = [s["return_pct"] for s in timeout if s.get("return_pct")]
        print(f"\n── ⏰ 超时{MAX_HOLD}天平仓 ({len(timeout)} 次) ──")
        if to_rets:
            st = stats(to_rets)
            print(f"  收益: 均值={st['mean']:+.2f}%  中位数={st['median']:+.2f}%  胜率={st['win_rate']:.1f}%")

    # 综合期望值
    all_rets = [s["return_pct"] for s in with_entry if s.get("return_pct") is not None]
    if all_rets:
        st_all = stats(all_rets)
        print(f"\n── 综合 (全部入场, n={st_all['n']}) ──")
        print(f"  胜率={st_all['win_rate']:.1f}%  均值={st_all['mean']:+.2f}%  "
              f"中位数={st_all['median']:+.2f}%  P25={st_all['p25']:+.2f}%  P75={st_all['p75']:+.2f}%")

        # 期望值估算
        w_r = np.mean([s["return_pct"] for s in wins  if s.get("return_pct")] or [0])
        l_r = np.mean([s["return_pct"] for s in stops if s.get("return_pct")] or [0])
        wr  = len(wins) / max(len(with_entry), 1)
        lr  = len(stops) / max(len(with_entry), 1)
        ev  = wr * w_r + lr * l_r
        print(f"\n  期望值估算: {wr:.2%}×({w_r:+.2f}%) + {lr:.2%}×({l_r:+.2f}%) ≈ {ev:+.2f}%")

    # 科创+创业
    star_entry = [s for s in with_entry if s["code"].startswith(("688","300","301"))]
    star_wins  = [s for s in star_entry if s.get("outcome") == "win"]
    star_rets  = [s["return_pct"] for s in star_entry if s.get("return_pct") is not None]
    if star_entry:
        st_s = stats(star_rets)
        print(f"\n── 科创+创业板 (入场={len(star_entry)}, "
              f"达目标={len(star_wins)} {len(star_wins)/len(star_entry)*100:.1f}%) ──")
        if st_s:
            print(f"  均值={st_s['mean']:+.2f}%  中位数={st_s['median']:+.2f}%  "
                  f"胜率={st_s['win_rate']:.1f}%  P75={st_s['p75']:+.2f}%")

    out = ROOT / "logs" / "backtest_three_red_bars_v3.jsonl"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for s in all_sigs:
            f.write(json.dumps(s, ensure_ascii=False, default=str) + "\n")
    print(f"\n[3rb_v3] → {out}", flush=True)


if __name__ == "__main__":
    main()
