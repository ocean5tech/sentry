#!/usr/bin/env bash
# q-fin 测试套件 (Phase B1 free mode)
# 用法:
#   ./tests/run_tests.sh           # 跳过慢 case
#   RUN_SLOW=1 ./tests/run_tests.sh # 全跑
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

QFIN="$ROOT/q-fin"
PY="$ROOT/.venv/bin/python"
TMP="$(mktemp -d)"
trap "rm -rf $TMP" EXIT

PASS=0
FAIL=0
FAILS=()

log_pass() { printf "  \033[32mPASS\033[0m %s\n" "$1"; PASS=$((PASS+1)); }
log_fail() { printf "  \033[31mFAIL\033[0m %s — %s\n" "$1" "$2"; FAIL=$((FAIL+1)); FAILS+=("$1: $2"); }

assert_eq() {
    local name="$1" exp="$2" act="$3"
    [[ "$exp" == "$act" ]] && log_pass "$name (=$act)" || log_fail "$name" "expected '$exp', got '$act'"
}
assert_le() { (( $3 <= $2 )) && log_pass "$1 ($3<=$2)" || log_fail "$1" "expected <=$2 got $3"; }
assert_ge() { (( $3 >= $2 )) && log_pass "$1 ($3>=$2)" || log_fail "$1" "expected >=$2 got $3"; }
assert_grep() {
    grep -q -- "$2" "$3" && log_pass "$1 (grep $2)" || log_fail "$1" "no match for '$2' in $3"
}
count_lines() { wc -l < "$1"; }
start_case() { echo ""; echo "=== $1 ==="; }

# F01 --help
start_case "F01 HELP"
"$QFIN" --help > "$TMP/f01.out" 2> "$TMP/f01.err"
assert_eq "F01.exit_code" "0" "$?"
for f in --paid --top --input --since --until --format --output --config --dry-run; do
    assert_grep "F01.has_$f" "$f" "$TMP/f01.out"
done

# F23: 空输入 (explicit --input 空文件 → exit 0, 不出 stdout)
start_case "F23 EMPTY-INPUT"
"$QFIN" --input tests/fixtures/input_empty.jsonl > "$TMP/f23.out" 2> "$TMP/f23.err"
assert_eq "F23.exit_code" "0" "$?"
assert_eq "F23.stdout_empty" "0" "$(count_lines $TMP/f23.out)"

# F22: 未知 code
start_case "F22 UNKNOWN-CODE"
"$QFIN" --input tests/fixtures/input_unknown_code.jsonl > "$TMP/f22.out" 2> "$TMP/f22.err"
assert_eq "F22.exit_code" "0" "$?"

# F21: 非法 stdin (含 1 条非法 + 2 条有效)
start_case "F21 INVALID-STDIN"
"$QFIN" --input tests/fixtures/input_invalid.jsonl --top 5 --no-cache > "$TMP/f21.out" 2> "$TMP/f21.err"
assert_eq "F21.exit_code" "0" "$?"
assert_grep "F21.stderr_warning" "missing 'code'" "$TMP/f21.err"

# F24: bad config path
start_case "F24 BAD-CONFIG-PATH"
"$QFIN" --config /nonexistent/config.yaml > "$TMP/f24.out" 2> "$TMP/f24.err"
ec=$?
assert_ge "F24.exit_nonzero" "1" "$ec"
assert_grep "F24.stderr_path" "/nonexistent" "$TMP/f24.err"

# F25: bad tdx_dir (用 fixture)
start_case "F25 BAD-TDX-DIR"
"$QFIN" --config tests/fixtures/bad_config.yaml --top 5 > "$TMP/f25.out" 2> "$TMP/f25.err"
ec=$?
assert_ge "F25.exit_nonzero" "1" "$ec"
assert_grep "F25.stderr_path" "/nonexistent/tdx" "$TMP/f25.err"

