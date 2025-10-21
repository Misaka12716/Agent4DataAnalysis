from planner.base_planner import BasePlanner
from knowledge.knowledge_base import KnowledgeBase
from utils.llm_response import ai_response, new_chat_session, get_chat_history
from aiohttp import ClientSession
from typing import Callable, List, Dict, Any
import json


# -------------------------- 类型定义（明确接口契约） --------------------------
ToolMetadata = Dict[
    str, str
]  # 工具元信息结构：name(工具名)、description(功能)、applicable_scenarios(适用场景)
ToolMetadataFetcher = Callable[
    [], List[ToolMetadata]
]  # 工具元信息获取接口（后续对接实际服务）


class MultiAgentPlanner(BasePlanner):
    """多智能体Planner：LLM驱动的智能任务规划实现"""

    def __init__(
        self,
        knowledge_base: KnowledgeBase,
        http_session: ClientSession,
        tool_metadata_fetcher: ToolMetadataFetcher,
    ):
        """
        初始化规划器
        :param knowledge_base: 知识库实例（暂未用，预留）
        :param http_session: LLM调用所需的HTTP会话
        :param tool_metadata_fetcher: 工具元信息获取器（后续替换为实际接口）
        """
        self.knowledge_base = knowledge_base
        self.http_session = http_session  # 异步LLM调用依赖
        self.tool_metadata_fetcher = tool_metadata_fetcher  # 工具元信息接口
        self.plan_session_id = new_chat_session()  # 规划专用会话（存储规划历史）
        print(f"[Planner] 初始化规划会话（ID: {self.plan_session_id}）")

    # -------------------------- 1. 结构化需求整理 --------------------------
    async def organize_requirement(self, input_data: str) -> Dict[str, Any]:
        print(f"[Planner] 解析需求：{input_data[:50]}...")

        # 设计Prompt：引导LLM生成标准化JSON（避免格式混乱）
        prompt = f"""
        请将以下用户需求解析为结构化JSON，仅返回JSON（无额外文字），包含必选字段：
        - task_type: 任务类型（如数据清洗、文本摘要、图表生成）
        - goal: 核心目标（清晰描述结果）
        - input_data: 输入描述（数据来源/格式）
        - output_requirement: 输出要求（格式/精度/样式）
        - constraints: 约束（时间/资源限制，无则填"无"）
        
        用户需求：{input_data}
        """

        # 调用LLM解析需求
        llm_result = await ai_response(
            prompt=prompt.strip(),
            session=self.http_session,
            need_thinking=True,  # 记录LLM思考过程（便于追溯）
        )

        # 处理结果：解析JSON+错误捕获
        if not llm_result["success"]:
            error = f"LLM调用失败：{llm_result['error']}"
            print(f"[Planner] {error}")
            return {"error": error, "raw_input": input_data}

        try:
            structured_req = json.loads(llm_result["content"])
            print(
                f"[Planner] 需求解析完成：{json.dumps(structured_req, ensure_ascii=False)[:80]}..."
            )
            return structured_req
        except json.JSONDecodeError as e:
            error = (
                f"LLM输出格式错误：{str(e)}，原始内容：{llm_result['content'][:50]}..."
            )
            print(f"[Planner] {error}")
            return {"error": error, "raw_llm_output": llm_result["content"]}

    # -------------------------- 2. 过往经验搜索 --------------------------
    async def search_experience(self, requirement: Dict[str, Any]) -> Dict[str, Any]:
        task_type = requirement.get("task_type", "未知类型")
        print(f"[Planner] 搜索{task_type}相关经验")

        # 1. 获取历史会话（优先规划专用会话，无则用模拟经验）
        history = get_chat_history(self.plan_session_id)
        if not history:
            history = [
                {"role": "user", "content": "清洗CSV数据（去空值+去重）"},
                {
                    "role": "assistant",
                    "content": "用tool_A：1.加载数据 2.清洗 3.导出，耗时20分钟",
                },
            ]

        # 2. 构造Prompt：让LLM分析经验相关性
        prompt = f"""
        过往任务历史：{json.dumps(history, ensure_ascii=False)}
        当前需求：{json.dumps(requirement, ensure_ascii=False)}
        
        请分析：
        1. 是否有相似任务（相似度≥70%）
        2. 有则总结经验（工具/步骤/耗时/注意事项），无则返回"无相关经验"
        """

        # 调用LLM生成经验总结
        llm_result = await ai_response(
            prompt=prompt.strip(), session=self.http_session, need_thinking=True
        )

        if not llm_result["success"]:
            return {
                "experience": f"经验搜索失败：{llm_result['error']}",
                "success": False,
            }

        return {
            "experience": llm_result["content"].strip(),
            "thinking": llm_result["thinking"],
            "success": True,
        }

    # -------------------------- 3. 知识搜索（暂不实现） --------------------------
    async def search_knowledge(self, requirement: Dict[str, Any]) -> Dict[str, Any]:
        print(
            f"[Planner] 知识搜索模块暂未集成（需求类型：{requirement.get('task_type')}）"
        )
        return {"knowledge": "知识搜索智能体待实现，将支持知识库检索", "success": False}

    # -------------------------- 4. 工具匹配（对接预留接口） --------------------------
    async def find_tools(self, requirement: Dict[str, Any]) -> Dict[str, Any]:
        print(f"[Planner] 匹配{requirement.get('task_type')}所需工具")

        # 1. 调用工具元信息接口（后续替换为实际服务）
        try:
            tool_metadata = self.tool_metadata_fetcher()
            if not tool_metadata:
                return {"tools": [], "error": "无可用工具元信息", "success": False}
        except Exception as e:
            error = f"工具接口调用失败：{str(e)}"
            return {"tools": [], "error": error, "success": False}

        # 2. 构造Prompt：让LLM匹配需求与工具
        prompt = f"""
        可用工具元信息：{json.dumps(tool_metadata, ensure_ascii=False, indent=2)}
        当前需求：{json.dumps(requirement, ensure_ascii=False)}
        
        请：
        1. 筛选1-3个匹配度最高的工具（按优先级排序）
        2. 按格式输出：
           工具列表：["工具1","工具2"]
           匹配理由：
           - 工具1：理由1；理由2
        """

        # 调用LLM筛选工具
        llm_result = await ai_response(
            prompt=prompt.strip(), session=self.http_session, need_thinking=True
        )

        if not llm_result["success"]:
            return {
                "tools": [],
                "error": f"工具匹配失败：{llm_result['error']}",
                "success": False,
            }

        # 解析LLM输出（提取工具列表和理由）
        try:
            content = llm_result["content"].strip()
            # 提取工具列表
            tools_line = [l for l in content.split("\n") if l.startswith("工具列表：")][
                0
            ]
            tools = json.loads(tools_line.replace("工具列表：", ""))
            # 提取匹配理由
            reasons = "\n".join([l for l in content.split("\n") if l.startswith("- ")])

            return {
                "tools": tools,
                "match_reasons": reasons,
                "thinking": llm_result["thinking"],
                "success": True,
            }
        except (IndexError, json.JSONDecodeError) as e:
            error = f"工具结果解析失败：{str(e)}，原始输出：{content[:50]}..."
            return {"tools": [], "error": error, "success": False}

    # -------------------------- 5. 任务分解与分配 --------------------------
    async def assign_tasks(
        self,
        requirement: Dict[str, Any],
        knowledge: Dict[str, Any],
        experience: Dict[str, Any],
        tools: Dict[str, Any],
    ) -> Dict[str, Any]:
        goal = requirement.get("goal", "未知目标")
        print(f"[Planner] 分解任务：{goal[:50]}...")

        # 校验依赖（工具必须存在）
        tool_list = tools.get("tools", [])
        if not tool_list:
            return {"tasks": [], "error": "无可用工具，无法分配任务", "success": False}

        # 构造Prompt：引导LLM生成结构化子任务
        prompt = f"""
        基于以下信息分解任务为2-5个子任务：
        1. 需求：{json.dumps(requirement, ensure_ascii=False, indent=2)}
        2. 经验：{experience.get('experience', '无')}
        3. 工具：{json.dumps(tool_list, ensure_ascii=False)}
        
        子任务必须包含字段：
        - task_id：整数ID（从1开始）
        - task_name：简洁名称
        - description：具体操作
        - required_tool：工具（从列表选，无则填"无"）
        - worker_type：Worker类型（如数据Worker、图表Worker）
        - dependencies：依赖的task_id列表（无则[]）
        - deadline：预估时间（如15分钟内）
        
        仅返回JSON数组（无额外文字）！
        """

        # 调用LLM分解任务
        llm_result = await ai_response(
            prompt=prompt.strip(), session=self.http_session, need_thinking=True
        )

        if not llm_result["success"]:
            return {
                "tasks": [],
                "error": f"任务分解失败：{llm_result['error']}",
                "success": False,
            }

        # 解析子任务并校验字段
        try:
            tasks = json.loads(llm_result["content"])
            required_fields = [
                "task_id",
                "task_name",
                "description",
                "required_tool",
                "worker_type",
                "dependencies",
                "deadline",
            ]

            # 校验每个子任务的字段完整性
            for task in tasks:
                missing = [f for f in required_fields if f not in task]
                if missing:
                    raise ValueError(f"子任务{task.get('task_id')}缺少字段：{missing}")

            return {"tasks": tasks, "thinking": llm_result["thinking"], "success": True}
        except (json.JSONDecodeError, ValueError) as e:
            error = (
                f"子任务解析失败：{str(e)}，原始输出：{llm_result['content'][:50]}..."
            )
            return {"tasks": [], "error": error, "success": False}


