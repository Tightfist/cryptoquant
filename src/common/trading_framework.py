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
from typing import Dict, Any, List, Optional, Set, Tuple, Callable
from dataclasses import dataclass
from enum import Enum, auto

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
        
        # 委托单位设置
        self.unit_type = self.strategy_config.get('unit_type', 'quote')
        
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
            "允许交易对": list(self.allowed_symbols) if self.enable_symbol_pool else "全部"
        })
        
        # 内存缓存 - 避免频繁请求
        self._contract_size_cache = {}
    
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
                self.logger.info(f"订阅 {symbol} 行情数据")
                asyncio.ensure_future(self.market_subscriber.subscribe_symbol(symbol))
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
            signal: 开仓信号
            
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        # 检查是否已存在同标的持仓
        if signal.symbol in self.positions:
            self.logger.warning(f"已存在 {signal.symbol} 的持仓，无法重复开仓")
            return False, f"已存在 {signal.symbol} 的持仓"
        
        # 验证交易对白名单
        if not self._validate_symbol(signal.symbol):
            self.logger.warning(f"未允许交易 {signal.symbol}，不在白名单中")
            return False, f"未允许交易 {signal.symbol}，不在白名单中"
        
        # 检查风险控制
        if hasattr(self.position_mgr, 'check_risk_control'):
            allowed, reason = self.position_mgr.check_risk_control(signal.symbol, signal.extra_data)
            if not allowed:
                self.logger.info(f"风控限制交易: {signal.symbol}, {reason}")
                return False, f"风控限制: {reason}"
        
        # 确定开仓数量/金额
        position_usdt = self._get_position_usdt(signal)
        leverage = self._get_leverage(signal)
        unit_type = self._get_unit_type(signal)
        
        self.logger.info(f"开仓参数: 币对={signal.symbol}, 方向={signal.direction}, 仓位USDT={position_usdt}, 杠杆={leverage}, 单位={unit_type}")
        
        # 尝试订阅行情数据
        # 注意：这里不使用await，因为订阅是异步操作，我们只需要触发它
        self._subscribe_market_data(signal.symbol)
        
        # 获取入场价格
        entry_price = await self._get_entry_price(signal)
        if not entry_price:
            self.logger.warning(f"无法获取 {signal.symbol} 的行情价格")
            return False, f"无法获取 {signal.symbol} 的行情价格"
            
        # 计算开仓数量
        position_size = 0
        
        # 如果信号中指定了数量，则直接使用
        if signal.quantity:
            position_size = signal.quantity
            self.logger.info(f"使用信号中指定的数量: {position_size}")
        else:
            # 否则，使用calculate_order_size计算数量
            from src.common.order_utils import calculate_order_size
            try:
                size, tgt_ccy = calculate_order_size(
                    trader=self.trader,
                    symbol=signal.symbol,
                    position_usdt=position_usdt,  # 使用配置或信号中的仓位大小
                    unit_type=unit_type,
                    leverage=leverage,
                    side="buy" if signal.direction == "long" else "sell"
                )
                position_size = size
                self.logger.info(f"计算得到的开仓数量: {position_size}, tgt_ccy: {tgt_ccy}")
            except Exception as e:
                self.logger.exception(f"计算开仓数量异常: {e}")
                return False, f"计算开仓数量异常: {e}"
        
        # 计算止盈止损价格
        tp_pct = signal.take_profit_pct if signal.take_profit_pct is not None else self.take_profit_pct
        sl_pct = signal.stop_loss_pct if signal.stop_loss_pct is not None else self.stop_loss_pct
        
        if signal.direction == "long":
            tp_price = entry_price * (1 + tp_pct)
            sl_price = entry_price * (1 - sl_pct)
        else:  # short
            tp_price = entry_price * (1 - tp_pct)
            sl_price = entry_price * (1 + sl_pct)
        
        self.logger.info(f"止盈止损: 止盈={tp_price:.4f} ({tp_pct*100:.2f}%), 止损={sl_price:.4f} ({sl_pct*100:.2f}%)")
        
        # 设置杠杆倍数
        try:
            leverage_result = self.trader.set_leverage(signal.symbol, leverage)
            self.logger.info(f"设置杠杆结果: {leverage_result}")
        except Exception as e:
            self.logger.warning(f"设置杠杆异常，继续开仓: {e}")
        
        # 执行开仓
        try:
            # 确定开仓方向和大小
            side = "buy" if signal.direction == "long" else "sell"
            pos_side = "long" if signal.direction == "long" else "short"
            
            # 确定单位类型参数
            tgt_ccy = None
            if unit_type == "quote":
                tgt_ccy = "quote_ccy"  # USDT
            elif unit_type == "base":
                tgt_ccy = "base_ccy"   # 币
            
            self.logger.info(f"准备开仓: 币对={signal.symbol}, 方向={side}/{pos_side}, 数量={position_size}, 单位类型={tgt_ccy}")
            
            # 执行开仓 - 注意这里不使用await，因为swap_order不是异步方法
            order_result = self.trader.swap_order(
                inst_id=signal.symbol,
                side=side,
                pos_side=pos_side,
                sz=position_size,
                tgt_ccy=tgt_ccy
            )
            
            self.logger.info(f"开仓结果: {order_result}")
            
            if not order_result:
                self.logger.error("开仓结果为空")
                return False, "开仓结果为空"
                
            if order_result.get("code") != "0":
                error_msg = f"开仓失败, code: {order_result.get('code')}"
                if "data" in order_result and len(order_result["data"]) > 0:
                    error_data = order_result["data"][0]
                    if "sMsg" in error_data:
                        error_msg += f", 错误信息: {error_data['sMsg']}"
                self.logger.error(error_msg)
                return False, f"开仓失败: {error_msg}"
            
            # 获取订单ID
            order_id = "unknown"
            if "data" in order_result and len(order_result["data"]) > 0:
                order_id = order_result["data"][0].get("ordId", "unknown")
            
            # 记录仓位信息 - 使用毫秒级时间戳
            timestamp = int(time.time() * 1000)
            position = Position(
                symbol=signal.symbol,
                position_id=order_id,
                entry_price=entry_price,
                quantity=position_size if signal.direction == "long" else -position_size,
                position_type="swap",
                leverage=leverage,
                timestamp=timestamp
            )
            
            self.logger.info(f"保存仓位信息: {vars(position)}")
            
            # 保存仓位
            try:
                self.position_mgr.save_position(position)
                self.logger.info(f"仓位已保存到数据库: {signal.symbol}")
            except Exception as e:
                self.logger.exception(f"保存仓位到数据库异常: {e}")
                
            # 保存到内存
            try:
                self.positions[signal.symbol] = position
                self.logger.info(f"仓位已保存到内存: {signal.symbol}, 当前内存中仓位: {list(self.positions.keys())}")
            except Exception as e:
                self.logger.exception(f"保存仓位到内存异常: {e}")
            
            # 记录高低价格，用于追踪止损
            try:
                self._price_high_low[signal.symbol] = {
                    "direction": signal.direction,
                    "highest": entry_price if signal.direction == "long" else 0,
                    "lowest": entry_price if signal.direction == "short" else float('inf')
                }
                self.logger.info(f"价格记录已初始化: {signal.symbol}")
            except Exception as e:
                self.logger.exception(f"初始化价格记录异常: {e}")
            
            self.logger.info(f"开仓成功 {signal.symbol} {signal.direction} @ {entry_price}", extra={
                "symbol": signal.symbol,
                "direction": signal.direction,
                "entry_price": entry_price,
                "size": position_size,
                "position_id": order_id,
                "tp_price": tp_price,
                "sl_price": sl_price
            })
            
            # 记录交易到风控
            if hasattr(self.position_mgr, 'record_trade'):
                self.position_mgr.record_trade(signal.symbol)
            
            return True, f"开仓成功: {signal.symbol} {signal.direction} @ {entry_price}"
        except Exception as e:
            self.logger.exception(f"开仓异常: {e}")
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
                await self._execute_close_position(signal.symbol, position)
                return True, f"成功平仓: {signal.symbol}"
            
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
                        timestamp=timestamp
                    )
                    
                    # 执行平仓操作
                    try:
                        await self._execute_close_position(symbol, position_obj)
                        return True, f"成功平仓: {symbol}"
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
        """监控所有持仓的止盈止损和追踪止损"""
        try:
            # 如果没有持仓，直接返回
            if not self.positions:
                return
            
            self.logger.info(f"监控持仓, 当前持仓数: {len(self.positions)}")
            
            # 检查每个持仓的止盈止损
            for symbol, position in list(self.positions.items()):
                try:
                    # 确保已订阅该交易对的行情数据
                    if self.market_subscriber and hasattr(self.market_subscriber, 'subscribed_symbols'):
                        if symbol not in self.market_subscriber.subscribed_symbols:
                            self.logger.info(f"检测到未订阅的交易对 {symbol}，正在订阅行情...")
                            self._subscribe_market_data(symbol)
                            # 给一点时间让订阅生效
                            await asyncio.sleep(1)
                    
                    # 获取当前价格
                    try:
                        # 尝试使用OKExDataCache的get_mark_price方法
                        mark_price = await self.data_cache.get_mark_price(symbol)
                    except AttributeError:
                        # 如果不是OKExDataCache，使用通用方法获取价格
                        self.logger.debug(f"使用通用方法获取{symbol}价格")
                        mark_price_data = await self.data_cache.get("mark-price", symbol)
                        mark_price = float(mark_price_data.get('markPx', 0.0))
                    
                    if not mark_price:
                        self.logger.warning(f"无法获取 {symbol} 的行情价格")
                        continue
                    
                    # 计算盈亏百分比
                    direction = "long" if position.quantity > 0 else "short"
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
                    
                    # 原始盈亏金额（未考虑杠杆）
                    if direction == "long":
                        raw_pnl_amount = abs(position.quantity) * (mark_price - position.entry_price) * contract_size
                    else:  # short
                        raw_pnl_amount = abs(position.quantity) * (position.entry_price - mark_price) * contract_size
                    
                    # 实际盈亏金额（考虑杠杆）- 实际上是保证金 * 杠杆后的收益率
                    pnl_amount = margin * leveraged_pnl_pct
                    
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
                    
                    # 获取止盈止损设置
                    tp_pct = self.strategy_config.get(f"{symbol}_take_profit_pct", self.take_profit_pct)
                    sl_pct = self.strategy_config.get(f"{symbol}_stop_loss_pct", self.stop_loss_pct)
                    use_trailing = self.strategy_config.get(f"{symbol}_trailing_stop", self.trailing_stop)
                    trailing_distance = self.strategy_config.get(f"{symbol}_trailing_distance", self.trailing_distance)
                    
                    # 计算止盈止损价格
                    if direction == "long":
                        tp_price = position.entry_price * (1 + tp_pct)
                        sl_price = position.entry_price * (1 - sl_pct)
                    else:  # short
                        tp_price = position.entry_price * (1 - tp_pct)
                        sl_price = position.entry_price * (1 + sl_pct)
                    
                    # 获取价格高低记录 - 修复引用问题，确保价格记录被正确初始化和更新
                    if symbol not in self._price_high_low:
                        # 如果没有记录，创建一个新的并保存到self._price_high_low
                        self._price_high_low[symbol] = {
                            "direction": direction,
                            "highest": position.entry_price if direction == "long" else 0,
                            "lowest": position.entry_price if direction == "short" else float('inf')
                        }
                        self.logger.info(f"初始化 {symbol} 价格记录: 方向={direction}, 初始价格={position.entry_price}")
                    
                    # 获取价格记录的引用
                    price_info = self._price_high_low[symbol]
                    
                    # 更新最高价和最低价
                    if direction == "long":
                        if mark_price > price_info["highest"]:
                            self.logger.info(f"{symbol} 更新最高价: {price_info['highest']} -> {mark_price}")
                            price_info["highest"] = mark_price
                    else:  # short
                        if mark_price < price_info["lowest"]:
                            self.logger.info(f"{symbol} 更新最低价: {price_info['lowest']} -> {mark_price}")
                            price_info["lowest"] = mark_price
                    
                    # 计算距离止盈和止损的百分比
                    distance_to_tp_pct = abs((tp_price - mark_price) / mark_price)
                    distance_to_sl_pct = abs((sl_price - mark_price) / mark_price)
                    
                    # 如果启用了追踪止损，计算追踪止损价格
                    trailing_stop_price = None
                    if use_trailing and pnl_pct > 0:
                        if direction == "long":
                            trailing_stop_price = price_info["highest"] * (1 - trailing_distance)
                        else:  # short
                            trailing_stop_price = price_info["lowest"] * (1 + trailing_distance)
                    
                    # 打印详细的持仓信息
                    position_info = (
                        f"\n{'='*70}\n"
                        f"持仓详情 - {symbol} ({direction.upper()})\n"
                        f"{'-'*70}\n"
                        f"入场价格: {position.entry_price:.4f}    当前价格: {mark_price:.4f}    数量: {abs(position.quantity)}\n"
                        f"杠杆倍数: {position.leverage}倍    持仓时间: {holding_time_str}    保证金: {margin:.2f} USDT\n"
                        f"{'-'*70}\n"
                        f"盈亏比例: {pnl_pct*100:+.2f}%    杠杆后盈亏: {leveraged_pnl_pct*100:+.2f}%    盈亏金额: {pnl_amount:+.2f} USDT\n"
                        f"{'-'*70}\n"
                        f"止盈价格: {tp_price:.4f} (距离: {distance_to_tp_pct*100:.2f}%)\n"
                        f"止损价格: {sl_price:.4f} (距离: {distance_to_sl_pct*100:.2f}%)\n"
                        f"追踪止损: {'启用' if use_trailing else '禁用'}"
                    )
                    if use_trailing:
                        position_info += f"    追踪止损价格: {'未触发' if trailing_stop_price is None else f'{trailing_stop_price:.4f}'}\n"
                    else:
                        position_info += "\n"
                        
                    if direction == "long":
                        position_info += f"最高价格: {price_info['highest']:.4f} (入场价: {position.entry_price:.4f})\n"
                    else:
                        position_info += f"最低价格: {price_info['lowest']:.4f} (入场价: {position.entry_price:.4f})\n"
                    
                    position_info += f"{'='*70}"
                    
                    self.logger.info(position_info)
                    
                    # 检查止盈止损
                    await self._check_take_profit_stop_loss(
                        symbol, position, mark_price, pnl_pct, leveraged_pnl_pct,
                        tp_price, sl_price, use_trailing, trailing_distance, price_info
                    )
                    
                except Exception as e:
                    self.logger.error(f"监控持仓异常 {symbol}: {e}", exc_info=True)
                    
        except Exception as e:
            self.logger.exception(f"监控持仓异常: {e}")
    
    async def _check_take_profit_stop_loss(self, symbol: str, position: Position, 
                                          current_price: float, pnl_pct: float, 
                                          leveraged_pnl_pct: float, 
                                          tp_price: float = None, sl_price: float = None,
                                          use_trailing: bool = None, trailing_distance: float = None,
                                          price_info: Dict[str, Any] = None):
        """
        检查止盈止损条件
        
        Args:
            symbol: 交易对
            position: 仓位对象
            current_price: 当前价格
            pnl_pct: 盈亏百分比
            leveraged_pnl_pct: 带杠杆的盈亏百分比
            tp_price: 止盈价格，若未提供则重新计算
            sl_price: 止损价格，若未提供则重新计算
            use_trailing: 是否使用追踪止损，若未提供则从配置获取
            trailing_distance: 追踪止损距离，若未提供则从配置获取
            price_info: 价格高低记录，若未提供则从_price_high_low获取
        """
        # 获取方向
        direction = "long" if position.quantity > 0 else "short"
        
        # 如果未提供参数，则从配置中获取
        if use_trailing is None or trailing_distance is None:
            # 获取该交易对的特殊设置，如果没有则使用全局设置
            tp_pct = self.strategy_config.get(f"{symbol}_take_profit_pct", self.take_profit_pct)
            sl_pct = self.strategy_config.get(f"{symbol}_stop_loss_pct", self.stop_loss_pct)
            use_trailing = self.strategy_config.get(f"{symbol}_trailing_stop", self.trailing_stop)
            trailing_distance = self.strategy_config.get(f"{symbol}_trailing_distance", self.trailing_distance)
        else:
            # 获取止盈止损比例，用于判断是否触发止盈止损
            tp_pct = self.strategy_config.get(f"{symbol}_take_profit_pct", self.take_profit_pct)
            sl_pct = self.strategy_config.get(f"{symbol}_stop_loss_pct", self.stop_loss_pct)
        
        # 如果未提供止盈止损价格，则计算
        if tp_price is None or sl_price is None:
            # 计算止盈止损价格
            if direction == "long":
                tp_price = position.entry_price * (1 + tp_pct)
                sl_price = position.entry_price * (1 - sl_pct)
            else:  # short
                tp_price = position.entry_price * (1 - tp_pct)
                sl_price = position.entry_price * (1 + sl_pct)
        
        # 获取价格高低记录
        if price_info is None:
            if symbol not in self._price_high_low:
                # 创建默认值并保存
                self._price_high_low[symbol] = {
                    "direction": direction,
                    "highest": position.entry_price if direction == "long" else 0,
                    "lowest": position.entry_price if direction == "short" else float('inf')
                }
                self.logger.info(f"在_check_take_profit_stop_loss中初始化 {symbol} 价格记录: 方向={direction}, 初始价格={position.entry_price}")
            price_info = self._price_high_low[symbol]
        
        # 更新最高价和最低价
        if direction == "long":
            if current_price > price_info["highest"]:
                old_highest = price_info["highest"]
                price_info["highest"] = current_price
                self.logger.info(f"{symbol} 在_check_take_profit_stop_loss中更新最高价: {old_highest:.4f} -> {current_price:.4f}")
        else:  # short
            if current_price < price_info["lowest"]:
                old_lowest = price_info["lowest"]
                price_info["lowest"] = current_price
                self.logger.info(f"{symbol} 在_check_take_profit_stop_loss中更新最低价: {old_lowest:.4f} -> {current_price:.4f}")
        
        # 检查是否触发止盈
        if pnl_pct >= tp_pct:
            self.logger.info(f"触发止盈 {symbol} {direction} 盈利: {pnl_pct*100:.2f}%", extra={
                "symbol": symbol,
                "direction": direction,
                "current_price": current_price,
                "entry_price": position.entry_price,
                "pnl_pct": pnl_pct
            })
            await self._execute_close_position(symbol, position)
            return
        
        # 检查是否触发追踪止损
        if use_trailing and pnl_pct > 0:
            if direction == "long":
                # 计算回撤距离
                drawdown = (price_info["highest"] - current_price) / price_info["highest"]
                if drawdown >= trailing_distance:
                    self.logger.info(f"触发追踪止损 {symbol} {direction} 回撤: {drawdown*100:.2f}%", extra={
                        "symbol": symbol,
                        "direction": direction,
                        "current_price": current_price,
                        "highest_price": price_info["highest"],
                        "drawdown": drawdown
                    })
                    await self._execute_close_position(symbol, position)
                    return
            else:  # short
                # 计算回撤距离
                drawdown = (current_price - price_info["lowest"]) / price_info["lowest"]
                if drawdown >= trailing_distance:
                    self.logger.info(f"触发追踪止损 {symbol} {direction} 回撤: {drawdown*100:.2f}%", extra={
                        "symbol": symbol,
                        "direction": direction,
                        "current_price": current_price,
                        "lowest_price": price_info["lowest"],
                        "drawdown": drawdown
                    })
                    await self._execute_close_position(symbol, position)
                    return
        
        # 检查是否触发止损
        if pnl_pct <= -sl_pct:
            self.logger.info(f"触发止损 {symbol} {direction} 亏损: {pnl_pct*100:.2f}%", extra={
                "symbol": symbol,
                "direction": direction,
                "current_price": current_price,
                "entry_price": position.entry_price,
                "pnl_pct": pnl_pct
            })
            await self._execute_close_position(symbol, position)
            return
    
    async def _execute_close_position(self, symbol: str, position: Position):
        """
        执行平仓操作
        
        Args:
            symbol: 交易对
            position: 仓位对象
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
            
            # 执行平仓 - 注意这里不使用await，因为swap_order不是异步方法
            close_result = self.trader.swap_order(
                inst_id=symbol,
                side=side,
                pos_side=pos_side,
                sz=abs(position.quantity)
            )
            
            if not close_result or close_result.get("code") != "0":
                self.logger.error(f"平仓失败: {close_result}")
                return
            
            # 计算收益信息
            direction = "long" if position.quantity > 0 else "short"
            if direction == "long":
                pnl_pct = (mark_price - position.entry_price) / position.entry_price
            else:  # short
                pnl_pct = (position.entry_price - mark_price) / position.entry_price
                
            # 计算带杠杆的盈亏百分比
            leveraged_pnl_pct = pnl_pct * position.leverage
            
            # 获取合约面值 - 使用同步方法
            contract_size = self.get_contract_size_sync(symbol)
            
            # 计算合约价值和保证金
            contract_value = abs(position.quantity) * position.entry_price * contract_size
            margin = contract_value / position.leverage
            
            # 计算实际盈亏金额
            pnl_amount = margin * leveraged_pnl_pct
            
            # 更新仓位状态 - 不删除记录，而是标记为已关闭并记录收益信息
            current_timestamp = int(time.time() * 1000)  # 使用毫秒级时间戳
            
            # 确保入场时间戳是毫秒级的
            entry_timestamp = position.timestamp
            if entry_timestamp < 9999999999:  # 秒级时间戳
                entry_timestamp *= 1000
                
            # 计算持仓时间用于日志
            holding_time_ms = current_timestamp - entry_timestamp
            holding_time_hours = holding_time_ms / (1000 * 60 * 60)
            
            self.position_mgr.close_position(
                symbol, 
                mark_price if mark_price else 0,
                current_timestamp,
                pnl_amount,
                pnl_pct
            )
            
            # 从内存中删除仓位
            if symbol in self.positions:
                del self.positions[symbol]
                
            # 清理价格记录
            if symbol in self._price_high_low:
                del self._price_high_low[symbol]
            
            # 记录详细的平仓信息
            self.logger.info(f"自动平仓成功 {symbol} @ {mark_price}", extra={
                "symbol": symbol,
                "direction": direction,
                "entry_price": position.entry_price,
                "exit_price": mark_price,
                "quantity": abs(position.quantity),
                "leverage": position.leverage,
                "pnl_pct": pnl_pct * 100,
                "leveraged_pnl_pct": leveraged_pnl_pct * 100,
                "pnl_amount": pnl_amount,
                "margin": margin,
                "entry_time": position.timestamp,
                "exit_time": current_timestamp
            })
        except Exception as e:
            self.logger.exception(f"执行平仓异常 {symbol}: {e}")
    
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
        获取策略状态信息
        
        Returns:
            Dict[str, Any]: 状态信息
        """
        # 基础信息
        status = {
            "app_name": self.app_name,
            "positions": self.get_position_summary(),
            "strategy_status": self._strategy_status,
            "status_message": self._status_message,
            "config": {
                "leverage": self.leverage,
                "per_position_usdt": self.per_position_usdt,
                "take_profit_pct": self.take_profit_pct,
                "stop_loss_pct": self.stop_loss_pct,
                "trailing_stop": self.trailing_stop,
                "trailing_distance": self.trailing_distance,
                "unit_type": self.unit_type,
                "enable_symbol_pool": self.enable_symbol_pool,
                "allowed_symbols": list(self.allowed_symbols) if self.enable_symbol_pool else "全部"
            }
        }
        
        # 子类可以在重写此方法中添加额外信息
        return status
        
    def get_daily_pnl(self, start_date: str = None, end_date: str = None) -> List[Dict]:
        """
        获取每日收益统计
        
        Args:
            start_date: 开始日期，格式为 YYYY-MM-DD，默认为7天前
            end_date: 结束日期，格式为 YYYY-MM-DD，默认为今天
            
        Returns:
            List[Dict]: 每日收益统计列表
        """
        return self.position_mgr.get_daily_pnl(start_date, end_date)
    
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
        return self.position_mgr.get_position_history(start_date, end_date, symbol, limit)

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


