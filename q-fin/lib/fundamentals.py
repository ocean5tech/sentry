"""
财报数据高层接口.

数据源优先级:
  1. 本地 TDX gpcw .dat (lib/gpcw_loader.py) — 主路径, 离线, 快, 字段全
  2. baostock fallback (老股票 / tdx_fin 缺数据 时) — 慢, 但兼容性好
  3. 都没数据 → 返回空字典, evidence 标记缺失

字段映射: config/gpcw_field_map.yaml
   只暴露 verified=true 的字段对外, unverified 字段日志警告但不返回.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np
import yaml

from gpcw_loader import GpcwStore


@dataclass
class FieldDef:
    name: str
    label: str
    idx: int
    unit: str
    verified: bool


@dataclass
class FundamentalsConfig:
    fin_dir: Path
    field_map_path: Path
    use_unverified_fields: bool = False
    load_recent_periods: int = 8   # 8 期 = 2 年, 够算 YoY


class FundamentalsService:
    """整合 gpcw_loader + 字段映射 + 同比计算."""

    def __init__(self, cfg: FundamentalsConfig):
        self.cfg = cfg
        self.store = GpcwStore(cfg.fin_dir)
        self.store.load_recent(cfg.load_recent_periods)
        self._field_map: dict[str, FieldDef] = {}
        self._yoy_specs: list[dict] = []
        self._derived_specs: list[dict] = []
        self._load_field_map()

    def _load_field_map(self):
        with open(self.cfg.field_map_path, encoding="utf-8") as f:
            mp = yaml.safe_load(f)
        for f in mp.get("fields", []):
            fd = FieldDef(
                name=f["name"],
                label=f.get("label", f["name"]),
                idx=int(f["idx"]),
                unit=f.get("unit", ""),
                verified=bool(f.get("verified", False)),
            )
            self._field_map[fd.name] = fd
        self._yoy_specs = mp.get("yoy", []) or []
        self._derived_specs = mp.get("derived", []) or []

    def loaded_periods(self) -> list[date]:
        return [r.period for r in self.store._reports]

    def get_latest(self, code: str) -> Optional[dict]:
        """返回该 code 最新一期的 fundamentals 字典 (含 period, 各 verified 字段, derived, yoy).
        没数据 → None.
        """
        latest = self.store.latest_for_code(code)
        if latest is None:
            return None
        period, arr = latest
        out = {
            "code": code,
            "period": period.isoformat(),
            "data_source": "tdx_gpcw",
            "src_file": f"gpcw{period.strftime('%Y%m%d')}.dat",
        }

        # 已验证字段
        for fname, fd in self._field_map.items():
            if not fd.verified and not self.cfg.use_unverified_fields:
                continue
            try:
                v = float(arr[fd.idx])
                if abs(v) < 1e-9:
                    v = 0.0
            except (IndexError, ValueError):
                v = None
            out[fname] = v
            if not fd.verified:
                out[f"_{fname}_unverified"] = True

        # 计算 derived 字段 (只用 verified 输入)
        for d in self._derived_specs:
            if not d.get("safe", False):
                continue
            try:
                # 简易表达式求值, 仅允许 / 和已验证字段
                expr = d["formula"]
                ctx = {k: out.get(k) for k in self._field_map.keys() if out.get(k) is not None}
                val = eval(expr, {"__builtins__": {}}, ctx)
                out[d["name"]] = round(float(val), 6) if val is not None else None
            except Exception:
                out[d["name"]] = None

        # YoY 计算
        history = self.store.all_periods_for_code(code)
        for spec in self._yoy_specs:
            base = spec["base_field"]
            fd = self._field_map.get(base)
            if fd is None or not fd.verified:
                continue
            cur_val = out.get(base)
            if cur_val is None or cur_val == 0:
                out[spec["name"]] = None
                continue
            # 找 4 期 (1 年) 前的同期 (匹配 month-day)
            cur_md = (period.month, period.day)
            same_period_last_year = None
            target_year = period.year - 1
            for p, hist_arr in history:
                if p.year == target_year and (p.month, p.day) == cur_md:
                    same_period_last_year = float(hist_arr[fd.idx])
                    break
            if same_period_last_year is None or same_period_last_year == 0:
                out[spec["name"]] = None
                continue
            yoy_pct = (cur_val - same_period_last_year) / abs(same_period_last_year) * 100
            out[spec["name"]] = round(yoy_pct, 2)

        return out
