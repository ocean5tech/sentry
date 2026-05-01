#!/usr/bin/env python3
"""
统计科创板+创业板中涨幅达上市价5倍的股票，是何时到达的。
分析：是否主要在上市2年内完成，还是2年后也有机会。
"""

import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.tdx_loader import list_tdx_symbols
from core.data_loader import load_daily

_VALID_PREFIXES = ("688", "300", "301")

THRESHOLD = 5.0   # IPO价格的N倍

results = []
symbols = [s for s in list_tdx_symbols() if any(s.startswith(p) for p in _VALID_PREFIXES)]
print(f"共 {len(symbols)} 只科创/创业板股票")

for sym in symbols:
    try:
        df = load_daily(sym)
        if df is None or len(df) < 60:
            continue
        ipo_price = float(df.iloc[0]["close"])
        if ipo_price <= 0:
            continue
        target = ipo_price * THRESHOLD
        closes = df["close"].values.astype(float)
        dates  = df["date"].values

        # 找到首次达到5倍的行
        hit_idx = np.where(closes >= target)[0]
        if len(hit_idx) == 0:
            continue

        first_hit = int(hit_idx[0])
        trading_days_to_hit = first_hit  # 从IPO算起的交易日数
        ipo_date   = df.iloc[0]["date"]
        hit_date   = df.iloc[first_hit]["date"]
        calendar_days = (hit_date - ipo_date).days
        years = calendar_days / 365.0

        results.append({
            "symbol":        sym,
            "ipo_price":     round(ipo_price, 2),
            "hit_price":     round(float(df.iloc[first_hit]["close"]), 2),
            "ipo_date":      str(ipo_date.date()) if hasattr(ipo_date, "date") else str(ipo_date),
            "hit_date":      str(hit_date.date()) if hasattr(hit_date, "date") else str(hit_date),
            "trading_days":  trading_days_to_hit,
            "calendar_days": calendar_days,
            "years":         round(years, 2),
        })
    except Exception:
        pass

results.sort(key=lambda x: x["years"])
print(f"\n达到上市价{THRESHOLD}倍的股票共 {len(results)} 只\n")

# 按年份分布
buckets = [0, 0, 0, 0, 0, 0, 0]  # <1yr, 1-2, 2-3, 3-4, 4-5, 5-6, >6yr
for r in results:
    y = r["years"]
    if   y < 1: buckets[0] += 1
    elif y < 2: buckets[1] += 1
    elif y < 3: buckets[2] += 1
    elif y < 4: buckets[3] += 1
    elif y < 5: buckets[4] += 1
    elif y < 6: buckets[5] += 1
    else:       buckets[6] += 1

total = len(results)
labels = ["<1年","1~2年","2~3年","3~4年","4~5年","5~6年",">6年"]
print("达到5倍价格所需时间分布:")
print(f"{'区间':>8}  {'数量':>6}  {'占比':>7}  {'累计':>7}")
cumsum = 0
for lb, cnt in zip(labels, buckets):
    cumsum += cnt
    pct   = cnt / total * 100
    cpct  = cumsum / total * 100
    bar   = "█" * int(pct / 2)
    print(f"{lb:>8}  {cnt:>6}  {pct:>6.1f}%  {cpct:>6.1f}%  {bar}")

# 统计量
years_list = [r["years"] for r in results]
print(f"\n中位数: {np.median(years_list):.1f} 年")
print(f"均值:   {np.mean(years_list):.1f} 年")
print(f"最短:   {min(years_list):.2f} 年")
print(f"最长:   {max(years_list):.2f} 年")
print(f"2年内到达: {sum(1 for y in years_list if y<=2):d} 只 ({sum(1 for y in years_list if y<=2)/total*100:.0f}%)")
print(f"3年内到达: {sum(1 for y in years_list if y<=3):d} 只 ({sum(1 for y in years_list if y<=3)/total*100:.0f}%)")
print(f"5年内到达: {sum(1 for y in years_list if y<=5):d} 只 ({sum(1 for y in years_list if y<=5)/total*100:.0f}%)")

# 列出最快的20只
print(f"\n最快达到5倍的前20只:")
print(f"{'代码':<8}  {'IPO价':>7}  {'5倍价':>8}  {'IPO日期':<12}  {'到达日期':<12}  {'年数':>6}  {'交易日':>6}")
for r in results[:20]:
    print(f"{r['symbol']:<8}  {r['ipo_price']:>7.2f}  {r['hit_price']:>8.2f}  {r['ipo_date']:<12}  {r['hit_date']:<12}  {r['years']:>6.2f}  {r['trading_days']:>6}")
