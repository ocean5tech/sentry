"""三角旗（收敛旗形/Pennant）检测 — 宏景风格.

必须同时满足:
  1. 几何收敛: 高点趋势线下倾 + 低点趋势线上倾 + 当前价在通道内
  2. 时长合理: 40~130 个交易日（2~6 个月）
  3. MA20 稳健上升: slope > 0 且单调上升比 ≥ 60%
  4. 低点单调递增: 摆动低点逐步抬高
  5. 波浪显著: 相邻摆动点间距 ≥ 10 天，每轮波幅 ≥ 5%
"""

import numpy as np

# 缩量下跌日判断参数（挖坑买入点）
DIP_VOL_RATIO = 0.80   # 量比 < 此值视为缩量
DIP_LOOKBACK  = 3      # 检测最近N根K线是否有缩量下跌

SWING_WIN = 5          # 摆动高低点判断窗口（±5天，过滤日内噪音）
MIN_SWINGS = 2
APEX_MAX_FUTURE = 30
APEX_MAX_PAST = 15

# 时长约束（交易日）
MIN_WIN = 40           # 最短2个月（约40个交易日）
MAX_WIN = 130          # 最长6个月

# 波浪质量约束
MIN_SWING_SPACING = 10  # 相邻摆动点间距至少10个交易日（约2周）
MIN_WAVE_AMP = 0.05     # 每轮波幅（高点到低点）至少5%

# MA20 要求
MA20_MIN_SLOPE = 0.0      # 斜率必须 > 0（上升）
MA20_MIN_MONO = 0.60      # 单调上升比 ≥ 60%

# 低点单调要求
LOW_MIN_MONO = 0.67       # 相邻低点中至少 2/3 上升

WINDOW_SIZES = [45, 55, 65, 80, 100, 130]


def _local_highs(H: np.ndarray, window: int = SWING_WIN) -> list[int]:
    return [i for i in range(window, len(H) - window)
            if H[i] >= max(H[i - window: i + window + 1])]


def _local_lows(L: np.ndarray, window: int = SWING_WIN) -> list[int]:
    return [i for i in range(window, len(L) - window)
            if L[i] <= min(L[i - window: i + window + 1])]


def _filter_spacing(indices: list[int], min_spacing: int) -> list[int]:
    """保留满足最小间距的摆动点序列（贪心选取）."""
    if not indices:
        return []
    kept = [indices[0]]
    for idx in indices[1:]:
        if idx - kept[-1] >= min_spacing:
            kept.append(idx)
    return kept


def _ma20_trend(C: np.ndarray):
    """返回 (slope_pct_per_bar, monotone_ratio, ma_array)."""
    n = len(C)
    ma = np.array([np.mean(C[max(0, i - 19): i + 1]) for i in range(n)])
    slope = float(np.polyfit(np.arange(n, dtype=float), ma, 1)[0])
    slope_pct = slope / (float(np.mean(ma)) + 1e-9) * 100
    rising = sum(1 for i in range(1, n) if ma[i] >= ma[i - 1])
    mono = rising / max(n - 1, 1)
    return slope_pct, mono, ma


def _low_monotone(sl_prices: list[float]):
    """返回相邻低点上升比例."""
    if len(sl_prices) < 2:
        return 0.0
    rises = sum(1 for i in range(1, len(sl_prices)) if sl_prices[i] > sl_prices[i - 1])
    return rises / (len(sl_prices) - 1)


