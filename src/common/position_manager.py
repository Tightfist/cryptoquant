import sqlite3
import os
from dataclasses import dataclass
from typing import Dict, List
from threading import Lock

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
            self.conn.execute('''CREATE TABLE IF NOT EXISTS positions
                (symbol TEXT PRIMARY KEY,
                 position_id TEXT,
                 entry_price REAL,
                 quantity REAL,
                 position_type TEXT,
                 leverage INTEGER,
                 timestamp INTEGER,
                 closed INTEGER,
                 exit_price REAL)''')

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
                    exit_price=row[8]
                )
                positions[pos.symbol] = pos
        return positions
    
    def save_position(self, position: Position):
        """保存或更新仓位"""
        with self.db_lock:
            self.conn.execute('''REPLACE INTO positions VALUES 
                (?,?,?,?,?,?,?,?,?)''', 
                (position.symbol, position.position_id, position.entry_price,
                 position.quantity, position.position_type, position.leverage,
                 position.timestamp, int(position.closed), position.exit_price))
            self.conn.commit()
    
    def close_position(self, symbol: str, exit_price: float):
        """标记仓位为已平仓"""
        with self.db_lock:
            self.conn.execute(
                "UPDATE positions SET closed=1, exit_price=? WHERE symbol=?",
                (exit_price, symbol))
            self.conn.commit()
    
