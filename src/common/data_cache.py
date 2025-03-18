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
    def __init__(self, exchange_name: str = "Generic"):
        self._data: Dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self.exchange_name = exchange_name
        self.logger = logging.getLogger(f"{exchange_name}-Cache")
        self._custom_updaters: Dict[str, Callable] = {}
        
    def configure(self, config: Dict[str, Any]):
        """
        配置数据缓存
        
        Args:
            config: 配置字典
        """
        # 基类中提供默认实现，子类可以重写此方法
        self.logger.info(f"配置 {self.exchange_name} 数据缓存")
        
    @classmethod
    def from_config(cls, config: Dict[str, Any], exchange_name: str = None):
        """
        从配置创建数据缓存实例
        
        Args:
            config: 配置字典
            exchange_name: 交易所名称，如果为None则从config中获取
            
        Returns:
            DataCache: 数据缓存实例
        """
        if exchange_name is None:
            exchange_name = config.get('exchange', {}).get('name', "Generic")
        
        # 添加日志
        logging.getLogger('DataCache').info(f"从配置创建数据缓存实例, 交易所: {exchange_name}")
        
        # 根据类型创建实例
        if cls == DataCache:  # 基类需要exchange_name参数
            instance = cls(exchange_name)
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
    def __init__(self):
        super().__init__("OKEx")
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
        self.cache_expiry_time = 60  # 1分钟
        
        # 合约面值缓存
        self._contract_size_cache = {}
        
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
        if timeframe != "15m" and timeframe != "1h":
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
        if timeframe == "1h":
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
        获取持仓量数据，先检查缓存，如果没有或者缓存过期则从API获取
        
        Args:
            inst_id: 交易对ID
            timeframe: 时间周期，如 "5m"
            limit: 返回的数据数量
            
        Returns:
            Dict: 包含持仓量数据和元信息的字典，格式为
                {
                    "data": [...],  # 持仓量数据列表
                    "is_fallback": bool,  # 是否为降级数据
                    "fallback_type": str,  # 降级类型，如 "expired_cache", "current_data_only", "reduced_data"
                    "original_request": {"inst_id": str, "timeframe": str, "limit": int}  # 原始请求参数
                }
        """
        cache_key = f"open_interest:{inst_id}:{timeframe}:{limit}"
        
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
        更新持仓量数据缓存
        
        Args:
            inst_id: 交易对ID
            timeframe: 时间周期
            open_interest: 持仓量数据
        """
        for limit in [10, 50, 100]:  # 常用的limit值
            cache_key = f"open_interest:{inst_id}:{timeframe}:{limit}"
            
            if cache_key in self._data:
                # 更新现有数据
                if len(open_interest) >= limit:
                    self._data[cache_key]["data"] = open_interest[-limit:]
                else:
                    self._data[cache_key]["data"] = open_interest
                
                self._last_refresh[cache_key] = time.time()
                self.logger.debug(f"更新持仓量缓存: {cache_key}")
    
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
        获取合约面值，优先从缓存获取，如果缓存不可用则从API获取
        
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