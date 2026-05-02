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
    summary["state_owned_pct"] = sh.get("state_owned_pct", 0)
    hc = sh.get("holder_count") or {}
    if hc and "holder_count_chg_pct" in hc:
        summary["holder_count_chg_pct"] = hc["holder_count_chg_pct"]
        summary["holder_count_current"] = hc.get("holder_count_current")

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

    # M&A 专项搜索结果（买方/被买方向、目标公司、交易方式）
    ma = rec.get("ma_research")
    if ma and ma.get("confidence") in ("高", "中"):
        summary["ma_research"] = {k: v for k, v in ma.items()
                                   if k not in ("_queries_used",) and v is not None}

    er = rec.get("entity_research") or {}
    if er and er.get("chain"):
        chain = er["chain"]
        summary["entity_research"] = {
            "chain_summary": _walk_chain_short(chain),
            # 顶层实体（新进方）的关键信息直接提前，方便 LLM 做重组分析
            "acquirer_entity": chain.get("entity"),
            "acquirer_identity": chain.get("identity", "")[:80],
            "acquirer_business": chain.get("business", "")[:150],
            "acquirer_key_persons": chain.get("key_persons", []),
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
        "【控盘度分析规则】\n"
        "state_owned_pct 是十大流通股东中国资持股占比. 国有股通常为限售股/非流通股, "
        "因此 state_owned_pct 越高 → 实际可流通筹码越少 → 控盘度越高 → 主力更容易拉升.\n"
        "  - state_owned_pct ≥ 30%: 高控盘, 流通盘极小, 视为利多因子\n"
        "  - state_owned_pct 10-30%: 中控盘\n"
        "  - state_owned_pct < 10%: 低控盘, 正常分析\n"
        "top10_concentration_pct 如果主要来自国资, 不应视为流动性风险, 而应视为控盘利多.\n\n"
        "【股东人数变化规则】\n"
        "holder_count_chg_pct 是当期 vs 上期股东人数变化百分比（季报口径）.\n"
        "  - 大幅减少 (< -10%): 筹码集中，主力锁仓，利多信号\n"
        "  - 小幅变化 (-10% ~ +10%): 中性\n"
        "  - 大幅增加 (> +10%): 散户涌入，筹码分散，可能已拉升过，需谨慎\n\n"
        "【重组/注资分析规则】\n"
        "如果数据中有产业资本新进或重组公告，必须详细分析以下内容并填入对应字段：\n"
        "  - acquirer_name: 新进方/重组方的真实名称（穿透SPV找到背后实控人或母公司）\n"
        "  - acquirer_background: 新进方的主营业务、行业背景、资本实力（≤60字）\n"
        "  - restructure_direction: 重组后公司可能转型的方向或注入资产类型（≤40字）\n"
        "若无重组/产业资本新进信号，这三个字段填 null.\n\n"
        "调查数据 (JSON):\n" + rec_summary + "\n\n"
        "请输出严格 JSON, 字段:\n"
        "  rating: 1-5 整数 (5=教科书级强信号, 1=噪音, 综合事件硬度+入主方质量+K线位置+已涨幅)\n"
        "  stars: 与 rating 一致的 ⭐ 字符串\n"
        "  one_liner: 一句话评价 (≤30 字)\n"
        "  theme_hardness: 硬/中/弱 (题材硬度)\n"
        "  entry_suggestion: 合理入场建议 (≤30 字)\n"
        "  key_risks: 关键风险 (string array, 1-3 条, 每条 ≤30 字)\n"
        "  themes: 命中题材 (string array, 例 [AI, 算力])\n"
        "  control_degree: 高控盘/中控盘/低控盘 (基于 state_owned_pct)\n"
        "  acquirer_name: 新进方/重组方名称 (无则 null)\n"
        "  acquirer_background: 新进方背景主业 (≤60字, 无则 null)\n"
        "  restructure_direction: 重组转型方向 (≤40字, 无则 null)\n\n"
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
