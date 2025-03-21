import logging
import json
from aiohttp import web

# 添加Webhook路由
class TradingViewWebhook:
    def __init__(self, framework):
        self.framework = framework
        self.logger = logging.getLogger("tradingview_signal_tracker.webhook")

    async def handle_webhook(self, request):
        try:
            # 获取请求内容类型
            content_type = request.headers.get('Content-Type', '')
            
            if 'application/json' in content_type:
                # JSON格式
                data = await request.json()
            else:
                # 文本格式
                text = await request.text()
                try:
                    # 尝试解析为JSON
                    data = json.loads(text)
                except json.JSONDecodeError:
                    # 非JSON格式，作为文本处理
                    data = {"text": text}
            
            # 处理信号
            success, message = await self.framework.process_signal(data)
            
            # 返回结果
            return web.json_response({
                "success": success,
                "message": message
            })
        except Exception as e:
            self.logger.exception(f"处理webhook异常: {e}")
            return web.json_response(
                {"success": False, "message": f"处理异常: {e}"},
                status=500
            )
                