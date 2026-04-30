"""递归实体调查 (--paid 启用).

根 entity = shareholders.major_new_entry.name (例: "无锡核芯听涛科技合伙企业")
深度: standard=2 / deep=3 (config.entity_research.max_depth_*)
fanout: 每层最多追 N 个关键人物 (默认 2)

每层 LLM 调用:
  prompt 描述: 调查 entity 真实身份 / GP / 实控人 / 业务 / 关联资本
  返回 JSON: {identity, business, key_persons, links_to_dig_deeper}

3 层 cache 优先级:
  1. config/entity_hints.yaml (本地手维 + 自动追加)
  2. data_cache/entity/ (90 天 TTL)
  3. LLM 真调用

per-stock budget 上限通过 budget_guard 控制.
"""

import json
import re
import time
from pathlib import Path
from typing import Optional

import yaml


def _try_parse_json(text: str) -> dict:
    """LLM 返回里抽 JSON. 容错: 包在 code fence 里 / 前后多余文字."""
    # 尝试直接 parse
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    # 抽 ```json ... ``` 或 { ... } 块
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {"identity": "parse_failed", "business": "", "key_persons": [], "links_to_dig_deeper": []}


def _hints_lookup(hints: dict, entity: str) -> Optional[dict]:
    """在 entity_hints 三类里查 entity. 命中返回 normalized dict."""
    if not hints:
        return None
    for cat in ("partnerships", "companies", "persons"):
        cat_data = hints.get(cat, {}) or {}
        for name, info in cat_data.items():
            if not isinstance(info, dict):
                continue
            if name == entity or info.get("name") == entity:
                # 收集所有可能的 links: gp / controller / key_persons / links_to_dig_deeper
                links: list[str] = []
                for k in ("gp", "controller"):
                    v = info.get(k)
                    if v: links.append(v)
                for k in ("key_persons", "links_to_dig_deeper"):
                    v = info.get(k) or []
                    if isinstance(v, list):
                        links.extend([x for x in v if isinstance(x, str) and x])
                # 去重保序
                seen = set(); uniq = []
                for x in links:
                    if x not in seen:
                        seen.add(x); uniq.append(x)
                return {
                    "identity": (info.get("identity") or
                                ("私募合伙企业" if cat == "partnerships"
                                 else "公司" if cat == "companies"
                                 else "自然人")),
                    "business": info.get("business", "") or info.get("background", ""),
                    "key_persons": uniq,
                    "links_to_dig_deeper": uniq,
                    "_source": "hints",
                }
    return None


