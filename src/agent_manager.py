# from .planner.agent_planner import AgentPlanner
# from .worker.multi_agent_worker import MultiAgentWorker
# from .reporter.result_reporter import ResultReporter
# from .discriminator.worker_discriminator import WorkerDiscriminator
# from .discriminator.total_discriminator import TotalDiscriminator
# from .knowledge.knowledge_base import KnowledgeBase

# class AgentManager:
#     """Agent管理器：协调各组件的控制流与数据流"""
#     def __init__(self):
#         # 初始化各组件
#         self.knowledge_base = KnowledgeBase()
#         self.planner = AgentPlanner(self.knowledge_base)
#         self.worker = MultiAgentWorker()
#         self.reporter = ResultReporter()
#         self.worker_discriminator = WorkerDiscriminator()
#         self.total_discriminator = TotalDiscriminator()

#     def run(self, input_data):
#         """执行Agent工作流：从输入到输出的完整流程"""
#         # 1. Planner：整理需求 + 搜索知识/经验 + 分配任务
#         requirement = self.planner.organize_requirement(input_data)
#         knowledge = self.planner.search_knowledge(requirement)
#         experience = self.planner.search_experience(requirement)
#         tools = self.planner.find_tools(requirement)
#         tasks = self.planner.assign_tasks(requirement, knowledge, experience, tools)

#         # 2. Worker + WorkerDiscriminator：执行子任务 + 判断子任务是否成功
#         worker_results = []
#         for task in tasks:
#             result = self.worker.execute_task(task, knowledge, tools)
#             is_success = self.worker_discriminator.judge(result)
#             result["success"] = is_success  # 记录子任务是否成功
#             worker_results.append(result)

#         # 3. Reporter：收集结果 + 生成报告
#         collected_results = self.reporter.collect_results(worker_results)
#         report = self.reporter.generate_report(collected_results)

#         # 4. TotalDiscriminator：判断总任务是否成功
#         total_success = self.total_discriminator.judge(report)

#         # 5. 结果返回（失败处理逻辑暂时留白，可后续扩展重试、重新规划等）
#         if total_success:
#             return {"status": "success", "output": report}
#         else:
#             print("[AgentManager] Total task failed! Handling retry...")
#             # TODO: 失败后重试、重新规划等逻辑
#             return {"status": "failed", "reason": "Total task judgment failed"}
