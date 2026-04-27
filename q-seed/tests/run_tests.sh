#!/usr/bin/env bash
# q-seed 测试套件
# 用法:
#   ./tests/run_tests.sh             # 跑快速 case (跳过全市场)
#   RUN_SLOW=1 ./tests/run_tests.sh  # 跑包含全市场的全部 case
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

QSEED="$ROOT/q-seed"
PY="$ROOT/.venv/bin/python"
TMP="$(mktemp -d)"
trap "rm -rf $TMP" EXIT

PASS_COUNT=0
FAIL_COUNT=0
FAIL_LIST=()

# ===== helpers =====
log_pass() { printf "  \033[32mPASS\033[0m %s\n" "$1"; PASS_COUNT=$((PASS_COUNT+1)); }
log_fail() { printf "  \033[31mFAIL\033[0m %s — %s\n" "$1" "$2"; FAIL_COUNT=$((FAIL_COUNT+1)); FAIL_LIST+=("$1: $2"); }

assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then
        log_pass "$name (= $actual)"
    else
        log_fail "$name" "expected '$expected', got '$actual'"
    fi
}

assert_lt() {
    local name="$1" max="$2" actual="$3"
    if (( actual < max )); then
        log_pass "$name ($actual < $max)"
    else
        log_fail "$name" "expected < $max, got $actual"
    fi
}

assert_le() {
    local name="$1" max="$2" actual="$3"
    if (( actual <= max )); then
        log_pass "$name ($actual <= $max)"
    else
        log_fail "$name" "expected <= $max, got $actual"
    fi
}

assert_ge() {
    local name="$1" min="$2" actual="$3"
    if (( actual >= min )); then
        log_pass "$name ($actual >= $min)"
    else
        log_fail "$name" "expected >= $min, got $actual"
    fi
}

assert_grep() {
    local name="$1" pattern="$2" file="$3"
    if grep -q -- "$pattern" "$file"; then
        log_pass "$name (grep '$pattern')"
    else
        log_fail "$name" "no match for '$pattern' in $file"
    fi
}

# 提取 jsonl 字段 (用 python 替代 jq)
jline() {
    local field="$1" file="$2"
    "$PY" -c "
import json,sys
for line in open('$file'):
    line=line.strip()
    if not line: continue
    obj=json.loads(line)
    val=obj
    for k in '$field'.split('.'):
        val=val.get(k) if isinstance(val,dict) else None
        if val is None: break
    print(val)
"
}

count_lines() { wc -l < "$1"; }

start_case() {
    echo ""
    echo "=== $1 ==="
}

# ===== Case C20: --help =====
start_case "C20 HELP"
"$QSEED" --help > "$TMP/c20.out" 2> "$TMP/c20.err"
exit_code=$?
assert_eq "C20.exit_code" "0" "$exit_code"
for flag in --top --template --input --since --until --format --output --config --no-fundamentals; do
    assert_grep "C20.has_flag $flag" "$flag" "$TMP/c20.out"
done

# ===== Case C18: bad config path =====
start_case "C18 BAD-CONFIG-PATH"
"$QSEED" --config /nonexistent/config.yaml > "$TMP/c18.out" 2> "$TMP/c18.err"
exit_code=$?
assert_ge "C18.exit_nonzero" "1" "$exit_code"
assert_grep "C18.stderr_has_path" "/nonexistent/config.yaml" "$TMP/c18.err"

# ===== Case C19: bad tdx_dir =====
start_case "C19 BAD-TDX-DIR"
"$QSEED" --config tests/fixtures/bad_config.yaml --top 5 > "$TMP/c19.out" 2> "$TMP/c19.err"
exit_code=$?
assert_ge "C19.exit_nonzero" "1" "$exit_code"
assert_grep "C19.stderr_has_path" "/nonexistent/tdx" "$TMP/c19.err"

