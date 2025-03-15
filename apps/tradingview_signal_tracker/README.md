# TradingView 信号追踪器

这是一个用于接收 TradingView 信号并自动执行交易的应用程序。它通过 WebSocket 服务器接收 TradingView 的警报信号，同时提供HTTP API接口，可以手动触发信号和查询状态，根据信号内容执行开仓、平仓等操作。

## 功能特点

- **WebSocket 服务器**：接收 TradingView 的警报信号
- **HTTP API接口**：提供手动触发信号、平仓和查询状态的接口
- **多标的支持**：可以同时处理多个交易对的信号
- **仓位持久化**：将仓位信息持久化到数据库，避免重启丢失
- **止盈止损**：支持设置止盈止损和追踪止损（基于未杠杆化的收益率）
- **交易对白名单**：可以配置允许交易的交易对列表
- **实时监控**：定期监控持仓状态和系统健康
- **价格异常保护**：防止因价格为0或异常导致的错误计算和操作

## 安装和配置

1. 确保已安装所需的依赖：
   ```
   pip install websockets tabulate
   ```

2. 配置 `config/tradingview_signal_tracker.json` 文件：
   ```json
   {
     "app_name": "tradingview_signal_tracker",  // 应用名称
      "webhook": {
        "host": "0.0.0.0",
        "port": 80,
        "path": "/webhook"
      },
     "strategy": {                              // 策略配置
       "leverage": 3,                           // 默认杠杆倍数
       "per_position_usdt": 100,                // 每个仓位的USDT金额
       "enable_symbol_pool": true,              // 是否启用交易对白名单
       "default_symbols": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"], // 允许交易的交易对列表
       "take_profit_pct": 0.05,                 // 止盈百分比（未杠杆化）
       "stop_loss_pct": 0.03,                   // 止损百分比（未杠杆化）
       "trailing_stop": true,                   // 是否启用追踪止损
       "trailing_distance": 0.02,               // 追踪止损距离
       "unit_type": "quote"                     // 委托单位类型：quote(USDT)、base(币)、contract(张)
     },
     "logging": {                               // 日志配置
       "level": "INFO",                         // 日志级别
       "file": "tradingview_signal_tracker.log", // 日志文件名
       "output_targets": ["file", "console"]    // 日志输出目标
     },
     "symbol_mapping": {                        // 交易对名称映射
       "BTCUSDT.P": "BTC-USDT-SWAP",            // 比特币永续合约映射
       "ETHUSDT.P": "ETH-USDT-SWAP"             // 以太坊永续合约映射
     }
   }
   ```

## 运行应用

