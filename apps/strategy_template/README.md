# 交易策略框架模板

这个模板演示了如何使用 `trading_framework` 框架实现自定义交易策略，借助框架提供的功能，你可以专注于策略本身的逻辑，而无需关心仓位管理、止盈止损等基础设施。

## 功能特点

- 统一的仓位管理（自动持久化，异常重启恢复）
- 自动止盈止损和追踪止损
- HTTP API接口用于信号接收和手动干预
- 支持多种交易信号处理

## 目录结构

```
strategy_template/
├── main.py              # 程序入口点
├── example_strategy.py  # 示例策略实现
├── README.md            # 本文档
├── scripts/             # Shell脚本工具
    ├── open_position.sh    # 开仓脚本
    ├── close_position.sh   # 平仓脚本
    ├── modify_position.sh  # 修改仓位参数脚本
    ├── close_all.sh        # 关闭所有持仓脚本
    └── get_status.sh       # 获取状态脚本
```

## 快速开始

1. 复制策略模板目录

```bash
cp -r apps/strategy_template apps/your_strategy_name
```

2. 修改配置文件

```bash
cp config/strategy_template.json config/your_strategy_name.json
# 然后编辑 your_strategy_name.json 文件
```

3. 实现你的策略

编辑 `your_strategy_name/example_strategy.py`，将其重命名为更合适的名称，并根据自己的需求实现 `process_signal` 方法。

4. 修改主文件

编辑 `your_strategy_name/main.py`，更新应用名称和策略类引用。

5. 运行策略

```bash
cd cryptoquant
python apps/your_strategy_name/main.py
```

## 实现自己的策略

要实现自己的策略，只需继承 `BaseStrategy` 并实现 `process_signal` 方法：

```python
from src.common.trading_framework import BaseStrategy, TradeSignal

class MyStrategy(BaseStrategy):
    async def process_signal(self, signal_data: Dict[str, Any]) -> Tuple[bool, str]:
        # 在这里解析你的自定义信号格式
        # 将信号转换为标准的TradeSignal对象
        # 调用self.handle_trade_signal处理标准化信号
        
        # 示例:
        signal = TradeSignal(
            action="open",
            symbol="BTC-USDT-SWAP",
            direction="long",
            entry_price=None,  # 使用市场价格
            quantity=None,     # 使用配置中的单笔仓位USDT金额
            # ... 其他参数
        )
        
        return await self.handle_trade_signal(signal)
```

## API接口

启动后，系统会提供以下HTTP API接口：

- **POST /webhook** - 接收原始交易信号
- **POST /api/trigger** - 手动触发交易操作
- **POST /api/close_all** - 关闭所有持仓
- **GET /api/status** - 查询策略状态

## 使用Shell脚本工具

策略模板提供了一系列Shell脚本，可以方便地与API交互：

### 开仓
```bash
# 基础用法
./scripts/open_position.sh

# 指定参数
./scripts/open_position.sh -s ETH-USDT-SWAP -d short -l 10
```

### 平仓
```bash
# 平仓指定交易对
./scripts/close_position.sh -s BTC-USDT-SWAP
```

### 修改仓位参数
```bash
# 修改止盈止损参数
./scripts/modify_position.sh -s BTC-USDT-SWAP -tp 0.08 -sl 0.02
```

### 关闭所有持仓
```bash
./scripts/close_all.sh
```

### 查询状态
```bash
./scripts/get_status.sh
```

## 配置参数说明

详细的配置参数说明请参考配置文件 `config/strategy_template.json` 中的注释。

## 信号处理流程

1. 信号进入 -> `process_signal`
2. 解析信号 -> 转换为 `TradeSignal`
3. 执行操作 -> `handle_trade_signal`
4. 定期检查持仓 -> 监控止盈止损

## 如何添加自定义功能

`BaseStrategy` 类可以在子类中扩展功能，例如：

```python
class MyStrategy(BaseStrategy):
    def __init__(self, app_name, trader, position_mgr, data_cache, config):
        super().__init__(app_name, trader, position_mgr, data_cache, config)
        # 初始化自定义功能
        
    async def my_custom_method(self):
        # 实现自定义功能
        pass
        
    # 覆盖框架方法以添加自定义行为
    async def get_status(self):
        status = await super().get_status()
        # 添加自定义状态信息
        status["custom_info"] = self.my_custom_info
        return status
```

## 异常处理与恢复

框架自动管理持仓信息并持久化到数据库，当程序重启时，会自动恢复之前的持仓信息。

## 信号格式示例

以下是不同类型信号的格式示例：

### 开仓信号

```json
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

### 平仓信号

```json
{
  "type": "close",
  "symbol": "BTC-USDT-SWAP"
}
```

### 修改仓位参数信号

```json
{
  "type": "modify",
  "symbol": "BTC-USDT-SWAP",
  "take_profit_pct": 0.08,
  "stop_loss_pct": 0.02
}
```

### 状态查询信号

```json
{
  "type": "status"
}
``` 