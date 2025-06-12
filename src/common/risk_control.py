"""
风险控制模块

提供统一的风险控制规则和机制，包括：
1. 冷却期机制：限制同一标的连续交易的时间间隔
2. 日交易上限：限制每日交易次数
3. 亏损限制：根据当日亏损百分比控制交易
4. 最大持仓数限制：限制同时持有的最大仓位数量
5. 低交易额过滤：过滤低交易额的品种
6. 短周期价格波动限制：限制短周期内价格波动过大的品种
7. 时间风控：限制周末和节假日交易

该模块设计为与仓位管理集成，通过交易信号中的风控参数，在开仓时自动判断风控条件
"""

import logging
import pytz
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Set, Tuple, Union

# 尝试导入holidays库，如果没有安装则使用内置的简单节假日判断
try:
    import holidays
    HAS_HOLIDAYS = True
except ImportError:
    HAS_HOLIDAYS = False


class RiskController:
    """风险控制器，提供风控规则检查"""
    
    def __init__(self, logger=None):
        """
        初始化风险控制器
        
        Args:
            logger: 日志记录器，如果不提供则使用默认记录器
        """
        self.logger = logger or logging.getLogger("RiskController")
        
        # 存储最后交易时间信息
        self.last_trade_time = {}
        
        # 当日交易统计
        self.daily_trades_count = 0
        self.daily_pnl_pct = 0.0
        
        # 风控配置
        self.cooling_period_minutes = 30
        self.max_daily_trades = 50
        self.max_daily_loss_pct = 50.0
        self.max_positions = 10  # 默认最大持仓数
        self.min_volume_filter = 0  # 默认不过滤低交易额品种（0表示不过滤）
        
        # 价格波动风控配置
        self.price_change_period_minutes = 15  # 价格波动检查周期，默认15分钟
        self.max_price_change_pct = 5.0  # 价格波动最大百分比，默认5%
        
        # 时间风控配置
        self.timezone = pytz.timezone('Asia/Shanghai')  # 东八区
        self.block_weekends = True  # 是否阻止周末交易
        self.block_us_holidays = True  # 是否阻止美国节假日交易
        
        # 当前开放的持仓数量
        self.current_positions_count = 0
        
        # 风控开关
        self.enable_cooling_period = False
        self.enable_daily_limit = False
        self.enable_loss_limit = False
        self.enable_max_positions = False
        self.enable_volume_filter = False
        self.enable_price_change_limit = False  # 价格波动风控开关
        self.enable_time_control = False  # 时间风控开关
        
        # 数据缓存引用，用于获取价格数据
        self.data_cache = None
        
        # 初始化美国节假日日历（如果可用）
        self._init_holidays()
    
    def set_data_cache(self, data_cache):
        """设置数据缓存引用"""
        self.data_cache = data_cache
    
    def _init_holidays(self):
        """初始化节假日日历"""
        if HAS_HOLIDAYS:
            # 使用美国节假日（NYSE交易所节假日）
            self.us_holidays = holidays.US(years=range(2020, 2030))
            self.logger.info("已加载美国节假日数据")
        else:
            self.us_holidays = None
            self.logger.warning("未安装holidays库，将使用简单的节假日判断")
            self.logger.info("建议安装holidays库以获得完整的节假日支持: pip install holidays")
    
    def configure(self, config: Dict[str, Any]) -> None:
        """
        配置风险控制参数
        
        Args:
            config: 风险控制配置
        """
        self.cooling_period_minutes = config.get('cooling_period_minutes', 30)
        self.max_daily_trades = config.get('max_daily_trades', 50)
        self.max_daily_loss_pct = config.get('max_daily_loss_pct', 50.0)
        self.max_positions = config.get('max_positions', 10)
        self.min_volume_filter = config.get('min_volume_filter', 0)
        
        # 价格波动风控配置
        self.price_change_period_minutes = config.get('price_change_period_minutes', 15)
        self.max_price_change_pct = config.get('max_price_change_pct', 5.0)
        
        # 时间风控配置
        timezone_str = config.get('timezone', 'Asia/Shanghai')
        try:
            self.timezone = pytz.timezone(timezone_str)
        except pytz.UnknownTimeZoneError:
            self.logger.warning(f"未知时区 {timezone_str}，使用默认时区 Asia/Shanghai")
            self.timezone = pytz.timezone('Asia/Shanghai')
        
        self.block_weekends = config.get('block_weekends', True)
        self.block_us_holidays = config.get('block_us_holidays', True)
        
        self.enable_cooling_period = config.get('enable_cooling_period', True)
        self.enable_daily_limit = config.get('enable_daily_limit', True)
        self.enable_loss_limit = config.get('enable_loss_limit', True)
        self.enable_max_positions = config.get('enable_max_positions', True)
        self.enable_volume_filter = config.get('enable_volume_filter', False)
        self.enable_price_change_limit = config.get('enable_price_change_limit', False)
        self.enable_time_control = config.get('enable_time_control', False)
        
        self.logger.info("风险控制配置已更新", extra={
            "冷却期": f"{self.cooling_period_minutes}分钟",
            "每日最大交易次数": self.max_daily_trades,
            "最大亏损比例": f"{self.max_daily_loss_pct}%",
            "最大持仓数": self.max_positions,
            "低交易额过滤": f"{self.min_volume_filter}",
            "价格波动周期": f"{self.price_change_period_minutes}分钟",
            "最大价格波动": f"{self.max_price_change_pct}%",
            "时区": str(self.timezone),
            "阻止周末交易": self.block_weekends,
            "阻止美国节假日交易": self.block_us_holidays,
            "冷却期开关": self.enable_cooling_period,
            "日限额开关": self.enable_daily_limit,
            "亏损限制开关": self.enable_loss_limit,
            "持仓数量限制": self.enable_max_positions,
            "交易额过滤开关": self.enable_volume_filter,
            "价格波动限制开关": self.enable_price_change_limit,
            "时间风控开关": self.enable_time_control
        })
    
    def update_daily_pnl(self, pnl_pct: float) -> None:
        """
        更新当日盈亏百分比
        
        Args:
            pnl_pct: 盈亏百分比
        """
        self.daily_pnl_pct = pnl_pct
    
    def reset_daily_counters(self) -> None:
        """重置每日计数器"""
        self.daily_trades_count = 0
        self.daily_pnl_pct = 0.0
        self.logger.info("已重置风控每日计数器")
    
    def set_positions_count(self, count: int) -> None:
        """
        设置当前持仓数量
        
        Args:
            count: 当前持仓数量
        """
        old_count = self.current_positions_count
        self.current_positions_count = count
        self.logger.info(f"手动更新持仓数量: {old_count} -> {count}")
    
    def record_trade(self, symbol: str) -> None:
        """
        记录交易信息
        
        Args:
            symbol: 交易标的
        """
        # 记录最后交易时间
        self.last_trade_time[symbol] = datetime.now()
        # 增加交易计数
        self.daily_trades_count += 1
        # 增加持仓计数
        old_count = self.current_positions_count
        self.current_positions_count += 1
        
        self.logger.info(f"记录交易: {symbol}, 当日第{self.daily_trades_count}笔, 当前持仓数: {old_count} -> {self.current_positions_count}")
    
    def record_close_position(self, symbol: str, is_partial_close: bool = False) -> None:
        """
        记录平仓信息
        
        Args:
            symbol: 交易标的
            is_partial_close: 是否部分平仓，为True时不减少持仓计数
        """
        # 只有在完全平仓时才减少持仓计数
        old_count = self.current_positions_count
        if not is_partial_close and self.current_positions_count > 0:
            self.current_positions_count -= 1
            self.logger.info(f"记录平仓: {symbol}, 当前持仓数: {old_count} -> {self.current_positions_count}")
        else:
            self.logger.info(f"记录部分平仓: {symbol}, 持仓数保持不变: {old_count}")
    
    async def check_price_change(self, symbol: str, risk_params: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        """
        检查价格波动风控
        
        Args:
            symbol: 交易标的
            risk_params: 风控参数，可覆盖默认设置
            
        Returns:
            Tuple[bool, str]: (是否允许, 原因)
        """
        if not self.data_cache:
            self.logger.warning("未设置数据缓存，无法检查价格波动")
            return True, "未设置数据缓存，无法检查价格波动"
            
        # 检查是否启用价格波动风控
        enable_check = risk_params.get('enable_price_change_limit', self.enable_price_change_limit)
        if not enable_check:
            return True, "未启用价格波动风控"
            
        # 获取价格波动检查周期和最大波动百分比
        period_minutes = risk_params.get('price_change_period_minutes', self.price_change_period_minutes)
        max_change_pct = risk_params.get('max_price_change_pct', self.max_price_change_pct)
        
        try:
            # 获取当前价格
            current_price = await self.data_cache.get_mark_price(symbol)
            if not current_price:
                self.logger.warning(f"无法获取 {symbol} 当前价格")
                return True, "无法获取当前价格，忽略价格波动检查"
                
            # 获取指定时间前的价格
            # 注意：这里需要根据实际的数据缓存API调整
            price_before = await self.data_cache.get_price_before(symbol, period_minutes)
            if not price_before:
                self.logger.warning(f"无法获取 {symbol} {period_minutes}分钟前价格")
                return True, f"无法获取{period_minutes}分钟前价格，忽略价格波动检查"
                
            # 计算价格变化百分比
            price_change_pct = abs((current_price - price_before) / price_before * 100)
            
            # 检查价格变化是否超过阈值
            if price_change_pct > max_change_pct:
                self.logger.info(f"{symbol} {period_minutes}分钟价格波动 {price_change_pct:.2f}% 超过限制 {max_change_pct}%")
                return False, f"{period_minutes}分钟价格波动 {price_change_pct:.2f}% 超过限制 {max_change_pct}%"
                
            self.logger.debug(f"{symbol} {period_minutes}分钟价格波动 {price_change_pct:.2f}% 在限制范围内")
            return True, f"{period_minutes}分钟价格波动在限制范围内"
            
        except Exception as e:
            self.logger.error(f"检查价格波动异常: {e}")
            # 出错时不阻止交易
            return True, f"检查价格波动异常: {e}"
    
    def check_time_allowed(self, risk_params: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        """
        检查时间风控
        
        Args:
            risk_params: 风控参数，可覆盖默认设置
            
        Returns:
            Tuple[bool, str]: (是否允许, 原因)
        """
        # 检查是否启用时间风控
        enable_time_control = risk_params.get('enable_time_control', self.enable_time_control) if risk_params else self.enable_time_control
        if not enable_time_control:
            return True, "未启用时间风控"
        
        # 获取当前时间（指定时区）
        now_utc = datetime.now(pytz.UTC)
        local_time = now_utc.astimezone(self.timezone)
        
        # 获取配置参数
        block_weekends = risk_params.get('block_weekends', self.block_weekends) if risk_params else self.block_weekends
        block_us_holidays = risk_params.get('block_us_holidays', self.block_us_holidays) if risk_params else self.block_us_holidays
        
        self.logger.debug(f"时间风控检查: 当前时间 {local_time.strftime('%Y-%m-%d %H:%M:%S %Z')}, 周几: {local_time.weekday()}")
        
        # 检查是否为周末（周六=5, 周日=6）
        if block_weekends and local_time.weekday() >= 5:
            weekday_name = "周六" if local_time.weekday() == 5 else "周日"
            return False, f"时间风控: 当前为{weekday_name}，禁止开仓"
        
        # 检查是否为美国节假日
        if block_us_holidays:
            is_holiday, holiday_name = self._is_us_holiday(local_time.date())
            if is_holiday:
                return False, f"时间风控: 当前为美国节假日({holiday_name})，禁止开仓"
        
        return True, "时间风控检查通过"
    
    def _is_us_holiday(self, date) -> Tuple[bool, str]:
        """
        检查是否为美国节假日
        
        Args:
            date: 要检查的日期
            
        Returns:
            Tuple[bool, str]: (是否为节假日, 节假日名称)
        """
        if HAS_HOLIDAYS and self.us_holidays:
            if date in self.us_holidays:
                holiday_name = self.us_holidays[date]
                self.logger.debug(f"检测到美国节假日: {date} - {holiday_name}")
                return True, holiday_name
        else:
            # 如果没有holidays库，使用简单的节假日判断
            # 这里只检查一些固定日期的节假日
            simple_holidays = self._get_simple_holidays(date.year)
            for holiday_date, holiday_name in simple_holidays:
                if date == holiday_date:
                    self.logger.debug(f"检测到美国节假日（简单判断）: {date} - {holiday_name}")
                    return True, holiday_name
        
        return False, ""
    
    def _get_simple_holidays(self, year: int) -> List[Tuple[datetime.date, str]]:
        """
        获取简单的美国节假日列表（当没有holidays库时使用）
        
        Args:
            year: 年份
            
        Returns:
            List[Tuple[datetime.date, str]]: 节假日日期和名称的列表
        """
        from datetime import date
        
        holidays_list = [
            # 新年
            (date(year, 1, 1), "New Year's Day"),
            # 独立日
            (date(year, 7, 4), "Independence Day"),
            # 圣诞节
            (date(year, 12, 25), "Christmas Day"),
        ]
        
        # 计算感恩节（11月第四个周四）
        import calendar
        thanksgiving_day = None
        nov_days = calendar.monthcalendar(year, 11)
        thursdays = [day for week in nov_days for day in week if day != 0 and calendar.weekday(year, 11, day) == 3]
        if len(thursdays) >= 4:
            thanksgiving_day = date(year, 11, thursdays[3])
            holidays_list.append((thanksgiving_day, "Thanksgiving Day"))
        
        return holidays_list
    
    def check_symbol_allowed(self, symbol: str, risk_params: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        """
        检查标的是否允许交易
        
        Args:
            symbol: 交易标的
            risk_params: 风控参数，可以覆盖默认设置
            
        Returns:
            Tuple[bool, str]: (是否允许, 原因)
        """
        # 如果没有风控参数，允许交易
        if risk_params is None:
            return True, "未使用风控"
        
        # 检查冷却期
        if risk_params.get('enable_cooling_period', self.enable_cooling_period):
            cooling_minutes = risk_params.get('cooling_period_minutes', self.cooling_period_minutes)
            
            if symbol in self.last_trade_time:
                cooling_end_time = self.last_trade_time[symbol] + timedelta(minutes=cooling_minutes)
                if datetime.now() < cooling_end_time:
                    remaining_seconds = (cooling_end_time - datetime.now()).total_seconds()
                    return False, f"冷却期限制: 还需等待 {int(remaining_seconds)} 秒"
        
        return True, "允许交易"
    
    async def check_trade_allowed(self, symbol: str, risk_params: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        """
        检查是否允许交易（全局限制）
        
        Args:
            symbol: 交易标的
            risk_params: 风控参数，可以覆盖默认设置
            
        Returns:
            Tuple[bool, str]: (是否允许, 原因)
        """
        # 如果没有风控参数，允许交易
        if risk_params is None:
            risk_params = {}
        
        # 检查交易额过滤
        if risk_params.get('enable_volume_filter', self.enable_volume_filter) and self.data_cache:
            min_volume = risk_params.get('min_volume_filter', self.min_volume_filter)
            if min_volume > 0:
                try:
                    volume_24h = await self.data_cache.get_volume_24h(symbol)
                    if volume_24h and volume_24h < min_volume:
                        return False, f"24小时交易额 {volume_24h} 低于最小要求 {min_volume}"
                except Exception as e:
                    self.logger.error(f"获取24小时交易额失败: {e}")
                    # 出错时不阻止交易
        
        # 检查最大持仓数
        if risk_params.get('enable_max_positions', self.enable_max_positions):
            max_positions = risk_params.get('max_positions', self.max_positions)
            self.logger.info(f"检查最大持仓风控: 当前持仓 {self.current_positions_count}/{max_positions} 个")
            if self.current_positions_count >= max_positions:
                self.logger.warning(f"达到最大持仓数限制: {max_positions}个，当前持仓: {self.current_positions_count}个")
                return False, f"达到最大持仓数限制: {max_positions}个，当前持仓: {self.current_positions_count}个"
        
        # 检查日交易上限
        if risk_params.get('enable_daily_limit', self.enable_daily_limit):
            max_trades = risk_params.get('max_daily_trades', self.max_daily_trades)
            
            if self.daily_trades_count >= max_trades:
                return False, f"达到每日交易上限: {max_trades}笔"
        
        # 检查亏损限制
        if risk_params.get('enable_loss_limit', self.enable_loss_limit):
            max_loss = risk_params.get('max_daily_loss_pct', self.max_daily_loss_pct)
            
            if self.daily_pnl_pct <= -max_loss:
                return False, f"达到每日最大亏损限制: {max_loss}%"
        
        return True, "允许交易"
    
    async def check_risk_control(self, symbol: str, signal_extra_data: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
        """
        综合检查风控条件
        
        Args:
            symbol: 交易标的
            signal_extra_data: 信号中的额外数据，包含风控参数
            
        Returns:
            Tuple[bool, str]: (是否允许, 原因)
        """
        self.logger.info(f"执行风控检查: {symbol}, 当前持仓数: {self.current_positions_count}")
        
        # 如果信号中没有包含风控信息，使用默认风控配置而不是直接允许交易
        if not signal_extra_data or 'risk_control' not in signal_extra_data:
            # 使用空的风控参数，这会导致内部方法使用默认配置
            risk_params = {}
            self.logger.debug(f"使用默认风控参数, 最大持仓数: {self.max_positions}, 开关状态: {self.enable_max_positions}")
        else:
            # 获取风控参数
            risk_params = signal_extra_data.get('risk_control', {})
            max_positions = risk_params.get('max_positions', self.max_positions)
            enable_max_positions = risk_params.get('enable_max_positions', self.enable_max_positions)
            self.logger.debug(f"使用信号风控参数, 最大持仓数: {max_positions}, 开关状态: {enable_max_positions}")
        
        # 检查全局交易限制
        allowed, reason = await self.check_trade_allowed(symbol, risk_params)
        if not allowed:
            return False, reason
        
        # 检查标的限制
        allowed, reason = self.check_symbol_allowed(symbol, risk_params)
        if not allowed:
            return False, reason
            
        # 检查价格波动限制
        allowed, reason = await self.check_price_change(symbol, risk_params)
        if not allowed:
            return False, reason
            
        # 检查时间风控
        allowed, reason = self.check_time_allowed(risk_params)
        if not allowed:
            return False, reason
        
        # 通过所有检查，允许交易
        return True, "通过风控检查" 