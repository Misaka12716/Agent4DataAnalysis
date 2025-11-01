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
        tools = []
        for tool in self.tools_info:
            # main_category对应type字段
            if main_category and tool.get("type") != main_category:
                continue
            # sub_category对应subtype字段
            if sub_category and tool.get("subtype") != sub_category:
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

    # 获取指定大类下的亚类（例如"代谢处理"）
    print("\n代谢处理下的亚类：")
    for sub in manager.get_sub_categories(main_category="代谢处理"):
        print(f"{sub['sub_category_chinese']} ({sub['sub_category_english']})")
