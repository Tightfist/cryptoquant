# CryptoQuant 量化交易系统

这是一个加密货币量化交易系统，目前支持 OKEx 交易所 API，提供多种交易策略和工具。系统采用模块化设计，包含多个独立的应用程序，共享底层的交易和数据处理组件。

## 项目结构

```
cryptoquant/
├── apps/                      # 应用程序目录
│   ├── funding_arbitrage/     # 资金费率套利策略
│   ├── price/                 # 价格监控工具
│   ├── trader_tools/          # 交易工具集
│   ├── tradingview_signal_tracker/ # TradingView信号追踪器
│   └── trend_following/       # 趋势跟踪策略 (待实现)
├── src/                       # 共享源代码
│   ├── common/                # 通用组件
│   │   ├── config_loader.py   # 配置加载器
│   │   ├── event_loop.py      # 事件循环
│   │   ├── logger.py          # 日志工具
│   │   ├── order_utils.py     # 订单工具
│   │   └── position_manager.py # 仓位管理器
│   └── exchange/              # 交易所接口
│       └── okex/              # OKEx交易所适配器
│           └── trader.py      # OKEx交易执行器
├── config/                    # 配置文件目录
│   ├── api.json               # 交易所API通用配置
│   ├── funding_arbitrage.json # 资金费率套利特定配置
│   ├── price.json             # 价格监控特定配置
│   ├── trader_tools.json      # 交易工具特定配置
│   ├── tradingview_signal_tracker.json # TradingView信号追踪器特定配置
│   └── trend_following.json   # 趋势跟踪特定配置
└── logs/                      # 日志目录
```

## 应用程序

### TradingView信号追踪器

TradingView信号追踪器可以接收并执行来自TradingView的交易信号，支持开仓、平仓、止盈止损等操作。

**主要功能**:
- 接收TradingView的Webhook信号
- 自动执行开仓和平仓操作
- 支持止盈止损设置
- 支持多种委托单位类型
- 支持交易对白名单管理

**使用方法**:
```bash
python -m cryptoquant.apps.tradingview_signal_tracker.main
```

### 资金费率套利

资金费率套利策略利用永续合约的资金费率进行套利，在资金费率为正时做空合约并同时做多现货，赚取资金费。

**主要功能**:
- 自动检测套利机会
- 执行合约做空和现货做多的套利组合
- 支持多种平仓条件
- 自动计算盈亏

**使用方法**:
```bash
python -m cryptoquant.apps.funding_arbitrage.funding_arbitrage
```

### 交易工具集

交易工具集提供了一系列命令行工具，用于执行常见的交易操作，如批量下单、平仓、查询余额等。

**主要功能**:
- 批量下单
- 批量平仓
- 查询账户余额
- 打新币

**使用方法**:
```bash
python -m cryptoquant.apps.trader_tools.trader_tools --mode order --symbols btc eth --amount 100 --leverage 3 --side buy --price-type market
python -m cryptoquant.apps.trader_tools.trader_tools --mode close --symbols btc eth --price-type market
python -m cryptoquant.apps.trader_tools.trader_tools --mode balance
```

## 共享组件

### 订单工具 (order_utils.py)

提供通用的订单计算功能，如计算订单大小等。

```python
from src.common.order_utils import calculate_order_size

# 计算订单大小
size, tgt_ccy = calculate_order_size(
    trader=trader,
    symbol="BTC-USDT-SWAP",
    position_usdt=100,
    unit_type="quote",
    leverage=3,
    side="buy"
)
```

### 仓位管理器 (position_manager.py)

管理交易仓位，支持仓位的保存、加载和更新。

```python
from src.common.position_manager import PositionManager, Position

# 创建仓位管理器
position_mgr = PositionManager("app_name")

# 创建仓位
position = Position(
    symbol="BTC-USDT-SWAP",
    position_id="123456",
    entry_price=50000,
    quantity=0.1,
    position_type="swap",
    leverage=3,
    timestamp=int(time.time()),
    closed=False
)

# 保存仓位
position_mgr.save_position(position)

# 加载仓位
positions = position_mgr.load_positions()

# 平仓
position_mgr.close_position("BTC-USDT-SWAP", 55000)
```

### 交易执行器 (trader.py)

提供与交易所的交互功能，如下单、查询价格、获取余额等。目前支持 OKEx 交易所。

```python
from src.exchange.okex.trader import OKExTrader

# 创建交易执行器
trader = OKExTrader("app_name", config)

# 设置杠杆
trader.set_leverage("BTC-USDT-SWAP", 3)

# 下单
trader.swap_order(
    inst_id="BTC-USDT-SWAP",
    side="buy",
    pos_side="long",
    sz=0.1
)

# 获取价格
price = trader.get_mark_price("BTC-USDT-SWAP")
```

## 配置

系统采用模块化的配置文件结构，所有配置文件都存放在 `config/` 目录下，采用JSON或YAML格式。

### 配置文件组织

- **api.json**: 包含交易所API的通用配置，如API密钥、密码等
- **<app_name>.json**: 每个应用程序的特定配置文件，包含该应用的特殊参数

这种组织方式避免了在多个应用中重复配置API信息，使配置更加清晰和易于维护。

### API配置示例 (api.json)

```json
{
  "exchange": {
    "type": "okex",
    "api_key": "your_api_key",
    "secret_key": "your_secret_key",
    "passphrase": "your_passphrase",
    "is_simulated": true
  },
  "logging": {
    "level": "INFO",
    "output_targets": ["file", "console"]
  }
}
```

### 应用特定配置示例 (tradingview_signal_tracker.json)

```json
{
  "app_name": "tradingview_signal_tracker",
  "logging": {
    "file": "tradingview_signal_tracker.log"
  },
  "strategy": {
    "leverage": 3,
    "per_position_usdt": 100,
    "take_profit_pct": 0.05,
    "stop_loss_pct": 0.03,
    "trailing_stop": false,
    "unit_type": "quote",
    "enable_symbol_pool": true,
    "default_symbols": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
  }
}
```

### 配置加载

系统使用 `config_loader.py` 模块加载配置，它会自动合并通用API配置和应用特定配置：

```python
from src.common.config_loader import get_app_config

# 加载应用配置（自动合并API配置）
config = get_app_config("tradingview_signal_tracker")
```

## 安装和依赖

1. 克隆仓库:
```bash
git clone https://github.com/Tightfist/cryptoquant.git
cd cryptoquant
```

2. 安装依赖:
```bash
pip install -r requirements.txt
```

3. 配置API密钥:
   - 复制 `config/api.example.json` 为 `config/api.json`
   - 编辑 `config/api.json` 文件，填入你的交易所API密钥
   - 复制并编辑各应用程序的配置文件

## 注意事项

- 请确保你了解交易风险，本系统不对任何交易损失负责。
- 建议先在模拟盘上测试策略，确认无误后再在实盘上运行。
- 定期备份你的数据和配置文件。
- 不要将包含API密钥的配置文件提交到版本控制系统。

## 贡献

欢迎提交问题和拉取请求。对于重大更改，请先开issue讨论您想要更改的内容。

## 许可证

[MIT](https://choosealicense.com/licenses/mit/) 