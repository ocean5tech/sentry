"""Layer 2 股东户数历史. akshare stock_zh_a_gdhs_detail_em."""


def analyze(code: str, ak_module, cache, n_periods: int = 8) -> list[dict]:
    """返回最近 N 期股东户数变化."""
    def _do():
        try:
            df = ak_module.stock_zh_a_gdhs_detail_em(symbol=code)
        except Exception as e:
            return {"_err": f"{type(e).__name__}: {e}", "rows": []}
        if df is None or df.empty:
            return {"rows": []}
        date_col = next((c for c in ["截至日期", "股东户数统计截止日", "date"] if c in df.columns), None)
        count_col = next((c for c in ["股东户数-本次", "股东户数", "户数"] if c in df.columns), None)
        chg_col = next((c for c in ["股东户数-增减", "增减", "户数增减"] if c in df.columns), None)
        chg_pct_col = next((c for c in ["股东户数-增减比例", "增减比例", "户均持股市值"] if c in df.columns), None)
        avg_mv_col = next((c for c in ["户均持股市值"] if c in df.columns), None)
        if not date_col or not count_col:
            return {"_err": "missing date/count cols", "rows": []}
        rows = []
        for _, r in df.iterrows():
            d = str(r[date_col])[:10]
            try:
                cnt = int(float(str(r[count_col]).replace(",", "")))
            except Exception:
                cnt = 0
            chg_pct = None
            if chg_pct_col and chg_pct_col != avg_mv_col:
                try:
                    chg_pct = float(str(r[chg_pct_col]).replace("%", ""))
                except Exception:
                    pass
            avg_mv = None
            if avg_mv_col:
                try:
                    avg_mv = float(str(r[avg_mv_col]).replace(",", "")) / 10000  # 转万元
                except Exception:
                    pass
            rows.append({"date": d, "count": cnt, "chg_pct": chg_pct, "avg_mv_wan": avg_mv})
        rows.sort(key=lambda x: x["date"], reverse=True)
        return {"rows": rows[:n_periods]}
    res = cache.get_or_set("holder_count", (code, n_periods), _do)
    return res.get("rows", [])
