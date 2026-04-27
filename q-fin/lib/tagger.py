"""8 方向 tag. 从 announcements 命中的 hot_keywords + fundamentals.industry 推断."""


def derive_tags(announcements: dict, kw_cfg: dict, industry: str = "") -> list[str]:
    """返回 tag 列表 (用户 8 方向命中的)."""
    tags = list(announcements.get("hot_keywords_hit", []))
    # industry 推断 (粗略)
    industry_lower = (industry or "").lower()
    industry_map = {
        "AI": ["计算机", "软件", "电子"],
        "算力": ["互联网", "云计算", "信息技术"],
        "半导体": ["半导体", "集成电路", "电子元器件"],
        "稀土": ["稀有金属"],
        "贵金属": ["黄金"],
        "创新药": ["医药", "生物医药"],
        "机器人": ["专用设备", "工业机器"],
    }
    for tag, kws in industry_map.items():
        if any(k in industry for k in kws) and tag not in tags:
            tags.append(tag)
    return tags
