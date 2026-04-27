#!/usr/bin/env python3
"""从 deep_analysis.md 提取关键字段, 生成 TOP 50 总览表."""
import re
from pathlib import Path
import pandas as pd

MD = Path("/home/wyatt/sentry/quant/logs/deep_analysis.md")
text = MD.read_text(encoding="utf-8")

# 按 # 切分每只股票段落
sections = re.split(r"\n# (\d{6}) (\S+)", text)
# sections[0] = 头部, 然后 [code, name, body, code, name, body, ...]

rows = []
for i in range(1, len(sections) - 2, 3):
    code = sections[i]
    name = sections[i + 1].split()[0]
    body = sections[i + 2]

    # 排名 / dist / sig_date / entry
    m = re.search(r"#(\d+) dist=([\d.]+), sig=([\d-]+), entry=([\d.]+)", body)
    rank = int(m.group(1)) if m else None
    dist = float(m.group(2)) if m else None
    sig_date = m.group(3) if m else ""
    entry = float(m.group(4)) if m else None

    # 当前价
    m = re.search(r"当前价 \(([\d-]+)\): \*\*([\d.]+)\*\*", body)
    last_close = float(m.group(2)) if m else None

    # 60 日 VWAP 偏离
    m = re.search(r"60 日 VWAP.*?现价偏离 \*\*([+-]?[\d.]+)%\*\*", body)
    vwap60_dev = float(m.group(1)) if m else None

    # 120 日 VWAP 偏离
    m = re.search(r"120 日 VWAP.*?现价偏离 ([+-]?[\d.]+)%", body)
    vwap120_dev = float(m.group(1)) if m else None

    # 距 60 日 high
    m = re.search(r"60 日 high.*?距高 ([+-]?[\d.]+)%", body)
    dist_high60 = float(m.group(1)) if m else None

    # 60 日累计涨幅
    m = re.search(r"60 日 ([+-][\d.]+)%", body)
    ret60 = float(m.group(1)) if m else None

    # 估算评估 emoji
    eval_tag = ""
    for emo, kw in [("🔴", "🔴"), ("🟡", "🟡"), ("🟢", "🟢"), ("⚪", "⚪")]:
        if emo in body:
            eval_tag = emo
            break

    # 总市值
    m = re.search(r"总市值 ([\d.]+) 亿", body)
    mcap = float(m.group(1)) if m else None

    # 行业
    m = re.search(r"行业 (\S+) \|", body)
    industry = m.group(1) if m else ""

    # 主营 第一项 (最大占比)
    m = re.search(r"主营构成 \([\d-]+\)\*\*:\n- ([^→]+) → \*\*([\d.]+)%\*\*", body)
    main_biz = m.group(1).strip()[:40] if m else ""
    main_pct = float(m.group(2)) if m else None

    # 新进股东数量
    new_holders = body.count("🆕**新进**")

    # 最新季净利率
    m = re.search(r"\| (\d{4}Q\d) \| ([\d.-]+) \|", body)
    latest_qtr = m.group(1) if m else ""
    latest_npm = float(m.group(2)) if m else None

    # 最新季 YoY
    m = re.search(r"\| \d{4}Q\d \| [\d.-]+ \| [\d.-]+ \| [\d.-]+ \| [\d.-]+ \| ([+-]?[\d.]+) \|", body)
    latest_yoy = float(m.group(1)) if m else None

    # 股东户数最新两期变化趋势
    holder_changes = re.findall(r"\| ([\d.+-]+)% \| ([\d.+-]+) 万 \|", body)
    last_holder_chg = float(holder_changes[-1][0]) if holder_changes else None

    rows.append({
        "排名": rank,
        "代码": code,
        "名称": name,
        "市值(亿)": mcap,
        "现价": last_close,
        "VWAP60偏离%": vwap60_dev,
        "距60高%": dist_high60,
        "60日涨幅%": ret60,
        "评估": eval_tag,
        "净利率%": latest_npm,
        "净利YoY%": latest_yoy,
        "新进股东": new_holders,
        "户数末期变化%": last_holder_chg,
        "主营": f"{main_biz} ({main_pct}%)" if main_pct else "",
    })

df = pd.DataFrame(rows).sort_values("排名")
print(df.to_string(index=False))
df.to_csv("/home/wyatt/sentry/quant/logs/top50_summary.csv", index=False, encoding="utf-8-sig")
print(f"\n写入 logs/top50_summary.csv")
