# q-backtest 用户手册

**v1.0.0** (2026-04-28) · walk-forward 回测 q-seed 候选 + 后续表现

围绕用户需求设计: **找主升浪起爆点的日线特征**, 不是测胜率.
模板独立 — hongjing 找类宏景, litong 找类利通, 6 个新模板各自. 不混合.

---

## 1. 部署

```bash
cd ~/sentry/quant/q-backtest
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt    # 仅 pyyaml
chmod +x ./q-backtest
```

依赖极小 (pyyaml). 主体逻辑用 stdlib + subprocess 调 q-seed.

## 2. 工作原理

```
For each as_of_date in [start, end]:
  1. 调用 q-seed --as-of-date <date> --template both --top N
     (限制 K 线数据 < as_of_date, 避免 look-ahead bias)
  2. q-seed 输出 N 个候选 (覆盖 6 个模板, 每模板均衡)
  3. 按 details.<template>.dist 分组, 每模板取 top X
  4. 对每个候选:
     - 入场: T+1 close (用户决定)
     - 退出: T+1+hold_days close
     - 一字板检测: T+1 OHLC 全相等 → skip
  5. 算 ret / max_drawdown / win / good_experience
按 template 分组统计 → 报告
```

## 3. 启动

```bash
# 默认: 2025-08-01 ~ 2026-04-22, 持仓 20 天, 胜利 +5%, 每模板 top 5, step 5
q-backtest

# 自定义窗口
q-backtest --start 2026-01-01 --end 2026-04-01 --step 7

# 调阈值
q-backtest --hold-days 20 --win-pct 0.10

# 仅某模板
q-backtest --templates hongjing,litong

# 单日 quick test (debug)
q-backtest --start 2026-03-09 --end 2026-03-09 --step 1 --top 3 --templates hongjing
```

完整参数: `q-backtest --help`.

## 4. 关键定义

| 概念 | 定义 |
|---|---|
| **as_of_date** | 模拟"今天". q-seed 只看该日期之前 K 线 (avoid look-ahead) |
| **入场价** | as_of_date 次日 (T+1) 收盘价 |
| **退出价** | T+1+hold_days 收盘价 (跳节假日, 用 TDX 上证指数日历) |
| **一字板放弃** | T+1 open == high == low == close (涨/跌停无法成交) |
| **胜利 (win)** | (exit - entry) / entry >= win_pct (默认 5%) |
| **持仓体验好** | win AND **持仓期间最低 low >= 入场价** (即过程中没跌破入场点) |
| **max_drawdown** | (min_low_during - entry) / entry — 持仓中最大浮亏 |

## 5. 输出 (JSON Lines + 文本报告)

### JSON Lines (`logs/q-backtest_*.jsonl`)

```jsonl
{
  "as_of_date": "2026-03-09",
  "template": "xiangnong",
  "code": "301658",
  "name": "...",
  "template_dist": 4.523,
  "score": ...,
  "qseed_rank_global": 7,
  "skipped_reason": null,
  "entry_date": "2026-03-10",
  "entry_price": 53.20,
  "exit_date": "2026-03-24",
  "exit_price": 83.20,
  "ret": 0.5640,
  "min_low_during": 51.50,
  "max_close_during": 85.00,
  "max_drawdown": -0.0319,
  "win": true,
  "good_experience": false  // 因 51.50 < 53.20 入场价
}
```

### 文本报告 (stdout)

```
=== q-backtest 报告 (2026-03-09 ~ 2026-03-09) ===
持仓 10d, 胜利 +5%, 模板 hongjing,litong,...

xiangnong:
  样本: 3 (一字板 skip 0)
  胜率 (ret >= 5%):                    1/3 = 33.3%
  持仓体验好:                           0/3 = 0.0%  ⭐
  平均收益: +10.92%
  平均最大回撤: -10.75%
  Top 3: 301658(+56.4%) 300442(-11.6%) 002657(-12.0%)

总: 样本 18 / 胜 2 (11.1%) / 持仓体验好 0 (0.0%)
```

## 6. 配置 `config.yaml`

```yaml
q_seed_command: "/home/wyatt/sentry/quant/q-seed/q-seed"

data:
  tdx_dir: "/home/wyatt/sentry/quant/data/tdx"

defaults:
  start_date: "2025-08-01"
  end_date: "2026-04-22"
  hold_days: 20                       # 主升浪 1 月默认
  win_pct: 0.05                       # +5% 算胜
  top_per_template: 5
  templates: ["hongjing", "litong", "xiangnong", "fujing", "yunnange", "lanqi"]
```

## 7. 性能

- 单 q-seed 调用 (全市场 6 模板, 1 个 as_of_date): **~25 min**
- 全季度回测 (90 天 step 7 = 13 日): **~5 小时**
- 瓶颈: q-seed 全市场扫描 5300 只 × 6 模板, scan_one_features 重复
- V1.5 优化方向: cache `find_launches` per stock, 估 5x 提速

实战建议:
- 周末跑 1 次大窗口 backtest
- 平时单日 smoke (`--start X --end X --step 1`) 验证个别 as_of_date

## 8. 实施细节

- DESIGN: `~/sentry/quant/docs/DESIGN_q-backtest.md` (本次 ship 略过, 内容在 USER_GUIDE)
- 核心模块:
  - `lib/trade_calendar.py` — TDX 上证指数派生交易日历 (无需 akshare)
  - `lib/forward_eval.py` — T+1 入场 / N 日退出 / 一字板检测 / max_drawdown
- q-seed 改动: 加 `--as-of-date <YYYY-MM-DD>` 限制 K 线 < 该日期 (avoid look-ahead bias)

## 9. 已知限制 (V1.0)

- **慢**: 单 q-seed 调用 25min, 大窗口必须 nohup 后台跑
- **持仓体验好阈值严格**: 持仓中任意时点 low < entry → 标 false. 真实 5d 内多数股波动会破入场, 所以"持仓体验好"率会很低. 这是预期行为
- **样本量小问题**: 单日 18 信号 (3 × 6 模板) 不能下结论, 至少跑 30+ 个 as_of_date 看趋势
- **xiangnong/fujing/yunnange/lanqi 各只 1 个锚点**: 模板表征薄, 找的"相似形态"主观性强

## 10. 典型工作流

```bash
# 周末跑全季度回测, 后台
nohup q-backtest --start 2025-08-01 --end 2026-04-22 --step 7 --hold-days 20 \
    > backtest_full.log 2>&1 &

# 看进度
tail -f backtest_full.log

# 跑完看结果
ls -lt logs/q-backtest_*.jsonl | head
.venv/bin/python -c "
import json
recs = [json.loads(l) for l in open('logs/q-backtest_LATEST.jsonl')]
# 按模板拆 + 排序看大牛股
from collections import defaultdict
by_tpl = defaultdict(list)
for r in recs:
    if r['ret'] is not None and r['ret'] > 0.20:    # 收益 > 20%
        by_tpl[r['template']].append(r)
for t, rs in by_tpl.items():
    rs.sort(key=lambda x: x['ret'], reverse=True)
    print(f'{t} 大牛股 (>20%):')
    for r in rs[:5]: print(f'  {r[\"as_of_date\"]} {r[\"code\"]} {r[\"name\"]} +{r[\"ret\"]*100:.0f}%')
"
```
