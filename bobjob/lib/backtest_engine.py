"""
回测引擎 - 按天回测框架
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from core.tdx_loader import read_day

from .selector import StockSelector
from .exit_manager import ExitManager
from .data_cache import DataCache


class BacktestEngine:
    """回测引擎"""
    
    def __init__(self, config: dict, use_cache: bool = True):
        self.config = config
        self.bt_config = config['backtest']
        
        # 初始化数据缓存
        self.data_cache = DataCache() if use_cache else None
        
        self.selector = StockSelector(config, self.data_cache)
        self.exit_manager = ExitManager(config, self.data_cache)
        
        # 回测状态
        self.capital = self.bt_config['initial_capital']
        self.initial_capital = self.capital
        self.positions = []  # 当前持仓
        self.trades = []     # 交易记录
        self.daily_stats = []  # 每日统计
        
        # 参数
        self.max_positions = self.bt_config['max_positions']
        self.commission = self.bt_config['commission_rate']
        self.slippage = self.bt_config['slippage_rate']
        
    def run(self, start_date: str, end_date: str, symbols: List[str]) -> Dict:
        """
        运行回测
        
        Args:
            start_date: 开始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD
            symbols: 股票池
            
        Returns:
            回测结果统计
        """
        print(f"\n开始回测: {start_date} 至 {end_date}")
        print(f"股票池: {len(symbols)} 只")
        print(f"初始资金: {self.initial_capital:,.0f}")
        print(f"最大持仓: {self.max_positions} 只")
        print("-" * 60)
        
        # 预加载数据到缓存
        if self.data_cache:
            self.data_cache.preload(symbols, verbose=True)
            print()
        
        # 获取交易日历
        trade_dates = self._get_trade_dates(start_date, end_date)
        print(f"交易日数: {len(trade_dates)} 天\n")
        
        # 按天回测
        for i, date in enumerate(trade_dates):
            if i % 20 == 0:
                print(f"进度: {i}/{len(trade_dates)} ({i/len(trade_dates)*100:.1f}%) - {date}")
            
            self._process_day(date, symbols)
        
        # 生成报告
        return self._generate_report()
    
    def _get_trade_dates(self, start: str, end: str) -> List[str]:
        """获取交易日历（简化版：使用任意股票的交易日）"""
        # 使用000001的交易日作为基准（总是从磁盘读取，不依赖缓存）
        df = read_day('000001')
        if df is None:
            raise ValueError("无法加载交易日历")
        
        df['date_str'] = df['date'].dt.strftime('%Y-%m-%d')
        mask = (df['date_str'] >= start) & (df['date_str'] <= end)
        return df[mask]['date_str'].tolist()
    
    def _process_day(self, date: str, symbols: List[str]):
        """处理单个交易日"""
        # 1. 检查现有持仓是否需要卖出
        self._check_exits(date)
        
        # 2. 如果有空位，选股买入
        if len(self.positions) < self.max_positions:
            self._select_and_buy(date, symbols)
        
        # 3. 记录当日统计
        self._record_daily_stats(date)
    
    def _check_exits(self, date: str):
        """检查并执行卖出"""
        remaining_positions = []
        
        for pos in self.positions:
            hold_days = self._calc_hold_days(pos['entry_date'], date)
            
            # 更新prev_close（用于次日判断低开）
            if 'prev_close' not in pos:
                df = self.data_cache.get(pos['code']) if self.data_cache else read_day(pos['code'])
                if df is not None:
                    df['date_str'] = df['date'].dt.strftime('%Y-%m-%d')
                    prev_idx = df[df['date_str'] == pos['entry_date']].index
                    if len(prev_idx) > 0:
                        pos['prev_close'] = float(df.iloc[prev_idx[0]]['close'])
            
            exit_signal = self.exit_manager.check_exit(pos, date, hold_days)
            
            if exit_signal['should_exit']:
                self._execute_sell(pos, date, exit_signal)
            else:
                remaining_positions.append(pos)
        
        self.positions = remaining_positions
    
    def _select_and_buy(self, date: str, symbols: List[str]):
        """选股并买入"""
        available_slots = self.max_positions - len(self.positions)
        if available_slots <= 0:
            return
        
        # 选股
        candidates = self.selector.select_stocks(date, symbols)
        
        # 买入前N只
        for candidate in candidates[:available_slots]:
            # 检查是否已持有
            if any(p['code'] == candidate['code'] for p in self.positions):
                continue
            
            # 计算买入数量
            buy_price = candidate['close'] * (1 + self.slippage)
            position_size = self.capital / self.max_positions
            shares = int(position_size / buy_price / 100) * 100  # 100股整数倍
            
            if shares < 100:
                continue
            
            cost = shares * buy_price * (1 + self.commission)
            
            if cost > self.capital:
                continue
            
            # 执行买入
            self.capital -= cost
            
            position = {
                'code': candidate['code'],
                'entry_date': date,
                'entry_price': buy_price,
                'shares': shares,
                'cost': cost,
                'signals': candidate['signals']
            }
            
            self.positions.append(position)
            
            self.trades.append({
                'date': date,
                'code': candidate['code'],
                'action': 'BUY',
                'price': buy_price,
                'shares': shares,
                'amount': cost,
                'reason': 'selected'
            })
    
    def _execute_sell(self, position: Dict, date: str, exit_signal: Dict):
        """执行卖出"""
        sell_price = exit_signal['exit_price'] * (1 - self.slippage)
        proceeds = position['shares'] * sell_price * (1 - self.commission)
        
        self.capital += proceeds
        
        profit = proceeds - position['cost']
        profit_pct = profit / position['cost']
        
        self.trades.append({
            'date': date,
            'code': position['code'],
            'action': 'SELL',
            'price': sell_price,
            'shares': position['shares'],
            'amount': proceeds,
            'reason': exit_signal['exit_reason'],
            'profit': profit,
            'profit_pct': profit_pct,
            'hold_days': self._calc_hold_days(position['entry_date'], date)
        })
    
    def _calc_hold_days(self, entry_date: str, exit_date: str) -> int:
        """计算持有天数"""
        d1 = datetime.strptime(entry_date, '%Y-%m-%d')
        d2 = datetime.strptime(exit_date, '%Y-%m-%d')
        return (d2 - d1).days
    
    def _record_daily_stats(self, date: str):
        """记录每日统计"""
        # 计算持仓市值
        position_value = 0
        for pos in self.positions:
            df = self.data_cache.get(pos['code']) if self.data_cache else read_day(pos['code'])
            if df is not None:
                df['date_str'] = df['date'].dt.strftime('%Y-%m-%d')
                if date in df['date_str'].values:
                    idx = df[df['date_str'] == date].index[0]
                    current_price = df.iloc[idx]['close']
                    position_value += pos['shares'] * current_price
        
        total_value = self.capital + position_value
        
        self.daily_stats.append({
            'date': date,
            'capital': self.capital,
            'position_value': position_value,
            'total_value': total_value,
            'positions_count': len(self.positions),
            'return_pct': (total_value / self.initial_capital - 1) * 100
        })
    
    def _generate_report(self) -> Dict:
        """生成回测报告"""
        # 统计交易
        sell_trades = [t for t in self.trades if t['action'] == 'SELL']
        
        if not sell_trades:
            return {
                'total_trades': 0,
                'win_rate': 0,
                'avg_profit_pct': 0,
                'total_return_pct': 0,
                'message': '无完成交易'
            }
        
        wins = [t for t in sell_trades if t['profit'] > 0]
        losses = [t for t in sell_trades if t['profit'] <= 0]
        
        total_profit = sum(t['profit'] for t in sell_trades)
        avg_profit_pct = np.mean([t['profit_pct'] for t in sell_trades]) * 100
        
        win_rate = len(wins) / len(sell_trades) * 100
        
        final_value = self.daily_stats[-1]['total_value'] if self.daily_stats else self.initial_capital
        total_return_pct = (final_value / self.initial_capital - 1) * 100
        
        # 计算最大回撤
        max_drawdown = self._calc_max_drawdown()
        
        report = {
            'total_trades': len(sell_trades),
            'win_trades': len(wins),
            'loss_trades': len(losses),
            'win_rate': win_rate,
            'avg_profit_pct': avg_profit_pct,
            'total_profit': total_profit,
            'total_return_pct': total_return_pct,
            'max_drawdown_pct': max_drawdown,
            'initial_capital': self.initial_capital,
            'final_capital': final_value,
            'avg_hold_days': np.mean([t['hold_days'] for t in sell_trades]),
            'trades': sell_trades,
            'daily_stats': self.daily_stats
        }
        
        return report
    
    def _calc_max_drawdown(self) -> float:
        """计算最大回撤"""
        if not self.daily_stats:
            return 0.0
        
        values = [s['total_value'] for s in self.daily_stats]
        peak = values[0]
        max_dd = 0.0
        
        for v in values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd
        
        return max_dd * 100
