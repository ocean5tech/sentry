"""
卖出逻辑模块 - 次日早盘卖出规则
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
from typing import Dict, Optional
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from core.tdx_loader import read_day
from core.mytt import MA


class ExitManager:
    """卖出管理器"""
    
    def __init__(self, config: dict, data_cache=None):
        self.config = config
        self.rules = config['strategy']['exit_rules']
        self.data_cache = data_cache
        
    def check_exit(self, position: Dict, current_date: str, hold_days: int) -> Dict:
        """
        检查是否应该卖出
        
        Args:
            position: 持仓信息 {code, entry_date, entry_price, ...}
            current_date: 当前交易日
            hold_days: 已持有天数
            
        Returns:
            {
                'should_exit': bool,
                'exit_price': float,
                'exit_reason': str,
                'exit_time': str  # 'open' | 'morning' | 'close'
            }
        """
        symbol = position['code']
        entry_price = position['entry_price']
        
        # 加载数据
        df = self.data_cache.get(symbol) if self.data_cache else read_day(symbol)
        if df is None:
            return {'should_exit': True, 'exit_price': entry_price, 
                   'exit_reason': '数据缺失', 'exit_time': 'open'}
        
        df['date_str'] = df['date'].dt.strftime('%Y-%m-%d')
        if current_date not in df['date_str'].values:
            return {'should_exit': False, 'exit_price': 0, 
                   'exit_reason': '非交易日', 'exit_time': 'none'}
        
        idx = df[df['date_str'] == current_date].index[0]
        current = df.iloc[idx]
        
        # 计算10日线
        df_slice = df.iloc[:idx+1]
        if len(df_slice) < 10:
            ma10 = df_slice['close'].mean()
        else:
            ma10 = MA(df_slice['close'].values, 10)[-1]
        
        # 规则1: 最大持有期
        if hold_days >= self.rules['max_hold_days']:
            return {
                'should_exit': True,
                'exit_price': float(current['close']),
                'exit_reason': f'达到最大持有期{hold_days}天',
                'exit_time': 'close'
            }
        
        # 规则2: 低开破线立即卖（开盘价）
        open_price = current['open']
        prev_close = position.get('prev_close', entry_price)
        
        # 低开判断
        is_low_open = (open_price / prev_close - 1) < self.rules['low_open_threshold']
        
        # 破10日线判断
        break_ma10 = open_price < ma10 * 0.99
        
        if is_low_open and break_ma10 and self.rules['break_ma10_sell']:
            return {
                'should_exit': True,
                'exit_price': float(open_price),
                'exit_reason': f'低开破线(开{open_price:.2f}<MA10{ma10:.2f})',
                'exit_time': 'open'
            }
        
        # 规则3: 平开/高开，10点左右卖出
        # 这里简化处理：如果不是低开破线，则在10点左右（用当日最高价模拟冲高）
        if hold_days == 1:  # 次日
            # 模拟10点卖出：取开盘价和最高价的中间价
            # 如果冲高（high > open * 1.02），在high * 0.98卖出
            # 否则在open * 1.01卖出（小幅冲高）
            high_price = current['high']
            
            if high_price > open_price * 1.02:
                # 冲高卖出
                exit_price = high_price * 0.98
                reason = f'冲高卖出(高{high_price:.2f})'
            else:
                # 平开小幅卖出
                exit_price = open_price * 1.005
                reason = '平开10点卖出'
            
            return {
                'should_exit': True,
                'exit_price': float(min(exit_price, high_price)),
                'exit_reason': reason,
                'exit_time': 'morning'
            }
        
        # 规则4: 第二天收盘强制卖出
        if hold_days >= 2:
            return {
                'should_exit': True,
                'exit_price': float(current['close']),
                'exit_reason': '第二天收盘',
                'exit_time': 'close'
            }
        
        # 不卖出
        return {
            'should_exit': False,
            'exit_price': 0,
            'exit_reason': '持有',
            'exit_time': 'none'
        }


def test_exit_manager():
    """测试卖出管理器"""
    import yaml
    
    config_path = Path(__file__).parent.parent / 'config.yaml'
    with open(config_path) as f:
        config = yaml.safe_load(f)
    
    manager = ExitManager(config)
    
    # 模拟持仓
    position = {
        'code': '000858',
        'entry_date': '2026-04-25',
        'entry_price': 10.5,
        'prev_close': 10.5
    }
    
    # 测试次日卖出
    result = manager.check_exit(position, '2026-04-28', hold_days=1)
    print(f"次日卖出检查: {result}")


if __name__ == '__main__':
    test_exit_manager()
