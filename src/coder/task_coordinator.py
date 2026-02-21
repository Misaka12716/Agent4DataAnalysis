import os
import re
import json
from typing import TypedDict, List, Dict, Optional, Any
from concurrent.futures import ProcessPoolExecutor, as_completed
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# 导入现有Coder智能体的核心组件
from coder.task_coder import (  # 替换为你现有Coder代码的文件名（如coder_agent.py）
    build_code_agent,
    CodeAgentState,
    init_llm as init_coder_llm,
)

# 导入ipynb操作工具函数（与现有代码一致）
from utils.config import (
    OPENAI_COMPATIBLE_API_BASE,
    API_KEY,
    DEFAULT_MODEL,
)
from utils.ipynb_operations import (
    create_empty_ipynb,
    add_cell,
    write_ipynb,
    read_ipynb,
)


# ====================== 1. 定义总控程序的核心数据结构 ======================
class SubTask(TypedDict):
    """拆分后的子任务结构，明确输入输出、依赖关系"""

    task_id: str  # 子任务唯一ID（如t1、t2）
    task_desc: str  # 子任务描述
    input_var_name: str  # 子任务输入变量名
    input_var_desc: str  # 子任务输入变量说明
    output_var_name: str  # 子任务输出变量名
    output_var_desc: str  # 子任务输出变量说明
    dependencies: List[str]  # 依赖的前置子任务ID列表（无依赖则为空[]）


class CoordinatorState(TypedDict):
    """总控程序的状态管理，贯穿整个流程"""

    user_requirement: str  # 用户输入的原始数据分析需求
    sub_tasks: List[SubTask]  # 拆分后的子任务列表
    subtask_agent_results: Dict[
        str, CodeAgentState
    ]  # 子任务执行结果（key: task_id, value: 对应Coder的最终状态）
    parse_error: Optional[str]  # 需求拆分错误信息
    execute_error: Optional[str]  # 子任务执行错误信息
    final_ipynb_path: Optional[str]  # 最终汇总的ipynb文件路径
    max_workers: int  # 并行执行的最大进程数（默认CPU核心数）


# ====================== 2. 工具函数：LLM输出清理/代码提取/依赖排序/Import去重 ======================
def clean_llm_json_output(llm_output: str) -> str:
    """清理LLM输出的JSON，移除Markdown的```json/```分隔符，确保可解析"""
    if not llm_output:
        return ""
    # 移除markdown分隔符
    llm_output = re.sub(r"^```json\s*", "", llm_output, flags=re.MULTILINE)
    llm_output = re.sub(r"^```\s*", "", llm_output, flags=re.MULTILINE)
    llm_output = re.sub(r"\s*```$", "", llm_output)
    # 移除首尾空白
    return llm_output.strip()


def extract_core_code_from_ipynb(ipynb_path: str) -> str:
    """从Coder生成的final_ipynb中提取核心代码（import+函数+基础运行语句，无测试代码）"""
    if not os.path.exists(ipynb_path):
        raise FileNotFoundError(f"子任务IPYNB文件不存在：{ipynb_path}")
    nb = read_ipynb(ipynb_path)
    core_code = ""
    for cell in nb.cells:
        if cell.cell_type == "code":
            core_code = cell.source.strip()
            break
    return core_code


def sort_subtasks_by_dependency(sub_tasks: List[SubTask]) -> List[SubTask]:
    """按依赖关系对_subtasks进行拓扑排序，确保前置子任务始终在前（处理无循环依赖）"""
    # 构建依赖映射：key=task_id, value=依赖的task_id列表
    dep_map = {t["task_id"]: t["dependencies"] for t in sub_tasks}
    # 已排序的子任务
    sorted_tasks = []
    # 待处理的子任务（无未处理的依赖）
    pending_tasks = [t for t in sub_tasks if not t["dependencies"]]

    while pending_tasks:
        current = pending_tasks.pop(0)
        sorted_tasks.append(current)
        # 找到依赖当前任务的后续任务
        for task in sub_tasks:
            if task["task_id"] in [t["task_id"] for t in sorted_tasks]:
                continue
            # 移除已完成的依赖
            remaining_deps = [
                d
                for d in task["dependencies"]
                if d not in [t["task_id"] for t in sorted_tasks]
            ]
            if not remaining_deps:
                pending_tasks.append(task)

    # 检查是否所有子任务都已排序（防止循环依赖）
    if len(sorted_tasks) != len(sub_tasks):
        raise ValueError("子任务存在循环依赖，无法排序！请重新拆分需求")
    return sorted_tasks


