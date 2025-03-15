import logging
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

def calculate_order_size(
    trader, 
    symbol: str, 
    position_usdt: float, 
    unit_type: str, 
    leverage: int, 
    side: Optional[str] = None
) -> Tuple[float, Optional[str]]:
    """
    计算订单大小
    
    Args:
        trader: 交易执行器，需要实现 get_mark_price 和 calculate_position_size 方法
        symbol: 交易对
        position_usdt: 保证金金额(USDT)
        unit_type: 单位类型 (quote/base/contract)
        leverage: 杠杆倍数
        side: 交易方向 (buy/sell)，用于确定市价单的单位
        
    Returns:
        tuple: (size, tgt_ccy) - 订单大小和目标货币类型
    """
    # 获取当前价格，用于计算
    current_price = trader.get_mark_price(symbol)
    
    # 检查合约类型
    is_usdt_margined = "-USDT-" in symbol  # USDT本位合约
    is_coin_margined = "-USD-" in symbol   # 币本位合约
    
    # 确定委托单位
    if unit_type.lower() in ['usdt', 'usd', 'quote']:
        if is_usdt_margined:
            # USDT本位合约按USDT计价
            tgt_ccy = "quote_ccy"
            size = position_usdt * leverage
            logger.info(f"USDT本位合约按USDT下单: 保证金={position_usdt} USDT, 杠杆={leverage}, 总仓位={size} USDT")
        elif is_coin_margined:
            # 币本位合约按张数下单
            tgt_ccy = None
            size = trader.calculate_position_size(
                symbol, False, position_usdt * leverage
            )
            logger.info(f"币本位合约按张数下单: 保证金={position_usdt} USDT, 杠杆={leverage}, 张数={size}")
        else:
            # 其他类型合约，默认按USDT
            tgt_ccy = "quote_ccy"
            size = position_usdt * leverage
            logger.info(f"按USDT下单: 保证金={position_usdt} USDT, 杠杆={leverage}, 总仓位={size} USDT")
    elif unit_type.lower() in ['coin', 'base']:
        # 按币种数量
        tgt_ccy = "base_ccy"
        total_value = position_usdt * leverage
        size = total_value / current_price
        logger.info(f"按币种下单: 保证金={position_usdt} USDT, 杠杆={leverage}, 总价值={total_value} USDT, 币数量约={size:.6f}")
    elif unit_type.lower() in ['contract', 'contracts']:
        # 按合约张数
        tgt_ccy = None
        size = trader.calculate_position_size(
            symbol, False, position_usdt * leverage
        )
        logger.info(f"按合约张数下单: 保证金={position_usdt} USDT, 杠杆={leverage}, 张数={size}")
    else:
        # 默认处理
        if is_usdt_margined:
            tgt_ccy = "quote_ccy"
            size = position_usdt * leverage
            logger.info(f"默认USDT本位合约按USDT下单: 保证金={position_usdt} USDT, 杠杆={leverage}, 总仓位={size} USDT")
        else:
            tgt_ccy = None
            size = trader.calculate_position_size(
                symbol, False, position_usdt * leverage
            )
            logger.info(f"默认按张数下单: 保证金={position_usdt} USDT, 杠杆={leverage}, 张数={size}")
    
    return size, tgt_ccy 