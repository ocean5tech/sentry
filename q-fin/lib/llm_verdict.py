"""综合结论 (--paid 启用): 一次 LLM 调用出 ⭐ 评级 + 一句话 + 风险 + 入场建议.

TOP N (config.providers.llm.<active>.verdict_model_top_n, 默认 5) 用 verdict_model (Sonnet),
其余用 default_model (Haiku).
"""

import json
import re
from typing import Optional


def _try_parse_json(text: str) -> dict:
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
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
    return {}


def _trim(d: dict | None, keep: list[str]) -> dict:
    if not d:
        return {}
    return {k: d.get(k) for k in keep if d.get(k) is not None}


def _summarize_record(rec: dict) -> str:
    """把 layer1+layer2+entity_research 浓缩成 prompt 可消费的小 JSON.
    避免 prompt 过长 (Haiku 价格便宜但仍按 token 算)."""

    summary = {
        "code": rec.get("code"),
        "name": rec.get("name"),
        "tags": rec.get("tags", []),
        "score": rec.get("score"),
    }

    l1 = rec.get("layer1_triggers") or {}
    summary["layer1"] = _trim(l1, ["matched_events", "matched_shareholder_signal", "severity_sum"])

    sh = rec.get("shareholders") or {}
    summary["major_new_entry"] = sh.get("major_new_entry")
    summary["top10_concentration_pct"] = sh.get("top10_concentration_pct")

    fund = rec.get("fundamentals") or {}
    summary["fundamentals"] = _trim(fund, [
        "industry", "report_qtr", "yoy_net_profit", "yoy_revenue",
        "net_margin", "eps", "market_cap_yi",
    ])

    kline = rec.get("kline") or {}
    summary["kline"] = _trim(kline, [
        "current_price", "vwap20_dev", "vwap60_dev", "ret60", "ret120", "kline_safety",
    ])

    ann = rec.get("announcements_90d") or {}
    summary["announcements"] = {
        "total": ann.get("total"),
        "by_category": ann.get("by_category"),
        "key_titles": (ann.get("key_titles") or [])[:5],
        "risk_flags": ann.get("risk_flags", []),
    }

    qsc = rec.get("q_seed_crosscheck") or {}
    summary["q_seed"] = _trim(qsc, ["is_in_q_seed", "matched_templates", "best_dist", "best_rank"])

    er = rec.get("entity_research") or {}
    if er and er.get("chain"):
        summary["entity_research"] = {
            "chain_summary": _walk_chain_short(er["chain"]),
        }

    return json.dumps(summary, ensure_ascii=False, default=str)


def _walk_chain_short(node: dict, depth: int = 0, max_depth: int = 3) -> list[dict]:
    if depth > max_depth:
        return []
    out = [{
        "entity": node.get("entity"),
        "depth": node.get("depth"),
        "identity": node.get("identity", "")[:50],
        "business": node.get("business", "")[:80],
    }]
    for sub in node.get("sub", []) or []:
        out.extend(_walk_chain_short(sub, depth + 1, max_depth))
    return out


def _build_prompt(rec_summary: str) -> str:
    return (
        "你是 A 股题材股分析师, 基于下面这只候选股的全部数据, 给出客观结论.\n\n"
        "调查数据 (JSON):\n" + rec_summary + "\n\n"
        "请输出严格 JSON, 字段:\n"
        "  rating: 1-5 整数 (5=教科书级强信号, 1=噪音, 综合事件硬度+入主方质量+K线位置+已涨幅)\n"
        "  stars: 与 rating 一致的 ⭐ 字符串\n"
        "  one_liner: 一句话评价 (≤30 字)\n"
        "  theme_hardness: 硬/中/弱 (题材硬度)\n"
        "  entry_suggestion: 合理入场建议 (≤30 字)\n"
        "  key_risks: 关键风险 (string array, 1-3 条, 每条 ≤30 字)\n"
        "  themes: 命中题材 (string array, 例 [AI, 算力])\n\n"
        "只输出 JSON, 不要解释."
    )


def make_verdict(
    rec: dict,
    rank: int,
    llm,
    budget,
    verdict_top_n: int = 5,
) -> dict | None:
    """生成 verdict. 返回 dict 或 None (预算不够 / 模块挂).
    rank = 当前股票在已排序列表里的排名 (1-based)
    """
    use_verdict_model = (rank <= verdict_top_n)
    model_kind = "verdict" if use_verdict_model else "default"

    summary = _summarize_record(rec)
    prompt = _build_prompt(summary)

    # 估算: prompt ~600 tokens in, ~250 tokens out
    est = llm.estimate_cost(600, 250, model_kind=model_kind)
    ok, reason = budget.can_spend(est, code=rec.get("code"))
    if not ok:
        return {
            "rating": None, "stars": "", "one_liner": "[budget_skipped]",
            "key_risks": [], "themes": [],
            "verdict_model": "skipped",
            "verdict_provider": llm.name,
            "verdict_cost_usd": 0.0,
            "_skip_reason": reason,
        }

    try:
        resp = llm.chat(prompt, model_kind=model_kind, max_tokens=600)
    except Exception as e:
        return {
            "rating": None, "stars": "", "one_liner": f"[llm_error: {type(e).__name__}]",
            "key_risks": [], "themes": [],
            "verdict_model": "error",
            "verdict_provider": llm.name,
            "verdict_cost_usd": 0.0,
        }

    budget.record(
        cost_usd=resp.cost_usd,
        provider=resp.provider,
        model=resp.model,
        input_tok=resp.input_tokens,
        output_tok=resp.output_tokens,
        code=rec.get("code"),
        kind="verdict",
    )

    parsed = _try_parse_json(resp.text)
    parsed["verdict_model"] = resp.model
    parsed["verdict_provider"] = resp.provider
    parsed["verdict_cost_usd"] = round(resp.cost_usd, 6)
    return parsed
