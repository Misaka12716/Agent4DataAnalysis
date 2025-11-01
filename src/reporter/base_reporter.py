from abc import ABC, abstractmethod

class BaseReporter(ABC):
    """Reporter抽象基类：定义“收集结果、生成报告”的接口"""
    @abstractmethod
    def collect_results(self, worker_results):
        """收集所有Worker的执行结果"""
        pass

    @abstractmethod
    def generate_report(self, collected_results):
        """基于结果生成汇总报告"""
        pass