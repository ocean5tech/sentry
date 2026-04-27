"""Layer 1 + Layer 2 评分. 返回单候选 score (0-10)."""


def score_candidate(rec: dict, kw_cfg: dict) -> float:
    """简单加权: severity_sum + new_entry pct + pivot hits + hot hits + qseed bonus."""
    score = 0.0

    # Layer 1 触发严重度
    l1 = rec.get("layer1_triggers", {}) or {}
    score += l1.get("severity_sum", 0) * 0.5
    msig = l1.get("matched_shareholder_signal") or {}
    if msig.get("new_entry_pct", 0) and msig["new_entry_pct"] >= 10:
        score += 2

    # 公告 pivot/hot 命中
    anns = rec.get("announcements_90d") or {}
    score += min(len(anns.get("pivot_keywords_hit", [])), 3) * 0.5
    score += min(len(anns.get("hot_keywords_hit", [])), 3) * 0.3

    # 概念升级
    cs = rec.get("concept_status") or {}
    if cs.get("is_pivot_story"):
        score += 1.5

    # q-seed 交叉
    qsc = rec.get("q_seed_crosscheck") or {}
    if qsc.get("is_in_q_seed") is True:
        score += qsc.get("crosscheck_bonus", 0.5)

    # 基本面 YoY
    fund = rec.get("fundamentals") or {}
    yoy = fund.get("yoy_net_profit")
    if yoy is not None:
        if yoy >= 50:
            score += 1.0
        elif yoy >= 20:
            score += 0.5

    # 风险减分
    risks = anns.get("risk_flags", [])
    score -= len(risks) * 0.3

    return round(score, 2)
