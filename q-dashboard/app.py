"""
q-dashboard: 4个板块
  1. 形态选股  — q-seed + q-fin 推荐结果
  2. 公告热点  — q-news 今日题材 + 原始链接
  3. 科创突破  — 科创/创业板平台突破 q-fin 分析
  4. 新股      — 近 7 天上市新股列表
"""
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

ROOT = Path(__file__).parent.parent
CFG  = yaml.safe_load((Path(__file__).parent / "config.yaml").read_text(encoding="utf-8"))
CACHE_DIR = ROOT / "logs" / "daily_cache"

sys.path.insert(0, str(ROOT))

st.set_page_config(page_title="福宝抓股", page_icon="📈", layout="wide",
                   initial_sidebar_state="expanded")

st.markdown("""<style>
.main,[data-testid="stSidebar"]{font-family:'SF Mono','Roboto Mono',monospace}
[data-testid="stMetricValue"]{font-size:2rem;color:#FFD700;font-weight:700}
[data-testid="stMetricLabel"]{color:#8B949E;font-size:.75rem;text-transform:uppercase;letter-spacing:.1rem}
h1,h2,h3{color:#E8E9EB;font-weight:600}
h1{border-bottom:1px solid #FFD700;padding-bottom:.4rem}
[data-baseweb="tab"][aria-selected="true"]{color:#FFD700!important;border-bottom-color:#FFD700!important}
.stDataFrame{font-family:monospace;font-size:.88rem}
.tag{display:inline-block;background:#1E2329;border:1px solid #444;color:#ccc;
     padding:.15rem .5rem;border-radius:3px;font-size:.75rem;margin:.1rem}
.up{color:#00D4AA;font-weight:600}.down{color:#FF4757;font-weight:600}
.chip{display:inline-block;background:#1E2329;border:1px solid #FFD700;color:#FFD700;
      padding:.2rem .7rem;border-radius:4px;font-weight:600;margin-right:.4rem}
</style>""", unsafe_allow_html=True)


# ── 工具函数 ────────────────────────────────────────────────────

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
def latest_qfin_file(scan_date: str) -> str | None:
    """找当天最新的 q-fin JSONL 文件"""
    log_dir = Path(CFG["paths"]["q_fin_logs"])
    ymd = scan_date.replace("-", "")
    matches = sorted(log_dir.glob(f"q-fin_top*_{ymd}_*.jsonl"), reverse=True)
    return str(matches[0]) if matches else None


@st.cache_data(ttl=120)
def latest_qnews_file(scan_date: str) -> str | None:
    log_dir = Path(CFG["paths"]["q_news_logs"])
    ymd = scan_date.replace("-", "")
    matches = sorted(log_dir.glob(f"q-news_top*_{ymd}_*.jsonl"), reverse=True)
    return str(matches[0]) if matches else None


@st.cache_data(ttl=120)
def all_qfin_today(scan_date: str) -> list[dict]:
    """合并当天所有 q-fin 文件，按 score 去重"""
    log_dir = Path(CFG["paths"]["q_fin_logs"])
    ymd = scan_date.replace("-", "")
    best: dict[str, dict] = {}
    for f in log_dir.glob(f"q-fin_top*_{ymd}_*.jsonl"):
        for r in load_jsonl(str(f)):
            code = r.get("code")
            if code and r.get("score", 0) >= best.get(code, {}).get("score", -1):
                best[code] = r
    return sorted(best.values(), key=lambda x: x.get("score", 0), reverse=True)


def is_kechuang(code: str) -> bool:
    return code.startswith(("688", "300", "301"))


def star_color(stars: str) -> str:
    n = stars.count("★") if stars else 0
    if n >= 4: return "🟢"
    if n >= 3: return "🟡"
    return "🔴"


def cninfo_url(code: str) -> str:
    return f"http://www.cninfo.com.cn/new/disclosure/stock?stockCode={code}"


