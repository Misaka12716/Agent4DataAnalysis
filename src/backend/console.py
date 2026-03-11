from aiohttp import ClientSession
from typing import AsyncGenerator, Dict, Any, Optional
from planner.agent_planner import AgentPlanner


class ConsoleAgentWorkflow:
    """控制台代理工作流管理器"""

    def __init__(
        self,
        http_session: Optional[ClientSession] = None,
    ):
        self.http_session: Optional[ClientSession] = http_session
        self.planner_result: Optional[Dict[str, Any]] = None  # 存储Planner最终结果

    async def _init_components(self):
        """初始化依赖组件"""
        # 创建异步HTTP会话
        self.http_session = ClientSession()

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

            async with AgentPlanner() as planner:
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
