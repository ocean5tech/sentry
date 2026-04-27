#!/usr/bin/env python3
"""
扫 hongjing+litong TOP 200, 找近 90 天公告标题含"芯片/AI/算力/机器人"等热门关键词的票.
排除控制权变更类 (单独追踪) 和 60 日 +50% 以上 (已爆涨).
"""

import sys
import warnings
from datetime import datetime, timedelta
from pathlib import Path

warnings.filterwarnings("ignore")

import akshare as ak
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from core.data_loader import load_daily

HOT_KW = [
    "芯片", "半导体", "集成电路", "GPU", "DPU", "NPU", "ASIC", "FPGA",
    "AI", "人工智能", "大模型", "AIGC",
    "算力", "智算", "数据中心", "推理", "训练",
    "机器人", "具身智能", "人形", "灵巧手", "减速器",
    "存储", "HBM", "DDR",
    "光模块", "硅光",
    "智能驾驶", "L3", "L4",
    "SiC", "GaN", "IGBT",
    "卫星", "商业航天",
    "脑机", "BCI",
    "创新药", "ADC", "PD-1", "GLP-1",
]

EXCLUDE_KW = [
    "控制权变更", "实际控制人变更", "协议转让", "要约收购",
    "实控人", "易主", "控股股东变更",
]


def to_ak(c):
    return f"SH{c}" if c.startswith(("60", "68", "90")) else f"SZ{c}"


def fetch_announces(code, days=90):
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    try:
        df = ak.stock_zh_a_disclosure_report_cninfo(
            symbol=code, market="沪深京", category="",
            start_date=start, end_date=end)
        if isinstance(df, pd.DataFrame) and not df.empty:
            return df
    except Exception:
        pass
    return None


def calc_60d_ret(code):
    try:
        df = load_daily(code).sort_values("date").reset_index(drop=True)
        if len(df) < 60:
            return None
        return (df["close"].iloc[-1] / df["close"].iloc[-60] - 1) * 100
    except Exception:
        return None


def main():
    hj = pd.read_csv(ROOT / "logs/similar_to_hongjing.csv", dtype={"code": str})
    lt = pd.read_csv(ROOT / "logs/similar_to_litong.csv", dtype={"code": str})
    hj200 = hj.sort_values("dist").head(200)[["code", "name", "dist"]].rename(columns={"dist": "dist_h"})
    lt200 = lt.sort_values("dist").head(200)[["code", "name", "dist"]].rename(columns={"dist": "dist_l"})
    union = pd.merge(hj200, lt200, on=["code", "name"], how="outer")
    union["min_dist"] = union[["dist_h", "dist_l"]].min(axis=1)
    union = union.sort_values("min_dist").reset_index(drop=True)
    print(f"Union TOP 200: {len(union)} 只")

    results = []
    for i, r in union.iterrows():
        code = r["code"]; name = r["name"]
        if i % 30 == 0:
            print(f"  {i}/{len(union)}  hits={len(results)}")
        ret60 = calc_60d_ret(code)
        if ret60 is None or ret60 > 50:
            continue
        ann = fetch_announces(code, 90)
        if ann is None: continue
        if ann["公告标题"].str.contains("|".join(EXCLUDE_KW), na=False).any():
            continue
        # 找热门关键词
        ann["hot_match"] = ann["公告标题"].str.findall("|".join(HOT_KW))
        hot_ann = ann[ann["hot_match"].apply(lambda x: len(x) > 0)]
        if len(hot_ann) == 0: continue

        # 提取所有命中的关键词 (去重)
        kws_set = set()
        for lst in hot_ann["hot_match"]:
            for kw in lst:
                kws_set.add(kw)

        results.append({
            "code": code, "name": name,
            "min_dist": r["min_dist"],
            "60日涨%": ret60,
            "热门公告数": len(hot_ann),
            "命中关键词": ",".join(sorted(kws_set)),
            "样本标题": " ||| ".join(hot_ann.head(5)["公告标题"].astype(str).str[:80]),
        })

    if not results:
        print("\n无 hits"); return

    out = pd.DataFrame(results).sort_values(["热门公告数", "min_dist"], ascending=[False, True])
    print(f"\n=== 命中热门关键词 (非控制权变更, 60日<+50%): {len(out)} 只 ===\n")
    for _, r in out.iterrows():
        print(f"🔥 {r['code']} {r['name']:8s}  dist={r['min_dist']:.2f}  60日{r['60日涨%']:+5.1f}%  "
              f"公告{int(r['热门公告数'])}  关键词: {r['命中关键词']}")
        print(f"   📋 {r['样本标题'][:300]}")
        print()

    out.to_csv(ROOT / "logs/hot_keyword_candidates.csv", index=False, encoding="utf-8-sig")
    print(f"已写入 logs/hot_keyword_candidates.csv")


if __name__ == "__main__":
    main()
