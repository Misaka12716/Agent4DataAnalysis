# run_planner.py
import asyncio
from aiohttp import ClientSession
from knowledge.knowledge_base import KnowledgeBase
from planner.multi_agent_planner import MultiAgentPlanner
from typing import Dict, Any, Optional, Callable


# -------------------------- 通用工具函数：DAG结构打印 --------------------------
def print_dag(
    nodes: list,
    node_id_key: str,
    dependencies_key: str,
    print_node: Callable,
    indent_level: int = 0,
    is_last_child: bool = False,
):
    """
    通用递归DAG打印函数（统一抽象任务和工具匹配的展示）

    :param nodes: 待打印的当前层级节点列表
    :param node_id_key: 节点字典中用于标识节点ID的键名（如"task_id"）
    :param dependencies_key: 节点字典中用于标识依赖关系的键名（如"dependencies"）
    :param print_node: 打印节点详情的回调函数（处理差异化内容）
    :param indent_level: 当前缩进级别
    :param is_last_child: 当前节点是否为父节点的最后一个子节点
    """
    for i, node in enumerate(nodes):
        # 判断当前节点是否为当前层级的最后一个节点
        is_last = i == len(nodes) - 1

        # 构建基础缩进（控制层级展示）
        indent = ""
        if indent_level > 0:
            for _ in range(indent_level - 1):
                indent += "│   "  # 上层节点的竖线占位
            indent += "└── " if is_last else "├── "  # 当前节点的分支线

        # 调用回调函数打印节点详情
        print_node(node, indent)

        # 查找子节点（依赖当前节点的节点）
        child_nodes = [
            n for n in nodes if node[node_id_key] in n.get(dependencies_key, [])
        ]

        # 递归打印子节点，缩进级别+1
        if child_nodes:
            print_dag(
                nodes=child_nodes,
                node_id_key=node_id_key,
                dependencies_key=dependencies_key,
                print_node=print_node,
                indent_level=indent_level + 1,
                is_last_child=is_last,
            )


# -------------------------- 核心流程：规划器执行流程封装 --------------------------
async def run_planner_flow(
    input_requirement: str,
    http_session: Optional[ClientSession] = None,
    knowledge_base: Optional[KnowledgeBase] = None,
) -> Dict[str, Any]:
    """
    封装MultiAgentPlanner的完整执行流程

    :param input_requirement: 待处理的用户需求
    :param http_session: 可选，预创建的HTTP会话（外部传入可复用）
    :param knowledge_base: 可选，预初始化的知识库实例（外部传入可复用）
    :return: 包含所有步骤结果的字典
    """
    # 初始化结果结构，存储整个流程数据
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

    # 处理可选参数（未提供则创建默认实例）
    if not http_session:
        http_session = ClientSession()
    if not knowledge_base:
        knowledge_base = KnowledgeBase()

    try:
        # 1. 创建规划器实例（注入依赖）
        planner = MultiAgentPlanner(
            knowledge_base=knowledge_base, http_session=http_session
        )

        # 2. 需求结构化处理
        structured_req = await planner.organize_requirement(input_requirement)
        if "error" in structured_req:
            raise ValueError(f"需求结构化失败：{structured_req['error']}")
        result["结构化需求"] = structured_req

        # 3. 历史经验搜索
        experience_results = await planner.search_experience(structured_req)
        result["经验搜索结果"] = experience_results

        # 4. 知识库检索
        knowledge_results = await planner.search_knowledge(structured_req)
        result["知识搜索结果"] = knowledge_results

        # 5. 任务分解与分配
        task_assignment = await planner.assign_tasks(
            requirement=structured_req,
            knowledge=knowledge_results,
            experience=experience_results,
        )
        if not task_assignment["success"]:
            raise ValueError(f"任务分解失败：{task_assignment['error']}")
        result["任务分配结果"] = task_assignment

        # 6. 子任务工具匹配
        tool_matching_results = []
        for task in task_assignment["tasks"]:
            tool_matching = await planner.find_tools(task)
            tool_matching_results.append(
                {
                    "任务ID": task["task_id"],
                    "任务名称": task["task_name"],
                    "工具匹配详情": tool_matching,
                    "依赖关系": task.get("dependencies", []),  # 继承任务依赖
                }
            )
        result["工具匹配结果"] = tool_matching_results

        # 标记流程执行成功
        result["执行成功"] = True

    except Exception as e:
        # 捕获所有异常并记录
        error_msg = str(e)
        result["错误信息"] = error_msg
        print(f"[Planner流程异常] {error_msg}")

    finally:
        # 关闭内部创建的HTTP会话（外部传入的由调用方管理）
        if not http_session.closed and not http_session._connector_owner:
            await http_session.close()

    return result


