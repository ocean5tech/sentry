#!/usr/bin/env python3
"""
扫描 hongjing+litong 候选, 找"业务方向/范围变更"公告 (非控制权变更),
且 60 日累计涨幅 < 50% (未爆涨).
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

# 业务变更关键词 (要找)
PIVOT_KW = [
    "经营范围", "修订公司章程", "新增业务",
    "对外投资", "设立全资子公司", "拟设立", "拟收购",
    "战略合作", "战略协议", "战略框架",
    "进军", "切入", "布局",
    "证券简称", "公司名称变更", "更名",
    "拟变更", "拟新增", "拓展业务",
    "向特定对象发行", "发行股份购买资产",
]

# 排除 (属于"控制权变更"类)
EXCLUDE_KW = [
    "控制权变更", "实际控制人变更", "协议转让", "要约收购",
    "实控人", "易主", "重大资产重组", "控股股东变更",
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
    """返回 60 日累计涨幅%."""
    try:
        df = load_daily(code).sort_values("date").reset_index(drop=True)
        if len(df) < 60:
            return None
        return (df["close"].iloc[-1] / df["close"].iloc[-60] - 1) * 100
    except Exception:
        return None


def main():
    # 读 union of hongjing + litong TOP 100
    hj = pd.read_csv(ROOT / "logs/similar_to_hongjing.csv", dtype={"code": str})
    lt = pd.read_csv(ROOT / "logs/similar_to_litong.csv", dtype={"code": str})
    hj100 = hj.sort_values("dist").head(100)[["code", "name", "dist"]].rename(columns={"dist": "dist_h"})
    lt100 = lt.sort_values("dist").head(100)[["code", "name", "dist"]].rename(columns={"dist": "dist_l"})
    union = pd.merge(hj100, lt100, on=["code", "name"], how="outer")
    union["min_dist"] = union[["dist_h", "dist_l"]].min(axis=1)
    union = union.sort_values("min_dist").reset_index(drop=True)
    print(f"Union TOP 100: {len(union)} 只")

    results = []
    for i, r in union.iterrows():
        code = r["code"]; name = r["name"]
        if i % 20 == 0:
            print(f"  {i}/{len(union)}  hits={len(results)}")
        # 60 日涨幅过滤
        ret60 = calc_60d_ret(code)
        if ret60 is None or ret60 > 50:
            continue
        ann = fetch_announces(code, 90)
        if ann is None: continue
        # 排除控制权变更
        excl_titles = ann[ann["公告标题"].str.contains("|".join(EXCLUDE_KW), na=False)]
        if len(excl_titles) > 0:
            continue   # 这只属于控制权变更类, 跳过
        # 找业务变更
        pivot_titles = ann[ann["公告标题"].str.contains("|".join(PIVOT_KW), na=False)]
        if len(pivot_titles) == 0: continue
        # 进一步: 标题里含 AI/芯片/机器人/算力 等热门方向
        hot_titles = pivot_titles[pivot_titles["公告标题"].str.contains(
            "AI|人工智能|算力|芯片|半导体|机器人|大模型|算力中心|存储|GPU|服务器", na=False)]
        # 排除单纯"修订公司章程"的(噪音)
        meaningful = pivot_titles[~pivot_titles["公告标题"].isin([
            "修订公司章程的公告", "公司章程修订对照表"])]
        if len(meaningful) == 0: continue

        results.append({
            "code": code, "name": name,
            "min_dist": r["min_dist"],
            "60日涨%": ret60,
            "热门方向公告数": len(hot_titles),
            "业务变更公告数": len(meaningful),
            "样本标题": "; ".join(meaningful.head(3)["公告标题"].astype(str).str[:50]),
            "热门标题": "; ".join(hot_titles.head(3)["公告标题"].astype(str).str[:60]) if len(hot_titles) > 0 else "",
        })

    if not results:
        print("无 hits")
        return

    out = pd.DataFrame(results).sort_values(["热门方向公告数", "min_dist"], ascending=[False, True])
    print(f"\n=== 业务变更候选 (非控制权变更, 60日<+50%): {len(out)} 只 ===\n")
    for _, r in out.iterrows():
        flag = "🔥" if r["热门方向公告数"] > 0 else ""
        print(f"{flag} {r['code']} {r['name']}: dist={r['min_dist']:.2f}, 60日={r['60日涨%']:+.1f}%, "
              f"业务公告 {int(r['业务变更公告数'])}, 热门方向 {int(r['热门方向公告数'])}")
        if r["热门标题"]:
            print(f"   🔥 {r['热门标题'][:200]}")
        print(f"   📋 {r['样本标题'][:200]}")
        print()

    out.to_csv(ROOT / "logs/business_pivot_candidates.csv", index=False, encoding="utf-8-sig")
    print(f"已写入 logs/business_pivot_candidates.csv")


if __name__ == "__main__":
    main()
