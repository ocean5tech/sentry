"""三红买入策略 (V4): 实时扫描当日是否在买入点.

信号条件:
  1. 近期出现3根箱体大阳 (箱体约束: 基准价偏移≤10%)
  2. 4根大阳在后续30天内出现
  3. 今日价格回调至第3根阳线中值附近 (±3%) 且缩量 (量比≤0.8)

输出字段:
  score        : 3=观察(等回调) / 4=今日在入场点 / 5=近日出现起爆日
  signal_label : "三红观察" / "三红买入" / "三红起爆"
  c3_mid       : 第3根阳线中值 (目标入场价)
  stop_price   : 第3根阳线 LOW (止损价)
  fourth_high  : 第4根阳线高点 (前期目标参考)
  launch_ret   : 起爆日涨幅 (若有)
  c1/c2/c3/fourth_date: 各阳线日期
"""

import numpy as np

NAME = "三红买入"
MIN_BARS = 40

# ── 参数 ────────────────────────────────────────────────────────────
MIN_CANDLE_PCT    = 0.08
MIN_SPACING       = 5
MAX_SPACING       = 25
MAX_SPACING_RATIO = 3.0
BASE_DRIFT_MAX    = 0.10   # 箱体约束

FOURTH_WINDOW     = 30     # 第3根后N天内找第4根
RETRACE_TOL       = 0.03   # 回调至第3根中值 ±3%
VOL_SHRINK        = 0.80   # 量比 < 0.8 视为缩量
LAUNCH_PCT        = 0.08   # 起爆日阈值
LOOKBACK          = 120    # 最多回溯历史


def _find_big_candles(C, O):
    return [i for i in range(1, len(C))
            if C[i-1] > 0
            and (C[i] - C[i-1]) / C[i-1] >= MIN_CANDLE_PCT
            and C[i] > O[i]]


def scan(df, symbol=None) -> dict | None:
    if df is None or len(df) < MIN_BARS:
        return None

    sub = df.tail(LOOKBACK).reset_index(drop=True)
    C   = sub["close"].values.astype(float)
    O   = sub["open"].values.astype(float)
    H   = sub["high"].values.astype(float)
    L   = sub["low"].values.astype(float)
    V   = sub["volume"].values.astype(float)
    n   = len(sub)

    try:
        dates = sub["date"].dt.strftime("%Y-%m-%d").values
    except Exception:
        dates = None

    big_idx = _find_big_candles(C, O)
    if len(big_idx) < 3:
        return None

    # 找最近的满足条件的3+4根链
    best = None
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
        spacings = [chain[k+1] - chain[k] for k in range(len(chain) - 1)]
        if max(spacings) / min(spacings) > MAX_SPACING_RATIO:
            continue

        c3 = chain[-3:]
        trig = c3[-1]

        # 箱体约束
        bp = [C[i-1] for i in c3 if i > 0 and C[i-1] > 0]
        if len(bp) >= 2 and bp[0] > 0:
            if abs(bp[-1] - bp[0]) / bp[0] > BASE_DRIFT_MAX:
                continue

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

        best = (c3, trig, fourth_idx, fourth_high)
        break  # 取最近一组

    if best is None:
        return None

    c3, trig, fourth_idx, fourth_high = best
    c3_mid    = (float(O[c3[2]]) + float(C[c3[2]])) / 2
    stop_price = float(L[c3[2]])
    vol_ma20  = float(np.mean(V[max(0, trig - 19): trig + 1]))

    # 今日状态检查
    cur_close  = float(C[-1])
    cur_vol    = float(V[-1])
    at_entry   = (cur_close <= c3_mid * (1 + RETRACE_TOL) and
                  vol_ma20 > 0 and cur_vol / vol_ma20 <= VOL_SHRINK)

    # 找最近起爆日（第4根之后至今，取最靠近今天的那根）
    launch_idx = None
    launch_ret = None
    for li in range(n - 1, fourth_idx, -1):
        if C[li-1] <= 0:
            continue
        ret = (C[li] - C[li-1]) / C[li-1]
        if ret >= LAUNCH_PCT and C[li] > O[li]:
            launch_idx = li
            launch_ret = round(ret * 100, 1)
            break  # 最近一根起爆日

    launch_days_ago = (n - 1 - launch_idx) if launch_idx is not None else None

    # 评分：起爆日在最近2个交易日内才算三红起爆（否则该卖了）
    if launch_idx is not None and launch_days_ago <= 1:
        score = 5
        label = "三红起爆"
    elif at_entry:
        score = 4
        label = "三红买入"
    else:
        score = 3
        label = "三红观察"

    result = {
        "score":        score,
        "signal_label": label,
        "c3_mid":       round(c3_mid, 2),
        "stop_price":   round(stop_price, 2),
        "fourth_high":  round(fourth_high, 2) if fourth_high else None,
        "at_entry":     at_entry,
        "vol_ratio":    round(cur_vol / vol_ma20, 2) if vol_ma20 > 0 else None,
        "cur_vs_mid":   round((cur_close - c3_mid) / c3_mid * 100, 1),
        "launch_ret":      launch_ret,
        "launch_days_ago": launch_days_ago,
    }

    if dates is not None:
        result["c1_date"]     = str(dates[c3[0]])
        result["c2_date"]     = str(dates[c3[1]])
        result["c3_date"]     = str(dates[c3[2]])
        result["fourth_date"] = str(dates[fourth_idx])
        if launch_idx is not None:
            result["launch_date"] = str(dates[launch_idx])

    return result
