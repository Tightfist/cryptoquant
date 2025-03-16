#!/bin/bash
# 平仓操作脚本

# 默认参数
HOST="localhost"
PORT="8888"
SYMBOL="BTC-USDT-SWAP"

# 显示帮助
show_help() {
  echo "用法: $0 [选项]"
  echo "选项:"
  echo "  -h, --host HOST          API服务器主机名 (默认: localhost)"
  echo "  -p, --port PORT          API服务器端口 (默认: 8888)"
  echo "  -s, --symbol SYMBOL      交易对 (默认: BTC-USDT-SWAP)"
  echo "  --help                   显示此帮助"
  exit 1
}

# 解析命令行参数
while [[ $# -gt 0 ]]; do
  case $1 in
    -h|--host)
      HOST="$2"
      shift 2
      ;;
    -p|--port)
      PORT="$2"
      shift 2
      ;;
    -s|--symbol)
      SYMBOL="$2"
      shift 2
      ;;
    --help)
      show_help
      ;;
    *)
      echo "未知选项: $1"
      show_help
      ;;
  esac
done

# 构建API URL
API_URL="http://${HOST}:${PORT}"

# 打印请求信息
echo "发送平仓请求:"
echo "交易对: $SYMBOL"
echo

# 构建webhook请求数据
WEBHOOK_DATA='{
  "type": "close",
  "symbol": "'$SYMBOL'"
}'

# 发送webhook请求
echo "发送到Webhook:"
curl -s -X POST "${API_URL}/webhook" \
  -H "Content-Type: application/json" \
  -d "$WEBHOOK_DATA" | jq .

echo 