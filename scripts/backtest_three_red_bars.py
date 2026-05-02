#!/usr/bin/env python3
"""
三根红棍策略历史回测.

扫描全市场历史数据，找出所有触发"第3根大阳"的时间点，
统计第3根出现后（以及缩量回调后买入）持仓 5/10/20 天的收益率.

入场假设:
  - 入场A: 第3根大阳当日收盘后次日开盘买入（简化）
  - 入场B: 第3根大阳后缩量回调买入 (量比 < 0.8x, 在3根后的5个交易日内)
  - 加仓: 第4根大阳出现时同价位加仓 (若出现)

输出: 每个样本的触发日期、实际收益率等
"""

import sys
import json
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import date

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.tdx_loader import list_tdx_symbols
from core.data_loader import load_daily
from core.stock_names import get_names

# ─── 策略参数（与 three_red_bars.py 一致）─────────────────────────────────
MIN_CANDLE_PCT = 0.08
MIN_SPACING = 5
MAX_SPACING = 25
MAX_SPACING_RATIO = 3.0
LOOKBACK = 100
RECENT_TRIGGER = 30  # 第3根必须在最近N个交易日内

# ─── 回测参数 ──────────────────────────────────────────────────────────────
HOLD_DAYS = [5, 10, 20]
SHRINK_WIN = 5        # 第3根后N天内找缩量回调
SHRINK_RATIO = 0.85   # 量比 < 此值视为缩量
MIN_IPO_AGE = 250     # 上市满250日才纳入
# 时间范围: 只统计有足够后续数据的样本 (最后一根后还有20+天)
MIN_FUTURE_BARS = 25

# ─── 板块过滤: 科创+创业 ──────────────────────────────────────────────────
def is_target_board(code: str) -> bool:
    return code.startswith(("688", "300", "301"))


def find_big_candles(C: np.ndarray, O: np.ndarray) -> list[int]:
    """返回所有大阳线的 bar 索引."""
    out = []
    for i in range(1, len(C)):
        if C[i - 1] <= 0:
            continue
        ret = (C[i] - C[i - 1]) / C[i - 1]
        if ret >= MIN_CANDLE_PCT and C[i] > O[i]:
            out.append(i)
    return out


