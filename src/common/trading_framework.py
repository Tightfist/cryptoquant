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
                 config: Dict[str, Any]):
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
        
        # 市场数据订阅器，由TradingFramework在初始化后设置
        self.market_subscriber = None
        
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
        
        # 加载持久化的仓位信息
        self.positions = self.position_mgr.load_positions()
        self.logger = logging.getLogger(app_name)
        self.logger.info(f"加载已有仓位: {len(self.positions)}个")
        
        # 检查是否有全局阶梯止盈配置，如果有则应用到恢复的仓位中
        if 'ladder_take_profit' in self.strategy_config:
            ladder_config = self.strategy_config['ladder_take_profit']
            ladder_tp = ladder_config.get('enabled', False)
            ladder_tp_pct = ladder_config.get('close_pct', 0.2)
            ladder_tp_step = ladder_config.get('step_pct', 0.2)
            
            # 应用到已恢复的所有仓位
            if ladder_tp:
                for symbol, position in self.positions.items():
                    # 只有当仓位没有设置阶梯止盈或阶梯止盈为False时才应用全局配置
                    if not hasattr(position, 'ladder_tp') or not position.ladder_tp:
                        position.ladder_tp = ladder_tp
                        position.ladder_tp_pct = ladder_tp_pct
                        position.ladder_tp_step = ladder_tp_step
                        # 更新数据库中的仓位记录
                        self.position_mgr.save_position(position)
                        self.logger.info(f"已将全局阶梯止盈配置应用到恢复的仓位 {symbol}: 启用={ladder_tp}, 步长={ladder_tp_step}, 每步平仓比例={ladder_tp_pct}")
        
        # 初始化价格高低记录，用于追踪止损
        self._price_high_low = {}
        
        # 注意：此时market_subscriber还未设置，订阅操作将在TradingFramework初始化后执行
        # 因此，我们将订阅操作存储起来，等market_subscriber设置后再执行
        self._pending_subscriptions = set(self.positions.keys())
        
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
        
        # 内存缓存 - 避免频繁请求
        self._contract_size_cache = {}
        
        # 初始化状态
        self.status = StrategyStatus.INITIALIZED
        self.status_message = "初始化完成"
        
        # 初始化交易对池
        self._init_symbol_pool()
        
        # 初始化风控组件
        self._init_risk_controller()
    
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
        # 这个方法会被TradingFramework替换，但为了完整性，我们仍提供一个基本实现
        if self.market_subscriber:
            try:
                self.logger.info(f"开始订阅交易对: {symbol}")
                # 订阅行情
                asyncio.ensure_future(self.market_subscriber.subscribe_symbol(symbol))
                
                # 订阅持仓量（如果WebSocket客户端支持）
                if hasattr(self.market_subscriber, 'subscribe_open_interest'):
                    asyncio.ensure_future(self.market_subscriber.subscribe_open_interest(symbol))
            except Exception as e:
                self.logger.error(f"订阅 {symbol} 行情数据失败: {e}")
        else:
            # 如果market_subscriber还未设置，将订阅请求加入等待队列
            self._pending_subscriptions.add(symbol)
            self.logger.debug(f"暂存订阅请求: {symbol}")
    
    # 添加一个方法，处理等待中的订阅请求
    def _process_pending_subscriptions(self):
        """处理等待中的订阅请求"""
        if not self.market_subscriber or not self._pending_subscriptions:
            return
            
        for symbol in self._pending_subscriptions:
            self._subscribe_market_data(symbol)
            
        self._pending_subscriptions.clear()
    
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
            signal: 交易信号对象
            
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        self.update_strategy_status(StrategyStatus.HANDLING_SIGNAL, f"处理开仓信号: {signal.symbol}")
        
        # 检查交易对是否允许交易
        if not self._validate_symbol(signal.symbol):
            return False, f"交易对 {signal.symbol} 不在允许列表中"
        
        # 检查是否已有持仓
        if signal.symbol in self.positions and not self.positions[signal.symbol].closed:
            return False, f"已有持仓 {signal.symbol}，不允许重复开仓"
        
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
            
            self.positions[signal.symbol] = position
            self.position_mgr.save_position(position)
            
            # 订阅交易对行情
            self._subscribe_market_data(signal.symbol)
            
            # 记录风控信息
            if hasattr(self.position_mgr, 'risk_controller'):
                self.position_mgr.risk_controller.record_trade(signal.symbol)
            
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
        self.logger.info(f"处理平仓信号: {signal.symbol}", extra={
            "action": signal.action,
            "symbol": signal.symbol,
            "direction": signal.direction
        })
        
        # 更新策略状态
        self.update_strategy_status(StrategyStatus.HANDLING_SIGNAL)
        
        try:
            # 首先检查本地缓存中是否存在该仓位
            if signal.symbol in self.positions:
                self.logger.info(f"在本地缓存中找到{signal.symbol}的仓位信息，执行平仓")
                position = self.positions[signal.symbol]
                success, msg = await self._execute_close_position(signal.symbol, position)
                if success:
                    return True, f"成功平仓: {signal.symbol}"
                else:
                    return False, f"平仓失败: {msg}"
            
            # 如果本地缓存没有，尝试从交易所API获取
            self.logger.info(f"本地缓存没有{signal.symbol}的仓位，尝试从交易所API获取")
            positions = self.trader.get_positions(inst_type="SWAP")
            
            # 筛选指定交易对的持仓
            filtered_positions = []
            for position in positions:
                inst_id = position.get("instId")
                if inst_id == signal.symbol and float(position.get("pos", 0) or 0) != 0:
                    filtered_positions.append(position)
            
            if filtered_positions:
                self.logger.info(f"从交易所API找到{signal.symbol}的持仓信息")
                for position in filtered_positions:
                    symbol = position.get("instId")
                    
                    # 安全地获取并转换值，处理可能的空字符串或无效值
                    pos_value = position.get("pos", "0")
                    pos_value = pos_value if pos_value else "0"
                    pos_float = float(pos_value)
                    
                    direction = "long" if pos_float > 0 else "short"
                    
                    # 读取持仓数量、保证金、杠杆倍数、开仓均价等信息 - 安全转换
                    position_size = abs(pos_float)
                    
                    # 安全地处理margin值
                    margin_value = position.get("margin", "0")
                    margin_value = margin_value if margin_value and margin_value.strip() else "0"
                    margin_balance = float(margin_value)
                    
                    # 安全地处理lever值
                    lever_value = position.get("lever", "1")
                    lever_value = lever_value if lever_value and lever_value.strip() else "1"
                    leverage = float(lever_value)
                    
                    # 安全地处理avgPx值
                    avg_px_value = position.get("avgPx", "0")
                    avg_px_value = avg_px_value if avg_px_value and avg_px_value.strip() else "0"
                    entry_price = float(avg_px_value)
                    
                    # 当前市场价格
                    try:
                        mark_price = float(self.trader.get_mark_price(symbol) or 0)
                    except (ValueError, TypeError):
                        self.logger.warning(f"无法获取有效的市场价格，使用0")
                        mark_price = 0
                    
                    # 计算收益率和杠杆后的收益率
                    pnl_percent = 0.0
                    
                    if position_size > 0 and entry_price > 0:
                        if direction == "long":
                            pnl_percent = (mark_price - entry_price) / entry_price
                        else:  # short
                            pnl_percent = (entry_price - mark_price) / entry_price
                    
                    # 杠杆后的收益率
                    leveraged_pnl_pct = pnl_percent * leverage
                    
                    # 获取合约面值 - 使用同步方法
                    contract_size = self.get_contract_size_sync(symbol)
                    
                    # 计算盈亏金额 - 修正计算方法
                    # 合约价值 = 数量 * 入场价格 * 合约面值
                    contract_value = abs(position_size) * entry_price * contract_size
                    
                    # 保证金 = 合约价值 / 杠杆倍数
                    margin = contract_value / leverage
                    
                    # 原始盈亏金额（未考虑杠杆）
                    if direction == "long":
                        raw_pnl_amount = abs(position_size) * (mark_price - entry_price) * contract_size
                    else:  # short
                        raw_pnl_amount = abs(position_size) * (entry_price - mark_price) * contract_size
                    
                    # 实际盈亏金额（考虑杠杆）- 实际上是保证金 * 杠杆后的收益率
                    pnl_amount = margin * leveraged_pnl_pct
                    
                    self.logger.info("持仓情况", extra={
                        "symbol": symbol,
                        "direction": direction,
                        "position_size": position_size,
                        "margin_balance": margin_balance,
                        "calculated_margin": margin,
                        "leverage": leverage,
                        "entry_price": entry_price,
                        "mark_price": mark_price,
                        "pnl_percent": pnl_percent,
                        "leveraged_pnl_pct": leveraged_pnl_pct,
                        "raw_pnl_amount": raw_pnl_amount,
                        "pnl_amount": pnl_amount,
                        "contract_size": contract_size,
                        "contract_value": contract_value
                    })
                    
                    # 构建仓位对象
                    timestamp = int(time.time())  # 使用当前时间戳，因为我们没有原始开仓时间
                    position_obj = Position(
                        symbol=symbol,
                        position_id="from_api",  # 由于API没有返回position_id，使用一个标记
                        entry_price=entry_price,
                        quantity=pos_float,  # 保留正负号以指示方向
                        position_type="swap",
                        leverage=leverage,
                        timestamp=timestamp,
                        high_price=entry_price,
                        low_price=entry_price
                    )
                    
                    # 执行平仓操作
                    try:
                        success, msg = await self._execute_close_position(symbol, position_obj)
                        if success:
                            return True, f"成功平仓: {symbol}"
                        else:
                            return False, f"平仓失败: {msg}"
                    except Exception as e:
                        self.logger.error(f"平仓失败: {e}", exc_info=True)
                        # 更新策略状态为错误
                        self.update_strategy_status(StrategyStatus.ERROR, str(e))
                        return False, f"平仓失败: {e}"
            else:
                self.logger.warning(f"未找到{signal.symbol}的持仓信息，无法平仓")
                # 更新策略状态为空闲
                self.update_strategy_status(StrategyStatus.IDLE)
                return False, f"未找到{signal.symbol}的持仓"
        except Exception as e:
            self.logger.error(f"处理平仓信号异常: {e}", exc_info=True)
            # 更新策略状态为错误
            self.update_strategy_status(StrategyStatus.ERROR, str(e))
            return False, f"处理平仓信号异常: {e}"
    
    async def _handle_modify_signal(self, signal: TradeSignal) -> Tuple[bool, str]:
        """
        处理修改仓位信号
        
        Args:
            signal: 修改信号
            
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        # 检查是否存在持仓
        if signal.symbol not in self.positions:
            return False, f"不存在 {signal.symbol} 的持仓"
        
        position = self.positions[signal.symbol]
        
        # 获取价格方向信息
        price_info = self._price_high_low.get(signal.symbol)
        if not price_info:
            # 创建默认值
            mark_price = await self.data_cache.get_mark_price(signal.symbol)
            if not mark_price:
                return False, f"无法获取 {signal.symbol} 的行情价格"
            
            direction = "long" if position.quantity > 0 else "short"
            self._price_high_low[signal.symbol] = {
                "direction": direction,
                "highest": mark_price if direction == "long" else 0,
                "lowest": mark_price if direction == "short" else float('inf')
            }
            price_info = self._price_high_low[signal.symbol]
        
        # 更新止盈止损比例
        if signal.take_profit_pct is not None:
            self.strategy_config[f"{signal.symbol}_take_profit_pct"] = signal.take_profit_pct
        
        if signal.stop_loss_pct is not None:
            self.strategy_config[f"{signal.symbol}_stop_loss_pct"] = signal.stop_loss_pct
        
        # 更新追踪止损设置
        if signal.trailing_stop is not None:
            self.strategy_config[f"{signal.symbol}_trailing_stop"] = signal.trailing_stop
        
        if signal.trailing_distance is not None:
            self.strategy_config[f"{signal.symbol}_trailing_distance"] = signal.trailing_distance
        
        self.logger.info(f"修改仓位参数成功 {signal.symbol}", extra={
            "symbol": signal.symbol,
            "take_profit_pct": signal.take_profit_pct,
            "stop_loss_pct": signal.stop_loss_pct,
            "trailing_stop": signal.trailing_stop,
            "trailing_distance": signal.trailing_distance
        })
        
        return True, f"修改仓位参数成功: {signal.symbol}"
    
    async def monitor_positions(self):
        """监控所有持仓，执行止盈止损策略"""
        try:
            # 添加调试代码检查方法定义
            self.logger.info(f"调试: _execute_close_position 方法ID: {id(self._execute_close_position)}")
            
            # 检查是否有持仓
            if not self.positions:
                return
            
            # 计算总盈亏以更新风控系统
            total_pnl_amount = 0
            total_margin = 0

            # 获取当前价格信息
            for symbol, position in list(self.positions.items()):
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
                    self.logger.info(f"【诊断】{symbol} 调整前的原始止盈止损百分比: 止盈={take_profit_pct*100:.2f}%, 止损={stop_loss_pct*100:.2f}%")
                    
                    trailing_stop = signal.trailing_stop if signal and hasattr(signal, 'trailing_stop') and signal.trailing_stop is not None else self.trailing_stop
                    trailing_distance = signal.trailing_distance if signal and hasattr(signal, 'trailing_distance') and signal.trailing_distance is not None else self.trailing_distance
                    
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
                        take_profit_price_no_adj = position.entry_price * (1 + take_profit_pct)
                        stop_loss_price_no_adj = position.entry_price * (1 - stop_loss_pct)
                        
                        # 添加诊断日志 - 未调整杠杆的止盈止损价格
                        self.logger.info(f"【诊断】{symbol} 未调整杠杆的止盈止损价格: 止盈={take_profit_price_no_adj:.4f}, 止损={stop_loss_price_no_adj:.4f}")
                        
                        # 如果有杠杆，调整百分比
                        if position.leverage > 1:
                            adj_take_profit_pct = take_profit_pct / position.leverage
                            adj_stop_loss_pct = stop_loss_pct / position.leverage
                            self.logger.info(f"【诊断】{symbol} 调整后的止盈止损百分比: 止盈={adj_take_profit_pct*100:.2f}%, 止损={adj_stop_loss_pct*100:.2f}%")
                            
                            # 使用调整后的百分比计算价格
                            take_profit_price = position.entry_price * (1 + adj_take_profit_pct)
                            stop_loss_price = position.entry_price * (1 - adj_stop_loss_pct)
                        else:
                            take_profit_price = take_profit_price_no_adj
                            stop_loss_price = stop_loss_price_no_adj
                        
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
                        take_profit_price_no_adj = position.entry_price * (1 - take_profit_pct)
                        stop_loss_price_no_adj = position.entry_price * (1 + stop_loss_pct)
                        
                        # 添加诊断日志 - 未调整杠杆的止盈止损价格
                        self.logger.info(f"【诊断】{symbol} 未调整杠杆的止盈止损价格: 止盈={take_profit_price_no_adj:.4f}, 止损={stop_loss_price_no_adj:.4f}")
                        
                        # 如果有杠杆，调整百分比
                        if position.leverage > 1:
                            adj_take_profit_pct = take_profit_pct / position.leverage
                            adj_stop_loss_pct = stop_loss_pct / position.leverage
                            self.logger.info(f"【诊断】{symbol} 调整后的止盈止损百分比: 止盈={adj_take_profit_pct*100:.2f}%, 止损={adj_stop_loss_pct*100:.2f}%")
                            
                            # 使用调整后的百分比计算价格
                            take_profit_price = position.entry_price * (1 - adj_take_profit_pct)
                            stop_loss_price = position.entry_price * (1 + adj_stop_loss_pct)
                        else:
                            take_profit_price = take_profit_price_no_adj
                            stop_loss_price = stop_loss_price_no_adj
                        
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
            
            # 如果应平仓百分比大于已平仓百分比，则执行部分平仓
            if current_ladder_level > 0 and target_closed_pct > ladder_closed_pct:
                # 计算本次要平仓的百分比和数量
                close_pct_this_time = target_closed_pct - position.ladder_closed_pct
                
                # 原数量
                original_quantity = abs(position.quantity)
                
                # 原始计划平仓数量
                raw_close_quantity = original_quantity * close_pct_this_time
                
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
                    close_quantity = round(raw_close_quantity / lot_size) * lot_size
                    close_quantity = round(close_quantity, precision)
                    close_quantity = max(close_quantity, lot_size)  # 确保不低于最小交易单位
                    
                    # 校正平仓百分比
                    close_pct_this_time = close_quantity / original_quantity if original_quantity > 0 else 0
                    
                    self.logger.info(f"{symbol} 圆整平仓数量: 原始={raw_close_quantity}, 圆整后={close_quantity}, 对应百分比={close_pct_this_time*100:.2f}%")
                except Exception as e:
                    # 如果获取合约信息失败，使用原始数量
                    close_quantity = raw_close_quantity
                    self.logger.warning(f"获取合约信息圆整数量失败，使用原始数量: {e}")
                
                # 确定持仓方向
                pos_side = "long" if position.direction == "long" else "short"
                # 平仓方向与开仓相反
                side = "sell" if position.direction == "long" else "buy"
                
                # 更新已平仓比例
                position.ladder_closed_pct = target_closed_pct
                
                # 更新持仓数据库
                self.position_mgr.save_position(position)
                
                # 执行部分平仓操作 - 注意这里不使用await，因为swap_order不是异步方法
                close_result = self.trader.swap_order(
                    inst_id=symbol,
                    side=side,
                    pos_side=pos_side,
                    sz=close_quantity
                )
                
                if close_result and close_result.get('code') == '0':
                    # 更新持仓数量
                    new_quantity = position.quantity * (1 - close_pct_this_time)
                    
                    # 圆整更新后的数量，避免浮点数精度问题
                    try:
                        # 保持原数量的正负号
                        sign = 1 if new_quantity > 0 else -1
                        abs_new_quantity = abs(new_quantity)
                        
                        # 使用相同的圆整逻辑
                        abs_new_quantity = round(abs_new_quantity / lot_size) * lot_size
                        abs_new_quantity = round(abs_new_quantity, precision)
                        
                        # 恢复正负号
                        position.quantity = sign * abs_new_quantity
                        
                        self.logger.info(f"{symbol} 更新后的持仓数量圆整: 原始={new_quantity}, 圆整后={position.quantity}")
                    except Exception as e:
                        # 如果圆整失败，使用原始计算结果
                        position.quantity = new_quantity
                        self.logger.warning(f"圆整更新后的持仓数量失败，使用原始计算结果: {e}")
                    
                    # 获取必要的变量
                    # 合约面值
                    contract_size = self.get_contract_size_sync(symbol)
                    
                    # 计算杠杆后的盈亏百分比 - 这个变量在当前函数中并没有定义，所以需要重新计算
                    if position.direction == 'long':
                        leveraged_pnl_pct = (mark_price - position.entry_price) / position.entry_price * position.leverage
                    else:  # short
                        leveraged_pnl_pct = (position.entry_price - mark_price) / position.entry_price * position.leverage
                    
                    # 计算部分平仓收益
                    # 合约价值 = 数量 * 入场价格 * 合约面值
                    closed_contract_value = close_quantity * position.entry_price * contract_size
                    # 保证金 = 合约价值 / 杠杆倍数
                    closed_margin = closed_contract_value / position.leverage
                    # 实际盈亏金额（考虑杠杆）
                    closed_pnl = closed_margin * leveraged_pnl_pct
                    
                    # 记录平仓信息
                    self.logger.info(f"{symbol} 部分平仓成功，平仓数量: {close_quantity}, 剩余持仓: {abs(position.quantity)}")
                    self.logger.info(f"{symbol} 部分平仓收益: {closed_pnl:.2f} USDT ({leveraged_pnl_pct*100:.2f}%)")
                    
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
                        self.position_mgr.save_position(position)
                        self.positions.pop(symbol, None)
                        
                        # 通知风控系统完全平仓 (确保计数器正确更新)
                        if hasattr(self.position_mgr, 'risk_controller'):
                            self.position_mgr.risk_controller.record_close_position(symbol, is_partial_close=False)
                            self.logger.debug(f"已通知风控系统完全平仓: {symbol}")
                    else:
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
            
            # 从缓存中移除仓位
            try:
                if symbol in self.positions:
                    del self.positions[symbol]
                    self.logger.info(f"仓位已从内存中移除: {symbol}")
                    
                    # 从价格记录中移除
                    if symbol in self._price_high_low:
                        del self._price_high_low[symbol]
                        self.logger.info(f"价格记录已从内存中移除: {symbol}")
            except Exception as e:
                self.logger.error(f"移除内存仓位异常: {e}", exc_info=True)
                # 记录但不返回失败，因为交易已经成功
            
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
    
    def get_position_summary(self) -> Dict[str, Any]:
        """获取持仓摘要信息"""
        positions_info = []
        
        for symbol, position in self.positions.items():
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
            "position_count": len(self.positions),
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
        手动关闭所有持仓
        
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        if not self.positions:
            return True, "没有需要关闭的持仓"
        
        success_count = 0
        fail_count = 0
        messages = []
        
        for symbol in list(self.positions.keys()):
            signal = TradeSignal(
                action="close",
                symbol=symbol
            )
            success, msg = await self.handle_trade_signal(signal)
            if success:
                success_count += 1
            else:
                fail_count += 1
                messages.append(msg)
        
        if fail_count == 0:
            return True, f"成功关闭所有 {success_count} 个持仓"
        else:
            return False, f"关闭 {success_count} 个持仓成功，{fail_count} 个失败: {', '.join(messages)}"
    
    async def get_status(self) -> Dict[str, Any]:
        """
        获取框架状态信息
        
        Returns:
            Dict[str, Any]: 状态信息
        """
        return await self.strategy.get_status()
    
    def get_daily_pnl(self, start_date: str = None, end_date: str = None) -> List[Dict]:
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
    
    def get_position_history(self, start_date: str = None, end_date: str = None, 
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
            
            # 从内存中删除持仓信息
            if symbol in self.positions:
                del self.positions[symbol]
                
            # 清理价格记录
            if symbol in self._price_high_low:
                del self._price_high_low[symbol]
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
        """初始化策略"""
        if hasattr(self, '_initialized') and self._initialized:
            return
            
        # 标记为已初始化
        self._initialized = True
        
        # 同步数据
        self._sync_positions()
        
        # 同步风控系统中的持仓数量
        if hasattr(self.position_mgr, 'risk_controller') and self.positions:
            # 只计算未关闭的仓位
            active_positions_count = sum(1 for p in self.positions.values() if not getattr(p, 'closed', False))
            self.position_mgr.risk_controller.set_positions_count(active_positions_count)
            self.logger.info(f"初始化风控持仓数量: {active_positions_count}（总仓位数: {len(self.positions)}）")
            
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
        self.position_mgr = PositionManager(app_name, self.logger)
        self.data_cache = OKExDataCache.from_config(self.config)
        
        # 初始化市场数据订阅器，使用OKEx特定的实现
        self.market_subscriber = OKExMarketSubscriber(self.data_cache, self.config, app_name)
        # 注意：这里不启动market_subscriber，而是在run_forever中启动
        
        # 初始化策略
        self.strategy = strategy_class(app_name, self.trader, self.position_mgr, self.data_cache, self.config)
        
        # 设置策略的market_subscriber属性
        self.strategy.market_subscriber = self.market_subscriber
        
        # 替换策略中的_subscribe_market_data方法
        self.strategy._subscribe_market_data = self._subscribe_market_data
        
        # 处理策略中等待的订阅请求
        self.strategy._process_pending_subscriptions()
        
        # 初始化风控系统的持仓数量
        if hasattr(self.position_mgr, 'risk_controller'):
            # 计算实际活跃仓位数量
            active_positions = sum(1 for p in self.strategy.positions.values() if not getattr(p, 'closed', False))
            # 设置风控系统中的持仓数量
            self.position_mgr.risk_controller.set_positions_count(active_positions)
            self.logger.info(f"初始化风控持仓数量: {active_positions}（总仓位数: {len(self.strategy.positions)}）")
            
            # 确保风控系统有数据缓存的引用
            if hasattr(self.position_mgr.risk_controller, 'set_data_cache'):
                self.position_mgr.risk_controller.set_data_cache(self.data_cache)
                self.logger.info("已为风控系统设置数据缓存引用")
    
    def _subscribe_market_data(self, symbol: str):
        """
        订阅标的物的行情数据
        
        Args:
            symbol: 交易对
        """
        try:
            self.logger.info(f"订阅 {symbol} 行情数据")
            # 使用market_subscriber订阅行情
            asyncio.ensure_future(self.market_subscriber.subscribe_symbol(symbol))
        except Exception as e:
            self.logger.error(f"订阅 {symbol} 行情数据失败: {e}")
    
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
        last_reset_date = datetime.utcnow().date()
        self.logger.info(f"初始化风控重置日期: {last_reset_date}")
        
        self.logger.info(f"启动交易框架 {self.app_name}")
        
        # 设置监控间隔
        self.monitor_interval = position_monitor_interval
        self.logger.info(f"持仓监控间隔: {position_monitor_interval}秒")
        
        error_count = 0
        while True:
            try:
                # 检查是否需要重置日期计数器（UTC时间跨天）
                current_date = datetime.utcnow().date()
                if current_date > last_reset_date:
                    self.logger.info(f"检测到日期变更: {last_reset_date} -> {current_date}, 重置风控每日计数器")
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
                
                # 启动行情订阅
                if not getattr(self, 'market_data_subscriber_started', False):
                    if hasattr(self, 'market_data_subscriber') and self.market_data_subscriber:
                        await self.market_data_subscriber.start()
                        self.market_data_subscriber_started = True
                    
                # 监控持仓
                await self.strategy.monitor_positions()
                
                # 休眠一段时间
                await asyncio.sleep(self.monitor_interval)
                
            except KeyboardInterrupt:
                self.logger.info("收到键盘中断信号，退出策略运行")
                if getattr(self, 'market_data_subscriber_started', False) and hasattr(self, 'market_data_subscriber') and self.market_data_subscriber:
                    await self.market_data_subscriber.stop()
                break
                
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
        获取系统当前状态
        
        Returns:
            Dict: 状态信息
        """
        # 准备持仓信息
        positions_list = []
        for symbol, position in self.strategy.positions.items():
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
        return self.strategy.get_daily_pnl(start_date, end_date)
    
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