#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
API脚本生成器

生成用于交互的API脚本，包括:
1. 获取状态脚本 (get_status.sh)
2. 开仓脚本 (open_position.sh)
3. 平仓脚本 (close_position.sh)
4. 修改仓位脚本 (modify_position.sh)
5. 关闭所有持仓脚本 (close_all.sh)
6. 获取每日收益脚本 (get_daily_pnl.sh)
7. 获取仓位历史脚本 (get_position_history.sh)
"""

import os
import stat
import argparse
from typing import Dict, Any

# 脚本模板
TEMPLATES = {
    "get_status.sh": """#!/bin/bash
#
# 获取状态脚本 - 获取{{APP_NAME}}的状态信息
#

# 默认值
DEFAULT_HOST="localhost"
DEFAULT_PORT="{{PORT}}"

# 显示用法信息
function show_usage {
    echo "用法: $0 [选项]"
    echo ""
    echo "该脚本用于获取{{APP_NAME}}的状态信息，包括当前持仓等。"
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
RESPONSE=$(curl -s -X GET "http://$HOST:$PORT{{BASE_PATH}}/api/status" \\
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
""",

    "close_all.sh": """#!/bin/bash
#
# 关闭所有持仓脚本 - 发送关闭所有持仓的信号到{{APP_NAME}}
#

# 默认值
DEFAULT_HOST="localhost"
DEFAULT_PORT="{{PORT}}"

# 显示用法信息
function show_usage {
    echo "用法: $0 [选项]"
    echo ""
    echo "该脚本用于向{{APP_NAME}}发送关闭所有持仓的信号。"
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
RESPONSE=$(curl -s -X POST "http://$HOST:$PORT{{BASE_PATH}}/api/close_all" \\
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
""",

    "open_position.sh": """#!/bin/bash
#
# 开仓脚本 - 向{{APP_NAME}}发送开仓信号
#

# 默认值
DEFAULT_HOST="localhost"
DEFAULT_PORT="{{PORT}}"
DEFAULT_DIRECTION="long"
DEFAULT_LEVERAGE=3
DEFAULT_UNIT_TYPE="quote"
DEFAULT_ENTRY_TYPE="market"  # market或limit

# 显示用法信息
function show_usage {
    echo "用法: $0 <交易对> [选项]"
    echo ""
    echo "该脚本用于向{{APP_NAME}}发送开仓信号。"
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
JSON="{\\\"action\\\":\\\"open\\\",\\\"symbol\\\":\\\"$SYMBOL\\\",\\\"direction\\\":\\\"$DIRECTION\\\",\\\"leverage\\\":$LEVERAGE,\\\"unit_type\\\":\\\"$UNIT_TYPE\\\""

# 添加可选参数
if [[ -n "$QUANTITY" ]]; then
    JSON="$JSON,\\\"quantity\\\":$QUANTITY"
fi

if [[ -n "$ENTRY_PRICE" ]]; then
    JSON="$JSON,\\\"entry_price\\\":$ENTRY_PRICE"
fi

if [[ -n "$TAKE_PROFIT" ]]; then
    JSON="$JSON,\\\"take_profit_pct\\\":$TAKE_PROFIT"
fi

if [[ -n "$STOP_LOSS" ]]; then
    JSON="$JSON,\\\"stop_loss_pct\\\":$STOP_LOSS"
fi

if [[ "$TRAILING_STOP" == "true" ]]; then
    JSON="$JSON,\\\"trailing_stop\\\":true"
fi

if [[ -n "$TRAILING_DISTANCE" ]]; then
    JSON="$JSON,\\\"trailing_distance\\\":$TRAILING_DISTANCE"
fi

# 关闭JSON
JSON="$JSON}"

# 发送请求
echo "正在发送开仓请求..."
echo "交易对: $SYMBOL, 方向: $DIRECTION"
RESPONSE=$(curl -s -X POST "http://$HOST:$PORT{{BASE_PATH}}/api/trigger" \\
    -H "Content-Type: application/json" \\
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
""",

    "close_position.sh": """#!/bin/bash
#
# 平仓脚本 - 向{{APP_NAME}}发送平仓信号
#

# 默认值
DEFAULT_HOST="localhost"
DEFAULT_PORT="{{PORT}}"

# 显示用法信息
function show_usage {
    echo "用法: $0 <交易对> [选项]"
    echo ""
    echo "该脚本用于向{{APP_NAME}}发送平仓信号。"
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
JSON="{\\\"action\\\":\\\"close\\\",\\\"symbol\\\":\\\"$SYMBOL\\\"}"

# 发送请求
echo "正在发送平仓请求..."
echo "交易对: $SYMBOL"
RESPONSE=$(curl -s -X POST "http://$HOST:$PORT{{BASE_PATH}}/api/trigger" \\
    -H "Content-Type: application/json" \\
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
""",

    "modify_position.sh": """#!/bin/bash
#
# 修改仓位脚本 - 向{{APP_NAME}}发送修改仓位参数的信号
#

# 默认值
DEFAULT_HOST="localhost"
DEFAULT_PORT="{{PORT}}"

# 显示用法信息
function show_usage {
    echo "用法: $0 <交易对> [选项]"
    echo ""
    echo "该脚本用于向{{APP_NAME}}发送修改仓位参数的信号。"
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
JSON="{\\\"action\\\":\\\"modify\\\",\\\"symbol\\\":\\\"$SYMBOL\\\""

# 添加可选参数
if [[ -n "$TAKE_PROFIT" ]]; then
    JSON="$JSON,\\\"take_profit_pct\\\":$TAKE_PROFIT"
fi

if [[ -n "$STOP_LOSS" ]]; then
    JSON="$JSON,\\\"stop_loss_pct\\\":$STOP_LOSS"
fi

if [[ -n "$TRAILING_STOP" ]]; then
    if [[ "$TRAILING_STOP" == "true" ]]; then
        JSON="$JSON,\\\"trailing_stop\\\":true"
    else
        JSON="$JSON,\\\"trailing_stop\\\":false"
    fi
fi

if [[ -n "$TRAILING_DISTANCE" ]]; then
    JSON="$JSON,\\\"trailing_distance\\\":$TRAILING_DISTANCE"
fi

# 关闭JSON
JSON="$JSON}"

# 发送请求
echo "正在发送修改仓位请求..."
echo "交易对: $SYMBOL"
RESPONSE=$(curl -s -X POST "http://$HOST:$PORT{{BASE_PATH}}/api/trigger" \\
    -H "Content-Type: application/json" \\
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
""",

    "get_daily_pnl.sh": """#!/bin/bash
#
# 获取每日收益脚本 - 获取{{APP_NAME}}的每日收益数据
#

# 默认值
DEFAULT_HOST="localhost"
DEFAULT_PORT="{{PORT}}"
DEFAULT_START_DATE=$(date -d "7 days ago" +%Y-%m-%d)
DEFAULT_END_DATE=$(date +%Y-%m-%d)

# 显示用法信息
function show_usage {
    echo "用法: $0 [选项]"
    echo ""
    echo "该脚本用于获取{{APP_NAME}}的每日收益数据。"
    echo ""
    echo "选项:"
    echo "  -h, --host <主机>          服务器主机地址 (默认: $DEFAULT_HOST)"
    echo "  -p, --port <端口>          服务器端口 (默认: $DEFAULT_PORT)"
    echo "  -s, --start-date <日期>    开始日期，格式为YYYY-MM-DD (默认: $DEFAULT_START_DATE)"
    echo "  -e, --end-date <日期>      结束日期，格式为YYYY-MM-DD (默认: $DEFAULT_END_DATE)"
    echo "  -f, --format <格式>        输出格式: json或table (默认: table)"
    echo "  --help                     显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0                         # 获取最近7天的每日收益"
    echo "  $0 -s 2023-01-01 -e 2023-01-31  # 获取指定日期范围的每日收益"
    echo "  $0 -f json                 # 以JSON格式输出结果"
    echo ""
    exit 1
}

# 解析命令行参数
HOST=$DEFAULT_HOST
PORT=$DEFAULT_PORT
START_DATE=$DEFAULT_START_DATE
END_DATE=$DEFAULT_END_DATE
FORMAT="table"

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
        -s|--start-date)
            START_DATE="$2"
            shift 2
            ;;
        -e|--end-date)
            END_DATE="$2"
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

# 验证日期格式
date_regex="^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
if ! [[ $START_DATE =~ $date_regex ]]; then
    echo "错误: 开始日期格式无效，应为YYYY-MM-DD"
    exit 1
fi
if ! [[ $END_DATE =~ $date_regex ]]; then
    echo "错误: 结束日期格式无效，应为YYYY-MM-DD"
    exit 1
fi

# 发送请求
echo "正在获取每日收益数据..."
RESPONSE=$(curl -s -X GET "http://$HOST:$PORT{{BASE_PATH}}/api/daily_pnl?start_date=$START_DATE&end_date=$END_DATE" \\
    -H "Content-Type: application/json")

# 获取响应中的success字段
SUCCESS=$(echo "$RESPONSE" | grep -o '"success":[^,}]*' | cut -d':' -f2 | tr -d ' "')

# 如果请求失败，直接显示错误信息并退出
if [[ "$SUCCESS" != "true" ]]; then
    echo "获取每日收益数据失败!"
    echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
    exit 1
fi

# 根据格式显示结果
if [[ "$FORMAT" == "json" ]]; then
    # JSON格式输出
    echo "$RESPONSE" | python3 -m json.tool
else
    # 表格格式输出
    echo "每日收益数据:"
    echo "========================================================"
    echo "日期         | 收益(USDT)  | 仓位数量 | 平均收益率(%)"
    echo "------------------------------------------------------"
    
    # 使用Python解析JSON并格式化输出
    python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
if 'data' in data and data['data']:
    for item in data['data']:
        date = item.get('date', 'N/A')
        pnl = item.get('pnl', 0)
        count = item.get('position_count', 0)
        avg_pct = item.get('avg_pnl_percentage', 0)
        print(f'{date} | {pnl:10.2f} | {count:8d} | {avg_pct:12.2f}')
else:
    print('没有收益数据')
" <<< "$RESPONSE"
    
    echo "========================================================"
fi

echo "完成!"
exit 0
""",

    "get_position_history.sh": """#!/bin/bash
#
# 获取仓位历史脚本 - 获取{{APP_NAME}}的历史仓位数据
#

# 默认值
DEFAULT_HOST="localhost"
DEFAULT_PORT="{{PORT}}"
DEFAULT_START_DATE=$(date -d "30 days ago" +%Y-%m-%d)
DEFAULT_END_DATE=$(date +%Y-%m-%d)
DEFAULT_LIMIT=100

# 显示用法信息
function show_usage {
    echo "用法: $0 [选项]"
    echo ""
    echo "该脚本用于获取{{APP_NAME}}的历史仓位数据。"
    echo ""
    echo "选项:"
    echo "  -h, --host <主机>          服务器主机地址 (默认: $DEFAULT_HOST)"
    echo "  -p, --port <端口>          服务器端口 (默认: $DEFAULT_PORT)"
    echo "  -s, --start-date <日期>    开始日期，格式为YYYY-MM-DD (默认: $DEFAULT_START_DATE)"
    echo "  -e, --end-date <日期>      结束日期，格式为YYYY-MM-DD (默认: $DEFAULT_END_DATE)"
    echo "  -y, --symbol <交易对>      交易对，例如BTC-USDT-SWAP (默认: 所有交易对)"
    echo "  -l, --limit <数量>         最大返回记录数 (默认: $DEFAULT_LIMIT)"
    echo "  -f, --format <格式>        输出格式: json或table (默认: table)"
    echo "  --help                     显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0                         # 获取最近30天的所有仓位历史"
    echo "  $0 -y BTC-USDT-SWAP        # 获取指定交易对的仓位历史"
    echo "  $0 -s 2023-01-01 -e 2023-01-31  # 获取指定日期范围的仓位历史"
    echo "  $0 -f json                 # 以JSON格式输出结果"
    echo ""
    exit 1
}

# 解析命令行参数
HOST=$DEFAULT_HOST
PORT=$DEFAULT_PORT
START_DATE=$DEFAULT_START_DATE
END_DATE=$DEFAULT_END_DATE
SYMBOL=""
LIMIT=$DEFAULT_LIMIT
FORMAT="table"

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
        -s|--start-date)
            START_DATE="$2"
            shift 2
            ;;
        -e|--end-date)
            END_DATE="$2"
            shift 2
            ;;
        -y|--symbol)
            SYMBOL="$2"
            shift 2
            ;;
        -l|--limit)
            LIMIT="$2"
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

# 验证日期格式
date_regex="^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
if ! [[ $START_DATE =~ $date_regex ]]; then
    echo "错误: 开始日期格式无效，应为YYYY-MM-DD"
    exit 1
fi
if ! [[ $END_DATE =~ $date_regex ]]; then
    echo "错误: 结束日期格式无效，应为YYYY-MM-DD"
    exit 1
fi

# 构建URL
URL="http://$HOST:$PORT{{BASE_PATH}}/api/position_history?start_date=$START_DATE&end_date=$END_DATE&limit=$LIMIT"
if [[ -n "$SYMBOL" ]]; then
    URL="${URL}&symbol=$SYMBOL"
fi

# 发送请求
echo "正在获取仓位历史数据..."
RESPONSE=$(curl -s -X GET "$URL" \\
    -H "Content-Type: application/json")

# 获取响应中的success字段
SUCCESS=$(echo "$RESPONSE" | grep -o '"success":[^,}]*' | cut -d':' -f2 | tr -d ' "')

# 如果请求失败，直接显示错误信息并退出
if [[ "$SUCCESS" != "true" ]]; then
    echo "获取仓位历史数据失败!"
    echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
    exit 1
fi

# 根据格式显示结果
if [[ "$FORMAT" == "json" ]]; then
    # JSON格式输出
    echo "$RESPONSE" | python3 -m json.tool
else
    # 表格格式输出
    echo "仓位历史数据:"
    echo "=============================================================================="
    echo "交易对      | 方向 | 入场价格  | 平仓价格  | 收益(USDT) | 收益率(%) | 持仓时间"
    echo "------------------------------------------------------------------------------"
    
    # 使用Python解析JSON并格式化输出
    python3 -c "
import json, sys
data = json.loads(sys.stdin.read())
if 'data' in data and data['data']:
    for item in data['data']:
        symbol = item.get('symbol', 'N/A')
        direction = item.get('direction', 'N/A')
        entry_price = item.get('entry_price', 0)
        exit_price = item.get('exit_price', 0)
        pnl_amount = item.get('pnl_amount', 0)
        pnl_percentage = item.get('pnl_percentage', 0)
        holding_time = item.get('holding_time', 'N/A')
        print(f'{symbol:11} | {direction:4} | {entry_price:9.2f} | {exit_price:9.2f} | {pnl_amount:10.2f} | {pnl_percentage:9.2f} | {holding_time}')
else:
    print('没有仓位历史数据')
" <<< "$RESPONSE"
    
    echo "=============================================================================="
fi

echo "完成!"
exit 0
"""
}

def create_script(target_dir: str, script_name: str, content: str, app_name: str, port: str, base_path: str = "") -> str:
    """
    创建脚本文件
    
    Args:
        target_dir: 目标目录
        script_name: 脚本名称
        content: 脚本内容
        app_name: 应用名称
        port: 端口号
        base_path: 基础路径
        
    Returns:
        str: 创建的脚本路径
    """
    # 替换模板变量
    content = content.replace("{{APP_NAME}}", app_name)
    content = content.replace("{{PORT}}", port)
    content = content.replace("{{BASE_PATH}}", base_path)
    
    # 确保目标目录存在
    os.makedirs(target_dir, exist_ok=True)
    
    # 创建脚本文件
    script_path = os.path.join(target_dir, script_name)
    with open(script_path, 'w') as f:
        f.write(content)
    
    # 设置执行权限
    os.chmod(script_path, os.stat(script_path).st_mode | stat.S_IEXEC)
    
    return script_path

def generate_api_scripts(target_dir: str, app_name: str, port: str, base_path: str = "") -> Dict[str, str]:
    """
    生成API脚本
    
    Args:
        target_dir: 目标目录
        app_name: 应用名称
        port: 端口号
        base_path: 基础路径(例如: "/webhook")
        
    Returns:
        Dict[str, str]: 脚本名称到路径的映射
    """
    # 初始化结果
    script_paths = {}
    
    # 处理基础路径
    if base_path and not base_path.startswith('/'):
        base_path = f"/{base_path}"
    
    # 生成所有脚本
    for script_name, content in TEMPLATES.items():
        script_paths[script_name] = create_script(target_dir, script_name, content, app_name, port, base_path)
    
    return script_paths

def main():
    """主函数"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='生成API脚本')
    parser.add_argument('app_name', help='应用名称')
    parser.add_argument('target_dir', help='脚本输出目录')
    parser.add_argument('--port', default='8080', help='API端口(默认: 8080)')
    parser.add_argument('--base-path', default='', help='API基础路径，例如 /webhook')
    
    args = parser.parse_args()
    
    # 生成脚本
    scripts = generate_api_scripts(args.target_dir, args.app_name, args.port, args.base_path)
    
    # 输出结果
    print(f"已为 {args.app_name} 生成以下脚本:")
    for name, path in scripts.items():
        print(f"  - {path}")

if __name__ == "__main__":
    main() 