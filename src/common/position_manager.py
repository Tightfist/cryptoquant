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

class PositionManager:
    def __init__(self, app_name: str, logger=None):
        """
        初始化仓位管理器
        
        Args:
            app_name: 应用名称
            logger: 日志记录器，如果不提供则使用应用名称创建一个
        """
        self.app_name = app_name
        self.db_path = os.path.join("databases", f"{app_name}.db")
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.db_lock = Lock()
        self.logger = logger or logging.getLogger(f"{app_name}.position")
        
        # 初始化风控器
        self.risk_controller = RiskController(self.logger)
        
        self._init_db()
    
    def _init_db(self):
        """初始化数据库表结构"""
        with self.db_lock:
            # 检查表是否已存在
            cursor = self.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='positions'")
            table_exists = cursor.fetchone() is not None
            
            if not table_exists:
                # 如果表不存在，创建新表结构
                self.conn.execute('''CREATE TABLE positions
                    (symbol TEXT,
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
                     pnl_percentage REAL)''')
                
                # 创建索引
                self.conn.execute('''CREATE INDEX idx_positions_symbol ON positions(symbol)''')
                self.conn.execute('''CREATE INDEX idx_positions_closed ON positions(closed)''')
                self.conn.execute('''CREATE INDEX idx_positions_timestamp ON positions(timestamp)''')
                self.conn.execute('''CREATE INDEX idx_positions_exit_timestamp ON positions(exit_timestamp)''')
            else:
                # 表已存在，检查并升级表结构
                try:
                    # 检查主键是否是symbol
                    cursor = self.conn.execute('PRAGMA table_info(positions)')
                    columns = cursor.fetchall()
                    primary_key_column = None
                    for col in columns:
                        if col[5] == 1:  # 第6列表示是否为主键
                            primary_key_column = col[1]  # 第2列是列名
                            break
                    
                    # 如果主键是symbol，则需要重建表
                    if primary_key_column == 'symbol':
                        self.logger.info("检测到数据库主键需要从symbol改为position_id，开始迁移数据...")
                        
                        # 备份旧表
                        self.conn.execute('ALTER TABLE positions RENAME TO positions_old')
                        
                        # 创建新表
                        self.conn.execute('''CREATE TABLE positions
                            (symbol TEXT,
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
                             pnl_percentage REAL)''')
                        
                        # 复制数据
                        self.conn.execute('''INSERT INTO positions 
                            SELECT * FROM positions_old''')
                        
                        # 创建索引
                        self.conn.execute('''CREATE INDEX idx_positions_symbol ON positions(symbol)''')
                        self.conn.execute('''CREATE INDEX idx_positions_closed ON positions(closed)''')
                        self.conn.execute('''CREATE INDEX idx_positions_timestamp ON positions(timestamp)''')
                        self.conn.execute('''CREATE INDEX idx_positions_exit_timestamp ON positions(exit_timestamp)''')
                        
                        # 删除旧表
                        self.conn.execute('DROP TABLE positions_old')
                        
                        self.logger.info("数据库迁移完成")
                    
                    # 检查是否有新增的列
                    cursor = self.conn.execute('PRAGMA table_info(positions)')
                    columns = [row[1] for row in cursor.fetchall()]
                    
                    # 添加缺失的列
                    if 'exit_timestamp' not in columns:
                        self.conn.execute('ALTER TABLE positions ADD COLUMN exit_timestamp INTEGER DEFAULT 0')
                        print("已添加exit_timestamp列")
                    
                    if 'pnl_amount' not in columns:
                        self.conn.execute('ALTER TABLE positions ADD COLUMN pnl_amount REAL DEFAULT 0.0')
                        print("已添加pnl_amount列")
                    
                    if 'pnl_percentage' not in columns:
                        self.conn.execute('ALTER TABLE positions ADD COLUMN pnl_percentage REAL DEFAULT 0.0')
                        print("已添加pnl_percentage列")
                    
                    # 尝试创建索引（如果不存在）
                    try:
                        self.conn.execute('''CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol)''')
                        self.conn.execute('''CREATE INDEX IF NOT EXISTS idx_positions_closed ON positions(closed)''')
                        self.conn.execute('''CREATE INDEX IF NOT EXISTS idx_positions_timestamp ON positions(timestamp)''')
                        self.conn.execute('''CREATE INDEX IF NOT EXISTS idx_positions_exit_timestamp ON positions(exit_timestamp)''')
                    except Exception as e:
                        print(f"创建索引时出错: {e}")
                    
                    self.conn.commit()
                except Exception as e:
                    print(f"升级数据库结构时出错: {e}")
                    # 如果升级失败，可以考虑备份旧表并创建新表
                    # 但这里我们只是报告错误，不执行破坏性操作

    def load_positions(self) -> Dict[str, Position]:
        """从数据库加载所有未平仓仓位"""
        positions = {}
        with self.db_lock:
            cursor = self.conn.execute(
                "SELECT * FROM positions WHERE closed=0")
            for row in cursor.fetchall():
                pos = Position(
                    symbol=row[0],
                    position_id=row[1],
                    entry_price=row[2],
                    quantity=row[3],
                    position_type=row[4],
                    leverage=row[5],
                    timestamp=row[6],
                    closed=bool(row[7]),
                    exit_price=row[8],
                    exit_timestamp=row[9],
                    pnl_amount=row[10],
                    pnl_percentage=row[11]
                )
                positions[pos.symbol] = pos
        return positions
    
    def save_position(self, position: Position):
        """保存或更新仓位"""
        with self.db_lock:
            self.conn.execute('''REPLACE INTO positions VALUES 
                (?,?,?,?,?,?,?,?,?,?,?,?)''', 
                (position.symbol, position.position_id, position.entry_price,
                 position.quantity, position.position_type, position.leverage,
                 position.timestamp, int(position.closed), position.exit_price,
                 position.exit_timestamp, position.pnl_amount, position.pnl_percentage))
            self.conn.commit()
    
    def close_position(self, symbol: str, exit_price: float, exit_timestamp: int = None, pnl_amount: float = 0.0, pnl_percentage: float = 0.0, position_id: str = None):
        """
        标记仓位为已平仓，并记录平仓信息
        
        Args:
            symbol: 交易对
            exit_price: 平仓价格
            exit_timestamp: 平仓时间戳，如果不提供则使用当前时间
            pnl_amount: 盈亏金额
            pnl_percentage: 盈亏百分比
            position_id: 仓位ID，如果提供则根据position_id关闭仓位，否则根据symbol关闭所有未平仓的仓位
        """
        if exit_timestamp is None:
            exit_timestamp = int(time.time() * 1000)
            
        with self.db_lock:
            if position_id:
                # 根据position_id关闭特定仓位
                self.logger.info(f"根据position_id关闭仓位: {position_id}, 价格: {exit_price}, 盈亏: {pnl_amount}")
                self.conn.execute(
                    "UPDATE positions SET closed=1, exit_price=?, exit_timestamp=?, pnl_amount=?, pnl_percentage=? WHERE position_id=? AND closed=0",
                    (exit_price, exit_timestamp, pnl_amount, pnl_percentage, position_id))
            else:
                # 根据symbol关闭所有未平仓的仓位
                self.logger.info(f"根据symbol关闭仓位: {symbol}, 价格: {exit_price}, 盈亏: {pnl_amount}")
                self.conn.execute(
                    "UPDATE positions SET closed=1, exit_price=?, exit_timestamp=?, pnl_amount=?, pnl_percentage=? WHERE symbol=? AND closed=0",
                    (exit_price, exit_timestamp, pnl_amount, pnl_percentage, symbol))
            
            self.conn.commit()
    
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
    
    def get_position_history(self, start_date: str = None, end_date: str = None, 
                            symbol: str = None, limit: int = 100) -> List[Dict]:
        """
        获取历史仓位记录
        
        Args:
            start_date: 开始日期，格式为 YYYY-MM-DD，默认为30天前
            end_date: 结束日期，格式为 YYYY-MM-DD，默认为今天
            symbol: 交易对，默认为所有
            limit: 最大返回记录数，默认100条
            
        Returns:
            List[Dict]: 历史仓位记录列表
        """
        # 设置默认日期范围
        if not end_date:
            end_date = datetime.datetime.now().strftime("%Y-%m-%d")
        
        if not start_date:
            # 默认查询30天
            start_date = (datetime.datetime.now() - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
        
        # 转换为时间戳
        start_ts = int(datetime.datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)
        # 结束日期加1天，以包含当天
        end_ts = int((datetime.datetime.strptime(end_date, "%Y-%m-%d") + datetime.timedelta(days=1)).timestamp() * 1000)
        
        with self.db_lock:
            query = """SELECT 
                symbol, position_id, entry_price, quantity, position_type, leverage,
                timestamp, exit_price, exit_timestamp, pnl_amount, pnl_percentage,
                datetime(timestamp/1000, 'unixepoch', 'localtime') as entry_time,
                datetime(exit_timestamp/1000, 'unixepoch', 'localtime') as exit_time
            FROM positions 
            WHERE closed=1 AND exit_timestamp BETWEEN ? AND ?"""
            
            params = [start_ts, end_ts]
            
            if symbol:
                query += " AND symbol=?"
                params.append(symbol)
                
            query += " ORDER BY exit_timestamp DESC LIMIT ?"
            params.append(limit)
            
            cursor = self.conn.execute(query, params)
            
            results = []
            for row in cursor.fetchall():
                direction = "long" if row[3] > 0 else "short"
                results.append({
                    "symbol": row[0],
                    "position_id": row[1],
                    "entry_price": row[2],
                    "quantity": abs(row[3]),
                    "direction": direction,
                    "position_type": row[4],
                    "leverage": row[5],
                    "entry_timestamp": row[6],
                    "exit_price": row[7],
                    "exit_timestamp": row[8],
                    "pnl_amount": row[9],
                    "pnl_percentage": row[10] * 100,  # 转换为百分比
                    "entry_time": row[11],
                    "exit_time": row[12],
                    "holding_time": self._calculate_holding_time(row[6], row[8])
                })
            
            return results
    
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
    
    def check_risk_control(self, symbol: str, signal_extra_data: Optional[Dict[str, any]] = None) -> Tuple[bool, str]:
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
            
        allowed, reason = self.risk_controller.check_risk_control(symbol, signal_extra_data)
        
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
    
