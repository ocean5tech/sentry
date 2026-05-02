"""意图识别：用 DeepSeek 语义路由，降级到关键词兜底"""
import json
import os
import re
import urllib.request

_CODE = re.compile(r'(?<![0-9])([0-9]{6})(?![0-9])')

_SYSTEM = """你是A股量化系统的路由助手。根据用户消息选择工具，只返回JSON。

工具说明和典型例子：
- recommend: 想找股票买、推荐选股。例："推荐几只""有啥好股""我想找能买的""小票有没有机会""今天买什么"
- news: 市场消息、公告、热点题材、大盘走势。例："今天有啥热点""大盘怎么走""有什么公告""市场消息"
- kechuang: 科创板/创业板突破策略。例："科创板有机会吗""创业板最近怎样"
- short: 三红买入短线策略、三根红棍买入信号。例："三红策略""短线买点""今日三红信号""有没有短线机会"
- analyze: 分析具体股票（提取code）。例："688211怎么样""分析中科微至""看看这只股"
- help: 问系统功能。例："你能做什么""怎么用""有什么功能"
- chat: 知识性问题、A股学习。例："主力是怎么操盘的""什么是量比"

返回格式：{"tool": "工具名", "code": "6位数字或null"}
只输出JSON，不要其他文字。"""


def _llm_route(text: str) -> tuple[str, dict]:
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        return _fallback(text)
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user",   "content": text},
        ],
        "max_tokens": 60,
        "temperature": 0,
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = json.loads(r.read())["choices"][0]["message"]["content"].strip()

    # 提取 JSON（防止 LLM 多输出文字）
    m = re.search(r'\{.*?\}', raw, re.DOTALL)
    if not m:
        return _fallback(text)
    result = json.loads(m.group())
    tool = result.get("tool", "chat")
    code = result.get("code") or None
    # 如果 LLM 没提取到代码，再用正则补
    if tool == "analyze" and not code:
        cm = _CODE.search(text)
        code = cm.group(1) if cm else None
    return tool, {"code": code, "raw": text}


def _fallback(text: str) -> tuple[str, dict]:
    """关键词兜底（LLM 不可用时）"""
    t = text.strip()
    cm = _CODE.search(t)
    code = cm.group(1) if cm else None
    if code and len(t) <= 8:
        return "analyze", {"code": code, "raw": t}
    for k in ['分析', '研究', '帮我看', '看一下', '说说']:
        if k in t:
            return "analyze", {"code": code, "raw": t}
    if code:
        for k in ['怎么样', '如何', '能买吗']:
            if k in t:
                return "analyze", {"code": code, "raw": t}
    for k in ['推荐', '买什么', '选股', '有啥好']:
        if k in t: return "recommend", {}
    for k in ['公告', '消息', '热点', '题材', '市场']:
        if k in t: return "news", {}
    for k in ['科创', '创业板', '平台突破']:
        if k in t: return "kechuang", {}
    for k in ['三红', '短线', '买入点', '三根红棍', '红棍']:
        if k in t: return "short", {}
    for k in ['帮助', 'help', '怎么用']:
        if k in t.lower(): return "help", {}
    return "chat", {"raw": t}


def parse_intent(text: str) -> tuple[str, dict]:
    try:
        return _llm_route(text)
    except Exception:
        return _fallback(text)
