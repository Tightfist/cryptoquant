# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch, MagicMock
import os
import sys
from abc import ABC, abstractmethod
from datetime import datetime

# 添加项目根目录到路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.exchange.exchange_adapter import ExchangeAdapter


class ExchangeAdapterTestBase(ABC, unittest.TestCase):
    """
    交易所适配器测试基类
    
    这是一个抽象基类，用于测试任何实现了ExchangeAdapter接口的交易所适配器。
    子类需要实现create_adapter方法来创建具体的适配器实例。
    """
    
    @abstractmethod
    def create_adapter(self, app_name: str, config: dict) -> ExchangeAdapter:
        """
        创建要测试的交易所适配器实例
        
        Args:
            app_name: 应用名称
            config: 配置信息
            
        Returns:
            交易所适配器实例
        """
        pass
    
    def setUp(self):
        """测试前的准备工作"""
        # 创建测试配置
        self.test_config = {
            'api_key': 'test_api_key',
            'secret_key': 'test_secret_key',
            'passphrase': 'test_passphrase',
            'is_simulated': True,
            'log_level': 'ERROR'  # 测试时使用ERROR级别减少日志输出
        }
        
        # 创建测试实例
        with patch('src.common.logger.configure_logger'):  # 避免实际配置日志
            self.adapter = self.create_adapter('test_app', self.test_config)
        
        # 模拟请求方法
        self.adapter._request = MagicMock()
    
    def test_spot_order(self):
        """测试现货下单"""
        # 设置模拟返回值
        self.adapter._request.return_value = {
            "code": "0",
            "data": [{"ordId": "12345", "clOrdId": "", "tag": "", "sCode": "0", "sMsg": ""}]
        }
        
        # 调用方法
        result = self.adapter.spot_order("BTC-USDT", "buy", 0.01)
        
        # 验证结果
        self.assertEqual(result["code"], "0")
        self.assertEqual(result["data"][0]["ordId"], "12345")
    
    def test_swap_order(self):
        """测试合约下单"""
        # 设置模拟返回值
        self.adapter._request.return_value = {
            "code": "0",
            "data": [{"ordId": "12345", "clOrdId": "", "tag": "", "sCode": "0", "sMsg": ""}]
        }
        
        # 调用方法
        result = self.adapter.swap_order("BTC-USDT-SWAP", "buy", "long", 1)
        
        # 验证结果
        self.assertEqual(result["code"], "0")
        self.assertEqual(result["data"][0]["ordId"], "12345")
    
    def test_get_funding_rate(self):
        """测试获取资金费率"""
        # 设置模拟返回值
        self.adapter._request.return_value = {
            "code": "0",
            "data": [{"fundingRate": "0.0001", "fundingTime": "1597026383085"}]
        }
        
        # 调用方法
        result = self.adapter.get_funding_rate("BTC-USDT-SWAP")
        
        # 验证结果
        self.assertEqual(result, 0.0001)
    
    def test_get_mark_price(self):
        """测试获取标记价格"""
        # 设置模拟返回值
        self.adapter._request.return_value = {
            "code": "0",
            "data": [{"instId": "BTC-USDT-SWAP", "instType": "SWAP", "markPx": "40000.1"}]
        }
        
        # 调用方法
        result = self.adapter.get_mark_price("BTC-USDT-SWAP")
        
        # 验证结果
        self.assertEqual(result, 40000.1)
    
    def test_get_spot_price(self):
        """测试获取现货价格"""
        # 设置模拟返回值
        self.adapter._request.return_value = {
            "code": "0",
            "data": [{"instId": "BTC-USDT", "last": "40000.5", "askPx": "40001", "bidPx": "40000"}]
        }
        
        # 调用方法
        result = self.adapter.get_spot_price("BTC-USDT")
        
        # 验证结果
        self.assertEqual(result, 40000.5)
    
    def test_get_usdt_balance(self):
        """测试获取USDT余额"""
        # 设置模拟返回值
        self.adapter._request.return_value = {
            "code": "0",
            "data": [{
                "totalEq": "10000",
                "details": [
                    {"ccy": "USDT", "eq": "5000", "availBal": "4500"},
                    {"ccy": "BTC", "eq": "5000", "availBal": "0.1"}
                ]
            }]
        }
        
        # 调用方法
        result = self.adapter.get_usdt_balance()
        
        # 验证结果
        self.assertEqual(result, 4500.0)
    
    def test_cancel_order(self):
        """测试取消订单"""
        # 设置模拟返回值
        self.adapter._request.return_value = {
            "code": "0",
            "data": [{"ordId": "12345", "clOrdId": "", "sCode": "0", "sMsg": ""}]
        }
        
        # 调用方法
        result = self.adapter.cancel_order("BTC-USDT-SWAP", "12345")
        
        # 验证结果
        self.assertEqual(result["code"], "0")
    
    def test_get_order_details(self):
        """测试获取订单详情"""
        # 设置模拟返回值
        self.adapter._request.return_value = {
            "code": "0",
            "data": [{
                "instId": "BTC-USDT-SWAP",
                "ordId": "12345",
                "sz": "1",
                "px": "40000",
                "state": "filled"
            }]
        }
        
        # 调用方法
        result = self.adapter.get_order_details("BTC-USDT-SWAP", "12345")
        
        # 验证结果
        self.assertEqual(result["data"][0]["ordId"], "12345")
        self.assertEqual(result["data"][0]["state"], "filled")
    
    def test_get_positions(self):
        """测试获取持仓"""
        # 设置模拟返回值
        self.adapter._request.return_value = {
            "code": "0",
            "data": [
                {
                    "instId": "BTC-USDT-SWAP",
                    "pos": "1",
                    "posSide": "long",
                    "avgPx": "40000"
                }
            ]
        }
        
        # 调用方法
        result = self.adapter.get_positions()
        
        # 验证结果
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["instId"], "BTC-USDT-SWAP")
        self.assertEqual(result[0]["posSide"], "long")
    
    def test_set_leverage(self):
        """测试设置杠杆"""
        # 设置模拟返回值
        self.adapter._request.return_value = {
            "code": "0",
            "data": [{"instId": "BTC-USDT-SWAP", "lever": "10", "mgnMode": "cross"}]
        }
        
        # 调用方法
        result = self.adapter.set_leverage("BTC-USDT-SWAP", 10)
        
        # 验证结果
        self.assertEqual(result["code"], "0")
        self.assertEqual(result["data"][0]["lever"], "10")
    
    def test_get_kline_data(self):
        """测试获取K线数据"""
        # 设置模拟返回值
        timestamp = int(datetime.now().timestamp() * 1000)
        self.adapter._request.return_value = {
            "code": "0",
            "data": [
                [str(timestamp), "40000", "40100", "39900", "40050", "100", "4000000"],
                [str(timestamp - 60000), "39900", "40000", "39800", "40000", "120", "4800000"]
            ]
        }
        
        # 调用方法
        result = self.adapter.get_kline_data("BTC-USDT", "1m", 2)
        
        # 验证结果
        self.assertEqual(len(result), 2)
    
    def test_batch_orders(self):
        """测试批量下单"""
        # 设置模拟返回值
        self.adapter._request.return_value = {
            "code": "0",
            "data": [
                {"clOrdId": "", "ordId": "12345", "sCode": "0", "sMsg": ""},
                {"clOrdId": "", "ordId": "12346", "sCode": "0", "sMsg": ""}
            ]
        }
        
        # 准备测试数据
        orders = [
            {
                "instId": "BTC-USDT-SWAP",
                "tdMode": "cross",
                "side": "buy",
                "ordType": "market",
                "sz": "1"
            },
            {
                "instId": "ETH-USDT-SWAP",
                "tdMode": "cross",
                "side": "buy",
                "ordType": "market",
                "sz": "10"
            }
        ]
        
        # 调用方法
        result = self.adapter.batch_orders(orders)
        
        # 验证结果
        self.assertEqual(result["code"], "0")
        self.assertEqual(len(result["data"]), 2)
        self.assertEqual(result["data"][0]["ordId"], "12345")
        self.assertEqual(result["data"][1]["ordId"], "12346")


# 示例：如何使用这个基类测试OKExTrader
"""
from src.exchange.okex.trader import OKExTrader

class TestOKExTrader(ExchangeAdapterTestBase):
    def create_adapter(self, app_name: str, config: dict) -> ExchangeAdapter:
        return OKExTrader(app_name, config)

if __name__ == '__main__':
    unittest.main()
""" 