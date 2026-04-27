"""
TDX gpcw 财报二进制解析器.

文件格式 (单文件 = 单期财报快照):
- Header (20 字节):
    [0:2]   uint16  version (=1)
    [2:6]   reserved
    [6:10]  uint32  N (股票数量)
    [10:12] uint16  index entry size (=11)
    [12:16] uint32  data record size (=2336 字节 = 584 × 4)
    [16:20] padding
- Index (N × 11 字节):
    [0:6]   ASCII   stock code (6 chars, e.g. "605389")
    [6]     0x00    null terminator
    [7:11]  uint32  data offset (file 起始算)
- Data (N × 2336 字节):
    584 × float32  原始字段 (含义见 config/gpcw_field_map.yaml)

跨期查询: 不同 .dat 覆盖度不同 (Q1 季报刚开始时只有几百家披露, Q3 季报覆盖最全).
使用 latest_available_for_code(code) 自动从最新期回溯找第一份有该 code 的 .dat.
"""

import re
import struct
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

import numpy as np


HEADER_SIZE = 20
INDEX_ENTRY_SIZE = 11   # 6 ASCII code + 1 NULL + 4 uint32 offset
RECORD_SIZE = 2336      # 584 × 4 bytes
N_FIELDS = RECORD_SIZE // 4  # 584

GPCW_NAME_RE = re.compile(r"^gpcw(\d{8})\.dat$")


@dataclass
class GpcwReport:
    """单期 gpcw 财报快照."""
    period: date            # 该报告期截止日 (e.g. 2025-12-31)
    src_path: Path
    n_stocks: int
    _index: dict[str, int]  # code → file offset of data
    _raw: bytes             # 全文件内存 buffer (~7MB / 期, 可接受)

    def has(self, code: str) -> bool:
        return code in self._index

    def codes(self) -> list[str]:
        return list(self._index.keys())

    def read_raw(self, code: str) -> Optional[np.ndarray]:
        """返回 584 维 float32 array, 找不到返回 None."""
        off = self._index.get(code)
        if off is None:
            return None
        return np.frombuffer(self._raw[off:off + RECORD_SIZE], dtype="<f4").copy()


def parse_gpcw(path: Path) -> GpcwReport:
    """解析单个 gpcw .dat 文件."""
    raw = path.read_bytes()
    if len(raw) < HEADER_SIZE:
        raise ValueError(f"file too small: {path}")

    version = struct.unpack("<H", raw[0:2])[0]
    n_stocks = struct.unpack("<I", raw[6:10])[0]
    idx_sz = struct.unpack("<H", raw[10:12])[0]
    rec_sz = struct.unpack("<I", raw[12:16])[0]

    if rec_sz != RECORD_SIZE:
        raise ValueError(f"unexpected record size {rec_sz}, expected {RECORD_SIZE}")
    if idx_sz != INDEX_ENTRY_SIZE:
        raise ValueError(f"unexpected index size {idx_sz}, expected {INDEX_ENTRY_SIZE}")

    expected = HEADER_SIZE + n_stocks * (idx_sz + rec_sz)
    if len(raw) < expected:
        raise ValueError(f"truncated file: got {len(raw)} expected {expected}")

    # 解析 index
    idx: dict[str, int] = {}
    cursor = HEADER_SIZE
    for _ in range(n_stocks):
        code = raw[cursor:cursor + 6].decode("ascii", errors="replace")
        offset = struct.unpack("<I", raw[cursor + 7:cursor + 11])[0]
        idx[code] = offset
        cursor += INDEX_ENTRY_SIZE

    # 推断 period 从文件名
    m = GPCW_NAME_RE.match(path.name)
    if m:
        ymd = m.group(1)
        period = date(int(ymd[0:4]), int(ymd[4:6]), int(ymd[6:8]))
    else:
        period = None

    return GpcwReport(
        period=period,
        src_path=path,
        n_stocks=n_stocks,
        _index=idx,
        _raw=raw,
    )


class GpcwStore:
    """跨多期 gpcw 文件查询. 给定 code, 自动从最新期回溯找第一份覆盖该 code 的报告."""

    def __init__(self, fin_dir: Path):
        self.fin_dir = Path(fin_dir)
        self._reports: list[GpcwReport] = []   # 按 period 降序
        self._loaded_periods: set[date] = set()

    def list_available_periods(self) -> list[date]:
        """扫 fin_dir 下所有 gpcw*.dat, 返回 period 列表 (降序)."""
        periods = []
        for f in sorted(self.fin_dir.glob("gpcw*.dat"), reverse=True):
            m = GPCW_NAME_RE.match(f.name)
            if m:
                ymd = m.group(1)
                periods.append(date(int(ymd[0:4]), int(ymd[4:6]), int(ymd[6:8])))
        return periods

    def load_recent(self, n_periods: int = 6):
        """加载最近 N 期 *有数据* 的报告. 跳过 N=0 的占位文件 (期货报告未到期时 TDX 会留空文件)."""
        periods = self.list_available_periods()  # 全部期, 降序
        loaded = 0
        for p in periods:
            if loaded >= n_periods:
                break
            if p in self._loaded_periods:
                continue
            ymd = p.strftime("%Y%m%d")
            path = self.fin_dir / f"gpcw{ymd}.dat"
            try:
                report = parse_gpcw(path)
            except Exception:
                continue
            if report.n_stocks == 0:
                # 占位空文件 (TDX 期货报告未到期), 跳过
                continue
            self._reports.append(report)
            self._loaded_periods.add(p)
            loaded += 1
        # 按 period 降序
        self._reports.sort(key=lambda r: r.period or date.min, reverse=True)

    def latest_for_code(self, code: str) -> Optional[tuple[date, np.ndarray]]:
        """从最新期回溯找第一份覆盖该 code 的报告. 返回 (period, raw_array) 或 None."""
        for r in self._reports:
            arr = r.read_raw(code)
            if arr is not None:
                return (r.period, arr)
        return None

    def all_periods_for_code(self, code: str) -> list[tuple[date, np.ndarray]]:
        """返回该 code 在所有已加载期的 (period, raw_array) 列表."""
        out = []
        for r in self._reports:
            arr = r.read_raw(code)
            if arr is not None:
                out.append((r.period, arr))
        return out
