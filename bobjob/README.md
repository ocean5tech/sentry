# 尾盘买早盘卖策略 - 回测系统

## 策略说明

**核心逻辑**：
- **选股时间**：下午2:30-2:50
- **选股条件**：
  1. 缩量阴线（成交量 < 前5日均量 × 0.8）
  2. 回调到10日线（距离10日线 -3% ~ +2%）
  3. 前期活跃（近20天至少4天成交量 > 均量 × 1.3）
  4. 价格区间：5-100元

- **卖出规则**：
  1. 次日低开破10日线：立即卖出（开盘价）
  2. 次日平开/高开：10点左右卖出（模拟冲高）
  3. 最多持有2天

## 快速开始

### 1. 快速测试（15只股票，3个月）
```bash
cd /home/wyatt/bob/sentry/quant/bobjob
python3 test_quick.py
```

**已验证结果**：
- 测试期间：2026-01-01 至 2026-04-28
- 交易次数：1笔
- 胜率：100%
- 平均收益率：0.41%
- 总收益率：0.08%

### 2. 中等规模回测（100只股票，6个月）
```bash
python3 run_medium_backtest.py
```
**注意**：需要约3-5分钟运行时间

### 3. 完整回测（500只股票，1年+）
```bash
# 后台运行（推荐）
nohup python3 run_full_backtest.py > logs/full_backtest.log 2>&1 &

# 查看进度
tail -f logs/full_backtest.log
```

## 配置文件

- `config.yaml` - 默认配置
- `config_test.yaml` - 测试配置（小样本）
- `config_full.yaml` - 完整回测配置

## 输出文件

回测完成后，在 `logs/` 目录下生成：
- `backtest_YYYYMMDD_HHMM.csv` - 交易明细
- `backtest_YYYYMMDD_HHMM.md` - 详细报告
- `daily_stats_YYYYMMDD_HHMM.csv` - 每日统计

## 性能优化

系统已实现数据预加载和缓存机制：
- 启动时一次性加载所有股票数据到内存
- 回测过程中直接从内存读取，避免重复IO
- 100只股票约需10-15秒预加载时间

## 策略调优

修改 `config.yaml` 中的参数：

```yaml
selection_criteria:
  volume_shrink_ratio: 0.8    # 缩量比例（越小越严格）
  ma10_distance_min: -0.03    # 10日线下限
  ma10_distance_max: 0.02     # 10日线上限
  active_min_days: 4          # 活跃天数要求
```

## 目录结构

```
bobjob/
├── config.yaml              # 默认配置
├── config_test.yaml         # 测试配置
├── config_full.yaml         # 完整配置
├── test_quick.py            # 快速测试
├── run_medium_backtest.py   # 中等规模回测
├── lib/                     # 核心库
│   ├── selector.py          # 选股逻辑
│   ├── exit_manager.py      # 卖出逻辑
│   ├── backtest_engine.py   # 回测引擎
│   ├── report_generator.py  # 报告生成
│   └── data_cache.py        # 数据缓存
├── logs/                    # 输出日志
└── tests/                   # 单元测试
```

## 注意事项

1. **数据依赖**：需要TDX日线数据在 `/home/wyatt/bob/sentry/quant/data/tdx/`
2. **内存占用**：500只股票约需500MB内存
3. **运行时间**：
   - 15只股票：< 10秒
   - 100只股票：3-5分钟
   - 500只股票：15-30分钟

## 下一步优化方向

1. 添加更多卖出信号（如破分时均线）
2. 引入止损/止盈机制
3. 参数自动优化（网格搜索）
4. 多策略组合回测
