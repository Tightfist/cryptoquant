# 交易工具 (Trader Tools)

## 概述

交易工具是一个命令行工具，用于快速执行常见的交易操作，如下单、平仓、查询余额等。它提供了简单直观的命令行界面，让用户能够方便地进行交易操作，而无需通过交易所网站或复杂的 API 调用。

## 功能特点

- **快速下单**：支持市价单、限价单等多种订单类型
- **批量操作**：支持同时对多个交易对进行操作
- **仓位计算**：自动计算合适的仓位大小
- **杠杆设置**：支持设置杠杆倍数
- **余额查询**：快速查看账户余额
- **IPO 申购**：支持参与新币发行

## 安装和配置

1. 确保已安装所需的依赖：
   ```bash
   pip install tabulate
   ```

2. 配置 `config/trader_tools.json` 文件：
   ```json
   {
     "app_name": "trader_tools",                // 应用名称
     "logging": {                               // 日志配置
       "level": "INFO",                         // 日志级别
       "file": "trader_tools.log",              // 日志文件名
       "output_targets": ["file", "console"]    // 日志输出目标
     },
     "trading": {                               // 交易相关配置
       "default_leverage": 3,                   // 默认杠杆倍数
       "default_price_type": "market"           // 默认价格类型(市价单、限价单)
     },
     "symbols": [                               // 支持的交易对列表
       {
         "name": "BTC-USDT-SWAP",               // 交易对名称
         "description": "比特币永续合约",        // 交易对描述
         "min_amount": 5                        // 最小交易金额(USDT)
       },
       {
         "name": "ETH-USDT-SWAP",               // 交易对名称
         "description": "以太坊永续合约",        // 交易对描述
         "min_amount": 5                        // 最小交易金额(USDT)
       },
       {
         "name": "SOL-USDT-SWAP",               // 交易对名称
         "description": "索拉纳永续合约",        // 交易对描述
         "min_amount": 5                        // 最小交易金额(USDT)
       },
       {
         "name": "AVAX-USDT-SWAP",              // 交易对名称
         "description": "雪崩永续合约",          // 交易对描述
         "min_amount": 5                        // 最小交易金额(USDT)
       },
       {
         "name": "CRV-USDT-SWAP",               // 交易对名称
         "description": "CRV永续合约",           // 交易对描述
         "min_amount": 5                        // 最小交易金额(USDT)
       },
       {
         "name": "ONDO-USDT-SWAP",              // 交易对名称
         "description": "ONDO永续合约",          // 交易对描述
         "min_amount": 5                        // 最小交易金额(USDT)
       }
     ]
   }
   ```

## 使用方法

1. 复制示例配置文件：
   ```bash
   cp config/trader_tools.example.json config/trader_tools.json
   ```

2. 修改配置文件，填入您的 API 密钥和其他参数

3. 运行应用程序：
   ```bash
   python apps/trader_tools/trader_tools.py [命令] [参数]
   ```

### 命令示例

1. **下单**：
   ```bash
   python apps/trader_tools/trader_tools.py order btc long 20 3
   ```
   这将在 BTC-USDT-SWAP 上开多仓，使用 20 USDT 保证金和 3 倍杠杆。

2. **平仓**：
   ```bash
   python apps/trader_tools/trader_tools.py close btc
   ```
   这将平掉 BTC-USDT-SWAP 上的仓位。

3. **查询余额**：
   ```bash
   python apps/trader_tools/trader_tools.py balance
   ```
   这将显示账户余额。

4. **IPO 申购**：
   ```bash
   python apps/trader_tools/trader_tools.py ipo new-token 100
   ```
   这将参与 new-token 的 IPO 申购，使用 100 USDT。

## 配置说明

- **app_name**: 应用名称，用于日志和配置文件识别
- **logging**: 日志配置
  - **level**: 日志级别 (INFO, DEBUG, WARNING, ERROR)
  - **file**: 日志文件名
  - **output_targets**: 日志输出目标 (file, console)
- **trading**: 交易相关配置
  - **default_leverage**: 默认杠杆倍数
  - **default_price_type**: 默认价格类型(市价单、限价单)
- **symbols**: 交易对配置
  - **name**: 交易对名称
  - **description**: 交易对描述
  - **min_amount**: 最小交易金额(USDT)

## 命令参数

### order 命令
```
order <symbol> <direction> <amount> [leverage] [price_type]
```
- **symbol**: 交易对简称 (btc, eth 等)
- **direction**: 方向 (long, short)
- **amount**: 保证金金额 (USDT)
- **leverage**: 杠杆倍数 (可选，默认使用配置中的值)
- **price_type**: 价格类型 (market, limit, 可选)

### close 命令
```
close <symbol>
```
- **symbol**: 交易对简称 (btc, eth 等)

### balance 命令
```
balance
```
不需要额外参数。

### ipo 命令
```
ipo <token> <amount>
```
- **token**: 代币名称
- **amount**: 申购金额 (USDT)

## 注意事项

1. **资金安全**：
   - 请确保 API 密钥的权限设置合理
   - 建议先使用模拟交易测试工具功能

2. **交易风险**：
   - 合理设置杠杆倍数和仓位大小
   - 注意市场波动风险
   - 定期检查持仓状态

3. **使用建议**：
   - 熟悉各命令的参数和功能再进行实际交易
   - 保持日志记录，方便排查问题

## 开发计划

1. **功能扩展**：
   - 添加更多订单类型支持
   - 实现止盈止损功能
   - 添加定时任务支持

2. **用户体验**：
   - 提供更友好的命令行界面
   - 添加交互式模式
   - 支持配置文件热加载

3. **安全性**：
   - 增加风险控制机制
   - 添加交易确认步骤
   - 实现交易日志审计 