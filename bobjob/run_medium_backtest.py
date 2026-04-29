#!/usr/bin/env python3
"""
中等规模回测 - 100只活跃股票，6个月数据
优化版本：添加数据缓存，提升性能
"""
import sys
from pathlib import Path
import yaml
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.backtest_engine import BacktestEngine
from lib.report_generator import ReportGenerator
from core.tdx_loader import read_day


def select_top_stocks(tdx_dir: Path, count: int = 100) -> list:
    """选择最活跃的N只股票"""
    print(f"正在筛选TOP {count}活跃股票...")
    
    stocks = []
    for market in ['sh', 'sz']:
        lday_dir = tdx_dir / market / 'lday'
        if not lday_dir.exists():
            continue
        
        for day_file in lday_dir.glob('*.day'):
            filename = day_file.stem
            if len(filename) >= 8:
                symbol = filename[2:]
                if symbol.startswith(('60', '00', '30', '68')):
                    df = read_day(symbol)
                    if df is not None and len(df) >= 120:
                        vol = df['volume'].iloc[-60:].mean()
                        amt = df['amount'].iloc[-60:].mean()
                        stocks.append((symbol, vol * amt))
    
    stocks.sort(key=lambda x: x[1], reverse=True)
    selected = [s[0] for s in stocks[:count]]
    print(f"已选择 {len(selected)} 只股票")
    return selected


def main():
    config_path = Path(__file__).parent / 'config_test.yaml'
    with open(config_path) as f:
        config = yaml.safe_load(f)
    
    # 修改回测参数
    config['backtest']['start_date'] = '2025-10-01'  # 6个月
    config['backtest']['end_date'] = '2026-04-28'
    
    print("=" * 60)
    print("中等规模回测 - 尾盘买早盘卖策略")
    print("=" * 60)
    
    tdx_dir = Path(config['data']['tdx_dir'])
    symbols = select_top_stocks(tdx_dir, 100)
    
    print(f"\n回测参数:")
    print(f"  区间: {config['backtest']['start_date']} 至 {config['backtest']['end_date']}")
    print(f"  股票池: {len(symbols)} 只")
    print(f"  初始资金: {config['backtest']['initial_capital']:,.0f}\n")
    
    engine = BacktestEngine(config)
    
    start_time = datetime.now()
    results = engine.run(
        config['backtest']['start_date'],
        config['backtest']['end_date'],
        symbols
    )
    elapsed = (datetime.now() - start_time).total_seconds()
    
    print("\n" + "=" * 60)
    print("回测结果")
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
        
        output_dir = Path(config['backtest']['output_dir'])
        output_dir.mkdir(exist_ok=True)
        
        generator = ReportGenerator(config)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M')
        
        csv_path = output_dir / f'backtest_medium_{timestamp}.csv'
        generator.save_trades_csv(results['trades'], csv_path)
        
        md_path = output_dir / f'backtest_medium_{timestamp}.md'
        generator.save_markdown_report(results, md_path)
        
        stats_path = output_dir / f'daily_stats_medium_{timestamp}.csv'
        generator.save_daily_stats_csv(results['daily_stats'], stats_path)
        
        print(f"\n报告已保存:")
        print(f"  {csv_path.name}")
        print(f"  {md_path.name}")
        print(f"  {stats_path.name}")
    else:
        print(f"\n{results.get('message', '无交易数据')}")
    
    print(f"\n回测耗时: {elapsed:.1f} 秒")
    print("=" * 60)


if __name__ == '__main__':
    main()
