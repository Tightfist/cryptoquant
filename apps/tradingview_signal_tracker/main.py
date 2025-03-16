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
from src.common.http.server import HttpServer
from src.common.trading_framework import TradingFramework, TradeSignal
from src.common.http.api_handlers import TradingFrameworkApiHandler
from src.common.scripts.generate_api_scripts import generate_api_scripts

from apps.tradingview_signal_tracker.trading_view_strategy import TradingViewStrategy

# 应用名称
APP_NAME = "tradingview_signal_tracker"

def main():
    """主函数"""
    try:
        # 使用统一的配置加载函数
        config = get_app_config(APP_NAME)

        # 创建并运行应用
        app = TradingViewSignalApp(config)
        app.run()
    except KeyboardInterrupt:
        print("用户中断，程序退出")
    except Exception as e:
        print(f"程序异常: {e}")
        import traceback
        traceback.print_exc()

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
        
        # 初始化事件循环
        self.event_loop = AsyncEventLoop()
        
        # 初始化框架
        self.framework = TradingFramework(
            app_name=self.app_name,
            strategy_class=TradingViewStrategy,
            config=config
        )
        
        # 初始化HTTP服务器
        self._init_http_server()
        
        # 注册信号处理
        self._register_signal_handlers()
        
        # 生成API脚本
        self._generate_api_scripts()
        
        self.logger.info(f"{self.app_name}初始化完成")
    
    def _init_http_server(self):
        """初始化HTTP服务器"""
        # 从配置中获取HTTP服务器设置
        webhook_config = self.config.get('webhook', {})
        self.port = webhook_config.get('port', 80)
        self.host = webhook_config.get('host', '0.0.0.0')
        self.path = webhook_config.get('path', '/webhook')
        
        # 正确初始化HTTP服务器
        self.http_server = HttpServer(
            port=self.port,
            message_handler=self._handle_webhook,
            host=self.host,
            path=self.path
        )
        
        self.logger.info(f"HTTP服务器初始化完成, 监听地址: {self.host}:{self.port}{self.path}")
    
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
            base_path = self.path
            
            # 生成脚本
            scripts = generate_api_scripts(scripts_dir, self.app_name, port, base_path)
            
            # 设置执行权限
            for script_path in scripts.values():
                os.chmod(script_path, 0o755)
                
            self.logger.info(f"生成API脚本成功，位于: {scripts_dir}")
        except Exception as e:
            self.logger.error(f"生成API脚本失败: {e}")
    
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
        """启动HTTP服务器和注册其他API路由"""
        self.logger.info("启动HTTP服务器...")
        
        # 创建API处理器并注册路由
        api_handler = TradingFrameworkApiHandler(self.framework, self.app_name)
        api_handler.register_routes(self.http_server.app, self.path)
        
        # 添加CORS支持
        cors = setup_cors(self.http_server.app, defaults={
            "*": ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
                allow_methods=["GET", "POST", "OPTIONS"]
            )
        })
        
        # 为所有路由添加CORS支持
        for route in list(self.http_server.app.router.routes()):
            cors.add(route)
            
        # 启动HTTP服务器
        await self.http_server.start()
    
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
            await self.http_server.stop()
        except Exception as e:
            self.logger.error(f"关闭HTTP服务器异常: {e}")
        
        self.logger.info("应用已关闭")
    
    async def _run_framework_forever(self):
        """运行框架监控任务的包装函数"""
        await self.framework.run_forever(position_monitor_interval=30)
    
    def _setup_tasks(self):
        """设置定时任务"""
        # 添加HTTP服务器启动任务
        self.event_loop.add_task(
            self.start_http_server,
            immediate=True
        )
        
        # 添加策略监控任务 - 使用包装函数替代kwargs参数
        self.event_loop.add_task(
            self._run_framework_forever,
            immediate=True
        )
    
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