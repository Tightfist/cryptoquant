#!/bin/bash
#
# 获取状态脚本 - 获取tradingview_signal_tracker的状态信息
#

# 默认值
DEFAULT_HOST="localhost"
DEFAULT_PORT="8080"

# 显示用法信息
function show_usage {
    echo "用法: $0 [选项]"
    echo ""
    echo "该脚本用于获取tradingview_signal_tracker的状态信息，包括当前持仓等。"
    echo ""
    echo "选项:"
    echo "  -h, --host <主机>          服务器主机地址 (默认: $DEFAULT_HOST)"
    echo "  -p, --port <端口>          服务器端口 (默认: $DEFAULT_PORT)"
    echo "  -f, --format <格式>        输出格式: json或table (默认: json)"
    echo "  --help                     显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0                         # 获取状态信息，以JSON格式显示"
    echo "  $0 -f table                # 获取状态信息，以表格格式显示"
    echo "  $0 -h 192.168.1.100        # 向指定主机请求状态信息"
    echo ""
    exit 1
}

# 解析命令行参数
HOST=$DEFAULT_HOST
PORT=$DEFAULT_PORT
FORMAT="json"

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
        -f|--format)
            FORMAT="$2"
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

# 验证格式参数
if [[ "$FORMAT" != "json" && "$FORMAT" != "table" ]]; then
    echo "错误: 格式必须是 'json' 或 'table'"
    exit 1
fi

# 发送请求
echo "正在获取状态信息..."
RESPONSE=$(curl -s -X GET "http://$HOST:$PORT/webhook/api/status" \
    -H "Content-Type: application/json")

# 获取响应中的success字段
SUCCESS=$(echo "$RESPONSE" | grep -o '"success":[^,}]*' | cut -d':' -f2 | tr -d ' "')

# 如果请求失败，直接显示错误信息并退出
if [[ "$SUCCESS" != "true" ]]; then
    echo "获取状态信息失败!"
    echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
    exit 1
fi

# 处理返回的数据
if [[ "$FORMAT" == "json" ]]; then
    # JSON格式，美化输出
    echo "$RESPONSE" | python3 -m json.tool
else
    # 表格格式，使用Python解析和显示
    echo "$RESPONSE" | python3 -c '
import sys, json
from datetime import datetime

# 读取输入
data = json.load(sys.stdin)

if not data.get("success"):
    print("获取状态失败:", data.get("message", "未知错误"))
    sys.exit(1)

status_data = data.get("data", {})
positions = status_data.get("positions", {}).get("positions", [])

if not positions:
    print("当前没有持仓")
    sys.exit(0)

# 打印概要信息
print(f"持仓总数: {len(positions)}")
print("-" * 100)
print("| {:<15} | {:<8} | {:<10} | {:<12} | {:<10} | {:<10} |".format(
    "交易对", "方向", "数量", "开仓价格", "杠杆", "持仓ID"))
print("-" * 100)

# 打印持仓详情
for pos in positions:
    # 提取基本信息
    symbol = pos.get("symbol", "N/A")
    direction = pos.get("direction", "N/A")
    quantity = pos.get("quantity", 0)
    entry_price = pos.get("entry_price", 0)
    leverage = pos.get("leverage", 1)
    position_id = pos.get("position_id", "N/A")
    
    print("| {:<15} | {:<8} | {:<10.4f} | {:<12.4f} | {:<10} | {:<10} |".format(
        symbol, direction, quantity, entry_price, leverage, position_id))

print("-" * 100)
'
fi

# 返回成功状态
exit 0
