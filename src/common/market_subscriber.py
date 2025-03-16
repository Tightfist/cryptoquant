"""
市场数据订阅管理模块

提供了基础市场数据订阅管理器和交易所特定的实现。
支持订阅和管理各种市场数据，如标记价格、资金费率等。
"""

import logging
from typing import Dict, Any, List, Set, Optional
from abc import ABC, abstractmethod
import asyncio
import json
from threading import Thread

from src.common.data_cache import DataCache
from src.common.websocket.client import ExchangeWebSocketClient, OKExWebSocketClient


class BaseMarketSubscriber(ABC):
    """市场数据订阅管理器基类"""
    
    def __init__(self, cache: DataCache, config: Dict[str, Any], logger_name: str = "market_subscriber"):
        """
        初始化市场数据订阅器基类
        
        Args:
            cache: 数据缓存对象
            config: 配置信息
            logger_name: 日志记录器名称
        """
        self.cache = cache
        self.config = config
        self.logger = logging.getLogger(logger_name)
        
        # 策略配置
        self.strategy_config = config.get('strategy', {})
        
        # 获取默认订阅的交易对
        self.default_symbols = self.strategy_config.get('default_symbols', [])
        
        # 当前已订阅的交易对集合
        self.subscribed_symbols: Set[str] = set(self.default_symbols)
        
        # 频道列表，由子类实现
        self.channels = []
        
        # 初始化WebSocket客户端，由子类实现
        self.client = self._init_websocket_client()
        
        # 初始化必要的频道订阅
        self._init_channels()
    
    @abstractmethod
    def _init_websocket_client(self) -> ExchangeWebSocketClient:
        """初始化WebSocket客户端，由子类实现"""
        pass
    
    def _init_channels(self):
        """初始化频道订阅配置"""
        self.channels = []
        
        # 为每个交易对订阅必要的频道
        for symbol in self.subscribed_symbols:
            self._add_symbol_channels(symbol)
    
    @abstractmethod
    def _add_symbol_channels(self, symbol: str):
        """
        为指定交易对添加频道订阅，由子类实现
        
        Args:
            symbol: 交易对
        """
        pass
    
    async def subscribe_symbol(self, symbol: str):
        """
        订阅新的交易对数据
        
        Args:
            symbol: 交易对名称
            
        Returns:
            bool: 是否订阅成功
        """
        if symbol in self.subscribed_symbols:
            self.logger.debug(f"交易对 {symbol} 已订阅")
            return True
            
        self.logger.info(f"开始订阅交易对: {symbol}")
            
        # 添加新的频道订阅
        new_channels = self._get_symbol_channels(symbol)
            
        # 执行订阅
        if new_channels:
            try:
                await self.client.subscribe(new_channels)
                
                # 更新已订阅集合
                self.subscribed_symbols.add(symbol)
                self.channels.extend(new_channels)
                
                # 给一些时间让数据流入
                await asyncio.sleep(1)
                
                self.logger.info(f"成功订阅交易对: {symbol}")
                return True
            except Exception as e:
                self.logger.error(f"订阅交易对 {symbol} 失败: {e}")
                return False
        else:
            self.logger.warning(f"无法为交易对 {symbol} 获取频道配置")
            return False
    
    @abstractmethod
    def _get_symbol_channels(self, symbol: str) -> List[Dict[str, Any]]:
        """
        获取交易对的频道配置，由子类实现
        
        Args:
            symbol: 交易对
            
        Returns:
            List[Dict[str, Any]]: 频道配置列表
        """
        pass
    
    async def unsubscribe_symbol(self, symbol: str):
        """
        取消订阅交易对数据
        
        Args:
            symbol: 交易对名称
        """
        if symbol not in self.subscribed_symbols:
            return
            
        # 构建取消订阅的频道列表
        channels_to_unsub = self._get_symbol_channels(symbol)
            
        # 执行取消订阅
        if channels_to_unsub:
            await self.client.unsubscribe(channels_to_unsub)
            
            # 更新已订阅集合和频道列表
            self.subscribed_symbols.remove(symbol)
            # 从频道列表中移除该交易对的所有频道
            self.channels = [c for c in self.channels if not self._is_channel_for_symbol(c, symbol)]
            
            self.logger.info(f"已取消订阅交易对: {symbol}")
    
    @abstractmethod
    def _is_channel_for_symbol(self, channel: Dict[str, Any], symbol: str) -> bool:
        """
        判断频道是否属于指定交易对，由子类实现
        
        Args:
            channel: 频道配置
            symbol: 交易对
            
        Returns:
            bool: 是否属于该交易对
        """
        pass
    
    async def start(self):
        """启动市场数据订阅"""
        self.logger.info(f"开始启动市场数据订阅，初始交易对: {', '.join(self.subscribed_symbols)}")
        
        # 先连接WebSocket
        connected = await self.client.connect()
        
        if not connected:
            self.logger.error("WebSocket连接失败，无法订阅频道")
            return False
            
        self.logger.info("WebSocket连接已建立，开始订阅频道")
        
        # 如果连接成功，订阅初始频道
        if self.channels:
            try:
                await self.client.subscribe(self.channels)
                self.logger.info(f"已订阅初始频道: {len(self.channels)}个")
                
                # 给一些时间让数据开始流入
                await asyncio.sleep(2)
                self.logger.info("订阅完成，等待数据流入")
                return True
            except Exception as e:
                self.logger.error(f"订阅频道异常: {e}")
                return False
        else:
            self.logger.info("没有初始频道需要订阅")
            return True
    
    async def stop(self):
        """停止市场数据订阅"""
        self.logger.info("停止市场数据订阅")
        await self.client._disconnect()


class OKExMarketSubscriber(BaseMarketSubscriber):
    """OKEx市场数据订阅管理器"""
    
    def __init__(self, data_cache: DataCache, config: Dict[str, Any], app_name: str = None):
        """
        初始化OKEx市场数据订阅器
        
        Args:
            data_cache: 数据缓存对象
            config: 配置信息
            app_name: 应用名称，用于日志记录
        """
        logger_name = app_name if app_name else "okex_market"
        super().__init__(data_cache, config, logger_name)
    
    def _init_websocket_client(self) -> ExchangeWebSocketClient:
        """初始化OKEx WebSocket客户端"""
        # 获取交易所配置
        exchange_config = self.config.get('exchange', {})
        
        # 创建OKEx特定的客户端
        client = OKExWebSocketClient(self.cache)
        
        # 可以在这里设置自定义的URI
        custom_uri = exchange_config.get('ws_uri')
        if custom_uri:
            client.set_uri(custom_uri)
            
        return client
    
    def _add_symbol_channels(self, symbol: str):
        """为OKEx交易对添加频道订阅"""
        channels = self._get_symbol_channels(symbol)
        self.channels.extend(channels)
    
    def _get_symbol_channels(self, symbol: str) -> List[Dict[str, Any]]:
        """获取OKEx交易对的频道配置"""
        return [
            {"channel": "mark-price", "instId": symbol},
            {"channel": "index-tickers", "instId": symbol},
            {"channel": "instruments", "instId": symbol}
        ]
    
    def _is_channel_for_symbol(self, channel: Dict[str, Any], symbol: str) -> bool:
        """判断OKEx频道是否属于指定交易对"""
        return channel.get("instId") == symbol 