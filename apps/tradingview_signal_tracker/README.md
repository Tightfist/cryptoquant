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
- **自动生成脚本**：启动后自动生成便捷的API交互脚本

## 安装和配置

1. 确保已安装所需的依赖：
   ```
   pip install -r requirements.txt
   ```

2. 配置 `config/tradingview_signal_tracker.json` 文件：
   ```json
   {
     "app_name": "tradingview_signal_tracker",  // 应用名称
      "webhook": {
        "host": "0.0.0.0",                      // 监听主机地址
        "port": 80,                             // 监听端口
        "path": "/webhook"                      // 基础路径
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

从项目根目录运行：

```bash
python apps/tradingview_signal_tracker/main.py
```

或者使用提供的脚本：

```bash
# 启动应用
./apps/tradingview_signal_tracker/scripts/run.sh

# 停止应用
./apps/tradingview_signal_tracker/scripts/stop.sh

# 重启应用
./apps/tradingview_signal_tracker/scripts/restart.sh
```

## TradingView 警报设置

在 TradingView 中，你需要设置 Webhook 警报，将信号发送到应用程序的 HTTP 服务器。

1. 在 TradingView 中创建一个新的警报
2. 在警报设置中，选择 "Webhook URL" 选项
3. 输入你的服务器地址，例如：`http://your-server-ip:80/webhook`
4. 在 "消息" 字段中，输入 JSON 格式的信号

### 信号格式示例

#### 开仓信号

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

```
// TradingView 警报模板
{
  "action": "{{strategy.order.comment}}",
  "symbol": "{{ticker}}",
  "direction": "{{strategy.order.action}}",
  "leverage": 3,
  "position_usdt": 100,
  "timestamp": "{{time}}"
}
```

#### 平仓信号

```json
{
  "action": "close",
  "symbol": "BTC-USDT-SWAP",
  "timestamp": {{time}}
}
```

#### 止盈/止损信号

```json
{
  "action": "tp",  // 或 "sl" 表示止损
  "symbol": "BTC-USDT-SWAP",
  "timestamp": {{time}}
}
```

#### 修改止盈止损信号

```json
{
  "action": "modify",
  "symbol": "BTC-USDT-SWAP",
  "take_profit_pct": 0.05,
  "stop_loss_pct": 0.03,
  "trailing_stop": true,
  "trailing_distance": 0.02,
  "timestamp": {{time}}
}
```

## HTTP API接口

应用程序提供了HTTP API接口，可以手动触发信号、平仓和查询状态。启动后会自动生成以下脚本，位于 `scripts` 目录下：

- `get_status.sh` - 获取当前状态
- `open_position.sh` - 开仓
- `close_position.sh` - 平仓指定交易对
- `modify_position.sh` - 修改持仓参数
- `close_all.sh` - 关闭所有持仓

### 使用脚本示例

```bash
# 获取当前状态
./scripts/get_status.sh -f table

# 开仓
./scripts/open_position.sh BTC-USDT-SWAP -d long -l 5 -q 0.1

# 平仓
./scripts/close_position.sh BTC-USDT-SWAP

# 修改止盈止损
./scripts/modify_position.sh BTC-USDT-SWAP -tp 0.1 -sl 0.05 -ts true

# 关闭所有持仓
./scripts/close_all.sh
```

### API端点详情

所有脚本都有详细的使用说明，可通过 `--help` 参数查看。底层API端点如下：

1. **手动触发信号**
   ```
   POST http://host:port/webhook/api/trigger
   ```

2. **手动平仓所有持仓**
   ```
   POST http://host:port/webhook/api/close_all
   ```

3. **查询当前状态**
   ```
   GET http://host:port/webhook/api/status
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

持仓监控信息包括：
- 入场价格和当前价格
- 数量和杠杆倍数
- 持仓时间
- 保证金金额
- 盈亏比例和盈亏金额
- 止盈止损价格及距离
- 追踪止损状态和价格
- 历史最高/最低价格

## 架构说明

应用程序基于公共交易框架构建，主要包含以下组件：

1. **TradingViewStrategy**：处理TradingView信号的具体策略实现
2. **TradingFramework**：提供统一的仓位管理和交易执行框架
3. **OKExMarketSubscriber**：订阅交易所行情数据
4. **OKExTrader**：处理与OKEx交易所的通信和交易执行
5. **PositionManager**：管理仓位数据的持久化和加载
6. **TradingFrameworkApiHandler**：处理HTTP API请求

## 注意事项

- 请确保你了解交易风险，本系统不对任何交易损失负责
- 建议先在模拟盘上测试策略，确认无误后再在实盘上运行
- 定期备份你的数据和配置文件
- 不要将包含API密钥的配置文件提交到版本控制系统
