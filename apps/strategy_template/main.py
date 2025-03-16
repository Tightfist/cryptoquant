#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
策略模板的主入口

功能：
1. 启动WebSocket服务器，接收信号
2. 初始化并运行策略框架
3. 提供API接口用于手动干预
"""

import os
import sys
import asyncio
import logging
import signal
from pathlib import Path
from typing import Dict, Any
import json

# 添加项目根目录到PYTHONPATH
sys.path.append(str(Path(__file__).parents[2]))

from src.common.logger import configure_logger
from src.common.event_loop import AsyncEventLoop
from src.common.config_loader import get_app_config
from src.common.http.server import HttpServer
from src.common.trading_framework import TradingFramework, TradeSignal

from apps.strategy_template.example_strategy import ExampleStrategy

# 应用名称
APP_NAME = "strategy_template"

def main():
    """主函数"""
    try:
        # 使用统一的配置加载函数
        config = get_app_config(APP_NAME)
        
        # 创建并运行应用
        app = StrategyApp(config)
        app.run()
    except KeyboardInterrupt:
        print("用户中断，程序退出")
    except Exception as e:
        print(f"程序异常: {e}")
        import traceback
        traceback.print_exc()

class StrategyApp:
    """策略应用"""
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
            strategy_class=ExampleStrategy,
            config=config
        )
        
        # 初始化HTTP服务器
        self._init_http_server()
        
        # 注册信号处理
        self._register_signal_handlers()
        
        self.logger.info(f"{self.app_name}初始化完成")
    
    def _init_http_server(self):
        """初始化HTTP服务器"""
        # 从配置中获取HTTP服务器设置
        api_config = self.config.get('api', {})
        self.port = api_config.get('port', 8080)
        self.host = api_config.get('host', '0.0.0.0')
        
        # 注册HTTP路由
        routes = {
            '/webhook': self._handle_webhook,
            '/api/trigger': self._handle_api_trigger,
            '/api/close_all': self._handle_api_close_all,
            '/api/status': self._handle_api_status
        }
        
        # 初始化HTTP服务器
        self.http_server = HttpServer(
            port=self.port,
            handler=None,
            host=self.host,
            routes=routes
        )
        
        self.logger.info(f"HTTP服务器初始化完成, 监听地址: {self.host}:{self.port}")
    
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
    
    async def _handle_api_trigger(self, request):
        """
        处理手动触发API请求
        
        Args:
            request: 请求对象
            
        Returns:
            web.Response: HTTP响应
        """
        try:
            # 从请求中获取JSON数据
            data = await request.json()
            
            # 检查必须的字段
            if 'action' not in data or 'symbol' not in data:
                return {
                    "success": False,
                    "message": "缺少必要字段: action, symbol"
                }
            
            # 创建交易信号
            signal = TradeSignal(
                action=data['action'],
                symbol=data['symbol'],
                direction=data.get('direction'),
                entry_price=data.get('entry_price'),
                quantity=data.get('quantity'),
                take_profit_pct=data.get('take_profit_pct'),
                stop_loss_pct=data.get('stop_loss_pct'),
                trailing_stop=data.get('trailing_stop'),
                trailing_distance=data.get('trailing_distance'),
                leverage=data.get('leverage'),
                unit_type=data.get('unit_type')
            )
            
            # 处理信号
            success, message = await self.framework.manual_trigger(signal)
            
            # 返回结果
            return {
                "success": success,
                "message": message
            }
        except Exception as e:
            self.logger.exception(f"处理API触发异常: {e}")
            return {
                "success": False,
                "message": f"处理异常: {e}"
            }
    
    async def _handle_api_close_all(self, request):
        """
        处理关闭所有持仓API请求
        
        Args:
            request: 请求对象
            
        Returns:
            Dict: 响应数据
        """
        try:
            # 关闭所有持仓
            success, message = await self.framework.manual_close_all()
            
            # 返回结果
            return {
                "success": success,
                "message": message
            }
        except Exception as e:
            self.logger.exception(f"处理关闭所有持仓API异常: {e}")
            return {
                "success": False,
                "message": f"处理异常: {e}"
            }
    
    async def _handle_api_status(self, request):
        """
        处理状态查询API请求
        
        Args:
            request: 请求对象
            
        Returns:
            Dict: 响应数据
        """
        try:
            # 获取状态
            status = await self.framework.get_status()
            
            # 返回结果
            return {
                "success": True,
                "data": status
            }
        except Exception as e:
            self.logger.exception(f"处理状态查询API异常: {e}")
            return {
                "success": False,
                "message": f"处理异常: {e}"
            }
    
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
    
    def _setup_tasks(self):
        """设置定时任务"""
        # 添加HTTP服务器启动任务
        self.event_loop.add_task(
            self.http_server.start,
            immediate=True
        )
        
        # 添加策略监控任务
        self.event_loop.add_task(
            self.framework.run_forever,
            immediate=True,
            kwargs={"position_monitor_interval": 30}
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