#!/usr/bin/env python3
"""
精细背景调查 (akshare + baostock + 本地 TDX).
每只候选输出 Markdown 报告:
  K线/庄家成本 / 主营构成 / 十大流通股东 / 股东户数变化 / 4 季财报趋势

用法:
  默认: 跑脚本内置 10 只 CANDIDATES
  --from-csv: 从 logs/similar_to_hongjing.csv 读 TOP N
  --top N: TOP N (默认 30)

输出: logs/deep_analysis.md
"""

import argparse
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import akshare as ak
import baostock as bs
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from core.data_loader import load_daily

OUT_MD = ROOT / "logs" / "deep_analysis.md"
DEFAULT_CSV = ROOT / "logs" / "similar_to_hongjing.csv"


CANDIDATES = [
    ("300054", "鼎龙股份", "🏆 一线 / 算力·半导体抛光"),
    ("002463", "沪电股份", "🏆 一线 / AI 高端 PCB"),
    ("603296", "华勤技术", "🏆 一线 / AI 服务器代工"),
    ("002222", "福晶科技", "🏆 一线 / 光学晶体"),
    ("688300", "联瑞新材", "🏆 一线 / 半导体球硅"),
    ("688297", "中无人机", "🟡 二线 / 太空·无人机"),
    ("301219", "腾远钴业", "🟡 二线 / 钴矿"),
    ("688328", "深科达",   "🟡 二线 / 半导体设备"),
    ("300813", "泰林生物", "🟡 二线 / 微生物检测"),
    ("300138", "晨光生物", "🟡 二线 / 天然提取"),
]


def to_bs(c):
    return f"sh.{c}" if c.startswith(("60", "68", "90")) else f"sz.{c}"


def to_ak(c):
    return f"SH{c}" if c.startswith(("60", "68", "90")) else f"SZ{c}"


def safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        return f"ERR: {type(e).__name__} {str(e)[:80]}"


def fmt_yi(v):
    try:
        return f"{float(v)/1e8:.1f} 亿"
    except Exception:
        return str(v)


def section_kline_cost(code, lines):
    """用本地 TDX 数据算 60/120 日 VWAP (估庄家成本) + 距高点 / 累计涨幅."""
    try:
        df = load_daily(code)
    except Exception as e:
        lines.append(f"\n**K线/成本**: 加载失败 {e}\n")
        return
    if df.empty or len(df) < 30:
        lines.append("\n**K线/成本**: 数据不足\n")
        return

    df = df.sort_values("date").reset_index(drop=True)
    last = df.iloc[-1]
    lc = float(last["close"])
    ld = last["date"].strftime("%Y-%m-%d") if hasattr(last["date"], "strftime") else str(last["date"])

    def vwap(sub):
        v = sub["volume"].values.astype(float)
        c = sub["close"].values.astype(float)
        return (c * v).sum() / v.sum() if v.sum() > 0 else None

    df60 = df.tail(60)
    df120 = df.tail(120) if len(df) >= 120 else df
    df30 = df.tail(30)
    df20 = df.tail(20)

    vw60 = vwap(df60)
    vw120 = vwap(df120) if len(df) >= 120 else None
    vw30 = vwap(df30)
    vw20 = vwap(df20)

    h60 = float(df60["high"].max())
    l60 = float(df60["low"].min())
    h120 = float(df120["high"].max())
    l120 = float(df120["low"].min())

    ret60 = lc / float(df60["close"].iloc[0]) - 1
    ret120 = lc / float(df120["close"].iloc[0]) - 1 if len(df) >= 120 else None
    ret20 = lc / float(df20["close"].iloc[0]) - 1

    def pct(x):
        return f"{x*100:+.1f}%" if x is not None else "?"

    lines.append("\n**K线/庄家成本估算**:\n")
    lines.append(f"- 当前价 ({ld}): **{lc:.2f}**\n")
    if vw20: lines.append(f"- 20 日 VWAP: {vw20:.2f}  → 现价偏离 {pct(lc/vw20-1)}\n")
    if vw60: lines.append(f"- **60 日 VWAP (≈短期庄家成本): {vw60:.2f}**  → 现价偏离 **{pct(lc/vw60-1)}**\n")
    if vw120: lines.append(f"- **120 日 VWAP (≈中期庄家成本): {vw120:.2f}**  → 现价偏离 **{pct(lc/vw120-1)}**\n")
    lines.append(f"- 60 日 high {h60:.2f} / low {l60:.2f}  → 距高 {pct(lc/h60-1)} / 距低 {pct(lc/l60-1)}\n")
    if len(df) >= 120:
        lines.append(f"- 120 日 high {h120:.2f} / low {l120:.2f}  → 距高 {pct(lc/h120-1)} / 距低 {pct(lc/l120-1)}\n")
    lines.append(f"- 累计涨幅: 20 日 {pct(ret20)} / 60 日 {pct(ret60)}" + (f" / 120 日 {pct(ret120)}" if ret120 is not None else "") + "\n")

    # 简单评估
    if vw60:
        prem = lc / vw60 - 1
        if prem > 0.40:
            tag = "🔴 **现价已远超短期庄家成本 (>40%)**, 散户接盘风险高"
        elif prem > 0.20:
            tag = "🟡 现价偏高 (20-40%), 进入获利兑现区"
        elif prem > 0.05:
            tag = "🟢 现价小幅高于成本 (5-20%), 健康"
        elif prem > -0.05:
            tag = "🟢 接近成本, 安全"
        else:
            tag = "⚪ 低于成本, 庄家也亏"
        lines.append(f"- 评估: {tag}\n")


