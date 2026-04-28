#!/usr/bin/env python3
"""
q-backtest-fast: 快速 walk-forward 回测.

性能 ~320x 提升 vs subprocess 版本:
  原: 每个 as_of_date 调 q-seed subprocess (25min × 175 days = 73h)
  新: 1 次预扫全市场所有 sigs (~5min) + 每 as_of_date filter+rank (~1s)
      + 多 hold_days 同时算 (单股 K 线只读 1 次)

实现:
  - import q-seed/lib/wave_model 直接 (轻微 break L2 但 backtest 工具属性)
  - 预扫返回 dict: code → list of {sig_date, features...}
  - 按 as_of_date 过滤窗口 → 每模板 KNN 排序 → top N
  - 对每 top, evaluate_multi 一次性算 5/10/20/40/80 天 outcome
"""

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / "q-seed" / "lib"))
sys.path.insert(0, str(ROOT.parent / "q-seed" / "lib" / "core"))

# q-seed 的核心算法 (直接 import, 避免 subprocess)
from wave_model import WaveParams, FilterParams, scan_one_features
import core.tdx_loader as tdx_loader
import core.data_loader as data_loader
import core.stock_names as stock_names

from lib.trade_calendar import get_trading_days, trading_days_between
from lib.multi_horizon import evaluate_multi


# ===================================================================
# 命令行 + 配置
# ===================================================================

def parse_args():
    ap = argparse.ArgumentParser(prog="q-backtest-fast",
                                 description="快速 walk-forward 回测 (multi-horizon)")
    ap.add_argument("--start", default="2025-08-01", help="回测起始 YYYY-MM-DD")
    ap.add_argument("--end", default="2026-04-22", help="回测结束 YYYY-MM-DD")
    ap.add_argument("--step", type=int, default=1, help="跳过 N 个交易日 (默认 1, 每日)")
    ap.add_argument("--hold-days", default="5,10,20,40,80",
                    help="持仓天数列表 (逗号分隔, 默认 5,10,20,40,80)")
    ap.add_argument("--win-pct", type=float, default=0.05, help="胜利阈值 (默认 +5%)")
    ap.add_argument("--top", type=int, default=5, help="每模板 top N 候选 (默认 5)")
    ap.add_argument("--templates", default=None, help="逗号分隔模板, 默认全部 6 个")
    ap.add_argument("--qseed-config", default="/home/wyatt/sentry/quant/q-seed/config.yaml")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--output", default=None, help="输出 jsonl 路径")
    return ap.parse_args()


# ===================================================================
# 全市场预扫 (一次性)
# ===================================================================

def prescan_market(qs_cfg: dict, wp: WaveParams, fp: FilterParams) -> pd.DataFrame:
    """对全市场所有 stocks 跑 wave_model.scan_one_features (strict + loose 各扫一次).
    返回 DataFrame: 每行一个 (code, sig_date, mode, features...) 样本.
    """
    print(f"[prescan] 加载 stocks 列表...", file=sys.stderr)
    syms = tdx_loader.list_tdx_symbols()
    names = stock_names.get_names()
    print(f"[prescan] {len(syms)} 只股票", file=sys.stderr)

    all_rows = []
    t0 = time.time()
    # strict + loose 各扫一次, 因为 hongjing/litong 等模板 mode 不同
    for mode in ("strict", "loose"):
        print(f"[prescan] mode={mode}...", file=sys.stderr)
        n_done = 0
        for sym in syms:
            rows = scan_one_features(sym, names.get(sym, ""), mode, wp, fp, data_loader.load_daily)
            for r in rows:
                r["_mode"] = mode
                all_rows.append(r)
            n_done += 1
            if n_done % 1000 == 0:
                elapsed = time.time() - t0
                print(f"[prescan]   {n_done}/{len(syms)} ({elapsed:.0f}s, {len(all_rows)} sigs)",
                      file=sys.stderr)

    elapsed = time.time() - t0
    print(f"[prescan] ✅ 完成: {len(all_rows)} sigs, {elapsed:.0f}s",
          file=sys.stderr)

    df = pd.DataFrame(all_rows)
    if not df.empty:
        df["sig_date_dt"] = pd.to_datetime(df["sig_date"])
    return df


# ===================================================================
# KNN 距离 (与 q-seed similar_knn 同样逻辑)
# ===================================================================

META_COLS = {"code", "name", "sig_date", "entry", "_launch", "_mode", "sig_date_dt"}


