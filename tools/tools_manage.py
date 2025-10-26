from typing import List, Dict, Optional, Tuple
import json
from utils.mysql_utils import mysql_handler
from utils.config import TABLE_TOOLS_META_INFO, TABLE_TOOLS_TAGS  # 导入表名配置


class ToolsManager:
    def __init__(self):
        """初始化工具管理器，建立数据库连接"""
        self.db_handler = mysql_handler

    def __del__(self):
        """销毁时关闭数据库连接"""
        self.db_handler.close()

    # ------------------------------
    # 一、工具池管理（元信息CRUD）
    # ------------------------------

    def add_tool(
        self,
        tool_id: str,
        name: str,
        brief_desc: str,
        detailed_desc: str,
        input_params: Dict,  # 输入参数（结构化数据，如{"param1": "int", ...}）
        output_params: Dict,  # 输出参数（同上）
        api_path: str,
        tags: Optional[List[str]] = None,  # 可选：初始标签
    ) -> Tuple[bool, Optional[str]]:
        """
        添加工具到工具池（含初始标签）
        :param tool_id: 工具唯一ID
        :param name: 工具名称
        :param brief_desc: 简要描述（30字以内）
        :param detailed_desc: 详细描述
        :param input_params: 输入参数（字典，将转为JSON存储）
        :param output_params: 输出参数（字典，将转为JSON存储）
        :param api_path: 工具接口路径（如"/tools/calculator"）
        :param tags: 初始标签列表（可选）
        :return: (是否成功, 错误信息)
        """
        # 数据校验：简要描述长度
        if len(brief_desc) > 30:
            return False, "简要描述不能超过30字"

        try:
            # 序列化输入/输出参数为JSON字符串（便于数据库存储）
            input_json = json.dumps(input_params, ensure_ascii=False)
            output_json = json.dumps(output_params, ensure_ascii=False)

            # 插入工具元信息到配置的元信息表
            tool_data = {
                "id": tool_id,
                "name": name,
                "brief_desc": brief_desc,
                "detailed_desc": detailed_desc,
                "input_params": input_json,
                "output_params": output_json,
                "api_path": api_path,
            }
            _, _, error = self.db_handler.insert(TABLE_TOOLS_META_INFO, tool_data)
            if error:
                return False, f"工具添加失败：{error}"

            # 若有初始标签，同步添加到配置的标签表
            if tags:
                tag_success, tag_error = self.add_tags_to_tool(tool_id, tags)
                if not tag_success:
                    # 标签添加失败时回滚工具插入（需确保数据库支持事务）
                    self.db_handler.execute(
                        f"DELETE FROM {TABLE_TOOLS_META_INFO} WHERE id = %s",
                        (tool_id,),
                    )
                    return False, f"工具添加成功，但标签添加失败：{tag_error}"

            return True, None
        except Exception as e:
            return False, f"添加工具异常：{str(e)}"

    def get_tool(self, tool_id: str) -> Tuple[Optional[Dict], Optional[str]]:
        """
        获取工具元信息（含标签）
        :param tool_id: 工具ID
        :return: (工具信息字典, 错误信息)；工具信息含标签列表
        """
        # 查询工具元信息（使用配置的元信息表）
        sql = f"SELECT * FROM {TABLE_TOOLS_META_INFO} WHERE id = %s"
        result, error = self.db_handler.query(sql, (tool_id,))
        if error:
            return None, f"查询工具失败：{error}"
        if not result:
            return None, f"工具ID {tool_id} 不存在"
        tool_info = result[0]

        # 反序列化输入/输出参数（JSON转字典）
        tool_info["input_params"] = json.loads(tool_info["input_params"])
        tool_info["output_params"] = json.loads(tool_info["output_params"])

        # 查询工具的标签
        tags, tag_error = self.get_tags_of_tool(tool_id)
        if tag_error:
            return tool_info, f"工具信息获取成功，但标签查询失败：{tag_error}"
        tool_info["tags"] = tags

        return tool_info, None

    def update_tool(
        self,
        tool_id: str,
        **kwargs,  # 支持更新的字段：name, brief_desc, detailed_desc, input_params, output_params, api_path
    ) -> Tuple[bool, Optional[str]]:
        """
        更新工具元信息（部分字段）
        :param tool_id: 工具ID
        :param kwargs: 需更新的字段（如name="新名称", ...）
        :return: (是否成功, 错误信息)
        """
        # 校验工具是否存在
        tool_exist, _ = self._tool_exists(tool_id)
        if not tool_exist:
            return False, f"工具ID {tool_id} 不存在"

        # 处理可更新字段（过滤非法字段）
        allowed_fields = [
            "name",
            "brief_desc",
            "detailed_desc",
            "input_params",
            "output_params",
            "api_path",
        ]
        update_fields = {k: v for k, v in kwargs.items() if k in allowed_fields}
        if not update_fields:
            return False, "无有效更新字段"

        # 校验简要描述长度
        if "brief_desc" in update_fields and len(update_fields["brief_desc"]) > 30:
            return False, "简要描述不能超过30字"

        try:
            # 序列化输入/输出参数（若更新）
            if "input_params" in update_fields:
                update_fields["input_params"] = json.dumps(
                    update_fields["input_params"], ensure_ascii=False
                )
            if "output_params" in update_fields:
                update_fields["output_params"] = json.dumps(
                    update_fields["output_params"], ensure_ascii=False
                )

            # 构建UPDATE SQL（使用配置的元信息表）
            set_clause = ", ".join([f"{k} = %s" for k in update_fields.keys()])
            sql = f"UPDATE {TABLE_TOOLS_META_INFO} SET {set_clause} WHERE id = %s"
            params = tuple(update_fields.values()) + (tool_id,)

            # 执行更新
            affected_rows, error = self.db_handler.execute(sql, params)
            if error:
                return False, f"更新工具失败：{error}"
            return True, None
        except Exception as e:
            return False, f"更新工具异常：{str(e)}"

    def delete_tool(self, tool_id: str) -> Tuple[bool, Optional[str]]:
        """
        删除工具（含其所有标签）
        :param tool_id: 工具ID
        :return: (是否成功, 错误信息)
        """
        # 校验工具是否存在
        tool_exist, _ = self._tool_exists(tool_id)
        if not tool_exist:
            return False, f"工具ID {tool_id} 不存在"

        try:
            # 先删除标签索引（避免外键约束错误）
            tag_success, tag_error = self.remove_tags_from_tool(tool_id, all_tags=True)
            if not tag_success:
                return False, f"标签删除失败：{tag_error}"

            # 再删除工具元信息（使用配置的元信息表）
            sql = f"DELETE FROM {TABLE_TOOLS_META_INFO} WHERE id = %s"
            affected_rows, error = self.db_handler.execute(sql, (tool_id,))
            if error:
                return False, f"删除工具失败：{error}"
            return True, None
        except Exception as e:
            return False, f"删除工具异常：{str(e)}"

    # ------------------------------
    # 二、标签索引管理（标签与工具映射）
    # ------------------------------

    def add_tags_to_tool(
        self, tool_id: str, tags: List[str]
    ) -> Tuple[bool, Optional[str]]:
        """
        给工具添加标签（自动去重）
        :param tool_id: 工具ID
        :param tags: 标签列表（如["计算", "数学"]）
        :return: (是否成功, 错误信息)
        """
        if not tags:
            return True, "无标签需添加"

        # 校验工具是否存在
        tool_exist, error = self._tool_exists(tool_id)
        if not tool_exist:
            return False, error or f"工具ID {tool_id} 不存在"

        try:
            # 批量插入标签（去重：先查询已存在的标签）
            existing_tags, _ = self.get_tags_of_tool(tool_id)
            new_tags = [tag for tag in tags if tag not in existing_tags]
            if not new_tags:
                return True, "标签已全部存在，无需重复添加"

            # 构建批量插入SQL（使用配置的标签表）
            insert_data = [{"tool_id": tool_id, "tag": tag} for tag in new_tags]
            for data in insert_data:
                _, _, err = self.db_handler.insert(
                    TABLE_TOOLS_TAGS, data, auto_commit=False
                )
                if err:
                    self.db_handler.connection.rollback()  # 批量插入失败回滚
                    return False, f"添加标签 {data['tag']} 失败：{err}"
            self.db_handler.connection.commit()  # 全部成功后提交
            return True, None
        except Exception as e:
            return False, f"添加标签异常：{str(e)}"

    def remove_tags_from_tool(
        self, tool_id: str, tags: Optional[List[str]] = None, all_tags: bool = False
    ) -> Tuple[bool, Optional[str]]:
        """
        从工具移除标签（支持批量移除或全部移除）
        :param tool_id: 工具ID
        :param tags: 标签列表（若all_tags=True则忽略）
        :param all_tags: 是否移除所有标签（默认False）
        :return: (是否成功, 错误信息)
        """
        # 校验工具是否存在
        tool_exist, error = self._tool_exists(tool_id)
        if not tool_exist:
            return False, error or f"工具ID {tool_id} 不存在"

        try:
            if all_tags:
                # 移除所有标签（使用配置的标签表）
                sql = f"DELETE FROM {TABLE_TOOLS_TAGS} WHERE tool_id = %s"
                params = (tool_id,)
            else:
                if not tags:
                    return True, "无标签需移除"
                # 移除指定标签（批量，使用配置的标签表）
                placeholders = ", ".join(["%s"] * len(tags))
                sql = f"DELETE FROM {TABLE_TOOLS_TAGS} WHERE tool_id = %s AND tag IN ({placeholders})"
                params = (tool_id,) + tuple(tags)

            affected_rows, error = self.db_handler.execute(sql, params)
            if error:
                return False, f"移除标签失败：{error}"
            return True, None
        except Exception as e:
            return False, f"移除标签异常：{str(e)}"

    def get_tags_of_tool(self, tool_id: str) -> Tuple[List[str], Optional[str]]:
        """
        获取工具的所有标签
        :param tool_id: 工具ID
        :return: (标签列表, 错误信息)
        """
        # 使用配置的标签表查询
        sql = f"SELECT tag FROM {TABLE_TOOLS_TAGS} WHERE tool_id = %s"
        result, error = self.db_handler.query(sql, (tool_id,))
        if error:
            return [], f"查询工具标签失败：{error}"
        tags = [item["tag"] for item in result]
        return tags, None

    def get_tool_ids_by_tags(
        self, tags: List[str], intersection: bool = False
    ) -> Tuple[List[str], Optional[str]]:
        """
        通过标签查询工具ID（支持并集/交集）
        :param tags: 标签列表
        :param intersection: 是否查询标签交集（默认False：并集）
        :return: (工具ID列表, 错误信息)
        """
        if not tags:
            return [], "标签列表不能为空"

        try:
            if intersection:
                # 交集：同时包含所有标签的工具（使用配置的标签表）
                placeholders = ", ".join(["%s"] * len(tags))
                sql = f"""
                    SELECT tool_id FROM {TABLE_TOOLS_TAGS} 
                    WHERE tag IN ({placeholders})
                    GROUP BY tool_id 
                    HAVING COUNT(DISTINCT tag) = {len(tags)}
                """
            else:
                # 并集：包含任一标签的工具（使用配置的标签表）
                placeholders = ", ".join(["%s"] * len(tags))
                sql = f"""
                    SELECT DISTINCT tool_id FROM {TABLE_TOOLS_TAGS} 
                    WHERE tag IN ({placeholders})
                """

            result, error = self.db_handler.query(sql, tuple(tags))
            if error:
                return [], f"通过标签查询工具失败：{error}"
            tool_ids = [item["tool_id"] for item in result]
            return tool_ids, None
        except Exception as e:
            return [], f"标签查询工具异常：{str(e)}"

    def get_all_tags(self) -> Tuple[List[str], Optional[str]]:
        """获取所有不重复的标签"""
        # 使用配置的标签表查询
        sql = f"SELECT DISTINCT tag FROM {TABLE_TOOLS_TAGS} ORDER BY tag"
        result, error = self.db_handler.query(sql)
        if error:
            return [], f"查询所有标签失败：{error}"
        return [item["tag"] for item in result], None

    # ------------------------------
    # 三、内部辅助方法
    # ------------------------------
    def _tool_exists(self, tool_id: str) -> Tuple[bool, Optional[str]]:
        """检查工具是否存在（内部使用）"""
        # 使用配置的元信息表查询
        sql = f"SELECT id FROM {TABLE_TOOLS_META_INFO} WHERE id = %s"
        result, error = self.db_handler.query(sql, (tool_id,))
        if error:
            return False, f"检查工具存在性失败：{error}"
        return len(result) > 0, None

    # ------------------------------
    # 四、批量从JSON导入工具
    # ------------------------------
    def batch_import_from_json(self, json_file_path: str) -> Dict[str, any]:
        """
        从JSON文件批量导入工具数据到数据库
        :param json_file_path: JSON文件路径
        :return: 导入结果统计（成功数、失败数、失败详情）
        """
        result = {
            "total": 0,
            "success": 0,
            "failed": 0,
            "fail_details": [],  # 存储失败的工具ID和原因
        }

        try:
            # 1. 读取JSON文件
            with open(json_file_path, "r", encoding="utf-8") as f:
                tools_json = json.load(f)
                if not isinstance(tools_json, list):
                    raise ValueError("JSON文件根节点必须是列表")
                result["total"] = len(tools_json)
                print(f"成功读取JSON文件，共发现 {len(tools_json)} 个工具")

            # 2. 遍历工具逐个导入
            for tool_data in tools_json:
                try:
                    # 2.1 从新JSON字段中提取核心信息（适配新格式）
                    # 工具ID：从api_path末尾截取（如"tools/1"取"1"），确保为字符串
                    api_path = tool_data.get("api_path", "")
                    tool_id = (
                        api_path.split("/")[-1]
                        if api_path
                        else str(hash(tool_data.get("name")))
                    )
                    if not tool_id:
                        raise ValueError("无法生成有效的工具ID")

                    # 直接复用新JSON中的字段
                    tool_name = tool_data.get("name", f"工具_{tool_id}")
                    brief_desc = tool_data.get("brief_desc", "")
                    detailed_desc = tool_data.get("detailed_desc", "")
                    original_input_params = tool_data.get(
                        "input_params", []
                    )  # 新JSON中的列表格式
                    original_output_params = tool_data.get(
                        "output_params", []
                    )  # 新JSON中的列表格式
                    api_path = tool_data.get(
                        "api_path", f"/tools/{tool_id}"
                    )  # 优先用JSON中的api_path

                    # 2.2 转换参数格式：列表→字典（适配add_tool的Dict类型要求）
                    # 转换规则：{参数名: "类型：xxx，描述：xxx"}
                    input_params = {}
                    for param in original_input_params:
                        param_name = param.get("name")
                        param_type = param.get("type", "")
                        param_desc = param.get("description", "")
                        if param_name:
                            input_params[param_name] = (
                                f"类型：{param_type}，描述：{param_desc}"
                            )

                    output_params = {}
                    for param in original_output_params:
                        param_name = param.get("name")
                        param_type = param.get("type", "")
                        param_desc = param.get("description", "")
                        if param_name:
                            output_params[param_name] = (
                                f"类型：{param_type}，描述：{param_desc}"
                            )

                    # 2.3 标签处理：新JSON无type/subtype，暂设为空列表（可根据需求自定义）
                    tags = []

                    # 2.4 调用add_tool导入（参数完全适配新JSON）
                    success, error = self.add_tool(
                        tool_id=tool_id,
                        name=tool_name,
                        brief_desc=brief_desc,
                        detailed_desc=detailed_desc,
                        input_params=input_params,
                        output_params=output_params,
                        api_path=api_path,
                        tags=tags,
                    )

                    # 2.5 记录结果
                    if success:
                        result["success"] += 1
                        print(f"工具 {tool_id}（{tool_name}）导入成功")
                    else:
                        result["failed"] += 1
                        result["fail_details"].append(
                            {
                                "tool_id": tool_id,
                                "tool_name": tool_name,
                                "reason": error or "未知错误",
                            }
                        )
                        print(f"工具 {tool_id}（{tool_name}）导入失败：{error}")

                except Exception as e:
                    # 单个工具处理异常（补充工具名称便于排查）
                    tool_name = tool_data.get("name", "未知名称工具")
                    result["failed"] += 1
                    result["fail_details"].append(
                        {
                            "tool_id": tool_id if "tool_id" in locals() else "未知ID",
                            "tool_name": tool_name,
                            "reason": f"处理异常：{str(e)}",
                        }
                    )
                    print(f"工具 {tool_name} 处理异常：{str(e)}")

            return result

        except FileNotFoundError:
            raise FileNotFoundError(f"JSON文件不存在：{json_file_path}")
        except json.JSONDecodeError:
            raise ValueError("JSON文件格式无效，无法解析")
        except Exception as e:
            raise Exception(f"批量导入失败：{str(e)}")


if __name__ == "__main__":
    tool_manager = ToolsManager()
    json_file_path = "./tools_info.json"
    try:
        print(f"开始从 {json_file_path} 批量导入工具...")
        # 3. 调用类内部的批量导入方法
        import_result = tool_manager.batch_import_from_json(json_file_path)

        # 4. 打印导入统计结果
        print("\n===== 批量导入结果 =====")
        print(f"总工具数：{import_result['total']}")
        print(f"成功导入：{import_result['success']}")
        print(f"导入失败：{import_result['failed']}")

        # 5. 打印失败详情（若有）
        if import_result["fail_details"]:
            print("\n失败详情：")
            for idx, detail in enumerate(import_result["fail_details"], 1):
                print(f"  {idx}. 工具ID {detail['tool_id']}：{detail['reason']}")

    except Exception as e:
        print(f"批量导入过程出错：{str(e)}")
    finally:
        # 确保连接关闭
        del tool_manager
        print("\n程序结束，数据库连接已关闭")
