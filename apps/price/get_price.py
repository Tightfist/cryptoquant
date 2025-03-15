# -*- coding: utf-8 -*-
import sys
import time
import os
import datetime
import logging
from typing import Dict, Any, List
from prettytable import PrettyTable
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 添加项目根目录到路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

# 导入交易所适配器和日志配置
from src.exchange.okex.trader import OKExTrader
from src.common.logger import configure_logger
from src.common.config_loader import get_app_config

# 加载配置
APP_NAME = "price"
CONFIG = get_app_config(APP_NAME)

# 配置日志 - 这会创建一个单例日志记录器，所有组件都会使用它
configure_logger(
    APP_NAME, 
    CONFIG.get('logging', {}).get('level', 'INFO'),
    CONFIG.get('logging', {}).get('file', 'trading.log'),
    CONFIG.get('logging', {}).get('output_targets')
)

# 获取应用专用的日志记录器 - 这会获取到上面配置的同一个日志记录器
logger = logging.getLogger(APP_NAME)

# 获取交易标的列表
INSTRUMENTS = CONFIG.get('instruments', [])

# 资产查询参数
ASSET_PARAMS = CONFIG.get('asset_params', {'ccy': 'USD'})

# 刷新间隔
REFRESH_INTERVAL = CONFIG.get('refresh_interval', 30)

# 存储股票数据的映射
stock_data_map = {}

class StockData:
    """存储和处理股票数据的类"""
    
    def __init__(self):
        self.data = {
            'volume_sum': 0,
            'prev_close': None,
            'open': None,
            'prev_volume': None,
            'date': None
        }
    
    def update_data(self, date: str, volume: float, prev_close: float, open_price: float, prev_volume: float) -> None:
        """更新股票数据"""
        self.data['volume_sum'] = volume
        self.data['prev_close'] = prev_close
        self.data['open'] = open_price
        self.data['prev_volume'] = prev_volume
        self.data['date'] = date
    
    def is_updated(self) -> bool:
        """检查数据是否已更新到最新日期"""
        current_date = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        if self.data['date'] is None:
            return False
        else:
            return self.data['date'] == current_date
    
    def get_volume_sum(self) -> float:
        """获取成交量总和"""
        return self.data['volume_sum']
    
    def get_prev_ZF(self) -> float:
        """获取前一天的涨跌幅"""
        return (self.data['prev_close'] - self.data['open']) / self.data['open'] * 100
    
    def get_prev_volume(self) -> float:
        """获取前一天的成交量"""
        return self.data['prev_volume']


