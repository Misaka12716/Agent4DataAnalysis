import ast


def extract_code_components(code: str) -> dict:
    """
    解析Python代码，提取函数、类、类方法等组件（包含原始源代码）

    参数:
        code: 待解析的Python代码字符串

    返回:
        包含各类组件及对应源代码的字典
    """
    # 解析代码生成AST树（保留位置信息）
    tree = ast.parse(code, mode="exec")

    # 将代码按行拆分（保留换行符，方便按行号截取），行号从1开始，列表索引从0开始
    lines = code.splitlines(True)

    # 初始化存储组件的字典
    components = {
        "functions": [],  # 顶级函数
        "classes": [],  # 类
        "class_methods": [],  # 类中的方法
    }

    # 辅助函数：根据AST节点的行号提取对应的源代码
    def get_node_source(node, lines):
        """
        从代码行列表中提取AST节点对应的源代码
        Args:
            node: AST节点（包含lineno/end_lineno属性）
            lines: 按行拆分的代码列表（保留换行符）
        Returns:
            str: 节点对应的源代码（去除首尾多余换行）
        """
        # 转换为0-based索引（AST行号是1-based）
        start_idx = node.lineno - 1
        # 处理end_lineno（Python3.8+支持，若不存在则只取当前行）
        if hasattr(node, "end_lineno"):
            end_idx = node.end_lineno  # 切片是左闭右开，end_lineno是1-based，直接用
        else:
            end_idx = node.lineno
            print(f"警告：Python版本<3.8，节点{node.name}无法获取结束行号，仅提取单行")

        # 截取并拼接源代码，去除首尾多余换行（保留内部格式）
        source = "".join(lines[start_idx:end_idx]).rstrip("\n")
        return source

    # 为节点添加parent属性（方便判断是否为类内方法）
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child.parent = node

    # 遍历AST节点提取组件
    for node in ast.walk(tree):
        # 提取顶级函数（FunctionDef对应def，AsyncFunctionDef对应async def）
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # 只收集顶级函数（排除类中的方法）
            if isinstance(node.parent, ast.Module):
                func_info = {
                    "name": node.name,
                    "args": [arg.arg for arg in node.args.args],  # 函数参数
                    "lineno": node.lineno,  # 函数定义的行号
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                    "source_code": get_node_source(node, lines),  # 新增：函数源代码
                }
                components["functions"].append(func_info)

        # 提取类
        elif isinstance(node, ast.ClassDef):
            class_info = {
                "name": node.name,
                "bases": [
                    base.id for base in node.bases if isinstance(base, ast.Name)
                ],  # 父类
                "lineno": node.lineno,
                "source_code": get_node_source(node, lines),  # 新增：类源代码
            }
            components["classes"].append(class_info)

            # 提取类中的方法
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_info = {
                        "class_name": node.name,
                        "method_name": item.name,
                        "args": [arg.arg for arg in item.args.args],
                        "is_async": isinstance(item, ast.AsyncFunctionDef),
                        "is_classmethod": any(
                            isinstance(deco, ast.Name) and deco.id == "classmethod"
                            for deco in item.decorator_list
                        ),
                        "is_staticmethod": any(
                            isinstance(deco, ast.Name) and deco.id == "staticmethod"
                            for deco in item.decorator_list
                        ),
                        "source_code": get_node_source(item, lines),  # 新增：方法源代码
                    }
                    components["class_methods"].append(method_info)

    return components
