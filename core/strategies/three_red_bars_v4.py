"""三红吸筹洗盘策略 (V4) — 形态选股

庄家通过三段大阳+次日高量洗盘推高散户成本，然后拉升。

选股条件（c3在最近5个交易日内）：
  1. c1/c2/c3 各涨幅≥8%，相邻间距5-15个交易日
  2. c1/c2 后1-3日内放量（≥1.5倍均量），确认洗盘；c3不要求
  3. c1→c3 整体缓慢上升（基准价涨幅0-30%）
  4. 三根阳线实体同价带：min(c1c2c3收盘) > max(c1c2c3开盘)
  5. c1到c3之间无额外≥8%涨跌K线（停牌复牌后连续涨跌停除外）

输出：
  score=5  起爆日（≥8%大阳）在最近1-2天内出现
  score=3  形态确认，c3在最近5个交易日内

止损参考：c1/c2/c3 三根中最低低点
"""

import numpy as np

NAME     = "三红买入"
MIN_BARS = 60

# ── 参数 ──────────────────────────────────────────────────────────
MIN_CANDLE_PCT    = 0.08    # 大阳线最小涨幅
MIN_SPACING       = 5       # 相邻最小间距（K线数）
MAX_SPACING       = 15      # 相邻最大间距
MAX_SPACING_RATIO = 2.5     # 间距均匀性
BASE_DRIFT_MAX    = 0.30    # 整体上升上限

WASH_VOL_MULT     = 1.5     # 洗盘放量倍数
WASH_WINDOW       = 3       # 洗盘检测窗口

BOX_TOL           = 0.05    # 框体高点容忍度
SUSPENSION_DAYS   = 7       # 停牌判断阈值（日历天）

SCAN_WINDOW       = 5       # c3距今最多5个交易日（1周）
LAUNCH_PCT        = 0.08    # 起爆日阈值


def _find_big_candles(C, O):
    return [i for i in range(1, len(C))
            if C[i-1] > 0
            and (C[i] - C[i-1]) / C[i-1] >= MIN_CANDLE_PCT
            and C[i] > O[i]]


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


def _wash_vol_ratio(V, candle_idx, vol_ma):
    if vol_ma <= 0:
        return 0.0
    end = min(candle_idx + WASH_WINDOW + 1, len(V))
    max_v = max((V[i] for i in range(candle_idx + 1, end)), default=0.0)
    return max_v / vol_ma


def scan(df, symbol=None) -> dict | None:
    if df is None or len(df) < MIN_BARS:
        return None

    sub   = df.tail(150).reset_index(drop=True)
    C     = sub["close"].values.astype(float)
    O     = sub["open"].values.astype(float)
    H     = sub["high"].values.astype(float)
    L     = sub["low"].values.astype(float)
    V     = sub["volume"].values.astype(float)
    n     = len(sub)

    try:
        dates = sub["date"].dt.strftime("%Y-%m-%d").values
    except Exception:
        dates = None

    big_idx = _find_big_candles(C, O)
    if len(big_idx) < 3:
        return None

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
        c1_i, c2_i, c3_i = c3[0], c3[1], c3[2]

        # c3在最近SCAN_WINDOW根内
        days_since_c3 = (n - 1) - c3_i
        if days_since_c3 > SCAN_WINDOW + 5:
            break  # 往后更远的链只会更老

        # 缓慢上升
        bp = [C[i-1] for i in c3 if i > 0 and C[i-1] > 0]
        if len(bp) < 2 or bp[0] <= 0:
            continue
        drift = (bp[-1] - bp[0]) / bp[0]
        if drift < 0 or drift > BASE_DRIFT_MAX:
            continue

        # 框体重叠：min(收) > max(开)
        if min(C[c1_i], C[c2_i], C[c3_i]) <= max(O[c1_i], O[c2_i], O[c3_i]):
            continue

        # 内部清洁 + 停牌复牌处理
        box_top = max(H[c1_i], H[c2_i], H[c3_i]) * (1 + BOX_TOL)
        c123 = {c1_i, c2_i, c3_i}
        dirty = False
        resumption = False
        for ii in range(c1_i + 1, c3_i):
            if ii in c123:
                resumption = False
                continue
            if _calendar_gap(dates, ii) > SUSPENSION_DAYS:
                resumption = True
            is_lim = C[ii-1] > 0 and abs(C[ii] - C[ii-1]) / C[ii-1] >= MIN_CANDLE_PCT
            if resumption:
                if is_lim:
                    continue
                else:
                    resumption = False
            if H[ii] > box_top or is_lim:
                dirty = True
                break
        if dirty:
            continue

        # 洗盘放量
        vol_ma_c1 = float(np.mean(V[max(0, c1_i - 19): c1_i + 1]))
        vol_ma_c2 = float(np.mean(V[max(0, c2_i - 19): c2_i + 1]))
        if _wash_vol_ratio(V, c1_i, vol_ma_c1) < WASH_VOL_MULT:
            continue
        if _wash_vol_ratio(V, c2_i, vol_ma_c2) < WASH_VOL_MULT:
            continue

        # 优先可操作链（c3在SCAN_WINDOW内且无起爆）
        has_launch = any(
            C[li-1] > 0 and (C[li] - C[li-1]) / C[li-1] >= LAUNCH_PCT and C[li] > O[li]
            for li in range(c3_i + 1, n)
        )
        actionable = days_since_c3 <= SCAN_WINDOW and not has_launch
        if best is None:
            best = (c3, vol_ma_c1, vol_ma_c2, actionable)
        elif actionable and not best[3]:
            best = (c3, vol_ma_c1, vol_ma_c2, actionable)
            break
        else:
            break

    if best is None:
        return None

    c3, vol_ma_c1, vol_ma_c2, _ = best
    c1_i, c2_i, c3_i = c3[0], c3[1], c3[2]
    days_since_c3 = (n - 1) - c3_i

    if days_since_c3 > SCAN_WINDOW + 5:
        return None

    stop_price    = min(float(L[c1_i]), float(L[c2_i]), float(L[c3_i]))
    wash_r1       = _wash_vol_ratio(V, c1_i, vol_ma_c1)
    wash_r2       = _wash_vol_ratio(V, c2_i, vol_ma_c2)

    # 起爆检测
    launch_idx = None
    launch_ret = None
    for li in range(c3_i + 1, n):
        if C[li-1] <= 0:
            continue
        ret = (C[li] - C[li-1]) / C[li-1]
        if ret >= LAUNCH_PCT and C[li] > O[li]:
            launch_idx = li
            launch_ret = round(ret * 100, 1)
            break

    launch_days_ago = (n - 1 - launch_idx) if launch_idx is not None else None

    score = 5 if (launch_idx is not None and launch_days_ago is not None and launch_days_ago <= 1) else 3
    label = "三红起爆" if score == 5 else "三红形态"

    result = {
        "score":           score,
        "signal_label":    label,
        "stop_price":      round(stop_price, 2),
        "days_since_c3":   days_since_c3,
        "launch_ret":      launch_ret,
        "launch_days_ago": launch_days_ago,
        "wash_vol_c1":     round(wash_r1, 2),
        "wash_vol_c2":     round(wash_r2, 2),
    }

    if dates is not None:
        result["c1_date"] = str(dates[c1_i])
        result["c2_date"] = str(dates[c2_i])
        result["c3_date"] = str(dates[c3_i])
        if launch_idx is not None:
            result["launch_date"] = str(dates[launch_idx])

    return result
