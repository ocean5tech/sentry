# 部署指南

## 环境要求

- Ubuntu 22.04 / WSL2（Windows 11）
- Python 3.11+
- 磁盘：≥ 50GB（TDX 日线数据约 3GB，财报约 1GB）
- 内存：≥ 8GB（全市场预扫约用 4GB）

---

## 一键部署

```bash
git clone https://github.com/ocean5tech/sentry.git
cd sentry/quant
chmod +x deploy.sh && ./deploy.sh
```

`deploy.sh` 会自动完成：创建虚拟环境 → 安装依赖 → 配置 PATH → 写密钥 → 设置 cron → 测试连通性。

---

## 手动步骤（deploy.sh 展开说明）

### 1. 克隆仓库

```bash
git clone https://github.com/ocean5tech/sentry.git ~/sentry
cd ~/sentry/quant
```

### 2. 创建各子系统虚拟环境

```bash
# q-seed（主算法环境，其他子系统共用它的 .venv）
cd q-seed && python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd ..

# q-fin
cd q-fin && python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd ..

# q-news
cd q-news && python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd ..

# q-dashboard
cd q-dashboard && python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd ..
```

### 3. 配置 PATH

```bash
echo 'export PATH="$HOME/sentry/quant/scripts:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

### 4. 密钥配置

密钥存储在仓库的 `.env.setup`（随 git 提交），`deploy.sh` 会自动复制为 `.env`。

```bash
# deploy.sh 已自动处理，无需手动操作
# 如需更新密钥，直接编辑：
nano ~/sentry/quant/.env
```

`.env.setup` 包含：DEEPSEEK_API_KEY / WECHAT_WEBHOOK_URL / TAVILY_API_KEY

### 5. 首次数据同步

```bash
# 从 Windows 侧手动复制 TDX 数据包到 WSL 可访问路径
# 假设已放到 /mnt/c/Users/<用户>/Downloads/

# 日线数据
q-sync --from-zip /mnt/c/Users/.../hsjday<mmdd>.zip

# 财报数据（季报期更新）
q-sync-fin --from-zip /mnt/c/Users/.../tdxfin<mmdd>.zip
```

### 6. 启动 dashboard（常驻后台）

```bash
cd ~/sentry/quant/q-dashboard
nohup .venv/bin/python -m streamlit run app.py \
  --server.address=0.0.0.0 --server.port=8501 --server.headless=true \
  > /tmp/dashboard.log 2>&1 &
echo "Dashboard: http://localhost:8501"
```

### 7. 设置 cron

```bash
# 启动 cron 服务（WSL2 需要手动启动）
sudo service cron start

# 配置 WSL2 开机自启 cron
echo '[boot]
command = service cron start' | sudo tee -a /etc/wsl.conf

# 安装 cron 任务（已在 deploy.sh 里，也可手动执行）
(crontab -l 2>/dev/null; echo "0 20 * * 1-5 /home/$USER/sentry/quant/scripts/q-pick-today-batch >> /home/$USER/sentry/quant/logs/cron.log 2>&1") | crontab -
(crontab -l 2>/dev/null; echo "15 20 * * 1-5 /home/$USER/sentry/quant/scripts/q-news-daily-batch >> /home/$USER/sentry/quant/logs/cron.log 2>&1") | crontab -
```

### 8. 验证安装

```bash
# 交易日判断
is_trading_day && echo "今天开市" || echo "今天休市"

# 形态扫描（只打印，不推送）
q-pick-today --skip-sync --skip-push --date 2026-04-30

