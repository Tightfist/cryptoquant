import asyncio
import websockets
import json
import logging
from typing import Callable, Optional, Dict, List, Any

class ExchangeWebSocketClient:
    """通用交易所WebSocket客户端"""
    def __init__(self, cache, uri: str = None, exchange_name: str = "Generic"):
        self.uri = uri
        self.cache = cache
        self.exchange_name = exchange_name
        self.logger = logging.getLogger(f"{exchange_name}-WS")
        self._reconnect_interval = 5
        self._active = True
        self.connection = None
        self._message_processor = None

    def set_uri(self, uri: str):
        """设置WebSocket URI"""
        self.uri = uri
        
    def set_message_processor(self, processor: Callable):
        """设置消息处理器"""
        self._message_processor = processor

    async def subscribe(self, channels: List[Dict[str, Any]]):
        """
        订阅指定频道
        
        Args:
            channels: 频道列表，格式由具体交易所决定
        """
        if not self.connection:
            self.logger.warning("尚未建立连接，无法订阅")
            return
            
        # 默认实现，子类可以覆盖
        payload = {
            "op": "subscribe",
            "args": channels
        }
        await self.connection.send(json.dumps(payload))
        self.logger.info(f"已订阅频道: {channels}")
        
    async def unsubscribe(self, channels: List[Dict[str, Any]]):
        """
        取消订阅指定频道
        
        Args:
            channels: 频道列表，格式由具体交易所决定
        """
        if not self.connection:
            self.logger.warning("尚未建立连接，无法取消订阅")
            return
            
        # 默认实现，子类可以覆盖
        payload = {
            "op": "unsubscribe",
            "args": channels
        }
        await self.connection.send(json.dumps(payload))
        self.logger.info(f"已取消订阅频道: {channels}")

    async def _connect(self):
        """建立连接并保持"""
        if not self.uri:
            self.logger.error("未设置WebSocket URI，无法连接")
            return
            
        while self._active:
            try:
                async with websockets.connect(self.uri) as ws:
                    self.connection = ws
                    self.logger.info(f"已连接到 {self.uri}")
                    await self._listen()
            except Exception as e:
                self.logger.error(f"连接断开，{self._reconnect_interval}秒后重试...", exc_info=e)
                await asyncio.sleep(self._reconnect_interval)

    async def _listen(self):
        """监听数据流"""
        if not self.connection:
            self.logger.error("未建立连接，无法监听")
            return
        
        try:
            async for message in self.connection:
                try:
                    data = json.loads(message)
                    await self._process_message(data)
                except json.JSONDecodeError:
                    self.logger.warning("收到无效的JSON数据")
                except Exception as e:
                    self.logger.error(f"处理消息时发生错误: {e}", exc_info=e)
        except websockets.ConnectionClosed:
            self.logger.warning("连接被服务器关闭")
        except Exception as e:
            self.logger.error(f"监听过程中发生错误: {e}", exc_info=e)

    async def _process_message(self, data: Dict[str, Any]):
        """
        处理接收到的消息
        
        Args:
            data: 解析后的JSON数据
        """
        # 如果设置了自定义处理器，则使用它
        if self._message_processor:
            await self._message_processor(data)
        else:
            # 默认实现，子类可以覆盖
            self.logger.debug(f"收到数据: {data}")

    async def _disconnect(self):
        """断开连接"""
        self._active = False
        if self.connection:
            try:
                await self.connection.close()
                self.logger.info("已断开连接")
            except Exception as e:
                self.logger.error(f"关闭连接时发生错误: {e}")
            finally:
                self.connection = None

    async def connect(self):
        """建立连接但不阻塞"""
        if not self.uri:
            self.logger.error("未设置WebSocket URI，无法连接")
            return False
        
        try:
            self.connection = await websockets.connect(self.uri)
            self.logger.info(f"已连接到 {self.uri}")
            # 启动监听任务
            asyncio.ensure_future(self._listen_task())
            return True
        except Exception as e:
            self.logger.error(f"连接失败: {e}")
            return False
        
    async def _listen_task(self):
        """监听任务"""
        try:
            await self._listen()
        except Exception as e:
            self.logger.error(f"监听任务异常: {e}")
        finally:
            # 如果连接断开，尝试重新连接
            if self._active:
                self.logger.info(f"{self._reconnect_interval}秒后尝试重新连接...")
                await asyncio.sleep(self._reconnect_interval)
                asyncio.ensure_future(self.connect())


class OKExWebSocketClient(ExchangeWebSocketClient):
    """OKEx行情订阅客户端"""
    def __init__(self, cache, uri: str = "wss://ws.okx.com:8443/ws/v5/public"):
        super().__init__(cache, uri, "OKEx")
        
    async def _process_message(self, data: Dict[str, Any]):
        """处理OKEx特定的消息格式"""
        if 'arg' in data and 'data' in data:
            channel = data['arg']['channel']
            for item in data['data']:
                # 更新缓存
                await self.cache.update(channel, item)
        elif 'event' in data:
            # 处理订阅确认等事件消息
            self.logger.debug(f"收到事件: {data['event']}")