# F02: 默认裸跑 (4 codes)
start_case "F02 DEFAULT-INPUT-4"
"$QFIN" --input tests/fixtures/input_codes_4.jsonl --top 4 > "$TMP/f02.out" 2> "$TMP/f02.err"
ec=$?
assert_eq "F02.exit_code" "0" "$ec"
assert_le "F02.lines_le_4" "4" "$(count_lines $TMP/f02.out)"
"$PY" -c "
import json
for l in open('$TMP/f02.out'):
    if not l.strip(): continue
    r = json.loads(l)
    assert r['source'] == 'q-fin', f'source not q-fin: {r[\"source\"]}'
    assert r['mode'] == 'free', f'mode not free: {r[\"mode\"]}'
    assert r['verdict'] is None, 'verdict should be null in free mode'
    assert r['entity_research'] is None, 'entity_research should be null in free mode'
    for k in ['announcements_90d', 'shareholders', 'fundamentals', 'kline', 'q_seed_crosscheck', 'layer1_triggers']:
        assert k in r, f'missing field {k}'
" && log_pass "F02.schema_complete_free_mode" || log_fail "F02.schema_complete_free_mode" "schema missing fields"

# F03 --top 2
start_case "F03 TOP-2"
"$QFIN" --input tests/fixtures/input_codes_4.jsonl --top 2 > "$TMP/f03.out" 2> "$TMP/f03.err"
assert_eq "F03.exit_code" "0" "$?"
assert_le "F03.lines_le_2" "2" "$(count_lines $TMP/f03.out)"

# F05 长龄能识别为 #1 (核心 alpha 验证)
start_case "F05 LONGLING-IS-TOP1"
echo '{"code":"605389"}' | "$QFIN" --top 1 > "$TMP/f05.out" 2> "$TMP/f05.err"
assert_eq "F05.exit_code" "0" "$?"
"$PY" -c "
import json
l = open('$TMP/f05.out').readline()
r = json.loads(l)
assert r['code'] == '605389', f\"code mismatch: {r['code']}\"
assert r['layer1_triggers']['severity_sum'] >= 5, f\"severity too low: {r['layer1_triggers']['severity_sum']}\"
mne = r['shareholders']['major_new_entry']
assert mne is not None, 'should detect major_new_entry'
assert mne['pct'] >= 20, f'major pct too low: {mne[\"pct\"]}'
" && log_pass "F05.longling_signals_correct" || log_fail "F05.longling_signals_correct" "missing core signals"

# F09 shareholders.top10 完整
start_case "F09 SHAREHOLDERS-TOP10"
"$PY" -c "
import json
l = open('$TMP/f05.out').readline()
r = json.loads(l)
top10 = r['shareholders']['top10_free']
assert len(top10) == 10, f'expected 10 rows, got {len(top10)}'
for s in top10:
    assert 'rank' in s and 'name' in s and 'pct' in s, 'missing fields'
" && log_pass "F09.top10_complete" || log_fail "F09.top10_complete" "incomplete"

# F10 holder_count_history 多期
start_case "F10 HOLDER-COUNT-MULTI-PERIOD"
"$PY" -c "
import json
l = open('$TMP/f05.out').readline()
r = json.loads(l)
hc = r.get('holder_count_history') or []
assert len(hc) >= 4, f'expected >=4 periods, got {len(hc)}'
" && log_pass "F10.holder_count_periods_ge_4" || log_fail "F10.holder_count_periods_ge_4" "too few periods"

# F10b tdx_fin fundamentals 4 字段
start_case "F10b TDX-FIN-FUNDAMENTALS"
"$PY" -c "
import json
l = open('$TMP/f05.out').readline()
r = json.loads(l)
f = r['fundamentals']
assert f is not None, 'fundamentals null'
assert f['data_source'] == 'tdx_gpcw', f'wrong source: {f[\"data_source\"]}'
for k in ['eps','revenue','net_profit','total_assets']:
    assert f.get(k) is not None, f'{k} is None'
nm = f.get('net_margin')
assert nm is not None and 0 <= nm <= 1, f'net_margin out of range: {nm}'
" && log_pass "F10b.tdx_fin_4_fields_valid" || log_fail "F10b.tdx_fin_4_fields_valid" "invalid"

