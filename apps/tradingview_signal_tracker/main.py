#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
TradingView信号追踪器

功能：
1. 启动WebSocket服务器，接收TradingView的信号
2. 根据信号执行开仓、平仓等操作
3. 支持止盈止损和追踪止损
4. 持久化仓位信息，避免重启丢失
"""

import os
import sys
import yaml
import asyncio
import logging
import signal
from pathlib import Path
from typing import Dict, Any

# 添加项目根目录到PYTHONPATH
sys.path.append(str(Path(__file__).parents[2]))

from src.common.logger import configure_logger
from src.common.event_loop import AsyncEventLoop
from src.common.data_cache import DataCache, OKExDataCache
from src.common.position_manager import PositionManager
from src.exchange.okex.trader import OKExTrader

from apps.tradingview_signal_tracker.signal_processor import SignalProcessor
from apps.tradingview_signal_tracker.market_client import MarketDataSubscriber
from apps.tradingview_signal_tracker.strategy import TradingViewSignalStrategy

def load_config() -> Dict[str, Any]:
    """加载配置文件"""
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

class TradingViewSignalApp:
    """TradingView信号追踪应用"""
    def __init__(self, config: Dict[str, Any]):
        """
        初始化应用
        
        Args:
            config: 配置信息
        """
        self.config = config
        self.app_name = config['app_name']
        
        # 配置日志
        log_config = config.get('logging', {})
        configure_logger(
            self.app_name, 
            log_config.get('level', 'INFO'),
            log_config.get('file', f"{self.app_name}.log"),
            log_config.get('output_targets', ["file", "console"])
        )
        self.logger = logging.getLogger(self.app_name)
        
        # 初始化事件循环
        self.event_loop = AsyncEventLoop()
        
        # 初始化数据缓存
        self._init_cache()
        
        # 初始化组件
        self._init_components()
        
        # 注册信号处理
        self._register_signal_handlers()
        
        self.logger.info(f"{self.app_name}初始化完成")
        
    def _init_cache(self):
        """初始化数据缓存"""
        # 根据交易所类型选择对应的缓存实现
        exchange_type = self.config.get('exchange', {}).get('type', 'okex').lower()
        
        if exchange_type == 'okex':
            self.cache = OKExDataCache()
            self.logger.info("使用OKEx数据缓存")
        else:
            self.cache = DataCache(exchange_name=exchange_type.capitalize())
            self.logger.info(f"使用通用数据缓存: {exchange_type}")
        
    def _init_components(self):
        """初始化各个组件"""
        # 初始化交易执行器
        self.trader = OKExTrader(self.app_name, self.config['exchange'])
        
        # 初始化仓位管理器
        self.position_mgr = PositionManager(self.app_name)
        
        # 初始化市场数据订阅器
        self.market_client = MarketDataSubscriber(self.cache, self.config)
        
        # 初始化策略
        self.strategy = TradingViewSignalStrategy(
            self.trader, 
            self.position_mgr, 
            self.market_client, 
            self.config
        )
        
        # 初始化信号处理器
        self.signal_processor = SignalProcessor(
            self.strategy.handle_signal, 
            self.config,
            self.cache
        )
        
    def _setup_tasks(self):
        """设置定时任务"""
        # 添加信号处理服务任务
        self.event_loop.add_task(
            self.signal_processor.start,
            immediate=True
        )
        
        # 添加市场数据订阅任务
        self.event_loop.add_task(
            self.market_client.start,
            immediate=True
        )
        
        # 添加持仓监控任务
        self.event_loop.add_task(
            self.strategy.monitor_positions,
            interval=30  # 每30秒监控一次持仓
        )
        
        # 添加缓存监控任务
        self.event_loop.add_task(
            self._monitor_cache,
            interval=60  # 每60秒监控一次缓存
        )
        
    async def _monitor_cache(self):
        """监控缓存数据健康状态"""
        try:
            # 获取BTC价格作为健康检查
            exchange_type = self.config.get('exchange', {}).get('type', 'okex').lower()
            default_symbol = self.config.get('strategy', {}).get('default_symbols', ["BTC-USDT-SWAP"])[0]
            
            if exchange_type == 'okex':
                btc_price = await self.cache.get_mark_price(default_symbol)
            else:
                # 通用方式获取价格
                mark_price_data = await self.cache.get("mark-price", default_symbol)
                btc_price = float(mark_price_data.get('price', 0.0))
            
            # 获取持仓摘要
            position_summary = self.strategy.get_position_summary()
            
            # 记录监控信息
            self.logger.info("系统监控", extra={
                f"{default_symbol}价格": btc_price,
                "缓存条目数": len(self.cache),
                "持仓数量": position_summary["position_count"],
                "持仓详情": position_summary["positions"]
            })
        except Exception as e:
            self.logger.exception(f"监控异常: {e}")
            
    def _register_signal_handlers(self):
        """注册系统信号处理函数"""
        # 注册SIGINT和SIGTERM信号处理
        for sig in [signal.SIGINT, signal.SIGTERM]:
            signal.signal(sig, self._handle_shutdown)
            
    def _handle_shutdown(self, signum, frame):
        """处理系统关闭信号"""
        self.logger.info(f"接收到关闭信号: {signum}")
        self.stop()
        
    async def _shutdown(self):
        """关闭应用"""
        self.logger.info("正在关闭应用...")
        
        # 关闭信号处理器
        try:
            await self.signal_processor.stop()
        except Exception as e:
            self.logger.error(f"关闭信号处理器异常: {e}")
            
        # 关闭市场数据订阅
        try:
            await self.market_client.stop()
        except Exception as e:
            self.logger.error(f"关闭市场数据订阅异常: {e}")
            
        self.logger.info("应用已关闭")
        
    def run(self):
        """运行应用"""
        self.logger.info("启动应用...")
        self._setup_tasks()
        self.event_loop.run_forever()
        
    def stop(self):
        """停止应用"""
        self.logger.info("正在停止应用...")
        
        # 添加关闭任务
        self.event_loop.add_task(
            self._shutdown,
            immediate=True
        )
        
        # 停止事件循环
        self.event_loop.stop()

def main():
    """主函数"""
    try:
        # 加载配置
        config = load_config()
        
        # 创建并运行应用
        app = TradingViewSignalApp(config)
        app.run()
    except KeyboardInterrupt:
        print("用户中断，程序退出")
    except Exception as e:
        print(f"程序异常: {e}")
        import traceback
        traceback.print_exc()
        
if __name__ == "__main__":
    main() 