"""
A股解禁期过滤工具

主要锁定期节点（从上市日起算）：
  180天  — 网下机构配售（最普遍，对股价压力最大）
  365天  — 一般发起人/战略配售
  1095天 — 控股股东/实控人（36个月）

用法：
    from core.lockup_filter import is_near_lockup
    if is_near_lockup(ipo_date):
        return None  # 解禁压力窗口，跳过
"""

from datetime import date, timedelta

LOCKUP_PERIODS = [180, 365, 1095]  # 天
DEFAULT_WINDOW = 30                 # 解禁日前后 N 天内视为危险窗口


def is_near_lockup(ipo_date, today: date | None = None, window: int = DEFAULT_WINDOW) -> bool:
    """
    ipo_date: date 或 datetime 对象（或可调用 .date() 的 pandas Timestamp）
    返回 True 表示当前处于某个解禁窗口内，建议跳过。
    """
    if today is None:
        today = date.today()
    if hasattr(ipo_date, "date"):
        ipo_date = ipo_date.date()

    for lock_days in LOCKUP_PERIODS:
        expiry = ipo_date + timedelta(days=lock_days)
        if abs((expiry - today).days) <= window:
            return True
    return False


def lockup_info(ipo_date, today: date | None = None) -> dict:
    """返回最近解禁节点信息，用于日志/推送。"""
    if today is None:
        today = date.today()
    if hasattr(ipo_date, "date"):
        ipo_date = ipo_date.date()

    nearest = None
    nearest_days = None
    for lock_days in LOCKUP_PERIODS:
        expiry = ipo_date + timedelta(days=lock_days)
        diff = (expiry - today).days
        if nearest_days is None or abs(diff) < abs(nearest_days):
            nearest = expiry
            nearest_days = diff

    return {
        "nearest_expiry": str(nearest),
        "days_to_expiry": nearest_days,  # 负数=已过, 正数=未到
    }
