"""
环境变量加载工具
提供环境变量的加载和管理功能
"""

import os
import logging
from typing import Optional, Any
from pathlib import Path
from dotenv import load_dotenv
from .utils import get_project_root

def load_env(env_file: str = ".env") -> None:
    """
    加载环境变量文件
    
    Args:
        env_file: 环境变量文件路径，默认为.env
    """
    try:
        # 获取项目根目录
        project_root = get_project_root()
        
        # 构建环境变量文件的完整路径
        env_path = os.path.join(project_root, env_file)
        
        # 检查文件是否存在
        if not os.path.exists(env_path):
            logging.warning(f"环境变量文件不存在: {env_path}")
            return
            
        # 加载环境变量
        load_dotenv(env_path)
        logging.info(f"成功加载环境变量文件: {env_path}")
        
    except Exception as e:
        logging.error(f"加载环境变量文件失败: {e}")
        raise

def get_env(key: str, default: Any = None) -> Optional[str]:
    """
    获取环境变量值
    
    Args:
        key: 环境变量名
        default: 默认值
        
    Returns:
        Optional[str]: 环境变量值，如果不存在则返回默认值
    """
    return os.getenv(key, default)

def get_required_env(key: str) -> str:
    """
    获取必需的环境变量值
    
    Args:
        key: 环境变量名
        
    Returns:
        str: 环境变量值
        
    Raises:
        ValueError: 如果环境变量不存在
    """
    value = os.getenv(key)
    if value is None:
        raise ValueError(f"必需的环境变量未设置: {key}")
    return value

def get_bool_env(key: str, default: bool = False) -> bool:
    """
    获取布尔类型的环境变量值
    
    Args:
        key: 环境变量名
        default: 默认值
        
    Returns:
        bool: 布尔值
    """
    value = os.getenv(key, str(default)).lower()
    return value in ('true', '1', 'yes', 'on')

def get_int_env(key: str, default: int = 0) -> int:
    """
    获取整数类型的环境变量值
    
    Args:
        key: 环境变量名
        default: 默认值
        
    Returns:
        int: 整数值
    """
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default

def get_float_env(key: str, default: float = 0.0) -> float:
    """
    获取浮点数类型的环境变量值
    
    Args:
        key: 环境变量名
        default: 默认值
        
    Returns:
        float: 浮点数值
    """
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default 