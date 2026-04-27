"""
FVG回踩突破策略

逻辑：
  1. 近20日出现放量突破前高（10日内发生）
  2. 当前价格回踩到突破K线的中线附近（±6%），且高于低点
  3. 缩量整理（成交量 < 5日均量*1.1）
  4. 5日振幅 < 10%（横盘整理）
  5. 均线多头（MA20 > MA60）

评分（0~11110）：每个条件贡献对应位数，全部满足=11110
"""

import numpy as np
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.formula_engine import _ref_smart


def _load_mytt():
    import importlib.util
    mytt_path = Path(__file__).parent.parent / "mytt.py"
    spec = importlib.util.spec_from_file_location("mytt", mytt_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_mytt = _load_mytt()
MA     = _mytt.MA
HHV    = _mytt.HHV
LLV    = _mytt.LLV
REF    = _ref_smart
CROSS  = _mytt.CROSS
BARSLAST = _mytt.BARSLAST
COUNT  = _mytt.COUNT


NAME = "FVG回踩突破"
MIN_BARS = 65   # 需要至少60根K线（MA60）


def scan(df) -> dict | None:
    """
    对单只股票运行策略。
    返回 None 表示不命中。
    返回 dict 包含 score（11110=全满足）及各分项标志。
    只有 score >= 10000（最近有过突破）才返回结果。
    """
    if len(df) < MIN_BARS:
        return None

    C = df["close"].values.astype(float)
    H = df["high"].values.astype(float)
    L = df["low"].values.astype(float)
    V = df["volume"].values.astype(float)

    N, M = 20, 5

    前高 = HHV(H, N)
    ma_v = MA(V, M)
    放量 = (V > ma_v * 1.5).astype(np.int8)
    突破 = (CROSS(C, REF(前高, 1)) & 放量).astype(np.int8)

    最近突破 = COUNT(突破, 10) > 0

    if not 最近突破[-1]:
        return None

    高点 = REF(H, BARSLAST(突破))
    低点 = REF(L, BARSLAST(突破))
    with np.errstate(invalid="ignore", divide="ignore"):
        中线 = (高点 + 低点) / 2
        回踩_flag = (np.abs(C - 中线) / 中线 < 0.06) & (C > 低点)

    缩量_flag = V < ma_v * 1.1
    整理_flag = HHV(H, 5) / LLV(L, 5) < 1.10
    趋势_flag = MA(C, 20) > MA(C, 60)

    f_回踩  = bool(回踩_flag[-1])
    f_缩量  = bool(缩量_flag[-1])
    f_整理  = bool(整理_flag[-1])
    f_趋势  = bool(趋势_flag[-1])

    score = 10000 + f_回踩 * 1000 + f_缩量 * 100 + f_整理 * 10 + f_趋势

    return {
        "score":  score,
        "最近突破": True,
        "回踩":   f_回踩,
        "缩量":   f_缩量,
        "整理":   f_整理,
        "趋势":   f_趋势,
    }
