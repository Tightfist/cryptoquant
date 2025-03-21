#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
TradingView信号追踪器

功能：
1. 启动WebSocket服务器，接收TradingView的信号
2. 根据信号执行开仓、平仓等操作
3. 支持止盈止损和追踪止损
4. 持久化仓位信息，避免重启丢失
"""

import os
import sys
import asyncio
import logging
import signal
import time
from pathlib import Path
from typing import Dict, Any
import json
from aiohttp import web
from aiohttp_cors import setup as setup_cors, ResourceOptions

# 添加项目根目录到PYTHONPATH
sys.path.append(str(Path(__file__).parents[2]))

from src.common.logger import configure_logger
from src.common.event_loop import AsyncEventLoop
from src.common.config_loader import get_app_config
from src.common.trading_framework import TradingFramework, TradeSignal
from src.common.http.api_handlers import TradingFrameworkApiHandler
from src.common.scripts.generate_api_scripts import generate_api_scripts
from src.common.http.server import HttpServer
from apps.tradingview_signal_tracker.routes import TradingViewWebhook
from apps.tradingview_signal_tracker.trading_view_strategy import TradingViewStrategy
from utils.env_loader import load_env, get_required_env, get_bool_env

# 应用名称
APP_NAME = "tradingview_signal_tracker"

def main():
    """主程序入口"""
    try:
        # 加载环境变量
        load_env()
        
        # 使用统一的配置加载函数
        config = get_app_config(APP_NAME)

        # 创建并运行应用
        app = TradingViewSignalApp(config)
        app.run()
    except KeyboardInterrupt:
        print("用户中断，程序退出")
        raise
    except Exception as e:
        print(f"应用启动失败: {e}")
        raise

class TradingViewSignalApp:
    """TradingView信号追踪应用"""
    def __init__(self, config: Dict[str, Any]):
        """
        初始化应用
        
        Args:
            config: 配置信息
        """
        self.config = config
        self.app_name = config['app_name']
        
        # 配置日志
        log_config = config.get('logging', {})
        configure_logger(
            self.app_name, 
            log_config.get('level', 'INFO'),
            log_config.get('file', f"{self.app_name}.log"),
            log_config.get('output_targets', ["file", "console"])
        )
        self.logger = logging.getLogger(self.app_name)
        
        # 初始化HTTP服务器
        self._init_http_server()

        # 初始化事件循环
        self.event_loop = AsyncEventLoop()
        
        # 初始化框架
        self.framework = TradingFramework(
            app_name=self.app_name,
            strategy_class=TradingViewStrategy,
            config=config
        )
        
        # 注册信号处理
        self._register_signal_handlers()
        
        # 生成API脚本
        self._generate_api_scripts()
        
        self.logger.info(f"{self.app_name}初始化完成")
    
    def _init_http_server(self):
        """初始化HTTP服务器"""
        # 兼容性检查：尝试获取http_server配置，如果不存在则使用webhook配置
        http_config = self.config.get('http_server', None)
        if http_config is None:
            # 使用旧的webhook配置
            webhook_config = self.config.get('webhook', {})
            self.port = webhook_config.get('port', 8080)
            self.host = webhook_config.get('host', '0.0.0.0')
            self.base_path = webhook_config.get('path', '/webhook')
            
            # 使用旧版方式初始化HTTP服务器
            self.http_server = HttpServer(
                port=self.port,
                message_handler=self._handle_webhook,
                host=self.host,
                path=self.base_path
            )
            
            self.logger.info(f"使用传统模式初始化HTTP服务器, 监听地址: {self.host}:{self.port}{self.base_path}")
            self._use_new_http_server = False
        else:
            # 使用新的http_server配置
            enabled = http_config.get('enabled', True)
            
            if not enabled:
                self.logger.info("HTTP服务器已禁用")
                self._use_new_http_server = False
                return
                
            self.port = http_config.get('port', 8080)
            self.host = http_config.get('host', '0.0.0.0')
            self.base_path = http_config.get('base_path', '/webhook')
            
            # HTTP服务器会在异步任务中启动
            self.http_server = None
            
            self.logger.info(f"使用新模式初始化HTTP服务器, 监听地址: {self.host}:{self.port}，服务正在启动中....")
            self._use_new_http_server = True
    
    async def _handle_webhook(self, data: Dict[str, Any], request):
        """
        处理Webhook请求
        
        Args:
            data: 请求数据
            request: 请求对象
            
        Returns:
            Dict: 响应数据
        """
        try:
            # 处理信号
            success, message = await self.framework.process_signal(data)
            
            # 返回结果
            return {
                "success": success,
                "message": message
            }
        except Exception as e:
            self.logger.exception(f"处理webhook异常: {e}")
            return {
                "success": False,
                "message": f"处理异常: {e}"
            }
    
    async def start_http_server(self):
        """启动HTTP服务器和注册API路由"""
        self.logger.info("启动HTTP服务器...")
        
        # 检查是否有HTTP服务器配置
        if not hasattr(self, '_use_new_http_server'):
            self.logger.info("HTTP服务器未初始化，跳过启动")
            return
            
        # 选择HTTP服务器模式
        if not self._use_new_http_server:
            # 使用传统HTTP服务器
            try:
                # 创建API处理器并注册路由
                api_handler = TradingFrameworkApiHandler(self.framework, self.app_name)
                api_handler.register_routes(self.http_server.app, self.base_path)
                
                # 添加CORS支持
                cors = setup_cors(self.http_server.app, defaults={
                    "*": ResourceOptions(
                        allow_credentials=True,
                        expose_headers="*",
                        allow_headers="*",
                        allow_methods=["GET", "POST", "OPTIONS"]
                    )
                })
                
                # 添加一个直接的测试路由
                async def test_handler(request):
                    self.logger.warning(f"测试路由收到请求: {request.method} {request.url}")
                    return web.json_response({"status": "ok", "message": "测试路由正常"})
                
                self.http_server.app.router.add_get("/test", test_handler)
                self.http_server.app.router.add_post("/test", test_handler)
                
                # 为所有路由添加CORS支持
                for route in list(self.http_server.app.router.routes()):
                    cors.add(route)
                    
                # 启动HTTP服务器
                await self.http_server.start()
                self.logger.info(f"HTTP服务器启动完成 (传统模式)，访问地址: http://{self.host}:{self.port}")
                
                # 打印所有已注册的路由
                self.logger.warning("已注册的路由:")
                for route in list(self.http_server.app.router.routes()):
                    self.logger.warning(f" - {route.method} {route.get_info()}")
            except Exception as e:
                self.logger.exception(f"启动传统HTTP服务器异常: {e}")
        else:
            # 使用新HTTP服务器
            try:
                # 创建API处理器
                api_handler = TradingFrameworkApiHandler(self.framework, self.app_name)
                
                # 准备路由列表
                routes = []
                
                # webhook接口
                webhook_handler = TradingViewWebhook(self.framework)
                # 添加Webhook路由
                webhook_path = f"{self.base_path}" if self.base_path.endswith('/') else f"{self.base_path}/"
                routes.append(('POST', webhook_path, webhook_handler.handle_webhook))
                
                # 同时注册不带斜杠的路由
                webhook_path_no_slash = self.base_path.rstrip('/')
                routes.append(('POST', webhook_path_no_slash, webhook_handler.handle_webhook))
                self.logger.warning(f"同时注册两个webhook路径: POST {webhook_path} 和 POST {webhook_path_no_slash}")
                
                # 获取API路由
                for route in api_handler.get_routes():
                    routes.append(route)
                
                # 获取静态文件目录
                static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 
                                        "src", "common", "http", "static")
                
                # 创建允许的CORS来源列表
                cors_origins = ["*"]
                
                # 从module导入run_http_server
                from src.common.http.server import run_http_server
                self.logger.warning("添加测试路由: GET/POST /test")
                
                # 启动HTTP服务器
                self.http_task = asyncio.ensure_future(
                    run_http_server(
                        host=self.host,
                        port=self.port,
                        routes=routes,
                        static_dir=static_dir,
                        static_path="/static",
                        cors_origins=cors_origins,
                        logger=self.logger
                    )
                )
                
                self.logger.info(f"HTTP服务器启动完成 (新模式)，访问地址: http://{self.host}:{self.port}")
                self.logger.info(f"仓位管理页面可通过访问: http://{self.host}:{self.port}/positions")
                
            except Exception as e:
                self.logger.exception(f"启动新HTTP服务器异常: {e}")
    
    def _generate_api_scripts(self):
        """生成API脚本"""
        # 检查脚本目录
        scripts_dir = os.path.join(os.path.dirname(__file__), 'scripts')
        os.makedirs(scripts_dir, exist_ok=True)
        
        # 生成脚本
        try:
            # 获取端口
            port = str(self.port)
            # 获取基础路径
            base_path = self.base_path
            
            # 生成脚本
            scripts = generate_api_scripts(scripts_dir, self.app_name, port, base_path)
            
            # 设置执行权限
            for script_path in scripts.values():
                os.chmod(script_path, 0o755)
                
            self.logger.info(f"生成API脚本成功，位于: {scripts_dir}")
        except Exception as e:
            self.logger.error(f"生成API脚本失败: {e}")
    
    def _register_signal_handlers(self):
        """注册系统信号处理函数"""
        # 注册SIGINT和SIGTERM信号处理
        for sig in [signal.SIGINT, signal.SIGTERM]:
            signal.signal(sig, self._handle_shutdown)
    
    def _handle_shutdown(self, signum, frame):
        """处理系统关闭信号"""
        self.logger.info(f"接收到关闭信号: {signum}")
        self.stop()
    
    async def _shutdown(self):
        """关闭应用"""
        self.logger.info("正在关闭应用...")
        
        # 关闭HTTP服务器
        try:
            if hasattr(self, '_use_new_http_server') and self._use_new_http_server:
                if hasattr(self, 'http_task') and self.http_task:
                    self.http_task.cancel()
                    self.logger.info("HTTP服务器已关闭 (新模式)")
            else:
                if hasattr(self, 'http_server') and self.http_server:
                    await self.http_server.stop()
                    self.logger.info("HTTP服务器已关闭 (传统模式)")
        except Exception as e:
            self.logger.error(f"关闭HTTP服务器异常: {e}")
        
        self.logger.info("应用已关闭")
    
    async def _run_framework_forever(self):
        """运行框架监控任务的包装函数"""
        await self.framework.run_forever(position_monitor_interval=15)
    
    def _setup_tasks(self):
        """设置异步任务"""
        # 启动HTTP服务器
        self.event_loop.add_task(self.start_http_server())
        
        # 添加策略监控任务
        self.event_loop.add_task(self._run_framework_forever())
    
    def run(self):
        """运行应用"""
        self.logger.info("启动应用...")
        self._setup_tasks()
        self.event_loop.run_forever()
    
    def stop(self):
        """停止应用"""
        self.logger.info("正在停止应用...")
        
        # 添加关闭任务
        self.event_loop.add_task(
            self._shutdown,
            immediate=True
        )
        
        # 停止事件循环
        self.event_loop.stop()

if __name__ == "__main__":
    main() 