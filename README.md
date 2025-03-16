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
│   └── strategy_template/     # 策略模板示例
├── src/                       # 共享源代码
│   ├── common/                # 通用组件
│   │   ├── config_loader.py   # 配置加载器
│   │   ├── data_cache.py      # 数据缓存
│   │   ├── event_loop.py      # 事件循环
│   │   ├── logger.py          # 日志工具
│   │   ├── market_subscriber.py # 市场数据订阅器
│   │   ├── order_utils.py     # 订单工具
│   │   ├── position_manager.py # 仓位管理器
│   │   ├── trading_framework.py # 交易框架
│   │   ├── http/              # HTTP相关组件
│   │   │   ├── server.py      # HTTP服务器
│   │   │   └── api_handlers/  # API请求处理器
│   │   ├── scripts/           # 通用脚本工具
│   │   └── websocket/         # WebSocket组件
│   └── exchange/              # 交易所接口
│       └── okex/              # OKEx交易所适配器
│           ├── trader.py      # OKEx交易执行器
│           └── websocket.py   # OKEx WebSocket适配器
├── config/                    # 配置文件目录
│   ├── api.json               # 交易所API通用配置
│   ├── funding_arbitrage.json # 资金费率套利特定配置
│   ├── price.json             # 价格监控特定配置
│   ├── trader_tools.json      # 交易工具特定配置
│   ├── tradingview_signal_tracker.json # TradingView信号追踪器特定配置
│   └── strategy_template.json # 策略模板特定配置
├── databases/                 # 数据库目录
├── logs/                      # 日志目录
└── tests/                     # 测试目录
```

## 应用程序

### TradingView信号追踪器

TradingView信号追踪器可以接收并执行来自TradingView的交易信号，支持开仓、平仓、止盈止损等操作。

**主要功能**:
- 接收TradingView的Webhook信号
- 自动执行开仓和平仓操作
- 支持止盈止损和追踪止损设置
- 自动生成API交互脚本
- 支持交易对白名单管理

**使用方法**:
```bash
python apps/tradingview_signal_tracker/main.py
```

详情请参阅 [TradingView信号追踪器文档](apps/tradingview_signal_tracker/README.md)

### 资金费率套利

资金费率套利策略利用永续合约的资金费率进行套利，在资金费率为正时做空合约并同时做多现货，赚取资金费。

**主要功能**:
- 自动检测套利机会
- 执行合约做空和现货做多的套利组合
- 支持多种平仓条件
- 自动计算盈亏

**使用方法**:
```bash
python apps/funding_arbitrage/main.py
```

### 策略模板

策略模板提供了一个基础框架，可用于快速开发新的交易策略。模板包含了常见的策略组件和功能，如信号处理、仓位管理、风险控制等。

**主要功能**:
- 提供策略开发的标准模板
- 包含完整的API接口和处理逻辑
- 内置常用的交易工具和功能

**使用方法**:
```bash
# 复制策略模板
cp -r apps/strategy_template apps/my_new_strategy

# 编辑配置文件
nano config/my_new_strategy.json

# 运行策略
python apps/my_new_strategy/main.py
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
python apps/trader_tools/main.py --mode order --symbols btc eth --amount 100 --leverage 3 --side buy --price-type market
python apps/trader_tools/main.py --mode close --symbols btc eth --price-type market
python apps/trader_tools/main.py --mode balance
```

## 共享组件

### 交易框架 (trading_framework.py)

提供统一的仓位管理和交易执行框架，支持止盈止损、追踪止损、仓位持久化等功能。

```python
from src.common.trading_framework import TradingFramework, BaseStrategy

# 创建策略类
class MyStrategy(BaseStrategy):
    async def process_signal(self, signal_data):
        # 处理信号的逻辑
        return True, "处理成功"

# 初始化交易框架
framework = TradingFramework(
    app_name="my_app",
    strategy_class=MyStrategy,
    config=config
)

# 启动框架
await framework.run_forever()
```

### 市场数据订阅器 (market_subscriber.py)

订阅和处理交易所的实时行情数据。

```python
from src.common.market_subscriber import OKExMarketSubscriber

