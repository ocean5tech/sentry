#!/usr/bin/env python3
"""
q-chatbot: 企业微信自建应用双向交互服务
GET  /callback  → URL验证 (echostr)
POST /callback  → 接收消息 → 意图路由 → 异步回复
"""

import os
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

from flask import Flask, request, abort

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# 加载 .env
_env = ROOT / ".env"
if _env.exists():
    for _line in _env.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            k, _, v = _line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from q_chatbot.crypto import WeChatCrypto
from q_chatbot.api    import WeChatAPI
from q_chatbot.intent import parse_intent

CORP_ID   = os.environ["WECHAT_CORP_ID"]
AGENT_ID  = int(os.environ["WECHAT_AGENT_ID"])
SECRET    = os.environ["WECHAT_CORP_SECRET"]
TOKEN     = os.environ["WECHAT_TOKEN"]
AES_KEY   = os.environ["WECHAT_AES_KEY"]

crypto = WeChatCrypto(TOKEN, AES_KEY, CORP_ID)
wx     = WeChatAPI(CORP_ID, SECRET, AGENT_ID)

app = Flask(__name__)


# ── 路由 ─────────────────────────────────────────────────────────────

@app.route("/callback", methods=["GET"])
def verify():
    sig  = request.args.get("msg_signature", "")
    ts   = request.args.get("timestamp", "")
    nc   = request.args.get("nonce", "")
    echo = request.args.get("echostr", "")
    if not crypto.verify(ts, nc, sig, echo):
        abort(403)
    return crypto.decrypt(echo), 200, {"Content-Type": "text/plain"}


@app.route("/callback", methods=["POST"])
def receive():
    import logging
    log = logging.getLogger("chatbot")
    sig = request.args.get("msg_signature", "")
    ts  = request.args.get("timestamp", "")
    nc  = request.args.get("nonce", "")

    try:
        tree    = ET.fromstring(request.data)
        encrypt = tree.findtext("Encrypt", "")
        if not crypto.verify(ts, nc, sig, encrypt):
            abort(403)

        msg     = ET.fromstring(crypto.decrypt(encrypt))
        user    = msg.findtext("FromUserName", "")
        mtype   = msg.findtext("MsgType", "")
        content = msg.findtext("Content", "").strip() if mtype == "text" else ""

        if not content:
            return "success"

        intent, args = parse_intent(content)
        t = threading.Thread(target=_dispatch, args=(intent, args, user), daemon=True)
        t.start()
    except Exception as e:
        print(f"[chatbot] error: {e}", flush=True)
    return "success"


# ── 调度 ─────────────────────────────────────────────────────────────

def _dispatch(intent: str, args: dict, user: str):
    try:
        if intent == "analyze":
            _do_analyze(args, user)
        elif intent == "recommend":
            _do_cached_batch("recommend", "q-pick-today-batch",
                             "正在扫描全市场形态，约需10分钟...", user)
        elif intent == "news":
            _do_cached_batch("news", "q-news-daily-batch",
                             "正在扫描今日重大公告，约需5分钟...", user)
        elif intent == "kechuang":
            _do_cached_batch("kechuang", "q-kechuang-batch",
                             "正在扫描科创/创业板平台突破，约需5分钟...", user)
        elif intent == "help":
            wx.send(user, _HELP_TEXT)
        else:
            _do_chat(args.get("raw", ""), user)
    except Exception as e:
        wx.send(user, f"处理出错：{e}")


def _do_analyze(args: dict, user: str):
    code = args.get("code")
    if not code:
        wx.send(user, "请提供6位股票代码，例如：分析 688211")
        return
    wx.send(user, f"正在分析 {code}，包含q-fin深度调研，约需3-5分钟...")
    from q_chatbot.analyst import generate
    report = generate(code)
    wx.send(user, report)


def _do_chat(text: str, user: str):
    import json, urllib.request
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        wx.send(user, "未配置LLM，无法回答")
        return
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "你是资深A股研究员，回答简洁专业，不超过200字。"},
            {"role": "user",   "content": text},
        ],
        "max_tokens": 400,
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=data,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        reply = json.loads(r.read())["choices"][0]["message"]["content"]
    wx.send(user, reply)


def _cache_path(key: str) -> Path:
    from datetime import date
    today = date.today().strftime("%Y%m%d")
    p = ROOT / "logs" / "daily_cache"
    p.mkdir(exist_ok=True)
    # 清理非今天的缓存文件
    for f in p.glob(f"{key}_*.txt"):
        if today not in f.name:
            f.unlink(missing_ok=True)
    return p / f"{key}_{today}.txt"


def _do_cached_batch(key: str, script: str, running_msg: str, user: str):
    cache = _cache_path(key)
    if cache.exists():
        wx.send(user, f"(今日缓存)\n{cache.read_text(encoding='utf-8')}")
        return
    wx.send(user, running_msg)
    # 后台跑批处理，完成后写缓存
    threading.Thread(
        target=_run_batch_and_cache, args=(script, cache), daemon=True
    ).start()


def _run_batch_and_cache(script: str, cache: Path):
    """运行批处理脚本，捕获推送摘要写入缓存"""
    import re
    log = ROOT / f"logs/chatbot_{script}.log"
    log.parent.mkdir(exist_ok=True)
    result = subprocess.run(
        [str(ROOT / "scripts" / script)],
        capture_output=True, text=True, timeout=900,
    )
    # 从脚本日志中提取命中摘要（抓 "命中 N 只" 之后的内容）
    combined = result.stdout + result.stderr
    with open(log, "a") as f:
        f.write(combined)
    # 简单摘要：取最后几行有意义的日志
    lines = [l for l in combined.splitlines()
             if any(k in l for k in ["命中", "推送", "完成", "score", "code"])]
    summary = "\n".join(lines[-20:]) if lines else "扫描完成，结果已推送到群"
    cache.write_text(summary, encoding="utf-8")


_HELP_TEXT = """福宝抓股 · 指令说明

分析 688211 — 深度分析单只股票（q-fin + 评论员报告）
推荐几只股票 — 全市场形态扫描
今天有什么公告 — 扫描重大公告
科创/创业板 — 科创平台突破策略
其他问题 — 直接问，AI自由回答"""


if __name__ == "__main__":
    port = int(os.environ.get("CHATBOT_PORT", 8502))
    print(f"[q-chatbot] 启动 port={port}")
    app.run(host="0.0.0.0", port=port, debug=False)
