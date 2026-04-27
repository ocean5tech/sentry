#!/usr/bin/env python3
"""
拉候选股的基本面 (baostock): 行业 / 最近季净利率毛利率ROE / 净利润YoY / 扣非YoY.

用法:
  默认: 用脚本内置 13 只人工精选 CANDIDATES
  --from-csv [path]: 从 logs/similar_to_hongjing.csv 读 TOP N 行
  --top N: 取前 N 行 (默认 20)

输出: logs/candidate_fundamentals.csv
"""

import argparse
import sys
from pathlib import Path

import baostock as bs
import pandas as pd

ROOT = Path(__file__).parent.parent
OUT_CSV = ROOT / "logs" / "candidate_fundamentals.csv"
DEFAULT_CSV = ROOT / "logs" / "similar_to_hongjing.csv"


CANDIDATES = {
    # 算力 / AI / 半导体
    "300054": ("鼎龙股份", "算力/半导体抛光"),
    "603296": ("华勤技术", "AI 服务器代工"),
    "002463": ("沪电股份", "AI 高端 PCB"),
    "300469": ("信息发展", "数字身份/数据"),
    "688300": ("联瑞新材", "半导体球硅"),
    "002222": ("福晶科技", "光学晶体/光模块"),
    "688328": ("深科达", "半导体设备"),
    "003043": ("华亚智能", "半导体设备结构件"),
    # 太空 / 无人机
    "688297": ("中无人机", "太空/无人机"),
    # 创新药 / 医药 (TOP 120 内)
    "300813": ("泰林生物", "微生物检测"),
    "300683": ("海特生物", "化学药"),
    "300138": ("晨光生物", "天然提取"),
    # 矿产
    "301219": ("腾远钴业", "钴/矿产"),
}


def to_bs(c):
    if c.startswith(("60", "68", "90")):
        return f"sh.{c}"
    return f"sz.{c}"


def safe_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fetch_one(code, label, theme):
    bsc = to_bs(code)
    row = {"code": code, "name": label, "我标注的题材": theme}

    # 行业 (申万)
    rs = bs.query_stock_industry(code=bsc)
    ind = rs.get_data()
    if not ind.empty:
        row["证监会行业"] = ind.iloc[0].get("industry", "")
        row["证监会分类"] = ind.iloc[0].get("industryClassification", "")

    # 最新季度财务 (尝试 2026Q1 → 2025Q4 → 2025Q3)
    for y, q in [(2026, 1), (2025, 4), (2025, 3), (2025, 2)]:
        rs = bs.query_profit_data(code=bsc, year=y, quarter=q)
        pdf = rs.get_data()
        if not pdf.empty:
            p = pdf.iloc[-1]
            row["报告期"] = f"{y}Q{q}"
            row["净利率%"] = (safe_float(p.get("npMargin")) or 0) * 100
            row["毛利率%"] = (safe_float(p.get("gpMargin")) or 0) * 100
            row["ROE%"] = (safe_float(p.get("roeAvg")) or 0) * 100
            np_v = safe_float(p.get("netProfit"))
            row["净利润(亿)"] = np_v / 1e8 if np_v is not None else None
            row["EPS_TTM"] = safe_float(p.get("epsTTM"))
            break

    # 同比增长
    for y, q in [(2026, 1), (2025, 4), (2025, 3), (2025, 2)]:
        rs = bs.query_growth_data(code=bsc, year=y, quarter=q)
        gdf = rs.get_data()
        if not gdf.empty:
            g = gdf.iloc[-1]
            row["增长报告期"] = f"{y}Q{q}"
            yoyni = safe_float(g.get("YOYNI"))
            yoypni = safe_float(g.get("YOYPNI"))
            yoyeq = safe_float(g.get("YOYEquity"))
            row["净利润YoY%"] = yoyni * 100 if yoyni is not None else None
            row["扣非净利YoY%"] = yoypni * 100 if yoypni is not None else None
            row["净资产YoY%"] = yoyeq * 100 if yoyeq is not None else None
            break

    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-csv", nargs="?", const=str(DEFAULT_CSV), default=None,
                    help="从 CSV 读候选 (默认 logs/similar_to_hongjing.csv)")
    ap.add_argument("--top", type=int, default=20, help="--from-csv 时取 TOP N (默认 20)")
    args = ap.parse_args()

    out_csv = OUT_CSV
    if args.from_csv:
        path = Path(args.from_csv)
        df = pd.read_csv(path, dtype={"code": str})
        df = df.sort_values("dist").head(args.top)
        cands = {row["code"]: (row["name"], f"dist={row['dist']:.2f}, sig={row['sig_date']}, entry={row['entry']:.2f}")
                 for _, row in df.iterrows()}
        # 输出文件名跟输入对应: similar_to_hongjing.csv -> fundamentals_hongjing.csv
        suffix = path.stem.replace("similar_to_", "")
        out_csv = ROOT / "logs" / f"fundamentals_{suffix}.csv"
        print(f"从 {path} 读 TOP {args.top} 候选 → 输出 {out_csv.name}")
    else:
        cands = CANDIDATES

    print("baostock login ...")
    bs.login()
    rows = []
    for code, (name, theme) in cands.items():
        print(f"  拉 {code} {name} ...")
        try:
            rows.append(fetch_one(code, name, theme))
        except Exception as e:
            print(f"    fail: {e}")
            rows.append({"code": code, "name": name, "error": str(e)})
    bs.logout()

    df = pd.DataFrame(rows)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")

    show_cols = ["code", "name", "我标注的题材", "证监会行业", "报告期",
                 "净利率%", "毛利率%", "ROE%", "净利润(亿)",
                 "净利润YoY%", "扣非净利YoY%"]
    show_cols = [c for c in show_cols if c in df.columns]
    print("\n=== 候选基本面 ===")
    print(df[show_cols].to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    print(f"\n已写入 {out_csv}")


if __name__ == "__main__":
    main()
