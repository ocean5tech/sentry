"""Layer 2 公告深挖. akshare stock_zh_a_disclosure_report_cninfo + 关键词分类."""

import re
from datetime import date, datetime
from typing import Any


def _classify(title: str, kw_cfg: dict) -> tuple[str | None, list[str], list[str]]:
    """对单条公告标题分类 (event_category, hot_tags 命中, pivot 命中)."""
    matched_event = None
    pivot_hits = []
    hot_tag_hits = []

    for cat_name, cfg in kw_cfg.get("events", {}).items():
        for pat in cfg.get("patterns", []):
            if pat in title:
                if matched_event is None or cfg.get("severity", 0) > 0:
                    matched_event = cat_name
                if cat_name == "主业转型" or cat_name == "跨界布局":
                    pivot_hits.append(pat)

    for tag, kws in kw_cfg.get("hot_tags", {}).items():
        for kw in kws:
            if kw in title and tag not in hot_tag_hits:
                hot_tag_hits.append(tag)

    return matched_event, pivot_hits, hot_tag_hits


def _is_noise(title: str, kw_cfg: dict) -> bool:
    for n in kw_cfg.get("exclusions", {}).get("noise_titles", []):
        if n in title:
            return True
    return False


def fetch_announcements(code: str, since: str, until: str, ak_module, cache) -> list[dict]:
    """拉取 [since, until] 区间公告. 返回 list of {date, title, category, url}."""
    def _do():
        try:
            df = ak_module.stock_zh_a_disclosure_report_cninfo(
                symbol=code,
                start_date=since.replace("-", ""),
                end_date=until.replace("-", ""),
            )
        except Exception as e:
            return {"_err": f"{type(e).__name__}: {e}", "rows": []}
        if df is None or df.empty:
            return {"rows": []}
        # 兼容字段名 (akshare 可能调整)
        rows = []
        title_col = next((c for c in ["公告标题", "标题", "title"] if c in df.columns), None)
        date_col = next((c for c in ["公告时间", "公告日期", "date"] if c in df.columns), None)
        url_col = next((c for c in ["公告链接", "链接", "url"] if c in df.columns), None)
        if not title_col or not date_col:
            return {"_err": "missing title/date cols", "rows": []}
        for _, r in df.iterrows():
            d = str(r[date_col])[:10]
            rows.append({
                "date": d,
                "title": str(r[title_col]),
                "url": str(r[url_col]) if url_col else "",
            })
        return {"rows": rows}
    return cache.get_or_set("announcements", (code, since, until), _do)


def analyze(code: str, since: str, until: str, kw_cfg: dict, ak_module, cache) -> dict:
    """对 code 跑公告分析, 返回 announcements_90d schema."""
    raw = fetch_announcements(code, since, until, ak_module, cache)
    rows = raw.get("rows", [])
    err = raw.get("_err")

    by_category: dict[str, int] = {}
    pivot_keywords_hit: list[str] = []
    hot_keywords_hit: list[str] = []
    key_titles: list[dict] = []
    risk_flags: list[str] = []

    for ann in rows:
        if _is_noise(ann["title"], kw_cfg):
            continue
        cat, pivots, hots = _classify(ann["title"], kw_cfg)
        cat_key = cat or "其他"
        by_category[cat_key] = by_category.get(cat_key, 0) + 1
        for p in pivots:
            if p not in pivot_keywords_hit:
                pivot_keywords_hit.append(p)
        for h in hots:
            if h not in hot_keywords_hit:
                hot_keywords_hit.append(h)
        if cat and cat in ("控制权变更", "借壳重组", "主业转型"):
            key_titles.append({"date": ann["date"], "title": ann["title"], "category": cat})

        # risk flags
        title_low = ann["title"]
        if "限售" in title_low and "解禁" in title_low:
            if "限售解禁" not in risk_flags:
                risk_flags.append("限售解禁")
        if "质押" in title_low or "股权质押" in title_low:
            if "控股股东质押" not in risk_flags:
                risk_flags.append("控股股东质押")
        if "异常波动" in title_low:
            if "异常波动警示" not in risk_flags:
                risk_flags.append("异常波动警示")

    # 限制 key_titles 数量, 避免 schema 过大
    key_titles = sorted(key_titles, key=lambda x: x["date"], reverse=True)[:10]

    return {
        "total": len(rows),
        "window": {"since": since, "until": until},
        "by_category": by_category,
        "pivot_keywords_hit": pivot_keywords_hit,
        "hot_keywords_hit": hot_keywords_hit,
        "key_titles": key_titles,
        "risk_flags": risk_flags,
        "_err": err,
    }
