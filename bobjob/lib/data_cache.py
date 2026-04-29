"""
数据缓存模块 - 预加载股票数据到内存
"""
import sys
from pathlib import Path
import pandas as pd
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from core.tdx_loader import read_day


class DataCache:
    """数据缓存器 - 预加载并缓存股票数据"""
    
    def __init__(self):
        self.cache: Dict[str, pd.DataFrame] = {}
        self.loaded_symbols: List[str] = []
    
    def preload(self, symbols: List[str], verbose: bool = True):
        """
        预加载股票数据到内存
        
        Args:
            symbols: 股票代码列表
            verbose: 是否显示进度
        """
        if verbose:
            print(f"正在预加载 {len(symbols)} 只股票数据...")
        
        success_count = 0
        for i, symbol in enumerate(symbols):
            if verbose and (i + 1) % 50 == 0:
                print(f"  进度: {i+1}/{len(symbols)} ({(i+1)/len(symbols)*100:.1f}%)")
            
            df = read_day(symbol)
            if df is not None and len(df) > 0:
                self.cache[symbol] = df
                self.loaded_symbols.append(symbol)
                success_count += 1
        
        if verbose:
            print(f"预加载完成: {success_count}/{len(symbols)} 只股票")
    
    def get(self, symbol: str) -> Optional[pd.DataFrame]:
        """
        获取股票数据（从缓存）
        
        Args:
            symbol: 股票代码
            
        Returns:
            DataFrame或None
        """
        return self.cache.get(symbol)
    
    def clear(self):
        """清空缓存"""
        self.cache.clear()
        self.loaded_symbols.clear()
    
    def get_loaded_symbols(self) -> List[str]:
        """获取已加载的股票列表"""
        return self.loaded_symbols.copy()