def compute_template_query(df_subset: pd.DataFrame, template_code: str,
                            template_dates: list[str]) -> tuple[np.ndarray, list[str]]:
    """从全 df 子集里提取模板 sigs, 算其归一化均值向量 (KNN query).
    返回 (q_vec, feat_cols).
    """
    feat_cols = [c for c in df_subset.columns
                 if c not in META_COLS
                 and not c.startswith("_")
                 and pd.api.types.is_numeric_dtype(df_subset[c])]

    # 先用全 subset 算 mu/sigma (同 q-seed 行为)
    X = df_subset[feat_cols].values.astype(float)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    mu = X.mean(axis=0)
    sigma = X.std(axis=0) + 1e-9

    tpl = df_subset[(df_subset["code"] == template_code) &
                    (df_subset["sig_date"].isin(template_dates))]
    if tpl.empty:
        return None, feat_cols
    tpl_X = tpl[feat_cols].values.astype(float)
    tpl_X = np.nan_to_num(tpl_X, nan=0.0, posinf=0.0, neginf=0.0)
    tpl_n = (tpl_X - mu) / sigma
    q = tpl_n.mean(axis=0)

    return q, feat_cols, mu, sigma


def compute_distances_to_query(df: pd.DataFrame, q: np.ndarray, feat_cols: list[str],
                                mu: np.ndarray, sigma: np.ndarray) -> pd.DataFrame:
    """对 df 每行算到 q 的欧氏距离."""
    if df.empty or q is None:
        return df
    df = df.copy()
    X = df[feat_cols].values.astype(float)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    Xn = (X - mu) / sigma
    df["dist"] = np.sqrt(((Xn - q) ** 2).sum(axis=1))
    return df


# ===================================================================
# 主流程
# ===================================================================

