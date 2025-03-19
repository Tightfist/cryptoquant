"""
HTTP服务器工具模块

提供创建和管理HTTP服务器的工具函数
"""

import asyncio
import logging
import os
import json
from typing import List, Tuple, Callable, Awaitable, Dict, Any
from aiohttp import web
import aiohttp_cors


class HttpServer:
    """HTTP服务器"""
    def __init__(self, port=8080, message_handler=None, host='0.0.0.0', path='/'):
        self.port = port
        self.host = host
        self.path = path
        self.app = web.Application()
        self.runner = None
        self.site = None
        self.app_initialized = False
        
        # 设置静态文件目录
        current_dir = os.path.dirname(os.path.abspath(__file__))
        self.static_dir = os.path.join(current_dir, "static")
        
        # 设置消息处理器
        self.message_handler = message_handler or WebhookHandler()
        
        # 设置API处理器
        self.api_handler = None
        
        # 注册路由
        self.app.router.add_post(self.path, self._handle_webhook)
        self.app.router.add_get("/health", self._handle_health_check)
        
    def set_trading_framework(self, trading_framework):
        """设置交易框架"""
        self.api_handler = TradingFrameworkApiHandler(trading_framework)
        self._add_api_routes()
        self.app_initialized = True
    
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
            result = await self.message_handler.handle(data, request)
            
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

    def _add_api_routes(self):
        """添加API路由"""
        # 添加HTML路由
        self.app.router.add_static('/webhook/static', self.static_dir)
        self.app.router.add_get('/webhook/positions', self._handle_positions_page)
        
        # 添加API路由
        self.app.router.add_post('/webhook', self.message_handler.handle)
        self.app.router.add_get('/webhook/api/open_positions', self.api_handler.handle_api_open_positions)
        self.app.router.add_get('/webhook/api/position_history', self.api_handler.handle_api_position_history)
        self.app.router.add_get('/webhook/api/daily_pnl', self.api_handler.handle_api_daily_pnl)
        self.app.router.add_post('/webhook/api/trigger', self.api_handler.handle_api_trigger)
        self.app.router.add_post('/webhook/api/close_all', self.api_handler.handle_api_close_all)

def create_http_server(
    host: str,
    port: int,
    routes: List[tuple] = None,
    static_dir: str = None,
    static_path: str = None,
    cors_origins: List[str] = None,
    logger=None
) -> web.Application:
    """
    创建HTTP服务器应用
    
    Args:
        host: 主机地址
        port: 端口号
        routes: 路由列表，格式为 [(method, path, handler), ...]
        static_dir: 静态文件目录路径
        static_path: 静态文件URL路径前缀
        cors_origins: CORS允许的来源列表
        logger: 日志记录器
        
    Returns:
        web.Application: web应用实例
    """
    logger = logger or logging.getLogger("HTTPServer")
    
    # 创建应用
    app = web.Application()
    
    # 添加路由
    if routes:
        for method, path, handler in routes:
            app.router.add_route(method, path, handler)
            logger.info(f"注册路由: {method} {path}")
    
    # 设置静态文件
    if static_dir and static_path:
        # 确保路径格式正确
        if not static_path.startswith('/'):
            static_path = f"/{static_path}"
            
        # 检查目录是否存在
        if not os.path.exists(static_dir):
            os.makedirs(static_dir)
            logger.info(f"创建静态文件目录: {static_dir}")
            
        # 添加静态文件处理
        app.router.add_static(static_path, static_dir)
        logger.info(f"注册静态文件路径: {static_path} -> {static_dir}")
        
        # 添加默认索引页面路由
        positions_page = os.path.join(static_dir, "positions.html")
        if os.path.exists(positions_page):
            async def serve_positions_page(request):
                return web.FileResponse(positions_page)
            
            app.router.add_get('/positions', serve_positions_page)
            logger.info(f"注册仓位页面路由: /positions -> {positions_page}")
    
    # 配置CORS
    if cors_origins:
        cors = aiohttp_cors.setup(app, defaults={
            origin: aiohttp_cors.ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
                allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"]
            ) for origin in cors_origins
        })
        
        # 对所有路由应用CORS设置
        for route in list(app.router.routes()):
            cors.add(route)
            
        logger.info(f"已配置CORS，允许来源: {cors_origins}")
        
    return app

async def run_http_server(
    host: str,
    port: int,
    routes: List[tuple] = None,
    static_dir: str = None,
    static_path: str = None,
    cors_origins: List[str] = None,
    logger=None
) -> None:
    """
    运行HTTP服务器
    
    Args:
        host: 主机地址
        port: 端口号
        routes: 路由列表，格式为 [(method, path, handler), ...]
        static_dir: 静态文件目录路径
        static_path: 静态文件URL路径前缀
        cors_origins: CORS允许的来源列表
        logger: 日志记录器
    """
    logger = logger or logging.getLogger("HTTPServer")
    
    app = create_http_server(host, port, routes, static_dir, static_path, cors_origins, logger)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, host, port)
    await site.start()
    
    logger.info(f"HTTP服务器已启动: http://{host}:{port}")
    
    # 保持运行
    while True:
        await asyncio.sleep(3600) 