# 在 multi_agent_planner.py 末尾添加
if __name__ == "__main__":
    import asyncio
    from aiohttp import ClientSession
    from knowledge.knowledge_base import KnowledgeBase  # 假设知识库有默认实现

    # -------------------------- 模拟工具元信息接口（后续替换） --------------------------
    def mock_tool_fetcher() -> List[ToolMetadata]:
        """模拟返回3个常用工具的元信息"""
        return [
            {
                "name": "tool_A_CSV_Processor",
                "description": "CSV处理：加载/去空值/去重/格式转换",
                "applicable_scenarios": "数据清洗、CSV预处理",
            },
            {
                "name": "tool_C_Chart_Generator",
                "description": "图表生成：柱状图/折线图/饼图，导出PNG/SVG",
                "applicable_scenarios": "数据可视化、报告图表",
            },
            {
                "name": "tool_B_Text_Summarizer",
                "description": "文本摘要：提取关键信息，自定义长度",
                "applicable_scenarios": "报告总结、长文档提炼",
            },
        ]

    # -------------------------- 测试完整规划流程 --------------------------
    async def test_planner():
        async with ClientSession() as http_session:
            # 初始化依赖
            kb = KnowledgeBase()  # 知识库默认实例
            planner = MultiAgentPlanner(
                knowledge_base=kb,
                http_session=http_session,
                tool_metadata_fetcher=mock_tool_fetcher,
            )

            # 模拟用户需求（自然语言）
            user_input = "处理2024年销售CSV数据：1.去空值和重复订单 2.生成月度销售额柱状图（标题'2024各月销售'） 3.导出PNG，1小时内完成"

            # 步骤1：整理结构化需求
            print("\n=== 1. 结构化需求 ===")
            req = await planner.organize_requirement(user_input)
            if "error" in req:
                print(f"失败：{req['error']}")
                return
            print(json.dumps(req, ensure_ascii=False, indent=2))

            # 步骤2：搜索过往经验
            print("\n=== 2. 过往经验 ===")
            exp = await planner.search_experience(req)
            if not exp["success"]:
                print(f"失败：{exp['experience']}")
                return
            print(f"经验：{exp['experience']}\n思考：{exp['thinking']}")

            # 步骤3：知识搜索（暂未实现）
            print("\n=== 3. 知识搜索 ===")
            knowledge = await planner.search_knowledge(req)
            print(knowledge["knowledge"])

            # 步骤4：匹配工具
            print("\n=== 4. 工具匹配 ===")
            tools = await planner.find_tools(req)
            if not tools["success"]:
                print(f"失败：{tools['error']}")
                return
            print(
                f"工具列表：{tools['tools']}\n理由：{tools['match_reasons']}\n思考：{tools['thinking']}"
            )

            # 步骤5：任务分配
            print("\n=== 5. 任务分解 ===")
            tasks = await planner.assign_tasks(req, knowledge, exp, tools)
            if not tasks["success"]:
                print(f"失败：{tasks['error']}")
                return
            for task in tasks["tasks"]:
                print(f"\n子任务{task['task_id']}：{task['task_name']}")
                print(f"描述：{task['description']}")
                print(
                    f"工具：{task['required_tool']} | Worker：{task['worker_type']} | 依赖：{task['dependencies']} | 截止：{task['deadline']}"
                )
            print(f"\n任务思考：{tasks['thinking']}")

    # 运行测试
    try:
        asyncio.run(test_planner())
    except KeyboardInterrupt:
        print("\n测试中断")
    except Exception as e:
        print(f"全局错误：{str(e)}")