def main():
    args = parse_args()

    # 加载 q-seed 配置
    qs_cfg = yaml.safe_load(Path(args.qseed_config).read_text(encoding="utf-8"))
    bt_cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))

    # 设置数据路径
    tdx_dir = qs_cfg["data"]["tdx_dir"]
    tdx_loader.set_tdx_dir(tdx_dir)
    stock_names.set_cache_path(qs_cfg["data"]["stock_names_csv"])

    wp = WaveParams.from_dict(qs_cfg.get("wave_params", {}))
    fp = FilterParams.from_dict(qs_cfg.get("filter", {}))

    # 模板列表
    all_templates = qs_cfg["templates"]
    template_names = (args.templates.split(",") if args.templates
                      else list(all_templates.keys()))
    print(f"[main] templates: {template_names}", file=sys.stderr)

    hold_days_list = [int(x) for x in args.hold_days.split(",")]
    print(f"[main] hold_days: {hold_days_list}", file=sys.stderr)

    # 交易日历
    sse_path = tdx_dir + "/sh/lday/sh000001.day"
    all_days = get_trading_days(sse_path)
    start_d = date.fromisoformat(args.start)
    end_d = date.fromisoformat(args.end)
    bt_days = trading_days_between(all_days, start_d, end_d)[::args.step]
    print(f"[main] backtest days: {len(bt_days)} (step={args.step})", file=sys.stderr)
    print(f"[main] win_pct: {args.win_pct}", file=sys.stderr)

    # ─── Step 1: 预扫全市场 ───
    df_all = prescan_market(qs_cfg, wp, fp)
    if df_all.empty:
        print("[main] ❌ 预扫返回空", file=sys.stderr)
        sys.exit(1)
    print(f"[main] 预扫得 {len(df_all)} 个 sigs", file=sys.stderr)

    # ─── Step 2: 对每模板预算 query 向量 (基于全数据 mu/sigma) ───
    template_queries: dict[str, dict] = {}
    for tname in template_names:
        tpl_cfg = all_templates[tname]
        # 该模板只看自己 mode 的 sigs (strict 模板 vs loose 模板归一化基础不同)
        df_mode = df_all[df_all["_mode"] == tpl_cfg["mode"]].copy()

        result = compute_template_query(df_mode, tpl_cfg["code"], tpl_cfg["sig_dates"])
        if result[0] is None:
            print(f"[main] ⚠️  template {tname} ({tpl_cfg['code']}) 找不到锚点 sigs",
                  file=sys.stderr)
            continue
        q, feat_cols, mu, sigma = result
        template_queries[tname] = {
            "q": q, "feat_cols": feat_cols, "mu": mu, "sigma": sigma,
            "mode": tpl_cfg["mode"],
            "code": tpl_cfg["code"],
            "name": tpl_cfg["name"],
        }
        print(f"[main] 模板 {tname}: query 向量已建 (mode={tpl_cfg['mode']})",
              file=sys.stderr)

    # ─── Step 3: 对每 as_of_date 过滤 + 排序 + 评估 ───
    print(f"\n[main] 开始 walk-forward {len(bt_days)} 天...", file=sys.stderr)
    all_results = []
    t_start = time.time()

    recent_days = wp.lookback   # 60 默认

    for i, as_of_d in enumerate(bt_days, 1):
        if i % 20 == 0 or i == 1 or i == len(bt_days):
            elapsed = time.time() - t_start
            avg = elapsed / max(i, 1)
            eta = avg * (len(bt_days) - i)
            print(f"[walk] ({i}/{len(bt_days)}) {as_of_d} elapsed={elapsed:.0f}s eta={eta:.0f}s",
                  file=sys.stderr)

        cutoff = pd.Timestamp(as_of_d)
        cutoff_low = cutoff - pd.Timedelta(days=recent_days)

        for tname, q_info in template_queries.items():
            df_mode = df_all[df_all["_mode"] == q_info["mode"]]
            df_window = df_mode[(df_mode["sig_date_dt"] >= cutoff_low) &
                                  (df_mode["sig_date_dt"] < cutoff)].copy()
            if df_window.empty:
                continue

            df_d = compute_distances_to_query(df_window, q_info["q"],
                                                q_info["feat_cols"],
                                                q_info["mu"], q_info["sigma"])
            # 每只股保留 dist 最小的 sig
            df_d = df_d.sort_values("dist").drop_duplicates(subset=["code"], keep="first")
            top_df = df_d.head(args.top)

            for _, row in top_df.iterrows():
                code = row["code"]
                # 取该 code 在 as_of_d 之后的多 horizon 表现
                mh = evaluate_multi(code, as_of_d, hold_days_list, args.win_pct, tdx_dir)
                rec = {
                    "as_of_date": as_of_d.isoformat(),
                    "template": tname,
                    "code": code,
                    "name": row.get("name", ""),
                    "sig_date": row["sig_date"],
                    "template_dist": round(float(row["dist"]), 3),
                    "skipped_reason": mh.get("skipped_reason"),
                    "entry_date": mh.get("entry_date"),
                    "entry_price": mh.get("entry_price"),
                    "horizons": mh.get("horizons", {}),
                }
                all_results.append(rec)

    print(f"[main] ✅ walk-forward 完成 {time.time() - t_start:.0f}s, {len(all_results)} 条记录",
          file=sys.stderr)

    # ─── 写 jsonl ───
    out_dir = ROOT / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = Path(args.output) if args.output else (out_dir / f"q-backtest-fast_{ts}.jsonl")
    with open(out_path, "w", encoding="utf-8") as f:
        for r in all_results:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    print(f"[main] jsonl: {out_path}", file=sys.stderr)

    # ─── 报告 (按 template × hold_days 拆) ───
    print()
    print("=" * 100)
    print(f"q-backtest-fast 报告 ({args.start} ~ {args.end} step={args.step})")
    print(f"hold_days: {hold_days_list} / win_pct: {args.win_pct} / top {args.top}/template")
    print("=" * 100)

    for tname in template_names:
        recs = [r for r in all_results if r["template"] == tname]
        valid = [r for r in recs if r["skipped_reason"] is None]
        if not valid:
            print(f"\n{tname}: 0 valid 信号")
            continue
        skipped_lu = sum(1 for r in recs if r["skipped_reason"] == "limit_up_no_fill")
        print(f"\n{tname}: 样本 {len(valid)} (一字板 skip {skipped_lu})")
        print(f"  hold_days     胜率(ret≥{args.win_pct*100:.0f}%)   持仓体验好 ⭐    平均收益    平均最大回撤")
        for hd in hold_days_list:
            rs = [r for r in valid if hd in r["horizons"] and "reason" not in r["horizons"][hd]]
            if not rs:
                continue
            n = len(rs)
            wins = sum(1 for r in rs if r["horizons"][hd]["win"])
            goods = sum(1 for r in rs if r["horizons"][hd]["good_experience"])
            avg_ret = sum(r["horizons"][hd]["ret"] for r in rs) / n * 100
            avg_dd = sum(r["horizons"][hd]["max_drawdown"] for r in rs) / n * 100
            print(f"  {hd:3d} 天        {wins}/{n} = {wins/n*100:5.1f}%    {goods}/{n} = {goods/n*100:5.1f}%    {avg_ret:+6.2f}%    {avg_dd:+6.2f}%")

    print("\n" + "=" * 100)
    print(f"详细 jsonl: {out_path}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[q-backtest-fast] interrupted", file=sys.stderr); sys.exit(130)
