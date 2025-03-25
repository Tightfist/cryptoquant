import time

async def _process_message(self, data):
    """处理WebSocket消息"""
    try:
        # 处理PING/PONG消息
        if 'event' in data and data['event'] == 'ping':
            await self._handle_ping(data)
            return
            
        # 处理订阅成功消息
        if 'event' in data and data['event'] == 'subscribe':
            self.logger.info(f"订阅成功: {data.get('arg', {})}")
            return
            
        # 处理行情数据推送
        if 'arg' in data and 'data' in data:
            channel = data['arg'].get('channel')
            inst_id = data['arg'].get('instId')
            
            if channel == 'tickers':
                await self._handle_ticker_update(inst_id, data['data'])
                
            elif channel == 'books5':
                await self._handle_depth_update(inst_id, data['data'])
                
            elif channel == 'trades':
                await self._handle_trade_update(inst_id, data['data'])
            
            elif channel == 'candle1m' or channel == 'candle5m' or channel == 'candle15m' or channel == 'candle1H':
                await self._handle_kline_update(inst_id, channel, data['data'])
            
            # 新增：处理持仓量更新
            elif channel == 'open-interest':
                await self._handle_open_interest_update(inst_id, data['data'])
                
            # 新增：处理余额和持仓更新
            elif channel == 'balance_and_position':
                await self._handle_balance_position_update(inst_id, data['data'])
                
            else:
                self.logger.debug(f"未处理的频道数据: {channel}, {inst_id}")
            
            # 更新缓存
            if self.cache:
                await self.cache.update(f"{channel}:{inst_id}", data)
            
            return
            
        # 处理其他消息
        self.logger.debug(f"收到其他消息: {data}")
        
    except Exception as e:
        self.logger.error(f"处理WebSocket消息异常: {e}", exc_info=True)

async def _handle_open_interest_update(self, inst_id, data_list):
    """处理持仓量更新"""
    try:
        if not data_list or not isinstance(data_list, list):
            self.logger.warning(f"无效的持仓量数据格式: {data_list}")
            return
            
        for data in data_list:
            self.logger.debug(f"收到持仓量更新: {inst_id}, {data}")
            
            # 更新滑动缓存
            if self.cache and hasattr(self.cache, 'update_open_interest_realtime'):
                await self.cache.update_open_interest_realtime(inst_id, data)
    except Exception as e:
        self.logger.error(f"处理持仓量更新异常: {inst_id}, {e}", exc_info=True)

async def _handle_balance_position_update(self, inst_id, data_list):
    """处理余额和持仓更新"""
    try:
        if not data_list or not isinstance(data_list, list):
            self.logger.warning(f"无效的余额和持仓数据格式: {data_list}")
            return
            
        self.logger.info(f"收到余额和持仓WebSocket推送更新: {inst_id}")
        self.logger.debug(f"推送数据详情: {data_list}")
            
        for data in data_list:
            # 检查是否是持仓数据推送
            if 'posData' in data:
                self.logger.info(f"收到持仓推送数据: {data['posData']}")
                
                # 详细记录持仓字段
                for pos in data.get('posData', []):
                    pos_info = {
                        'instId': pos.get('instId', ''),
                        'posId': pos.get('posId', ''),
                        'avgPx': pos.get('avgPx', '0'),
                        'pos': pos.get('pos', '0'),
                        'availPos': pos.get('availPos', '0'),
                        'upl': pos.get('upl', '0'),
                        'realizedPnl': pos.get('realizedPnl', '0'),
                        'posSide': pos.get('posSide', ''),
                        'uTime': pos.get('uTime', '')
                    }
                    self.logger.info(f"持仓字段详情: {pos_info}")
                    
                    # 更新具体交易对的持仓缓存
                    if self.cache and hasattr(self.cache, 'update_position_data'):
                        position_symbol = pos.get('instId')
                        if position_symbol:
                            # 为每个持仓创建格式化的数据结构
                            formatted_data = {
                                "symbol": position_symbol,
                                "data": pos,  # 直接使用推送的持仓对象
                                "timestamp": int(time.time() * 1000)
                            }
                            
                            # 更新缓存
                            await self.cache.update_position_data(position_symbol, formatted_data)
                            self.logger.info(f"已将持仓数据更新到缓存: {position_symbol}")
            
            # 检查是否是资金数据推送
            if 'balData' in data:
                self.logger.info(f"收到资金推送数据: {data['balData']}")
            
    except Exception as e:
        self.logger.error(f"处理余额和持仓更新异常: {inst_id}, {e}", exc_info=True)
        import traceback
        self.logger.error(f"详细异常堆栈: {traceback.format_exc()}")

async def subscribe_tickers(self, inst_id):
    """订阅交易行情频道"""
    channels = [{
        "channel": "tickers",
        "instId": inst_id
    }]
    await self.subscribe(channels)
    
async def subscribe_depth(self, inst_id, depth="5"):
    """订阅深度行情频道"""
    channels = [{
        "channel": f"books{depth}",
        "instId": inst_id
    }]
    await self.subscribe(channels)
    
async def subscribe_trades(self, inst_id):
    """订阅交易频道"""
    channels = [{
        "channel": "trades",
        "instId": inst_id
    }]
    await self.subscribe(channels)
    
async def subscribe_kline(self, inst_id, period="1m"):
    """订阅K线频道"""
    channels = [{
        "channel": f"candle{period}",
        "instId": inst_id
    }]
    await self.subscribe(channels)
    
async def subscribe_open_interest(self, inst_id):
    """订阅持仓量频道"""
    channels = [{
        "channel": "open-interest",
        "instId": inst_id
    }]
    await self.subscribe(channels)

async def subscribe_balance_and_position(self, inst_id=None):
    """
    订阅余额和持仓变动频道
    
    Args:
        inst_id: 可选，指定交易对。如果不提供，则订阅所有持仓变动。
    """
    channels = []
    
    if inst_id:
        # 订阅特定交易对的持仓变动
        channels.append({
            "channel": "balance_and_position",
            "instId": inst_id
        })
    else:
        # 订阅所有持仓变动
        channels.append({
            "channel": "balance_and_position"
        })
    
    await self.subscribe(channels)
    self.logger.info(f"已订阅{'特定'+inst_id if inst_id else '所有'}余额和持仓变动") 