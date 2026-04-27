"""Layer 2 概念/业务变化推断. 从 announcements 派生."""


def derive(announcements: dict) -> dict:
    """从 announcements_90d schema 推断 concept_status."""
    titles = announcements.get("key_titles", [])
    pivots = announcements.get("pivot_keywords_hit", [])
    hots = announcements.get("hot_keywords_hit", [])

    name_changed = any("名称变更" in t["title"] or "证券简称变更" in t["title"] for t in titles)
    name_change_titles = [t for t in titles if "名称变更" in t["title"]]

    scope_change_titles = [t for t in titles if "经营范围" in t["title"]]
    scope_change_recent = len(scope_change_titles) > 0
    scope_change_date = scope_change_titles[0]["date"] if scope_change_titles else None

    is_pivot = scope_change_recent or len(pivots) >= 2 or name_changed

    return {
        "name_changed": name_changed,
        "name_change_history": [t["date"] for t in name_change_titles],
        "scope_change_recent": scope_change_recent,
        "scope_change_date": scope_change_date,
        "new_business_keywords": hots,
        "is_pivot_story": is_pivot,
    }
