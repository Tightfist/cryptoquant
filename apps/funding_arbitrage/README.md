# 资金费率套利策略 (Funding Rate Arbitrage)

## 概述

资金费率套利策略是一种利用永续合约资金费率与现货市场之间价差的交易策略。当永续合约的资金费率为正时，做空合约方向会收取资金费，做多方向会支付资金费。本策略通过在资金费率为正时，同时做空永续合约和做多现货，以获取资金费收益，同时对冲价格风险。

## 策略原理

1. **资金费套利机制**：
   - 当资金费率为正时，合约做空方向收取资金费，做多方向支付资金费
   - 通过做空合约和做多现货，可以在对冲价格风险的同时获取资金费收益

2. **开仓条件**：
   - 资金费率高于设定的最小阈值
   - 合约与现货之间的基差在可接受范围内
   - 账户有足够的可用资金

3. **平仓条件**：
   - 达到目标收益率
   - 触发止损线
   - 持仓时间超过最大限制
   - 资金费率由正转负

## 系统架构

### 核心组件

- **FundingArbitrage**：策略主类，负责套利逻辑实现
- **OKExTrader**：交易接口，负责与交易所交互
- **PositionManager**：仓位管理器，负责仓位的存储和检索
- **AsyncEventLoop**：异步事件循环，负责定期执行任务

### 主要功能

1. **套利机会检测** (`check_opportunity`)：
   - 检查资金费率是否满足条件
   - 检查基差是否在合理范围内
   - 检查是否已有持仓

2. **套利执行** (`execute_arbitrage`)：
   - 计算合约和现货的下单数量
   - 执行合约做空和现货做多
   - 记录持仓信息

3. **平仓条件检测** (`check_unwind_condition`)：
   - 检查是否达到目标收益
   - 检查是否触发止损
   - 检查持仓时间是否超限
   - 检查资金费率是否转负

4. **平仓执行** (`execute_unwind`)：
   - 平掉合约空头仓位
   - 卖出现货多头仓位
   - 计算并记录盈亏

5. **盈亏计算** (`calculate_unrealized_pnl`)：
   - 计算合约和现货的未实现盈亏
   - 计算总投入资金
   - 计算盈亏百分比

## 配置说明

策略配置文件需包含以下主要参数：

```json
{
  "app_name": "funding_arbitrage",  // 应用名称，用于日志和配置文件识别
  "strategy": {
    "min_funding_rate": 0.0001,     // 最小资金费率阈值，高于此值才会开仓
    "max_basis": 0.002,             // 最大基差阈值，合约与现货价差不超过此值才会开仓
    "target_profit": 0.02,          // 目标收益率，达到此值会触发平仓
    "stop_loss": -0.02,             // 止损线，亏损达到此值会触发平仓
    "max_hold_hours": 48,           // 最大持仓时间(小时)，超过此时间会触发平仓
    "leverage": 3,                  // 合约杠杆倍数
    "allow_reverse_arbitrage": false // 是否允许反向套利(资金费率为负时)
  },
  "instruments": [                  // 要监控的交易对列表
    "BTC-USDT-SWAP",                // 比特币永续合约
    "ETH-USDT-SWAP"                 // 以太坊永续合约
  ],
  "capital_efficiency": {           // 资金利用率相关配置
    "enabled": false,               // 是否启用高资金利用率模式
    "usage_ratio": 0.8,             // 资金使用比例
    "spot_ratio": 0.3,              // 现货资金比例
    "swap_ratio": 0.7               // 合约资金比例
  },
  "event_loop": {                   // 事件循环配置
    "check_interval": 60,           // 检查套利机会间隔(秒)
    "unwind_check_interval": 300,   // 检查平仓条件间隔(秒)
    "max_workers": 5                // 最大工作线程数
  },
  "exchange": {                     // 交易所API配置
    "api_key": "your_api_key",      // 交易所API密钥
    "secret_key": "your_secret_key", // 交易所密钥
    "passphrase": "your_passphrase", // 交易所API密码
    "is_simulated": false           // 是否为模拟交易
  },
  "logging": {                      // 日志配置
    "level": "INFO",                // 日志级别
    "file": "funding_arbitrage.log", // 日志文件名
    "output_targets": ["file", "console"] // 日志输出目标
  }
}
```

## 使用方法

1. **安装依赖**：
   ```bash
   pip install -r requirements.txt
   ```

2. **配置策略**：
   - 复制 `config/funding_arbitrage.example.json` 为 `config/funding_arbitrage.json`
   - 根据需要修改配置参数

3. **运行策略**：
   ```bash
   python apps/funding_arbitrage/funding_arbitrage.py
   ```

## 注意事项

1. **资金安全**：
   - 请确保API密钥权限设置合理，建议只开启交易权限
   - 建议先使用模拟交易测试策略

2. **风险控制**：
   - 合理设置止损和目标收益
   - 控制单次交易资金比例
   - 定期检查策略运行状态

3. **技术限制**：
   - 市价单可能存在滑点
   - 交易所API可能存在延迟
   - 现货卖出时会考虑手续费，预留0.5%缓冲

## 日志说明

策略运行时会生成详细日志，包括：
- 套利机会检测结果
- 开仓和平仓详情
- 订单执行状态
- 盈亏计算详情
- 错误和异常信息

日志文件默认保存在 `logs/funding_arbitrage/` 目录下。

## 开发计划

1. **功能优化**：
   - 实现订单成交推送回调，获取实际成交数量
   - 优化资金分配算法
   - 增加更多平仓条件选项

2. **风险管理**：
   - 增加风险度监控
   - 实现动态止损策略
   - 增加资金管理模块

3. **性能提升**：
   - 优化API请求频率
   - 实现本地缓存机制
   - 提高事件循环效率 