def find_signals(df: pd.DataFrame) -> list[dict]:
    """
    在 df 中找出所有"第3根大阳"触发点.
    返回样本列表，每个包含触发 bar index 及后续信息.
    """
    if df is None or len(df) < 60:
        return []

    C = df["close"].values.astype(float)
    O = df["open"].values.astype(float)
    V = df["volume"].values.astype(float)
    dates = df["date"].values
    n = len(df)

    big_idx = find_big_candles(C, O)
    if len(big_idx) < 3:
        return []

    signals = []

    # 滑动窗口找所有满足条件的"第3根"位置
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
        spacings = [chain[k + 1] - chain[k] for k in range(len(chain) - 1)]
        if max(spacings) / min(spacings) > MAX_SPACING_RATIO:
            continue

        # 取最近3根
        c3 = chain[-3:]
        trigger_idx = c3[-1]

        # 触发点后必须有足够的 future bars
        if n - 1 - trigger_idx < MIN_FUTURE_BARS:
            continue

        # 避免重复样本: 同一个 trigger_idx 只记录一次
        if signals and signals[-1]["trigger_idx"] == trigger_idx:
            continue

        # ── 箱体约束: 首根与末根基准价(前日收盘)偏移 ≤10% ─────────────
        base_prices = [C[i - 1] for i in c3 if i > 0 and C[i - 1] > 0]
        if len(base_prices) >= 2:
            drift = abs(base_prices[-1] - base_prices[0]) / base_prices[0]
            if drift > 0.10:
                continue  # 趋势行情，不是箱体震荡

        # 第3根大阳的涨幅
        rets_3 = [(C[i] - C[i - 1]) / C[i - 1] for i in c3]

        # ── 入场A: 触发后次日开盘 ──────────────────────────────────────
        entry_idx = trigger_idx + 1
        if entry_idx >= n:
            continue
        entry_price = O[entry_idx]
        if entry_price <= 0:
            continue

        # ── 找缩量回调入场B ─────────────────────────────────────────────
        shrink_entry_idx = None
        shrink_entry_price = None
        vol_ma5 = np.mean(V[max(0, trigger_idx - 5):trigger_idx + 1])
        for k in range(entry_idx, min(entry_idx + SHRINK_WIN, n)):
            if vol_ma5 > 0 and V[k] / vol_ma5 < SHRINK_RATIO and C[k] < C[trigger_idx]:
                shrink_entry_idx = k + 1 if k + 1 < n else k
                shrink_entry_price = O[shrink_entry_idx] if shrink_entry_idx < n else C[k]
                break

        # ── 找第4根大阳（在触发后30天内，不是前3根之一）────────────────
        fourth_idx = None
        fourth_ret = None
        chain_set = set(c3)
        for fj in range(trigger_idx + 1, min(trigger_idx + 31, n)):
            if fj in chain_set:
                continue
            if C[fj - 1] <= 0:
                continue
            ret = (C[fj] - C[fj - 1]) / C[fj - 1]
            if ret >= MIN_CANDLE_PCT and C[fj] > O[fj]:
                fourth_idx = fj
                fourth_ret = round(ret * 100, 1)
                break

        # ── 计算持仓收益 ──────────────────────────────────────────────
        def hold_returns(ep: float, ei: int, label: str) -> dict:
            if ep <= 0 or ei >= n:
                return {}
            row = {"entry_price": round(ep, 2), "entry_label": label}
            for h in HOLD_DAYS:
                exit_idx = ei + h
                if exit_idx < n:
                    row[f"ret_{h}d"] = round((C[exit_idx] - ep) / ep * 100, 1)
                else:
                    row[f"ret_{h}d"] = None
            return row

        sig = {
            "trigger_idx": trigger_idx,
            "trigger_date": str(pd.Timestamp(dates[trigger_idx]).date()),
            "candle1_date": str(pd.Timestamp(dates[c3[0]]).date()),
            "candle2_date": str(pd.Timestamp(dates[c3[1]]).date()),
            "candle3_date": str(pd.Timestamp(dates[c3[2]]).date()),
            "candle1_ret": round(rets_3[0] * 100, 1),
            "candle2_ret": round(rets_3[1] * 100, 1),
            "candle3_ret": round(rets_3[2] * 100, 1),
            "spacing": [c3[1] - c3[0], c3[2] - c3[1]],
            "entry_a": hold_returns(entry_price, entry_idx, "次日开盘"),
        }
        if shrink_entry_price and shrink_entry_idx:
            sig["entry_b"] = hold_returns(shrink_entry_price, shrink_entry_idx, "缩量回调")
        if fourth_idx:
            sig["fourth_candle"] = {
                "date": str(pd.Timestamp(dates[fourth_idx]).date()),
                "ret": fourth_ret,
                "days_after_trigger": fourth_idx - trigger_idx,
                **hold_returns(O[fourth_idx + 1] if fourth_idx + 1 < n else C[fourth_idx],
                               fourth_idx + 1 if fourth_idx + 1 < n else fourth_idx,
                               "第4根次日"),
            }

        signals.append(sig)

    return signals


