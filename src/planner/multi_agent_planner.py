from planner.base_planner import BasePlanner
from knowledge.knowledge_base import KnowledgeBase
from utils.llm_response import (
    ai_response,
    ai_chat,  # 已支持流式返回异步生成器
    new_chat_session,
    get_chat_history,
    close_chat_session,
)
from prompts.prompt_builder import PromptBuilder
from tools.tools_category_manager import ToolsCategoryManager
from aiohttp import ClientSession
from typing import Callable, List, Dict, Any, AsyncGenerator
import json
import json_repair


# -------------------------- Type Definitions --------------------------
ToolMetadata = Dict[str, str]
ToolMetadataFetcher = Callable[[], List[ToolMetadata]]


class MultiAgentPlanner(BasePlanner):
    """Multi-Agent Planner: 流式版本（返回异步生成器）"""

    def __init__(
        self,
        http_session: ClientSession,
        knowledge_base: KnowledgeBase = None,
    ):
        self.knowledge_base = knowledge_base
        self.http_session = http_session
        self.prompt_builder = PromptBuilder("zh")
        self.system_prompt = self.prompt_builder.build_system_prompt("planner")
        self.plan_session_id = new_chat_session(system_prompt=self.system_prompt)
        self.tools_category_manager = ToolsCategoryManager()
        self._closed = False

    async def close(self):
        if self._closed:
            return
        print(f"[Planner] 开始关闭资源（会话ID：{self.plan_session_id}）")
        if self.plan_session_id:
            try:
                await close_chat_session(self.plan_session_id)
                print(f"[Planner] LLM会话关闭成功（ID：{self.plan_session_id}）")
            except Exception as e:
                print(f"[Warning] LLM会话关闭失败：{str(e)}")
        if self.http_session and not self.http_session.closed:
            try:
                await self.http_session.close()
                print(f"[Planner] HTTP会话关闭成功")
            except Exception as e:
                print(f"[Warning] HTTP会话关闭失败：{str(e)}")
        if self.knowledge_base:
            await self.knowledge_base.close()
            print(f"[Planner] 知识库关闭成功")
        self._closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
        return False

    # -------------------------- 1. 结构化需求组织（流式版本） --------------------------
    async def organize_requirement(
        self,
        input_data: str,
        file_info: str = "No files uploaded",
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """流式返回需求解析过程及结果：
        - type="status": 阶段状态
        - type="llm_chunk": LLM流式片段
        - type="llm_complete": LLM完整响应
        - type="result": 最终解析结果
        - type="error": 错误信息
        """
        yield {
            "type": "status",
            "message": f"开始解析需求：{input_data}",
            "step": "start",
        }

        # 构建prompt
        prompt = self.prompt_builder.build_user_prompt(
            role="planner",
            task="req_analysis",
            input_data=input_data,
            file_info=file_info,
        )
        yield {"type": "status", "message": "调用LLM获取响应", "step": "call_llm"}

        try:
            # 调用流式ai_chat
            llm_stream = ai_chat(
                session_id=self.plan_session_id,
                prompt=prompt.strip(),
                http_session=self.http_session,
                need_thinking=True,
                stream=True,  # 开启流式
            )

            full_content = ""
            thinking = ""
            # 迭代LLM流式响应
            async for chunk in llm_stream:
                if chunk["type"] == "chunk":
                    yield {
                        "type": "llm_chunk",
                        "content": chunk["content"],
                        "model": chunk["model"],
                    }
                elif chunk["type"] == "complete":
                    full_content = chunk["content"]
                    thinking = chunk["thinking"]
                    yield {
                        "type": "llm_complete",
                        "content": full_content,
                        "thinking": thinking,
                    }

            # 解析LLM结果
            yield {
                "type": "status",
                "message": "解析LLM返回的JSON结果",
                "step": "parse_result",
            }
            repaired_json = json_repair.repair_json(full_content)
            structured_req = json.loads(repaired_json)
            parsed_result = json.dumps(structured_req, ensure_ascii=False, indent=2)

            yield {
                "type": "result",
                "structured_requirement": structured_req,
                "thinking": thinking,
                "success": True,
            }

        except Exception as e:
            error_msg = str(e)
            yield {
                "type": "error",
                "message": f"需求解析失败：{error_msg}",
                "raw_input": input_data,
                "success": False,
            }

    # -------------------------- 2. 过往经验搜索（流式版本） --------------------------
    async def search_experience(
        self, requirement: Dict[str, Any]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """流式返回经验搜索过程及结果：
        - type="status": 阶段状态
        - type="item_process": 处理单个历史项
        - type="llm_chunk": 单个项的LLM片段
        - type="item_result": 单个项的处理结果
        - type="result": 最终有效结果列表
        """
        task_type = requirement.get("task_type", "Unknown Type")
        yield {
            "type": "status",
            "message": f"搜索{task_type}相关经验（相似度≥70%）",
            "step": "start",
        }

        task_history = []  # 实际场景替换为数据库查询
        valid_results: List[Dict[str, Any]] = []

        if not task_history:
            yield {"type": "status", "message": "无历史任务记录", "step": "no_history"}
            valid_results.append(
                {
                    "experience_id": "",
                    "similarity": "",
                    "core_content": "未找到相似度≥70%的相关经验",
                    "thinking": "无历史记录或均不满足相似度要求",
                    "history_item": None,
                }
            )
            yield {
                "type": "result",
                "results": valid_results,
                "total": len(valid_results),
            }
            return

        yield {
            "type": "status",
            "message": f"开始处理{len(task_history)}条历史记录",
            "step": "process_items",
        }

        for idx, item in enumerate(task_history, 1):
            yield {
                "type": "item_process",
                "item_index": idx,
                "item": item,
                "step": "start",
            }
            try:
                # 构建prompt
                prompt = self.prompt_builder.build_user_prompt(
                    role="planner",
                    task="exp_search",
                    structured_req=requirement,
                    task_history=item,
                )

                # 流式调用LLM
                llm_stream = ai_chat(
                    session_id=self.plan_session_id,
                    prompt=prompt.strip(),
                    http_session=self.http_session,
                    need_thinking=True,
                    stream=True,
                )

                full_content = ""
                thinking = ""
                async for chunk in llm_stream:
                    if chunk["type"] == "chunk":
                        yield {
                            "type": "llm_chunk",
                            "item_index": idx,
                            "content": chunk["content"],
                        }
                    elif chunk["type"] == "complete":
                        full_content = chunk["content"]
                        thinking = chunk["thinking"]

                # 解析结果
                repaired_json = json_repair.repair_json(full_content)
                ai_content = json.loads(repaired_json.strip())

                # 验证字段和相似度
                required_fields = ["experience_id", "similarity", "core_content"]
                if not all(field in ai_content for field in required_fields):
                    missing = [f for f in required_fields if f not in ai_content]
                    yield {
                        "type": "item_result",
                        "item_index": idx,
                        "status": "skip",
                        "reason": f"缺少字段：{missing}",
                    }
                    continue

                similarity_str = ai_content["similarity"].strip().replace("%", "")
                similarity = int(similarity_str)
                if not (0 <= similarity <= 100):
                    yield {
                        "type": "item_result",
                        "item_index": idx,
                        "status": "skip",
                        "reason": f"相似度格式错误：{similarity_str}",
                    }
                    continue

                # 过滤相似度≥70%的结果
                if similarity >= 70:
                    valid_item = {
                        "experience_id": ai_content["experience_id"],
                        "similarity": f"{similarity}%",
                        "core_content": ai_content["core_content"],
                        "thinking": thinking,
                        "history_item": item,
                    }
                    valid_results.append(valid_item)
                    yield {
                        "type": "item_result",
                        "item_index": idx,
                        "status": "valid",
                        "result": valid_item,
                    }
                else:
                    yield {
                        "type": "item_result",
                        "item_index": idx,
                        "status": "skip",
                        "reason": f"相似度不足：{similarity}% < 70%",
                    }

            except Exception as e:
                error_msg = str(e)
                yield {
                    "type": "item_result",
                    "item_index": idx,
                    "status": "error",
                    "error": error_msg,
                }
                continue

        # 返回最终结果
        if not valid_results:
            valid_results.append(
                {
                    "experience_id": "",
                    "similarity": "",
                    "core_content": "未找到相似度≥70%的相关经验",
                    "thinking": "所有历史项均不满足要求",
                    "history_item": None,
                }
            )

        yield {
            "type": "result",
            "results": valid_results,
            "total": len(valid_results),
            "step": "complete",
        }

    # -------------------------- 3. 知识搜索（占位，流式版本） --------------------------
    async def search_knowledge(
        self, requirement: Dict[str, Any]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        task_type = requirement.get("task_type", "Unknown Type")
        yield {
            "type": "status",
            "message": f"知识搜索模块暂未集成（需求类型：{task_type}）",
        }
        yield {
            "type": "result",
            "knowledge": "Knowledge search agent to be implemented",
            "success": False,
            "message": "Knowledge search module not integrated yet",
        }

    # -------------------------- 4. 任务分解与分配（流式版本） --------------------------
    async def assign_tasks(
        self,
        requirement: Dict[str, Any],
        knowledge: Dict[str, Any] = None,
        experience: Dict[str, Any] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """流式返回任务分解过程及结果"""
        knowledge = knowledge or {}
        experience = experience or {}
        goal = requirement.get("goal", "Unknown Goal")
        yield {"type": "status", "message": f"开始分解任务：{goal}", "step": "start"}

        knowledge_str = json.dumps(knowledge, ensure_ascii=False)
        experience_str = json.dumps(experience, ensure_ascii=False)
        yield {
            "type": "status",
            "message": f"输入知识：{knowledge_str}",
            "step": "input_knowledge",
        }
        yield {
            "type": "status",
            "message": f"输入经验：{experience_str}",
            "step": "input_experience",
        }

        # 构建prompt
        prompt = self.prompt_builder.build_user_prompt(
            role="planner",
            task="assign_tasks",
            structured_req=requirement,
            searched_experience=experience,
            collected_knowledge=knowledge,
        )

        try:
            # 流式调用LLM
            llm_stream = ai_chat(
                session_id=self.plan_session_id,
                prompt=prompt,
                http_session=self.http_session,
                need_thinking=True,
                stream=True,
            )

            full_content = ""
            thinking = ""
            async for chunk in llm_stream:
                if chunk["type"] == "chunk":
                    yield {
                        "type": "llm_chunk",
                        "content": chunk["content"],
                        "model": chunk["model"],
                    }
                elif chunk["type"] == "complete":
                    full_content = chunk["content"]
                    thinking = chunk["thinking"]
                    yield {
                        "type": "llm_complete",
                        "content": full_content,
                        "thinking": thinking,
                    }

            # 解析任务
            yield {
                "type": "status",
                "message": "解析分解后的子任务",
                "step": "parse_tasks",
            }
            repaired_content = (
                full_content.replace("```json", "").replace("```", "").strip()
            )
            tasks = json.loads(repaired_content)

            # 验证任务结构
            if not isinstance(tasks, list):
                raise ValueError("输出必须为JSON数组")
            if len(tasks) > 10:
                raise ValueError(f"子任务数量超过限制：{len(tasks)} > 10")

            task_ids = []
            required_fields = [
                "task_id",
                "task_name",
                "description",
                "dependencies",
                "worker_type",
            ]
            for task in tasks:
                missing = [f for f in required_fields if f not in task]
                if missing:
                    raise ValueError(f"子任务缺少字段：{missing}")
                tid = task["task_id"]
                if tid in task_ids:
                    raise ValueError(f"重复task_id：{tid}")
                task_ids.append(tid)

            # 返回最终任务结果
            yield {
                "type": "result",
                "tasks": tasks,
                "thinking": thinking,
                "total_tasks": len(tasks),
                "success": True,
            }

        except Exception as e:
            error_msg = str(e)
            yield {
                "type": "error",
                "message": f"任务分解失败：{error_msg}",
                "success": False,
                "thinking": thinking if "thinking" in locals() else "",
            }

    # -------------------------- 5. 工具匹配（流式版本） --------------------------
    async def find_tools(
        self, task: Dict[str, Any]
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """流式返回工具匹配过程及结果"""
        task_name = task.get("task_name", "Unnamed Task")
        result = {
            "main_category": [],
            "sub_category": [],
            "matched_tools": [],
            "reasons": "",
            "success": False,
            "task_name": task_name,
        }

        yield {
            "type": "status",
            "message": f"开始匹配子任务[{task_name}]的工具类别",
            "step": "start",
        }
        task_type = task.get("task_type", "Unknown Type")
        task_desc = task.get("description", "无描述")
        task_info = (
            f"任务名称：{task_name}\n任务描述：{task_desc}\n任务类型：{task_type}"
        )
        yield {
            "type": "status",
            "message": f"子任务详情：{task_info}",
            "step": "task_info",
        }

        try:
            # -------------------------- 步骤1：匹配大类 --------------------------
            yield {
                "type": "status",
                "message": "匹配工具大类",
                "step": "match_main_category",
            }
            main_categories = self.tools_category_manager.get_main_categories()
            main_prompt = self.prompt_builder.build_user_prompt(
                role="planner",
                task="tools_select",
                worker_task_desc=f"Task Name: {task_name}\nTask Description: {task_desc}\nTask Type: {task_type}",
                tools_categories=main_categories,
                categories_content="选择最匹配的工具大类",
            )

            # 流式调用LLM选大类
            main_llm_stream = ai_chat(
                session_id=self.plan_session_id,
                prompt=main_prompt.strip(),
                http_session=self.http_session,
                need_thinking=True,
                stream=True,
            )

            main_full = ""
            async for chunk in main_llm_stream:
                if chunk["type"] == "chunk":
                    yield {
                        "type": "llm_chunk",
                        "step": "main_category",
                        "content": chunk["content"],
                    }
                elif chunk["type"] == "complete":
                    main_full = chunk["content"]

            # 解析大类结果
            main_repaired = json_repair.repair_json(main_full)
            selected_main_cats = json.loads(main_repaired)
            if not isinstance(selected_main_cats, list) or len(selected_main_cats) == 0:
                raise ValueError("大类选择结果必须为非空数组")

            # 验证大类有效性
            all_main_names = []
            for cat in main_categories:
                all_main_names.append(cat["main_category_chinese"])
                all_main_names.append(cat["main_category_english"])

            valid_main_cats = []
            for cat in selected_main_cats:
                cat_name = cat.get("categoryName")
                if cat_name in all_main_names:
                    valid_main_cats.append(
                        {
                            "categoryName": cat_name,
                            "type": cat.get("type", ""),
                            "reason": cat.get("reason", ""),
                        }
                    )
            if len(valid_main_cats) == 0:
                raise ValueError("无有效大类")

            result["main_category"] = valid_main_cats
            main_cat_names = [cat["categoryName"] for cat in valid_main_cats]
            result["reasons"] += f"选中大类：{main_cat_names}；"
            yield {
                "type": "status",
                "message": f"匹配到有效大类：{main_cat_names}",
                "step": "main_category_done",
            }

            # -------------------------- 步骤2：匹配亚类 --------------------------
            yield {
                "type": "status",
                "message": "匹配工具亚类",
                "step": "match_sub_category",
            }
            selected_sub_cats = []
            for main_cat in valid_main_cats:
                main_cat_name = main_cat["categoryName"]
                sub_cats = self.tools_category_manager.get_sub_categories(
                    main_category=main_cat_name
                )
                if len(sub_cats) == 0:
                    yield {
                        "type": "status",
                        "message": f"大类[{main_cat_name}]下无亚类，跳过",
                        "step": "no_sub_category",
                    }
                    continue

                # 构建亚类prompt并流式调用
                sub_prompt = self.prompt_builder.build_user_prompt(
                    role="planner",
                    task="tools_select",
                    worker_task_desc=f"Task Name: {task_name}\nTask Description: {task_desc}\nTask Type: {task_type}",
                    tools_categories=sub_cats,
                    categories_content=f"当前大类：{main_cat_name}，选择匹配的亚类",
                )

                sub_llm_stream = ai_chat(
                    session_id=self.plan_session_id,
                    prompt=sub_prompt.strip(),
                    http_session=self.http_session,
                    need_thinking=True,
                    stream=True,
                )

                sub_full = ""
                async for chunk in sub_llm_stream:
                    if chunk["type"] == "chunk":
                        yield {
                            "type": "llm_chunk",
                            "step": f"sub_category_{main_cat_name}",
                            "content": chunk["content"],
                        }
                    elif chunk["type"] == "complete":
                        sub_full = chunk["content"]

                # 解析亚类结果
                sub_repaired = json_repair.repair_json(sub_full)
                cat_sub_cats = json.loads(sub_repaired)
                if not isinstance(cat_sub_cats, list):
                    yield {
                        "type": "status",
                        "message": f"大类[{main_cat_name}]亚类结果非数组，跳过",
                        "step": "sub_category_skip",
                    }
                    continue

                # 验证亚类有效性
                sub_cat_names = []
                for sub in sub_cats:
                    sub_cat_names.append(sub["sub_category_chinese"])
                    sub_cat_names.append(sub["sub_category_english"])

                for sub_cat in cat_sub_cats:
                    sub_cat_name = sub_cat.get("categoryName")
                    if sub_cat_name in sub_cat_names:
                        sub_cat_obj = {
                            "categoryName": sub_cat_name,
                            "type": sub_cat.get("type", ""),
                            "reason": sub_cat.get("reason", ""),
                            "main_category": main_cat_name,
                        }
                        if sub_cat_obj not in selected_sub_cats:
                            selected_sub_cats.append(sub_cat_obj)
                            result[
                                "reasons"
                            ] += f"大类[{main_cat_name}]选中亚类：{sub_cat_name}；"

            if len(selected_sub_cats) == 0:
                raise ValueError("无有效亚类")

            result["sub_category"] = selected_sub_cats
            result["success"] = True
            sub_cat_names = [sub["categoryName"] for sub in selected_sub_cats]

            # 返回最终工具匹配结果
            yield {
                "type": "result",
                "data": result,
                "main_categories": main_cat_names,
                "sub_categories": sub_cat_names,
                "success": True,
            }

        except Exception as e:
            error_msg = str(e)
            result["error"] = f"工具匹配失败：{error_msg}"
            result["reasons"] += f"工具匹配失败：{error_msg}"
            yield {
                "type": "error",
                "data": result,
                "message": error_msg,
                "success": False,
            }
