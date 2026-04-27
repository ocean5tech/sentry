#!/usr/bin/env python3
"""
预判涨停实验台.

策略框架:
  - 信号: T0 满足某特征组合 (T0 收盘前可见的特征)
  - 入场: T0 收盘价
  - 目标: 触及 entry * 1.09 (盘中, 不要求封板)
  - 卖出: 触及 +9% 卖出 (按 1.09 价); 一字板不卖 (顺延, 不消耗持有日);
          3 个非一字板交易日内未触发 -> 当日收盘 timeout

第一次跑构建特征矩阵 parquet 缓存; 之后每个 --filter 实验是秒级查询.

Usage:
    python3 scripts/predict_lu_lab.py --build           # 重建缓存
    python3 scripts/predict_lu_lab.py --list            # 列可用特征
    python3 scripts/predict_lu_lab.py --baseline        # 不过滤的 base rate
    python3 scripts/predict_lu_lab.py --filter F_t0_lu+F_close_at_h
"""

import argparse
import importlib.util
import struct
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.data_loader import load_daily
from core.tdx_loader import list_tdx_symbols
from core.formula_engine import _ref_smart
from core.stock_names import get_names

CACHE = ROOT / "data" / "predict_lu_features.parquet"
MIN_BARS = 130
MAX_HOLD = 3
TARGET = 0.09
COST = 0.0022
IS_CUT = pd.Timestamp("2025-01-01")


