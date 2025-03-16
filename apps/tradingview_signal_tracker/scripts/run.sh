#!/bin/bash
#
# 运行脚本 - 启动TradingView信号追踪器
#

# 设置工作目录为脚本所在目录的上级目录（应用根目录）
cd "$(dirname "$0")/.." || exit

# 项目根目录
PROJECT_ROOT="$(cd ../.. && pwd)"
echo "项目根目录: $PROJECT_ROOT"

# 设置Python路径
export PYTHONPATH=$PROJECT_ROOT:$PYTHONPATH

# 默认值
DEFAULT_LOG_LEVEL="INFO"
DEFAULT_PORT="80"
DEFAULT_CONFIG_PATH=""

# 显示用法信息
function show_usage {
    echo "用法: $0 [选项]"
    echo ""
    echo "该脚本用于启动TradingView信号追踪器。"
    echo ""
    echo "选项:"
    echo "  -c, --config <配置文件>     配置文件路径 (默认: 使用自动识别的配置)"
    echo "  -l, --log-level <日志级别>  日志级别: DEBUG, INFO, WARNING, ERROR (默认: $DEFAULT_LOG_LEVEL)"
    echo "  -p, --port <端口>           服务器端口 (默认: $DEFAULT_PORT)"
    echo "  -b, --background           后台运行模式"
    echo "  --help                     显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0                         # 使用默认配置启动"
    echo "  $0 -l DEBUG                # 以DEBUG日志级别启动"
    echo "  $0 -p 9000                 # 监听9000端口"
    echo "  $0 -b                      # 后台运行模式"
    echo ""
    exit 1
}

# 解析命令行参数
LOG_LEVEL=$DEFAULT_LOG_LEVEL
PORT=$DEFAULT_PORT
CONFIG_PATH=$DEFAULT_CONFIG_PATH
BACKGROUND=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        -c|--config)
            CONFIG_PATH="$2"
            shift 2
            ;;
        -l|--log-level)
            LOG_LEVEL="$2"
            shift 2
            ;;
        -p|--port)
            PORT="$2"
            shift 2
            ;;
        -b|--background)
            BACKGROUND=true
            shift
            ;;
        --help)
            show_usage
            ;;
        -*)
            echo "错误: 未知选项 $1"
            show_usage
            ;;
        *)
            echo "错误: 未知参数 $1"
            show_usage
            ;;
    esac
done

# 创建环境变量
export TV_SIGNAL_TRACKER_LOG_LEVEL=$LOG_LEVEL
export TV_SIGNAL_TRACKER_PORT=$PORT

if [ ! -z "$CONFIG_PATH" ]; then
    export TV_SIGNAL_TRACKER_CONFIG_PATH=$CONFIG_PATH
fi

# 检查虚拟环境
if [ -d "$PROJECT_ROOT/venv" ]; then
    echo "使用虚拟环境..."
    source "$PROJECT_ROOT/venv/bin/activate"
fi

# 创建日志目录
mkdir -p logs

# 启动命令
CMD="python3 $PROJECT_ROOT/apps/tradingview_signal_tracker/main.py"

# 如果是后台运行模式
if [ "$BACKGROUND" = true ]; then
    echo "TradingView信号追踪器将在后台运行..."
    nohup $CMD > logs/nohup.out 2>&1 &
    PID=$!
    echo "进程ID: $PID"
    echo "日志输出: logs/nohup.out"
    # 将PID保存到文件中
    echo $PID > logs/pid
else
    echo "启动TradingView信号追踪器..."
    $CMD
fi 