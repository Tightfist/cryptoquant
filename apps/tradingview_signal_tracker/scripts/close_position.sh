#!/bin/bash
#
# 平仓脚本 - 向tradingview_signal_tracker发送平仓信号
#

# 默认值
DEFAULT_HOST="localhost"
DEFAULT_PORT="8080"

# 显示用法信息
function show_usage {
    echo "用法: $0 <交易对> [选项]"
    echo ""
    echo "该脚本用于向tradingview_signal_tracker发送平仓信号。"
    echo ""
    echo "参数:"
    echo "  <交易对>                   要平仓的交易对 (例如: BTC-USDT-SWAP)"
    echo ""
    echo "选项:"
    echo "  -h, --host <主机>          服务器主机地址 (默认: $DEFAULT_HOST)"
    echo "  -p, --port <端口>          服务器端口 (默认: $DEFAULT_PORT)"
    echo "  --help                     显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0 BTC-USDT-SWAP                     # 平仓指定交易对"
    echo "  $0 ETH-USDT-SWAP -h 192.168.1.100    # 向指定主机发送平仓信号"
    echo ""
    exit 1
}

# 检查是否提供了足够的参数
if [ $# -lt 1 ]; then
    echo "错误: 缺少交易对参数"
    show_usage
fi

# 获取交易对
SYMBOL=$1
shift

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

# 构建请求JSON
JSON="{\"action\":\"close\",\"symbol\":\"$SYMBOL\"}"

# 发送请求
echo "正在发送平仓请求..."
echo "交易对: $SYMBOL"
RESPONSE=$(curl -s -X POST "http://$HOST:$PORT/webhook/api/trigger" \
    -H "Content-Type: application/json" \
    -d "$JSON")

# 输出结果
echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"

# 获取响应中的success字段
SUCCESS=$(echo "$RESPONSE" | grep -o '"success":[^,}]*' | cut -d':' -f2 | tr -d ' "')

# 根据success字段的值返回退出码
if [[ "$SUCCESS" == "true" ]]; then
    echo "平仓请求成功!"
    exit 0
else
    echo "平仓请求失败!"
    exit 1
fi