def _mytt():
    spec = importlib.util.spec_from_file_location("mytt", ROOT / "core" / "mytt.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_MT = _mytt()
MA = _MT.MA
MACD = _MT.MACD
KDJ = _MT.KDJ
HHV = _MT.HHV
REF = _MT.REF


def read_index(path):
    raw = open(path, "rb").read()
    rows = []
    for i in range(len(raw) // 32):
        d, o, h, l, c, amt, vol, _ = struct.unpack("<IIIIIfII", raw[i*32:(i+1)*32])
        if c:
            rows.append({"date": pd.Timestamp(str(d)), "close": c / 100})
    return pd.DataFrame(rows)


def compute_dapan(sh300):
    C = sh300["close"].values.astype(float)
    ma20 = MA(C, 20)
    return {d.date(): bool(u) for d, u in zip(sh300["date"], ma20 > _ref_smart(ma20, 5))}


def board_ok(sym):
    return sym.startswith(("00", "60", "30", "68"))


def is_20cm(sym):
    return sym.startswith(("30", "68"))


def shifted(arr, k):
    out = np.full_like(arr, np.nan, dtype=float)
    if k > 0 and len(arr) > k:
        out[:-k] = arr[k:]
    return out


def build_one(sym, name, mkt_up):
    if not board_ok(sym):
        return None
    if "ST" in (name or "").upper():
        return None
    try:
        df = load_daily(sym)
    except Exception:
        return None
    n = len(df)
    if n < MIN_BARS + 4:
        return None

    cb = is_20cm(sym)
    lu_thresh = 0.195 if cb else 0.095

    C = df["close"].values.astype(float)
    O = df["open"].values.astype(float)
    H = df["high"].values.astype(float)
    L = df["low"].values.astype(float)
    V = df["volume"].values.astype(float)

    prev_C = np.concatenate(([np.nan], C[:-1]))
    ret_t0 = C / prev_C - 1
    is_lu = (ret_t0 >= lu_thresh).astype(np.int8)

    lu_s = pd.Series(is_lu)
    lu_5d = lu_s.rolling(5, min_periods=1).sum().values
    lu_10d = lu_s.rolling(10, min_periods=1).sum().values
    prev_lu = np.concatenate(([0], is_lu[:-1]))
    consec_lu = ((is_lu == 1) & (prev_lu == 1)).astype(np.int8)
    first_lu = ((is_lu == 1) & (lu_10d <= 1)).astype(np.int8)

    ma_v5 = MA(V, 5)
    prev_ma_v5 = _ref_smart(ma_v5, 1).astype(float)
    vol_ratio = V / np.where(prev_ma_v5 > 0, prev_ma_v5, np.nan)

    ma5 = MA(C, 5)
    ma10 = MA(C, 10)
    ma20 = MA(C, 20)
    ma60 = MA(C, 60)
    ma120 = MA(C, 120)

    ret5 = np.full(n, np.nan); ret5[5:] = C[5:] / C[:-5] - 1
    ret20 = np.full(n, np.nan); ret20[20:] = C[20:] / C[:-20] - 1

    high_252 = pd.Series(H).rolling(252, min_periods=60).max().values
    dist_high = C / high_252 - 1
    prev_h20 = pd.Series(H).rolling(20, min_periods=20).max().shift(1).values
    new_h20 = (C > prev_h20).astype(np.int8)
    prev_h60 = pd.Series(H).rolling(60, min_periods=60).max().shift(1).values
    new_h60 = (C > prev_h60).astype(np.int8)

    body = (C - O) / np.where(O > 0, O, np.nan)
    close_at_h = (C >= H * 0.995).astype(np.int8)
    up_day = (C > O).astype(np.int8)
    consec_3up = (pd.Series(up_day).rolling(3, min_periods=3).sum() == 3).astype(np.int8).values

    # ----- 新增特征组 (按用户选择: 2,3,4,5,6) -----
    # 6. KDJ + MACD 双金叉 (5 日内都金叉过)
    DIF, DEA, _ = MACD(C)
    K, D, _ = KDJ(C, H, L)
    macd_x = (DIF > DEA) & (np.concatenate(([False], (DIF[:-1] <= DEA[:-1]))))
    kdj_x = (K > D) & (np.concatenate(([False], (K[:-1] <= D[:-1]))))
    # 5 日内两个都至少金叉过一次 (含当日)
    macd_5d = pd.Series(macd_x.astype(np.int8)).rolling(5, min_periods=1).sum().values > 0
    kdj_5d = pd.Series(kdj_x.astype(np.int8)).rolling(5, min_periods=1).sum().values > 0
    f_kdj_macd_x = (macd_5d & kdj_5d).astype(np.int8)

    # 2. 强势股回踩 MA10
    lu_30d = lu_s.rolling(30, min_periods=1).sum().values
    above_ma10_5d = pd.Series((C > ma10).astype(np.int8)).rolling(5, min_periods=1).sum().values > 0
    f_pullback_ma10 = (
        (lu_30d >= 1) &
        (np.abs(C / np.where(ma10 > 0, ma10, np.nan) - 1) < 0.03) &
        above_ma10_5d
    ).astype(np.int8)

    # 3. 缩量横盘后放量突破
    ma_v5_back = pd.Series(V).shift(5).rolling(5, min_periods=5).mean().values   # T-10..T-6 均量
    ma_v5_recent = pd.Series(V).shift(1).rolling(5, min_periods=5).mean().values  # T-5..T-1 均量
    ma_v10 = pd.Series(V).shift(1).rolling(10, min_periods=10).mean().values
    high_5_prev = pd.Series(H).shift(1).rolling(5, min_periods=5).max().values
    f_squeeze_break = (
        (ma_v5_recent < ma_v5_back * 0.8) &
        (V > ma_v10 * 1.5) &
        (C > high_5_prev)
    ).astype(np.int8)

    # 4. 向上 FVG (T-2 high < T0 low)
    h_prev2 = np.concatenate(([np.nan, np.nan], H[:-2]))
    f_fvg_up = (h_prev2 < L).astype(np.int8)

    # 5. 半路打板: 涨幅 6%~涨停, 收盘近 high, 放量
    f_half_board = (
        (ret_t0 >= 0.06) & (ret_t0 < lu_thresh) &
        (C >= H * 0.99) &
        (vol_ratio > 1.5)
    ).astype(np.int8)

    dates_d = df["date"].dt.date.values
    mkt = np.array([mkt_up.get(d, False) for d in dates_d], dtype=np.int8)

    H1 = shifted(H, 1); L1 = shifted(L, 1); C1 = shifted(C, 1); V1 = shifted(V, 1); O1 = shifted(O, 1)
    H2 = shifted(H, 2); L2 = shifted(L, 2); C2 = shifted(C, 2); O2 = shifted(O, 2)
    H3 = shifted(H, 3); L3 = shifted(L, 3); C3 = shifted(C, 3); O3 = shifted(O, 3)

    # 一字板: H==L 且 当日涨停 (相对前一日 close)
    ow1 = ((H1 == L1) & ((H1 / C - 1) >= lu_thresh)).astype(np.int8)
    ow2 = ((H2 == L2) & ((H2 / C1 - 1) >= lu_thresh)).astype(np.int8)
    ow3 = ((H3 == L3) & ((H3 / C2 - 1) >= lu_thresh)).astype(np.int8)

    out = pd.DataFrame({
        "date": df["date"].values,
        "code": sym,
        "is_20cm": np.int8(cb),
        "t0_close": C,
        "t1_o": O1, "t1_h": H1, "t1_c": C1, "t1_ow": ow1, "t1_v": V1,
        "t2_o": O2, "t2_h": H2, "t2_c": C2, "t2_ow": ow2,
        "t3_o": O3, "t3_h": H3, "t3_c": C3, "t3_ow": ow3,
        "F_t0_lu":      is_lu,
        "F_first_lu":   first_lu,
        "F_consec_lu":  consec_lu,
        "F_ret_gt3":    (ret_t0 > 0.03).astype(np.int8),
        "F_ret_gt5":    (ret_t0 > 0.05).astype(np.int8),
        "F_ret_gt7":    (ret_t0 > 0.07).astype(np.int8),
        "F_lu5_ge1":    (lu_5d >= 1).astype(np.int8),
        "F_lu5_ge2":    (lu_5d >= 2).astype(np.int8),
        "F_lu10_ge1":   (lu_10d >= 1).astype(np.int8),
        "F_close_at_h": close_at_h,
        "F_body_gt3":   (body > 0.03).astype(np.int8),
        "F_body_gt5":   (body > 0.05).astype(np.int8),
        "F_3up":        consec_3up,
        "F_new_h20":    new_h20,
        "F_new_h60":    new_h60,
        "F_vol_2x":     (vol_ratio > 2).astype(np.int8),
        "F_vol_3x":     (vol_ratio > 3).astype(np.int8),
        "F_vol_5x":     (vol_ratio > 5).astype(np.int8),
        "F_3line_up":   ((ma20 > ma60) & (ma60 > ma120)).astype(np.int8),
        "F_5_10_20":    ((ma5 > ma10) & (ma10 > ma20)).astype(np.int8),
        "F_dist_h_lt5": (dist_high > -0.05).astype(np.int8),
        "F_dist_h_lt10":(dist_high > -0.10).astype(np.int8),
        "F_ret5_gt10":  (ret5 > 0.10).astype(np.int8),
        "F_ret20_gt20": (ret20 > 0.20).astype(np.int8),
        "F_mkt_up":     mkt,
        "F_kdj_macd_x":   f_kdj_macd_x,
        "F_pullback_ma10": f_pullback_ma10,
        "F_squeeze_break": f_squeeze_break,
        "F_fvg_up":        f_fvg_up,
        "F_half_board":    f_half_board,
    })

    valid = (V > 0) & (V1 > 0) & (~np.isnan(C3)) & (~np.isnan(ma120))
    valid_idx = np.zeros(n, dtype=bool); valid_idx[MIN_BARS:] = True
    valid &= valid_idx
    return out[valid].copy()


def build_universe(force=False):
    if CACHE.exists() and not force:
        print(f"[cache] {CACHE}")
        return pd.read_parquet(CACHE)

    sh300 = read_index(ROOT / "data" / "tdx" / "sh" / "lday" / "sh000300.day")
    mkt_up = compute_dapan(sh300)
    names = get_names()
    syms = list_tdx_symbols()
    print(f"build: 扫 {len(syms)} 只股票...")

    parts = []
    for i, s in enumerate(syms, 1):
        if i % 1500 == 0:
            print(f"  {i}/{len(syms)}  累计行 {sum(len(p) for p in parts):,}")
        r = build_one(s, names.get(s, ""), mkt_up)
        if r is not None and len(r):
            parts.append(r)

    df = pd.concat(parts, ignore_index=True)
    print(f"完成: {len(df):,} 行  字段 {len(df.columns)}")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(CACHE)
    print(f"缓存 -> {CACHE}")
    return df


def simulate(sub, mode="ideal", entry_prem=0.05, cost=COST):
    """模拟交易.
    mode='ideal': T0 收盘买入 (理想, 假设能买到)
    mode='real':  T+1 开盘买入 (实战, 跳过 T+1 一字板 / T+1 高开>entry_prem)
                  cost 默认 0.35% (双佣 0.05+ 印花 0.05+ 过户 0.002+ 滑点 0.2)
    """
    if mode == "real":
        # 过滤: T+1 必须能买入
        t0c = sub["t0_close"].values.astype(float)
        t1o = sub["t1_o"].values.astype(float)
        t1ow = sub["t1_ow"].values
        valid = (~np.isnan(t1o)) & (t1o > 0) & (t1ow == 0) & (t1o <= t0c * (1 + entry_prem))
        sub = sub[valid].copy()

    n = len(sub)
    if n == 0:
        return None

    if mode == "real":
        entry = sub["t1_o"].values.astype(float)
    else:
        entry = sub["t0_close"].values.astype(float)
    tgt = entry * (1 + TARGET)

    H = np.column_stack([sub[f"t{k}_h"].values for k in (1, 2, 3)]).astype(float)
    C = np.column_stack([sub[f"t{k}_c"].values for k in (1, 2, 3)]).astype(float)
    OW = np.column_stack([sub[f"t{k}_ow"].values for k in (1, 2, 3)]).astype(bool)

    rets = np.full(n, np.nan)
    held = np.zeros(n, dtype=np.int8)
    rcode = np.zeros(n, dtype=np.int8)  # 0=timeout, 1/2/3=tk_target, 9=all_ow

    for i in range(n):
        ent = entry[i]; t = tgt[i]
        days_h = 0
        done = False
        for slot in range(3):
            if OW[i, slot]:
                continue
            if H[i, slot] >= t:
                rets[i] = TARGET
                held[i] = slot + 1
                rcode[i] = slot + 1
                done = True
                break
            days_h += 1
            if days_h >= MAX_HOLD:
                rets[i] = (C[i, slot] - ent) / ent
                held[i] = slot + 1
                rcode[i] = 0
                done = True
                break
        if not done:
            rets[i] = (C[i, 2] - ent) / ent
            held[i] = 3
            rcode[i] = 9

    out = sub[["date", "code", "is_20cm"]].copy()
    out["ret_gross"] = rets
    out["ret_net"] = rets - cost
    out["hold_d"] = held
    out["rcode"] = rcode
    return out


def report(out, label, base_n):
    n = len(out)
    print(f"\n=== {label}  (n={n:,}, 占全样本 {n/base_n*100:.2f}%) ===")
    if n == 0:
        return
    g = out["ret_gross"]; ne = out["ret_net"]
    catch = (g >= TARGET - 1e-6).mean() * 100
    win_g = (g > 0).mean() * 100
    big_l = (g < -0.05).mean() * 100
    mean_g = g.mean() * 100
    mean_n = ne.mean() * 100
    med_n = ne.median() * 100
    days = out["hold_d"].mean()
    rd = pd.Series(out["rcode"]).value_counts(normalize=True) * 100
    desc = {0: "timeout", 1: "T1中", 2: "T2中", 3: "T3中", 9: "全一字"}

    print(f"  抓涨停 {catch:5.1f}%  毛胜率 {win_g:5.1f}%  毛均 {mean_g:+5.2f}%  "
          f"净均 {mean_n:+5.2f}%  净中位 {med_n:+5.2f}%  <-5% {big_l:5.1f}%  均持仓 {days:.2f}d")
    print(f"  退出: " + "  ".join(f"{desc[k]}={v:.1f}%" for k, v in rd.items()))

    out["dt"] = pd.to_datetime(out["date"])
    isd = out[out["dt"] < IS_CUT]
    oosd = out[out["dt"] >= IS_CUT]
    for lab2, sub2 in [("IS 21-24", isd), ("OOS 25+", oosd)]:
        if len(sub2) == 0:
            print(f"  [{lab2}] 无样本"); continue
        c2 = (sub2["ret_gross"] >= TARGET - 1e-6).mean() * 100
        n2 = sub2["ret_net"].mean() * 100
        print(f"  [{lab2}] n={len(sub2):,}  抓涨停 {c2:5.1f}%  净均 {n2:+5.2f}%")
    for tag, m in [("主板", out["is_20cm"] == 0), ("创/科", out["is_20cm"] == 1)]:
        s = out[m]
        if len(s) == 0:
            continue
        c2 = (s["ret_gross"] >= TARGET - 1e-6).mean() * 100
        n2 = s["ret_net"].mean() * 100
        print(f"  [{tag}] n={len(s):,}  抓涨停 {c2:5.1f}%  净均 {n2:+5.2f}%")


def simulate_portfolio(out, n_slots=10, slot_amount=50000):
    """50 万 / N slot 资金管理. 按 (entry_date, code) 升序先到先得.
       slot 释放: exit 那天结束 -> 下个交易日可用.
    """
    sh300 = read_index(ROOT / "data" / "tdx" / "sh" / "lday" / "sh000300.day")
    cal = sorted(set(sh300["date"].dt.date))
    cal_idx = {d: i for i, d in enumerate(cal)}

    df = out.copy()
    df["entry_d"] = pd.to_datetime(df["date"]).dt.date
    df["entry_i"] = df["entry_d"].map(cal_idx)
    df = df.dropna(subset=["entry_i"]).copy()
    df["entry_i"] = df["entry_i"].astype(int)
    df["exit_i"] = df["entry_i"] + df["hold_d"].astype(int)
    df = df.sort_values(["entry_i", "code"]).reset_index(drop=True)

    slots = [-1] * n_slots
    sel_idx = []
    skipped = 0
    for i, row in enumerate(df.itertuples(index=False)):
        ei = row.entry_i
        free = next((k for k, s in enumerate(slots) if s < ei), None)
        if free is None:
            skipped += 1
            continue
        slots[free] = row.exit_i
        sel_idx.append(i)

    sel = df.iloc[sel_idx].copy()
    sel["pnl"] = sel["ret_net"] * slot_amount
    return sel, skipped, len(df), cal


def report_portfolio(sel, skipped, total_signals, cal, slot_amount, n_slots, label):
    capital = slot_amount * n_slots
    print(f"\n--- 资金管理: {n_slots} slot × {slot_amount/10000:.0f}万 = {capital/10000:.0f}万 ---")
    print(f"信号 {total_signals:,}  执行 {len(sel):,}  跳过 {skipped:,} ({skipped/total_signals*100:.1f}%)")
    if sel.empty:
        return

    sel = sel.sort_values("entry_i").reset_index(drop=True)
    total_pnl = sel["pnl"].sum()
    total_ret = total_pnl / capital
    span_days = (sel["entry_d"].max() - sel["entry_d"].min()).days or 1
    ann = total_ret / span_days * 365 * 100
    win = (sel["ret_net"] > 0).mean() * 100
    print(f"总盈亏 {total_pnl:+,.0f}  累计收益 {total_ret*100:+.1f}%  年化 {ann:+.1f}%  净胜率 {win:.1f}%")

    sel["year"] = pd.to_datetime(sel["entry_d"]).dt.year
    yr = sel.groupby("year").agg(
        n=("code", "size"),
        win=("ret_net", lambda x: (x > 0).mean() * 100),
        net_mean=("ret_net", lambda x: x.mean() * 100),
        pnl=("pnl", "sum"),
    )
    yr["cum_pnl"] = yr["pnl"].cumsum()
    yr["yoy_ret%"] = yr["pnl"] / capital * 100
    print(yr.to_string(float_format=lambda v: f"{v:.1f}"))

    # 回撤 (按交易日合并 pnl)
    daily_pnl = sel.groupby("entry_i")["pnl"].sum()
    equity = capital + daily_pnl.cumsum()
    peak = equity.cummax()
    dd = (equity - peak) / peak
    max_dd = dd.min() * 100
    sharpe_d = daily_pnl.mean() / (daily_pnl.std() + 1e-9) * np.sqrt(250)
    print(f"最大回撤 {max_dd:.1f}%  日 Sharpe≈{sharpe_d:.2f}  权益峰值 {equity.max():,.0f}  谷值 {equity.min():,.0f}")


def parse_filter(s):
    if not s:
        return []
    return [x.strip() for x in s.split("+") if x.strip()]


def apply_filter(df, feats):
    if not feats:
        return np.ones(len(df), dtype=bool)
    m = np.ones(len(df), dtype=bool)
    cols = set(df.columns)
    for f in feats:
        neg = False
        if f.startswith("!"):
            neg = True; f = f[1:]
        if f not in cols:
            raise SystemExit(f"未知特征: {f}")
        v = df[f].values == 1
        m &= (~v if neg else v)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--filter", default="")
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--label", default="")
    ap.add_argument("--portfolio", action="store_true", help="跑 50万/10slot 资金管理")
    ap.add_argument("--slots", type=int, default=10)
    ap.add_argument("--slot-amount", type=float, default=50000)
    ap.add_argument("--mode", choices=["ideal", "real"], default="ideal",
                    help="ideal=T0收盘买; real=T+1开盘买,跳过一字板/高开过大")
    ap.add_argument("--cost", type=float, default=0.0035,
                    help="单笔总成本 (默认 0.35%, real 模式建议)")
    ap.add_argument("--entry-prem", type=float, default=0.05, help="real 模式 T+1 高开容忍")
    ap.add_argument("--start", default="", help="入场日下限 YYYY-MM-DD")
    ap.add_argument("--end", default="", help="入场日上限 YYYY-MM-DD")
    args = ap.parse_args()

    df = build_universe(force=args.build)

    if args.list:
        feats = [c for c in df.columns if c.startswith("F_")]
        print(f"\n可用特征 (全样本 n={len(df):,}):")
        ent = df["t0_close"].values; tgt = ent * 1.09
        # 单因子 hit rate (粗算: T+1 触及+9%, 不算一字顺延)
        t1_hit = df["t1_h"].values >= tgt
        for f in feats:
            v = df[f].values == 1
            tr = v.mean() * 100
            if v.sum() > 0:
                hr = t1_hit[v].mean() * 100
                lift = hr / t1_hit.mean() if t1_hit.mean() > 0 else 0
            else:
                hr = lift = 0
            print(f"  {f:18}  触发 {tr:5.2f}%  T+1抓+9% {hr:5.1f}%  Lift {lift:.2f}x")
        return

    base_n = len(df)
    if args.baseline:
        ent = df["t0_close"].values; tgt = ent * 1.09
        t1_hit = (df["t1_h"].values >= tgt).mean() * 100
        any_hit = ((df["t1_h"].values >= tgt) |
                   (df["t2_h"].values >= tgt) |
                   (df["t3_h"].values >= tgt)).mean() * 100
        print(f"\n=== Baseline (无过滤)  全样本 n={base_n:,} ===")
        print(f"  T+1 触及+9%: {t1_hit:.2f}%  (粗算, 未做一字顺延)")
        print(f"  3日内触及+9%: {any_hit:.2f}%")
        print("  (跳过完整 simulate: 全样本太大)")
        return

    feats = parse_filter(args.filter)
    label = args.label or (" + ".join(feats) if feats else "全样本")
    mask = apply_filter(df, feats)
    sub = df[mask]
    if args.start:
        sub = sub[pd.to_datetime(sub["date"]) >= pd.Timestamp(args.start)]
    if args.end:
        sub = sub[pd.to_datetime(sub["date"]) <= pd.Timestamp(args.end)]
    label = f"{label} [{args.mode}, cost={args.cost*100:.2f}%]"
    out = simulate(sub, mode=args.mode, entry_prem=args.entry_prem, cost=args.cost)
    if out is None:
        print(f"\n=== {label}: 无信号 ===")
        return
    report(out, label, base_n)

    if args.portfolio:
        sel, skipped, total, cal = simulate_portfolio(out, args.slots, args.slot_amount)
        report_portfolio(sel, skipped, total, cal, args.slot_amount, args.slots, label)


if __name__ == "__main__":
    main()
