#!/usr/bin/env bash
# q-news 测试套件 (Phase B4)
# 用法:
#   ./tests/run_tests.sh           # 默认: 离线 fixture + dummy provider
#   RUN_NETWORK=1 ./tests/run_tests.sh  # 加跑真实网络拉取
#   RUN_REAL_LLM=1 ./tests/run_tests.sh  # 加跑真 LLM (需 ANTHROPIC_API_KEY)
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

QNEWS="$ROOT/q-news"
PY="$ROOT/.venv/bin/python"
TMP="$(mktemp -d)"
trap "rm -rf $TMP" EXIT

PASS=0
FAIL=0
FAILS=()

log_pass() { printf "  \033[32mPASS\033[0m %s\n" "$1"; PASS=$((PASS+1)); }
log_fail() { printf "  \033[31mFAIL\033[0m %s — %s\n" "$1" "$2"; FAIL=$((FAIL+1)); FAILS+=("$1: $2"); }

assert_eq() { [[ "$2" == "$3" ]] && log_pass "$1 (=$3)" || log_fail "$1" "expected '$2', got '$3'"; }
assert_le() { (( $3 <= $2 )) && log_pass "$1 ($3<=$2)" || log_fail "$1" "expected <=$2 got $3"; }
assert_ge() { (( $3 >= $2 )) && log_pass "$1 ($3>=$2)" || log_fail "$1" "expected >=$2 got $3"; }
assert_grep() { grep -q -- "$2" "$3" && log_pass "$1 (grep $2)" || log_fail "$1" "no match for '$2' in $3"; }
count_lines() { wc -l < "$1" | tr -d ' '; }
start_case() { echo ""; echo "=== $1 ==="; }

CFG="$ROOT/tests/fixtures/test_config.yaml"
NEWS="$ROOT/tests/fixtures/news_sample.jsonl"

# N01 --help
start_case "N01 HELP"
"$QNEWS" --help > "$TMP/n01.out" 2> "$TMP/n01.err"
assert_eq "N01.exit_code" "0" "$?"
for f in --paid --top --input --since --until --sources --tags --format --output --config; do
    assert_grep "N01.has_$f" "$f" "$TMP/n01.out"
done

# N13 单规则单事件命中 (核心)
start_case "N13 RULE-MATCH-OPEC"
"$QNEWS" --news-file "$NEWS" --config "$CFG" --top 10 > "$TMP/n13.out" 2> "$TMP/n13.err"
assert_eq "N13.exit_code" "0" "$?"
"$PY" -c "
import json
recs = [json.loads(l) for l in open('$TMP/n13.out')]
# 至少 1 条 002409 雅克 命中 opec_减产
yake = [r for r in recs if r.get('code') == '002409']
assert yake, 'no 雅克 record'
ev = yake[0].get('triggered_events', [])
assert any(e.get('rule', {}).get('id') == 'opec_减产' for e in ev), f'no opec rule in events'
print('OK')
" && log_pass "N13.opec_yake" || log_fail "N13.opec_yake" "missing"

# N15 trigger 不足不激活
start_case "N15 INSUFFICIENT-TRIGGERS"
echo '{"event_id":"x","source":"test","title":"OPEC 总部今日放假","content":"无关.","publish_time":"2026-04-26T10:00:00","tags":[]}' > "$TMP/single.jsonl"
"$QNEWS" --news-file "$TMP/single.jsonl" --config "$CFG" --top 5 > "$TMP/n15.out" 2> "$TMP/n15.err"
"$PY" -c "
recs = open('$TMP/n15.out').readlines()
assert len(recs) == 0, f'expected 0 records, got {len(recs)}'
print('OK')
" && log_pass "N15.not_triggered" || log_fail "N15.not_triggered" "got matches"

# N16 unmatched 写日志
start_case "N16 UNMATCHED-LOG"
rm -f logs/unmatched_events.jsonl
echo '{"event_id":"y","source":"test","title":"今日天气晴朗适合出行","content":"风和日丽","publish_time":"2026-04-26T10:00:00","tags":[]}' > "$TMP/weather.jsonl"
"$QNEWS" --news-file "$TMP/weather.jsonl" --config "$CFG" --top 5 > /dev/null 2>&1
if [[ -s logs/unmatched_events.jsonl ]] && grep -q "天气" logs/unmatched_events.jsonl; then
    log_pass "N16.unmatched_logged"
else
    log_fail "N16.unmatched_logged" "no log entry"
fi

# N17 概念未启动 (q-seed 没命中). 用 --top 20 确保 002409 不被 balance 滤掉
start_case "N17 CONCEPT-NOT-STARTED"
"$QNEWS" --news-file "$NEWS" --config "$CFG" --top 20 > "$TMP/n17.out" 2> "$TMP/n17.err"
"$PY" -c "
import json
recs = [json.loads(l) for l in open('$TMP/n17.out')]
yake = [r for r in recs if r.get('code') == '002409']
assert yake, f'no yake; codes={[r.get(\"code\") for r in recs]}'
cs = yake[0].get('concept_status', {})
assert cs.get('已启动') == False, f'expected False, got {cs.get(\"已启动\")}'
assert cs.get('active_pct', 1) == 0.0, f'expected 0 active, got {cs.get(\"active_pct\")}'
print('OK')
" && log_pass "N17.concept_inactive" || log_fail "N17.concept_inactive" "wrong concept_status"

# N20 --format jsonl
start_case "N20 FORMAT-JSONL"
"$QNEWS" --news-file "$NEWS" --config "$CFG" --top 3 --format jsonl > "$TMP/n20.out" 2> "$TMP/n20.err"
ec=$?
"$PY" -c "
import json
for l in open('$TMP/n20.out'):
    if l.strip(): json.loads(l)
