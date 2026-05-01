"""Layer 2 十大流通股东. akshare 接口字段不稳定, 多 fallback."""

from datetime import date, timedelta


def _is_partnership(name: str) -> bool:
    return any(k in name for k in ["合伙企业", "合伙"])


def _is_institution(name: str) -> bool:
    return any(k in name for k in ["基金", "公募", "私募", "资产管理", "投资", "证券", "保险"])


def _is_state_owned(name: str) -> bool:
    return any(k in name for k in [
        "国有资产", "国资", "国家控股",
        "财政部", "中央汇金",                    # 国家直接持股
        "国家电网", "南方电网",                  # 电力央企
        "中国石油", "中国石化", "中国移动", "中国联通", "中国电信",  # 三桶油/三大运营商
        "国家开发", "中国建投", "中国诚通",      # 国开/国有资本运营
    ]) or (
        # 以"中国"开头且含"集团"/"股份"/"控股" → 大概率央企
        name.startswith("中国") and any(k in name for k in ["集团", "股份有限公司", "控股"])
    )


def _entity_type(name: str) -> str:
    if _is_partnership(name):
        return "产业资本"
    if _is_state_owned(name):
        return "国资"
    if _is_institution(name):
        return "机构"
    return "其他"


def _to_em_symbol(code: str) -> str:
    if code.startswith(("60", "68", "9")):
        return f"sh{code}"
    elif code.startswith(("0", "30", "20")):
        return f"sz{code}"
    elif code.startswith(("4", "8")):
        return f"bj{code}"
    return code


def _try_fetch(ak_module, code: str, report_date: str):
    """尝试多个 akshare 接口拿十大流通."""
    sym = _to_em_symbol(code)
    candidates = [
        ("stock_gdfx_free_top_10_em", {"symbol": sym, "date": report_date.replace("-", "")}),
        ("stock_gdfx_top_10_em", {"symbol": sym, "date": report_date.replace("-", "")}),
    ]
    last_err = None
    for fn_name, kwargs in candidates:
        try:
            fn = getattr(ak_module, fn_name, None)
            if fn is None:
                continue
            df = fn(**kwargs)
            if df is not None and not df.empty:
                return df, fn_name, None
        except Exception as e:
            last_err = f"{fn_name}: {type(e).__name__}: {e}"
            continue
    return None, None, last_err


def _latest_report_date_candidates() -> list[str]:
    """生成可能的最新报告期 (近期季报披露周期)."""
    today = date.today()
    out = []
    for y in [today.year, today.year - 1]:
        for md in ["12-31", "09-30", "06-30", "03-31"]:
            out.append(f"{y}-{md}")
    out.sort(reverse=True)
    # 过滤未到期的
    return [d for d in out if date.fromisoformat(d) <= today]


def analyze(code: str, kw_cfg: dict, ak_module, cache) -> dict:
    """返回 shareholders schema. 失败 graceful 返回部分字段."""
    def _do():
        for rd in _latest_report_date_candidates():
            df, src, err = _try_fetch(ak_module, code, rd)
            if df is not None:
                # 提取字段, 兼容多种 akshare 字段命名
                rows = []
                for i, r in df.iterrows():
                    name_col = next((c for c in ["股东名称", "股东", "name"] if c in df.columns), None)
                    pct_col = next((c for c in ["持股比例", "占总流通股本持股比例", "持股比例 %", "比例"] if c in df.columns), None)
                    chg_col = next((c for c in ["增减变化", "变化", "持股变动"] if c in df.columns), None)
                    if not name_col or not pct_col:
                        continue
                    nm = str(r[name_col])
                    try:
                        pct = float(str(r[pct_col]).replace("%", ""))
                    except Exception:
                        pct = 0.0
                    chg_raw = str(r.get(chg_col, "")) if chg_col else ""
                    rows.append({
                        "rank": i + 1,
                        "name": nm,
                        "pct": pct,
                        "change": chg_raw,
                        "is_partnership": _is_partnership(nm),
                        "entity_type": _entity_type(nm),
                    })
                if not rows:
                    continue
                return {"report_date": rd, "src": src, "top10_free": rows}
            # else 继续往前找
        return {"_err": "no shareholders data found", "top10_free": []}

    raw = cache.get_or_set("shareholders", (code,), _do)

    top10 = raw.get("top10_free", [])
    rd = raw.get("report_date", None)
    err = raw.get("_err")

    # 显式 "新进" 标记 (akshare 有时 change 列为空, 该字段不可靠)
    explicit_new = [r for r in top10 if "新进" in str(r.get("change", ""))]
    # major_new_entry 判定: 优先用显式新进, 否则把"持股 ≥ 5% 的产业资本/合伙企业"当作潜在新进
    # (这是基于宏景/长龄等借壳案例: top1 是新成立合伙企业 = 高概率新入主)
    new_entries_count = len(explicit_new)
    top1_pct = top10[0]["pct"] if top10 else 0.0
    concentration = sum(r["pct"] for r in top10)

    large_threshold = kw_cfg.get("shareholders", {}).get("large_new_entry_pct", 0.05) * 100  # 转 %

    major = None
    # 路径 1: 显式新进
    for r in explicit_new:
        if r["pct"] >= large_threshold:
            major = {
                "name": r["name"],
                "pct": r["pct"],
                "type": r["entity_type"],
                "warning_emoji": "🔥" if r["pct"] >= 10 else "⚠️",
                "detection": "explicit_new_entry",
            }
            break
    # 路径 2: top10 里产业资本/合伙企业且 pct ≥ 5% 视为潜在新入主 (借壳模式特征)
    if major is None:
        for r in top10:
            if r["entity_type"] == "产业资本" and r["pct"] >= large_threshold:
                major = {
                    "name": r["name"],
                    "pct": r["pct"],
                    "type": r["entity_type"],
                    "warning_emoji": "🔥" if r["pct"] >= 10 else "⚠️",
                    "detection": "partnership_top_holder",
                }
                break

    state_owned_pct = round(sum(
        r["pct"] for r in top10 if r.get("entity_type") == "国资"
    ), 2)

    return {
        "report_date": rd,
        "top10_free": top10,
        "new_entries_count": new_entries_count,
        "top1_pct": top1_pct,
        "top10_concentration_pct": round(concentration, 2),
        "state_owned_pct": state_owned_pct,          # 十大流通股东中国资合计持股%
        "major_new_entry": major,
        "_err": err,
    }
