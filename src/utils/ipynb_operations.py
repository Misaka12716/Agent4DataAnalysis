import json
import os
import nbformat
from nbclient import NotebookClient
from nbclient.exceptions import CellExecutionError
from nbformat import v4 as nbv4
from utils.config import IPYNB_KERNEL_NAME, IPYNB_KERNEL_DISPLAY_NAME


def read_ipynb(file_path: str) -> nbformat.NotebookNode:
    """
    读取.ipynb文件，返回结构化的Notebook对象
    :param file_path: ipynb文件的路径（绝对/相对）
    :return: NotebookNode对象（可像字典一样操作）
    :raises: FileNotFoundError, json.JSONDecodeError, nbformat.ValidationError
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        # 使用nbformat读取（自动处理不同版本的ipynb文件）
        nb = nbformat.read(f, as_version=4)  # 统一转换为v4版本
    return nb


def write_ipynb(
    nb: nbformat.NotebookNode, save_path: str, overwrite: bool = False
) -> None:
    """
    将Notebook对象写入/保存为.ipynb文件
    :param nb: NotebookNode对象（由read_ipynb或创建的空对象）
    :param save_path: 保存路径（绝对/相对）
    :param overwrite: 是否覆盖已存在的文件（默认False）
    :raises: FileExistsError, PermissionError
    """
    if os.path.exists(save_path) and not overwrite:
        raise FileExistsError(f"文件已存在，若需覆盖请设置overwrite=True: {save_path}")

    # 确保保存目录存在
    save_dir = os.path.dirname(save_path)
    if save_dir and not os.path.exists(save_dir):
        os.makedirs(save_dir)

    with open(save_path, "w", encoding="utf-8") as f:
        nbformat.write(nb, f)


def run_ipynb(
    file_path: str, timeout: int = 300, return_nb: bool = True
) -> nbformat.NotebookNode | None:
    """
    运行.ipynb文件中的所有单元格，捕获执行结果和异常
    :param file_path: ipynb文件路径
    :param timeout: 每个单元格的执行超时时间（秒，默认5分钟）
    :param return_nb: 是否返回执行后的Notebook对象（包含输出）
    :return: 执行后的Notebook对象（若return_nb=True），否则返回None
    :raises: FileNotFoundError, CellExecutionError（单元格执行失败）
    """
    nb = read_ipynb(file_path)

    # 配置执行客户端
    client = NotebookClient(
        nb,
        timeout=timeout,
        kernel_name=IPYNB_KERNEL_NAME,  # 指定Python内核
        allow_errors=False,  # 遇到错误立即停止
        record_timing=False,
    )

    try:
        # 执行所有单元格
        client.execute()
        print(f"✅ {file_path} 执行完成")

        if return_nb:
            return nb
    except CellExecutionError as e:
        # 捕获单元格执行错误，打印详细信息
        print(f"❌ 单元格执行失败 - 错误: {e}")
        raise
    except Exception as e:
        print(f"❌ 执行出错: {e}")
        raise


def create_empty_ipynb() -> nbformat.NotebookNode:
    """
    创建一个空的.ipynb文件对象（基础框架）
    :return: 空的NotebookNode对象
    """
    nb = nbv4.new_notebook()
    # 设置默认元数据
    nb.metadata = {
        "kernelspec": {
            "display_name": IPYNB_KERNEL_DISPLAY_NAME,
            "language": "python",
            "name": IPYNB_KERNEL_NAME,
        },
        "language_info": {
            "codemirror_mode": {"name": "ipython", "version": 3},
            "file_extension": ".py",
            "mimetype": "text/x-python",
            "name": "python",
            "nbconvert_exporter": "python",
            "pygments_lexer": "ipython3",
            "version": "3.8+",
        },
    }
    return nb


def add_cell(nb: nbformat.NotebookNode, cell_type: str, content: str) -> None:
    """
    向Notebook对象添加新单元格
    :param nb: NotebookNode对象
    :param cell_type: 单元格类型（"code" 或 "markdown"）
    :param content: 单元格内容（代码/文本）
    :raises: ValueError（无效的单元格类型）
    """
    if cell_type not in ["code", "markdown"]:
        raise ValueError("cell_type必须是 'code' 或 'markdown'")

    if cell_type == "code":
        cell = nbv4.new_code_cell(source=content)
    else:
        cell = nbv4.new_markdown_cell(source=content)

    nb.cells.append(cell)


def modify_cell(nb: nbformat.NotebookNode, cell_index: int, new_content: str) -> None:
    """
    修改指定索引的单元格内容
    :param nb: NotebookNode对象
    :param cell_index: 单元格索引（从0开始）
    :param new_content: 新的单元格内容
    :raises: IndexError（索引超出范围）
    """
    if cell_index < 0 or cell_index >= len(nb.cells):
        raise IndexError(f"单元格索引超出范围，当前共有 {len(nb.cells)} 个单元格")

    nb.cells[cell_index].source = new_content


def delete_cell(nb: nbformat.NotebookNode, cell_index: int) -> None:
    """
    删除指定索引的单元格
    :param nb: NotebookNode对象
    :param cell_index: 单元格索引（从0开始）
    :raises: IndexError（索引超出范围）
    """
    if cell_index < 0 or cell_index >= len(nb.cells):
        raise IndexError(f"单元格索引超出范围，当前共有 {len(nb.cells)} 个单元格")

    del nb.cells[cell_index]


def extract_code_cells(nb: nbformat.NotebookNode) -> list[str]:
    """
    提取Notebook中所有代码单元格的内容
    :param nb: NotebookNode对象
    :return: 代码内容列表（每个元素是一个代码单元格的内容）
    """
    code_list = []
    for cell in nb.cells:
        if cell.cell_type == "code":
            code_list.append(cell.source)
    return code_list


# ------------------- 示例用法 -------------------
if __name__ == "__main__":
    # 1. 创建一个新的空Notebook
    new_nb = create_empty_ipynb()

    # 2. 添加代码和markdown单元格
    add_cell(new_nb, "markdown", "# 自动生成的Notebook")
    add_cell(
        new_nb,
        "code",
        "import numpy as np\nprint('Hello, IPynb!')\nprint(f'numpy版本: {np.__version__}')",
    )

    # 3. 保存Notebook
    save_path = "test_notebook.ipynb"
    write_ipynb(new_nb, save_path, overwrite=True)
    print(f"📝 已保存空Notebook到: {save_path}")

    # 4. 读取并运行Notebook
    try:
        executed_nb = run_ipynb(save_path)
        # 输出执行结果
        for i, cell in enumerate(executed_nb.cells):
            print(f"\n--- 单元格 {i} 输出 ---")
            if cell.cell_type == "code":
                print(cell.outputs[0].text)
            else:
                print(cell.source)

        # 5. 提取所有代码单元格内容
        code_content = extract_code_cells(executed_nb)
        print("\n📄 提取的代码内容:")
        for i, code in enumerate(code_content):
            print(f"单元格 {i}:\n{code}\n")

        # 6. 修改第一个代码单元格
        modify_cell(
            executed_nb,
            1,
            "import pandas as pd\nprint('修改后的代码运行成功!')\nprint(f'pandas版本: {pd.__version__}')",
        )

        # 7. 保存修改后的Notebook
        write_ipynb(executed_nb, "modified_notebook.ipynb", overwrite=True)
        print("✅ 修改后的Notebook已保存")

    except Exception as e:
        print(f"❌ 示例运行出错: {e}")