# -------------------------- 示例调用 --------------------------
async def main():
    # 待处理的用户需求
    input_requirement = (
        "分析2024年第一季度用户消费数据，生成可视化报告并提取关键消费趋势"
    )

    # 执行规划器流程
    planner_result = await run_planner_flow(input_requirement)

    # 格式化打印执行结果
    if planner_result["执行成功"]:
        print("\n=============================================")
        print("[Planner流程执行成功]")
        print("=============================================\n")

        # 1. 打印输入需求
        print("--- 输入需求 ---")
        print(f"{planner_result['输入需求']}\n")

        # 2. 打印结构化需求
        print("--- 结构化需求 ---")
        print(f"{planner_result['结构化需求']}\n")

        # 3. 打印经验搜索结果
        print("--- 过往经验搜索结果 ---")
        print(f"{planner_result['经验搜索结果']}\n")

        # 4. 打印知识搜索结果
        print("--- 知识搜索结果 ---")
        print(f"{planner_result['知识搜索结果']}\n")

        # 5. 打印任务分解与分配结果（DAG格式）
        print("--- 任务分解与分配结果 ---")
        tasks = planner_result["任务分配结果"]["tasks"]
        print(f"子任务数量: {len(tasks)}")
        print("子任务依赖流程图:\n")

        # 定义任务节点打印回调
        def print_task_node(task: dict, indent: str):
            print(f"{indent}任务 {task['task_id']}: {task['task_name']}")

        # 获取根任务（无依赖的任务）
        root_tasks = [t for t in tasks if not t.get("dependencies", [])]

        # 打印任务DAG
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

        # 定义工具匹配节点打印回调
        def print_tool_node(tool_match: dict, indent: str):
            # 打印任务基础信息
            print(f"{indent}任务 {tool_match['任务ID']}: {tool_match['任务名称']}")

            # 详情缩进（比节点缩进深一级）
            detail_indent = indent + "    "

            # 获取工具匹配数据
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
                        f"{cat_indent}名称: {cat['categoryName']} | 所属大类: {cat['main_category']} | 类型: {cat['type']}"
                    )
                    print(f"{cat_indent}匹配原因: {cat['reason']}")

            # 打印具体匹配工具
            tools = tool_data.get("matched_tools", [])
            print(f"{detail_indent}具体匹配工具:")
            if tools:
                for i, tool in enumerate(tools):
                    tool_indent = detail_indent + (
                        "└── " if i == len(tools) - 1 else "├── "
                    )
                    print(f"{tool_indent}{tool}")
            else:
                print(f"{detail_indent}└── 无")

            # 打印匹配总结与状态
            print(f"{detail_indent}匹配总结: {tool_data.get('reasons', '无总结')}")
            print(
                f"{detail_indent}状态: {'成功' if tool_data.get('success') else '失败'}\n"
            )

        # 获取根工具匹配项（对应根任务）
        root_tool_matches = [m for m in tool_matches if not m.get("依赖关系", [])]

        # 打印工具匹配DAG
        print_dag(
            nodes=tool_matches,
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
