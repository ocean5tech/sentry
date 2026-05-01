"""
科创/创业板平台突破策略 (kechuang_breakout)

逻辑：
  针对科创板(688xxx)和创业板(300xxx)的新股特有模式：
  上市后 1~5 年内，经历一段低振幅横盘整理（筹码收集期），
  随后价格放量突破横盘区间上沿 → 发出买入信号。
  （品高688227上市4.3年命中、源杰688498上市3.4年命中、寒武纪688256上市5.8年命中）

  例子股: 品高股份(688227)  源杰科技(688498)  寒武纪(688256)

条件：
  1. 交易所过滤：仅 688xxx（科创板）或 300xxx（创业板）
  2. 上市年龄：IPO_MIN_DAYS 交易日 ~ IPO_MAX_YEARS 年（确保有足够数据 + 筹码还未完全换手）
  3. 近 PLATFORM_WIN 天形成低振幅平台：振幅 < PLATFORM_MAX_AMP
  4. 平台不能是下跌趋势（MA20 斜率 >= -0.3%/天）
  5. 今日收盘突破平台上沿（前 PLATFORM_WIN 天最高收盘价）
  6. 突破时放量：今日成交量 > 20日均量 × VOL_MUL
  7. 非 ST

评分（0~5）：
  3分基础 + 1分(振幅极低<15%) + 1分(量比>=2.0)
"""

import numpy as np
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.lockup_filter import is_near_lockup

NAME = "科创/创业板平台突破"
MIN_BARS = 80

# ── 参数 ─────────────────────────────────────────────────────────
PLATFORM_WIN     = 60     # 平台期回望窗口（交易日）
PLATFORM_MAX_AMP = 0.30   # 平台振幅上限（(max-min)/min）
VOL_MUL          = 1.5    # 突破放量倍数（相对20日均量）
BREAKOUT_MARGIN  = 0.01   # 突破需超出平台上沿的幅度（1%，过滤假突破）
IPO_MIN_DAYS     = 80     # 上市至少 N 个交易日（需要足够数据建立平台）
IPO_MAX_YEARS    = 5      # 上市不超过 N 年（品高4.3yr/源杰3.4yr均在此窗口内）
SLOPE_MIN        = -0.003 # 平台 MA20 斜率下限（避免下跌途中的假平台）

# 科创板 + 创业板前缀
_STAR_PREFIXES = ("688",)
_CHINEXT_PREFIXES = ("300", "301")
_VALID_PREFIXES = _STAR_PREFIXES + _CHINEXT_PREFIXES


def scan(df, symbol: str = "") -> dict | None:
    """
    df: 全历史日线 DataFrame（含 date/open/high/low/close/volume）
    symbol: 股票代码（6位字符串），用于交易所过滤
    """
    # 1. 交易所过滤
    if symbol and not any(symbol.startswith(p) for p in _VALID_PREFIXES):
        return None

    n = len(df)
    if n < MIN_BARS:
        return None

    # 2. 上市年龄过滤：用首行日期作为 IPO 日，精确判断
    if n < IPO_MIN_DAYS:
        return None
    ipo_date    = df.iloc[0]["date"]
    latest_date = df.iloc[-1]["date"]
    calendar_days = (latest_date - ipo_date).days
    if calendar_days > IPO_MAX_YEARS * 365:
        return None

    # D: 解禁期过滤（前后30天内跳过）
    if is_near_lockup(ipo_date):
        return None

    closes  = df["close"].values.astype(float)
    highs   = df["high"].values.astype(float)
    volumes = df["volume"].values.astype(float)

    # 3. 平台期：最近 PLATFORM_WIN 根 K 线
    if n < PLATFORM_WIN + 5:
        return None

    plat_closes = closes[-(PLATFORM_WIN + 1):-1]   # 平台期（不含今天）
    plat_hi     = float(np.max(plat_closes))
    plat_lo     = float(np.min(plat_closes))

    if plat_lo <= 0:
        return None

    amplitude = (plat_hi - plat_lo) / plat_lo
    if amplitude > PLATFORM_MAX_AMP:
        return None

    # 4. 平台不是下跌趋势（MA20 的前后对比）
    ma20_start = float(np.mean(plat_closes[:20]))
    ma20_end   = float(np.mean(plat_closes[-20:]))
    slope = (ma20_end - ma20_start) / ma20_start / PLATFORM_WIN
    if slope < SLOPE_MIN:
        return None

    # 5. 今日放量突破平台上沿
    today_close  = float(closes[-1])
    today_vol    = float(volumes[-1])
    ma_vol20     = float(np.mean(volumes[-21:-1]))   # 今天之前20天均量

    if ma_vol20 <= 0:
        return None

    vol_ratio = today_vol / ma_vol20

    breakout_threshold = plat_hi * (1 + BREAKOUT_MARGIN)
    if today_close < breakout_threshold:
        return None

    if vol_ratio < VOL_MUL:
        return None

    # 6. 非 ST（代码传入时无法判断名称，只做基础检查）

    # ── 评分 ──────────────────────────────────────────────────
    score = 3                                    # 基础分
    if amplitude < 0.15:
        score += 1                               # +1: 极低振幅平台（< 15%）
    if vol_ratio >= 2.0:
        score += 1                               # +1: 强放量（≥ 2倍）

    match_pct = max(0, round((1 - amplitude / PLATFORM_MAX_AMP) * 100))

    return {
        "score":          score,
        "platform_amp":   round(amplitude * 100, 1),   # 平台振幅 %
        "platform_high":  round(plat_hi, 2),            # 平台上沿价格
        "platform_low":   round(plat_lo, 2),            # 平台下沿价格
        "platform_days":  PLATFORM_WIN,
        "vol_ratio":      round(vol_ratio, 2),           # 今日量比
        "breakout_pct":   round((today_close / plat_hi - 1) * 100, 1),  # 突破幅度 %
        "ipo_date":        str(ipo_date.date()) if hasattr(ipo_date, "date") else str(ipo_date),
        "ipo_calendar_days": calendar_days,              # 上市至今日历天数
        "match_pct":      match_pct,
        "exchange":       "科创板" if (symbol.startswith("688")) else "创业板",
    }
