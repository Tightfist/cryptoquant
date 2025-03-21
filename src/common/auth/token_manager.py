"""
令牌管理模块
提供令牌生成、验证和管理功能
"""

import os
import time
import logging
import base64
import json
import hmac
import hashlib
from typing import Dict, Optional, Any, Union
from abc import ABC, abstractmethod


class TokenManager(ABC):
    """令牌管理器抽象基类"""
    
    @abstractmethod
    def generate_token(self, username: str, role: str, expiry: int = 86400, **extra) -> str:
        """
        生成认证令牌
        
        Args:
            username: 用户名
            role: 用户角色
            expiry: 有效期（秒），默认为1天
            **extra: 额外数据
            
        Returns:
            str: 认证令牌
        """
        pass
    
    @abstractmethod
    def verify_token(self, token: str) -> Dict[str, Any]:
        """
        验证令牌
        
        Args:
            token: 认证令牌
            
        Returns:
            Dict[str, Any]: 解码后的令牌载荷，验证失败则返回空字典
        """
        pass


class JwtTokenManager(TokenManager):
    """JWT令牌管理器"""
    
    def __init__(self, secret_key: str = None):
        """
        初始化JWT令牌管理器
        
        Args:
            secret_key: 密钥，如果为None则生成随机密钥
        """
        self.logger = logging.getLogger("common.auth.token_manager")
        
        # 设置密钥
        if secret_key is None:
            # 使用环境变量中的密钥或生成随机密钥
            key = os.environ.get("JWT_SECRET_KEY")
            if key is None:
                self.secret_key = os.urandom(32).hex()
                self.logger.warning("使用随机生成的JWT密钥，重启后令牌将失效，建议设置环境变量JWT_SECRET_KEY")
            else:
                self.secret_key = key
        else:
            self.secret_key = secret_key
    
    def _base64url_encode(self, data: Union[str, bytes]) -> str:
        """
        Base64URL编码
        
        Args:
            data: 要编码的数据
            
        Returns:
            str: 编码后的字符串
        """
        if isinstance(data, str):
            data = data.encode('utf-8')
        
        # Base64编码
        encoded = base64.urlsafe_b64encode(data).decode('utf-8')
        
        # 去除填充符
        return encoded.rstrip('=')
    
    def _base64url_decode(self, data: str) -> bytes:
        """
        Base64URL解码
        
        Args:
            data: 要解码的字符串
            
        Returns:
            bytes: 解码后的数据
        """
        # 补全填充符
        padding_needed = len(data) % 4
        if padding_needed:
            data += '=' * (4 - padding_needed)
        
        # Base64解码
        return base64.urlsafe_b64decode(data)
    
    def generate_token(self, username: str, role: str, expiry: int = 86400, **extra) -> str:
        """
        生成JWT令牌
        
        Args:
            username: 用户名
            role: 用户角色
            expiry: 有效期（秒），默认为1天
            **extra: 额外数据
            
        Returns:
            str: JWT令牌
        """
        # 创建头部
        header = {
            "alg": "HS256",
            "typ": "JWT"
        }
        
        # 当前时间
        now = int(time.time())
        
        # 创建载荷
        payload = {
            "sub": username,
            "role": role,
            "iat": now,
            "exp": now + expiry,
            **extra
        }
        
        # 编码头部和载荷
        header_encoded = self._base64url_encode(json.dumps(header))
        payload_encoded = self._base64url_encode(json.dumps(payload))
        
        # 创建签名
        message = f"{header_encoded}.{payload_encoded}"
        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).digest()
        signature_encoded = self._base64url_encode(signature)
        
        # 组合JWT令牌
        return f"{header_encoded}.{payload_encoded}.{signature_encoded}"
    
    def verify_token(self, token: str) -> Dict[str, Any]:
        """
        验证JWT令牌
        
        Args:
            token: JWT令牌
            
        Returns:
            Dict[str, Any]: 解码后的令牌载荷，验证失败则返回空字典
        """
        try:
            # 拆分令牌
            parts = token.split('.')
            if len(parts) != 3:
                self.logger.warning("无效的JWT格式")
                return {}
            
            header_encoded, payload_encoded, signature_encoded = parts
            
            # 验证签名
            message = f"{header_encoded}.{payload_encoded}"
            expected_signature = hmac.new(
                self.secret_key.encode('utf-8'),
                message.encode('utf-8'),
                hashlib.sha256
            ).digest()
            expected_signature_encoded = self._base64url_encode(expected_signature)
            
            if signature_encoded != expected_signature_encoded:
                self.logger.warning("JWT签名验证失败")
                return {}
            
            # 解码载荷
            payload = json.loads(self._base64url_decode(payload_encoded))
            
            # 验证过期时间
            now = int(time.time())
            if payload.get("exp", 0) < now:
                self.logger.warning("JWT已过期")
                return {}
            
            return payload
            
        except Exception as e:
            self.logger.error(f"验证JWT异常: {e}")
            return {} 