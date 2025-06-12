import sqlite3
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from threading import Lock
import time
import datetime
import logging

from src.common.risk_control import RiskController

@dataclass
class Position:
    symbol: str
    position_id: str
    entry_price: float
    quantity: float
    position_type: str  # spot/swap
    leverage: int
    timestamp: int
    closed: bool = False
    exit_price: float = 0.0
    exit_timestamp: int = 0
    pnl_amount: float = 0.0
    pnl_percentage: float = 0.0
    # 阶梯止盈相关参数
    ladder_tp: bool = False  # 是否启用阶梯止盈
    ladder_tp_pct: float = 0.2  # 阶梯止盈每档百分比(0.2表示每增加20%收益平仓20%)
    ladder_tp_step: float = 0.2  # 阶梯止盈每档的档位(0.2表示20%收益，40%收益，60%收益...)
    ladder_closed_pct: float = 0.0  # 已经通过阶梯止盈平仓的百分比
    direction: str = None  # 方向：long或short
    high_price: float = 0.0  # 持仓期间的最高价格
    low_price: float = float('inf')  # 持仓期间的最低价格
    signal: any = None  # 开仓信号
    
    # 新增字段 - 从API获取的实际数据
    avg_price: float = 0.0  # API返回的持仓均价
    pos_id: str = ""  # 交易所持仓ID
    realized_pnl: float = 0.0  # API返回的已实现盈亏
    unrealized_pnl: float = 0.0  # 未实现盈亏(upl)
    last_sync_time: int = 0  # 上次与API同步的时间戳
    margin: float = 0.0  # 保证金
    last_price: float = 0.0  # 最新价格
    extra_data: Dict = None  # 额外数据，用于存储临时信息
    status: str = "open"  # 状态：open/closed
    close_time: int = 0  # 平仓时间戳
    holding_time: float = 0.0  # 持仓时间（分钟）
    
    def __post_init__(self):
        """初始化后的处理"""
        # 设置open_time属性为timestamp的别名，用于兼容
        self.open_time = self.timestamp
        
        # 如果没有指定方向，则根据数量确定
        if self.direction is None:
            self.direction = "long" if self.quantity > 0 else "short"
        elif not isinstance(self.direction, str):
            # 防止direction被错误设置为非字符串值(如价格)
            self.direction = "long" if self.quantity > 0 else "short"
            
        # 确保direction只能是"long"或"short"
        if self.direction not in ["long", "short"]:
            # 如果direction不是有效值，根据数量重新设置
            self.direction = "long" if self.quantity > 0 else "short"
            
        # 确保close_time是整数类型
        if not isinstance(self.close_time, int):
            try:
                self.close_time = int(self.close_time) if self.close_time else 0
            except (ValueError, TypeError):
                self.close_time = 0
            
        # 记录原始的已实现盈亏值，便于调试  
        original_realized_pnl = self.realized_pnl
            
        # 确保已实现盈亏是浮点数类型，避免字段混淆
        if self.realized_pnl in ["long", "short"]:
            # 如果realized_pnl被错误地设置为方向值，将其重置为0.0
            if hasattr(self, 'logger') and self.logger:
                self.logger.warning(f"{self.symbol} 发现已实现盈亏值被错误设置为方向值: {self.realized_pnl}")
            self.realized_pnl = 0.0
        elif isinstance(self.realized_pnl, str):
            try:
                self.realized_pnl = float(self.realized_pnl)
            except (ValueError, TypeError):
                if hasattr(self, 'logger') and self.logger:
                    self.logger.warning(f"{self.symbol} 无法将已实现盈亏字符串转换为浮点数: {self.realized_pnl}")
                self.realized_pnl = 0.0
        elif not isinstance(self.realized_pnl, (int, float)):
            # 如果是其他非数值类型，重置为0
            if hasattr(self, 'logger') and self.logger:
                self.logger.warning(f"{self.symbol} 已实现盈亏类型错误: {type(self.realized_pnl)}，值: {self.realized_pnl}")
            self.realized_pnl = 0.0
            
        # 确保margin是浮点数类型
        if isinstance(self.margin, str):
            try:
                self.margin = float(self.margin)
            except (ValueError, TypeError):
                if hasattr(self, 'logger') and self.logger:
                    self.logger.warning(f"{self.symbol} 无法将保证金字符串转换为浮点数: {self.margin}")
                self.margin = 0.0
        elif not isinstance(self.margin, (int, float)):
            # 如果是其他非数值类型，重置为0
            if hasattr(self, 'logger') and self.logger:
                self.logger.warning(f"{self.symbol} 保证金类型错误: {type(self.margin)}，值: {self.margin}")
            self.margin = 0.0
            
        # 如果已实现盈亏变化很大，记录日志
        if isinstance(original_realized_pnl, (int, float)) and abs(self.realized_pnl - original_realized_pnl) > 0.01:
            if hasattr(self, 'logger') and self.logger:
                self.logger.warning(f"{self.symbol} 已实现盈亏值在初始化中发生变化: {original_realized_pnl} -> {self.realized_pnl}")
                
        # 初始化高低价，确保是float类型
        if self.high_price == 0.0 or isinstance(self.high_price, str):
            if isinstance(self.high_price, str):
                try:
                    self.high_price = float(self.high_price)
                except (ValueError, TypeError):
                    self.high_price = self.entry_price
            else:
                self.high_price = self.entry_price
        
        if self.low_price == float('inf') or isinstance(self.low_price, str):
            self.low_price = float(self.entry_price) if self.entry_price else float('inf')
        else:
            # 确保low_price是float类型
            self.low_price = float(self.low_price)
            
        # 确保所有价格字段都是float类型
        if isinstance(self.entry_price, str):
            self.entry_price = float(self.entry_price)
            
        if isinstance(self.exit_price, str):
            self.exit_price = float(self.exit_price)
            
        if isinstance(self.avg_price, str):
            self.avg_price = float(self.avg_price)
            
        if isinstance(self.last_price, str):
            self.last_price = float(self.last_price)
            
        # 初始化extra_data如果为None
        if self.extra_data is None:
            self.extra_data = {}
        
        # 存储原始的已实现盈亏值用于恢复
        self.extra_data['initial_realized_pnl'] = self.realized_pnl

