# Sentry Quant — A股量化选股系统

**Status**: V1.0 投产可用 (2026-04-27)
**Owner**: wooyoo@gmail.com
**Last update**: 2026-04-27

围绕 A 股 (~5500 只) 的事件/形态驱动选股. 单进程 + 文件存储, 无 DB, 无后端服务.

---

## 当前能力

```
形态相似度  q-seed       (永远 free)
        ↓
基本面深挖  q-fin        (free / --paid 烧 token, 含 ⭐ verdict + 实体调查)
        ↓
新闻驱动   q-news       (free 规则引擎, --paid V1.5 LLM 兜底)
        ↓
推送       q-push       → 企业微信群机器人
        ↓
可视化     q-dashboard  → http://localhost:8501 (华尔街金融配色)
```

5 个辅助命令 + 1 个 cron 编排自动化.

**核心 alpha** (q-fin F44 真 LLM 实测验证, 2026-04-26):
长龄液压 605389 自动追到 "核芯听涛 SPV → 核芯互联 → 胡康桥 (AMD出身, 中科院半导体所博士)", 与 0424 手工调研一致, 单只 ~$0.10.

---

## 9 个 q-* 命令

| 命令 | 职责 | 状态 | 默认成本 |
|---|---|---|---|
| `q-seed` | 形态 KNN (hongjing 严格 + litong loose) | ✅ v1 | $0 永远 free |
| `q-fin` | 基本面 + 公告 + 股东 + 实体调查 + ⭐ verdict | ✅ v1 | $0 free / 显式 `--paid` 才烧 |
| `q-news` | 新闻规则引擎 → 概念启动判定 | ✅ v1 | $0 free |
| `q-sync` | 下 TDX 日线 zip + 备份回滚 | ✅ v1 | — |
| `q-sync-fin` | 下 TDX 财报 zip + 备份回滚 | ✅ v1 | — |
| `q-clean` | retention 删旧文件 + 磁盘检查 | ✅ v1 | — |
| `q-push` | stdin JSONL → 企业微信群机器人 | ✅ v1 | — |
| `q-dashboard` | Streamlit 4 tab 详情展示 | ✅ v1 | — |
| `q-batch.sh` | 6pm cron 编排所有 | ✅ v1 | — |

---

## 设计原则

### L2 独立性
每个命令独立目录: 自带 `.venv`, `lib/`, `config.yaml`, `requirements.txt`. 互相不 import. 删任意一个不影响其他. 允许代码重复 > 共享耦合.

### 默认 free, 显式 `--paid` 才烧 token
- `q-seed` 无 `--paid` 参数 (永远 free)
- `q-fin` / `q-news` 默认 free, `--paid` / `--paid=deep` 启用 LLM
- 单只 paid 成本 $0.04-0.13 (max_uses=1 / max_uses=2)
- 月度 deep dive 估算 $10-15

### 配置先行 (无硬编码)
所有路径/接口名/阈值/关键词/概念库都在 `config.yaml` 或 `config/*.yaml`.

### Provider 抽象 (LLM/Search 可切供应商)
- LLM 5 个: anthropic / openai_compat (覆盖 OpenAI/DeepSeek/Moonshot/通义/豆包) / dummy / dummy_priced / factory
- Search 4 个: duckduckgo / anthropic_tool / dummy / factory
- 切供应商改 `config.providers.<llm/search>.active` 一字段, 业务代码不动

### TDD 工作流
每个命令 ship v1 都跟随 `tests/TEST_PLAN_*.md` + `tests/run_tests.sh`. 总通过率:
- q-seed: 57 PASS / 0 FAIL
- q-fin: 65 PASS / 0 FAIL (含 F44 真 LLM 验证)
- q-news: 31 PASS / 0 FAIL (含网络拉新闻)

---

## 数据源

### 行情
- **TDX 日线** `~/sentry/quant/data/tdx/{sh,sz,bj}/lday/*.day` (q-sync, ~5500 只)
- **TDX 财报** `~/sentry/quant/data/tdx_fin/gpcw*.dat` (q-sync-fin, 二进制 584 字段/股, 4 verified idx 交叉验证)

### 新闻 (实测 IBM 内网通)
- **akshare 5 接口**: `stock_info_global_{cls,em}` / `stock_info_cjzc_em` / `news_cctv` / `stock_news_em`
- **新华网 5 RSS**: 时政/经济/科技/国际/军事
- **不可用**: 海关 (412 反爬), RSSHub (403), Reuters/Bloomberg (403/401)

### 基本面 (akshare)
- `stock_zh_a_disclosure_report_cninfo` (公告)
- `stock_gdfx_free_top_10_em` (十大流通)
- `stock_zh_a_gdhs_detail_em` (股东户数)

### LLM
- Anthropic Claude 4.5 Haiku (entity_research) / Sonnet 4.6 (verdict TOP 5)
- 内置 web_search_20250305 tool (DDG 中文实测不可用)
- 用户 $50 余额, 月预估 $10-15 (deep dive 3-5 只/天)

---

## 文档清单

`docs/` 目录:

