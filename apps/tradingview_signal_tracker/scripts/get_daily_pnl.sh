#!/bin/bash
#
# 获取每日收益脚本 - 获取tradingview_signal_tracker的每日收益数据
#

# 默认值
DEFAULT_HOST="localhost"
DEFAULT_PORT="8080"
DEFAULT_START_DATE=$(date -d "7 days ago" +%Y-%m-%d)
DEFAULT_END_DATE=$(date +%Y-%m-%d)

# 显示用法信息
function show_usage {
    echo "用法: $0 [选项]"
    echo ""
    echo "该脚本用于获取tradingview_signal_tracker的每日收益数据。"
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
RESPONSE=$(curl -s -X GET "http://$HOST:$PORT/webhook/api/daily_pnl?start_date=$START_DATE&end_date=$END_DATE" \
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
try:
    data = json.load(sys.stdin)
    if isinstance(data, dict) and 'data' in data and data['data']:
        data_obj = data['data']
        if isinstance(data_obj, dict):
            # 处理dict格式的data字段 (汇总信息)
            today_pnl = float(data_obj.get('today_pnl', 0))
            win_rate = float(data_obj.get('win_rate', 0))
            win_count = int(data_obj.get('win_count', 0))
            total_closed = int(data_obj.get('total_closed', 0))
            
            print(f'今日       | {today_pnl:10.2f} | {total_closed:8d} | {win_rate:12.2f}')
            print(f'--------------------------------')
            print(f'胜率: {win_rate:.2f}% (赢: {win_count}/{total_closed})')
        elif isinstance(data_obj, list):
            # 处理列表格式 (按日期的历史数据)
            for item in data_obj:
                if not isinstance(item, dict):
                    continue
                date = item.get('date', 'N/A')
                pnl = float(item.get('pnl', 0))
                count = int(item.get('position_count', 0))
                avg_pct = float(item.get('avg_pnl_percentage', 0))
                print(f'{date} | {pnl:10.2f} | {count:8d} | {avg_pct:12.2f}')
        else:
            print(f'警告: 无法识别的数据格式: {type(data_obj)}')
            print('没有收益数据')
    else:
        print('没有收益数据')
except Exception as e:
    print(f'解析数据时出错: {str(e)}')
    print('原始响应:')
    print(sys.stdin.read())
" <<< "$RESPONSE"
    
    echo "========================================================"
fi

echo "完成!"
exit 0