def aggregate(samples: list[dict]) -> dict:
    """汇总统计."""
    if not samples:
        return {}

    def stats(vals, cap=200.0):
        vs = [v for v in vals if v is not None and abs(v) < cap]
        if not vs:
            return {}
        return {
            "n": len(vs),
            "mean": round(np.mean(vs), 2),
            "median": round(np.median(vs), 2),
            "win_rate": round(sum(v > 0 for v in vs) / len(vs) * 100, 1),
            "p25": round(np.percentile(vs, 25), 2),
            "p75": round(np.percentile(vs, 75), 2),
        }

    out = {"total_samples": len(samples)}
    for h in HOLD_DAYS:
        key = f"ret_{h}d"
        a_vals = [s["entry_a"].get(key) for s in samples if "entry_a" in s]
        b_vals = [s["entry_b"].get(key) for s in samples if "entry_b" in s]
        f_vals = [s["fourth_candle"].get(key) for s in samples if "fourth_candle" in s]
        out[f"hold_{h}d"] = {
            "entry_a_次日开盘": stats(a_vals),
            "entry_b_缩量回调": stats(b_vals),
            "fourth_candle": stats(f_vals),
        }

    out["fourth_candle_rate"] = round(
        sum(1 for s in samples if "fourth_candle" in s) / len(samples) * 100, 1
    )
    out["entry_b_hit_rate"] = round(
        sum(1 for s in samples if "entry_b" in s) / len(samples) * 100, 1
    )
    return out


def main():
    names = get_names()
    syms = list_tdx_symbols()

    all_samples = []
    total = len(syms)
    hit_count = 0

    print(f"[backtest_3rb] 扫描 {total} 只股票...", flush=True)

    for i, code in enumerate(syms):
        if i % 1000 == 0:
            print(f"  {i}/{total} ({hit_count} 样本)...", flush=True)

        df = load_daily(code)
        if df is None or len(df) < 60:
            continue

        # 时间过滤: 上市满 MIN_IPO_AGE 天
        if len(df) < MIN_IPO_AGE:
            continue

        sigs = find_signals(df)
        if not sigs:
            continue

        name = names.get(code, "")
        for s in sigs:
            s["code"] = code
            s["name"] = name
            all_samples.append(s)
            hit_count += 1

    print(f"[backtest_3rb] 共找到 {hit_count} 个样本 (来自 {len(set(s['code'] for s in all_samples))} 只股票)")

    # 保存原始样本
    out_path = ROOT / "logs" / "backtest_three_red_bars.jsonl"
    out_path.parent.mkdir(exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for s in all_samples:
            f.write(json.dumps(s, ensure_ascii=False, default=str) + "\n")
    print(f"[backtest_3rb] 原始样本 → {out_path}")

    # 科创+创业板子集
    star_chiNext = [s for s in all_samples
                    if s["code"].startswith(("688", "300", "301"))]
    print(f"[backtest_3rb] 科创+创业板 子集: {len(star_chiNext)} 样本")

    # 汇总统计
    agg = aggregate(all_samples)
    agg["star_chinext_subset"] = aggregate(star_chiNext)
    agg_path = ROOT / "logs" / "backtest_three_red_bars_summary.json"
    with open(agg_path, "w", encoding="utf-8") as f:
        json.dump(agg, f, ensure_ascii=False, indent=2)
    print(f"[backtest_3rb] 汇总 → {agg_path}")

    def print_summary(label: str, a: dict):
        print(f"\n{'='*60}")
        print(f"三根红棍策略 历史回测 摘要 [{label}]")
        print("="*60)
        print(f"总样本: {a.get('total_samples', 0)}")
        print(f"第4根大阳出现率: {a.get('fourth_candle_rate', 0)}%")
        print(f"缩量回调命中率 (5日内): {a.get('entry_b_hit_rate', 0)}%")
        print()
        for h in HOLD_DAYS:
            print(f"── 持仓 {h} 天 ──")
            for lbl, key in [("入场A(次日开盘)", "entry_a_次日开盘"),
                              ("入场B(缩量回调)", "entry_b_缩量回调"),
                              ("第4根加仓", "fourth_candle")]:
                st = a.get(f"hold_{h}d", {}).get(key, {})
                if st:
                    print(f"  {lbl}: n={st['n']} | 胜率={st['win_rate']}% | "
                          f"均值={st['mean']}% | 中位数={st['median']}% | "
                          f"P25={st['p25']}% P75={st['p75']}%")
            print()

    print_summary("全市场", agg)
    sc = agg.get("star_chinext_subset", {})
    if sc:
        print_summary("科创+创业板", sc)


if __name__ == "__main__":
    main()
