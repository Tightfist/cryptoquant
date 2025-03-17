#!/usr/bin/env python3
"""
短线逐利策略API测试脚本

提供了简单的命令行工具，用于测试短线逐利策略的API功能。
"""

import os
import sys
import json
import argparse
import requests
from typing import Dict, Any


def send_request(host: str, port: int, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    发送API请求
    
    Args:
        host: 主机地址
        port: 端口号
        data: 请求数据
        
    Returns:
        Dict[str, Any]: 响应数据
    """
    url = f"http://{host}:{port}/api/trigger"
    headers = {"Content-Type": "application/json"}
    
    try:
        response = requests.post(url, json=data, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"请求异常: {e}")
        return {"success": False, "message": str(e)}


def check_status(host: str, port: int) -> None:
    """
    查询策略状态
    
    Args:
        host: 主机地址
        port: 端口号
    """
    data = {"type": "status"}
    result = send_request(host, port, data)
    
    if "success" in result and result["success"]:
        status_data = json.loads(result["message"])
        print("\n策略状态:")
        print(f"符合条件的标的数量: {status_data.get('eligible_symbols_count', 0)}")
        print(f"今日交易次数: {status_data.get('daily_trades_count', 0)}/{status_data.get('max_daily_trades', 50)}")
        print(f"今日盈亏: {status_data.get('daily_pnl_pct', 0):.2f}%")
        print(f"最大亏损限制: {status_data.get('max_daily_loss_pct', 50)}%")
        print(f"上次参数更新: {status_data.get('last_param_update', '')}")
        
        # 输出活跃持仓
        if "positions" in status_data:
            print("\n当前持仓:")
            for pos in status_data["positions"]:
                print(f"  {pos['symbol']} {pos['direction']} {pos['size']} 开仓价:{pos['entry_price']} 当前价:{pos['current_price']} 收益:{pos['pnl']:.2f}({pos['pnl_pct']:.2f}%)")
        
        # 输出参数信息
        if "strategy_parameters" in status_data:
            params = status_data["strategy_parameters"]
            print("\n当前参数:")
            print(f"  价格涨幅阈值: {params.get('min_price_increase_pct', 0)}%")
            print(f"  持仓增长阈值: {params.get('min_position_growth_pct', 0)}%")
            print(f"  成交量比例阈值: {params.get('volume_increase_ratio', 0)}")
            print(f"  止盈比例: {params.get('fixed_take_profit_pct', 0)}%")
            print(f"  止损比例: {params.get('fixed_stop_loss_pct', 0)}%")
    else:
        print(f"查询状态失败: {result.get('message', '未知错误')}")


def trigger_scan(host: str, port: int) -> None:
    """
    触发市场扫描
    
    Args:
        host: 主机地址
        port: 端口号
    """
    data = {"type": "scan"}
    result = send_request(host, port, data)
    
    if "success" in result and result["success"]:
        print(f"市场扫描已触发: {result['message']}")
    else:
        print(f"触发扫描失败: {result.get('message', '未知错误')}")


def update_parameters(host: str, port: int) -> None:
    """
    更新策略参数
    
    Args:
        host: The host address
        port: The port number
    """
    data = {"type": "update_params"}
    result = send_request(host, port, data)
    
    if "success" in result and result["success"]:
        print(f"参数已更新: {result['message']}")
    else:
        print(f"更新参数失败: {result.get('message', '未知错误')}")


def reset_counters(host: str, port: int) -> None:
    """
    重置每日计数器
    
    Args:
        host: 主机地址
        port: 端口号
    """
    data = {"type": "reset"}
    result = send_request(host, port, data)
    
    if "success" in result and result["success"]:
        print(f"计数器已重置: {result['message']}")
    else:
        print(f"重置计数器失败: {result.get('message', '未知错误')}")


def open_position(host: str, port: int, symbol: str) -> None:
    """
    手动开仓
    
    Args:
        host: 主机地址
        port: 端口号
        symbol: 交易对
    """
    data = {
        "type": "open",
        "symbol": symbol,
        "direction": "long",
        "take_profit_pct": 0.05,
        "stop_loss_pct": 0.03,
        "trailing_stop": True,
        "trailing_distance": 0.02
    }
    result = send_request(host, port, data)
    
    if "success" in result and result["success"]:
        print(f"开仓成功: {result['message']}")
    else:
        print(f"开仓失败: {result.get('message', '未知错误')}")


def close_position(host: str, port: int, symbol: str) -> None:
    """
    手动平仓
    
    Args:
        host: 主机地址
        port: 端口号
        symbol: 交易对
    """
    data = {
        "type": "close",
        "symbol": symbol
    }
    result = send_request(host, port, data)
    
    if "success" in result and result["success"]:
        print(f"平仓成功: {result['message']}")
    else:
        print(f"平仓失败: {result.get('message', '未知错误')}")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="短线逐利策略API测试工具")
    parser.add_argument("--host", default="localhost", help="API主机地址")
    parser.add_argument("--port", type=int, default=8080, help="API端口号")
    
    subparsers = parser.add_subparsers(dest="command", help="命令")
    
    # 查询状态命令
    status_parser = subparsers.add_parser("status", help="查询策略状态")
    
    # 触发扫描命令
    scan_parser = subparsers.add_parser("scan", help="触发市场扫描")
    
    # 更新参数命令
    update_parser = subparsers.add_parser("update", help="更新策略参数")
    
    # 重置计数器命令
    reset_parser = subparsers.add_parser("reset", help="重置每日计数器")
    
    # 开仓命令
    open_parser = subparsers.add_parser("open", help="手动开仓")
    open_parser.add_argument("symbol", help="交易对，例如 BTC-USDT-SWAP")
    
    # 平仓命令
    close_parser = subparsers.add_parser("close", help="手动平仓")
    close_parser.add_argument("symbol", help="交易对，例如 BTC-USDT-SWAP")
    
    args = parser.parse_args()
    
    if args.command == "status":
        check_status(args.host, args.port)
    elif args.command == "scan":
        trigger_scan(args.host, args.port)
    elif args.command == "update":
        update_parameters(args.host, args.port)
    elif args.command == "reset":
        reset_counters(args.host, args.port)
    elif args.command == "open":
        open_position(args.host, args.port, args.symbol)
    elif args.command == "close":
        close_position(args.host, args.port, args.symbol)
    else:
        parser.print_help()


if __name__ == "__main__":
    main() 