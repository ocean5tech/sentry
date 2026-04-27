"""Layer 1 触发检测.

V0 设计: input mode (`--input` 或 stdin pipe) 主流用法下, 不做全市场预筛 (慢/低效).
对给定 code, 从已拉的 announcements + shareholders 数据派生 layer1_triggers 字段.

全市场扫 (q-fin 裸跑) 留到 V1 优化 (可加并发 / 增量 / 倒排索引).
"""


def derive(announcements: dict, shareholders: dict, kw_cfg: dict) -> dict:
    """从 Layer 2 拉到的数据派生 layer1_triggers schema."""
    matched_events = []
    severity_sum = 0

    # 从 announcements key_titles 提取触发事件
    for kt in announcements.get("key_titles", []):
        cat = kt.get("category", "其他")
        sev = kw_cfg.get("events", {}).get(cat, {}).get("severity", 0)
        # 推断哪个 keyword 命中 (取该分类第一个出现的 pattern)
        title = kt.get("title", "")
        kw_hit = None
        for pat in kw_cfg.get("events", {}).get(cat, {}).get("patterns", []):
            if pat in title:
                kw_hit = pat
                break
        matched_events.append({
            "category": cat,
            "keyword": kw_hit,
            "source": "akshare",
            "ann_date": kt.get("date", ""),
            "title": title,
        })
        severity_sum += sev

    # 股东大额新进信号
    major = shareholders.get("major_new_entry")
    shareholder_sig = None
    if major:
        shareholder_sig = {
            "new_entry_pct": major.get("pct"),
            "entity_type": major.get("type"),
            "entity_name": major.get("name"),
            "triggered_rule": "first_new_entry_pct_10" if major.get("pct", 0) >= 10 else "large_new_entry_pct_5",
        }
        severity_sum += 5 if major.get("pct", 0) >= 10 else 3

    return {
        "matched_events": matched_events[:10],   # 最多 10 条避免 schema 过大
        "matched_shareholder_signal": shareholder_sig,
        "severity_sum": severity_sum,
    }
