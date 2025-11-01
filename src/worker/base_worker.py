from abc import ABC, abstractmethod

class BaseWorker(ABC):
    """Worker抽象基类：定义“执行子任务”的接口"""
    @abstractmethod
    def execute_task(self, task, knowledge, tools_metadata):
        """执行分配的子任务"""
        pass