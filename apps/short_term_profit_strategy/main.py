#!/usr/bin/env python3
"""
短线逐利策略的主入口

该模块负责加载配置、初始化策略并启动交易框架。
"""

import os
import sys
import json
import logging
import asyncio
import signal
from typing import Dict, Any

# 添加项目根目录到Python路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from src.common.logger import configure_logger
from src.common.trading_framework import TradingFramework
from src.common.config_loader import get_app_config
from src.common.event_loop import AsyncEventLoop
from src.common.http.server import HttpServer
from src.common.http.api_handlers.trading_framework_api import TradingFrameworkApiHandler
from apps.short_term_profit_strategy.short_term_profit_strategy import ShortTermProfitStrategy

# 应用名称
APP_NAME = "short_term_profit_strategy"

def main():
    """主函数"""
    try:
        # 加载配置
        config = get_app_config(APP_NAME)
        
        # 创建并运行应用
        app = ShortTermProfitApp(config)
        app.run()
    except KeyboardInterrupt:
        print("用户中断，程序退出")
    except Exception as e:
        print(f"程序异常: {e}")
        import traceback
        traceback.print_exc()

class ShortTermProfitApp:
    """短线逐利策略应用"""
    
    def __init__(self, config: Dict[str, Any]):
        """
        初始化应用
        
        Args:
            config: 配置信息
        """
        self.config = config
        self.app_name = config.get('app_name', APP_NAME)
        
        # 配置日志
        configure_logger(self.app_name)
        self.logger = logging.getLogger(self.app_name)
        self.logger.info("初始化短线逐利策略应用")
        
        # 创建事件循环
        self.event_loop = AsyncEventLoop()
        
        # 初始化框架
        self.framework = TradingFramework(
            app_name=self.app_name,
            strategy_class=ShortTermProfitStrategy,
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
        http_config = self.config.get('http_server', {})
        self.enable_http = http_config.get('enabled', False)
        
        if not self.enable_http:
            self.logger.info("HTTP服务器已禁用")
            return
            
        self.port = http_config.get('port', 8080)
        self.host = http_config.get('host', '0.0.0.0')
        
        # 初始化HTTP服务器 - 只设置Webhook处理
        self.http_server = HttpServer(
            port=self.port,
            message_handler=self._handle_webhook,
            host=self.host,
            path="/webhook"
        )
        
        # 创建API处理器并注册通用API路由
        self.api_handler = TradingFrameworkApiHandler(self.framework, self.app_name)
        self.api_handler.register_routes(self.http_server.app)
        
        # 注册自定义路由 - 使用专用的factor_analysis路径
        self.http_server.app.router.add_post("/api/factor_analysis", self._handle_factor_analysis)
        
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
    
    async def _handle_factor_analysis(self, request):
        """
        处理因子分析请求
        
        Args:
            request: HTTP请求对象
            
        Returns:
            HTTP响应
        """
        try:
            # 解析请求数据
            data = await request.json()
            self.logger.info(f"收到因子分析请求: {data}")
            
            # 获取top_n参数和max_symbols参数
            top_n = data.get('top_n', 10)
            max_symbols = data.get('max_symbols', 20)
            
            # 执行因子分析
            try:
                result = await self.framework.strategy._analyze_factors(top_n, max_symbols)
                
                # 返回响应
                from aiohttp import web
                return web.json_response({
                    "success": True,
                    "data": result
                })
            except Exception as e:
                self.logger.exception(f"执行因子分析异常: {e}")
                from aiohttp import web
                return web.json_response({
                    "success": False,
                    "message": f"执行因子分析异常: {e}"
                }, status=500)
                
        except json.JSONDecodeError:
            from aiohttp import web
            return web.json_response({"success": False, "message": "无效的JSON数据"}, 400)
        except Exception as e:
            self.logger.exception(f"处理因子分析请求异常: {e}")
            from aiohttp import web
            return web.json_response({"success": False, "message": str(e)}, 500)
    
    async def _handle_api_trigger(self, request):
        """
        处理API触发请求
        
        Args:
            request: HTTP请求对象
            
        Returns:
            HTTP响应
        """
        try:
            # 解析请求数据
            data = await request.json()
            self.logger.info(f"收到API触发请求: {data}")
            
            # 检查是否为因子分析请求
            if data.get('type') == 'factor_analysis':
                # 获取排名前多少
                top_n = data.get('top_n', 10)
                
                # 执行因子分析
                try:
                    result = await self.framework.strategy._analyze_factors(top_n)
                    
                    # 返回响应
                    from aiohttp import web
                    return web.json_response({
                        "success": True,
                        "data": result
                    })
                except Exception as e:
                    self.logger.exception(f"执行因子分析异常: {e}")
                    from aiohttp import web
                    return web.json_response({
                        "success": False,
                        "message": f"执行因子分析异常: {e}"
                    }, status=500)
            
            # 常规请求，处理信号
            success, message = await self.framework.process_signal(data)
            
            # 返回响应
            from aiohttp import web
            status_code = 200 if success else 400
            response = {
                "success": success,
                "message": message
            }
            
            return web.json_response(response, status=status_code)
            
        except json.JSONDecodeError:
            from aiohttp import web
            return web.json_response({"success": False, "message": "无效的JSON数据"}, 400)
        except Exception as e:
            self.logger.exception(f"处理API触发请求异常: {e}")
            from aiohttp import web
            return web.json_response({"success": False, "message": str(e)}, 500)
    
    async def _handle_api_close_all(self, request):
        """
        处理平仓所有持仓请求
        
        Args:
            request: HTTP请求对象
            
        Returns:
            HTTP响应
        """
        try:
            self.logger.info("收到平仓所有持仓请求")
            
            # 执行平仓
            success, message = await self.framework.manual_close_all()
            
            # 返回响应
            status_code = 200 if success else 400
            response = {
                "success": success,
                "message": message
            }
            
            return self._json_response(response, status_code)
        except Exception as e:
            self.logger.exception(f"处理平仓所有持仓请求异常: {e}")
            return self._json_response({"success": False, "message": str(e)}, 500)
    
    async def _handle_api_status(self, request):
        """
        处理获取状态请求
        
        Args:
            request: HTTP请求对象
            
        Returns:
            HTTP响应
        """
        try:
            self.logger.info("收到获取状态请求")
            
            # 获取状态
            status = await self.framework.get_status()
            
            # 返回响应
            return self._json_response({"success": True, "data": status}, 200)
        except Exception as e:
            self.logger.exception(f"处理获取状态请求异常: {e}")
            return self._json_response({"success": False, "message": str(e)}, 500)
    
    def _json_response(self, data, status=200):
        """
        创建JSON响应
        
        Args:
            data: 响应数据
            status: 状态码
            
        Returns:
            HTTP响应
        """
        import aiohttp.web
        return aiohttp.web.json_response(data, status=status)
    
    def _register_signal_handlers(self):
        """注册信号处理器"""
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)
    
    def _handle_shutdown(self, signum, frame):
        """处理关闭信号"""
        self.logger.info(f"收到信号 {signum}，准备关闭...")
        self.stop()
    
    async def _shutdown(self):
        """关闭框架"""
        self.logger.info("正在关闭框架...")
        try:
            # 停止策略监控
            await self.framework.strategy.stop_monitoring()
            self.logger.info("策略监控已停止")
            
            # 如果有HTTP服务器，停止它
            if hasattr(self, 'http_server'):
                self.logger.info("正在停止HTTP服务器...")
                await self.http_server.stop()
                self.logger.info("HTTP服务器已停止")
                
        except Exception as e:
            self.logger.error(f"关闭框架时发生异常: {e}")
    
    async def _run_framework_forever(self):
        """运行框架主循环"""
        # 启动市场监控
        await self.framework.strategy.start_monitoring()
        
        # 如果启用了HTTP服务器，启动它
        if hasattr(self, 'http_server'):
            self.logger.info("启动HTTP服务器...")
            await self.http_server.start()
            self.logger.info(f"HTTP服务器已启动，监听地址: {self.host}:{self.port}")
        
        # 启动框架主循环
        await self.framework.run_forever()
    
    def _setup_tasks(self):
        """设置任务"""
        # 添加框架运行任务
        self.event_loop.add_task(
            self._run_framework_forever,
            immediate=True
        )
    
    def run(self):
        """运行应用"""
        self.logger.info("启动短线逐利策略应用...")
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