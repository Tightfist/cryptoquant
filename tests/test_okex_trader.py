# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch, MagicMock
import json
import os
import sys
from datetime import datetime

# 添加项目根目录到路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.exchange.okex.trader import OKExTrader


class TestOKExTrader(unittest.TestCase):
    """OKExTrader 单元测试类"""

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
            self.trader = OKExTrader('test_app', self.test_config)
        
        # 模拟请求方法
        self.trader._request = MagicMock()

    def test_init(self):
        """测试初始化"""
        self.assertEqual(self.trader.api_key, 'test_api_key')
        self.assertEqual(self.trader.secret_key, 'test_secret_key')
        self.assertEqual(self.trader.passphrase, 'test_passphrase')
        self.assertEqual(self.trader.base_url, 'https://www.okx.com')
        self.assertTrue(self.trader.is_simulated)

    @patch('requests.get')
    @patch('requests.post')
    def test_request(self, mock_post, mock_get):
        """测试请求方法"""
        # 恢复原始的_request方法
        self.trader._request = OKExTrader._request.__get__(self.trader)
        
        # 模拟响应
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"code": "0", "data": [{"result": "success"}]}
        mock_get.return_value = mock_response
        mock_post.return_value = mock_response
        
        # 测试GET请求
        result = self.trader._request("GET", "/test/path", {"param": "value"})
        self.assertEqual(result["code"], "0")
        self.assertEqual(result["data"][0]["result"], "success")
        
        # 测试POST请求
        result = self.trader._request("POST", "/test/path", {"param": "value"})
        self.assertEqual(result["code"], "0")
        self.assertEqual(result["data"][0]["result"], "success")
        
        # 测试错误处理
        mock_response.status_code = 400
        mock_response.text = "Error"
        result = self.trader._request("GET", "/test/path")
        self.assertEqual(result["code"], "-1")

    def test_spot_order(self):
        """测试现货下单"""
        # 设置模拟返回值
        self.trader._request.return_value = {
            "code": "0",
            "data": [{"ordId": "12345", "clOrdId": "", "tag": "", "sCode": "0", "sMsg": ""}]
        }
        
        # 调用方法
        result = self.trader.spot_order("BTC-USDT", "buy", 0.01)
        
        # 验证结果
        self.trader._request.assert_called_with(
            "POST", 
            "/api/v5/trade/order", 
            {
                "instId": "BTC-USDT",
                "tdMode": "cash",
                "side": "buy",
                "ordType": "market",
                "sz": "0.01",
                "tgtCcy": "base_ccy"
            }
        )
        self.assertEqual(result["code"], "0")
        self.assertEqual(result["data"][0]["ordId"], "12345")

    def test_swap_order(self):
        """测试合约下单"""
        # 设置模拟返回值
        self.trader._request.return_value = {
            "code": "0",
            "data": [{"ordId": "12345", "clOrdId": "", "tag": "", "sCode": "0", "sMsg": ""}]
        }
        
        # 调用方法
        result = self.trader.swap_order("BTC-USDT-SWAP", "buy", "long", 1)
        
        # 验证结果
        self.trader._request.assert_called_with(
            "POST", 
            "/api/v5/trade/order", 
            {
                "instId": "BTC-USDT-SWAP",
                "tdMode": "cross",
                "side": "buy",
                "posSide": "long",
                "ordType": "market",
                "sz": "1"
            }
        )
        self.assertEqual(result["code"], "0")
        self.assertEqual(result["data"][0]["ordId"], "12345")

    def test_get_funding_rate(self):
        """测试获取资金费率"""
        # 设置模拟返回值
        self.trader._request.return_value = {
            "code": "0",
            "data": [{"fundingRate": "0.0001", "fundingTime": "1597026383085"}]
        }
        
        # 调用方法
        result = self.trader.get_funding_rate("BTC-USDT-SWAP")
        
        # 验证结果
        self.trader._request.assert_called_with(
            "GET", 
            "/api/v5/public/funding-rate", 
            {"instId": "BTC-USDT-SWAP"}
        )
        self.assertEqual(result, 0.0001)

    def test_get_mark_price(self):
        """测试获取标记价格"""
        # 设置模拟返回值
        self.trader._request.return_value = {
            "code": "0",
            "data": [{"instId": "BTC-USDT-SWAP", "instType": "SWAP", "markPx": "40000.1"}]
        }
        
        # 调用方法
        result = self.trader.get_mark_price("BTC-USDT-SWAP")
        
        # 验证结果
        self.trader._request.assert_called_with(
            "GET", 
            "/api/v5/public/mark-price", 
            {"instId": "BTC-USDT-SWAP"}
        )
        self.assertEqual(result, 40000.1)

    def test_get_spot_price(self):
        """测试获取现货价格"""
        # 设置模拟返回值
        self.trader._request.return_value = {
            "code": "0",
            "data": [{"instId": "BTC-USDT", "last": "40000.5", "askPx": "40001", "bidPx": "40000"}]
        }
        
        # 调用方法
        result = self.trader.get_spot_price("BTC-USDT")
        
        # 验证结果
        self.trader._request.assert_called_with(
            "GET", 
            "/api/v5/market/ticker", 
            {"instId": "BTC-USDT"}
        )
        self.assertEqual(result, 40000.5)

    def test_get_contract_info(self):
        """测试获取合约信息"""
        # 设置模拟返回值
        self.trader._request.return_value = {
            "code": "0",
            "data": [{"instId": "BTC-USDT-SWAP", "ctVal": "0.01", "lotSz": "1"}]
        }
        
        # 调用方法
        result = self.trader.get_contract_info("BTC-USDT-SWAP", False)
        
        # 验证结果
        self.trader._request.assert_called_with(
            "GET", 
            "/api/v5/public/instruments", 
            {"instType": "SWAP", "instId": "BTC-USDT-SWAP"}
        )
        self.assertEqual(result["data"][0]["instId"], "BTC-USDT-SWAP")

    def test_get_usdt_balance(self):
        """测试获取USDT余额"""
        # 设置模拟返回值
        self.trader._request.return_value = {
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
        result = self.trader.get_usdt_balance()
        
        # 验证结果
        self.trader._request.assert_called_with("GET", "/api/v5/account/balance")
        self.assertEqual(result, 4500.0)

    def test_calculate_position_size(self):
        """测试计算头寸大小"""
        # 模拟依赖方法
        self.trader.get_contract_info = MagicMock(return_value={
            "data": [{"ctVal": "0.01", "lotSz": "1"}]
        })
        self.trader.get_spot_price = MagicMock(return_value=40000.0)
        
        # 测试现货
        result = self.trader.calculate_position_size("BTC-USDT", True, 1000.0)
        self.assertEqual(result, 0.025)  # 1000 / 40000 = 0.025
        
        # 测试合约
        result = self.trader.calculate_position_size("BTC-USDT-SWAP", False, 1000.0)
        self.assertEqual(result, 2.0)  # 1000 / (0.01 * 40000) = 2.5, 向下取整为2

    def test_cancel_order(self):
        """测试取消订单"""
        # 设置模拟返回值
        self.trader._request.return_value = {
            "code": "0",
            "data": [{"ordId": "12345", "clOrdId": "", "sCode": "0", "sMsg": ""}]
        }
        
        # 调用方法
        result = self.trader.cancel_order("BTC-USDT-SWAP", "12345")
        
        # 验证结果
        self.trader._request.assert_called_with(
            "POST", 
            "/api/v5/trade/cancel-order", 
            {"instId": "BTC-USDT-SWAP", "ordId": "12345"}
        )
        self.assertEqual(result["code"], "0")

    def test_get_order_details(self):
        """测试获取订单详情"""
        # 设置模拟返回值
        self.trader._request.return_value = {
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
        result = self.trader.get_order_details("BTC-USDT-SWAP", "12345")
        
        # 验证结果
        self.trader._request.assert_called_with(
            "GET", 
            "/api/v5/trade/order", 
            {"instId": "BTC-USDT-SWAP", "ordId": "12345"}
        )
        self.assertEqual(result["data"][0]["ordId"], "12345")
        self.assertEqual(result["data"][0]["state"], "filled")

    def test_get_positions(self):
        """测试获取持仓"""
        # 设置模拟返回值
        self.trader._request.return_value = {
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
        result = self.trader.get_positions()
        
        # 验证结果
        self.trader._request.assert_called_with(
            "GET", 
            "/api/v5/account/positions", 
            {"instType": "SWAP"}
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["instId"], "BTC-USDT-SWAP")
        self.assertEqual(result[0]["posSide"], "long")

    def test_set_leverage(self):
        """测试设置杠杆"""
        # 设置模拟返回值
        self.trader._request.return_value = {
            "code": "0",
            "data": [{"instId": "BTC-USDT-SWAP", "lever": "10", "mgnMode": "cross"}]
        }
        
        # 调用方法
        result = self.trader.set_leverage("BTC-USDT-SWAP", 10)
        
        # 验证结果
        self.trader._request.assert_called_with(
            "POST", 
            "/api/v5/account/set-leverage", 
            {"instId": "BTC-USDT-SWAP", "lever": "10", "mgnMode": "cross"}
        )
        self.assertEqual(result["code"], "0")
        self.assertEqual(result["data"][0]["lever"], "10")

    def test_get_kline_data(self):
        """测试获取K线数据"""
        # 设置模拟返回值
        timestamp = int(datetime.now().timestamp() * 1000)
        self.trader._request.return_value = {
            "code": "0",
            "data": [
                [str(timestamp), "40000", "40100", "39900", "40050", "100", "4000000"],
                [str(timestamp - 60000), "39900", "40000", "39800", "40000", "120", "4800000"]
            ]
        }
        
        # 调用方法
        result = self.trader.get_kline_data("BTC-USDT", "1m", 2)
        
        # 验证结果
        self.trader._request.assert_called_with(
            "GET", 
            "/api/v5/market/candles", 
            {"instId": "BTC-USDT", "bar": "1m", "limit": "2"}
        )
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["open"], 40000.0)
        self.assertEqual(result[0]["close"], 40050.0)
        self.assertEqual(result[1]["volume"], 120.0)

    def test_batch_orders(self):
        """测试批量下单"""
        # 设置模拟返回值
        self.trader._request.return_value = {
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
        result = self.trader.batch_orders(orders)
        
        # 验证结果
        self.trader._request.assert_called_with(
            "POST", 
            "/api/v5/trade/batch-orders", 
            {"orders": orders}
        )
        self.assertEqual(result["code"], "0")
        self.assertEqual(len(result["data"]), 2)
        self.assertEqual(result["data"][0]["ordId"], "12345")
        self.assertEqual(result["data"][1]["ordId"], "12346")


if __name__ == '__main__':
    unittest.main()