# 新闻扫描（free 模式，不花钱）
q-news --format md | head -30
```

---

## deploy.sh 脚本

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "=== sentry/quant 一键部署 ==="

# 1. Python 版本检查
python3 --version | grep -E "3\.(11|12|13)" || { echo "需要 Python 3.11+"; exit 1; }

# 2. 各子系统 venv
for mod in q-seed q-fin q-news q-dashboard; do
    if [ ! -d "$mod/.venv" ]; then
        echo "创建 $mod venv..."
        cd "$mod"
        python3 -m venv .venv
        .venv/bin/pip install -r requirements.txt -q
        cd "$ROOT"
    else
        echo "$mod venv 已存在，跳过"
    fi
done

# 3. PATH
PROFILE="$HOME/.bashrc"
if ! grep -q "sentry/quant/scripts" "$PROFILE"; then
    echo "export PATH=\"$ROOT/scripts:\$PATH\"" >> "$PROFILE"
    echo "PATH 已配置到 $PROFILE"
fi
export PATH="$ROOT/scripts:$PATH"

# 4. .env 检查
if [ ! -f "$ROOT/.env" ]; then
    echo "⚠️  .env 不存在，请手动创建:"
    echo "   DEEPSEEK_API_KEY=..."
    echo "   WECHAT_WEBHOOK_URL=..."
    echo "   TAVILY_API_KEY=..."
else
    echo ".env 已存在"
fi

# 5. logs 目录
mkdir -p "$ROOT/logs"

# 6. cron（工作日 20:00 + 20:15）
ALREADY=$(crontab -l 2>/dev/null | grep -c "q-pick-today-batch" || true)
if [ "$ALREADY" -eq 0 ]; then
    (crontab -l 2>/dev/null; cat << EOF
# sentry/quant 自动化
0 20 * * 1-5 $ROOT/scripts/q-pick-today-batch >> $ROOT/logs/cron.log 2>&1
15 20 * * 1-5 $ROOT/scripts/q-news-daily-batch >> $ROOT/logs/cron.log 2>&1
EOF
    ) | crontab -
    echo "cron 已安装"
else
    echo "cron 已存在，跳过"
fi

# 7. cron 服务（WSL2）
sudo service cron start 2>/dev/null || true

echo ""
echo "=== 部署完成 ==="
echo "下一步:"
echo "  1. 编辑 .env 填入密钥（如未填）"
echo "  2. q-sync --from-zip <hsjday zip> 同步日线数据"
echo "  3. q-sync-fin --from-zip <tdxfin zip> 同步财报数据"
echo "  4. q-pick-today --skip-sync --skip-push  # 测试运行"
echo "  5. 浏览器打开 http://localhost:8501 查看 dashboard"
```

---

## 数据目录结构

部署后 `~/sentry/quant/data/` 结构：

```
data/
  tdx/                    # TDX 日线数据（q-sync 同步）
    sh/lday/sh*.day       # 上交所（~6000只）
    sz/lday/sz*.day       # 深交所（~5800只）
    bj/lday/bj*.day       # 北交所（~600只）
  tdx_fin/                # TDX 财报数据（q-sync-fin 同步）
    gpcw20261231.dat      # 最新年报
    gpcw20260331.dat      # 最新一季报
    ...
  backup/                 # 上一次同步的备份（自动管理）
  stock_names.csv         # 股票名称缓存
```

---

## 常见问题

**Q: cron 不执行？**
```bash
sudo service cron start
crontab -l  # 确认任务存在
tail -f ~/sentry/quant/logs/cron.log  # 查看执行日志
```

**Q: q-fin 没有推理链（entity_research 为空）？**
正常现象：只有 Layer 1 发现控制权变更、要约收购等重大事件时才触发实体调查链。普通股无重大事件 = entity_research 为空。

**Q: 想切回 Anthropic 用联网搜索？**
```bash
# 临时切换（单次）
echo '{"code":"..."}' | q-fin --paid --llm anthropic

# 永久切换
sed -i 's/active: "deepseek"/active: "anthropic"/' q-fin/config.yaml q-news/config.yaml
```

**Q: IBM 企业代理拦截下载？**
- TDX 数据：在 Windows 侧手动下载 zip，放到固定目录，q-sync/q-sync-fin 用 `--from-zip` 读取
- akshare/RSS 等：通过 baostock TCP 协议绕过（已配置）
