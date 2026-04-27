# q-* 辅助命令用户手册

**v1.0.0** · 2026-04-27 ship

围绕 q-seed/q-fin/q-news 的 5 个辅助命令 + 1 个 cron 编排. 每个命令单一职责.

---

## 1. `q-sync` — 下载 TDX 日线 zip

```bash
q-sync                              # 下载 hsjday.zip → data/tdx/
```

**逻辑**:
1. 备份文件夹清空 (`data/backup/tdx/`)
2. 当天文件夹剪到备份 (`data/tdx/` → `data/backup/tdx/`)
3. 下载 `https://data.tdx.com.cn/vipdoc/hsjday.zip` 到 `/tmp/`
4. 解压到 `data/tdx/`
5. 失败 → 当天清空, 备份 cp 回当天 (回滚)
6. 删 /tmp zip

**配置** (`q-sync/config.yaml`):
```yaml
url: "https://data.tdx.com.cn/vipdoc/hsjday.zip"
target_dir: "/home/wyatt/sentry/quant/data/tdx"
backup_dir: "/home/wyatt/sentry/quant/data/backup/tdx"
```

---

## 2. `q-sync-fin` — 下载 TDX 财报 zip

```bash
q-sync-fin                          # 下载 tdxfin.zip → data/tdx_fin/
```

逻辑同 `q-sync`, 共享 `q-sync/_download.py`.

---

## 3. `q-clean` — 硬盘清理

```bash
q-clean                             # 默认 dry-run, 看会删什么
q-clean --apply                     # 真删
q-clean --apply --quiet             # 安静模式 (cron 用)
```

**清理规则** (`q-clean/config.yaml`):
- `data/backup/**` 留 3 天 (上次下载备份)
- `data/daily/*.parquet` 留 180 天
- `q-*/logs/q-*_*.jsonl,md` 留 30 天
- `q-fin/data_cache/*.json` 留 7 天
- `q-news/data_cache/*.json` 留 1 天

**永不删**: `*.env`, `**/config.yaml`, `cost_log.jsonl`.

报告磁盘剩余空间 (free < 5GB 时警告).

---

## 4. `q-push` — 企业微信群机器人推送

```bash
# 单向推送 (markdown 格式)
q-fin --paid --top 5 | q-push --tag "基本面 TOP 5"

# Dry-run 看 markdown 不真发
q-fin --top 3 | q-push --tag "测试" --dry-run

# 不带 dashboard 链接
q-news --top 5 | q-push --tag "新闻" --no-link
```

**输入**: stdin JSON Lines (q-seed/q-fin/q-news 的输出).
**输出**: POST 企业微信群机器人 webhook.

**消息格式示例**:
```
🌟 q-fin · 基本面 TOP 5 (2026-04-27)

🥇 长龄液压 (605389) ⭐⭐⭐
> score=30.5 · 教科书级芯片产业资本借壳

🥈 扬电科技 (301012) ⭐⭐
> score=5.2 · 控制权变更但已涨 62%
...
[查看 dashboard](http://localhost:8501?date=2026-04-27)
```

**凭据**: `~/sentry/quant/.env` 的 `WECHAT_WEBHOOK_URL`.

⚠️ webhook key 在 chat 暴露过, 部署前到企业微信群机器人设置重新生成.

---

## 5. `q-dashboard` — Streamlit 详情展示

```bash
q-dashboard                         # 启动 → http://localhost:8501
```

**界面**: 华尔街金融页面风格 (dark + 金色强调 + 等宽字体).

**4 个 Tab**:
- **OVERVIEW** — 当日统计 (q-seed/q-fin/q-news 各多少条) + token 月度成本曲线 + 执行履历
- **Q-SEED** — TOP N 表格 + 点击展开 details + kline
- **Q-FIN** — TOP N + ⭐ verdict + 点击展开 entity_research.chain + Layer 2 全量
- **Q-NEWS** — TOP N + concept_status.已启动 + 点击展开 5 层 reasoning_chain

**日期选择**: 左侧 sidebar, 仅显示有 logs 的日期 (隐含交易日). URL `?date=2026-04-27` 直接跳.

