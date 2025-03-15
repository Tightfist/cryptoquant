import asyncio
import inspect
from typing import Callable, Coroutine, Union, Any
from concurrent.futures import ThreadPoolExecutor
import logging

class AsyncEventLoop:
    """支持混合模式的事件循环"""
    def __init__(self, max_workers=5):
        self.loop = asyncio.new_event_loop()
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.tasks = set()
        self.logger = logging.getLogger("EventLoop")

    def _safe_create_task(self, coro):
        """安全包装异步任务"""
        task = asyncio.ensure_future(coro, loop=self.loop)
        task.add_done_callback(self._handle_task_exception)
        self.tasks.add(task)
        return task

    def _handle_task_exception(self, task):
        """统一异常处理"""
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error("事件循环任务异常", exc_info=e)
        # 从任务集合中移除已完成的任务
        if task in self.tasks:
            self.tasks.remove(task)

    def add_task(self, func_or_coro: Union[Callable, Coroutine], interval: float = 0, immediate=False):
        """
        添加定时/周期任务
        
        Args:
            func_or_coro: 函数、协程函数或协程对象
            interval: 执行间隔（秒），0表示只执行一次
            immediate: 是否立即执行
        """
        # 处理协程对象
        if inspect.iscoroutine(func_or_coro):
            if interval > 0:
                self.logger.warning("协程对象不支持周期执行，将只执行一次")
            # 直接创建任务
            self._safe_create_task(func_or_coro)
            return
            
        # 处理协程函数
        if inspect.iscoroutinefunction(func_or_coro):
            wrapper = self._async_task_wrapper(func_or_coro, interval)
        # 处理普通函数
        else:
            wrapper = self._sync_task_wrapper(func_or_coro, interval)

        # 创建并执行任务
        self._safe_create_task(wrapper())

    def _async_task_wrapper(self, coro_func, interval):
        """异步任务包装器"""
        async def wrapper():
            while True:
                try:
                    await coro_func()
                except Exception as e:
                    self.logger.error("异步任务执行失败", exc_info=e)
                if interval <= 0:
                    break
                await asyncio.sleep(interval)
        return wrapper

    def _sync_task_wrapper(self, func, interval):
        """同步任务包装器"""
        async def wrapper():
            while True:
                try:
                    await self.loop.run_in_executor(self.executor, func)
                except Exception as e:
                    self.logger.error("同步任务执行失败", exc_info=e)
                if interval <= 0:
                    break
                await asyncio.sleep(interval)
        return wrapper

    def run_forever(self):
        """启动事件循环"""
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_forever()
        except KeyboardInterrupt:
            self.logger.info("接收到关闭信号")
        finally:
            self.shutdown()

    def shutdown(self):
        """优雅关闭"""
        self.logger.info("开始关闭事件循环...")
        for task in list(self.tasks):
            task.cancel()
        self.loop.run_until_complete(self.loop.shutdown_asyncgens())
        self.loop.close()
        
    def stop(self):
        """停止事件循环"""
        self.loop.stop()
