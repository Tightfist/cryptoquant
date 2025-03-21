import os

def get_project_root() -> str:
    """
    获取项目根目录路径
    
    Returns:
        str: 项目根目录的绝对路径
    """
    # 获取当前文件的绝对路径
    current_file = os.path.abspath(__file__)
    
    # 获取utils目录的父目录,即项目根目录
    project_root = os.path.dirname(os.path.dirname(current_file))
    
    return project_root
