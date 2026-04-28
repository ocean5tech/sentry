"""数据质量校验.
Bob review 提出的 High 风险项: TDX/baostock 数据无校验.
对 K 线 DataFrame 加 data_quality_flag 标记异常.
"""

import pandas as pd


def validate_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """对 OHLCV DataFrame 检测异常, 加 data_quality_flag 列.

    标记规则 (累加, 多个问题 | 连接):
      - "ohlc_relation": high < max(open, close) 或 low > min(open, close)
      - "price_jump": abs(close/prev_close - 1) > 0.30 且非 ±10% 涨跌停
      - "volume_anomaly": volume > 前 20 日均量 × 10
      - "negative": ohlc 任一负数
      - "zero_low": low <= 0 (停牌或数据错)

    返回原 df + data_quality_flag 列 (空字符串=干净, 非空=有问题).
    """
    if df is None or df.empty or "close" not in df.columns:
        return df

    df = df.copy()

    # 1. OHLC 关系
    rel_bad = (
        (df["high"] < df[["open", "close"]].max(axis=1)) |
        (df["low"] > df[["open", "close"]].min(axis=1))
    )

    # 2. 负数 / 0
    neg_bad = (df[["open", "high", "low", "close"]] < 0).any(axis=1)
    zero_low = df["low"] <= 0

    # 3. 价格跳变
    prev_close = df["close"].shift(1)
    pct = (df["close"] / prev_close - 1).abs()
    # ±10% 涨跌停 (主板) ±20% (创业板/科创) 算正常
    jump_bad = (pct > 0.30) & prev_close.notna()

    # 4. 成交量异常
    if "volume" in df.columns:
        v_mean = df["volume"].rolling(20).mean()
        vol_bad = df["volume"] > v_mean * 10
        vol_bad = vol_bad.fillna(False)
    else:
        vol_bad = pd.Series([False] * len(df), index=df.index)

    flags = []
    for i in range(len(df)):
        f = []
        if rel_bad.iloc[i]: f.append("ohlc_relation")
        if neg_bad.iloc[i]: f.append("negative")
        if zero_low.iloc[i]: f.append("zero_low")
        if jump_bad.iloc[i]: f.append("price_jump")
        if vol_bad.iloc[i]: f.append("volume_anomaly")
        flags.append("|".join(f))

    df["data_quality_flag"] = flags
    return df


def quality_summary(df: pd.DataFrame) -> dict:
    """统计 data_quality_flag 分布. 返回 {flag_type: count}."""
    if "data_quality_flag" not in df.columns:
        return {}
    out: dict[str, int] = {}
    for flag_str in df["data_quality_flag"].dropna():
        if not flag_str:
            continue
        for f in flag_str.split("|"):
            out[f] = out.get(f, 0) + 1
    out["total_flagged"] = (df["data_quality_flag"] != "").sum()
    out["total_rows"] = len(df)
    return out
