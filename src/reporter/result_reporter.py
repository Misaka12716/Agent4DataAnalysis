from .base_reporter import BaseReporter

class ResultReporter(BaseReporter):
    """结果汇报器：收集Worker结果并生成报告"""
    def collect_results(self, worker_results):
        """示例：收集Worker结果"""
        print("[Reporter] Collecting worker results...")
        return worker_results

    def generate_report(self, collected_results):
        """示例：生成汇总报告"""
        print("[Reporter] Generating total report...")
        return {
            "summary": "Overall task summary",
            "details": collected_results
        }