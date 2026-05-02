#!/usr/bin/env python3
"""
三根红棍 V2 回测: 缩量回调至第3根中值买入，目标第4根高点.

流程:
  1. 找到3根箱体大阳 (箱体约束: 基准价偏移≤10%)
  2. 4根大阳在触发后30天内出现
  3. 4根大阳后，等待价格缩量回调至第3根阳线中值附近
       * 收盘价 ≤ 第3根中值 × (1 + MIDPOINT_TOL)
       * 且当日成交量 ≤ 近20日均量 × VOL_SHRINK
  4. 买入: 符合条件当日次日开盘价
  5. 卖出目标: 第4根阳线的最高点 (HIGH)
  6. 统计:
       - 回调命中率 (有多少触发了回调入场点)
       - 胜率 (成功到达目标价)
       - 达到目标价的平均持仓天数
       - 达到目标价的平均收益率
       - 未达到目标价时在第N天的收益（假设N天止损离场）
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

# ── 策略参数 ──────────────────────────────────────────────────────────────────
MIN_CANDLE_PCT   = 0.08     # 大阳线涨幅阈值
MIN_SPACING      = 5        # 两根大阳最小间距（交易日）
MAX_SPACING      = 25       # 最大间距
MAX_SPACING_RATIO= 3.0      # 均匀度约束
BASE_DRIFT_MAX   = 0.10     # 箱体约束：基准价偏移≤10%
LOOKBACK         = 100      # 扫描回溯

FOURTH_WINDOW    = 30       # 第3根后N天内找第4根
RETRACE_WINDOW   = 30       # 第4根后N天内找回调点
MIDPOINT_TOL     = 0.03     # 回调点在第3根中值3%范围内
VOL_SHRINK       = 0.80     # 成交量须 < 近20日均 × 此值
STOP_DAYS        = [5, 10, 20]  # 未达目标时强制止损的天数


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

        # 箱体约束
        bp = [C[i-1] for i in c3 if i > 0 and C[i-1] > 0]
        if len(bp) >= 2 and bp[0] > 0:
            if abs(bp[-1] - bp[0]) / bp[0] > BASE_DRIFT_MAX:
                continue

        seen_trig.add(trig)

        # 第3根阳线中值（body midpoint）
        c3_idx   = c3[2]
        c3_mid   = (float(O[c3_idx]) + float(C[c3_idx])) / 2

        # 找第4根大阳
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
            continue  # 要求必须有第4根

        # 第4根后找缩量回调点
        vol_ma20 = float(np.mean(V[max(0, trig - 19): trig + 1]))
        retrace_entry_idx   = None
        retrace_entry_price = None

        for ri in range(fourth_idx + 1, min(fourth_idx + RETRACE_WINDOW + 1, n)):
            if C[ri] > c3_mid * (1 + MIDPOINT_TOL):
                continue      # 价格还未回调到位
            vol_ok = vol_ma20 > 0 and V[ri] / vol_ma20 <= VOL_SHRINK
            if not vol_ok:
                continue
            # 找到回调入场点
            ei = ri + 1
            if ei >= n:
                break
            ep = float(O[ei])
            if ep <= 0:
                continue
            retrace_entry_idx   = ei
            retrace_entry_price = ep
            break

        # 构建信号
        sig = {
            "trig_date":      str(pd.Timestamp(dt[trig]).date()),
            "c1_date":        str(pd.Timestamp(dt[c3[0]]).date()),
            "c2_date":        str(pd.Timestamp(dt[c3[1]]).date()),
            "c3_date":        str(pd.Timestamp(dt[c3[2]]).date()),
            "c3_mid":         round(c3_mid, 2),
            "fourth_date":    str(pd.Timestamp(dt[fourth_idx]).date()),
            "fourth_high":    round(fourth_high, 2),
            "has_retrace":    retrace_entry_idx is not None,
        }

        if retrace_entry_idx is not None:
            ep   = retrace_entry_price
            tgt  = fourth_high
            if tgt <= 0 or ep <= 0:
                signals.append(sig)
                continue

            tgt_ret = round((tgt - ep) / ep * 100, 1)
            sig["entry_date"]  = str(pd.Timestamp(dt[retrace_entry_idx]).date())
            sig["entry_price"] = round(ep, 2)
            sig["target_pct"]  = tgt_ret

            # 逐日跟踪: 何时达到目标 or 强制止损
            hit_target = False
            hit_day    = None
            for day in range(retrace_entry_idx, min(retrace_entry_idx + max(STOP_DAYS) + 1, n)):
                if float(H[day]) >= tgt:
                    hit_target = True
                    hit_day    = day - retrace_entry_idx
                    break

            sig["hit_target"]      = hit_target
            sig["days_to_target"]  = hit_day

            # 各止损天的实际收益
            for sd in STOP_DAYS:
                ei2 = retrace_entry_idx + sd
                if ei2 < n:
                    sig[f"ret_{sd}d"] = round((float(C[ei2]) - ep) / ep * 100, 1)

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

    print(f"[3rb_v2] 扫描 {total} 只股票...", flush=True)
    print(f"  入场: 第4根后缩量回调至第3根中值±{MIDPOINT_TOL*100:.0f}%  量比≤{VOL_SHRINK}", flush=True)
    print(f"  目标: 第4根高点", flush=True)

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

    print(f"\n[3rb_v2] 完成. 共 {len(all_sigs)} 个3根信号 / "
          f"{len(set(s['code'] for s in all_sigs))} 只股票", flush=True)

    # 分析
    with_fourth  = [s for s in all_sigs if s.get("fourth_high")]
    with_retrace = [s for s in all_sigs if s.get("has_retrace")]
    with_entry   = [s for s in all_sigs if s.get("entry_price") is not None]
    hit          = [s for s in with_entry if s.get("hit_target")]
    miss         = [s for s in with_entry if not s.get("hit_target")]

    print(f"\n── 漏斗分析 ──")
    print(f"  3根箱体信号:        {len(all_sigs)}")
    print(f"  其中有第4根大阳:    {len(with_fourth)} ({len(with_fourth)/max(len(all_sigs),1)*100:.1f}%)")
    print(f"  其中有缩量回调点:   {len(with_retrace)} ({len(with_retrace)/max(len(with_fourth),1)*100:.1f}% of 有第4根)")
    print(f"  实际入场:           {len(with_entry)}")
    print(f"  达到目标(第4根高):  {len(hit)} ({len(hit)/max(len(with_entry),1)*100:.1f}%)")

    if hit:
        dtgt = [s["days_to_target"] for s in hit if s.get("days_to_target") is not None]
        rets = [s["target_pct"]     for s in hit if s.get("target_pct") is not None]
        print(f"\n── 达到目标 ({len(hit)} 次) ──")
        print(f"  平均持仓天数:  {np.mean(dtgt):.1f}天  中位数: {np.median(dtgt):.1f}天")
        print(f"  平均收益:      {np.mean(rets):+.2f}%  中位数: {np.median(rets):+.2f}%")
        print(f"  P25={np.percentile(rets,25):+.2f}%  P75={np.percentile(rets,75):+.2f}%  P90={np.percentile(rets,90):+.2f}%")

    print(f"\n── 未达目标 ({len(miss)} 次) — 按强制止损天统计 ──")
    for sd in STOP_DAYS:
        vals = [s.get(f"ret_{sd}d") for s in miss if s.get(f"ret_{sd}d") is not None]
        if vals:
            st = stats(vals)
            print(f"  {sd:2d}天止损: n={st['n']:4d} | 胜率={st['win_rate']:5.1f}% | "
                  f"均值={st['mean']:+6.2f}% | 中位数={st['median']:+6.2f}%")

    print(f"\n── 入场后各天收益（全部含达到/未达到目标）──")
    for sd in STOP_DAYS:
        vals = [s.get(f"ret_{sd}d") for s in with_entry if s.get(f"ret_{sd}d") is not None]
        if vals:
            st = stats(vals)
            print(f"  {sd:2d}天: n={st['n']:4d} | 胜率={st['win_rate']:5.1f}% | "
                  f"均值={st['mean']:+6.2f}% | 中位数={st['median']:+6.2f}% | "
                  f"P25={st['p25']:+6.2f}% P75={st['p75']:+6.2f}%")

    # 科创+创业
    star_entry = [s for s in with_entry if s["code"].startswith(("688","300","301"))]
    star_hit   = [s for s in star_entry if s.get("hit_target")]
    if star_entry:
        print(f"\n── 科创+创业板 (入场={len(star_entry)}, 达目标={len(star_hit)} "
              f"{len(star_hit)/len(star_entry)*100:.1f}%) ──")
        for sd in STOP_DAYS:
            vals = [s.get(f"ret_{sd}d") for s in star_entry if s.get(f"ret_{sd}d") is not None]
            if vals:
                st = stats(vals)
                print(f"  {sd:2d}天: 胜率={st['win_rate']:5.1f}% | 均值={st['mean']:+6.2f}% | "
                      f"中位数={st['median']:+6.2f}%")

    # 保存
    out = ROOT / "logs" / "backtest_three_red_bars_v2.jsonl"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for s in all_sigs:
            f.write(json.dumps(s, ensure_ascii=False, default=str) + "\n")
    print(f"\n[3rb_v2] → {out}", flush=True)


if __name__ == "__main__":
    main()
