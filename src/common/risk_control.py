"""
风险控制模块

提供统一的风险控制规则和机制，包括：
1. 冷却期机制：限制同一标的连续交易的时间间隔
2. 日交易上限：限制每日交易次数
3. 亏损限制：根据当日亏损百分比控制交易

该模块设计为与仓位管理集成，通过交易信号中的风控参数，在开仓时自动判断风控条件
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Set, Tuple, Union


class RiskController:
    """风险控制器，提供风控规则检查"""
    
    def __init__(self, logger=None):
        """
        初始化风险控制器
        
        Args:
            logger: 日志记录器，如果不提供则使用默认记录器
        """
        self.logger = logger or logging.getLogger("RiskController")
        
        # 存储最后交易时间信息
        self.last_trade_time = {}
        
        # 当日交易统计
        self.daily_trades_count = 0
        self.daily_pnl_pct = 0.0
        
        # 风控配置
        self.cooling_period_minutes = 30
        self.max_daily_trades = 50
        self.max_daily_loss_pct = 50.0
        
        # 风控开关
        self.enable_cooling_period = True
        self.enable_daily_limit = True
        self.enable_loss_limit = True
    
    def configure(self, config: Dict[str, Any]) -> None:
        """
        配置风险控制参数
        
        Args:
            config: 风险控制配置
        """
        self.cooling_period_minutes = config.get('cooling_period_minutes', 30)
        self.max_daily_trades = config.get('max_daily_trades', 50)
        self.max_daily_loss_pct = config.get('max_daily_loss_pct', 50.0)
        
        self.enable_cooling_period = config.get('enable_cooling_period', True)
        self.enable_daily_limit = config.get('enable_daily_limit', True)
        self.enable_loss_limit = config.get('enable_loss_limit', True)
        
        self.logger.info("风险控制配置已更新", extra={
            "冷却期": f"{self.cooling_period_minutes}分钟",
            "每日最大交易次数": self.max_daily_trades,
            "最大亏损比例": f"{self.max_daily_loss_pct}%",
            "冷却期开关": self.enable_cooling_period,
            "日限额开关": self.enable_daily_limit,
            "亏损限制开关": self.enable_loss_limit
        })
    
    def update_daily_pnl(self, pnl_pct: float) -> None:
        """
        更新当日盈亏百分比
        
        Args:
            pnl_pct: 盈亏百分比
        """
        self.daily_pnl_pct = pnl_pct
    
    def reset_daily_counters(self) -> None:
        """重置每日计数器"""
        self.daily_trades_count = 0
        self.daily_pnl_pct = 0.0
        self.logger.info("已重置风控每日计数器")
    
    def record_trade(self, symbol: str) -> None:
        """
        记录交易信息
        
        Args:
            symbol: 交易标的
        """
        # 记录最后交易时间
        self.last_trade_time[symbol] = datetime.now()
        # 增加交易计数
        self.daily_trades_count += 1
        
        self.logger.info(f"记录交易: {symbol}, 当日第{self.daily_trades_count}笔")
    
    def check_symbol_allowed(self, symbol: str, risk_params: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        """
        检查标的是否允许交易
        
        Args:
            symbol: 交易标的
            risk_params: 风控参数，可以覆盖默认设置
            
        Returns:
            Tuple[bool, str]: (是否允许, 原因)
        """
        # 如果没有风控参数，允许交易
        if risk_params is None:
            return True, "未使用风控"
        
        # 检查冷却期
        if risk_params.get('enable_cooling_period', self.enable_cooling_period):
            cooling_minutes = risk_params.get('cooling_period_minutes', self.cooling_period_minutes)
            
            if symbol in self.last_trade_time:
                cooling_end_time = self.last_trade_time[symbol] + timedelta(minutes=cooling_minutes)
                if datetime.now() < cooling_end_time:
                    remaining_seconds = (cooling_end_time - datetime.now()).total_seconds()
                    return False, f"冷却期限制: 还需等待 {int(remaining_seconds)} 秒"
        
        return True, "允许交易"
    
    def check_trade_allowed(self, risk_params: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        """
        检查是否允许交易（全局限制）
        
        Args:
            risk_params: 风控参数，可以覆盖默认设置
            
        Returns:
            Tuple[bool, str]: (是否允许, 原因)
        """
        # 如果没有风控参数，允许交易
        if risk_params is None:
            return True, "未使用风控"
        
        # 检查日交易上限
        if risk_params.get('enable_daily_limit', self.enable_daily_limit):
            max_trades = risk_params.get('max_daily_trades', self.max_daily_trades)
            
            if self.daily_trades_count >= max_trades:
                return False, f"达到每日交易上限: {max_trades}笔"
        
        # 检查亏损限制
        if risk_params.get('enable_loss_limit', self.enable_loss_limit):
            max_loss = risk_params.get('max_daily_loss_pct', self.max_daily_loss_pct)
            
            if self.daily_pnl_pct <= -max_loss:
                return False, f"达到每日最大亏损限制: {max_loss}%"
        
        return True, "允许交易"
    
    def check_risk_control(self, symbol: str, signal_extra_data: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        """
        综合检查风控条件
        
        Args:
            symbol: 交易标的
            signal_extra_data: 信号中的额外数据，包含风控参数
            
        Returns:
            Tuple[bool, str]: (是否允许, 原因)
        """
        # 如果信号中没有包含风控信息，默认允许交易
        if not signal_extra_data or 'risk_control' not in signal_extra_data:
            return True, "信号未包含风控信息"
        
        # 获取风控参数
        risk_params = signal_extra_data.get('risk_control', {})
        
        # 检查全局交易限制
        allowed, reason = self.check_trade_allowed(risk_params)
        if not allowed:
            return False, reason
        
        # 检查标的限制
        allowed, reason = self.check_symbol_allowed(symbol, risk_params)
        if not allowed:
            return False, reason
        
        # 通过所有检查，允许交易
        return True, "通过风控检查" 