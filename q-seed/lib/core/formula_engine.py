"""
TDX (通达信) formula engine.
Translates a subset of TDX formula language to Python and executes it
against a stock DataFrame, returning True/False for today's row.

Supported:
  - Price variables: CLOSE HIGH LOW OPEN VOL/VOLUME AMOUNT
  - Operators: AND OR NOT = <> > < >= <=
  - Assignments: N:=20
  - All MyTT functions: MA EMA REF HHV LLV MACD KDJ RSI BOLL CROSS etc.
  - Arithmetic: + - * /
"""

import re
import logging
import numpy as np
import pandas as pd
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Future function detection ─────────────────────────────────────────────────

_FUTURE_PATTERNS = [
    (r'\bZIG\b',               'ZIG'),
    (r'\bBACKSET\b',           'BACKSET'),
    (r'REF\s*\([^,]+,\s*-\d', 'REF 负数参数'),
]

def check_future_functions(formula: str) -> list[str]:
    found = []
    for pattern, label in _FUTURE_PATTERNS:
        if re.search(pattern, formula, re.IGNORECASE):
            found.append(label)
    return found


# ── Translation ───────────────────────────────────────────────────────────────

def _strip_comments(formula: str) -> str:
    formula = re.sub(r'/\*.*?\*/', '', formula, flags=re.DOTALL)
    formula = re.sub(r'\{[^}]*\}', '', formula)
    return formula


def _split_logical(expr: str, op: str) -> list[str]:
    """
    Split expr on top-level AND/OR keyword (ignoring content inside parentheses).
    Example: "A>B AND MA(C,3)>MA(C,5)" → ["A>B", "MA(C,3)>MA(C,5)"]
    """
    parts = []
    depth = 0
    current: list[str] = []
    tokens = re.split(rf'(\(|\)|(?<!\w){op}(?!\w))', expr, flags=re.IGNORECASE)
    for token in tokens:
        if token == '(':
            depth += 1
            current.append(token)
        elif token == ')':
            depth -= 1
            current.append(token)
        elif re.fullmatch(op, token, re.IGNORECASE) and depth == 0:
            parts.append(''.join(current).strip())
            current = []
        else:
            current.append(token)
    if current:
        parts.append(''.join(current).strip())
    return [p for p in parts if p]


def _translate_atom(expr: str) -> str:
    """Translate one atomic expression (no top-level AND/OR)."""
    # NOT keyword → ~ (applied to the whole sub-expression)
    expr = re.sub(r'\bNOT\b\s*', '~', expr, flags=re.IGNORECASE)
    # <> → !=
    expr = expr.replace('<>', '!=')
    # single = (not :=, !=, <=, >=, ==) → ==
    expr = re.sub(r'(?<![:<>!=])=(?!=)', '==', expr)
    # uppercase ASCII-only identifiers (MyTT function names), preserve CJK variable names
    def _upper_ascii(m: re.Match) -> str:
        s = m.group(0)
        return s.upper() if s.isascii() else s
    return re.sub(r'[A-Za-z_]\w*', _upper_ascii, expr)


def _translate_condition(expr: str) -> str:
    """
    Recursively translate a TDX condition with AND/OR to Python.
    Ensures each comparison is wrapped in parens to defeat Python's
    &/| vs > precedence issue.
    """
    expr = expr.strip()

    # Split on top-level OR first (lower precedence than AND)
    or_parts = _split_logical(expr, 'OR')
    if len(or_parts) > 1:
        translated = [_translate_condition(p) for p in or_parts]
        return ' | '.join(f'({t})' for t in translated)

    # Split on top-level AND
    and_parts = _split_logical(expr, 'AND')
    if len(and_parts) > 1:
        translated = [_translate_condition(p) for p in and_parts]
        return ' & '.join(f'({t})' for t in translated)

    # Leaf: no AND/OR at top level
    return _translate_atom(expr)


