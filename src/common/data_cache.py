import asyncio
from typing import Dict, Any, Optional, Callable, List
import logging
import os
import json
import time

# 添加OKExTrader导入
from src.exchange.okex.trader import OKExTrader

class DataCache:
    """线程安全的异步数据缓存"""
    def __init__(self, app_name: str = "Generic"):
        self._data: Dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self.app_name = app_name
        self.logger = logging.getLogger(f"{app_name}.datacache")
        self._custom_updaters: Dict[str, Callable] = {}
        
    def configure(self, config: Dict[str, Any]):
        """
        配置数据缓存
        
        Args:
            config: 配置字典
        """
        # 基类中提供默认实现，子类可以重写此方法
        self.logger.info(f"配置 {self.app_name} 数据缓存")
        
    @classmethod
    def from_config(cls, config: Dict[str, Any], app_name: str = None):
        """
        从配置创建数据缓存实例
        
        Args:
            config: 配置字典
            app_name: app名称，如果为None则从config中获取
            
        Returns:
            DataCache: 数据缓存实例
        """
        if app_name is None:
            app_name = config.get('app_name', "Generic")
        
        # 添加日志
        logging.getLogger(f"{app_name}.datacache").info(f"从配置创建数据缓存实例, APP: {app_name}")
        
        # 根据类型创建实例
        if cls == DataCache:  # 基类需要app_name参数
            instance = cls(app_name)
        else:  # 但OKExDataCache的__init__不接受参数
            instance = cls()
            
        instance.configure(config)
        return instance
        
    def register_updater(self, channel: str, updater: Callable):
        """
        注册自定义更新处理器
        
        Args:
            channel: 频道名称
            updater: 更新处理函数，接收(channel, data)参数
        """
        self._custom_updaters[channel] = updater
    
    async def update(self, channel: str, data: dict):
        """
        更新缓存数据
        
        Args:
            channel: 频道名称
            data: 数据内容
        """
        async with self._lock:
            # 如果有自定义处理器，则使用它
            if channel in self._custom_updaters:
                await self._custom_updaters[channel](channel, data)
                return
            # 按频道分类存储
            if channel not in self._data:
                self._data[channel] = {}
            
            # 通用更新逻辑
            inst_id = data.get('instId')
            if inst_id:
                self._data[channel][inst_id] = data
                
                # 更新缓存时间
                if channel not in self._cache_update_time:
                    self._cache_update_time[channel] = {}
                self._cache_update_time[channel][inst_id] = time.time()
                
                self.logger.debug(f"已更新 {channel}/{inst_id} 数据")

    async def get(self, channel: str, inst_id: str) -> dict:
        """
        获取指定数据
        
        Args:
            channel: 频道名称
            inst_id: 交易对ID
            
        Returns:
            dict: 缓存的数据
        """
        async with self._lock:
            return self._data.get(channel, {}).get(inst_id, {})
            
    async def get_all(self, channel: str = None) -> Dict[str, Any]:
        """
        获取所有数据
        
        Args:
            channel: 可选，指定频道
            
        Returns:
            dict: 缓存的数据
        """
        async with self._lock:
            if channel:
                return self._data.get(channel, {})
            return self._data
    
    def __len__(self) -> int:
        """获取缓存条目数"""
        count = 0
        for channel, items in self._data.items():
            count += len(items)
        return count


