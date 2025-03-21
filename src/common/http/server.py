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

# 添加认证相关导入
from ..auth import UserManager, JwtTokenManager, auth_middleware
from ..auth.auth_api import AuthApiHandler

class WebhookHandler:
    """默认的Webhook处理器"""
    async def handle(self, data, request):
        """处理Webhook请求"""
        logger = logging.getLogger("common.http.server")
        logger.warning(f"默认WebhookHandler收到请求，但没有设置具体的处理器: {data}")
        return {"status": "error", "message": "未设置具体的处理器"}

class HttpServer:
    """HTTP服务器"""
    def __init__(self, port=8080, message_handler=None, host='0.0.0.0', path='/'):
        self.port = port
        self.host = host
        self.path = path
        
        # 初始化Web应用时添加中间件
        self.app = web.Application(middlewares=[auth_middleware])
        
        self.runner = None
        self.site = None
        self.app_initialized = False
        
        # 使用通用日志
        self.logger = logging.getLogger("common.http.server")
        
        # 设置静态文件目录
        current_dir = os.path.dirname(os.path.abspath(__file__))
        self.static_dir = os.path.join(current_dir, "static")
        
        # 设置消息处理器
        self.message_handler = message_handler or WebhookHandler()
        
        # 设置API处理器
        self.api_handler = None
        
        # 设置认证处理器
        self.auth_handler = AuthApiHandler()
        self.app['token_manager'] = JwtTokenManager()
        
        # 注册路由 - 修复路径
        # 确保路径格式正确
        if not self.path.startswith('/'):
            self.path = f"/{self.path}"
        
        self.logger.warning(f"注册webhook路由: POST {self.path}")
        self.app.router.add_post(self.path, self._handle_webhook)
        self.app.router.add_get("/health", self._handle_health_check)
        
        # 添加认证路由
        self._add_auth_routes()
        # 添加api路由
        self._add_api_routes()
        
    def set_trading_framework(self, trading_framework):
        """设置交易框架"""
        self.api_handler = TradingFrameworkApiHandler(trading_framework)
        self.app_initialized = True
    
    async def _handle_webhook(self, request):
        """处理Webhook请求"""
        try:
            # 添加详细日志
            self.logger.warning(f"HttpServer._handle_webhook收到请求: {request.method} {request.url}")
            self.logger.warning(f"请求头: {request.headers}")
            
            # 获取请求内容类型
            content_type = request.headers.get('Content-Type', '')
            
            if 'application/json' in content_type:
                # JSON格式
                data = await request.json()
                self.logger.warning(f"收到JSON Webhook: {data}")
            else:
                # 文本格式
                text = await request.text()
                self.logger.warning(f"收到文本Webhook: {text}")
                try:
                    # 尝试解析为JSON
                    data = json.loads(text)
                    self.logger.warning(f"成功解析为JSON: {data}")
                except json.JSONDecodeError:
                    # 非JSON格式，作为文本处理
                    data = {"text": text}
                    self.logger.warning(f"非JSON格式，作为文本处理: {data}")
            
            # 调用处理函数
            self.logger.warning(f"准备调用message_handler: {self.message_handler}")
            
            # 检查message_handler类型，如果是函数则直接调用
            if callable(self.message_handler):
                try:
                    # 尝试作为协程函数调用
                    if asyncio.iscoroutinefunction(self.message_handler):
                        result = await self.message_handler(data, request)
                    else:
                        # 尝试作为普通函数调用
                        result = self.message_handler(data, request)
                    self.logger.warning(f"message_handler处理结果: {result}")
                except Exception as e:
                    self.logger.exception(f"调用message_handler异常: {e}")
                    return web.json_response(
                        {"status": "error", "message": f"处理异常: {str(e)}"},
                        status=500
                    )
            else:
                # 否则尝试调用handle方法
                result = await self.message_handler.handle(data, request)
                self.logger.warning(f"message_handler.handle处理结果: {result}")
            
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

    def _add_auth_routes(self):
        """添加认证路由"""
        self.app.router.add_post('/webhook/api/auth/register', self.auth_handler.handle_register)
        self.app.router.add_post('/webhook/api/auth/login', self.auth_handler.handle_login)
        self.app.router.add_get('/webhook/api/auth/me', self.auth_handler.handle_current_user)
        self.app.router.add_post('/webhook/api/auth/logout', self.auth_handler.handle_logout)

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

    async def _handle_positions_page(self, request):
        """处理仓位页面请求"""
        # 检查用户是否已认证，未认证则重定向到登录页面
        is_authenticated = request.get('is_authenticated', False)
        print(f"is_authenticated: {is_authenticated}")
        if not is_authenticated:
            login_page = os.path.join(self.static_dir, "login.html")
            if os.path.exists(login_page):
                return web.FileResponse(login_page)
            else:
                # 如果登录页面不存在，则继续提供仓位页面
                self.logger.warning(f"登录页面文件不存在: {login_page}")
        
        positions_page = os.path.join(self.static_dir, "positions.html")
        if os.path.exists(positions_page):
            return web.FileResponse(positions_page)
        else:
            self.logger.warning(f"仓位页面文件不存在: {positions_page}")
            return web.Response(text="仓位页面文件不存在", status=404)

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
    logger = logger or logging.getLogger("common.http.server")
    
    try:
        # 创建应用
        app = web.Application(middlewares=[auth_middleware])
        # 设置CORS
        if cors_origins:
            cors = aiohttp_cors.setup(app, defaults={
                "*": aiohttp_cors.ResourceOptions(
                    allow_credentials=True,
                    expose_headers="*",
                    allow_headers="*",
                    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"]
                )
            })
            
        # 添加根路由重定向到登录页面
        async def root_handler(request):
            raise web.HTTPFound('/static/login.html')
        app.router.add_get('/', root_handler)
        logger.info("注册根路由: GET / -> /static/login.html")
            
        # 添加认证相关路由
        auth_handler = AuthApiHandler()
        app['token_manager'] = JwtTokenManager()
        auth_routes = [
            ('POST', '/api/auth/register', auth_handler.handle_register),
            ('POST', '/api/auth/login', auth_handler.handle_login),
            ('GET', '/api/auth/me', auth_handler.handle_current_user),
            ('POST', '/api/auth/logout', auth_handler.handle_logout)
        ]
        
        # 添加用户自定义路由
        if routes:
            for method, path, handler in routes:
                try:
                    app.router.add_route(method, path, handler)
                    logger.info(f"注册路由: {method} {path}")
                    
                    # 如果启用了CORS，为路由添加CORS支持
                    if cors_origins:
                        # 获取最后添加的路由
                        route = list(app.router.routes())[-1]
                        cors.add(route)
                except Exception as e:
                    logger.error(f"添加路由失败: {method} {path}, 错误: {str(e)}")
                    
        # 添加认证路由
        for method, path, handler in auth_routes:
            try:
                app.router.add_route(method, path, handler)
                logger.info(f"注册认证路由: {method} {path}")
                
                # 如果启用了CORS，为路由添加CORS支持
                if cors_origins:
                    # 获取最后添加的路由
                    route = list(app.router.routes())[-1]
                    cors.add(route)
            except Exception as e:
                logger.error(f"添加认证路由失败: {method} {path}, 错误: {str(e)}")
                
        # 设置静态文件
        if static_dir and static_path:
            try:
                # 确保路径格式正确
                if not static_path.startswith('/'):
                    static_path = f"/{static_path}"
                    
                # 检查目录是否存在
                if not os.path.exists(static_dir):
                    os.makedirs(static_dir)
                    logger.info(f"创建静态文件目录: {static_dir}")
                    
                # 添加静态文件处理
                app.router.add_static(static_path, static_dir, show_index=False, follow_symlinks=True)
                
                # 遍历目录下的所有文件
                for root, dirs, files in os.walk(static_dir):
                    # 计算相对路径
                    rel_path = os.path.relpath(root, static_dir)
                    # 为每个文件添加单独的路由
                    for file in files:
                        file_path = os.path.join(root, file)
                        url_path = f"{static_path}/{rel_path}/{file}".replace("//", "/")
                        app.router.add_get(url_path, lambda r, fp=file_path: web.FileResponse(fp))
                        logger.info(f"注册文件路由: GET {url_path} -> {file_path}")
                
                logger.info(f"注册静态文件路径: {static_path} -> {static_dir}")
                
                # 如果启用了CORS，为静态文件路由添加CORS支持
                if cors_origins:
                    # 获取最后添加的路由
                    route = list(app.router.routes())[-1]
                    cors.add(route)
        
            except Exception as e:
                logger.error(f"添加静态文件路由失败: {static_path}, 错误: {str(e)}")
                
        # 添加健康检查路由
        async def health_check(request):
            return web.json_response({"status": "ok"})

        app.router.add_get('/health', health_check)
        logger.info("注册健康检查路由: GET /health")
        if cors_origins:
            cors.add(list(app.router.routes())[-1])

        # 添加默认路由处理器
        async def default_handler(request):
            logger.info(f"未找到路由: {request.method} {request.path}, 重定向到登录页面")
            raise web.HTTPFound('/static/login.html')
            
        # 注册默认路由处理器
        app.router.add_get('/', default_handler)
        logger.info("注册默认路由: GET / -> /static/login.html")

        return app
        
    except Exception as e:
        logger.error(f"创建HTTP服务器应用失败: {str(e)}")
        raise

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
    logger = logger or logging.getLogger("common.http.server")
    app = create_http_server(host, port, routes, static_dir, static_path, cors_origins, logger)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info(f"HTTP服务器已启动: http://{host}:{port}")
    
    # 保持运行
    while True:
        await asyncio.sleep(3600) 