def translate_formula(formula: str) -> tuple[list[str], str]:
    """
    Parse TDX formula into (assignment_stmts, condition_expr).

    Multi-line example:
        N:=20;
        HH:=HHV(HIGH,N);
        CLOSE>=HH*0.95 AND VOL>MA(VOL,5)

    Returns:
        (['N = 20', 'HH = HHV(HIGH,N)'],
         '(CLOSE>=HH*0.95) & (VOL>MA(VOL,5))')
    """
    formula = _strip_comments(formula)
    # Split on both newlines and semicolons, then clean up
    raw_lines = [seg.strip()
                 for l in formula.strip().splitlines()
                 for seg in l.split(';')]
    lines = [l for l in raw_lines if l]

    if not lines:
        raise ValueError("Empty formula")

    assignments: list[str] = []
    conditions:  list[str] = []

    for line in lines:
        # Strip signal labels like "XG:", "买点1:" (TDX output labels, not assignments)
        line = re.sub(r'^[\w\u4e00-\u9fff]+\s*:\s*(?!=)', '', line).strip()
        if not line:
            continue
        if ':=' in line:
            var, rhs = line.split(':=', 1)
            assignments.append(
                f"{var.strip()} = {_translate_condition(rhs.strip())}"
            )
        else:
            conditions.append(_translate_condition(line))

    if not conditions:
        raise ValueError("Formula contains only assignments and no condition")

    final = ' & '.join(f'({c})' for c in conditions) if len(conditions) > 1 else conditions[0]
    return assignments, final


# ── Execution context ─────────────────────────────────────────────────────────

def _build_mytt_context() -> dict:
    mytt_path = Path(__file__).parent / 'mytt.py'
    ns: dict = {}
    exec(compile(mytt_path.read_text(encoding='utf-8'), str(mytt_path), 'exec'), ns)
    return {k: v for k, v in ns.items() if not k.startswith('_')}

_MYTT_CTX: dict | None = None

def _get_mytt() -> dict:
    global _MYTT_CTX
    if _MYTT_CTX is None:
        _MYTT_CTX = _build_mytt_context()
    return _MYTT_CTX


def _ref_smart(S: np.ndarray, N) -> np.ndarray:
    """REF that handles both scalar and array N (TDX element-wise lookback).
    Preserves integer dtype for logical arrays; uses float+NaN for continuous arrays."""
    S = np.asarray(S)
    is_logical = S.dtype.kind in ('b', 'i', 'u') or (
        S.dtype.kind == 'f' and np.all((S == 0) | (S == 1) | np.isnan(S))
    )
    if is_logical:
        out_dtype, fill = np.int8, 0
        S = np.nan_to_num(S, nan=0).astype(np.int8)
    else:
        out_dtype, fill = float, np.nan
        S = S.astype(float)

    if np.ndim(N) == 0:
        n = int(N)
        if n == 0:
            return S
        result = np.empty(len(S), dtype=out_dtype)
        result[:n] = fill
        result[n:] = S[:-n]
        return result
    # Array N: element-wise lookback
    N = np.asarray(N, dtype=int)
    result = np.empty(len(S), dtype=out_dtype)
    for i in range(len(S)):
        j = i - N[i]
        result[i] = S[j] if 0 <= j < len(S) else fill
    return result


def _safe_and(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Element-wise AND that handles float arrays (treats NaN as False)."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    return ((a != 0) & ~np.isnan(a) & (b != 0) & ~np.isnan(b)).astype(float)


def _safe_or(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Element-wise OR that handles float arrays (treats NaN as False)."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    return (((a != 0) & ~np.isnan(a)) | ((b != 0) & ~np.isnan(b))).astype(float)


def run_formula(formula: str, df: pd.DataFrame) -> bool:
    """
    Execute TDX formula against a stock DataFrame.
    Returns True if the LAST row satisfies the condition.
    """
    if len(df) < 5:
        return False

    assignments, condition = translate_formula(formula)

    ctx = dict(_get_mytt())
    close  = df['close'].values.astype(float)
    open_  = df['open'].values.astype(float)
    high   = df['high'].values.astype(float)
    low    = df['low'].values.astype(float)
    vol    = df['volume'].values.astype(float)
    amount = df['amount'].values.astype(float)
    ctx.update({
        'CLOSE': close,  'C': close,
        'OPEN':  open_,  'O': open_,
        'HIGH':  high,   'H': high,
        'LOW':   low,    'L': low,
        'VOL':   vol,    'V': vol,  'VOLUME': vol,
        'AMOUNT': amount,
        'REF':   _ref_smart,
        'np':    np,
    })

    for stmt in assignments:
        exec(stmt, ctx)

    # Cast all bool arrays to int8 so & operator works uniformly with REF results
    for key, val in list(ctx.items()):
        if isinstance(val, np.ndarray) and val.dtype == bool:
            ctx[key] = val.view(np.int8)

    result = eval(condition, ctx)

    if isinstance(result, (np.ndarray, pd.Series)):
        val = result[-1]
    else:
        val = result

    if isinstance(val, float) and np.isnan(val):
        return False
    return bool(val)
