import logging
from typing import Tuple, Optional, Dict, Any

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
    is_swap = "-SWAP" in symbol            # 永续合约
    
    # 确定委托单位
    if unit_type.lower() in ['usdt', 'usd', 'quote']:
        if is_usdt_margined and is_swap:
            # USDT本位永续合约按张数下单
            tgt_ccy = None
            size = trader.calculate_position_size(
                symbol, False, position_usdt * leverage
            )
            logger.info(f"USDT本位永续合约按张数下单: 保证金={position_usdt} USDT, 杠杆={leverage}, 张数={size}")
        elif is_usdt_margined:
            # 其他USDT本位合约按USDT计价
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
        if is_usdt_margined and is_swap:
            tgt_ccy = None
            size = trader.calculate_position_size(
                symbol, False, position_usdt * leverage
            )
            logger.info(f"默认USDT本位永续合约按张数下单: 保证金={position_usdt} USDT, 杠杆={leverage}, 张数={size}")
        elif is_usdt_margined:
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

def get_price_precision(trader, symbol: str, is_spot: bool = False) -> int:
    """
    从交易所获取交易对的价格精度
    
    Args:
        trader: 交易执行器，需要实现 get_contract_info 方法
        symbol: 交易对，如 BTC-USDT-SWAP
        is_spot: 是否为现货交易
        
    Returns:
        int: 价格精度，即小数点后的位数
    """
    try:
        # 从交易所获取合约/交易对信息
        contract_info = trader.get_contract_info(symbol, is_spot)["data"][0]
        
        # 获取最小价格变动单位（tick size）
        tick_size = contract_info.get('tickSz', '0.0001')  # 默认精度为4位
        
        # 计算精度（小数点后的位数）
        if '.' in tick_size:
            precision = len(tick_size.split('.')[1])
        else:
            precision = 0
            
        logger.debug(f"获取{symbol}价格精度: {precision}位小数 (tickSz={tick_size})")
        return precision
    except Exception as e:
        logger.error(f"获取价格精度失败: {e}", exc_info=True)
        return 4  # 默认返回4位精度作为备选 