def section_basic(code, lines):
    info = safe(ak.stock_individual_info_em, symbol=code)
    if isinstance(info, pd.DataFrame) and not info.empty:
        d = dict(zip(info["item"], info["value"]))
        lines.append(f"**基本**: 总市值 {fmt_yi(d.get('总市值','?'))} | 流通市值 {fmt_yi(d.get('流通市值','?'))} "
                     f"| 行业 {d.get('行业','?')} | 上市 {d.get('上市时间','?')}\n")


def section_main_business(code, lines):
    mb = safe(ak.stock_zygc_em, symbol=to_ak(code))
    if not isinstance(mb, pd.DataFrame) or mb.empty:
        return
    latest = mb["报告日期"].max()
    by_prod = mb[(mb["报告日期"] == latest) & (mb["分类类型"] == "按产品分类")]
    by_ind = mb[(mb["报告日期"] == latest) & (mb["分类类型"] == "按行业分类")]
    target = by_ind if not by_ind.empty else by_prod
    if target.empty:
        target = mb[mb["报告日期"] == latest]

    date_str = latest.strftime("%Y-%m-%d") if hasattr(latest, "strftime") else str(latest)
    lines.append(f"\n**主营构成 ({date_str})**:\n")
    for _, r in target.head(6).iterrows():
        seg = str(r.get("主营构成", ""))[:80]
        pct = r.get("收入比例", None)
        if pd.notna(pct):
            lines.append(f"- {seg} → **{float(pct)*100:.1f}%**\n")


def section_top10_holders(code, lines):
    for date in ("20251231", "20250930", "20250630"):
        h = safe(ak.stock_gdfx_free_top_10_em, symbol=to_ak(code), date=date)
        if isinstance(h, pd.DataFrame) and not h.empty:
            lines.append(f"\n**前 10 流通股东 ({date[:4]}-{date[4:6]}-{date[6:]})**:\n")
            for _, r in h.iterrows():
                n = str(r.get("股东名称", "?"))[:50]
                pct = r.get("占总流通股本持股比例", "?")
                chg = r.get("增减", "")
                rate = r.get("变动比率", None)
                rate_str = f" ({float(rate):+.1f}%)" if pd.notna(rate) else ""
                if chg == "新进":
                    chg_str = " 🆕**新进**"
                elif chg == "不变":
                    chg_str = " 不变"
                elif isinstance(chg, str) and chg.replace("-", "").replace(".", "").isdigit():
                    chg_str = f" ({chg}{rate_str})"
                else:
                    chg_str = f" {chg}{rate_str}"
                pct_str = f"{float(pct):.2f}%" if isinstance(pct, (int, float)) else str(pct)
                lines.append(f"- {n}: **{pct_str}**{chg_str}\n")
            return
    lines.append("\n**前 10 流通股东**: 拉取失败\n")


