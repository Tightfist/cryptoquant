"""
认证API处理器
提供用户注册、登录等API接口
"""

import logging
import json
import os
from typing import Dict, Any, Optional
from aiohttp import web
from .user_manager import UserManager
from .token_manager import TokenManager, JwtTokenManager
from .auth_middleware import login_required


class AuthApiHandler:
    """认证API处理器"""
    
    def __init__(self, user_manager: UserManager = None, token_manager: TokenManager = None):
        """
        初始化认证API处理器
        
        Args:
            user_manager: 用户管理器
            token_manager: 令牌管理器
        """
        self.logger = logging.getLogger("common.auth.auth_api")
        # 设置用户管理器
        self.user_manager = user_manager or UserManager()
        # 设置令牌管理器
        self.token_manager = token_manager or JwtTokenManager()
        
        # 读取邀请码配置
        self.invitation_code = os.environ.get("INVITATION_CODE", "")
        self.require_invitation_code = os.environ.get("REQUIRE_INVITATION_CODE", "false").lower() == "true"
        
    async def handle_register(self, request: web.Request) -> web.Response:
        """
        处理注册请求
        
        Args:
            request: Web请求
            
        Returns:
            web.Response: Web响应
        """
        try:
            # 获取请求数据
            data = await request.json()
            
            # 验证请求数据
            username = data.get("username")
            password = data.get("password")
            email = data.get("email")
            invitation_code = data.get("invitation_code", "")
            
            if not username or not password:
                return web.json_response(
                    {"success": False, "message": "缺少用户名或密码"},
                    status=400
                )
            
            # 验证邀请码
            if self.require_invitation_code and not self.user_manager.has_users():
                # 第一个用户不需要邀请码
                pass
            elif self.require_invitation_code and invitation_code != self.invitation_code:
                return web.json_response(
                    {"success": False, "message": "邀请码无效"},
                    status=400
                )
            
            # 检查用户名是否已存在
            if self.user_manager.user_exists(username):
                return web.json_response(
                    {"success": False, "message": "用户名已存在"},
                    status=400
                )
            
            # 设置用户角色
            role = "admin" if not self.user_manager.has_users() else "user"
            
            # 创建用户
            success = self.user_manager.create_user(username, password, email, role)
            
            if not success:
                return web.json_response(
                    {"success": False, "message": "创建用户失败"},
                    status=500
                )
            
            # 生成令牌
            token = self.token_manager.generate_token(username, role)
            
            # 返回结果
            return web.json_response({
                "success": True,
                "message": "注册成功",
                "data": {
                    "username": username,
                    "role": role,
                    "token": token
                }
            })
            
        except json.JSONDecodeError:
            return web.json_response(
                {"success": False, "message": "无效的JSON数据"},
                status=400
            )
        except Exception as e:
            self.logger.exception(f"处理注册请求异常: {e}")
            return web.json_response(
                {"success": False, "message": f"服务器错误: {str(e)}"},
                status=500
            )
    
    async def handle_login(self, request: web.Request) -> web.Response:
        """
        处理登录请求
        
        Args:
            request: Web请求
            
        Returns:
            web.Response: Web响应
        """
        try:
            # 获取请求数据
            data = await request.json()
            
            # 验证请求数据
            username = data.get("username")
            password = data.get("password")
            
            if not username or not password:
                return web.json_response(
                    {"success": False, "message": "缺少用户名或密码"},
                    status=400
                )
            
            # 验证用户凭据
            if not self.user_manager.verify_user(username, password):
                return web.json_response(
                    {"success": False, "message": "用户名或密码错误"},
                    status=401
                )
            
            # 获取用户信息
            user_info = self.user_manager.get_user(username)
            
            # 生成令牌
            token = self.token_manager.generate_token(username, user_info["role"])
            
            # 设置响应和Cookie
            response = web.json_response({
                "success": True,
                "message": "登录成功",
                "data": {
                    "username": username,
                    "role": user_info["role"],
                    "token": token
                }
            })
            
            # 设置Cookie（可选）
            response.set_cookie(
                name="auth_token",
                value=token,
                max_age=86400,  # 1天
                httponly=True,
                secure=request.url.scheme == "https",
                samesite="Strict"
            )
            
            return response
            
        except json.JSONDecodeError:
            return web.json_response(
                {"success": False, "message": "无效的JSON数据"},
                status=400
            )
        except Exception as e:
            self.logger.exception(f"处理登录请求异常: {e}")
            return web.json_response(
                {"success": False, "message": f"服务器错误: {str(e)}"},
                status=500
            )
    
    @login_required
    async def handle_current_user(self, request: web.Request) -> web.Response:
        """
        获取当前用户信息
        
        Args:
            request: Web请求
            
        Returns:
            web.Response: Web响应
        """
        # 从请求中获取用户信息
        user = request.get('user', {})
        
        # 获取用户名
        username = user.get('sub')
        
        # 获取用户详细信息
        user_info = self.user_manager.get_user(username) or {}
        
        return web.json_response({
            "success": True,
            "data": user_info
        })
    
    async def handle_logout(self, request: web.Request) -> web.Response:
        """
        处理注销请求
        
        Args:
            request: Web请求
            
        Returns:
            web.Response: Web响应
        """
        # 创建响应
        response = web.json_response({
            "success": True,
            "message": "已注销"
        })
        
        # 清除Cookie
        response.del_cookie("auth_token")
        
        return response 