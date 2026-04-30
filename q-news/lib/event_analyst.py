"""
LLM 事件分析: 对公告/新闻给出 利多/利空 + 影响程度 + 逻辑链推理.
支持三种输入: 个股公告 / 板块新闻 / 大盘新闻.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EventAnalysis:
    direction: str          # "利多" / "利空" / "中性" / "待观察"
    impact: str             # "高" / "中" / "低"
    logic: str              # 2-3句逻辑链
    action: str             # "直接推送" / "交叉验证" / "观察池"
    affected_sectors: list[str] = field(default_factory=list)   # 大盘/板块新闻时填
    affected_stocks:  list[str] = field(default_factory=list)   # 具体股票代码
    cost_usd: float = 0.0
    model: str = ""


_STOCK_PROMPT = """你是A股研究员。分析以下上市公司公告对股票的影响。

公司: {name}({code})
公告标题: {title}
公告类型: {ann_type}
公告日期: {date}

请用JSON回答:
{{
  "direction": "利多" | "利空" | "中性" | "待观察",
  "impact": "高" | "中" | "低",
  "logic": "简洁逻辑链(2-3句): 事件→传导→影响",
  "action": "直接推送" | "交叉验证" | "观察池",
  "note": "补充说明(可空)"
}}

判断标准:
- 高影响直接推送: 控制权变更+概念切换预期, 重大并购注入优质资产, 业绩超预期大幅扭亏
- 中影响交叉验证: 一般并购/合同, 业绩小幅超预期, 需结合形态确认
- 低影响观察池: 常规事项, 定期报告, 暂无操作价值"""


_NEWS_PROMPT = """你是A股研究员。分析以下市场新闻对A股板块/个股的影响。

新闻来源: {source}
标题: {title}
内容摘要: {content}

请用JSON回答:
{{
  "direction": "利多" | "利空" | "中性" | "待观察",
  "impact": "高" | "中" | "低",
  "logic": "逻辑链(2-3句): 政策/事件→传导路径→受益/受损",
  "affected_sectors": ["板块1", "板块2"],
  "affected_stocks_hint": "受影响的细分方向或龙头方向(简短)",
  "action": "直接推送" | "交叉验证" | "观察池"
}}"""


def _parse_json_response(text: str) -> dict:
    """从 LLM 输出提取 JSON."""
    # 先找 ```json ... ```
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 再找第一个 {...}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return {}


def analyze_stock_announcement(
    llm,
    budget,
    code: str,
    name: str,
    title: str,
    ann_type: str,
    date: str,
) -> Optional[EventAnalysis]:
    """分析个股公告."""
    est = llm.estimate_cost(300, 200)
    ok, reason = budget.can_spend(est, code=code)
    if not ok:
        return None

    prompt = _STOCK_PROMPT.format(
        name=name, code=code, title=title, ann_type=ann_type, date=date
    )
    try:
        resp = llm.chat(prompt, model_kind="default", max_tokens=600)
    except Exception as e:
        return EventAnalysis("待观察", "低", f"LLM错误: {e}", "观察池")

    budget.record(
        cost_usd=resp.cost_usd, provider=resp.provider, model=resp.model,
        input_tok=resp.input_tokens, output_tok=resp.output_tokens,
        code=code, kind="event_analysis",
    )

    parsed = _parse_json_response(resp.text)
    return EventAnalysis(
        direction=parsed.get("direction", "待观察"),
        impact=parsed.get("impact", "低"),
        logic=parsed.get("logic", resp.text[:150]),
        action=parsed.get("action", "观察池"),
        cost_usd=resp.cost_usd,
        model=resp.model,
    )


def analyze_market_news(
    llm,
    budget,
    source: str,
    title: str,
    content: str,
) -> Optional[EventAnalysis]:
    """分析大盘/板块新闻."""
    est = llm.estimate_cost(400, 300)
    ok, _ = budget.can_spend(est, code="MARKET")
    if not ok:
        return None

    prompt = _NEWS_PROMPT.format(source=source, title=title, content=content[:500])
    try:
        resp = llm.chat(prompt, model_kind="default", max_tokens=500)
    except Exception as e:
        return EventAnalysis("待观察", "低", f"LLM错误: {e}", "观察池")

    budget.record(
        cost_usd=resp.cost_usd, provider=resp.provider, model=resp.model,
        input_tok=resp.input_tokens, output_tok=resp.output_tokens,
        code="MARKET", kind="news_analysis",
    )

    parsed = _parse_json_response(resp.text)
    sectors = parsed.get("affected_sectors") or []
    if isinstance(sectors, str):
        sectors = [sectors]

    return EventAnalysis(
        direction=parsed.get("direction", "待观察"),
        impact=parsed.get("impact", "低"),
        logic=parsed.get("logic", resp.text[:150]),
        action=parsed.get("action", "观察池"),
        affected_sectors=sectors,
        affected_stocks=[parsed.get("affected_stocks_hint", "")],
        cost_usd=resp.cost_usd,
        model=resp.model,
    )
