"""
q-dashboard: 华尔街金融页面风格的 q-* 结果展示
读 q-seed/q-fin/q-news 的 logs/*.jsonl, 按交易日筛选, 4 个 tab.
"""

import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

ROOT = Path(__file__).parent
CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


# ─────── 页面配置 + CSS 美化 ────────────────────────────────
st.set_page_config(
    page_title="Quant Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    /* 金融感 typography */
    .main, [data-testid="stSidebar"] {
        font-family: 'SF Mono', 'Monaco', 'Inconsolata', 'Roboto Mono', monospace;
    }

    /* 数字突出 */
    [data-testid="stMetricValue"] {
        font-size: 2.2rem;
        color: #FFD700;
        font-weight: 700;
        font-family: 'SF Mono', monospace;
    }
    [data-testid="stMetricLabel"] {
        color: #8B949E;
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 0.1rem;
    }
    [data-testid="stMetricDelta"] {
        color: #00D4AA;
    }

    /* 标题 */
    h1, h2, h3 {
        color: #E8E9EB;
        font-weight: 600;
        letter-spacing: 0.05rem;
    }
    h1 {
        border-bottom: 1px solid #FFD700;
        padding-bottom: 0.5rem;
    }

    /* tab 样式 */
    [data-baseweb="tab"] {
        font-weight: 600;
        font-family: monospace;
        letter-spacing: 0.05rem;
    }
    [data-baseweb="tab"][aria-selected="true"] {
        color: #FFD700 !important;
        border-bottom-color: #FFD700 !important;
    }

    /* dataframe 表格 */
    .stDataFrame {
        font-family: monospace;
        font-size: 0.9rem;
    }

    /* json 块 */
    .stJson {
        background-color: #0A0E13 !important;
        border-left: 3px solid #FFD700;
    }

    /* 涨跌色 */
    .up { color: #00D4AA; font-weight: 600; }
    .down { color: #FF4757; font-weight: 600; }
    .neutral { color: #8B949E; }

    /* 交易日 chip */
    .day-chip {
        display: inline-block;
        background: #1E2329;
        border: 1px solid #FFD700;
        color: #FFD700;
        padding: 0.3rem 0.8rem;
        border-radius: 4px;
        font-family: monospace;
        font-weight: 600;
        margin-right: 0.5rem;
    }
</style>
""", unsafe_allow_html=True)


# ─────── 数据加载 ───────────────────────────────────────────
@st.cache_data(ttl=60)
def list_log_files(log_dir: str) -> pd.DataFrame:
    """扫一个 logs 目录, 返回 (file, scan_date, top_n, mtime) 的 DataFrame."""
    p = Path(log_dir)
    if not p.exists():
        return pd.DataFrame(columns=["file", "scan_date", "top_n", "mtime"])
    rows = []
    for f in p.glob("q-*_top*_*.jsonl"):
        m = re.match(r"q-(seed|fin|news)_top(\d+)_(\d{8})_(\d{4})\.jsonl", f.name)
        if not m:
            continue
        cmd, top_n, ymd, hm = m.groups()
        scan_date = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
        rows.append({
            "file": str(f),
            "name": f.name,
            "cmd": cmd,
            "scan_date": scan_date,
            "top_n": int(top_n),
            "mtime": f.stat().st_mtime,
            "ts": f"{ymd}_{hm}",
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("ts", ascending=False)
    return df


@st.cache_data(ttl=60)
def load_jsonl(path: str) -> list[dict]:
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    except Exception as e:
        st.error(f"读 {path} 失败: {e}")
    return out


@st.cache_data(ttl=60)
def load_cost_log(path: str) -> pd.DataFrame:
    """cost_log.jsonl → DataFrame."""
    rows = []
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        return pd.DataFrame()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], unit="s")
    df["date"] = df["ts"].dt.strftime("%Y-%m-%d")
    return df


@st.cache_data(ttl=86400)
def get_trading_days() -> set[str]:
    """从 q-* logs 的 scan_date 集合派生交易日 (无需 akshare). 简化方案."""
    days = set()
    for cmd in ("seed", "fin", "news"):
        df = list_log_files(CFG["paths"][f"q_{cmd}_logs"])
        if not df.empty:
            days.update(df["scan_date"].unique().tolist())
    return days


# ─────── Sidebar: 日期选择 ──────────────────────────────────
with st.sidebar:
    st.markdown("### 📅 交易日")
    days = sorted(get_trading_days(), reverse=True)
    if not days:
        st.warning("无 logs, 先跑 q-seed/q-fin/q-news")
        st.stop()

    # URL ?date=2026-04-27 自动选
    qp = st.query_params
    default_date = qp.get("date", days[0])
    try:
        default_idx = days.index(default_date) if default_date in days else 0
    except ValueError:
        default_idx = 0

    selected_date = st.selectbox(
        "选日期",
        days,
        index=default_idx,
        format_func=lambda d: d,
    )
    # 同步 URL
    st.query_params["date"] = selected_date

    st.markdown("---")
    st.markdown("### 🛠 快捷")
    if st.button("🔄 刷新 cache"):
        st.cache_data.clear()
        st.rerun()

    st.markdown("---")
    st.markdown("### 📁 路径")
    for k, v in CFG["paths"].items():
        st.caption(f"`{k}`: `{v[-30:]}`")


# ─────── 顶部标题 ───────────────────────────────────────────
st.markdown(f"# 📈 Quant Dashboard <span class='day-chip'>{selected_date}</span>", unsafe_allow_html=True)


# ─────── 4 个 tab ───────────────────────────────────────────
tab_overview, tab_seed, tab_fin, tab_news = st.tabs([
    "📊 OVERVIEW",
    "🌱 Q-SEED",
    "💼 Q-FIN",
    "📰 Q-NEWS",
])


# ───── TAB 1: OVERVIEW ─────
with tab_overview:
    col1, col2, col3, col4 = st.columns(4)

    # 各命令的最新 jsonl 文件
    n_seed = n_fin = n_news = 0
    fin_cost = 0.0
    news_cost = 0.0

    for cmd, col, label in [
        ("seed", col1, "Q-SEED"),
        ("fin", col2, "Q-FIN"),
        ("news", col3, "Q-NEWS"),
    ]:
        files = list_log_files(CFG["paths"][f"q_{cmd}_logs"])
        if not files.empty:
            today = files[files["scan_date"] == selected_date]
            if not today.empty:
                latest = today.iloc[0]
                records = load_jsonl(latest["file"])
                n = len(records)
                if cmd == "seed": n_seed = n
                elif cmd == "fin": n_fin = n
                elif cmd == "news": n_news = n
                col.metric(label, f"{n}", f"top {latest['top_n']}")
            else:
                col.metric(label, "—", "今日无")
        else:
            col.metric(label, "—", "无数据")

    # token 总成本
    fin_cost_df = load_cost_log(CFG["paths"]["q_fin_cost_log"])
    if not fin_cost_df.empty:
        today_cost = fin_cost_df[fin_cost_df["date"] == selected_date]["cost_usd"].sum()
        month = selected_date[:7]
        month_cost = fin_cost_df[fin_cost_df["date"].str.startswith(month)]["cost_usd"].sum()
        col4.metric("TOKEN 花费", f"${today_cost:.4f}", f"月累计 ${month_cost:.2f}")
    else:
        col4.metric("TOKEN 花费", "$0.00", "无数据")

    st.markdown("---")
    st.markdown("### 🕐 执行履历")

    # 列出当日所有 logs 文件 (执行履历)
    timeline = []
    for cmd in ("seed", "fin", "news"):
        files = list_log_files(CFG["paths"][f"q_{cmd}_logs"])
        if not files.empty:
            today = files[files["scan_date"] == selected_date]
            for _, row in today.iterrows():
                timeline.append({
                    "时间": row["ts"][9:11] + ":" + row["ts"][11:],
                    "命令": f"q-{cmd}",
                    "TOP N": row["top_n"],
                    "文件": row["name"],
                })
    if timeline:
        st.dataframe(
            pd.DataFrame(timeline).sort_values("时间"),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info(f"{selected_date} 当日无执行记录")

    # 月度成本曲线
    if not fin_cost_df.empty:
        st.markdown("### 💰 Token 成本 (近 30 天)")
        last_30 = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        recent = fin_cost_df[fin_cost_df["date"] >= last_30]
        if not recent.empty:
            daily = recent.groupby("date")["cost_usd"].sum().reset_index()
            st.bar_chart(daily.set_index("date"), use_container_width=True)


# ───── 公用: 选当日最新文件 ─────
def latest_file_for(cmd: str, scan_date: str) -> dict | None:
    files = list_log_files(CFG["paths"][f"q_{cmd}_logs"])
    if files.empty:
        return None
    today = files[files["scan_date"] == scan_date]
    if today.empty:
        return None
    return today.iloc[0].to_dict()


# ───── TAB 2: Q-SEED ─────
with tab_seed:
    latest = latest_file_for("seed", selected_date)
    if not latest:
        st.info(f"{selected_date} 无 q-seed 输出")
    else:
        st.caption(f"📁 {latest['name']} · TOP {latest['top_n']}")
        records = load_jsonl(latest["file"])
        if not records:
            st.warning("文件为空")
        else:
            # 表格
            rows = []
            for r in records:
                rows.append({
                    "rank": r.get("rank"),
                    "code": r.get("code"),
                    "name": r.get("name"),
                    "score": r.get("score"),
                    "templates": ",".join(r.get("templates_matched", [])),
                    "best_template": r.get("best_template"),
                    "best_dist": r.get("best_dist"),
                    "kline_safety": (r.get("kline") or {}).get("kline_safety", ""),
                })
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)

            # 详情展开
            st.markdown("### 🔍 详情 (点击展开)")
            for r in records:
                with st.expander(f"#{r['rank']} {r.get('code')} {r.get('name')} (dist={r.get('best_dist')})"):
                    col1, col2 = st.columns(2)
                    with col1:
                        st.markdown("**Details**")
                        st.json(r.get("details", {}))
                    with col2:
                        st.markdown("**Kline 快照**")
                        st.json(r.get("kline", {}))


# ───── TAB 3: Q-FIN ─────
with tab_fin:
    latest = latest_file_for("fin", selected_date)
    if not latest:
        st.info(f"{selected_date} 无 q-fin 输出")
    else:
        st.caption(f"📁 {latest['name']} · TOP {latest['top_n']}")
        records = load_jsonl(latest["file"])
        if not records:
            st.warning("文件为空")
        else:
            # 表格
            rows = []
            for r in records:
                v = r.get("verdict") or {}
                rows.append({
                    "rank": r.get("rank"),
                    "code": r.get("code"),
                    "name": r.get("name"),
                    "score": r.get("score"),
                    "stars": v.get("stars", ""),
                    "one_liner": (v.get("one_liner") or "")[:50],
                    "yoy_ni": (r.get("fundamentals") or {}).get("yoy_net_profit"),
                    "kline_safety": (r.get("kline") or {}).get("kline_safety", ""),
                })
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)

            # 详情
            st.markdown("### 🔍 详情 (点击展开)")
            for r in records:
                v = r.get("verdict") or {}
                er = r.get("entity_research") or {}
                title = f"#{r['rank']} {r.get('code')} {r.get('name')} · score={r.get('score')} {v.get('stars','')}"
                with st.expander(title):
                    if v:
                        st.markdown("**🏅 Verdict**")
                        c1, c2 = st.columns(2)
                        c1.metric("Rating", f"{v.get('rating', '-')}", v.get('theme_hardness', ''))
                        c2.markdown(f"**入场建议**: {v.get('entry_suggestion', '-')}")
                        st.markdown(f"**结论**: {v.get('one_liner', '-')}")
                        if v.get("key_risks"):
                            st.markdown("**风险**:")
                            for r2 in v["key_risks"]:
                                st.markdown(f"- {r2}")

                    if er and er.get("chain"):
                        st.markdown("**🕸 Entity Research Chain**")
                        st.json(er["chain"])
                    elif er:
                        st.json(er)

                    st.markdown("**📊 Layer 2 数据**")
                    cols = st.columns(3)
                    cols[0].markdown("**Layer 1 触发**")
                    cols[0].json(r.get("layer1_triggers", {}))
                    cols[1].markdown("**Shareholders**")
                    cols[1].json((r.get("shareholders") or {}).get("major_new_entry") or {})
                    cols[2].markdown("**Fundamentals**")
                    cols[2].json(r.get("fundamentals") or {})


# ───── TAB 4: Q-NEWS ─────
with tab_news:
    latest = latest_file_for("news", selected_date)
    if not latest:
        st.info(f"{selected_date} 无 q-news 输出")
    else:
        st.caption(f"📁 {latest['name']} · TOP {latest['top_n']}")
        records = load_jsonl(latest["file"])
        if not records:
            st.warning("文件为空")
        else:
            # 表格
            rows = []
            for r in records:
                cs = r.get("concept_status") or {}
                rows.append({
                    "rank": r.get("rank"),
                    "code": r.get("code"),
                    "name": r.get("name"),
                    "score": r.get("score"),
                    "concept": cs.get("concept"),
                    "已启动": cs.get("已启动"),
                    "active_pct": cs.get("active_pct"),
                    "events": len(r.get("triggered_events", [])),
                })
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)

            # 详情
            st.markdown("### 🔍 详情 (点击展开)")
            for r in records:
                cs = r.get("concept_status") or {}
                started_emoji = "🔴" if cs.get("已启动") else ("🟢" if cs.get("已启动") is False else "⚪")
                title = f"#{r['rank']} {r.get('code') or '-'} {r.get('name','')} · {cs.get('concept','-')} {started_emoji}"
                with st.expander(title):
                    st.markdown(f"**Concept Status**: {cs.get('verdict', '-')}")

                    st.markdown("**📰 Triggered Events**")
                    for ev in r.get("triggered_events", []):
                        st.markdown(f"---")
                        st.markdown(f"**{ev.get('title', '')}** ({ev.get('source', '')})")
                        rule = ev.get("rule") or {}
                        st.caption(f"rule={rule.get('id')} · confidence={rule.get('confidence')} · direction={rule.get('direction')}")
                        chain = ev.get("reasoning_chain") or []
                        if chain:
                            st.markdown("**推理链**:")
                            for step in chain:
                                layer = step.get("layer", "?")
                                t = step.get("type", "")
                                content = step.get("content", "")
                                weight = step.get("weight")
                                w_str = f" `w={weight}`" if weight is not None else ""
                                st.markdown(f"- L{layer} `{t}` → {content}{w_str}")
