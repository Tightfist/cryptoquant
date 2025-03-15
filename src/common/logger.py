import logging
import os
import json
from logging.config import dictConfig

class ExtraInfoFormatter(logging.Formatter):
    """自定义日志格式化器，支持打印extra字段中的信息"""
    def format(self, record):
        # 先使用标准格式化
        formatted_message = super().format(record)
        
        # 收集所有extra信息
        extra_dict = {}
        
        # 遍历record的所有属性，找出不是标准属性的内容
        standard_attrs = [
            'args', 'asctime', 'created', 'exc_info', 'exc_text', 'filename',
            'funcName', 'id', 'levelname', 'levelno', 'lineno', 'module',
            'msecs', 'message', 'msg', 'name', 'pathname', 'process',
            'processName', 'relativeCreated', 'stack_info', 'thread', 'threadName'
        ]
        
        for attr, value in record.__dict__.items():
            if attr not in standard_attrs and not attr.startswith('_'):
                extra_dict[attr] = value
        
        # 如果有extra信息，添加到日志消息中
        if extra_dict:
            try:
                # 将extra信息转换为单行JSON字符串，确保中文正确显示
                extra_str = json.dumps(extra_dict, ensure_ascii=False, separators=(',', ':'))
                formatted_message += f" | Extra: {extra_str}"
            except Exception as e:
                formatted_message += f" | Error formatting extra info: {str(e)}"
        
        return formatted_message

def configure_logger(app_name: str, log_level: str = "INFO", log_file: str = None, output_targets: list = None):
    """动态配置日志，根据app名称隔离
    
    Args:
        app_name: 应用名称，用于隔离日志
        log_level: 日志级别，默认为INFO
        log_file: 日志文件名，如果不指定则使用默认的trading.log
        output_targets: 日志输出目标，可以是['file']、['console']或['file', 'console']，默认为['file', 'console']
    """
    log_dir = os.path.join("logs", app_name)
    os.makedirs(log_dir, exist_ok=True)
    
    # 如果没有指定日志文件名，则使用默认的trading.log
    if log_file is None:
        log_file = "trading.log"
    
    # 确保日志文件路径是完整的
    log_file_path = os.path.join(log_dir, log_file)
    
    # 如果没有指定输出目标，则默认输出到文件和控制台
    if output_targets is None:
        output_targets = ['file', 'console']
    
    # 确保输出目标是列表
    if isinstance(output_targets, str):
        output_targets = [output_targets]
    
    # 创建日志配置
    config = {
        "version": 1,
        "formatters": {
            "detailed": {
                "()": ExtraInfoFormatter,
                "format": "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
            }
        },
        "handlers": {},
        "loggers": {}
    }
    
    # 根据输出目标添加处理器
    handlers = []
    
    if 'file' in output_targets:
        config["handlers"]["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": log_file_path,
            "maxBytes": 10*1024*1024,  # 10MB
            "backupCount": 5,
            "formatter": "detailed",
            "encoding": "utf-8"
        }
        handlers.append("file")
    
    if 'console' in output_targets:
        config["handlers"]["console"] = {
            "class": "logging.StreamHandler",
            "formatter": "detailed"
        }
        handlers.append("console")
    
    # 添加日志记录器配置
    config["loggers"] = {
        "OKExTrader": {
            "handlers": handlers,
            "level": log_level
        },
        "Strategy": {
            "handlers": handlers,
            "level": log_level
        },
        # 添加应用的日志配置
        app_name: {
            "handlers": handlers,
            "level": log_level
        }
    }
    
    # 应用配置
    dictConfig(config)
