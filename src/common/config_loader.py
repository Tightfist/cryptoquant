import os
import sys
import json
import yaml
from typing import Dict, Any, Optional, Union

def load_config(app_name: str, config_path: Optional[str] = None) -> Dict[str, Any]:
    """
    加载应用配置文件
    
    Args:
        app_name: 应用名称，用于在未指定配置文件路径时构建默认路径
        config_path: 配置文件路径，如果为None，则使用默认路径
        
    Returns:
        Dict[str, Any]: 配置字典
        
    Raises:
        SystemExit: 如果配置文件加载失败
    """
    # 如果未指定配置文件路径，则使用默认路径
    if config_path is None:
        # 尝试在多个位置查找配置文件
        possible_paths = [
            # 1. 当前工作目录
            os.path.join(os.getcwd(), f"{app_name}_config.yaml"),
            os.path.join(os.getcwd(), f"{app_name}_config.json"),
            # 2. 应用目录
            os.path.join(os.getcwd(), "apps", app_name, "config.yaml"),
            os.path.join(os.getcwd(), "apps", app_name, "config.json"),
            # 3. 项目根目录的config目录
            os.path.join(os.getcwd(), "config", f"{app_name}.yaml"),
            os.path.join(os.getcwd(), "config", f"{app_name}.json"),
        ]
        
        # 尝试每个可能的路径
        for path in possible_paths:
            if os.path.exists(path):
                config_path = path
                break
                
        # 如果仍然没有找到配置文件，使用最后一个路径
        if config_path is None:
            config_path = possible_paths[-1]  # 使用config目录作为默认位置
    
    try:
        config = _load_file(config_path)
            
        # 添加配置文件路径到配置中，方便后续使用
        config['_config_path'] = config_path
        return config
    except Exception as e:
        print(f"加载配置文件失败: {str(e)}")
        print(f"尝试加载的配置文件路径: {config_path}")
        sys.exit(1)

def _load_file(file_path: str) -> Dict[str, Any]:
    """
    根据文件扩展名加载配置文件
    
    Args:
        file_path: 文件路径
        
    Returns:
        Dict[str, Any]: 配置字典
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        if file_path.endswith('.yaml') or file_path.endswith('.yml'):
            return yaml.safe_load(f)
        elif file_path.endswith('.json'):
            return json.load(f)
        else:
            raise ValueError(f"不支持的配置文件格式: {file_path}")

def get_app_config(app_name: str, config_path: Optional[str] = None) -> Dict[str, Any]:
    """
    获取应用配置，合并通用API配置和应用特定配置
    
    Args:
        app_name: 应用名称
        config_path: 配置文件路径，如果为None，则使用默认路径
        
    Returns:
        Dict[str, Any]: 合并后的配置字典
    """
    # 加载应用特定配置
    app_config = load_config(app_name, config_path)
    
    # 尝试加载通用API配置
    api_config = _load_api_config()
    
    # 合并配置
    if api_config:
        # 深度合并配置
        merged_config = _deep_merge(api_config, app_config)
        return merged_config
    
    return app_config

def _load_api_config() -> Optional[Dict[str, Any]]:
    """
    加载通用API配置
    
    Returns:
        Optional[Dict[str, Any]]: API配置字典，如果加载失败则返回None
    """
    # 尝试在多个位置查找API配置文件
    possible_paths = [
        os.path.join(os.getcwd(), "config", "api.yaml"),
        os.path.join(os.getcwd(), "config", "api.json"),
    ]
    
    for path in possible_paths:
        if os.path.exists(path):
            try:
                return _load_file(path)
            except Exception as e:
                print(f"加载API配置文件失败: {str(e)}")
                print(f"尝试加载的API配置文件路径: {path}")
    
    return None

def _deep_merge(dict1: Dict[str, Any], dict2: Dict[str, Any]) -> Dict[str, Any]:
    """
    深度合并两个字典，dict2的值会覆盖dict1的值
    
    Args:
        dict1: 第一个字典
        dict2: 第二个字典
        
    Returns:
        Dict[str, Any]: 合并后的字典
    """
    result = dict1.copy()
    
    for key, value in dict2.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            # 如果两个值都是字典，则递归合并
            result[key] = _deep_merge(result[key], value)
        else:
            # 否则，使用dict2的值覆盖dict1的值
            result[key] = value
    
    return result

def save_config(config: Dict[str, Any], config_path: Optional[str] = None) -> bool:
    """
    保存配置到文件
    
    Args:
        config: 配置字典
        config_path: 配置文件路径，如果为None，则使用config中的_config_path
        
    Returns:
        bool: 是否保存成功
    """
    if config_path is None:
        config_path = config.get('_config_path')
        if config_path is None:
            print("保存配置失败: 未指定配置文件路径")
            return False
    
    try:
        # 创建目录（如果不存在）
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        
        # 移除内部使用的_config_path字段
        config_to_save = {k: v for k, v in config.items() if not k.startswith('_')}
        
        # 根据文件扩展名选择保存格式
        if config_path.endswith('.yaml') or config_path.endswith('.yml'):
            with open(config_path, 'w', encoding='utf-8') as f:
                yaml.dump(config_to_save, f, default_flow_style=False, allow_unicode=True)
        elif config_path.endswith('.json'):
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config_to_save, f, indent=2, ensure_ascii=False)
        else:
            print(f"不支持的配置文件格式: {config_path}")
            return False
            
        return True
    except Exception as e:
        print(f"保存配置失败: {str(e)}")
        return False 