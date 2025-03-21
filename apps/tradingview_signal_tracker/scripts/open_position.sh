#!/bin/bash
#
# 开仓脚本 - 向tradingview_signal_tracker发送开仓信号
#

# 默认值
DEFAULT_HOST="localhost"
DEFAULT_PORT="8080"
DEFAULT_DIRECTION="long"
DEFAULT_LEVERAGE=3
DEFAULT_UNIT_TYPE="quote"
DEFAULT_ENTRY_TYPE="market"  # market或limit

# 显示用法信息
function show_usage {
    echo "用法: $0 <交易对> [选项]"
    echo ""
    echo "该脚本用于向tradingview_signal_tracker发送开仓信号。"
    echo ""
    echo "参数:"
    echo "  <交易对>                   要开仓的交易对 (例如: BTC-USDT-SWAP)"
    echo ""
    echo "选项:"
    echo "  -h, --host <主机>          服务器主机地址 (默认: $DEFAULT_HOST)"
    echo "  -p, --port <端口>          服务器端口 (默认: $DEFAULT_PORT)"
    echo "  -d, --direction <方向>     仓位方向: long或short (默认: $DEFAULT_DIRECTION)"
    echo "  -q, --quantity <数量>      开仓数量 (不指定则使用策略默认值)"
    echo "  -e, --entry <价格>         入场价格 (不指定则使用市场价)"
    echo "  -l, --leverage <杠杆>      杠杆倍数 (默认: $DEFAULT_LEVERAGE)"
    echo "  -u, --unit_type <单位>     委托单位: quote, base, contract (默认: $DEFAULT_UNIT_TYPE)"
    echo "  -tp, --take_profit <比例>  止盈比例 (例如: 0.05 表示5%)"
    echo "  -sl, --stop_loss <比例>    止损比例 (例如: 0.03 表示3%)"
    echo "  -ts, --trailing_stop       启用追踪止损"
    echo "  -td, --trailing_distance <比例> 追踪止损距离 (例如: 0.02 表示2%)"
    echo "  --help                     显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0 BTC-USDT-SWAP                     # 使用默认参数开多仓"
    echo "  $0 ETH-USDT-SWAP -d short -l 5       # 5倍杠杆开空仓"
    echo "  $0 BTC-USDT-SWAP -q 0.1 -e 50000     # 指定数量和价格开仓"
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
DIRECTION=$DEFAULT_DIRECTION
QUANTITY=""
ENTRY_PRICE=""
LEVERAGE=$DEFAULT_LEVERAGE
UNIT_TYPE=$DEFAULT_UNIT_TYPE
TAKE_PROFIT=""
STOP_LOSS=""
TRAILING_STOP="false"
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
        -d|--direction)
            DIRECTION="$2"
            shift 2
            ;;
        -q|--quantity)
            QUANTITY="$2"
            shift 2
            ;;
        -e|--entry)
            ENTRY_PRICE="$2"
            shift 2
            ;;
        -l|--leverage)
            LEVERAGE="$2"
            shift 2
            ;;
        -u|--unit_type)
            UNIT_TYPE="$2"
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
            TRAILING_STOP="true"
            shift
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

# 验证参数
if [[ "$DIRECTION" != "long" && "$DIRECTION" != "short" ]]; then
    echo "错误: 方向必须是 'long' 或 'short'"
    exit 1
fi

if [[ "$UNIT_TYPE" != "quote" && "$UNIT_TYPE" != "base" && "$UNIT_TYPE" != "contract" ]]; then
    echo "错误: 委托单位必须是 'quote', 'base' 或 'contract'"
    exit 1
fi

# 构建请求JSON
JSON="{\"action\":\"open\",\"symbol\":\"$SYMBOL\",\"direction\":\"$DIRECTION\",\"leverage\":$LEVERAGE,\"unit_type\":\"$UNIT_TYPE\""

# 添加可选参数
if [[ -n "$QUANTITY" ]]; then
    JSON="$JSON,\"quantity\":$QUANTITY"
fi

if [[ -n "$ENTRY_PRICE" ]]; then
    JSON="$JSON,\"entry_price\":$ENTRY_PRICE"
fi

if [[ -n "$TAKE_PROFIT" ]]; then
    JSON="$JSON,\"take_profit_pct\":$TAKE_PROFIT"
fi

if [[ -n "$STOP_LOSS" ]]; then
    JSON="$JSON,\"stop_loss_pct\":$STOP_LOSS"
fi

if [[ "$TRAILING_STOP" == "true" ]]; then
    JSON="$JSON,\"trailing_stop\":true"
fi

if [[ -n "$TRAILING_DISTANCE" ]]; then
    JSON="$JSON,\"trailing_distance\":$TRAILING_DISTANCE"
fi

# 关闭JSON
JSON="$JSON}"

# 发送请求
echo "正在发送开仓请求..."
echo "交易对: $SYMBOL, 方向: $DIRECTION"
RESPONSE=$(curl -s -X POST "http://$HOST:$PORT/webhook/api/trigger" \
    -H "Content-Type: application/json" \
    -d "$JSON")

# 输出结果
echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"

# 获取响应中的success字段
SUCCESS=$(echo "$RESPONSE" | grep -o '"success":[^,}]*' | cut -d':' -f2 | tr -d ' "')

# 根据success字段的值返回退出码
if [[ "$SUCCESS" == "true" ]]; then
    echo "开仓请求成功!"
    exit 0
else
    echo "开仓请求失败!"
    exit 1
fi
