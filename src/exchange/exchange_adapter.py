# -*- coding: utf-8 -*-
import logging
from abc import ABC, abstractmethod
from typing import Dict, Optional, Any, List, Union

from src.common.logger import configure_logger

class ExchangeAdapter(ABC):
    """
    交易所适配器基类，定义通用的交易所接口方法
    所有具体交易所实现都应该继承此类并实现相应方法
    """
    
    def __init__(self, app_name: str, config: Dict):
        """
        初始化交易所适配器
        
        Args:
            app_name: 应用名称，用于日志隔离
            config: 交易所配置信息
        """
        self.app_name = app_name
        self.config = config
        
        # 使用 configure_logger 初始化日志
        # 从配置中获取日志级别、日志文件名和输出目标
        log_level = config.get('log_level', 'INFO')
        log_file = None
        output_targets = None
        
        # 如果配置中有logging部分，尝试获取日志配置
        if 'logging' in config:
            log_level = config['logging'].get('level', log_level)
            log_file = config['logging'].get('file')
            output_targets = config['logging'].get('output_targets')
        
        # 不再在这里调用 configure_logger，因为应用程序已经配置了日志
        # 只需获取日志记录器即可
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # 初始化时打印配置摘要
        self.logger.info(f"交易所适配器初始化完成 [App={app_name}]", extra={
            "exchange": self.__class__.__name__,
            "simulated": config.get('is_simulated', False)
        })
    
    @abstractmethod
    def _request(self, method: str, path: str, params: Optional[dict] = None) -> dict:
        """
        发送API请求到交易所
        
        Args:
            method: HTTP方法 (GET, POST等)
            path: API路径
            params: 请求参数
            
        Returns:
            交易所返回的响应数据
        """
        pass
    
    # ------------------- 现货接口 -------------------
    @abstractmethod
    def spot_order(self, inst_id: str, side: str, sz: float, ord_type: str = "market") -> dict:
        """
        现货下单
        
        Args:
            inst_id: 交易对ID
            side: 买卖方向 (buy/sell)
            sz: 交易数量
            ord_type: 订单类型 (market/limit等)
            
        Returns:
            订单结果
        """
        pass
    
    # ------------------- 合约接口 -------------------
    @abstractmethod
    def swap_order(self, inst_id: str, side: str, pos_side: str, sz: float, ord_type: str = "market") -> dict:
        """
        永续合约下单
        
        Args:
            inst_id: 合约ID
            side: 买卖方向 (buy/sell)
            pos_side: 持仓方向 (long/short)
            sz: 合约数量
            ord_type: 订单类型 (market/limit等)
            
        Returns:
            订单结果
        """
        pass
    
    # ------------------- 行情接口 -------------------
    @abstractmethod
    def get_funding_rate(self, inst_id: str) -> float:
        """
        获取资金费率
        
        Args:
            inst_id: 合约ID
            
        Returns:
            资金费率
        """
        pass
    
    @abstractmethod
    def get_mark_price(self, inst_id: str) -> float:
        """
        获取标记价格
        
        Args:
            inst_id: 合约ID
            
        Returns:
            标记价格
        """
        pass
    
    @abstractmethod
    def get_spot_price(self, inst_id: str) -> float:
        """
        获取现货价格
        
        Args:
            inst_id: 交易对ID
            
        Returns:
            现货价格
        """
        pass
    
    # ------------------- 账户接口 -------------------
    @abstractmethod
    def get_usdt_balance(self) -> float:
        """
        获取可用USDT余额
        
        Returns:
            可用USDT余额
        """
        pass
    
    # ------------------- 工具函数 -------------------
    @abstractmethod
    def calculate_position_size(self, inst_id: str, is_spot: bool, target_usdt: float, target_leverage: float = 0) -> float:
        """
        计算头寸大小
        
        Args:
            inst_id: 交易对/合约ID
            is_spot: 是否为现货
            target_usdt: 目标USDT金额
            target_leverage: 目标杠杆倍数
            
        Returns:
            计算后的头寸大小
        """
        pass
    
    # ------------------- 订单管理接口 -------------------
    @abstractmethod
    def cancel_order(self, inst_id: str, order_id: str) -> dict:
        """
        取消订单
        
        Args:
            inst_id: 交易对/合约ID
            order_id: 订单ID
            
        Returns:
            取消结果
        """
        pass
    
    @abstractmethod
    def get_order_details(self, inst_id: str, order_id: str) -> dict:
        """
        查询订单详情
        
        Args:
            inst_id: 交易对/合约ID
            order_id: 订单ID
            
        Returns:
            订单详情
        """
        pass
    
    @abstractmethod
    def batch_orders(self, orders: list) -> dict:
        """
        批量下单
        
        Args:
            orders: 订单列表，每个订单为一个字典，包含交易对、方向等信息
            
        Returns:
            批量下单结果
        """
        pass
    
    # ------------------- 仓位管理接口 -------------------
    @abstractmethod
    def get_positions(self, inst_type: str = "SWAP") -> list:
        """
        查询当前持仓
        
        Args:
            inst_type: 产品类型，如 SWAP(永续合约), SPOT(现货), FUTURES(交割合约)等
            
        Returns:
            持仓列表
        """
        pass
    
    @abstractmethod
    def set_leverage(self, inst_id: str, leverage: int, mgnMode: str = "cross") -> dict:
        """
        设置杠杆倍数
        
        Args:
            inst_id: 合约ID
            leverage: 杠杆倍数
            mgnMode: 保证金模式，cross(全仓) 或 isolated(逐仓)
            
        Returns:
            设置结果
        """
        pass
    
    # ------------------- 行情数据接口 -------------------
    @abstractmethod
    def get_kline_data(self, inst_id: str, bar: str = "1m", limit: int = 100) -> list:
        """
        获取K线数据
        
        Args:
            inst_id: 交易对/合约ID
            bar: K线周期，如 1m, 5m, 15m, 30m, 1H, 2H, 4H, 6H, 12H, 1D, 1W, 1M
            limit: 返回的K线数量
            
        Returns:
            K线数据列表
        """
        pass 