def section_holder_count(code, lines):
    df = safe(ak.stock_zh_a_gdhs_detail_em, symbol=code)
    if isinstance(df, pd.DataFrame) and not df.empty:
        latest = df.tail(8).copy()
        lines.append("\n**股东户数变化 (最近 8 期)**:\n\n")
        lines.append("| 日期 | 户数 | 较上期 | 户均市值 | 区间涨跌 |\n|---|---:|---:|---:|---:|\n")
        for _, r in latest.iterrows():
            d = r.get("股东户数统计截止日", "?")
            if hasattr(d, "strftime"):
                d = d.strftime("%Y-%m-%d")
            n_now = r.get("股东户数-本次", 0)
            chg_pct = r.get("股东户数-增减比例", 0)
            mv = r.get("户均持股市值", 0)
            ret = r.get("区间涨跌幅", 0)
            lines.append(f"| {d} | {int(n_now):,} | {float(chg_pct):+.1f}% | {float(mv)/1e4:.1f} 万 | {float(ret):+.1f}% |\n")


def section_baostock_quarters(code, lines):
    bsc = to_bs(code)
    rows = []
    for y, q in [(2026, 1), (2025, 4), (2025, 3), (2025, 2), (2025, 1), (2024, 4), (2024, 3)]:
        rs = bs.query_growth_data(code=bsc, year=y, quarter=q)
        gdf = rs.get_data()
        if not gdf.empty:
            g = gdf.iloc[-1]
            rs2 = bs.query_profit_data(code=bsc, year=y, quarter=q)
            pdf = rs2.get_data()
            p = pdf.iloc[-1] if not pdf.empty else {}
            try:
                yoyni = float(g.get("YOYNI", 0)) * 100
            except: yoyni = None
            try:
                npm = float(p.get("npMargin", 0)) * 100
            except: npm = None
            try:
                gp = float(p.get("gpMargin", 0)) * 100
            except: gp = None
            try:
                roe = float(p.get("roeAvg", 0)) * 100
            except: roe = None
            try:
                np_yi = float(p.get("netProfit", 0)) / 1e8
            except: np_yi = None
            rows.append((f"{y}Q{q}", npm, gp, roe, np_yi, yoyni))
    if rows:
        lines.append("\n**财报季度趋势 (baostock)**:\n\n")
        lines.append("| 季度 | 净利率% | 毛利率% | ROE% | 净利润(亿) | 净利YoY% |\n|---|---:|---:|---:|---:|---:|\n")
        for q, npm, gp, roe, np_yi, yoyni in rows:
            f = lambda v: f"{v:.2f}" if v is not None else "?"
            lines.append(f"| {q} | {f(npm)} | {f(gp)} | {f(roe)} | {f(np_yi)} | {f(yoyni)} |\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-csv", nargs="?", const=str(DEFAULT_CSV), default=None)
    ap.add_argument("--top", type=int, default=30)
    args = ap.parse_args()

    out_md = OUT_MD
    if args.from_csv:
        path = Path(args.from_csv)
        df = pd.read_csv(path, dtype={"code": str})
        df = df.sort_values("dist").head(args.top)
        cands = []
        for _, r in df.iterrows():
            tag = f"#{int(_)+1} dist={r['dist']:.2f}, sig={r['sig_date']}, entry={r['entry']:.2f}"
            cands.append((r["code"], r["name"], tag))
        # 输出文件按输入推导: similar_to_hongjing.csv -> deep_analysis_hongjing.md
        suffix = path.stem.replace("similar_to_", "")
        out_md = ROOT / "logs" / f"deep_analysis_{suffix}.md"
        print(f"从 {path} 读 TOP {args.top} 候选 → 输出 {out_md.name}")
    else:
        cands = CANDIDATES

    print("baostock login ...")
    bs.login()

    lines = []
    lines.append("# 候选股精细背景调查\n")
    lines.append(f"生成: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}\n")

    for i, (code, name, tag) in enumerate(cands, 1):
        print(f"\n>>> [{i}/{len(cands)}] {code} {name}")
        lines.append(f"\n---\n\n# {code} {name} {tag}\n")
        try:
            section_kline_cost(code, lines)
            section_basic(code, lines)
            section_main_business(code, lines)
            section_top10_holders(code, lines)
            section_holder_count(code, lines)
            section_baostock_quarters(code, lines)
        except Exception as e:
            lines.append(f"\n**ERROR**: {type(e).__name__} {e}\n")

    bs.logout()
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("".join(lines), encoding="utf-8")
    print(f"\n写入 {out_md} ({out_md.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
