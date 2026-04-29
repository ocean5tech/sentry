#!/usr/bin/env python3
"""
快速测试脚本 - 小样本验证策略逻辑
"""
import sys
from pathlib import Path
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.backtest_engine import BacktestEngine


def main():
    """快速测试"""
    # 加载测试配置
    config_path = Path(__file__).parent / 'config_test.yaml'
    with open(config_path) as f:
        config = yaml.safe_load(f)
    
    print("=" * 60)
    print("快速测试 - 尾盘买早盘卖策略")
    print("=" * 60)
    
    # 手动指定测试股票池（选择活跃股票）
    test_symbols = [
        '600519',  # 贵州茅台
        '000858',  # 五粮液
        '601318',  # 中国平安
        '600036',  # 招商银行
        '000001',  # 平安银行
        '600000',  # 浦发银行
        '601166',  # 兴业银行
        '000002',  # 万科A
        '600030',  # 中信证券
        '601888',  # 中国中免
        '300750',  # 宁德时代
        '002594',  # 比亚迪
        '688981',  # 中芯国际
        '603259',  # 药明康德
        '300059',  # 东方财富
    ]
    
    print(f"\n测试股票池: {len(test_symbols)} 只")
    print(f"回测区间: {config['backtest']['start_date']} 至 {config['backtest']['end_date']}")
    print(f"初始资金: {config['backtest']['initial_capital']:,.0f}\n")
    
    # 创建回测引擎
    engine = BacktestEngine(config)
    
    # 运行回测
    results = engine.run(
        config['backtest']['start_date'],
        config['backtest']['end_date'],
        test_symbols
    )
    
    # 显示结果
    print("\n" + "=" * 60)
    print("测试结果")
    print("=" * 60)
    
    print(f"\n总交易次数: {results['total_trades']}")
    
    if results['total_trades'] > 0:
        print(f"盈利次数: {results['win_trades']}")
        print(f"亏损次数: {results['loss_trades']}")
        print(f"胜率: {results['win_rate']:.2f}%")
        print(f"平均收益率: {results['avg_profit_pct']:.2f}%")
        print(f"平均持有天数: {results['avg_hold_days']:.1f} 天")
        print(f"\n初始资金: {results['initial_capital']:,.0f}")
        print(f"最终资金: {results['final_capital']:,.0f}")
        print(f"总收益率: {results['total_return_pct']:.2f}%")
        print(f"最大回撤: {results['max_drawdown_pct']:.2f}%")
        
        # 显示部分交易明细
        print(f"\n最近10笔交易:")
        for i, trade in enumerate(results['trades'][-10:], 1):
            if trade['action'] == 'SELL':
                print(f"  {i}. {trade['date']} {trade['code']}: "
                      f"{trade['profit_pct']*100:+.2f}% "
                      f"({trade['hold_days']}天) - {trade['reason']}")
    else:
        print(f"\n{results.get('message', '无交易数据')}")
        print("\n可能原因:")
        print("  1. 选股条件过于严格，没有符合的股票")
        print("  2. 测试期间市场环境不符合策略要求")
        print("  3. 数据质量问题")
    
    print("\n" + "=" * 60)


if __name__ == '__main__':
    main()