# 创建市场数据订阅器
subscriber = OKExMarketSubscriber(data_cache, config, "my_app")

# 启动订阅器
await subscriber.start()

# 订阅特定交易对
await subscriber.subscribe_symbol("BTC-USDT-SWAP")
```

### 订单工具 (order_utils.py)

提供通用的订单计算功能，如计算订单大小等。

```python
from src.common.order_utils import calculate_order_size

# 计算订单大小
size = calculate_order_size(
    price=50000,
    amount=100,
    unit_type="quote",
    leverage=3
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
    timestamp=int(time.time())
)

# 保存仓位
position_mgr.save_position(position)

# 加载仓位
positions = position_mgr.load_positions()

# 平仓
position_mgr.close_position("BTC-USDT-SWAP", 55000)
```

### HTTP API处理器 (http/api_handlers/trading_framework_api.py)

提供统一的HTTP API处理器，用于处理交易框架的API请求。

```python
from src.common.http.api_handlers import TradingFrameworkApiHandler

# 创建API处理器
api_handler = TradingFrameworkApiHandler(framework, "my_app")

# 注册API路由
api_handler.register_routes(app, base_path="/webhook")
```

### API脚本生成器 (scripts/generate_api_scripts.py)

自动生成与交易框架API交互的Shell脚本。

```python
from src.common.scripts.generate_api_scripts import generate_api_scripts

# 生成API脚本
scripts = generate_api_scripts(
    target_dir="./scripts",
    app_name="my_app",
    port="8080",
    base_path="/webhook"
)
```

### 交易执行器 (exchange/okex/trader.py)

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

系统采用模块化的配置文件结构，所有配置文件都存放在 `config/` 目录下，采用JSON格式。

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
  "webhook": {
    "host": "0.0.0.0",
    "port": 80,
    "path": "/webhook"
  },
  "logging": {
    "file": "tradingview_signal_tracker.log"
  },
  "strategy": {
    "leverage": 3,
    "per_position_usdt": 100,
    "take_profit_pct": 0.05,
    "stop_loss_pct": 0.03,
    "trailing_stop": true,
    "trailing_distance": 0.02,
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

## 新增应用程序开发指南

要开发一个新的交易应用程序，你可以按照以下步骤操作：

1. **复制策略模板**
   ```bash
   cp -r apps/strategy_template apps/my_new_strategy
   ```

2. **创建配置文件**
   ```bash
   cp config/strategy_template.json config/my_new_strategy.json
   ```

3. **编辑配置文件**
   ```bash
   nano config/my_new_strategy.json
   ```

4. **实现策略逻辑**
   - 编辑 `apps/my_new_strategy/example_strategy.py` 重命名并实现你的策略逻辑
   - 该文件包含一个继承自 `BaseStrategy` 的类，你需要重写 `process_signal` 方法

5. **更新主程序**
   - 编辑 `apps/my_new_strategy/main.py`
   - 更新应用名称和导入的策略类

6. **运行应用**
   ```bash
   python apps/my_new_strategy/main.py
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

## 核心功能扩展

### 添加新的交易所支持

系统设计为支持多个交易所，要添加新的交易所支持，需要：

1. 在 `src/exchange/<exchange_name>/` 目录下创建新的交易所模块
2. 实现交易执行器，继承自通用的 `ExchangeAdapter` 类
3. 实现市场数据订阅器，继承自 `BaseMarketSubscriber` 类
4. 实现数据缓存，继承自 `DataCache` 类

### 添加新的API端点

要添加新的API端点，可以：

1. 创建一个新的API处理器类，或扩展现有的 `TradingFrameworkApiHandler` 类
2. 实现新的处理方法并注册路由
3. 在脚本生成器中添加相应的脚本模板

## 注意事项

- 请确保你了解交易风险，本系统不对任何交易损失负责。
- 建议先在模拟盘上测试策略，确认无误后再在实盘上运行。
- 定期备份你的数据和配置文件。
- 不要将包含API密钥的配置文件提交到版本控制系统。
- 所有止盈止损判断都基于未杠杆化的收益率，以避免过高杠杆带来的风险。

## 贡献

欢迎提交问题和拉取请求。对于重大更改，请先开issue讨论您想要更改的内容。 