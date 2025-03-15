# -*- coding: utf-8 -*-
import sys
import time
import argparse
import os
from typing import List, Optional, Dict, Any
from datetime import datetime
import urllib3
from tabulate import tabulate  # 用于美化命令行输出
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 添加项目根目录到路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

# 导入我们的交易所适配器和日志配置
from src.exchange.okex.trader import OKExTrader
from src.common.logger import configure_logger
from src.common.config_loader import get_app_config
from src.common.order_utils import calculate_order_size  # 导入通用的 calculate_order_size 函数

# 加载配置
APP_NAME = "trader_tools"
CONFIG = get_app_config(APP_NAME)

# 配置日志
configure_logger(APP_NAME, CONFIG.get('logging', {}).get('level', 'INFO'))

# ------------------------ 交易类型定义 ------------------------
# 使用字典替代枚举，更灵活且易于扩展
ORDER_TYPES = {
    "market": {"value": "market", "description": "市价单"},
    "optimal5": {"value": "optimal5", "description": "最优5档"},
    "limit": {"value": "limit", "description": "限价单"}
}

SIDES = {
    "buy": {"value": "buy", "description": "买入"},
    "sell": {"value": "sell", "description": "卖出"}
}

MODES = {
    "order": {"value": "order", "description": "下单"},
    "close": {"value": "close", "description": "平仓"},
    "balance": {"value": "balance", "description": "查询余额"},
    "ipo": {"value": "ipo", "description": "打新"}
}

# 从配置文件加载交易标的
SYMBOLS = {}
for symbol_info in CONFIG.get('symbols', []):
    name = symbol_info['name']
    SYMBOLS[name.replace('-USDT-SWAP', '').lower()] = {
        "value": name,
        "description": symbol_info.get('description', name),
        "min_amount": symbol_info.get('min_amount', 5)
    }

# ------------------------ 核心交易类 ------------------------
class OKExTradingTool:
    """OKEx交易工具类，提供批量下单、平仓等常用交易操作的封装"""
    
    def __init__(
        self, 
        config: Dict[str, Any],
        trading_symbols: List[str]
    ):
        # 使用OKExTrader作为底层实现
        self.trader = OKExTrader(config['app_name'], config['exchange'])
        
        # 保存交易标的
        self.trading_symbols = trading_symbols
        
        # 验证标的合法性
        valid_symbols = {symbol_info['value'] for symbol_info in SYMBOLS.values()}
        if not set(self.trading_symbols).issubset(valid_symbols):
            raise ValueError("包含非法的交易标的")

    def get_contract_info(self, symbol: str) -> dict:
        """获取合约信息"""
        return self.trader.get_contract_info(symbol, False)["data"][0]

    def calculate_order_size(self, symbol: str, margin_usdt: float, leverage: int) -> str:
        """
        根据保证金和杠杆计算可下单张数
        
        Args:
            symbol: 交易标的
            margin_usdt: 保证金金额
            leverage: 杠杆倍数
            
        Returns:
            str: 可下单张数
        """
        # 使用通用的 calculate_order_size 函数
        size, _ = calculate_order_size(
            trader=self.trader,
            symbol=symbol,
            position_usdt=margin_usdt,
            unit_type='quote',  # 使用 quote 作为单位类型，即 USDT
            leverage=leverage,
            side='buy'  # 这里 side 参数对计算大小没有影响
        )
        return f"{size:.8f}".rstrip('0').rstrip('.')  # 去除无效小数位

    # ------------------------ 交易方法 ------------------------
    def place_batch_orders(
        self,
        price_type: str,
        margin_usdt: float,
        leverage: int,
        side: str
    ) -> dict:
        """批量下单（带错误隔离与日志追踪）"""
        results = []
        
        # 对每个标的下单
        for symbol in self.trading_symbols:
            # 设置杠杆（单个设置）
            leverage_res = self.trader.set_leverage(symbol, leverage)
            
            # 校验杠杆设置结果
            if leverage_res.get("code") != "0":
                error_msg = leverage_res.get("data", [{}])[0].get("sMsg", "Unknown error")
                self.trader.logger.error(f"杠杆设置失败: {symbol} | {error_msg}")
                continue  # 跳过当前标的

            # 计算下单数量
            sz = self.calculate_order_size(symbol, margin_usdt, leverage)

            # 下单
            pos_side = "long" if side == "buy" else "short"
            order_res = self.trader.swap_order(
                inst_id=symbol,
                side=side,
                pos_side=pos_side,
                sz=float(sz),
                ord_type=price_type
            )
            
            if order_res.get("code") == "0":
                results.append(order_res)
                self.trader.logger.info(f"订单提交成功: {symbol} margin_usdt:{margin_usdt} leverage:{leverage}")
            else:
                error_data = order_res.get("data", [{}])[0]
                self.trader.logger.error(
                    f"订单失败: {symbol} | Code: {error_data.get('sCode')} | Msg: {error_data.get('sMsg')}"
                )
        
        self.trader.logger.info(f"批量下单完成，成功{len(results)}/{len(self.trading_symbols)}")
        return results

    def close_all_positions(self, price_type: str) -> dict:
        """批量平仓（带异常捕获）"""
        positions = self.trader.get_positions()
        results = []
        
        for pos in positions:
            symbol = pos.get('instId', '')
            if symbol not in self.trading_symbols:
                continue
            
            try:
                # 使用OKExTrader的swap_order方法平仓
                side = "sell" if pos['posSide'] == "long" else "buy"
                pos_side = "long" if pos['posSide'] == "long" else "short"
                
                res = self.trader.swap_order(
                    inst_id=symbol,
                    side=side,
                    pos_side=pos_side,
                    sz=float(pos['availPos']),
                    ord_type=price_type
                )
                
                if res.get("code") == "0":
                    results.append(res)
                    self.trader.logger.info(f"平仓成功: {symbol}")
                else:
                    error_data = res.get("data", [{}])[0]
                    self.trader.logger.error(f"平仓失败: {symbol} | {error_data.get('sMsg')}")
            except KeyError as e:
                self.trader.logger.error(f"仓位解析异常: {symbol} | 缺失字段 {str(e)}")
        
        return results

    def get_account_balance(self) -> dict:
        """查询账户资产"""
        # 直接使用OKExTrader的_request方法
        return self.trader._request("GET", "/api/v5/account/balance")

    def subscribe_to_ipo(
        self, 
        symbol: str,
        usdt_amount: float,
        max_retries: int = 10,
        is_spot: bool = True
    ) -> dict:
        """打新币（带重试机制）"""
        # 使用OKExTrader的spot_order方法
        for _ in range(max_retries):
            if is_spot:
                result = self.trader.spot_order(
                    inst_id=symbol,
                    side="buy",
                    sz=usdt_amount
                )
            else:
                result = self.trader.swap_order(
                    inst_id=symbol,
                    side="buy",
                    pos_side="long",
                    sz=usdt_amount
                )
                
            if result.get('code') == '0':
                self.trader.logger.info(f"打新成功: {symbol}")
                return result
            time.sleep(0.1)
        self.trader.logger.error(f"打新失败: {symbol}")
        return result

