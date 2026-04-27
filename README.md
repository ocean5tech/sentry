# sentry — A股量化选股系统

**V1.0** (2026-04-27) · 9 个 q-* 命令 + cron 编排, 围绕 A 股 ~5500 只股票的事件/形态/基本面驱动选股.

> 完整介绍 → [PROJECT_SUMMARY.md](./PROJECT_SUMMARY.md)

## 命令清单

```
核心 (V0)
├── q-seed       形态相似度 KNN (永远 free)
├── q-fin        基本面深挖 (free / --paid 烧 token)
└── q-news       事件驱动新闻 (规则引擎)

辅助 (V1)
├── q-sync       下 TDX 日线 zip + 备份回滚
├── q-sync-fin   下 TDX 财报 zip + 备份回滚
├── q-clean      retention 磁盘清理
├── q-push       stdin JSONL → 企业微信群机器人
├── q-dashboard  Streamlit 4-tab 详情 (华尔街金融配色)
└── q-batch.sh   6pm cron 编排所有
```

## 文档

| 文档 | 用途 |
|---|---|
| [PROJECT_SUMMARY.md](./PROJECT_SUMMARY.md) | 项目总览 (开始读这个) |
| [docs/USER_GUIDE_q-seed.md](./docs/USER_GUIDE_q-seed.md) | q-seed 用户手册 |
| [docs/USER_GUIDE_q-fin.md](./docs/USER_GUIDE_q-fin.md) | q-fin 用户手册 (含 paid + provider 切换) |
| [docs/USER_GUIDE_q-news.md](./docs/USER_GUIDE_q-news.md) | q-news 用户手册 |
| [docs/USER_GUIDE_aux.md](./docs/USER_GUIDE_aux.md) | 5 辅助命令 + cron 部署 |
| [docs/DESIGN_q-{seed,fin,news}.md](./docs/) | 各命令架构设计 |
| [docs/SOURCES_q-news.md](./docs/SOURCES_q-news.md) | 内网新闻源实测可用性 |
| [CLAUDE.md](./CLAUDE.md) | AI 助手 guidelines (含疯牛种子 spec) |

## 部署

```bash
# 1. 凭据
cat > ~/sentry/quant/.env <<EOF
ANTHROPIC_API_KEY=sk-ant-...
WECHAT_WEBHOOK_URL=https://qyapi.weixin.qq.com/...
EOF
chmod 600 ~/sentry/quant/.env

# 2. 各命令 venv
for cmd in q-seed q-fin q-news q-dashboard; do
    cd ~/sentry/quant/$cmd
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
done

# 3. cron 6pm
crontab -e
# 加: 0 18 * * 1-5 /home/wyatt/sentry/quant/q-batch.sh
```

详见 [docs/USER_GUIDE_aux.md](./docs/USER_GUIDE_aux.md).

## 维护

私人项目, wooyoo@gmail.com.
