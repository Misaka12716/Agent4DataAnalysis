from abc import ABC, abstractmethod
from typing import Dict, Any, List

class BaseWorker(ABC):
    """Worker抽象基类：定义"执行子任务"的接口"""
    @abstractmethod
    def execute_task(self, tasks: List[Dict[str, Any]], knowledge: Dict[str, Any] = None, tools_metadata: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        执行分配的子任务列表
        
        参数:
            tasks: 子任务列表，每个任务包含 task_id, task_name, description, dependencies, worker_type, input, output
            knowledge: 知识信息（可选）
            tools_metadata: 工具元数据列表（可选）
        
        返回:
            执行结果字典，包含任务执行状态和结果
        """
        pass