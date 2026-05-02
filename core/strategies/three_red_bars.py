"""三根红棍策略: 日线中3根均匀分布的大阳线(≥8%), 预警第4根拉升.

信号:
  score=3, signal_level="watch" — 第3根大阳出现 (建底仓观察)
  score=4, signal_level="buy"  — 第4根大阳出现 (强势入场信号)
"""

import numpy as np

NAME = "三根红棍"
MIN_BARS = 30

MIN_CANDLE_PCT = 0.08     # 大阳线最小涨幅
MIN_SPACING = 5           # 两根大阳线之间最小间距（交易日）
MAX_SPACING = 25          # 最大间距
MAX_SPACING_RATIO = 3.0   # 最大/最小间距 ≤ 3x (均匀性约束)
LOOKBACK = 100            # 最多回溯交易日
RECENT_TRIGGER = 30       # 第3根大阳必须在最近N个交易日内


def scan(df) -> dict | None:
    if df is None or len(df) < MIN_BARS:
        return None

    sub = df.tail(LOOKBACK).reset_index(drop=True)
    C = sub["close"].values.astype(float)
    O = sub["open"].values.astype(float)
    n = len(sub)

    # 找大阳线: 涨幅≥MIN_CANDLE_PCT 且收>开
    big_idx = []
    for i in range(1, n):
        if C[i - 1] <= 0:
            continue
        ret = (C[i] - C[i - 1]) / C[i - 1]
        if ret >= MIN_CANDLE_PCT and C[i] > O[i]:
            big_idx.append(i)

    if len(big_idx) < 3:
        return None

    # 从最新大阳线往前找满足间距条件的连续序列
    best_chain: list[int] = []
    for end in range(len(big_idx) - 1, 1, -1):
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
        if max(spacings) / min(spacings) <= MAX_SPACING_RATIO:
            # 取最新4根
            chain = chain[-4:]
            if len(chain) > len(best_chain):
                best_chain = chain

    if len(best_chain) < 3:
        return None

    last_idx = best_chain[-1]
    bars_since_last = n - 1 - last_idx

    # 第3根（最后一根已知大阳）必须足够近
    if bars_since_last > RECENT_TRIGGER:
        return None

    n_candles = len(best_chain)
    spacings = [best_chain[k + 1] - best_chain[k] for k in range(n_candles - 1)]
    rets = [(C[i] - C[i - 1]) / C[i - 1] for i in best_chain]

    try:
        date_vals = sub["date"].dt.strftime("%Y-%m-%d").values
    except Exception:
        date_vals = None

    candle_list = []
    for k, idx in enumerate(best_chain):
        item = {
            "ret_pct": round(rets[k] * 100, 1),
            "bars_ago": n - 1 - idx,
        }
        if date_vals is not None:
            item["date"] = str(date_vals[idx])
        candle_list.append(item)

    signal_level = "buy" if n_candles >= 4 and bars_since_last <= 5 else "watch"
    score = 4 if signal_level == "buy" else 3

    return {
        "score": score,
        "signal_level": signal_level,
        "signal_label": "第四根大阳" if signal_level == "buy" else "三根红棍",
        "n_candles": n_candles,
        "avg_spacing": round(float(np.mean(spacings)), 1),
        "max_spacing": max(spacings),
        "min_spacing": min(spacings),
        "bars_since_last": bars_since_last,
        "big_candles": candle_list,
    }
