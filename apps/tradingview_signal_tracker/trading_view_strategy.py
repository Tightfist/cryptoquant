"""
TradingView信号策略

基于交易框架实现TradingView信号的处理逻辑
"""

import logging
import re
import json
import time
from typing import Dict, Any, Tuple, Optional, List

from src.common.trading_framework import BaseStrategy, TradeSignal


class TradingViewStrategy(BaseStrategy):
    """TradingView信号策略，处理来自TradingView的Webhook信号"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # 初始化合约名称映射
        self._init_symbol_mapping()
        
    def _init_symbol_mapping(self):
        """初始化合约名称映射"""
        # 从配置中获取合约名称映射
        self.symbol_mapping = self.config.get('symbol_mapping', {})
        
        # 如果没有配置，使用默认映射规则
        if not self.symbol_mapping:
            # 默认映射规则：将 BTCUSDT.P 转换为 BTC-USDT-SWAP
            self.symbol_mapping = {
                "BTCUSDT.P": "BTC-USDT-SWAP",
                "ETHUSDT.P": "ETH-USDT-SWAP",
                "LTCUSDT.P": "LTC-USDT-SWAP",
                "XRPUSDT.P": "XRP-USDT-SWAP",
                "EOSUSDT.P": "EOS-USDT-SWAP",
                "BCHUSDT.P": "BCH-USDT-SWAP",
                "ETCUSDT.P": "ETC-USDT-SWAP",
                "LINKUSDT.P": "LINK-USDT-SWAP",
                "DOGEUSDT.P": "DOGE-USDT-SWAP",
                "ADAUSDT.P": "ADA-USDT-SWAP",
                "DOTUSDT.P": "DOT-USDT-SWAP",
                "UNIUSDT.P": "UNI-USDT-SWAP",
                "SOLUSDT.P": "SOL-USDT-SWAP",
                "MATICUSDT.P": "MATIC-USDT-SWAP",
                "FILUSDT.P": "FIL-USDT-SWAP",
                "AVAXUSDT.P": "AVAX-USDT-SWAP",
                "SHIBUSDT.P": "SHIB-USDT-SWAP",
                "NEARUSDT.P": "NEAR-USDT-SWAP",
                "APTUSDT.P": "APT-USDT-SWAP",
                "OPUSDT.P": "OP-USDT-SWAP"
            }
    
    def _convert_symbol(self, symbol: str) -> str:
        """
        将TradingView的合约名称转换为OKEx的格式
        
        Args:
            symbol: TradingView的合约名称，如 BTCUSDT.P, SWARMSUSDT.P
            
        Returns:
            str: OKEx的合约名称，如 BTC-USDT-SWAP, SWARMS-USDT-SWAP
        """
        # 如果在映射表中，直接返回映射结果
        if symbol in self.symbol_mapping:
            return self.symbol_mapping[symbol]
            
        # 尝试使用正则表达式进行转换
        # 例如：将 BTCUSDT.P 或 SWARMSUSDT.P 转换为 BTC-USDT-SWAP 或 SWARMS-USDT-SWAP
        match = re.match(r"([A-Z]+)USDT\.P", symbol)
        if match:
            coin = match.group(1)
            return f"{coin}-USDT-SWAP"
            
        # 尝试提取所有大写字母作为币种名称
        match = re.match(r"([A-Z]+)", symbol)
        if match:
            coin = match.group(1)
            if "USDT" in symbol:
                return f"{coin}-USDT-SWAP"
                
        # 如果无法转换，记录警告并返回一个可能的格式
        self.logger.warning(f"无法精确转换合约名称: {symbol}，尝试使用通用格式")
        return symbol
    
    async def process_signal(self, signal_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        处理TradingView信号
        
        Args:
            signal_data: TradingView webhook发送的原始信号数据
            
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        try:
            # 记录收到的信号
            self.logger.info(f"收到TradingView信号: {signal_data}")
            
            # 验证信号数据
            if not self._validate_tv_signal(signal_data):
                return False, "信号数据不完整或格式错误"
            
            # 解析信号类型
            signal_type = signal_data.get('strategy', {}).get('action', '').lower()
            
            # 转换为标准化交易信号
            if signal_type in ['buy', 'long']:
                trade_signal = self._parse_open_signal(signal_data, "long")
            elif signal_type in ['sell', 'short']:
                trade_signal = self._parse_open_signal(signal_data, "short")
            elif signal_type in ['close', 'exit', 'close_all']:
                trade_signal = self._parse_close_signal(signal_data)
            elif signal_type in ['modify', 'update']:
                trade_signal = self._parse_modify_signal(signal_data)
            elif signal_type in ['status', 'query']:
                status = await self.get_status()
                return True, json.dumps(status)
            else:
                return False, f"未知信号类型: {signal_type}"
            
            # 处理交易信号
            if trade_signal:
                return await self.handle_trade_signal(trade_signal)
            else:
                return False, "无法解析交易信号"
                
        except Exception as e:
            self.logger.exception(f"处理TradingView信号异常: {e}")
            return False, f"处理信号异常: {e}"
    
    def _validate_tv_signal(self, signal_data: Dict[str, Any]) -> bool:
        """
        验证TradingView信号的格式
        
        Args:
            signal_data: TradingView信号数据
            
        Returns:
            bool: 信号是否有效
        """
        # 检查必须字段
        if not signal_data:
            self.logger.warning("空信号数据")
            return False
        
        # 标准TradingView信号应该包含strategy字段
        if 'strategy' not in signal_data:
            # 尝试识别可能的简化格式
            if 'action' in signal_data and 'symbol' in signal_data:
                return True
            
            self.logger.warning("信号中缺少strategy字段或关键信息")
            return False
            
        # 检查strategy字段是否有效
        strategy = signal_data.get('strategy', {})
        if not isinstance(strategy, dict):
            self.logger.warning("strategy字段不是字典类型")
            return False
            
        # 检查必要的字段
        required_fields = ['action']
        missing_fields = [field for field in required_fields if field not in strategy]
        
        if missing_fields:
            self.logger.warning(f"信号缺少必要字段: {', '.join(missing_fields)}")
            return False
            
        # 如果是开仓或平仓操作，检查市场字段
        action = strategy.get('action', '').lower()
        if action in ['buy', 'sell', 'long', 'short', 'close', 'exit']:
            if 'market' not in strategy and 'symbol' not in signal_data:
                self.logger.warning("信号缺少市场/合约信息")
                return False
                
        return True
    
    def _parse_open_signal(self, signal_data: Dict[str, Any], direction: str) -> Optional[TradeSignal]:
        """
        解析开仓信号
        
        Args:
            signal_data: TradingView信号数据
            direction: 开仓方向 (long或short)
            
        Returns:
            Optional[TradeSignal]: 解析后的交易信号
        """
        try:
            # 获取策略信息
            strategy = signal_data.get('strategy', {})
            
            # 获取交易对
            symbol = strategy.get('market', signal_data.get('symbol'))
            if not symbol:
                self.logger.warning("信号缺少交易对信息")
                return None
                
            # 转换合约名称
            okex_symbol = self._convert_symbol(symbol)
            
            # 获取其他参数
            entry_price = strategy.get('price', signal_data.get('price'))
            quantity = strategy.get('contracts', signal_data.get('contracts'))
            
            # 获取止盈止损参数
            # 允许使用不同的字段名
            tp_price = strategy.get('tp_price', signal_data.get('tp_price'))
            sl_price = strategy.get('sl_price', signal_data.get('sl_price'))
            
            # 如果有价格而没有比例，则计算比例
            take_profit_pct = strategy.get('tp_pct', signal_data.get('tp_pct'))
            stop_loss_pct = strategy.get('sl_pct', signal_data.get('sl_pct'))
            
            if tp_price and entry_price and not take_profit_pct:
                if direction == "long":
                    take_profit_pct = (float(tp_price) - float(entry_price)) / float(entry_price)
                else:  # short
                    take_profit_pct = (float(entry_price) - float(tp_price)) / float(entry_price)
                
            if sl_price and entry_price and not stop_loss_pct:
                if direction == "long":
                    stop_loss_pct = (float(entry_price) - float(sl_price)) / float(entry_price)
                else:  # short
                    stop_loss_pct = (float(sl_price) - float(entry_price)) / float(entry_price)
            
            # 获取追踪止损参数
            trailing_stop = strategy.get('trailing_stop', signal_data.get('trailing_stop'))
            trailing_distance = strategy.get('trailing_distance', signal_data.get('trailing_distance'))
            
            # 获取杠杆倍数
            leverage = strategy.get('leverage', signal_data.get('leverage'))
            
            # 获取单位类型
            unit_type = strategy.get('unit_type', signal_data.get('unit_type'))
            
            # 创建TradeSignal对象
            trade_signal = TradeSignal(
                action="open",
                symbol=okex_symbol,
                direction=direction,
                entry_price=float(entry_price) if entry_price else None,
                quantity=float(quantity) if quantity else None,
                take_profit_pct=float(take_profit_pct) if take_profit_pct else self.take_profit_pct,
                stop_loss_pct=float(stop_loss_pct) if stop_loss_pct else self.stop_loss_pct,
                trailing_stop=trailing_stop if trailing_stop is not None else self.trailing_stop,
                trailing_distance=float(trailing_distance) if trailing_distance else self.trailing_distance,
                leverage=int(leverage) if leverage else self.leverage,
                unit_type=unit_type if unit_type else self.unit_type,
                extra_data={
                    "raw_signal": signal_data,
                    "tv_symbol": symbol,
                    "tp_price": tp_price,
                    "sl_price": sl_price
                }
            )
            
            return trade_signal
        except Exception as e:
            self.logger.exception(f"解析开仓信号异常: {e}")
            return None
    
    def _parse_close_signal(self, signal_data: Dict[str, Any]) -> Optional[TradeSignal]:
        """
        解析平仓信号
        
        Args:
            signal_data: TradingView信号数据
            
        Returns:
            Optional[TradeSignal]: 解析后的交易信号
        """
        try:
            # 获取策略信息
            strategy = signal_data.get('strategy', {})
            
            # 获取交易对
            symbol = strategy.get('market', signal_data.get('symbol'))
            
            # 如果是close_all操作，但未指定交易对，则返回close_all信号
            action = strategy.get('action', '').lower()
            if action == 'close_all' and not symbol:
                # 返回一个特殊信号，在外部处理所有持仓的平仓
                return TradeSignal(
                    action="close_all",
                    symbol="ALL",  # 这个值不会被使用，只是一个占位符
                    extra_data={"raw_signal": signal_data}
                )
            
            # 对于常规平仓操作，需要交易对
            if not symbol:
                self.logger.warning("平仓信号缺少交易对信息")
                return None
                
            # 转换合约名称
            okex_symbol = self._convert_symbol(symbol)
            
            # 创建TradeSignal对象
            trade_signal = TradeSignal(
                action="close",
                symbol=okex_symbol,
                extra_data={"raw_signal": signal_data, "tv_symbol": symbol}
            )
            
            return trade_signal
        except Exception as e:
            self.logger.exception(f"解析平仓信号异常: {e}")
            return None
    
    def _parse_modify_signal(self, signal_data: Dict[str, Any]) -> Optional[TradeSignal]:
        """
        解析修改仓位参数信号
        
        Args:
            signal_data: TradingView信号数据
            
        Returns:
            Optional[TradeSignal]: 解析后的交易信号
        """
        try:
            # 获取策略信息
            strategy = signal_data.get('strategy', {})
            
            # 获取交易对
            symbol = strategy.get('market', signal_data.get('symbol'))
            if not symbol:
                self.logger.warning("修改信号缺少交易对信息")
                return None
                
            # 转换合约名称
            okex_symbol = self._convert_symbol(symbol)
            
            # 获取修改参数
            # 获取止盈止损参数
            entry_price = strategy.get('price', signal_data.get('price'))
            tp_price = strategy.get('tp_price', signal_data.get('tp_price'))
            sl_price = strategy.get('sl_price', signal_data.get('sl_price'))
            
            # 获取止盈止损百分比
            take_profit_pct = strategy.get('tp_pct', signal_data.get('tp_pct'))
            stop_loss_pct = strategy.get('sl_pct', signal_data.get('sl_pct'))
            
            # 如果有价格而没有比例，尝试计算比例
            if tp_price and entry_price and not take_profit_pct:
                # 需要知道方向，尝试从已有仓位获取
                if okex_symbol in self.positions:
                    direction = "long" if self.positions[okex_symbol].quantity > 0 else "short"
                    if direction == "long":
                        take_profit_pct = (float(tp_price) - float(entry_price)) / float(entry_price)
                    else:  # short
                        take_profit_pct = (float(entry_price) - float(tp_price)) / float(entry_price)
                
            if sl_price and entry_price and not stop_loss_pct:
                # 需要知道方向，尝试从已有仓位获取
                if okex_symbol in self.positions:
                    direction = "long" if self.positions[okex_symbol].quantity > 0 else "short"
                    if direction == "long":
                        stop_loss_pct = (float(entry_price) - float(sl_price)) / float(entry_price)
                    else:  # short
                        stop_loss_pct = (float(sl_price) - float(entry_price)) / float(entry_price)
            
            # 获取追踪止损参数
            trailing_stop = strategy.get('trailing_stop', signal_data.get('trailing_stop'))
            trailing_distance = strategy.get('trailing_distance', signal_data.get('trailing_distance'))
            
            # 创建TradeSignal对象
            trade_signal = TradeSignal(
                action="modify",
                symbol=okex_symbol,
                take_profit_pct=float(take_profit_pct) if take_profit_pct else None,
                stop_loss_pct=float(stop_loss_pct) if stop_loss_pct else None,
                trailing_stop=trailing_stop,
                trailing_distance=float(trailing_distance) if trailing_distance else None,
                extra_data={
                    "raw_signal": signal_data,
                    "tv_symbol": symbol,
                    "tp_price": tp_price,
                    "sl_price": sl_price
                }
            )
            
            return trade_signal
        except Exception as e:
            self.logger.exception(f"解析修改信号异常: {e}")
            return None
            
    async def handle_trade_signal(self, signal: TradeSignal) -> Tuple[bool, str]:
        """
        重写handle_trade_signal方法，添加对close_all特殊情况的处理
        
        Args:
            signal: 交易信号对象
            
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        # 处理特殊的close_all信号
        if signal.action == "close_all" and signal.symbol == "ALL":
            return await self.manual_close_all()
            
        # 调用父类的处理方法
        return await super().handle_trade_signal(signal) 