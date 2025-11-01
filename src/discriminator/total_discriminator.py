from .base_discriminator import BaseDiscriminator

class TotalDiscriminator(BaseDiscriminator):
    """总任务判别器：判断整个任务是否成功"""
    def judge(self, total_report):
        """示例：判断总任务是否成功（如所有子任务成功则总成功）"""
        print("[TotalDiscriminator] Judging total task...")
        all_success = all(r.get("success", True) for r in total_report["details"])
        return all_success