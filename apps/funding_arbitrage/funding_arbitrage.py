#-*- coding: utf-8 -*-
import time
import asyncio
from datetime import datetime
from typing import Dict, Any, List, Optional
import sys
import os
import uuid
import json

# 添加项目根目录到路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

# 导入交易所适配器和日志配置
from src.exchange.okex.trader import OKExTrader
from src.common.logger import configure_logger
from src.common.config_loader import get_app_config
from src.common.position_manager import PositionManager, Position
from src.common.event_loop import AsyncEventLoop
from src.common.order_utils import calculate_order_size  # 导入通用的 calculate_order_size 函数
import logging

# 加载配置
APP_NAME = "funding_arbitrage"
CONFIG = get_app_config(APP_NAME)

# 配置日志 - 这会创建一个单例日志记录器，所有组件都会使用它
configure_logger(
    APP_NAME, 
    CONFIG.get('logging', {}).get('level', 'INFO'),
    CONFIG.get('logging', {}).get('file', 'funding_arbitrage.log'),
    CONFIG.get('logging', {}).get('output_targets')
)

# 获取应用专用的日志记录器 - 这会获取到上面配置的同一个日志记录器
logger = logging.getLogger(APP_NAME)

class FundingArbitrage:
    """资金费率套利策略"""
    
    def __init__(self, trader: OKExTrader, config: Dict, position_manager: PositionManager):
        """
        初始化套利策略
        
        Args:
            trader: OKEx交易接口
            config: 策略配置
            position_manager: 仓位管理器
        """
        self.trader = trader
        self.config = config
        self.position_manager = position_manager
        self.logger = logging.getLogger(APP_NAME)
        
        # 从数据库加载活跃仓位
        self.active_positions = self.position_manager.load_positions()
        self.logger.info(f"从数据库加载了 {len(self.active_positions)} 个活跃仓位")

    async def check_opportunity(self, inst_id: str) -> bool:
        """
        检测套利机会
        
        Args:
            inst_id: 合约ID
            
        Returns:
            是否存在套利机会
        """
        # 确保使用正确的合约ID格式
        if "-SWAP" not in inst_id:
            self.logger.warning(f"传入的标的应该包含-SWAP后缀: {inst_id}，已自动添加")
            inst_id = f"{inst_id}-SWAP"
            
        # 如果已有持仓，跳过
        if inst_id in self.active_positions:
            return False
            
        # 获取资金费率
        funding_rate = self.trader.get_funding_rate(inst_id)
        
        # 如果不允许反向套利且资金费率为负，跳过
        # 注意：正向套利是在资金费率为正时做空合约，收取资金费
        if not self.config['strategy'].get('allow_reverse_arbitrage', False) and funding_rate < 0:
            return False
            
        # 获取价格
        mark_price = self.trader.get_mark_price(inst_id)
        spot_id = inst_id.replace('-SWAP', '')
        spot_price = self.trader.get_spot_price(spot_id)
        
        # 计算基差
        basis = (spot_price - mark_price) / mark_price * 100
        
        # 记录检查信息
        self.logger.info("套利条件检查", extra={
            "inst_id": inst_id,
            "funding_rate": funding_rate,
            "mark_price": mark_price,
            "spot_price": spot_price,
            "basis": basis
        })
        
        # 判断条件
        min_rate = self.config['strategy']['min_funding_rate']
        max_basis = self.config['strategy']['max_basis'] * 100  # 转换为百分比
        
        # 打印详细的判断条件
        self.logger.debug("套利条件详细判断", extra={
            "inst_id": inst_id,
            "funding_rate": funding_rate,
            "min_funding_rate": min_rate,
            "funding_rate_check": funding_rate > min_rate,
            "basis": basis,
            "max_basis": max_basis,
            "basis_check": abs(basis) < max_basis
        })
        
        if funding_rate > min_rate and abs(basis) < max_basis:
            self.logger.info("发现套利机会", extra={
                "inst_id": inst_id,
                "funding_rate": funding_rate,
                "basis": basis
            })
            return True
            
        return False

    async def execute_arbitrage(self, inst_id: str) -> None:
        """
        执行套利策略
        
        Args:
            inst_id: 合约ID
        """
        try:
            # 设置杠杆
            leverage = self.config['strategy']['leverage']
            self.trader.set_leverage(inst_id, leverage)
            
            # 获取价格
            mark_price = self.trader.get_mark_price(inst_id)
            spot_id = inst_id.replace('-SWAP', '')
            spot_price = self.trader.get_spot_price(spot_id)
            
            # 获取可用资金
            available_usdt = self.trader.get_usdt_balance()
            
            # 计算下单数量
            capital_config = self.config['strategy'].get('capital_efficiency', {})
            
            if capital_config.get('enabled', False):
                # 高资金利用率模式
                usage_ratio = capital_config.get('usage_ratio', 0.8)
                spot_ratio = capital_config.get('spot_ratio', 0.3)
                swap_ratio = capital_config.get('swap_ratio', 0.7)
                
                # 计算资金分配
                total_usdt = available_usdt * usage_ratio
                spot_usdt = total_usdt * spot_ratio
                swap_usdt = total_usdt * swap_ratio
                
                # 计算数量
                # 现货数量
                spot_qty = spot_usdt / spot_price
                
                # 合约数量 - 使用通用的 calculate_order_size 函数
                swap_qty, _ = calculate_order_size(
                    trader=self.trader,
                    symbol=inst_id,
                    position_usdt=swap_usdt,
                    unit_type='quote',  # 使用 quote 作为单位类型，即 USDT
                    leverage=leverage,
                    side='sell'  # 套利中合约方向是做空
                )
            else:
                # 标准模式 - 每次使用固定金额
                usdt_amount = 80
                
                # 现货数量
                spot_qty = usdt_amount / spot_price
                
                # 合约数量 - 使用通用的 calculate_order_size 函数
                swap_qty, _ = calculate_order_size(
                    trader=self.trader,
                    symbol=inst_id,
                    position_usdt=usdt_amount,
                    unit_type='quote',  # 使用 quote 作为单位类型，即 USDT
                    leverage=leverage,
                    side='sell'  # 套利中合约方向是做空
                )
            
            # 记录开仓信息
            self.logger.info("开始执行套利", extra={
                "inst_id": inst_id,
                "leverage": leverage,
                "mark_price": mark_price,
                "spot_price": spot_price,
                "swap_qty": swap_qty,
                "spot_qty": spot_qty,
                "available_usdt": available_usdt
            })
            
            # 合约做空
            swap_order = self.trader.swap_order(
                inst_id=inst_id,
                side="sell",
                pos_side="short",
                sz=swap_qty
            )
            
            # 检查合约订单是否成功
            swap_success = self._check_order_success(swap_order)
            
            # 现货做多
            spot_order = self.trader.spot_order(
                inst_id=spot_id,
                side="buy",
                sz=spot_qty
            )
            
            # 检查现货订单是否成功
            spot_success = self._check_order_success(spot_order)
            
            # 记录订单信息
            self.logger.info("套利订单已提交", extra={
                "swap_order": swap_order,
                "spot_order": spot_order,
                "swap_success": swap_success,
                "spot_success": spot_success
            })
            
            # 检查是否有单边持仓，如果有则平掉
            if swap_success and not spot_success:
                self.logger.warning("现货订单失败，平掉合约仓位", extra={
                    "inst_id": inst_id,
                    "swap_order": swap_order
                })
                # 获取合约订单ID并平仓
                await self._close_swap_position(inst_id)
                return
            
            if spot_success and not spot_success:
                self.logger.warning("合约订单失败，平掉现货仓位", extra={
                    "inst_id": spot_id,
                    "spot_order": spot_order
                })
                # 获取现货订单ID并平仓
                await self._close_spot_position(spot_id)
                return
            
            # 如果两边都失败，直接返回
            if not swap_success and not spot_success:
                self.logger.error("套利订单双边失败，无需处理", extra={
                    "swap_order": swap_order,
                    "spot_order": spot_order
                })
                return
            
            # 只有当两边都成功时，才创建仓位记录
            if swap_success and spot_success:
                # 获取实际成交数量
                # 注意：这是临时解决方案，后续会通过成交推送获取实际成交量
                actual_swap_qty = self._get_filled_quantity(swap_order)
                actual_spot_qty = self._get_filled_quantity(spot_order)
                
                # 如果没有获取到成交数量，暂时使用请求数量
                if actual_swap_qty <= 0:
                    self.logger.warning("未获取到合约成交数量，暂时使用请求数量", extra={
                        "inst_id": inst_id,
                        "requested_qty": swap_qty
                    })
                    actual_swap_qty = swap_qty
                
                if actual_spot_qty <= 0:
                    self.logger.warning("未获取到现货成交数量，暂时使用请求数量", extra={
                        "inst_id": spot_id,
                        "requested_qty": spot_qty
                    })
                    actual_spot_qty = spot_qty
                
                self.logger.info("获取实际成交数量", extra={
                    "requested_swap_qty": swap_qty,
                    "actual_swap_qty": actual_swap_qty,
                    "requested_spot_qty": spot_qty,
                    "actual_spot_qty": actual_spot_qty
                })
                
                # 生成唯一的仓位ID
                position_id = str(uuid.uuid4())
                
                # 创建仓位对象
                swap_position = Position(
                    symbol=inst_id,
                    position_id=position_id,
                    entry_price=mark_price,
                    quantity=actual_swap_qty,  # 使用实际成交数量
                    position_type="swap_short",
                    leverage=leverage,
                    timestamp=int(datetime.now().timestamp()),
                    closed=False,
                    exit_price=0.0
                )
                
                spot_position = Position(
                    symbol=spot_id,
                    position_id=position_id,
                    entry_price=spot_price,
                    quantity=actual_spot_qty,  # 使用实际成交数量
                    position_type="spot_long",
                    leverage=1,
                    timestamp=int(datetime.now().timestamp()),
                    closed=False,
                    exit_price=0.0
                )
                
                # 保存仓位到数据库
                self.position_manager.save_position(swap_position)
                self.position_manager.save_position(spot_position)
                
                # 更新内存中的活跃仓位
                self.active_positions[inst_id] = {
                    "swap": swap_position,
                    "spot": spot_position,
                    "open_time": datetime.now(),
                    "funding_rate": self.trader.get_funding_rate(inst_id)
                }
                
                self.logger.info("套利执行完成，仓位已保存", extra={
                    "position_id": position_id,
                    "inst_id": inst_id
                })
            
        except Exception as e:
            self.logger.error("套利执行失败", exc_info=True)
            # 紧急平仓
            await self.emergency_close(inst_id)
    
    def _check_order_success(self, order_response: Dict) -> bool:
        """
        检查订单是否成功
        
        Args:
            order_response: 订单响应
            
        Returns:
            是否成功
        """
        # 检查订单响应
        if not order_response:
            return False
        
        # 检查状态码
        if order_response.get('code') != '0':
            return False
        
        # 检查数据
        data = order_response.get('data', [])
        if not data:
            return False
        
        # 检查每个订单的状态码
        for order in data:
            if order.get('sCode') != '0':
                return False
        
        return True
    
    async def _close_swap_position(self, inst_id: str) -> None:
        """
        平掉合约仓位
        
        Args:
            inst_id: 合约ID
        """
        try:
            # 获取持仓信息
            positions = self.trader.get_positions(inst_id)
            
            # 检查是否有持仓
            if not positions or 'data' not in positions or not positions['data']:
                self.logger.info("没有找到合约持仓，无需平仓", extra={"inst_id": inst_id})
                return
            
            # 遍历持仓
            for position in positions['data']:
                if position.get('pos') and float(position.get('pos')) > 0:
                    # 获取持仓方向
                    pos_side = position.get('posSide', 'long')
                    
                    # 根据持仓方向确定平仓方向
                    side = "buy" if pos_side == "short" else "sell"
                    
                    # 平仓
                    close_order = self.trader.swap_order(
                        inst_id=inst_id,
                        side=side,
                        pos_side=pos_side,
                        sz=position.get('pos')
                    )
                    
                    self.logger.info("合约单边平仓完成", extra={
                        "inst_id": inst_id,
                        "position": position,
                        "close_order": close_order
                    })
        
        except Exception as e:
            self.logger.error("合约单边平仓失败", exc_info=True, extra={"inst_id": inst_id})
    
    async def _close_spot_position(self, spot_id: str) -> None:
        """
        平掉现货仓位
        
        Args:
            spot_id: 现货ID
        """
        try:
            # 获取现货余额
            balances = self.trader.get_balance(spot_id.split('-')[0])
            
            # 检查是否有余额
            if not balances or 'data' not in balances or not balances['data']:
                self.logger.info("没有找到现货余额，无需平仓", extra={"spot_id": spot_id})
                return
            
            # 遍历余额
            for balance in balances['data']:
                for detail in balance.get('details', []):
                    if detail.get('ccy') == spot_id.split('-')[0] and float(detail.get('availBal', 0)) > 0:
                        # 平仓
                        close_order = self.trader.spot_order(
                            inst_id=spot_id,
                            side="sell",
                            sz=detail.get('availBal')
                        )
                        
                        self.logger.info("现货单边平仓完成", extra={
                            "spot_id": spot_id,
                            "balance": detail,
                            "close_order": close_order
                        })
        
        except Exception as e:
            self.logger.error("现货单边平仓失败", exc_info=True, extra={"spot_id": spot_id})

    def _get_contract_size(self, inst_id: str) -> float:
        """
        获取合约面值
        
        Args:
            inst_id: 合约ID
            
        Returns:
            合约面值
        """
        try:
            # 从交易所获取合约信息
            contract_info = self.trader.get_contract_info(inst_id, is_spot=False)
            
            # 提取合约面值
            if contract_info and 'data' in contract_info and len(contract_info['data']) > 0:
                ct_val = float(contract_info['data'][0].get('ctVal', 0.01))
                self.logger.debug("获取合约面值", extra={
                    "inst_id": inst_id,
                    "contract_size": ct_val,
                    "contract_info": contract_info['data'][0]
                })
                return ct_val
            
            # 如果获取失败，使用默认值
            self.logger.warning("无法获取合约面值，使用默认值", extra={"inst_id": inst_id})
            return 0.01
        except Exception as e:
            self.logger.error("获取合约面值异常", exc_info=True, extra={
                "inst_id": inst_id,
                "error": str(e)
            })
            # 出错时使用默认值
            return 0.01

    async def check_unwind_condition(self, inst_id: str) -> bool:
        """
        检查是否满足平仓条件
        
        Args:
            inst_id: 合约ID或现货ID
            
        Returns:
            是否应该平仓
        """
        if inst_id not in self.active_positions:
            return False
        
        position = self.active_positions[inst_id]
        
        # 确保使用合约ID获取资金费率
        swap_id = inst_id
        if "-SWAP" not in inst_id:
            swap_id = f"{inst_id}-SWAP"
            # 如果合约ID不存在于活跃仓位中，则无法检查
            if swap_id not in self.active_positions:
                self.logger.warning(f"找不到对应的合约仓位: {swap_id}")
                return False
        
        # 计算持仓时间 - 使用 timestamp 属性而不是 'open_time'
        hold_hours = (datetime.now() - datetime.fromtimestamp(position.timestamp)).total_seconds() / 3600
        
        # 获取当前资金费率 - 使用合约ID (确保带有-SWAP后缀)
        current_funding_rate = self.trader.get_funding_rate(swap_id)
        
        # 获取开仓时的资金费率 - 这里需要从其他地方获取，因为 Position 类中没有这个属性
        # 暂时使用默认值 0
        open_funding_rate = 0
        
        # 计算未实现盈亏
        pnl, pnl_percent = await self.calculate_unrealized_pnl(swap_id)
        
        # 记录检查信息
        self.logger.info("平仓条件检查", extra={
            "inst_id": inst_id,
            "swap_id": swap_id,
            "hold_hours": hold_hours,
            "pnl": pnl,
            "pnl_percent": pnl_percent,
            "current_funding_rate": current_funding_rate,
            "open_funding_rate": open_funding_rate,
            "target_profit": self.config['strategy']['target_profit'],
            "stop_loss": self.config['strategy']['stop_loss'],
            "max_hold_hours": self.config['strategy']['max_hold_hours']
        })
        
        # 检查条件
        # 1. 达到目标收益
        target_profit_pct = self.config['strategy']['target_profit'] * 100  # 转换为百分比
        if pnl_percent >= target_profit_pct:
            self.logger.info("达到目标收益，准备平仓", extra={
                "inst_id": inst_id,
                "pnl_percent": pnl_percent,
                "target_profit_pct": target_profit_pct
            })
            return True
        
        # 2. 触发止损
        stop_loss_pct = self.config['strategy']['stop_loss'] * 100  # 转换为百分比
        if pnl_percent <= stop_loss_pct:
            self.logger.info("触发止损，准备平仓", extra={
                "inst_id": inst_id,
                "pnl_percent": pnl_percent,
                "stop_loss_pct": stop_loss_pct
            })
            return True
        
        # 3. 超过最大持仓时间
        if hold_hours >= self.config['strategy']['max_hold_hours']:
            self.logger.info("超过最大持仓时间，准备平仓", extra={
                "inst_id": inst_id,
                "hold_hours": hold_hours
            })
            return True
        
        # 4. 资金费率变为负数（如果开仓时为正）
        if open_funding_rate > 0 and current_funding_rate < 0:
            self.logger.info("资金费率由正转负，准备平仓", extra={
                "inst_id": inst_id,
                "open_funding_rate": open_funding_rate,
                "current_funding_rate": current_funding_rate
            })
            return True
        
        return False

    async def execute_unwind(self, inst_id: str) -> None:
        """
        执行平仓策略
        
        Args:
            inst_id: 合约ID
        """
        # 确保使用正确的合约ID格式
        if "-SWAP" not in inst_id:
            self.logger.warning(f"传入的标的应该包含-SWAP后缀: {inst_id}，已自动添加")
            inst_id = f"{inst_id}-SWAP"
            
        if inst_id not in self.active_positions:
            self.logger.warning(f"尝试平仓不存在的持仓: {inst_id}")
            return
        
        try:
            position = self.active_positions[inst_id]
            swap_position = position['swap']
            spot_position = position['spot']
            
            # 获取当前价格
            mark_price = self.trader.get_mark_price(inst_id)
            spot_id = inst_id.replace('-SWAP', '')
            spot_price = self.trader.get_spot_price(spot_id)
            
            # 计算现货卖出数量，考虑手续费
            # 减去一定比例作为手续费缓冲，避免因手续费导致的卖出失败
            fee_buffer_percent = 0.005  # 0.5%的缓冲
            adjusted_spot_qty = spot_position.quantity * (1 - fee_buffer_percent)
            
            # 如果现货价值较小，直接减去固定金额
            spot_value = spot_position.quantity * spot_price
            if spot_value <= 100:  # 如果现货价值小于100 USDT
                fee_buffer_usdt = 0.5  # 减去0.5 USDT
                adjusted_spot_qty = max(0, spot_position.quantity - (fee_buffer_usdt / spot_price))
            
            self.logger.info("开始执行平仓", extra={
                "inst_id": inst_id,
                "swap_position": vars(swap_position),
                "spot_position": vars(spot_position),
                "current_mark_price": mark_price,
                "current_spot_price": spot_price,
                "original_spot_qty": spot_position.quantity,
                "adjusted_spot_qty": adjusted_spot_qty,
                "fee_buffer_applied": f"{fee_buffer_percent * 100}%"
            })
            
            # 合约平空
            swap_order = self.trader.swap_order(
                inst_id=inst_id,
                side="buy",
                pos_side="short",
                sz=swap_position.quantity
            )
            
            # 现货卖出 - 使用调整后的数量
            spot_order = self.trader.spot_order(
                inst_id=spot_id,
                side="sell",
                sz=adjusted_spot_qty
            )
            
            # 检查订单是否成功
            swap_success = self._check_order_success(swap_order)
            spot_success = self._check_order_success(spot_order)
            
            # 记录订单信息
            self.logger.info("平仓订单已提交", extra={
                "swap_order": swap_order,
                "spot_order": spot_order,
                "swap_success": swap_success,
                "spot_success": spot_success
            })
            
            # 如果有订单失败，记录错误但仍然继续处理
            if not swap_success or not spot_success:
                self.logger.warning("部分平仓订单失败", extra={
                    "swap_success": swap_success,
                    "spot_success": spot_success
                })
            
            # 计算盈亏
            pnl, pnl_percent = await self.calculate_unrealized_pnl(inst_id)
            
            # 更新仓位状态
            self.position_manager.close_position(inst_id, mark_price)
            self.position_manager.close_position(spot_id, spot_price)
            
            # 记录平仓结果
            self.logger.info("平仓执行完成", extra={
                "inst_id": inst_id,
                "pnl": pnl,
                "pnl_percent": pnl_percent
            })
            
            # 移除持仓记录
            del self.active_positions[inst_id]
            
        except Exception as e:
            self.logger.error("平仓执行失败", exc_info=True, extra={"error": str(e)})
            # 不移除持仓记录，等待下次尝试

    async def emergency_close(self, inst_id: str) -> None:
        """
        紧急平仓处理
        
        Args:
            inst_id: 合约ID
        """
        if inst_id not in self.active_positions:
            return
        
        try:
            self.logger.warning(f"执行紧急平仓: {inst_id}")
            
            # 尝试平仓
            await self.execute_unwind(inst_id)
            
        except Exception as e:
            self.logger.critical("紧急平仓失败", exc_info=True, extra={
                "inst_id": inst_id,
                "error": str(e)
            })
            
            # 即使失败也移除持仓记录，防止卡死
            if inst_id in self.active_positions:
                del self.active_positions[inst_id]

    async def calculate_unrealized_pnl(self, inst_id: str) -> tuple:
        """
        计算未实现盈亏
        
        Args:
            inst_id: 合约ID
            
        Returns:
            (盈亏金额, 盈亏百分比)
        """
        if inst_id not in self.active_positions:
            return 0, 0
        
        # 确保使用正确的合约ID和现货ID格式
        swap_id = inst_id
        spot_id = inst_id.replace('-SWAP', '')
        
        # 获取字典中的position对象
        position_data = self.active_positions[inst_id]
        if not isinstance(position_data, dict) or 'swap' not in position_data or 'spot' not in position_data:
            self.logger.warning(f"仓位数据格式不正确: {inst_id}")
            return 0, 0
            
        swap_position = position_data['swap']
        spot_position = position_data['spot']
        
        # 获取当前价格
        current_price = self.trader.get_mark_price(swap_id)
        
        # 合约盈亏 (入场价格 - 当前价格) * 数量 (因为是做空)
        contract_size = self._get_contract_size(swap_id)
        swap_pnl = swap_position.quantity * (swap_position.entry_price - current_price) * contract_size
        
        # 现货盈亏 (当前价格 - 入场价格) * 数量 (因为是做多)
        spot_price = self.trader.get_spot_price(spot_id)
        spot_pnl = spot_position.quantity * (spot_price - spot_position.entry_price)
        
        # 总盈亏
        total_pnl = swap_pnl + spot_pnl
        
        # 计算投入资金 (合约保证金 + 现货投入)
        # 合约保证金 = 合约面值 * 数量 * 入场价格 / 杠杆
        swap_margin = swap_position.quantity * swap_position.entry_price * contract_size / swap_position.leverage
        
        # 现货投入 = 数量 * 入场价格
        spot_investment = spot_position.quantity * spot_position.entry_price
        
        # 总投入资金
        initial_investment = swap_margin + spot_investment
        
        # 计算盈亏百分比
        pnl_percent = (total_pnl / initial_investment) * 100 if initial_investment > 0 else 0
        
        # 记录详细的盈亏计算
        self.logger.debug("盈亏计算详情", extra={
            "inst_id": inst_id,
            "current_price": current_price,
            "spot_price": spot_price,
            "swap_entry_price": swap_position.entry_price,
            "spot_entry_price": spot_position.entry_price,
            "swap_quantity": swap_position.quantity,
            "spot_quantity": spot_position.quantity,
            "contract_size": contract_size,
            "swap_pnl": swap_pnl,
            "spot_pnl": spot_pnl,
            "total_pnl": total_pnl,
            "swap_margin": swap_margin,
            "spot_investment": spot_investment,
            "initial_investment": initial_investment,
            "pnl_percent": pnl_percent
        })
        
        return total_pnl, pnl_percent

    def _get_filled_quantity(self, order_response: Dict) -> float:
        """
        从订单响应中获取实际成交数量
        
        Args:
            order_response: 订单响应
            
        Returns:
            实际成交数量
        """
        try:
            # 检查订单响应
            if not order_response or 'data' not in order_response or not order_response['data']:
                return 0.0
            
            # 获取第一个订单数据
            order_data = order_response['data'][0]
            
            # 尝试获取成交数量
            filled_qty = order_data.get('fillSz', 0)
            if filled_qty and float(filled_qty) > 0:
                return float(filled_qty)
            
            # 如果没有成交数量，则使用请求数量
            # 注意：这是临时解决方案，后续会通过成交推送获取实际成交量
            requested_qty = order_data.get('sz', 0)
            
            # 由于是市价单，假设请求数量会全部成交
            # 后续会通过成交推送更新实际成交量
            if requested_qty:
                self.logger.info("订单响应中没有成交数量信息，暂时使用请求数量", extra={
                    "order_id": order_data.get('ordId', ''),
                    "requested_qty": requested_qty
                })
                return float(requested_qty)
            
            return 0.0
            
        except Exception as e:
            self.logger.error("获取实际成交数量失败", exc_info=True, extra={
                "order_response": order_response,
                "error": str(e)
            })
            # 出错时返回0
            return 0.0

    async def run_opportunity_check(self) -> None:
        """定期检查套利机会"""
        for inst_id in self.config.get('instruments', []):
            try:
                # 确保使用正确的合约ID格式
                if "-SWAP" not in inst_id:
                    self.logger.warning(f"配置的标的应该包含-SWAP后缀: {inst_id}，已自动添加")
                    inst_id = f"{inst_id}-SWAP"
                    
                if await self.check_opportunity(inst_id):
                    await self.execute_arbitrage(inst_id)
            except Exception as e:
                self.logger.error(f"检查套利机会异常: {inst_id}", exc_info=True)

    async def run_unwind_check(self) -> None:
        """定期检查平仓条件"""
        for inst_id in list(self.active_positions.keys()):
            try:
                # 记录正在检查的仓位
                self.logger.debug(f"检查平仓条件: {inst_id}", extra={
                    "position_type": type(self.active_positions[inst_id]).__name__
                })
                
                # 确保inst_id包含-SWAP后缀
                if "-SWAP" not in inst_id:
                    self.logger.warning(f"仓位ID应该包含-SWAP后缀: {inst_id}，可能会导致问题")
                
                if await self.check_unwind_condition(inst_id):
                    await self.execute_unwind(inst_id)
            except Exception as e:
                self.logger.error(f"检查平仓条件异常: {inst_id}", exc_info=True, extra={
                    "error": str(e),
                    "position_type": type(self.active_positions[inst_id]).__name__ if inst_id in self.active_positions else "Unknown"
                })

