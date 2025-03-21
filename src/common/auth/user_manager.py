"""
用户管理模块
提供用户创建、验证和管理功能
"""

import os
import sqlite3
import hashlib
import logging
import time
from typing import Dict, List, Optional, Any, Union
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64

from utils.utils import get_project_root


class UserManager:
    """用户管理类"""

    def __init__(self, db_path: str = None):
        """
        初始化用户管理器
        
        Args:
            db_path: 数据库文件路径，默认为databases目录下的users.db
        """
        self.logger = logging.getLogger("common.auth.user_manager")
        
        # 设置数据库路径
        if db_path is None:
            db_dir = os.path.join(get_project_root(), "databases")
            self.db_path = os.path.join(db_dir, "users.db")
        else:
            self.db_path = db_path
            
        # 确保数据库目录存在
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        # 初始化数据库
        self._init_db()

        # 初始化加密密钥
        self._init_encryption()

    def _init_encryption(self):
        """初始化加密密钥"""
        # 从环境变量获取密钥，如果没有则生成新的
        key = os.environ.get("USER_ENCRYPTION_KEY")
        if key:
            try:
                self.fernet = Fernet(key)
            except Exception as e:
                self.logger.error(f"初始化Fernet加密失败: {e}")
                raise
        else:
            # 生成新的密钥
            key = Fernet.generate_key()
            self.fernet = Fernet(key)
            self.logger.warning("使用新生成的加密密钥，建议设置环境变量USER_ENCRYPTION_KEY")
    def _init_db(self):
        """初始化数据库"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # 创建用户表
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    email TEXT,
                    role TEXT NOT NULL DEFAULT 'user',
                    created_at INTEGER NOT NULL,
                    last_login INTEGER,
                    UNIQUE(username)
                )
                ''')
                
                # 创建用户设置表
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_settings (
                    username TEXT PRIMARY KEY,
                    settings TEXT NOT NULL,
                    FOREIGN KEY (username) REFERENCES users(username)
                )
                ''')
                
                conn.commit()
                
        except Exception as e:
            self.logger.error(f"初始化数据库失败: {e}")
            raise
    
    def _hash_password(self, password: str, salt: str = None) -> tuple:
        """
        对密码进行哈希处理
        
        Args:
            password: 原始密码
            salt: 盐值，如果为None则生成新的盐值
            
        Returns:
            tuple: (哈希后的密码, 盐值)
        """
        if salt is None:
            salt = os.urandom(16).hex()
        
        # 使用PBKDF2算法进行密码哈希
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt.encode('utf-8'),
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode('utf-8')))
        return key.decode('utf-8'), salt
    
    def _encrypt_data(self, data: str) -> str:
        """
        加密数据
        
        Args:
            data: 要加密的数据
            
        Returns:
            str: 加密后的数据
        """
        return self.fernet.encrypt(data.encode()).decode()
    
    def _decrypt_data(self, encrypted_data: str) -> str:
        """
        解密数据
        
        Args:
            encrypted_data: 加密的数据
            
        Returns:
            str: 解密后的数据
        """
        return self.fernet.decrypt(encrypted_data.encode()).decode()
    
    def create_user(self, username: str, password: str, email: str = None, role: str = "user") -> bool:
        """
        创建新用户
        
        Args:
            username: 用户名
            password: 密码
            email: 电子邮件
            role: 用户角色，默认为"user"
            
        Returns:
            bool: 是否创建成功
        """
        try:
            # 检查用户名是否已存在
            if self.user_exists(username):
                self.logger.warning(f"用户名已存在: {username}")
                return False
            
            # 哈希密码
            hashed_password, salt = self._hash_password(password)
            
            # 加密敏感数据
            encrypted_email = self._encrypt_data(email) if email else None
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # 插入用户数据
                cursor.execute('''
                INSERT INTO users (username, password_hash, salt, email, role, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ''', (username, hashed_password, salt, encrypted_email, role, int(time.time())))
                
                # 创建用户设置
                cursor.execute('''
                INSERT INTO user_settings (username, settings)
                VALUES (?, ?)
                ''', (username, '{}'))
                
                conn.commit()
                return True
                
        except Exception as e:
            self.logger.error(f"创建用户失败: {e}")
            return False
    
    def verify_user(self, username: str, password: str) -> bool:
        """
        验证用户凭据
        
        Args:
            username: 用户名
            password: 密码
            
        Returns:
            bool: 验证是否成功
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # 获取用户数据
                cursor.execute('''
                SELECT password_hash, salt FROM users WHERE username = ?
                ''', (username,))
                
                result = cursor.fetchone()
                if not result:
                    self.logger.warning(f"用户不存在: {username}")
                    return False
                
                stored_hash, salt = result
                
                # 验证密码
                hashed_password, _ = self._hash_password(password, salt)
                
                if hashed_password == stored_hash:
                    # 更新最后登录时间
                    cursor.execute('''
                    UPDATE users SET last_login = ? WHERE username = ?
                    ''', (int(time.time()), username))
                    conn.commit()
                    return True
                else:
                    self.logger.warning(f"密码验证失败: {username}")
                    return False
                    
        except Exception as e:
            self.logger.error(f"验证用户失败: {e}")
            return False
    
    def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        """
        获取用户信息
        
        Args:
            username: 用户名
            
        Returns:
            Optional[Dict[str, Any]]: 用户信息
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # 获取用户数据
                cursor.execute('''
                SELECT username, email, role, created_at, last_login
                FROM users WHERE username = ?
                ''', (username,))
                
                result = cursor.fetchone()
                if not result:
                    return None
                
                # 解密敏感数据
                username, encrypted_email, role, created_at, last_login = result
                email = self._decrypt_data(encrypted_email) if encrypted_email else None
                
                return {
                    "username": username,
                    "email": email,
                    "role": role,
                    "created_at": created_at,
                    "last_login": last_login
                }
                
        except Exception as e:
            self.logger.error(f"获取用户信息失败: {e}")
            return None
    
    def update_user(self, username: str, data: Dict[str, Any]) -> bool:
        """
        更新用户信息
        
        Args:
            username: 用户名
            data: 要更新的数据
            
        Returns:
            bool: 是否更新成功
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # 检查用户是否存在
                if not self.user_exists(username):
                    self.logger.warning(f"用户不存在: {username}")
                    return False
                
                # 构建更新语句
                update_fields = []
                update_values = []
                
                if "email" in data:
                    update_fields.append("email = ?")
                    update_values.append(self._encrypt_data(data["email"]))
                
                if "role" in data:
                    update_fields.append("role = ?")
                    update_values.append(data["role"])
                
                if update_fields:
                    update_values.append(username)
                    cursor.execute(f'''
                    UPDATE users SET {", ".join(update_fields)}
                    WHERE username = ?
                    ''', update_values)
                    
                    conn.commit()
                return True
                
        except Exception as e:
            self.logger.error(f"更新用户信息失败: {e}")
            return False
    
    def change_password(self, username: str, old_password: str, new_password: str) -> bool:
        """
        修改用户密码
        
        Args:
            username: 用户名
            old_password: 旧密码
            new_password: 新密码
            
        Returns:
            bool: 是否修改成功
        """
        # 验证旧密码
        if not self.verify_user(username, old_password):
            self.logger.warning(f"旧密码验证失败: {username}")
            return False
        
        try:
            # 哈希新密码
            hashed_password, salt = self._hash_password(new_password)
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # 更新密码
                cursor.execute('''
                UPDATE users SET password_hash = ?, salt = ?
                WHERE username = ?
                ''', (hashed_password, salt, username))
                
                conn.commit()
                return True
                
        except Exception as e:
            self.logger.error(f"修改密码失败: {e}")
            return False
    
    def delete_user(self, username: str) -> bool:
        """
        删除用户
        
        Args:
            username: 用户名
            
        Returns:
            bool: 是否删除成功
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # 删除用户设置
                cursor.execute('DELETE FROM user_settings WHERE username = ?', (username,))
                
                # 删除用户
                cursor.execute('DELETE FROM users WHERE username = ?', (username,))
                
                conn.commit()
                return True
                
        except Exception as e:
            self.logger.error(f"删除用户失败: {e}")
            return False
    
    def get_all_users(self) -> List[Dict[str, Any]]:
        """
        获取所有用户信息
        
        Returns:
            List[Dict[str, Any]]: 用户信息列表
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # 获取所有用户数据
                cursor.execute('''
                SELECT username, email, role, created_at, last_login
                FROM users
                ''')
                
                users = []
                for row in cursor.fetchall():
                    username, encrypted_email, role, created_at, last_login = row
                    email = self._decrypt_data(encrypted_email) if encrypted_email else None
                    
                    users.append({
                        "username": username,
                        "email": email,
                        "role": role,
                        "created_at": created_at,
                        "last_login": last_login
                    })
                
                return users
                
        except Exception as e:
            self.logger.error(f"获取所有用户信息失败: {e}")
            return []
    
    def has_users(self) -> bool:
        """
        检查数据库中是否已有用户
        
        Returns:
            bool: 是否存在用户
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # 查询用户数量
                cursor.execute('''
                SELECT COUNT(*) FROM users
                ''')
                
                count = cursor.fetchone()[0]
                return count > 0
                
        except Exception as e:
            self.logger.error(f"检查是否有用户失败: {e}")
            return False
    
    def user_exists(self, username: str) -> bool:
        """
        检查用户是否存在
        
        Args:
            username: 用户名
            
        Returns:
            bool: 用户是否存在
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # 查询用户
                cursor.execute('''
                SELECT 1 FROM users WHERE username = ? LIMIT 1
                ''', (username,))
                
                return cursor.fetchone() is not None
                
        except Exception as e:
            self.logger.error(f"检查用户是否存在失败: {e}")
            return False 