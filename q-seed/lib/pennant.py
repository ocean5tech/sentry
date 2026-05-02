"""三角旗（收敛旗形/Pennant）检测.

高点趋势线下倾 + 低点趋势线上倾 → 收敛 → 即将起爆.
采用多窗口扫描，自动找最近最收敛的形态段.
"""

import numpy as np

SWING_WIN = 2          # 局部极值窗口 (±N bars)，2 更敏感
MIN_SWINGS = 2         # 最少摆动点数（高低点各不少于2个）
APEX_MAX_FUTURE = 20   # apex 最多N天后（收敛尖端未到）
APEX_MAX_PAST = 10     # apex 最多N天前（已穿过尖端）

# 尝试的窗口尺寸（从短到长，取最新最收敛的）
WINDOW_SIZES = [22, 28, 35, 45, 60]


def _local_highs(H: np.ndarray, window: int = SWING_WIN) -> list[int]:
    idxs = []
    n = len(H)
    for i in range(window, n - window):
        if H[i] >= max(H[i - window: i + window + 1]):
            idxs.append(i)
    return idxs


def _local_lows(L: np.ndarray, window: int = SWING_WIN) -> list[int]:
    idxs = []
    n = len(L)
    for i in range(window, n - window):
        if L[i] <= min(L[i - window: i + window + 1]):
            idxs.append(i)
    return idxs


def _check_window(H, L, closes, dates_arr, n) -> dict | None:
    """检测一个固定长度窗口是否形成三角旗. 成功返回结果 dict, 否则 None."""
    sh = _local_highs(H)
    sl = _local_lows(L)

    if len(sh) < MIN_SWINGS or len(sl) < MIN_SWINGS:
        return None

    coef_h = np.polyfit(sh, H[sh], 1)
    coef_l = np.polyfit(sl, L[sl], 1)
    slope_h, slope_l = float(coef_h[0]), float(coef_l[0])

    # 必须收敛: 高点下倾 + 低点上倾
    if slope_h >= 0 or slope_l <= 0:
        return None

    # Apex
    denom = slope_h - slope_l
    if abs(denom) < 1e-10:
        return None
    apex_x = (coef_l[1] - coef_h[1]) / denom
    days_to_apex = int(round(apex_x - (n - 1)))

    if days_to_apex < -APEX_MAX_PAST or days_to_apex > APEX_MAX_FUTURE:
        return None

    # 宽度压缩比
    mid = float(np.mean(closes[-5:]))
    if mid <= 0:
        return None
    w0 = (float(np.polyval(coef_h, 0)) - float(np.polyval(coef_l, 0))) / mid
    wn = (float(np.polyval(coef_h, n - 1)) - float(np.polyval(coef_l, n - 1))) / mid

    # 初始宽度必须 > 最终宽度（真正在收缩）
    if w0 <= 0 or wn <= 0 or wn >= w0:
        return None

    # 当前价格必须在通道内 (否则已起爆/已跌破)
    current_close = float(closes[-1])
    upper_now = float(np.polyval(coef_h, n - 1))
    lower_now = float(np.polyval(coef_l, n - 1))
    # 允许轻微突破上沿5%（即将起爆的信号），但不能超出太多
    if current_close > upper_now * 1.08 or current_close < lower_now * 0.92:
        return None

    compression = round(wn / w0, 3)

    return {
        "detected": True,
        "slope_high": round(slope_h, 4),
        "slope_low": round(slope_l, 4),
        "days_to_apex": days_to_apex,
        "compression": compression,
        "width_pct_now": round(wn * 100, 1),
        "width_pct_start": round(w0 * 100, 1),
        "near_breakout": abs(days_to_apex) <= 15,
        "pennant_start": str(dates_arr[0]) if dates_arr is not None else "",
        "pennant_end": str(dates_arr[-1]) if dates_arr is not None else "",
        "_n_sh": len(sh),
        "_n_sl": len(sl),
    }


def detect_pennant(df) -> dict:
    """
    多窗口扫描，返回最近最收敛的三角旗形态.

    Returns dict:
      detected       bool
      slope_high     float  (<0 = 高点下倾)
      slope_low      float  (>0 = 低点上倾)
      days_to_apex   int    (正=收敛尖端在未来, 0/负=已穿过)
      compression    float  (当前宽度/初始宽度, 越小越收敛)
      width_pct_now  float  (当前通道宽度/价格%)
      near_breakout  bool   (days_to_apex 绝对值 ≤ 7)
      pennant_start  str    YYYY-MM-DD
      pennant_end    str    YYYY-MM-DD
    """
    if df is None or len(df) < 25:
        return {"detected": False}

    try:
        dates_str = df["date"].dt.strftime("%Y-%m-%d").values
    except Exception:
        dates_str = None

    H_all = df["high"].values.astype(float)
    L_all = df["low"].values.astype(float)
    C_all = df["close"].values.astype(float)
    total = len(df)

    best = None
    # 从最短窗口开始，最短窗口优先（最近的形态）
    for win in WINDOW_SIZES:
        if total < win + 5:
            continue
        H = H_all[-win:]
        L = L_all[-win:]
        C = C_all[-win:]
        d = dates_str[-win:] if dates_str is not None else None
        res = _check_window(H, L, C, d, win)
        if res is not None:
            # 取第一个（最短窗口）检测到的结果
            best = res
            break

    if best is None:
        return {"detected": False}

    # 清理内部调试字段
    best.pop("_n_sh", None)
    best.pop("_n_sl", None)
    return best
