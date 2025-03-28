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
        # 获取最新持仓数据
        positions = self.get_positions()
        
        # 检查信号有效性
        if not signal.symbol or not signal.direction:
            return False, "缺少交易对或方向"
        
        # 检查是否已经有此交易对的持仓
        if signal.symbol in positions and not positions[signal.symbol].closed:
            # 如果现有持仓方向与信号方向相同，返回提示
            if (positions[signal.symbol].direction == signal.direction or
                (positions[signal.symbol].quantity > 0 and signal.direction == "long") or
                (positions[signal.symbol].quantity < 0 and signal.direction == "short")):
                return False, f"已存在同向持仓: {signal.symbol}"
            # 如果现有持仓方向与信号方向相反，返回提示
            else:
                return False, f"已存在反向持仓: {signal.symbol}"
        
        self.update_strategy_status(StrategyStatus.HANDLING_SIGNAL, f"处理开仓信号: {signal.symbol}")
        
        # 检查交易对是否允许交易
        if not self._validate_symbol(signal.symbol):
            return False, f"交易对 {signal.symbol} 不在允许列表中"
        
        # 创建开仓前置检查列表
        pre_checks = []
        
        # 添加风控检查 - 无论是否有风控参数都进行检查
        # 进行风控检查
        passed, reason = await self.position_mgr.check_risk_control(signal.symbol, signal.extra_data)
        if not passed:
            return False, f"风控检查不通过: {reason}"
        
        # 只有通过了风控检查才记录交易
        pre_checks.append("风控检查通过")
        
        # 设置合约类型
        is_spot = self.strategy_config.get('is_spot', False)
        position_type = "spot" if is_spot else "swap"
        
        # 设置杠杆
        leverage = signal.leverage or self._get_leverage(signal)
        
        # 生成仓位ID
        position_id = f"{signal.symbol}_{signal.direction}_{int(time.time()*1000)}"
        
        # 设置开仓方向
        side = "buy" if signal.direction == "long" else "sell"
        
        # 设置开仓价格
        entry_price = None
        if signal.entry_price:
            entry_price = signal.entry_price
        else:
            # 获取市场价格
            entry_price = await self._get_entry_price(signal)
        
        self.logger.info(f"开仓信息: {signal.symbol} {position_type} {side}", extra={
            "entry_price": entry_price,
            "leverage": leverage,
            "checks": pre_checks
        })
        
        # 计算开仓数量
        quantity = None
        
        # 检查是否已指定数量
        if signal.quantity:
            quantity = signal.quantity
        else:
            # 获取仓位大小
            position_usdt = self._get_position_usdt(signal)
            unit_type = self._get_unit_type(signal)
            
            # 计算开仓数量
            try:
                quantity, tgt_ccy = calculate_order_size(
                    self.trader, 
                    symbol=signal.symbol, 
                    position_usdt=position_usdt,
                    unit_type=unit_type, 
                    leverage=leverage,
                    side=side
                )
            except Exception as e:
                self.logger.error(f"计算开仓数量异常: {e}", exc_info=True)
                quantity = None
        
        if not quantity or quantity <= 0:
            return False, f"开仓数量计算失败, 获取到的数量为: {quantity}"
        
        # 设置止盈止损
        take_profit_pct = signal.take_profit_pct if signal.take_profit_pct is not None else self.take_profit_pct
        stop_loss_pct = signal.stop_loss_pct if signal.stop_loss_pct is not None else self.stop_loss_pct
        trailing_stop = signal.trailing_stop if signal.trailing_stop is not None else self.trailing_stop
        trailing_distance = signal.trailing_distance if signal.trailing_distance is not None else self.trailing_distance
        
        # 记录原始止盈止损比例（不涉及 entry_price，所以可以安全地记录）
        self.logger.info(f"【开仓】{signal.symbol} 原始止盈止损比例: 止盈={take_profit_pct*100:.2f}%, 止损={stop_loss_pct*100:.2f}%")
        
        # 检查信号中是否包含阶梯止盈设置
        if signal.extra_data and 'ladder_tp' in signal.extra_data:
            ladder_tp = signal.extra_data['ladder_tp']
            ladder_tp_pct = signal.extra_data.get('ladder_tp_pct', 0.2)
            ladder_tp_step = signal.extra_data.get('ladder_tp_step', 0.2)
            self.logger.info(f"使用信号中的阶梯止盈: 启用={ladder_tp}, 步长={ladder_tp_step}, 每步平仓比例={ladder_tp_pct}")
        # 如果信号中没有设置，检查全局配置
        elif 'ladder_take_profit' in self.strategy_config:
            ladder_config = self.strategy_config['ladder_take_profit']
            ladder_tp = ladder_config.get('enabled', False)
            ladder_tp_pct = ladder_config.get('close_pct', 0.2)
            ladder_tp_step = ladder_config.get('step_pct', 0.2)
            self.logger.info(f"使用全局配置的阶梯止盈: 启用={ladder_tp}, 步长={ladder_tp_step}, 每步平仓比例={ladder_tp_pct}")
            self.logger.debug(f"全局阶梯止盈配置详情: {self.strategy_config['ladder_take_profit']}")
        else:
            ladder_tp = False
            ladder_tp_pct = 0.2
            ladder_tp_step = 0.2
            self.logger.warning("未找到阶梯止盈设置，使用默认值(禁用状态)")
        
        # 如果有杠杆交易，调整止盈止损比例
        if not is_spot and leverage > 1:
            # 调整止盈止损比例（除以杠杆倍数）
            take_profit_pct = take_profit_pct / leverage
            stop_loss_pct = stop_loss_pct / leverage
            self.logger.info(f"【开仓】{signal.symbol} 杠杆调整后止盈止损比例: 止盈={take_profit_pct*100:.2f}%, 止损={stop_loss_pct*100:.2f}%")
            
            # 确保有入场价格
            if signal.entry_price is None:
                self.logger.warning(f"【开仓】{signal.symbol} 信号中未指定入场价格，无法计算止盈止损价格")
            else:
                # 计算实际止盈止损价格和触发方向
                if signal.direction == "long":
                    tp_price = signal.entry_price * (1 + take_profit_pct)
                    sl_price = signal.entry_price * (1 - stop_loss_pct)
                    self.logger.info(f"【开仓】{signal.symbol} 多头止盈止损价格: 入场={signal.entry_price:.4f}, 止盈={tp_price:.4f}, 止损={sl_price:.4f}")
                else:
                    tp_price = signal.entry_price * (1 - take_profit_pct)
                    sl_price = signal.entry_price * (1 + stop_loss_pct)
                    self.logger.info(f"【开仓】{signal.symbol} 空头止盈止损价格: 入场={signal.entry_price:.4f}, 止盈={tp_price:.4f}, 止损={sl_price:.4f}")
        
        self.update_strategy_status(StrategyStatus.OPENING_POSITION, f"开仓: {signal.symbol} {side}")
        
        # 设置杠杆
        if not is_spot and leverage > 1:
            try:
                self.logger.info(f"设置杠杆: {signal.symbol} {leverage}倍")
                set_leverage_result = self.trader.set_leverage(
                    inst_id=signal.symbol,
                    leverage=leverage
                )
                
                if not set_leverage_result or 'error' in set_leverage_result:
                    error_msg = set_leverage_result.get('error', '未知错误')
                    self.logger.error(f"设置杠杆失败: {error_msg}")
            except Exception as e:
                self.logger.error(f"设置杠杆异常: {e}", exc_info=True)
        
        # 执行市价开仓
        try:
            order_result = self.trader.order_market(
                symbol=signal.symbol,
                side=side,
                size=quantity,
                is_spot=is_spot
            )
            
            self.logger.debug(f"开仓结果: {order_result}")
            
            if not order_result or 'error' in order_result:
                error_msg = order_result.get('error', '未知错误')
                self.logger.error(f"开仓失败: {error_msg}")
                self.update_strategy_status(StrategyStatus.ERROR, f"开仓失败: {error_msg}")
                return False, f"开仓失败: {error_msg}"
            
            # 获取实际成交价格
            actual_price = float(order_result.get('data', [{}])[0].get('fillPrice', 0))
            if actual_price <= 0:
                actual_price = entry_price
            
            # 保存仓位信息
            position = Position(
                symbol=signal.symbol,
                position_id=position_id,
                entry_price=actual_price,
                quantity=quantity,
                position_type=position_type,
                leverage=leverage,
                timestamp=int(time.time() * 1000),
                direction=signal.direction,
                ladder_tp=ladder_tp,
                ladder_tp_pct=ladder_tp_pct,
                ladder_tp_step=ladder_tp_step,
                high_price=actual_price,
                low_price=actual_price,
                signal=signal
            )
            self.position_mgr.save_position(position)
            
            # 订阅交易对行情
            self._subscribe_market_data(signal.symbol)

            # 记录风控信息
            if hasattr(self.position_mgr, 'risk_controller'):
                self.position_mgr.risk_controller.record_trade(signal.symbol)

            await self._sync_positions_task();
            
            self.update_strategy_status(StrategyStatus.POSITION_OPENED, f"开仓完成: {signal.symbol} at {actual_price}")
            
            return True, f"开仓成功: {signal.symbol} {side} 价格: {actual_price} 数量: {quantity}"
        except Exception as e:
            self.logger.exception(f"执行开仓异常: {e}")
            self.update_strategy_status(StrategyStatus.ERROR, f"开仓异常: {e}")
            return False, f"开仓异常: {e}"
    
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
                    
                    # 添加诊断日志 - 调整前的原始止盈止损比例
                    # self.logger.info(f"【诊断】{symbol} 调整前的原始止盈止损百分比: 止盈={take_profit_pct*100:.2f}%, 止损={stop_loss_pct*100:.2f}%")
                    
                    trailing_stop = signal.trailing_stop if signal and hasattr(signal, 'trailing_stop') and signal.trailing_stop is not None else self.trailing_stop
                    trailing_distance = signal.trailing_distance if signal and hasattr(signal, 'trailing_distance') and signal.trailing_distance is not None else self.trailing_distance
                    
                    if position.leverage > 1:
                        take_profit_pct = take_profit_pct / position.leverage
                        stop_loss_pct = stop_loss_pct  / position.leverage
                        trailing_distance = trailing_distance / position.leverage
                    self.logger.info(f"【诊断】{symbol} 调整后的止盈止损百分比: 止盈={take_profit_pct*100:.2f}%, 止损={stop_loss_pct*100:.2f}% 跟踪止损={trailing_distance*100:.2f}%")

                    # 调试日志 - 阶梯止盈
                    ladder_tp = getattr(position, 'ladder_tp', False)
                    ladder_tp_pct = getattr(position, 'ladder_tp_pct', 0.2)
                    ladder_tp_step = getattr(position, 'ladder_tp_step', 0.2)
                    self.logger.info(f"持仓 {symbol} 的阶梯止盈设置: 启用={ladder_tp}, 每档比例={ladder_tp_pct}, 档位间隔={ladder_tp_step}")
                    
                    # 更新最高/最低价格
                    if direction == "long":
                        # 多头情况下更新最高价
                        if mark_price > position.high_price:
                            position.high_price = mark_price
                            self.logger.info(f"{symbol} 创新高: {mark_price}")
                        
                        # 计算止盈价和止损价
                        take_profit_price = position.entry_price * (1 + take_profit_pct)
                        stop_loss_price = position.entry_price * (1 - stop_loss_pct)
                        
                        # 如果开启了追踪止损，计算追踪止损价格
                        trailing_stop_price = None
                        if trailing_stop and pnl_pct > 0:
                            trailing_stop_price = position.high_price * (1 - trailing_distance)
                            # 如果追踪止损价格高于原止损价，则使用追踪止损价格
                            if trailing_stop_price > stop_loss_price:
                                stop_loss_price = trailing_stop_price
                                self.logger.debug(f"{symbol} 多头追踪止损价格: {stop_loss_price}")
                    else:  # short
                        # 空头情况下更新最低价
                        if mark_price < position.low_price or position.low_price == 0:
                            position.low_price = mark_price
                            self.logger.info(f"{symbol} 创新低: {mark_price}")
                        
                        # 计算止盈价和止损价
                        take_profit_price = position.entry_price * (1 - take_profit_pct)
                        stop_loss_price = position.entry_price * (1 + stop_loss_pct)
                        
                        # 如果开启了追踪止损，计算追踪止损价格
                        trailing_stop_price = None
                        if trailing_stop and pnl_pct > 0:
                            trailing_stop_price = position.low_price * (1 + trailing_distance)
                            # 如果追踪止损价格低于原止损价，则使用追踪止损价格
                            if trailing_stop_price < stop_loss_price:
                                stop_loss_price = trailing_stop_price
                                self.logger.debug(f"{symbol} 空头追踪止损价格: {stop_loss_price}")
                    
                    # 添加诊断日志 - 最终止盈止损价格
                    self.logger.info(f"【诊断】{symbol} 最终止盈止损价格: 止盈={take_profit_price:.4f}, 止损={stop_loss_price:.4f}")
                    
                    # 检查时间止损条件
                    time_stop_triggered = await self._check_time_based_stop_loss(symbol, position)
                    if time_stop_triggered:
                        self.logger.info(f"{symbol} 触发时间止损，执行平仓")
                        success, msg = await self._execute_close_position(symbol, position)
                        if success:
                            self.logger.info(f"{symbol} 时间止损平仓成功: {msg}")
                        else:
                            self.logger.error(f"{symbol} 时间止损平仓失败: {msg}")
                        continue
                    
                    # 检查是否触发止盈止损条件
                    await self._check_take_profit_stop_loss(position, mark_price, take_profit_price, stop_loss_price)
                    
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
    
    async def _check_take_profit_stop_loss(self, position: Position, mark_price: float, 
                                          take_profit_price: float, stop_loss_price: float):
        """
        检查是否触发止盈止损条件
        
        Args:
            position: 仓位信息
            mark_price: 当前价格
            take_profit_price: 止盈价格
            stop_loss_price: 止损价格
        """
        # 确保position有symbol属性
        symbol = position.symbol if hasattr(position, 'symbol') else None
        if not symbol:
            self.logger.error("Position缺少symbol属性")
            return
            
        # 确保position有direction属性
        if not hasattr(position, 'direction') or position.direction is None:
            position.direction = "long" if position.quantity > 0 else "short"
        
        # 添加debug日志，显示止盈止损价格
        self.logger.info(f"【止盈止损检查】{symbol} 当前价格: {mark_price:.4f}, 止盈价: {take_profit_price:.4f}, 止损价: {stop_loss_price:.4f}")
        
        # 检查是否需要止损
        if position.direction == 'long' and mark_price <= stop_loss_price:
            self.logger.info(f"{symbol} 触发止损，价格: {mark_price} <= {stop_loss_price}")
            success, msg = await self._execute_close_position(symbol, position)
            if success:
                self.logger.info(f"{symbol} 止损平仓成功: {msg}")
            else:
                self.logger.error(f"{symbol} 止损平仓失败: {msg}")
            return
        elif position.direction == 'short' and mark_price >= stop_loss_price:
            self.logger.info(f"{symbol} 触发止损，价格: {mark_price} >= {stop_loss_price}")
            success, msg = await self._execute_close_position(symbol, position)
            if success:
                self.logger.info(f"{symbol} 止损平仓成功: {msg}")
            else:
                self.logger.error(f"{symbol} 止损平仓失败: {msg}")
            return
            
        # 检查阶梯止盈
        ladder_tp = getattr(position, 'ladder_tp', False)
        if ladder_tp and hasattr(position, 'ladder_tp_pct') and hasattr(position, 'ladder_tp_step'):
            ladder_tp_pct = position.ladder_tp_pct
            ladder_tp_step = position.ladder_tp_step
            ladder_closed_pct = getattr(position, 'ladder_closed_pct', 0.0)
            
            # 计算当前盈利百分比 - 使用杠杆后的收益率
            if position.direction == 'long':
                current_pnl_pct = (mark_price - position.entry_price) / position.entry_price * position.leverage
            else:  # short
                current_pnl_pct = (position.entry_price - mark_price) / position.entry_price * position.leverage
            
            # 计算应该触发的阶梯级别（向下取整）
            current_ladder_level = int(current_pnl_pct / ladder_tp_step)
            
            # 计算该级别对应的应平仓百分比
            target_closed_pct = current_ladder_level * ladder_tp_pct
            
            # 确保不超过1.0（全部平仓）
            target_closed_pct = min(target_closed_pct, 1.0)
            
            # 检查是否有新的平仓需求
            if current_ladder_level > 0 and target_closed_pct > ladder_closed_pct:
                # 计算本次需要平仓的百分比
                new_close_pct = target_closed_pct - ladder_closed_pct
                
                # 计算本次平仓数量
                close_quantity = abs(position.quantity) * new_close_pct
                
                # 获取合约信息，用于圆整数量
                try:
                    contract_info = self.trader.get_contract_info(symbol, False)["data"][0]
                    
                    # 获取最小交易单位
                    lot_size = float(contract_info['lotSz']) if 'lotSz' in contract_info else 1
                    
                    # 计算精度
                    if '.' in str(contract_info.get('lotSz', '1')):
                        precision = str(contract_info['lotSz']).split('.')[1].find('1') + 1
                    else:
                        precision = 0
                    
                    # 圆整平仓数量
                    close_quantity = round(close_quantity / lot_size) * lot_size
                    close_quantity = round(close_quantity, precision)
                    
                    self.logger.info(f"{symbol} 阶梯止盈平仓数量 (已圆整): {close_quantity} ({new_close_pct*100:.2f}%)")
                except Exception as e:
                    self.logger.warning(f"获取合约信息圆整数量失败，使用原始数量: {e}")
                
                # 确定平仓方向（与持仓方向相反）
                side = "sell" if position.direction == "long" else "buy"
                pos_side = position.direction
                
                # 如果是同时触发阶梯止盈和常规止盈，则直接执行全部平仓
                should_full_close = False
                if (position.direction == 'long' and mark_price >= take_profit_price) or \
                   (position.direction == 'short' and mark_price <= take_profit_price):
                    self.logger.info(f"{symbol} 同时触发阶梯止盈和常规止盈，执行全部平仓")
                    should_full_close = True
                
                if should_full_close:
                    # 执行全部平仓
                    success, msg = await self._execute_close_position(symbol, position)
                    if success:
                        self.logger.info(f"{symbol} 全部平仓成功: {msg}")
                    else:
                        self.logger.error(f"{symbol} 全部平仓失败: {msg}")
                    return
                
                # 执行部分平仓
                close_result = self.trader.swap_order(
                    inst_id=symbol,
                    side=side,
                    pos_side=pos_side,
                    sz=close_quantity
                )
                
                if close_result and close_result.get("code") == "0":
                    # 更新已平仓百分比
                    position.ladder_closed_pct = target_closed_pct
                    
                    # 更新持仓量
                    original_quantity = position.quantity
                    new_quantity = position.quantity * (1 - new_close_pct)
                    
                    if position.direction == "long":
                        new_quantity = min(new_quantity, original_quantity)  # 确保不会增加持仓
                    else:  # short
                        new_quantity = max(new_quantity, original_quantity)  # 确保不会增加持仓
                    
                    try:
                        # 圆整更新后的持仓数量
                        if position.direction == "long":
                            new_quantity = round(new_quantity / lot_size) * lot_size
                            new_quantity = round(new_quantity, precision)
                        else:  # short
                            new_quantity = round(new_quantity / lot_size) * lot_size
                            new_quantity = round(new_quantity, precision)
                        
                    except Exception as e:
                        position.quantity = new_quantity
                        self.logger.warning(f"圆整更新后的持仓数量失败，使用原始计算结果: {e}")
                    
                    # 计算部分平仓收益（估算）
                    # 合约价值 = 数量 * 入场价格 * 合约面值
                    contract_size = self.get_contract_size_sync(symbol)
                    closed_contract_value = close_quantity * position.entry_price * contract_size
                    
                    # 计算杠杆收益率
                    if position.direction == 'long':
                        pnl_pct = (mark_price - position.entry_price) / position.entry_price
                    else:  # short
                        pnl_pct = (position.entry_price - mark_price) / position.entry_price
                    
                    # 带杠杆的收益率
                    leveraged_pnl_pct = pnl_pct * position.leverage
                    
                    closed_margin = closed_contract_value / position.leverage
                    closed_pnl = closed_margin * leveraged_pnl_pct
                    
                    # 记录部分平仓信息
                    self.logger.info(f"{symbol} 部分平仓成功，平仓数量: {close_quantity}, 剩余持仓: {abs(new_quantity)}")
                    self.logger.info(f"{symbol} 部分平仓收益: {closed_pnl:.2f} USDT ({leveraged_pnl_pct*100:.2f}%)")
                    
                    # 更新持仓数量
                    position.quantity = new_quantity
                    
                    # 累计已实现收益 - 如果属性不存在则初始化为0
                    if not hasattr(position, 'realized_pnl'):
                        position.realized_pnl = 0.0
                    position.realized_pnl += closed_pnl
                    self.logger.info(f"{symbol} 累计已实现收益: {position.realized_pnl:.2f} USDT")

                    # 通知风控系统部分平仓信息
                    if hasattr(self.position_mgr, 'risk_controller'):
                        self.position_mgr.risk_controller.record_close_position(symbol, is_partial_close=True)
                        self.logger.debug(f"已通知风控系统部分平仓: {symbol}")
                    
                    # 检查是否已全部平仓
                    if abs(position.quantity) < 0.0001 or target_closed_pct >= 0.9999:
                        self.logger.info(f"{symbol} 持仓已全部平仓")
                        position.closed = True
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
                else:
                    error_msg = close_result.get('msg', 'Unknown error') if close_result else 'No response'
                    self.logger.error(f"部分平仓失败: {error_msg}")
                    self.logger.warning(f"部分平仓失败，尝试全部平仓")
                    success, msg = await self._execute_close_position(symbol, position)
                    if success:
                        self.logger.info(f"{symbol} 全部平仓成功: {msg}")
                    else:
                        self.logger.error(f"{symbol} 全部平仓失败: {msg}")
                
                return
        
        # 检查常规止盈
        if position.direction == 'long' and mark_price >= take_profit_price:
            self.logger.info(f"{symbol} 触发止盈，价格: {mark_price} >= {take_profit_price}")
            success, msg = await self._execute_close_position(symbol, position)
            if success:
                self.logger.info(f"{symbol} 止盈平仓成功: {msg}")
            else:
                self.logger.error(f"{symbol} 止盈平仓失败: {msg}")
            return
        elif position.direction == 'short' and mark_price <= take_profit_price:
            self.logger.info(f"{symbol} 触发止盈，价格: {mark_price} <= {take_profit_price}")
            success, msg = await self._execute_close_position(symbol, position)
            if success:
                self.logger.info(f"{symbol} 止盈平仓成功: {msg}")
            else:
                self.logger.error(f"{symbol} 止盈平仓失败: {msg}")
            return
    
    async def _execute_close_position(self, symbol: str, position: Position) -> Tuple[bool, str]:
        """
        执行平仓操作
        
        Args:
            symbol: 交易对
            position: 仓位对象
            
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        try:
            # 获取当前价格
            mark_price = await self.data_cache.get_mark_price(symbol)
            if not mark_price:
                self.logger.warning(f"无法获取 {symbol} 的行情价格，使用上次价格平仓")
                
            # 确定持仓方向
            pos_side = "long" if position.quantity > 0 else "short"
            # 平仓方向与开仓相反
            side = "sell" if position.quantity > 0 else "buy"
            
            # 处理数量精度，避免浮点数精度问题
            close_quantity = abs(position.quantity)
            
            # 获取合约信息，用于圆整数量
            try:
                contract_info = self.trader.get_contract_info(symbol, False)["data"][0]
                
                # 获取最小交易单位
                lot_size = float(contract_info['lotSz']) if 'lotSz' in contract_info else 1
                
                # 计算精度
                if '.' in str(contract_info.get('lotSz', '1')):
                    precision = str(contract_info['lotSz']).split('.')[1].find('1') + 1
                else:
                    precision = 0
                
                # 圆整平仓数量
                close_quantity = round(close_quantity / lot_size) * lot_size
                close_quantity = round(close_quantity, precision)
                
                self.logger.info(f"{symbol} 圆整平仓数量: 原始={abs(position.quantity)}, 圆整后={close_quantity}")
            except Exception as e:
                self.logger.warning(f"获取合约信息圆整数量失败，使用原始数量: {e}")
            
            # 执行平仓 - 注意这里不使用await，因为swap_order不是异步方法
            close_result = self.trader.swap_order(
                inst_id=symbol,
                side=side,
                pos_side=pos_side,
                sz=close_quantity
            )
            
            if not close_result or close_result.get("code") != "0":
                error_msg = close_result.get("msg", "未知错误") if close_result else "未知错误"
                self.logger.error(f"平仓失败: {error_msg}")
                return False, f"平仓交易执行失败: {error_msg}"
            
            # 计算收益信息
            direction = "long" if position.quantity > 0 else "short"
            if direction == "long":
                pnl_pct = (mark_price - position.entry_price) / position.entry_price
            else:  # short
                pnl_pct = (position.entry_price - mark_price) / position.entry_price
                
            # 计算带杠杆的盈亏百分比
            leveraged_pnl_pct = pnl_pct * position.leverage
            
            # 获取合约面值
            contract_size = self.get_contract_size_sync(symbol)
            
            # 计算盈亏金额
            # 合约价值 = 数量 * 入场价格 * 合约面值
            contract_value = abs(position.quantity) * position.entry_price * contract_size
            
            # 保证金 = 合约价值 / 杠杆倍数
            margin = contract_value / position.leverage
            
            # 实际盈亏金额（考虑杠杆）- 实际上是保证金 * 杠杆后的收益率
            pnl_amount = margin * leveraged_pnl_pct
            
            # 更新持仓信息
            self.logger.info(f"平仓成功 {symbol} {direction} @ {mark_price}, PnL: {pnl_amount:.2f} USDT ({leveraged_pnl_pct*100:.2f}%)")

            # 注意：此处我们只启动任务，不等待其完成，这样可以更快地返回平仓结果
            self._start_position_update_task(position.pos_id, symbol)

            # 更新数据库和风控 - 标记为已平仓
            db_updated = False
            risk_updated = False
            try:
                exit_timestamp = int(time.time() * 1000)
                # 传递position_id，确保关闭正确的仓位
                self.position_mgr.close_position(
                    symbol=symbol,
                    exit_price=mark_price,
                    exit_timestamp=exit_timestamp,
                    pnl_amount=pnl_amount,
                    pnl_percentage=leveraged_pnl_pct,
                    position_id=position.position_id
                )
                self.logger.info(f"仓位已在数据库中标记为已平仓: {symbol}, position_id: {position.position_id}")
                db_updated = True
                
                # 通知风控系统平仓信息
                if hasattr(self.position_mgr, 'risk_controller'):
                    self.position_mgr.risk_controller.record_close_position(symbol, is_partial_close=False)
                    self.logger.debug(f"已通知风控系统平仓: {symbol}")
                    risk_updated = True
            except Exception as e:
                self.logger.error(f"更新数据库或风控系统状态异常: {e}", exc_info=True)
                # 交易已成功但数据库更新失败，返回警告信息
                if not db_updated:
                    return True, f"平仓成功但数据库更新失败: {symbol} @ {mark_price}, PnL: {pnl_amount:.2f} USDT"
                elif not risk_updated:
                    return True, f"平仓成功但风控系统更新失败: {symbol} @ {mark_price}, PnL: {pnl_amount:.2f} USDT"
            
            return True, f"平仓成功: {symbol} @ {mark_price}, PnL: {pnl_amount:.2f} USDT"
        except Exception as e:
            self.logger.error(f"平仓异常: {e}", exc_info=True)
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

    async def _check_time_based_stop_loss(self, symbol: str, position: Position) -> bool:
        """
        检查基于时间的止损条件
        
        Args:
            symbol: 交易对
            position: 仓位信息
            
        Returns:
            bool: 是否触发止损
        """
        if not self.enable_time_stop:
            return False
            
        # 计算开仓以来的时间（分钟）
        current_timestamp = int(time.time() * 1000)
        position_timestamp = position.timestamp
        if position_timestamp < 9999999999:  # 如果是秒级时间戳
            position_timestamp *= 1000
        
        elapsed_minutes = (current_timestamp - position_timestamp) / (1000 * 60)
        
        # 如果没有达到检查的最小时间，直接返回
        min_check_minutes = self.time_stop_candle_timeframe * self.time_stop_candle_count
        if elapsed_minutes < min_check_minutes:
            self.logger.debug(f"{symbol} 开仓时间不足 {min_check_minutes} 分钟，跳过时间止损检查")
            return False
            
        try:
            # 获取历史K线数据
            # 从交易所获取K线数据
            candles = None
            try:
                # 获取K线数据，使用OKEx API
                # 周期格式转换为OKEx格式 (15min -> 15m)
                bar_type = f"{self.time_stop_candle_timeframe}m"
                
                # 确保 time_stop_candle_count 是整数
                candle_count = int(self.time_stop_candle_count)
                
                # 获取最近n根K线 - 使用 get_kline_data
                candles = self.trader.get_kline_data(
                    inst_id=symbol,
                    bar=bar_type,
                    limit=candle_count
                )
                
                # 调试信息
                self.logger.debug(f"获取到的K线数据: {candles}")
                
                # OKExTrader.get_kline_data方法的返回结构分析
                if not candles:
                    self.logger.warning(f"获取 {symbol} 的K线数据失败: 返回空数据")
                    return False
                
                # 检查返回的数据格式，确定是列表还是包含data字段的字典
                candle_data = None
                if isinstance(candles, dict) and 'data' in candles:
                    candle_data = candles['data']
                elif isinstance(candles, list):
                    candle_data = candles
                else:
                    self.logger.warning(f"获取 {symbol} 的K线数据格式不识别: {candles}")
                    return False
                
                # 检查是否有足够的K线数据
                if not candle_data or len(candle_data) < candle_count:
                    self.logger.warning(f"{symbol} K线数据不足 {candle_count} 根，跳过时间止损检查，获取了: {len(candle_data) if candle_data else 0} 根")
                    return False
                
                # 处理K线数据，提取收盘价
                try:
                    closing_prices = []
                    for candle in candle_data[:candle_count]:
                        if isinstance(candle, list) and len(candle) > 4 and candle[4]:
                            try:
                                closing_prices.append(float(candle[4]))
                            except (ValueError, TypeError):
                                self.logger.warning(f"{symbol} K线收盘价转换失败: {candle[4]}")
                        else:
                            self.logger.warning(f"{symbol} K线数据格式异常: {candle}")
                    
                    if len(closing_prices) < candle_count:
                        self.logger.warning(f"{symbol} 有效收盘价不足 {candle_count} 个，跳过时间止损检查")
                        return False
                    
                    # K线是按时间倒序排列的，最新的在前面，转为时间正序
                    closing_prices.reverse()
                    
                    self.logger.info(f"{symbol} 成功获取K线收盘价: {closing_prices}")
                    
                except Exception as e:
                    self.logger.error(f"{symbol} 处理K线数据异常: {e}, 原始数据: {candle_data[:candle_count]}")
                    return False
                    
                # 检查是否所有K线都没有收益
                direction = position.direction
                if direction == "long":
                    # 多头：检查所有收盘价是否都小于等于开仓价
                    no_profit = all(price <= position.entry_price for price in closing_prices)
                else:  # short
                    # 空头：检查所有收盘价是否都大于等于开仓价
                    no_profit = all(price >= position.entry_price for price in closing_prices)
                    
                if no_profit:
                    self.logger.info(f"{symbol} 连续 {self.time_stop_candle_count} 根 {self.time_stop_candle_timeframe} 分钟K线没有收益，触发时间止损")
                    self.logger.info(f"{symbol} 开仓价: {position.entry_price}, K线收盘价: {closing_prices}")
                    return True
                else:
                    self.logger.debug(f"{symbol} 未触发时间止损，开仓价: {position.entry_price}, K线收盘价: {closing_prices}")
                    return False
                    
            except Exception as e:
                self.logger.error(f"检查 {symbol} 时间止损异常: {e}")
                return False
                
        except Exception as e:
            self.logger.error(f"{symbol} 时间止损检查异常: {e}")
            return False
        
        return False


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
