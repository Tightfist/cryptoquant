# 交易框架通用 API 处理器

本模块提供了交易框架的通用 API 端点处理，用于简化交易应用的 HTTP API 实现。

## 功能特性

- 统一处理交易框架的常用 API 端点
- 支持以下标准端点：
  - `/api/trigger` - 触发交易信号（开仓、平仓、修改仓位等）
  - `/api/close_all` - 关闭所有持仓
  - `/api/status` - 获取框架状态信息

## 使用示例

### 在应用中集成 API 处理器

```python
from aiohttp import web
from src.common.http.api_handlers import TradingFrameworkApiHandler
from src.common.trading_framework import TradingFramework

# 假设已经初始化了交易框架
framework = TradingFramework(...)

# 创建 API 处理器
api_handler = TradingFrameworkApiHandler(framework, "my_app_name")

# 创建 web 应用
app = web.Application()

# 注册 API 路由
api_handler.register_routes(app, base_path="/webhook")
```

### 生成通用 API 脚本

可以使用提供的脚本生成器来生成用于与 API 交互的 shell 脚本：

```bash
python -m src.common.scripts.generate_api_scripts my_app_name ./scripts --port 8080 --base-path /webhook
```

这将生成以下脚本：
- `get_status.sh` - 获取状态信息
- `open_position.sh` - 开仓
- `close_position.sh` - 平仓
- `modify_position.sh` - 修改仓位参数
- `close_all.sh` - 关闭所有持仓

## 注意事项

- 确保交易框架实例已正确初始化并传递给 API 处理器
- 生成的脚本需要 `curl` 和 `python3` 支持
- 所有脚本都支持通过命令行参数定制行为，使用 `--help` 查看详细用法 