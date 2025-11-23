import asyncio
from aiohttp import ClientSession
from knowledge.knowledge_base import KnowledgeBase
from planner.multi_agent_planner import MultiAgentPlanner
from typing import Dict, Any, Optional, Callable
import json


# -------------------------- 通用工具函数：流式生成器处理（实时打印+结果收集） --------------------------
async def process_stream_generator(
    gen, step_name: str, return_key: str = "structured_requirement"
) -> Any:
    """
    处理流式生成器，实时打印中间输出并收集最终结果
    :param gen: 异步生成器（planner方法返回）
    :param step_name: 步骤名称（用于日志标识）
    :param return_key: 最终结果的提取键（如"structured_requirement"/"results"/"tasks"）
    :return: 步骤的最终结果
    """
    final_result = None
    async for data in gen:
        # 状态信息：打印处理阶段
        if data["type"] == "status":
            print(f"\n[{step_name}][{data.get('step', 'unknown')}] {data['message']}")

        # LLM实时片段：无换行打印（模拟打字机）
        elif data["type"] == "llm_chunk":
            print(f"{data['content']}", end="", flush=True)

        # LLM完整结果：打印思考过程和完整内容
        elif data["type"] == "llm_complete":
            print(f"\n\n[{step_name}][LLM完整结果]")
            print(f"思考过程：{data.get('thinking', '无')}")
            print(f"完整内容：{data.get('content', '无')}")

        # 最终结果：提取并保存
        elif data["type"] == "result":
            final_result = data.get(return_key, data)

        # 错误信息：抛出异常中断流程
        elif data["type"] == "error":
            raise ValueError(
                f"{step_name}执行失败：{data['message']}（{data.get('raw_input', '')}）"
            )

        # 经验搜索的单条结果：辅助打印
        elif data["type"] == "item_result":
            print(
                f"\n[{step_name}][{data.get('item_index', 'unknown')}] 状态：{data['status']} - {data.get('reason', '')}"
            )
    if not final_result:
        raise ValueError(f"{step_name}未返回有效结果")
    return final_result


# -------------------------- 通用工具函数：DAG结构打印 --------------------------
def print_dag(
    nodes: list,
    node_id_key: str,
    dependencies_key: str,
    print_node: Callable,
    indent_level: int = 0,
    is_last_child: bool = False,
):
    """通用递归DAG打印函数（保留原有逻辑）"""
    for i, node in enumerate(nodes):
        is_last = i == len(nodes) - 1
        indent = ""
        if indent_level > 0:
            for _ in range(indent_level - 1):
                indent += "│   "
            indent += "└── " if is_last else "├── "
        print_node(node, indent)
        child_nodes = [
            n for n in nodes if node[node_id_key] in n.get(dependencies_key, [])
        ]
        if child_nodes:
            print_dag(
                nodes=child_nodes,
                node_id_key=node_id_key,
                dependencies_key=dependencies_key,
                print_node=print_node,
                indent_level=indent_level + 1,
                is_last_child=is_last,
            )


# -------------------------- 核心流程：规划器执行流程封装（适配流式） --------------------------
async def run_planner_flow(
    input_requirement: str,
    http_session: Optional[ClientSession] = None,
    knowledge_base: Optional[KnowledgeBase] = None,
) -> Dict[str, Any]:
    """封装MultiAgentPlanner的完整流式执行流程"""
    result = {
        "输入需求": input_requirement,
        "结构化需求": None,
        "经验搜索结果": None,
        "知识搜索结果": None,
        "任务分配结果": None,
        "工具匹配结果": None,
        "执行成功": False,
        "错误信息": None,
    }

    # 处理可选参数
    if not http_session:
        http_session = ClientSession()
    if not knowledge_base:
        knowledge_base = KnowledgeBase()

    try:
        # 1. 创建规划器实例
        planner = MultiAgentPlanner(
            http_session=http_session, knowledge_base=knowledge_base
        )
        print(f"\n[初始化] 规划器会话ID：{planner.plan_session_id}")

        # 2. 需求结构化处理（流式）
        print("\n=============================================")
        print("步骤1：需求结构化分析")
        print("=============================================")
        organize_gen = planner.organize_requirement(input_requirement)
        structured_result = await process_stream_generator(
            organize_gen, step_name="需求结构化", return_key="structured_requirement"
        )
        result["结构化需求"] = structured_result

        # 3. 历史经验搜索（流式）
        print("\n=============================================")
        print("步骤2：过往经验搜索")
        print("=============================================")
        experience_gen = planner.search_experience(structured_result)
        experience_result = await process_stream_generator(
            experience_gen, step_name="经验搜索", return_key="results"
        )
        result["经验搜索结果"] = experience_result

        # 4. 知识库检索（流式）
        print("\n=============================================")
        print("步骤3：知识库检索")
        print("=============================================")
        knowledge_gen = planner.search_knowledge(structured_result)
        knowledge_result = await process_stream_generator(
            knowledge_gen, step_name="知识搜索", return_key="knowledge"
        )
        result["知识搜索结果"] = knowledge_result

        # 5. 任务分解与分配（流式）
        print("\n=============================================")
        print("步骤4：任务分解与分配")
        print("=============================================")
        assign_gen = planner.assign_tasks(
            requirement=structured_result,
            knowledge=knowledge_result,
            experience=experience_result,
        )
        task_result = await process_stream_generator(
            assign_gen, step_name="任务分配", return_key="tasks"
        )
        # 组装任务分配结果（兼容原有格式）
        result["任务分配结果"] = {
            "tasks": task_result,
            "total_tasks": len(task_result),
            "success": True,
        }

        # 6. 子任务工具匹配（逐个任务流式处理）
        print("\n=============================================")
        print("步骤5：子任务工具匹配")
        print("=============================================")
        tool_matching_results = []
        for task in task_result:
            print(f"\n[工具匹配] 处理任务：{task['task_id']} - {task['task_name']}")
            tool_gen = planner.find_tools(task)
            tool_result = await process_stream_generator(
                tool_gen, step_name=f"工具匹配-{task['task_id']}", return_key="data"
            )
            tool_matching_results.append(
                {
                    "任务ID": task["task_id"],
                    "任务名称": task["task_name"],
                    "工具匹配详情": tool_result,
                    "依赖关系": task.get("dependencies", []),
                }
            )
        result["工具匹配结果"] = tool_matching_results
        result["执行成功"] = True

    except Exception as e:
        error_msg = str(e)
        result["错误信息"] = error_msg
        print(f"\n[Planner流程异常] {error_msg}")

    finally:
        # 关闭内部创建的HTTP会话
        if not http_session.closed and getattr(http_session, "_connector_owner", False):
            await http_session.close()
            print("\n[清理] HTTP会话已关闭")

    return result