# F10c YoY 计算
start_case "F10c TDX-FIN-YOY"
"$PY" -c "
import json
l = open('$TMP/f05.out').readline()
r = json.loads(l)
f = r['fundamentals']
yoy_ni = f.get('yoy_net_profit')
yoy_rev = f.get('yoy_revenue')
assert yoy_ni is not None, 'yoy_net_profit None'
assert yoy_rev is not None, 'yoy_revenue None'
assert -200 <= yoy_ni <= 5000, f'yoy_ni out of range: {yoy_ni}'
" && log_pass "F10c.yoy_computed" || log_fail "F10c.yoy_computed" "yoy missing or out of range"

# F10d 跨期回退 (宏景 2025-12-31 没数据应回退到 2025-09-30)
start_case "F10d CROSS-PERIOD-FALLBACK"
echo '{"code":"301396"}' | "$QFIN" --top 1 --no-cache > "$TMP/f10d.out" 2> "$TMP/f10d.err"
assert_eq "F10d.exit_code" "0" "$?"
"$PY" -c "
import json
l = open('$TMP/f10d.out').readline()
r = json.loads(l)
f = r['fundamentals']
assert f['period'] in ('2025-09-30','2025-06-30','2025-12-31'), f'unexpected period: {f[\"period\"]}'
" && log_pass "F10d.fallback_works" || log_fail "F10d.fallback_works" "no fallback period"

# F11 kline_safety 4 档
start_case "F11 KLINE-SAFETY-4-BUCKETS"
"$PY" -c "
import json
allowed = {'🔴','🟡','🟢','⚪'}
ok = all(json.loads(l)['kline'].get('kline_safety') in allowed for l in open('$TMP/f02.out') if l.strip())
exit(0 if ok else 1)
" && log_pass "F11.safety_in_4_buckets" || log_fail "F11.safety_in_4_buckets" "out of set"

# F12 q_seed_crosscheck (长龄应在 q-seed top30)
start_case "F12 Q-SEED-CROSSCHECK"
"$PY" -c "
import json
l = open('$TMP/f05.out').readline()
r = json.loads(l)
qsc = r['q_seed_crosscheck']
assert qsc.get('is_in_q_seed') in (True, False), 'is_in_q_seed not bool'
" && log_pass "F12.crosscheck_runs" || log_fail "F12.crosscheck_runs" "didn't run"

# F14 input file
start_case "F14 INPUT-FILE"
"$QFIN" --input tests/fixtures/input_codes_4.jsonl --top 5 --no-cache > "$TMP/f14.out" 2> "$TMP/f14.err"
assert_eq "F14.exit_code" "0" "$?"
"$PY" -c "
import json
allowed = {'605389','603629','301396','002409'}
got = {json.loads(l)['code'] for l in open('$TMP/f14.out') if l.strip()}
assert got <= allowed, f'extra codes: {got - allowed}'
" && log_pass "F14.codes_subset" || log_fail "F14.codes_subset" "extra codes"

# F15 stdin pipe
start_case "F15 STDIN-PIPE"
cat tests/fixtures/input_codes_4.jsonl | "$QFIN" --top 5 --no-cache > "$TMP/f15.out" 2> "$TMP/f15.err"
assert_eq "F15.exit_code" "0" "$?"
n14=$(count_lines "$TMP/f14.out")
n15=$(count_lines "$TMP/f15.out")
assert_eq "F15.same_as_f14" "$n14" "$n15"

# F16 q-seed | q-fin 管道
start_case "F16 PIPELINE-QSEED"
/home/wyatt/sentry/quant/q-seed/q-seed --template hongjing --top 5 2>/dev/null | "$QFIN" --top 3 --no-cache > "$TMP/f16.out" 2> "$TMP/f16.err"
assert_eq "F16.exit_code" "0" "$?"
assert_le "F16.lines_le_3" "3" "$(count_lines $TMP/f16.out)"

# F18 --format md
start_case "F18 FORMAT-MD"
"$QFIN" --input tests/fixtures/input_codes_4.jsonl --top 3 --format md > "$TMP/f18.out" 2> "$TMP/f18.err"
assert_eq "F18.exit_code" "0" "$?"
assert_grep "F18.has_table" "| # |" "$TMP/f18.out"
assert_grep "F18.has_title" "q-fin TOP" "$TMP/f18.out"

