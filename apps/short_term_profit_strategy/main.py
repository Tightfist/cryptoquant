#!/usr/bin/env python3
"""
短线逐利策略的主入口

该模块负责加载配置、初始化策略并启动交易框架。
"""

import os
import sys
import json
import logging
import asyncio
import signal
from typing import Dict, Any

# 添加项目根目录到Python路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from src.common.logger import configure_logger
from src.common.trading_framework import TradingFramework
from src.common.config_loader import get_app_config
from src.common.event_loop import AsyncEventLoop
from apps.short_term_profit_strategy.short_term_profit_strategy import ShortTermProfitStrategy

# 应用名称
APP_NAME = "short_term_profit_strategy"

def main():
    """主函数"""
    try:
        # 加载配置
        config = get_app_config(APP_NAME)
        
        # 创建并运行应用
        app = ShortTermProfitApp(config)
        app.run()
    except KeyboardInterrupt:
        print("用户中断，程序退出")
    except Exception as e:
        print(f"程序异常: {e}")
        import traceback
        traceback.print_exc()

class ShortTermProfitApp:
    """短线逐利策略应用"""
    
    def __init__(self, config: Dict[str, Any]):
        """
        初始化应用
        
        Args:
            config: 配置信息
        """
        self.config = config
        self.app_name = config.get('app_name', APP_NAME)
        
        # 配置日志
        configure_logger(self.app_name)
        self.logger = logging.getLogger(self.app_name)
        self.logger.info("初始化短线逐利策略应用")
        
        # 创建事件循环
        self.event_loop = AsyncEventLoop()
        
        # 初始化框架
        self.framework = TradingFramework(
            app_name=self.app_name,
            strategy_class=ShortTermProfitStrategy,
            config=config
        )
        
        # 注册信号处理
        self._register_signal_handlers()
        
        self.logger.info(f"{self.app_name}初始化完成")
    
    def _register_signal_handlers(self):
        """注册信号处理器"""
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)
    
    def _handle_shutdown(self, signum, frame):
        """处理关闭信号"""
        self.logger.info(f"收到信号 {signum}，准备关闭...")
        self.stop()
    
    async def _shutdown(self):
        """关闭框架"""
        self.logger.info("正在关闭框架...")
        try:
            # 停止策略监控
            await self.framework.strategy.stop_monitoring()
            self.logger.info("策略监控已停止")
        except Exception as e:
            self.logger.error(f"停止策略监控时发生异常: {e}")
    
    async def _run_framework_forever(self):
        """运行框架主循环"""
        # 启动市场监控
        await self.framework.strategy.start_monitoring()
        
        # 启动框架主循环
        await self.framework.run_forever()
    
    def _setup_tasks(self):
        """设置任务"""
        # 添加框架运行任务
        self.event_loop.add_task(
            self._run_framework_forever,
            immediate=True
        )
    
    def run(self):
        """运行应用"""
        self.logger.info("启动短线逐利策略应用...")
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

if __name__ == "__main__":
    main() 