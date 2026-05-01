"""
布林带支撑/突破策略 (boll_support)

两个买入信号：
  信号1 — 下沿开仓：
    价格在布林带下轨附近（位置 <= 25%），布林带收窄（横盘确认），
    大盘下跌时该股抗跌（近20日大盘跌幅>0.5%的交易日，该股平均跑赢大盘）
    → 有资金护盘/吸筹，低风险开仓点，止损设在下轨下方。

  信号2 — 上沿放量突破加仓：
    收盘突破布林带上轨，量比 >= 1.5 → 趋势确认，加仓。

适用范围：科创板(688) + 创业板(300/301)，非 ST。

评分：
  信号1(下沿): 2分基础 + 1分(位置<=10%) + 1分(抗跌优势>1%)
  信号2(突破): 3分基础 + 1分(量比>=2.0)  + 1分(突破幅度>2%)
"""

import numpy as np
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.lockup_filter import is_near_lockup

NAME = "布林带支撑/突破"
MIN_BARS = 30

BOLL_N          = 20     # 布林带周期
BOLL_K          = 2.0    # 布林带倍数
MAX_WIDTH_PCT   = 15.0   # 横盘确认：布林带宽度上限 %（(upper-lower)/ma）
LOWER_POS_PCT   = 25.0   # 下沿区：价格位置上限（0=下轨）
MID_POS_PCT     = 50.0   # 中线区：价格位置上限（25-50%，需额外缩量条件）
VOL_SHRINK      = 0.8    # 缩量判定：今日量比 < 此值（中线区/回踩入场要求）
VOL_MUL         = 1.5    # 突破放量倍数
PULLBACK_WIN    = 15     # 回踩信号：向前找突破的窗口（交易日）
PULLBACK_MIN_PCT= 80.0   # 回踩信号：价格位置下限（不能跌太深）
PULLBACK_MAX_PCT= 115.0  # 回踩信号：价格位置上限（不能还在高位没回踩）
PULLBACK_PRIOR  = 1.03   # 回踩信号：近期最高价需超过上轨的倍数（确认曾经突破）
RS_WIN          = 20     # 抗跌性回望窗口（交易日）
RS_DOWN_THRESH  = -0.005 # 大盘"下跌日"判定阈值（日收益 < -0.5%）
RS_MIN_DAYS     = 5      # 至少需要 N 个下跌日才做抗跌判断

_VALID_PREFIXES = ("688", "300", "301")

# 模块级缓存：避免每只股票重复加载指数
_index_cache: dict = {}


def _get_index_returns(index_code: str) -> np.ndarray | None:
    """返回指数最近 RS_WIN 日的日收益率数组（含今日）"""
    if index_code in _index_cache:
        return _index_cache[index_code]
    try:
        from core.data_loader import load_daily
        df = load_daily(index_code)
        if df is None or len(df) < RS_WIN + 1:
            return None
        closes = df["close"].values.astype(float)
        rets = np.diff(closes) / closes[:-1]   # 日收益率
        result = rets[-RS_WIN:]                 # 最近 RS_WIN 天
        _index_cache[index_code] = result
        return result
    except Exception:
        return None


def _calc_resilience(stock_rets: np.ndarray, index_rets: np.ndarray) -> tuple[float, int]:
    """
    在大盘下跌日，计算股票平均超额收益。
    返回 (avg_outperformance, n_down_days)
    """
    down_mask = index_rets < RS_DOWN_THRESH
    n_down = int(np.sum(down_mask))
    if n_down < RS_MIN_DAYS:
        return 0.0, n_down
    excess = stock_rets[down_mask] - index_rets[down_mask]
    return float(np.mean(excess)) * 100, n_down  # 转为 %