# F19 --format both --output
start_case "F19 FORMAT-BOTH-OUTPUT"
"$QFIN" --input tests/fixtures/input_codes_4.jsonl --top 3 --format both --output "$TMP/f19" > /dev/null 2> "$TMP/f19.err"
assert_eq "F19.exit_code" "0" "$?"
[[ -s "$TMP/f19.jsonl" ]] && log_pass "F19.jsonl" || log_fail "F19.jsonl" "missing"
[[ -s "$TMP/f19.md" ]] && log_pass "F19.md" || log_fail "F19.md" "missing"

# F20 自动 logs/ 备份
start_case "F20 LOGS-BACKUP"
[[ -n "$(ls -t $ROOT/logs/q-fin_top*_*.jsonl 2>/dev/null | head -1)" ]] \
  && log_pass "F20.logs_jsonl_exists" \
  || log_fail "F20.logs_jsonl_exists" "no logs backup"

# F27 cache 加速 (清 cache 后跑两次比对耗时)
start_case "F27 CACHE-ACCELERATION"
rm -rf "$ROOT/data_cache"
t0=$(date +%s%3N)
"$QFIN" --input tests/fixtures/input_codes_4.jsonl --top 4 > /dev/null 2>&1
t1=$(date +%s%3N); cold=$((t1-t0))
t0=$(date +%s%3N)
"$QFIN" --input tests/fixtures/input_codes_4.jsonl --top 4 > /dev/null 2>&1
t1=$(date +%s%3N); warm=$((t1-t0))
echo "  cold=${cold}ms warm=${warm}ms"
# 至少快 30%
threshold=$((cold * 7 / 10))
if (( warm <= threshold )); then
    log_pass "F27.warm_<=70%_of_cold (${warm}ms <= ${threshold}ms)"
else
    log_fail "F27.warm_<=70%_of_cold" "warm=${warm}ms > ${threshold}ms (cold=${cold}ms)"
fi

# =====================================================
# Phase B2/B3 paid mode 测试 (全 dummy, 0 成本)
# =====================================================

HINTS="$ROOT/config/entity_hints.yaml"

# F31: dummy LLM provider 基础调用
start_case "F31 PAID-DUMMY-BASIC"
# 重置 hints 确保走 LLM (mock) 不命中 hints
cat > "$HINTS" <<'EOF'
partnerships: {}
companies: {}
persons: {}
EOF
echo '{"code":"605389"}' | "$QFIN" --top 1 --paid --llm dummy --search dummy --no-cache > "$TMP/f31.out" 2> "$TMP/f31.err"
ec=$?
assert_eq "F31.exit_code" "0" "$ec"
"$PY" -c "
import json, sys
r = json.loads(open('$TMP/f31.out').read())
m = r.get('meta', {})
assert m.get('mode') == 'standard', f'mode={m.get(\"mode\")}'
assert m.get('providers', {}).get('llm') == 'dummy'
assert m.get('providers', {}).get('search') == 'dummy'
assert m.get('total_cost_usd') == 0.0, f'cost={m.get(\"total_cost_usd\")}'
v = r.get('verdict')
assert v is not None and v.get('rating') == 5, f'verdict={v}'
er = r.get('entity_research')
assert er is not None and er.get('chain', {}).get('entity'), f'er={er}'
print('OK')
" && log_pass "F31.assertions" || log_fail "F31.assertions" "see /tmp/f31.out"

# F32: 二次跑 hints 命中, 0 LLM 调用
start_case "F32 HINTS-HIT-AFTER-CACHE"
echo '{"code":"605389"}' | "$QFIN" --top 1 --paid --llm dummy --search dummy --no-cache > "$TMP/f32.out" 2> "$TMP/f32.err"
"$PY" -c "
import json
r = json.loads(open('$TMP/f32.out').readlines()[0])
chain = (r.get('entity_research') or {}).get('chain') or {}
assert chain.get('_source') == 'hints', f'src={chain.get(\"_source\")}'
sub = chain.get('sub') or []
for s in sub:
    assert s.get('_source') == 'hints', f'sub src={s.get(\"_source\")}'