# ------------------------ 命令行交互 ------------------------
def print_available_options():
    """打印可用的选项，美化输出"""
    print("\n=== 可用的交易模式 ===")
    mode_table = [[key, info["description"]] for key, info in MODES.items()]
    print(tabulate(mode_table, headers=["模式", "描述"], tablefmt="grid"))
    
    print("\n=== 可用的交易方向 ===")
    side_table = [[key, info["description"]] for key, info in SIDES.items()]
    print(tabulate(side_table, headers=["方向", "描述"], tablefmt="grid"))
    
    print("\n=== 可用的价格类型 ===")
    type_table = [[key, info["description"]] for key, info in ORDER_TYPES.items()]
    print(tabulate(type_table, headers=["类型", "描述"], tablefmt="grid"))
    
    print("\n=== 可用的交易标的 ===")
    symbol_table = [[key, info["value"], info["description"], info["min_amount"]] 
                   for key, info in SYMBOLS.items()]
    print(tabulate(symbol_table, headers=["简称", "标的", "描述", "最小金额"], tablefmt="grid"))

def parse_args():
    parser = argparse.ArgumentParser(description="OKEx量化交易脚本")
    
    # 添加帮助选项
    parser.add_argument('--help-options', action='store_true', help="显示所有可用的选项")
    
    # 添加模式参数
    parser.add_argument('--mode', choices=MODES.keys(), help="交易模式")
    
    # 添加标的参数，使用简称
    parser.add_argument('--symbols', nargs='+', choices=SYMBOLS.keys(), help="标的简称列表")
    
    # 其他参数
    parser.add_argument('--amount', type=float, help="USDT保证金数量")
    parser.add_argument('--leverage', type=int, default=CONFIG.get('trading', {}).get('default_leverage', 3), help="杠杆倍数")
    parser.add_argument('--side', choices=SIDES.keys(), help="买卖方向")
    parser.add_argument('--price-type', dest='price_type', choices=ORDER_TYPES.keys(), help="价格类型")
    
    args = parser.parse_args()
    
    # 如果请求帮助选项，打印选项并退出
    if args.help_options:
        print_available_options()
        sys.exit(0)
    
    # 验证必要参数
    if args.mode is None:
        parser.error("必须指定 --mode 参数")
    
    if args.symbols is None:
        parser.error("必须指定 --symbols 参数")
    
    # 根据模式验证其他参数
    if args.mode == "order":
        if args.amount is None:
            parser.error("下单模式必须指定 --amount 参数")
        if args.side is None:
            parser.error("下单模式必须指定 --side 参数")
        if args.price_type is None:
            parser.error("下单模式必须指定 --price-type 参数")
    
    elif args.mode == "close":
        if args.price_type is None:
            parser.error("平仓模式必须指定 --price-type 参数")
    
    elif args.mode == "ipo":
        if args.amount is None:
            parser.error("打新模式必须指定 --amount 参数")
    
    return args

def main():
    args = parse_args()
    
    # 将简称转换为完整标的名称
    trading_symbols = [SYMBOLS[symbol]["value"] for symbol in args.symbols]
    
    # 初始化交易实例
    trader = OKExTradingTool(
        config=CONFIG,
        trading_symbols=trading_symbols
    )
    
    if args.mode == "order":
        trader.place_batch_orders(
            price_type=ORDER_TYPES[args.price_type]["value"],
            margin_usdt=args.amount,
            leverage=args.leverage,
            side=SIDES[args.side]["value"]
        )
    elif args.mode == "close":
        trader.close_all_positions(ORDER_TYPES[args.price_type]["value"])
    elif args.mode == "balance":
        balance = trader.get_account_balance()
        total_equity = float(balance["data"][0]["totalEq"])
        trader.trader.logger.info(f"当前交易账户资产: {total_equity}")
        usdt_available = balance.get("data", [{}])[0].get("details", [{}])[0].get("availBal", "0")
        trader.trader.logger.info(f"可用USDT余额: {usdt_available}")
        
        # 打印到控制台，更友好的展示
        print(f"\n=== 账户资产 ===")
        print(f"总资产: {total_equity} USDT")
        print(f"可用USDT: {usdt_available} USDT")
    elif args.mode == "ipo":
        trader.subscribe_to_ipo(
            symbol=trading_symbols[0],
            usdt_amount=args.amount
        )

if __name__ == "__main__":
    main()