# -------------------------- 示例调用 --------------------------
async def main():
    # 待处理的用户需求
    input_requirement = input("请输入需求：")

    # 执行规划器流程（流式）
    planner_result = await run_planner_flow(input_requirement)

    # 格式化打印最终结果（保留原有DAG展示）
    if planner_result["执行成功"]:
        print("\n\n=============================================")
        print("[Planner流程最终结果汇总]")
        print("=============================================\n")

        # 1. 打印输入需求
        print("--- 输入需求 ---")
        print(f"{planner_result['输入需求']}\n")

        # 2. 打印结构化需求（格式化JSON）
        print("--- 结构化需求 ---")
        print(json.dumps(planner_result["结构化需求"], ensure_ascii=False, indent=2))
        print()

        # 3. 打印经验搜索结果
        print("--- 过往经验搜索结果 ---")
        print(json.dumps(planner_result["经验搜索结果"], ensure_ascii=False, indent=2))
        print()

        # 4. 打印知识搜索结果
        print("--- 知识搜索结果 ---")
        print(json.dumps(planner_result["知识搜索结果"], ensure_ascii=False, indent=2))
        print()

        # 5. 打印任务分解与分配结果（DAG格式）
        print("--- 任务分解与分配结果 ---")
        tasks = planner_result["任务分配结果"]["tasks"]
        print(f"子任务数量: {len(tasks)}")
        print("子任务依赖流程图:\n")

        def print_task_node(task: dict, indent: str):
            print(f"{indent}任务 {task['task_id']}: {task['task_name']}")

        root_tasks = [t for t in tasks if not t.get("dependencies", [])]
        print_dag(
            nodes=tasks,
            node_id_key="task_id",
            dependencies_key="dependencies",
            print_node=print_task_node,
            indent_level=0,
            is_last_child=True,
        )
        print()

        # 6. 打印工具匹配结果（DAG格式）
        print("--- 工具匹配结果 ---")
        tool_matches = planner_result["工具匹配结果"]
        print(f"工具匹配项数量: {len(tool_matches)}\n")

        def print_tool_node(tool_match: dict, indent: str):
            print(f"{indent}任务 {tool_match['任务ID']}: {tool_match['任务名称']}")
            detail_indent = indent + "    "
            tool_data = tool_match["工具匹配详情"]

            # 打印匹配大类
            main_cats = tool_data.get("main_category", [])
            if main_cats:
                print(f"{detail_indent}匹配大类:")
                for i, cat in enumerate(main_cats):
                    cat_indent = detail_indent + (
                        "└── " if i == len(main_cats) - 1 else "├── "
                    )
                    print(
                        f"{cat_indent}名称: {cat['categoryName']} | 类型: {cat['type']}"
                    )
                    print(f"{cat_indent}匹配原因: {cat['reason']}")

            # 打印匹配子类
            sub_cats = tool_data.get("sub_category", [])
            if sub_cats:
                print(f"{detail_indent}匹配子类:")
                for i, cat in enumerate(sub_cats):
                    cat_indent = detail_indent + (
                        "└── " if i == len(sub_cats) - 1 else "├── "
                    )
                    print(
                        f"{cat_indent}名称: {cat['categoryName']} | 所属大类: {cat['main_category']}"
                    )
                    print(f"{cat_indent}匹配原因: {cat['reason']}")

            print(f"{detail_indent}匹配总结: {tool_data.get('reasons', '无')}")
            print(
                f"{detail_indent}状态: {'成功' if tool_data.get('success') else '失败'}\n"
            )

        root_tool_matches = [m for m in tool_matches if not m.get("依赖关系", [])]
        print_dag(
            nodes=root_tool_matches,
            node_id_key="任务ID",
            dependencies_key="依赖关系",
            print_node=print_tool_node,
            indent_level=0,
            is_last_child=True,
        )

    else:
        print("\n=============================================")
        print("[Planner流程执行失败]")
        print(f"失败原因: {planner_result['错误信息']}")
        print("=============================================\n")


if __name__ == "__main__":
    asyncio.run(main())
