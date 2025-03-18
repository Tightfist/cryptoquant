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
            
            self.logger.info(f"已订阅 {symbol} 的行情数据")
        else:
            self.logger.warning(f"WebSocket客户端未初始化，无法订阅 {symbol} 行情")
            
    except Exception as e:
        self.logger.error(f"订阅 {symbol} 行情失败: {e}", exc_info=True) 