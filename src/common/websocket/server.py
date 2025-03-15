import asyncio
import websockets
import json
import logging
from typing import Callable, Set

class WebSocketServer:
    """支持多客户端的WebSocket服务端"""
    def __init__(self, port: int, message_handler: Callable, host: str = "0.0.0.0"):
        self.port = port
        self.host = host
        self.handler = message_handler
        self.clients: Set[websockets.WebSocketServerProtocol] = set()
        self.logger = logging.getLogger("WS-Server")
        self._server = None
        self._task = None

    async def _handle_connection(self, websocket):
        """处理单个客户端连接"""
        self.clients.add(websocket)
        self.logger.info(f"客户端连接，当前连接数: {len(self.clients)}")
        
        try:
            async for message in websocket:
                await self.handler(message, websocket)
        except websockets.ConnectionClosed:
            self.logger.info("客户端断开连接")
        finally:
            self.clients.remove(websocket)

    async def broadcast(self, message: str):
        """向所有客户端广播消息"""
        if self.clients:
            await asyncio.gather(
                *[client.send(message) for client in self.clients]
            )

    async def start(self):
        """启动服务"""
        self.logger.info(f"启动WebSocket服务端，端口: {self.port}")
        # 创建服务器但不等待它完成
        self._server = await websockets.serve(self._handle_connection, self.host, self.port)
        # 不再创建永久运行的任务，让服务器在后台运行

    async def _run_forever(self):
        """保持服务运行"""
        try:
            # Python 3.6 兼容方式：创建一个永不完成的 Future
            future = asyncio.Future()
            await future  # 永久运行
        except asyncio.CancelledError:
            self.logger.info("WebSocket服务任务被取消")
            
    async def stop(self):
        """停止服务"""
        self.logger.info("正在停止WebSocket服务...")
        
        # 关闭所有客户端连接
        if self.clients:
            close_tasks = [client.close() for client in self.clients.copy()]
            if close_tasks:
                await asyncio.gather(*close_tasks, return_exceptions=True)
            self.clients.clear()
            
        # 关闭服务器
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            
        self.logger.info("WebSocket服务已停止")
