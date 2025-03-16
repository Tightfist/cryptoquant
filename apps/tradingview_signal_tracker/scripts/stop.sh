#!/bin/bash
#
# 停止脚本 - 停止TradingView信号追踪器
#

# 设置工作目录为脚本所在目录的上级目录（应用根目录）
cd "$(dirname "$0")/.." || exit

# PID文件路径
PID_FILE="logs/pid"

# 显示用法信息
function show_usage {
    echo "用法: $0 [选项]"
    echo ""
    echo "该脚本用于停止TradingView信号追踪器。"
    echo ""
    echo "选项:"
    echo "  -f, --force    强制停止 (发送SIGKILL信号)"
    echo "  --help         显示此帮助信息"
    echo ""
    exit 1
}

# 解析命令行参数
FORCE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        -f|--force)
            FORCE=true
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

# 检查PID文件是否存在
if [ ! -f "$PID_FILE" ]; then
    echo "错误: PID文件不存在，无法停止服务。"
    echo "提示: 确保服务在后台运行，并且PID文件($PID_FILE)存在。"
    exit 1
fi

# 读取PID
PID=$(cat "$PID_FILE")

# 检查PID是否是一个有效的数字
if ! [[ "$PID" =~ ^[0-9]+$ ]]; then
    echo "错误: PID文件包含无效的进程ID: $PID"
    exit 1
fi

# 检查进程是否存在
if ! ps -p "$PID" > /dev/null; then
    echo "警告: 进程ID $PID 不存在，可能服务已经停止。"
    rm -f "$PID_FILE"
    exit 0
fi

# 发送信号
if [ "$FORCE" = true ]; then
    echo "正在强制停止进程ID $PID..."
    kill -9 "$PID"
else
    echo "正在停止进程ID $PID..."
    kill "$PID"
fi

# 等待进程停止
for i in $(seq 1 10); do
    if ! ps -p "$PID" > /dev/null; then
        echo "服务已停止。"
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 1
done

# 如果进程仍然存在
if ps -p "$PID" > /dev/null; then
    echo "警告: 服务未能在10秒内停止。"
    echo "您可以使用 '$0 --force' 强制停止服务。"
    exit 1
else
    echo "服务已停止。"
    rm -f "$PID_FILE"
    exit 0
fi 