| 文档 | 用途 |
|---|---|
| `PROJECT_SUMMARY.md` | 本文档 (项目总览) |
| `USER_GUIDE_q-seed.md` | q-seed 完整手册 (~280 行) |
| `USER_GUIDE_q-fin.md` | q-fin 完整手册 (~430 行) |
| `USER_GUIDE_q-news.md` | q-news 完整手册 (~430 行) |
| `USER_GUIDE_aux.md` | 辅助 5 命令 + cron 部署 (~250 行) |
| `DESIGN_q-seed.md` | q-seed 架构设计 |
| `DESIGN_q-fin.md` | q-fin 两层流水线 + provider 抽象 设计 (~890 行) |
| `DESIGN_q-news.md` | q-news 推理引擎升级链 设计 |
| `SOURCES_q-news.md` | 新闻源 IBM 内网实测 (哪些通/拦) |
| `SYSTEM_DESIGN.md` | 0424 v2 架构 (3 命令 + JSON Lines 管道) |

会话日志 (供 audit, 不必读):
- `0424.mem` (~9459 行 0424 设计会话)
- `0425.mem` (~6894 行 0425 实施会话)

---

## 项目目录结构

```
~/sentry/quant/
├── README.md                       # 项目入口
├── PROJECT_SUMMARY.md              # 本总结
├── CLAUDE.md                       # AI 助手 guidelines (内含疯牛种子完整 spec)
├── q-batch.sh                      # cron 6pm 编排
│
├── q-seed/      ─┐
├── q-fin/        │ 3 个核心命令 (V0)
├── q-news/      ─┘
│
├── q-sync/      ─┐
├── q-sync-fin/   │
├── q-clean/      │ 5 个辅助命令 (V1)
├── q-push/       │
├── q-dashboard/ ─┘
│
├── docs/                           # 所有文档
├── data/                           # TDX 日线 + 财报 (gitignore)
├── logs/                           # batch 日志 (gitignore)
└── scripts/                        # 老 q-scan / q-pull 等
```

---

## 部署 checklist

```bash
# 1. 凭据
cat ~/sentry/quant/.env             # 应含 ANTHROPIC_API_KEY + WECHAT_WEBHOOK_URL
chmod 600 ~/sentry/quant/.env

# 2. 各命令 venv (一次性)
for cmd in q-seed q-fin q-news q-dashboard; do
    cd ~/sentry/quant/$cmd
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
done

# 3. 装 cron
crontab -e
# 加: 0 18 * * 1-5 /home/wyatt/sentry/quant/q-batch.sh

# 4. 启 dashboard
cd ~/sentry/quant/q-dashboard && ./q-dashboard
# 浏览器: http://localhost:8501
```

---

## 已知限制

| 项 | 影响 | 解决方案 |
|---|---|---|
| IBM 内网拦 `data.tdx.com.cn` | q-sync 无法直连下载 | `q-sync --from-zip <path>` 用本地 zip |
| 内网拦 `push2.eastmoney.com` | akshare 概念成份股接口 (`stock_board_concept_cons_em`) 不通 | sector_map 手维 / 我代填 |
| DDG 中文搜索失效 | q-fin entity_research 凭知识猜会偏 | 自动切 anthropic 内置 web_search tool |
| sector_map.yaml stocks 部分代填 | 我用 LLM 训练知识填了 138 只龙头, 用户应 review | 用户审核调整 |

---

## 下一步路线 (V1.5+)

| 项 | 工时 | 状态 |
|---|---|---|
| `q-news` paid LLM 兜底 (规则未命中事件用 Haiku) | 2h | 待启 |
| 企业微信双向交互 (`q-bot` 应用 API + 内网穿透) | 1d | 待启 |
| akshare 交易日历集成 (dashboard sidebar 仅显交易日) | 30min | 待启 |
| 月度报表推送 (token 累计 / 命中率) | 1h | 待启 |
| TDX gpcw 字段映射扩充 (现 4 verified, 需 ROE/毛利率/现金流) | 2h | 待启 |

---

## 开发历程 (按 commit 时间)

- **2026-04-17** V0 ship (q-scan + q-pull + q-sync legacy)
- **2026-04-21** signal validation (backtest_limit_up / backtest_squeeze 反向特征研究)
- **2026-04-22~23** wave_washout 研究 → 疯牛种子 (q-seed 雏形)
- **2026-04-24** SYSTEM_DESIGN 草案 (3 命令架构 + JSON Lines)
- **2026-04-25** q-seed v1 (57 PASS) + q-fin B1 free (48 PASS, gpcw 本地财报跑通)
- **2026-04-26** q-fin B2/B3 paid (65 PASS + F44 真 LLM 长龄案例 ✓) + q-news B4 (31 PASS) + 2 个 bug 修
- **2026-04-27** 辅助 5 命令 + q-batch.sh + dashboard 华尔街配色 ship → V1.0 投产

---

## 凭据安全

`~/sentry/quant/.env` (chmod 600, 永远 git 忽略) 含:
- `ANTHROPIC_API_KEY` (sk-ant-api03-...)
- `WECHAT_WEBHOOK_URL` (https://qyapi.weixin.qq.com/...)

⚠️ 部署前用户应到对应平台 revoke 旧 key 重新生成 (chat 暴露过).

---

## License

私人项目, 不公开.

## Maintainer

wooyoo@gmail.com
