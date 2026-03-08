import os
import re
import json
from typing import TypedDict, Optional, List
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from utils.config import (
    OPENAI_COMPATIBLE_API_BASE,
    API_KEY,
    DEFAULT_MODEL,
    DEFAULT_CODER_MODEL,
)
from utils.code_operations import extract_code_components

# 导入之前的ipynb操作函数（确保ipynb_operations.py在同级目录）
from utils.ipynb_operations import (
    create_empty_ipynb,
    add_cell,
    write_ipynb,
    run_ipynb,
    read_ipynb,
    delete_cell,
    modify_cell,
)


# ====================== 工具函数：清理代码中的Markdown格式 ======================
def clean_code_from_markdown(code_str: str) -> str:
    """
    清理代码字符串中的Markdown格式，移除```python/```分隔符和多余的空白
    """
    if not code_str:
        return ""

    # 移除```python开头和```结尾
    code_str = re.sub(r"^```python\s*", "", code_str, flags=re.MULTILINE)
    code_str = re.sub(r"^```\s*", "", code_str, flags=re.MULTILINE)
    code_str = re.sub(r"\s*```$", "", code_str)

    # 移除首尾空白行
    code_str = code_str.strip()

    # 移除行首的多余空格（保持代码缩进）
    lines = code_str.split("\n")
    if lines:
        # 找到最小缩进量（排除空行）
        min_indent = float("inf")
        for line in lines:
            stripped = line.lstrip()
            if stripped:
                indent = len(line) - len(stripped)
                min_indent = min(min_indent, indent)
        # 移除最小缩进（如果有）
        if min_indent > 0 and min_indent < float("inf"):
            lines = [line[min_indent:] if line.strip() else line for line in lines]
        code_str = "\n".join(lines)

    return code_str


# ====================== 1. 定义智能体状态（核心） ======================
class CodeAgentState(TypedDict):
    """智能体状态定义，包含所有流程中需要传递的信息"""

    # 任务基本信息（用户输入）
    task_desc: str  # 任务描述（要干什么）
    input_var_name: str  # 输入变量名
    input_var_desc: str  # 输入变量说明
    output_var_name: str  # 输出变量名
    output_var_desc: str  # 输出变量说明
    # 生成/执行相关
    ipynb_path: str  # 生成的ipynb文件路径
    generated_code: Optional[str]  # 生成的纯代码内容（替代原JSON）
    execution_error: Optional[str]  # 执行错误信息
    correction_count: int  # 自修正次数（防止无限循环）
    max_corrections: int  # 最大修正次数（默认3次）
    # 最终结果
    final_ipynb_path: Optional[str]  # 整理后的最终ipynb路径


# ====================== 2. 核心节点函数定义 ======================
def init_llm():
    """初始化LLM模型（使用qwen3-coder，保证代码生成质量）"""
    return ChatOpenAI(
        model=DEFAULT_CODER_MODEL,
        temperature=0.1,
        api_key=API_KEY,
        base_url=OPENAI_COMPATIBLE_API_BASE,  # 低温度保证代码准确性
    )


def generate_ipynb_node(state: CodeAgentState) -> CodeAgentState:
    """
    节点1：直接生成纯代码，创建单格.ipynb文件
    该单元格包含：import相关库 + 封装好的函数 + demo测试代码
    """
    llm = init_llm()

    # 直接生成纯代码
    code_prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """
你是专业的Python程序员，需要根据任务要求生成规范的Python代码，严格遵循以下规则：
1. 代码包含且仅包含以下三部分内容（按顺序整合在一个代码块中）：
   - 第一部分：仅包含必要的import语句（不要多余库）；
   - 第二部分：封装一个完整的函数，函数名清晰（如task_xxx），严格使用指定的输入/输出变量名：
     - 输入变量名：{input_var_name}（说明：{input_var_desc}）
     - 输出变量名：{output_var_name}（说明：{output_var_desc}）
     函数必须有清晰的文档字符串（说明功能、参数、返回值），参数和返回值严格匹配指定变量名；
   - 第三部分：demo测试代码，创建测试输入（符合{input_var_name}的类型/格式），调用函数，打印{output_var_name}，验证函数正确性；
2. 代码必须可运行，无语法错误，符合Python最佳实践；
3. 不要添加任何多余内容（如markdown、注释说明、分隔符等），仅返回纯Python代码；
4. 确保代码缩进正确，格式规范。
            """,
            ),
            ("user", "任务要求：{task_desc}"),
        ]
    )

    # 构建链并生成纯代码
    chain = code_prompt | llm | StrOutputParser()
    raw_code = chain.invoke(
        {
            "task_desc": state["task_desc"],
            "input_var_name": state["input_var_name"],
            "input_var_desc": state["input_var_desc"],
            "output_var_name": state["output_var_name"],
            "output_var_desc": state["output_var_desc"],
        }
    )

    # 清理代码中的Markdown格式
    code_content = clean_code_from_markdown(raw_code)

    # 创建ipynb文件路径
    ipynb_path = f"generated_task_{hash(state['task_desc'])}.ipynb"

    # 直接创建空Notebook并添加单单元格代码
    nb = create_empty_ipynb()
    add_cell(nb, "code", code_content.strip())
    write_ipynb(nb, ipynb_path, overwrite=True)

    # 更新状态
    state["generated_code"] = code_content  # 存储生成的纯代码
    print(f"✅ 已生成.ipynb文件：{ipynb_path}")
    state["ipynb_path"] = ipynb_path
    state["execution_error"] = None  # 重置错误
    return state


