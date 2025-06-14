"""
退出策略实现模块

该模块提供各种退出策略的实现，包括止盈止损、追踪止损等，与交易框架集成
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, Any, Optional, List, Tuple, Callable
from dataclasses import dataclass, field
import time
import logging
import asyncio
import numpy as np
import pandas as pd
from typing import Optional
import math
from datetime import datetime, timedelta

# 添加对order_utils的导入，获取价格精度函数
from src.common.order_utils import get_price_precision

# 平仓触发类型枚举
class ExitTriggerType(str, Enum):
    """平仓触发类型"""
    TAKE_PROFIT = "TAKE_PROFIT"  # 止盈
    STOP_LOSS = "STOP_LOSS"      # 止损
    TRAILING_STOP = "TRAILING_STOP"  # 追踪止损
    LADDER_TP = "LADDER_TP"      # 阶梯止盈
    TIME_BASED = "TIME_BASED"    # 基于时间
    ATR_BASED = "ATR_BASED"      # 基于ATR
    CUSTOM = "CUSTOM"            # 自定义

@dataclass
class ExitSignal:
    """平仓信号数据结构"""
    triggered: bool              # 是否触发
    exit_type: ExitTriggerType   # 触发类型
    close_percentage: float      # 平仓比例，1.0表示全部平仓
    price: float                 # 触发价格
    message: str = ""            # 描述信息
    params: Dict[str, Any] = field(default_factory=dict)  # 额外参数
    need_cleanup: bool = False   # 是否需要执行完整的资源清理流程

class ExitStrategy(ABC):
    """平仓策略基类"""
    
    def __init__(self, app_name: str, name: str, priority: int = 0, position_mgr=None, 
                 strategy_config: Dict[str, Any] = None, data_cache=None, trader=None):
        """
        初始化平仓策略
        
        Args:
            app_name: 应用名称，用于日志记录
            name: 策略名称
            priority: 优先级，数值越小优先级越高
            position_mgr: 仓位管理器
            strategy_config: 策略配置
            data_cache: 数据缓存对象
            trader: 交易执行器
        """
        self.app_name = app_name
        self.name = name
        self.priority = priority
        self.enabled = True
        self.position_mgr = position_mgr
        self.strategy_config = strategy_config or {}
        self.data_cache = data_cache
        self.trader = trader
        self.logger = logging.getLogger(app_name)
        
        # 记录初始化信息
        self.logger.info(f"初始化退出策略: {self.name}, 优先级: {self.priority}")
    
    @abstractmethod
    async def check_exit_condition(self, position: Any, current_price: float, **kwargs) -> ExitSignal:
        """
        检查是否满足平仓条件
        
        Args:
            position: 仓位对象
            current_price: 当前价格
            **kwargs: 额外参数
            
        Returns:
            ExitSignal: 平仓信号
        """
        pass
    
    async def execute_exit(self, position: Any, exit_signal: ExitSignal, 
                         execute_close_func: Callable = None) -> bool:
        """
        执行平仓操作
        
        Args:
            position: 仓位对象
            exit_signal: 平仓信号
            execute_close_func: 执行平仓的函数，接收symbol和position参数
            
        Returns:
            bool: 是否执行成功
        """
        try:
            symbol = position.symbol
            
            # 计算平仓数量
            close_percentage = exit_signal.close_percentage
            
            # 如果是阶梯止盈策略，更新已平仓百分比
            if isinstance(self, LadderExitStrategy):
                self.update_closed_percentage(symbol, close_percentage)
            
            # 记录平仓信息
            self.logger.info(f"执行平仓 {symbol}: {exit_signal.exit_type}, 百分比: {close_percentage*100:.1f}%, "
                           f"价格: {exit_signal.price}, 原因: {exit_signal.message}")
            
            # 如果提供了执行平仓的函数，则调用
            if execute_close_func:
                # 修改：传递close_percentage参数给执行函数
                success, msg = await execute_close_func(symbol, position, close_percentage)
                if success:
                    self.logger.info(f"{symbol} {exit_signal.exit_type} 平仓成功: {msg}")
                    # 如果完全平仓，清理与该symbol相关的资源
                    if close_percentage >= 0.999:
                        self.clean_symbol_resources(symbol, position.id if hasattr(position, 'id') else None)
                else:
                    self.logger.error(f"{symbol} {exit_signal.exit_type} 平仓失败: {msg}")
                return success
            
            # 否则，使用默认逻辑
            # 这里仅记录日志，实际平仓由调用方负责
            self.logger.warning(f"{symbol} 未提供平仓执行函数，无法执行平仓")
            return False
            
        except Exception as e:
            self.logger.error(f"执行平仓失败: {e}", exc_info=True)
            return False
    
    def clean_symbol_resources(self, symbol: str, position_id: str = None):
        """
        清理与指定交易对相关的资源
        
        Args:
            symbol: 交易对
            position_id: 仓位ID，如果提供则只清理该仓位的资源
        """
        # 基类实现为空，子类根据需要重写
        pass
    
    def init_position_resources(self, position: Any):
        """
        初始化仓位相关资源
        
        Args:
            position: 仓位对象
        """
        # 基类实现为空，子类根据需要重写
        pass
    
    @abstractmethod
    def to_dict(self) -> Dict[str, Any]:
        """将策略转换为字典，用于序列化"""
        return {
            "name": self.name,
            "type": self.__class__.__name__,
            "priority": self.priority,
            "enabled": self.enabled
        }
    
    @classmethod
    @abstractmethod
    def from_dict(cls, data: Dict[str, Any], app_name: str, position_mgr=None, 
                 strategy_config=None, data_cache=None, trader=None) -> 'ExitStrategy':
        """从字典创建策略对象"""
        pass
    
    def update_params(self, params: Dict[str, Any]) -> None:
        """更新策略参数"""
        for key, value in params.items():
            if hasattr(self, key):
                setattr(self, key, value)
                self.logger.info(f"更新策略参数 {self.name}: {key}={value}")

class FixedPercentExitStrategy(ExitStrategy):
    """固定百分比止盈止损策略"""
    
    def __init__(self, app_name: str, take_profit_pct: float = 0.05, stop_loss_pct: float = 0.03, 
                 priority: int = 10, name: str = "固定百分比", position_mgr=None, 
                 strategy_config=None, data_cache=None, trader=None):
        """
        初始化固定百分比止盈止损策略
        
        Args:
            app_name: 应用名称，用于日志记录
            take_profit_pct: 止盈百分比，默认0.05(5%)
            stop_loss_pct: 止损百分比，默认0.03(3%)
            priority: 优先级，数值越小优先级越高
            name: 策略名称
            position_mgr: 仓位管理器
            strategy_config: 策略配置
            data_cache: 数据缓存对象
            trader: 交易执行器
        """
        super().__init__(app_name, name, priority, position_mgr, strategy_config, data_cache, trader)
        
        # 从策略配置中读取止盈止损参数，如果没有则使用默认值
        if strategy_config and 'strategy' in strategy_config:
            strategy_settings = strategy_config['strategy']
            self.take_profit_pct = strategy_settings.get('take_profit_pct', take_profit_pct)
            self.stop_loss_pct = strategy_settings.get('stop_loss_pct', stop_loss_pct)
        else:
            self.take_profit_pct = take_profit_pct
            self.stop_loss_pct = stop_loss_pct
            
        self.logger.info(f"固定百分比策略参数: 止盈={self.take_profit_pct*100:.2f}%, 止损={self.stop_loss_pct*100:.2f}%")
    
    async def check_exit_condition(self, position: Any, current_price: float, **kwargs) -> ExitSignal:
        """
        检查是否满足固定百分比止盈止损条件
        
        Args:
            position: 仓位对象
            current_price: 当前价格
            kwargs: 额外参数
            
        Returns:
            ExitSignal: 平仓信号
        """
        if not self.enabled:
            return ExitSignal(triggered=False, exit_type=ExitTriggerType.CUSTOM, 
                             close_percentage=0, price=current_price)
        
        # 获取仓位信息
        direction = position.direction
        entry_price = position.entry_price
        leverage = getattr(position, 'leverage', 1)
        symbol = position.symbol
        
        # 从交易所获取价格精度
        precision = 4  # 默认精度
        if self.trader:
            # 确定是否为现货或合约
            is_spot = not ("-SWAP" in symbol or "-FUTURES" in symbol or "-PERPETUAL" in symbol)
            try:
                precision = get_price_precision(self.trader, symbol, is_spot)
            except Exception as e:
                self.logger.warning(f"获取价格精度失败，使用默认值: {e}")
        
        # 获取止盈止损设置 - 可能来自仓位或信号
        signal = getattr(position, 'signal', None)
        take_profit_pct = signal.take_profit_pct if signal and hasattr(signal, 'take_profit_pct') and signal.take_profit_pct is not None else self.take_profit_pct
        stop_loss_pct = signal.stop_loss_pct if signal and hasattr(signal, 'stop_loss_pct') and signal.stop_loss_pct is not None else self.stop_loss_pct
        
        # 如果有杠杆，需要调整止盈止损比例
        if leverage > 1:
            take_profit_pct = take_profit_pct / leverage
            stop_loss_pct = stop_loss_pct / leverage
        
        # 计算当前的盈亏百分比
        pnl_pct = 0.0
        if direction == "long":
            pnl_pct = (current_price - entry_price) / entry_price
            target_tp_price = entry_price * (1 + take_profit_pct)
            target_sl_price = entry_price * (1 - stop_loss_pct)
        else:  # short
            pnl_pct = (entry_price - current_price) / entry_price
            target_tp_price = entry_price * (1 - take_profit_pct)
            target_sl_price = entry_price * (1 + stop_loss_pct)
        
        # 使用动态精度格式化价格
        tp_price_formatted = f"{{:.{precision}f}}".format(target_tp_price)
        sl_price_formatted = f"{{:.{precision}f}}".format(target_sl_price)
        
        # 添加更详细的日志
        self.logger.debug(f"检查 {position.symbol} {direction}仓位固定止盈止损条件: 入场价={entry_price}, 当前价={current_price}, "
                         f"当前盈亏={pnl_pct*100:.2f}%, 止盈比例={take_profit_pct*100:.2f}%, 价格={tp_price_formatted}; "
                         f"止损比例={stop_loss_pct*100:.2f}%, 价格={sl_price_formatted}")
        
        if direction == "long":
            # 多头止盈
            if current_price >= target_tp_price:
                return ExitSignal(
                    triggered=True,
                    exit_type=ExitTriggerType.TAKE_PROFIT,
                    close_percentage=1.0,
                    price=current_price,
                    message=f"触发多头固定止盈: {current_price} >= {tp_price_formatted}, 盈利: {pnl_pct*100:.2f}%"
                )
            # 多头止损
            elif current_price <= target_sl_price:
                return ExitSignal(
                    triggered=True,
                    exit_type=ExitTriggerType.STOP_LOSS,
                    close_percentage=1.0,
                    price=current_price,
                    message=f"触发多头固定止损: {current_price} <= {sl_price_formatted}, 亏损: {-pnl_pct*100:.2f}%"
                )
        else:  # short
            # 空头止盈
            if current_price <= target_tp_price:
                return ExitSignal(
                    triggered=True,
                    exit_type=ExitTriggerType.TAKE_PROFIT,
                    close_percentage=1.0,
                    price=current_price,
                    message=f"触发空头固定止盈: {current_price} <= {tp_price_formatted}, 盈利: {pnl_pct*100:.2f}%"
                )
            # 空头止损
            elif current_price >= target_sl_price:
                return ExitSignal(
                    triggered=True,
                    exit_type=ExitTriggerType.STOP_LOSS,
                    close_percentage=1.0,
                    price=current_price,
                    message=f"触发空头固定止损: {current_price} >= {sl_price_formatted}, 亏损: {-pnl_pct*100:.2f}%"
                )
        
        # 未触发条件
        return ExitSignal(
            triggered=False,
            exit_type=ExitTriggerType.CUSTOM,
            close_percentage=0,
            price=current_price
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """将策略转换为字典，用于序列化"""
        data = super().to_dict()
        data.update({
            "take_profit_pct": self.take_profit_pct,
            "stop_loss_pct": self.stop_loss_pct
        })
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any], app_name: str, position_mgr=None, 
                 strategy_config=None, data_cache=None, trader=None) -> 'FixedPercentExitStrategy':
        """从字典创建策略对象"""
        return cls(
            app_name=app_name,
            take_profit_pct=data.get("take_profit_pct", 0.05),
            stop_loss_pct=data.get("stop_loss_pct", 0.03),
            priority=data.get("priority", 10),
            name=data.get("name", "固定百分比"),
            position_mgr=position_mgr,
            strategy_config=strategy_config,
            data_cache=data_cache,
            trader=trader
        )

class TrailingStopExitStrategy(ExitStrategy):
    """追踪止损策略"""
    
    def __init__(self, app_name: str, trailing_distance: float = 0.02, activation_pct: float = 0.01,
                 priority: int = 20, name: str = "追踪止损", position_mgr=None, 
                 strategy_config=None, data_cache=None, trader=None):
        """
        初始化追踪止损策略
        
        Args:
            app_name: 应用名称，用于日志记录
            trailing_distance: 追踪距离，默认0.02(2%)
            activation_pct: 激活追踪止损的收益百分比，默认0.01(1%)
            priority: 优先级，数值越小优先级越高
            name: 策略名称
            position_mgr: 仓位管理器
            strategy_config: 策略配置
            data_cache: 数据缓存对象
            trader: 交易执行器
        """
        super().__init__(app_name, name, priority, position_mgr, strategy_config, data_cache, trader)
        
        # 从策略配置中读取参数，如果没有则使用默认值
        if strategy_config and 'strategy' in strategy_config:
            strategy_settings = strategy_config['strategy']
            self.trailing_distance = strategy_settings.get('trailing_distance', trailing_distance)
            self.activation_pct = strategy_settings.get('activation_pct', activation_pct)
        else:
            self.trailing_distance = trailing_distance
            self.activation_pct = activation_pct
        
        # 跟踪的最高价和最低价，初始为0和无穷大
        # 修改为使用(symbol, position_id)作为key的字典，以支持同一交易对多个仓位
        self.highest_price = {}  # (symbol, position_id) -> highest_price 映射
        self.lowest_price = {}   # (symbol, position_id) -> lowest_price 映射
        
        self.logger.info(f"追踪止损策略参数: 追踪距离={self.trailing_distance*100:.2f}%, 激活收益={self.activation_pct*100:.2f}%")
    
    def _get_position_key(self, position):
        """获取仓位的唯一键"""
        position_id = getattr(position, 'id', None) or getattr(position, 'position_id', str(id(position)))
        return (position.symbol, position_id)
    
    def init_position_resources(self, position: Any):
        """
        初始化持仓追踪止损的资源
        """
        # 先检查持仓是否已关闭
        if hasattr(position, 'closed') and position.closed:
            self.logger.warning(f"跳过初始化已关闭的持仓追踪止损资源: {position.symbol} (ID: {position.position_id})")
            return
            
        key = self._get_position_key(position)
        symbol = position.symbol
        entry_price = position.entry_price
        
        # 使用position的high_price和low_price，如果有的话
        if hasattr(position, 'high_price') and position.high_price:
            self.highest_price[key] = position.high_price
        else:
            self.highest_price[key] = entry_price
            
        if hasattr(position, 'low_price') and position.low_price and position.low_price != float('inf'):
            self.lowest_price[key] = position.low_price
        else:
            self.lowest_price[key] = entry_price
            
        self.logger.info(f"初始化追踪止损仓位资源: {symbol} (ID: {key[1]}), 入场价: {entry_price}")
    
    def clean_symbol_resources(self, symbol: str, position_id: str = None):
        """清理与指定交易对相关的资源"""
        keys_to_remove = []
        
        # 如果指定了仓位ID，只清理该仓位的资源
        if position_id:
            key = (symbol, position_id)
            if key in self.highest_price:
                del self.highest_price[key]
            if key in self.lowest_price:
                del self.lowest_price[key]
            self.logger.info(f"清理追踪止损资源: {symbol} (ID: {position_id})")
        else:
            # 否则清理该交易对的所有资源
            for key in list(self.highest_price.keys()):
                if key[0] == symbol:
                    del self.highest_price[key]
                    self.logger.info(f"清理追踪止损资源: {symbol} (ID: {key[1]})")
            
            for key in list(self.lowest_price.keys()):
                if key[0] == symbol:
                    del self.lowest_price[key]
    
    async def check_exit_condition(self, position: Any, current_price: float, **kwargs) -> ExitSignal:
        """
        检查是否满足追踪止损条件
        
        Args:
            position: 仓位对象
            current_price: 当前价格
            kwargs: 额外参数
            
        Returns:
            ExitSignal: 平仓信号
        """
        if not self.enabled:
            return ExitSignal(triggered=False, exit_type=ExitTriggerType.CUSTOM, 
                             close_percentage=0, price=current_price)
        
        # 获取仓位信息
        symbol = position.symbol
        direction = position.direction
        entry_price = position.entry_price
        leverage = getattr(position, 'leverage', 1)
        
        # 获取仓位的唯一键
        key = self._get_position_key(position)
        
        # 获取追踪止损设置
        signal = getattr(position, 'signal', None)
        trailing_distance = signal.trailing_distance if signal and hasattr(signal, 'trailing_distance') and signal.trailing_distance is not None else self.trailing_distance
        trailing_stop = signal.trailing_stop if signal and hasattr(signal, 'trailing_stop') and signal.trailing_stop is not None else True
        
        # 如果不启用追踪止损，直接返回
        if not trailing_stop:
            return ExitSignal(triggered=False, exit_type=ExitTriggerType.CUSTOM, 
                             close_percentage=0, price=current_price)
        
        # 如果有杠杆，调整追踪距离和激活阈值
        activation_pct = self.activation_pct
        if leverage > 1:
            trailing_distance = trailing_distance / leverage
            activation_pct = activation_pct / leverage
        
        # 初始化最高/最低价
        if key not in self.highest_price or key not in self.lowest_price:
            self.init_position_resources(position)
        
        # 计算当前收益率
        if direction == "long":
            pnl_pct = (current_price - entry_price) / entry_price
            
            # 更新最高价
            if current_price > self.highest_price[key]:
                old_highest = self.highest_price[key]
                self.highest_price[key] = current_price
                self.logger.debug(f"{symbol} {direction}仓位更新最高价: {old_highest:.6f} -> {current_price:.6f}")
            
            # 只有当收益率超过激活百分比时才启用追踪止损
            if pnl_pct >= activation_pct:
                # 计算追踪止损价格
                stop_price = self.highest_price[key] * (1 - trailing_distance)
                price_distance_pct = (self.highest_price[key] - current_price) / self.highest_price[key] * 100
                stop_distance_pct = (current_price - stop_price) / current_price * 100
                
                self.logger.debug(f"{symbol} {direction}仓位追踪止损激活: 当前盈利={pnl_pct*100:.2f}% >= 激活阈值={activation_pct*100:.2f}%")
                self.logger.debug(f"{symbol} {direction}仓位追踪止损价格: 最高价={self.highest_price[key]:.6f} * (1 - {trailing_distance}) = {stop_price:.6f}")
                self.logger.debug(f"{symbol} {direction}仓位距离最高点: {price_distance_pct:.2f}%, 距离止损线: {stop_distance_pct:.2f}%")
                
                # 检查是否触发追踪止损
                if current_price <= stop_price:
                    self.logger.info(f"{symbol} 触发多头追踪止损: 最高价={self.highest_price[key]:.4f}, 当前价={current_price:.4f}, 止损线={stop_price:.4f}, 回撤={price_distance_pct:.2f}%")
                    return ExitSignal(
                        triggered=True,
                        exit_type=ExitTriggerType.TRAILING_STOP,
                        close_percentage=1.0,
                        price=current_price,
                        message=f"触发多头追踪止损: 最高价={self.highest_price[key]:.4f}, 当前价={current_price:.4f}, 止损线={stop_price:.4f}, 回撤={price_distance_pct:.2f}%"
                    )
        else:  # short
            pnl_pct = (entry_price - current_price) / entry_price
            
            # 更新最低价
            if current_price < self.lowest_price[key]:
                old_lowest = self.lowest_price[key]
                self.lowest_price[key] = current_price
                self.logger.debug(f"{symbol} {direction}仓位更新最低价: {old_lowest:.6f} -> {current_price:.6f}")
            
            # 只有当收益率超过激活百分比时才启用追踪止损
            if pnl_pct >= activation_pct:
                # 计算追踪止损价格
                stop_price = self.lowest_price[key] * (1 + trailing_distance)
                price_distance_pct = (current_price - self.lowest_price[key]) / self.lowest_price[key] * 100
                stop_distance_pct = (stop_price - current_price) / current_price * 100
                
                self.logger.debug(f"{symbol} {direction}仓位追踪止损激活: 当前盈利={pnl_pct*100:.2f}% >= 激活阈值={activation_pct*100:.2f}%")
                self.logger.debug(f"{symbol} {direction}仓位追踪止损价格: 最低价={self.lowest_price[key]:.6f} * (1 + {trailing_distance}) = {stop_price:.6f}")
                self.logger.debug(f"{symbol} {direction}仓位距离最低点: {price_distance_pct:.2f}%, 距离止损线: {stop_distance_pct:.2f}%")
                
                # 检查是否触发追踪止损
                if current_price >= stop_price:
                    self.logger.info(f"{symbol} 触发空头追踪止损: 最低价={self.lowest_price[key]:.4f}, 当前价={current_price:.4f}, 止损线={stop_price:.4f}, 回撤={price_distance_pct:.2f}%")
                    return ExitSignal(
                        triggered=True,
                        exit_type=ExitTriggerType.TRAILING_STOP,
                        close_percentage=1.0,
                        price=current_price,
                        message=f"触发空头追踪止损: 最低价={self.lowest_price[key]:.4f}, 当前价={current_price:.4f}, 止损线={stop_price:.4f}, 回撤={price_distance_pct:.2f}%"
                    )
        
        # 未触发条件
        return ExitSignal(
            triggered=False,
            exit_type=ExitTriggerType.CUSTOM,
            close_percentage=0,
            price=current_price
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """将策略转换为字典，用于序列化"""
        data = super().to_dict()
        data.update({
            "trailing_distance": self.trailing_distance,
            "activation_pct": self.activation_pct
        })
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any], app_name: str, position_mgr=None, 
                 strategy_config=None, data_cache=None, trader=None) -> 'TrailingStopExitStrategy':
        """从字典创建策略对象"""
        return cls(
            app_name=app_name,
            trailing_distance=data.get("trailing_distance", 0.02),
            activation_pct=data.get("activation_pct", 0.01),
            priority=data.get("priority", 20),
            name=data.get("name", "追踪止损"),
            position_mgr=position_mgr,
            strategy_config=strategy_config,
            data_cache=data_cache,
            trader=trader
        )

class LadderExitStrategy(ExitStrategy):
    """阶梯止盈策略"""
    
    def __init__(self, app_name: str, ladder_step_pct: float = 0.2, close_pct_per_step: float = 0.2,
                 priority: int = 30, name: str = "阶梯止盈", position_mgr=None, 
                 strategy_config=None, data_cache=None, trader=None):
        """
        初始化阶梯止盈策略
        
        Args:
            app_name: 应用名称，用于日志记录
            ladder_step_pct: 每阶梯的价格间隔百分比，默认0.2(20%)
            close_pct_per_step: 每阶梯的平仓百分比，默认0.2(20%)
            priority: 优先级，数值越小优先级越高
            name: 策略名称
            position_mgr: 仓位管理器
            strategy_config: 策略配置
            data_cache: 数据缓存对象
            trader: 交易执行器
        """
        super().__init__(app_name, name, priority, position_mgr, strategy_config, data_cache, trader)
        
        # 从策略配置中读取参数，如果没有则使用默认值
        if strategy_config and 'strategy' in strategy_config:
            ladder_config = strategy_config['strategy'].get('ladder_take_profit', {})
            self.ladder_step_pct = ladder_config.get('step_pct', ladder_step_pct)
            self.close_pct_per_step = ladder_config.get('close_pct', close_pct_per_step)
        else:
            self.ladder_step_pct = ladder_step_pct
            self.close_pct_per_step = close_pct_per_step
        
        # 跟踪已触发的最高阶梯级别和已平仓的百分比
        # 修改为使用(symbol, position_id)作为key的字典，以支持同一交易对多个仓位
        self.max_triggered_level = {}  # (symbol, position_id) -> level 映射
        self.closed_percentage = {}    # (symbol, position_id) -> percentage 映射
        
        self.logger.info(f"阶梯止盈策略参数: 阶梯间隔={self.ladder_step_pct*100:.2f}%, 每阶梯平仓比例={self.close_pct_per_step*100:.2f}%")
    
    def _get_position_key(self, position):
        """获取仓位的唯一键"""
        position_id = getattr(position, 'id', None) or getattr(position, 'position_id', str(id(position)))
        return (position.symbol, position_id)
    
    def get_max_triggered_level(self, position: Any) -> int:
        """获取已触发的最高阶梯级别"""
        key = self._get_position_key(position)
        return self.max_triggered_level.get(key, 0)
    
    def get_closed_percentage(self, position: Any) -> float:
        """获取已平仓的百分比"""
        key = self._get_position_key(position)
        return self.closed_percentage.get(key, 0.0)
    
    def update_closed_percentage(self, symbol: str, percentage: float, position_id: str = None) -> None:
        """更新已平仓百分比"""
        if position_id is None:
            # 兼容旧接口，尝试查找该symbol的所有key
            for key in list(self.closed_percentage.keys()):
                if key[0] == symbol:
                    current_percentage = self.closed_percentage.get(key, 0.0)
                    self.closed_percentage[key] = current_percentage + percentage
                    self.logger.info(f"{symbol} (ID: {key[1]}) 更新已平仓百分比: {current_percentage:.2f} -> {self.closed_percentage[key]:.2f}")
        else:
            # 使用指定的position_id
            key = (symbol, position_id)
            current_percentage = self.closed_percentage.get(key, 0.0)
            self.closed_percentage[key] = current_percentage + percentage
            self.logger.info(f"{symbol} (ID: {position_id}) 更新已平仓百分比: {current_percentage:.2f} -> {self.closed_percentage[key]:.2f}")
    
    def init_position_resources(self, position: Any):
        """
        初始化仓位相关资源
        """
        # 先检查持仓是否已关闭
        if hasattr(position, 'closed') and position.closed:
            self.logger.warning(f"跳过初始化已关闭的持仓阶梯平仓资源: {position.symbol} (ID: {position.position_id})")
            return
            
        key = self._get_position_key(position)
        symbol = position.symbol
        
        # 如果仓位已经有阶梯级别和已平仓比例，使用这些值
        if hasattr(position, 'ladder_closed_pct'):
            self.closed_percentage[key] = position.ladder_closed_pct
        else:
            self.closed_percentage[key] = 0.0
        
        self.max_triggered_level[key] = 0
        
        self.logger.info(f"初始化阶梯止盈仓位资源: {symbol} (ID: {key[1]})")
    
    def clean_symbol_resources(self, symbol: str, position_id: str = None):
        """清理与指定交易对相关的资源"""
        # 如果指定了仓位ID，只清理该仓位的资源
        if position_id:
            key = (symbol, position_id)
            if key in self.max_triggered_level:
                del self.max_triggered_level[key]
            if key in self.closed_percentage:
                del self.closed_percentage[key]
            self.logger.info(f"清理阶梯止盈资源: {symbol} (ID: {position_id})")
        else:
            # 否则清理该交易对的所有资源
            for key in list(self.max_triggered_level.keys()):
                if key[0] == symbol:
                    del self.max_triggered_level[key]
            
            for key in list(self.closed_percentage.keys()):
                if key[0] == symbol:
                    del self.closed_percentage[key]
            
            self.logger.info(f"清理阶梯止盈资源: {symbol} (所有仓位)")
    
    async def check_exit_condition(self, position: Any, current_price: float, **kwargs) -> ExitSignal:
        """
        检查是否满足阶梯止盈条件
        
        Args:
            position: 仓位对象
            current_price: 当前价格
            kwargs: 额外参数
            
        Returns:
            ExitSignal: 平仓信号
        """
        if not self.enabled:
            return ExitSignal(triggered=False, exit_type=ExitTriggerType.CUSTOM, 
                             close_percentage=0, price=current_price)
        
        # 获取仓位信息
        symbol = position.symbol
        direction = position.direction
        entry_price = position.entry_price
        leverage = getattr(position, 'leverage', 1)
        
        # 获取仓位的唯一键
        key = self._get_position_key(position)
        
        # 获取阶梯止盈设置
        # 先检查仓位是否有阶梯止盈设置
        ladder_tp = getattr(position, 'ladder_tp', None)
        ladder_tp_step = getattr(position, 'ladder_tp_step', self.ladder_step_pct)
        ladder_tp_pct = getattr(position, 'ladder_tp_pct', self.close_pct_per_step)
        ladder_closed_pct = getattr(position, 'ladder_closed_pct', 0.0)
        
        # 如果仓位没有启用阶梯止盈，检查信号是否有设置
        if ladder_tp is None:
            signal = getattr(position, 'signal', None)
            if signal and hasattr(signal, 'extra_data') and signal.extra_data:
                ladder_tp = signal.extra_data.get('ladder_tp', False)
                ladder_tp_step = signal.extra_data.get('ladder_tp_step', self.ladder_step_pct)
                ladder_tp_pct = signal.extra_data.get('ladder_tp_pct', self.close_pct_per_step)
        
        # 如果仍未设置，使用策略配置中的值
        if ladder_tp is None and 'strategy' in self.strategy_config:
            ladder_config = self.strategy_config['strategy'].get('ladder_take_profit', {})
            ladder_tp = ladder_config.get('enabled', False)
        
        # 如果不启用阶梯止盈，直接返回
        if not ladder_tp:
            return ExitSignal(triggered=False, exit_type=ExitTriggerType.CUSTOM, 
                             close_percentage=0, price=current_price)
        
        # 初始化最高触发级别和已平仓百分比
        if key not in self.max_triggered_level or key not in self.closed_percentage:
            self.init_position_resources(position)
        
        # 计算当前盈利百分比 - 使用杠杆后的收益率
        if direction == 'long':
            current_pnl_pct = (current_price - entry_price) / entry_price * leverage
        else:  # short
            current_pnl_pct = (entry_price - current_price) / entry_price * leverage
        
        # 计算应该触发的阶梯级别（向下取整）
        current_ladder_level = int(current_pnl_pct / ladder_tp_step)
        
        # 如果当前级别高于已触发的最高级别，并且级别大于0
        if current_ladder_level > self.max_triggered_level.get(key, 0) and current_ladder_level > 0:
            # 计算本次应平仓的百分比
            total_should_close_pct = current_ladder_level * ladder_tp_pct
            
            # 确保不超过1.0（全部平仓）
            total_should_close_pct = min(total_should_close_pct, 1.0)
            
            # 计算本次新增的平仓百分比
            current_closed_pct = self.closed_percentage.get(key, 0.0)
            close_pct_this_time = total_should_close_pct - current_closed_pct
            
            # 如果需要平仓的比例为0或负数，说明已经全部平仓，返回未触发
            if close_pct_this_time <= 0:
                return ExitSignal(triggered=False, exit_type=ExitTriggerType.CUSTOM, 
                                 close_percentage=0, price=current_price)
            
            # 更新最高触发级别和已平仓百分比
            self.max_triggered_level[key] = current_ladder_level
            
            # 记录仓位的最新阶梯级别和已平仓比例
            if self.position_mgr:
                position.ladder_closed_pct = total_should_close_pct
                self.position_mgr.save_position(position)
            
            return ExitSignal(
                triggered=True,
                exit_type=ExitTriggerType.LADDER_TP,
                close_percentage=close_pct_this_time,
                price=current_price,
                message=f"触发阶梯止盈 级别{current_ladder_level}: 回吐{close_pct_this_time*100:.1f}%, 累计平仓{total_should_close_pct*100:.1f}%",
                params={
                    "ladder_level": current_ladder_level,
                    "total_closed_pct": total_should_close_pct,
                    "symbol": symbol,
                    "position_id": key[1]  # 添加position_id到参数中
                }
            )
        
        # 未触发条件
        return ExitSignal(
            triggered=False,
            exit_type=ExitTriggerType.CUSTOM,
            close_percentage=0,
            price=current_price
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """将策略转换为字典，用于序列化"""
        data = super().to_dict()
        data.update({
            "ladder_step_pct": self.ladder_step_pct,
            "close_pct_per_step": self.close_pct_per_step
        })
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any], app_name: str, position_mgr=None, 
                 strategy_config=None, data_cache=None, trader=None) -> 'LadderExitStrategy':
        """从字典创建策略对象"""
        return cls(
            app_name=app_name,
            ladder_step_pct=data.get("ladder_step_pct", 0.2),
            close_pct_per_step=data.get("close_pct_per_step", 0.2),
            priority=data.get("priority", 30),
            name=data.get("name", "阶梯止盈"),
            position_mgr=position_mgr,
            strategy_config=strategy_config,
            data_cache=data_cache,
            trader=trader
        )

class TimeBasedExitStrategy(ExitStrategy):
    """基于K线的时间止损策略"""
    
    def __init__(self, app_name: str, candle_timeframe: str = "15m", candle_count: int = 3,
                 priority: int = 50, name: str = "K线时间止损", position_mgr=None, 
                 strategy_config=None, data_cache=None, trader=None):
        """
        初始化基于K线的时间止损策略
        
        Args:
            app_name: 应用名称，用于日志记录
            candle_timeframe: K线周期，如"1m", "5m", "15m", "1h", "4h", "1d"
            candle_count: 检查的K线数量，默认为3
            priority: 优先级，数值越小优先级越高
            name: 策略名称
            position_mgr: 仓位管理器
            strategy_config: 策略配置
            data_cache: 数据缓存对象
            trader: 交易执行器
        """
        super().__init__(app_name, name, priority, position_mgr, strategy_config, data_cache, trader)
        
        # 从策略配置中读取参数，如果没有则使用默认值
        if strategy_config and 'strategy' in strategy_config:
            time_stop_loss = strategy_config['strategy'].get('time_stop_loss', {})
            self.enable_time_stop = time_stop_loss.get('enabled', False)
            self.candle_timeframe = time_stop_loss.get('candle_timeframe', candle_timeframe)
            self.candle_count = time_stop_loss.get('candle_count', candle_count)
        else:
            self.enable_time_stop = True
            self.candle_timeframe = candle_timeframe
            self.candle_count = candle_count
        
        # 将candle_timeframe转换为OKEx API需要的格式
        self.bar_type = self._convert_timeframe_to_bar_type(self.candle_timeframe)
        
        self.logger.info(f"K线时间止损策略参数: 启用={self.enable_time_stop}, K线周期={self.candle_timeframe}, K线数量={self.candle_count}")
    
    def _convert_timeframe_to_bar_type(self, timeframe: str) -> str:
        """
        将timeframe转换为OKEx API需要的bar格式
        
        Args:
            timeframe: K线周期，如"1m", "5m", "15m", "1h", "4h", "1d"
            
        Returns:
            str: 转换后的bar格式
        """
        # 如果timeframe是数字+m/h/d格式，直接返回
        if isinstance(timeframe, str) and timeframe[-1] in ['m', 'h', 'd']:
            return timeframe
        
        # 如果timeframe是数字，认为是分钟
        try:
            minutes = int(timeframe)
            return f"{minutes}m"
        except (ValueError, TypeError):
            self.logger.warning(f"无法解析timeframe: {timeframe}，使用默认值15m")
            return "15m"
    
    async def check_exit_condition(self, position: Any, current_price: float, **kwargs) -> ExitSignal:
        """
        检查是否满足基于K线的时间止损条件
        
        Args:
            position: 仓位对象
            current_price: 当前价格
            kwargs: 额外参数
            
        Returns:
            ExitSignal: 平仓信号
        """
        if not self.enabled or not self.enable_time_stop:
            return ExitSignal(triggered=False, exit_type=ExitTriggerType.CUSTOM, 
                             close_percentage=0, price=current_price)
        
        # 获取仓位信息
        symbol = position.symbol
        direction = position.direction
        entry_price = position.entry_price
        
        # 计算持仓时间（分钟）
        current_time = int(time.time() * 1000)
        position_timestamp = position.timestamp
        if position_timestamp < 9999999999:  # 如果是秒级时间戳
            position_timestamp *= 1000
        
        holding_time_ms = current_time - position_timestamp
        holding_time_minutes = holding_time_ms / (1000 * 60)
        
        # 计算最小检查时间 - 根据K线周期和数量
        min_check_minutes = self._get_minutes_from_timeframe(self.candle_timeframe) * self.candle_count
        
        # 如果持仓时间不足最小检查时间，不执行检查
        if holding_time_minutes < min_check_minutes:
            self.logger.debug(f"{symbol} {direction}仓位持仓时间 {holding_time_minutes:.1f} 分钟，小于最小检查时间 {min_check_minutes} 分钟，跳过检查")
            return ExitSignal(triggered=False, exit_type=ExitTriggerType.CUSTOM, 
                             close_percentage=0, price=current_price)
        
        # 获取K线数据
        try:
            # 获取K线数据
            candles = await self._get_candle_data(symbol)
            
            if not candles or len(candles) < self.candle_count:
                self.logger.warning(f"{symbol} {direction}仓位K线数据不足 {self.candle_count} 根，跳过时间止损检查")
                return ExitSignal(triggered=False, exit_type=ExitTriggerType.CUSTOM, 
                                 close_percentage=0, price=current_price)
            
            # 检查K线是否有收益
            # 注意：K线按时间倒序排列，最新的在前面
            no_profit = True
            pnl_percentages = []
            
            for i in range(self.candle_count):
                if i >= len(candles):
                    break
                    
                close_price = float(candles[i][4])  # 收盘价在第4个位置
                
                # 计算每根K线的盈亏百分比
                pnl_pct = 0
                if direction == "long":
                    pnl_pct = (close_price - entry_price) / entry_price * 100
                    # 多头：如果收盘价高于开仓价，说明有收益
                    if close_price > entry_price:
                        no_profit = False
                        break
                else:  # short
                    pnl_pct = (entry_price - close_price) / entry_price * 100
                    # 空头：如果收盘价低于开仓价，说明有收益
                    if close_price < entry_price:
                        no_profit = False
                        break
                
                pnl_percentages.append(pnl_pct)
            
            # 如果连续多根K线都没有收益，触发平仓
            if no_profit:
                # 获取K线收盘价列表用于日志输出
                close_prices = [float(candles[i][4]) for i in range(min(self.candle_count, len(candles)))]
                
                self.logger.info(f"{symbol} {direction}仓位连续 {self.candle_count} 根 {self.candle_timeframe} K线没有收益，触发时间止损")
                self.logger.info(f"{symbol} {direction}仓位开仓价: {entry_price}, K线收盘价: {close_prices}")
                self.logger.info(f"{symbol} {direction}仓位各K线盈亏百分比: {pnl_percentages}%")
                
                return ExitSignal(
                    triggered=True,
                    exit_type=ExitTriggerType.TIME_BASED,
                    close_percentage=1.0,
                    price=current_price,
                    message=f"{direction}仓位连续 {self.candle_count} 根 {self.candle_timeframe} K线没有收益，触发时间止损"
                )
            
            # 未触发条件
            return ExitSignal(
                triggered=False,
                exit_type=ExitTriggerType.CUSTOM,
                close_percentage=0,
                price=current_price
            )
            
        except Exception as e:
            self.logger.error(f"检查时间止损异常: {e}", exc_info=True)
            return ExitSignal(
                triggered=False,
                exit_type=ExitTriggerType.CUSTOM,
                close_percentage=0,
                price=current_price
            )
    
    def _get_minutes_from_timeframe(self, timeframe: str) -> int:
        """
        从timeframe字符串获取对应的分钟数
        
        Args:
            timeframe: K线周期，如"1m", "5m", "15m", "1h", "4h", "1d"
            
        Returns:
            int: 分钟数
        """
        try:
            if timeframe.endswith('m'):
                return int(timeframe[:-1])
            elif timeframe.endswith('h'):
                return int(timeframe[:-1]) * 60
            elif timeframe.endswith('d'):
                return int(timeframe[:-1]) * 60 * 24
            else:
                # 如果是纯数字，认为是分钟
                return int(timeframe)
        except (ValueError, TypeError):
            self.logger.warning(f"无法解析timeframe: {timeframe}，使用默认值15")
            return 15
    
    async def _get_candle_data(self, symbol: str) -> List:
        """
        获取K线数据
        
        Args:
            symbol: 交易对
            
        Returns:
            List: K线数据
        """
        try:
            # 确保整数
            candle_count = int(self.candle_count)
            
            # 使用trader获取K线数据
            if self.trader:
                candles = self.trader.get_kline_data(
                    inst_id=symbol,
                    bar=self.bar_type,
                    limit=candle_count
                )
                
                # 根据返回数据类型处理
                if isinstance(candles, dict) and 'data' in candles:
                    return candles['data']
                elif isinstance(candles, list):
                    return candles
                else:
                    self.logger.warning(f"获取 {symbol} 的K线数据格式不识别: {candles}")
                    return []
            
            # 如果data_cache支持获取K线数据，也可以使用它
            elif self.data_cache and hasattr(self.data_cache, 'get_candle_data'):
                return await self.data_cache.get_candle_data(
                    symbol=symbol,
                    bar_type=self.bar_type,
                    count=candle_count
                )
            
            # 没有可用方法获取K线数据
            else:
                self.logger.error(f"无法获取K线数据: 没有可用的trader或data_cache")
                return []
                
        except Exception as e:
            self.logger.error(f"获取 {symbol} 的K线数据异常: {e}", exc_info=True)
            return []
    
    def to_dict(self) -> Dict[str, Any]:
        """将策略转换为字典，用于序列化"""
        data = super().to_dict()
        data.update({
            "candle_timeframe": self.candle_timeframe,
            "candle_count": self.candle_count,
            "enable_time_stop": self.enable_time_stop
        })
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any], app_name: str, position_mgr=None, 
                 strategy_config=None, data_cache=None, trader=None) -> 'TimeBasedExitStrategy':
        """从字典创建策略对象"""
        return cls(
            app_name=app_name,
            candle_timeframe=data.get("candle_timeframe", "15m"),
            candle_count=data.get("candle_count", 3),
            priority=data.get("priority", 50),
            name=data.get("name", "K线时间止损"),
            position_mgr=position_mgr,
            strategy_config=strategy_config,
            data_cache=data_cache,
            trader=trader
        )

class ATRBasedExitStrategy(ExitStrategy):
    """基于ATR的动态止损策略"""
    
    def __init__(self, app_name: str, atr_period: int = 14, atr_timeframe: str = "15m", 
                 atr_multiplier: float = 2.5, min_stop_loss_pct: float = 0.02,
                 priority: int = 5, name: str = "ATR动态止损", position_mgr=None, 
                 strategy_config=None, data_cache=None, trader=None):
        """
        初始化基于ATR的动态止损策略
        
        Args:
            app_name: 应用名称，用于日志记录
            atr_period: 计算ATR的周期，默认14
            atr_timeframe: 计算ATR的时间周期，默认15m
            atr_multiplier: ATR乘数，确定止损距离为ATR的多少倍，默认2.5倍
            min_stop_loss_pct: 最小止损百分比(已废弃，保留参数仅为向后兼容)
            priority: 优先级，数值越小优先级越高
            name: 策略名称
            position_mgr: 仓位管理器
            strategy_config: 策略配置
            data_cache: 数据缓存对象
            trader: 交易执行器
        """
        super().__init__(app_name, name, priority, position_mgr, strategy_config, data_cache, trader)
        
        # 从策略配置中读取ATR参数
        if strategy_config and 'strategy' in strategy_config:
            atr_config = strategy_config['strategy']
            self.atr_period = atr_config.get('period', atr_period)
            self.atr_timeframe = atr_config.get('timeframe', atr_timeframe)
            self.atr_multiplier = atr_config.get('multiplier', atr_multiplier)
            # 保留参数但不使用
            self.min_stop_loss_pct = atr_config.get('min_stop_loss_pct', min_stop_loss_pct)
        else:
            self.atr_period = atr_period
            self.atr_timeframe = atr_timeframe
            self.atr_multiplier = atr_multiplier
            # 保留参数但不使用
            self.min_stop_loss_pct = min_stop_loss_pct

        # 最高价和最低价，初始为0和无穷大
        # 修改为使用(symbol, position_id)作为key的字典，以支持同一交易对多个仓位
        self.highest_price = {}  # (symbol, position_id) -> highest_price 映射
        self.lowest_price = {}   # (symbol, position_id) -> lowest_price 映射
            
        # 缓存每个交易对的ATR值和最近更新时间
        self.atr_values = {}  # symbol -> {"value": atr_value, "time": last_update_time}
        self.atr_cache_duration = 300  # 缓存有效期(秒)，默认5分钟更新一次
        
        self.logger.info(f"ATR动态止损参数: 周期={self.atr_period}, 时间框架={self.atr_timeframe}, " +
                        f"乘数={self.atr_multiplier}")
    
    def _get_position_key(self, position):
        """获取仓位的唯一键"""
        position_id = getattr(position, 'id', None) or getattr(position, 'position_id', str(id(position)))
        return (position.symbol, position_id)
    
    def init_position_resources(self, position: Any):
        """
        初始化持仓相关的资源，主要是添加持仓到ATR计算资源池
        """
        # 先检查持仓是否已关闭
        if hasattr(position, 'closed') and position.closed:
            self.logger.warning(f"跳过初始化已关闭的持仓ATR资源: {position.symbol} (ID: {position.position_id})")
            return
            
        symbol = position.symbol
        entry_price = position.entry_price
        
        # 获取仓位的唯一键
        key = self._get_position_key(position)
        
        # 使用position的high_price和low_price，如果有的话
        if hasattr(position, 'high_price') and position.high_price:
            self.highest_price[key] = position.high_price
        else:
            self.highest_price[key] = entry_price
            
        if hasattr(position, 'low_price') and position.low_price and position.low_price != float('inf'):
            self.lowest_price[key] = position.low_price
        else:
            self.lowest_price[key] = entry_price
            
        self.logger.info(f"初始化ATR止损仓位资源: {symbol} (ID: {position.position_id}), 入场价: {entry_price}")
    
    def clean_symbol_resources(self, symbol: str, position_id: str = None):
        """清理与指定交易对相关的资源"""
        # 如果指定了仓位ID，只清理该仓位的资源
        if position_id:
            key = (symbol, position_id)
            if key in self.highest_price:
                del self.highest_price[key]
            if key in self.lowest_price:
                del self.lowest_price[key]
            self.logger.info(f"清理ATR止损资源: {symbol} (ID: {position_id})")
        else:
            # 否则清理该交易对的所有资源
            for key in list(self.highest_price.keys()):
                if key[0] == symbol:
                    del self.highest_price[key]
            
            for key in list(self.lowest_price.keys()):
                if key[0] == symbol:
                    del self.lowest_price[key]
            
            self.logger.info(f"清理ATR止损资源: {symbol} (所有仓位)")
            
            # ATR值缓存也可以清理，这是按symbol缓存的
            if symbol in self.atr_values:
                del self.atr_values[symbol]
                self.logger.info(f"清理ATR缓存: {symbol}")
    
    async def get_atr_value(self, symbol: str) -> float:
        """
        获取指定交易对的ATR值，如果缓存中有且未过期则使用缓存
        
        Args:
            symbol: 交易对
            
        Returns:
            float: ATR值，如果无法计算返回None
        """
        current_time = time.time()
        
        # 检查缓存
        if symbol in self.atr_values:
            cache_data = self.atr_values[symbol]
            # 如果缓存未过期，直接返回
            if current_time - cache_data["time"] < self.atr_cache_duration:
                self.logger.debug(f"{symbol} ATR缓存命中: {cache_data['value']:.6f}, 缓存时间: {int(current_time - cache_data['time'])}秒前")
                return cache_data["value"]
            else:
                self.logger.debug(f"{symbol} ATR缓存过期, 缓存时间: {int(current_time - cache_data['time'])}秒前")
        
        # 计算新的ATR值
        self.logger.info(f"{symbol} 计算新的ATR值...")
        atr_value = await self.calculate_atr(symbol)
        
        if atr_value is not None:
            # 更新缓存
            self.atr_values[symbol] = {"value": atr_value, "time": current_time}
            self.logger.info(f"{symbol} ATR计算结果: {atr_value:.6f}, 周期:{self.atr_period}, 时间框架:{self.atr_timeframe}")
        else:
            self.logger.warning(f"{symbol} 无法计算ATR值")
            
        return atr_value
    
    async def calculate_atr(self, symbol: str) -> Optional[float]:
        """
        计算指定交易对的ATR值
        
        Args:
            symbol: 交易对
            
        Returns:
            float: ATR值，如果无法计算返回None
        """
        try:
            # 获取K线数据，增加获取数量以稳定ATR计算
            # 我们需要至少 atr_period 根K线来计算初始SMA，以及更多数据来平滑
            num_candles_to_fetch = self.atr_period + 10 
            self.logger.debug(f"{symbol} 获取K线数据用于ATR: 周期={self.atr_timeframe}, 数量={num_candles_to_fetch}")
            candles = await self._get_candle_data(symbol, self.atr_timeframe, num_candles_to_fetch)
            
            # 确保有足够的K线数据
            if not candles or len(candles) < self.atr_period:
                self.logger.warning(f"{symbol} K线数据不足 {self.atr_period} 根，无法计算ATR (获取到 {len(candles) if candles else 0} 根)")
                return None
            
            self.logger.debug(f"{symbol} 成功获取 {len(candles)} 根K线数据用于ATR计算")

            # 将K线数据转换为DataFrame
            df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'volccy', 'volccyquote', 'confirm'])
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            df['close'] = df['close'].astype(float)

            # 计算真实波动幅度（TR）
            df['previous_close'] = df['close'].shift(1)
            # 对于第一行，TR = high - low
            df['tr1'] = df['high'] - df['low'] 
            df['tr2'] = abs(df['high'] - df['previous_close'])
            df['tr3'] = abs(df['low'] - df['previous_close'])
            
            # TR是三者中的最大值。对于第一根K线，由于previous_close是NaN，tr2和tr3会是NaN。
            # .max(axis=1) 在这种情况下会正确处理，如果某列是NaN，它不会参与比较，除非所有列都是NaN。
            # 我们需要确保第一行的TR是 high - low。
            df['true_range'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
            df['true_range'].iloc[0] = df['high'].iloc[0] - df['low'].iloc[0] # 显式设置第一个TR

            # 初始化ATR序列 (Series)
            atr_series = pd.Series(index=df.index, dtype=float, name='atr')

            # 计算第一个ATR值：前atr_period个TR的简单移动平均(SMA)
            # 这个值对应于第 atr_period-1 索引处（即第 atr_period 根K线）的ATR
            if len(df['true_range']) >= self.atr_period:
                initial_atr = df['true_range'].iloc[:self.atr_period].mean()
                atr_series.iloc[self.atr_period - 1] = initial_atr
            else:
                # 如果数据不足以计算初始SMA，则无法继续 (理论上已被上面的长度检查覆盖)
                self.logger.warning(f"{symbol} 数据不足以计算 {self.atr_period} 周期的初始ATR SMA")
                return None

            # 递归计算后续的ATR值 (Wilder's Smoothing)
            # ATR_current = (ATR_previous * (N-1) + TR_current) / N
            # N = self.atr_period
            for i in range(self.atr_period, len(df)):
                atr_series.iloc[i] = (atr_series.iloc[i-1] * (self.atr_period - 1) + df['true_range'].iloc[i]) / self.atr_period
            
            df['atr'] = atr_series # 将计算得到的ATR序列添加到DataFrame
            atr_value = df['atr'].iloc[-1] # 取最新的ATR值

            if pd.isna(atr_value):
                self.logger.warning(f"{symbol} 计算得到的ATR值为NaN，可能由于数据不足或计算问题。检查TR值和ATR计算过程。DataFrame尾部：\n{df.tail()}")
                return None

            self.logger.info(f"{symbol} ATR (SMA初始化+Wilder平滑) 计算完成: {atr_value:.6f}")
            return atr_value
            
        except Exception as e:
            self.logger.error(f"{symbol} 计算ATR异常 (SMA初始化+Wilder平滑): {e}", exc_info=True)
            return None
    
    async def _get_candle_data(self, symbol: str, timeframe: str, count: int) -> List:
        """
        获取K线数据
        
        Args:
            symbol: 交易对
            timeframe: K线周期
            count: 需要的K线数量
            
        Returns:
            List: K线数据
        """
        try:
            # 尝试从trader获取K线数据
            if self.trader:
                candles = self.trader.get_kline_data(
                    inst_id=symbol,
                    bar=timeframe,
                    limit=count
                )
                
                # 根据返回数据类型处理
                if isinstance(candles, dict) and 'data' in candles:
                    return candles['data']
                elif isinstance(candles, list):
                    return candles
                else:
                    self.logger.warning(f"获取 {symbol} 的K线数据格式不识别: {candles}")
                    return []
            
            # 如果data_cache支持获取K线数据，也可以使用它
            elif self.data_cache and hasattr(self.data_cache, 'get_candle_data'):
                return await self.data_cache.get_candle_data(
                    symbol=symbol,
                    bar_type=timeframe,
                    count=count
                )
            
            # 没有可用方法获取K线数据
            else:
                self.logger.error(f"无法获取K线数据: 没有可用的trader或data_cache")
                return []
                
        except Exception as e:
            self.logger.error(f"获取 {symbol} 的K线数据异常: {e}", exc_info=True)
            return []
    
    async def check_exit_condition(self, position: Any, current_price: float, **kwargs) -> ExitSignal:
        """
        检查是否满足ATR止损条件
        
        Args:
            position: 仓位对象
            current_price: 当前价格
            kwargs: 额外参数
            
        Returns:
            ExitSignal: 平仓信号
        """
        if not self.enabled:
            return ExitSignal(triggered=False, exit_type=ExitTriggerType.CUSTOM, 
                             close_percentage=0, price=current_price)
        
        # 获取仓位信息
        symbol = position.symbol
        direction = position.direction
        entry_price = position.entry_price
        leverage = getattr(position, 'leverage', 1)
        position_time = getattr(position, 'timestamp', 0)
        
        # 获取仓位的唯一键
        key = self._get_position_key(position)
        
        # 获取信号中的定制参数
        signal = getattr(position, 'signal', None)
        custom_multiplier = None
        
        if signal and hasattr(signal, 'extra_data') and signal.extra_data:
            custom_multiplier = signal.extra_data.get('atr_multiplier')
        
        # 使用信号中的参数，如果有的话
        atr_multiplier = custom_multiplier if custom_multiplier is not None else self.atr_multiplier
        
        # 记录使用的参数
        self.logger.info(f"{symbol} ATR止损验证 - {direction}仓位信息: 入场价={entry_price:.6f}, 当前价={current_price:.6f}, "
                         f"杠杆={leverage}, 开仓时间={position_time}, ATR乘数={atr_multiplier}")
        
        # 获取ATR值
        self.logger.info(f"{symbol} ATR止损验证 - 开始获取ATR值")
        atr_value = await self.get_atr_value(symbol)
        if atr_value is None:
            self.logger.warning(f"{symbol} 无法获取ATR值，跳过ATR止损检查")
            return ExitSignal(triggered=False, exit_type=ExitTriggerType.CUSTOM, 
                             close_percentage=0, price=current_price)
        
        # 计算基于ATR的止损距离（以价格单位表示，不再除以入场价格）
        atr_stop_price_distance = atr_value * atr_multiplier
        self.logger.info(f"{symbol} ATR止损验证 - 计算结果: ATR={atr_value:.6f}, 乘数={atr_multiplier}")
        self.logger.info(f"{symbol} ATR止损验证 - 止损价格距离: {atr_value:.6f} * {atr_multiplier} = {atr_stop_price_distance:.6f}")

        # 初始化最高/最低价
        if key not in self.highest_price or key not in self.lowest_price:
            self.init_position_resources(position)
        
        # 计算当前盈亏百分比
        if direction == "long":
            pnl_pct = (current_price - entry_price) / entry_price * 100
        else:  # short
            pnl_pct = (entry_price - current_price) / entry_price * 100
        
        # 计算止损价格
        if direction == "long":
            # 更新最高价
            if current_price > self.highest_price[key]:
                self.highest_price[key] = current_price
            stop_price = self.highest_price[key] - atr_stop_price_distance
            stop_distance_percent = (current_price - stop_price) / current_price * 100
            self.logger.info(f"{symbol} (ID: {key[1]}) ATR止损验证 - 多头止损价格: {self.highest_price[key]:.6f} - {atr_stop_price_distance:.6f} = {stop_price:.6f} (距离: {stop_distance_percent:.4f}%)")
            
            # 检查是否触发止损
            if current_price <= stop_price:
                self.logger.info(f"{symbol} (ID: {key[1]}) 触发多头ATR止损: 入场价={entry_price:.6f}, " +
                               f"当前价={current_price:.6f}, 止损线={stop_price:.6f}, " +
                               f"ATR={atr_value:.6f}, 止损距离={atr_stop_price_distance:.6f}, 盈亏={pnl_pct:.2f}%")
                           
                return ExitSignal(
                    triggered=True,
                    exit_type=ExitTriggerType.ATR_BASED,
                    close_percentage=1.0,
                    price=current_price,
                    message=f"触发多头ATR止损: ATR={atr_value:.6f}, 止损线={stop_price:.6f}, 盈亏={pnl_pct:.2f}%"
                )
            else:
                self.logger.info(f"{symbol} (ID: {key[1]}) 未触发多头ATR止损: 当前价 {current_price:.6f} > 止损价 {stop_price:.6f}, 差距: {(current_price - stop_price):.6f}, 盈亏: {pnl_pct:.2f}%")
        else:  # short
            # 更新最低价
            if current_price < self.lowest_price[key]:
                self.lowest_price[key] = current_price
            stop_price = self.lowest_price[key] + atr_stop_price_distance
            stop_distance_percent = (stop_price - current_price) / current_price * 100
            self.logger.info(f"{symbol} (ID: {key[1]}) ATR止损验证 - 空头止损价格: {self.lowest_price[key]:.6f} + {atr_stop_price_distance:.6f} = {stop_price:.6f} (距离: {stop_distance_percent:.4f}%)")
            
            # 检查是否触发止损
            if current_price >= stop_price:
                self.logger.info(f"{symbol} (ID: {key[1]}) 触发空头ATR止损: 入场价={entry_price:.6f}, " +
                               f"当前价={current_price:.6f}, 止损线={stop_price:.6f}, " +
                               f"ATR={atr_value:.6f}, 止损距离={atr_stop_price_distance:.6f}, 盈亏={pnl_pct:.2f}%")
                           
                return ExitSignal(
                    triggered=True,
                    exit_type=ExitTriggerType.ATR_BASED,
                    close_percentage=1.0,
                    price=current_price,
                    message=f"触发空头ATR止损: ATR={atr_value:.6f}, 止损线={stop_price:.6f}, 盈亏={pnl_pct:.2f}%"
                )
            else:
                self.logger.info(f"{symbol} (ID: {key[1]}) 未触发空头ATR止损: 当前价 {current_price:.6f} < 止损价 {stop_price:.6f}, 差距: {(stop_price - current_price):.6f}, 盈亏: {pnl_pct:.2f}%")
        
        # 未触发条件
        return ExitSignal(
            triggered=False,
            exit_type=ExitTriggerType.CUSTOM,
            close_percentage=0,
            price=current_price
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """将策略转换为字典，用于序列化"""
        data = super().to_dict()
        data.update({
            "atr_period": self.atr_period,
            "atr_timeframe": self.atr_timeframe,
            "atr_multiplier": self.atr_multiplier,
            "min_stop_loss_pct": self.min_stop_loss_pct
        })
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any], app_name: str, position_mgr=None, 
                 strategy_config=None, data_cache=None, trader=None) -> 'ATRBasedExitStrategy':
        """从字典创建策略对象"""
        return cls(
            app_name=app_name,
            atr_period=data.get("atr_period", 14),
            atr_timeframe=data.get("atr_timeframe", "15m"),
            atr_multiplier=data.get("atr_multiplier", 2.5),
            min_stop_loss_pct=data.get("min_stop_loss_pct", 0.02),
            priority=data.get("priority", 5),
            name=data.get("name", "ATR动态止损"),
            position_mgr=position_mgr,
            strategy_config=strategy_config,
            data_cache=data_cache,
            trader=trader
        )

class OrderedTakeProfitStopLossStrategy(ExitStrategy):
    """委托单止盈止损策略：开始监控时就直接委托止盈限价单，同时监控止损条件，满足止损条件时撤销止盈单并委托市价止损单"""
    
    def __init__(self, app_name: str, take_profit_pct: float = 0.05, stop_loss_pct: float = 0.03, 
                 priority: int = 15, name: str = "委托单止盈止损", position_mgr=None, 
                 strategy_config=None, data_cache=None, trader=None):
        """
        初始化委托单止盈止损策略
        
        Args:
            app_name: 应用名称，用于日志记录
            take_profit_pct: 止盈百分比，默认0.05(5%)
            stop_loss_pct: 止损百分比，默认0.03(3%)
            priority: 优先级，数值越小优先级越高
            name: 策略名称
            position_mgr: 仓位管理器
            strategy_config: 策略配置
            data_cache: 数据缓存对象
            trader: 交易执行器
        """
        super().__init__(app_name, name, priority, position_mgr, strategy_config, data_cache, trader)
        
        # 从策略配置中读取止盈止损参数，如果没有则使用默认值
        if strategy_config and 'strategy' in strategy_config:
            strategy_settings = strategy_config['strategy']
            self.take_profit_pct = strategy_settings.get('take_profit_pct', take_profit_pct)
            self.stop_loss_pct = strategy_settings.get('stop_loss_pct', stop_loss_pct)
            self.check_order_interval = strategy_settings.get('check_order_interval', 60)  # 默认60秒检查一次订单状态
        else:
            self.take_profit_pct = take_profit_pct
            self.stop_loss_pct = stop_loss_pct
            self.check_order_interval = 60
        
        # 保存已提交的止盈止损订单
        # key: (symbol, position_id), value: {"tp_order_id": "xxx", "status": "submitted", "last_check_time": timestamp}
        self.submitted_orders = {}
        
        self.logger.info(f"委托单止盈止损策略参数: 止盈={self.take_profit_pct*100:.2f}%, 止损={self.stop_loss_pct*100:.2f}%, 订单检查间隔={self.check_order_interval}秒")
    
    def _get_position_key(self, position):
        """获取仓位的唯一键"""
        position_id = getattr(position, 'id', None) or getattr(position, 'position_id', str(id(position)))
        return (position.symbol, position_id)
    
    async def _check_order_status(self, symbol: str, order_id: str) -> str:
        """
        检查订单状态
        
        Args:
            symbol: 交易对
            order_id: 订单ID
            
        Returns:
            str: 订单状态, 可能的值: 'open', 'filled', 'canceled', 'unknown'
        """
        if not self.trader:
            self.logger.warning(f"未提供交易执行器，无法检查订单 {order_id} 状态")
            return "unknown"
        
        try:
            # 调用trader的get_order_details方法查询订单状态
            result = self.trader.get_order_details(symbol, order_id)
            
            if not result or 'code' not in result or result['code'] != '0':
                error_msg = result.get('msg', '未知错误') if result else '请求失败'
                self.logger.warning(f"获取订单状态失败: {symbol} 订单ID={order_id}, 错误: {error_msg}")
                return "unknown"
                
            # 解析订单数据
            if 'data' in result and len(result['data']) > 0:
                order_data = result['data'][0]
                status = order_data.get('state', '').lower()
                
                # OKEx订单状态映射
                # canceled: 撤单成功
                # live: 等待成交
                # partially_filled: 部分成交
                # filled: 完全成交
                if status in ['filled', 'complete', 'completed']:
                    self.logger.info(f"订单已成交: {symbol} 订单ID={order_id}")
                    return "filled"
                elif status in ['partially_filled']:
                    self.logger.info(f"订单部分成交: {symbol} 订单ID={order_id}")
                    return "partially_filled"
                elif status in ['live', 'open', 'active', 'new']:
                    self.logger.debug(f"订单仍在挂单中: {symbol} 订单ID={order_id}")
                    return "open"
                elif status in ['canceled', 'cancelled', 'rejected']:
                    self.logger.info(f"订单已取消: {symbol} 订单ID={order_id}")
                    return "canceled"
                else:
                    self.logger.warning(f"未知订单状态: {symbol} 订单ID={order_id}, 状态={status}")
                    return "unknown"
            else:
                self.logger.warning(f"未找到订单数据: {symbol} 订单ID={order_id}")
                return "unknown"
            
        except Exception as e:
            self.logger.error(f"检查订单状态异常: {e}", exc_info=True)
            return "unknown"
    
    def init_position_resources(self, position: Any):
        """
        初始化仓位相关资源 - 提交止盈限价单
        
        Args:
            position: 仓位对象
        """
        # 先检查持仓是否已关闭
        if hasattr(position, 'closed') and position.closed:
            self.logger.warning(f"跳过初始化已关闭的持仓委托单资源: {position.symbol} (ID: {position.position_id})")
            return
            
        # 获取仓位信息
        symbol = position.symbol
        direction = position.direction
        entry_price = position.entry_price
        quantity = position.quantity  # 使用quantity而不是size
        key = self._get_position_key(position)
        
        # 如果已有该仓位的订单记录，跳过
        if key in self.submitted_orders:
            self.logger.info(f"仓位 {symbol} (ID: {key[1]}) 已有委托单记录，跳过初始化")
            return
        
        # 获取止盈止损设置 - 可能来自仓位或信号
        signal = getattr(position, 'signal', None)
        take_profit_pct = signal.take_profit_pct if signal and hasattr(signal, 'take_profit_pct') and signal.take_profit_pct is not None else self.take_profit_pct
        leverage = getattr(position, 'leverage', 1)
        
        # 如果有杠杆，需要调整止盈止损比例
        if leverage > 1:
            take_profit_pct = take_profit_pct / leverage

        # 如果没有交易执行器，无法提交订单
        if not self.trader:
            self.logger.warning(f"未提供交易执行器，无法为 {symbol} 提交止盈限价单")
            return
        
        # 计算止盈价格
        tp_price = 0
        
        # 计算止盈价格和操作方向
        if direction == "long":
            tp_price = entry_price * (1 + take_profit_pct)
            tp_side = "sell"  # 多仓止盈卖出
            pos_side = "long"  # 持仓方向为多
        else:  # short
            tp_price = entry_price * (1 - take_profit_pct)
            tp_side = "buy"   # 空仓止盈买入
            pos_side = "short"  # 持仓方向为空

        # 确定是否为现货或合约
        is_spot = not ("-SWAP" in symbol or "-FUTURES" in symbol or "-PERPETUAL" in symbol)
        # 将价格精度调整为合适的小数位数
        # 从交易所获取价格精度，而不是硬编码为4
        if self.trader:
            precision = get_price_precision(self.trader, symbol, is_spot)
            tp_price = round(tp_price, precision)
            self.logger.debug(f"使用交易所价格精度 {precision} 位小数，调整止盈价格为: {tp_price}")
        else:
            tp_price = round(tp_price, 4)  # 如果无法获取精度，使用默认值
            self.logger.warning(f"无法获取交易所价格精度，使用默认值(4)，止盈价格: {tp_price}")
        
        # 提交止盈限价单
        try:
            if is_spot:
                # 现货限价单
                params = {
                    "instId": symbol,
                    "tdMode": "cash",
                    "side": tp_side,
                    "ordType": "limit",
                    "px": str(tp_price),
                    "sz": str(abs(quantity)),  # 使用绝对值确保数量总是正数
                    "reduceOnly": "true"  # 确保是平仓单
                }
                self.logger.debug(f"提交现货限价止盈单参数: {params}")
                result = self.trader._request("POST", "/api/v5/trade/order", params)
            else:
                # 合约限价单
                params = {
                    "instId": symbol,
                    "tdMode": "cross",
                    "side": tp_side,
                    "posSide": pos_side,
                    "ordType": "limit",
                    "px": str(tp_price),
                    "sz": str(abs(quantity)),  # 使用绝对值确保数量总是正数
                    "reduceOnly": "true"  # 确保是平仓单
                }
                self.logger.debug(f"提交合约限价止盈单参数: {params}")
                result = self.trader._request("POST", "/api/v5/trade/order", params)
            
            # 检查下单结果
            if result.get('code') == '0' and 'data' in result and len(result['data']) > 0:
                order_id = result['data'][0].get('ordId')
                if order_id:
                    now = time.time()
                    self.submitted_orders[key] = {
                        "tp_order_id": order_id,
                        "status": "submitted",
                        "tp_price": tp_price,
                        "direction": direction,
                        "last_check_time": now  # 记录最后检查时间
                    }
                    self.logger.info(f"为 {symbol} {direction}仓位提交止盈限价单成功: 价格={tp_price:.6f}, 订单ID={order_id}")
                else:
                    self.logger.error(f"为 {symbol} {direction}仓位提交止盈限价单失败: 无法获取订单ID, 响应={result}")
            else:
                error_msg = result.get('msg', '未知错误')
                self.logger.error(f"为 {symbol} {direction}仓位提交止盈限价单失败: {error_msg}, 响应={result}")
        except Exception as e:
            self.logger.error(f"提交止盈限价单异常: {e}", exc_info=True)
    
    def clean_symbol_resources(self, symbol: str, position_id: str = None):
        """
        清理与指定交易对相关的资源，撤销未成交的订单
        
        Args:
            symbol: 交易对
            position_id: 仓位ID，如果提供则只清理该仓位的资源
        """
        # 如果指定了仓位ID，只清理该仓位的资源
        if position_id:
            key = (symbol, position_id)
            order_info = self.submitted_orders.get(key)
            if order_info and order_info["status"] == "submitted":
                self._cancel_order(symbol, order_info["tp_order_id"])
            
            if key in self.submitted_orders:
                del self.submitted_orders[key]
            
            self.logger.info(f"清理委托单资源: {symbol} (ID: {position_id})")
        else:
            # 否则清理该交易对的所有资源
            keys_to_remove = []
            for key, order_info in self.submitted_orders.items():
                if key[0] == symbol:
                    if order_info["status"] == "submitted":
                        self._cancel_order(symbol, order_info["tp_order_id"])
                    keys_to_remove.append(key)
            
            for key in keys_to_remove:
                del self.submitted_orders[key]
            
            self.logger.info(f"清理委托单资源: {symbol} (所有仓位)")
    
    def _cancel_order(self, symbol: str, order_id: str) -> bool:
        """
        撤销订单
        
        Args:
            symbol: 交易对
            order_id: 订单ID
            
        Returns:
            bool: 是否成功撤销
        """
        if not self.trader:
            self.logger.warning(f"未提供交易执行器，无法撤销订单 {order_id}")
            return False
        
        try:
            # 使用OKEx的cancel_order方法撤销订单
            result = self.trader.cancel_order(symbol, order_id)
            
            # 检查撤单结果
            success = False
            if result and isinstance(result, dict):
                if result.get('code') == '0':
                    success = True
                    self.logger.info(f"撤销 {symbol} 订单成功: {order_id}")
                else:
                    error_msg = result.get('msg', '未知错误')
                    # 如果错误是因为订单不存在或已完成，也视为成功
                    if "order does not exist" in error_msg.lower() or "order already fully filled" in error_msg.lower():
                        self.logger.info(f"订单 {order_id} 已不存在或已完成，视为撤单成功")
                        success = True
                    else:
                        self.logger.warning(f"撤销 {symbol} 订单失败: {error_msg}")
            else:
                self.logger.warning(f"撤销 {symbol} 订单失败: 返回结果异常 {result}")
            
            return success
            
        except Exception as e:
            self.logger.error(f"撤销订单异常: {e}", exc_info=True)
            return False
    
    async def check_exit_condition(self, position: Any, current_price: float, **kwargs) -> ExitSignal:
        """
        检查是否满足止损条件（止盈由委托单负责）
        同时检查委托单的状态，如果止盈已经成交，将返回触发信号
        
        Args:
            position: 仓位对象
            current_price: 当前价格
            kwargs: 额外参数
            
        Returns:
            ExitSignal: 平仓信号
        """
        if not self.enabled:
            return ExitSignal(triggered=False, exit_type=ExitTriggerType.CUSTOM, 
                             close_percentage=0, price=current_price)
        
        # 获取仓位信息
        symbol = position.symbol
        direction = position.direction
        entry_price = position.entry_price
        pos_id = position.position_id
        key = self._get_position_key(position)
        
        # 获取价格精度
        precision = 4  # 默认精度
        if self.trader:
            # 确定是否为现货或合约
            is_spot = not ("-SWAP" in symbol or "-FUTURES" in symbol or "-PERPETUAL" in symbol)
            try:
                precision = get_price_precision(self.trader, symbol, is_spot)
            except Exception as e:
                self.logger.warning(f"获取价格精度失败，使用默认值: {e}")
        
        # 1. 检查我们是否有此仓位的止盈委托单
        if key not in self.submitted_orders:
            # 如果没有记录委托单，尝试初始化该仓位
            self.logger.info(f"没有找到 {symbol} (ID: {pos_id}) 的委托单记录，尝试初始化")
            self.init_position_resources(position)
            
            # 再次检查是否成功创建了委托单
            if key not in self.submitted_orders:
                self.logger.warning(f"初始化 {symbol} (ID: {pos_id}) 的委托单失败，跳过检查")
                return ExitSignal(triggered=False, exit_type=ExitTriggerType.CUSTOM, 
                                 close_percentage=0, price=current_price)
        
        order_data = self.submitted_orders[key]
        tp_order_id = order_data.get('tp_order_id')
        
        # 2. 检查止盈委托单状态
        if tp_order_id:
            order_status = await self._check_order_status(symbol, tp_order_id)
            
            # 如果订单已完成
            if order_status in ['canceled', 'unknown', 'filled']:
                self.logger.info(f"{symbol} (ID: {pos_id}) 止盈委托单已成交: {tp_order_id}")
                
                # 返回止盈触发信号
                tp_price = order_data.get('tp_price', current_price)
                return ExitSignal(
                    triggered=True,
                    exit_type=ExitTriggerType.TAKE_PROFIT,
                    close_percentage=1.0,
                    price=tp_price,
                    message=f"止盈委托单已成交: {tp_order_id}, 价格: {tp_price}",
                    need_cleanup=True
                )
        # 3. 检查是否触发止损条件（如果止盈未触发）
        # 获取杠杆倍数
        leverage = getattr(position, 'leverage', 1)
        
        # 获取止盈止损设置 - 可能来自仓位或信号
        signal = getattr(position, 'signal', None)
        take_profit_pct = signal.take_profit_pct if signal and hasattr(signal, 'take_profit_pct') and signal.take_profit_pct is not None else self.take_profit_pct
        stop_loss_pct = signal.stop_loss_pct if signal and hasattr(signal, 'stop_loss_pct') and signal.stop_loss_pct is not None else self.stop_loss_pct
        
        # 如果有杠杆，需要调整止盈止损比例
        if leverage > 1:
            take_profit_pct = take_profit_pct / leverage
            stop_loss_pct = stop_loss_pct / leverage
        
        # 计算止损价格
        if direction == "long":
            stop_loss_price = entry_price * (1 - stop_loss_pct)
            # 使用动态精度格式化价格
            sl_price_formatted = f"{{:.{precision}f}}".format(stop_loss_price)
            # 检查是否触发止损
            if current_price <= stop_loss_price:
                self.logger.info(f"{symbol} 触发止损: 当前价格 {current_price} <= 止损价格 {sl_price_formatted}")
                return ExitSignal(
                    triggered=True,
                    exit_type=ExitTriggerType.STOP_LOSS,
                    close_percentage=1.0,
                    price=current_price,
                    message=f"价格下跌触发止损: {current_price:.{precision}f} <= {sl_price_formatted}"
                )
        else:  # short
            stop_loss_price = entry_price * (1 + stop_loss_pct)
            # 使用动态精度格式化价格
            sl_price_formatted = f"{{:.{precision}f}}".format(stop_loss_price)
            # 检查是否触发止损
            if current_price >= stop_loss_price:
                self.logger.info(f"{symbol} 触发止损: 当前价格 {current_price} >= 止损价格 {sl_price_formatted}")
                return ExitSignal(
                    triggered=True,
                    exit_type=ExitTriggerType.STOP_LOSS,
                    close_percentage=1.0,
                    price=current_price,
                    message=f"价格上涨触发止损: {current_price:.{precision}f} >= {sl_price_formatted}"
                )
        
        # 没有触发任何条件
        return ExitSignal(
            triggered=False, 
            exit_type=ExitTriggerType.CUSTOM, 
            close_percentage=0, 
            price=current_price
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """将策略转换为字典，用于序列化"""
        data = super().to_dict()
        data.update({
            "take_profit_pct": self.take_profit_pct,
            "stop_loss_pct": self.stop_loss_pct,
            "check_order_interval": self.check_order_interval
        })
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any], app_name: str, position_mgr=None, 
                 strategy_config=None, data_cache=None, trader=None) -> 'OrderedTakeProfitStopLossStrategy':
        """从字典创建策略对象"""
        return cls(
            app_name=app_name,
            take_profit_pct=data.get("take_profit_pct", 0.05),
            stop_loss_pct=data.get("stop_loss_pct", 0.03),
            priority=data.get("priority", 15),
            name=data.get("name", "委托单止盈止损"),
            position_mgr=position_mgr,
            strategy_config=strategy_config,
            data_cache=data_cache,
            trader=trader
        )

class ExitStrategyManager:
    """平仓策略管理器"""
    
    def __init__(self, app_name: str, position_mgr=None, strategy_config=None, 
                 data_cache=None, trader=None):
        """
        初始化平仓策略管理器
        
        Args:
            app_name: 应用名称
            position_mgr: 仓位管理器
            strategy_config: 策略配置
            data_cache: 数据缓存对象
            trader: 交易执行器
        """
        self.app_name = app_name
        self.logger = logging.getLogger(app_name)
        self.position_mgr = position_mgr
        self.strategy_config = strategy_config or {}
        self.data_cache = data_cache
        self.trader = trader
        self.strategies = {}  # {strategy_name: strategy_obj}
        self.logger.info(f"初始化平仓策略管理器")
        # 读取和应用统一的策略配置
        if strategy_config:
            self.strategy_config = strategy_config
            self.load_default_strategies()
        else:
            self.strategy_config = None
            self.logger.warning("没有指定策略配置，未自动加载退出策略")
        
        # 初始化已有仓位的资源 - 明确指定不包含已关闭的持仓
        if self.position_mgr:
            try:
                # 显式指定include_closed=False确保只加载未关闭的持仓
                positions = self.position_mgr.load_positions(dict_format=True, include_closed=False)
                self.logger.info(f"加载未关闭持仓进行退出策略初始化: {len(positions)}个")
                for symbol, position in positions.items():
                    # 二次检查确保持仓未关闭
                    if hasattr(position, 'closed') and position.closed:
                        self.logger.warning(f"跳过初始化已关闭的持仓: {symbol} (ID: {position.position_id})")
                        continue
                        
                    self.logger.info(f"初始化退出策略管理器中 {symbol} 仓位的资源 (ID: {position.position_id}), 入场价: {position.entry_price}")
                    for strategy in self.strategies.values():
                        if hasattr(strategy, 'init_position_resources') and strategy.enabled:
                            strategy.init_position_resources(position)
            except Exception as e:
                self.logger.error(f"初始化已有仓位资源异常: {e}", exc_info=True)
    
    def load_default_strategies(self):
        """加载默认策略"""
        # 获取exit_strategies配置
        exit_strategies_config = {}
        if (self.strategy_config and 'strategy' in self.strategy_config and 
            'exit_strategies' in self.strategy_config['strategy']):
            exit_strategies_config = self.strategy_config['strategy']['exit_strategies']
            self.logger.info(f"读取到的退出策略配置: {exit_strategies_config}")
        
        # 固定百分比止盈止损策略
        fixed_config = None
        if 'fixed_percent_exit' in exit_strategies_config:
            fixed_config = {'strategy': exit_strategies_config['fixed_percent_exit']}
            self.logger.info(f"固定百分比止盈止损策略配置: {fixed_config}")
        
        fixed_strategy = FixedPercentExitStrategy(
            app_name=self.app_name,
            position_mgr=self.position_mgr,
            strategy_config=fixed_config,
            data_cache=self.data_cache,
            trader=self.trader
        )
        self.add_strategy(fixed_strategy)
        
        # ATR动态止损策略
        atr_config = None
        if 'atr_stop_loss' in exit_strategies_config:
            atr_config = {'strategy': exit_strategies_config['atr_stop_loss']}
            self.logger.info(f"ATR动态止损策略配置: {atr_config}")
        
        atr_strategy = ATRBasedExitStrategy(
            app_name=self.app_name,
            position_mgr=self.position_mgr,
            strategy_config=atr_config,
            data_cache=self.data_cache,
            trader=self.trader
        )
        self.add_strategy(atr_strategy)
        
        # 追踪止损策略
        trailing_config = None
        if 'trailing_stop_exit' in exit_strategies_config:
            trailing_config = {'strategy': exit_strategies_config['trailing_stop_exit']}
            self.logger.info(f"追踪止损策略配置: {trailing_config}")
        
        trailing_strategy = TrailingStopExitStrategy(
            app_name=self.app_name,
            position_mgr=self.position_mgr,
            strategy_config=trailing_config,
            data_cache=self.data_cache,
            trader=self.trader
        )
        self.add_strategy(trailing_strategy)
        
        # 阶梯止盈策略
        ladder_config = None
        if 'ladder_exit' in exit_strategies_config:
            ladder_config = {'strategy': {'ladder_take_profit': exit_strategies_config['ladder_exit']}}
            self.logger.info(f"阶梯止盈策略配置: {ladder_config}")
        
        ladder_strategy = LadderExitStrategy(
            app_name=self.app_name,
            position_mgr=self.position_mgr,
            strategy_config=ladder_config,
            data_cache=self.data_cache,
            trader=self.trader
        )
        self.add_strategy(ladder_strategy)
        
        # 时间止损策略
        time_config = None
        if 'time_based_exit' in exit_strategies_config:
            time_config = {'strategy': {'time_stop_loss': exit_strategies_config['time_based_exit']}}
            self.logger.info(f"时间止损策略配置: {time_config}")
        
        time_strategy = TimeBasedExitStrategy(
            app_name=self.app_name,
            position_mgr=self.position_mgr,
            strategy_config=time_config,
            data_cache=self.data_cache,
            trader=self.trader
        )
        self.add_strategy(time_strategy)
        
        # 委托单止盈止损策略
        ordered_tp_sl_config = None
        if 'ordered_tp_sl' in exit_strategies_config:
            ordered_tp_sl_config = {'strategy': exit_strategies_config['ordered_tp_sl']}
            self.logger.info(f"委托单止盈止损策略配置: {ordered_tp_sl_config}")
        
        ordered_tp_sl_strategy = OrderedTakeProfitStopLossStrategy(
            app_name=self.app_name,
            position_mgr=self.position_mgr,
            strategy_config=ordered_tp_sl_config,
            data_cache=self.data_cache,
            trader=self.trader
        )
        self.add_strategy(ordered_tp_sl_strategy)
        
        # 根据配置启用或禁用策略
        if 'default_enabled' in exit_strategies_config:
            enabled_strategies = exit_strategies_config['default_enabled']
            
            # 创建策略类型到启用标志的映射
            strategy_type_map = {
                'fixed_percent': FixedPercentExitStrategy,
                'atr_stop_loss': ATRBasedExitStrategy,
                'trailing_stop': TrailingStopExitStrategy,
                'ladder_exit': LadderExitStrategy,
                'time_based_exit': TimeBasedExitStrategy,
                'ordered_tp_sl': OrderedTakeProfitStopLossStrategy
            }
            
            # 先禁用所有策略
            for strategy_name in self.strategies:
                self.disable_strategy(strategy_name)
            
            # 再根据配置启用特定策略
            for enabled_type in enabled_strategies:
                if enabled_type in strategy_type_map:
                    for name, strategy in self.strategies.items():
                        if isinstance(strategy, strategy_type_map[enabled_type]):
                            self.enable_strategy(name)
                            self.logger.info(f"启用策略类型 {enabled_type}: {name}")
        
        # 直接从配置中读取各个策略的enabled状态
        for name, strategy in self.strategies.items():
            config_key = None
            if isinstance(strategy, FixedPercentExitStrategy):
                config_key = 'fixed_percent_exit'
            elif isinstance(strategy, ATRBasedExitStrategy):
                config_key = 'atr_stop_loss'
            elif isinstance(strategy, TrailingStopExitStrategy):
                config_key = 'trailing_stop_exit'
            elif isinstance(strategy, LadderExitStrategy):
                config_key = 'ladder_exit'
            elif isinstance(strategy, TimeBasedExitStrategy):
                config_key = 'time_based_exit'
            elif isinstance(strategy, OrderedTakeProfitStopLossStrategy):
                config_key = 'ordered_tp_sl'
            
            if config_key and config_key in exit_strategies_config:
                enabled = exit_strategies_config[config_key].get('enabled', True)
                if enabled:
                    self.enable_strategy(name)
                else:
                    self.disable_strategy(name)
        
        self.logger.info(f"已加载 {len(self.strategies)} 个退出策略")
        
        # 打印已加载的策略及其状态
        for name, strategy in self.strategies.items():
            self.logger.info(f"策略: {name}, 启用状态: {strategy.enabled}, 优先级: {strategy.priority}")
            
            # 打印策略参数
            if isinstance(strategy, FixedPercentExitStrategy):
                self.logger.info(f"  - 止盈: {strategy.take_profit_pct*100:.2f}%, 止损: {strategy.stop_loss_pct*100:.2f}%")
            elif isinstance(strategy, ATRBasedExitStrategy):
                self.logger.info(f"  - ATR周期: {strategy.atr_period}, 时间框架: {strategy.atr_timeframe}, " +
                               f"乘数: {strategy.atr_multiplier}, 最小止损: {strategy.min_stop_loss_pct*100:.2f}%")
            elif isinstance(strategy, TrailingStopExitStrategy):
                self.logger.info(f"  - 追踪距离: {strategy.trailing_distance*100:.2f}%, 激活阈值: {strategy.activation_pct*100:.2f}%")
            elif isinstance(strategy, LadderExitStrategy):
                self.logger.info(f"  - 阶梯间隔: {strategy.ladder_step_pct*100:.2f}%, 每阶梯平仓比例: {strategy.close_pct_per_step*100:.2f}%")
            elif isinstance(strategy, TimeBasedExitStrategy):
                self.logger.info(f"  - K线周期: {strategy.candle_timeframe}, K线数量: {strategy.candle_count}")
    
    def add_strategy(self, strategy: ExitStrategy) -> None:
        """
        添加策略
        
        Args:
            strategy: 平仓策略对象
        """
        self.strategies[strategy.name] = strategy
        self.logger.info(f"添加平仓策略: {strategy.name}, 优先级: {strategy.priority}")
    
    def remove_strategy(self, strategy_name: str) -> None:
        """
        移除策略
        
        Args:
            strategy_name: 策略名称
        """
        if strategy_name in self.strategies:
            del self.strategies[strategy_name]
            self.logger.info(f"移除平仓策略: {strategy_name}")
    
    def get_strategy(self, strategy_name: str) -> Optional[ExitStrategy]:
        """
        获取特定名称的策略
        
        Args:
            strategy_name: 策略名称
            
        Returns:
            退出策略对象，如果不存在则返回None
        """
        # 尝试直接匹配策略名称
        if strategy_name in self.strategies:
            return self.strategies[strategy_name]
        
        # 尝试匹配类型名称
        for strategy in self.strategies.values():
            if strategy.__class__.__name__ == strategy_name:
                return strategy
            
        # 尝试匹配ExitTriggerType的值
        for trigger_type in ExitTriggerType:
            if strategy_name == trigger_type.value:
                # 查找对应的策略
                for strategy in self.strategies.values():
                    if (isinstance(strategy, FixedPercentExitStrategy) and trigger_type in [ExitTriggerType.TAKE_PROFIT, ExitTriggerType.STOP_LOSS]) or \
                       (isinstance(strategy, TrailingStopExitStrategy) and trigger_type == ExitTriggerType.TRAILING_STOP) or \
                       (isinstance(strategy, LadderExitStrategy) and trigger_type == ExitTriggerType.LADDER_TP) or \
                       (isinstance(strategy, TimeBasedExitStrategy) and trigger_type == ExitTriggerType.TIME_BASED):
                        return strategy
        
        # 没有找到匹配的策略
        return None
    
    def enable_strategy(self, strategy_name: str) -> None:
        """
        启用策略
        
        Args:
            strategy_name: 策略名称
        """
        if strategy_name in self.strategies:
            self.strategies[strategy_name].enabled = True
            self.logger.info(f"启用平仓策略: {strategy_name}")
    
    def disable_strategy(self, strategy_name: str) -> None:
        """
        禁用策略
        
        Args:
            strategy_name: 策略名称
        """
        if strategy_name in self.strategies:
            self.strategies[strategy_name].enabled = False
            self.logger.info(f"禁用平仓策略: {strategy_name}")
    
    def update_strategy_params(self, strategy_name: str, params: Dict[str, Any]) -> None:
        """
        更新策略参数
        
        Args:
            strategy_name: 策略名称
            params: 参数字典
        """
        if strategy_name in self.strategies:
            self.strategies[strategy_name].update_params(params)
            self.logger.info(f"更新平仓策略参数: {strategy_name}, {params}")
    
    async def check_exit_conditions(self, position: Any, current_price: float, 
                                  execute_close_func: Callable = None, **kwargs) -> Tuple[bool, Optional[ExitSignal]]:
        """
        检查所有策略是否满足平仓条件，并执行平仓
        
        Args:
            position: 仓位对象
            current_price: 当前价格
            execute_close_func: 执行平仓的函数，接收symbol、position和close_percentage参数
            kwargs: 额外参数
            
        Returns:
            Tuple[bool, Optional[ExitSignal]]: 
                - 第一个元素表示是否有策略触发并执行了平仓
                - 第二个元素包含退出信号（如有），特别是当need_cleanup=True时
        """
        import time  # 导入time模块用于获取时间戳
        from typing import Tuple, Optional  # 导入类型提示
        
        if not self.strategies:
            return False, None
        
        # 按优先级排序策略
        sorted_strategies = sorted(self.strategies.values(), key=lambda s: s.priority)
        
        # 检查每个策略
        for strategy in sorted_strategies:
            if not strategy.enabled:
                continue
                
            signal = await strategy.check_exit_condition(position, current_price, 
                                                       exit_strategy_manager=self,
                                                       **kwargs)
            
            # 处理需要清理的信号
            if signal and signal.need_cleanup:
                self.logger.info(f"策略 {strategy.name} 信号需要执行完整平仓清理流程: {signal.message}")
                # 直接将需要清理的信号返回出去，让BaseStrategy处理
                return True, signal
            
            # 处理常规触发信号            
            if signal and signal.triggered:
                self.logger.info(f"策略 {strategy.name} 触发平仓: {signal.message}")
                
                # 尝试取消可能存在的委托单
                try:
                    # 检查是否有OrderedTakeProfitStopLossStrategy并取消相关订单
                    tp_sl_strategy = self.get_strategy("委托单止盈止损")
                    if tp_sl_strategy and hasattr(tp_sl_strategy, '_get_position_key') and hasattr(tp_sl_strategy, 'submitted_orders') and hasattr(tp_sl_strategy, '_cancel_order'):
                        key = tp_sl_strategy._get_position_key(position)
                        if key in tp_sl_strategy.submitted_orders:
                            order_info = tp_sl_strategy.submitted_orders[key]
                            if order_info.get("status") == "submitted":
                                tp_sl_strategy._cancel_order(position.symbol, order_info.get("tp_order_id"))
                                self.logger.info(f"已取消 {position.symbol} 的止盈委托单，订单ID: {order_info.get('tp_order_id')}")
                                # 更新状态并移除已取消的订单
                                order_info["status"] = "canceled"
                                # 稍后会在平仓成功后清理，这里不移除
                except Exception as e:
                    self.logger.warning(f"尝试取消委托单时发生错误: {e}")
                
                # 执行平仓
                success = await strategy.execute_exit(position, signal, execute_close_func)
                
                if success:
                    self.logger.info(f"策略 {strategy.name} 平仓执行成功")
                    return True, None
                else:
                    self.logger.warning(f"策略 {strategy.name} 平仓执行失败")
                
        return False, None
    
    def to_dict(self) -> Dict[str, Any]:
        """将所有策略转换为字典，用于序列化"""
        return {strategy.name: strategy.to_dict() for strategy in self.strategies.values()}
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any], app_name: str, position_mgr=None, 
                 strategy_config=None, data_cache=None, trader=None) -> 'ExitStrategyManager':
        """
        从字典创建策略管理器
        
        Args:
            data: 字典数据
            app_name: 应用名称
            position_mgr: 仓位管理器
            strategy_config: 策略配置
            data_cache: 数据缓存对象
            trader: 交易执行器
            
        Returns:
            ExitStrategyManager: 策略管理器对象
        """
        manager = cls(
            app_name=app_name,
            position_mgr=position_mgr,
            strategy_config=strategy_config,
            data_cache=data_cache,
            trader=trader
        )
        
        # 清空默认策略
        manager.strategies = {}
        
        # 根据字典数据创建策略
        for strategy_name, strategy_data in data.items():
            strategy_type = strategy_data.get("type")
            
            strategy = None
            if strategy_type == "FixedPercentExitStrategy":
                strategy = FixedPercentExitStrategy.from_dict(strategy_data, app_name, position_mgr, strategy_config, data_cache, trader)
            elif strategy_type == "TrailingStopExitStrategy":
                strategy = TrailingStopExitStrategy.from_dict(strategy_data, app_name, position_mgr, strategy_config, data_cache, trader)
            elif strategy_type == "LadderExitStrategy":
                strategy = LadderExitStrategy.from_dict(strategy_data, app_name, position_mgr, strategy_config, data_cache, trader)
            elif strategy_type == "TimeBasedExitStrategy":
                strategy = TimeBasedExitStrategy.from_dict(strategy_data, app_name, position_mgr, strategy_config, data_cache, trader)
            elif strategy_type == "ATRBasedExitStrategy":
                strategy = ATRBasedExitStrategy.from_dict(strategy_data, app_name, position_mgr, strategy_config, data_cache, trader)
            elif strategy_type == "OrderedTakeProfitStopLossStrategy":
                strategy = OrderedTakeProfitStopLossStrategy.from_dict(strategy_data, app_name, position_mgr, strategy_config, data_cache, trader)
            
            if strategy:
                manager.add_strategy(strategy)
        
        return manager