# ===== Case C17: empty input =====
start_case "C17 EMPTY-INPUT"
"$QSEED" --input tests/fixtures/input_empty.jsonl > "$TMP/c17.out" 2> "$TMP/c17.err"
exit_code=$?
assert_eq "C17.exit_code" "0" "$exit_code"
out_lines=$(count_lines "$TMP/c17.out")
assert_eq "C17.stdout_empty" "0" "$out_lines"

# ===== Case C16: unknown code =====
start_case "C16 UNKNOWN-CODE"
"$QSEED" --input tests/fixtures/input_unknown_code.jsonl > "$TMP/c16.out" 2> "$TMP/c16.err"
exit_code=$?
assert_eq "C16.exit_code" "0" "$exit_code"
out_lines=$(count_lines "$TMP/c16.out")
assert_eq "C16.stdout_empty" "0" "$out_lines"
assert_grep "C16.stderr_has_warning" "999999" "$TMP/c16.err"

# ===== Case C15: invalid stdin =====
start_case "C15 INVALID-STDIN"
"$QSEED" --input tests/fixtures/input_invalid.jsonl --top 5 > "$TMP/c15.out" 2> "$TMP/c15.err"
exit_code=$?
assert_eq "C15.exit_code" "0" "$exit_code"
assert_grep "C15.stderr_has_warning" "missing 'code'" "$TMP/c15.err"
out_lines=$(count_lines "$TMP/c15.out")
assert_ge "C15.stdout_some_results" "1" "$out_lines"

# ===== Case C09: stdin single code =====
start_case "C09 STDIN-1-CODE"
echo '{"code":"301396"}' | "$QSEED" --top 1 > "$TMP/c09.out" 2> "$TMP/c09.err"
exit_code=$?
assert_eq "C09.exit_code" "0" "$exit_code"
out_lines=$(count_lines "$TMP/c09.out")
assert_eq "C09.lines_eq_1" "1" "$out_lines"
code=$(jline code "$TMP/c09.out" | head -1)
assert_eq "C09.code_is_301396" "301396" "$code"

# ===== Case C07: --input file =====
start_case "C07 INPUT-4-CODES"
"$QSEED" --input tests/fixtures/input_codes_4.jsonl --top 5 > "$TMP/c07.out" 2> "$TMP/c07.err"
exit_code=$?
assert_eq "C07.exit_code" "0" "$exit_code"
out_lines=$(count_lines "$TMP/c07.out")
assert_le "C07.lines_le_4" "4" "$out_lines"
# 输出 code 必须 ⊂ {301396,603629,605389,002409}
"$PY" -c "
import json
allowed = {'301396','603629','605389','002409'}
got = {json.loads(l)['code'] for l in open('$TMP/c07.out') if l.strip()}
extra = got - allowed
exit(1 if extra else 0)
" && log_pass "C07.codes_subset_of_input" || log_fail "C07.codes_subset_of_input" "found extra codes"

# ===== Case C08: stdin pipe =====
start_case "C08 STDIN-PIPE"
cat tests/fixtures/input_codes_4.jsonl | "$QSEED" --top 5 > "$TMP/c08.out" 2> "$TMP/c08.err"
exit_code=$?
assert_eq "C08.exit_code" "0" "$exit_code"
# 行数应与 C07 相同
c07_lines=$(count_lines "$TMP/c07.out")
c08_lines=$(count_lines "$TMP/c08.out")
assert_eq "C08.same_as_c07" "$c07_lines" "$c08_lines"

# ===== Case C04: --template hongjing =====
start_case "C04 TEMPLATE-HONGJING-ONLY"
"$QSEED" --input tests/fixtures/input_codes_4.jsonl --template hongjing --top 5 > "$TMP/c04.out" 2> "$TMP/c04.err"
exit_code=$?
assert_eq "C04.exit_code" "0" "$exit_code"
"$PY" -c "
import json
ok = True
for l in open('$TMP/c04.out'):
    if not l.strip(): continue
    d = json.loads(l)
    if d['templates_matched'] != ['hongjing']: ok = False; break
    if d['details'].get('litong') is not None: ok = False; break
exit(0 if ok else 1)
" && log_pass "C04.templates_matched_hongjing_only" || log_fail "C04.templates_matched_hongjing_only" "litong present in output"