**配色**:
- 背景 `#0E1117` (深炭黑)
- 主色 `#FFD700` (金, 高亮数字+链接)
- 涨 `#00D4AA` (青绿) / 跌 `#FF4757` (红)
- 字体 SF Mono / Monaco 等宽

**端口**: 默认 8501 (改 `.streamlit/config.toml` 调).

---

## 6. `q-batch.sh` — 6pm cron 编排

```bash
./q-batch.sh                        # 默认 free, 不烧 token
./q-batch.sh --paid                 # paid, q-fin 烧 ~$0.30/天
./q-batch.sh --skip-sync            # 跳过下载 (调试)
./q-batch.sh --skip-push            # 跳过企业微信推送
./q-batch.sh --top-seed 50 --top-fin 20 --top-news 30
```

**流程** (顺序执行, 失败 continue):
1. `q-sync` 下日线
2. `q-sync-fin` 下财报
3. `q-seed --top 30` → `_pipe_seed.jsonl`
4. `q-fin --top 10 [--paid]` (input = q-seed 输出)
5. `q-news --top 30 --window-hours 24`
6. `q-push` (推 fin top 5 + news top 5 到企业微信)
7. `q-clean --apply --quiet`

**日志**: `~/sentry/quant/logs/batch_YYYYMMDD_HHMM.log`.

**Cron 配置** (你装):
```cron
# 每周一到周五 18:00 跑 free 模式
0 18 * * 1-5 /home/wyatt/sentry/quant/q-batch.sh >> /home/wyatt/sentry/quant/logs/batch.log 2>&1

# 周日 19:00 跑 paid 深度版
0 19 * * 0 /home/wyatt/sentry/quant/q-batch.sh --paid >> /home/wyatt/sentry/quant/logs/batch.log 2>&1
```

`crontab -e` 加上面两行即可.

---

## 部署检查清单

```bash
# 1. 凭据
cat ~/sentry/quant/.env                    # 应含 ANTHROPIC_API_KEY + WECHAT_WEBHOOK_URL
chmod 600 ~/sentry/quant/.env

# 2. 各命令可执行
chmod +x ~/sentry/quant/q-sync/q-sync
chmod +x ~/sentry/quant/q-sync-fin/q-sync-fin
chmod +x ~/sentry/quant/q-clean/q-clean
chmod +x ~/sentry/quant/q-push/q-push
chmod +x ~/sentry/quant/q-dashboard/q-dashboard
chmod +x ~/sentry/quant/q-batch.sh

# 3. (可选) 加到 PATH
ln -s ~/sentry/quant/q-sync/q-sync          ~/.local/bin/q-sync-day
ln -s ~/sentry/quant/q-sync-fin/q-sync-fin  ~/.local/bin/q-sync-fin
ln -s ~/sentry/quant/q-clean/q-clean        ~/.local/bin/q-clean
ln -s ~/sentry/quant/q-push/q-push          ~/.local/bin/q-push
ln -s ~/sentry/quant/q-dashboard/q-dashboard ~/.local/bin/q-dashboard

# 4. 装 cron
crontab -e
# 粘上面 cron 配置

# 5. 启 dashboard (前台)
q-dashboard
# 浏览器打开 http://localhost:8501
```

---

## 常见问题

### Q: 我手动跑 q-batch.sh 看效果
```bash
~/sentry/quant/q-batch.sh --skip-sync         # 跳过下载, 用现有数据
```

### Q: dashboard 不显示今天的数据
检查 `~/sentry/quant/q-{seed,fin,news}/logs/` 有没有今日 jsonl 文件. 没有的话先 `q-batch.sh`.

### Q: q-push 报错 webhook 失败
1. 检查 `~/sentry/quant/.env` 的 `WECHAT_WEBHOOK_URL` 有效
2. 企业微信群机器人 key 可能被废 (你之前 chat 贴了, 应该重置过)
3. dry-run 看 markdown: `... | q-push --dry-run`

### Q: cron 跑了但没看到日志
`~/sentry/quant/logs/batch_*.log` 看每次跑的输出. `crontab -l` 看当前 cron.

### Q: 磁盘紧张
`q-clean --apply` 看清掉多少. config 调短 `retention_days`.

### Q: 想加企业微信双向交互 (机器人聊天)
V0 不做 (内网拦回调). 真要的话 V1 加 `q-bot` (企业微信应用 API + 内网穿透).
