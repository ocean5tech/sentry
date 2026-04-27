"""新闻拉取 + 标准化 schema.

数据源:
  - akshare 5 接口: stock_info_global_{cls,em}, stock_info_cjzc_em, news_cctv, news_economic_baidu
  - RSS 5 个 (新华网时政/经济/科技/国际/军事)

标准化输出 (每条 news):
  {
    event_id: str (sha256 of source+title+time, 16 字符)
    source: str (例 "akshare:cls" / "rss:新华网-经济")
    source_weight: float
    title: str
    content: str
    publish_time: ISO 8601 string
    url: str | None
    tags: list[str] (来自 source 配置)
  }
"""

import hashlib
import time
from datetime import datetime, timedelta
from typing import Optional


def _eid(source: str, title: str, ts: str) -> str:
    h = hashlib.sha256(f"{source}|{title}|{ts}".encode()).hexdigest()[:16]
    return f"{source.replace(':', '-')}-{h}"


def _normalize_dt(value) -> str:
    """各 source 时间格式不同, 统一到 ISO 8601 (本地时间)."""
    if value is None:
        return datetime.now().isoformat(timespec="seconds")
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    s = str(value).strip()
    if not s:
        return datetime.now().isoformat(timespec="seconds")
    # 试常见格式
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).isoformat(timespec="seconds")
        except ValueError:
            continue
    # feedparser RFC822 / 其他
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s).isoformat(timespec="seconds")
    except Exception:
        pass
    return s  # 原样返回


def fetch_akshare(source_name: str, func_name: str, args: dict | None, ak_module, cache, source_weight: float = 1.0) -> list[dict]:
    """单 akshare 接口. 失败返回 []."""
    args = args or {}
    if "date" in args and args["date"] == "today":
        args["date"] = datetime.now().strftime("%Y%m%d")

    def _do():
        fn = getattr(ak_module, func_name, None)
        if fn is None:
            return {"_err": f"akshare.{func_name} not exist", "rows": []}
        try:
            df = fn(**args)
        except Exception as e:
            return {"_err": f"{type(e).__name__}: {e}", "rows": []}
        rows = []
        for _, r in df.iterrows():
            rows.append(r.to_dict())
        return {"rows": rows, "_err": None}

    raw = cache.get_or_call(f"news_{func_name}", _do, args)
    if raw.get("_err"):
        return []

    out = []
    for r in raw["rows"]:
        # 各接口字段名不同, 多 fallback
        title = r.get("标题") or r.get("title") or r.get("新闻标题") or ""
        content = r.get("内容") or r.get("摘要") or r.get("content") or r.get("新闻内容") or ""
        time_field = (r.get("发布时间") or r.get("发布日期") or r.get("时间") or r.get("date") or
                      f"{r.get('发布日期','')} {r.get('发布时间','')}".strip() or None)
        url = r.get("链接") or r.get("url") or None

        if not title:
            continue
        ts = _normalize_dt(time_field)
        out.append({
            "event_id": _eid(source_name, title, ts),
            "source": source_name,
            "source_weight": source_weight,
            "title": str(title).strip(),
            "content": str(content).strip(),
            "publish_time": ts,
            "url": str(url).strip() if url else None,
            "tags": [],
        })
    return out


def fetch_rss(feed_cfg: dict, cache, user_agent: str, timeout: int) -> list[dict]:
    """单 RSS feed. 失败返回 []."""
    import feedparser

    name = feed_cfg["name"]
    url = feed_cfg["url"]
    tags = feed_cfg.get("tags", [])
    weight = feed_cfg.get("weight", 1.0)

    def _do():
        try:
            # feedparser 支持 user-agent
            d = feedparser.parse(url, agent=user_agent, request_headers={"timeout": str(timeout)})
            if d.bozo:
                # bozo=1 但 entries 可能仍有数据, 不直接 fail
                if not getattr(d, "entries", None):
                    return {"_err": f"feedparser bozo: {d.bozo_exception}", "rows": []}
            entries = []
            for e in d.entries[:50]:        # 单 feed 最多取 50 条
                entries.append({
                    "title": e.get("title", ""),
                    "summary": e.get("summary", "") or e.get("description", ""),
                    "link": e.get("link", ""),
                    "published": e.get("published", "") or e.get("updated", ""),
                })
            return {"rows": entries, "_err": None}
        except Exception as ex:
            return {"_err": f"{type(ex).__name__}: {ex}", "rows": []}

    raw = cache.get_or_call(f"rss_{name}", _do, url)
    if raw.get("_err"):
        return []

    out = []
    for r in raw["rows"]:
        title = r.get("title", "").strip()
        if not title:
            continue
        ts = _normalize_dt(r.get("published"))
        out.append({
            "event_id": _eid(name, title, ts),
            "source": name,
            "source_weight": weight,
            "title": title,
            "content": r.get("summary", ""),
            "publish_time": ts,
            "url": r.get("link") or None,
            "tags": list(tags),
        })
    return out