def execute_ipynb_node(state: CodeAgentState) -> CodeAgentState:
    """
    节点2：运行生成的.ipynb文件，捕获执行错误
    """
    ipynb_path = state["ipynb_path"]
    try:
        # 运行ipynb，捕获执行结果
        run_ipynb(ipynb_path)
        state["execution_error"] = None  # 无错误
    except Exception as e:
        # 捕获所有执行错误，保存详细信息
        error_msg = f"执行错误：{str(e)}"
        state["execution_error"] = error_msg
        print(f"❌ {error_msg}")
    return state


def check_execution_node(state: CodeAgentState) -> str:
    """
    节点3：检查执行结果，决定下一步流程
    返回："correct"（修正） / "finalize"（整理最终文件）
    """
    # 有错误且未达最大修正次数 → 修正
    if (
        state["execution_error"] is not None
        and state["correction_count"] < state["max_corrections"]
    ):
        return "correct"
    # 无错误 或 达到最大修正次数 → 整理最终文件
    else:
        return "finalize"


def correct_ipynb_node(state: CodeAgentState) -> CodeAgentState:
    """
    节点4：自修正节点 → 根据执行错误修正.ipynb代码（单单元格）
    """
    llm = init_llm()
    ipynb_path = state["ipynb_path"]
    error_msg = state["execution_error"]

    # 读取当前ipynb的内容（单单元格）
    nb = read_ipynb(ipynb_path)
    # 提取现有代码（单格）
    existing_code = ""
    for cell in nb.cells:
        if cell.cell_type == "code":
            existing_code = cell.source
            break

    # 构建修正Prompt（专注于纯代码修正）
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """
你是专业的Python调试工程师，需要修正以下代码的错误：
1. 现有代码（包含import、函数、测试三部分，整合在一个代码块中）：
{existing_code}
2. 执行错误信息：
{error_msg}
3. 修正规则：
   - 仅修正错误，不改变原有的输入/输出变量名和核心逻辑；
   - 保持代码结构：仍包含import、函数、测试三部分（整合在一个代码块中）；
   - 修正后代码必须可运行，缩进正确；
   - 仅返回修正后的完整纯Python代码，不要其他解释。
        """,
            ),
            ("user", "请修正上述代码错误"),
        ]
    )

    # 调用LLM生成修正后的代码
    chain = prompt | llm | StrOutputParser()
    corrected_raw_code = chain.invoke(
        {"existing_code": existing_code, "error_msg": error_msg}
    )

    corrected_code = clean_code_from_markdown(corrected_raw_code)

    # 将修正后的代码写入ipynb（更新单单元格）
    if nb.cells:
        nb.cells[0].source = corrected_code.strip()  # 更新唯一的代码单元格
        # 保存修正后的文件
        write_ipynb(nb, ipynb_path, overwrite=True)
        print(f"✅ 已完成第{state['correction_count']+1}次修正")

    # 更新状态：修正次数+1
    state["correction_count"] += 1
    state["generated_code"] = corrected_code  # 更新为修正后的代码
    return state


