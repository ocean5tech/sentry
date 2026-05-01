"""
q-dashboard: TWS风格单页布局
顶部 metrics → 左栏(形态选股+科创突破) + 右栏(公告热点+新股)
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

ROOT     = Path(__file__).parent.parent
CFG      = yaml.safe_load((Path(__file__).parent / "config.yaml").read_text(encoding="utf-8"))
CACHE    = ROOT / "logs" / "daily_cache"
sys.path.insert(0, str(ROOT))

st.set_page_config(page_title="福宝抓股", page_icon="📈", layout="wide",
                   initial_sidebar_state="collapsed")

st.markdown("""<style>
/* 全局字体 */
html,body,[class*="css"]{font-family:'SF Mono','Roboto Mono',monospace!important}
/* 隐藏默认页边距 */
.block-container{padding:0.8rem 1rem!important}
/* 手机端：两栏变单栏 */
@media (max-width:768px){
  .block-container{padding:0.4rem 0.4rem!important}
  [data-testid="column"]{min-width:100%!important;width:100%!important}
  [data-testid="stMetricValue"]{font-size:1.1rem!important}
  h1{font-size:.95rem!important}
  h2{font-size:.85rem!important}
  details summary{font-size:.78rem!important}
}
/* 标题 */
h1{font-size:1.1rem!important;color:#FFD700!important;margin:0!important;padding:0!important}
h2{font-size:.95rem!important;color:#E8E9EB!important;margin:.3rem 0 .2rem!important;
   border-bottom:1px solid #2D3748!important;padding-bottom:.15rem!important}
h3{font-size:.85rem!important;color:#A0AEC0!important;margin:.2rem 0 .1rem!important}
/* Metric */
[data-testid="stMetricValue"]{font-size:1.6rem!important;color:#FFD700!important;font-weight:700!important}
[data-testid="stMetricLabel"]{font-size:.68rem!important;color:#718096!important;text-transform:uppercase!important}
[data-testid="stMetricDelta"]{font-size:.7rem!important}
/* Expander */
details summary{font-size:.82rem!important;padding:.25rem .5rem!important}
details[open] summary{color:#FFD700!important}
/* 表格 */
.stDataFrame{font-size:.8rem!important}
/* 颜色tag */
.up{color:#00D4AA!important;font-weight:600}
.down{color:#FF4757!important;font-weight:600}
.tag{display:inline-block;background:#1A202C;border:1px solid #2D3748;color:#A0AEC0;
     padding:.08rem .4rem;border-radius:3px;font-size:.72rem;margin:.1rem .1rem .1rem 0}
.star4{color:#00D4AA}.star3{color:#ECC94B}.star2{color:#FC8181}
/* 分隔线 */
hr{border-color:#2D3748!important;margin:.4rem 0!important}
/* sidebar collapse */
[data-testid="collapsedControl"]{display:none}
/* 紧凑expander */
[data-testid="stExpander"]{margin:.15rem 0!important;border:1px solid #2D3748!important;border-radius:4px!important}
</style>""", unsafe_allow_html=True)


# ── 工具函数 ──────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_jsonl(path: str) -> list[dict]:
    out = []
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
    except Exception:
        pass
    return out

@st.cache_data(ttl=60)
def load_json(path: str) -> list:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return []

@st.cache_data(ttl=120)
def qseed_recommend(scan_date: str) -> tuple[str, list[dict]]:
    """以 q-seed 结果为锚，再用 q-fin 补充详情。
    返回 (实际数据日期, records)"""
    seed_dir = Path(CFG["paths"]["q_seed_logs"])
    fin_dir  = Path(CFG["paths"]["q_fin_logs"])
    ymd = scan_date.replace("-", "")

    # 找当天 q-seed 文件，没有则找最近一次
    seed_files = sorted(seed_dir.glob(f"q-seed_top*_{ymd}_*.jsonl"), reverse=True)
    actual_date = scan_date
    if not seed_files:
        all_seed = sorted(seed_dir.glob("q-seed_top*_*.jsonl"), reverse=True)
        seed_files = all_seed[:1]
        if seed_files:
            m = re.search(r"(\d{8})", seed_files[0].name)
            if m:
                d = m.group(1)
                actual_date = f"{d[:4]}-{d[4:6]}-{d[6:]}"

    if not seed_files:
        return scan_date, []

    seed_records = load_jsonl(str(seed_files[0]))
    seed_codes = {r.get("code") for r in seed_records if r.get("code")}

    # 找 q-fin 补充（用实际日期的文件，只取 top_n > 3 的批处理文件）
    fin_ymd = actual_date.replace("-", "")
    fin_index: dict[str, dict] = {}
    for f in fin_dir.glob(f"q-fin_top*_{fin_ymd}_*.jsonl"):
        top_n_m = re.search(r"q-fin_top(\d+)_", f.name)
        if top_n_m and int(top_n_m.group(1)) <= 2:
            continue  # 跳过单股/双股的零散分析
        for r in load_jsonl(str(f)):
            code = r.get("code")
            if code and r.get("score", 0) >= fin_index.get(code, {}).get("score", -1):
                fin_index[code] = r

    # 合并：q-seed 定顺序，q-fin 补详情
    result = []
    for r in seed_records:
        code = r.get("code")
        if not code:
            continue
        merged = {**r}
        if code in fin_index:
            merged.update({k: v for k, v in fin_index[code].items()
                           if v is not None and k not in ("code","name","rank")})
        result.append(merged)

    return actual_date, result


@st.cache_data(ttl=120)
def kechuang_records(scan_date: str) -> list[dict]:
    """科创/创业板：只用 top_n >= 5 的 q-fin 批处理文件"""
    fin_dir = Path(CFG["paths"]["q_fin_logs"])
    ymd = scan_date.replace("-", "")
    best: dict[str, dict] = {}
    for f in fin_dir.glob(f"q-fin_top*_{ymd}_*.jsonl"):
        top_n_m = re.search(r"q-fin_top(\d+)_", f.name)
        if not top_n_m or int(top_n_m.group(1)) < 5:
            continue
        for r in load_jsonl(str(f)):
            code = r.get("code","")
            if is_kechuang(code) and r.get("score", 0) >= best.get(code, {}).get("score", -1):
                best[code] = r
    return sorted(best.values(), key=lambda x: x.get("score", 0), reverse=True)

@st.cache_data(ttl=120)
def latest_qnews(scan_date: str) -> list[dict]:
    log_dir = Path(CFG["paths"]["q_news_logs"])
    ymd = scan_date.replace("-", "")
    files = sorted(log_dir.glob(f"q-news_top*_{ymd}_*.jsonl"), reverse=True)
    return load_jsonl(str(files[0])) if files else []

def is_kechuang(code: str) -> bool:
    return code.startswith(("688", "300", "301"))

def star_text(stars: str) -> str:
    """纯文本星级，用于 expander 标题"""
    return stars if stars else ""

def pct_text(v: float) -> str:
    """纯文本涨跌，用于 expander 标题"""
    return f"{v:+.1f}%"

def pct_html(v: float) -> str:
    """HTML 带色涨跌，用于 expander 内容"""
    s = f"{v:+.1f}%"
    return f"<span class='up'>{s}</span>" if v > 0 else (f"<span class='down'>{s}</span>" if v < 0 else s)

def cninfo_url(code: str) -> str:
    return f"http://www.cninfo.com.cn/new/disclosure/stock?stockCode={code}"

def render_entity_chain(chain: dict, depth: int = 0) -> str:
    """将实体研究链 dict 渲染为可读 Markdown"""
    if not chain or not isinstance(chain, dict):
        return ""
    indent = "  " * depth
    lines = []
    entity   = chain.get("entity", "")
    identity = chain.get("identity", "")
    business = (chain.get("business") or "")[:150]
    persons  = chain.get("key_persons") or []

    if entity:
        lines.append(f"{indent}**{'└─ ' if depth else ''}实体**: {entity}")
    if identity and identity != "parse_failed":
        lines.append(f"{indent}**性质**: {identity}")
    if business:
        lines.append(f"{indent}**背景**: {business}{'…' if len(chain.get('business',''))>150 else ''}")
    if persons:
        lines.append(f"{indent}**关键人**: {', '.join(str(p) for p in persons[:3])}")

    for sub in (chain.get("sub") or [])[:2]:
        lines.append("")
        lines.append(render_entity_chain(sub, depth + 1))

    return "\n\n".join(l for l in lines if l)

def available_dates() -> list[str]:
    days = set()
    for key in ("q_fin_logs", "q_news_logs"):
        p = Path(CFG["paths"][key])
        if p.exists():
            for f in p.glob("*.jsonl"):
                m = re.search(r"(\d{8})", f.name)
                if m:
                    d = m.group(1)
                    days.add(f"{d[:4]}-{d[4:6]}-{d[6:]}")
    if CACHE.exists():
        for f in CACHE.glob("ipo_*.json"):
            d = f.stem[4:]
            days.add(f"{d[:4]}-{d[4:6]}-{d[6:]}")
    return sorted(days, reverse=True) or [str(date.today())]


# ── 日期选择（顶栏）────────────────────────────────────────────
days    = available_dates()
qp      = st.query_params
default = qp.get("date", days[0])
idx     = days.index(default) if default in days else 0

hdr_l, hdr_m, hdr_r = st.columns([2, 6, 2])
with hdr_l:
    st.markdown("# 📈 福宝抓股")
with hdr_m:
    sel = st.selectbox("", days, index=idx, label_visibility="collapsed")
    st.query_params["date"] = sel
with hdr_r:
    if st.button("🔄 刷新", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

st.markdown("<hr>", unsafe_allow_html=True)

# ── 数据加载 ───────────────────────────────────────────────────
ymd = sel.replace("-", "")
seed_date, recommend = qseed_recommend(sel)
kechuang  = kechuang_records(sel)
news_all  = latest_qnews(sel)
ipo_path  = CACHE / f"ipo_{ymd}.json"
ipos      = load_json(str(ipo_path)) if ipo_path.exists() else []

# ── 顶部 Metrics ───────────────────────────────────────────────
m1, m2, m3, m4 = st.columns(4)
m1.metric("形态选股", len(recommend), "只")
m2.metric("公告热点", len(news_all),  "条")
m3.metric("科创突破", len(kechuang),  "只")
m4.metric("近期新股", len(ipos),      "只")

st.markdown("<hr>", unsafe_allow_html=True)

# ── 主体：左栏 + 右栏 ──────────────────────────────────────────
left, right = st.columns([6, 4], gap="medium")


# ════ 左栏 ════════════════════════════════════════════════════

with left:

    # ── 形态选股 ──────────────────────────────────────────
    st.markdown("## 🌱 形态选股")
    if seed_date != sel:
        st.caption(f"⚠️ 当日无选股记录，显示最近一次: {seed_date}")
    if not recommend:
        st.caption("今日无数据")
    else:
        for r in recommend[:8]:
            v    = r.get("verdict") or {}
            k    = r.get("kline")   or {}
            code = r.get("code","")
            name = r.get("name","")
            stars= v.get("stars","")
            ret20= (k.get("ret20") or 0) * 100
            price= k.get("current_price","-")
            liner= (v.get("one_liner") or "")[:40]
            entry= (v.get("entry_suggestion") or "")[:35]

            label = (f"{star_text(stars)} {name}({code}) "
                     f"¥{price} {pct_text(ret20)} | {liner}")
            with st.expander(label, expanded=False):
                # 风险
                risks = v.get("key_risks") or []
                if risks:
                    st.markdown("**风险** " + " · ".join(f"`{r2}`" for r2 in risks[:3]))
                # 操作建议
                if entry:
                    st.markdown(f"**操作** {entry}")
                # 题材
                themes = v.get("themes") or []
                if themes:
                    tags = " ".join(f"<span class='tag'>{t}</span>" for t in themes)
                    st.markdown(tags, unsafe_allow_html=True)
                # 股东实体链
                er    = r.get("entity_research") or {}
                chain = er.get("chain")
                if chain:
                    st.markdown("**股东研究**")
                    rendered = render_entity_chain(chain)
                    if rendered:
                        st.markdown(rendered)
                # 公告摘要
                ann = r.get("announcements_90d") or {}
                titles = [t.get("title","")[:35] for t in (ann.get("key_titles") or [])[:2]]
                if titles:
                    st.markdown("**近期公告** " + " · ".join(titles))
                st.markdown(f"[📋 cninfo]({cninfo_url(code)})", unsafe_allow_html=False)

    # ── 科创突破 ──────────────────────────────────────────
    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown("## 🚀 科创突破")
    if not kechuang:
        st.caption("今日无数据")
    else:
        for r in kechuang[:6]:
            v    = r.get("verdict") or {}
            k    = r.get("kline")   or {}
            code = r.get("code","")
            name = r.get("name","")
            exch = "科创" if code.startswith("688") else "创业"
            stars= v.get("stars","")
            ret5 = (k.get("ret5")  or 0) * 100
            ret20= (k.get("ret20") or 0) * 100
            price= k.get("current_price","-")
            liner= (v.get("one_liner") or "")[:38]

            label = (f"[{exch}] {star_text(stars)} {name}({code}) "
                     f"¥{price} 5日{pct_text(ret5)} | {liner}")
            with st.expander(label, expanded=False):
                c1, c2 = st.columns(2)
                c1.markdown(f"**20日** {pct_text(ret20)}")
                c2.markdown(f"**入场** {(v.get('entry_suggestion') or '-')[:30]}")
                risks = v.get("key_risks") or []
                if risks:
                    st.markdown("**风险** " + " · ".join(f"`{r2}`" for r2 in risks[:2]))
                ann = r.get("announcements_90d") or {}
                titles = [t.get("title","")[:35] for t in (ann.get("key_titles") or [])[:2]]
                if titles:
                    st.markdown("**公告** " + " · ".join(titles))
                st.markdown(f"[📋 cninfo]({cninfo_url(code)})")


# ════ 右栏 ════════════════════════════════════════════════════

with right:

    # ── 公告热点 ──────────────────────────────────────────
    st.markdown("## 📰 公告热点")
    if not news_all:
        st.caption("今日无数据")
    else:
        for r in news_all[:12]:
            cs    = r.get("concept_status") or {}
            code  = r.get("code","")
            name  = r.get("name","")
            concept = cs.get("concept","")
            started = cs.get("已启动")
            flag  = "🔴" if started else ("🟢" if started is False else "⚪")
            score = r.get("score",0)

            label = f"{flag} **{name}({code})** {concept} `{score:.1f}`"
            with st.expander(label, expanded=False):
                verdict = cs.get("verdict","")
                if verdict:
                    st.caption(verdict[:80])

                evs = r.get("triggered_events") or []
                for ev in evs[:3]:
                    title  = ev.get("title","")[:50]
                    src    = ev.get("source","")
                    url    = ev.get("url") or ev.get("link","")
                    rule   = ev.get("rule") or {}
                    direct = rule.get("direction","")
                    conf   = rule.get("confidence","")

                    st.markdown("---")
                    if url:
                        st.markdown(f"[{title}]({url}) `{src}`")
                    else:
                        st.markdown(f"**{title}** `{src}`")
                        if code:
                            st.markdown(f"[查看{code}全部公告]({cninfo_url(code)})")

                    if direct or conf:
                        st.caption(f"方向={direct} 置信={conf}")

                    chain_steps = ev.get("reasoning_chain") or []
                    if chain_steps:
                        summary = " → ".join(
                            step.get("content","")[:30]
                            for step in chain_steps[:3]
                        )
                        st.caption(f"推理: {summary}")

                if code:
                    st.markdown(f"[📋 cninfo]({cninfo_url(code)})")

    # ── 新股 ──────────────────────────────────────────────
    st.markdown("<hr>", unsafe_allow_html=True)
    st.markdown("## 🆕 近期新股")
    if not ipos:
        st.caption("无近期新股 / 未生成缓存")
        if st.button("扫描新股"):
            import subprocess
            with st.spinner("扫描中…"):
                subprocess.run([sys.executable, str(ROOT/"scripts/q-ipo-watch")], timeout=120)
            st.cache_data.clear()
            st.rerun()
    else:
        rows = []
        for r in ipos:
            chg = r.get("change_pct", 0)
            rows.append({
                "代码":   r.get("code"),
                "名称":   r.get("name"),
                "上市日": r.get("ipo_date","")[-5:],   # MM-DD
                "板块":   r.get("market",""),
                "发行价": r.get("ipo_price"),
                "现价":   r.get("last_price"),
                "涨跌%":  chg,
            })
        df = pd.DataFrame(rows)

        # 用 map 替代已废弃的 applymap
        def color_chg(v):
            if not isinstance(v, (int, float)):
                return ""
            return "color:#00D4AA" if v > 0 else ("color:#FF4757" if v < 0 else "")

        st.dataframe(
            df.style.map(color_chg, subset=["涨跌%"]),
            use_container_width=True,
            hide_index=True,
        )
        # 每只新股详情
        for r in ipos:
            code = r.get("code","")
            chg  = r.get("change_pct",0)
            label = f"{r.get('name',code)}({code}) {r.get('market','')} {pct_text(chg)}"
            with st.expander(label, expanded=False):
                c1, c2, c3 = st.columns(3)
                c1.metric("发行价", f"¥{r.get('ipo_price','-')}")
                c2.metric("现价",   f"¥{r.get('last_price','-')}")
                c3.metric("涨跌",   f"{chg:+.1f}%")
                st.caption(f"上市日期: {r.get('ipo_date')}  |  已上市 {r.get('days_listed',0)} 天")
                if r.get("industry"):
                    st.caption(f"行业: {r['industry']}")
                st.markdown(f"[📋 cninfo]({cninfo_url(code)}) · "
                            f"[东方财富](https://quote.eastmoney.com/"
                            f"{'sh' if code.startswith('6') else 'sz'}{code}.html)")
