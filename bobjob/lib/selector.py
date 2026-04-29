"""
选股逻辑模块 - 尾盘买早盘卖策略
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
from typing import List, Dict, Optional
from datetime import datetime, timedelta

# 添加父项目路径以复用core模块
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from core.tdx_loader import read_day, list_tdx_symbols
from core.mytt import MA


class StockSelector:
    """股票选择器"""
    
    def __init__(self, config: dict, data_cache=None):
        self.config = config
        self.criteria = config['strategy']['selection_criteria']
        self.tdx_dir = Path(config['data']['tdx_dir'])
        self.data_cache = data_cache
        
    def select_stocks(self, trade_date: str, all_symbols: List[str]) -> List[Dict]:
        """
        在指定交易日选股
        
        Args:
            trade_date: 交易日期 YYYY-MM-DD
            all_symbols: 所有股票代码列表
            
        Returns:
            符合条件的股票列表，每个元素包含 {code, name, score, signals}
        """
        candidates = []
        
        for symbol in all_symbols:
            try:
                # 加载日线数据
                df = self.data_cache.get(symbol) if self.data_cache else read_day(symbol)
                if df is None or len(df) < 60:
                    continue
                
                # 找到交易日在数据中的位置
                df['date_str'] = df['date'].dt.strftime('%Y-%m-%d')
                if trade_date not in df['date_str'].values:
                    continue
                
                idx = df[df['date_str'] == trade_date].index[0]
                if idx < 20:  # 需要至少20天历史数据
                    continue
                
                # 截取到当前交易日的数据
                df_current = df.iloc[:idx+1].copy()
                
                # 检查选股条件
                signals = self._check_criteria(df_current, symbol)
                
                if signals['pass']:
                    candidates.append({
                        'code': symbol,
                        'date': trade_date,
                        'close': float(df_current.iloc[-1]['close']),
                        'volume': float(df_current.iloc[-1]['volume']),
                        'signals': signals
                    })
                    
            except Exception as e:
                # 静默跳过错误股票
                continue
        
        # 按综合得分排序
        candidates.sort(key=lambda x: x['signals']['score'], reverse=True)
        return candidates
    
    def _check_criteria(self, df: pd.DataFrame, symbol: str) -> Dict:
        """
        检查选股条件
        
        Returns:
            {
                'pass': bool,
                'score': float,
                'is_shrink_volume': bool,
                'is_red_candle': bool,
                'ma10_distance': float,
                'is_active': bool,
                'reasons': List[str]
            }
        """
        signals = {
            'pass': False,
            'score': 0.0,
            'reasons': []
        }
        
        current = df.iloc[-1]
        
        # 1. 检查是否阴线
        is_red = current['close'] < current['open']
        signals['is_red_candle'] = is_red
        if not is_red:
            signals['reasons'].append('非阴线')
            return signals
        
        # 2. 检查缩量
        vol_ma5 = df['volume'].iloc[-6:-1].mean()
        vol_ratio = current['volume'] / vol_ma5 if vol_ma5 > 0 else 0
        signals['volume_ratio'] = float(vol_ratio)
        
        is_shrink = vol_ratio < self.criteria['volume_shrink_ratio']
        signals['is_shrink_volume'] = is_shrink
        if not is_shrink:
            signals['reasons'].append(f'未缩量(量比{vol_ratio:.2f})')
            return signals
        
        # 3. 检查10日线距离
        ma10 = MA(df['close'].values, 10)
        ma10_current = ma10[-1]
        distance = (current['close'] - ma10_current) / ma10_current
        signals['ma10_distance'] = float(distance)
        
        in_range = (self.criteria['ma10_distance_min'] <= distance <= 
                   self.criteria['ma10_distance_max'])
        signals['near_ma10'] = in_range
        if not in_range:
            signals['reasons'].append(f'偏离10日线({distance*100:.1f}%)')
            return signals
        
        # 4. 检查前期活跃度
        lookback = self.criteria['active_days']
        if len(df) < lookback + 5:
            signals['reasons'].append('历史数据不足')
            return signals
        
        recent_df = df.iloc[-lookback-5:-1]
        vol_ma = recent_df['volume'].mean()
        active_days = (recent_df['volume'] > vol_ma * self.criteria['active_volume_ratio']).sum()
        signals['active_days'] = int(active_days)
        
        is_active = active_days >= self.criteria['active_min_days']
        signals['is_active'] = is_active
        if not is_active:
            signals['reasons'].append(f'不够活跃(仅{active_days}天)')
            return signals
        
        # 5. 检查价格区间
        price = current['close']
        in_price_range = (self.criteria['price_min'] <= price <= 
                         self.criteria['price_max'])
        if not in_price_range:
            signals['reasons'].append(f'价格超范围({price:.2f})')
            return signals
        
        # 6. 检查ST（简单检查，实际需要股票名称）
        # 这里暂时跳过，需要stock_names.csv
        
        # 所有条件通过
        signals['pass'] = True
        signals['reasons'].append('全部通过')
        
        # 计算综合得分
        score = 0.0
        score += (1.0 - vol_ratio) * 30  # 缩量越多越好，最高30分
        score += (1.0 - abs(distance) / 0.02) * 30  # 越接近10日线越好，最高30分
        score += (active_days / self.criteria['active_min_days']) * 20  # 活跃度，最高20分
        score += 20  # 基础分
        
        signals['score'] = min(100.0, score)
        
        return signals


def test_selector():
    """测试选股器"""
    import yaml
    
    config_path = Path(__file__).parent.parent / 'config.yaml'
    with open(config_path) as f:
        config = yaml.safe_load(f)
    
    selector = StockSelector(config)
    
    # 测试2026-04-25选股
    test_symbols = ['000001', '000002', '600000', '600519', '000858']
    candidates = selector.select_stocks('2026-04-25', test_symbols)
    
    print(f"找到 {len(candidates)} 只候选股票:")
    for c in candidates[:10]:
        print(f"  {c['code']}: 得分{c['signals']['score']:.1f}, "
              f"量比{c['signals'].get('volume_ratio', 0):.2f}, "
              f"距10日线{c['signals'].get('ma10_distance', 0)*100:.1f}%")


if __name__ == '__main__':
    test_selector()