def available_dates() -> list[str]:
    days = set()
    for cmd in ("q_fin_logs", "q_news_logs"):
        p = Path(CFG["paths"][cmd])
        if p.exists():
            for f in p.glob("*.jsonl"):
                m = re.search(r"(\d{8})", f.name)
                if m:
                    d = m.group(1)
                    days.add(f"{d[:4]}-{d[4:6]}-{d[6:]}")
    # 也把有 ipo cache 的日期加进来
    if CACHE_DIR.exists():
        for f in CACHE_DIR.glob("ipo_*.json"):
            d = f.stem[4:]  # ipo_20260501 → 20260501
            days.add(f"{d[:4]}-{d[4:6]}-{d[6:]}")
    return sorted(days, reverse=True) or [str(date.today())]


# ── Sidebar ──────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### 📅 日期")
    days = available_dates()
    qp   = st.query_params
    default = qp.get("date", days[0])
    idx  = days.index(default) if default in days else 0
    sel  = st.selectbox("选日期", days, index=idx)
    st.query_params["date"] = sel

    st.markdown("---")
    if st.button("🔄 刷新"):
        st.cache_data.clear()
        st.rerun()
    st.markdown("---")
    st.caption("福宝抓股 · Quant Dashboard")

st.markdown(f"# 📈 福宝抓股 <span class='chip'>{sel}</span>", unsafe_allow_html=True)

tab1, tab2, tab3, tab4 = st.tabs(["🌱 形态选股", "📰 公告热点", "🚀 科创突破", "🆕 新股"])


