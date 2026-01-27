# tools_category_manager.py
# 工具分类管理器，用于处理工具的大类和亚类信息

import json
import os
from utils.config import PATH

TOOLS_INFO_PATH = "tools"


class ToolsCategoryManager:
    def __init__(self):
        # 初始化工具分类数据（基于之前的JSON结构）
        self.categories = json.load(
            open(
                os.path.join(PATH, TOOLS_INFO_PATH, "tools_category.json"),
                "r",
                encoding="utf-8",
            )
        )
        self.tools_info = json.load(
            open(
                os.path.join(PATH, TOOLS_INFO_PATH, "tools_info.json"),
                "r",
                encoding="utf-8",
            )
        )

        # ========== 核心优化：初始化时构建映射字典，仅执行一次 ==========
        # 构建大类中文 -> 英文的映射字典（实例属性）
        self.main_cate_map = {
            item["一级目录中文"]: item["一级目录英文"] for item in self.categories
        }
        # 构建亚类中文 -> 英文的映射字典（实例属性）
        self.sub_cate_map = {}
        for main_item in self.categories:
            for sub_item in main_item["二级目录"]:
                self.sub_cate_map[sub_item["二级目录中文"]] = sub_item["二级目录英文"]

    def get_main_categories(self):
        """获取所有工具大类（一级目录）"""
        return [
            {
                "main_category_chinese": item["一级目录中文"],
                "main_category_english": item["一级目录英文"],
            }
            for item in self.categories
        ]

    def get_sub_categories(self, main_category=None):
        """
        获取工具亚类（二级目录）

        参数:
            main_category: 可选，指定大类中文名称，若提供则只返回该大类下的亚类
                          若不提供则返回所有亚类及其所属大类信息
        """
        sub_categories = []
        for main_item in self.categories:
            # 如果指定了大类且不匹配当前大类，则跳过
            if main_category and main_item["一级目录中文"] != main_category:
                continue

            # 提取当前大类信息
            main_info = {
                "main_category_chinese": main_item["一级目录中文"],
                "main_category_english": main_item["一级目录英文"],
            }

            # 遍历当前大类下的所有亚类
            for sub_item in main_item["二级目录"]:
                sub_info = {
                    **main_info,  # 继承大类信息
                    "sub_category_chinese": sub_item["二级目录中文"],
                    "sub_category_english": sub_item["二级目录英文"],
                }
                sub_categories.append(sub_info)

        return sub_categories

    def get_tools_from_category(self, main_category=None, sub_category=None):
        """
        获取指定大类或亚类下的所有工具

        参数:
            main_category: 可选，指定大类中文名称，若提供则只返回该大类下的工具
                          若不提供则返回所有工具
            sub_category: 可选，指定亚类中文名称，若提供则只返回该亚类下的工具
                          若不提供则返回所有工具
        """
        # 直接使用初始化时构建的映射字典，无需重复构建
        main_category_en = (
            self.main_cate_map.get(main_category) if main_category else None
        )
        sub_category_en = self.sub_cate_map.get(sub_category) if sub_category else None

        tools = []
        for tool in self.tools_info:
            # 匹配大类（type字段为英文）
            if main_category_en and tool.get("type") != main_category_en:
                continue
            # 匹配亚类（subtype字段为英文）
            if sub_category_en and tool.get("subtype") != sub_category_en:
                continue
            tools.append(tool)
        return tools


# 示例用法
if __name__ == "__main__":
    manager = ToolsCategoryManager()

    # 获取所有大类
    print("所有工具大类：")
    for main in manager.get_main_categories():
        print(f"{main['main_category_chinese']} ({main['main_category_english']})")

    # 获取所有亚类
    print("\n所有工具亚类：")
    for sub in manager.get_sub_categories():
        print(
            f"{sub['main_category_chinese']} -> {sub['sub_category_chinese']} ({sub['sub_category_english']})"
        )

    # 获取指定大类下的亚类（例如"通用表型处理"）
    print("\n通用表型处理下的亚类：")
    for sub in manager.get_sub_categories(main_category="通用表型处理"):
        print(f"{sub['sub_category_chinese']} ({sub['sub_category_english']})")

    # 新增示例：获取指定大类（中文）下的工具
    print("\n通用表型处理大类下的所有工具：")
    generic_tools = manager.get_tools_from_category(main_category="通用表型处理")
    for tool in generic_tools:
        print(
            f"工具名称：{tool.get('name')}，类型：{tool.get('type')}，亚类：{tool.get('subtype')}"
        )
