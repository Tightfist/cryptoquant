# TradingView 信号追踪器

这是一个用于接收 TradingView 信号并自动执行交易的应用程序。它通过 WebSocket 服务器接收 TradingView 的警报信号，然后根据信号内容执行开仓、平仓等操作。

## 功能特点

- **WebSocket 服务器**：接收 TradingView 的警报信号
- **多标的支持**：可以同时处理多个交易对的信号
- **仓位持久化**：将仓位信息持久化到数据库，避免重启丢失
- **止盈止损**：支持设置止盈止损和追踪止损
- **交易对白名单**：可以配置允许交易的交易对列表
- **实时监控**：定期监控持仓状态和系统健康

## 安装和配置

1. 确保已安装所需的依赖：
   ```
   pip install pyyaml websockets ccxt
   ```

2. 配置 `config.yaml` 文件：
   ```yaml
   app_name: "tradingview_signal_tracker"
   exchange:
     type: "okex"  # 交易所类型: okex, binance, bybit等
     api_key: "你的API密钥"
     secret_key: "你的密钥"
     passphrase: "你的密码"
     is_simulated: false
   websocket:
     port: 8765
     host: "0.0.0.0"
   strategy:
     leverage: 3
     per_position_usdt: 100
     enable_symbol_pool: true
     default_symbols: ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
     take_profit_pct: 0.05
     stop_loss_pct: 0.03
     trailing_stop: true
     trailing_distance: 0.02
   logging:
     level: "INFO"
     file: "tradingview_signal_tracker.log"
     output_targets: ["file", "console"]
   symbol_mapping:
     "BTCUSDT.P": "BTC-USDT-SWAP"
     "ETHUSDT.P": "ETH-USDT-SWAP"
   ```

## 运行应用

```bash
cd okex
python -m apps.tradingview_signal_tracker.main
```

## TradingView 警报设置
在 TradingView 中，你需要设置 Webhook 警报，将信号发送到应用程序的 HTTP 服务器。

1. 在 TradingView 中创建一个新的警报
2. 在警报设置中，选择 "Webhook URL" 选项
3. 输入你的服务器地址，例如：`http://your-server-ip:8765/webhook`
4. 在 "消息" 字段中，输入 JSON 格式的信号

### 开仓信号示例

```json
{
  "action": "open",
  "symbol": "BTC-USDT-SWAP",
  "direction": "long",
  "leverage": 3,
  "position_usdt": 100,
  "timestamp": {{time}}
}
```

``` tradingview 警报样式
{
  "action": "{{strategy.order.comment}}",
  "symbol": "{{ticker}}",
  "direction": "{{strategy.order.action == 'buy' ? 'long' : 'short'}}",
  "leverage": 3,
  "position_usdt": 100,
  "timestamp": "{{time}}"
}
```

### 平仓信号示例

```json
{
  "action": "close",
  "symbol": "BTC-USDT-SWAP",
  "timestamp": {{time}}
}
```

### 止盈信号示例

```json
{
  "action": "tp",
  "symbol": "BTC-USDT-SWAP",
  "timestamp": {{time}}
}
```

### 止损信号示例

```json
{
  "action": "sl",
  "symbol": "BTC-USDT-SWAP",
  "timestamp": {{time}}
}
```

### 修改止盈止损信号示例

```json
{
  "action": "modify",
  "symbol": "BTC-USDT-SWAP",
  "tp_price": 50000,
  "sl_price": 45000,
  "timestamp": {{time}}
}
```

## 多标的支持

你可以在信号中使用 `symbols` 字段来同时操作多个交易对：

```json
{
  "action": "open",
  "symbols": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
  "direction": "long",
  "leverage": 3,
  "position_usdt": 100,
  "timestamp": {{time}}
}
```

## 交易对白名单管理

应用程序默认只允许交易配置文件中指定的交易对。你可以通过以下方式覆盖此限制：

1. 在配置文件中设置 `enable_symbol_pool: false` 允许所有交易对
2. 在信号中添加 `override_symbol_pool: true` 强制允许该信号的交易对

## 日志和监控

应用程序会定期记录系统状态和持仓信息。日志文件默认保存在 `logs` 目录下。

## 多交易所支持

该应用程序设计为支持多个交易所。目前已实现 OKEx 交易所的支持，可以通过配置文件中的 `exchange.type` 字段指定交易所类型：

```yaml
exchange:
  type: "okex"  # 交易所类型: okex, binance, bybit等
  api_key: "你的API密钥"
  secret_key: "你的密钥"
  passphrase: "你的密码"
  is_simulated: false
```
要添加新的交易所支持，需要：

1. 在 `src/common/websocket/client.py` 中创建新的交易所特定的 WebSocket 客户端类
2. 在 `src/common/data_cache.py` 中创建新的交易所特定的数据缓存类
3. 在 `market_client.py` 中添加新交易所的频道格式和消息处理逻辑

## 合约名称映射

TradingView 和 OKEx 使用不同的合约命名格式。例如，TradingView 可能使用 `BTCUSDT.P`，而 OKEx 使用 `BTC-USDT-SWAP`。应用程序会自动将 TradingView 的合约名称转换为 OKEx 的格式。

你可以在配置文件中自定义合约名称映射：

```yaml
symbol_mapping:
  "BTCUSDT.P": "BTC-USDT-SWAP"
  "ETHUSDT.P": "ETH-USDT-SWAP"
  # 更多映射...
```

如果没有找到映射，应用程序会尝试使用正则表达式进行转换，例如将 `BTCUSDT.P` 转换为 `BTC-USDT-SWAP`。

## 注意事项

- 确保 API 密钥具有交易权限
- 建议先在模拟环境中测试（设置 `is_simulated: true`）
- 定期备份数据库文件
- 监控日志文件以确保系统正常运行 
