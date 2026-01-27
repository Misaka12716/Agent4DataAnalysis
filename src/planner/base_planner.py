from abc import ABC, abstractmethod
from typing import Dict, Any


class BasePlanner(ABC):
    """规划器基类：定义规划器接口规范"""

    @abstractmethod
    async def organize_requirement(
        self, input_data: str, file_info: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """结构化需求整理"""
        pass

    @abstractmethod
    async def search_experience(self, requirement: Dict[str, Any]) -> Dict[str, Any]:
        """搜索过往经验"""
        pass

    @abstractmethod
    async def search_knowledge(self, requirement: Dict[str, Any]) -> Dict[str, Any]:
        """知识搜索"""
        pass

    @abstractmethod
    async def find_tools(self, requirement: Dict[str, Any]) -> Dict[str, Any]:
        """工具匹配"""
        pass

    @abstractmethod
    async def assign_tasks(
        self,
        requirement: Dict[str, Any],
        knowledge: Dict[str, Any],
        experience: Dict[str, Any],
        tools: Dict[str, Any],
    ) -> Dict[str, Any]:
        """任务分解与分配"""
        pass
