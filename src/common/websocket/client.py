import asyncio
import websockets
import json
import logging
import time
from typing import Callable, Optional, Dict, List, Any, Set

class ExchangeWebSocketClient:
    """通用交易所WebSocket客户端"""
    def __init__(self, cache, uri: str = None, app_name: str = "Generic"):
        self.uri = uri
        self.cache = cache
        self.app_name = app_name
        self.logger = logging.getLogger(f"{app_name}.WS")
        self._reconnect_interval = 5
        self._active = True
        self.connection = None
        self._message_processor = None
        # 跟踪已订阅的频道
        self._subscribed_channels: List[Dict[str, Any]] = []
        # 跟踪待订阅的频道
        self._pending_subscriptions: List[Dict[str, Any]] = []
        # 最后一次活动时间
        self.last_activity_time = time.time()
        # 心跳超时时间（秒）
        self.heartbeat_timeout = 30
        # 自动重新订阅间隔（秒）
        self.resubscribe_interval = 3600  # 1小时
        # 记录上次重新订阅的时间
        self.last_resubscribe_time = time.time()

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
            self.logger.warning("尚未建立连接，将频道添加到待订阅列表")
            # 将频道添加到待订阅列表
            for channel in channels:
                if channel not in self._pending_subscriptions:
                    self._pending_subscriptions.append(channel)
            return
            
        # 默认实现，子类可以覆盖
        payload = {
            "op": "subscribe",
            "args": channels
        }
        await self.connection.send(json.dumps(payload))
        self.logger.info(f"已订阅频道: {channels}")
        
        # 记录已订阅的频道
        for channel in channels:
            if channel not in self._subscribed_channels:
                self._subscribed_channels.append(channel)
        
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
        
        # 从已订阅列表中移除
        for channel in channels:
            if channel in self._subscribed_channels:
                self._subscribed_channels.remove(channel)

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
                    # 重新订阅之前的频道
                    await self._resubscribe_all_channels()
                    await self._listen()
            except Exception as e:
                self.logger.error(f"连接断开，{self._reconnect_interval}秒后重试...", exc_info=e)
                await asyncio.sleep(self._reconnect_interval)

    async def _resubscribe_all_channels(self):
        """重新订阅所有之前订阅的频道"""
        channels_to_subscribe = []
        
        # 添加已订阅的频道
        if self._subscribed_channels:
            channels_to_subscribe.extend(self._subscribed_channels)
            self.logger.info(f"准备重新订阅 {len(self._subscribed_channels)} 个已订阅频道")
        
        # 添加待订阅的频道
        if self._pending_subscriptions:
            for channel in self._pending_subscriptions:
                if channel not in channels_to_subscribe:
                    channels_to_subscribe.append(channel)
            self.logger.info(f"准备订阅 {len(self._pending_subscriptions)} 个待订阅频道")
            # 清空待订阅列表
            self._pending_subscriptions = []
            
        if not channels_to_subscribe:
            self.logger.info("没有需要重新订阅的频道")
            return
            
        self.logger.info(f"共订阅 {len(channels_to_subscribe)} 个频道")
        await self.subscribe(channels_to_subscribe)
        self.last_resubscribe_time = time.time()

    async def _listen(self):
        """监听数据流"""
        if not self.connection:
            self.logger.error("未建立连接，无法监听")
            return
        
        try:
            # 启动心跳检查任务
            heartbeat_task = asyncio.ensure_future(self._check_heartbeat())
            
            # 启动定期重新订阅任务
            resubscribe_task = asyncio.ensure_future(self._periodic_resubscribe())
            
            async for message in self.connection:
                try:
                    # 更新最后活动时间
                    self.last_activity_time = time.time()
                    
                    data = json.loads(message)
                    await self._process_message(data)
                except json.JSONDecodeError:
                    self.logger.warning("收到无效的JSON数据")
                except Exception as e:
                    self.logger.error(f"处理消息时发生错误: {e}", exc_info=e)
                    
            # 取消心跳任务
            heartbeat_task.cancel()
            resubscribe_task.cancel()
        except websockets.ConnectionClosed:
            self.logger.warning("连接被服务器关闭")
        except Exception as e:
            self.logger.error(f"监听过程中发生错误: {e}", exc_info=e)

    async def _check_heartbeat(self):
        """检查心跳，如果长时间没有活动，认为连接已断开"""
        while True:
            try:
                await asyncio.sleep(10)  # 每10秒检查一次
                
                current_time = time.time()
                if current_time - self.last_activity_time > self.heartbeat_timeout:
                    self.logger.warning(f"超过 {self.heartbeat_timeout} 秒未收到消息，可能连接已断开")
                    
                    # 尝试发送ping消息
                    try:
                        if self.connection:
                            pong = await self.connection.ping()
                            self.logger.info("Ping成功，连接仍然活跃")
                            self.last_activity_time = current_time
                    except Exception as e:
                        self.logger.error(f"Ping失败，连接可能已断开: {e}")
                        # 主动断开连接，触发重连
                        if self.connection:
                            await self.connection.close()
                            self.connection = None
                            break
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"心跳检查异常: {e}")

    async def _periodic_resubscribe(self):
        """定期重新订阅所有频道，确保数据流的稳定性"""
        while True:
            try:
                await asyncio.sleep(60)  # 每分钟检查一次
                
                current_time = time.time()
                if current_time - self.last_resubscribe_time > self.resubscribe_interval:
                    self.logger.info(f"已经 {self.resubscribe_interval/3600:.1f} 小时未重新订阅，执行定期重新订阅")
                    await self._resubscribe_all_channels()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"定期重新订阅异常: {e}")

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
            # 连接后重新订阅之前的频道
            if self._subscribed_channels:
                self.logger.info(f"连接成功，重新订阅 {len(self._subscribed_channels)} 个频道")
                await self._resubscribe_all_channels()
            
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
    def __init__(self, cache, app_name: str = "Generic", uri: str = "wss://ws.okx.com:8443/ws/v5/public"):
        super().__init__(cache, uri, app_name)
        
    async def _process_message(self, data: Dict[str, Any]):
        """处理OKEx特定的消息格式"""
        if 'arg' in data and 'data' in data:
            channel = data['arg']['channel']
            for item in data['data']:
                # 更新缓存
                await self.cache.update(channel, item)
        elif 'event' in data:
            # 处理订阅确认等事件消息
            # self.logger.debug(f"收到事件: {data['event']}")
            
            # 如果是订阅成功事件，记录频道信息
            if data['event'] == 'subscribe' and 'arg' in data:
                channel_info = data['arg']
                if channel_info not in self._subscribed_channels:
                    self._subscribed_channels.append(channel_info)
                    self.logger.debug(f"已确认订阅频道: {channel_info}")
