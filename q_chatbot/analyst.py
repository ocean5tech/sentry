"""单股深度分析报告生成（q-seed信号 + q-fin基本面 + DeepSeek评论员）"""
import json
import os
import subprocess
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _llm(system: str, user: str, max_tokens: int = 700) -> str:
    import urllib.request
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        return "(LLM不可用：未配置DEEPSEEK_API_KEY)"
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "system", "content": system},
                     {"role": "user",   "content": user}],
        "max_tokens": max_tokens,
        "temperature": 0.6,
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


def _run_qfin(code: str, name: str, close: float) -> dict:
    rec = {"code": code, "name": name, "date": str(date.today()),
           "score": 0, "close": round(close, 2)}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        tmp_in = f.name
    tmp_out = tmp_in.replace(".jsonl", "_out.jsonl")
    try:
        subprocess.run(
            [str(ROOT / "q-fin/.venv/bin/python"), str(ROOT / "q-fin/main.py"),
             "--paid", "--input", tmp_in, "--format", "jsonl", "--output", tmp_out],
            timeout=300, cwd=str(ROOT),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for line in open(tmp_out):
            r = json.loads(line)
            if r.get("code") == code:
                return r
    except Exception:
        pass
    finally:
        for p in (tmp_in, tmp_out):
            try: os.unlink(p)
            except: pass
    return {}


def generate(code: str) -> str:
    """主入口：返回分析师报告字符串"""
    import sys
    sys.path.insert(0, str(ROOT))
    from core.data_loader import load_daily
    from core.stock_names import get_name

    df = load_daily(code)
    if df is None or df.empty:
        return f"找不到 {code} 的行情数据，请确认代码正确。"

    name = get_name(code) or code
    close_arr = df["close"].values.astype(float)
    price = float(close_arr[-1])

    # 近期涨幅
    def ret(n):
        return (close_arr[-1] / close_arr[-n - 1] - 1) * 100 if len(close_arr) > n else 0

    price_ctx = (f"{name}（{code}）现价 {price:.2f} 元\n"
                 f"近期涨幅：5日{ret(5):+.1f}% | 20日{ret(20):+.1f}% | 60日{ret(60):+.1f}%")

    # 策略信号
    signals = []
    try:
        from core.strategies import kechuang_breakout
        r = kechuang_breakout.scan(df, symbol=code)
        if r:
            signals.append(f"科创平台突破 评分{r['score']} | 振幅{r['platform_amp']}% | 量比{r['vol_ratio']}")
    except Exception:
        pass

    # q-fin 分析
    qf = _run_qfin(code, name, price)
    verdict   = qf.get("verdict", {})
    fund      = qf.get("fundamentals", {})
    anns      = qf.get("announcements_90d", {})
    holders   = qf.get("shareholders", {})

    fin_ctx = ""
    if verdict:
        stars     = verdict.get("stars", "")
        one_liner = verdict.get("one_liner", "")
        risks     = "；".join(verdict.get("key_risks", [])[:3])
        themes    = "、".join(verdict.get("themes", []))
        entry     = verdict.get("entry_suggestion", "")
        fin_ctx += f"\nq-fin评级：{stars} {one_liner}"
        if themes:  fin_ctx += f"\n题材：{themes}"
        if risks:   fin_ctx += f"\n风险点：{risks}"
        if entry:   fin_ctx += f"\n操盘参考：{entry}"

    if fund:
        eps = fund.get("eps")
        np_ = fund.get("net_profit")
        if eps is not None:
            np_str = f"{np_/1e8:.2f}亿" if np_ and abs(np_) > 1e6 else str(np_)
            fin_ctx += f"\nEPS {eps:.3f} | 净利润 {np_str}"

    ann_titles = [t["title"][:25] for t in anns.get("key_titles", [])[:2]]
    if ann_titles:
        fin_ctx += f"\n近期公告：{'；'.join(ann_titles)}"

    top1 = (holders.get("top10_free") or [{}])[0]
    if top1.get("name"):
        fin_ctx += f"\n第一大股东：{top1['name']} {top1.get('pct', 0):.1f}%"

    # 组合上下文 → LLM
    context = price_ctx
    if signals:
        context += "\n技术信号：" + "；".join(signals)
    context += fin_ctx or "\n（基本面数据获取中）"

    system = """你是资深A股股票评论员，说话直接有观点，用散户听得懂的语言。
根据数据给出明确结论。格式严格如下，每项独立一行：

【结论】买入 / 持有 / 卖出（三选一）
【核心逻辑】（2-3句，说最关键的理由）
【最大风险】（1-2条）
【操作建议】（入场区间 / 止损位 / 目标位，或"暂不介入"）
【综合评分】★★★☆☆（1-5星）

总字数不超过280字。"""

    report = _llm(system, f"请分析：\n{context}")
    disclaimer = "⚠️ 仅供参考，不构成投资建议。所有买卖请独立判断，严格执行止损。"
    return f"=== {name}（{code}）分析报告 ===\n\n{report}\n\n{disclaimer}"