def finalize_ipynb_node(state: CodeAgentState) -> CodeAgentState:
    """
    节点5：整理最终.ipynb文件（适配单单元格）
    步骤：
    1. 从单单元格中删除测试代码部分
    2. 向该单元格添加函数运行语句（使用指定的输入/输出变量名）
    3. 保存最终文件
    """
    ipynb_path = state["ipynb_path"]
    nb = read_ipynb(ipynb_path)

    # 步骤1&2：处理单单元格内容（删除测试代码，添加运行语句）
    if nb.cells and nb.cells[0].cell_type == "code":
        code_cell = nb.cells[0]
        code_lines = code_cell.source.split("\n")

        # 分离import、函数、测试部分（找到测试代码的起始位置）
        func_end_idx = -1
        test_start_idx = len(code_lines)
        # 1. 先找函数定义的结束位置（找最后一个def/函数体结束）
        in_func = False
        indent_level = 0
        for i, line in enumerate(code_lines):
            stripped_line = line.strip()
            if stripped_line.startswith("def "):
                in_func = True
                indent_level = len(line) - len(stripped_line)
            if in_func:
                # 函数体结束判断（缩进小于函数定义的缩进）
                if (
                    len(line) - len(stripped_line) < indent_level
                    and stripped_line != ""
                ):
                    func_end_idx = i
                    in_func = False
        # 2. 测试代码通常在函数之后，取函数结束后的内容作为测试代码
        if func_end_idx != -1:
            test_start_idx = func_end_idx + 1

        # 保留import + 函数部分，删除测试部分
        core_code_lines = code_lines[:test_start_idx]
        core_code = "\n".join([line for line in core_code_lines if line.strip()])

        # 提取函数名
        func_name = None
        for line in core_code_lines:
            if line.strip().startswith("def "):
                func_name = line.strip().split("def ")[1].split("(")[0]
                break

        # 添加函数运行语句
        if func_name:
            input_var = state["input_var_name"]
            output_var = state["output_var_name"]
            run_statement = f"""
# 运行函数
{output_var} = {func_name}({input_var})
print(f"输出结果（{output_var}）：{{{output_var}}}")
"""
            core_code += run_statement

        # 更新单元格内容
        code_cell.source = core_code
        modify_cell(nb, 0, code_cell.source)

    # 步骤3：保存最终文件
    final_path = f"final_{os.path.basename(ipynb_path)}"
    write_ipynb(nb, final_path, overwrite=True)
    state["final_ipynb_path"] = final_path
    print(f"✅ 最终.ipynb文件已保存：{final_path}")
    return state


# ====================== 3. 构建LangGraph智能体 ======================
def build_code_agent():
    """构建并返回LangGraph智能体"""
    # 初始化状态图
    graph = StateGraph(CodeAgentState)

    # 添加节点
    graph.add_node("generate", generate_ipynb_node)
    graph.add_node("execute", execute_ipynb_node)
    graph.add_node("correct", correct_ipynb_node)
    graph.add_node("finalize", finalize_ipynb_node)

    # 添加边（定义流程）
    # 生成 → 执行
    graph.add_edge("generate", "execute")
    # 执行 → 检查（条件边）
    graph.add_conditional_edges(
        "execute",
        check_execution_node,
        {
            "correct": "correct",  # 修正 → 修正节点
            "finalize": "finalize",  # 整理 → 整理节点
        },
    )
    # 修正 → 执行（修正后重新运行）
    graph.add_edge("correct", "execute")
    # 整理 → 结束
    graph.add_edge("finalize", END)

    # 设置入口节点
    graph.set_entry_point("generate")

    # 编译图
    agent = graph.compile()
    return agent


# ====================== 4. 测试示例 ======================
if __name__ == "__main__":
    # 初始化智能体
    code_agent = build_code_agent()

    # 定义测试任务（可替换为任意任务）
    test_task = {
        "task_desc": "编写一个函数，计算输入列表中所有数字的平均值",
        "input_var_name": "num_list",
        "input_var_desc": "包含数字的列表（如[1,2,3,4]）",
        "output_var_name": "avg_value",
        "output_var_desc": "列表中数字的平均值（浮点数）",
        "ipynb_path": "",  # 初始为空，由生成节点填充
        "generated_code": None,  # 存储生成的纯代码
        "execution_error": None,
        "correction_count": 0,  # 初始修正次数
        "max_corrections": 3,  # 最大修正3次
        "final_ipynb_path": None,
    }

    # 运行智能体
    print("🚀 启动代码生成智能体...")
    result = code_agent.invoke(test_task)

    # 输出最终结果
    print(f"\n🎉 智能体运行完成！")
    print(f"最终.ipynb文件路径：{result['final_ipynb_path']}")
    if result["execution_error"] is not None:
        print(
            f"⚠️  注意：达到最大修正次数，最终文件可能仍有错误：{result['execution_error']}"
        )
