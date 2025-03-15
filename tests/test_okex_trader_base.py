# -*- coding: utf-8 -*-
import os
import sys

# 添加项目根目录到路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.exchange.okex.trader import OKExTrader
from tests.test_exchange_adapter import ExchangeAdapterTestBase
from src.exchange.exchange_adapter import ExchangeAdapter


class TestOKExTraderBase(ExchangeAdapterTestBase):
    """使用通用测试基类测试OKExTrader"""
    
    def create_adapter(self, app_name: str, config: dict) -> ExchangeAdapter:
        """创建OKExTrader实例"""
        return OKExTrader(app_name, config)


if __name__ == '__main__':
    import unittest
    unittest.main() 