"""规则引擎: 加载 knowledge_base + sector_map, 对新闻做关键词匹配, 输出 reasoning_chain.

规则命中条件: triggers 至少 N 个 (默认 2) 命中 news.title + news.content.

输出: 每条匹配产生 1 个 trigger record:
  {
    rule_id: str
    triggers_matched: [str, ...]
    reasoning_chain: [{layer, type, content, by, weight}]
    target_sectors: [str]            # 命中的概念名
    confidence: float
    direction: bullish | bearish | neutral
    priority: 1-3
  }
"""

from pathlib import Path
from typing import Optional

import yaml


def load_kb(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {"rules": {}}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    rules = data.get("rules", {})
    if isinstance(rules, list):
        # 兼容 list 格式: 转 dict
        rules = {r["id"]: r for r in rules if "id" in r}
    return {"rules": rules}


def load_sector_map(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    # 顶层 key 即概念名, value 含 description/priority/synonyms/stocks
    return data


def _count_matches(text: str, triggers: list[str]) -> tuple[int, list[str]]:
    """统计 text 里命中了多少 triggers, 返回 (count, matched_list)."""
    text_lower = text.lower()
    matched = []
    for t in triggers:
        if not t:
            continue
        if t.lower() in text_lower:
            matched.append(t)
    return len(matched), matched


def match_news(news: dict, kb: dict, min_match: int = 2) -> list[dict]:
    """对一条新闻跑规则匹配, 返回所有命中的 rule.
    news = {title, content, ...}
    kb = {rules: {rule_id: rule_def}}
    """
    text = (news.get("title", "") + "\n" + news.get("content", ""))
    out = []
    for rule_id, rule in (kb.get("rules") or {}).items():
        if not isinstance(rule, dict):
            continue
        triggers = rule.get("triggers", []) or []
        n_matched, matched_list = _count_matches(text, triggers)
        if n_matched < min_match:
            continue

        # 构建 reasoning_chain (规则定义的 layer 2-4 + 自动加 layer 1 news)
        chain = [{
            "layer": 1,
            "type": "news",
            "content": news.get("title", ""),
            "by": "akshare/rss",
            "source": news.get("source"),
            "publish_time": news.get("publish_time"),
        }]
        for c in rule.get("reasoning_chain", []) or []:
            chain.append({
                "layer": c.get("layer"),
                "type": c.get("type"),
                "content": c.get("content"),
                "by": c.get("by", "rule"),
                "weight": c.get("weight"),
            })

        # confidence 调整: base × (匹配数 / 期望数), 多命中加分
        base = float(rule.get("confidence_base", 0.6))
        bonus = min(0.15, (n_matched - min_match) * 0.05)
        conf = min(1.0, base + bonus)

        out.append({
            "rule_id": rule_id,
            "triggers_matched": matched_list,
            "reasoning_chain": chain,
            "target_sectors": rule.get("target_sectors", []) or [],
            "confidence": round(conf, 3),
            "direction": rule.get("direction", "neutral"),
            "priority": rule.get("priority", 2),
        })
    return out


def expand_to_stocks(matched_rules: list[dict], sector_map: dict, news: dict) -> list[dict]:
    """把 rule matches 展开到 stock-level records.

    每个 rule.target_sectors 查 sector_map, 输出该概念下所有 stocks.
    sector_map.stocks 为空时, 输出 event-level (code=null) 记录.

    返回 list of {
      code: str | None,
      name: str,
      concept: str,
      role: str | None,
      news: dict (原 news),
      rule: dict (matched rule),
    }
    """
    out = []
    for rule in matched_rules:
        sectors = rule.get("target_sectors") or []
        if not sectors:
            # 元规则 (如 控制权变更_借壳) 没 target_sectors, 跳过
            # 这种规则建议管道串到 q-fin
            continue
        for sector in sectors:
            sector_def = sector_map.get(sector) or {}
            stocks = sector_def.get("stocks") or []

            if not stocks:
                # 概念暂未填股票 → 输出 event-level (code=null)
                out.append({
                    "code": None,
                    "name": f"<{sector} 概念股待用户填>",
                    "concept": sector,
                    "concept_priority": sector_def.get("priority"),
                    "role": None,
                    "news": news,
                    "rule": rule,
                })
                continue

            for s in stocks:
                if not isinstance(s, dict):
                    continue
                code = s.get("code")
                if not code:
                    continue
                out.append({
                    "code": str(code),
                    "name": s.get("name", ""),
                    "concept": sector,
                    "concept_priority": sector_def.get("priority"),
                    "role": s.get("role"),
                    "news": news,
                    "rule": rule,
                })
    return out
