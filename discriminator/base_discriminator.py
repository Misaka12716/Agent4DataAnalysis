from abc import ABC, abstractmethod

class BaseDiscriminator(ABC):
    """Discriminator抽象基类：定义“任务是否成功”的判断接口"""
    @abstractmethod
    def judge(self, data):
        """判断任务（子任务/总任务）是否成功"""
        pass