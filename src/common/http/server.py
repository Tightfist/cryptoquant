import asyncio
import logging
import json
from typing import Callable, Dict, Any
from aiohttp import web

class HttpServer:
    """HTTP服务器，用于接收Webhook请求"""
    def __init__(self, port: int, message_handler: Callable, host: str = "0.0.0.0", path: str = "/webhook"):
        """
        初始化HTTP服务器
        
        Args:
            port: 监听端口
            message_handler: 消息处理函数，接收(data, request)参数
            host: 监听地址
            path: Webhook路径
        """
        self.port = port
        self.host = host
        self.path = path
        self.handler = message_handler
        self.logger = logging.getLogger("HTTP-Server")
        self.app = web.Application()
        self.runner = None
        self.site = None
        
        # 注册路由
        self.app.router.add_post(self.path, self._handle_webhook)
        self.app.router.add_get("/health", self._handle_health_check)
        
    async def _handle_webhook(self, request):
        """处理Webhook请求"""
        try:
            # 获取请求内容类型
            content_type = request.headers.get('Content-Type', '')
            
            if 'application/json' in content_type:
                # JSON格式
                data = await request.json()
                self.logger.debug(f"收到JSON Webhook: {data}")
            else:
                # 文本格式
                text = await request.text()
                self.logger.debug(f"收到文本Webhook: {text}")
                try:
                    # 尝试解析为JSON
                    data = json.loads(text)
                except json.JSONDecodeError:
                    # 非JSON格式，作为文本处理
                    data = {"text": text}
            
            # 调用处理函数
            result = await self.handler(data, request)
            
            # 返回处理结果
            if result is None:
                return web.json_response({"status": "success"})
            return web.json_response(result)
            
        except Exception as e:
            self.logger.exception(f"处理Webhook请求异常: {e}")
            return web.json_response(
                {"status": "error", "message": str(e)},
                status=500
            )
    
    async def _handle_health_check(self, request):
        """健康检查接口"""
        return web.json_response({"status": "ok"})
        
    async def start(self):
        """启动服务器"""
        self.logger.info(f"启动HTTP服务器，监听地址: {self.host}:{self.port}, 路径: {self.path}")
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        
    async def stop(self):
        """停止服务器"""
        self.logger.info("停止HTTP服务器")
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup() 