async def main_async():
    """异步主函数"""
    # 创建交易工具
    exchange_config = CONFIG['exchange'].copy()
    if 'logging' in CONFIG:
        exchange_config['logging'] = CONFIG['logging']
    
    trader = OKExTrader(APP_NAME, exchange_config)
    
    # 创建仓位管理器
    position_manager = PositionManager(APP_NAME)
    
    # 创建套利策略实例
    arbitrage = FundingArbitrage(trader, CONFIG, position_manager)
    
    # 创建事件循环
    event_loop_config = CONFIG.get('event_loop', {})
    event_loop = AsyncEventLoop(max_workers=event_loop_config.get('max_workers', 5))
    
    # 添加定期任务
    check_interval = event_loop_config.get('check_interval', 60)
    unwind_check_interval = event_loop_config.get('unwind_check_interval', 300)
    
    # 添加检查套利机会的任务
    event_loop.add_task(arbitrage.run_opportunity_check, interval=check_interval, immediate=True)
    
    # 添加检查平仓条件的任务
    event_loop.add_task(arbitrage.run_unwind_check, interval=unwind_check_interval, immediate=True)
    
    return event_loop

def main():
    """主函数"""
    # 获取日志配置
    log_file = CONFIG.get('logging', {}).get('file', 'funding_arbitrage.log')
    output_targets = CONFIG.get('logging', {}).get('output_targets', ['file', 'console'])
    
    # 显示程序启动信息
    print(f"资金费率套利策略已启动")
    
    # 根据输出目标显示日志文件位置
    if 'file' in output_targets:
        print(f"日志文件位置: logs/{APP_NAME}/{log_file}")
    
    # 记录程序启动日志
    logger.info("资金费率套利策略已启动", extra={
        "strategy_config": CONFIG.get('strategy', {}),
        "instruments": CONFIG.get('instruments', [])
    })
    
    # 运行异步主函数 - Python 3.6 兼容方式
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        # 创建并获取事件循环
        event_loop = loop.run_until_complete(main_async())
        # 运行事件循环
        event_loop.run_forever()
    except KeyboardInterrupt:
        logger.info("程序已停止")
    finally:
        loop.close()

if __name__ == "__main__":
    main()