class OKExDataCache(DataCache):
    """OKEx特定的数据缓存实现"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 注册OKEx特定的更新处理器
        self.register_updater("funding-rate", self._update_funding_rate)
        
        # 添加用于直接API调用的trader实例
        self._direct_trader = None
        self._api_config = None
        
        # 缓存刷新时间记录
        self._last_refresh = {}
        
        # 缓存数据的最后更新时间
        self._cache_update_time = {}
        
        # 缓存过期时间（秒）
        self.cache_expiry_time = 15  # 1分钟
        
        # 持仓数据缓存过期时间（秒）
        self.position_ttl = 60  # 1分钟
        
        # 合约面值缓存
        self._contract_size_cache = {}
        
        # 持仓量滑动缓存，格式: {inst_id: {'data': [时间戳升序排列的持仓量记录], 'last_update': timestamp}}
        self._oi_sliding_cache = {}
        
        # 持仓量缓存最大容量(每个交易对)
        self._oi_cache_max_size = 1000
        
        # 持仓量自动清理阈值
        self._oi_clean_threshold = 800
        
        # 持仓量数据统计
        self._oi_stats = {
            'total_updates': 0,
            'invalid_data_count': 0,
            'last_stat_time': time.time()
        }
        
        # 持仓数据缓存
        self._position_cache = {}
        self._position_last_update = {}
        
    def configure(self, config: Dict[str, Any]):
        """
        配置OKEx数据缓存
        
        Args:
            config: 配置字典
        """
        super().configure(config)
        
        # 记录配置结构以便调试
        config_structure = {
            'has_exchange': 'exchange' in config,
            'exchange_keys': list(config.get('exchange', {}).keys()),
        }
        self.logger.info(f"OKEx数据缓存配置结构: {config_structure}")
        
        # 保存API配置 - 从config中提取OKEx所需的所有API配置
        if 'exchange' in config:
            exchange_config = config['exchange']
            required_keys = ['api_key', 'secret_key', 'passphrase']
            
            # 提取API配置
            api_config = {}
            for key in required_keys:
                if key in exchange_config:
                    api_config[key] = exchange_config[key]
            
            # 添加其他可选配置
            if 'is_simulated' in exchange_config:
                api_config['is_simulated'] = exchange_config['is_simulated']
                
            # 只有在找到所有必需的键时才设置_api_config
            if all(key in api_config for key in required_keys):
                self._api_config = api_config
                self.logger.info(f"已配置OKEx API参数，键: {list(self._api_config.keys())}")
                
                # 如果存在API配置，立即初始化trader
                try:
                    self._direct_trader = OKExTrader("datacache", self._api_config)
                    self.logger.info("初始化API调用器成功")
                except Exception as e:
                    self.logger.error(f"初始化API调用器失败: {e}")
            else:
                missing_keys = [key for key in required_keys if key not in api_config]
                self.logger.warning(f"API配置不完整，缺少以下键: {missing_keys}，将使用默认配置加载机制")
        else:
            self.logger.warning("未找到exchange配置，将使用默认配置加载机制")
        
        # 持仓量缓存配置
        cache_config = config.get('cache', {})
        self._oi_cache_max_size = cache_config.get('oi_cache_max_size', 1000)
        self._oi_clean_threshold = cache_config.get('oi_clean_threshold', 800)
        
    def _init_trader(self):
        """初始化交易对象用于API调用"""
        if self._direct_trader is not None:
            return
            
        try:
            # 使用通用配置加载器获取API配置
            from src.common.config_loader import _load_api_config
            config = _load_api_config()
            
            if config:
                # 提取OKEx所需的API配置
                required_keys = ['api_key', 'secret_key', 'passphrase']
                api_config = {}
                
                # 从config中提取API配置
                for key in required_keys:
                    if key in config:
                        api_config[key] = config[key]
                
                # 添加可选配置
                if 'is_simulated' in config:
                    api_config['is_simulated'] = config['is_simulated']
                    
                # 检查是否找到所有必需的键
                if all(key in api_config for key in required_keys):
                    self._api_config = api_config
                    self._direct_trader = OKExTrader("datacache", self._api_config)
                    self.logger.info("初始化API调用器成功")
                else:
                    missing_keys = [key for key in required_keys if key not in api_config]
                    self.logger.error(f"API配置不完整，缺少以下键: {missing_keys}")
            else:
                self.logger.error("未能加载API配置")
        except Exception as e:
            self.logger.error(f"初始化API调用器失败: {e}")
        
    async def _update_funding_rate(self, channel: str, data: dict):
        """OKEx资金费率特殊处理"""
        if channel not in self._data:
            self._data[channel] = {}
            
        inst_id = data.get('instId')
        if inst_id:
            self._data[channel][inst_id] = data
            # 特殊处理资金费率时间
            self._data[channel][inst_id]['nextFundingTime'] = int(data['fundingTime'])
            
    async def get_mark_price(self, inst_id: str) -> float:
        """
        获取标记价格，优先从缓存获取，如果缓存不可用则直接从API获取
        
        Args:
            inst_id: 交易对ID
            
        Returns:
            float: 标记价格
        """
        try:
            # 首先尝试从缓存获取
            mark_price_data = await self.get("mark-price", inst_id)
            current_time = time.time()
            
            # 检查缓存是否存在且未过期
            cache_valid = (
                mark_price_data and 
                'mark-price' in self._cache_update_time and 
                inst_id in self._cache_update_time['mark-price'] and 
                (current_time - self._cache_update_time['mark-price'][inst_id]) < self.cache_expiry_time
            )
            
            if cache_valid:
                mark_price = float(mark_price_data.get('markPx', 0.0))
                if mark_price > 0:
                    return mark_price
            
            # 如果缓存无效或过期，直接从API获取
            if not cache_valid:
                self.logger.info(f"{inst_id} 标记价格缓存过期或无效，从API获取最新数据")
                
                # 确保trader已初始化
                if self._direct_trader is None:
                    self._init_trader()
                    
                if self._direct_trader:
                    # 从API获取最新价格
                    mark_price = self._direct_trader.get_mark_price(inst_id)
                    
                    if mark_price > 0:
                        # 更新缓存
                        # 手动更新缓存
                        async with self._lock:
                            if 'mark-price' not in self._data:
                                self._data['mark-price'] = {}
                            
                            self._data['mark-price'][inst_id] = {'instId': inst_id, 'markPx': str(mark_price)}
                            
                            # 更新缓存时间
                            if 'mark-price' not in self._cache_update_time:
                                self._cache_update_time['mark-price'] = {}
                            self._cache_update_time['mark-price'][inst_id] = current_time
                            
                        self.logger.info(f"已从API更新 {inst_id} 标记价格: {mark_price}")
                        return mark_price
            
            # 如果还是获取不到价格，记录警告并返回0
            self.logger.warning(f"无法获取 {inst_id} 的标记价格，缓存和API均失败")
            return 0.0
            
        except Exception as e:
            self.logger.error(f"获取标记价格异常: {e}", exc_info=True)
            return 0.0
    
    def get_mark_price_sync(self, inst_id: str) -> float:
        """
        获取标记价格的同步版本，优先从缓存获取，如果缓存不可用则直接从API获取
        
        Args:
            inst_id: 交易对ID
            
        Returns:
            float: 标记价格
        """
        try:
            # 首先尝试从缓存获取
            if not self._direct_trader:
                self._init_trader()
                
            # 使用API直接获取
            if self._direct_trader:
                mark_price = self._direct_trader.get_mark_price(inst_id)
                if mark_price > 0:
                    return mark_price
            
            self.logger.warning(f"无法获取{inst_id}的标记价格")
            return 0.0
        except Exception as e:
            self.logger.error(f"同步获取{inst_id}标记价格异常: {e}")
            return 0.0
    
    async def get_funding_rate(self, inst_id: str) -> float:
        """
        获取资金费率
        
        Args:
            inst_id: 交易对ID
            
        Returns:
            float: 资金费率
        """
        data = await self.get("funding-rate", inst_id)
        return float(data.get('fundingRate', 0.0)) 
    
    async def get_klines(self, inst_id: str, timeframe: str = "5m", limit: int = 100) -> Dict:
        """
        获取K线数据，先检查缓存，如果没有或者缓存过期则从API获取
        
        Args:
            inst_id: 交易对ID
            timeframe: 时间周期，如 "5m"
            limit: 返回的K线数量
            
        Returns:
            Dict: 包含K线数据和元信息的字典，格式为
                {
                    "data": [...],  # K线数据列表
                    "is_fallback": bool,  # 是否为降级数据
                    "fallback_type": str,  # 降级类型，如 "expired_cache", "alternative_timeframe", "reduced_data"
                    "original_request": {"inst_id": str, "timeframe": str, "limit": int}  # 原始请求参数
                }
        """
        cache_key = f"klines:{inst_id}:{timeframe}:{limit}"
        
        # 准备返回结果结构
        result = {
            "data": [],
            "is_fallback": False,
            "fallback_type": None,
            "original_request": {"inst_id": inst_id, "timeframe": timeframe, "limit": limit}
        }
        
        # 检查缓存是否存在且是否过期 (5分钟有效期)
        current_time = time.time()
        if cache_key in self._data and "data" in self._data[cache_key]:
            # 检查缓存时间，默认5分钟有效期
            last_update = self._last_refresh.get(cache_key, 0)
            if current_time - last_update < 300:  # 5分钟 = 300秒
                self.logger.debug(f"使用缓存中的K线数据: {inst_id}")
                result["data"] = self._data[cache_key]["data"]
                return result
        
        # 缓存不存在或已过期，从API获取
        self.logger.info(f"从API获取K线数据: {inst_id}, {timeframe}")
        if not self._direct_trader:
            self._init_trader()
        
        # 保存旧缓存数据，用于潜在的回退
        old_cache_data = None
        if cache_key in self._data and "data" in self._data[cache_key]:
            old_cache_data = self._data[cache_key]["data"]
        
        # 尝试从API获取数据，添加重试机制
        max_retries = 3
        retry_delay = 1  # 初始重试延迟（秒）
        
        for retry in range(max_retries):
            try:
                # 添加更详细的日志，记录API调用前的状态
                self.logger.debug(f"开始调用get_kline_data: {inst_id}, {timeframe}, 请求数量={limit}")
                
                klines = self._direct_trader.get_kline_data(inst_id, timeframe, limit)
                
                # 添加更详细的返回数据检查
                self.logger.debug(f"获取K线数据返回: 类型={type(klines)}, 数量={len(klines) if klines else 0}")
                
                # 增强的数据有效性检查
                if klines and isinstance(klines, list) and len(klines) > 0:
                    # 记录返回的第一条数据结构，帮助调试
                    self.logger.debug(f"K线数据样例: {klines[0] if klines else 'None'}")
                    
                    # 保存到缓存
                    if cache_key not in self._data:
                        self._data[cache_key] = {}
                    
                    self._data[cache_key]["data"] = klines
                    self._last_refresh[cache_key] = current_time
                    
                    result["data"] = klines
                    return result
                else:
                    self.logger.warning(f"API返回的K线数据无效或为空: {inst_id}, 重试 {retry+1}/{max_retries}, 数据={klines}")
            except Exception as e:
                import traceback
                error_details = traceback.format_exc()
                self.logger.error(f"获取K线数据失败 (重试 {retry+1}/{max_retries}): {e}\n{error_details}")
            
            # 如果不是最后一次重试，则等待后再尝试
            if retry < max_retries - 1:
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # 指数退避
        
        # 所有重试都失败，尝试备用方案
        
        # 备用方案1: 使用过期的缓存数据
        if old_cache_data and len(old_cache_data) > 0:
            self.logger.warning(f"使用过期的缓存数据: {inst_id}, {timeframe}")
            result["data"] = old_cache_data
            result["is_fallback"] = True
            result["fallback_type"] = "expired_cache"
            return result
        
        # 备用方案2: 尝试不同的时间周期
        if timeframe != "15m" and timeframe != "1H":
            self.logger.warning(f"尝试使用不同时间周期获取数据: {inst_id}, 从 {timeframe} 切换到 15m")
            try:
                # 尝试使用15分钟周期获取数据
                backup_result = await self.get_klines(inst_id, "15m", min(limit, 100))
                if backup_result["data"] and len(backup_result["data"]) > 0:
                    backup_result["is_fallback"] = True
                    backup_result["fallback_type"] = "alternative_timeframe"
                    return backup_result
            except Exception as e:
                self.logger.error(f"备用时间周期获取失败: {e}")
        
        # 备用方案3: 尝试获取较少的数据
        if limit > 50:
            self.logger.warning(f"尝试获取较少的数据: {inst_id}, {timeframe}, 限制数量从 {limit} 减少到 50")
            try:
                reduced_result = await self.get_klines(inst_id, timeframe, 50)
                if reduced_result["data"] and len(reduced_result["data"]) > 0:
                    reduced_result["is_fallback"] = True
                    reduced_result["fallback_type"] = "reduced_data"
                    return reduced_result
            except Exception as e:
                self.logger.error(f"尝试获取较少数据失败: {e}")
        
        # 备用方案4: 对于1小时数据尝试获取15分钟数据并合并（新增加）
        if timeframe == "1H":
            self.logger.warning(f"尝试通过15分钟数据构造1小时数据: {inst_id}")
            try:
                # 尝试获取更多的15分钟数据
                m15_result = await self.get_klines(inst_id, "15m", min(limit * 4, 200))
                if m15_result["data"] and len(m15_result["data"]) >= 4:
                    # 每4条15分钟数据合并为1条1小时数据（这是简化示例，实际合并逻辑可能更复杂）
                    # 这里只是一个示例，实际应用中可能需要更复杂的合并逻辑
                    hourly_data = []
                    for i in range(0, len(m15_result["data"]), 4):
                        if i + 3 < len(m15_result["data"]):  # 确保有完整的4条数据
                            # 假设K线数据格式为 [time, open, high, low, close, vol, ...]
                            hour_data = m15_result["data"][i].copy()  # 使用第一个15分钟的时间和开盘价
                            # 找出4个周期中的最高价和最低价
                            hour_data[2] = max(float(m15_result["data"][j][2]) for j in range(i, i+4))  # high
                            hour_data[3] = min(float(m15_result["data"][j][3]) for j in range(i, i+4))  # low
                            # 使用最后一个15分钟的收盘价
                            hour_data[4] = m15_result["data"][i+3][4]  # close
                            # 累加成交量
                            hour_data[5] = sum(float(m15_result["data"][j][5]) for j in range(i, i+4))  # volume
                            hourly_data.append(hour_data)
                    
                    if hourly_data:
                        result["data"] = hourly_data
                        result["is_fallback"] = True
                        result["fallback_type"] = "constructed_from_lower_timeframe"
                        return result
            except Exception as e:
                self.logger.error(f"尝试构造1小时数据失败: {e}")
        
        # 如果所有备用方案都失败，返回空结果
        self.logger.error(f"所有获取K线数据的尝试都失败: {inst_id}, {timeframe}, {limit}")
        return result
        
    async def get_open_interest(self, inst_id: str, timeframe: str = "5m", limit: int = 100) -> Dict:
        """
        获取持仓量数据，整合了历史API和滑动缓存
        """
        # 准备返回结果结构
        result = {
            "data": [],
            "is_fallback": False,
            "fallback_type": None,
            "original_request": {"inst_id": inst_id, "timeframe": timeframe, "limit": limit}
        }
        
        # 首先尝试从缓存获取数据
        cache_key = f"open_interest:{inst_id}:{timeframe}:{limit}"
        
        # 检查缓存是否存在且是否过期 (5分钟有效期)
        current_time = time.time()
        if cache_key in self._data and "data" in self._data[cache_key]:
            # 检查缓存时间，默认5分钟有效期
            last_update = self._last_refresh.get(cache_key, 0)
            if current_time - last_update < 300:  # 5分钟 = 300秒
                self.logger.debug(f"使用缓存中的持仓量数据: {inst_id}")
                result["data"] = self._data[cache_key]["data"]
                return result
        
        # 缓存不存在或已过期，从API获取
        self.logger.info(f"从API获取持仓量数据: {inst_id}, {timeframe}")
        if not self._direct_trader:
            self._init_trader()
        
        # 保存旧缓存数据，用于潜在的回退
        old_cache_data = None
        if cache_key in self._data and "data" in self._data[cache_key]:
            old_cache_data = self._data[cache_key]["data"]
        
        # 尝试从API获取数据，添加重试机制
        max_retries = 3
        retry_delay = 1  # 初始重试延迟（秒）
        
        for retry in range(max_retries):
            try:
                # 检查trader是否有对应的方法
                if hasattr(self._direct_trader, 'get_open_interest_history'):
                    # 添加详细日志
                    self.logger.debug(f"开始调用get_open_interest_history: {inst_id}, {timeframe}, 请求数量={limit}")
                    
                    open_interest = self._direct_trader.get_open_interest_history(inst_id, timeframe, limit)
                    
                    # 添加详细的数据检查
                    self.logger.debug(f"获取持仓量数据返回: 类型={type(open_interest)}, 数量={len(open_interest) if open_interest else 0}")
                    
                    # 验证结果是否有效
                    if open_interest and isinstance(open_interest, list) and len(open_interest) > 0:
                        # 记录样例数据
                        self.logger.debug(f"持仓量数据样例: {open_interest[0] if open_interest else 'None'}")
                        
                        # 保存到缓存
                        if cache_key not in self._data:
                            self._data[cache_key] = {}
                        
                        self._data[cache_key]["data"] = open_interest
                        self._last_refresh[cache_key] = current_time
                        
                        result["data"] = open_interest
                        
                        # 如果API获取成功，现在尝试从滑动缓存获取补充数据
                        if len(open_interest) < limit:
                            # 尝试从滑动缓存获取补充数据
                            cache_result = await self.get_open_interest_from_cache(inst_id, timeframe, limit)
                            
                            if cache_result["data"]:
                                # 合并API数据和缓存数据
                                merged_data = result["data"].copy()
                                api_timestamps = {int(record['ts']) for record in merged_data}
                                
                                for cache_record in cache_result["data"]:
                                    cache_ts = int(cache_record['ts'])
                                    if cache_ts not in api_timestamps:
                                        merged_data.append(cache_record)
                                
                                # 按时间戳排序
                                merged_data.sort(key=lambda x: int(x['ts']))
                                
                                # 限制返回数量
                                merged_data = merged_data[-limit:] if len(merged_data) > limit else merged_data
                                
                                result["data"] = merged_data
                                result["is_fallback"] = True
                                result["fallback_type"] = "api_cache_merged"
                                
                                self.logger.info(f"合并API和缓存数据: {inst_id}, {timeframe}, 总计 {len(merged_data)} 条")
                        
                        return result
                    else:
                        self.logger.warning(f"API返回的持仓量数据无效或为空: {inst_id}, 重试 {retry+1}/{max_retries}, 数据={open_interest}")
                else:
                    # 如果trader没有实现方法，退出重试循环
                    self.logger.warning(f"Trader没有实现get_open_interest_history方法")
                    break
            except Exception as e:
                import traceback
                error_details = traceback.format_exc()
                self.logger.error(f"获取持仓量数据失败 (重试 {retry+1}/{max_retries}): {e}\n{error_details}")
            
            # 如果不是最后一次重试，则等待后再尝试
            if retry < max_retries - 1:
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # 指数退避
        
        # 尝试仅从滑动缓存获取数据
        cache_result = await self.get_open_interest_from_cache(inst_id, timeframe, limit)
        if cache_result["data"]:
            self.logger.info(f"API获取失败，使用滑动缓存数据: {inst_id}, {timeframe}")
            return cache_result
        
        # 所有重试都失败，尝试备用方案
        
        # 备用方案1: 使用过期的缓存数据
        if old_cache_data and len(old_cache_data) > 0:
            self.logger.warning(f"使用过期的缓存数据: {inst_id}, {timeframe}")
            result["data"] = old_cache_data
            result["is_fallback"] = True
            result["fallback_type"] = "expired_cache"
            return result
        
        # 备用方案2: 尝试获取当前持仓量而不是历史数据
        try:
            self.logger.warning(f"尝试获取当前持仓量数据: {inst_id}")
            if hasattr(self._direct_trader, 'get_open_interest'):
                current_oi = self._direct_trader.get_open_interest(inst_id)
                if current_oi and isinstance(current_oi, dict) and current_oi.get('oi'):
                    # 记录当前持仓量数据样例
                    self.logger.debug(f"当前持仓量数据: {current_oi}")
                    
                    # 创建一个只包含当前数据的列表作为替代
                    result["data"] = [current_oi]
                    result["is_fallback"] = True
                    result["fallback_type"] = "current_data_only"
                    return result
                else:
                    self.logger.warning(f"当前持仓量数据无效或为空: {current_oi}")
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            self.logger.error(f"获取当前持仓量失败: {e}\n{error_details}")
        
        # 备用方案3: 尝试获取较少的数据
        if limit > 50:
            self.logger.warning(f"尝试获取较少的持仓量数据: {inst_id}, {timeframe}, 限制数量从 {limit} 减少到 50")
            try:
                reduced_result = await self.get_open_interest(inst_id, timeframe, 50)
                if reduced_result["data"] and len(reduced_result["data"]) > 0:
                    reduced_result["is_fallback"] = True
                    reduced_result["fallback_type"] = "reduced_data"
                    return reduced_result
            except Exception as e:
                self.logger.error(f"尝试获取较少持仓量数据失败: {e}")
        
        # 如果所有备用方案都失败，返回空结果
        self.logger.error(f"所有获取持仓量数据的尝试都失败: {inst_id}, {timeframe}, {limit}")
        return result
    
    async def update_open_interest(self, inst_id: str, timeframe: str, open_interest: List):
        """
        更新持仓量缓存数据
        
        Args:
            inst_id: 交易对ID
            timeframe: 时间周期
            open_interest: 持仓量数据
        """
        # 在现有方法基础上增加对滑动缓存的支持
        cache_key = f"open_interest:{inst_id}:{timeframe}"
        
        # 更新常规缓存
        if cache_key not in self._data:
            self._data[cache_key] = {}
        
        self._data[cache_key]["data"] = open_interest
        self._last_refresh[cache_key] = time.time()
    
    async def update_open_interest_realtime(self, inst_id: str, data: Dict[str, Any]):
        """
        更新实时持仓量滑动缓存
        
        Args:
            inst_id: 交易对ID
            data: 持仓量数据，格式如 {'ts': '1634841600000', 'oi': '12345.67', ...}
        """
        if not inst_id or not data:
            return
            
        # 更新全局统计
        self._oi_stats['total_updates'] += 1
            
        # 确保缓存存在
        if inst_id not in self._oi_sliding_cache:
            self._oi_sliding_cache[inst_id] = {
                'data': [],
                'last_update': time.time(),
                'updates': 0,
                'invalid_count': 0,
                'duplicates': 0
            }
        
        cache_info = self._oi_sliding_cache[inst_id]
        
        # 提取时间戳和持仓量数据
        try:
            ts = int(data.get('ts', int(time.time() * 1000)))
            oi = data.get('oi')
            
            if not oi:
                cache_info['invalid_count'] += 1
                self._oi_stats['invalid_data_count'] += 1
                if cache_info['invalid_count'] % 10 == 0:  # 每10次记录一次警告
                    self.logger.warning(f"收到无效的持仓量数据: {inst_id}, 无效计数={cache_info['invalid_count']}, 数据={data}")
                return
            
            # 创建持仓量记录
            oi_record = {
                'ts': str(ts),
                'oi': str(oi),
                'oiCcy': data.get('oiCcy', ''),
                'instType': data.get('instType', 'SWAP'),
                'instId': inst_id
            }
            
            # 将记录添加到滑动缓存
            cache_data = cache_info['data']
            
            # 检查是否有重复记录，避免添加相同时间戳的数据
            is_duplicate = False
            for i, record in enumerate(cache_data):
                if int(record['ts']) == ts:
                    # 更新现有记录
                    cache_data[i] = oi_record
                    cache_info['last_update'] = time.time()
                    cache_info['duplicates'] += 1
                    is_duplicate = True
                    break
            
            # 如果不是重复记录，添加新记录
            if not is_duplicate:
                cache_data.append(oi_record)
                cache_info['updates'] += 1
                
                # 每100次更新打印统计信息
                if cache_info['updates'] % 100 == 0:
                    self.logger.info(f"持仓量缓存更新: {inst_id}, 记录数={len(cache_data)}, " +
                                     f"总更新={cache_info['updates']}, " +
                                     f"重复={cache_info['duplicates']}, " +
                                     f"无效={cache_info['invalid_count']}")
                
                # 按时间戳排序
                cache_data.sort(key=lambda x: int(x['ts']))
                
                # 更新最后更新时间
                cache_info['last_update'] = time.time()
            
            # 清理缓存，避免无限增长
            if len(cache_data) > self._oi_clean_threshold:
                old_len = len(cache_data)
                # 保留最新的数据
                cache_info['data'] = cache_data[-self._oi_cache_max_size:]
                self.logger.debug(f"清理持仓量滑动缓存: {inst_id}, 从 {old_len} 条减少到 {len(cache_info['data'])} 条")
                
            # 每分钟打印一次全局统计
            current_time = time.time()
            if current_time - self._oi_stats['last_stat_time'] > 60:
                self.logger.info(f"持仓量数据全局统计: 总更新={self._oi_stats['total_updates']}, " +
                                f"无效数据={self._oi_stats['invalid_data_count']}, " +
                                f"缓存币种数={len(self._oi_sliding_cache)}")
                self._oi_stats['last_stat_time'] = current_time
                
            # 更新5分钟周期的常规缓存，供策略直接访问
            self._update_5m_oi_cache(inst_id, oi_record)
            
        except Exception as e:
            self.logger.error(f"更新持仓量滑动缓存异常: {inst_id}, {e}", exc_info=True)
    
    def _update_5m_oi_cache(self, inst_id: str, oi_record: Dict[str, Any]):
        """
        更新5分钟周期的持仓量缓存，用于策略直接访问
        
        Args:
            inst_id: 交易对ID
            oi_record: 持仓量记录
        """
        try:
            # 为5m持仓量缓存直接更新
            cache_key = f"open_interest:{inst_id}:5m"
            
            # 初始化缓存
            if cache_key not in self._data:
                self._data[cache_key] = {"data": []}
                
            cache_data = self._data[cache_key]["data"]
            current_ts = int(oi_record['ts'])
            
            # 检查是否需要添加新记录
            if not cache_data:
                # 缓存为空，直接添加
                cache_data.append(oi_record)
                self._last_refresh[cache_key] = time.time()
                return
                
            # 获取最后一条记录的时间戳
            last_ts = int(cache_data[-1]['ts'])
            
            # 如果时间差超过5分钟，添加新记录
            # 否则，更新最后一条记录
            if current_ts - last_ts >= 300000:  # 5分钟 = 300000毫秒
                cache_data.append(oi_record)
                
                # 限制缓存大小
                if len(cache_data) > 100:  # 保留100条记录
                    self._data[cache_key]["data"] = cache_data[-100:]
            else:
                # 更新最后一条记录
                cache_data[-1] = oi_record
                
            # 更新刷新时间
            self._last_refresh[cache_key] = time.time()
            
        except Exception as e:
            self.logger.error(f"更新5分钟持仓量缓存异常: {inst_id}, {e}")
    
    async def get_open_interest_from_cache(self, inst_id: str, timeframe: str = "5m", limit: int = 100) -> Dict:
        """
        从滑动缓存获取持仓量数据，用于替代或补充历史API数据
        
        Args:
            inst_id: 交易对ID
            timeframe: 时间周期，如 "5m"
            limit: 返回的数据数量
            
        Returns:
            Dict: 包含持仓量数据和元信息的字典
        """
        result = {
            "data": [],
            "is_fallback": False,
            "fallback_type": None,
            "original_request": {"inst_id": inst_id, "timeframe": timeframe, "limit": limit}
        }
        
        # 检查是否有缓存数据
        if inst_id not in self._oi_sliding_cache or not self._oi_sliding_cache[inst_id]['data']:
            self.logger.debug(f"滑动缓存中没有持仓量数据: {inst_id}")
            return result
        
        # 获取缓存数据
        cache_data = self._oi_sliding_cache[inst_id]['data']
        
        # 根据timeframe和limit选择合适的数据
        # 这里需要根据timeframe进行采样，例如5m对应每5分钟取一个点
        
        # 解析timeframe
        interval_unit = timeframe[-1].lower()  # 如 "m", "h", "d"
        interval_value = int(timeframe[:-1])   # 如 "5m" => 5
        
        # 转换为毫秒
        interval_ms = 0
        if interval_unit == 'm':
            interval_ms = interval_value * 60 * 1000
        elif interval_unit == 'h':
            interval_ms = interval_value * 60 * 60 * 1000
        elif interval_unit == 'd':
            interval_ms = interval_value * 24 * 60 * 60 * 1000
        else:
            self.logger.warning(f"不支持的时间周期单位: {interval_unit}")
            return result
        
        # 获取当前时间戳（毫秒）
        current_ts = int(time.time() * 1000)
        
        # 计算起始时间戳
        start_ts = current_ts - (interval_ms * limit)
        
        # 对缓存数据进行采样
        sampled_data = []
        last_sample_ts = 0
        
        # 找出时间范围内的所有记录
        filtered_cache = [record for record in cache_data if int(record['ts']) >= start_ts]
        
        # 执行时间间隔采样
        for record in filtered_cache:
            record_ts = int(record['ts'])
            
            # 如果与上一个采样点的时间差足够大，则添加到结果中
            if record_ts - last_sample_ts >= interval_ms or not sampled_data:
                sampled_data.append(record)
                last_sample_ts = record_ts
        
        # 限制返回数量
        sampled_data = sampled_data[-limit:] if len(sampled_data) > limit else sampled_data
        
        # 设置结果
        result["data"] = sampled_data
        
        # 如果数据不足，标记为降级数据
        if len(sampled_data) < limit:
            result["is_fallback"] = True
            result["fallback_type"] = "insufficient_cache_data"
        
        self.logger.debug(f"从滑动缓存获取持仓量数据: {inst_id}, {timeframe}, 获取 {len(sampled_data)}/{limit} 条")
        return result
    
    async def get_tickers(self, inst_type: str = "SWAP") -> Dict:
        """
        获取全市场交易对ticker信息，先检查缓存，如果没有或者缓存过期则从API获取
        
        Args:
            inst_type: 产品类型，如 SWAP（永续合约）, SPOT（现货）
            
        Returns:
            Dict: 包含ticker数据和元信息的字典，格式为
                {
                    "data": [...],  # ticker数据列表
                    "is_fallback": bool,  # 是否为降级数据
                    "fallback_type": str,  # 降级类型，如 "expired_cache"
                    "original_request": {"inst_type": str}  # 原始请求参数
                }
        """
        cache_key = f"tickers:{inst_type}"
        
        # 准备返回结果结构
        result = {
            "data": [],
            "is_fallback": False,
            "fallback_type": None,
            "original_request": {"inst_type": inst_type}
        }
        
        # 检查缓存是否存在且是否过期 (2分钟有效期，比K线短一些，因为ticker变化更快)
        current_time = time.time()
        if cache_key in self._data and "data" in self._data[cache_key]:
            # 检查缓存时间，默认2分钟有效期
            last_update = self._last_refresh.get(cache_key, 0)
            if current_time - last_update < 120:  # 2分钟 = 120秒
                self.logger.debug(f"使用缓存中的ticker数据: {inst_type}")
                result["data"] = self._data[cache_key]["data"]
                return result
        
        # 缓存不存在或已过期，从API获取
        self.logger.info(f"从API获取ticker数据: {inst_type}")
        if not self._direct_trader:
            self._init_trader()
        
        # 保存旧缓存数据，用于潜在的回退
        old_cache_data = None
        if cache_key in self._data and "data" in self._data[cache_key]:
            old_cache_data = self._data[cache_key]["data"]
        
        # 尝试从API获取数据，添加重试机制
        max_retries = 3
        retry_delay = 1  # 初始重试延迟（秒）
        
        for retry in range(max_retries):
            try:
                if hasattr(self._direct_trader, 'get_tickers'):
                    # 不传递参数，因为OKExTrader.get_tickers()不接受参数
                    tickers = self._direct_trader.get_tickers()
                    
                    # 验证结果是否有效
                    if tickers and len(tickers) > 0:
                        # 保存到缓存
                        if cache_key not in self._data:
                            self._data[cache_key] = {}
                        
                        self._data[cache_key]["data"] = tickers
                        self._last_refresh[cache_key] = current_time
                        
                        result["data"] = tickers
                        return result
                    else:
                        self.logger.warning(f"API返回的ticker数据为空: {inst_type}, 重试 {retry+1}/{max_retries}")
                else:
                    # 如果trader没有实现方法，退出重试循环
                    self.logger.warning(f"Trader没有实现get_tickers方法")
                    break
            except Exception as e:
                self.logger.error(f"获取ticker数据失败 (重试 {retry+1}/{max_retries}): {e}")
            
            # 如果不是最后一次重试，则等待后再尝试
            if retry < max_retries - 1:
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # 指数退避
        
        # 所有重试都失败，尝试备用方案
        
        # 备用方案: 使用过期的缓存数据
        if old_cache_data and len(old_cache_data) > 0:
            self.logger.warning(f"使用过期的缓存数据: {inst_type}")
            result["data"] = old_cache_data
            result["is_fallback"] = True
            result["fallback_type"] = "expired_cache"
            return result
        
        # 如果所有备用方案都失败，返回空结果
        self.logger.error(f"所有获取ticker数据的尝试都失败: {inst_type}")
        return result
        
    async def get_account_balance(self) -> Dict:
        """
        获取账户余额信息，先检查缓存，如果没有或者缓存过期则从API获取
        
        Returns:
            Dict: 包含账户余额数据和元信息的字典，格式为
                {
                    "data": {...},  # 账户余额数据
                    "is_fallback": bool,  # 是否为降级数据
                    "fallback_type": str,  # 降级类型，如 "expired_cache"
                }
        """
        cache_key = "account_balance"
        
        # 准备返回结果结构
        result = {
            "data": {},
            "is_fallback": False,
            "fallback_type": None
        }
        
        # 检查缓存是否存在且是否过期 (30秒有效期，账户余额信息很重要，需要较高频率更新)
        current_time = time.time()
        if cache_key in self._data and "data" in self._data[cache_key]:
            # 检查缓存时间，默认30秒有效期
            last_update = self._last_refresh.get(cache_key, 0)
            if current_time - last_update < 30:  # 30秒
                self.logger.debug("使用缓存中的账户余额数据")
                result["data"] = self._data[cache_key]["data"]
                return result
        
        # 缓存不存在或已过期，从API获取
        self.logger.info("从API获取账户余额数据")
        if not self._direct_trader:
            self._init_trader()
        
        # 保存旧缓存数据，用于潜在的回退
        old_cache_data = None
        if cache_key in self._data and "data" in self._data[cache_key]:
            old_cache_data = self._data[cache_key]["data"]
        
        # 尝试从API获取数据，添加重试机制
        max_retries = 3
        retry_delay = 1  # 初始重试延迟（秒）
        
        for retry in range(max_retries):
            try:
                if hasattr(self._direct_trader, 'get_account_balance'):
                    balance = self._direct_trader.get_account_balance()
                    
                    # 验证结果是否有效
                    if balance:
                        # 保存到缓存
                        if cache_key not in self._data:
                            self._data[cache_key] = {}
                        
                        self._data[cache_key]["data"] = balance
                        self._last_refresh[cache_key] = current_time
                        
                        result["data"] = balance
                        return result
                    else:
                        self.logger.warning(f"API返回的账户余额数据为空, 重试 {retry+1}/{max_retries}")
                else:
                    # 如果trader没有实现方法，退出重试循环
                    self.logger.warning(f"Trader没有实现get_account_balance方法")
                    break
            except Exception as e:
                self.logger.error(f"获取账户余额数据失败 (重试 {retry+1}/{max_retries}): {e}")
            
            # 如果不是最后一次重试，则等待后再尝试
            if retry < max_retries - 1:
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # 指数退避
        
        # 所有重试都失败，尝试备用方案
        
        # 备用方案: 使用过期的缓存数据
        if old_cache_data:
            self.logger.warning("使用过期的账户余额缓存数据")
            result["data"] = old_cache_data
            result["is_fallback"] = True
            result["fallback_type"] = "expired_cache"
            return result
        
        # 如果所有备用方案都失败，返回空结果
        self.logger.error("所有获取账户余额数据的尝试都失败")
        return result

    async def get_contract_size(self, symbol: str) -> float:
        """
        获取合约面值
        
        Args:
            symbol: 合约代码
            
        Returns:
            float: 合约面值
        """
        # 检查缓存
        if symbol in self._contract_size_cache:
            return self._contract_size_cache[symbol]
            
        # 确保有trader实例
        if not self._direct_trader:
            self._init_trader()
            
        # 如果初始化失败，返回默认值
        if not self._direct_trader:
            self.logger.warning(f"无法获取合约面值，使用默认值1: {symbol}")
            return 1
            
        try:
            # 获取合约信息
            contract_info = self._direct_trader.get_contract_info(symbol, is_spot=False)
            
            if contract_info and 'data' in contract_info and len(contract_info['data']) > 0:
                ct_val = float(contract_info['data'][0].get('ctVal', 1))
                # 缓存结果
                self._contract_size_cache[symbol] = ct_val
                return ct_val
            
            # 如果获取失败，使用默认值
            self.logger.warning(f"无法获取合约面值，使用默认值1: {symbol}")
            return 1
        except Exception as e:
            self.logger.error(f"获取合约面值异常: {e}", exc_info=True)
            return 1
            
    async def get_price_before(self, symbol: str, minutes_before: int = 15) -> float:
        """
        获取指定时间前的价格
        
        Args:
            symbol: 合约代码
            minutes_before: 前多少分钟，默认15分钟
            
        Returns:
            float: 指定时间前的价格，如果无法获取则返回None
        """
        try:
            # 获取当前K线数据
            # 默认使用1m时间周期，确保能获取到精确的时间点价格
            klines = await self.get_klines(symbol, timeframe="1m", limit=minutes_before + 5)
            
            if not klines or 'data' not in klines or not klines['data']:
                self.logger.warning(f"无法获取 {symbol} 的K线数据")
                return None
                
            # 找到对应时间的K线
            # K线数据是按时间倒序排列的，最新的在前面
            data = klines['data']
            
            # 检查是否有足够的数据点
            if len(data) <= minutes_before:
                self.logger.warning(f"K线数据不足，仅有 {len(data)} 条，需要 {minutes_before} 条")
                # 如果没有足够的数据，使用最早的数据点
                if data:
                    last_kline = data[-1]
                    # 使用收盘价作为价格参考
                    return float(last_kline[4])  # 收盘价通常是第5列
                return None
                
            # 获取指定分钟前的K线
            target_kline = data[minutes_before]
            
            # 使用收盘价作为价格参考
            if len(target_kline) >= 5:
                price = float(target_kline[4])  # 收盘价通常是第5列
                self.logger.debug(f"{symbol} {minutes_before}分钟前价格: {price}")
                return price
                
            return None
        except Exception as e:
            self.logger.error(f"获取 {symbol} {minutes_before}分钟前价格异常: {e}")
            return None
            
    def get_contract_size_sync(self, symbol: str) -> float:
        """
        获取合约面值的同步版本，优先从缓存获取，如果缓存不可用则从API获取
        
        Args:
            symbol: 合约ID
            
        Returns:
            float: 合约面值
        """
        try:
            # 先从缓存获取
            if symbol in self._contract_size_cache:
                self.logger.debug(f"使用缓存中的合约面值: {symbol} = {self._contract_size_cache[symbol]}")
                return self._contract_size_cache[symbol]
                
            # 缓存不可用，从API获取
            self.logger.info(f"缓存中无法获取 {symbol} 合约面值，从API获取")
            if not self._direct_trader:
                self._init_trader()
                
            # 确保trader初始化成功
            if not self._direct_trader:
                raise Exception("无法初始化API调用器")
                
            # 获取合约信息
            contract_info = self._direct_trader.get_contract_info(symbol, is_spot=False)
            
            # 提取合约面值
            if contract_info and 'data' in contract_info and len(contract_info['data']) > 0:
                ct_val = float(contract_info['data'][0].get('ctVal', 1))
                self.logger.debug(f"获取合约面值成功: {symbol} = {ct_val}")
                
                # 存入缓存
                self._contract_size_cache[symbol] = ct_val
                return ct_val
            
            # 如果获取失败，使用默认值并记录警告
            self.logger.warning(f"无法获取合约面值，使用默认值1: {symbol}")
            self._contract_size_cache[symbol] = 1
            return 1
        except Exception as e:
            self.logger.error(f"获取合约面值异常: {e}", exc_info=True)
            # 出错时使用默认值但不缓存错误值
            return 1

    async def update_position_data(self, inst_id: str, data: Dict[str, Any]):
        """
        更新持仓数据缓存
        
        Args:
            inst_id: 合约ID
            data: 持仓数据
        """
        async with self._lock:
            cache_key = f"position:{inst_id}" if inst_id else "position:all"
            
            # 检查数据有效性
            if not data:
                self.logger.warning(f"无效的持仓数据: {data}")
                return
                
            # 更新缓存
            self._data[cache_key] = {
                "data": data,
                "timestamp": time.time()
            }
            self._last_refresh[cache_key] = time.time()
            
            self.logger.debug(f"已更新持仓数据缓存: {cache_key}", extra={
                "data_sample": str(data)[:200] + "..." if len(str(data)) > 200 else str(data)
            })
    
    async def get_position_data(self, symbol: str, force_update: bool = False) -> Dict:
        """
        获取持仓数据，先检查缓存，如果没有或者缓存过期则从API获取
        
        Args:
            symbol: 合约ID
            force_update: 是否强制从API更新
            
        Returns:
            Dict: 持仓数据
        """
        cache_key = f"position:{symbol}"
        
        # 检查是否需要强制更新或缓存过期
        if force_update or cache_key not in self._data or (time.time() - self._last_refresh.get(cache_key, 0)) > self.position_ttl:
            self.logger.info(f"从API获取持仓数据: {symbol}, 强制更新={force_update}")
            
            # 检查trader是否初始化
            if self._direct_trader is None:
                self.logger.warning(f"无法获取持仓数据: trader未初始化")
                return {}
            
            # 查询持仓数据
            try:
                positions = self._direct_trader.get_position_details(symbol)
                
                self.logger.debug(f"API返回的持仓数据: {positions}")
                
                # 格式化数据
                formatted_data = {
                    "symbol": symbol,
                    "data": None,
                    "timestamp": int(time.time() * 1000)
                }
                
                # 检查是否有该交易对的持仓
                if positions and isinstance(positions, dict):
                    # 直接使用返回的持仓数据
                    if positions.get('instId') == symbol:
                        # 收集并记录所有字段
                        field_info = {
                            "avgPx": positions.get('avgPx', '0'),  # 开仓均价
                            "availPos": positions.get('availPos', '0'),  # 可平仓数量
                            "posId": positions.get('posId', ''),  # 持仓ID
                            "posSide": positions.get('posSide', 'net'),  # 持仓方向
                            "pos": positions.get('pos', '0'),  # 持仓量
                            "realizedPnl": positions.get('realizedPnl', '0'),  # 已实现收益
                            "upl": positions.get('upl', '0'),  # 未实现收益
                            "lever": positions.get('lever', '1'),  # 杠杆
                            "uTime": positions.get('uTime', str(int(time.time() * 1000)))  # 最后更新时间
                        }
                        
                        self.logger.info(f"获取到 {symbol} 的持仓数据字段: {field_info}")
                        
                        # 设置数据
                        formatted_data["data"] = positions
                    else:
                        self.logger.warning(f"API返回的持仓数据中未找到匹配的交易对: {symbol}")
                elif positions and isinstance(positions, list):
                    # 如果返回的是列表，则遍历查找匹配的交易对
                    position_found = False
                    for pos in positions:
                        if pos.get('instId') == symbol:
                            # 找到匹配的持仓
                            position_found = True
                            
                            # 收集并记录所有字段
                            field_info = {
                                "avgPx": pos.get('avgPx', '0'),  # 开仓均价
                                "availPos": pos.get('availPos', '0'),  # 可平仓数量
                                "posId": pos.get('posId', ''),  # 持仓ID
                                "posSide": pos.get('posSide', 'net'),  # 持仓方向
                                "pos": pos.get('pos', '0'),  # 持仓量
                                "realizedPnl": pos.get('realizedPnl', '0'),  # 已实现收益
                                "upl": pos.get('upl', '0'),  # 未实现收益
                                "lever": pos.get('lever', '1'),  # 杠杆
                                "uTime": pos.get('uTime', str(int(time.time() * 1000)))  # 最后更新时间
                            }
                            
                            self.logger.info(f"获取到 {symbol} 的持仓数据字段: {field_info}")
                            
                            # 设置数据
                            formatted_data["data"] = pos
                            break
                    
                    if not position_found:
                        self.logger.warning(f"API返回的持仓数据中未找到 {symbol} 的持仓")
                else:
                    self.logger.warning(f"API未返回任何持仓数据: {positions}")
                
                # 更新缓存
                await self.update_position_data(symbol, formatted_data)
                
                return formatted_data
            except Exception as e:
                self.logger.error(f"从API获取持仓数据异常: {e}", exc_info=True)
                return {}
        else:
            # 使用缓存数据
            cached_data = self._data.get(cache_key, {}).get("data", {})
            if cached_data:
                self.logger.debug(f"使用缓存的持仓数据: {symbol}, 缓存时间: {self._last_refresh.get(cache_key, 0)}")
                # 整理返回格式，确保与API获取时格式一致
                return {
                    "symbol": symbol,
                    "data": cached_data,
                    "timestamp": self._last_refresh.get(cache_key, 0)
                }
            else:
                self.logger.warning(f"缓存中没有 {symbol} 的持仓数据")
                return {}