def _check_window(H, L, C, dates_arr, n) -> dict | None:
    sh = _filter_spacing(_local_highs(H), MIN_SWING_SPACING)
    sl = _filter_spacing(_local_lows(L),  MIN_SWING_SPACING)
    if len(sh) < MIN_SWINGS or len(sl) < MIN_SWINGS:
        return None

    # 波幅检验：第一个摆动高点到第一个摆动低点的振幅必须足够大
    avg_price = float(np.mean(C)) + 1e-9
    first_high = float(H[sh[0]])
    first_low  = float(L[sl[0]])
    wave_amp = abs(first_high - first_low) / avg_price
    if wave_amp < MIN_WAVE_AMP:
        return None

    coef_h = np.polyfit(sh, H[sh], 1)
    coef_l = np.polyfit(sl, L[sl], 1)
    slope_h, slope_l = float(coef_h[0]), float(coef_l[0])

    # 对称收敛: 高点必须整体下降，低点必须整体上升
    if slope_h >= 0 or slope_l <= 0:
        return None
    # 显式验证: 第一个摆动高点 > 最后一个摆动高点，第一个摆动低点 < 最后一个摆动低点
    if H[sh[0]] <= H[sh[-1]] or L[sl[0]] >= L[sl[-1]]:
        return None

    denom = slope_h - slope_l
    if abs(denom) < 1e-10:
        return None
    apex_x = (coef_l[1] - coef_h[1]) / denom
    days_to_apex = int(round(apex_x - (n - 1)))
    if days_to_apex < -APEX_MAX_PAST or days_to_apex > APEX_MAX_FUTURE:
        return None

    mid = float(np.mean(C[-5:]))
    if mid <= 0:
        return None
    w0 = (float(np.polyval(coef_h, 0)) - float(np.polyval(coef_l, 0))) / mid
    wn = (float(np.polyval(coef_h, n - 1)) - float(np.polyval(coef_l, n - 1))) / mid
    if w0 <= 0 or wn <= 0 or wn >= w0:
        return None

    # 当前价必须在通道内（允许轻微突破上沿 8%）
    cur = float(C[-1])
    upper_now = float(np.polyval(coef_h, n - 1))
    lower_now = float(np.polyval(coef_l, n - 1))
    if cur > upper_now * 1.08 or cur < lower_now * 0.92:
        return None

    # MA20 稳健上升
    ma_slope, ma_mono, _ = _ma20_trend(C)
    if ma_slope <= MA20_MIN_SLOPE or ma_mono < MA20_MIN_MONO:
        return None

    # 低点单调递增
    sl_prices = [float(L[i]) for i in sl]
    low_mono = _low_monotone(sl_prices)
    if low_mono < LOW_MIN_MONO:
        return None

    compression = round(wn / w0, 3)

    # 摆动高点和低点的日期+价格（用于推送展示）
    def _pts(indices, prices, dates):
        out = []
        for i in indices:
            d = str(dates[i]) if dates is not None else f"bar{i}"
            out.append({"date": d, "price": round(float(prices[i]), 2)})
        return out

    swing_highs = _pts(sh, H, dates_arr)
    swing_lows  = _pts(sl, L, dates_arr)

    return {
        "detected": True,
        "slope_high": round(slope_h, 4),
        "slope_low": round(slope_l, 4),
        "days_to_apex": days_to_apex,
        "compression": compression,
        "width_pct_now": round(wn * 100, 1),
        "width_pct_start": round(w0 * 100, 1),
        "near_breakout": abs(days_to_apex) <= 15,
        "ma20_slope_pct": round(ma_slope, 4),
        "ma20_mono": round(ma_mono, 2),
        "low_mono": round(low_mono, 2),
        "pennant_start": str(dates_arr[0]) if dates_arr is not None else "",
        "pennant_end": str(dates_arr[-1]) if dates_arr is not None else "",
        "swing_highs": swing_highs,
        "swing_lows":  swing_lows,
    }


def detect_pennant(df) -> dict:
    """
    多窗口扫描，返回符合宏景风格的三角旗形态.

    宏景风格要求:
      - 几何收敛 (高点↓ 低点↑ 通道内)
      - 时长 20~65 个交易日
      - MA20 稳健上升 (slope>0, 单调比≥60%)
      - 摆动低点单调递增 (相邻低点≥2/3 上升)

    Returns dict: detected + metadata (see _check_window)
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

    for win in WINDOW_SIZES:
        if win < MIN_WIN or win > MAX_WIN:
            continue
        if total < win + 5:
            continue
        H = H_all[-win:]
        L = L_all[-win:]
        C = C_all[-win:]
        d = dates_str[-win:] if dates_str is not None else None
        res = _check_window(H, L, C, d, win)
        if res is not None:
            return res

    return {"detected": False}


def is_dip_day(df) -> bool:
    """
    检测最近 DIP_LOOKBACK 根 K 线内是否有缩量下跌日（三角旗后的挖坑买入点）.

    条件：
      - 当日收盘 < 前日收盘 (下跌)
      - 当日成交量 < 近 20 日均量 × DIP_VOL_RATIO (缩量)
    """
    if df is None or len(df) < 25:
        return False
    C = df["close"].values.astype(float)
    V = df["volume"].values.astype(float)
    n = len(df)
    vol_ma20 = float(np.mean(V[max(0, n - 21): n - 1]))
    if vol_ma20 <= 0:
        return False
    for i in range(n - DIP_LOOKBACK, n):
        if i < 1:
            continue
        if C[i] < C[i - 1] and V[i] / vol_ma20 < DIP_VOL_RATIO:
            return True
    return False
