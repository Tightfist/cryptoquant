async def subscribe_symbol(self, symbol):
    """订阅指定交易对的行情"""
    try:
        if self._ws_client:
            # 订阅tickers
            await self._ws_client.subscribe_tickers(symbol)
            
            # 订阅深度行情
            await self._ws_client.subscribe_depth(symbol)
            
            # 订阅K线
            await self._ws_client.subscribe_kline(symbol, period="1m")
            
            # 订阅持仓量
            if hasattr(self._ws_client, 'subscribe_open_interest'):
                await self._ws_client.subscribe_open_interest(symbol)
            
            # 订阅余额和持仓
            if hasattr(self._ws_client, 'subscribe_balance_and_position'):
                await self._ws_client.subscribe_balance_and_position(symbol)
            
            self.logger.info(f"已订阅 {symbol} 的行情数据")
        else:
            self.logger.warning(f"WebSocket客户端未初始化，无法订阅 {symbol} 行情")
            
    except Exception as e:
        self.logger.error(f"订阅 {symbol} 行情失败: {e}", exc_info=True)
        
async def subscribe_account(self):
    """订阅账户变更"""
    try:
        if self._ws_client and hasattr(self._ws_client, 'subscribe_balance_and_position'):
            await self._ws_client.subscribe_balance_and_position()
            self.logger.info("已订阅账户余额和持仓变动")
        else:
            self.logger.warning("WebSocket客户端未初始化或不支持账户订阅")
    except Exception as e:
        self.logger.error(f"订阅账户变更失败: {e}", exc_info=True)

async def subscribe(self, symbol: str):
    """订阅市场数据"""
    self.logger.info(f"开始订阅市场数据: {symbol}")
    
    try:
        if self._ws_client:
            # 订阅行情数据
            if hasattr(self._ws_client, 'subscribe_tickers'):
                await self._ws_client.subscribe_tickers(symbol)
                self.logger.info(f"已订阅行情数据: {symbol}")
            
            # 订阅深度数据
            if hasattr(self._ws_client, 'subscribe_depth'):
                await self._ws_client.subscribe_depth(symbol)
                self.logger.info(f"已订阅深度数据: {symbol}")
            
            # 订阅K线数据
            if hasattr(self._ws_client, 'subscribe_kline'):
                await self._ws_client.subscribe_kline(symbol)
                self.logger.info(f"已订阅K线数据: {symbol}")
            
            # 订阅持仓数据 - 重要!
            if hasattr(self._ws_client, 'subscribe_balance_and_position'):
                self.logger.info(f"准备订阅余额和持仓数据: {symbol}")
                await self._ws_client.subscribe_balance_and_position(symbol)
                self.logger.info(f"已成功订阅余额和持仓数据: {symbol}")
            else:
                self.logger.warning(f"WebSocket客户端不支持订阅余额和持仓数据")
                
            # 订阅成功
            self.logger.info(f"所有数据订阅完成: {symbol}")
            return True
        else:
            self.logger.error(f"WebSocket客户端未初始化，无法订阅数据: {symbol}")
            return False
    except Exception as e:
        self.logger.error(f"订阅市场数据异常: {symbol}, {e}", exc_info=True)
        return False

async def subscribe_all_positions(self):
    """订阅所有持仓的余额和仓位变化"""
    self.logger.info(f"开始订阅所有持仓的余额和仓位变化")
    
    try:
        # 确保WebSocket客户端已初始化
        if not self._ws_client:
            await self._init_websocket()
            self.logger.info("WebSocket客户端初始化完成")
            
        # 确保WebSocket客户端已连接
        if self._ws_client and not getattr(self._ws_client, 'connected', False):
            if hasattr(self._ws_client, 'connect'):
                await self._ws_client.connect()
                self.logger.info("WebSocket客户端已连接")
        
        # 检查WebSocket客户端是否支持订阅余额和持仓
        if self._ws_client and hasattr(self._ws_client, 'subscribe_balance_and_position'):
            self.logger.info(f"调用WebSocket客户端订阅所有持仓的余额和仓位变化")
            
            # 记录交易所类型
            exchange_type = getattr(self._ws_client, 'exchange_id', 'unknown')
            self.logger.info(f"当前交易所类型: {exchange_type}")
            
            # 调用订阅方法，不传入特定的symbol以订阅所有持仓
            await self._ws_client.subscribe_balance_and_position()
            self.logger.info(f"已成功订阅所有持仓的余额和仓位变化")
            return True
        else:
            self.logger.warning(f"WebSocket客户端不支持subscribe_balance_and_position方法")
            # 尝试使用其他方法订阅账户数据
            if self._ws_client and hasattr(self._ws_client, 'subscribe_account'):
                self.logger.info("尝试使用subscribe_account方法替代")
                await self._ws_client.subscribe_account()
                self.logger.info("使用subscribe_account方法成功订阅账户数据")
                return True
            elif self._ws_client and hasattr(self._ws_client, 'subscribe_balance'):
                self.logger.info("尝试使用subscribe_balance方法替代")
                await self._ws_client.subscribe_balance()
                self.logger.info("使用subscribe_balance方法成功订阅余额数据")
                return True
                
            self.logger.error(f"WebSocket客户端不支持订阅余额和持仓数据，可用方法: {dir(self._ws_client)}")
            return False
    except Exception as e:
        self.logger.error(f"订阅所有持仓数据异常: {e}", exc_info=True)
        import traceback
        self.logger.error(f"详细错误: {traceback.format_exc()}")
        return False
        
async def _init_websocket(self):
    """确保WebSocket客户端已初始化"""
    try:
        if not self._ws_client:
            from src.exchange.okex.okex_websocket import OKExWebSocketClient
            
            # 获取API配置
            api_key = self.config.get('api_key', '')
            api_secret = self.config.get('api_secret', '')
            api_passphrase = self.config.get('api_passphrase', '')
            
            # 初始化WebSocket客户端
            self._ws_client = OKExWebSocketClient(
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
                app_name=self.app_name,
                cache=self.data_cache
            )
            
            # 连接WebSocket
            await self._ws_client.connect()
            
            self.logger.info("WebSocket客户端初始化并连接成功")
    except Exception as e:
        self.logger.error(f"初始化WebSocket客户端异常: {e}", exc_info=True) 