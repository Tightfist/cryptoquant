#!/bin/bash
#
# 修改仓位脚本 - 向tradingview_signal_tracker发送修改仓位参数的信号
#

# 默认值
DEFAULT_HOST="localhost"
DEFAULT_PORT="80"

# 显示用法信息
function show_usage {
    echo "用法: $0 <交易对> [选项]"
    echo ""
    echo "该脚本用于向tradingview_signal_tracker发送修改仓位参数的信号。"
    echo ""
    echo "参数:"
    echo "  <交易对>                   要修改的交易对 (例如: BTC-USDT-SWAP)"
    echo ""
    echo "选项:"
    echo "  -h, --host <主机>          服务器主机地址 (默认: $DEFAULT_HOST)"
    echo "  -p, --port <端口>          服务器端口 (默认: $DEFAULT_PORT)"
    echo "  -tp, --take_profit <比例>  止盈比例 (例如: 0.05 表示5%)"
    echo "  -sl, --stop_loss <比例>    止损比例 (例如: 0.03 表示3%)"
    echo "  -ts, --trailing_stop <值>  启用或禁用追踪止损 (true/false)"
    echo "  -td, --trailing_distance <比例> 追踪止损距离 (例如: 0.02 表示2%)"
    echo "  --help                     显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0 BTC-USDT-SWAP -tp 0.1         # 修改止盈为10%"
    echo "  $0 ETH-USDT-SWAP -ts true -td 0.03  # 启用追踪止损，距离3%"
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
TAKE_PROFIT=""
STOP_LOSS=""
TRAILING_STOP=""
TRAILING_DISTANCE=""

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
        -tp|--take_profit)
            TAKE_PROFIT="$2"
            shift 2
            ;;
        -sl|--stop_loss)
            STOP_LOSS="$2"
            shift 2
            ;;
        -ts|--trailing_stop)
            TRAILING_STOP="$2"
            shift 2
            ;;
        -td|--trailing_distance)
            TRAILING_DISTANCE="$2"
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

# 检查是否至少有一个修改参数
if [[ -z "$TAKE_PROFIT" && -z "$STOP_LOSS" && -z "$TRAILING_STOP" && -z "$TRAILING_DISTANCE" ]]; then
    echo "错误: 必须提供至少一个修改参数"
    show_usage
fi

# 构建请求JSON
JSON="{\"action\":\"modify\",\"symbol\":\"$SYMBOL\""

# 添加可选参数
if [[ -n "$TAKE_PROFIT" ]]; then
    JSON="$JSON,\"take_profit_pct\":$TAKE_PROFIT"
fi

if [[ -n "$STOP_LOSS" ]]; then
    JSON="$JSON,\"stop_loss_pct\":$STOP_LOSS"
fi

if [[ -n "$TRAILING_STOP" ]]; then
    if [[ "$TRAILING_STOP" == "true" ]]; then
        JSON="$JSON,\"trailing_stop\":true"
    else
        JSON="$JSON,\"trailing_stop\":false"
    fi
fi

if [[ -n "$TRAILING_DISTANCE" ]]; then
    JSON="$JSON,\"trailing_distance\":$TRAILING_DISTANCE"
fi

# 关闭JSON
JSON="$JSON}"

# 发送请求
echo "正在发送修改仓位请求..."
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
    echo "修改仓位请求成功!"
    exit 0
else
    echo "修改仓位请求失败!"
    exit 1
fi