def scan(df, symbol: str = "") -> dict | None:
    if symbol and not any(symbol.startswith(p) for p in _VALID_PREFIXES):
        return None

    n = len(df)
    if n < MIN_BARS:
        return None

    closes  = df["close"].values.astype(float)
    volumes = df["volume"].values.astype(float)

    # 解禁期过滤
    ipo_date = df.iloc[0]["date"]
    if is_near_lockup(ipo_date):
        return None

    # 布林带（基于最近 BOLL_N 根收盘）
    win = closes[-BOLL_N:]
    ma  = float(np.mean(win))
    std = float(np.std(win))
    if ma <= 0 or std <= 0:
        return None

    upper = ma + BOLL_K * std
    lower = ma - BOLL_K * std
    width_pct = (upper - lower) / ma * 100

    cur = float(closes[-1])
    pos_pct = (cur - lower) / (upper - lower) * 100  # 0=下轨, 100=上轨

    # 量比
    vol_ma = float(np.mean(volumes[-21:-1])) if n >= 21 else float(np.mean(volumes[:-1]))
    vol_ratio = float(volumes[-1]) / vol_ma if vol_ma > 0 else 1.0

    ipo_str = str(ipo_date.date()) if hasattr(ipo_date, "date") else str(ipo_date)
    exchange = "科创板" if symbol.startswith("688") else "创业板"
    # 科创板参照科创50，创业板参照创业板指
    index_code = "000688" if symbol.startswith("688") else "399006"

    # ── 信号1：下沿 / 中线缩量 开仓 ─────────────────────────
    if width_pct <= MAX_WIDTH_PCT and 0 <= pos_pct <= MID_POS_PCT:
        in_lower = pos_pct <= LOWER_POS_PCT
        in_mid   = not in_lower

        # 中线区额外要求缩量（缩量回调，不是放量下跌）
        if in_mid and vol_ratio >= VOL_SHRINK:
            return None

        # 抗跌性：大盘下跌日，该股跑赢大盘
        index_rets = _get_index_returns(index_code)
        if index_rets is not None and n >= RS_WIN + 1:
            stock_rets = np.diff(closes) / closes[:-1]
            stock_rets_win = stock_rets[-RS_WIN:]
            outperf, n_down = _calc_resilience(stock_rets_win, index_rets)
        else:
            outperf, n_down = 0.0, 0

        if n_down >= RS_MIN_DAYS and outperf <= 1.0:
            return None   # 大盘跌时跑赢不足1%，弱势股，跳过

        if in_lower:
            signal_label = "下沿开仓"
            score = 2
            if pos_pct <= 10:
                score += 1   # 极贴下轨
        else:
            signal_label = "中线缩量"
            score = 1
        if n_down >= RS_MIN_DAYS and outperf > 1.0:
            score += 1       # 抗跌优势显著

        return {
            "score":        score,
            "signal":       "support_entry",
            "signal_label": signal_label,
            "boll_pos":     round(pos_pct, 1),
            "boll_width":   round(width_pct, 1),
            "lower":        round(lower, 2),
            "upper":        round(upper, 2),
            "ma20":         round(ma, 2),
            "vol_ratio":    round(vol_ratio, 2),
            "resilience":   round(outperf, 2),
            "down_days":    n_down,
            "ipo_date":     ipo_str,
            "exchange":     exchange,
        }

    # ── 信号2：上沿放量突破加仓 ──────────────────────────────
    if cur > upper and vol_ratio >= VOL_MUL:
        # 时效性过滤：突破必须是今天刚发生，前一天收盘不能已在上轨之上
        # 连续 N 天在上轨以上 = 追高陷阱，无入场价值
        consecutive_above = 0
        for prev_c in reversed(closes[:-1]):
            if prev_c > upper:
                consecutive_above += 1
            else:
                break
        if consecutive_above >= 1:
            return None   # 昨天已在上轨以上，突破非今日，跳过

        breakout_pct = (cur - upper) / upper * 100
        score = 3
        if vol_ratio >= 2.0:
            score += 1
        if breakout_pct > 2.0:
            score += 1
        return {
            "score":        score,
            "signal":       "breakout_add",
            "signal_label": "上沿突破加仓",
            "boll_pos":     round(pos_pct, 1),
            "boll_width":   round(width_pct, 1),
            "lower":        round(lower, 2),
            "upper":        round(upper, 2),
            "ma20":         round(ma, 2),
            "breakout_pct": round(breakout_pct, 1),
            "vol_ratio":    round(vol_ratio, 2),
            "ipo_date":     ipo_str,
            "exchange":     exchange,
        }

    # ── 信号3：回踩上沿确认买入 ──────────────────────────────
    # 条件：近期曾突破上轨 → 现在回踩到上轨附近 → 缩量 → 确认支撑
    if PULLBACK_MIN_PCT <= pos_pct <= PULLBACK_MAX_PCT and n >= PULLBACK_WIN + BOLL_N:
        recent_max = float(np.max(closes[-(PULLBACK_WIN + 1):-1]))
        if recent_max > upper * PULLBACK_PRIOR and vol_ratio < VOL_SHRINK:
            score = 3
            if pos_pct >= 95:
                score += 1   # 刚好贴着上轨回踩（最理想位置）
            if vol_ratio < 0.5:
                score += 1   # 极度缩量，主力不出货
            return {
                "score":        score,
                "signal":       "pullback_entry",
                "signal_label": "回踩上沿",
                "boll_pos":     round(pos_pct, 1),
                "boll_width":   round(width_pct, 1),
                "lower":        round(lower, 2),
                "upper":        round(upper, 2),
                "ma20":         round(ma, 2),
                "vol_ratio":    round(vol_ratio, 2),
                "prior_high":   round(recent_max, 2),
                "ipo_date":     ipo_str,
                "exchange":     exchange,
            }

    return None