class TradingFramework:
    """交易框架，管理策略和信号处理"""
    
    def __init__(self, app_name: str, strategy_class, config: Dict[str, Any]):
        """
        初始化交易框架
        
        Args:
            app_name: 应用名称
            strategy_class: 策略类，必须继承自BaseStrategy
            config: 配置信息
        """
        self.app_name = app_name
        self.config = config
        
        # 配置日志
        self.logger = logging.getLogger(app_name)
        
        # 初始化组件
        self.trader = OKExTrader(app_name, config['exchange'])
        self.position_mgr = PositionManager(app_name, self.logger)
        self.data_cache = OKExDataCache.from_config(config)
        
        # 初始化市场数据订阅器，使用OKEx特定的实现
        self.market_subscriber = OKExMarketSubscriber(self.data_cache, config, app_name)
        # 注意：这里不启动market_subscriber，而是在run_forever中启动
        
        # 初始化策略
        self.strategy = strategy_class(app_name, self.trader, self.position_mgr, self.data_cache, config)
        
        # 设置策略的market_subscriber属性
        self.strategy.market_subscriber = self.market_subscriber
        
        # 替换策略中的_subscribe_market_data方法
        self.strategy._subscribe_market_data = self._subscribe_market_data
        
        # 处理策略中等待的订阅请求
        self.strategy._process_pending_subscriptions()
    
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
    
    async def run_forever(self, position_monitor_interval: int = 30):
        """
        运行框架的主循环
        
        Args:
            position_monitor_interval: 监控持仓的间隔时间（秒）
        """
        self.logger.info(f"启动交易框架 {self.app_name}")
        
        try:
            # 启动市场数据订阅器并等待它初始化完成
            self.logger.info("正在启动市场数据订阅器...")
            started = await self.market_subscriber.start()
            
            if not started:
                self.logger.error("市场数据订阅器启动失败，将继续尝试运行框架")
            else:
                self.logger.info("市场数据订阅器已启动成功")
            
            # 给WebSocket一些额外时间来稳定连接和收到初始数据
            await asyncio.sleep(3)
            
            while True:
                # 监控持仓
                await self.strategy.monitor_positions()
                
                # 等待下一次监控
                await asyncio.sleep(position_monitor_interval)
        except asyncio.CancelledError:
            self.logger.info("交易框架被取消")
        except Exception as e:
            self.logger.exception(f"交易框架异常: {e}")
        finally:
            self.logger.info("交易框架已停止")
    
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
        """获取框架状态信息"""
        return await self.strategy.get_status()
        
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
        return self.strategy.get_position_history(start_date, end_date, symbol, limit) 