# 通用脚本模块

本模块提供了一系列通用的脚本工具，可用于各种自动化任务。

## 可用工具

### API 脚本生成器 (`generate_api_scripts.py`)

自动生成用于与交易框架 API 交互的 shell 脚本。

#### 功能

- 生成获取状态脚本 (get_status.sh)
- 生成开仓脚本 (open_position.sh)
- 生成平仓脚本 (close_position.sh)
- 生成修改仓位脚本 (modify_position.sh)
- 生成关闭所有持仓脚本 (close_all.sh)

#### 用法

可以通过以下方式使用：

1. 作为独立脚本：

```bash
python -m src.common.scripts.generate_api_scripts <应用名称> <输出目录> --port <端口> --base-path <基础路径>
```

示例：
```bash
python -m src.common.scripts.generate_api_scripts tradingview_signal_tracker ./scripts --port 8080 --base-path /webhook
```

2. 作为导入模块：

```python
from src.common.scripts.generate_api_scripts import generate_api_scripts

scripts = generate_api_scripts(
    target_dir="./scripts",
    app_name="my_app",
    port="8080",
    base_path="/webhook"
)
```

#### 生成的脚本

每个生成的脚本都包含详细的帮助信息，可通过 `--help` 参数查看：

```bash
./scripts/get_status.sh --help
``` 