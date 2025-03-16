#!/bin/bash
# 关闭所有持仓脚本

# 默认参数
HOST="localhost"
PORT="8888"

# 显示帮助
show_help() {
  echo "用法: $0 [选项]"
  echo "选项:"
  echo "  -h, --host HOST          API服务器主机名 (默认: localhost)"
  echo "  -p, --port PORT          API服务器端口 (默认: 8888)"
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
echo "关闭所有持仓请求:"
echo

# 发送POST请求
curl -s -X POST "${API_URL}/api/close_all" | jq .

echo 