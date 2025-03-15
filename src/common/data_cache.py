import asyncio
from typing import Dict, Any, Optional, Callable
import logging

class DataCache:
    """线程安全的异步数据缓存"""
    def __init__(self, exchange_name: str = "Generic"):
        self._data: Dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self.exchange_name = exchange_name
        self.logger = logging.getLogger(f"{exchange_name}-Cache")
        self._custom_updaters: Dict[str, Callable] = {}
        
    def register_updater(self, channel: str, updater: Callable):
        """
        注册自定义更新处理器
        
        Args:
            channel: 频道名称
            updater: 更新处理函数，接收(channel, data)参数
        """
        self._custom_updaters[channel] = updater
    
    async def update(self, channel: str, data: dict):
        """
        更新缓存数据
        
        Args:
            channel: 频道名称
            data: 数据内容
        """
        async with self._lock:
            # 如果有自定义处理器，则使用它
            if channel in self._custom_updaters:
                await self._custom_updaters[channel](channel, data)
                return
                
            # 按频道分类存储
            if channel not in self._data:
                self._data[channel] = {}
            
            # 通用更新逻辑
            inst_id = data.get('instId')
            if inst_id:
                self._data[channel][inst_id] = data
                self.logger.debug(f"已更新 {channel}/{inst_id} 数据")

    async def get(self, channel: str, inst_id: str) -> dict:
        """
        获取指定数据
        
        Args:
            channel: 频道名称
            inst_id: 交易对ID
            
        Returns:
            dict: 缓存的数据
        """
        async with self._lock:
            return self._data.get(channel, {}).get(inst_id, {})
            
    async def get_all(self, channel: str = None) -> Dict[str, Any]:
        """
        获取所有数据
        
        Args:
            channel: 可选，指定频道
            
        Returns:
            dict: 缓存的数据
        """
        async with self._lock:
            if channel:
                return self._data.get(channel, {})
            return self._data
    
    def __len__(self) -> int:
        """获取缓存条目数"""
        count = 0
        for channel, items in self._data.items():
            count += len(items)
        return count


class OKExDataCache(DataCache):
    """OKEx特定的数据缓存实现"""
    def __init__(self):
        super().__init__("OKEx")
        # 注册OKEx特定的更新处理器
        self.register_updater("funding-rate", self._update_funding_rate)
        
    async def _update_funding_rate(self, channel: str, data: dict):
        """OKEx资金费率特殊处理"""
        if channel not in self._data:
            self._data[channel] = {}
            
        inst_id = data.get('instId')
        if inst_id:
            self._data[channel][inst_id] = data
            # 特殊处理资金费率时间
            self._data[channel][inst_id]['nextFundingTime'] = int(data['fundingTime'])
            
    async def get_mark_price(self, inst_id: str) -> float:
        """
        获取标记价格
        
        Args:
            inst_id: 交易对ID
            
        Returns:
            float: 标记价格
        """
        data = await self.get("mark-price", inst_id)
        return float(data.get('markPx', 0.0))
    
    async def get_funding_rate(self, inst_id: str) -> float:
        """
        获取资金费率
        
        Args:
            inst_id: 交易对ID
            
        Returns:
            float: 资金费率
        """
        data = await self.get("funding-rate", inst_id)
        return float(data.get('fundingRate', 0.0)) 