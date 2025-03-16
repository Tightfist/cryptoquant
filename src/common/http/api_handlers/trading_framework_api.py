"""
交易框架通用API处理器

提供用于交易框架的通用HTTP API端点处理，包括：
1. /api/trigger - 触发交易信号
2. /api/close_all - 关闭所有持仓
3. /api/status - 获取框架状态
"""

import logging
import json
from typing import Dict, Any, Callable, Awaitable
from aiohttp import web
from src.common.trading_framework import TradingFramework, TradeSignal

class TradingFrameworkApiHandler:
    """交易框架API处理器，提供通用的API端点实现"""
    
    def __init__(self, framework: TradingFramework, app_name: str):
        """
        初始化API处理器
        
        Args:
            framework: 交易框架实例
            app_name: 应用名称，用于日志记录
        """
        self.framework = framework
        self.app_name = app_name
        self.logger = logging.getLogger(app_name)
    
    async def handle_api_trigger(self, request: web.Request) -> web.Response:
        """
        处理手动触发API请求
        
        Args:
            request: HTTP请求对象
            
        Returns:
            web.Response: HTTP响应
        """
        try:
            data = await request.json()
            
            # 检查必须的字段
            if 'action' not in data or 'symbol' not in data:
                return web.json_response({
                    "success": False,
                    "message": "缺少必要字段: action, symbol"
                }, status=400)
            
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
            return web.json_response({
                "success": success,
                "message": message
            })
            
        except json.JSONDecodeError:
            self.logger.error("无效的JSON格式")
            return web.json_response(
                {"success": False, "message": "Invalid JSON format"},
                status=400
            )
        except Exception as e:
            self.logger.exception(f"处理API触发异常: {e}")
            return web.json_response(
                {"success": False, "message": f"Error processing API trigger: {str(e)}"},
                status=500
            )
    
    async def handle_api_close_all(self, request: web.Request) -> web.Response:
        """
        处理关闭所有持仓API请求
        
        Args:
            request: HTTP请求对象
            
        Returns:
            web.Response: HTTP响应
        """
        try:
            # 关闭所有持仓
            success, message = await self.framework.manual_close_all()
            
            # 返回结果
            return web.json_response({
                "success": success,
                "message": message
            })
        except Exception as e:
            self.logger.exception(f"处理关闭所有持仓API异常: {e}")
            return web.json_response(
                {"success": False, "message": f"处理异常: {e}"},
                status=500
            )
    
    async def handle_api_status(self, request: web.Request) -> web.Response:
        """
        处理状态查询API请求
        
        Args:
            request: HTTP请求对象
            
        Returns:
            web.Response: HTTP响应
        """
        try:
            # 获取状态
            status = await self.framework.get_status()
            
            # 返回结果
            return web.json_response({
                "success": True,
                "data": status
            })
        except Exception as e:
            self.logger.exception(f"处理状态查询API异常: {e}")
            return web.json_response(
                {"success": False, "message": f"处理异常: {e}"},
                status=500
            )
    
    def register_routes(self, app: web.Application, base_path: str = ""):
        """
        向web应用注册API路由
        
        Args:
            app: web应用实例
            base_path: 基础路径，例如 "/webhook"
        """
        # 确保基础路径的格式正确
        if base_path and not base_path.startswith('/'):
            base_path = f"/{base_path}"
        
        # 构建API路径
        trigger_path = f"{base_path}/api/trigger" if base_path else "/api/trigger"
        close_all_path = f"{base_path}/api/close_all" if base_path else "/api/close_all"
        status_path = f"{base_path}/api/status" if base_path else "/api/status"
        
        # 注册路由
        app.router.add_post(trigger_path, self.handle_api_trigger)
        app.router.add_post(close_all_path, self.handle_api_close_all)
        app.router.add_get(status_path, self.handle_api_status)
        
        self.logger.info(f"已注册交易框架API路由: {trigger_path}, {close_all_path}, {status_path}")
        
        return [trigger_path, close_all_path, status_path] 