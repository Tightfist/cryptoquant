#!/bin/bash
# TradingView信号追踪器API工具

# 配置
HOST="localhost"
PORT="80"
BASE_PATH="/webhook"

# 命令行参数解析
show_help() {
    echo "用法: $0 [选项]"
    echo "选项:"
    echo "  -h, --host HOST      指定主机地址 (默认: $HOST)"
    echo "  -p, --port PORT      指定端口 (默认: $PORT)"
    echo "  -b, --base BASE_PATH 指定基础路径 (默认: $BASE_PATH)"
    echo "  --help               显示帮助信息"
    exit 0
}

# 解析命令行参数
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
        -b|--base)
            BASE_PATH="$2"
            shift 2
            ;;
        --help)
            show_help
            ;;
        *)
            break
            ;;
    esac
done

# 构建基础URL
BASE_URL="http://${HOST}:${PORT}${BASE_PATH}"

# 发送API请求的函数
send_request() {
    local endpoint="$1"
    local data="$2"
    local url="${BASE_URL}${endpoint}"
    
    echo "发送请求到: $url"
    echo "数据: $data"
    
    if [ -z "$data" ]; then
        # GET请求
        curl -s "$url"
    else
        # POST请求
        curl -s -X POST -H "Content-Type: application/json" -d "$data" "$url"
    fi
    
    echo -e "\n"
}

# 根据第一个参数执行不同的操作
case "$1" in
    status)
        # 获取当前状态
        send_request "/api/status"
        ;;
    close_all)
        # 平掉所有仓位
        send_request "/api/close_all"
        ;;
    open)
        # 开仓
        if [ -z "$2" ]; then
            echo "错误: 缺少交易对参数"
            echo "用法: $0 open <交易对> [方向] [杠杆] [金额]"
            exit 1
        fi
        
        symbol="$2"
        direction="${3:-long}"  # 默认为多仓
        leverage="${4:-10}"     # 默认杠杆10倍
        amount="${5:-30}"       # 默认30 USDT
        
        data="{\"action\":\"open\",\"symbol\":\"$symbol\",\"direction\":\"$direction\",\"leverage\":$leverage,\"position_usdt\":$amount}"
        send_request "/api/trigger" "$data"
        ;;
    close)
        # 平仓
        if [ -z "$2" ]; then
            echo "错误: 缺少交易对参数"
            echo "用法: $0 close <交易对>"
            exit 1
        fi
        
        symbol="$2"
        data="{\"action\":\"close\",\"symbol\":\"$symbol\"}"
        send_request "/api/trigger" "$data"
        ;;
    modify)
        # 修改止盈止损
        if [ -z "$2" ]; then
            echo "错误: 缺少交易对参数"
            echo "用法: $0 modify <交易对> <止盈价格> <止损价格>"
            exit 1
        fi
        
        symbol="$2"
        tp_price="$3"
        sl_price="$4"
        
        # 构建JSON数据
        data="{\"action\":\"modify\",\"symbol\":\"$symbol\""
        if [ ! -z "$tp_price" ]; then
            data="$data,\"tp_price\":$tp_price"
        fi
        if [ ! -z "$sl_price" ]; then
            data="$data,\"sl_price\":$sl_price"
        fi
        data="$data}"
        
        # 修复JSON格式
        data=$(echo $data | sed 's/,}/}/g')
        
        send_request "/api/trigger" "$data"
        ;;
    *)
        echo "未知命令: $1"
        echo "可用命令: status, close_all, open, close, modify"
        echo "用法示例:"
        echo "  $0 status                           # 获取当前状态"
        echo "  $0 close_all                        # 平掉所有仓位"
        echo "  $0 open BTC-USDT-SWAP long 10 100   # 开多仓，10倍杠杆，100 USDT"
        echo "  $0 close BTC-USDT-SWAP              # 平掉BTC仓位"
        echo "  $0 modify BTC-USDT-SWAP 50000 45000 # 设置止盈价50000，止损价45000"
        exit 1
        ;;
esac 