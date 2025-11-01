from .base_discriminator import BaseDiscriminator

class WorkerDiscriminator(BaseDiscriminator):
    """子任务判别器：判断单个Worker的任务是否成功"""
    def judge(self, worker_result):
        """示例：判断子任务是否成功（简化逻辑）"""
        print(f"[WorkerDiscriminator] Judging task {worker_result['task_id']}...")
        return worker_result.get("success", True)  # 暂时假设成功