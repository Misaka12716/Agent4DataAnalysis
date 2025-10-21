#!/usr/bin/env python3
"""
运行多智能体规划器测试的脚本
"""
import sys
import os
import asyncio
import json
from aiohttp import ClientSession

# 添加当前目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from planner.multi_agent_planner import MultiAgentPlanner, ToolMetadata
from knowledge.knowledge_base import KnowledgeBase

# -------------------------- 模拟工具元信息接口（后续替换） --------------------------
def mock_tool_fetcher() -> list[ToolMetadata]:
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

if __name__ == "__main__":
    # 运行测试
    try:
        asyncio.run(test_planner())
    except KeyboardInterrupt:
        print("\n测试中断")
    except Exception as e:
        print(f"全局错误：{str(e)}")
        import traceback
        traceback.print_exc()
