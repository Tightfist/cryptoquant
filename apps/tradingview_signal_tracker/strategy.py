import logging
import time
from typing import Dict, Any, List, Set, Optional
from dataclasses import dataclass

from src.exchange.okex.trader import OKExTrader
from src.common.position_manager import PositionManager, Position
from src.common.order_utils import calculate_order_size
from .market_client import MarketDataSubscriber

logger = logging.getLogger("tradingview_signal_tracker")

@dataclass
class OrderInfo:
    """订单信息"""
    symbol: str
    side: str  # buy/sell
    position_side: str  # long/short
    size: float
    price: float
    timestamp: int
    order_id: str
    tp_price: Optional[float] = None  # 止盈价格
    sl_price: Optional[float] = None  # 止损价格

class TradingViewSignalStrategy:
    """TradingView信号追踪策略"""
    def __init__(self, trader: OKExTrader, position_mgr: PositionManager, 
                 market_client: MarketDataSubscriber, config: Dict[str, Any]):
        """
        初始化策略
        
        Args:
            trader: 交易执行器
            position_mgr: 仓位管理器
            market_client: 市场数据订阅器
            config: 配置信息
        """
        self.trader = trader
        self.position_mgr = position_mgr
        self.market_client = market_client
        self.config = config
        
        # 从配置中获取策略参数
        self.strategy_config = config.get('strategy', {})
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
        logger.info(f"加载已有仓位: {len(self.positions)}个")
        
        # 打印策略初始化信息
        logger.info("TradingView信号追踪策略初始化完成", extra={
            "杠杆": self.leverage,
            "单笔仓位USDT": self.per_position_usdt,
            "止盈比例": f"{self.take_profit_pct*100}%",
            "止损比例": f"{self.stop_loss_pct*100}%",
            "追踪止损": self.trailing_stop,
            "委托单位": self.unit_type,
            "允许交易对": list(self.allowed_symbols) if self.enable_symbol_pool else "全部"
        })
    
    def _validate_symbol(self, symbol: str, signal: Dict[str, Any]) -> bool:
        """
        验证交易对是否允许交易
        
        Args:
            symbol: 交易对
            signal: 信号数据
            
        Returns:
            bool: 是否允许交易
        """
        # 如果未启用交易对池，允许所有交易对
        if not self.enable_symbol_pool:
            return True
            
        # 如果信号中指定强制覆盖
        if signal.get('override_symbol_pool', False):
            return True
            
        return symbol in self.allowed_symbols
    
    async def handle_signal(self, signal: Dict[str, Any]):
        """
        处理TradingView信号
        
        Args:
            signal: TradingView信号数据
        """
        action = signal.get('action')
        logger.info(f"处理信号: {action}", extra={"signal": signal})
        
        if action == 'open':
            await self._handle_open_signal(signal)
        elif action == 'close':
            await self._handle_close_signal(signal)
        elif action == 'tp':
            await self._handle_take_profit_signal(signal)
        elif action == 'sl':
            await self._handle_stop_loss_signal(signal)
        elif action == 'modify':
            await self._handle_modify_signal(signal)
        else:
            logger.warning(f"未知的信号类型: {action}")
    
    async def _handle_open_signal(self, signal: Dict[str, Any]):
        """
        处理开仓信号
        
        Args:
            signal: 开仓信号数据
        """
        # 获取信号中的交易对，如果有多个交易对则处理多个
        symbols = signal.get('symbols', [signal.get('symbol')])
        
        # 处理每个交易对
        for symbol in symbols:
            # 验证交易对是否允许交易
            if not self._validate_symbol(symbol, signal):
                logger.warning(f"交易对未授权: {symbol}")
                continue
                
            # 检查是否已有仓位
            if symbol in self.positions:
                logger.info(f"已有{symbol}仓位，跳过开仓")
                continue
            
            # 获取方向
            direction = signal.get('direction', 'long')
            
            # 标准化方向：将 buy/sell 转换为 long/short
            if direction.lower() in ['buy', 'long']:
                side = 'buy'
                pos_side = 'long'
            elif direction.lower() in ['sell', 'short']:
                side = 'sell'
                pos_side = 'short'
            else:
                logger.warning(f"未知的交易方向: {direction}")
                continue
            
            # 设置杠杆
            leverage = signal.get('leverage', self.leverage)
            try:
                self.trader.set_leverage(symbol, leverage)
                logger.info(f"设置{symbol}杠杆: {leverage}倍")
            except Exception as e:
                logger.error(f"设置杠杆失败: {e}")
                continue
            
            # 计算开仓数量
            try:
                position_usdt = signal.get('position_usdt', self.per_position_usdt)
                
                # 确定委托单位
                # 优先使用信号中指定的单位类型，如果没有则使用配置中的设置
                unit_type = signal.get('unit_type', self.unit_type)
                
                # 传递 side 参数
                size, tgt_ccy = calculate_order_size(
                    self.trader, symbol, position_usdt, unit_type, leverage, side
                )
            except Exception as e:
                logger.error(f"计算开仓数量失败: {e}")
                continue
            
            # 执行开仓
            try:
                # 获取当前价格
                current_price = signal.get('current_price')
                if not current_price:
                    current_price = self.trader.get_mark_price(symbol)
                
                # 执行开仓
                order_result = self.trader.swap_order(
                    symbol, side, pos_side, size, tgt_ccy=tgt_ccy
                )
                
                # 检查订单是否成功
                if (order_result and 
                    order_result.get('code') == '0' and  # 检查顶层code是否为0
                    order_result.get('data') and 
                    len(order_result['data']) > 0 and
                    order_result['data'][0].get('sCode') == '0'):  # 检查data中的sCode是否为0
                    
                    order_id = order_result['data'][0]['ordId']
                    logger.info(f"开仓成功: {symbol} {direction} {size}", 
                                extra={"order_id": order_id})
                    
                    # 创建仓位对象
                    position = Position(
                        symbol=symbol,
                        position_id=order_id,
                        entry_price=current_price,
                        quantity=size,
                        position_type="swap",
                        leverage=leverage,
                        timestamp=int(time.time()),
                        closed=False
                    )
                    
                    # 保存仓位
                    self.position_mgr.save_position(position)
                    self.positions[symbol] = position
                    
                    # 设置止盈止损
                    if self.take_profit_pct > 0 or self.stop_loss_pct > 0:
                        await self._set_take_profit_stop_loss(
                            symbol, direction, current_price, size,
                            unit_type=unit_type
                        )
                    
                    # 确保订阅了该交易对的行情
                    await self.market_client.subscribe_symbol(symbol)
                else:
                    error_msg = "未知错误"
                    if order_result and order_result.get('data') and len(order_result['data']) > 0:
                        error_msg = order_result['data'][0].get('sMsg', '未知错误')
                    logger.error(f"开仓失败: {error_msg}, 详情: {order_result}")
            except Exception as e:
                logger.exception(f"开仓执行失败: {e}")
    
    async def _handle_close_signal(self, signal: Dict[str, Any]):
        """
        处理平仓信号
        
        Args:
            signal: 平仓信号数据
        """
        # 获取信号中的交易对，如果有多个交易对则处理多个
        symbols = signal.get('symbols', [signal.get('symbol')])
        
        # 如果未指定交易对，则平掉所有仓位
        if not symbols or (len(symbols) == 1 and not symbols[0]):
            symbols = list(self.positions.keys())
            logger.info(f"未指定交易对，将平掉所有仓位: {symbols}")
        
        # 处理每个交易对
        for symbol in symbols:
            # 检查是否有仓位
            if symbol not in self.positions:
                logger.info(f"无{symbol}仓位，跳过平仓")
                continue
            
            position = self.positions[symbol]
            
            # 确定平仓方向
            direction = signal.get('direction')
            
            # 如果信号中指定了方向，需要确保它是平仓方向而不是开仓方向
            if direction:
                # 如果信号中的方向是 'sell' 或 'short'，则平仓方向是 'long'（平多仓）
                if direction.lower() in ['sell', 'short']:
                    direction = 'long'
                # 如果信号中的方向是 'buy' 或 'long'，则平仓方向是 'short'（平空仓）
                elif direction.lower() in ['buy', 'long']:
                    direction = 'short'
            
            # 如果信号中未指定方向或方向不明确，则根据持仓信息确定
            if not direction:
                # 首先尝试从本地缓存获取方向
                if hasattr(position, 'position_side') and position.position_side:
                    direction = position.position_side
                elif position.position_type == "swap":
                    # 假设 swap 类型的仓位是多仓
                    direction = 'long'
                else:
                    # 从交易所获取持仓信息
                    positions = self.trader.get_positions()
                    for pos in positions:
                        if pos['instId'] == symbol:
                            direction = 'long' if float(pos['pos']) > 0 else 'short'
                            break
            
            # 如果仍然无法确定方向，使用默认值
            if not direction:
                direction = 'long'
            
            logger.info(f"平仓方向: {direction}")
            
            # 根据平仓方向确定交易参数
            if direction.lower() == 'long':
                pos_side = 'long'
                side = 'sell'  # 平多仓需要卖出
            elif direction.lower() == 'short':
                pos_side = 'short'
                side = 'buy'   # 平空仓需要买入
            else:
                logger.warning(f"未知的交易方向: {direction}")
                continue
            
            # 获取单位类型和平仓数量
            unit_type = signal.get('unit_type', self.unit_type)
            close_percent = signal.get('close_percent', 100)  # 默认全部平仓
            
            # 获取原始仓位大小
            original_size = position.quantity
            
            # 如果指定了平仓金额或百分比
            if signal.get('close_amount') or close_percent < 100:
                # 如果指定了平仓金额
                close_amount = signal.get('close_amount')
                if close_amount:
                    # 使用指定的平仓金额
                    position_usdt = close_amount
                    logger.info(f"使用指定平仓金额: {position_usdt} USDT")
                elif close_percent < 100:
                    # 计算部分平仓的保证金金额
                    position_usdt = (position.entry_price * original_size / self.leverage) * (close_percent / 100)
                    logger.info(f"部分平仓: {close_percent}%, 保证金约={position_usdt} USDT")
                
                # 计算平仓数量
                size, tgt_ccy = calculate_order_size(
                    self.trader, symbol, position_usdt, unit_type, self.leverage, side
                )
            else:
                # 全部平仓，直接使用原始仓位大小
                size = original_size
                # 根据单位类型确定 tgt_ccy
                if unit_type.lower() in ['usdt', 'usd', 'quote']:
                    tgt_ccy = "quote_ccy"
                elif unit_type.lower() in ['coin', 'base']:
                    tgt_ccy = "base_ccy"
                else:
                    tgt_ccy = None  # 按张数平仓
                
                logger.info(f"全部平仓，使用原始仓位大小: {size} {'USDT' if tgt_ccy == 'quote_ccy' else '币' if tgt_ccy == 'base_ccy' else '张'}")
            
            # 执行平仓
            try:
                order_result = self.trader.swap_order(
                    symbol, side, pos_side, size, ord_type="market", tgt_ccy=tgt_ccy
                )
                
                # 检查订单是否成功
                if (order_result and 
                    order_result.get('code') == '0' and  # 检查顶层code是否为0
                    order_result.get('data') and 
                    len(order_result['data']) > 0 and
                    order_result['data'][0].get('sCode') == '0'):  # 检查data中的sCode是否为0
                    
                    order_id = order_result['data'][0]['ordId']
                    logger.info(f"平仓成功: {symbol} {direction} {size}", 
                                extra={"order_id": order_id})
                    
                    # 获取当前价格
                    current_price = signal.get('current_price')
                    if not current_price:
                        current_price = self.trader.get_mark_price(symbol)
                    
                    # 计算收益
                    entry_price = position.entry_price
                    if hasattr(position, 'position_side') and position.position_side == 'short':
                        profit_pct = (entry_price - current_price) / entry_price * 100
                    else:  # 默认为多仓
                        profit_pct = (current_price - entry_price) / entry_price * 100
                    
                    profit_amount = position.quantity * abs(current_price - entry_price)
                    
                    # 打印收益信息
                    logger.info(f"平仓收益: {symbol} 收益率={profit_pct:.2f}% 收益额={profit_amount:.2f} USDT", 
                                extra={"profit_pct": profit_pct, "profit_amount": profit_amount})
                    
                    # 更新仓位状态
                    self.position_mgr.close_position(symbol, current_price)
                    del self.positions[symbol]
                else:
                    error_msg = "未知错误"
                    if order_result and order_result.get('data') and len(order_result['data']) > 0:
                        error_msg = order_result['data'][0].get('sMsg', '未知错误')
                    logger.error(f"平仓失败: {error_msg}, 详情: {order_result}")
            except Exception as e:
                logger.exception(f"平仓执行失败: {e}")
    
    async def _handle_take_profit_signal(self, signal: Dict[str, Any]):
        """
        处理止盈信号
        
        Args:
            signal: 止盈信号数据
        """
        # 止盈信号本质上是平仓信号
        await self._handle_close_signal(signal)
    
    async def _handle_stop_loss_signal(self, signal: Dict[str, Any]):
        """
        处理止损信号
        
        Args:
            signal: 止损信号数据
        """
        # 止损信号本质上是平仓信号
        await self._handle_close_signal(signal)
    
    async def _handle_modify_signal(self, signal: Dict[str, Any]):
        """
        处理修改信号
        
        Args:
            signal: 修改信号数据
        """
        symbol = signal.get('symbol')
        if not symbol or symbol not in self.positions:
            logger.warning(f"无法修改不存在的仓位: {symbol}")
            return
        
        # 获取当前价格
        current_price = signal.get('current_price')
        if not current_price:
            current_price = self.trader.get_mark_price(symbol)
        
        # 获取持仓信息
        position = self.positions[symbol]
        
        # 修改止盈止损
        tp_price = signal.get('tp_price')
        sl_price = signal.get('sl_price')
        
        if tp_price or sl_price:
            # 确定方向
            direction = signal.get('direction', 'long')
            size = position.quantity
            
            # 获取单位类型
            unit_type = signal.get('unit_type', self.unit_type)
            
            # 设置新的止盈止损
            await self._set_take_profit_stop_loss(
                symbol, direction, current_price, size,
                tp_price=tp_price, sl_price=sl_price,
                unit_type=unit_type
            )
    
    async def _set_take_profit_stop_loss(self, symbol: str, direction: str, 
                                        price: float, size: float,
                                        tp_price: Optional[float] = None, 
                                        sl_price: Optional[float] = None,
                                        unit_type: Optional[str] = None):
        """
        设置止盈止损
        
        Args:
            symbol: 交易对
            direction: 方向 (long/short)
            price: 当前价格
            size: 仓位大小
            tp_price: 指定的止盈价格
            sl_price: 指定的止损价格
            unit_type: 单位类型 (quote/base/contract)
        """
        try:
            # 标准化方向
            if direction.lower() in ['buy', 'long']:
                direction = 'long'
            elif direction.lower() in ['sell', 'short']:
                direction = 'short'
            
            # 如果未指定单位类型，则使用配置中的设置
            if unit_type is None:
                unit_type = self.unit_type
            
            # 如果未指定止盈价格，则根据配置计算
            if tp_price is None and self.take_profit_pct > 0:
                if direction == 'long':
                    tp_price = price * (1 + self.take_profit_pct)
                else:
                    tp_price = price * (1 - self.take_profit_pct)
            
            # 如果未指定止损价格，则根据配置计算
            if sl_price is None and self.stop_loss_pct > 0:
                if direction == 'long':
                    sl_price = price * (1 - self.stop_loss_pct)
                else:
                    sl_price = price * (1 + self.stop_loss_pct)
            
            # 设置止盈止损订单
            if tp_price:
                logger.info(f"设置止盈: {symbol} {direction} 价格={tp_price:.2f}")
                # 这里需要根据实际交易所API实现止盈订单
                # 例如：
                # tp_side = 'sell' if direction == 'long' else 'buy'
                # tp_pos_side = direction
                # self.trader.swap_order(symbol, tp_side, tp_pos_side, size, 
                #                       ord_type="limit", px=tp_price, tgt_ccy=tgt_ccy)
            
            if sl_price:
                logger.info(f"设置止损: {symbol} {direction} 价格={sl_price:.2f}")
                # 这里需要根据实际交易所API实现止损订单
                # 例如：
                # sl_side = 'sell' if direction == 'long' else 'buy'
                # sl_pos_side = direction
                # self.trader.swap_order(symbol, sl_side, sl_pos_side, size,
                #                       ord_type="limit", px=sl_price, tgt_ccy=tgt_ccy)
                
        except Exception as e:
            logger.exception(f"设置止盈止损失败: {e}")
    
    def add_symbols_to_pool(self, symbols: List[str]):
        """
        动态添加允许的交易对
        
        Args:
            symbols: 交易对列表
        """
        if self.enable_symbol_pool:
            for symbol in symbols:
                if symbol not in self.allowed_symbols:
                    self.allowed_symbols.add(symbol)
                    logger.info(f"添加交易对到白名单: {symbol}")
    
    def remove_symbols_from_pool(self, symbols: List[str]):
        """
        动态移除交易对
        
        Args:
            symbols: 交易对列表
        """
        if self.enable_symbol_pool:
            for symbol in symbols:
                if symbol in self.allowed_symbols:
                    self.allowed_symbols.remove(symbol)
                    logger.info(f"从白名单移除交易对: {symbol}")
    
    async def monitor_positions(self):
        """监控持仓状态，处理追踪止损等逻辑"""
        if not self.positions:
            return
            
        for symbol, position in list(self.positions.items()):
            try:
                # 获取最新价格
                current_price = self.trader.get_mark_price(symbol)
                
                # 获取持仓方向和入场价格
                entry_price = position.entry_price
                
                # 这里可以实现追踪止损逻辑
                if self.trailing_stop:
                    # 根据实际需求实现追踪止损
                    pass
                    
                # 打印持仓监控信息
                logger.debug(f"持仓监控: {symbol} 入场={entry_price} 当前={current_price}")
                
            except Exception as e:
                logger.warning(f"监控持仓异常: {e}")
                
    def get_position_summary(self) -> Dict[str, Any]:
        """获取持仓摘要信息"""
        summary = {
            "position_count": len(self.positions),
            "positions": {}
        }
        
        for symbol, position in self.positions.items():
            try:
                # 获取最新价格
                current_price = self.trader.get_mark_price(symbol)
                
                # 计算盈亏
                entry_price = position.entry_price
                pnl_pct = (current_price - entry_price) / entry_price
                if position.position_type == "short":
                    pnl_pct = -pnl_pct
                
                summary["positions"][symbol] = {
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "quantity": position.quantity,
                    "pnl_percent": f"{pnl_pct*100:.2f}%",
                    "position_type": position.position_type,
                    "leverage": position.leverage,
                    "timestamp": position.timestamp
                }
            except Exception as e:
                logger.warning(f"获取持仓摘要异常: {e}")
                summary["positions"][symbol] = {
                    "error": str(e)
                }
                
        return summary 