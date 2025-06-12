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
            # 解析请求数据
            data = await request.json()
            action = data.get("action", "close")
            
            if action == "sync_only":
                # 仅同步持仓
                if hasattr(self.framework, "position_mgr") and hasattr(self.framework.position_mgr, "sync_positions_from_api"):
                    await self.framework.position_mgr.sync_positions_from_api()
                    return web.json_response({
                        "status": "success",
                        "message": "持仓同步成功"
                    })
                else:
                    return web.json_response({
                        "status": "error",
                        "message": "系统不支持持仓同步功能"
                    })
            else:
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
            # 获取查询参数
            params = request.query
            start_date = params.get('start_date')
            end_date = params.get('end_date')
            
            # 获取每日收益数据
            daily_pnl = await self.framework.get_daily_pnl(start_date, end_date)
            
            # 计算今日总收益和胜率
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            today_pnl = 0
            win_positions = 0
            total_positions = 0
            
            for day_data in daily_pnl:
                if day_data.get('date') == today:
                    today_pnl = day_data.get('pnl', 0)
                    break
            
            # 获取今日胜率
            # 获取当天结束的仓位
            today_datetime = datetime.datetime.strptime(today, "%Y-%m-%d")
            today_timestamp = int(today_datetime.timestamp() * 1000)
            tomorrow_timestamp = int((today_datetime + datetime.timedelta(days=1)).timestamp() * 1000)
            
            # 简单计算胜率
            positions = await self.framework.get_position_history(today, today)
            for position in positions:
                if position.get('exit_timestamp', 0) >= today_timestamp and position.get('exit_timestamp', 0) < tomorrow_timestamp:
                    total_positions += 1
                    if position.get('pnl_amount', 0) > 0:
                        win_positions += 1
            
            win_rate = 0 if total_positions == 0 else (win_positions / total_positions) * 100
            
            # 返回结果
            return web.json_response({
                "success": True,
                "data": {
                    "daily_pnl": daily_pnl, 
                    "today_pnl": today_pnl,
                    "win_rate": win_rate,
                    "total_positions": total_positions
                }
            })
        except Exception as e:
            self.logger.exception(f"处理每日收益查询API异常: {e}")
            return web.json_response(
                {"success": False, "message": f"处理异常: {e}"},
                status=500
            )
    
    async def handle_api_btc_price_today(self, request: web.Request) -> web.Response:
        """
        处理获取BTC今日价格变化API请求，用于收益曲线图表显示
        
        Args:
            request: HTTP请求对象
            
        Returns:
            web.Response: HTTP响应，包含今日每个小时的BTC价格数据
        """
        try:
            # 获取BTC的K线数据
            # 指定24根1小时K线数据，覆盖整个交易日
            klines_result = await self.framework.strategy.data_cache.get_klines("BTC-USDT-SWAP", "1H", 24)
            
            if not klines_result or 'data' not in klines_result or not klines_result['data']:
                return web.json_response({
                    "success": False,
                    "message": "无法获取BTC价格数据"
                }, status=500)
            
            # 获取今日开始时间（0点）
            today = datetime.datetime.now()
            today = datetime.datetime(today.year, today.month, today.day, 0, 0, 0)
            today_timestamp = int(today.timestamp() * 1000)  # 转为毫秒
            
            # 从K线数据中筛选今日的数据
            klines_data = klines_result['data']
            today_klines = []
            
            for kline in klines_data:
                kline_timestamp = int(kline[0])  # K线的时间戳
                if kline_timestamp >= today_timestamp:
                    today_klines.append({
                        "timestamp": kline_timestamp,
                        "price": float(kline[4])  # 使用收盘价
                    })
            
            # 如果没有今日数据，获取最新价格
            if not today_klines:
                mark_price = await self.framework.strategy.data_cache.get_mark_price("BTC-USDT-SWAP")
                now_timestamp = int(datetime.datetime.now().timestamp() * 1000)
                today_klines = [{
                    "timestamp": today_timestamp,
                    "price": mark_price
                }, {
                    "timestamp": now_timestamp,
                    "price": mark_price
                }]
            
            # 确保有当天开始时间的价格点
            # 检查是否已有0点数据，如果没有，添加一个
            has_start_time = False
            for kline in today_klines:
                if abs(kline["timestamp"] - today_timestamp) < 3600000:  # 1小时内
                    has_start_time = True
                    break
            
            if not has_start_time and today_klines:
                # 获取最早的价格点作为0点价格
                earliest_price = today_klines[0]["price"]
                today_klines.insert(0, {
                    "timestamp": today_timestamp,
                    "price": earliest_price
                })
            
            # 按时间排序
            today_klines.sort(key=lambda x: x["timestamp"])
            
            return web.json_response({
                "success": True,
                "data": today_klines
            })
        except Exception as e:
            self.logger.exception(f"处理BTC价格查询API异常: {e}")
            return web.json_response({
                "success": False,
                "message": f"处理异常: {e}"
            }, status=500)
    
    async def handle_api_position_history(self, request: web.Request) -> web.Response:
        """
        处理仓位历史查询API请求
        
        Args:
            request: HTTP请求对象
            
        Returns:
            web.Response: HTTP响应
        """
        try:
            # 获取请求信息
            request_id = id(request)
            
            # 获取查询参数
            params = request.query
            start_date = params.get('start_date')
            end_date = params.get('end_date')
            symbol = params.get('symbol')
            limit_str = params.get('limit')
            
            # 如果没有指定日期，使用今天的日期
            if not start_date or not end_date:
                today = datetime.datetime.now().strftime("%Y-%m-%d")
                start_date = start_date or today
                end_date = end_date or today
            
            # 处理limit参数：如果用户指定了limit则使用，否则不限制
            limit = None
            if limit_str:
                try:
                    limit = int(limit_str)
                except ValueError:
                    limit = None
            
            # 记录查询参数
            self.logger.info(f"[请求ID:{request_id}] 历史仓位查询: start_date={start_date}, end_date={end_date}, symbol={symbol}, limit={limit}")
            
            # 执行实际查询
            position_history = await self.framework.get_position_history(
                start_date, end_date, symbol, limit
            )
            
            if position_history is None:
                position_history = []
            
            # 返回结果
            response_data = {
                "success": True,
                "data": position_history,
                "timestamp": int(datetime.datetime.now().timestamp()),
                "count": len(position_history)
            }
            self.logger.info(f"[请求ID:{request_id}] 历史仓位响应: count={len(position_history)}")
            return web.json_response(response_data)
        except Exception as e:
            request_id = id(request) if hasattr(request, 'id') else 'unknown'
            self.logger.exception(f"[请求ID:{request_id}] 处理仓位历史查询API异常: {e}")
            return web.json_response(
                {"success": False, "message": f"处理异常: {e}", "timestamp": int(datetime.datetime.now().timestamp()), "data": []},
                status=200  # 即使出错也返回200状态码而不是500，让前端能正常处理
            )
    
    async def handle_api_open_positions(self, request: web.Request) -> web.Response:
        """
        处理查询当前持仓的API请求
        
        Args:
            request: HTTP请求对象
        
        Returns:
            web.Response: JSON响应，包含当前持仓信息
        """
        try:
            params = request.query_string if request.query_string else "无参数"
            self.logger.info(f"当前持仓查询: 参数={params}")
            
            # 获取持仓摘要信息
            positions_summary = self.framework.strategy.get_position_summary()
            
            # 简明打印持仓摘要
            positions_count = len(positions_summary.get('positions', []))
            
            response_data = {
                "status": "success",
                "data": positions_summary,
                "message": f"当前持有 {positions_count} 个持仓"
            }
            self.logger.info(f"当前持仓响应: count={positions_count}")
            return web.json_response(response_data)
        except Exception as e:
            self.logger.error(f"处理当前持仓查询请求异常: {e}")
            return web.json_response({
                "status": "error",
                "message": f"查询当前持仓失败: {str(e)}"
            }, status=500)
    
    async def handle_api_positions(self, request: web.Request) -> web.Response:
        """
        处理获取详细持仓数据的API请求
        
        Args:
            request: HTTP请求对象
        
        Returns:
            web.Response: JSON响应，包含详细持仓数据
        """
        try:
            self.logger.info(f"开始处理获取详细持仓数据请求 - 请求路径: {request.url}, 查询参数: {request.query_string}")
            
            # 获取查询参数
            params = request.query
            include_closed = params.get('include_closed', '0') == '1'
            symbol = params.get('symbol', None)
            
            self.logger.info(f"持仓查询参数: include_closed={include_closed}, symbol={symbol}")
            
            # 检查position_mgr是否存在并可访问
            if not hasattr(self.framework, 'position_mgr'):
                self.logger.error("framework对象缺少position_mgr属性!")
                return web.json_response({
                    "status": "error",
                    "message": "系统配置错误: 持仓管理器未初始化"
                }, status=500)
            
            # 从框架中获取详细持仓数据
            if hasattr(self.framework.position_mgr, 'load_positions'):
                self.logger.info("开始调用position_mgr.load_positions获取持仓数据...")
                positions = self.framework.position_mgr.load_positions(
                    include_closed=include_closed,
                    symbol=symbol,
                    dict_format=False
                )
                
                self.logger.info(f"从数据库加载到 {len(positions) if positions else 0} 条持仓记录")
                
                # 将Position对象转换为可序列化的字典
                positions_data = []
                for pos in positions:
                    pos_dict = {k: v for k, v in vars(pos).items() if k != 'signal'}
                    # 添加其他需要的计算字段
                    if not pos.closed and hasattr(self.framework.trader, 'get_mark_price'):
                        try:
                            self.logger.info(f"获取 {pos.symbol} 的最新标记价格...")
                            current_price = self.framework.trader.get_mark_price(pos.symbol)
                            if current_price and pos.entry_price:
                                if pos.direction == 'long':
                                    unrealized_pnl_pct = (current_price - pos.entry_price) / pos.entry_price
                                else:
                                    unrealized_pnl_pct = (pos.entry_price - current_price) / pos.entry_price
                                pos_dict['current_price'] = current_price
                                pos_dict['unrealized_pnl_pct'] = unrealized_pnl_pct
                                self.logger.info(f"计算 {pos.symbol} 的未实现盈亏: 入场价={pos.entry_price}, 当前价={current_price}, 盈亏比例={unrealized_pnl_pct}")
                        except Exception as e:
                            self.logger.warning(f"计算持仓 {pos.symbol} 的未实现盈亏异常: {e}")
                    
                    positions_data.append(pos_dict)
                
                response_data = {
                    "status": "success",
                    "data": positions_data,
                    "count": len(positions_data),
                    "message": f"获取到 {len(positions_data)} 条持仓数据"
                }
                
                self.logger.info(f"返回详细持仓查询响应: status=success, count={len(positions_data)}")
                if len(positions_data) > 0:
                    # 只打印第一条记录的示例
                    self.logger.info(f"数据示例: {list(positions_data[0].keys())}")
                
                return web.json_response(response_data)
            else:
                self.logger.error("持仓管理器不支持load_positions方法!")
                return web.json_response({
                    "status": "error",
                    "message": "持仓管理器不支持获取详细持仓数据"
                }, status=400)
                
        except Exception as e:
            self.logger.error(f"处理详细持仓数据请求异常: {e}", exc_info=True)
            return web.json_response({
                "status": "error",
                "message": f"获取详细持仓数据失败: {str(e)}"
            }, status=500)
    
    async def handle_api_position_sync(self, request: web.Request) -> web.Response:
        """
        处理同步持仓数据的API请求
        
        Args:
            request: HTTP请求对象
        
        Returns:
            web.Response: JSON响应，包含同步结果
        """
        try:
            self.logger.info("处理同步持仓数据请求")
            
            # 获取请求体数据
            try:
                data = await request.json()
            except:
                data = {}
                
            # 获取需要同步的特定合约
            symbol = data.get('symbol', None)
            
            # 执行同步
            if hasattr(self.framework, 'sync_positions'):
                result = await self.framework.sync_positions()
                
                return web.json_response({
                    "status": "success" if result else "error",
                    "message": "持仓数据同步完成" if result else "持仓数据同步失败"
                })
            else:
                return web.json_response({
                    "status": "error",
                    "message": "交易框架不支持持仓数据同步"
                }, status=400)
                
        except Exception as e:
            self.logger.error(f"处理同步持仓数据请求异常: {e}", exc_info=True)
            return web.json_response({
                "status": "error",
                "message": f"同步持仓数据失败: {str(e)}"
            }, status=500)
    
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
        btc_price_path = f"{base_path}/api/btc_price_today" if base_path else "/api/btc_price_today"
        
        # 新增持仓相关API路径
        positions_path = f"{base_path}/api/data/positions" if base_path else "/api/data/positions"
        position_sync_path = f"{base_path}/api/action/sync_positions" if base_path else "/api/action/sync_positions"
        
        # 返回路由列表
        return [
            ('POST', trigger_path, self.handle_api_trigger),
            ('POST', close_all_path, self.handle_api_close_all),
            ('GET', status_path, self.handle_api_status),
            ('GET', daily_pnl_path, self.handle_api_daily_pnl),
            ('GET', position_history_path, self.handle_api_position_history),
            ('GET', open_positions_path, self.handle_api_open_positions),
            ('GET', btc_price_path, self.handle_api_btc_price_today),
            
            # 新增持仓相关路由
            ('GET', positions_path, self.handle_api_positions),
            ('POST', position_sync_path, self.handle_api_position_sync)
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