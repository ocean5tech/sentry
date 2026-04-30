"""
cninfo 全市场重大公告扫描.
每天拉最近 N 个交易日的"重大事项"公告, 按关键词过滤出高价值事件.
"""

import json
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Optional


# ── 高影响关键词 (标题含这些才值得 LLM 分析) ─────────────────────
HIGH_IMPACT = [
    # 控制权/股权
    "控股股东", "实际控制人", "控制权", "要约收购", "股权转让",
    "入主", "接盘", "举牌",
    # 重组/并购
    "重大资产重组", "重大重组", "并购", "资产注入", "借壳",
    "合并", "收购",
    # 业绩
    "业绩预亏", "亏损", "业绩大幅", "扭亏", "超预期",
    # 重大合同/战略
    "重大合同", "战略合作", "框架协议",
    # 政策/资质
    "获批", "中标", "专利",
]

# ── 低价值类型 (直接跳过, 不做 LLM) ─────────────────────────────
SKIP_TYPES = {
    "募集资金使用情况报告", "定期报告摘要", "临时停牌",
    "复牌", "分红", "回购进展",
}

# ── 内容缓存文件 ─────────────────────────────────────────────────
_CACHE_FILE = Path(__file__).parent.parent / "data_cache" / "cninfo_scan_cache.json"


def _load_cache() -> dict:
    try:
        if _CACHE_FILE.exists():
            return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_cache(cache: dict):
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _fetch_one_day(date_str: str, cache: dict) -> list[dict]:
    """拉单日重大事项公告, 有缓存直接返回."""
    if date_str in cache:
        return cache[date_str]

    import akshare as ak
    try:
        df = ak.stock_notice_report(symbol="重大事项", date=date_str)
    except Exception as e:
        print(f"[cninfo_scanner] {date_str} 拉取失败: {e}", flush=True)
        return []

    rows = []
    for _, r in df.iterrows():
        rows.append({
            "code":  str(r.get("代码", "") or "").zfill(6),
            "name":  str(r.get("名称", "") or ""),
            "title": str(r.get("公告标题", "") or ""),
            "type":  str(r.get("公告类型", "") or ""),
            "date":  str(r.get("公告日期", date_str) or date_str),
            "url":   str(r.get("网址", "") or ""),
        })

    cache[date_str] = rows
    _save_cache(cache)
    return rows


def _is_high_impact(row: dict) -> bool:
    """标题含高影响关键词 且 类型不在跳过列表."""
    if row["type"] in SKIP_TYPES:
        return False
    title = row["title"]
    return any(kw in title for kw in HIGH_IMPACT)


def scan_recent(n_days: int = 7, end_date: Optional[date] = None,
                sleep_between: float = 1.0) -> list[dict]:
    """
    扫描最近 n_days 个交易日的重大公告.
    返回过滤后的高影响公告列表 (去重 by code+title).
    """
    if end_date is None:
        end_date = date.today()

    # 生成日期列表 (往前推 n_days × 2 天取足 n_days 个工作日)
    dates = []
    d = end_date
    while len(dates) < n_days:
        if d.weekday() < 5:          # 只取工作日 (不精确但够用)
            dates.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)

    cache = _load_cache()
    all_rows: list[dict] = []
    seen: set[str] = set()

    for date_str in dates:
        rows = _fetch_one_day(date_str, cache)
        for r in rows:
            if not r["code"] or r["code"] == "000000":
                continue
            key = f"{r['code']}|{r['title'][:30]}"
            if key in seen:
                continue
            seen.add(key)
            if _is_high_impact(r):
                all_rows.append(r)
        time.sleep(sleep_between)

    # 按日期降序
    all_rows.sort(key=lambda x: x["date"], reverse=True)
    return all_rows