# ════════════════════════════════════════════════════════════════
# TAB 1  形态选股
# ════════════════════════════════════════════════════════════════
with tab1:
    records = [r for r in all_qfin_today(sel) if not is_kechuang(r.get("code",""))]
    if not records:
        st.info(f"{sel} 无形态选股数据（q-pick-today 还未运行？）")
    else:
        # 汇总表
        rows = []
        for r in records:
            v = r.get("verdict") or {}
            k = r.get("kline") or {}
            rows.append({
                "代码": r.get("code"), "名称": r.get("name"),
                "score": r.get("score"), "评级": v.get("stars",""),
                "一句话": (v.get("one_liner") or "")[:35],
                "现价": k.get("current_price"), "20日%": f"{(k.get('ret20') or 0)*100:+.1f}",
                "入场建议": (v.get("entry_suggestion") or "")[:25],
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        st.markdown("### 🔍 详情")
        for r in records:
            v   = r.get("verdict") or {}
            er  = r.get("entity_research") or {}
            k   = r.get("kline") or {}
            code = r.get("code","")
            title = f"{star_color(v.get('stars',''))} **{r.get('name','')} ({code})** — score={r.get('score')} {v.get('stars','')}"
            with st.expander(title):
                c1, c2, c3 = st.columns(3)
                c1.metric("现价", f"¥{k.get('current_price','-')}")
                c2.metric("20日涨幅", f"{(k.get('ret20') or 0)*100:+.1f}%")
                c3.metric("60日涨幅", f"{(k.get('ret60') or 0)*100:+.1f}%")

                st.markdown(f"**结论**: {v.get('one_liner','-')}")
                st.markdown(f"**入场**: {v.get('entry_suggestion','-')}")
                if v.get("key_risks"):
                    st.markdown("**风险**: " + " | ".join(v["key_risks"][:3]))
                if v.get("themes"):
                    for t in v["themes"]:
                        st.markdown(f"<span class='tag'>{t}</span>", unsafe_allow_html=True)

                if er.get("chain"):
                    with st.expander("🕸 股东实体研究链"):
                        st.json(er["chain"])

                f_data = r.get("fundamentals") or {}
                ann    = r.get("announcements_90d") or {}
                co1, co2 = st.columns(2)
                with co1:
                    st.markdown("**基本面**")
                    eps = f_data.get("eps")
                    np_ = f_data.get("net_profit")
                    if eps is not None:
                        st.caption(f"EPS {eps:.3f} | 净利润 {np_/1e8:.2f}亿" if np_ else f"EPS {eps:.3f}")
                with co2:
                    st.markdown(f"**近期公告** (共{ann.get('total',0)}条)")
                    for t in (ann.get("key_titles") or [])[:3]:
                        st.caption(f"• {t.get('title','')[:40]}")
                    st.markdown(f"[cninfo 公告页]({cninfo_url(code)})")


# ════════════════════════════════════════════════════════════════
# TAB 2  公告热点
# ════════════════════════════════════════════════════════════════
with tab2:
    path = latest_qnews_file(sel)
    if not path:
        st.info(f"{sel} 无 q-news 数据")
    else:
        records = load_jsonl(path)
        if not records:
            st.warning("q-news 文件为空")
        else:
            # 汇总
            rows = []
            for r in records:
                cs = r.get("concept_status") or {}
                started = cs.get("已启动")
                flag = "🔴启动" if started else ("🟢未启动" if started is False else "⚪")
                rows.append({
                    "代码": r.get("code"), "名称": r.get("name"),
                    "score": r.get("score"), "题材": cs.get("concept",""),
                    "状态": flag, "触发事件数": len(r.get("triggered_events",[])),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

            st.markdown("### 🔍 详情 + 原始链接")
            for r in records:
                cs     = r.get("concept_status") or {}
                code   = r.get("code") or ""
                started = cs.get("已启动")
                flag   = "🔴" if started else ("🟢" if started is False else "⚪")
                title  = f"{flag} **{r.get('name','')} ({code})** · {cs.get('concept','-')} · score={r.get('score')}"
                with st.expander(title):
                    st.markdown(f"**题材判断**: {cs.get('verdict', '-')}")

                    evs = r.get("triggered_events") or []
                    if evs:
                        st.markdown("**触发事件**")
                    for ev in evs:
                        src   = ev.get("source","")
                        ev_title = ev.get("title","")
                        url   = ev.get("url","") or ev.get("link","")
                        rule  = ev.get("rule") or {}

                        st.markdown("---")
                        if url:
                            st.markdown(f"**[{ev_title}]({url})** `{src}`")
                        else:
                            st.markdown(f"**{ev_title}** `{src}`")
                            # 公告类 → 构造 cninfo 链接
                            if code and src in ("cninfo", "akshare", "公告"):
                                st.markdown(f"[查看 {code} 全部公告]({cninfo_url(code)})")

                        st.caption(f"方向={rule.get('direction','-')} | 置信度={rule.get('confidence','-')}")

                        chain = ev.get("reasoning_chain") or []
                        if chain:
                            with st.expander("推理链"):
                                for step in chain:
                                    l, t, c = step.get("layer","?"), step.get("type",""), step.get("content","")
                                    w = step.get("weight")
                                    st.markdown(f"- L{l} `{t}` → {c}" + (f" `w={w}`" if w else ""))

                    if code:
                        st.markdown(f"[📋 cninfo 公告页]({cninfo_url(code)})")


# ════════════════════════════════════════════════════════════════
# TAB 3  科创突破
# ════════════════════════════════════════════════════════════════
with tab3:
    records = [r for r in all_qfin_today(sel) if is_kechuang(r.get("code",""))]
    if not records:
        st.info(f"{sel} 无科创/创业板突破数据（q-kechuang-batch 还未运行？）")
    else:
        rows = []
        for r in records:
            v = r.get("verdict") or {}
            k = r.get("kline") or {}
            exch = "科创板" if r.get("code","").startswith("688") else "创业板"
            rows.append({
                "交易所": exch, "代码": r.get("code"), "名称": r.get("name"),
                "score": r.get("score"), "评级": v.get("stars",""),
                "一句话": (v.get("one_liner") or "")[:35],
                "现价": k.get("current_price"), "20日%": f"{(k.get('ret20') or 0)*100:+.1f}",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        st.markdown("### 🔍 详情")
        for r in records:
            v    = r.get("verdict") or {}
            k    = r.get("kline") or {}
            code = r.get("code","")
            exch = "科创板" if code.startswith("688") else "创业板"
            title = f"{star_color(v.get('stars',''))} **{r.get('name','')} ({code})** [{exch}] score={r.get('score')} {v.get('stars','')}"
            with st.expander(title):
                c1, c2, c3 = st.columns(3)
                c1.metric("现价", f"¥{k.get('current_price','-')}")
                c2.metric("5日", f"{(k.get('ret5') or 0)*100:+.1f}%")
                c3.metric("20日", f"{(k.get('ret20') or 0)*100:+.1f}%")

                st.markdown(f"**突破结论**: {v.get('one_liner','-')}")
                st.markdown(f"**操盘建议**: {v.get('entry_suggestion','-')}")
                if v.get("key_risks"):
                    st.markdown("**风险**: " + " | ".join(v["key_risks"][:3]))

                ann = r.get("announcements_90d") or {}
                st.markdown(f"**近 90 日公告** {ann.get('total',0)} 条")
                for t in (ann.get("key_titles") or [])[:3]:
                    st.caption(f"• {t.get('title','')[:45]}")
                st.markdown(f"[📋 cninfo 公告页]({cninfo_url(code)})")

                f_data = r.get("fundamentals") or {}
                eps = f_data.get("eps")
                np_ = f_data.get("net_profit")
                if eps is not None:
                    profit_str = f"{np_/1e8:.2f}亿" if np_ else "-"
                    st.caption(f"EPS {eps:.3f} | 净利润 {profit_str}")


# ════════════════════════════════════════════════════════════════
# TAB 4  新股
# ════════════════════════════════════════════════════════════════
with tab4:
    ymd = sel.replace("-", "")
    ipo_path = CACHE_DIR / f"ipo_{ymd}.json"

    if not ipo_path.exists():
        st.info("新股数据未生成，运行: `python scripts/q-ipo-watch`")
        if st.button("立即扫描新股"):
            import subprocess
            with st.spinner("扫描中..."):
                subprocess.run([sys.executable, str(ROOT / "scripts/q-ipo-watch")],
                               timeout=120)
            st.cache_data.clear()
            st.rerun()
    else:
        ipos = load_json(str(ipo_path))
        if not ipos:
            st.warning("近 7 天无新股上市")
        else:
            st.markdown(f"**近 7 天新股** · 共 {len(ipos)} 只")

            rows = []
            for r in ipos:
                chg = r.get("change_pct", 0)
                rows.append({
                    "代码":      r.get("code"),
                    "名称":      r.get("name"),
                    "上市日期":  r.get("ipo_date"),
                    "已上市天数": r.get("days_listed"),
                    "发行价":    r.get("ipo_price"),
                    "最新价":    r.get("last_price"),
                    "涨跌幅%":   chg,
                    "行业":      r.get("industry",""),
                    "上市板":    r.get("market",""),
                })

            df = pd.DataFrame(rows)
            st.dataframe(
                df.style.applymap(
                    lambda v: "color:#00D4AA" if isinstance(v, float) and v > 0
                              else ("color:#FF4757" if isinstance(v, float) and v < 0 else ""),
                    subset=["涨跌幅%"]
                ),
                use_container_width=True,
                hide_index=True,
            )

            st.markdown("### 🔍 个股详情")
            for r in ipos:
                code = r.get("code","")
                chg  = r.get("change_pct", 0)
                chg_str = f"+{chg:.1f}%" if chg >= 0 else f"{chg:.1f}%"
                color = "up" if chg >= 0 else "down"
                title = f"**{r.get('name','')} ({code})** 上市 {r.get('ipo_date')} · <span class='{color}'>{chg_str}</span>"
                with st.expander(title, unsafe_allow_html=True):
                    c1, c2, c3 = st.columns(3)
                    c1.metric("发行价", f"¥{r.get('ipo_price','-')}")
                    c2.metric("最新价", f"¥{r.get('last_price','-')}")
                    c3.metric("涨跌", chg_str)

                    if r.get("industry"):
                        st.caption(f"行业: {r['industry']}  |  板块: {r.get('market','')}")
                    st.markdown(f"[📋 cninfo 公告页]({cninfo_url(code)})")
                    st.markdown(f"[东方财富 行情](https://quote.eastmoney.com/{'sh' if code.startswith('6') else 'sz'}{code}.html)")
