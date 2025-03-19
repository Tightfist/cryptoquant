"""
交易框架通用API处理器

提供用于交易框架的通用HTTP API端点处理，包括：
1. /api/trigger - 触发交易信号
2. /api/close_all - 关闭所有持仓
3. /api/status - 获取框架状态
"""

import logging
import json
from typing import Dict, Any, Callable, Awaitable, List, Tuple
from aiohttp import web
from src.common.trading_framework import TradingFramework, TradeSignal
import datetime

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
    
    async def handle_api_daily_pnl(self, request: web.Request) -> web.Response:
        """
        处理每日收益查询API请求
        
        Args:
            request: HTTP请求对象
            
        Returns:
            web.Response: HTTP响应
        """
        try:
            # 获取今日盈亏
            today_pnl = await self.framework.get_daily_pnl()
            
            # 计算胜率
            position_history = await self.framework.get_position_history()
            win_count = 0
            total_closed = 0
            
            for position in position_history:
                if position.get('closed', False) or position.get('exit_timestamp', 0) > 0:
                    total_closed += 1
                    if position.get('pnl_amount', 0) > 0:
                        win_count += 1
            
            win_rate = (win_count / total_closed * 100) if total_closed > 0 else 0
            
            # 当日盈亏总和
            today_total_pnl = 0
            if today_pnl:
                for day_data in today_pnl:
                    if day_data.get('date') == datetime.datetime.now().strftime('%Y-%m-%d'):
                        today_total_pnl = day_data.get('pnl', 0)
                        break
            
            return web.json_response({
                'success': True,
                'data': {
                    'today_pnl': today_total_pnl,
                    'win_rate': win_rate,
                    'win_count': win_count,
                    'total_closed': total_closed
                }
            })
        except Exception as e:
            self.logger.exception(f"获取每日盈亏和胜率数据异常: {str(e)}", exc_info=True)
            return web.json_response({
                'success': False,
                'message': f"获取每日盈亏和胜率数据异常: {str(e)}"
            })
    
    async def handle_api_position_history(self, request: web.Request) -> web.Response:
        """
        处理仓位历史查询API请求
        
        Args:
            request: HTTP请求对象
            
        Returns:
            web.Response: HTTP响应
        """
        try:
            # 获取查询参数
            params = request.query
            start_date = params.get('start_date')
            end_date = params.get('end_date')
            symbol = params.get('symbol')
            limit_str = params.get('limit', '100')
            
            # 转换limit为整数
            try:
                limit = int(limit_str)
            except ValueError:
                limit = 100
            
            # 添加调试日志
            self.logger.info(f"处理仓位历史查询API请求: start_date={start_date}, end_date={end_date}, symbol={symbol}, limit={limit}")
            
            # 获取仓位历史数据
            try:
                position_history = await self.framework.get_position_history(
                    start_date, end_date, symbol, limit
                )
                
                if position_history is None:
                    position_history = []
                    self.logger.warning("获取仓位历史返回None")
                
                self.logger.info(f"获取到 {len(position_history)} 条仓位历史记录")
                
                # 如果返回的列表为空，添加调试日志
                if not position_history:
                    self.logger.warning("仓位历史为空列表")
                
            except Exception as e:
                self.logger.exception(f"获取仓位历史发生异常: {e}")
                position_history = []
            
            # 返回结果
            return web.json_response({
                "success": True,
                "data": position_history
            })
        except Exception as e:
            self.logger.exception(f"处理仓位历史查询API异常: {e}")
            return web.json_response(
                {"success": False, "message": f"处理异常: {e}"},
                status=500
            )
    
    async def handle_api_open_positions(self, request: web.Request) -> web.Response:
        """
        处理未平仓仓位查询API请求
        
        Args:
            request: HTTP请求对象
            
        Returns:
            web.Response: HTTP响应
        """
        try:
            # 获取状态，其中包含未平仓仓位
            status = await self.framework.get_status()
            
            # 提取未平仓仓位
            positions_summary = status.get('positions', {})
            
            # 修复: 确保我们正确获取positions列表
            open_positions = positions_summary.get('positions', [])
            
            result_positions = []
            
            # 为每个仓位添加更多信息
            for pos in open_positions:
                if not isinstance(pos, dict):
                    self.logger.warning(f"无效的仓位数据类型: {type(pos)}, 值: {pos}")
                    continue
                
                # 获取当前价格和计算收益
                symbol = pos.get('symbol')
                if not symbol:
                    self.logger.warning(f"仓位缺少symbol: {pos}")
                    continue
                    
                entry_price = pos.get('entry_price', 0)
                leverage = pos.get('leverage', 1)
                direction = pos.get('direction', 'long')
                
                try:
                    # 获取当前价格
                    current_price = await self.framework.strategy.data_cache.get_mark_price(symbol)
                    
                    if current_price and entry_price:
                        # 计算盈亏
                        if direction == 'long':
                            pnl_pct = (current_price - entry_price) / entry_price
                        else:
                            pnl_pct = (entry_price - current_price) / entry_price
                        
                        # 计算杠杆收益
                        leveraged_pnl_pct = pnl_pct * leverage
                        
                        # 添加到响应中
                        pos['current_price'] = current_price
                        pos['pnl_pct'] = pnl_pct
                        pos['leveraged_pnl_pct'] = leveraged_pnl_pct
                        
                        # 从仓位对象获取阶梯止盈信息
                        if symbol in self.framework.strategy.positions:
                            position_obj = self.framework.strategy.positions[symbol]
                            ladder_tp = getattr(position_obj, 'ladder_tp', False)
                            ladder_tp_pct = getattr(position_obj, 'ladder_tp_pct', 0.2)
                            ladder_tp_step = getattr(position_obj, 'ladder_tp_step', 0.2)
                            ladder_closed_pct = getattr(position_obj, 'ladder_closed_pct', 0.0)
                            
                            # 添加调试日志
                            self.logger.info(f"阶梯止盈信息 {symbol}: 状态={ladder_tp}, 每档比例={ladder_tp_pct}, 档位间隔={ladder_tp_step}, 已平仓={ladder_closed_pct}")
                            
                            pos['ladder_tp'] = ladder_tp
                            pos['ladder_tp_pct'] = ladder_tp_pct
                            pos['ladder_tp_step'] = ladder_tp_step
                            pos['ladder_closed_pct'] = ladder_closed_pct
                        
                        # 获取止盈止损百分比
                        take_profit_pct = self.framework.strategy.take_profit_pct
                        stop_loss_pct = self.framework.strategy.stop_loss_pct
                        
                        # 如果仓位对象有信号，优先使用信号的止盈止损设置
                        if symbol in self.framework.strategy.positions:
                            position_obj = self.framework.strategy.positions[symbol]
                            signal = getattr(position_obj, 'signal', None)
                            if signal:
                                if hasattr(signal, 'take_profit_pct') and signal.take_profit_pct is not None:
                                    take_profit_pct = signal.take_profit_pct
                                if hasattr(signal, 'stop_loss_pct') and signal.stop_loss_pct is not None:
                                    stop_loss_pct = signal.stop_loss_pct
                        
                        # 计算止盈止损价格，注意杠杆影响
                        # 如果不是现货，杠杆影响止盈止损百分比
                        if pos.get('position_type', 'swap') != 'spot' and leverage > 1:
                            adjusted_tp_pct = take_profit_pct / leverage
                            adjusted_sl_pct = stop_loss_pct / leverage
                        else:
                            adjusted_tp_pct = take_profit_pct
                            adjusted_sl_pct = stop_loss_pct
                        
                        pos['take_profit_pct'] = adjusted_tp_pct
                        pos['stop_loss_pct'] = adjusted_sl_pct
                        
                        # 计算止盈止损价格
                        if direction == 'long':
                            pos['take_profit_price'] = entry_price * (1 + adjusted_tp_pct)
                            pos['stop_loss_price'] = entry_price * (1 - adjusted_sl_pct)
                        else:
                            pos['take_profit_price'] = entry_price * (1 - adjusted_tp_pct)
                            pos['stop_loss_price'] = entry_price * (1 + adjusted_sl_pct)
                        
                        # 添加合约面值和保证金信息，用于前端计算持仓价值
                        try:
                            # 获取合约面值
                            contract_size = self.framework.strategy.data_cache.get_contract_size_sync(symbol)
                            pos['contract_size'] = contract_size
                            
                            # 计算合约价值
                            quantity = abs(float(pos.get('quantity', 0)))
                            contract_value = quantity * entry_price * contract_size
                            
                            # 计算保证金
                            margin = contract_value / leverage
                            pos['margin'] = margin
                            
                            # 计算盈亏金额
                            pnl_amount = margin * leveraged_pnl_pct
                            pos['pnl_amount'] = pnl_amount
                            
                            self.logger.info(f"仓位盈亏 {symbol}: 保证金={margin:.2f}, 盈亏比例={leveraged_pnl_pct*100:.2f}%, 盈亏金额={pnl_amount:.2f} USDT")
                        except Exception as e:
                            self.logger.error(f"计算保证金和合约面值异常: {symbol}, {e}")
                    
                    result_positions.append(pos)
                except Exception as e:
                    self.logger.error(f"计算仓位收益异常: {symbol}, {e}")
            
            # 返回结果
            return web.json_response({
                "success": True,
                "data": result_positions
            })
        except Exception as e:
            self.logger.exception(f"处理未平仓仓位查询API异常: {e}")
            return web.json_response(
                {"success": False, "message": f"处理异常: {e}"},
                status=500
            )
    
    def get_routes(self, base_path: str = ""):
        """
        获取API路由列表
        
        Args:
            base_path: 基础路径，例如 "/webhook"
            
        Returns:
            List[Tuple]: 路由列表，格式为[(method, path, handler), ...]
        """
        # 确保基础路径的格式正确
        if base_path and not base_path.startswith('/'):
            base_path = f"/{base_path}"
        
        # 构建API路径
        trigger_path = f"{base_path}/api/trigger" if base_path else "/api/trigger"
        close_all_path = f"{base_path}/api/close_all" if base_path else "/api/close_all"
        status_path = f"{base_path}/api/status" if base_path else "/api/status"
        daily_pnl_path = f"{base_path}/api/daily_pnl" if base_path else "/api/daily_pnl"
        position_history_path = f"{base_path}/api/position_history" if base_path else "/api/position_history"
        open_positions_path = f"{base_path}/api/open_positions" if base_path else "/api/open_positions"
        
        # 返回路由列表
        return [
            ('POST', trigger_path, self.handle_api_trigger),
            ('POST', close_all_path, self.handle_api_close_all),
            ('GET', status_path, self.handle_api_status),
            ('GET', daily_pnl_path, self.handle_api_daily_pnl),
            ('GET', position_history_path, self.handle_api_position_history),
            ('GET', open_positions_path, self.handle_api_open_positions)
        ]
    
    def register_routes(self, app: web.Application, base_path: str = ""):
        """
        向web应用注册API路由
        
        Args:
            app: web应用实例
            base_path: 基础路径，例如 "/webhook"
        """
        if app is None:
            self.logger.warning("无法注册路由: app参数为None")
            return []
            
        # 获取路由列表
        routes = self.get_routes(base_path)
        
        # 注册路由
        for method, path, handler in routes:
            app.router.add_route(method, path, handler)
        
        route_paths = [path for _, path, _ in routes]
        self.logger.info(f"已注册交易框架API路由: {', '.join(route_paths)}")
        
        return route_paths 