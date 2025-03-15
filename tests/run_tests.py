# -*- coding: utf-8 -*-
import unittest
import os
import sys

# 添加项目根目录到路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


def run_all_tests():
    """运行所有测试"""
    # 发现并加载所有测试
    test_loader = unittest.TestLoader()
    test_suite = test_loader.discover(os.path.dirname(__file__), pattern="test_*.py")
    
    # 运行测试
    test_runner = unittest.TextTestRunner(verbosity=2)
    result = test_runner.run(test_suite)
    
    # 返回测试结果
    return result.wasSuccessful()


if __name__ == "__main__":
    print("=" * 70)
    print("运行所有交易所适配器测试")
    print("=" * 70)
    
    success = run_all_tests()
    
    # 根据测试结果设置退出码
    sys.exit(0 if success else 1) 