def _save_hints(hints_path: Path, new_chain: dict):
    """跑完后把发现的 entity 自动追加到 hints (避免重复 LLM 调用)."""
    hints_path.parent.mkdir(parents=True, exist_ok=True)
    if hints_path.exists():
        loaded = yaml.safe_load(hints_path.read_text(encoding="utf-8"))
        existing = loaded if isinstance(loaded, dict) else {}
    else:
        existing = {}
    for cat in ("partnerships", "companies", "persons"):
        if not isinstance(existing.get(cat), dict):
            existing[cat] = {}

    def _walk(node):
        ent = node.get("entity")
        if ent and node.get("_source") != "hints":
            cat = "partnerships" if "合伙" in ent else (
                "persons" if len(ent) <= 4 and not any(k in ent for k in ["公司", "科技", "集团", "投资"])
                else "companies"
            )
            existing[cat].setdefault(ent, {
                "identity": node.get("identity", ""),
                "business": node.get("business", ""),
                "key_persons": node.get("key_persons", []),
                "auto_added": True,
                "added_ts": time.time(),
            })
        for sub in node.get("sub", []):
            _walk(sub)

    _walk(new_chain)
    hints_path.write_text(
        yaml.safe_dump(existing, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _build_prompt(entity: str, search_evidence: str = "") -> str:
    evidence_block = ""
    if search_evidence:
        evidence_block = (
            "\n=== 网络搜索证据 (供参考, 优先于你的训练知识) ===\n"
            f"{search_evidence}\n"
            "=== 证据结束 ===\n\n"
        )
    return (
        f"调查以下实体的真实身份和背景: 「{entity}」\n"
        f"{evidence_block}"
        "重要规则:\n"
        "  1. 优先用上面的搜索证据回答, 不要凭空臆测.\n"
        "  2. 如果实体名形如 'XX投资合伙企业'/'XX听涛科技合伙企业', 它通常是**持股载体 (SPV)**, 用于资本运作, 业务=载体本身, 重点是查它的 GP 公司和实际控制人.\n"
        "  3. links_to_dig_deeper 必须是**具体的人名或公司名** (例: '胡康桥', '核芯互联科技'), 严禁填'天眼查'/'工商系统'/'查询渠道' 这类工具/网站.\n"
        "  4. 如果证据不足, 字段写空 (空字符串或空数组), 不要编造.\n\n"
        "输出严格 JSON, 字段:\n"
        "  identity: 实体类型/角色 (例: '私募合伙企业(SPV持股载体)' / '模拟芯片设计公司' / '自然人, 芯片产业资本家'), ≤40 字\n"
        "  business: 业务/背景描述, ≤100 字\n"
        "  key_persons: 关联关键人物/公司名列表 (string array, 实体名, 不要工具名)\n"
        "  links_to_dig_deeper: 值得递归调查的下一层实体 (具体人名/公司名, ≤3 个, 不要工具)\n\n"
        "只输出 JSON, 不要解释."
    )


def _format_search_evidence(results) -> str:
    """把 search results [{title, url, snippet}, ...] 拼成 prompt 友好格式."""
    if not results:
        return ""
    lines = []
    for i, r in enumerate(results[:3], 1):
        title = (getattr(r, "title", "") or "").strip()[:80]
        snippet = (getattr(r, "snippet", "") or "").strip()[:200]
        lines.append(f"[{i}] {title}\n    {snippet}")
    return "\n".join(lines)


def research(
    root_entity: str,
    code: str,
    cfg: dict,
    llm,
    search,                # 当前 V0 不直接用, 后续把 web search 喂给 LLM
    budget,
    hints_path: Path,
) -> dict:
    """递归调查 root_entity. 返回 chain dict.

    cfg = config["entity_research"] + 上层 mode (standard/deep)
    llm = LLMProvider 实例
    budget = BudgetGuard 实例
    """
    max_depth = cfg.get("max_depth", 2)
    fanout = cfg.get("fanout_per_layer", 2)

    hints = {}
    if cfg.get("prefer_entity_hints", True) and hints_path.exists():
        hints = yaml.safe_load(hints_path.read_text(encoding="utf-8")) or {}

    # 路由策略:
    # - 如果 LLM provider = anthropic → 用 Claude 内置 web_search tool (服务端搜+综合, 质量高)
    # - 否则尝试 search.query() 把 snippet 喂 prompt (DDG/SerpAPI/dummy 走这条)
    use_anthropic_tool = (getattr(llm, "name", "") == "anthropic")
    use_external_search = (
        not use_anthropic_tool
        and bool(search)
        and getattr(search, "name", None) not in ("anthropic_tool", None)
    )

    web_search_max_uses = int(cfg.get("web_search_max_uses", 1))
    web_search_tool_spec = [{
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": web_search_max_uses,
    }] if use_anthropic_tool else None

    reasoning_steps: list[dict] = []   # 推理链: 每步搜索+结论

    def _query(entity: str, depth: int) -> dict:
        """单 entity 查询: hints → (web_search via tool 或 prompt-injected snippet) → LLM."""
        step: dict = {"depth": depth, "entity": entity,
                      "search_used": False, "search_snippets": [], "conclusion": ""}
        # 1. hints
        hit = _hints_lookup(hints, entity)
        if hit:
            step["conclusion"] = f"[缓存] {(hit.get('identity') or '')[:60]}"
            reasoning_steps.append(step)
            return hit

        # 2. 外部 search 取证 (仅 non-anthropic provider)
        evidence = ""
        if use_external_search:
            try:
                results = search.query(entity, max_results=3)
                evidence = _format_search_evidence(results)
                step["search_used"] = True
                step["search_snippets"] = [r.title[:55] for r in results]
            except Exception:
                evidence = ""

        # 3. budget 估算
        # anthropic + tool: 估 ~600 in + 400 out + 1-2 search ($0.01 each) = ~$0.025
        # external search: ~400 in + 250 out
        # 无 search: ~150 in + 200 out
        if use_anthropic_tool:
            # 每次 search 把全文塞进 prompt → input ~15-30K tokens 实际比预想大
            # max_uses=1: ~15K in + 400 out = $0.017 + $0.01(search) = $0.027
            # max_uses=2: ~30K in + 400 out = $0.032 + $0.02(search) = $0.052
            est_in = 15000 * web_search_max_uses
            est = llm.estimate_cost(est_in, 400) + 0.01 * web_search_max_uses
        elif evidence:
            est = llm.estimate_cost(400, 250)
        else:
            est = llm.estimate_cost(150, 200)
        ok, reason = budget.can_spend(est, code=code)
        if not ok:
            return {
                "identity": "budget_skipped",
                "business": "",
                "key_persons": [],
                "links_to_dig_deeper": [],
                "_skip_reason": reason,
                "_source": "budget_skip",
            }

        # 4. LLM 调用
        prompt = _build_prompt(entity, evidence if not use_anthropic_tool else "")
        try:
            resp = llm.chat(
                prompt,
                model_kind="default",
                max_tokens=800,
                tools=web_search_tool_spec,
            )
        except Exception as e:
            return {
                "identity": "llm_error",
                "business": str(e)[:200],
                "key_persons": [],
                "links_to_dig_deeper": [],
                "_source": "llm_error",
            }

        budget.record(
            cost_usd=resp.cost_usd,
            provider=resp.provider,
            model=resp.model,
            input_tok=resp.input_tokens,
            output_tok=resp.output_tokens,
            code=code,
            kind="entity_research",
        )

        parsed = _try_parse_json(resp.text)
        parsed["_source"] = "llm"
        if use_anthropic_tool:
            parsed["_evidence_used"] = True
            parsed["_search_calls"] = (resp.raw or {}).get("search_calls", 0)
        elif evidence:
            parsed["_evidence_used"] = True

        # 记录推理步骤
        step["conclusion"] = (parsed.get("identity") or "")[:80]
        step["business_summary"] = (parsed.get("business") or "")[:100]
        step["model"] = resp.model
        reasoning_steps.append(step)
        return parsed

    def _recurse(entity: str, depth: int) -> dict:
        info = _query(entity, depth)
        node = {
            "entity": entity,
            "depth": depth,
            "identity": info.get("identity", ""),
            "business": info.get("business", ""),
            "key_persons": info.get("key_persons", []),
            "_source": info.get("_source", "unknown"),
            "sub": [],
        }
        if info.get("_evidence_used"):
            node["_evidence_used"] = True
        if "_search_calls" in info:
            node["_search_calls"] = info["_search_calls"]
        if depth >= max_depth:
            return node
        # 递归
        targets = (info.get("links_to_dig_deeper") or [])[:fanout]
        for t in targets:
            if not t or not isinstance(t, str):
                continue
            sub = _recurse(t, depth + 1)
            node["sub"].append(sub)
        return node

    chain = _recurse(root_entity, depth=0)

    # 自动追加到 hints (容错, 失败不影响主流程)
    try:
        _save_hints(hints_path, chain)
    except Exception as e:
        print(f"[entity_research] WARN failed to save hints: {e}", file=__import__("sys").stderr)

    return {
        "max_depth_used": max_depth,
        "budget_used_usd": round(budget.per_stock_used(code), 6),
        "chain": chain,
        "reasoning_steps": reasoning_steps,
        "llm_provider": getattr(llm, "name", "unknown"),
        "search_provider": getattr(search, "name", "none") if search else "none",
    }
