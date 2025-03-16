#!/bin/bash
# 重启TradingView信号追踪器服务

# 查找进程
PID=$(ps -ef | grep "python.*tradingview_signal_tracker" | grep -v grep | awk '{print $2}')

if [ -n "$PID" ]; then
    echo "找到TradingView信号追踪器进程: $PID，正在停止..."
    kill $PID
    sleep 2
    
    # 检查进程是否已停止
    if ps -p $PID > /dev/null; then
        echo "进程未停止，强制终止..."
        kill -9 $PID
        sleep 1
    fi
    
    echo "进程已停止"
else
    echo "未找到TradingView信号追踪器进程"
fi

# 启动服务
echo "正在启动TradingView信号追踪器服务..."
cd $(dirname $(dirname $(dirname $(dirname $0))))
nohup python3 apps/tradingview_signal_tracker/main.py > logs/tradingview_signal_tracker/stdout.log 2>&1 &

# 检查是否启动成功
sleep 2
NEW_PID=$(ps -ef | grep "python.*tradingview_signal_tracker" | grep -v grep | awk '{print $2}')
if [ -n "$NEW_PID" ]; then
    echo "TradingView信号追踪器服务已启动，PID: $NEW_PID"
else
    echo "启动失败，请检查日志"
fi 