# ===== Case C05: --template litong =====
start_case "C05 TEMPLATE-LITONG-ONLY"
"$QSEED" --input tests/fixtures/input_codes_4.jsonl --template litong --top 5 > "$TMP/c05.out" 2> "$TMP/c05.err"
exit_code=$?
assert_eq "C05.exit_code" "0" "$exit_code"
"$PY" -c "
import json
ok = True
for l in open('$TMP/c05.out'):
    if not l.strip(): continue
    d = json.loads(l)
    if d['templates_matched'] != ['litong']: ok = False; break
    if d['details'].get('hongjing') is not None: ok = False; break
exit(0 if ok else 1)
" && log_pass "C05.templates_matched_litong_only" || log_fail "C05.templates_matched_litong_only" "hongjing present in output"

# ===== Case C10: --since filter =====
start_case "C10 SINCE-FILTER"
"$QSEED" --input tests/fixtures/input_codes_4.jsonl --since 2026-04-15 --top 30 > "$TMP/c10.out" 2> "$TMP/c10.err"
exit_code=$?
assert_eq "C10.exit_code" "0" "$exit_code"
"$PY" -c "
import json
from datetime import date
cutoff = date(2026, 4, 15)
ok = True
for l in open('$TMP/c10.out'):
    if not l.strip(): continue
    d = json.loads(l)
    for tname, det in d['details'].items():
        if det is None: continue
        sd = date.fromisoformat(det['sig_date'])
        if sd < cutoff: ok = False; break
    if not ok: break
exit(0 if ok else 1)
" && log_pass "C10.all_sig_dates_ge_cutoff" || log_fail "C10.all_sig_dates_ge_cutoff" "found sig_date < 2026-04-15"

# ===== Case C11: --since + --until =====
start_case "C11 SINCE-UNTIL-WINDOW"
"$QSEED" --input tests/fixtures/input_codes_4.jsonl --since 2026-04-01 --until 2026-04-15 --top 30 > "$TMP/c11.out" 2> "$TMP/c11.err"
exit_code=$?
assert_eq "C11.exit_code" "0" "$exit_code"
"$PY" -c "
import json
from datetime import date
lo, hi = date(2026,4,1), date(2026,4,15)
ok = True
for l in open('$TMP/c11.out'):
    if not l.strip(): continue
    d = json.loads(l)
    for tname, det in d['details'].items():
        if det is None: continue
        sd = date.fromisoformat(det['sig_date'])
        if not (lo <= sd <= hi): ok = False; break
    if not ok: break
exit(0 if ok else 1)
" && log_pass "C11.all_sig_dates_in_window" || log_fail "C11.all_sig_dates_in_window" "found sig_date out of window"

# ===== Case C12: --format md =====
start_case "C12 FORMAT-MD"
"$QSEED" --input tests/fixtures/input_codes_4.jsonl --top 4 --format md > "$TMP/c12.out" 2> "$TMP/c12.err"
exit_code=$?
assert_eq "C12.exit_code" "0" "$exit_code"
assert_grep "C12.has_table_header" "| # |" "$TMP/c12.out"
assert_grep "C12.has_title" "q-seed TOP" "$TMP/c12.out"

# ===== Case C13: --format both --output =====
start_case "C13 FORMAT-BOTH-OUTPUT"
"$QSEED" --input tests/fixtures/input_codes_4.jsonl --top 3 --format both --output "$TMP/c13" > /dev/null 2> "$TMP/c13.err"
exit_code=$?
assert_eq "C13.exit_code" "0" "$exit_code"
[[ -s "$TMP/c13.jsonl" ]] && log_pass "C13.jsonl_written" || log_fail "C13.jsonl_written" "missing $TMP/c13.jsonl"
[[ -s "$TMP/c13.md" ]] && log_pass "C13.md_written" || log_fail "C13.md_written" "missing $TMP/c13.md"

