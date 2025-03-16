"""
Example Strategy using the Trading Framework

This is a template showing how to implement a custom strategy
using the trading framework. This example implements a simple
strategy that handles TradingView style webhook signals.
"""

import logging
import json
import time
from typing import Dict, Any, Tuple, Optional

from src.common.trading_framework import BaseStrategy, TradeSignal


class ExampleStrategy(BaseStrategy):
    """Example strategy implementation using the trading framework"""
    
    async def process_signal(self, signal_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        处理接收到的信号
        
        Args:
            signal_data: 原始信号数据，格式由策略自行定义
            
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        try:
            # 在这里根据信号数据格式进行解析
            # 这个例子中我们假设使用类似TradingView的格式
            
            # 输出原始信号数据用于调试
            self.logger.info(f"收到信号: {signal_data}")
            
            # 检查信号完整性
            if not self._validate_signal(signal_data):
                return False, "信号数据不完整或无效"
            
            # 解析信号类型
            signal_type = signal_data.get('type', '').lower()
            
            if signal_type == 'open':
                # 解析开仓信号
                trade_signal = self._parse_open_signal(signal_data)
                if not trade_signal:
                    return False, "无法解析开仓信号"
                
                # 处理开仓信号
                return await self.handle_trade_signal(trade_signal)
                
            elif signal_type == 'close':
                # 解析平仓信号
                trade_signal = self._parse_close_signal(signal_data)
                if not trade_signal:
                    return False, "无法解析平仓信号"
                
                # 处理平仓信号
                return await self.handle_trade_signal(trade_signal)
                
            elif signal_type == 'modify':
                # 解析修改信号
                trade_signal = self._parse_modify_signal(signal_data)
                if not trade_signal:
                    return False, "无法解析修改信号"
                
                # 处理修改信号
                return await self.handle_trade_signal(trade_signal)
                
            elif signal_type == 'status':
                # 返回状态信息
                status = await self.get_status()
                return True, json.dumps(status)
                
            else:
                return False, f"未知信号类型: {signal_type}"
                
        except Exception as e:
            self.logger.exception(f"处理信号异常: {e}")
            return False, f"处理信号异常: {e}"
    
    def _validate_signal(self, signal_data: Dict[str, Any]) -> bool:
        """
        验证信号数据完整性
        
        Args:
            signal_data: 原始信号数据
            
        Returns:
            bool: 信号是否有效
        """
        # 检查必须字段
        if not signal_data:
            return False
            
        # 检查信号类型
        signal_type = signal_data.get('type', '').lower()
        if not signal_type:
            return False
            
        # 根据不同信号类型检查必须字段
        if signal_type == 'open':
            # 开仓信号必须包含标的和方向
            if 'symbol' not in signal_data:
                return False
            if 'direction' not in signal_data:
                return False
                
        elif signal_type == 'close':
            # 平仓信号必须包含标的
            if 'symbol' not in signal_data:
                return False
                
        elif signal_type == 'modify':
            # 修改信号必须包含标的
            if 'symbol' not in signal_data:
                return False
            
        return True
    
    def _parse_open_signal(self, signal_data: Dict[str, Any]) -> Optional[TradeSignal]:
        """
        解析开仓信号
        
        Args:
            signal_data: 原始信号数据
            
        Returns:
            Optional[TradeSignal]: 解析后的交易信号
        """
        try:
            # 提取必要信息
            symbol = signal_data.get('symbol')
            direction = signal_data.get('direction')
            
            # 转换为标准化的交易信号
            trade_signal = TradeSignal(
                action="open",
                symbol=symbol,
                direction=direction.lower() if direction else None,
                entry_price=signal_data.get('entry_price'),
                quantity=signal_data.get('quantity'),
                take_profit_pct=signal_data.get('take_profit_pct'),
                stop_loss_pct=signal_data.get('stop_loss_pct'),
                trailing_stop=signal_data.get('trailing_stop'),
                trailing_distance=signal_data.get('trailing_distance'),
                leverage=signal_data.get('leverage'),
                unit_type=signal_data.get('unit_type'),
                extra_data=signal_data.get('extra', {})
            )
            
            return trade_signal
        except Exception as e:
            self.logger.exception(f"解析开仓信号异常: {e}")
            return None
    
    def _parse_close_signal(self, signal_data: Dict[str, Any]) -> Optional[TradeSignal]:
        """
        解析平仓信号
        
        Args:
            signal_data: 原始信号数据
            
        Returns:
            Optional[TradeSignal]: 解析后的交易信号
        """
        try:
            # 提取必要信息
            symbol = signal_data.get('symbol')
            
            # 转换为标准化的交易信号
            trade_signal = TradeSignal(
                action="close",
                symbol=symbol,
                extra_data=signal_data.get('extra', {})
            )
            
            return trade_signal
        except Exception as e:
            self.logger.exception(f"解析平仓信号异常: {e}")
            return None
    
    def _parse_modify_signal(self, signal_data: Dict[str, Any]) -> Optional[TradeSignal]:
        """
        解析修改信号
        
        Args:
            signal_data: 原始信号数据
            
        Returns:
            Optional[TradeSignal]: 解析后的交易信号
        """
        try:
            # 提取必要信息
            symbol = signal_data.get('symbol')
            
            # 转换为标准化的交易信号
            trade_signal = TradeSignal(
                action="modify",
                symbol=symbol,
                take_profit_pct=signal_data.get('take_profit_pct'),
                stop_loss_pct=signal_data.get('stop_loss_pct'),
                trailing_stop=signal_data.get('trailing_stop'),
                trailing_distance=signal_data.get('trailing_distance'),
                extra_data=signal_data.get('extra', {})
            )
            
            return trade_signal
        except Exception as e:
            self.logger.exception(f"解析修改信号异常: {e}")
            return None


# 以下是使用示例（通常会放在main.py中）
if __name__ == "__main__":
    # 假设这是我们的配置
    example_config = {
        "app_name": "example_strategy",
        "exchange": {
            "api_key": "YOUR_API_KEY",
            "secret_key": "YOUR_SECRET_KEY",
            "passphrase": "YOUR_PASSPHRASE",
            "type": "okex"
        },
        "strategy": {
            "leverage": 5,
            "per_position_usdt": 200,
            "take_profit_pct": 0.05,
            "stop_loss_pct": 0.03,
            "trailing_stop": True,
            "trailing_distance": 0.02,
            "unit_type": "quote",
            "enable_symbol_pool": True,
            "default_symbols": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
        }
    }
    
    # 注意：这里只是示例代码，实际使用时应通过TradingFramework类来初始化和运行
    # from src.common.trading_framework import TradingFramework
    # 
    # framework = TradingFramework(
    #     app_name="example_strategy",
    #     strategy_class=ExampleStrategy,
    #     config=example_config
    # )
    # 
    # # 启动策略监控
    # import asyncio
    # asyncio.run(framework.run_forever()) 