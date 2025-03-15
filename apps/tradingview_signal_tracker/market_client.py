import logging
from typing import Dict, Any, List, Set
from src.common.websocket.client import ExchangeWebSocketClient, OKExWebSocketClient
from src.common.data_cache import DataCache, OKExDataCache

logger = logging.getLogger("tradingview_signal_tracker")

class MarketDataSubscriber:
    """市场数据订阅管理器"""
    def __init__(self, cache: DataCache, config: Dict[str, Any]):
        """
        初始化市场数据订阅器
        
        Args:
            cache: 数据缓存对象
            config: 配置信息
        """
        self.cache = cache
        self.config = config
        
        # 获取交易所配置
        exchange_config = config.get('exchange', {})
        exchange_type = exchange_config.get('type', 'okex').lower()
        
        # 根据配置创建对应的WebSocket客户端
        if exchange_type == 'okex':
            # 使用OKEx特定的客户端
            self.client = OKExWebSocketClient(cache)
            # 可以在这里设置自定义的URI
            custom_uri = exchange_config.get('ws_uri')
            if custom_uri:
                self.client.set_uri(custom_uri)
        else:
            # 使用通用客户端
            self.client = ExchangeWebSocketClient(cache, exchange_name=exchange_type.capitalize())
            # 必须设置URI
            ws_uri = exchange_config.get('ws_uri')
            if not ws_uri:
                raise ValueError(f"未配置 {exchange_type} 的WebSocket URI")
            self.client.set_uri(ws_uri)
            
            # 设置自定义消息处理器
            if hasattr(self, f"_process_{exchange_type}_message"):
                processor = getattr(self, f"_process_{exchange_type}_message")
                self.client.set_message_processor(processor)
        
        # 从配置中获取默认订阅的交易对
        self.strategy_config = config.get('strategy', {})
        self.default_symbols = self.strategy_config.get('default_symbols', ["BTC-USDT-SWAP"])
        
        # 当前已订阅的交易对集合
        self.subscribed_symbols: Set[str] = set(self.default_symbols)
        
        # 初始化必要的频道订阅
        self._init_channels()
        
    def _init_channels(self):
        """初始化频道订阅配置"""
        self.channels = []
        
        # 为每个交易对订阅必要的频道
        for symbol in self.subscribed_symbols:
            self._add_symbol_channels(symbol)
            
    def _add_symbol_channels(self, symbol: str):
        """为指定交易对添加频道订阅"""
        # 这里可以根据不同交易所定制不同的频道格式
        exchange_type = self.config.get('exchange', {}).get('type', 'okex').lower()
        
        if exchange_type == 'okex':
            channels = [
                {"channel": "mark-price", "instId": symbol},
                {"channel": "funding-rate", "instId": symbol},
                {"channel": "open-interest", "instId": symbol}
            ]
        else:
            # 其他交易所的频道格式
            channels = []
            logger.warning(f"未实现 {exchange_type} 的频道格式")
            
        self.channels.extend(channels)
        
    async def subscribe_symbol(self, symbol: str):
        """
        订阅新的交易对数据
        
        Args:
            symbol: 交易对名称
        """
        if symbol in self.subscribed_symbols:
            logger.debug(f"交易对 {symbol} 已订阅")
            return
            
        # 添加新的频道订阅
        new_channels = []
        exchange_type = self.config.get('exchange', {}).get('type', 'okex').lower()
        
        if exchange_type == 'okex':
            for channel in ["mark-price", "funding-rate", "open-interest"]:
                new_channels.append({"channel": channel, "instId": symbol})
        else:
            # 其他交易所的频道格式
            logger.warning(f"未实现 {exchange_type} 的频道格式")
            
        # 执行订阅
        if new_channels:
            await self.client.subscribe(new_channels)
            
            # 更新已订阅集合
            self.subscribed_symbols.add(symbol)
            self.channels.extend(new_channels)
            
            logger.info(f"已订阅交易对: {symbol}")
        
    async def unsubscribe_symbol(self, symbol: str):
        """
        取消订阅交易对数据
        
        Args:
            symbol: 交易对名称
        """
        if symbol not in self.subscribed_symbols:
            return
            
        # 构建取消订阅的频道列表
        channels_to_unsub = []
        exchange_type = self.config.get('exchange', {}).get('type', 'okex').lower()
        
        if exchange_type == 'okex':
            for channel in ["mark-price", "funding-rate", "open-interest"]:
                channels_to_unsub.append({"channel": channel, "instId": symbol})
        else:
            # 其他交易所的频道格式
            logger.warning(f"未实现 {exchange_type} 的频道格式")
            
        # 执行取消订阅
        if channels_to_unsub:
            await self.client.unsubscribe(channels_to_unsub)
            
            # 更新已订阅集合和频道列表
            self.subscribed_symbols.remove(symbol)
            self.channels = [c for c in self.channels if c.get("instId") != symbol]
            
            logger.info(f"已取消订阅交易对: {symbol}")
        
    async def start(self):
        """启动市场数据订阅"""
        logger.info(f"启动市场数据订阅，初始交易对: {', '.join(self.subscribed_symbols)}")
        
        # 先连接WebSocket
        connected = await self.client.connect()
        
        # 如果连接成功，订阅初始频道
        if connected and self.channels:
            await self.client.subscribe(self.channels)
        elif not connected:
            logger.error("WebSocket连接失败，无法订阅频道")
        
    async def stop(self):
        """停止市场数据订阅"""
        logger.info("停止市场数据订阅")
        await self.client._disconnect()
        
    # 以下是不同交易所的消息处理器
    async def _process_binance_message(self, data: Dict[str, Any]):
        """处理Binance消息格式"""
        # 这里实现Binance特定的消息处理逻辑
        logger.debug(f"处理Binance消息: {data}")
        
    async def _process_bybit_message(self, data: Dict[str, Any]):
        """处理Bybit消息格式"""
        # 这里实现Bybit特定的消息处理逻辑
        logger.debug(f"处理Bybit消息: {data}") 