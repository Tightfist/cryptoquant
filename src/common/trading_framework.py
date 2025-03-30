"""
交易框架抽象层

提供可扩展的交易策略框架，包含以下功能：
1. 统一的仓位管理
2. 自动止盈止损处理
3. 异常重启后的仓位恢复
4. 状态查询与手动干预接口
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Set, Tuple, Callable, Type
from dataclasses import dataclass
from enum import Enum, auto
from datetime import datetime, timezone

from src.common.position_manager import PositionManager, Position
from src.common.data_cache import OKExDataCache
from src.exchange.okex.trader import OKExTrader
from src.common.order_utils import calculate_order_size

# 导入市场数据订阅器
from src.common.market_subscriber import OKExMarketSubscriber
# 导入退出策略管理器
from src.common.exit_strategies import ExitStrategyManager, ExitSignal, ExitTriggerType


class StrategyStatus(str, Enum):
    """策略状态枚举"""
    IDLE = "IDLE"  # 空闲状态
    HANDLING_SIGNAL = "HANDLING_SIGNAL"  # 处理信号中
    OPENING_POSITION = "OPENING_POSITION"  # 开仓中
    CLOSING_POSITION = "CLOSING_POSITION"  # 平仓中
    MODIFYING_POSITION = "MODIFYING_POSITION"  # 修改仓位中
    MONITORING = "MONITORING"  # 监控中
    POSITION_OPENED = "POSITION_OPENED"  # 已开仓
    POSITION_CLOSED = "POSITION_CLOSED"  # 已平仓
    POSITION_MODIFIED = "POSITION_MODIFIED"  # 已修改仓位
    ERROR = "ERROR"  # 错误状态
    INITIALIZED = "INITIALIZED"  # 已初始化


@dataclass
class TradeSignal:
    """交易信号数据结构"""
    action: str  # open, close, modify, status
    symbol: str
    direction: Optional[str] = None  # long, short
    entry_price: Optional[float] = None
    quantity: Optional[float] = None
    take_profit_pct: Optional[float] = None  # 止盈百分比
    stop_loss_pct: Optional[float] = None  # 止损百分比
    trailing_stop: Optional[bool] = None  # 是否启用追踪止损
    trailing_distance: Optional[float] = None  # 追踪止损距离
    leverage: Optional[int] = None  # 杠杆倍数
    unit_type: Optional[str] = None  # quote, base, contract
    position_id: Optional[str] = None  # 仓位ID
    extra_data: Optional[Dict[str, Any]] = None  # 额外数据


class BaseStrategy(ABC):
    """交易策略基类"""
    
    def __init__(self, app_name: str, trader: OKExTrader, 
                 position_mgr: PositionManager, 
                 data_cache: OKExDataCache, 
                 config: Dict[str, Any],
                 market_subscriber: OKExMarketSubscriber):
        """
        初始化策略基类
        
        Args:
            app_name: 应用名称
            trader: 交易执行器
            position_mgr: 仓位管理器
            data_cache: 数据缓存
            config: 配置信息
        """
        self.app_name = app_name
        self.trader = trader
        self.position_mgr = position_mgr
        self.data_cache = data_cache
        self.config = config
        self.market_subscriber = market_subscriber
        
        # 获取策略配置
        self.strategy_config = config.get('strategy', {})
        
        # 默认杠杆和每个仓位的USDT数量
        self.leverage = self.strategy_config.get('leverage', 3)
        self.per_position_usdt = self.strategy_config.get('per_position_usdt', 100)
        
        # 止盈止损配置
        self.take_profit_pct = self.strategy_config.get('take_profit_pct', 0.05)
        self.stop_loss_pct = self.strategy_config.get('stop_loss_pct', 0.03)
        self.trailing_stop = self.strategy_config.get('trailing_stop', False)
        self.trailing_distance = self.strategy_config.get('trailing_distance', 0.02)
        
        # 时间止损配置
        time_stop_loss = self.strategy_config.get('time_stop_loss', {})
        self.enable_time_stop = time_stop_loss.get('enabled', False)
        self.time_stop_candle_timeframe = time_stop_loss.get('candle_timeframe', 15)  # 默认15分钟
        self.time_stop_candle_count = time_stop_loss.get('candle_count', 3)  # 默认3根K线
        
        # 委托单位设置
        self.unit_type = self.strategy_config.get('unit_type', 'quote')
        
        # 日志详细程度控制
        self.verbose_log = self.strategy_config.get('verbose_log', True)
        
        # 初始化允许的交易对列表
        self.enable_symbol_pool = self.strategy_config.get('enable_symbol_pool', True)
        self.allowed_symbols: Set[str] = set()
        if self.enable_symbol_pool:
            default_symbols = self.strategy_config.get('default_symbols', [])
            self.allowed_symbols.update(default_symbols)
        
        # 提取退出策略配置并初始化退出策略管理器
        # 修复：正确传递退出策略配置
        exit_strategies_config = self.strategy_config.get('exit_strategies', {})
        exit_strategy_config_wrapper = {'strategy': {'exit_strategies': exit_strategies_config}}
        
        self.exit_strategy_manager = ExitStrategyManager(
            app_name=app_name,
            position_mgr=position_mgr,
            strategy_config=exit_strategy_config_wrapper,
            data_cache=data_cache,
            trader=trader
        )
        
        # 加载持久化的仓位信息（仅用于初始化）
        positions = self.position_mgr.load_positions(dict_format=True)
        self.logger = logging.getLogger(app_name)
        self.logger.info(f"加载已有仓位: {len(positions)}个")

        for symbol in list(positions.keys()):
            if positions[symbol].closed:
                continue
            try:
                self._subscribe_market_data(symbol)
            except Exception as e:
                self.logger.error(f"BaseStrategy 订阅 {symbol} 行情数据失败: {e}")

        # 平仓数据更新任务管理
        self._closing_position_tasks = {}  # 存储pos_id -> task的映射)
        
        # 策略状态
        self._strategy_status = "IDLE"
        self._status_message = ""
        
        self.logger.info("基础策略初始化完成", extra={
            "杠杆": self.leverage,
            "单笔仓位USDT": self.per_position_usdt,
            "止盈比例": f"{self.take_profit_pct*100}%",
            "止损比例": f"{self.stop_loss_pct*100}%",
            "追踪止损": self.trailing_stop,
            "委托单位": self.unit_type,
            "允许交易对": list(self.allowed_symbols) if self.enable_symbol_pool else "全部",
            "详细日志": "开启" if self.verbose_log else "关闭",
            "时间止损": "开启" if self.enable_time_stop else "关闭",
            "时间止损K线周期": f"{self.time_stop_candle_timeframe}分钟" if self.enable_time_stop else "禁用",
            "时间止损K线数量": self.time_stop_candle_count if self.enable_time_stop else "禁用"
        })
        
        # 初始化状态
        self.status = StrategyStatus.INITIALIZED
        self.status_message = "初始化完成"
        
        # 初始化交易对池
        self._init_symbol_pool()
        
        # 初始化风控组件
        self._init_risk_controller()
            
    async def _sync_positions_task(self):
        """定期同步持仓数据的任务"""
        try:
            self.logger.info("运行持仓同步任务")
            # 记录更新前的持仓数据(字段和值)
            before_positions = self.position_mgr.load_positions(dict_format=True)
            
            # 同步持仓数据
            success = await self.position_mgr.sync_positions_from_api()
            
            if success:
                # 记录更新后的持仓数据
                after_positions = self.position_mgr.load_positions(dict_format=True)
                
                # 检查关键字段是否一致性
                for symbol, position in after_positions.items():
                    if symbol in before_positions:
                        before_pos = before_positions[symbol]
                        # 检查关键字段
                        if (before_pos.entry_price != position.entry_price or 
                            before_pos.quantity != position.quantity or
                            before_pos.realized_pnl != position.realized_pnl):
                            self.logger.info(f"持仓 {symbol} 数据已更新: " + 
                                           f"入场价格 {before_pos.entry_price}->{position.entry_price}, " +
                                           f"数量 {before_pos.quantity}->{position.quantity}, " +
                                           f"已实现盈亏 {before_pos.realized_pnl}->{position.realized_pnl}")
                
                self.logger.info(f"持仓同步完成，成功更新 {len(after_positions)} 个持仓")
            else:
                self.logger.warning("持仓同步任务未成功完成")
        except Exception as e:
            self.logger.error(f"持仓同步任务异常: {e}", exc_info=True)

    def get_positions(self, dict_format=True):
        """
        实时获取最新持仓数据
        
        Args:
            dict_format: 是否返回字典格式
            
        Returns:
            Dict[str, Position]或List[Position]: 持仓列表或字典
        """
        return self.position_mgr.load_positions(dict_format=dict_format)
    
    async def _get_contract_size(self, symbol) -> float:
        """
        获取合约面值
        
        Args:
            symbol: 合约代码
            
        Returns:
            float: 合约面值
        """
        try:
            # 优先从data_cache中获取
            if self.data_cache:
                return await self.data_cache.get_contract_size(symbol)
            
            # 如果没有data_cache，从trader获取
            self.logger.debug(f"从trader获取合约面值: {symbol}")
            contract_info = self.trader.get_contract_info(symbol, is_spot=False)
            
            if contract_info and 'data' in contract_info and len(contract_info['data']) > 0:
                ct_val = float(contract_info['data'][0].get('ctVal', 1))
                self.logger.debug(f"获取合约面值成功: {symbol} = {ct_val}")
                return ct_val
            
            # 如果获取失败，使用默认值
            self.logger.warning(f"无法获取合约面值，使用默认值1: {symbol}")
            return 1
        except Exception as e:
            self.logger.error(f"获取合约面值异常: {e}", exc_info=True)
            return 1
            
    def get_contract_size_sync(self, symbol) -> float:
        """
        获取合约面值（同步版本）
        
        Args:
            symbol: 合约代码
            
        Returns:
            float: 合约面值
        """
        try:
            # 优先从data_cache中获取
            if self.data_cache:
                return self.data_cache.get_contract_size_sync(symbol)
            
            # 如果没有data_cache，从trader获取
            self.logger.debug(f"从trader获取合约面值: {symbol}")
            contract_info = self.trader.get_contract_info(symbol, is_spot=False)
            
            if contract_info and 'data' in contract_info and len(contract_info['data']) > 0:
                ct_val = float(contract_info['data'][0].get('ctVal', 1))
                self.logger.debug(f"获取合约面值成功: {symbol} = {ct_val}")
                return ct_val
            
            # 如果获取失败，使用默认值
            self.logger.warning(f"无法获取合约面值，使用默认值1: {symbol}")
            return 1
        except Exception as e:
            self.logger.error(f"获取合约面值异常: {e}", exc_info=True)
            return 1
    
    def _subscribe_market_data(self, symbol: str):
        """
        订阅标的物的行情数据
        
        Args:
            symbol: 交易对
        """
        if self.market_subscriber:
            try:
                asyncio.get_event_loop().run_until_complete(self.market_subscriber.subscribe_symbol(symbol))
            except Exception as e:
                self.logger.error(f"订阅 {symbol} 行情数据失败: {e}")
        else:
            # market_subscriber还未设置
            self.logger.debug(f"_subscribe_market_data market_subscriber还未设置 ")
    
    @abstractmethod
    async def process_signal(self, signal_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        处理接收到的信号，由子类实现
        
        Args:
            signal_data: 原始信号数据
            
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        pass
    
    async def handle_trade_signal(self, signal: TradeSignal) -> Tuple[bool, str]:
        """
        处理标准化的交易信号
        
        Args:
            signal: 交易信号对象
            
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        # 验证交易对是否允许交易
        if not self._validate_symbol(signal.symbol):
            return False, f"交易对 {signal.symbol} 不在允许列表中"
        
        try:
            if signal.action == "open":
                return await self._handle_open_signal(signal)
            elif signal.action == "close":
                return await self._handle_close_signal(signal)
            elif signal.action == "modify":
                return await self._handle_modify_signal(signal)
            elif signal.action == "status":
                return True, str(self.get_position_summary())
            else:
                return False, f"未知操作: {signal.action}"
        except Exception as e:
            self.logger.exception(f"处理信号异常: {e}")
            return False, f"处理信号异常: {e}"
    
    def _validate_symbol(self, symbol: str) -> bool:
        """
        验证交易对是否允许交易
        
        Args:
            symbol: 交易对
            
        Returns:
            bool: 是否允许交易
        """
        # 如果未启用交易对池，允许所有交易对
        if not self.enable_symbol_pool:
            return True
            
        # 检查是否在允许列表中
        return symbol in self.allowed_symbols
    
    async def _handle_open_signal(self, signal: TradeSignal) -> Tuple[bool, str]:
        """
        处理开仓信号
        
        Args:
            signal: 开仓信号
            
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        symbol = signal.symbol
        direction = signal.direction
        quantity = signal.quantity
        entry_price = signal.entry_price
        unit_type = signal.unit_type or self.unit_type
        leverage = signal.leverage or self.leverage
        extra_data = signal.extra_data or {}
        
        # 处理退出策略配置
        if extra_data and 'exit_strategies' in extra_data:
            exit_strategies_config = extra_data['exit_strategies']
            self.logger.info(f"从信号中获取退出策略配置: {exit_strategies_config}")
            
            try:
                # 遍历退出策略配置
                for strategy_name, strategy_config in exit_strategies_config.items():
                    # 获取策略对象
                    strategy = self.exit_strategy_manager.get_strategy(strategy_name)
                    if not strategy:
                        self.logger.warning(f"未找到退出策略: {strategy_name}")
                        continue
                    
                    # 处理启用/禁用
                    if 'enabled' in strategy_config:
                        if strategy_config['enabled']:
                            self.exit_strategy_manager.enable_strategy(strategy_name)
                        else:
                            self.exit_strategy_manager.disable_strategy(strategy_name)
                    
                    # 更新策略参数
                    params = {k: v for k, v in strategy_config.items() if k != 'enabled'}
                    if params:
                        self.exit_strategy_manager.update_strategy_params(strategy_name, params)
            except Exception as e:
                self.logger.error(f"应用退出策略配置异常: {e}", exc_info=True)
        
        # 检查交易对是否在允许的池中
        if self.enable_symbol_pool and symbol not in self.allowed_symbols:
            self.logger.warning(f"交易对 {symbol} 不在允许的交易对列表中")
            return False, f"交易对 {symbol} 不在允许的列表中"
        # 风控检查 - 如果有风控系统
        # 创建开仓前置检查列表
        pre_checks = []
        
        # 添加风控检查 - 无论是否有风控参数都进行检查
        # 进行风控检查
        passed, reason = await self.position_mgr.check_risk_control(signal.symbol, signal.extra_data)
        if not passed:
            return False, f"风控检查不通过: {reason}"
        
        # 只有通过了风控检查才记录交易
        pre_checks.append("风控检查通过")
        
        # 获取现价
        current_price = await self.data_cache.get_mark_price(symbol)
        if not current_price:
            self.logger.warning(f"无法获取 {symbol} 的现价，使用信号给定价格")
            current_price = entry_price
        
        # 记录开仓信息
        self.logger.info(f"处理开仓信号: {symbol} {direction}, 杠杆: {leverage}, 当前价: {current_price}")
        
        # 计算下单数量
        calculated_quantity = None
        if quantity is None:
            # 使用默认的下单金额
            usdt_amount = self.per_position_usdt
            
            # 计算下单数量
            calculated_quantity = calculate_order_size(
                trader=self.trader,
                symbol=symbol,
                position_usdt=usdt_amount,
                unit_type=unit_type,
                leverage=leverage
            )[0]  # 只取返回的size，忽略tgt_ccy
            
            self.logger.info(f"计算下单数量: {calculated_quantity}, USDT金额: {usdt_amount}, 单位类型: {unit_type}")
        else:
            calculated_quantity = quantity
            
        # 设置状态为开仓中
        self.status = StrategyStatus.OPENING_POSITION
        self.status_message = f"正在开仓 {symbol} {direction}"
        
        # 设置全仓模式
        self.logger.info(f"设置 {symbol} 杠杆: {leverage}")
        self.trader.set_leverage(symbol, leverage)
        
        # 执行下单
        side = "buy" if direction == "long" else "sell"
        pos_side = direction
        
        # 下单
        order_result = self.trader.swap_order(
            inst_id=symbol,
            side=side,
            pos_side=pos_side,
            sz=calculated_quantity
        )
        
        if not order_result or order_result.get("code") != "0":
            error_msg = order_result.get("msg", "未知错误") if order_result else "未知错误"
            self.status = StrategyStatus.ERROR
            self.status_message = f"开仓失败: {error_msg}"
            self.logger.error(f"开仓失败: {error_msg}")
            return False, f"开仓失败: {error_msg}"
        
        # 获取订单ID
        order_id = order_result.get("data", [{}])[0].get("ordId", "")
        
        # 记录开仓信息
        self.logger.info(f"开仓下单成功: {symbol} {direction}, 数量: {calculated_quantity}, 价格: {current_price}, 订单ID: {order_id}")
        
        # 订阅行情数据（用于止盈止损监控）
        self._subscribe_market_data(symbol)
        
        # 创建Position对象并保存
        import time
        import uuid
        position_id = f"{symbol}_{uuid.uuid4().hex[:8]}_{int(time.time())}"
        
        from src.common.position_manager import Position
        position = Position(
            symbol=symbol,
            position_id=position_id,
            entry_price=current_price,
            quantity=calculated_quantity if direction == "long" else -calculated_quantity,
            position_type="swap",
            leverage=leverage,
            timestamp=int(time.time() * 1000),
            direction=direction,
            high_price=current_price,
            low_price=current_price,
            signal=signal,
            extra_data=extra_data,
            ladder_tp=extra_data.get('ladder_tp', False),
            ladder_tp_pct=extra_data.get('ladder_tp_pct', 0.2),
            ladder_tp_step=extra_data.get('ladder_tp_step', 0.2),
            pos_id=order_id
        )
        
        # 设置止盈止损参数
        # 从信号中获取止盈止损参数，如果没有则使用策略默认值
        position.take_profit_pct = signal.take_profit_pct or self.take_profit_pct
        position.stop_loss_pct = signal.stop_loss_pct or self.stop_loss_pct
        position.trailing_stop = signal.trailing_stop if signal.trailing_stop is not None else self.trailing_stop
        position.trailing_distance = signal.trailing_distance or self.trailing_distance
        
        # 保存仓位
        self.position_mgr.save_position(position)
        self.logger.info(f"已保存仓位信息: {position_id}")
        
        # 通知风控系统
        if hasattr(self.position_mgr, 'risk_controller'):
            self.position_mgr.risk_controller.record_trade(symbol)
            self.logger.debug(f"通知风控系统新开仓: {symbol}")
        
        # 设置状态为已开仓
        self.status = StrategyStatus.POSITION_OPENED
        self.status_message = f"已开仓 {symbol} {direction}, 数量: {calculated_quantity}, 价格: {current_price}"
        
        return True, f"开仓成功: {symbol} {direction}, 数量: {calculated_quantity}, 价格: {current_price}"
    
    async def _handle_close_signal(self, signal: TradeSignal) -> Tuple[bool, str]:
        """
        处理平仓信号
        
        Args:
            signal: 平仓信号
            
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        # 获取最新持仓数据
        positions = self.get_positions()
        
        # 检查信号有效性
        if not signal.symbol:
            return False, "缺少交易对"
            
        # 检查是否有此交易对的持仓
        if signal.symbol not in positions:
            return False, f"没有 {signal.symbol} 的持仓"
            
        # 获取持仓信息
        position = positions[signal.symbol]
        
        self.logger.info(f"处理平仓信号: {signal.symbol}", extra={
            "action": signal.action,
            "symbol": signal.symbol
        })
        
        if position.closed:
            self.logger.warning(f"仓位 {signal.symbol} 已经是平仓状态")
            return False, f"仓位 {signal.symbol} 已经平仓"
            
        # 执行平仓
        self.update_strategy_status(StrategyStatus.CLOSING_POSITION, f"正在平仓: {signal.symbol}")
        success, msg = await self._execute_close_position(signal.symbol, position)
        
        if success:
            self.logger.info(f"平仓成功: {signal.symbol}, {msg}")
            self.update_strategy_status(StrategyStatus.POSITION_CLOSED, f"已平仓: {signal.symbol}")
            return True, f"平仓成功: {msg}"
        else:
            self.logger.error(f"平仓失败: {signal.symbol}, {msg}")
            self.update_strategy_status(StrategyStatus.ERROR, f"平仓失败: {signal.symbol}, {msg}")
            return False, f"平仓失败: {msg}"
    
    async def _handle_modify_signal(self, signal: TradeSignal) -> Tuple[bool, str]:
        """
        处理修改信号
        
        Args:
            signal: 修改信号
            
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        # 获取最新持仓数据
        positions = self.get_positions()
        
        # 检查信号有效性
        if not signal.symbol:
            return False, "缺少交易对"
        
        # 检查是否有此交易对的持仓
        if signal.symbol not in positions:
            return False, f"没有 {signal.symbol} 的持仓"
            
        # 获取持仓信息
        position = positions[signal.symbol]
        
        # 更新策略状态
        self.update_strategy_status(StrategyStatus.MODIFYING_POSITION, f"正在修改持仓: {signal.symbol}")
        
        # 记录修改前的值
        original_values = {
            "take_profit_pct": getattr(position, "take_profit_pct", self.take_profit_pct),
            "stop_loss_pct": getattr(position, "stop_loss_pct", self.stop_loss_pct),
            "trailing_stop": getattr(position, "trailing_stop", self.trailing_stop),
            "trailing_distance": getattr(position, "trailing_distance", self.trailing_distance),
            "ladder_tp": getattr(position, "ladder_tp", False),
            "ladder_tp_pct": getattr(position, "ladder_tp_pct", 0.2),
            "ladder_tp_step": getattr(position, "ladder_tp_step", 0.2)
        }
        
        # 修改止盈止损参数
        if signal.take_profit_pct is not None:
            position.take_profit_pct = signal.take_profit_pct
        
        if signal.stop_loss_pct is not None:
            position.stop_loss_pct = signal.stop_loss_pct
        
        if signal.trailing_stop is not None:
            position.trailing_stop = signal.trailing_stop
        
        if signal.trailing_distance is not None:
            position.trailing_distance = signal.trailing_distance
        
        # 阶梯止盈相关
        if signal.extra_data:
            if 'ladder_tp' in signal.extra_data:
                position.ladder_tp = signal.extra_data['ladder_tp']
                
            if 'ladder_tp_pct' in signal.extra_data:
                position.ladder_tp_pct = signal.extra_data['ladder_tp_pct']
                
            if 'ladder_tp_step' in signal.extra_data:
                position.ladder_tp_step = signal.extra_data['ladder_tp_step']
        
        # 保存更新后的持仓信息
        self.position_mgr.save_position(position)
        
        # 更新策略状态
        self.update_strategy_status(StrategyStatus.POSITION_MODIFIED, f"已修改持仓: {signal.symbol}")
        
        # 构建修改信息
        modified_fields = []
        for key, orig_value in original_values.items():
            new_value = getattr(position, key, orig_value)
            if new_value != orig_value:
                if isinstance(new_value, bool):
                    modified_fields.append(f"{key}: {orig_value} -> {new_value}")
                else:
                    modified_fields.append(f"{key}: {orig_value:.4f} -> {new_value:.4f}")
        
        if modified_fields:
            modifications = ", ".join(modified_fields)
            self.logger.info(f"修改持仓参数成功: {signal.symbol}, {modifications}")
            return True, f"修改成功: {modifications}"
        else:
            self.logger.info(f"持仓 {signal.symbol} 没有变更")
            return True, "没有实际修改"
    
    async def monitor_positions(self):
        """
        监控所有持仓的状态，检查是否需要平仓
        """
        try:
            # 获取最新持仓数据
            positions = self.get_positions()
            
            if not positions:
                return
            
            # 计算总盈亏以更新风控系统
            total_pnl_amount = 0
            total_margin = 0

            # 获取当前价格信息
            for symbol, position in list(positions.items()):
                # 跳过已平仓的持仓
                if position.closed:
                    continue
                    
                try:
                    # 获取当前市场价格
                    mark_price = await self.data_cache.get_mark_price(symbol)
                    if not mark_price:
                        self.logger.warning(f"无法获取 {symbol} 的行情价格")
                        continue
                    
                    # 确保position有direction属性
                    if not hasattr(position, 'direction') or position.direction is None:
                        position.direction = "long" if position.quantity > 0 else "short"
                    
                    # 方向，保证兼容
                    direction = position.direction
                    
                    # 价格变动百分比 - 不考虑杠杆
                    if direction == "long":
                        pnl_pct = (mark_price - position.entry_price) / position.entry_price
                    else:  # short
                        pnl_pct = (position.entry_price - mark_price) / position.entry_price
                    
                    # 计算带杠杆的盈亏百分比
                    leveraged_pnl_pct = pnl_pct * position.leverage
                    
                    # 获取合约面值
                    contract_size = self.get_contract_size_sync(symbol)
                    
                    # 计算盈亏金额 - 修正计算方法
                    # 合约价值 = 数量 * 入场价格 * 合约面值
                    contract_value = abs(position.quantity) * position.entry_price * contract_size
                    
                    # 保证金 = 合约价值 / 杠杆倍数
                    margin = contract_value / position.leverage
                    total_margin += margin
                    
                    # 原始盈亏金额（未考虑杠杆）
                    if direction == "long":
                        raw_pnl_amount = abs(position.quantity) * (mark_price - position.entry_price) * contract_size
                    else:  # short
                        raw_pnl_amount = abs(position.quantity) * (position.entry_price - mark_price) * contract_size
                    
                    # 实际盈亏金额（考虑杠杆）- 实际上是保证金 * 杠杆后的收益率
                    pnl_amount = margin * leveraged_pnl_pct
                    total_pnl_amount += pnl_amount
                    
                    # 计算持仓时间 - 确保时间戳格式一致性
                    current_timestamp = int(time.time() * 1000)
                    
                    # 确保position.timestamp是毫秒级时间戳
                    position_timestamp = position.timestamp
                    if position_timestamp < 9999999999:  # 如果是秒级时间戳
                        self.logger.debug(f"检测到秒级时间戳，转换为毫秒级: {position_timestamp} -> {position_timestamp * 1000}")
                        position_timestamp *= 1000
                    
                    holding_time_ms = current_timestamp - position_timestamp
                    
                    # 防止出现负值
                    if holding_time_ms < 0:
                        self.logger.warning(f"检测到异常的持仓时间(负值)，使用默认值0: {holding_time_ms}ms")
                        holding_time_ms = 0
                    
                    # 将毫秒转换为小时
                    holding_time_hours = holding_time_ms / (1000 * 60 * 60)
                    
                    # 将持仓时间格式化为更易读的形式
                    if holding_time_hours < 24:
                        holding_time_str = f"{holding_time_hours:.2f}小时"
                    else:
                        holding_time_days = holding_time_hours / 24
                        if holding_time_days < 30:
                            holding_time_str = f"{holding_time_days:.2f}天 ({holding_time_hours:.1f}小时)"
                        else:
                            holding_time_months = holding_time_days / 30
                            holding_time_str = f"{holding_time_months:.2f}月 ({holding_time_days:.1f}天)"
                    
                    # 检查该信号是否有指定的止盈止损点
                    signal = position.signal
                    take_profit_pct = signal.take_profit_pct if signal and hasattr(signal, 'take_profit_pct') and signal.take_profit_pct is not None else self.take_profit_pct
                    stop_loss_pct = signal.stop_loss_pct if signal and hasattr(signal, 'stop_loss_pct') and signal.stop_loss_pct is not None else self.stop_loss_pct
                    
                    trailing_stop = signal.trailing_stop if signal and hasattr(signal, 'trailing_stop') and signal.trailing_stop is not None else self.trailing_stop
                    trailing_distance = signal.trailing_distance if signal and hasattr(signal, 'trailing_distance') and signal.trailing_distance is not None else self.trailing_distance
                    
                    if position.leverage > 1:
                        take_profit_pct = take_profit_pct / position.leverage
                        stop_loss_pct = stop_loss_pct  / position.leverage
                        trailing_distance = trailing_distance / position.leverage

                    # 更新最高/最低价格
                    if direction == "long":
                        # 多头情况下更新最高价
                        if mark_price > position.high_price:
                            position.high_price = mark_price
                            self.logger.info(f"{symbol} 创新高: {mark_price}")
                    else:  # short
                        # 空头情况下更新最低价
                        if mark_price < position.low_price or position.low_price == 0:
                            position.low_price = mark_price
                            self.logger.info(f"{symbol} 创新低: {mark_price}")
                    
                    # 使用退出策略管理器检查是否满足平仓条件
                    # 修复：创建能处理部分平仓的回调函数
                    async def execute_close_callback(symbol, position, close_percentage=1.0):
                        return await self._execute_close_position(symbol, position, close_percentage)
                    
                    # 调用退出策略管理器检查平仓条件
                    await self.exit_strategy_manager.check_exit_conditions(
                        position=position,
                        current_price=mark_price,
                        execute_close_func=execute_close_callback
                    )
                    
                except Exception as e:
                    self.logger.error(f"监控持仓 {symbol} 发生异常: {e}")
            
            # 更新风控系统的盈亏数据
            if total_margin > 0 and hasattr(self.position_mgr, 'update_risk_pnl'):
                # 计算总盈亏百分比
                total_pnl_pct = (total_pnl_amount / total_margin) * 100
                self.position_mgr.update_risk_pnl(total_pnl_pct)
                self.logger.debug(f"更新风控盈亏数据: 总盈亏={total_pnl_amount:.2f}USDT, 总保证金={total_margin:.2f}USDT, 盈亏率={total_pnl_pct:.2f}%")
                    
            self.logger.debug("持仓监控完成")
        except Exception as e:
            self.logger.error(f"监控持仓异常: {e}")
            import traceback
            self.logger.error(traceback.format_exc())

    async def _execute_close_position(self, symbol: str, position: Position, close_percentage: float = 1.0) -> Tuple[bool, str]:
        """
        执行平仓操作
        
        Args:
            symbol: 交易对
            position: 仓位对象
            close_percentage: 平仓百分比，1.0表示全部平仓
            
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        try:
            # 记录平仓信息
            is_partial_close = close_percentage < 1.0
            if is_partial_close:
                self.logger.info(f"执行部分平仓 {symbol}: {close_percentage*100:.1f}%")
            else:
                self.logger.info(f"执行全部平仓 {symbol}")
            
            # 计算平仓数量
            close_quantity = abs(position.quantity) * close_percentage
            
            # 使用trader的calculate_position_size方法圆整数量
            try:
                # 计算对应的USDT价值
                current_price = await self.data_cache.get_mark_price(symbol)
                if not current_price:
                    self.logger.warning(f"无法获取 {symbol} 的现价，使用仓位入场价")
                    current_price = position.entry_price
                
                contract_size = self.get_contract_size_sync(symbol)
                usdt_value = close_quantity * current_price * contract_size / position.leverage
                
                # 使用calculate_position_size圆整
                close_quantity = self.trader.calculate_position_size(
                    inst_id=symbol,
                    is_spot=False,  # 永续合约
                    target_usdt=usdt_value,
                    target_leverage=position.leverage
                )
                
                self.logger.info(f"{symbol} 平仓数量 (已圆整): {close_quantity}")
            except Exception as e:
                self.logger.warning(f"使用calculate_position_size圆整数量失败，使用原始数量: {e}")
            
            # 确定平仓方向（与持仓方向相反）
            side = "sell" if position.direction == "long" else "buy"
            pos_side = position.direction
            
            # 执行平仓操作
            close_result = self.trader.swap_order(
                inst_id=symbol,
                side=side,
                pos_side=pos_side,
                sz=close_quantity
            )
            
            if close_result and close_result.get("code") == "0":
                self.logger.info(f"{symbol} {'部分' if is_partial_close else '全部'}平仓委托成功")
                
                if is_partial_close:
                    # 如果是部分平仓，更新持仓量
                    original_quantity = position.quantity
                    new_quantity = position.quantity * (1 - close_percentage)
                    
                    if position.direction == "long":
                        new_quantity = min(new_quantity, original_quantity)  # 确保不会增加持仓
                    else:  # short
                        new_quantity = max(new_quantity, original_quantity)  # 确保不会增加持仓
                    
                    try:
                        # 计算对应的USDT价值
                        remaining_usdt_value = abs(new_quantity) * current_price * contract_size / position.leverage
                        
                        # 使用calculate_position_size圆整剩余数量
                        rounded_new_quantity = self.trader.calculate_position_size(
                            inst_id=symbol,
                            is_spot=False,
                            target_usdt=remaining_usdt_value,
                            target_leverage=position.leverage
                        )
                        
                        # 保持方向一致
                        if position.direction == "long":
                            new_quantity = rounded_new_quantity
                        else:  # short
                            new_quantity = -rounded_new_quantity
                        
                    except Exception as e:
                        self.logger.warning(f"圆整更新后的持仓数量失败，使用原始计算结果: {e}")
                    
                    # 计算部分平仓收益（估算）
                    # 合约价值 = 数量 * 入场价格 * 合约面值
                    contract_size = self.get_contract_size_sync(symbol)
                    closed_contract_value = close_quantity * position.entry_price * contract_size
                    
                    # 获取当前价格
                    mark_price = await self.data_cache.get_mark_price(symbol)
                    if not mark_price:
                        mark_price = position.entry_price  # 如果无法获取当前价格，使用入场价格
                    
                    # 计算杠杆收益率
                    if position.direction == 'long':
                        pnl_pct = (mark_price - position.entry_price) / position.entry_price
                    else:  # short
                        pnl_pct = (position.entry_price - mark_price) / position.entry_price
                    
                    # 带杠杆的收益率
                    leveraged_pnl_pct = pnl_pct * position.leverage
                    
                    closed_margin = closed_contract_value / position.leverage
                    closed_pnl = closed_margin * leveraged_pnl_pct
                    
                    # 更新仓位信息
                    position.quantity = new_quantity
                    
                    # 累计已实现收益 - 如果属性不存在则初始化为0
                    if not hasattr(position, 'realized_pnl'):
                        position.realized_pnl = 0.0
                    position.realized_pnl += closed_pnl
                    
                    # 更新阶梯止盈相关信息
                    if hasattr(position, 'ladder_closed_pct'):
                        prev_closed_pct = position.ladder_closed_pct if position.ladder_closed_pct else 0.0
                        position.ladder_closed_pct = prev_closed_pct + close_percentage
                        self.logger.info(f"{symbol} 更新阶梯止盈已平仓百分比: {prev_closed_pct:.2f} -> {position.ladder_closed_pct:.2f}")
                    
                    # 记录部分平仓信息
                    self.logger.info(f"{symbol} 部分平仓成功，平仓数量: {close_quantity}, 剩余持仓: {abs(new_quantity)}")
                    self.logger.info(f"{symbol} 部分平仓收益: {closed_pnl:.2f} USDT ({leveraged_pnl_pct*100:.2f}%)")
                    self.logger.info(f"{symbol} 累计已实现收益: {position.realized_pnl:.2f} USDT")

                    # 通知风控系统部分平仓信息
                    if hasattr(self.position_mgr, 'risk_controller'):
                        self.position_mgr.risk_controller.record_close_position(symbol, is_partial_close=True)
                        self.logger.debug(f"已通知风控系统部分平仓: {symbol}")
                    
                    # 检查是否已全部平仓
                    if abs(position.quantity) < 0.0001 or position.ladder_closed_pct >= 0.9999:
                        self.logger.info(f"{symbol} 持仓已全部平仓")
                        position.closed = 1
                        position.close_time = int(time.time() * 1000)
                        self.position_mgr.save_position(position)
                        # 通知风控系统完全平仓 (确保计数器正确更新)
                        if hasattr(self.position_mgr, 'risk_controller'):
                            self.position_mgr.risk_controller.record_close_position(symbol, is_partial_close=False)
                            self.logger.debug(f"已通知风控系统完全平仓: {symbol}")

                        # 更新平仓数据
                        self._start_position_update_task(position.pos_id, symbol)
                    else:
                        # 从api获取仓位信息 部分平仓时 无需更新平仓价格，已实现盈亏 = api已实现盈亏 + api卖出手续费 + 之前缓存的已实现盈亏
                        pos_data = await self.data_cache.get_position_data(symbol, force_update=True)
                        if pos_data and pos_data.get('data'):
                            api_position = pos_data.get('data')
                            self.logger.info(f"API返回的仓位数据-部分平仓: {api_position}")
                            realized_pnl = float(api_position['realizedPnl']) + float(api_position['fee']) + position.realized_pnl
                            # 更新仓位信息
                            position.realized_pnl = realized_pnl
                            self.logger.info(f"{symbol} 部分平仓 更新仓位信息: {position}")

                    # 更新持仓数据库
                    self.position_mgr.save_position(position)
                    
                    return True, f"部分平仓成功，平仓比例: {close_percentage*100:.1f}%"
                else:
                    # 全部平仓的逻辑
                    position.closed = 1
                    position.close_time = int(time.time() * 1000)
                    position.exit_timestamp = position.close_time  # 确保exit_timestamp也被设置
                    
                    # 从API获取平仓价格和已实现盈亏
                    try:
                        pos_data = await self.data_cache.get_position_data(symbol, force_update=True)
                        if pos_data and pos_data.get('data'):
                            api_position = pos_data.get('data')
                            self.logger.info(f"API返回的仓位数据-全部平仓: {api_position}")
                            
                            # 获取平仓价格和已实现盈亏
                            exit_price = float(api_position.get('markPx', 0))
                            position.exit_price = exit_price
                            position.close_price = exit_price  # 同时设置close_price和exit_price
                            position.realized_pnl = float(api_position.get('realizedPnl', 0)) + float(api_position.get('fee', 0))
                        else:
                            # 如果无法从API获取，则使用当前市场价格
                            mark_price = await self.data_cache.get_mark_price(symbol)
                            if mark_price:
                                position.exit_price = mark_price
                                position.close_price = mark_price
                    except Exception as e:
                        self.logger.error(f"获取平仓信息异常: {e}")
                    
                    # 保存更新后的仓位信息
                    self.position_mgr.save_position(position)
                    
                    # 通知风控系统平仓信息
                    if hasattr(self.position_mgr, 'risk_controller'):
                        self.position_mgr.risk_controller.record_close_position(symbol, is_partial_close=False)
                        self.logger.debug(f"已通知风控系统全部平仓: {symbol}")
                    
                    # 更新平仓数据
                    self._start_position_update_task(position.pos_id, symbol)
                    
                    return True, "平仓成功"
            else:
                error_msg = close_result.get('msg', '未知错误') if close_result else '无响应'
                self.logger.error(f"{symbol} 平仓失败: {error_msg}")
                return False, f"平仓失败: {error_msg}"
        
        except Exception as e:
            self.logger.error(f"{symbol} 执行平仓操作异常: {e}")
            return False, f"平仓异常: {e}"
    
    def _start_position_update_task(self, pos_id: str, symbol: str):
        """启动异步任务来更新平仓后的仓位信息"""
        # 如果已经有同样的任务在运行，就不再启动新任务
        if pos_id in self._closing_position_tasks and not self._closing_position_tasks[pos_id].done():
            self.logger.info(f"已有针对仓位 {pos_id} 的更新任务在运行")
            return
            
        # 创建并启动异步任务 - 使用ensure_future替代create_task以兼容Python 3.6.8
        task = asyncio.ensure_future(self._update_closed_position_info(pos_id, symbol))
        self._closing_position_tasks[pos_id] = task
        
        # 添加任务完成回调，用于清理
        task.add_done_callback(lambda t: self._cleanup_position_task(pos_id, t))
        
        self.logger.info(f"已启动仓位 {pos_id} 的更新任务")
        
    def _cleanup_position_task(self, pos_id: str, task):
        """清理已完成的任务"""
        # 从字典中移除任务
        if pos_id in self._closing_position_tasks:
            del self._closing_position_tasks[pos_id]
            
        # 检查任务是否有异常
        if task.exception():
            self.logger.error(f"仓位 {pos_id} 更新任务出错: {task.exception()}")
            
    async def _update_closed_position_info(self, pos_id: str, symbol: str, max_attempts=10, initial_delay=1, max_delay=30):
        """
        异步任务：持续尝试获取平仓后的历史数据，并更新到数据库
        汇总符合条件的所有平仓记录收益，使用最近一次平仓价格
        
        Args:
            pos_id: 仓位ID
            symbol: 交易对
            max_attempts: 最大尝试次数
            initial_delay: 初始延迟(秒)
            max_delay: 最大延迟(秒)
        """
        self.logger.info(f"开始异步更新平仓信息任务: 仓位ID={pos_id}, 交易对={symbol}")
        
        delay = initial_delay
        
        # 获取原始仓位信息，用于后续对比确认
        position = self.position_mgr.get_position_by_id(pos_id)
        if not position:
            self.logger.error(f"无法获取仓位信息: {pos_id}")
            return
            
        # 记录原始仓位信息以供校验
        original_open_time = position.timestamp  # 使用timestamp作为开仓时间
        original_direction = position.direction
        original_entry_price = position.entry_price
        local_close_time = getattr(position, 'close_time', 0)  # 获取本地平仓时间
        
        if not local_close_time:
            self.logger.warning(f"仓位未记录本地平仓时间，可能是旧数据: {pos_id}")
        
        for attempt in range(1, max_attempts + 1):
            self.logger.info(f"第{attempt}/{max_attempts}次尝试获取平仓历史数据: {pos_id}")
            
            # 查询历史持仓数据
            history_data = self.trader.get_position_history(pos_id=pos_id)
            
            if history_data and len(history_data) > 0:
                # 找到所有符合条件的历史记录:
                # 1. 交易对匹配
                # 2. 方向匹配
                # 3. 开仓价格接近
                # 4. 平仓时间晚于开仓时间
                matched_records = []
                
                for record in history_data:
                    if (record['instId'] == symbol and 
                        record['direction'] == original_direction and
                        abs(float(record['openAvgPx']) - original_entry_price) < 0.00000001):
                        
                        close_time = int(record.get('closeTime', 0))
                        if close_time > original_open_time:
                            matched_records.append(record)
                            self.logger.info(f"找到匹配的平仓记录: {record}")
                
                # 按平仓时间排序
                matched_records.sort(key=lambda x: int(x.get('closeTime', 0)))
                
                # 检查是否有最新的API数据
                api_data_updated = False
                if matched_records and local_close_time > 0:
                    # 获取最近一次平仓记录
                    latest_record = matched_records[-1]
                    latest_close_time = int(latest_record.get('closeTime', 0))
                    
                    # 计算时间差（毫秒）
                    time_diff = abs(latest_close_time - local_close_time)
                    
                    # 如果时间差在15秒内，认为API数据已更新
                    if time_diff <= 15000:  # 15秒 = 15000毫秒
                        api_data_updated = True
                        self.logger.info(f"API数据已更新，时间差: {time_diff/1000:.2f}秒")
                    else:
                        self.logger.info(f"API数据可能未更新，时间差: {time_diff/1000:.2f}秒")
                
                # 只有当API数据已更新时，才进行处理
                if api_data_updated and matched_records:
                    # 汇总所有匹配记录的收益
                    total_realized_pnl = 0.0
                    for record in matched_records:
                        pnl = float(record.get('realizedPnl', 0))
                        total_realized_pnl += pnl
                    
                    # 使用最近一次平仓价格作为出场价格
                    latest_exit_price = float(matched_records[-1]['closeAvgPx'])
                    
                    # 更新仓位信息
                    position.exit_price = latest_exit_price
                    position.realized_pnl = total_realized_pnl
                    position.pnl_amount = total_realized_pnl
                    if position.margin > 0:
                        position.pnl_percentage = position.pnl_amount / position.margin
                    position.unrealized_pnl = 0.0
                    
                    # 保存更新后的仓位信息
                    self.position_mgr.save_position(position)
                    
                    self.logger.info(f"成功更新平仓信息: 仓位ID={pos_id}, 平仓价={position.exit_price}, " +
                                   f"汇总已实现盈亏={position.realized_pnl}, 匹配记录数={len(matched_records)}")
                    return True
            
            # 如果是最后一次尝试，不再等待
            if attempt >= max_attempts:
                self.logger.warning(f"达到最大尝试次数({max_attempts})，仍未找到匹配的平仓记录或API数据未更新: {pos_id}")
                break
            
            # 使用指数退避策略增加延迟
            delay = min(delay * 1.5, max_delay)
            self.logger.info(f"等待{delay:.1f}秒后重试...")
            await asyncio.sleep(delay)
        
        self.logger.warning(f"无法获取平仓详细信息，使用默认值: 仓位ID={pos_id}")
        return False
    
    def get_position_summary(self) -> Dict[str, Any]:
        """获取持仓摘要信息"""
        # 获取最新持仓数据
        positions = self.get_positions()
        
        # 计算总盈亏
        total_pnl = 0.0
        unrealized_pnl = 0.0
        
        # 持仓列表
        positions_list = []
        
        for symbol, position in positions.items():
            if position.closed:
                continue
            
            position_info = {
                "symbol": symbol,
                "entry_price": position.entry_price,
                "quantity": position.quantity,
                "direction": "long" if position.quantity > 0 else "short",
                "leverage": position.leverage,
                "timestamp": position.timestamp,
                "position_id": position.position_id
            }
            positions_info.append(position_info)
        
        return {
            "position_count": len(positions),
            "positions": positions_info
        }
    
    async def manual_trigger(self, signal: TradeSignal) -> Tuple[bool, str]:
        """
        手动触发信号
        
        Args:
            signal: 交易信号
            
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        return await self.handle_trade_signal(signal)
    
    async def manual_close_all(self) -> Tuple[bool, str]:
        """
        手动平掉所有持仓
        
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        # 获取最新持仓数据
        positions = self.get_positions()
        
        if not positions:
            return True, "当前没有持仓"
        
        success_count = 0
        error_messages = []
        
        for symbol in list(positions.keys()):
            if positions[symbol].closed:
                continue
                
            try:
                signal = TradeSignal(
                    action="close",
                    symbol=symbol
                )
                success, msg = await self.handle_trade_signal(signal)
                if success:
                    success_count += 1
                else:
                    error_messages.append(msg)
            except Exception as e:
                error_messages.append(f"关闭持仓 {symbol} 失败: {e}")
        
        if success_count == 0:
            return False, f"关闭所有持仓失败: {', '.join(error_messages)}"
        else:
            return True, f"成功关闭 {success_count} 个持仓"
    
    async def get_daily_pnl(self, start_date: str = None, end_date: str = None) -> List[Dict]:
        """
        获取每日收益统计
        
        Args:
            start_date: 开始日期，格式为 YYYY-MM-DD，默认为7天前
            end_date: 结束日期，格式为 YYYY-MM-DD，默认为今天
            
        Returns:
            List[Dict]: 每日收益统计列表
        """
        # 直接调用position_mgr的方法
        result = self.position_mgr.get_daily_pnl(start_date, end_date)
        if result:
            # 添加调试日志
            self.logger.info(f"获取每日收益统计成功: {len(result)}条")
        else:
            self.logger.warning("获取每日收益统计为空")
        return result
    
    async def get_position_history(self, start_date: str = None, end_date: str = None, 
                            symbol: str = None, limit: int = 100) -> List[Dict]:
        """
        获取历史仓位记录
        
        Args:
            start_date: 开始日期，格式为 YYYY-MM-DD，默认为30天前
            end_date: 结束日期，格式为 YYYY-MM-DD，默认为今天
            symbol: 交易对，默认为所有
            limit: 最大返回记录数，默认100条
            
        Returns:
            List[Dict]: 历史仓位记录列表
        """
        # 直接调用position_mgr的方法
        result = self.position_mgr.get_position_history(start_date, end_date, symbol, limit)
        if result:
            # 添加调试日志
            self.logger.info(f"获取历史仓位记录成功: {len(result)}条")
        else:
            self.logger.warning("获取历史仓位记录为空")
        return result

    def update_strategy_status(self, status: str, message: str = ""):
        """
        更新策略状态
        
        Args:
            status: 状态名称
            message: 状态描述消息
        """
        self._strategy_status = status
        self._status_message = message
        self.logger.info(f"策略状态更新: {status}", extra={"status_detail": message})

    def _execute_close_params(self, params: Dict[str, Any]):
        """
        根据参数字典执行平仓操作
        
        Args:
            params: 平仓参数字典，包含symbol、direction、size等信息
        """
        try:
            symbol = params["symbol"]
            direction = params["direction"]
            size = params["size"]
            price = params.get("price")
            
            # 更新策略状态
            self.update_strategy_status(StrategyStatus.CLOSING_POSITION)
            
            pos_side = "long" if direction == "buy" else "short"
            
            # 执行平仓
            close_result = self.trader.swap_order(
                inst_id=symbol,
                side=direction,
                pos_side=pos_side,
                sz=size,
                px=price
            )
            
            if not close_result or close_result.get("code") != "0":
                self.logger.error(f"平仓失败: {close_result}")
                self.update_strategy_status(StrategyStatus.ERROR, f"平仓失败: {close_result}")
                return
            
            # 记录平仓订单
            self.logger.info("平仓订单已提交", extra={
                "symbol": symbol,
                "direction": direction,
                "size": size,
                "type": "market" if price is None else "limit"
            })
            
            # 更新策略状态为已平仓
            self.update_strategy_status(StrategyStatus.POSITION_CLOSED)
            
            # 策略状态设置为空闲
            self.update_strategy_status(StrategyStatus.IDLE)

        except Exception as e:
            self.logger.error(f"执行平仓失败: {e}", exc_info=True)
            self.update_strategy_status(StrategyStatus.ERROR, str(e))
            raise

    def _init_symbol_pool(self):
        """
        初始化交易对池
        """
        # 实现初始化交易对池的逻辑
        pass

    def _init_risk_controller(self):
        """
        初始化风控组件
        """
        # 检查配置中是否包含风控配置
        if 'risk_control' in self.config:
            # 从配置中获取风控配置
            risk_config = self.config.get('risk_control', {})
            
            # 配置风控组件
            if hasattr(self.position_mgr, 'risk_controller'):
                # 设置数据缓存引用
                if hasattr(self.position_mgr.risk_controller, 'set_data_cache'):
                    self.position_mgr.risk_controller.set_data_cache(self.data_cache)
                    self.logger.info("已为风控组件设置数据缓存引用")
                
                # 配置风控组件
                self.position_mgr.configure_risk_control(risk_config)
                self.logger.info(f"已配置风控组件: {risk_config}")
            else:
                self.logger.warning("position_mgr 没有 risk_controller 属性，无法配置风控")
        else:
            self.logger.info("配置中未找到风控配置，使用默认风控参数")

    def initialize(self):
        """初始化策略 -- 基类的初始化函数暂时无人调用 这儿不要再给里面加代码了 直接在框架层调用你需要的初始化函数"""
        if hasattr(self, '_initialized') and self._initialized:
            return
        # 标记为已初始化
        self._initialized = True
            
        # 初始化子类
        self._init_strategy()