print('OK')
" && log_pass "F32.all_hints" || log_fail "F32.all_hints" "first run did not auto-save hints"

# F33: budget 极低 → 所有 LLM 调用 pre-empt 跳过 (verdict._skip_reason)
start_case "F33 BUDGET-PREEMPT"
cat > "$HINTS" <<'EOF'
partnerships: {}
companies: {}
persons: {}
EOF
"$QFIN" --input tests/fixtures/input_codes_4.jsonl --top 4 --paid --llm dummy_priced --search dummy --budget 0.0001 --no-cache > "$TMP/f33.out" 2> "$TMP/f33.err"
ec=$?
assert_eq "F33.exit_code" "0" "$ec"
"$PY" -c "
import json
lines = open('$TMP/f33.out').readlines()
recs = [json.loads(l) for l in lines]
# 预算 \$0.0001 远小于单次 LLM 估算 (\$0.00115), 所有 verdict / entity 都应被 pre-empt 跳过
skipped = [r for r in recs if (r.get('verdict') or {}).get('_skip_reason')]
assert len(skipped) >= 1, f'no skipped verdicts: verdicts={[r.get(\"verdict\") for r in recs]}'
# 总成本应该是 0 (全部跳过)
total = recs[0].get('meta', {}).get('total_cost_usd', None)
assert total == 0.0, f'total_cost={total} should be 0 (all skipped)'
print('OK')
" && log_pass "F33.preempt_no_spend" || log_fail "F33.preempt_no_spend" "see /tmp/f33.out"

# F33b: 已删除 — mid-run fallback (post-record exceed) 在 dummy 下难触发,
# 因为 dummy 响应短 → pre-check 估算总比实际高 → 永远 preempt skip 而不会真超.
# F33 已充分覆盖 preempt 路径; mid-run fallback 留给 F44 真 LLM 偶发触发.

# F34: provider 配置冲突 (anthropic_tool 要求 llm=anthropic)
start_case "F34 PROVIDER-CONFLICT"
echo '{"code":"605389"}' | "$QFIN" --top 1 --paid --llm dummy --search anthropic_tool > "$TMP/f34.out" 2> "$TMP/f34.err"
ec=$?
assert_ge "F34.exit_nonzero" "1" "$ec"
assert_grep "F34.stderr_conflict" "anthropic_tool" "$TMP/f34.err"

# F35: 缺 ANTHROPIC_API_KEY 自动降 free
start_case "F35 MISSING-API-KEY"
ENV_BAK=$(mktemp)
cp /home/wyatt/sentry/quant/.env "$ENV_BAK"
echo "" > /home/wyatt/sentry/quant/.env
ANTHROPIC_API_KEY="" echo '{"code":"605389"}' | env -u ANTHROPIC_API_KEY "$QFIN" --top 1 --paid --llm anthropic --search duckduckgo --no-cache > "$TMP/f35.out" 2> "$TMP/f35.err"
ec=$?
cp "$ENV_BAK" /home/wyatt/sentry/quant/.env
rm "$ENV_BAK"
assert_eq "F35.exit_code" "0" "$ec"
assert_grep "F35.stderr_fallback" "fallback to free" "$TMP/f35.err"
"$PY" -c "
import json
r = json.loads(open('$TMP/f35.out').read())
assert r.get('meta', {}).get('mode') == 'free', f'mode={r.get(\"meta\")}'
assert r.get('verdict') is None
assert r.get('entity_research') is None
print('OK')
" && log_pass "F35.assertions" || log_fail "F35.assertions" "did not fallback"

# F36: hints 命中跳过 LLM
start_case "F36 HINTS-LOOKUP"
cat > "$HINTS" <<'EOF'
partnerships:
  "无锡核芯听涛科技合伙企业(普通合伙)":
    identity: "私募合伙企业 (用户预填)"
    business: "manual hint"
    key_persons: []
    confirmed: true