class PriceTool:
    """价格查询工具类"""
    
    def __init__(self, config: Dict[str, Any]):
        """初始化价格查询工具"""
        # 创建OKExTrader实例，传入应用名称和配置
        # 这样OKExTrader会使用与应用程序相同的日志记录器
        # 确保exchange配置中包含logging信息
        exchange_config = config['exchange'].copy()
        if 'logging' in config:
            exchange_config['logging'] = config['logging']
        
        self.trader = OKExTrader(config['app_name'], exchange_config)
        self.stock_data_map = {}
        self.logger = logging.getLogger(config['app_name'])  # 使用与OKExTrader相同的日志记录器
    
    def get_volume(self, inst_id: str, bar: str) -> StockData:
        """获取交易量数据"""
        # 如果没有该标的的数据，创建一个新的
        if inst_id not in self.stock_data_map:
            self.stock_data_map[inst_id] = StockData()
        
        stock_data = self.stock_data_map[inst_id]
        
        # 如果数据未更新，则获取最新数据
        if not stock_data.is_updated():
            params = {
                "instId": inst_id,
                "bar": bar,
                "limit": 5
            }
            response = self.trader._request("GET", "/api/v5/market/history-candles", params)
            
            if response.get('code') == '0' and 'data' in response:
                data = response['data']
                volume_sum = 0
                
                for item in data:
                    volume_sum += float(item[5])
                
                pre_data = data[0]
                volume_sum = volume_sum - float(pre_data[5])
                
                # 更新股票数据
                date = datetime.datetime.fromtimestamp(int(pre_data[0]) / 1000).strftime('%Y-%m-%d')
                stock_data.update_data(
                    date=date,
                    volume=volume_sum,
                    prev_close=float(pre_data[4]),
                    open_price=float(pre_data[1]),
                    prev_volume=float(pre_data[5])
                )
                self.logger.debug(f"更新交易量数据: {inst_id}", extra={
                    "date": date,
                    "volume_sum": volume_sum
                })
            else:
                self.logger.error(f"获取K线数据失败: {inst_id}", extra={"response": response})
        
        return stock_data
    
    def get_asset(self) -> None:
        """获取资产信息并打印"""
        response = self.trader._request("GET", "/api/v5/asset/asset-valuation", ASSET_PARAMS)
        
        if response.get('code') == '0' and 'data' in response:
            # 提取数据
            details = response['data'][0]['details']
            total_bal = response['data'][0]['totalBal']
            
            # 记录日志
            self.logger.info("资产查询成功", extra={
                "total_balance": total_bal,
                "earn": details.get('earn', '0'),
                "funding": details.get('funding', '0'),
                "trading": details.get('trading', '0')
            })
            
            # 控制台输出
            table = PrettyTable()
            table.field_names = ["总资产(USD)", "理财", "资金", "交易"]
            table.add_row([
                total_bal,
                details.get('earn', '0'),
                details.get('funding', '0'),
                details.get('trading', '0')
            ])
            print("\n=== 资产信息 ===")
            print(table)
        else:
            self.logger.error("资产查询失败", extra={"response": response})
            print("获取资产信息失败，请查看日志了解详情")
    
    def get_market_data(self) -> None:
        """获取行情数据并打印"""
        # 创建表格用于控制台输出
        table = PrettyTable()
        table.field_names = ["交易对", "最新价", "24h高", "24h低", "涨跌幅", "24h成交量", "昨日涨跌", "昨日成交量", "量比"]
        
        # 记录成功获取数据的标的数量
        success_count = 0
        
        for inst_id in INSTRUMENTS:
            # 获取行情数据
            params = {"instId": inst_id}
            response = self.trader._request("GET", "/api/v5/market/ticker", params)
            
            if response.get('code') == '0' and 'data' in response:
                data = response['data'][0]
                
                # 获取历史成交量数据
                stock_data = self.get_volume(inst_id, "1D")
                
                # 计算涨跌幅
                zf = round((float(data['last']) - float(data['open24h'])) / float(data['open24h']) * 100, 2)
                pre_zf = round(stock_data.get_prev_ZF(), 2)
                
                # 计算量比
                value_ratio = round((stock_data.get_volume_sum() + float(data['vol24h'])) / stock_data.get_volume_sum() * 0.8, 2)
                
                # 记录日志
                self.logger.info(f"行情数据: {inst_id}", extra={
                    "price": data['last'],
                    "change_percent": zf,
                    "volume": data['vol24h'],
                    "prev_change": pre_zf,
                    "volume_ratio": value_ratio
                })
                
                # 添加到表格
                table.add_row([
                    data['instId'],
                    data['last'],
                    data['high24h'],
                    data['low24h'],
                    f"{zf}%",
                    data['vol24h'],
                    f"{pre_zf}%",
                    stock_data.get_prev_volume(),
                    value_ratio
                ])
                
                success_count += 1
            else:
                self.logger.error(f"获取行情数据失败: {inst_id}", extra={"response": response})
        
        # 控制台输出
        if success_count > 0:
            print("\n=== 行情数据 ===")
            print(table)
        else:
            print("获取行情数据失败，请查看日志了解详情")
    
    def get_all_assets(self) -> None:
        """获取所有资产信息"""
        response = self.trader._request("GET", "/api/v5/account/balance")
        
        if response.get('code') == '0':
            self.logger.info("获取所有资产成功", extra={"data": response.get('data', [])})
            
            # 控制台输出
            print("\n=== 所有资产信息 ===")
            print("资产详情已记录到日志文件")
        else:
            self.logger.error("获取所有资产失败", extra={"response": response})
            print("获取所有资产信息失败，请查看日志了解详情")


def main():
    """主函数"""
    # 创建价格查询工具
    price_tool = PriceTool(CONFIG)
    
    # 获取日志配置
    log_file = CONFIG.get('logging', {}).get('file', 'trading.log')
    output_targets = CONFIG.get('logging', {}).get('output_targets', ['file', 'console'])
    
    # 显示程序启动信息
    print(f"价格查询工具已启动，将每 {REFRESH_INTERVAL} 秒刷新一次数据")
    
    # 根据输出目标显示日志文件位置
    if 'file' in output_targets:
        print(f"日志文件位置: logs/{APP_NAME}/{log_file}")
    
    print("按 Ctrl+C 停止程序")
    
    # 记录程序启动日志
    logger.info("价格查询工具已启动", extra={
        "refresh_interval": REFRESH_INTERVAL,
        "instruments": INSTRUMENTS,
        "output_targets": output_targets
    })
    
    try:
        while True:
            # 获取资产信息
            price_tool.get_asset()
            
            # 获取行情数据
            price_tool.get_market_data()
            
            # 等待刷新间隔
            print(f"\n等待 {REFRESH_INTERVAL} 秒后刷新...")
            time.sleep(REFRESH_INTERVAL)
    except KeyboardInterrupt:
        print("\n程序已停止")
        logger.info("程序已停止")
    except Exception as e:
        error_msg = str(e)
        print(f"发生错误: {error_msg}")
        logger.error(f"程序异常", exc_info=True, extra={"error": error_msg})


if __name__ == "__main__":
    main()