def deduplicate_imports(code_str: str) -> str:
    """去重代码中的import语句，保留唯一的import/from...import，保持原有顺序"""
    lines = code_str.split("\n")
    import_lines = set()  # 存储已出现的import语句（去重）
    non_import_lines = []  # 存储非import语句
    import_prefixes = ("import ", "from ")

    for line in lines:
        stripped_line = line.strip()
        if stripped_line.startswith(import_prefixes) and stripped_line:
            import_lines.add(stripped_line)
        else:
            non_import_lines.append(line)

    # 重新拼接：去重的import + 原有非import语句
    deduped_imports = "\n".join(
        sorted(import_lines, key=lambda x: lines.index(x.strip()))
    )
    final_code = f"{deduped_imports}\n\n" + "\n".join(non_import_lines)
    # 移除多余空行
    final_code = re.sub(r"\n{3,}", "\n\n", final_code).strip()
    return final_code


# ====================== 3. 总控核心函数：需求拆分/子任务初始化/并行执行/代码汇总 ======================
def init_coordinator_llm():
    """初始化总控LLM（用于需求拆分，用通用大模型即可）"""
    return ChatOpenAI(
        model=DEFAULT_MODEL,
        temperature=0.1,  # 低温度保证拆分结果稳定
        api_key=API_KEY,
        base_url=OPENAI_COMPATIBLE_API_BASE,
    )


