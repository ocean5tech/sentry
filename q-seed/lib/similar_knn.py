"""
KNN 相似度: 把所有 Sig 特征 z-score 归一, 算与模板均值的欧氏距离.
"""

import numpy as np
import pandas as pd


# 不参与距离计算的列 (元数据)
META_COLS = {"code", "name", "sig_date", "entry", "_launch"}


def compute_distances(df: pd.DataFrame, template_code: str, template_dates: list[str]) -> pd.DataFrame:
    """对 df (含所有 Sig + 特征) 加一列 dist (与 template 均值的欧氏距离).
    返回新 df, dist 越小越像模板.
    若找不到任何 template Sig, 返回空 df.
    """
    if df.empty:
        return df

    df = df.copy()
    df["sig_date_dt"] = pd.to_datetime(df["sig_date"])

    tpl = df[(df["code"] == template_code) & (df["sig_date"].isin(template_dates))]
    if tpl.empty:
        return pd.DataFrame()

    feat_cols = [c for c in df.columns
                 if c not in META_COLS
                 and c != "sig_date_dt"
                 and not c.startswith("_")
                 and pd.api.types.is_numeric_dtype(df[c])]

    X = df[feat_cols].values.astype(float)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    mu = X.mean(axis=0)
    sigma = X.std(axis=0) + 1e-9
    Xn = (X - mu) / sigma

    tpl_X = tpl[feat_cols].values.astype(float)
    tpl_X = np.nan_to_num(tpl_X, nan=0.0, posinf=0.0, neginf=0.0)
    tpl_n = (tpl_X - mu) / sigma
    q = tpl_n.mean(axis=0)

    df["dist"] = np.sqrt(((Xn - q) ** 2).sum(axis=1))
    return df


def filter_recent(df: pd.DataFrame, recent_days: int,
                  since: str | None = None,
                  until: str | None = None) -> pd.DataFrame:
    """按 sig_date 过滤. 优先用 since/until, 否则用 recent_days."""
    if df.empty:
        return df
    if "sig_date_dt" not in df.columns:
        df = df.copy()
        df["sig_date_dt"] = pd.to_datetime(df["sig_date"])

    if since:
        df = df[df["sig_date_dt"] >= pd.Timestamp(since)]
    if until:
        df = df[df["sig_date_dt"] <= pd.Timestamp(until)]
    if not since and not until:
        latest = df["sig_date_dt"].max()
        cutoff = latest - pd.Timedelta(days=recent_days)
        df = df[df["sig_date_dt"] >= cutoff]
    return df


def topn_by_distance(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """按 dist 升序去重 (每个 code 保留最近一次 sig_date 的 dist 最小条目), 取 TOP N."""
    if df.empty:
        return df
    # 每个 code 保留 dist 最小的一条 (最像模板的那次触发)
    df = df.sort_values("dist").drop_duplicates(subset=["code"], keep="first")
    df = df.sort_values("dist").reset_index(drop=True)
    return df.head(n)
