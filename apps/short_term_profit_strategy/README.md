# 短线逐利策略

这是一个基于交易框架实现的短线逐利策略，专注于捕捉短期价格波动带来的交易机会。该策略通过多因子分析和风险控制机制，在加密货币市场中寻找短期交易机会。

## 策略核心逻辑

### 1. 标的筛选模块

- **流动性过滤**：优先选择24小时成交量排名前50-100的标的，排除流动性不足的币种
- **波动性筛选**：计算标的的ATR（平均真实波幅），选择5分钟ATR≥0.5%的品种
- **异动排除机制**：剔除过去1小时价格振幅超过15%的异常波动标的

### 2. 买入信号增强

多因子复合验证：
- **价格因子**：5分钟涨幅≥2%（高于市场95%分位数）
- **持仓量因子**：5分钟持仓增长率≥5%
- **成交量因子**：当前5分钟成交量＞过去24小时同周期均量×3

### 3. 卖出信号优化

- **主动止盈**：固定止盈或者跟踪止盈
- **固定止损**：设置固定的止损比例
- **强制平仓**：当持仓量5分钟内下降3%或5分钟跌幅>2%时触发平仓

### 4. 参数动态调整机制

- 每4小时更新一次参数，根据市场波动性调整买入和卖出阈值

### 5. 风险控制系统

- **冷却期机制**：同币种两次交易间隔≥30分钟（防范高频磨损）
- **日交易上限**：总次数≤50次，亏损达50%停止当日交易

## 安装和使用

### 安装依赖

确保已经安装了Python 3.7+和所需依赖：

```bash
pip install -r requirements.txt
```

### 配置

在`config`目录下创建`short_term_profit_strategy.json`配置文件，或者首次运行时将自动创建默认配置。

配置文件示例：

```json
{
    "app_name": "short_term_profit_strategy",
    "exchange": {
        "api_key": "YOUR_API_KEY",
        "secret_key": "YOUR_SECRET_KEY",
        "passphrase": "YOUR_PASSPHRASE",
        "type": "okex"
    },
    "strategy": {
        "leverage": 5,
        "per_position_usdt": 200,
        "unit_type": "quote",
        "enable_symbol_pool": true,
        
        "top_volume_limit": 100,
        "min_atr_pct": 0.5,
        "max_price_change_pct": 15.0,
        
        "min_price_increase_pct": 2.0,
        "min_position_growth_pct": 5.0,
        "volume_increase_ratio": 3.0,
        
        "fixed_take_profit_pct": 5.0,
        "fixed_stop_loss_pct": 3.0,
        "use_trailing_stop": true,
        "trailing_distance_pct": 2.0,
        "position_decrease_threshold": 3.0,
        "price_drop_threshold": 2.0,
        
        "cooling_period_minutes": 30,
        "max_daily_trades": 50,
        "max_daily_loss_pct": 50.0,
        
        "param_update_hours": 4
    },
    "http_server": {
        "enabled": true,
        "host": "0.0.0.0",
        "port": 8080
    }
}
```

### 运行

```bash
# 直接运行
python apps/short_term_profit_strategy/main.py

# 或者使用nohup在后台运行
nohup python apps/short_term_profit_strategy/main.py > logs/short_term_profit_strategy.log 2>&1 &
```

## API接口

策略提供了HTTP API接口，可以通过API触发扫描、查询状态、重置计数器等操作。

### 触发市场扫描

```
POST /api/trigger
Content-Type: application/json

{
    "type": "scan"
}
```

### 查询策略状态

```
POST /api/trigger
Content-Type: application/json

{
    "type": "status"
}
```

### 重置每日计数器

```
POST /api/trigger
Content-Type: application/json

{
    "type": "reset"
}
```

### 更新策略参数

```
POST /api/trigger
Content-Type: application/json

{
    "type": "update_params"
}
```

### 手动开仓

```
POST /api/trigger
Content-Type: application/json

{
    "type": "open",
    "symbol": "BTC-USDT-SWAP",
    "direction": "long",
    "take_profit_pct": 0.05,
    "stop_loss_pct": 0.03,
    "trailing_stop": true,
    "trailing_distance": 0.02,
    "leverage": 5
}
```

### 手动平仓

```
POST /api/trigger
Content-Type: application/json

{
    "type": "close",
    "symbol": "BTC-USDT-SWAP"
}
```

## 信号格式说明

### 开仓信号

开仓信号采用标准化的`TradeSignal`格式，包含以下字段：

```python
{
    "action": "open",
    "symbol": "BTC-USDT-SWAP",
    "direction": "long",
    "take_profit_pct": 0.05,
    "stop_loss_pct": 0.03,
    "trailing_stop": True,
    "trailing_distance": 0.02,
    "leverage": 5,
    "unit_type": "quote",
    "extra_data": {
        "strategy": "short_term_profit",
        "price_change_pct": 2.5,
        "position_change_pct": 6.3,
        "volume_ratio": 3.8
    }
}
```

### 平仓信号

平仓信号格式：

```python
{
    "action": "close",
    "symbol": "BTC-USDT-SWAP",
    "extra_data": {
        "strategy": "short_term_profit",
        "reason": "forced_exit"
    }
}
```

## 日志说明

策略将详细日志记录在`logs`目录下，包含以下信息：

1. 策略初始化信息
2. 市场扫描过程
3. 信号检测结果
4. 交易执行情况
5. 风控处理过程
6. 异常和错误信息

可以通过查看日志了解策略运行状态和性能。 