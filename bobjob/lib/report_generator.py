"""
报告生成器 - 生成CSV和Markdown格式报告
"""
import pandas as pd
from pathlib import Path
from typing import List, Dict


class ReportGenerator:
    """报告生成器"""
    
    def __init__(self, config: dict):
        self.config = config
    
    def save_trades_csv(self, trades: List[Dict], output_path: Path):
        """保存交易明细到CSV"""
        if not trades:
            return
        
        df = pd.DataFrame(trades)
        df.to_csv(output_path, index=False, encoding='utf-8-sig')
    
    def save_daily_stats_csv(self, daily_stats: List[Dict], output_path: Path):
        """保存每日统计到CSV"""
        if not daily_stats:
            return
        
        df = pd.DataFrame(daily_stats)
        df.to_csv(output_path, index=False, encoding='utf-8-sig')
    
    def save_markdown_report(self, results: Dict, output_path: Path):
        """生成Markdown格式报告"""
        lines = []
        
        lines.append("# 尾盘买早盘卖策略 - 回测报告")
        lines.append("")
        lines.append(f"**生成时间**: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")
        
        # 策略概述
        lines.append("## 策略概述")
        lines.append("")
        lines.append("- **选股时间**: 下午2:30-2:50")
        lines.append("- **选股条件**: 缩量阴线 + 回调10日线 + 前期活跃")
        lines.append("- **卖出规则**: 次日低开破线立即卖，平开/高开10点左右卖，最多持有2天")
        lines.append("")
        
        # 回测参数
        bt_config = self.config['backtest']
        lines.append("## 回测参数")
        lines.append("")
        lines.append(f"- **回测区间**: {bt_config['start_date']} 至 {bt_config['end_date']}")
        lines.append(f"- **初始资金**: {bt_config['initial_capital']:,.0f} 元")
        lines.append(f"- **最大持仓**: {bt_config['max_positions']} 只")
        lines.append(f"- **佣金率**: {bt_config['commission_rate']*100:.2f}%")
        lines.append(f"- **滑点率**: {bt_config['slippage_rate']*100:.2f}%")
        lines.append("")
        
        # 核心指标
        lines.append("## 核心指标")
        lines.append("")
        lines.append("| 指标 | 数值 |")
        lines.append("|------|------|")
        lines.append(f"| 总交易次数 | {results['total_trades']} |")
        lines.append(f"| 盈利次数 | {results['win_trades']} |")
        lines.append(f"| 亏损次数 | {results['loss_trades']} |")
        lines.append(f"| **胜率** | **{results['win_rate']:.2f}%** |")
        lines.append(f"| **平均收益率** | **{results['avg_profit_pct']:.2f}%** |")
        lines.append(f"| 平均持有天数 | {results['avg_hold_days']:.1f} 天 |")
        lines.append(f"| 初始资金 | {results['initial_capital']:,.0f} 元 |")
        lines.append(f"| 最终资金 | {results['final_capital']:,.0f} 元 |")
        lines.append(f"| **总收益率** | **{results['total_return_pct']:.2f}%** |")
        lines.append(f"| **最大回撤** | **{results['max_drawdown_pct']:.2f}%** |")
        lines.append("")
        
        # 交易明细（前20笔）
        lines.append("## 交易明细（前20笔）")
        lines.append("")
        lines.append("| 日期 | 代码 | 操作 | 价格 | 数量 | 盈亏 | 收益率 | 持有天数 | 原因 |")
        lines.append("|------|------|------|------|------|------|--------|----------|------|")
        
        for trade in results['trades'][:20]:
            profit = trade.get('profit', 0)
            profit_pct = trade.get('profit_pct', 0) * 100
            hold_days = trade.get('hold_days', 0)
            
            lines.append(
                f"| {trade['date']} | {trade['code']} | {trade['action']} | "
                f"{trade['price']:.2f} | {trade['shares']} | "
                f"{profit:,.0f} | {profit_pct:.2f}% | {hold_days} | "
                f"{trade['reason']} |"
            )
        
        lines.append("")
        
        if len(results['trades']) > 20:
            lines.append(f"*（共{len(results['trades'])}笔交易，仅显示前20笔）*")
            lines.append("")
        
        # 收益分布
        lines.append("## 收益分布")
        lines.append("")
        
        profit_pcts = [t.get('profit_pct', 0) * 100 for t in results['trades']]
        if profit_pcts:
            lines.append(f"- 最大单笔收益: {max(profit_pcts):.2f}%")
            lines.append(f"- 最大单笔亏损: {min(profit_pcts):.2f}%")
            lines.append(f"- 收益中位数: {pd.Series(profit_pcts).median():.2f}%")
            lines.append("")
        
        # 写入文件
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
