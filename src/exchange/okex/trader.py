# -*- coding: utf-8 -*-
import hmac
import time
import base64
import requests
import json
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional, Dict

from src.exchange.exchange_adapter import ExchangeAdapter

class OKExTrader(ExchangeAdapter):
    def __init__(self, app_name: str, config: Dict):
        # 调用父类初始化方法，会使用 configure_logger 初始化日志
        super().__init__(app_name, config)
        
        # OKEx 特有的配置
        self.api_key = config['api_key']
        self.secret_key = config['secret_key']
        self.passphrase = config['passphrase']
        self.base_url = "https://www.okx.com"
        self.is_simulated = config.get('is_simulated', False)
        
        # 初始化时打印配置摘要
        self.logger.info(f"OKEx交易所初始化完成 [App={app_name}]", extra={
            "endpoint": self.base_url,
            "simulated": self.is_simulated
        })

    def _generate_signature(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        # 关键点1：严格按规则拼接签名消息
        message = timestamp + method.upper() + path + body
        #logger.debug(f"签名消息: {message}")
        
        # 关键点2：使用Base64编码的HMAC-SHA256
        secret = self.secret_key.encode('utf-8')
        signature = hmac.new(secret, message.encode('utf-8'), hashlib.sha256).digest()
        return base64.b64encode(signature).decode('utf-8')

    def _request(self, method: str, path: str, params: Optional[dict] = None) -> dict:
        # 关键点3：生成符合OKEx要求的时间戳
        timestamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')  # 输出示例：2023-10-15T03:30:45.123Z
        
        headers = {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json"
        }

        try:
            start_time = time.time()
            if method.upper() == "GET":
                # 关键点4：GET请求参数处理
                query_str = ""
                if params:
                    sorted_params = sorted(params.items(), key=lambda x: x[0])  # 参数按字母排序
                    query_str = '?' + '&'.join([f"{k}={v}" for k, v in sorted_params])
                
                full_path = path + query_str
                signature = self._generate_signature(timestamp, method, full_path, "")
                headers["OK-ACCESS-SIGN"] = signature
                response = requests.get(self.base_url + full_path, headers=headers)
            else:
                # 关键点5：POST请求体处理
                body_str = ""
                if params:
                    body_str = json.dumps(params, separators=(',', ':'), sort_keys=True)  # 无空格且排序
                
                signature = self._generate_signature(timestamp, method, path, body_str)
                headers["OK-ACCESS-SIGN"] = signature
                #print("HTTP_POST: url-" + self.base_url + path + " headers:" + str(headers) + " data:" + body_str)
                response = requests.post(self.base_url + path, headers=headers, data=body_str)
            latency = (time.time() - start_time) * 1000  # 毫秒
            log_data = {
                "endpoint": path,
                "params": params,
                "status": response.status_code,
                "latency_ms": round(latency, 2)
            }
            self.logger.info("API请求详情", extra={"details": log_data})  
            if response.status_code != 200:
                self.logger.error(
                    f"请求失败: HTTP状态码 {response.status_code}",
                    extra={"url": path, "params": params, "response": response.text}
                )
                return {"code": "-1", "msg": f"HTTP错误: {response.status_code}"}
            
            result = response.json()
            if result.get("code") != "0":
                # 修复 data 为空列表时的索引越界问题
                data_list = result.get("data", [{}])
                error_msg = result.get("msg", "未知错误")
                
                if data_list and len(data_list) > 0:
                    error_data = data_list[0]
                    error_msg = error_data.get('sMsg', error_msg)
                
                self.logger.error(
                    f"请求失败: {path} | Code: {result.get('code')} | Msg: {error_msg}",
                    extra={"params": params}  # 记录请求参数
                )
            
            response.raise_for_status()
            return result
        except requests.exceptions.RequestException as e:
            self.logger.error("API请求异常",
                extra={
                    "error": str(e),
                    "endpoint": path
                },
                exc_info=True
            )
            return {"code": "-1", "msg": str(e)}
    # ------------------- 现货接口 -------------------
    def spot_order(self, inst_id: str, side: str, sz: float, ord_type: str = "market") -> dict:
        """现货市价单"""
        params = {
            "instId": inst_id,
            "tdMode": "cash",
            "side": side,
            "ordType": ord_type,
            "sz": str(sz),
            "tgtCcy": "base_ccy"
        }
        self.logger.debug("现货下单参数", extra={"params": params})
        return self._request("POST", "/api/v5/trade/order", params)

    # ------------------- 合约接口 -------------------
    def swap_order(self, inst_id: str, side: str, pos_side: str, sz: float, ord_type: str = "market", tgt_ccy: str = None) -> dict:
        """
        永续合约市价单
        
        Args:
            inst_id: 合约ID，如 BTC-USDT-SWAP
            side: 订单方向，buy 或 sell
            pos_side: 持仓方向，long 或 short
            sz: 委托数量
            ord_type: 订单类型，默认为 market
            tgt_ccy: 委托数量单位，base_ccy(币) 或 quote_ccy(U)，默认为 None (按张数)
        
        Returns:
            dict: 订单结果
        """
        params = {
            "instId": inst_id,
            "tdMode": "cross",
            "side": side,
            "posSide": pos_side,
            "ordType": ord_type,
            "sz": str(sz),
        }
        
        # 如果指定了委托数量单位，则添加到参数中
        if tgt_ccy:
            params["tgtCcy"] = tgt_ccy
        
        self.logger.debug("合约下单参数", extra={"params": params})
        return self._request("POST", "/api/v5/trade/order", params)

    # ------------------- 行情接口 -------------------
    def get_funding_rate(self, inst_id: str) -> float:
        """获取资金费率"""
        response = self._request("GET", "/api/v5/public/funding-rate", {"instId": inst_id})
        rate = float(response['data'][0]['fundingRate'])
        self.logger.info("查询资金费率", extra={"inst_id": inst_id, "rate": rate})
        return rate

    def get_mark_price(self, inst_id: str) -> float:
        """获取标记价格"""
        response = self._request("GET", "/api/v5/public/mark-price", {"instId": inst_id})
        price = float(response['data'][0]['markPx'])
        self.logger.debug("标记价格更新", extra={"inst_id": inst_id, "price": price})
        return price

    def get_spot_price(self, inst_id: str) -> float:
        """获取现货价格"""
        response = self._request("GET", "/api/v5/market/ticker", {"instId": inst_id})
        price = float(response['data'][0]['last'])
        self.logger.debug("现货价格更新", extra={"inst_id": inst_id, "price": price})
        return price

    # --------------------- 查询合约信息 -----------------
    def get_contract_info(self, symbol: str, is_spot: bool) -> dict:
        params = {"instType": "SPOT" if is_spot else "SWAP", "instId": symbol}
        response = self._request("GET", "/api/v5/public/instruments", params)
        return response

    # ------------------- 账户接口 -------------------
    def get_usdt_balance(self) -> float:
        """获取USDT可用余额"""
        response = self._request("GET", "/api/v5/account/balance")
        try:
            for item in response.get('data', []):
                for detail in item.get('details', []):
                    if detail['ccy'] == 'USDT':
                        balance = float(detail['availBal'])
                        self.logger.info("账户余额查询", extra={"usdt_balance": balance})
                        return balance
            return 0.0
        except Exception as e:
            self.logger.error(f"获取USDT余额异常: {e}")
            return 0.0
            
    def get_account_balance(self) -> dict:
        """获取完整的账户余额信息"""
        response = self._request("GET", "/api/v5/account/balance")
        self.logger.info("获取完整账户余额信息")
        return response
        
    def get_tickers(self) -> list:
        """获取所有交易对的ticker信息"""
        response = self._request("GET", "/api/v5/market/tickers", {"instType": "SWAP"})
        self.logger.info("获取所有交易对ticker信息")
        return response.get('data', [])

    # ------------------- 工具函数 -------------------
    def calculate_position_size(self, inst_id: str, is_spot: bool, target_usdt: float, target_leverage: float = 0) -> float:
        """统一计算现货或合约头寸数量"""
        # 合并获取合约信息和价格
        contract_info = self.get_contract_info(inst_id, is_spot)["data"][0]
        mark_price = self.get_spot_price(inst_id)
        
        # 参数解析
        if not is_spot:
            ct_val = float(contract_info['ctVal'] if contract_info['ctVal'] else 0)  # 合约面值
        lot_size = float(contract_info['lotSz'])  # 最小交易单位
        
        if '.' in contract_info['lotSz']:
            precision = contract_info['lotSz'].split('.')[1].find('1') + 1
        else:
            precision = 0

        if is_spot:
            # 现货数量 = USDT分配量 / 标记价格
            raw_qty = target_usdt / mark_price
        else:
            # 合约张数 = (USDT保证金 * 杠杆) / (面值 * 标记价格) 暂时不用累计杠杆 杠杆是固定的 这儿给出总持仓U 换算成总张数
            raw_qty = target_usdt / (ct_val * mark_price)
        # 根据规则圆整
        adjusted_qty = round(raw_qty / lot_size) * lot_size  # 按最小交易单位圆整
        adjusted_qty = round(adjusted_qty, precision)
        return max(adjusted_qty, lot_size)  # 不低于最小交易量

    def cancel_order(self, inst_id: str, order_id: str) -> dict:
        """取消订单"""
        params = {
            "instId": inst_id,
            "ordId": order_id
        }
        self.logger.info("取消订单", extra={"inst_id": inst_id, "order_id": order_id})
        return self._request("POST", "/api/v5/trade/cancel-order", params)
        
    def get_order_details(self, inst_id: str, order_id: str) -> dict:
        """查询订单详情"""
        params = {
            "instId": inst_id,
            "ordId": order_id
        }
        response = self._request("GET", "/api/v5/trade/order", params)
        self.logger.debug("查询订单详情", extra={"inst_id": inst_id, "order_id": order_id})
        return response
        
    def get_positions(self, inst_type: str = "SWAP") -> list:
        """查询当前持仓"""
        params = {"instType": inst_type}
        response = self._request("GET", "/api/v5/account/positions", params)
        positions = response.get('data', [])
        self.logger.info("查询持仓信息", extra={"inst_type": inst_type, "position_count": len(positions)})
        return positions
        
    def set_leverage(self, inst_id: str, leverage: int, mgnMode: str = "cross") -> dict:
        """设置杠杆倍数"""
        params = {
            "instId": inst_id,
            "lever": str(leverage),
            "mgnMode": mgnMode
        }
        self.logger.info("设置杠杆", extra={"inst_id": inst_id, "leverage": leverage, "mode": mgnMode})
        return self._request("POST", "/api/v5/account/set-leverage", params)
        
    def get_kline_data(self, inst_id: str, bar: str = "1m", limit: int = 100) -> list:
        """获取K线数据
        
        Args:
            inst_id: 交易对/合约ID
            bar: K线周期，如 1m, 5m, 15m, 30m, 1H, 2H, 4H, 6H, 12H, 1D, 1W, 1M
            limit: 返回的K线数量，最大值为300
            
        Returns:
            K线数据列表，每个元素包含：[时间戳, 开盘价, 最高价, 最低价, 收盘价, 成交量, 成交额]
        """
        params = {
            "instId": inst_id,
            "bar": bar,
            "limit": str(min(limit, 300))  # API限制最大300条
        }
        try:
            response = self._request("GET", "/api/v5/market/candles", params)
            candles = response.get('data', [])
            
            # 添加更详细的日志
            log_data = {
                "inst_id": inst_id, 
                "bar": bar, 
                "count": len(candles)
            }
            
            # 添加数据样例，但避免可能的索引错误
            if candles and len(candles) > 0:
                log_data["data_sample"] = candles[0]
            else:
                log_data["data_sample"] = "No data"
                
            self.logger.debug("获取K线数据", extra=log_data)
            
            # 特别处理错误情况
            if not candles or len(candles) == 0:
                self.logger.warning(f"获取K线数据为空: {inst_id}, {bar}, 响应: {response}")
            
            return candles
        except Exception as e:
            self.logger.error(f"获取K线数据异常: {inst_id}, {bar}, 错误: {e}", exc_info=True)
            return []
    
    def get_open_interest_history(self, inst_id: str, bar: str = "5m", limit: int = 100) -> list:
        """获取持仓量历史数据
        
        Args:
            inst_id: 交易对/合约ID
            bar: 数据周期，如 5m, 1H, 1D
            limit: 返回的数据数量，最大值为100
            
        Returns:
            持仓量数据列表
        """
        params = {
            "instId": inst_id,
            "period": bar,
            "limit": str(min(limit, 100))  # API可能有限制
        }
        try:
            response = self._request("GET", "/api/v5/public/open-interest-archive", params)
            data = response.get('data', [])
            
            # 添加更详细的日志
            log_data = {
                "inst_id": inst_id, 
                "bar": bar, 
                "count": len(data)
            }
            
            # 添加数据样例，但避免可能的索引错误
            if data and len(data) > 0:
                log_data["data_sample"] = data[0]
            else:
                log_data["data_sample"] = "No data"
                
            self.logger.debug("获取持仓量历史数据", extra=log_data)
            
            # 特别处理错误情况
            if not data or len(data) == 0:
                self.logger.warning(f"获取持仓量历史数据为空: {inst_id}, {bar}, 响应: {response}")
            
            # API返回的结构大概是 {'ts': '1634841600000', 'oi': '12345.67', 'oiCcy': '123.45', 'instType': 'SWAP', 'instId': 'BTC-USDT-SWAP'}
            return data
        except Exception as e:
            self.logger.error(f"获取持仓量历史数据异常: {inst_id}, {bar}, 错误: {e}", exc_info=True)
            return []
    
    def get_open_interest(self, inst_id: str) -> dict:
        """获取当前持仓量数据
        
        Args:
            inst_id: 交易对/合约ID
            
        Returns:
            当前持仓量数据
        """
        params = {
            "instId": inst_id
        }
        try:
            response = self._request("GET", "/api/v5/public/open-interest", params)
            data_list = response.get('data', [])
            
            # 获取第一个数据项，如果存在的话
            data = data_list[0] if data_list and len(data_list) > 0 else {}
            
            # 添加更详细的日志
            log_data = {
                "inst_id": inst_id,
                "has_data": bool(data)
            }
            
            if data:
                log_data["data"] = data
                
            self.logger.debug("获取当前持仓量数据", extra=log_data)
            
            # 特别处理错误情况
            if not data:
                self.logger.warning(f"获取当前持仓量数据为空: {inst_id}, 响应: {response}")
            
            return data
        except Exception as e:
            self.logger.error(f"获取当前持仓量数据异常: {inst_id}, 错误: {e}", exc_info=True)
            return {}

    def batch_orders(self, orders: list) -> dict:
        """批量下单
        
        Args:
            orders: 订单列表，每个订单为一个字典，包含instId, tdMode, side等字段
            
        Returns:
            批量下单结果
        """
        self.logger.info("批量下单", extra={"order_count": len(orders)})
        return self._request("POST", "/api/v5/trade/batch-orders", {"orders": orders})