def parse_user_requirement(state: CoordinatorState) -> CoordinatorState:
    """
    核心步骤1：解析用户数据分析需求，拆分为带输入输出/依赖的子任务
    调用LLM严格按SubTask格式输出JSON，确保子任务信息完整
    """
    llm = init_coordinator_llm()
    user_req = state["user_requirement"]

    # 严格约束的Prompt：修复大括号转义问题，示例JSON用双大括号转义
    parse_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """
你是专业的数据分析任务拆分工程师，擅长将用户的整体数据分析需求拆分为**可独立执行、输入输出明确、依赖关系清晰**的子任务，严格遵循以下规则：
1. 拆分原则：
   - 每个子任务仅完成一个核心操作（如读取数据、数据清洗、统计分析、绘图、保存结果等）；
   - 子任务数量适中（3-8个为宜），避免过粗或过细；
   - 明确子任务间的依赖关系（如“保存结果”依赖“统计分析”完成），无依赖的子任务尽量独立。
2. 数据分析任务的常见拆分方向（参考）：
   - 数据读取（如读取csv/excel/json）；
   - 数据预处理（如缺失值处理、异常值剔除、类型转换）；
   - 探索性分析（如描述性统计、相关性分析）；
   - 建模分析（如聚类、回归、分类）；
   - 可视化绘图（如折线图、柱状图、热力图）；
   - 结果保存（如保存为excel、导出图片、生成报告）。
3. 输出格式要求：
   - 仅返回**纯JSON字符串**，不添加任何解释、Markdown格式、分隔符；
   - JSON根节点是数组，每个元素是一个子任务，包含以下字段（字段名严格一致，不可缺失）：
     - task_id：字符串，子任务唯一ID，如t1、t2、t3；
     - task_desc：字符串，子任务的详细描述；
     - input_var_name：字符串，子任务的输入变量名（如file_path、raw_df、cleaned_df）；
     - input_var_desc：字符串，输入变量的类型/格式说明（如excel文件路径str、原始数据DataFrame、清洗后的DataFrame）；
     - output_var_name：字符串，子任务的输出变量名（如raw_df、cleaned_df、cluster_result、score_plot）；
     - output_var_desc：字符串，输出变量的类型/格式说明；
     - dependencies：数组，依赖的前置子任务ID列表，无依赖则为空数组[]。
4. 变量命名规范：
   - 变量名使用小写+下划线（如raw_df、score_cluster、sales_plot）；
   - 相同含义的变量在子任务间保持一致（如前一个子任务输出raw_df，后一个子任务直接输入raw_df）。

示例输出（JSON）：
[
  {{
    "task_id": "t1",
    "task_desc": "读取指定路径的excel格式数据文件，加载为pandas的DataFrame",
    "input_var_name": "file_path",
    "input_var_desc": "excel数据文件的绝对/相对路径，字符串类型",
    "output_var_name": "raw_df",
    "output_var_desc": "加载后的原始数据DataFrame，包含所有列和行",
    "dependencies": []
  }},
  {{
    "task_id": "t2",
    "task_desc": "对原始数据进行预处理，筛选出成绩列，处理缺失值和异常值",
    "input_var_name": "raw_df",
    "input_var_desc": "原始数据DataFrame，来自子任务t1的输出",
    "output_var_name": "cleaned_score_df",
    "output_var_desc": "仅包含成绩列的清洗后DataFrame，无缺失值和异常值",
    "dependencies": ["t1"]
  }},
  {{
    "task_id": "t3",
    "task_desc": "对清洗后的成绩数据进行聚类分析，生成聚类结果和类别标签",
    "input_var_name": "cleaned_score_df",
    "input_var_desc": "清洗后的成绩列DataFrame，来自子任务t2的输出",
    "output_var_name": "score_cluster_result",
    "output_var_desc": "聚类结果，包含数据和对应的类别标签的DataFrame",
    "dependencies": ["t2"]
  }}
]
                """,
            ),
            ("user", f"请拆分以下数据分析需求为符合规则的子任务：{user_req}"),
        ]
    )

    try:
        # 调用LLM并解析结果
        chain = parse_prompt | llm | StrOutputParser()
        llm_raw_output = chain.invoke({"user_req": user_req})
        clean_json = clean_llm_json_output(llm_raw_output)
        sub_tasks = json.loads(clean_json)
        # 验证子任务格式（简单校验，确保字段存在）
        required_fields = [
            "task_id",
            "task_desc",
            "input_var_name",
            "input_var_desc",
            "output_var_name",
            "output_var_desc",
            "dependencies",
        ]
        for task in sub_tasks:
            if not all(f in task for f in required_fields):
                raise ValueError(f"子任务字段缺失：{task}")
        state["sub_tasks"] = sub_tasks
        state["parse_error"] = None
        print(f"✅ 需求拆分完成，共生成{len(sub_tasks)}个子任务")
        for t in sub_tasks:
            print(f"  - {t['task_id']}：{t['task_desc']}（依赖：{t['dependencies']}）")
    except Exception as e:
        error_msg = f"需求拆分失败：{str(e)}"
        state["parse_error"] = error_msg
        state["sub_tasks"] = []
        print(f"❌ {error_msg}")
    return state


def init_subtask_agent_state(sub_task: SubTask) -> CodeAgentState:
    """
    核心步骤2：将拆分后的SubTask转换为现有Coder智能体能接受的CodeAgentState
    生成唯一的ipynb路径，初始化修正次数等参数
    """
    task_id = sub_task["task_id"]
    # 生成唯一的ipynb路径（按task_id区分，避免并行执行时文件覆盖）
    ipynb_path = f"subtask_{task_id}_{hash(sub_task['task_desc'])}.ipynb"
    # 构造Coder智能体的状态（与现有代码完全兼容）
    agent_state: CodeAgentState = {
        "task_desc": sub_task["task_desc"],
        "input_var_name": sub_task["input_var_name"],
        "input_var_desc": sub_task["input_var_desc"],
        "output_var_name": sub_task["output_var_name"],
        "output_var_desc": sub_task["output_var_desc"],
        "ipynb_path": ipynb_path,
        "generated_code": None,
        "execution_error": None,
        "correction_count": 0,
        "max_corrections": 3,  # 与现有Coder保持一致的最大修正次数
        "final_ipynb_path": None,
    }
    return agent_state


