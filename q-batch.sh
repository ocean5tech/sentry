#!/usr/bin/env bash
# q-batch: 6pm 批处理编排. cron 入口.
# 单一职责协调: 调用各 q-* 命令, 不做复杂逻辑.
#
# 用法:
#   ./q-batch.sh              # 默认 free (不烧 token)
#   ./q-batch.sh --paid       # paid 模式 (烧 ~$0.30/天)
#   ./q-batch.sh --paid --skip-sync  # 跳过下载 (调试用)
#
# Cron 配置 (每天 18:00):
#   0 18 * * 1-5 /home/wyatt/sentry/quant/q-batch.sh >> /home/wyatt/sentry/quant/logs/batch.log 2>&1
#   (1-5 = 周一到周五, 跳过周末)

set -uo pipefail

QUANT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$QUANT_DIR/logs"
RUN_LOG="$LOG_DIR/batch_$(date +%Y%m%d_%H%M).log"

mkdir -p "$LOG_DIR"

# ── flags ──────────────────────────────────────────────────────
PAID=""
SKIP_SYNC=""
SKIP_PUSH=""
TOP_SEED=30
TOP_FIN=10
TOP_NEWS=30

while [[ $# -gt 0 ]]; do
    case "$1" in
        --paid)      PAID="--paid"; shift ;;
        --skip-sync) SKIP_SYNC="1"; shift ;;
        --skip-push) SKIP_PUSH="1"; shift ;;
        --top-seed)  TOP_SEED="$2"; shift 2 ;;
        --top-fin)   TOP_FIN="$2"; shift 2 ;;
        --top-news)  TOP_NEWS="$2"; shift 2 ;;
        *)           echo "未知参数: $1"; exit 2 ;;
    esac
done

# ── 工具函数 ────────────────────────────────────────────────────
log() {
    local msg="[$(date '+%H:%M:%S')] $*"
    echo "$msg" | tee -a "$RUN_LOG"
}

run_step() {
    local name="$1"; shift
    log "===== $name ====="
    log "$ $*"
    if "$@" >>"$RUN_LOG" 2>&1; then
        log "✅ $name 完成"
    else
        log "❌ $name 失败 (continue)"
    fi
}

# ── 执行 ──────────────────────────────────────────────────────
log "🌟 q-batch 开始 (mode=$([[ -n $PAID ]] && echo paid || echo free))"

# 1. 数据下载
if [[ -z "$SKIP_SYNC" ]]; then
    run_step "q-sync 日线"  "$QUANT_DIR/q-sync/q-sync"
    run_step "q-sync-fin 财报" "$QUANT_DIR/q-sync-fin/q-sync-fin"
else
    log "跳过 q-sync / q-sync-fin"
fi

# 2. q-seed 形态扫
SEED_OUT="$LOG_DIR/_pipe_seed.jsonl"
log "===== q-seed 形态 (top=$TOP_SEED) ====="
"$QUANT_DIR/q-seed/q-seed" --top "$TOP_SEED" > "$SEED_OUT" 2>>"$RUN_LOG" || log "❌ q-seed 失败"
log "  q-seed 输出 $(wc -l < "$SEED_OUT") 条"

# 3. q-fin 基本面 (free 或 paid)
FIN_OUT="$LOG_DIR/_pipe_fin.jsonl"
log "===== q-fin 基本面 (top=$TOP_FIN $PAID) ====="
if [[ -s "$SEED_OUT" ]]; then
    cat "$SEED_OUT" | "$QUANT_DIR/q-fin/q-fin" --top "$TOP_FIN" $PAID --budget 0.50 > "$FIN_OUT" 2>>"$RUN_LOG" || log "❌ q-fin 失败"
    log "  q-fin 输出 $(wc -l < "$FIN_OUT") 条"
else
    log "⚠️ q-seed 输出空, 跳过 q-fin"
    : > "$FIN_OUT"
fi

# 4. q-news 新闻
NEWS_OUT="$LOG_DIR/_pipe_news.jsonl"
log "===== q-news 新闻 (top=$TOP_NEWS) ====="
"$QUANT_DIR/q-news/q-news" --top "$TOP_NEWS" --window-hours 24 < /dev/null > "$NEWS_OUT" 2>>"$RUN_LOG" || log "❌ q-news 失败"
log "  q-news 输出 $(wc -l < "$NEWS_OUT") 条"

# 5. q-push 企业微信推送
if [[ -z "$SKIP_PUSH" ]]; then
    if [[ -s "$FIN_OUT" ]]; then
        run_step "q-push (fin top 5)" bash -c "head -5 '$FIN_OUT' | '$QUANT_DIR/q-push/q-push' --tag '基本面 TOP 5'"
    fi
    if [[ -s "$NEWS_OUT" ]]; then
        run_step "q-push (news top 5)" bash -c "head -5 '$NEWS_OUT' | '$QUANT_DIR/q-push/q-push' --tag '新闻 TOP 5'"
    fi
else
    log "跳过 q-push"
fi

# 6. q-clean 清理
run_step "q-clean" "$QUANT_DIR/q-clean/q-clean" --apply --quiet

log "🏁 q-batch 完成"
log "完整日志: $RUN_LOG"

# 清理 pipe 中间文件
rm -f "$SEED_OUT" "$FIN_OUT" "$NEWS_OUT"
