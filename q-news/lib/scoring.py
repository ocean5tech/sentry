"""q-news 综合评分.
权重见 config.scoring.weights.

score = base × Σ(weight × signal):
  - rule_confidence: 规则置信度 (核心)
  - concept_inactive_bonus: 概念未启动 → +bonus (alpha 来源)
  - direction_bullish_bonus: 利好方向 +bonus
  - qseed_inactive_bonus: 个股 q-seed 未命中 +bonus (单股层面"未启动")
  - fundamentals_yoy_bonus: yoy_ni > 0 +bonus
"""


def score_record(rec: dict, weights: dict) -> float:
    """rec 含 triggered_events[*].rule + concept_status + q_seed_crosscheck + q_fin_crosscheck."""
    score = 0.0

    # 1. 规则置信度: 取多事件中最高的
    events = rec.get("triggered_events", []) or []
    max_conf = max((e.get("rule", {}).get("confidence", 0) for e in events), default=0)
    score += max_conf * weights.get("rule_confidence", 0.4) * 10  # 放大到 0-10 范围

    # 2. 概念未启动 bonus
    cs = rec.get("concept_status") or {}
    started = cs.get("已启动")
    if started is False:
        score += weights.get("concept_inactive_bonus", 0.3) * 10
    elif started is None:
        score += weights.get("concept_inactive_bonus", 0.3) * 5  # 数据缺失给一半

    # 3. direction
    has_bullish = any((e.get("rule", {}).get("direction") == "bullish") for e in events)
    if has_bullish:
        score += weights.get("direction_bullish_bonus", 0.1) * 10

    # 4. qseed 未命中 bonus (个股 layer)
    qsc = rec.get("q_seed_crosscheck") or {}
    if qsc.get("is_in_q_seed") is False:
        score += weights.get("qseed_inactive_bonus", 0.1) * 10

    # 5. fundamentals
    qfc = rec.get("q_fin_crosscheck") or {}
    yoy = qfc.get("fundamentals_yoy_ni")
    if yoy is not None and yoy > 0:
        score += weights.get("fundamentals_yoy_bonus", 0.1) * 10

    return round(score, 2)
