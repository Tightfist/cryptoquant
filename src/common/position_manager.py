import sqlite3
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from threading import Lock
import time
import datetime

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
    def __init__(self, app_name: str):
        self.app_name = app_name
        self.db_path = os.path.join("databases", f"{app_name}.db")
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.db_lock = Lock()
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
    
    def close_position(self, symbol: str, exit_price: float, exit_timestamp: int = None, pnl_amount: float = 0.0, pnl_percentage: float = 0.0):
        """标记仓位为已平仓，并记录平仓信息"""
        if exit_timestamp is None:
            exit_timestamp = int(time.time() * 1000)
            
        with self.db_lock:
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
    