def run_single_subtask(sub_task: SubTask) -> (str, CodeAgentState):
    """
    单个子任务执行函数（供多进程调用）
    输入：子任务SubTask
    输出：(task_id, Coder智能体的最终执行状态)
    """
    task_id = sub_task["task_id"]
    try:
        # 初始化Coder智能体和子任务状态
        code_agent = build_code_agent()
        agent_state = init_subtask_agent_state(sub_task)
        # 运行Coder智能体（与现有测试逻辑一致）
        print(f"🚀 启动子任务{task_id}的Coder智能体...")
        final_agent_state = code_agent.invoke(agent_state)
        print(
            f"🎉 子任务{task_id}执行完成，最终文件：{final_agent_state['final_ipynb_path']}"
        )
        return task_id, final_agent_state
    except Exception as e:
        # 子任务执行失败，构造错误状态
        error_state = init_subtask_agent_state(sub_task)
        error_state["execution_error"] = f"子任务执行异常：{str(e)}"
        print(f"❌ 子任务{task_id}执行失败：{str(e)}")
        return task_id, error_state


def execute_subtasks_parallel(state: CoordinatorState) -> CoordinatorState:
    """
    核心步骤3：多进程并行执行所有子任务
    无依赖的子任务完全并行，利用ProcessPoolExecutor实现进程隔离
    """
    sub_tasks = state["sub_tasks"]
    if not sub_tasks:
        state["execute_error"] = "无可用子任务，跳过执行"
        return state

    subtask_agent_results = {}
    max_workers = state.get("max_workers", os.cpu_count())  # 默认使用CPU核心数
    print(f"\n🚀 启动并行执行，最大进程数：{max_workers}")

    try:
        # 多进程并行执行子任务
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有子任务到进程池
            future_to_task = {
                executor.submit(run_single_subtask, t): t for t in sub_tasks
            }
            # 获取执行结果
            for future in as_completed(future_to_task):
                task_id, final_agent_state = future.result()
                subtask_agent_results[task_id] = final_agent_state

        state["subtask_agent_results"] = subtask_agent_results
        state["execute_error"] = None
        # 检查是否有子任务执行失败
        failed_tasks = [
            tid
            for tid, s in subtask_agent_results.items()
            if s["execution_error"] is not None
        ]
        if failed_tasks:
            print(
                f"⚠️  以下子任务执行失败（已达最大修正次数）：{','.join(failed_tasks)}"
            )
        else:
            print("✅ 所有子任务并行执行完成！")
    except Exception as e:
        error_msg = f"子任务并行执行失败：{str(e)}"
        state["execute_error"] = error_msg
        state["subtask_agent_results"] = {}
        print(f"❌ {error_msg}")
    return state