```bash
python apps/tradingview_signal_tracker/main.py
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
  "direction": "{{strategy.order.action}}",
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

## HTTP API接口

应用程序提供了HTTP API接口，可以手动触发信号、平仓和查询状态。

### 1. 手动触发信号

```
POST http://your-server-ip:8765/webhook/api/trigger
```

请求体示例：
```json
{
  "action": "open",
  "symbol": "BTC-USDT-SWAP",
  "direction": "long",
  "leverage": 3,
  "position_usdt": 100
}
```

### 2. 手动平仓所有持仓

```
POST http://your-server-ip:8765/webhook/api/close_all
```

### 3. 查询当前状态

```
GET http://your-server-ip:8765/webhook/api/status
```

响应示例：
```json
{
  "status": "success",
  "positions": {
    "position_count": 1,
    "positions": {
      "BTC-USDT-SWAP": {
        "entry_price": 50000,
        "current_price": 51000,
        "quantity": 0.01,
        "pnl_percent": "2.00%",
        "leveraged_pnl_percent": "6.00%",
        "position_type": "swap",
        "leverage": 3,
        "timestamp": 1642035502
      }
    }
  },
  "allowed_symbols": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
  "config": {
    "leverage": 3,
    "per_position_usdt": 100,
    "take_profit_pct": 0.05,
    "stop_loss_pct": 0.03,
    "trailing_stop": true,
    "trailing_distance": 0.02,
    "unit_type": "quote",
    "enable_symbol_pool": true
  }
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

## 止盈止损机制

应用程序支持设置止盈止损和追踪止损，所有止盈止损判断都基于**未杠杆化的收益率**。

- **止盈**：当未杠杆收益率达到设定的止盈比例时触发平仓
- **止损**：当未杠杆收益率低于设定的止损比例时触发平仓
- **追踪止损**：
  - 多仓：当价格从最高点回落超过设定的追踪距离时触发平仓
  - 空仓：当价格从最低点反弹超过设定的追踪距离时触发平仓

例如，如果设置止盈比例为5%，杠杆为3倍，则当未杠杆收益率达到5%（杠杆后为15%）时触发止盈。

## 交易对白名单管理

应用程序默认只允许交易配置文件中指定的交易对。你可以通过以下方式覆盖此限制：

1. 在配置文件中设置 `enable_symbol_pool: false` 允许所有交易对
2. 在信号中添加 `override_symbol_pool: true` 强制允许该信号的交易对

## 价格异常保护

应用程序会检查价格是否有效，防止因价格为0或异常导致的错误计算和操作：

- 当获取到的价格为0或无效时，会跳过收益率计算和止盈止损检查
- 当计算出的收益率异常（超过100%）时，会跳过止盈止损检查
- 日志中会清晰显示价格和收益率信息，便于排查问题

## 日志和监控

应用程序会定期记录系统状态和持仓信息。日志文件默认保存在 `logs` 目录下。

日志中会显示详细的持仓信息，包括：
- 持仓方向
- 杠杆倍数
- 入场价格
- 当前价格
- 未杠杆收益率
- 杠杆后收益率

## 多交易所支持

该应用程序设计为支持多个交易所。目前已实现 OKEx 交易所的支持，可以通过配置文件中的 `exchange.type` 字段指定交易所类型：

```json
{
  "exchange": {
    "type": "okex",
    "api_key": "你的API密钥",
    "secret_key": "你的密钥",
    "passphrase": "你的密码",
    "is_simulated": false
  }
}
```

要添加新的交易所支持，需要：

1. 在 `src/common/websocket/client.py` 中创建新的交易所特定的 WebSocket 客户端类
2. 在 `src/common/data_cache.py` 中创建新的交易所特定的数据缓存类
3. 在 `market_client.py` 中添加新交易所的频道格式和消息处理逻辑

## 合约名称映射

TradingView 和 OKEx 使用不同的合约命名格式。例如，TradingView 可能使用 `BTCUSDT.P`，而 OKEx 使用 `BTC-USDT-SWAP`。应用程序会自动将 TradingView 的合约名称转换为 OKEx 的格式。

你可以在配置文件中自定义合约名称映射：

```json
{
  "symbol_mapping": {
    "BTCUSDT.P": "BTC-USDT-SWAP",
    "ETHUSDT.P": "ETH-USDT-SWAP"
  }
}
```

如果没有找到映射，应用程序会尝试使用正则表达式进行转换，例如将 `BTCUSDT.P` 转换为 `BTC-USDT-SWAP`。

## 脚本工具

在 `scripts` 目录下提供了一些实用的脚本工具，可以帮助你快速发送API请求：

- `tv_api.sh`：用于发送API请求的Shell脚本，支持查询状态、手动触发信号和平仓等操作

使用示例：
```bash
# 查询状态
./tv_api.sh status

# 开仓
./tv_api.sh open BTC-USDT-SWAP long 3 100

# 平仓
./tv_api.sh close BTC-USDT-SWAP

# 平仓所有持仓
./tv_api.sh close_all
```

## 注意事项

- 确保 API 密钥具有交易权限
- 建议先在模拟环境中测试（设置 `is_simulated: true`）
- 定期备份数据库文件
- 监控日志文件以确保系统正常运行
- 止盈止损判断基于未杠杆化的收益率，而不是杠杆后的收益率
- 价格为0或异常时会跳过收益率计算和止盈止损检查