class PositionManager:
    def __init__(self, app_name: str, logger=None, db_file=None, config=None, trader=None, symbol=None, data_cache=None):
        """
        初始化仓位管理器
        
        Args:
            app_name: 应用名称
            logger: 日志记录器，如果不提供则使用应用名称创建一个
            db_file: 数据库文件路径
            config: 风控配置参数
            trader: 交易员实例
            symbol: 合约ID
            data_cache: 数据缓存对象
        """
        self.app_name = app_name
        
        # 确保数据库目录存在
        os.makedirs("databases", exist_ok=True)
        self.db_path = os.path.join("databases", f"{app_name}.db")

        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.db_lock = Lock()
        self.logger = logger or logging.getLogger(f"{app_name}.position")
        
        # 初始化风控器
        self.risk_controller = RiskController(self.logger)
        
        self.trader = trader
        self.data_cache = data_cache
        
        self._init_db()
    
    def _init_db(self):
        """初始化数据库表结构"""
        # 创建position表
        self.conn.execute('''
        CREATE TABLE IF NOT EXISTS positions (
            symbol TEXT,
            position_id TEXT PRIMARY KEY,
            entry_price REAL,
            quantity REAL,
            position_type TEXT,
            leverage INTEGER,
            timestamp INTEGER,
            closed INTEGER,
            exit_price REAL,
            exit_timestamp INTEGER,
            pnl_amount REAL,
            pnl_percentage REAL,
            ladder_tp INTEGER,
            ladder_tp_pct REAL,
            ladder_tp_step REAL,
            ladder_closed_pct REAL,
            direction TEXT,
            high_price REAL,
            low_price REAL
        )
        ''')
        
        self.conn.commit()
        
        # 添加新字段
        self._add_column_if_not_exists("positions", "high_price", "REAL", 0.0)
        self._add_column_if_not_exists("positions", "low_price", "REAL", 0.0)
        self._add_column_if_not_exists("positions", "avg_price", "REAL", 0.0)
        self._add_column_if_not_exists("positions", "pos_id", "TEXT", "")
        self._add_column_if_not_exists("positions", "realized_pnl", "REAL", 0.0)
        self._add_column_if_not_exists("positions", "unrealized_pnl", "REAL", 0.0)
        self._add_column_if_not_exists("positions", "last_sync_time", "INTEGER", 0)
        self._add_column_if_not_exists("positions", "margin", "REAL", 0.0)
        self._add_column_if_not_exists("positions", "last_price", "REAL", 0.0)
        self._add_column_if_not_exists("positions", "signal", "TEXT", None)
        self._add_column_if_not_exists("positions", "direction", "TEXT", "long") # 确保添加方向列
        self._add_column_if_not_exists("positions", "close_time", "INTEGER", 0) # 添加平仓时间字段
        
    def _column_exists(self, table, column):
        """检查表中是否存在指定列"""
        cursor = self.conn.execute(f"PRAGMA table_info({table})")
        columns = [row[1] for row in cursor.fetchall()]
        return column in columns
        
    def _add_column_if_not_exists(self, table, column, column_type, default_value=None):
        """如果列不存在则添加"""
        if not self._column_exists(table, column):
            default_clause = ""
            if default_value is not None:
                if isinstance(default_value, str):
                    default_clause = f" DEFAULT '{default_value}'"
                else:
                    default_clause = f" DEFAULT {default_value}"
                    
            sql = f"ALTER TABLE {table} ADD COLUMN {column} {column_type}{default_clause}"
            self.logger.info(f"添加列: {sql}")
            self.conn.execute(sql)
            self.conn.commit()
            
    def load_positions(self, include_closed=False, symbol=None, from_timestamp=None, to_timestamp=None, limit=1000, dict_format=False):
        """
        从数据库加载仓位
        
        Args:
            include_closed: 是否包含已平仓的仓位
            symbol: 币种
            from_timestamp: 起始时间戳
            to_timestamp: 结束时间戳
            limit: 最大返回数量
            dict_format: 是否返回字典格式，True则以symbol为键返回字典
            
        Returns:
            List[Position] 或 Dict[str, Position]: 仓位列表或字典
        """
        try:
            cursor = self.conn.cursor()
            
            # 首先获取表结构，确认列顺序
            cursor.execute("PRAGMA table_info(positions)")
            columns_info = cursor.fetchall()
            column_names = [col[1] for col in columns_info]
            
            # 找到关键字段的索引
            realized_pnl_idx = column_names.index('realized_pnl') if 'realized_pnl' in column_names else None
            margin_idx = column_names.index('margin') if 'margin' in column_names else None
            direction_idx = column_names.index('direction') if 'direction' in column_names else None
            
            #self.logger.info(f"数据库字段索引: realized_pnl={realized_pnl_idx}, margin={margin_idx}, direction={direction_idx}")
            
            query = "SELECT * FROM positions"
            params = []
            conditions = []
            
            if not include_closed:
                conditions.append("closed = 0")
                
            if symbol:
                conditions.append("symbol = ?")
                params.append(symbol)
                
            if from_timestamp:
                conditions.append("timestamp >= ?")
                params.append(from_timestamp)
                
            if to_timestamp:
                conditions.append("timestamp <= ?")
                params.append(to_timestamp)
                
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
                
            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            positions = []
            
            for row in rows:
                # 基本字段
                symbol = row[0]
                position_id = row[1]
                entry_price = row[2]
                quantity = row[3]
                position_type = row[4]
                leverage = row[5]
                timestamp = row[6]
                closed = bool(row[7])
                exit_price = row[8]
                exit_timestamp = row[9]
                pnl_amount = row[10]
                pnl_percentage = row[11]
                ladder_tp = bool(row[12])
                ladder_tp_pct = row[13]
                ladder_tp_step = row[14]
                ladder_closed_pct = row[15]
                
                # 使用索引获取方向，确保正确
                direction = row[direction_idx] if direction_idx is not None and direction_idx < len(row) else None
                if direction is None or direction not in ['long', 'short']:
                    direction = "long" if quantity > 0 else "short"
                
                # 使用索引获取high_price和low_price
                high_price_idx = column_names.index('high_price') if 'high_price' in column_names else None
                low_price_idx = column_names.index('low_price') if 'low_price' in column_names else None
                high_price = row[high_price_idx] if high_price_idx is not None and high_price_idx < len(row) else 0.0
                low_price = row[low_price_idx] if low_price_idx is not None and low_price_idx < len(row) else float('inf')
                
                # 创建基本的Position对象
                position = Position(
                    symbol=symbol,
                    position_id=position_id,
                    entry_price=entry_price,
                    quantity=quantity,
                    position_type=position_type,
                    leverage=leverage,
                    timestamp=timestamp,
                    closed=closed,
                    exit_price=exit_price,
                    exit_timestamp=exit_timestamp,
                    pnl_amount=pnl_amount,
                    pnl_percentage=pnl_percentage,
                    ladder_tp=ladder_tp,
                    ladder_tp_pct=ladder_tp_pct,
                    ladder_tp_step=ladder_tp_step,
                    ladder_closed_pct=ladder_closed_pct,
                    direction=direction,
                    high_price=high_price,
                    low_price=low_price
                )
                
                # 使用索引获取额外字段
                avg_price_idx = column_names.index('avg_price') if 'avg_price' in column_names else None
                pos_id_idx = column_names.index('pos_id') if 'pos_id' in column_names else None
                realized_pnl_idx = column_names.index('realized_pnl') if 'realized_pnl' in column_names else None
                unrealized_pnl_idx = column_names.index('unrealized_pnl') if 'unrealized_pnl' in column_names else None
                last_sync_time_idx = column_names.index('last_sync_time') if 'last_sync_time' in column_names else None
                margin_idx = column_names.index('margin') if 'margin' in column_names else None
                last_price_idx = column_names.index('last_price') if 'last_price' in column_names else None
                
                # 添加额外字段
                if avg_price_idx is not None and avg_price_idx < len(row):
                    position.avg_price = row[avg_price_idx] if row[avg_price_idx] is not None else 0.0
                
                if pos_id_idx is not None and pos_id_idx < len(row):
                    position.pos_id = row[pos_id_idx] if row[pos_id_idx] is not None else ""
                
                if realized_pnl_idx is not None and realized_pnl_idx < len(row):
                    # 确保已实现盈亏是数值类型
                    raw_realized_pnl = row[realized_pnl_idx]
                    if raw_realized_pnl in ['long', 'short']:
                        self.logger.warning(f"数据库中 {symbol} 的已实现盈亏字段包含方向值 '{raw_realized_pnl}'，设置为默认值0.0")
                        position.realized_pnl = 0.0
                    else:
                        try:
                            position.realized_pnl = float(raw_realized_pnl) if raw_realized_pnl is not None else 0.0
                        except (ValueError, TypeError):
                            self.logger.warning(f"无法将 {symbol} 的已实现盈亏 '{raw_realized_pnl}' 转换为浮点数，设置为0.0")
                            position.realized_pnl = 0.0
                
                if unrealized_pnl_idx is not None and unrealized_pnl_idx < len(row):
                    position.unrealized_pnl = float(row[unrealized_pnl_idx]) if row[unrealized_pnl_idx] is not None else 0.0
                
                if last_sync_time_idx is not None and last_sync_time_idx < len(row):
                    position.last_sync_time = row[last_sync_time_idx] if row[last_sync_time_idx] is not None else 0
                
                if margin_idx is not None and margin_idx < len(row):
                    # 确保保证金是数值类型
                    raw_margin = row[margin_idx]
                    if raw_margin in ['long', 'short']:
                        self.logger.warning(f"数据库中 {symbol} 的保证金字段包含方向值 '{raw_margin}'，设置为默认值0.0")
                        position.margin = 0.0
                    else:
                        try:
                            position.margin = float(raw_margin) if raw_margin is not None else 0.0
                        except (ValueError, TypeError):
                            self.logger.warning(f"无法将 {symbol} 的保证金 '{raw_margin}' 转换为浮点数，设置为0.0")
                            position.margin = 0.0
                
                if last_price_idx is not None and last_price_idx < len(row):
                    position.last_price = float(row[last_price_idx]) if row[last_price_idx] is not None else 0.0
                
                # 添加close_time字段
                close_time_idx = column_names.index('close_time') if 'close_time' in column_names else None
                if close_time_idx is not None and close_time_idx < len(row):
                    position.close_time = row[close_time_idx] if row[close_time_idx] is not None else 0
                
                # 日志输出实际读取的值
                self.logger.debug(f"加载 {symbol} 持仓: realized_pnl={position.realized_pnl}, margin={position.margin}, direction={position.direction}")
                
                positions.append(position)
            
            # 如果需要返回字典格式
            if dict_format:
                positions_dict = {}
                for position in positions:
                    positions_dict[position.symbol] = position
                return positions_dict
            
            # 否则返回列表格式
            return positions
        except Exception as e:
            self.logger.error(f"加载仓位失败: {str(e)}", exc_info=True)
            return {} if dict_format else []
    
    def save_position(self, position):
        """
        保存仓位到数据库，如果仓位ID已存在则更新，否则新增
        """
        try:
            # 添加字段验证
            # 检查realized_pnl字段，确保是浮点数且不是方向值
            realized_pnl = position.realized_pnl
            if realized_pnl in ['long', 'short']:
                self.logger.warning(f"【数据错误】{position.symbol} 保存前发现realized_pnl为方向值: {realized_pnl}，重置为0.0")
                position.realized_pnl = 0.0
                realized_pnl = 0.0
            elif not isinstance(realized_pnl, (int, float)):
                try:
                    position.realized_pnl = float(realized_pnl)
                    self.logger.warning(f"【数据格式】{position.symbol} 保存前转换realized_pnl为浮点数: {realized_pnl} -> {position.realized_pnl}")
                except (ValueError, TypeError):
                    self.logger.error(f"【数据类型】{position.symbol} realized_pnl类型错误: {type(realized_pnl)}，值: {realized_pnl}")
                    position.realized_pnl = 0.0
            
            # 检查保证金字段
            margin = position.margin
            if not isinstance(margin, (int, float)):
                try:
                    position.margin = float(margin)
                    self.logger.warning(f"【数据格式】{position.symbol} 保存前转换margin为浮点数: {margin} -> {position.margin}")
                except (ValueError, TypeError):
                    self.logger.error(f"【数据类型】{position.symbol} margin类型错误: {type(margin)}，值: {margin}")
                    position.margin = 0.0
            
            # 检查是否有异常大的实现盈亏值(超过保证金的2倍可能有问题)
            if abs(position.realized_pnl) > abs(position.margin) * 2 and abs(position.margin) > 0:
                self.logger.warning(f"【数据异常】{position.symbol} realized_pnl({position.realized_pnl})远大于margin({position.margin})，可能存在错误")
                
            # 添加调试日志，记录关键字段的值
            self.logger.info(f"【保存】{position.symbol} 保存字段: realized_pnl={position.realized_pnl}, margin={position.margin}")
                
            cursor = self.conn.cursor()
            
            # 检查是否存在
            cursor.execute("SELECT COUNT(*) FROM positions WHERE position_id = ?", (position.position_id,))
            exists = cursor.fetchone()[0] > 0
            
            if exists:
                # 更新现有记录
                cursor.execute('''
                UPDATE positions SET 
                    symbol = ?, 
                    entry_price = ?, 
                    quantity = ?, 
                    position_type = ?,
                    leverage = ?,
                    timestamp = ?,
                    closed = ?, 
                    exit_price = ?, 
                    exit_timestamp = ?,
                    pnl_amount = ?,
                    pnl_percentage = ?,
                    ladder_tp = ?, 
                    ladder_tp_pct = ?, 
                    ladder_tp_step = ?, 
                    ladder_closed_pct = ?,
                    direction = ?,
                    high_price = ?,
                    low_price = ?,
                    avg_price = ?,
                    pos_id = ?,
                    realized_pnl = ?,
                    unrealized_pnl = ?,
                    last_sync_time = ?,
                    margin = ?,
                    last_price = ?,
                    close_time = ?
                WHERE position_id = ?
                ''', (
                    position.symbol, 
                    position.entry_price, 
                    position.quantity, 
                    position.position_type,
                    position.leverage,
                    position.timestamp,
                    1 if position.closed else 0, 
                    position.exit_price, 
                    position.exit_timestamp,
                    position.pnl_amount,
                    position.pnl_percentage,
                    1 if position.ladder_tp else 0, 
                    position.ladder_tp_pct, 
                    position.ladder_tp_step, 
                    position.ladder_closed_pct,
                    position.direction,
                    position.high_price,
                    position.low_price,
                    position.avg_price,
                    position.pos_id,
                    position.realized_pnl,  # 确保这里是realized_pnl
                    position.unrealized_pnl,
                    position.last_sync_time,
                    position.margin,        # 确保这里是margin
                    position.last_price,
                    position.close_time,    # 添加close_time
                    position.position_id
                ))
            else:
                # 插入新记录
                cursor.execute('''
                INSERT INTO positions (
                    symbol, 
                    position_id, 
                    entry_price, 
                    quantity, 
                    position_type,
                    leverage,
                    timestamp,
                    closed, 
                    exit_price, 
                    exit_timestamp,
                    pnl_amount,
                    pnl_percentage,
                    ladder_tp, 
                    ladder_tp_pct, 
                    ladder_tp_step, 
                    ladder_closed_pct,
                    direction,
                    high_price,
                    low_price,
                    avg_price,
                    pos_id,
                    realized_pnl,
                    unrealized_pnl,
                    last_sync_time,
                    margin,
                    last_price,
                    close_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    position.symbol, 
                    position.position_id, 
                    position.entry_price, 
                    position.quantity, 
                    position.position_type,
                    position.leverage,
                    position.timestamp,
                    1 if position.closed else 0, 
                    position.exit_price, 
                    position.exit_timestamp,
                    position.pnl_amount,
                    position.pnl_percentage,
                    1 if position.ladder_tp else 0, 
                    position.ladder_tp_pct, 
                    position.ladder_tp_step, 
                    position.ladder_closed_pct,
                    position.direction,
                    position.high_price,
                    position.low_price,
                    position.avg_price,
                    position.pos_id,
                    position.realized_pnl,  # 确保这里是realized_pnl
                    position.unrealized_pnl,
                    position.last_sync_time,
                    position.margin,        # 确保这里是margin
                    position.last_price,
                    position.close_time     # 添加close_time
                ))
            
            self.conn.commit()
            # 保存成功后验证
            try:
                # 首先获取表结构，确认列顺序
                cursor.execute("PRAGMA table_info(positions)")
                columns_info = cursor.fetchall()
                column_names = [col[1] for col in columns_info]
                
                # 找到关键字段的索引
                realized_pnl_idx = column_names.index('realized_pnl') if 'realized_pnl' in column_names else None
                margin_idx = column_names.index('margin') if 'margin' in column_names else None
                
                # 使用列名直接查询特定字段
                cursor.execute("SELECT realized_pnl, margin FROM positions WHERE position_id = ?", (position.position_id,))
                row = cursor.fetchone()
                if row and len(row) >= 2:
                    db_realized_pnl, db_margin = row
                    
                    # 检查类型并尝试转换
                    try:
                        if isinstance(db_realized_pnl, str) and db_realized_pnl in ['long', 'short']:
                            self.logger.error(f"【严重错误】{position.symbol} 保存后，数据库中realized_pnl为方向值: {db_realized_pnl}")
                            # 直接更新数据库字段
                            cursor.execute("UPDATE positions SET realized_pnl = ? WHERE position_id = ?", 
                                          (position.realized_pnl, position.position_id))
                            self.conn.commit()
                            db_realized_pnl = position.realized_pnl
                        else:
                            db_realized_pnl = float(db_realized_pnl) if db_realized_pnl is not None else 0.0
                    except (ValueError, TypeError):
                        self.logger.error(f"【数据转换】{position.symbol} 无法转换数据库中realized_pnl: {db_realized_pnl}")
                        db_realized_pnl = 0.0
                    
                    try:
                        if isinstance(db_margin, str) and db_margin in ['long', 'short']:
                            self.logger.error(f"【严重错误】{position.symbol} 保存后，数据库中margin为方向值: {db_margin}")
                            # 直接更新数据库字段
                            cursor.execute("UPDATE positions SET margin = ? WHERE position_id = ?", 
                                          (position.margin, position.position_id))
                            self.conn.commit()
                            db_margin = position.margin
                        else:
                            db_margin = float(db_margin) if db_margin is not None else 0.0
                    except (ValueError, TypeError):
                        self.logger.error(f"【数据转换】{position.symbol} 无法转换数据库中margin: {db_margin}")
                        db_margin = 0.0
                    
                    # 比较值是否一致
                    if abs(float(db_realized_pnl) - float(position.realized_pnl)) > 0.0001:
                        self.logger.warning(f"【数据不一致】{position.symbol} 保存后realized_pnl不一致: 对象={position.realized_pnl}, 数据库={db_realized_pnl}")
                    if abs(float(db_margin) - float(position.margin)) > 0.0001:
                        self.logger.warning(f"【数据不一致】{position.symbol} 保存后margin不一致: 对象={position.margin}, 数据库={db_margin}")
                    
                    self.logger.info(f"【保存验证】{position.symbol} 字段: realized_pnl={db_realized_pnl}, margin={db_margin}")
            except Exception as e:
                self.logger.error(f"保存后验证失败: {e}")
        except Exception as e:
            self.logger.error(f"保存仓位失败: {str(e)}", exc_info=True)
            self.conn.rollback()
    
    def close_position(self, symbol: str, exit_price: float, exit_timestamp: int = None, pnl_amount: float = 0.0, pnl_percentage: float = 0.0, position_id: str = None):
        """
        标记仓位为已平仓，并记录平仓信息
        
        Args:
            symbol: 交易对
            exit_price: 平仓价格
            exit_timestamp: 平仓时间戳，如果不提供则使用当前时间
            pnl_amount: 盈亏金额（USDT）
            pnl_percentage: 盈亏百分比（0.05表示5%）
            position_id: 持仓ID，如果提供则使用这个ID查询，否则用symbol查询
        """
        try:
            if not self.conn:
                self._init_db()
            
            if not exit_timestamp:
                exit_timestamp = int(time.time() * 1000)
                
            # 记录本地平仓时间
            local_close_time = exit_timestamp
            
            with self.db_lock:
                # 根据不同情况构建查询条件
                if position_id:
                    cursor = self.conn.execute("SELECT * FROM positions WHERE position_id=? AND closed=0", (position_id,))
                else:
                    cursor = self.conn.execute("SELECT * FROM positions WHERE symbol=? AND closed=0", (symbol,))
                
                row = cursor.fetchone()
                if not row:
                    if position_id:
                        self.logger.warning(f"未找到待平仓的持仓记录: position_id={position_id}")
                    else:
                        self.logger.warning(f"未找到待平仓的持仓记录: symbol={symbol}")
                    return
                
                # 计算持仓时间
                entry_timestamp = row[6]  # timestamp字段的索引
                holding_time_ms = exit_timestamp - entry_timestamp
                
                # 将毫秒转换为分钟
                holding_time_minutes = int(holding_time_ms / (1000 * 60))
                
                # 格式化持仓时间
                if holding_time_minutes < 60:
                    holding_time = f"{holding_time_minutes}分钟"
                elif holding_time_minutes < 24 * 60:
                    hours = holding_time_minutes // 60
                    mins = holding_time_minutes % 60
                    holding_time = f"{hours}小时{mins}分钟"
                else:
                    days = holding_time_minutes // (24 * 60)
                    hours = (holding_time_minutes % (24 * 60)) // 60
                    holding_time = f"{days}天{hours}小时"
                
                # 打印详细日志
                self.logger.info(f"平仓信息: {symbol}, 入场价={row[2]}, 出场价={exit_price}, " +
                                f"盈亏金额={pnl_amount:.2f} USDT, 盈亏比例={pnl_percentage*100:.2f}%, " +
                                f"持仓时间={holding_time}, 本地平仓时间={local_close_time}")
                
                # 更新数据库结构以支持local_close_time字段
                try:
                    # 检查字段是否存在
                    cursor = self.conn.execute("SELECT close_time FROM positions LIMIT 1")
                except sqlite3.OperationalError:
                    # 字段不存在，添加字段
                    self.logger.info("添加local_close_time字段到positions表")
                    self.conn.execute("ALTER TABLE positions ADD COLUMN close_time INTEGER")
                
                # 更新仓位状态为已平仓，并记录本地平仓时间
                self.conn.execute(
                    "UPDATE positions SET closed=1, exit_price=?, exit_timestamp=?, pnl_amount=?, pnl_percentage=?, close_time=? WHERE position_id=?",
                    (exit_price, exit_timestamp, pnl_amount, pnl_percentage, local_close_time, row[1])
                )
                self.conn.commit()
                self.logger.info(f"仓位已标记为已平仓: {symbol}, position_id={row[1]}, close_time={local_close_time}")
                
                # 返回平仓的详细信息
                entry_price = row[2]
                quantity = row[3]
                leverage = row[5] if len(row) > 5 else 1
                direction = "long" if quantity > 0 else "short"
                
                return {
                    "symbol": symbol,
                    "position_id": row[1],
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "quantity": abs(quantity),
                    "direction": direction,
                    "leverage": leverage,
                    "entry_timestamp": entry_timestamp,
                    "exit_timestamp": exit_timestamp,
                    "pnl_amount": pnl_amount,
                    "pnl_percentage": pnl_percentage * 100,  # 转为百分比
                    "holding_time": holding_time,
                    "close_time": local_close_time
                }
                
        except Exception as e:
            self.logger.error(f"标记仓位平仓失败: {e}", exc_info=True)
            self.conn.rollback()
    
    def get_daily_pnl(self, start_date: str = None, end_date: str = None) -> List[Dict]:
        """
        获取每日收益统计
        
        Args:
            start_date: 开始日期，格式为 YYYY-MM-DD，默认为7天前
            end_date: 结束日期，格式为 YYYY-MM-DD，默认为今天
            
        Returns:
            List[Dict]: 每日收益统计列表
        """
        # 设置默认日期范围
        if not end_date:
            end_date = datetime.datetime.now().strftime("%Y-%m-%d")
        
        if not start_date:
            # 默认查询7天
            start_date = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        
        # 转换为时间戳
        start_ts = int(datetime.datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)
        # 结束日期加1天，以包含当天
        end_ts = int((datetime.datetime.strptime(end_date, "%Y-%m-%d") + datetime.timedelta(days=1)).timestamp() * 1000)
        
        with self.db_lock:
            # 查询在指定时间范围内平仓的仓位
            cursor = self.conn.execute(
                """SELECT 
                    date(exit_timestamp/1000, 'unixepoch', 'localtime') as exit_date,
                    SUM(pnl_amount) as daily_pnl,
                    COUNT(*) as position_count,
                    AVG(pnl_percentage) as avg_pnl_pct
                FROM positions 
                WHERE closed=1 AND exit_timestamp BETWEEN ? AND ?
                GROUP BY exit_date
                ORDER BY exit_date DESC""",
                (start_ts, end_ts)
            )
            
            results = []
            for row in cursor.fetchall():
                results.append({
                    "date": row[0],
                    "pnl": row[1],
                    "position_count": row[2],
                    "avg_pnl_percentage": row[3]
                })
            
            return results
    
    def get_position_history(self, start_date=None, end_date=None, symbol=None, limit=None):
        """
        获取已平仓的历史仓位
        
        Args:
            start_date (str, optional): 开始日期 "YYYY-MM-DD"
            end_date (str, optional): 结束日期 "YYYY-MM-DD"
            symbol (str, optional): 交易对
            limit (int, optional): 返回记录数量限制，如果不指定则不限制
            
        Returns:
            list: 历史仓位列表
        """
        try:
            # 简洁记录输入参数
            self.logger.info(f"历史仓位查询参数: start_date={start_date}, end_date={end_date}, symbol={symbol}, limit={limit}")
            
            # 转换日期为时间戳
            start_timestamp = None
            end_timestamp = None
            
            if start_date:
                try:
                    from datetime import datetime
                    # 转换为当天零点
                    dt = datetime.strptime(start_date, "%Y-%m-%d")
                    # 转为毫秒时间戳
                    start_timestamp = int(dt.timestamp() * 1000)
                except Exception as e:
                    self.logger.error(f"开始日期格式错误: {e}")
                    from datetime import datetime, timedelta
                    # 使用30天前作为默认开始时间
                    dt = datetime.now() - timedelta(days=30)
                    start_timestamp = int(dt.timestamp() * 1000)
            else:
                from datetime import datetime, timedelta
                # 使用30天前作为默认开始时间
                dt = datetime.now() - timedelta(days=30)
                start_timestamp = int(dt.timestamp() * 1000)
            
            if end_date:
                try:
                    from datetime import datetime, timedelta
                    # 转换为下一天零点（包含当天所有时间）
                    dt = datetime.strptime(end_date, "%Y-%m-%d")
                    next_day = dt + timedelta(days=1)
                    # 转为毫秒时间戳
                    end_timestamp = int(next_day.timestamp() * 1000)
                except Exception as e:
                    self.logger.error(f"结束日期格式错误: {e}")
                    from datetime import datetime
                    # 使用现在作为默认结束时间
                    dt = datetime.now()
                    end_timestamp = int(dt.timestamp() * 1000)
            else:
                from datetime import datetime
                # 使用现在作为默认结束时间
                dt = datetime.now()
                end_timestamp = int(dt.timestamp() * 1000)
            
            # 检查数据库是否初始化
            if self.conn is None:
                self._init_db()
            
            cursor = self.conn.cursor()
            
            # 构建SQL查询
            sql = "SELECT * FROM positions WHERE closed=1"
            params = []
            
            if start_timestamp is not None:
                sql += " AND exit_timestamp >= ?"
                params.append(start_timestamp)
            
            if end_timestamp is not None:
                sql += " AND exit_timestamp <= ?"
                params.append(end_timestamp)
            
            if symbol is not None and symbol != "":
                sql += " AND symbol = ?"
                params.append(symbol)
            
            # 按退出时间倒序排列
            sql += " ORDER BY exit_timestamp DESC"
            
            if limit is not None and limit > 0:
                sql += f" LIMIT {limit}"
            
            # 执行查询
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            
            # 获取列名
            column_names = [description[0] for description in cursor.description]
            
            # 转换为字典列表
            result = []
            from datetime import datetime
            for row in rows:
                record = dict(zip(column_names, row))
                # 检查和转换字段名称以适应API
                entry_time = record.get('entry_timestamp')
                if entry_time:
                    # 转换为可读的日期时间
                    dt_entry = datetime.fromtimestamp(entry_time / 1000)
                    record['entry_time'] = dt_entry.strftime("%Y-%m-%d %H:%M:%S")
                
                exit_time = record.get('exit_timestamp')
                if exit_time:
                    # 转换为可读的日期时间
                    dt_exit = datetime.fromtimestamp(exit_time / 1000)
                    record['exit_time'] = dt_exit.strftime("%Y-%m-%d %H:%M:%S")
                
                # 检查方向字段
                if 'direction' not in record:
                    record['direction'] = 'long'
                
                result.append(record)

            self.logger.info(f"历史仓位查询结果: 返回 {len(result)} 条记录")
            
            return result
        except Exception as e:
            self.logger.exception(f"获取历史仓位异常: {e}")
            return []
    
    def _calculate_holding_time(self, entry_ts: int, exit_ts: int) -> str:
        """计算持仓时间并格式化为易读形式"""
        holding_time_ms = exit_ts - entry_ts
        holding_time_hours = holding_time_ms / (1000 * 60 * 60)
        
        if holding_time_hours < 24:
            return f"{holding_time_hours:.2f}小时"
        else:
            holding_time_days = holding_time_hours / 24
            if holding_time_days < 30:
                return f"{holding_time_days:.2f}天 ({holding_time_hours:.1f}小时)"
            else:
                holding_time_months = holding_time_days / 30
                return f"{holding_time_months:.2f}月 ({holding_time_days:.1f}天)"
    
    def configure_risk_control(self, config: Dict[str, any]) -> None:
        """
        配置风险控制模块
        
        Args:
            config: 风控配置参数
        """
        if self.risk_controller:
            self.risk_controller.configure(config)
            self.logger.info("已更新风控配置")
    
    def reset_daily_risk_control(self) -> None:
        """重置每日风控计数器"""
        if self.risk_controller:
            self.risk_controller.reset_daily_counters()
            self.logger.info("已重置风控每日计数器")
    
    def update_risk_pnl(self, pnl_pct: float) -> None:
        """
        更新风控的当日盈亏百分比
        
        Args:
            pnl_pct: 盈亏百分比
        """
        if self.risk_controller:
            self.risk_controller.update_daily_pnl(pnl_pct)
    
    async def check_risk_control(self, symbol: str, signal_extra_data: Optional[Dict[str, any]] = None) -> Tuple[bool, str]:
        """
        检查风控条件
        
        Args:
            symbol: 交易标的
            signal_extra_data: 信号中的额外数据，包含风控参数
            
        Returns:
            Tuple[bool, str]: (是否允许, 原因)
        """
        if not self.risk_controller:
            return True, "未启用风控"
            
        allowed, reason = await self.risk_controller.check_risk_control(symbol, signal_extra_data)
        
        if allowed:
            self.logger.debug(f"{symbol} 风控检查通过: {reason}")
        else:
            self.logger.info(f"{symbol} 风控限制: {reason}")
            
        return allowed, reason
    
    def record_trade(self, symbol: str) -> None:
        """
        记录交易信息到风控
        
        Args:
            symbol: 交易标的
        """
        if self.risk_controller:
            self.risk_controller.record_trade(symbol)
    
    async def sync_position_from_api(self, symbol: str, data_cache=None) -> Tuple[bool, str]:
        """
        从API同步持仓数据
        
        Args:
            symbol: 交易对
            data_cache: 数据缓存对象，如果为None则使用实例的data_cache
            
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        try:
            # 使用传入的data_cache或实例中的data_cache
            if data_cache is None:
                if hasattr(self, 'data_cache') and self.data_cache is not None:
                    data_cache = self.data_cache
                else:
                    self.logger.error(f"无法同步持仓 {symbol}: 未提供数据缓存对象")
                    return False, "未提供数据缓存对象"
            
            # 检查本地是否有此持仓
            positions = self.load_positions(dict_format=True)
            if symbol not in positions:
                self.logger.warning(f"本地不存在 {symbol} 的持仓记录，无法同步")
                return False, "本地不存在此持仓记录"
            
            # 获取本地持仓
            position = positions[symbol]
            
            # 保存原始的已实现盈亏，用于后续比较和调试
            original_realized_pnl = 0.0
            if hasattr(position, 'realized_pnl'):
                # 确保不是误用方向值
                if position.realized_pnl not in ['long', 'short']:
                    try:
                        if isinstance(position.realized_pnl, str):
                            original_realized_pnl = float(position.realized_pnl)
                        else:
                            original_realized_pnl = position.realized_pnl
                    except (ValueError, TypeError):
                        self.logger.warning(f"{symbol} 已实现盈亏值异常，使用默认值0")
            self.logger.info(f"{symbol} 原始已实现盈亏: {original_realized_pnl}")
            # 从API获取持仓数据
            if not hasattr(data_cache, 'get_position_data'):
                self.logger.warning(f"数据缓存对象不支持持仓数据获取: {type(data_cache)}")
                return False, "数据缓存不支持持仓数据获取"
            
            pos_data = await data_cache.get_position_data(symbol, force_update=True)
            if not pos_data or not pos_data.get('data'):
                self.logger.warning(f"无法从API获取 {symbol} 的持仓数据，返回: {pos_data}")
                return False, "无法获取持仓数据"
            
            api_position = pos_data.get('data')
            self.logger.info(f"API返回的持仓数据: {api_position}")
            
            # 更新持仓信息 - 使用API返回的字段
            # avgPx: 开仓均价
            if 'avgPx' in api_position and api_position['avgPx']:
                try:
                    avg_price = float(api_position['avgPx'])
                    if avg_price > 0:
                        # 记录更新前的价格
                        old_avg_price = position.avg_price
                        old_entry_price = position.entry_price
                        
                        # 更新均价
                        position.avg_price = avg_price
                        
                        # 同时更新入场价（这是关键修改）
                        position.entry_price = avg_price
                        
                        self.logger.info(f"从API更新 {symbol} 价格信息 - 旧均价: {old_avg_price}, 新均价: {avg_price}, " +
                                       f"旧入场价: {old_entry_price}, 新入场价: {position.entry_price}")
                except (ValueError, TypeError) as e:
                    self.logger.warning(f"无法转换avgPx值 '{api_position['avgPx']}' 为浮点数: {e}")
            
            # posId: 持仓ID
            if 'posId' in api_position and api_position['posId']:
                position.pos_id = str(api_position['posId'])
                self.logger.info(f"从API更新 {symbol} 持仓ID: {position.pos_id}")
            
            # posSide: 持仓方向，确保方向正确
            if 'posSide' in api_position and api_position['posSide']:
                api_direction = api_position['posSide']  # API中的posSide就是方向: long或short
                # 如果API返回的方向与本地不同，需要更新
                if api_direction != position.direction and api_direction != "net":
                    self.logger.warning(f"{symbol} 持仓方向与API不一致: 本地={position.direction}, API={api_direction}")
                    # 更新方向
                    position.direction = api_direction
                    # 确保数量符号与方向一致
                    if position.direction == 'long' and position.quantity < 0:
                        position.quantity = abs(position.quantity)
                    elif position.direction == 'short' and position.quantity > 0:
                        position.quantity = -abs(position.quantity)
                    self.logger.info(f"从API更新 {symbol} 持仓方向: {position.direction}, 数量调整为: {position.quantity}")
            
            # availPos: 可平仓数量，如果与本地记录不同，则更新
            if 'availPos' in api_position and api_position['availPos']:
                try:
                    avail_pos = float(api_position['availPos'])
                    # 检查是否与当前记录的数量有差异
                    if abs(avail_pos) != abs(position.quantity) and avail_pos > 0:
                        self.logger.info(f"{symbol} 可平仓数量与本地记录不一致: API={avail_pos}, 本地={position.quantity}")
                        # 确保方向保持不变
                        direction = position.direction
                        # 更新数量，保持方向一致
                        position.quantity = avail_pos if direction == 'long' else -avail_pos
                except (ValueError, TypeError) as e:
                    self.logger.warning(f"无法转换availPos值 '{api_position['availPos']}' 为浮点数: {e}")
            
            # realizedPnl: 已实现收益
            if 'realizedPnl' in api_position and api_position['realizedPnl']:
                try:
                    # 确保转换为浮点数
                    realized_pnl = float(api_position['realizedPnl'])

                    
                    # 设置已实现盈亏值(只设置一次)
                    position.realized_pnl = realized_pnl
                    
                    # 如果已实现盈亏发生明显变化，记录日志
                    if abs(realized_pnl - original_realized_pnl) > 0.01:
                        self.logger.info(f"【重要】从API更新 {symbol} 已实现收益: {original_realized_pnl} -> {realized_pnl}, " +
                                       f"变化: {realized_pnl - original_realized_pnl:.4f}")
                    else:
                        self.logger.debug(f"从API更新 {symbol} 已实现收益: {realized_pnl}")
                    
                    # 将已实现盈亏也保存到extra_data中用于验证
                    if not hasattr(position, 'extra_data') or position.extra_data is None:
                        position.extra_data = {}
                    position.extra_data['realizedPnl'] = realized_pnl
                    position.extra_data['correct_realized_pnl'] = realized_pnl
                except (ValueError, TypeError) as e:
                    self.logger.warning(f"无法转换realizedPnl值 '{api_position['realizedPnl']}' 为浮点数: {e}")
            
            # 记录保证金计算前的已实现盈亏值，用于后续检查
            pre_margin_realized_pnl = getattr(position, 'realized_pnl', 0.0)
            
            # 计算并保存保证金值 (mm)，避免与已实现盈亏混淆
            if 'lever' in api_position and api_position['lever'] and 'notionalUsd' in api_position and api_position['notionalUsd']:
                try:
                    leverage = float(api_position['lever'])
                    notional_usd = float(api_position['notionalUsd'])
                    if leverage > 0:
                        margin = notional_usd / leverage
                        # 记录原始保证金值
                        old_margin = getattr(position, 'margin', 0.0)
                        # 只更新margin字段，不要影响realized_pnl
                        position.margin = margin
                        self.logger.info(f"从API计算 {symbol} 保证金: {margin:.4f} (原始值={old_margin:.4f}, 名义价值={notional_usd:.4f}, 杠杆={leverage})")

                except (ValueError, TypeError) as e:
                    self.logger.warning(f"无法计算保证金: {e}")
            
            # unrealizedPnl: 未实现收益
            if 'upl' in api_position and api_position['upl']:
                try:
                    unrealized_pnl = float(api_position['upl'])
                    position.unrealized_pnl = unrealized_pnl
                    self.logger.info(f"从API更新 {symbol} 未实现收益: {unrealized_pnl}")
                except (ValueError, TypeError) as e:
                    self.logger.warning(f"无法转换upl值 '{api_position['upl']}' 为浮点数: {e}")
            
            # uTime: 最近更新时间
            if 'uTime' in api_position and api_position['uTime']:
                try:
                    update_time = int(api_position['uTime'])
                    position.last_sync_time = update_time
                    dt_string = datetime.datetime.fromtimestamp(update_time/1000).strftime('%Y-%m-%d %H:%M:%S')
                    self.logger.info(f"从API更新 {symbol} 最近更新时间: {dt_string}")
                except (ValueError, TypeError) as e:
                    self.logger.warning(f"无法转换uTime值 '{api_position['uTime']}' 为整数: {e}")
            
            # 最终检查，确保字段类型和值合理
            if not isinstance(position.realized_pnl, float):
                try:
                    position.realized_pnl = float(position.realized_pnl)
                except (ValueError, TypeError):
                    position.realized_pnl = original_realized_pnl
                    self.logger.warning(f"最终检查: {symbol} 的已实现盈亏不是浮点数，恢复为原始值: {original_realized_pnl}")
            
            if not isinstance(position.margin, float):
                try:
                    position.margin = float(position.margin)
                except (ValueError, TypeError):
                    position.margin = 0.0
                    self.logger.warning(f"最终检查: {symbol} 的保证金不是浮点数，设置为默认值: 0.0")

                    
            # 保存更新后的持仓信息
            self.save_position(position)
            self.logger.info(f"{symbol} 从API同步持仓数据完成")
            

            
            return True, "持仓同步成功"
        except Exception as e:
            self.logger.error(f"从API同步 {symbol} 持仓数据异常: {e}", exc_info=True)
            return False, f"持仓同步异常: {str(e)}"

    def get_all_position_symbols(self) -> List[str]:
        """获取所有未平仓持仓的交易对"""
        with self.db_lock:
            cursor = self.conn.execute("SELECT symbol FROM positions WHERE closed=0")
            return [row[0] for row in cursor.fetchall()]
    
    def get_position_by_id(self, pos_id: str) -> Optional[Position]:
        """
        通过仓位ID获取仓位对象
        
        Args:
            pos_id: 仓位ID
            
        Returns:
            Position: 仓位对象，如果不存在则返回None
        """
        try:
            cursor = self.conn.cursor()
            
            # 首先获取表结构，确认列顺序
            cursor.execute("PRAGMA table_info(positions)")
            columns_info = cursor.fetchall()
            column_names = [col[1] for col in columns_info]
            
            # 查询对应pos_id的仓位，按时间戳降序排序，只返回最新的一条
            cursor.execute("SELECT * FROM positions WHERE pos_id = ? ORDER BY timestamp DESC LIMIT 1", (pos_id,))
            row = cursor.fetchone()
            
            if not row:
                # 尝试用position_id再查一次，同样按时间排序并只返回最新的
                cursor.execute("SELECT * FROM positions WHERE position_id = ? ORDER BY timestamp DESC LIMIT 1", (pos_id,))
                row = cursor.fetchone()
                
            if not row:
                self.logger.warning(f"未找到ID为 {pos_id} 的仓位")
                return None
                
            # 找到关键字段的索引
            realized_pnl_idx = column_names.index('realized_pnl') if 'realized_pnl' in column_names else None
            margin_idx = column_names.index('margin') if 'margin' in column_names else None
            direction_idx = column_names.index('direction') if 'direction' in column_names else None
            high_price_idx = column_names.index('high_price') if 'high_price' in column_names else None
            low_price_idx = column_names.index('low_price') if 'low_price' in column_names else None
            
            # 基本字段
            symbol = row[0]
            position_id = row[1]
            entry_price = row[2]
            quantity = row[3]
            position_type = row[4]
            leverage = row[5]
            timestamp = row[6]
            closed = bool(row[7])
            exit_price = row[8]
            exit_timestamp = row[9]
            pnl_amount = row[10]
            pnl_percentage = row[11]
            ladder_tp = bool(row[12])
            ladder_tp_pct = row[13]
            ladder_tp_step = row[14]
            ladder_closed_pct = row[15]
            
            # 使用索引获取方向，确保正确
            direction = row[direction_idx] if direction_idx is not None and direction_idx < len(row) else None
            if direction is None or direction not in ['long', 'short']:
                direction = "long" if quantity > 0 else "short"
            
            # 高低价
            high_price = row[high_price_idx] if high_price_idx is not None and high_price_idx < len(row) else 0.0
            low_price = row[low_price_idx] if low_price_idx is not None and low_price_idx < len(row) else float('inf')
            
            # 创建基本的Position对象
            position = Position(
                symbol=symbol,
                position_id=position_id,
                entry_price=entry_price,
                quantity=quantity,
                position_type=position_type,
                leverage=leverage,
                timestamp=timestamp,
                closed=closed,
                exit_price=exit_price,
                exit_timestamp=exit_timestamp,
                pnl_amount=pnl_amount,
                pnl_percentage=pnl_percentage,
                ladder_tp=ladder_tp,
                ladder_tp_pct=ladder_tp_pct,
                ladder_tp_step=ladder_tp_step,
                ladder_closed_pct=ladder_closed_pct,
                direction=direction,
                high_price=high_price,
                low_price=low_price
            )
            
            # 使用索引获取额外字段
            avg_price_idx = column_names.index('avg_price') if 'avg_price' in column_names else None
            pos_id_idx = column_names.index('pos_id') if 'pos_id' in column_names else None
            realized_pnl_idx = column_names.index('realized_pnl') if 'realized_pnl' in column_names else None
            unrealized_pnl_idx = column_names.index('unrealized_pnl') if 'unrealized_pnl' in column_names else None
            last_sync_time_idx = column_names.index('last_sync_time') if 'last_sync_time' in column_names else None
            margin_idx = column_names.index('margin') if 'margin' in column_names else None
            last_price_idx = column_names.index('last_price') if 'last_price' in column_names else None
            
            # 添加额外字段
            if avg_price_idx is not None and avg_price_idx < len(row):
                position.avg_price = row[avg_price_idx] if row[avg_price_idx] is not None else 0.0
            
            if pos_id_idx is not None and pos_id_idx < len(row):
                position.pos_id = row[pos_id_idx] if row[pos_id_idx] is not None else ""
            
            if realized_pnl_idx is not None and realized_pnl_idx < len(row):
                # 确保已实现盈亏是数值类型
                raw_realized_pnl = row[realized_pnl_idx]
                if raw_realized_pnl in ['long', 'short']:
                    self.logger.warning(f"数据库中 {symbol} 的已实现盈亏字段包含方向值 '{raw_realized_pnl}'，设置为默认值0.0")
                    position.realized_pnl = 0.0
                else:
                    try:
                        position.realized_pnl = float(raw_realized_pnl) if raw_realized_pnl is not None else 0.0
                    except (ValueError, TypeError):
                        self.logger.warning(f"无法将 {symbol} 的已实现盈亏 '{raw_realized_pnl}' 转换为浮点数，设置为0.0")
                        position.realized_pnl = 0.0
            
            if unrealized_pnl_idx is not None and unrealized_pnl_idx < len(row):
                position.unrealized_pnl = float(row[unrealized_pnl_idx]) if row[unrealized_pnl_idx] is not None else 0.0
            
            if last_sync_time_idx is not None and last_sync_time_idx < len(row):
                position.last_sync_time = row[last_sync_time_idx] if row[last_sync_time_idx] is not None else 0
            
            if margin_idx is not None and margin_idx < len(row):
                # 确保保证金是数值类型
                raw_margin = row[margin_idx]
                if raw_margin in ['long', 'short']:
                    self.logger.warning(f"数据库中 {symbol} 的保证金字段包含方向值 '{raw_margin}'，设置为默认值0.0")
                    position.margin = 0.0
                else:
                    try:
                        position.margin = float(raw_margin) if raw_margin is not None else 0.0
                    except (ValueError, TypeError):
                        self.logger.warning(f"无法将 {symbol} 的保证金 '{raw_margin}' 转换为浮点数，设置为0.0")
                        position.margin = 0.0
            
            if last_price_idx is not None and last_price_idx < len(row):
                position.last_price = float(row[last_price_idx]) if row[last_price_idx] is not None else 0.0
            
            # 添加close_time字段
            close_time_idx = column_names.index('close_time') if 'close_time' in column_names else None
            if close_time_idx is not None and close_time_idx < len(row):
                position.close_time = row[close_time_idx] if row[close_time_idx] is not None else 0
            
            # 日志输出实际读取的值
            self.logger.debug(f"加载 {symbol} 持仓: realized_pnl={position.realized_pnl}, margin={position.margin}, direction={position.direction}")
            
            self.logger.info(f"根据ID {pos_id} 获取到仓位: {position.symbol}, 方向={position.direction}, 入场价={position.entry_price}")
            return position
            
        except Exception as e:
            self.logger.error(f"根据ID获取仓位失败: {str(e)}", exc_info=True)
            return None
    
    async def sync_positions_from_api(self) -> None:
        """
        从API同步所有持仓数据
        
        Returns:
            bool: 是否同步成功
        """
        self.logger.info("开始同步持仓")
        
        try:
            # 确保trader已经初始化
            if not hasattr(self, 'trader') or self.trader is None:
                self.logger.error("无法同步：trader未初始化")
                return False
                
            # 获取所有持仓符号
            symbols = self.get_all_position_symbols()
            
            if not symbols:
                self.logger.info("无需同步的持仓")
                return True
                
            self.logger.info(f"需同步持仓: count={len(symbols)}")
            
            # 逐个同步持仓
            success_count = 0
            for symbol in symbols:
                try:
                    success, _ = await self.sync_position_from_api(symbol)
                    if success:
                        success_count += 1
                except Exception as e:
                    self.logger.error(f"同步持仓 {symbol} 失败: {str(e)}")
                    
            self.logger.info(f"同步完成, 成功率: {success_count}/{len(symbols)}")
            
            # 只要有一个成功，就算整体同步成功
            return success_count > 0
        except Exception as e:
            self.logger.error(f"同步持仓异常: {str(e)}")
            return False

    
