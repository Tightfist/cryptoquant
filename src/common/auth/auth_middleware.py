"""
身份验证中间件
提供Web请求的身份验证功能
"""

import logging
import functools
from typing import Callable, Dict, Any, Optional
from aiohttp import web
from .token_manager import TokenManager, JwtTokenManager


async def auth_middleware(app, handler):
    """
    身份验证中间件
    
    Args:
        app: Web应用
        handler: 请求处理器
        
    Returns:
        Callable: 中间件处理函数
    """
    async def middleware(request):
        # 获取令牌管理器
        token_manager = app.get('token_manager')
        if token_manager is None:
            # 如果应用中没有设置令牌管理器，则创建一个
            token_manager = JwtTokenManager()
            app['token_manager'] = token_manager
        
        # 提取令牌
        token = _extract_token(request)
        
        # 设置请求上下文
        request['user'] = None
        request['is_authenticated'] = False
        
        # 如果有令牌，则验证
        if token:
            payload = token_manager.verify_token(token)
            if payload:
                request['user'] = payload
                request['is_authenticated'] = True
        
        return await handler(request)
    
    return middleware


def login_required(func):
    """
    需要登录的装饰器
    
    Args:
        func: 要装饰的函数
        
    Returns:
        Callable: 装饰后的函数
    """
    @functools.wraps(func)
    async def wrapper(request):
        if not request.get('is_authenticated', False):
            return web.json_response(
                {"success": False, "message": "需要登录"},
                status=401
            )
        return await func(request)
    
    return wrapper


def optional_auth(func):
    """
    可选认证装饰器
    
    Args:
        func: 要装饰的函数
        
    Returns:
        Callable: 装饰后的函数
    """
    @functools.wraps(func)
    async def wrapper(request):
        # 不强制要求认证，但会设置用户信息
        return await func(request)
    
    return wrapper


def role_required(role: str):
    """
    需要特定角色的装饰器
    
    Args:
        role: 所需角色
        
    Returns:
        Callable: 装饰器函数
    """
    def decorator(func):
        @functools.wraps(func)
        @login_required
        async def wrapper(request):
            user = request.get('user', {})
            if user.get('role') != role:
                return web.json_response(
                    {"success": False, "message": f"需要 {role} 角色"},
                    status=403
                )
            return await func(request)
        
        return wrapper
    
    return decorator


def _extract_token(request) -> Optional[str]:
    """
    从请求中提取认证令牌
    
    Args:
        request: HTTP请求
        
    Returns:
        Optional[str]: 认证令牌
    """
    # 从Authorization头提取
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        return auth_header[7:]  # 跳过"Bearer "前缀
    
    # 从Cookie提取
    return request.cookies.get('auth_token') 