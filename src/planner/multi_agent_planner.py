from planner.base_planner import BasePlanner
from knowledge.knowledge_base import KnowledgeBase
from utils.llm_response import (
    ai_response,
    new_chat_session,
    get_chat_history,
    close_chat_session,
)
from prompts.prompt_builder import PromptBuilder
from tools.tools_category_manager import ToolsCategoryManager
from aiohttp import ClientSession
from typing import Callable, List, Dict, Any
import json
import json_repair


# -------------------------- Type Definitions (Clear Interface Contract) --------------------------
ToolMetadata = Dict[
    str, str
]  # Tool metadata structure: name(tool name), description(function), applicable_scenarios(applicable scenarios)
ToolMetadataFetcher = Callable[
    [], List[ToolMetadata]
]  # Tool metadata fetch interface (to be connected to actual services later)


class MultiAgentPlanner(BasePlanner):
    """Multi-Agent Planner: LLM-driven intelligent task planning implementation"""

    def __init__(
        self,
        http_session: ClientSession,
        knowledge_base: KnowledgeBase = None,
    ):
        """
        Initialize planner
        :param knowledge_base: Knowledge base instance (reserved, not used yet)
        :param http_session: HTTP session required for LLM calls
        """
        self.knowledge_base = knowledge_base
        self.http_session = http_session  # Dependent on asynchronous LLM calls
        self.prompt_builder = PromptBuilder("zh")  # Initialize prompt builder (Chinese)

        # Create a dedicated session (store planning history) and add system_prompt to the planning session
        self.system_prompt = self.prompt_builder.build_system_prompt("planner")
        self.plan_session_id = new_chat_session(system_prompt=self.system_prompt)
        print(f"[Planner] 初始化规划会话（ID：{self.plan_session_id}）")

        # Load tool package information (only categories included)
        self.tools_category_manager = ToolsCategoryManager()

        # Flag: whether resources have been closed
        self._closed = False

    async def close(self):
        """Asynchronously close all resources (supports manual call and automatic call by context manager)"""
        if self._closed:
            return
        print(f"[Planner] 开始关闭资源（会话ID：{self.plan_session_id}）")

        # 1. Close LLM session (assuming the tool class provides close_chat_session method)
        if self.plan_session_id:
            try:
                await close_chat_session(
                    self.plan_session_id
                )  # Asynchronously close session
                print(f"[Planner] LLM会话关闭成功（ID：{self.plan_session_id}）")
            except Exception as e:
                print(f"[Warning] LLM会话关闭失败：{str(e)}")

        # 2. Close aiohttp ClientSession
        if self.http_session and not self.http_session.closed:
            try:
                await self.http_session.close()
                print(f"[Planner] HTTP会话关闭成功")
            except Exception as e:
                print(f"[Warning] HTTP会话关闭失败：{str(e)}")

        # 3. Close knowledge base
        if self.knowledge_base:
            await self.knowledge_base.close()
            print(f"[Planner] 知识库关闭成功")

        # 4. Set flag: resources have been closed
        self._closed = True

    # -------------------------- Asynchronous Context Manager (Auto-close Resources) --------------------------
    async def __aenter__(self):
        """Return the instance itself when entering async with context"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Automatically close resources when exiting async with context"""
        await self.close()
        # If there is an exception, return False to continue throwing, True to indicate handled
        return False

    # -------------------------- 1. Structured Requirement Organization --------------------------
    async def organize_requirement(self, input_data: str) -> Dict[str, Any]:
        print(f"[Planner] 开始解析需求：{input_data}")

        # Design prompt
        prompt = self.prompt_builder.build_user_prompt(
            role="planner",
            task="req_analysis",
            input_data=input_data,
        )

        # Call LLM to parse requirement
        llm_result = await ai_response(
            prompt=prompt.strip(),
            session=self.http_session,
            need_thinking=True,  # Record LLM thinking process (for traceability)
        )

        # Process result: parse JSON + error capture
        if not llm_result["success"]:
            error_detail = llm_result["error"]
            print(f"[Planner] LLM调用失败：{error_detail}")
            return {
                "error": f"LLM call failed: {error_detail}",
                "raw_input": input_data,
            }

        try:
            repaired_json = json_repair.repair_json(llm_result["content"])
            structured_req = json.loads(repaired_json)
            parsed_result = json.dumps(structured_req, ensure_ascii=False, indent=2)
            print(f"[Planner] 需求解析完成\n" f"[Planner] 解析结果：\n{parsed_result}")
            return structured_req
        except json.JSONDecodeError as e:
            error_msg = str(e)
            raw_content = llm_result["content"]
            print(f"[Planner] LLM输出格式错误：{error_msg}，原始内容：{raw_content}")
            return {
                "error": f"LLM output format error: {error_msg}",
                "raw_llm_output": raw_content,
            }

    # -------------------------- 2. Past Experience Search --------------------------
    async def search_experience(
        self, requirement: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        task_type = requirement.get("task_type", "Unknown Type")
        print(f"[Planner] 搜索{task_type}相关经验（筛选相似度≥70%的结果）")
        valid_results: List[Dict[str, Any]] = (
            []
        )  # Final valid results to return (only keep ≥70%)

        # 1. Query similar task history from database (replace with real query logic in actual scenarios)
        # task_history = await self.db.query_similar_tasks(requirement, top_k=5)
        task_history = (
            []
        )  # Temporary placeholder, should be obtained from database in actual use

        # 2. Process historical records one by one, call AI to analyze and filter
        for item in task_history:
            try:
                # Build prompt (fix spelling error)
                prompt = self.prompt_builder.build_user_prompt(
                    role="planner",
                    task="exp_search",
                    structured_req=requirement,
                    task_history=item,
                )

                # Call AI to get result
                llm_result = await ai_response(
                    prompt=prompt.strip(), session=self.http_session, need_thinking=True
                )

                if not llm_result["success"]:
                    error_detail = llm_result["error"]
                    print(f"[Warning] AI调用失败（历史项：{item}）：{error_detail}")
                    continue  # Skip items with failed calls, not included in results

                # Parse AI returned content (assumed to be JSON string)
                try:
                    repaired_json = json_repair.repair_json(llm_result["content"])
                    ai_content = json.loads(repaired_json.strip())
                except json.JSONDecodeError as e:
                    error_msg = str(e)
                    print(
                        f"[Warning] AI返回格式错误（非JSON，历史项：{item}）：{error_msg}"
                    )
                    continue  # Skip items with format errors

                # Verify required fields
                required_fields = ["experience_id", "similarity", "core_content"]
                if not all(field in ai_content for field in required_fields):
                    missing = [f for f in required_fields if f not in ai_content]
                    print(f"[Warning] AI返回缺少字段{missing}（历史项：{item}）")
                    continue  # Skip items with missing fields

                # Process similarity: convert to integer (remove percentage sign) and verify range
                similarity_str = ai_content["similarity"].strip().replace("%", "")
                try:
                    similarity = int(similarity_str)
                    if not (0 <= similarity <= 100):
                        raise ValueError("Similarity must be between 0-100")
                except ValueError as e:
                    error_msg = str(e)
                    print(
                        f"[Warning] 相似度格式错误（{similarity_str}，历史项：{item}）：{error_msg}"
                    )
                    continue  # Skip items with similarity format errors

                # Filter: only keep results with similarity ≥70%
                if similarity >= 70:
                    valid_results.append(
                        {
                            "experience_id": ai_content["experience_id"],
                            "similarity": f"{similarity}%",  # Restore percentage format
                            "core_content": ai_content[
                                "core_content"
                            ],  # Should be valid summary (non-empty) at this time
                            "thinking": llm_result[
                                "thinking"
                            ],  # Keep AI thinking process
                            "history_item": item,  # Associate original historical item
                        }
                    )
                else:
                    # Results below 70% are not included, only print log (optional)
                    print(f"[Filter] 相似度不足（{similarity}% < 70%，历史项：{item}）")

            except Exception as e:
                error_msg = str(e)
                print(f"[Error] 处理历史项{item}时发生异常：{error_msg}")
                continue  # Skip other exceptions directly

        # Handle case with no valid results (including no historical records or all filtered out)
        if not valid_results:
            valid_results.append(
                {
                    "experience_id": "",
                    "similarity": "",
                    "core_content": "No relevant experiences with similarity ≥ 70% found",
                    "thinking": "All historical items do not meet similarity requirements or no historical records",
                    "history_item": None,
                }
            )
            print(f"[Planner] 未找到有效经验结果")

        # Print final valid results
        total = len(valid_results)
        print(f"[Planner] 经验搜索完成，有效结果总数：{total}")
        for idx, res in enumerate(valid_results, 1):
            res_str = json.dumps(res, ensure_ascii=False, indent=2)
            print(f"[Planner] 有效结果{idx}：\n{res_str}")

        return valid_results

    # -------------------------- 3. Knowledge Search (Not Implemented Yet) --------------------------
    async def search_knowledge(self, requirement: Dict[str, Any]) -> Dict[str, Any]:
        task_type = requirement.get("task_type", "Unknown Type")
        print(f"[Planner] 知识搜索模块暂未集成（需求类型：{task_type}）")
        return {
            "knowledge": "Knowledge search agent to be implemented, will support knowledge base retrieval",
            "success": False,
            "message": "Knowledge search module not integrated yet",
        }

    # -------------------------- 4. Task Decomposition and Allocation --------------------------
    async def assign_tasks(
        self,
        requirement: Dict[str, Any],
        knowledge: Dict[str, Any] = None,
        experience: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        knowledge = knowledge or {}
        experience = experience or {}
        goal = requirement.get("goal", "Unknown Goal")
        print(f"[Planner] 开始分解任务：{goal}")
        knowledge_str = json.dumps(knowledge, ensure_ascii=False)
        print(f"[Planner] 任务分解输入 - 知识：{knowledge_str}")
        experience_str = json.dumps(experience, ensure_ascii=False)
        print(f"[Planner] 任务分解输入 - 经验：{experience_str}")

        # Construct prompt (fix parameter spelling)
        prompt = self.prompt_builder.build_user_prompt(
            role="planner",
            task="assign_tasks",
            structured_req=requirement,
            searched_experience=experience,
            collected_knowledge=knowledge,
        )

        # Call LLM to decompose task
        llm_result = await ai_response(
            prompt=prompt,
            session=self.http_session,
            need_thinking=True,
        )

        if not llm_result["success"]:
            error_detail = llm_result["error"]
            print(f"[Planner] 任务分解失败：{error_detail}")
            return {
                "tasks": [],
                "error": f"Task decomposition failed: {error_detail}",
                "success": False,
                "thinking": llm_result.get("thinking", ""),
            }

        # Simplified verification: only focus on task_id uniqueness and basic structure
        try:
            # Clean code block markers
            repaired_content = (
                llm_result["content"].replace("```json", "").replace("```", "").strip()
            )
            tasks = json.loads(repaired_content)

            # Basic structure verification: must be an array and quantity ≤10
            if not isinstance(tasks, list):
                raise ValueError("Output must be a JSON array")
            total_tasks = len(tasks)
            if total_tasks > 10:
                raise ValueError(f"Subtask quantity must be ≤10, actual: {total_tasks}")

            # Extract all task_ids, check uniqueness and existence
            task_ids = []
            required_fields = [
                "task_id",
                "task_name",
                "description",
                "dependencies",
                "worker_type",
            ]
            for task in tasks:
                # Check if required fields exist (only confirm existence, not verify type/length)
                missing = [f for f in required_fields if f not in task]
                if missing:
                    raise ValueError(f"Subtask missing required fields: {missing}")

                # Collect task_ids and check uniqueness
                tid = task["task_id"]
                if tid in task_ids:
                    raise ValueError(f"Duplicate task_id: {tid}")
                task_ids.append(tid)

            tasks_str = json.dumps(tasks, ensure_ascii=False, indent=2)
            print(f"[Planner] 任务分解完成，子任务总数：{total_tasks}")
            print(f"[Planner] 分解后的子任务：\n{tasks_str}")
            return {
                "tasks": tasks,
                "thinking": llm_result["thinking"],
                "success": True,
                "total_tasks": total_tasks,
            }

        except (json.JSONDecodeError, ValueError) as e:
            error_msg = str(e)
            raw_output = llm_result["content"]
            print(f"[Planner] 子任务解析失败：{error_msg}，原始输出：{raw_output}")
            return {
                "tasks": [],
                "error": f"Subtask parsing failed: {error_msg}",
                "success": False,
                "thinking": llm_result.get("thinking", ""),
            }

    # -------------------------- 5. Tool Matching --------------------------
    async def find_tools(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Find suitable tool categories for a single worker subtask (main category first, then subcategory),
        and return all matched specific tools
        :param task: Single subtask structure (including task_name/description, etc.)
        :return: {'main_category': ..., 'sub_category': ..., 'matched_tools': [...], 'reasons': ..., 'success': True/False}
        """
        task_name = task.get("task_name", "Unnamed Task")
        print(f"[Planner] 开始匹配子任务[{task_name}]所需工具类别")
        result = {
            "main_category": [],  # Selected main category list (categoryName/type/reason)
            "sub_category": [],  # Selected subcategory list (categoryName/type/reason/main_category)
            "matched_tools": [],  # Matched specific tool metadata
            "reasons": "",  # Matching reasons (summary of LLM thinking process)
            "success": False,
            "task_name": task_name,
        }

        # 1. Construct a unified task description string
        task_type = task.get("task_type", "Unknown Type")
        task_desc = task.get("description", "无描述")
        task_string_cn = (
            f"任务名称：{task_name}\n"
            f"任务描述：{task_desc}\n"
            f"任务类型：{task_type}"
        )
        print(f"[Planner] 子任务详情：\n{task_string_cn}")

        try:
            # -------------------------- Step 1: Select matched main categories --------------------------
            main_categories = self.tools_category_manager.get_main_categories()
            main_prompt = self.prompt_builder.build_user_prompt(
                role="planner",
                task="tools_select",
                worker_task_desc=f"Task Name: {task_name}\nTask Description: {task_desc}\nTask Type: {task_type}",
                tools_categories=main_categories,
                categories_content="Each main category corresponds to a core processing scenario, select the most matched main category according to task content",
            )

            # Call LLM to select main categories
            main_llm_result = await ai_response(
                prompt=main_prompt.strip(),
                session=self.http_session,
                need_thinking=True,
            )
            if not main_llm_result["success"]:
                raise ValueError(
                    f"LLM call failed for main category selection: {main_llm_result['error']}"
                )

            # Parse main category selection results, output format is JSON array, each element is {categoryName, type, reason}
            main_repaired = json_repair.repair_json(main_llm_result["content"])
            selected_main_cats = json.loads(main_repaired)
            if not isinstance(selected_main_cats, list) or len(selected_main_cats) == 0:
                raise ValueError(
                    "Main category selection result must be a non-empty array"
                )

            # Verify if selected main categories exist in category manager
            valid_main_cats = []
            all_main_chinese = [cat["main_category_chinese"] for cat in main_categories]
            all_main_english = [cat["main_category_english"] for cat in main_categories]
            all_main_names = all_main_chinese + all_main_english
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
                else:
                    print(f"[Warning] 选中的大类不存在：{cat_name}，已过滤")
            if len(valid_main_cats) == 0:
                raise ValueError("No valid selected main categories")
            result["main_category"] = valid_main_cats
            main_cat_names = [cat["categoryName"] for cat in valid_main_cats]
            result["reasons"] += f"选中大类：{main_cat_names}；"

            # -------------------------- Step 2: Select matched subcategories for each main category --------------------------
            selected_sub_cats = []
            for main_cat in valid_main_cats:
                main_cat_name = main_cat["categoryName"]
                # Get all subcategories under current main category
                sub_cats = self.tools_category_manager.get_sub_categories(
                    main_category=main_cat_name
                )
                sub_cat_chinese = [cat["sub_category_chinese"] for cat in sub_cats]
                sub_cat_english = [cat["sub_category_english"] for cat in sub_cats]
                sub_cat_names = sub_cat_chinese + sub_cat_english
                if len(sub_cats) == 0:
                    print(f"[Warning] 大类{main_cat_name}下无亚类，跳过该大类亚类选择")
                    continue

                # Construct subcategory selection prompt
                sub_prompt = self.prompt_builder.build_user_prompt(
                    role="planner",
                    task="tools_select",
                    worker_task_desc=f"Task Name: {task_name}\nTask Description: {task_desc}\nTask Type: {task_type}",
                    tools_categories=sub_cats,
                    categories_content=f"Current main category: {main_cat_name}, select the most matched subcategories under this main category according to task content",
                )

                # Call LLM to select subcategories
                sub_llm_result = await ai_response(
                    prompt=sub_prompt.strip(),
                    session=self.http_session,
                    need_thinking=True,
                )
                if not sub_llm_result["success"]:
                    error_detail = sub_llm_result["error"]
                    print(
                        f"[Warning] 大类{main_cat_name}亚类选择LLM调用失败，跳过：{error_detail}"
                    )
                    continue

                # Parse subcategory selection results (format is JSON array, each element is {categoryName, type, reason})
                sub_repaired = json_repair.repair_json(sub_llm_result["content"])
                cat_sub_cats = json.loads(sub_repaired)
                if not isinstance(cat_sub_cats, list):
                    print(f"[Warning] 大类{main_cat_name}亚类选择结果非数组，跳过")
                    continue

                # Verify subcategory validity and deduplicate, add main_category field
                for sub_cat in cat_sub_cats:
                    sub_cat_name = sub_cat.get("categoryName")
                    # Check if the subcategory belongs to current main category
                    if sub_cat_name not in sub_cat_names:
                        print(
                            f"[Warning] 大类{main_cat_name}选中的亚类不存在：{sub_cat_name}，已过滤"
                        )
                        continue

                    # Construct standard format
                    sub_cat_obj = {
                        "categoryName": sub_cat_name,
                        "type": sub_cat.get("type", ""),
                        "reason": sub_cat.get("reason", ""),
                        "main_category": main_cat_name,
                    }
                    # Deduplicate
                    if sub_cat_obj not in selected_sub_cats:
                        selected_sub_cats.append(sub_cat_obj)
                        result[
                            "reasons"
                        ] += f"大类{main_cat_name}选中亚类：{sub_cat_name}；"

            if len(selected_sub_cats) == 0:
                raise ValueError("No valid selected subcategories")
            result["sub_category"] = selected_sub_cats
            result["success"] = True

            sub_cat_names = [sub["categoryName"] for sub in selected_sub_cats]
            print(f"[Planner] 工具匹配完成")
            print(f"[Planner] 匹配的大类：{main_cat_names}")
            print(f"[Planner] 匹配的亚类：{sub_cat_names}")
            print(f"[Planner] 匹配理由：{result['reasons']}")

        except Exception as e:
            error_msg = str(e)
            result["error"] = f"Tool matching failed: {error_msg}"
            result["reasons"] = f"工具匹配失败：{error_msg}"
            print(f"[Planner] 工具匹配失败：{error_msg}")

        return result