def fetch_individual(code: str, ak_module, cache, func_name: str = "stock_news_em", source_weight: float = 1.0) -> list[dict]:
    """反查个股新闻 (--input 模式).
    akshare stock_news_em 字段: 关键词 / 新闻标题 / 新闻内容 / 发布时间 / 文章来源
    """
    def _do():
        fn = getattr(ak_module, func_name, None)
        if fn is None:
            return {"_err": "func not exist", "rows": []}
        try:
            df = fn(symbol=code)
            return {"rows": [r.to_dict() for _, r in df.iterrows()], "_err": None}
        except Exception as e:
            return {"_err": f"{type(e).__name__}: {e}", "rows": []}

    raw = cache.get_or_call(f"news_individual_{func_name}", _do, code)
    if raw.get("_err"):
        return []

    out = []
    src_name = f"akshare:individual:{func_name}"
    for r in raw["rows"]:
        title = r.get("新闻标题") or r.get("title") or ""
        if not title:
            continue
        ts = _normalize_dt(r.get("发布时间"))
        out.append({
            "event_id": _eid(src_name, title, ts),
            "source": src_name,
            "source_weight": source_weight,
            "title": str(title),
            "content": r.get("新闻内容", "") or "",
            "publish_time": ts,
            "url": None,
            "tags": [f"individual:{code}"],
        })
    return out


def filter_by_window(events: list[dict], hours: int = 24, since_iso: str | None = None, until_iso: str | None = None) -> list[dict]:
    """按时间窗口过滤. since/until 优先于 hours."""
    if since_iso or until_iso:
        s = datetime.fromisoformat(since_iso) if since_iso else datetime(1970, 1, 1)
        u = datetime.fromisoformat(until_iso) if until_iso else datetime.now()
    else:
        u = datetime.now()
        s = u - timedelta(hours=hours)

    out = []
    for e in events:
        try:
            pt = datetime.fromisoformat(e["publish_time"])
        except Exception:
            continue
        if s <= pt <= u:
            out.append(e)
    return out


def dedupe(events: list[dict]) -> list[dict]:
    """按 event_id 去重 (多源同条新闻只留 1 条, 保留 source_weight 最高的)."""
    by_id: dict[str, dict] = {}
    for e in events:
        prev = by_id.get(e["event_id"])
        if prev is None or e.get("source_weight", 0) > prev.get("source_weight", 0):
            by_id[e["event_id"]] = e
    return list(by_id.values())


def fetch_all(cfg: dict, cache, ak_module, sources_filter: list[str] | None = None) -> list[dict]:
    """全市场新闻拉取 (akshare + RSS). sources_filter=['akshare:cls', 'rss:新华网-经济'] 限制源."""
    events: list[dict] = []

    # akshare
    if cfg.get("sources", {}).get("akshare", {}).get("enabled", True):
        for iface in cfg["sources"]["akshare"].get("interfaces", []):
            if sources_filter and iface["name"] not in sources_filter:
                continue
            try:
                rows = fetch_akshare(
                    iface["name"], iface["func"], iface.get("args"),
                    ak_module, cache, iface.get("weight", 1.0)
                )
                events.extend(rows)
            except Exception as e:
                print(f"[news_fetcher] WARN {iface['name']} failed: {e}", file=__import__('sys').stderr)

    # RSS
    if cfg.get("sources", {}).get("rss", {}).get("enabled", True):
        rss_cfg = cfg["sources"]["rss"]
        ua = rss_cfg.get("user_agent", "Mozilla/5.0")
        timeout = rss_cfg.get("timeout_seconds", 10)
        for feed in rss_cfg.get("feeds", []):
            if sources_filter and feed["name"] not in sources_filter:
                continue
            try:
                rows = fetch_rss(feed, cache, ua, timeout)
                events.extend(rows)
            except Exception as e:
                print(f"[news_fetcher] WARN {feed['name']} blocked: {e}", file=__import__('sys').stderr)

    return dedupe(events)
