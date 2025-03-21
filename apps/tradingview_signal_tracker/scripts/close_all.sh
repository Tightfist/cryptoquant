#!/bin/bash
#
# 关闭所有持仓脚本 - 发送关闭所有持仓的信号到tradingview_signal_tracker
#

# 默认值
DEFAULT_HOST="localhost"
DEFAULT_PORT="8080"

# 显示用法信息
function show_usage {
    echo "用法: $0 [选项]"
    echo ""
    echo "该脚本用于向tradingview_signal_tracker发送关闭所有持仓的信号。"
    echo ""
    echo "选项:"
    echo "  -h, --host <主机>          服务器主机地址 (默认: $DEFAULT_HOST)"
    echo "  -p, --port <端口>          服务器端口 (默认: $DEFAULT_PORT)"
    echo "  --help                     显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0                         # 关闭所有持仓"
    echo "  $0 -h 192.168.1.100        # 向指定主机发送关闭所有持仓的信号"
    echo ""
    exit 1
}

# 解析命令行参数
HOST=$DEFAULT_HOST
PORT=$DEFAULT_PORT

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--host)
            HOST="$2"
            shift 2
            ;;
        -p|--port)
            PORT="$2"
            shift 2
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

# 发送请求
echo "正在发送关闭所有持仓请求..."
RESPONSE=$(curl -s -X POST "http://$HOST:$PORT/webhook/api/close_all" \
    -H "Content-Type: application/json")

# 输出结果
echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"

# 获取响应中的success字段
SUCCESS=$(echo "$RESPONSE" | grep -o '"success":[^,}]*' | cut -d':' -f2 | tr -d ' "')

# 根据success字段的值返回退出码
if [[ "$SUCCESS" == "true" ]]; then
    echo "关闭所有持仓请求成功!"
    exit 0
else
    echo "关闭所有持仓请求失败!"
    exit 1
fi