def summarize_codes_to_final_ipynb(state: CoordinatorState) -> CoordinatorState:
    """
    核心步骤4：汇总所有子任务的核心代码，生成最终的整合版ipynb
    关键操作：按依赖排序、去重import、自动传递子任务间的输入输出变量
    """
    user_req = state["user_requirement"]
    sub_tasks = state["sub_tasks"]
    subtask_results = state["subtask_agent_results"]

    if not sub_tasks or not subtask_results:
        state["final_ipynb_path"] = None
        state["execute_error"] = "无可用子任务结果，无法汇总"
        return state

    try:
        # 1. 按依赖关系排序子任务，确保前置子任务在前
        sorted_subtasks = sort_subtasks_by_dependency(sub_tasks)
        # 2. 提取每个子任务的核心代码，并按排序存储
        task_code_map = {}
        for task in sorted_subtasks:
            task_id = task["task_id"]
            agent_state = subtask_results[task_id]
            if not agent_state["final_ipynb_path"]:
                raise ValueError(f"子任务{task_id}无最终IPYNB文件，跳过汇总")
            core_code = extract_core_code_from_ipynb(agent_state["final_ipynb_path"])
            task_code_map[task_id] = (task, core_code)

        # 3. 拼接所有核心代码，按排序顺序
        all_code = ""
        var_assingment = ""  # 子任务间的变量传递语句（核心）
        for task in sorted_subtasks:
            task_id = task["task_id"]
            task_info, core_code = task_code_map[task_id]
            all_code += (
                f"\n# ========== 子任务{task_id}：{task_info['task_desc']} ==========\n"
            )
            all_code += core_code + "\n"
            # 构建变量传递语句：前置子任务的输出 → 后续子任务的输入
            # 如：cleaned_df = preprocess_data(raw_df)
            in_var = task_info["input_var_name"]
            out_var = task_info["output_var_name"]
            # 提取子任务的函数名（从核心代码中匹配def语句）
            func_name = re.search(r"def\s+(\w+)\(", core_code)
            if func_name:
                func_name = func_name.group(1)
                # 变量传递语句：输出变量 = 函数名(输入变量)
                var_assingment += (
                    f"\n# 执行子任务{task_id}\n{out_var} = {func_name}({in_var})\n"
                )

        # 4. 去重import语句，优化代码结构
        final_code = deduplicate_imports(all_code)
        # 5. 添加**全局初始化语句**和**变量传递执行语句**（数据分析专属）
        # 全局初始化：如文件路径、图片保存路径等（让用户可直接修改）
        global_init = f"""
# ==============================================
# 数据分析总任务：{user_req}
# 自动生成的整合代码 - 所有子任务已按依赖关系排序
# 说明：请修改以下全局变量为实际值，然后直接运行整个单元格
# ==============================================
# 全局初始化变量（根据子任务需求修改）
file_path = "your_data_file.csv"  # 数据文件路径
save_path = "analysis_result.xlsx"  # 结果保存路径
plot_save_path = "analysis_plot.png"  # 图片保存路径
"""
        # 拼接最终完整代码：全局初始化 → 核心函数 → 变量传递执行
        complete_code = global_init + final_code + "\n" + var_assingment
        # 6. 创建最终的ipynb文件并写入代码
        final_ipynb_path = f"final_analysis_{hash(user_req)}.ipynb"
        nb = create_empty_ipynb()
        add_cell(nb, "code", complete_code.strip())
        write_ipynb(nb, final_ipynb_path, overwrite=True)

        state["final_ipynb_path"] = final_ipynb_path
        print(f"\n✅ 最终整合版IPYNB已生成：{final_ipynb_path}")
        print(
            f"💡 说明：文件中已自动完成子任务间的变量传递，修改全局初始化变量后可直接运行！"
        )
    except Exception as e:
        error_msg = f"代码汇总失败：{str(e)}"
        state["final_ipynb_path"] = None
        print(f"❌ {error_msg}")
    return state


# ====================== 4. 总控程序主入口 ======================
def run_data_analysis_coordinator(max_workers: int = None) -> None:
    """
    数据分析任务总控程序主函数
    流程：读取用户输入 → 拆分子任务 → 并行执行 → 汇总代码 → 生成最终ipynb
    :param max_workers: 并行执行的最大进程数，默认CPU核心数
    """
    # 1. 读取用户输入的数据分析需求
    print("=" * 50)
    print("📊 数据分析任务总控程序 v1.0")
    print("=" * 50)
    user_requirement = input("请输入你的数据分析需求（详细描述）：\n")
    if not user_requirement.strip():
        print("❌ 需求不能为空！")
        return

    # 2. 初始化总控状态
    init_state: CoordinatorState = {
        "user_requirement": user_requirement,
        "sub_tasks": [],
        "subtask_agent_results": {},
        "parse_error": None,
        "execute_error": None,
        "final_ipynb_path": None,
        "max_workers": max_workers or os.cpu_count(),
    }

    # 3. 执行总控全流程
    state = parse_user_requirement(init_state)  # 拆分需求
    if state["parse_error"]:
        return
    state = execute_subtasks_parallel(state)  # 并行执行子任务
    if state["execute_error"] and not state["subtask_agent_results"]:
        return
    state = summarize_codes_to_final_ipynb(state)  # 汇总代码生成最终ipynb

    # 4. 输出最终结果
    print("\n" + "=" * 50)
    if state["final_ipynb_path"]:
        print(f"🎉 总控程序运行完成！")
        print(f"📁 最终整合版IPYNB文件：{state['final_ipynb_path']}")
        print(
            f"💡 下一步操作：打开该文件，修改全局初始化变量（如file_path），直接运行整个代码单元格即可完成所有数据分析！"
        )
    else:
        print(f"❌ 总控程序运行失败，原因：{state['execute_error'] or '未知错误'}")
    print("=" * 50)


# ====================== 5. 测试运行 ======================
if __name__ == "__main__":
    # 运行总控程序（可指定max_workers，如max_workers=4）
    run_data_analysis_coordinator()