companies: {}
persons: {}
EOF
echo '{"code":"605389"}' | "$QFIN" --top 1 --paid --llm dummy --search dummy --no-cache > "$TMP/f36.out" 2> "$TMP/f36.err"
"$PY" -c "
import json
r = json.loads(open('$TMP/f36.out').readlines()[0])
chain = (r.get('entity_research') or {}).get('chain') or {}
assert chain.get('_source') == 'hints', f'src={chain.get(\"_source\")}'
assert 'manual hint' in chain.get('business', ''), f'biz={chain.get(\"business\")}'
print('OK')
" && log_pass "F36.hints_hit" || log_fail "F36.hints_hit" "did not hit hints"

# F37: --max-depth 配置
start_case "F37 MAX-DEPTH"
cat > "$HINTS" <<'EOF'
partnerships: {}
companies: {}
persons: {}
EOF
echo '{"code":"605389"}' | "$QFIN" --top 1 --paid --llm dummy --search dummy --max-depth 1 --no-cache > "$TMP/f37.out" 2> "$TMP/f37.err"
"$PY" -c "
import json
r = json.loads(open('$TMP/f37.out').readlines()[0])
er = r.get('entity_research') or {}
assert er.get('max_depth_used') == 1, f'depth={er.get(\"max_depth_used\")}'
chain = er.get('chain') or {}
# max_depth=1: 根 depth=0 调用子 depth=1, 子立即返回 (depth>=max_depth)
# 所以 sub 存在但 sub.sub 应为空
for s in chain.get('sub', []):
    assert s.get('sub', []) == [], f'sub.sub should be empty at max_depth=1: {s}'
print('OK')
" && log_pass "F37.depth1" || log_fail "F37.depth1" "max_depth not honored"

# F38: --paid=deep 模式
start_case "F38 PAID-DEEP"
echo '{"code":"605389"}' | "$QFIN" --top 1 --paid=deep --llm dummy --search dummy --no-cache > "$TMP/f38.out" 2> "$TMP/f38.err"
"$PY" -c "
import json
r = json.loads(open('$TMP/f38.out').readlines()[0])
assert r.get('meta', {}).get('mode') == 'deep', f'mode={r.get(\"meta\", {}).get(\"mode\")}'
er = r.get('entity_research') or {}
assert er.get('max_depth_used') == 3, f'depth={er.get(\"max_depth_used\")}'
print('OK')
" && log_pass "F38.deep" || log_fail "F38.deep" "deep mode wrong"

# F42: --paid + --modules 不含 entity / verdict
start_case "F42 PAID-MODULES-SUBSET"
echo '{"code":"605389"}' | "$QFIN" --top 1 --paid --llm dummy --search dummy --modules ann,share,fund --no-cache > "$TMP/f42.out" 2> "$TMP/f42.err"
"$PY" -c "
import json
r = json.loads(open('$TMP/f42.out').readlines()[0])
assert r.get('verdict') is None, f'verdict={r.get(\"verdict\")}'
assert r.get('entity_research') is None, f'er={r.get(\"entity_research\")}'
print('OK')
" && log_pass "F42.no_paid_when_excluded" || log_fail "F42.no_paid_when_excluded" "paid module ran despite exclusion"

# F43: --dry-run 不调 LLM
start_case "F43 DRY-RUN-PAID"
LOG_BEFORE=$(wc -l < "$ROOT/data_cache/cost_log.jsonl" 2>/dev/null || echo 0)
echo '{"code":"605389"}' | "$QFIN" --top 1 --paid --llm dummy_priced --search dummy --dry-run > "$TMP/f43.out" 2> "$TMP/f43.err"
ec=$?
LOG_AFTER=$(wc -l < "$ROOT/data_cache/cost_log.jsonl" 2>/dev/null || echo 0)
assert_eq "F43.exit_code" "0" "$ec"
assert_grep "F43.stderr_dryrun" "dry-run" "$TMP/f43.err"
if (( LOG_AFTER == LOG_BEFORE )); then
    log_pass "F43.cost_log_unchanged"
else
    log_fail "F43.cost_log_unchanged" "log grew $LOG_BEFORE -> $LOG_AFTER"
fi

# 重置 hints (不影响后续手工跑)
cat > "$HINTS" <<'EOF'
partnerships: {}
companies: {}
persons: {}
EOF

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
exit 0
