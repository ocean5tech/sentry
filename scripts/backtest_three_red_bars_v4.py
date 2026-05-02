#!/usr/bin/env python3
"""
三根红棍 V4 回测: 起爆日后持有 + 移动止损版.

入场: 第4根大阳后缩量回调至第3根阳线中值，次日开盘买入
止损1: 第3根阳线的 LOW（不是三根最低，只有第3根）
起爆日: 买入后出现 ≥8% 大阳线（有无放量均计）
  → 出现起爆日后再持有 HOLD_AFTER_LAUNCH 天卖出
  → 止损2移至起爆日阳线中值 (open+close)/2，跌破立刻清仓

无起爆日: 最多持仓 MAX_HOLD 天强制平仓

统计维度:
  - 漏斗: 信号 → 有4根 → 有回调 → 有入场 → 有起爆
  - 有起爆: 胜率 / 持仓天数 / 收益
  - 无起爆: 亏损情况
  - 综合期望值
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
MIN_CANDLE_PCT    = 0.08      # 大阳线涨幅
MIN_SPACING       = 5
MAX_SPACING       = 25
MAX_SPACING_RATIO = 3.0
BASE_DRIFT_MAX    = 0.10      # 箱体约束

FOURTH_WINDOW     = 30        # 第3根后N天内找第4根
RETRACE_WINDOW    = 30        # 第4根后N天内找回调入场点
MIDPOINT_TOL      = 0.03      # 回调点在第3根中值±3%
VOL_SHRINK        = 0.80      # 缩量比

LAUNCH_WINDOW     = 30        # 买入后N天内找起爆日
LAUNCH_PCT        = 0.08      # 起爆日涨幅阈值（≥8%）
HOLD_AFTER_LAUNCH = 3         # 起爆日后持有天数再卖
MAX_HOLD          = 40        # 无起爆时最长持仓天数（然后按收盘平仓）


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

    big_idx  = _find_big_candles(C, O)
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

        # 关键价位
        c3_mid   = (float(O[c3[2]]) + float(C[c3[2]])) / 2   # 第3根中值
        stop1    = float(L[c3[2]])                             # 止损1: 第3根阳线LOW

        # 找第4根
        fourth_idx = None
        c3_set = set(c3)
        for fi in range(trig + 1, min(trig + FOURTH_WINDOW + 1, n)):
            if fi in c3_set:
                continue
            if C[fi-1] <= 0:
                continue
            if (C[fi] - C[fi-1]) / C[fi-1] >= MIN_CANDLE_PCT and C[fi] > O[fi]:
                fourth_idx = fi
                break

        if fourth_idx is None:
            continue

        # 找缩量回调入场点
        vol_ma20 = float(np.mean(V[max(0, trig - 19): trig + 1]))
        entry_idx   = None
        entry_price = None

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
            if ep <= stop1:          # 入场价已在止损下方，跳过
                continue
            entry_idx   = ei
            entry_price = ep
            break

        if entry_idx is None:
            sig = {
                "trig_date": str(pd.Timestamp(dt[trig]).date()),
                "has_fourth": True,
                "has_retrace": False,
            }
            signals.append(sig)
            continue

        ep   = entry_price
        stop = stop1      # 当前止损

        # 核心逻辑: 日内跟踪
        outcome       = "timeout"
        exit_price    = None
        hold_days     = 0
        launch_idx    = None
        launch_mid    = None

        for day in range(entry_idx, min(entry_idx + MAX_HOLD + 1, n)):
            d_h   = float(H[day])
            d_l   = float(L[day])
            d_o   = float(O[day])
            d_c   = float(C[day])
            hold_days = day - entry_idx

            # 优先检查止损
            if d_l <= stop:
                outcome   = "stop"
                exit_price = stop
                break

            # 检查是否出现起爆日（先于止损检查后执行）
            if launch_idx is None and hold_days > 0:    # 不算入场当天
                if d_h > 0 and C[day-1] > 0:
                    day_ret = (d_c - C[day-1]) / C[day-1]
                    if day_ret >= LAUNCH_PCT and d_c > d_o:
                        launch_idx  = day
                        launch_mid  = (d_o + d_c) / 2   # 起爆日中值 → 新止损
                        stop        = launch_mid          # 移动止损
                        continue                          # 继续持有

            # 起爆日后 HOLD_AFTER_LAUNCH 天
            if launch_idx is not None:
                days_since_launch = day - launch_idx
                if days_since_launch >= HOLD_AFTER_LAUNCH:
                    outcome    = "launch_exit"
                    exit_price = d_c   # 收盘卖出
                    break

        # 超时处理
        if outcome == "timeout":
            ei_t = min(entry_idx + MAX_HOLD, n - 1)
            exit_price = float(C[ei_t])
            hold_days  = min(MAX_HOLD, n - 1 - entry_idx)

        ret_pct = round((exit_price - ep) / ep * 100, 1) if exit_price and ep > 0 else None

        sig = {
            "trig_date":   str(pd.Timestamp(dt[trig]).date()),
            "c3_mid":      round(c3_mid, 2),
            "stop1":       round(stop1, 2),
            "stop1_pct":   round((stop1 - ep) / ep * 100, 1) if ep > 0 else None,
            "fourth_date": str(pd.Timestamp(dt[fourth_idx]).date()),
            "entry_date":  str(pd.Timestamp(dt[entry_idx]).date()),
            "entry_price": round(ep, 2),
            "has_fourth":  True,
            "has_retrace": True,
            "launch_date": str(pd.Timestamp(dt[launch_idx]).date()) if launch_idx else None,
            "launch_mid":  round(launch_mid, 2) if launch_mid else None,
            "outcome":     outcome,
            "hold_days":   hold_days,
            "exit_price":  round(exit_price, 2) if exit_price else None,
            "return_pct":  ret_pct,
        }
        signals.append(sig)

    return signals


def stats(vals, cap=300.0):
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

    print(f"[3rb_v4] 扫描 {total} 只股票...", flush=True)
    print(f"  止损1=第3根LOW  起爆≥{LAUNCH_PCT*100:.0f}%  起爆后持{HOLD_AFTER_LAUNCH}天  最长{MAX_HOLD}天", flush=True)

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
    with_entry  = [s for s in all_sigs if s.get("has_retrace") and "outcome" in s]
    launched    = [s for s in with_entry if s.get("launch_date")]
    not_launch  = [s for s in with_entry if not s.get("launch_date")]
    launch_exit = [s for s in launched   if s.get("outcome") == "launch_exit"]
    launch_stop = [s for s in launched   if s.get("outcome") == "stop"]
    no_launch_stop = [s for s in not_launch if s.get("outcome") == "stop"]

    print(f"\n── 漏斗分析 ──")
    print(f"  3根箱体信号:         {len(all_sigs)}")
    print(f"  实际入场:            {len(with_entry)}")
    print(f"  ⚡ 有起爆日:         {len(launched)}  ({len(launched)/max(len(with_entry),1)*100:.1f}%)")
    print(f"  ─── 起爆后正常退出:  {len(launch_exit)}")
    print(f"  ─── 起爆后止损(跌破起爆中值): {len(launch_stop)}")
    print(f"  😴 无起爆日:         {len(not_launch)}  ({len(not_launch)/max(len(with_entry),1)*100:.1f}%)")
    print(f"  ─── 无起爆后止损(跌破第3根LOW): {len(no_launch_stop)}")

    # ── 有起爆日 ──────────────────────────────────────────────────────────────
    if launched:
        l_rets = [s["return_pct"] for s in launched if s.get("return_pct") is not None]
        l_days = [s["hold_days"]  for s in launched if s.get("hold_days")  is not None]
        st = stats(l_rets)
        print(f"\n── ⚡ 有起爆日 ({len(launched)} 次) ──")
        print(f"  持仓天数: 均={np.mean(l_days):.1f}天  中位={np.median(l_days):.1f}天")
        print(f"  综合收益: 胜率={st['win_rate']:.1f}%  均值={st['mean']:+.2f}%  "
              f"中位数={st['median']:+.2f}%  P25={st['p25']:+.2f}%  P75={st['p75']:+.2f}%  P90={st['p90']:+.2f}%")

        # 起爆后正常退出
        if launch_exit:
            e_rets = [s["return_pct"] for s in launch_exit if s.get("return_pct") is not None]
            st_e = stats(e_rets)
            print(f"\n  正常退出 ({len(launch_exit)} 次): 胜率={st_e['win_rate']:.1f}%  "
                  f"均值={st_e['mean']:+.2f}%  中位数={st_e['median']:+.2f}%  P90={st_e['p90']:+.2f}%")

        # 起爆后止损
        if launch_stop:
            ls_rets = [s["return_pct"] for s in launch_stop if s.get("return_pct") is not None]
            print(f"  止损退出 ({len(launch_stop)} 次): 均值={np.mean(ls_rets):+.2f}%  "
                  f"中位数={np.median(ls_rets):+.2f}%")

    # ── 无起爆日 ──────────────────────────────────────────────────────────────
    if not_launch:
        nl_rets = [s["return_pct"] for s in not_launch if s.get("return_pct") is not None]
        st_nl = stats(nl_rets)
        print(f"\n── 😴 无起爆日 ({len(not_launch)} 次) ──")
        print(f"  胜率={st_nl['win_rate']:.1f}%  均值={st_nl['mean']:+.2f}%  "
              f"中位数={st_nl['median']:+.2f}%  P25={st_nl['p25']:+.2f}%  P75={st_nl['p75']:+.2f}%")
        # 止损幅度
        stop1_pcts = [s["stop1_pct"] for s in not_launch if s.get("stop1_pct") is not None]
        if stop1_pcts:
            print(f"  止损位距入场: 均值={np.mean(stop1_pcts):+.2f}%  中位数={np.median(stop1_pcts):+.2f}%")

    # ── 综合 ──────────────────────────────────────────────────────────────────
    all_rets = [s["return_pct"] for s in with_entry if s.get("return_pct") is not None]
    if all_rets:
        st_all = stats(all_rets)
        print(f"\n── 综合 (全部入场 n={st_all['n']}) ──")
        print(f"  胜率={st_all['win_rate']:.1f}%  均值={st_all['mean']:+.2f}%  "
              f"中位数={st_all['median']:+.2f}%  P75={st_all['p75']:+.2f}%  P90={st_all['p90']:+.2f}%")

    # ── 科创+创业 ──────────────────────────────────────────────────────────────
    star = [s for s in with_entry if s["code"].startswith(("688","300","301"))]
    star_launch = [s for s in star if s.get("launch_date")]
    if star:
        star_rets = [s["return_pct"] for s in star if s.get("return_pct") is not None]
        st_s = stats(star_rets)
        star_l_rets = [s["return_pct"] for s in star_launch if s.get("return_pct") is not None]
        st_sl = stats(star_l_rets) if star_l_rets else {}
        print(f"\n── 科创+创业板 (入场={len(star)}, 有起爆={len(star_launch)} {len(star_launch)/max(len(star),1)*100:.1f}%) ──")
        print(f"  综合: 胜率={st_s['win_rate']:.1f}%  均值={st_s['mean']:+.2f}%  中位数={st_s['median']:+.2f}%")
        if st_sl:
            print(f"  有起爆: 胜率={st_sl['win_rate']:.1f}%  均值={st_sl['mean']:+.2f}%  中位数={st_sl['median']:+.2f}%")

    # 期望值对比
    if launched and not_launch:
        l_mean  = float(np.mean([s["return_pct"] for s in launched   if s.get("return_pct") is not None] or [0]))
        nl_mean = float(np.mean([s["return_pct"] for s in not_launch if s.get("return_pct") is not None] or [0]))
        l_rate  = len(launched)   / max(len(with_entry), 1)
        nl_rate = len(not_launch) / max(len(with_entry), 1)
        ev = l_rate * l_mean + nl_rate * nl_mean
        print(f"\n  期望值: {l_rate:.1%}×({l_mean:+.2f}%) + {nl_rate:.1%}×({nl_mean:+.2f}%) ≈ {ev:+.2f}%")

    out = ROOT / "logs" / "backtest_three_red_bars_v4.jsonl"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for s in all_sigs:
            f.write(json.dumps(s, ensure_ascii=False, default=str) + "\n")
    print(f"\n[3rb_v4] → {out}", flush=True)


if __name__ == "__main__":
    main()
