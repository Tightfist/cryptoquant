import json
import logging
import re
from typing import Dict, Any, Callable, Optional
from src.common.http.server import HttpServer
from src.common.data_cache import DataCache

logger = logging.getLogger("tradingview_signal_tracker")

class SignalProcessor:
    """处理TradingView的Webhook信号"""
    def __init__(self, strategy_callback: Callable, config: Dict[str, Any], cache: Optional[DataCache] = None):
        """
        初始化信号处理器
        
        Args:
            strategy_callback: 处理信号的策略回调函数
            config: 配置信息
            cache: 数据缓存对象，如果为None则创建新的
        """
        self.cache = cache if cache is not None else DataCache()
        self.config = config
        self.strategy_callback = strategy_callback
        
        # 从配置中获取HTTP服务器设置
        webhook_config = config.get('webhook', {})
        self.port = webhook_config.get('port', 8765)
        self.host = webhook_config.get('host', '0.0.0.0')
        self.path = webhook_config.get('path', '/webhook')
        
        # 初始化HTTP服务器
        self.server = HttpServer(self.port, self._handle_webhook, host=self.host, path=self.path)
        logger.info(f"信号处理器初始化完成，监听地址: {self.host}:{self.port}{self.path}")
        
        # 初始化合约名称映射
        self._init_symbol_mapping()
        
    def _init_symbol_mapping(self):
        """初始化合约名称映射"""
        # 从配置中获取合约名称映射
        self.symbol_mapping = self.config.get('symbol_mapping', {})
        
        # 如果没有配置，使用默认映射规则
        if not self.symbol_mapping:
            # 默认映射规则：将 BTCUSDT.P 转换为 BTC-USDT-SWAP
            self.symbol_mapping = {
                "BTCUSDT.P": "BTC-USDT-SWAP",
                "ETHUSDT.P": "ETH-USDT-SWAP",
                "LTCUSDT.P": "LTC-USDT-SWAP",
                "XRPUSDT.P": "XRP-USDT-SWAP",
                "EOSUSDT.P": "EOS-USDT-SWAP",
                "BCHUSDT.P": "BCH-USDT-SWAP",
                "ETCUSDT.P": "ETC-USDT-SWAP",
                "LINKUSDT.P": "LINK-USDT-SWAP",
                "DOGEUSDT.P": "DOGE-USDT-SWAP",
                "ADAUSDT.P": "ADA-USDT-SWAP",
                "DOTUSDT.P": "DOT-USDT-SWAP",
                "UNIUSDT.P": "UNI-USDT-SWAP",
                "SOLUSDT.P": "SOL-USDT-SWAP",
                "MATICUSDT.P": "MATIC-USDT-SWAP",
                "FILUSDT.P": "FIL-USDT-SWAP",
                "AVAXUSDT.P": "AVAX-USDT-SWAP",
                "SHIBUSDT.P": "SHIB-USDT-SWAP",
                "NEARUSDT.P": "NEAR-USDT-SWAP",
                "APTUSDT.P": "APT-USDT-SWAP",
                "OPUSDT.P": "OP-USDT-SWAP"
            }
            
    def _convert_symbol(self, symbol: str) -> str:
        """
        将TradingView的合约名称转换为OKEx的格式
        
        Args:
            symbol: TradingView的合约名称，如 BTCUSDT.P
            
        Returns:
            str: OKEx的合约名称，如 BTC-USDT-SWAP
        """
        # 如果在映射表中，直接返回映射结果
        if symbol in self.symbol_mapping:
            return self.symbol_mapping[symbol]
            
        # 尝试使用正则表达式进行转换
        # 例如：将 BTCUSDT.P 转换为 BTC-USDT-SWAP
        match = re.match(r"([A-Z]+)USDT\.P", symbol)
        if match:
            coin = match.group(1)
            return f"{coin}-USDT-SWAP"
            
        # 如果无法转换，返回原始名称
        logger.warning(f"无法转换合约名称: {symbol}")
        return symbol

    async def _handle_webhook(self, data: Dict[str, Any], request):
        """处理接收到的Webhook请求"""
        try:
            # 检查是否是手动触发的API请求
            path = request.path if hasattr(request, 'path') else ''
            if path.endswith('/api/trigger'):
                return await self._handle_manual_trigger(data)
            elif path.endswith('/api/close_all'):
                return await self._handle_manual_close_all()
            elif path.endswith('/api/status'):
                return await self._handle_get_status()
                
            logger.debug(f"收到信号: {data}")
            
            # 验证信号有效性
            if not self._validate_signal(data):
                logger.warning(f"无效信号: {data}")
                return {"status": "error", "message": "Invalid signal format"}
                
            # 转换合约名称
            if 'symbol' in data:
                original_symbol = data['symbol']
                data['symbol'] = self._convert_symbol(original_symbol)
                logger.info(f"转换合约名称: {original_symbol} -> {data['symbol']}")
                
            # 如果有多个合约，也进行转换
            if 'symbols' in data and isinstance(data['symbols'], list):
                original_symbols = data['symbols']
                data['symbols'] = [self._convert_symbol(s) for s in original_symbols]
                logger.info(f"转换多个合约名称: {original_symbols} -> {data['symbols']}")
                
            # 获取最新行情数据
            if 'symbol' in data:
                try:
                    # 根据交易所类型获取价格
                    exchange_type = self.config.get('exchange', {}).get('type', 'okex').lower()
                    symbol = data['symbol']
                    
                    if exchange_type == 'okex' and hasattr(self.cache, 'get_mark_price'):
                        price = await self.cache.get_mark_price(symbol)
                    else:
                        # 通用方式获取价格
                        mark_price_data = await self.cache.get("mark-price", symbol)
                        price = float(mark_price_data.get('price', 0.0))
                        
                    data['current_price'] = price
                    logger.info(f"当前{symbol}价格: {price}")
                except Exception as e:
                    logger.warning(f"获取价格失败: {e}")
            
            # 执行策略回调
            await self.strategy_callback(data)
            
            # 返回成功响应
            return {
                "status": "success",
                "message": "Signal processed successfully"
            }
            
        except json.JSONDecodeError:
            logger.error("无效的JSON格式")
            return {
                "status": "error",
                "message": "Invalid JSON format"
            }
        except Exception as e:
            logger.exception(f"处理信号时发生错误: {e}")
            return {
                "status": "error",
                "message": f"Error processing signal: {str(e)}"
            }
            
    async def _handle_manual_trigger(self, data: Dict[str, Any]):
        """处理手动触发信号请求"""
        try:
            # 验证必要参数
            if 'action' not in data or 'symbol' not in data:
                return {"status": "error", "message": "Missing required parameters: action, symbol"}
                
            # 转换合约名称
            original_symbol = data['symbol']
            data['symbol'] = self._convert_symbol(original_symbol)
            
            # 调用策略的手动触发方法
            from apps.tradingview_signal_tracker.strategy import TradingViewSignalStrategy
            strategy = self.strategy_callback.__self__
            if isinstance(strategy, TradingViewSignalStrategy):
                result = await strategy.manual_trigger(data['action'], data['symbol'], **data)
                return result
            else:
                return {"status": "error", "message": "Strategy does not support manual trigger"}
        except Exception as e:
            logger.exception(f"手动触发信号异常: {e}")
            return {"status": "error", "message": f"Error triggering signal: {str(e)}"}
            
    async def _handle_manual_close_all(self):
        """处理手动平仓所有持仓请求"""
        try:
            # 调用策略的手动平仓方法
            from apps.tradingview_signal_tracker.strategy import TradingViewSignalStrategy
            strategy = self.strategy_callback.__self__
            if isinstance(strategy, TradingViewSignalStrategy):
                result = await strategy.manual_close_all()
                return result
            else:
                return {"status": "error", "message": "Strategy does not support manual close all"}
        except Exception as e:
            logger.exception(f"手动平仓异常: {e}")
            return {"status": "error", "message": f"Error closing positions: {str(e)}"}
            
    async def _handle_get_status(self):
        """处理获取状态请求"""
        try:
            # 调用策略的获取状态方法
            from apps.tradingview_signal_tracker.strategy import TradingViewSignalStrategy
            strategy = self.strategy_callback.__self__
            if isinstance(strategy, TradingViewSignalStrategy):
                result = await strategy.get_status()
                return result
            else:
                return {"status": "error", "message": "Strategy does not support get status"}
        except Exception as e:
            logger.exception(f"获取状态异常: {e}")
            return {"status": "error", "message": f"Error getting status: {str(e)}"}
            
    async def start(self):
        """启动HTTP服务器"""
        logger.info("启动信号处理服务...")
        # 注册API路由 - 修复路由注册方式
        self.server.app.router.add_post(f"{self.path}/api/trigger", self._handle_webhook)
        self.server.app.router.add_post(f"{self.path}/api/close_all", self._handle_webhook)
        self.server.app.router.add_get(f"{self.path}/api/status", self._handle_webhook)
        await self.server.start()
        
    async def stop(self):
        """停止HTTP服务器"""
        logger.info("停止信号处理服务...") 
        await self.server.stop() 