# ===== Case C14: self-pipe =====
start_case "C14 SELF-PIPE (small input)"
"$QSEED" --input tests/fixtures/input_codes_4.jsonl --top 4 2>/dev/null \
  | "$QSEED" --top 2 > "$TMP/c14.out" 2> "$TMP/c14.err"
exit_code=$?
assert_eq "C14.exit_code" "0" "$exit_code"
out_lines=$(count_lines "$TMP/c14.out")
assert_le "C14.lines_le_2" "2" "$out_lines"

# ===== Case C02: --top 5 (input mode for speed) =====
start_case "C02 TOP-5 (input mode)"
"$QSEED" --input tests/fixtures/input_codes_4.jsonl --top 3 > "$TMP/c02.out" 2> "$TMP/c02.err"
exit_code=$?
assert_eq "C02.exit_code" "0" "$exit_code"
out_lines=$(count_lines "$TMP/c02.out")
assert_le "C02.lines_le_3" "3" "$out_lines"
"$PY" -c "
import json
ranks = [json.loads(l)['rank'] for l in open('$TMP/c02.out') if l.strip()]
exit(0 if ranks == list(range(1, len(ranks)+1)) else 1)
" && log_pass "C02.ranks_consecutive" || log_fail "C02.ranks_consecutive" "ranks not 1..N"

# ===== Case C03: --top overflow =====
start_case "C03 TOP-OVERFLOW (input mode)"
"$QSEED" --input tests/fixtures/input_codes_4.jsonl --top 5000 > "$TMP/c03.out" 2> "$TMP/c03.err"
exit_code=$?
assert_eq "C03.exit_code" "0" "$exit_code"
out_lines=$(count_lines "$TMP/c03.out")
assert_le "C03.lines_le_4" "4" "$out_lines"

# ===== SLOW cases (全市场) =====
if [[ "${RUN_SLOW:-0}" == "1" ]]; then
    start_case "C01 DEFAULT-FULL-MARKET (SLOW)"
    t0=$(date +%s)
    "$QSEED" > "$TMP/c01.out" 2> "$TMP/c01.err"
    exit_code=$?
    t1=$(date +%s); dt=$((t1-t0))
    echo "  耗时: ${dt}s"
    assert_eq "C01.exit_code" "0" "$exit_code"
    out_lines=$(count_lines "$TMP/c01.out")
    assert_eq "C01.lines_eq_30" "30" "$out_lines"
    # safety 字段在 4 档内
    "$PY" -c "
import json
allowed = {'🔴','🟡','🟢','⚪'}
ok = all(json.loads(l)['kline'].get('kline_safety') in allowed for l in open('$TMP/c01.out') if l.strip())
exit(0 if ok else 1)
" && log_pass "C01.safety_in_4_buckets" || log_fail "C01.safety_in_4_buckets" "found out-of-set safety"
    # 自动备份 logs/
    [[ -n "$(ls -t $ROOT/logs/q-seed_top30_*.jsonl 2>/dev/null | head -1)" ]] \
      && log_pass "C01.logs_jsonl_present" \
      || log_fail "C01.logs_jsonl_present" "no q-seed_top30_*.jsonl in logs/"

    start_case "C06 TEMPLATE-BOTH-EXPLICIT (SLOW)"
    t0=$(date +%s)
    "$QSEED" --template both --top 5 > "$TMP/c06.out" 2> "$TMP/c06.err"
    t1=$(date +%s); dt=$((t1-t0))
    echo "  耗时: ${dt}s"
    assert_eq "C06.exit_code" "0" "$?"
    assert_eq "C06.lines_eq_5" "5" "$(count_lines $TMP/c06.out)"
else
    echo ""
    echo "(跳过 C01/C06 全市场 case; 用 RUN_SLOW=1 启用)"
fi

# ===== 总结 =====
echo ""
echo "=========================================="
echo "Total: $PASS_COUNT PASS / $FAIL_COUNT FAIL"
echo "=========================================="
if (( FAIL_COUNT > 0 )); then
    echo ""
    echo "失败列表:"
    for f in "${FAIL_LIST[@]}"; do
        echo "  - $f"
    done
    exit 1
fi
exit 0