class TradingFramework:
    """交易框架，管理策略和信号处理"""
    
    def __init__(self, app_name: str, strategy_class: Type[BaseStrategy], config: Dict[str, Any]):
        """
        初始化交易框架
        
        Args:
            app_name: 应用名称
            strategy_class: 策略类，必须继承自BaseStrategy
            config: 配置信息
        """
        self.app_name = app_name
        self.config = config
        self.strategy_config = config.get('strategy', {})
        
        # 配置日志
        self.logger = logging.getLogger(app_name)
        self.logger.info(f"交易框架初始化，策略配置: {self.strategy_config}")
        
        # 检查并显示阶梯止盈配置
        ladder_tp_config = self.strategy_config.get('ladder_take_profit', {})
        self.logger.info(f"阶梯止盈配置: {ladder_tp_config}")
        
        # 初始化组件
        self.trader = OKExTrader(app_name, self.config['exchange'])
        self.data_cache = OKExDataCache.from_config(self.config)
        self.position_mgr = PositionManager(app_name=app_name, logger=self.logger, trader=self.trader, data_cache=self.data_cache)

        # 初始化市场数据订阅器，使用OKEx特定的实现
        self.market_subscriber = OKExMarketSubscriber(self.data_cache, self.config, app_name)
        # 注意：这里不启动market_subscriber，而是在run_forever中启动
        
        # 初始化策略
        self.strategy = strategy_class(app_name, self.trader, self.position_mgr, self.data_cache, self.config, self.market_subscriber)
        
        # 初始化风控系统的持仓数量
        if hasattr(self.position_mgr, 'risk_controller'):
            # 计算实际活跃仓位数量
            active_positions = sum(1 for p in self.strategy.get_positions().items() if not getattr(p, 'closed', False))
            # 设置风控系统中的持仓数量
            self.position_mgr.risk_controller.set_positions_count(active_positions)
            self.logger.info(f"初始化风控持仓数量: {active_positions}（总仓位数: {len(self.strategy.get_positions())}）")
            
            # 确保风控系统有数据缓存的引用
            if hasattr(self.position_mgr.risk_controller, 'set_data_cache'):
                self.position_mgr.risk_controller.set_data_cache(self.data_cache)
                self.logger.info("已为风控系统设置数据缓存引用")
    
    async def process_signal(self, signal_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        处理原始信号数据
        
        Args:
            signal_data: 原始信号数据
            
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        return await self.strategy.process_signal(signal_data)
    
    async def run_forever(self, position_monitor_interval: int = 30, restart_after_errors=True, max_errors=10, error_throttle_seconds=10):
        """
        运行框架的主循环
        
        Args:
            position_monitor_interval: 监控持仓的间隔时间（秒）
            restart_after_errors(bool): 是否在错误后自动重启
            max_errors(int): 最大错误次数，超过后不再重启
            error_throttle_seconds(int): 错误后等待重启的秒数
        """
        # 记录最后风控重置日期
        last_reset_date = datetime.now().date()
        self.logger.info(f"初始化风控重置日期: {last_reset_date} (本地时间)")
        
        self.logger.info(f"启动交易框架 {self.app_name}")
        
        # 设置监控间隔
        self.monitor_interval = position_monitor_interval
        self.logger.info(f"持仓监控间隔: {position_monitor_interval}秒")
        
        # 初始化推送组件
        await self.market_subscriber.start()

        error_count = 0
        while True:
            try:
                # 检查是否需要重置日期计数器（本地时间的午夜）
                current_date = datetime.now().date()
                if current_date > last_reset_date:
                    self.logger.info(f"检测到日期变更: {last_reset_date} -> {current_date} (本地时间), 重置风控每日计数器")
                    if hasattr(self.position_mgr, 'risk_controller'):
                        # 重置每日计数器，但保留持仓数
                        current_positions = self.position_mgr.risk_controller.current_positions_count
                        self.position_mgr.risk_controller.reset_daily_counters()
                        # 确保持仓数保持不变
                        self.position_mgr.risk_controller.set_positions_count(current_positions)
                        self.logger.info(f"已重置风控每日计数器，保留持仓数: {current_positions}")
                    else:
                        self.logger.warning(f"无法重置风控每日计数器，risk_controller不存在")
                    last_reset_date = current_date

                # 实现每分钟执行一次_sync_positions_task
                current_minute = datetime.now().minute
                if not hasattr(self, '_last_sync_minute') or self._last_sync_minute != current_minute:
                    self._last_sync_minute = current_minute
                    self.logger.debug(f"执行每分钟同步任务，当前分钟: {current_minute}")
                    try:
                        await self.strategy._sync_positions_task()
                    except Exception as e:
                        self.logger.error(f"每分钟同步任务执行错误: {e}", exc_info=True)
                
                # 监控持仓
                await self.strategy.monitor_positions()
                
                # 休眠一段时间
                await asyncio.sleep(self.monitor_interval)
                
            except KeyboardInterrupt:
                self.logger.info("收到键盘中断信号，退出策略运行")
                
            except Exception as e:
                error_count += 1
                self.logger.error(f"策略运行异常: {e}")
                import traceback
                self.logger.error(traceback.format_exc())
                
                # 如果达到最大错误次数，退出
                if error_count >= max_errors:
                    self.logger.error(f"达到最大错误次数 {max_errors}，退出策略运行")
                    break
                    
                # 如果不自动重启，退出
                if not restart_after_errors:
                    self.logger.error("策略配置为不自动重启，退出策略运行")
                    break
                    
                # 等待一段时间后重启
                self.logger.info(f"等待 {error_throttle_seconds} 秒后重启策略")
                await asyncio.sleep(error_throttle_seconds)
    
    async def manual_trigger(self, signal: TradeSignal) -> Tuple[bool, str]:
        """
        手动触发信号
        
        Args:
            signal: 交易信号
            
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        return await self.strategy.manual_trigger(signal)
    
    async def manual_close_all(self) -> Tuple[bool, str]:
        """
        手动关闭所有持仓
        
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        return await self.strategy.manual_close_all()
    
    async def get_status(self) -> Dict[str, Any]:
        """
        获取交易框架状态
        
        Returns:
            Dict: 状态信息 
        """
        # 获取最新持仓数据
        positions = self.strategy.get_positions()
        
        # 转为列表格式
        positions_list = []
        for symbol, position in positions.items():
            if position.closed:
                continue  # 跳过已关闭的仓位
            
            # 获取信号信息
            signal_info = None
            if hasattr(position, 'signal') and position.signal:
                signal_info = {
                    'action': position.signal.action,
                    'direction': position.signal.direction,
                    'entry_price': position.signal.entry_price,
                    'quantity': position.signal.quantity,
                    'take_profit_pct': position.signal.take_profit_pct,
                    'stop_loss_pct': position.signal.stop_loss_pct,
                    'trailing_stop': position.signal.trailing_stop,
                    'trailing_distance': position.signal.trailing_distance
                }
            
            # 获取当前市场价格计算最新的未实现盈亏
            try:
                mark_price = self.data_cache.get_mark_price_sync(symbol)
                contract_size = self.strategy.get_contract_size_sync(symbol)
                
                # 计算盈亏百分比 - 不考虑杠杆
                if position.direction == "long":
                    pnl_pct = (mark_price - position.entry_price) / position.entry_price
                else:  # short
                    pnl_pct = (position.entry_price - mark_price) / position.entry_price
                
                # 计算带杠杆的盈亏百分比
                leveraged_pnl_pct = pnl_pct * position.leverage
                
                # 计算盈亏金额
                # 合约价值 = 数量 * 入场价格 * 合约面值
                contract_value = abs(position.quantity) * position.entry_price * contract_size
                
                # 保证金 = 合约价值 / 杠杆倍数
                margin = contract_value / position.leverage
                
                # 实际盈亏金额（考虑杠杆）
                pnl_amount = margin * leveraged_pnl_pct
                
                # 获取已实现盈亏
                realized_pnl = getattr(position, 'realized_pnl', 0.0)
                
                # 计算总收益（未实现+已实现）
                total_pnl = pnl_amount + realized_pnl
                
                # 计算止盈止损价格 - 获取止盈止损百分比
                signal = position.signal if hasattr(position, 'signal') else None
                take_profit_pct = signal.take_profit_pct if signal and hasattr(signal, 'take_profit_pct') and signal.take_profit_pct is not None else self.strategy.take_profit_pct
                stop_loss_pct = signal.stop_loss_pct if signal and hasattr(signal, 'stop_loss_pct') and signal.stop_loss_pct is not None else self.strategy.stop_loss_pct
                
                # 记录原始止盈止损比例 (Web界面计算)
                self.logger.info(f"【Web界面】{symbol} 原始止盈止损比例: 止盈={take_profit_pct*100:.2f}%, 止损={stop_loss_pct*100:.2f}%")
                
                # 如果有杠杆，调整百分比（与开仓逻辑一致）
                if position.leverage > 1:
                    take_profit_pct = take_profit_pct / position.leverage
                    stop_loss_pct = stop_loss_pct / position.leverage
                    self.logger.info(f"【Web界面】{symbol} 杠杆调整后止盈止损比例: 止盈={take_profit_pct*100:.2f}%, 止损={stop_loss_pct*100:.2f}%")
                
                # 计算实际止盈止损价格
                if position.direction == "long":
                    take_profit_price = position.entry_price * (1 + take_profit_pct)
                    stop_loss_price = position.entry_price * (1 - stop_loss_pct)
                else:  # short
                    take_profit_price = position.entry_price * (1 - take_profit_pct)
                    stop_loss_price = position.entry_price * (1 + stop_loss_pct)
                
                self.logger.info(f"【Web界面】{symbol} 计算的止盈止损价格: 入场={position.entry_price:.4f}, 止盈={take_profit_price:.4f}, 止损={stop_loss_price:.4f}")
                
                # 更新计算的PnL数据
                current_price = mark_price
                unrealized_pnl = pnl_amount
            except Exception as e:
                self.logger.warning(f"计算{symbol}盈亏异常: {e}")
                current_price = 0
                unrealized_pnl = 0
                total_pnl = getattr(position, 'realized_pnl', 0.0)
                take_profit_price = 0
                stop_loss_price = 0
            
            # 添加持仓信息
            pos_data = {
                'symbol': symbol,
                'position_id': position.position_id,
                'entry_price': position.entry_price,
                'quantity': position.quantity,
                'margin': position.margin,
                'position_type': position.position_type,
                'leverage': position.leverage,
                'timestamp': position.timestamp,
                'direction': position.direction,
                'high_price': position.high_price if hasattr(position, 'high_price') else 0,
                'low_price': position.low_price if hasattr(position, 'low_price') else 0,
                'ladder_tp': position.ladder_tp if hasattr(position, 'ladder_tp') else False,
                'ladder_tp_pct': position.ladder_tp_pct if hasattr(position, 'ladder_tp_pct') else 0,
                'ladder_tp_step': position.ladder_tp_step if hasattr(position, 'ladder_tp_step') else 0,
                'ladder_closed_pct': position.ladder_closed_pct if hasattr(position, 'ladder_closed_pct') else 0,
                'realized_pnl': position.realized_pnl if hasattr(position, 'realized_pnl') else 0,
                'unrealized_pnl': unrealized_pnl,
                'pnl_amount': unrealized_pnl,
                'total_pnl': total_pnl,
                'current_price': current_price,
                'leveraged_pnl_pct': leveraged_pnl_pct if 'leveraged_pnl_pct' in locals() else 0,
                'take_profit_price': take_profit_price if 'take_profit_price' in locals() else 0,
                'stop_loss_price': stop_loss_price if 'stop_loss_price' in locals() else 0,
                'signal': signal_info
            }
            positions_list.append(pos_data)
            
            # 添加日志以便调试
            self.logger.info(f"持仓信息 {symbol}: ladder_tp={position.ladder_tp}, 止盈比例={position.ladder_tp_pct}, 档位间隔={position.ladder_tp_step}")
        
        # 统计持仓信息
        positions_info = {
            'count': len(positions_list),
            'positions': positions_list
        }
        
        # 基础信息
        status = {
            "app_name": self.app_name,
            "positions": positions_info,
            "strategy_status": self.strategy._strategy_status,
            "status_message": self.strategy._status_message,
            "config": {
                "leverage": self.strategy.leverage,
                "per_position_usdt": self.strategy.per_position_usdt,
                "take_profit_pct": self.strategy.take_profit_pct,
                "stop_loss_pct": self.strategy.stop_loss_pct,
                "trailing_stop": self.strategy.trailing_stop,
                "trailing_distance": self.strategy.trailing_distance,
                "unit_type": self.strategy.unit_type,
                "enable_symbol_pool": self.strategy.enable_symbol_pool,
                "allowed_symbols": list(self.strategy.allowed_symbols) if self.strategy.enable_symbol_pool else "全部"
            }
        }
        
        # 子类可以在重写此方法中添加额外信息
        return status
        
    async def get_daily_pnl(self, start_date: str = None, end_date: str = None) -> List[Dict]:
        """
        获取每日收益统计
        
        Args:
            start_date: 开始日期，格式为 YYYY-MM-DD，默认为7天前
            end_date: 结束日期，格式为 YYYY-MM-DD，默认为今天
            
        Returns:
            List[Dict]: 每日收益统计列表
        """
        return await self.strategy.get_daily_pnl(start_date, end_date)
    
    async def get_position_history(self, start_date: str = None, end_date: str = None, 
                                  symbol: str = None, limit: int = 100) -> List[Dict]:
        """
        获取历史仓位记录
        
        Args:
            start_date: 开始日期，格式为 YYYY-MM-DD，默认为30天前
            end_date: 结束日期，格式为 YYYY-MM-DD，默认为今天
            symbol: 交易对，默认为所有
            limit: 最大返回记录数，默认100条
            
        Returns:
            List[Dict]: 历史仓位记录列表
        """
        return await self.strategy.get_position_history(start_date, end_date, symbol, limit)
