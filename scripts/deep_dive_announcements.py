#!/usr/bin/env python3
"""
深挖指定股票的近期公告 + 概念归属.
输入: 股票代码 list
输出: logs/deep_dive_<日期>.md
"""

import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path

warnings.filterwarnings("ignore")

import akshare as ak
import pandas as pd

ROOT = Path(__file__).parent.parent
OUT_MD = ROOT / "logs" / f"deep_dive_announce.md"


CANDIDATES = [
    ("603296", "华勤技术"),
    ("301668", "昊创瑞通"),
    ("603175", "超颖电子"),
    ("300850", "新强联"),
    ("300571", "平治信息"),
    ("300037", "新宙邦"),
]


def safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        return f"ERR {type(e).__name__}: {str(e)[:100]}"


def fetch_announces(code, days_back=90):
    """拉最近 N 天公告标题."""
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days_back)).strftime("%Y%m%d")
    df = safe(ak.stock_zh_a_disclosure_report_cninfo,
              symbol=code, market="沪深京", category="",
              start_date=start, end_date=end)
    if isinstance(df, pd.DataFrame) and not df.empty:
        return df
    return None


def categorize(title):
    """简单关键词分类公告."""
    t = title or ""
    if any(k in t for k in ["业绩预告", "业绩快报", "业绩说明", "年度报告", "季度报告"]):
        return "📊 业绩"
    if any(k in t for k in ["减持", "增持"]):
        return "💰 股东买卖"
    if any(k in t for k in ["质押", "解除质押"]):
        return "🔒 股权质押"
    if any(k in t for k in ["定增", "非公开发行", "可转债", "募集资金", "定向增发"]):
        return "💵 募资"
    if any(k in t for k in ["收购", "重组", "出售", "资产", "并购"]):
        return "🔄 资产重组"
    if any(k in t for k in ["合同", "中标", "订单"]):
        return "📝 合同/订单"
    if any(k in t for k in ["回购"]):
        return "🔁 回购"
    if any(k in t for k in ["激励", "员工持股"]):
        return "🎯 激励/员工持股"
    if any(k in t for k in ["分红", "派息", "权益分派"]):
        return "🎁 分红"
    if any(k in t for k in ["关联交易", "诉讼", "处罚", "异常"]):
        return "⚠️ 异常事项"
    if any(k in t for k in ["H 股", "H股", "境外"]):
        return "🌏 境外上市"
    if "更名" in t or "证券简称" in t:
        return "✏️ 更名"
    return "ℹ️ 其他"


def find_concepts(code, name):
    """通过股票名/主营反查热点概念 (业务匹配, 不是 API 查询)."""
    # 这里只能用启发式: 名字关键词 + 用户已知行业知识
    # 真正的概念需要付费同花顺或定期爬虫
    return ""


def main():
    lines = []
    lines.append("# 深挖 - 公告与概念调研\n")
    lines.append(f"生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

    for code, name in CANDIDATES:
        print(f">>> {code} {name}")
        lines.append(f"\n---\n\n# {code} {name}\n")

        # 主营 (复用 zygc)
        mb = safe(ak.stock_zygc_em, symbol=("SH" if code.startswith(("60","68")) else "SZ") + code)
        if isinstance(mb, pd.DataFrame) and not mb.empty:
            latest = mb["报告日期"].max()
            t = mb[(mb["报告日期"] == latest) & (mb["分类类型"] == "按产品分类")]
            if t.empty:
                t = mb[mb["报告日期"] == latest]
            d = latest.strftime("%Y-%m-%d") if hasattr(latest, "strftime") else str(latest)
            lines.append(f"\n**主营 ({d})**:\n")
            for _, r in t.head(5).iterrows():
                seg = str(r.get("主营构成", ""))[:80]
                pct = r.get("收入比例")
                if pd.notna(pct):
                    lines.append(f"- {seg} → **{float(pct)*100:.1f}%**\n")

        # 公告 (近 90 天)
        ann = fetch_announces(code, days_back=90)
        if ann is not None:
            lines.append(f"\n**近 90 天公告 (共 {len(ann)} 条, 按类别分组)**:\n")
            ann["分类"] = ann["公告标题"].apply(categorize)
            grp = ann.groupby("分类")
            # 按重要性顺序
            order = ["🔄 资产重组", "📝 合同/订单", "💰 股东买卖", "📊 业绩",
                     "💵 募资", "🔒 股权质押", "🎁 分红", "🎯 激励/员工持股",
                     "🔁 回购", "⚠️ 异常事项", "🌏 境外上市", "✏️ 更名", "ℹ️ 其他"]
            for cat in order:
                if cat in grp.groups:
                    sub = grp.get_group(cat).sort_values("公告时间", ascending=False)
                    lines.append(f"\n**{cat}** ({len(sub)} 条):\n")
                    for _, r in sub.head(8).iterrows():
                        d = r.get("公告时间", "?")
                        d = d.strftime("%m-%d") if hasattr(d, "strftime") else str(d)[:10]
                        title = str(r.get("公告标题", ""))[:90]
                        lines.append(f"- {d}: {title}\n")
        else:
            lines.append("\n**公告**: 拉取失败\n")

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("".join(lines), encoding="utf-8")
    print(f"\n写入 {OUT_MD} ({OUT_MD.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