print('OK')
" && log_pass "N20.valid_jsonl" || log_fail "N20.valid_jsonl" "invalid JSON"

# N21 --format md
start_case "N21 FORMAT-MD"
"$QNEWS" --news-file "$NEWS" --config "$CFG" --top 3 --format md > "$TMP/n21.out" 2>/dev/null
assert_grep "N21.has_table" "| code |" "$TMP/n21.out"

# N22 --output 写两份
start_case "N22 FORMAT-BOTH-OUTPUT"
"$QNEWS" --news-file "$NEWS" --config "$CFG" --top 3 --format both --output "$TMP/n22" > /dev/null 2>&1
[[ -f "$TMP/n22.jsonl" ]] && log_pass "N22.jsonl_exists" || log_fail "N22.jsonl_exists" "missing"
[[ -f "$TMP/n22.md" ]] && log_pass "N22.md_exists" || log_fail "N22.md_exists" "missing"

# N23 自动 logs 备份
start_case "N23 LOGS-AUTO-BACKUP"
"$QNEWS" --news-file "$NEWS" --config "$CFG" --top 3 > /dev/null 2>&1
LATEST=$(ls -t logs/q-news_top*.jsonl 2>/dev/null | head -1)
if [[ -n "$LATEST" && -s "$LATEST" ]]; then
    log_pass "N23.log_file_written ($(basename $LATEST))"
else
    log_fail "N23.log_file_written" "no log file"
fi

# N24 非法 --input 行
start_case "N24 INVALID-INPUT-LINE"
cat > "$TMP/bad.jsonl" <<EOF
{"code":"605389"}
{"name":"无 code 字段"}
{"code":"301396"}
EOF
"$QNEWS" --input "$TMP/bad.jsonl" --config "$CFG" --top 5 > "$TMP/n24.out" 2> "$TMP/n24.err"
ec=$?
assert_eq "N24.exit_code" "0" "$ec"
assert_grep "N24.warn_skip" "missing 'code'" "$TMP/n24.err"

# N25 非法 --paid 值
start_case "N25 INVALID-PAID"
"$QNEWS" --paid foobar --config "$CFG" > "$TMP/n25.out" 2> "$TMP/n25.err"
ec=$?
assert_ge "N25.exit_nonzero" "1" "$ec"

# N26 bad config
start_case "N26 BAD-CONFIG"
"$QNEWS" --config /nonexistent/config.yaml > "$TMP/n26.out" 2> "$TMP/n26.err"
ec=$?
assert_ge "N26.exit_nonzero" "1" "$ec"
assert_grep "N26.stderr_path" "/nonexistent" "$TMP/n26.err"

# N27 BrokenPipe (用 head)
start_case "N27 BROKEN-PIPE"
"$QNEWS" --news-file "$NEWS" --config "$CFG" --top 100 2>/dev/null | head -1 > /dev/null
ec_arr=("${PIPESTATUS[@]}")
[[ "${ec_arr[0]}" == "0" ]] && log_pass "N27.upstream_exit_0" || log_fail "N27.upstream_exit_0" "got ${ec_arr[0]}"

# N30 --paid + dummy
start_case "N30 PAID-DUMMY"
"$QNEWS" --news-file "$NEWS" --config "$CFG" --top 3 --paid --llm dummy --search dummy > "$TMP/n30.out" 2> "$TMP/n30.err"
ec=$?
assert_eq "N30.exit_code" "0" "$ec"
"$PY" -c "
import json
for l in open('$TMP/n30.out'):
    r = json.loads(l)
    assert r.get('mode') == 'standard', f'mode={r.get(\"mode\")}'
print('OK')
" && log_pass "N30.mode_standard" || log_fail "N30.mode_standard" "wrong mode"

# N02 默认裸跑 (RUN_NETWORK=1)
if [[ "${RUN_NETWORK:-0}" == "1" ]]; then
    start_case "N02 DEFAULT-NETWORK-FETCH"
    "$QNEWS" --top 5 --window-hours 48 --no-cache > "$TMP/n02.out" 2> "$TMP/n02.err"
    ec=$?
    assert_eq "N02.exit_code" "0" "$ec"
    "$PY" -c "
import json
n_lines = sum(1 for l in open('$TMP/n02.out') if l.strip())
import re
err = open('$TMP/n02.err').read()
m = re.search(r'events_after_filter:\s*(\d+)', err)
total = int(m.group(1)) if m else 0
assert total > 0, f'no events fetched, total={total}'
print(f'OK fetched {total} events, output {n_lines} records')
" && log_pass "N02.network_fetch" || log_fail "N02.network_fetch" "fetch failed"
fi

# N44 真 LLM (RUN_REAL_LLM=1)
if [[ "${RUN_REAL_LLM:-0}" == "1" ]]; then
    start_case "N44 REAL-LLM (anthropic, 预算 \$0.10)"
    echo "  ⚠️ 即将调真 LLM, 估成本 ~\$0.05-0.10"
    "$QNEWS" --news-file "$NEWS" --config "$CFG" --top 3 --paid --llm anthropic --budget 0.10 > "$TMP/n44.out" 2> "$TMP/n44.err"
    ec=$?
    assert_eq "N44.exit_code" "0" "$ec"
    cat "$TMP/n44.err" | tail -5
fi

# 总结
echo ""
echo "=========================================="
echo "Total: $PASS PASS / $FAIL FAIL"
echo "=========================================="
if (( FAIL > 0 )); then
    echo ""
    echo "失败列表:"
    for f in "${FAILS[@]}"; do echo "  - $f"; done
    exit 1
fi
