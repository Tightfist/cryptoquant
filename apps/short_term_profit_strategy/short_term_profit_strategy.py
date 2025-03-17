"""
短线逐利策略

该策略专注于捕捉短期价格波动带来的交易机会，基于多因子分析和风险控制机制。
主要功能包括：
1. 标的筛选模块：筛选流动性好、波动性适中的标的
2. 买入信号增强：多因子复合验证
3. 卖出信号优化：主动止盈、固定止损、强制平仓
4. 风险控制系统：通过在信号中携带风控参数，使用仓位管理器中的风控功能
"""

import logging
import json
import time
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, List, Tuple, Optional, Set
import numpy as np

from src.common.trading_framework import BaseStrategy, TradeSignal


class ShortTermProfitStrategy(BaseStrategy):
    """短线逐利策略实现"""
    
    def __init__(self, app_name: str, trader, position_mgr, data_cache, config: Dict[str, Any]):
        """
        初始化短线逐利策略
        
        Args:
            app_name: 应用名称
            trader: 交易执行器
            position_mgr: 仓位管理器
            data_cache: 数据缓存
            config: 配置信息
        """
        super().__init__(app_name, trader, position_mgr, data_cache, config)
        
        # 获取策略配置
        self.strat_config = config.get('strategy', {})
        
        # 标的筛选配置
        self.top_volume_limit = self.strat_config.get('top_volume_limit', 100)
        self.min_atr_pct = self.strat_config.get('min_atr_pct', 0.5)
        self.max_price_change_pct = self.strat_config.get('max_price_change_pct', 15.0)
        
        # 买入信号配置
        self.min_price_increase_pct = self.strat_config.get('min_price_increase_pct', 2.0)
        self.min_position_growth_pct = self.strat_config.get('min_position_growth_pct', 5.0)
        self.volume_increase_ratio = self.strat_config.get('volume_increase_ratio', 3.0)
        
        # 卖出信号配置
        self.fixed_take_profit_pct = self.strat_config.get('fixed_take_profit_pct', 5.0)
        self.fixed_stop_loss_pct = self.strat_config.get('fixed_stop_loss_pct', 3.0)
        self.use_trailing_stop = self.strat_config.get('use_trailing_stop', True)
        self.trailing_distance_pct = self.strat_config.get('trailing_distance_pct', 2.0)
        self.position_decrease_threshold = self.strat_config.get('position_decrease_threshold', 3.0)
        self.price_drop_threshold = self.strat_config.get('price_drop_threshold', 2.0)
        
        # 风险控制配置
        self.cooling_period_minutes = self.strat_config.get('cooling_period_minutes', 30)
        self.max_daily_trades = self.strat_config.get('max_daily_trades', 50)
        self.max_daily_loss_pct = self.strat_config.get('max_daily_loss_pct', 50.0)
        
        # 参数动态调整配置
        self.param_update_hours = self.strat_config.get('param_update_hours', 4)
        
        # 初始化状态变量
        self.last_param_update_time = datetime.now()
        self.daily_starting_balance = None  # 记录当日起始余额
        self.eligible_symbols = set()  # 符合交易条件的标的集合
        
        # 配置仓位管理器的风控
        risk_config = {
            "cooling_period_minutes": self.cooling_period_minutes,
            "max_daily_trades": self.max_daily_trades,
            "max_daily_loss_pct": self.max_daily_loss_pct,
            "enable_cooling_period": True,
            "enable_daily_limit": True,
            "enable_loss_limit": True
        }
        if hasattr(self.position_mgr, 'configure_risk_control'):
            self.position_mgr.configure_risk_control(risk_config)
        
        # 初始化监控任务
        self.monitoring_task = None
        
        self.logger.info("短线逐利策略初始化完成", extra={
            "标的筛选配置": {
                "交易量排名上限": self.top_volume_limit,
                "最小ATR百分比": f"{self.min_atr_pct}%",
                "最大价格变动": f"{self.max_price_change_pct}%"
            },
            "买入信号配置": {
                "最小价格涨幅": f"{self.min_price_increase_pct}%",
                "最小持仓增长率": f"{self.min_position_growth_pct}%",
                "成交量增长比例": self.volume_increase_ratio
            },
            "卖出信号配置": {
                "固定止盈": f"{self.fixed_take_profit_pct}%",
                "固定止损": f"{self.fixed_stop_loss_pct}%",
                "启用追踪止损": self.use_trailing_stop,
                "追踪止损距离": f"{self.trailing_distance_pct}%",
                "持仓下降阈值": f"{self.position_decrease_threshold}%",
                "价格下跌阈值": f"{self.price_drop_threshold}%"
            },
            "风险控制配置": {
                "冷却期": f"{self.cooling_period_minutes}分钟",
                "每日最大交易次数": self.max_daily_trades,
                "最大亏损比例": f"{self.max_daily_loss_pct}%"
            }
        })
    
    async def start_monitoring(self):
        """启动后台监控任务"""
        if self.monitoring_task is None:
            self.monitoring_task = asyncio.ensure_future(self._monitor_loop())
            self.logger.info("已启动市场监控任务")
    
    async def stop_monitoring(self):
        """停止后台监控任务"""
        if self.monitoring_task:
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass
            self.monitoring_task = None
            self.logger.info("已停止市场监控任务")
    
    async def _monitor_loop(self):
        """
        市场监控主循环
        - 每分钟检查一次市场情况
        - 每4小时更新一次策略参数
        - 每天重置交易计数和盈亏统计
        """
        try:
            # 初始化当日起始余额，使用缓存
            balance_result = await self.data_cache.get_account_balance()
            account_info = balance_result["data"]
            
            # 如果是降级数据，记录日志
            if balance_result["is_fallback"]:
                self.logger.warning(f"初始化当日起始余额时使用降级数据，类型: {balance_result['fallback_type']}")
                
            self.daily_starting_balance = self._calculate_total_balance(account_info)
            self.logger.info(f"初始化当日起始余额: {self.daily_starting_balance} USDT")
            
            while True:
                try:
                    current_time = datetime.now()
                    
                    # 检查是否需要更新参数
                    if (current_time - self.last_param_update_time).total_seconds() >= self.param_update_hours * 3600:
                        await self._update_parameters()
                        self.last_param_update_time = current_time
                    
                    # 检查是否需要重置日交易计数（按UTC时间）
                    if current_time.hour == 0 and current_time.minute < 5:
                        await self._reset_daily_counters()
                    
                    # 更新符合条件的标的列表
                    await self._update_eligible_symbols()
                    
                    # 扫描市场寻找交易信号
                    await self._scan_for_signals()
                    
                    # 检查现有持仓是否需要平仓
                    await self._check_exit_signals()
                    
                    # 等待下一分钟
                    await asyncio.sleep(60)
                    
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.logger.exception(f"监控循环发生异常: {e}")
                    await asyncio.sleep(60)  # 出错后等待一分钟再继续
                    
        except asyncio.CancelledError:
            self.logger.info("市场监控任务已取消")
        except Exception as e:
            self.logger.exception(f"市场监控任务异常退出: {e}")
    
    async def _update_parameters(self):
        """动态更新策略参数"""
        self.logger.info("开始动态更新策略参数")
        
        try:
            # 这里可以实现基于市场状况的参数自适应逻辑
            # 例如，根据市场波动率调整阈值
            market_volatility = await self._calculate_market_volatility()
            
            # 根据市场波动调整买入阈值
            if market_volatility > 1.5:  # 高波动
                self.min_price_increase_pct = 2.5
                self.min_position_growth_pct = 6.0
                self.volume_increase_ratio = 3.5
            elif market_volatility < 0.7:  # 低波动
                self.min_price_increase_pct = 1.5
                self.min_position_growth_pct = 4.0
                self.volume_increase_ratio = 2.5
            else:  # 中等波动
                self.min_price_increase_pct = 2.0
                self.min_position_growth_pct = 5.0
                self.volume_increase_ratio = 3.0
            
            # 根据市场波动调整卖出阈值
            if market_volatility > 1.5:
                self.fixed_take_profit_pct = 6.0
                self.fixed_stop_loss_pct = 3.5
                self.trailing_distance_pct = 2.5
            elif market_volatility < 0.7:
                self.fixed_take_profit_pct = 4.0
                self.fixed_stop_loss_pct = 2.5
                self.trailing_distance_pct = 1.5
            else:
                self.fixed_take_profit_pct = 5.0
                self.fixed_stop_loss_pct = 3.0
                self.trailing_distance_pct = 2.0
            
            self.logger.info("策略参数已更新", extra={
                "市场波动率": market_volatility,
                "最小价格涨幅": f"{self.min_price_increase_pct}%",
                "最小持仓增长率": f"{self.min_position_growth_pct}%",
                "成交量增长比例": self.volume_increase_ratio,
                "固定止盈": f"{self.fixed_take_profit_pct}%",
                "固定止损": f"{self.fixed_stop_loss_pct}%",
                "追踪止损距离": f"{self.trailing_distance_pct}%"
            })
            
        except Exception as e:
            self.logger.exception(f"更新参数时发生异常: {e}")
    
    async def _calculate_market_volatility(self) -> float:
        """
        计算市场整体波动情况，用于判断市场环境
        
        Returns:
            float: 市场波动率因子
        """
        try:
            # 获取BTC作为市场整体波动的参考，使用缓存减少API调用
            result = await self.data_cache.get_klines("BTC-USDT-SWAP", "5m", 288)  # 24小时的5分钟K线
            
            # 从结果中提取数据
            btc_data = result["data"]
            
            # 如果是降级数据，记录日志
            if result["is_fallback"]:
                self.logger.warning(f"使用降级的BTC市场数据，类型: {result['fallback_type']}")
            
            if not btc_data or len(btc_data) < 288:
                return 1.0  # 默认为正常波动
            
            # 计算收盘价
            closes = np.array([float(k[4]) for k in btc_data])
            
            # 计算5分钟收益率
            returns = np.diff(closes) / closes[:-1]
            
            # 计算波动率（标准差）
            volatility = np.std(returns) * 100  # 转为百分比
            
            # 计算相对于历史的波动率因子
            normal_volatility = 0.5  # 假设正常波动率为0.5%
            volatility_factor = volatility / normal_volatility
            
            self.logger.info(f"当前市场波动率: {volatility:.2f}%，波动因子: {volatility_factor:.2f}")
            
            return volatility_factor
            
        except Exception as e:
            self.logger.exception(f"计算市场波动率异常: {e}")
            return 1.0  # 出错时返回默认值
    
    async def _reset_daily_counters(self):
        """重置每日计数器"""
        # 重置风控计数器
        if hasattr(self.position_mgr, 'reset_daily_risk_control'):
            self.position_mgr.reset_daily_risk_control()
        
        # 更新每日起始余额
        try:
            self.daily_starting_balance = await self._reset_daily_counters_and_get_balance()
            
            self.logger.info(f"已重置每日计数器，新的日初始余额: {self.daily_starting_balance} USDT")
            
        except Exception as e:
            self.logger.exception(f"重置每日计数器异常: {e}")
    
    async def _reset_daily_counters_and_get_balance(self) -> float:
        """
        重置日交易计数并获取当前账户余额
        
        Returns:
            float: 当前账户USDT余额总和
        """
        # 获取当前账户余额，使用缓存
        balance_result = await self.data_cache.get_account_balance()
        account_info = balance_result["data"]
        
        # 如果是降级数据，记录日志
        if balance_result["is_fallback"]:
            self.logger.warning(f"重置日计数器时获取账户余额使用降级数据，类型: {balance_result['fallback_type']}")
            
        return self._calculate_total_balance(account_info)
    
    def _calculate_total_balance(self, account_info: Dict[str, Any]) -> float:
        """
        计算账户总余额
        
        Args:
            account_info: 账户信息
            
        Returns:
            float: 总余额（USDT）
        """
        try:
            # 具体实现取决于account_info的数据结构
            # 这里只是一个示例，需要根据实际情况调整
            total_equity = 0.0
            if 'data' in account_info and 'totalEq' in account_info['data'][0]:
                total_equity = float(account_info['data'][0]['totalEq'])
            
            return total_equity
            
        except Exception as e:
            self.logger.exception(f"计算总余额异常: {e}")
            return 0.0
    
    async def _update_eligible_symbols(self):
        """更新符合交易条件的标的列表"""
        try:
            # 获取24小时交易量排名
            # 使用data_cache获取tickers数据，避免直接API调用
            result = await self.data_cache.get_tickers("SWAP")
            tickers = result["data"]
            
            # 如果是降级数据，记录日志
            if result["is_fallback"]:
                self.logger.warning(f"获取市场列表时使用降级数据，类型: {result['fallback_type']}")
                
            if not tickers:
                return
            
            # 按24小时成交量排序
            sorted_tickers = sorted(
                [t for t in tickers if t['instId'].endswith('-USDT-SWAP')],
                key=lambda x: float(x.get('volCcy24h', 0)),
                reverse=True
            )
            
            # 取前N名
            top_tickers = sorted_tickers[:self.top_volume_limit]
            
            # 筛选符合条件的标的
            new_eligible_symbols = set()
            for ticker in top_tickers:
                symbol = ticker['instId']
                
                # 检查5分钟ATR
                atr_pct = await self._calculate_atr(symbol, "5m", 14)
                
                # 检查1小时价格波动
                price_change_pct = await self._calculate_price_change(symbol, "1h", 24)
                
                # 符合条件的标的加入列表
                if atr_pct >= self.min_atr_pct and abs(price_change_pct) <= self.max_price_change_pct:
                    new_eligible_symbols.add(symbol)
                    
                    # 如果是新标的，订阅行情
                    if symbol not in self.eligible_symbols:
                        self._subscribe_market_data(symbol)
            
            old_count = len(self.eligible_symbols)
            self.eligible_symbols = new_eligible_symbols
            new_count = len(self.eligible_symbols)
            
            self.logger.info(f"更新符合条件的标的列表: {old_count} -> {new_count}")
            
        except Exception as e:
            self.logger.exception(f"更新符合条件的标的列表异常: {e}")
    
    async def _calculate_atr(self, symbol: str, timeframe: str = "1h", period: int = 14) -> float:
        """
        计算平均真实波幅(ATR)
        
        Args:
            symbol: 交易对
            timeframe: 时间周期
            period: ATR周期
            
        Returns:
            float: ATR值
        """
        try:
            # 使用data_cache获取K线数据，避免直接API调用
            result = await self.data_cache.get_klines(symbol, timeframe, period+1)
            klines = result["data"]
            
            # 如果是降级数据，记录日志
            if result["is_fallback"]:
                self.logger.warning(f"计算ATR时使用降级数据，交易对: {symbol}, 类型: {result['fallback_type']}")
            
            if not klines or len(klines) <= period:
                return 0.0
            
            tr_values = []
            
            # 计算真实波幅(TR)系列
            for i in range(1, len(klines)):
                high = float(klines[i][2])
                low = float(klines[i][3])
                prev_close = float(klines[i-1][4])
                
                tr1 = high - low
                tr2 = abs(high - prev_close)
                tr3 = abs(low - prev_close)
                
                tr = max(tr1, tr2, tr3)
                tr_values.append(tr)
            
            # 计算ATR (简单平均)
            atr = sum(tr_values) / len(tr_values)
            
            # 转换为百分比 (相对于当前价格)
            current_price = float(klines[-1][4])
            atr_pct = (atr / current_price) * 100
            
            return atr_pct
            
        except Exception as e:
            self.logger.exception(f"计算ATR异常: {e}")
            return 0.0
    
    async def _calculate_price_change(self, symbol: str, timeframe: str = "1h", periods: int = 24) -> float:
        """
        计算价格变化率
        
        Args:
            symbol: 交易对
            timeframe: 时间周期
            periods: 计算周期数量
            
        Returns:
            float: 价格变化百分比
        """
        try:
            # 使用data_cache获取K线数据，避免直接API调用
            result = await self.data_cache.get_klines(symbol, timeframe, periods)
            klines = result["data"]
            
            # 如果是降级数据，记录日志
            if result["is_fallback"]:
                self.logger.warning(f"计算价格变化率时使用降级数据，交易对: {symbol}, 类型: {result['fallback_type']}")
            
            if not klines or len(klines) < 2:
                return 0.0
            
            # 计算收盘价变化
            start_price = float(klines[0][4])  # 第一根K线收盘价
            end_price = float(klines[-1][4])   # 最后一根K线收盘价
            
            if start_price == 0:
                return 0.0
                
            change_pct = (end_price - start_price) / start_price * 100
            return change_pct
            
        except Exception as e:
            self.logger.error(f"计算价格变化率异常: {symbol} {e}")
            return 0.0
    
    async def _calculate_volume_ratio(self, symbol: str, timeframe: str = "5m", periods: int = 12) -> float:
        """
        计算成交量比率，判断是否放量
        
        Args:
            symbol: 交易对
            timeframe: 时间周期
            periods: 计算周期数量
            
        Returns:
            float: 成交量比率
        """
        try:
            # 使用data_cache获取K线数据，避免直接API调用
            result = await self.data_cache.get_klines(symbol, timeframe, periods)
            klines = result["data"]
            
            # 如果是降级数据，记录日志
            if result["is_fallback"]:
                self.logger.warning(f"计算成交量比率时使用降级数据，交易对: {symbol}, 类型: {result['fallback_type']}")
            
            if not klines or len(klines) < periods:
                return 1.0  # 默认中性值
            
            # 提取成交量数据
            volumes = [float(k[5]) for k in klines]
            
            # 计算历史成交量均值（排除当前周期）
            hist_avg_volume = sum(volumes[:-1]) / (len(volumes) - 1)
            
            # 获取当前周期成交量
            current_volume = volumes[-1]
            
            # 计算比例
            if hist_avg_volume == 0:
                return 0.0
                
            volume_ratio = current_volume / hist_avg_volume
            
            return volume_ratio
            
        except Exception as e:
            self.logger.exception(f"计算成交量比例异常: {e}")
            return 0.0
    
    async def _scan_for_signals(self):
        """扫描市场寻找交易信号"""
        for symbol in self.eligible_symbols:
            try:
                # 检查是否已有持仓
                if symbol in self.positions:
                    continue
                
                # 检查买入信号
                signal = await self._check_buy_signal(symbol)
                
                if signal:
                    # 在日志中记录标准化的开仓信号
                    self.logger.info(f"发现买入信号: {symbol}", extra={
                        "信号详情": signal
                    })
                    
                    # 处理买入信号
                    success, message = await self.handle_trade_signal(signal)
                    
                    if success:
                        self.logger.info(f"执行买入成功: {symbol}, {message}")
                    else:
                        self.logger.warning(f"执行买入失败: {symbol}, {message}")
                
            except Exception as e:
                self.logger.exception(f"扫描{symbol}信号异常: {e}")
    
    async def _check_buy_signal(self, symbol: str) -> Optional[TradeSignal]:
        """
        检查买入信号
        
        Args:
            symbol: 交易对
            
        Returns:
            Optional[TradeSignal]: 买入信号，如果没有则返回None
        """
        try:
            # 计算5分钟涨幅
            price_change_pct = await self._calculate_price_change(symbol, "5m", 1)
            
            # 计算持仓量变化
            position_change_pct = await self._calculate_position_change(symbol, "5m", 1)
            
            # 计算成交量比例
            volume_ratio = await self._calculate_volume_ratio(symbol, "5m", 288)  # 24小时
            
            # 检查信号条件
            price_signal = price_change_pct >= self.min_price_increase_pct
            position_signal = position_change_pct >= self.min_position_growth_pct
            volume_signal = volume_ratio >= self.volume_increase_ratio
            
            self.logger.debug(f"信号检查 {symbol}: 价格={price_change_pct:.2f}%, 持仓={position_change_pct:.2f}%, 成交量比={volume_ratio:.2f}")
            
            # 多因子验证
            if price_signal and position_signal and volume_signal:
                # 构建交易信号
                return TradeSignal(
                    action="open",
                    symbol=symbol,
                    direction="long",  # 短线策略主要做多
                    take_profit_pct=self.fixed_take_profit_pct / 100,
                    stop_loss_pct=self.fixed_stop_loss_pct / 100,
                    trailing_stop=self.use_trailing_stop,
                    trailing_distance=self.trailing_distance_pct / 100,
                    leverage=self.leverage,
                    unit_type=self.unit_type,
                    extra_data={
                        "strategy": "short_term_profit",
                        "price_change_pct": price_change_pct,
                        "position_change_pct": position_change_pct,
                        "volume_ratio": volume_ratio,
                        "risk_control": {
                            "enable_cooling_period": True,
                            "cooling_period_minutes": self.cooling_period_minutes,
                            "enable_daily_limit": True,
                            "max_daily_trades": self.max_daily_trades,
                            "enable_loss_limit": True,
                            "max_daily_loss_pct": self.max_daily_loss_pct
                        }
                    }
                )
            
            return None
            
        except Exception as e:
            self.logger.exception(f"检查买入信号异常: {e}")
            return None
    
    async def _calculate_position_change(self, symbol: str, timeframe: str = "5m", periods: int = 12) -> float:
        """
        计算持仓量变化百分比
        
        Args:
            symbol: 交易对
            timeframe: 时间周期
            periods: 计算周期数量
            
        Returns:
            float: 持仓量变化百分比
        """
        try:
            # 使用data_cache获取持仓数据，避免直接API调用
            result = await self.data_cache.get_open_interest(symbol, timeframe, periods+1)
            open_interest_data = result["data"]
            
            # 如果是降级数据，记录日志
            if result["is_fallback"]:
                self.logger.warning(f"计算持仓量变化时使用降级数据，交易对: {symbol}, 类型: {result['fallback_type']}")
            
            if not open_interest_data or len(open_interest_data) < periods+1:
                return 0.0
            
            # 计算持仓量变化
            start_oi = float(open_interest_data[0]['oi'])
            end_oi = float(open_interest_data[-1]['oi'])
            
            if start_oi == 0:
                return 0.0
                
            position_change_pct = (end_oi - start_oi) / start_oi * 100
            return position_change_pct
            
        except Exception as e:
            self.logger.error(f"计算持仓量变化异常: {symbol} {e}")
            return 0.0
    
    async def _check_exit_signals(self):
        """检查平仓信号"""
        # 遍历当前持仓
        for symbol, position in list(self.positions.items()):
            try:
                # 检查是否符合强制平仓条件
                force_exit = await self._check_force_exit_signal(symbol, position)
                
                if force_exit:
                    # 构建平仓信号
                    signal = TradeSignal(
                        action="close",
                        symbol=symbol,
                        extra_data={
                            "strategy": "short_term_profit",
                            "reason": "forced_exit"
                        }
                    )
                    
                    # 在日志中记录标准化的平仓信号
                    self.logger.info(f"发现强制平仓信号: {symbol}", extra={
                        "信号详情": signal
                    })
                    
                    # 处理平仓信号
                    success, message = await self.handle_trade_signal(signal)
                    
                    if success:
                        self.logger.info(f"执行强制平仓成功: {symbol}, {message}")
                    else:
                        self.logger.warning(f"执行强制平仓失败: {symbol}, {message}")
                
            except Exception as e:
                self.logger.exception(f"检查平仓信号异常 {symbol}: {e}")
    
    async def _check_force_exit_signal(self, symbol: str, position) -> bool:
        """
        检查强制平仓信号
        
        Args:
            symbol: 交易对
            position: 仓位信息
            
        Returns:
            bool: 是否触发强制平仓
        """
        try:
            # 检查持仓量下降
            position_change_pct = await self._calculate_position_change(symbol, "5m", 1)
            position_decrease = position_change_pct <= -self.position_decrease_threshold
            
            # 检查价格下跌
            price_change_pct = await self._calculate_price_change(symbol, "5m", 1)
            price_drop = price_change_pct <= -self.price_drop_threshold
            
            # 满足任一条件即触发
            force_exit = position_decrease or price_drop
            
            if force_exit:
                self.logger.info(f"触发强制平仓条件 {symbol}: " + 
                                 f"持仓变化={position_change_pct:.2f}%, " + 
                                 f"价格变化={price_change_pct:.2f}%")
            
            return force_exit
            
        except Exception as e:
            self.logger.exception(f"检查强制平仓信号异常: {e}")
            return False
    
    async def process_signal(self, signal_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        处理接收到的信号数据
        
        Args:
            signal_data: 原始信号数据
            
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        try:
            # 输出原始信号数据用于调试
            self.logger.info(f"收到信号: {signal_data}")
            
            # 检查信号完整性
            if not self._validate_signal(signal_data):
                return False, "信号数据不完整或无效"
            
            # 解析信号类型
            signal_type = signal_data.get('type', '').lower()
            
            if signal_type == 'scan':
                # 触发一次市场扫描
                await self._scan_for_signals()
                return True, "已触发市场扫描"
                
            elif signal_type == 'status':
                # 返回策略状态
                status = await self.get_status()
                return True, json.dumps(status)
                
            elif signal_type == 'reset':
                # 重置每日计数器
                await self._reset_daily_counters()
                return True, "已重置每日计数器"
                
            elif signal_type == 'update_params':
                # 更新参数
                await self._update_parameters()
                return True, "已更新策略参数"
                
            else:
                # 对于开仓、平仓等标准信号，使用父类处理
                return await super().process_signal(signal_data)
                
        except Exception as e:
            self.logger.exception(f"处理信号异常: {e}")
            return False, f"处理信号异常: {e}"
    
    async def get_status(self) -> Dict[str, Any]:
        """
        获取策略状态信息
        
        Returns:
            Dict[str, Any]: 策略状态信息
        """
        # 获取基本状态
        base_status = await super().get_status()
        
        # 添加短线逐利策略的特定状态
        try:
            # 获取账户余额，使用缓存
            balance_result = await self.data_cache.get_account_balance()
            account_info = balance_result["data"]
            
            # 如果是降级数据，记录日志
            if balance_result["is_fallback"]:
                self.logger.warning(f"获取账户余额时使用降级数据，类型: {balance_result['fallback_type']}")
                
            current_balance = self._calculate_total_balance(account_info)
            
            # 计算今日盈亏
            daily_pnl_pct = 0.0
            if self.daily_starting_balance and self.daily_starting_balance > 0:
                daily_pnl_pct = ((current_balance - self.daily_starting_balance) / self.daily_starting_balance) * 100
                # 更新风控的盈亏数据
                if hasattr(self.position_mgr, 'update_risk_pnl'):
                    self.position_mgr.update_risk_pnl(daily_pnl_pct)
            
            # 获取风控状态
            daily_trades_count = 0
            if hasattr(self.position_mgr, 'risk_controller'):
                daily_trades_count = self.position_mgr.risk_controller.daily_trades_count
                daily_pnl_pct = self.position_mgr.risk_controller.daily_pnl_pct
            
            # 构建状态信息
            strategy_status = {
                "eligible_symbols_count": len(self.eligible_symbols),
                "daily_trades_count": daily_trades_count,
                "max_daily_trades": self.max_daily_trades,
                "daily_pnl_pct": daily_pnl_pct,
                "max_daily_loss_pct": self.max_daily_loss_pct,
                "last_param_update": self.last_param_update_time.isoformat(),
                "param_update_hours": self.param_update_hours,
                "strategy_parameters": {
                    "min_price_increase_pct": self.min_price_increase_pct,
                    "min_position_growth_pct": self.min_position_growth_pct,
                    "volume_increase_ratio": self.volume_increase_ratio,
                    "fixed_take_profit_pct": self.fixed_take_profit_pct,
                    "fixed_stop_loss_pct": self.fixed_stop_loss_pct,
                    "trailing_distance_pct": self.trailing_distance_pct
                }
            }
            
            # 合并基本状态和特定状态
            status = {**base_status, **strategy_status}
            
            return status
            
        except Exception as e:
            self.logger.exception(f"获取策略状态异常: {e}")
            return base_status  # 出错时返回基本状态 