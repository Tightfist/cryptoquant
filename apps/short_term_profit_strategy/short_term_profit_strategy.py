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
        
        # 启动持仓量数据诊断
        self._enable_oi_diagnostics = self.strat_config.get('enable_oi_diagnostics', False)
        self._oi_diagnostic_symbols = set()
        self._oi_diagnostic_interval = self.strat_config.get('oi_diagnostic_interval', 15)  # 分钟
        self._last_oi_diagnostic_time = 0
        
        # 启动字典进行日志增强
        self.extra_data = {
            'position_changes': {},  # 记录持仓量变化
            'last_diagnostic_time': time.time(),
            'oi_diagnostics': {
                'valid_reads': 0,
                'failed_reads': 0,
                'cache_hits': 0,
                'api_calls': 0
            }
        }
        
        # 如果配置了持仓量诊断，启动相关任务
        if self._enable_oi_diagnostics:
            self.logger.info("已启用持仓量数据诊断功能")
            asyncio.ensure_future(self._run_oi_diagnostics())
        
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
                price_change_pct = await self._calculate_price_change(symbol, "1H", 24)
                
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
    
    async def _calculate_atr(self, symbol: str, timeframe: str = "1H", period: int = 14) -> float:
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
    
    async def _calculate_price_change(self, symbol: str, timeframe: str = "5m", periods: int = 1) -> float:
        """
        计算价格变化率
        优化后聚焦于最近5分钟与前5分钟的对比，更准确捕捉短期价格变动
        
        Args:
            symbol: 交易对
            timeframe: 时间周期
            periods: 计算周期数量 (已优化，当periods=1时比较最近两个5分钟K线)
            
        Returns:
            float: 价格变化百分比
        """
        try:
            # 至少获取2根K线
            fetch_periods = max(3, periods + 1)
            
            # 使用data_cache获取K线数据，避免直接API调用
            result = await self.data_cache.get_klines(symbol, timeframe, fetch_periods)
            klines = result["data"]
            
            # 如果是降级数据，记录日志
            if result["is_fallback"]:
                self.logger.warning(f"计算价格变化率时使用降级数据，交易对: {symbol}, 类型: {result['fallback_type']}")
            
            if not klines or len(klines) < 2:
                return 0.0
            
            if periods == 1:
                # 对于单周期，比较当前K线与前一根K线的价格变化
                # 使用收盘价对比，因为收盘价最能反映K线结束时的价格状态
                prev_close = float(klines[-2][4])  # 前一根K线收盘价
                current_close = float(klines[-1][4])  # 当前K线收盘价
                
                if prev_close == 0:
                    return 0.0
                    
                change_pct = (current_close - prev_close) / prev_close * 100
                
                # 增加涨跌幅信号强度分析
                current_amplitude = self._calculate_k_amplitude(klines[-1])  # 当前K线振幅
                prev_amplitude = self._calculate_k_amplitude(klines[-2])  # 前一根K线振幅
                
                # 记录详细日志
                self.logger.debug(f"价格变化 {symbol} 5分钟: 前收={prev_close:.2f}, 当收={current_close:.2f}, " +
                               f"变化={change_pct:.2f}%, 当前振幅={current_amplitude:.2f}%, 前振幅={prev_amplitude:.2f}%")
            else:
                # 多周期，计算周期间的价格变化
                start_price = float(klines[-min(periods, len(klines))][4]) if len(klines) >= periods else float(klines[0][4])
                end_price = float(klines[-1][4])
                
                if start_price == 0:
                    return 0.0
                    
                change_pct = (end_price - start_price) / start_price * 100
                self.logger.debug(f"多周期价格变化 {symbol}, {timeframe}, {periods}周期: 起始价={start_price:.2f}, 结束价={end_price:.2f}, 变化={change_pct:.2f}%")
            
            return change_pct
            
        except Exception as e:
            self.logger.error(f"计算价格变化率异常: {symbol} {e}")
            return 0.0
            
    def _calculate_k_amplitude(self, k_data) -> float:
        """
        计算K线振幅
        
        Args:
            k_data: K线数据
            
        Returns:
            float: 振幅百分比
        """
        try:
            high = float(k_data[2])
            low = float(k_data[3])
            open_price = float(k_data[1])
            
            if open_price == 0:
                return 0.0
                
            amplitude = (high - low) / open_price * 100
            return amplitude
        except Exception:
            return 0.0
    
    async def _calculate_volume_ratio(self, symbol: str, timeframe: str = "5m", periods: int = 12) -> float:
        """
        计算成交量比率，判断是否放量
        优化后聚焦于最近5分钟与前30分钟的比较
        
        Args:
            symbol: 交易对
            timeframe: 时间周期
            periods: 计算周期数量 (默认使用12个周期，即1小时)
            
        Returns:
            float: 成交量比率
        """
        try:
            # 确保至少获取7个周期的数据 (1个当前周期 + 6个历史周期)
            fetch_periods = max(7, periods)
            
            # 使用data_cache获取K线数据，避免直接API调用
            result = await self.data_cache.get_klines(symbol, timeframe, fetch_periods)
            klines = result["data"]
            
            # 如果是降级数据，记录日志
            if result["is_fallback"]:
                self.logger.warning(f"计算成交量比率时使用降级数据，交易对: {symbol}, 类型: {result['fallback_type']}")
            
            if not klines or len(klines) < 7:  # 确保至少有当前K线+前6根K线
                return 1.0  # 默认中性值
            
            # 提取成交量数据
            volumes = [float(k[5]) for k in klines]
            
            # 获取当前周期(最近5分钟)成交量
            current_volume = volumes[-1]
            
            # 获取前6根K线(30分钟)的成交量
            recent_volumes = volumes[-7:-1]  # 不包括当前K线
            
            # 计算最近30分钟的平均成交量
            recent_avg_volume = sum(recent_volumes) / len(recent_volumes)
            
            # 计算成交量中位数，避免极端值影响
            recent_volumes_sorted = sorted(recent_volumes)
            if len(recent_volumes_sorted) % 2 == 0:
                mid_idx = len(recent_volumes_sorted) // 2
                recent_median_volume = (recent_volumes_sorted[mid_idx-1] + recent_volumes_sorted[mid_idx]) / 2
            else:
                mid_idx = len(recent_volumes_sorted) // 2
                recent_median_volume = recent_volumes_sorted[mid_idx]
            
            # 避免除以0
            if recent_avg_volume == 0:
                avg_ratio = 0.0
            else:
                avg_ratio = current_volume / recent_avg_volume
                
            if recent_median_volume == 0:
                median_ratio = 0.0
            else:
                median_ratio = current_volume / recent_median_volume
            
            # 使用均值和中位数的加权组合作为最终比率
            # 中位数权重更高，因为它不受极端值影响
            final_ratio = avg_ratio * 0.4 + median_ratio * 0.6
            
            # 记录详细日志
            self.logger.debug(f"成交量分析 {symbol}: 当前={current_volume:.2f}, " +
                            f"30分钟均值={recent_avg_volume:.2f}, 中位数={recent_median_volume:.2f}, " +
                            f"均值比={avg_ratio:.2f}, 中位数比={median_ratio:.2f}, 最终比={final_ratio:.2f}")
            
            return final_ratio
            
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
        优化后更聚焦于捕捉最近5分钟的价格、持仓量和成交量变化
        
        Args:
            symbol: 交易对
            
        Returns:
            Optional[TradeSignal]: 买入信号，如果没有则返回None
        """
        try:
            # 计算5分钟涨幅（当前5分钟K线相对于前一个5分钟K线）
            price_change_pct = await self._calculate_price_change(symbol, "5m", 1)
            
            # 计算持仓量变化（当前5分钟相对于前一个5分钟）
            position_change_pct = await self._calculate_position_change(symbol, "5m", 1)
            
            # 计算成交量比例（当前5分钟相对于前30分钟平均）
            volume_ratio = await self._calculate_volume_ratio(symbol, "5m", 7)  # 7个K线：当前K线+前6根
            
            # 记录详细的信号检查结果
            signal_data = {
                "symbol": symbol,
                "price_change_pct": f"{price_change_pct:.2f}%",
                "position_change_pct": f"{position_change_pct:.2f}%",
                "volume_ratio": f"{volume_ratio:.2f}",
                "min_price_increase_pct": f"{self.min_price_increase_pct:.2f}%",
                "min_position_growth_pct": f"{self.min_position_growth_pct:.2f}%",
                "volume_increase_ratio": f"{self.volume_increase_ratio:.2f}",
                "price_signal": price_change_pct >= self.min_price_increase_pct,
                "position_signal": position_change_pct >= self.min_position_growth_pct,
                "volume_signal": volume_ratio >= self.volume_increase_ratio
            }
            
            # 检查信号条件
            price_signal = price_change_pct >= self.min_price_increase_pct
            position_signal = position_change_pct >= self.min_position_growth_pct
            volume_signal = volume_ratio >= self.volume_increase_ratio
            
            self.logger.debug(f"信号检查 {symbol}", extra=signal_data)
            
            # 多因子验证 - 考虑持仓量数据可能不可用的情况
            # 如果持仓量因子无法获取（为0），仍允许使用价格和成交量因子
            if position_change_pct == 0 and price_signal and volume_signal:
                self.logger.info(f"持仓量数据不可用，使用价格和成交量因子: {symbol}")
                # 加入额外检查条件，价格和成交量信号必须更强
                if price_change_pct >= self.min_price_increase_pct * 1.2 and volume_ratio >= self.volume_increase_ratio * 1.2:
                    return self._create_buy_signal(symbol, price_change_pct, position_change_pct, volume_ratio)
            # 常规多因子验证
            elif price_signal and position_signal and volume_signal:
                return self._create_buy_signal(symbol, price_change_pct, position_change_pct, volume_ratio)
            
            return None
            
        except Exception as e:
            self.logger.exception(f"检查买入信号异常: {e}")
            return None
            
    def _create_buy_signal(self, symbol: str, price_change_pct: float, position_change_pct: float, volume_ratio: float) -> TradeSignal:
        """
        创建买入信号
        
        Args:
            symbol: 交易对
            price_change_pct: 价格变化百分比
            position_change_pct: 持仓量变化百分比
            volume_ratio: 成交量比例
            
        Returns:
            TradeSignal: 买入信号
        """
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
    
    async def _calculate_position_change(self, symbol: str, timeframe: str = "5m", periods: int = 1) -> float:
        """
        计算持仓量变化百分比
        优化后聚焦于最近5分钟与前5分钟的对比
        
        Args:
            symbol: 交易对
            timeframe: 时间周期
            periods: 计算周期数量 (已优化，当periods=1时比较最近两个5分钟周期)
            
        Returns:
            float: 持仓量变化百分比
        """
        try:
            # 至少获取3个周期数据 (保证即使只比较2个周期也有足够的数据)
            fetch_periods = max(3, periods + 1)
            
            # 使用data_cache获取持仓数据，避免直接API调用
            result = await self.data_cache.get_open_interest(symbol, timeframe, fetch_periods)
            open_interest_data = result["data"]
            
            # 如果是降级数据，记录日志
            if result["is_fallback"]:
                self.logger.warning(f"计算持仓量变化时使用降级数据，交易对: {symbol}, 类型: {result['fallback_type']}")
            
            # 获取相同时期的价格变化方向
            price_change = await self._calculate_price_change(symbol, timeframe, periods)
            price_direction = 1 if price_change >= 0 else -1
            
            if not open_interest_data or len(open_interest_data) < 2:
                # 如果数据不足，尝试使用缓存数据计算
                return self._calculate_position_change_from_cache(symbol, price_direction)
            
            if periods == 1:
                # 比较最近两个5分钟周期的持仓量变化
                prev_oi = float(open_interest_data[-2]['oi'])
                current_oi = float(open_interest_data[-1]['oi'])
                
                # 记录当前持仓量到缓存，以便后续使用
                self.extra_data[f"{symbol}_last_oi"] = current_oi
                
                if prev_oi <= 0:
                    self.logger.warning(f"前一周期持仓量为零或负值: {symbol}, 值: {prev_oi}")
                    return 0.0
                    
                raw_position_change_pct = (current_oi - prev_oi) / prev_oi * 100
                
                # 考虑价格方向调整持仓量变化信号
                adjusted_position_change = self._adjust_position_change_with_price(raw_position_change_pct, price_direction)
                
                # 更详细的日志
                self.logger.debug(f"持仓量变化 {symbol} 5分钟: 前值={prev_oi:.2f}, 当前={current_oi:.2f}, " +
                               f"原始变化={raw_position_change_pct:.2f}%, 调整后={adjusted_position_change:.2f}%, 价格方向={price_direction}")
                
                return adjusted_position_change
            else:
                # 处理数据长度，确保使用需要的时间段
                if len(open_interest_data) > periods+1:
                    # 使用最近的periods+1条数据
                    open_interest_data = open_interest_data[-(periods+1):]
                
                # 计算持仓量变化 - 比较periods个周期前和当前的持仓量
                start_oi = float(open_interest_data[0]['oi'])
                end_oi = float(open_interest_data[-1]['oi'])
                
                # 记录当前持仓量到缓存
                self.extra_data[f"{symbol}_last_oi"] = end_oi
                
                if start_oi <= 0:
                    self.logger.warning(f"初始持仓量为零或负值: {symbol}, 值: {start_oi}")
                    return 0.0
                    
                raw_position_change_pct = (end_oi - start_oi) / start_oi * 100
                
                # 考虑价格方向调整持仓量变化信号
                adjusted_position_change = self._adjust_position_change_with_price(raw_position_change_pct, price_direction)
                
                # 更详细的日志
                self.logger.debug(f"持仓量变化(多周期) {symbol}, {timeframe}, {periods}周期: 从 {start_oi:.2f} 到 {end_oi:.2f}, " +
                               f"原始变化={raw_position_change_pct:.2f}%, 调整后={adjusted_position_change:.2f}%, 价格方向={price_direction}")
                
                return adjusted_position_change
            
        except Exception as e:
            self.logger.error(f"计算持仓量变化异常: {symbol} {e}", exc_info=True)
            return 0.0
    
    def _calculate_position_change_from_cache(self, symbol: str, price_direction: int) -> float:
        """
        从缓存数据计算持仓量变化
        
        Args:
            symbol: 交易对
            price_direction: 价格变化方向
            
        Returns:
            float: 调整后的持仓量变化百分比
        """
        try:
            # 获取符号的缓存键
            cache_key = f"{symbol}_last_oi"
            
            # 获取上次记录的持仓量
            last_oi = self.extra_data.get(cache_key, 0.0)
            
            # 获取当前持仓量 (这里在实际场景下可能需要一个API调用)
            current_oi = 0.0  # 此处简化，需要实际API调用
            
            # 更新缓存
            if current_oi > 0:
                self.extra_data[cache_key] = current_oi
            
            # 如果有上次记录，计算变化
            if last_oi > 0 and current_oi > 0:
                raw_position_change_pct = (current_oi - last_oi) / last_oi * 100
                
                # 考虑价格方向调整持仓量变化信号
                adjusted_position_change = self._adjust_position_change_with_price(raw_position_change_pct, price_direction)
                
                self.logger.info(f"使用缓存计算持仓量变化: {symbol}, 从 {last_oi:.2f} 到 {current_oi:.2f}, " +
                              f"原始变化={raw_position_change_pct:.2f}%, 调整后={adjusted_position_change:.2f}%, 价格方向={price_direction}")
                return adjusted_position_change
            else:
                self.logger.info(f"缓存数据不足，无法计算变化: {symbol}")
                return 0.0
        except Exception as e:
            self.logger.error(f"从缓存计算持仓量变化异常: {symbol}, {e}")
            return 0.0
    
    def _adjust_position_change_with_price(self, position_change_pct: float, price_direction: int) -> float:
        """
        根据价格方向调整持仓量变化信号强度
        
        Args:
            position_change_pct: 原始持仓量变化百分比
            price_direction: 价格变化方向 (1=上涨, -1=下跌)
            
        Returns:
            float: 调整后的持仓量变化百分比
        """
        # 持仓量增加 + 价格上涨 = 多头进场 (信号增强)
        # 持仓量增加 + 价格下跌 = 空头进场 (信号减弱)
        # 持仓量减少 + 价格上涨 = 空头退场 (信号中性)
        # 持仓量减少 + 价格下跌 = 多头退场 (信号减弱)
        
        # 如果持仓量变化与价格方向一致 (都为正或都为负)
        if (position_change_pct * price_direction) > 0:
            # 持仓量增加且价格上涨，强化信号
            if position_change_pct > 0 and price_direction > 0:
                return position_change_pct * 1.3
            # 持仓量减少且价格下跌，弱化信号
            else:
                return position_change_pct * 0.7
        else:
            # 持仓量增加但价格下跌，可能是空头进场，弱化信号
            if position_change_pct > 0 and price_direction < 0:
                return position_change_pct * 0.5
            # 持仓量减少但价格上涨，可能是空头退场，中性评估
            else:
                return position_change_pct * 0.9
                
        return position_change_pct  # 默认情况，不调整
    
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
            
            # 获取持仓时间
            position_time = position.entry_time
            current_time = time.time()
            position_duration_minutes = (current_time - position_time) / 60
            
            # 记录详细的退出信号检查
            exit_data = {
                "symbol": symbol,
                "position_change_pct": f"{position_change_pct:.2f}%",
                "price_change_pct": f"{price_change_pct:.2f}%",
                "position_decrease_threshold": f"{self.position_decrease_threshold:.2f}%",
                "price_drop_threshold": f"{self.price_drop_threshold:.2f}%",
                "position_duration_minutes": f"{position_duration_minutes:.1f}分钟",
                "position_decrease_signal": position_decrease,
                "price_drop_signal": price_drop
            }
            
            # 持仓量降级处理：如果持仓量数据不可用（为0），仅使用价格指标
            if position_change_pct == 0:
                self.logger.warning(f"持仓量数据不可用，仅使用价格指标: {symbol}")
                # 对新开仓位置更保守，对持有较久的仓位更激进
                if position_duration_minutes < 15:  # 15分钟内的新仓位
                    # 价格下跌更多才平仓
                    force_exit = price_change_pct <= -self.price_drop_threshold * 1.5
                else:
                    # 老仓位，价格下跌即平仓
                    force_exit = price_drop
            else:
                # 正常情况：持仓量下降或价格下跌满足条件即触发平仓
                force_exit = position_decrease or price_drop
            
            if force_exit:
                self.logger.info(f"触发强制平仓条件 {symbol}", extra=exit_data)
            else:
                self.logger.debug(f"检查强制平仓 {symbol}", extra=exit_data)
            
            return force_exit
            
        except Exception as e:
            self.logger.exception(f"检查强制平仓信号异常: {e}")
            return False
    
    async def process_signal(self, signal_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        处理接收到的信号数据
        
        Args:
            signal_data: 接收到的信号数据
            
        Returns:
            Tuple[bool, str]: 处理结果和消息
        """
        try:
            signal_type = signal_data.get('type', '')
            
            if signal_type == 'scan':
                # 触发立即扫描市场
                asyncio.ensure_future(self._scan_market())
                return True, "已触发市场扫描"
                
            elif signal_type == 'status':
                # 获取策略当前状态
                status = await self.get_status()
                return True, json.dumps(status)
                
            elif signal_type == 'reset':
                # 重置每日计数器
                self.daily_trades = 0
                self.daily_loss = 0
                return True, "已重置每日计数器"
                
            elif signal_type == 'update_params':
                # 更新策略参数
                await self._update_parameters()
                return True, "已更新策略参数"
                
            elif signal_type == 'open':
                # 手动开仓
                symbol = signal_data.get('symbol')
                direction = signal_data.get('direction', 'long')
                take_profit_pct = signal_data.get('take_profit_pct', self.fixed_take_profit_pct / 100)
                stop_loss_pct = signal_data.get('stop_loss_pct', self.fixed_stop_loss_pct / 100)
                trailing_stop = signal_data.get('trailing_stop', self.use_trailing_stop)
                trailing_distance = signal_data.get('trailing_distance', self.trailing_distance_pct / 100)
                leverage = signal_data.get('leverage', self.leverage)
                
                # 创建交易信号
                signal = TradeSignal(
                    action="open",
                    symbol=symbol,
                    direction=direction,
                    take_profit_pct=take_profit_pct,
                    stop_loss_pct=stop_loss_pct,
                    trailing_stop=trailing_stop,
                    trailing_distance=trailing_distance,
                    leverage=leverage,
                    unit_type=self.unit_type,
                    extra_data={"strategy": "short_term_profit", "manual": True}
                )
                
                # 处理信号
                success, msg = await self.handle_trade_signal(signal)
                return success, msg
                
            elif signal_type == 'close':
                # 手动平仓
                symbol = signal_data.get('symbol')
                
                # 创建平仓信号
                signal = TradeSignal(
                    action="close",
                    symbol=symbol,
                    extra_data={"strategy": "short_term_profit", "reason": "manual_close"}
                )
                
                # 处理信号
                success, msg = await self.handle_trade_signal(signal)
                return success, msg
                
            elif signal_type == 'factor_analysis':
                # 新增: 因子分析
                top_n = signal_data.get('top_n', 10)
                max_symbols = signal_data.get('max_symbols', 20)
                result = await self._analyze_factors(top_n, max_symbols)
                return True, json.dumps(result)
                
            else:
                return False, f"未知的信号类型: {signal_type}"
                
        except Exception as e:
            self.logger.exception(f"处理信号异常: {e}")
            return False, f"处理信号异常: {e}"
            
    async def _analyze_factors(self, top_n: int = 10, max_symbols: int = 20) -> Dict[str, Any]:
        """
        分析所有符合条件标的的因子数据，按因子值排序并返回
        
        Args:
            top_n: 返回排名前多少的数据
            max_symbols: 最多分析多少个标的，设为0表示分析全部
            
        Returns:
            Dict[str, Any]: 因子分析结果
        """
        self.logger.info(f"开始分析因子数据，返回前 {top_n} 条, 最多分析 {max_symbols if max_symbols > 0 else '全部'} 个标的")
        
        result = {
            "timestamp": time.time(),
            "eligible_symbols_count": len(self.eligible_symbols),
            "price_factor": [],
            "position_factor": [],
            "volume_factor": [],
            "signal_analysis": []
        }
        
        # 临时保存因子数据的列表
        all_data = []
        
        # 确定要分析的标的列表
        symbols_to_analyze = list(self.eligible_symbols)
        if max_symbols > 0:
            symbols_to_analyze = symbols_to_analyze[:max_symbols]
            
        self.logger.info(f"实际分析标的数量: {len(symbols_to_analyze)}")
        
        for symbol in symbols_to_analyze:
            try:
                # 获取三个因子的数据
                price_change_pct = await self._calculate_price_change(symbol, "5m", 1)
                position_change_pct = await self._calculate_position_change(symbol, "5m", 1)
                volume_ratio = await self._calculate_volume_ratio(symbol, "5m", 288)
                
                # 判断各因子是否满足条件
                price_signal = price_change_pct >= self.min_price_increase_pct
                position_signal = position_change_pct >= self.min_position_growth_pct
                volume_signal = volume_ratio >= self.volume_increase_ratio
                
                # 保存数据
                symbol_data = {
                    "symbol": symbol,
                    "price_change_pct": round(price_change_pct, 2),
                    "position_change_pct": round(position_change_pct, 2),
                    "volume_ratio": round(volume_ratio, 2),
                    "price_signal": price_signal,
                    "position_signal": position_signal,
                    "volume_signal": volume_signal,
                    "all_signals": price_signal and position_signal and volume_signal
                }
                
                all_data.append(symbol_data)
                
            except Exception as e:
                self.logger.error(f"分析 {symbol} 因子数据异常: {e}")
        
        # 按价格变化排序
        price_sorted = sorted(all_data, key=lambda x: x["price_change_pct"], reverse=True)
        result["price_factor"] = price_sorted[:top_n]
        
        # 按持仓量变化排序
        position_sorted = sorted(all_data, key=lambda x: x["position_change_pct"], reverse=True)
        result["position_factor"] = position_sorted[:top_n]
        
        # 按成交量比例排序
        volume_sorted = sorted(all_data, key=lambda x: x["volume_ratio"], reverse=True)
        result["volume_factor"] = volume_sorted[:top_n]
        
        # 信号分析
        total = len(all_data)
        price_pass = sum(1 for item in all_data if item["price_signal"])
        position_pass = sum(1 for item in all_data if item["position_signal"])
        volume_pass = sum(1 for item in all_data if item["volume_signal"])
        all_pass = sum(1 for item in all_data if item["all_signals"])
        
        result["signal_analysis"] = {
            "total_analyzed": total,
            "price_factor_pass": f"{price_pass}/{total} ({price_pass/total*100:.1f}%)",
            "position_factor_pass": f"{position_pass}/{total} ({position_pass/total*100:.1f}%)",
            "volume_factor_pass": f"{volume_pass}/{total} ({volume_pass/total*100:.1f}%)",
            "all_factors_pass": f"{all_pass}/{total} ({all_pass/total*100:.1f}%)",
            "min_price_increase_pct": self.min_price_increase_pct,
            "min_position_growth_pct": self.min_position_growth_pct,
            "volume_increase_ratio": self.volume_increase_ratio
        }
        
        # 在日志中打印关键信息
        self.logger.info(f"因子分析结果: 分析标的 {total} 个, " + 
                       f"价格因子通过率: {price_pass/total*100:.1f}%, " + 
                       f"持仓因子通过率: {position_pass/total*100:.1f}%, " + 
                       f"成交量因子通过率: {volume_pass/total*100:.1f}%, " + 
                       f"满足所有条件: {all_pass/total*100:.1f}%")
                       
        # 打印前3个排名的标的详情
        if len(price_sorted) >= 3:
            self.logger.info("价格变化因子前3名: " + ", ".join([
                f"{item['symbol']}: {item['price_change_pct']}%" for item in price_sorted[:3]
            ]))
            
        if len(position_sorted) >= 3:
            self.logger.info("持仓量变化因子前3名: " + ", ".join([
                f"{item['symbol']}: {item['position_change_pct']}%" for item in position_sorted[:3]
            ]))
            
        if len(volume_sorted) >= 3:
            self.logger.info("成交量比率因子前3名: " + ", ".join([
                f"{item['symbol']}: {item['volume_ratio']}x" for item in volume_sorted[:3]
            ]))
            
        return result
    
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
    
    async def _run_oi_diagnostics(self):
        """定期运行持仓量数据诊断"""
        self.logger.info("启动持仓量数据诊断任务")
        
        try:
            while True:
                # 等待指定的诊断间隔
                await asyncio.sleep(self._oi_diagnostic_interval * 60)
                
                current_time = time.time()
                self.extra_data['last_diagnostic_time'] = current_time
                
                # 如果有符合条件的标的，进行诊断
                if self.eligible_symbols:
                    # 从符合条件的标的中随机选择一个
                    diag_symbols = list(self.eligible_symbols)[:5]  # 最多取5个
                    self.logger.info(f"执行持仓量数据诊断，标的: {diag_symbols}")
                    
                    for symbol in diag_symbols:
                        try:
                            # 测试持仓量数据获取
                            for timeframe in ["5m", "15m", "1H"]:
                                result = await self.data_cache.get_open_interest(symbol, timeframe, 12)
                                
                                # 检查结果
                                if result["data"] and len(result["data"]) > 0:
                                    self.extra_data['oi_diagnostics']['valid_reads'] += 1
                                    self.logger.info(f"持仓量数据诊断成功: {symbol}, {timeframe}, " +
                                                   f"数据量: {len(result['data'])}, " +
                                                   f"降级: {result['is_fallback']}, " +
                                                   f"降级类型: {result['fallback_type']}")
                                    
                                    # 计算最早和最新数据点之间的间隔
                                    if len(result["data"]) >= 2:
                                        first_ts = int(result["data"][0]["ts"])
                                        last_ts = int(result["data"][-1]["ts"])
                                        minutes_span = (last_ts - first_ts) / (60 * 1000)
                                        self.logger.info(f"持仓量数据时间跨度: {minutes_span:.1f}分钟, " +
                                                       f"平均间隔: {minutes_span/(len(result['data'])-1):.1f}分钟/条")
                                        
                                    # 检查是否有数据点间隔过大
                                    large_gaps = []
                                    for i in range(1, len(result["data"])):
                                        curr_ts = int(result["data"][i]["ts"])
                                        prev_ts = int(result["data"][i-1]["ts"])
                                        gap_minutes = (curr_ts - prev_ts) / (60 * 1000)
                                        
                                        # 间隔超过预期的2倍
                                        expected_interval = 5 if timeframe == "5m" else (15 if timeframe == "15m" else 60)
                                        if gap_minutes > expected_interval * 2:
                                            large_gaps.append(f"{i-1}->{i}: {gap_minutes:.1f}分钟")
                                    
                                    if large_gaps:
                                        self.logger.warning(f"持仓量数据存在大间隔: {symbol}, {timeframe}, 间隔: {large_gaps}")
                                    
                                else:
                                    self.extra_data['oi_diagnostics']['failed_reads'] += 1
                                    self.logger.warning(f"持仓量数据诊断失败: {symbol}, {timeframe}, 无数据")
                            
                            # 计算最近5分钟持仓量变化
                            change_pct = await self._calculate_position_change(symbol, "5m", 1)
                            self.logger.info(f"持仓量5分钟变化: {symbol}, {change_pct:.2f}%")
                            
                            # 检查持仓量滑动缓存状态
                            if hasattr(self.data_cache, '_oi_sliding_cache'):
                                if symbol in self.data_cache._oi_sliding_cache:
                                    cache_info = self.data_cache._oi_sliding_cache[symbol]
                                    self.logger.info(f"持仓量滑动缓存状态: {symbol}, " +
                                                   f"记录数={len(cache_info['data'])}, " +
                                                   f"更新次数={cache_info.get('updates', 0)}, " +
                                                   f"最后更新={time.time() - cache_info['last_update']:.1f}秒前")
                                else:
                                    self.logger.warning(f"持仓量滑动缓存中不存在: {symbol}")
                        
                        except Exception as e:
                            self.logger.error(f"持仓量诊断异常: {symbol}, {e}", exc_info=True)
                
                # 打印诊断统计
                diagnostics = self.extra_data['oi_diagnostics']
                self.logger.info(f"持仓量诊断统计: 成功={diagnostics['valid_reads']}, " +
                               f"失败={diagnostics['failed_reads']}, " +
                               f"缓存命中={diagnostics['cache_hits']}, " +
                               f"API调用={diagnostics['api_calls']}")
                
        except asyncio.CancelledError:
            self.logger.info("持仓量数据诊断任务已取消")
        except Exception as e:
            self.logger.error(f"持仓量数据诊断任务异常: {e}", exc_info=True) 