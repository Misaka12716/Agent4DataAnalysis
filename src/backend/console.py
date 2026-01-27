from aiohttp import ClientSession
from typing import AsyncGenerator, Dict, Any, Optional
from planner.agent_planner import AgentPlanner
from knowledge.knowledge_base import KnowledgeBase  # 按需导入知识库模块


class ConsoleAgentWorkflow:
    """控制台代理工作流管理器"""

    def __init__(
        self,
        http_session: Optional[ClientSession] = None,
        knowledge_base: Optional[KnowledgeBase] = None,
    ):
        self.http_session: Optional[ClientSession] = http_session
        self.knowledge_base: Optional[KnowledgeBase] = knowledge_base
        self.planner_result: Optional[Dict[str, Any]] = None  # 存储Planner最终结果

    async def _init_components(self):
        """初始化依赖组件"""
        # 创建异步HTTP会话
        self.http_session = ClientSession()
        # 初始化知识库（可选，根据实际需求配置）
        # self.knowledge_base = KnowledgeBase(...)  # 需根据知识库实际初始化参数调整

    async def run_workflow(
        self, input_data: str, file_info: str = "No files uploaded"
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        执行完整工作流：Planner → [Worker] → [Reporter]

        Args:
            input_data: 用户输入的原始需求
            file_info: 上传文件信息

        Yields:
            工作流各阶段的处理结果
        """
        try:
            # 初始化组件
            await self._init_components()
            yield {
                "type": "workflow_status",
                "stage": "init",
                "message": "组件初始化完成，开始执行Planner流程",
            }

            # -------------------------- 1. 执行Planner流程 --------------------------
            yield {
                "type": "workflow_stage_start",
                "stage": "planner",
                "message": "启动Planner任务规划",
            }

            async with AgentPlanner(
                http_session=self.http_session, knowledge_base=self.knowledge_base
            ) as planner:
                # 流式处理Planner结果
                async for planner_step in planner.run_flow(input_data, file_info):
                    # 转发Planner的流式输出
                    yield {"type": "planner_step", "data": planner_step}

                    # 捕获Planner最终结果
                    if planner_step.get("type") == "stage_result":
                        self.planner_result = planner_step["data"]

            yield {
                "type": "workflow_stage_complete",
                "stage": "planner",
                "message": "Planner流程执行完成",
                "result": self.planner_result,
            }

            # -------------------------- 2. 预留Worker调用位置 --------------------------
            yield {
                "type": "workflow_stage_start",
                "stage": "worker",
                "message": "准备执行Worker任务（待实现）",
            }

            # TODO: 实际调用Worker时替换以下占位逻辑
            worker_result = await self._call_worker_placeholder()
            yield {
                "type": "workflow_stage_complete",
                "stage": "worker",
                "message": "Worker任务执行完成（占位）",
                "result": worker_result,
            }

            # -------------------------- 3. 预留Reporter调用位置 --------------------------
            yield {
                "type": "workflow_stage_start",
                "stage": "reporter",
                "message": "准备生成最终报告（待实现）",
            }

            # TODO: 实际调用Reporter时替换以下占位逻辑
            reporter_result = await self._call_reporter_placeholder(worker_result)
            yield {
                "type": "workflow_stage_complete",
                "stage": "reporter",
                "message": "报告生成完成（占位）",
                "result": reporter_result,
            }

            # 工作流最终结果
            yield {
                "type": "workflow_final_result",
                "success": True,
                "summary": {
                    "planner_result": self.planner_result,
                    "worker_result": worker_result,
                    "reporter_result": reporter_result,
                },
            }

        except Exception as e:
            yield {
                "type": "workflow_error",
                "stage": "unknown",
                "error": str(e),
                "traceback": str(e.__traceback__),
            }
        finally:
            # 资源清理
            await self._cleanup()

    async def _call_worker_placeholder(self) -> Dict[str, Any]:
        """Worker调用占位方法（待实现真实逻辑）"""
        return {
            "status": "worker_placeholder",
            "planner_input": self.planner_result,
            "message": "Worker模块尚未实现，此处为占位结果",
        }

    async def _call_reporter_placeholder(
        self, worker_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Reporter调用占位方法（待实现真实逻辑）"""
        return {
            "status": "reporter_placeholder",
            "worker_input": worker_result,
            "message": "Reporter模块尚未实现，此处为占位结果",
            "final_report": "根据Planner规划和Worker执行结果生成的报告（待实现）",
        }

    async def _cleanup(self):
        """清理资源"""
        if self.http_session and not self.http_session.closed:
            await self.http_session.close()
        if self.knowledge_base:
            await self.knowledge_base.close()


# -------------------------- 测试代码 --------------------------
async def main():
    """测试工作流执行"""
    workflow = ConsoleAgentWorkflow()

    # 示例输入
    test_input = "分析上传的销售数据文件，生成月度销售总结报告"
    test_file_info = "sales_data_202501.xlsx (大小：2.4MB，包含1200条记录)"

    # 执行工作流并打印结果
    async for result in workflow.run_workflow(test_input, test_file_info):
        print(f"\n[{result['type']}]")
        if "stage" in result:
            print(f"阶段: {result['stage']}")
        if "message" in result:
            print(f"信息: {result['message']}")
        if "data" in result:
            print(f"数据: {result['data']}")
        if "result" in result:
            print(f"结果: {result['result']}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
