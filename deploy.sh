#!/usr/bin/env bash
# deploy.sh — sentry/quant 一键部署
# 适用: Ubuntu 22.04 / WSL2 (Windows 11)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
die()  { echo -e "${RED}✗${NC} $*"; exit 1; }
step() { echo -e "\n${YELLOW}── $* ──${NC}"; }

echo "╔══════════════════════════════════════╗"
echo "║   sentry/quant 一键部署              ║"
echo "╚══════════════════════════════════════╝"

# ── 0. 前置检查 ──────────────────────────────────────────────
step "检查环境"
command -v python3 >/dev/null || die "需要 python3"
PY_VER=$(python3 -c "import sys; print(sys.version_info[:2])")
[[ "$PY_VER" > "(3, 10)" ]] || die "需要 Python 3.11+，当前 $PY_VER"
ok "Python $PY_VER"

command -v git >/dev/null || die "需要 git"
ok "git $(git --version | awk '{print $3}')"

command -v rsync >/dev/null || { warn "rsync 未安装，自动安装"; sudo apt-get install -y rsync -q; }
ok "rsync 可用"

# ── 1. 各子系统 venv ─────────────────────────────────────────
step "创建虚拟环境 & 安装依赖"
for mod in q-seed q-fin q-news q-dashboard; do
    if [ ! -d "$ROOT/$mod/.venv" ]; then
        echo "  创建 $mod/.venv ..."
        cd "$ROOT/$mod"
        python3 -m venv .venv
        .venv/bin/pip install --upgrade pip -q
        .venv/bin/pip install -r requirements.txt -q
        cd "$ROOT"
        ok "$mod 依赖安装完成"
    else
        ok "$mod/.venv 已存在，跳过"
    fi
done

# q-backtest 复用 q-seed venv（已在 q-backtest-fast 脚本里指定）
ok "q-backtest 复用 q-seed/.venv"

# ── 2. PATH 配置 ─────────────────────────────────────────────
step "配置 PATH"
PROFILE="$HOME/.bashrc"
SCRIPTS_PATH="$ROOT/scripts"
if ! grep -q "sentry/quant/scripts" "$PROFILE" 2>/dev/null; then
    echo "export PATH=\"$SCRIPTS_PATH:\$PATH\"" >> "$PROFILE"
    ok "PATH 已追加到 $PROFILE"
else
    ok "PATH 已存在，跳过"
fi
export PATH="$SCRIPTS_PATH:$PATH"

# ── 3. 目录结构 ──────────────────────────────────────────────
step "创建数据目录"
mkdir -p "$ROOT/data/tdx/sh/lday"
mkdir -p "$ROOT/data/tdx/sz/lday"
mkdir -p "$ROOT/data/tdx/bj/lday"
mkdir -p "$ROOT/data/tdx_fin"
mkdir -p "$ROOT/data/backup"
mkdir -p "$ROOT/logs"
ok "目录结构已创建"

# ── 4. .env 配置（从 .env.setup 复制）─────────────────────────
step "密钥配置"
if [ ! -f "$ROOT/.env" ]; then
    if [ -f "$ROOT/.env.setup" ]; then
        cp "$ROOT/.env.setup" "$ROOT/.env"
        ok ".env 已从 .env.setup 复制"
    else
        warn ".env.setup 不存在，创建空模板"
        cat > "$ROOT/.env" << 'EOF'
ANTHROPIC_API_KEY=
DEEPSEEK_API_KEY=
WECHAT_WEBHOOK_URL=
TAVILY_API_KEY=
EOF
        warn "请编辑 $ROOT/.env 填入密钥"
    fi
else
    ok ".env 已存在"
fi
# 检查必要密钥
for KEY in DEEPSEEK_API_KEY WECHAT_WEBHOOK_URL; do
    if grep -q "^${KEY}=.\+" "$ROOT/.env"; then
        ok "  $KEY 已配置"
    else
        warn "  $KEY 未配置，某些功能不可用"
    fi
done

# ── 5. Cron 配置 ──────────────────────────────────────────────
step "配置 Cron（每工作日 20:00 + 20:15）"
ALREADY=$(crontab -l 2>/dev/null | grep -c "q-pick-today-batch" || true)
if [ "$ALREADY" -eq 0 ]; then
    (crontab -l 2>/dev/null; cat << CRON
# sentry/quant 自动化 (每工作日)
0 20 * * 1-5 $ROOT/scripts/q-pick-today-batch >> $ROOT/logs/cron.log 2>&1
15 20 * * 1-5 $ROOT/scripts/q-news-daily-batch >> $ROOT/logs/cron.log 2>&1
CRON
    ) | crontab -
    ok "Cron 已安装"
else
    ok "Cron 已存在，跳过"
fi

# WSL2 启动 cron 服务
if sudo service cron start 2>/dev/null; then
    ok "Cron 服务已启动"
else
    warn "Cron 服务启动失败，可能需要手动: sudo service cron start"
    warn "WSL2 永久自启: echo '[boot]\\ncommand = service cron start' | sudo tee -a /etc/wsl.conf"
fi

# ── 6. dashboard 后台启动 ─────────────────────────────────────
step "启动 Dashboard"
DASH_PID=$(pgrep -f "streamlit run app.py" || true)
if [ -n "$DASH_PID" ]; then
    ok "Dashboard 已在运行 (PID $DASH_PID) → http://localhost:8501"
else
    cd "$ROOT/q-dashboard"
    nohup .venv/bin/python -m streamlit run app.py \
        --server.address=0.0.0.0 --server.port=8501 --server.headless=true \
        > /tmp/dashboard.log 2>&1 &
    sleep 2
    ok "Dashboard 已启动 → http://localhost:8501"
    cd "$ROOT"
fi

# ── 7. 连通性测试 ─────────────────────────────────────────────
step "连通性测试"

# DeepSeek
if grep -q "^DEEPSEEK_API_KEY=.\+" "$ROOT/.env" 2>/dev/null; then
    source "$ROOT/.env"
    RESULT=$(cd "$ROOT/q-fin" && .venv/bin/python -c "
import openai, os
client = openai.OpenAI(api_key=os.environ.get('DEEPSEEK_API_KEY',''), base_url='https://api.deepseek.com/v1')
r = client.chat.completions.create(model='deepseek-chat', max_tokens=5, messages=[{'role':'user','content':'ok'}])
print(r.choices[0].message.content[:20])
" 2>/dev/null || echo "FAIL")
    if [ "$RESULT" != "FAIL" ]; then
        ok "DeepSeek API 连通"
    else
        warn "DeepSeek API 连通失败，检查 DEEPSEEK_API_KEY"
    fi
else
    warn "DEEPSEEK_API_KEY 未配置，跳过测试"
fi

# 交易日判断
"$ROOT/scripts/is_trading_day" && ok "今天是交易日" || ok "今天是非交易日（正常）"

# ── 8. 完成 ───────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════╗"
echo "║   部署完成！                         ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "下一步:"
echo "  1. 如 .env 有未填密钥，编辑补全"
echo "  2. q-sync --from-zip <hsjday zip>    # 同步日线数据"
echo "  3. q-sync-fin --from-zip <tdxfin zip> # 同步财报数据"
echo "  4. q-pick-today --skip-sync --skip-push  # 测试（不推送）"
echo "  5. 打开 http://localhost:8501 查看 Dashboard"
echo ""
echo "Cron 日志: tail -f $ROOT/logs/cron.log"
