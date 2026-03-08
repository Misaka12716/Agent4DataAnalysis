# 导入基础规划器类，提供规划器的基础接口和功能
from planner.base_planner import BasePlanner

# 导入知识库模块，用于知识检索和管理
from knowledge.knowledge_base import KnowledgeBase

# 导入LLM响应处理工具，包含AI对话、会话管理等功能
from utils.llm_response import (
    ai_response,  # AI单次响应生成
    ai_chat,  # AI多轮对话（支持流式）
    new_chat_session,  # 创建新的对话会话
    get_chat_history,  # 获取对话历史
    close_chat_session,  # 关闭对话会话
)

# 导入提示词构建器，用于生成标准化的LLM提示词
from prompts.prompt_builder import PromptBuilder
# 系统提示词统一从 configs 读取
from configs.prompts import get_system_prompt
# 工作区与 Excel Schema 增强
from utils.workspace_manager import resolve_workspace_root
from utils.dataframe_reader import read_workspace_excel_schema_and_sample
from db.session_store import SessionStore

# 导入工具类别管理器，用于工具分类和匹配
from tools.tools_category_manager import ToolsCategoryManager

# 导入异步HTTP会话，用于网络请求
from aiohttp import ClientSession

# 导入类型注解相关模块
from typing import Callable, List, Dict, Any, AsyncGenerator
import json  # JSON数据处理
import os
import json_repair  # JSON数据修复工具（处理LLM返回的不规范JSON）
from typing import AsyncGenerator, Dict, Any  # 重复导入，实际可优化


# -------------------------- 类型定义 --------------------------
# 工具元数据类型：包含工具的名称、描述、参数等信息
ToolMetadata = Dict[str, str]
# 工具元数据获取器：无参数，返回工具元数据列表的可调用对象
ToolMetadataFetcher = Callable[[], List[ToolMetadata]]


class AgentPlanner(BasePlanner):
    """
    多智能体规划器类（流式版本）
    继承自基础规划器，实现完整的多智能体任务规划流程，支持流式返回处理结果
    主要功能：需求结构化分析、过往经验搜索、知识检索、任务分解分配、工具匹配
    """

    def __init__(
        self,
        http_session: ClientSession,
        knowledge_base: KnowledgeBase = None,
    ):
        """
        初始化多智能体规划器

        参数：
            http_session: 异步HTTP会话对象，用于网络请求（如调用LLM API）
            knowledge_base: 知识库实例，用于知识检索（可选）
        """
        self.knowledge_base = knowledge_base  # 知识库实例
        self.http_session = http_session  # 异步HTTP会话
        self.prompt_builder = PromptBuilder("zh")  # 初始化提示词构建器（中文）
        # 系统提示词统一从 configs.prompts 读取，严禁硬编码
        self.system_prompt = get_system_prompt("planner", "zh") or self.prompt_builder.build_system_prompt("planner")
        self.plan_session_id = new_chat_session(
            system_prompt=self.system_prompt
        )  # 创建规划器专属对话会话
        self.tools_category_manager = ToolsCategoryManager()  # 初始化工具类别管理器
        self._closed = False  # 资源关闭状态标记

    async def close(self):
        """
        关闭规划器资源（异步）
        包括：LLM对话会话、HTTP会话、知识库连接
        """
        if self._closed:  # 避免重复关闭
            return
        print(f"[Planner] 开始关闭资源（会话ID：{self.plan_session_id}）")

        # 关闭LLM对话会话
        if self.plan_session_id:
            try:
                close_chat_session(self.plan_session_id)
                print(f"[Planner] LLM会话关闭成功（ID：{self.plan_session_id}）")
            except Exception as e:
                print(f"[Warning] LLM会话关闭失败：{str(e)}")

        # 关闭HTTP会话
        if self.http_session and not self.http_session.closed:
            try:
                await self.http_session.close()
                print(f"[Planner] HTTP会话关闭成功")
            except Exception as e:
                print(f"[Warning] HTTP会话关闭失败：{str(e)}")

        # 关闭知识库连接
        if self.knowledge_base:
            await self.knowledge_base.close()
            print(f"[Planner] 知识库关闭成功")

        self._closed = True  # 标记为已关闭

    async def __aenter__(self):
        """异步上下文管理器进入方法，支持async with语法"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        异步上下文管理器退出方法
        自动关闭资源，即使发生异常
        """
        print("[Planner] 异步上下文管理器退出，开始关闭资源...")
        print(f"Exception type: {exc_type}, value: {exc_val}, traceback: {exc_tb}")
        await self.close()
        return False  # 不抑制异常

    # -------------------------- 1. 结构化需求组织（流式版本） --------------------------
    async def organize_requirement(
        self,
        input_data: str,
        file_info: str = "No files uploaded",
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        流式解析用户原始需求，生成结构化需求

        参数：
            input_data: 用户输入的原始需求文本
            file_info: 上传文件信息（默认：无文件上传）

        返回：
            AsyncGenerator: 流式返回处理过程，包含以下类型：
                - type="status": 处理阶段状态
                - type="llm_chunk": LLM流式输出片段
                - type="llm_complete": LLM完整响应
                - type="result": 最终结构化需求结果
                - type="error": 错误信息
        """
        # 输出开始解析状态
        yield {
            "type": "status",
            "message": f"开始解析需求：{input_data}",
            "step": "start",
        }

        # 构建需求分析提示词
        prompt = self.prompt_builder.build_user_prompt(
            role="planner",  # 角色：规划器
            task="req_analysis",  # 任务：需求分析
            input_data=input_data,  # 用户输入数据
            file_info=file_info,  # 文件信息
        )
        yield {"type": "status", "message": "调用LLM获取响应", "step": "call_llm"}

        try:
            # 调用流式AI对话接口
            llm_stream = ai_chat(
                session_id=self.plan_session_id,  # 规划器会话ID
                prompt=prompt.strip(),  # 格式化后的提示词
                http_session=self.http_session,  # HTTP会话
                need_thinking=True,  # 需要返回思考过程
                stream=True,  # 开启流式响应
            )

            full_content = ""  # 存储LLM完整响应内容
            thinking = ""  # 存储LLM思考过程
            # 迭代处理LLM流式响应
            async for chunk in llm_stream:
                if chunk["type"] == "chunk":
                    # 返回LLM流式片段
                    yield {
                        "type": "llm_chunk",
                        "content": chunk["content"],
                        "model": chunk["model"],
                    }
                elif chunk["type"] == "complete":
                    # LLM响应完成，存储完整内容和思考过程
                    full_content = chunk["content"]
                    thinking = chunk["thinking"]
                    yield {
                        "type": "llm_complete",
                        "content": full_content,
                        "thinking": thinking,
                    }

            # 解析LLM返回的JSON结果
            yield {
                "type": "status",
                "message": "解析LLM返回的JSON结果",
                "step": "parse_result",
            }
            # 修复可能不规范的JSON
            repaired_json = json_repair.repair_json(full_content)
            # 解析为结构化字典
            structured_req = json.loads(repaired_json)
            # 格式化JSON字符串（便于展示）
            parsed_result = json.dumps(structured_req, ensure_ascii=False, indent=2)

            # 返回最终结构化需求结果
            yield {
                "type": "result",
                "structured_requirement": structured_req,
                "thinking": thinking,
                "success": True,
            }

        except Exception as e:
            # 异常处理，返回错误信息
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
        """
        根据结构化需求搜索过往相似任务经验（流式）

        参数：
            requirement: 结构化需求字典

        返回：
            AsyncGenerator: 流式返回搜索过程，包含以下类型：
                - type="status": 处理阶段状态
                - type="item_process": 单个历史项处理开始
                - type="llm_chunk": 单个项的LLM输出片段
                - type="item_result": 单个项的处理结果
                - type="result": 最终有效经验列表
        """
        # 获取任务类型（用于针对性搜索）
        task_type = requirement.get("task_type", "Unknown Type")
        yield {
            "type": "status",
            "message": f"搜索{task_type}相关经验（相似度≥70%）",
            "step": "start",
        }

        task_history = []  # 历史任务列表（实际场景需替换为数据库查询）
        valid_results: List[Dict[str, Any]] = []  # 有效经验结果列表

        # 无历史任务记录处理
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

        # 开始处理历史记录
        yield {
            "type": "status",
            "message": f"开始处理{len(task_history)}条历史记录",
            "step": "process_items",
        }

        # 遍历每条历史记录
        for idx, item in enumerate(task_history, 1):
            yield {
                "type": "item_process",
                "item_index": idx,
                "item": item,
                "step": "start",
            }
            try:
                # 构建经验匹配提示词
                prompt = self.prompt_builder.build_user_prompt(
                    role="planner",  # 角色：规划器
                    task="exp_search",  # 任务：经验搜索
                    structured_req=requirement,  # 结构化需求
                    task_history=item,  # 当前历史任务项
                )

                # 流式调用LLM分析历史项相似度
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
                        # 返回单个历史项的LLM流式片段
                        yield {
                            "type": "llm_chunk",
                            "item_index": idx,
                            "content": chunk["content"],
                        }
                    elif chunk["type"] == "complete":
                        full_content = chunk["content"]
                        thinking = chunk["thinking"]

                # 解析LLM返回结果
                repaired_json = json_repair.repair_json(full_content)
                ai_content = json.loads(repaired_json.strip())

                # 验证必要字段
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

                # 验证相似度格式（转换为整数）
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

                # 过滤相似度≥70%的有效经验
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
                # 单个历史项处理异常
                error_msg = str(e)
                yield {
                    "type": "item_result",
                    "item_index": idx,
                    "status": "error",
                    "error": error_msg,
                }
                continue

        # 无有效经验时的默认结果
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

        # 返回最终经验搜索结果
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
        """
        知识搜索模块（占位实现，待集成）
        计划从知识库中检索与需求相关的知识

        参数：
            requirement: 结构化需求字典

        返回：
            AsyncGenerator: 流式返回占位信息
        """
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
        """
        根据结构化需求、知识和经验，分解为子任务并分配执行角色（流式）

        参数：
            requirement: 结构化需求字典
            knowledge: 知识搜索结果（默认：空）
            experience: 经验搜索结果（默认：空）

        返回：
            AsyncGenerator: 流式返回任务分解过程及结果
        """
        knowledge = knowledge or {}  # 处理空知识
        experience = experience or {}  # 处理空经验
        goal = requirement.get("goal", "Unknown Goal")  # 获取需求目标
        yield {"type": "status", "message": f"开始分解任务：{goal}", "step": "start"}

        # 构建任务分解提示词
        prompt = self.prompt_builder.build_user_prompt(
            role="planner",  # 角色：规划器
            task="assign_tasks",  # 任务：任务分配
            structured_req=requirement,  # 结构化需求
            searched_experience=experience,  # 搜索到的经验
            collected_knowledge=knowledge,  # 收集到的知识
        )

        try:
            # 流式调用LLM进行任务分解
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

            # 解析分解后的子任务
            yield {
                "type": "status",
                "message": "解析分解后的子任务",
                "step": "parse_tasks",
            }
            # 清理LLM返回的代码块标记（如```json）
            cleaned_content = (
                full_content.replace("```json", "").replace("```", "").strip()
            )
            # 修复可能不规范的JSON（包括控制字符等问题）
            repaired_content = json_repair.repair_json(cleaned_content)
            # 解析为子任务列表
            tasks = json.loads(repaired_content)

            # 验证子任务结构
            if not isinstance(tasks, list):
                raise ValueError("输出必须为JSON数组")
            if len(tasks) > 10:
                raise ValueError(f"子任务数量超过限制：{len(tasks)} > 10")

            # 验证子任务字段和唯一性
            task_ids = []
            required_fields = [
                "task_id",  # 任务ID（唯一）
                "task_name",  # 任务名称
                "description",  # 任务描述
                "dependencies",  # 依赖任务ID列表
                "worker_type",  # 执行角色类型
                "input",  # 任务输入
                "output",  # 任务输出
            ]
            for task in tasks:
                # 检查必要字段
                missing = [f for f in required_fields if f not in task]
                if missing:
                    raise ValueError(f"子任务缺少字段：{missing}")
                # 检查task_id唯一性
                tid = task["task_id"]
                if tid in task_ids:
                    raise ValueError(f"重复task_id：{tid}")
                task_ids.append(tid)

            # 返回最终任务分解结果
            yield {
                "type": "result",
                "tasks": tasks,
                "thinking": thinking,
                "total_tasks": len(tasks),
                "success": True,
            }

        except Exception as e:
            # 任务分解异常处理
            error_msg = str(e)
            yield {
                "type": "error",
                "message": f"任务分解失败：{error_msg}",
                "success": False,
                "thinking": thinking if "thinking" in locals() else "",
            }

    def _get_workspace_file_info(self, session_id: str) -> str:
        """根据 session_id 获取工作区 input 目录的 Excel Schema/样本，用于规划前输入增强。"""
        workspace_abs = SessionStore.get_workspace_path(session_id) or (
            resolve_workspace_root(session_id) if session_id else None
        )
        if not workspace_abs:
            return "No files uploaded"
        input_dir = os.path.join(workspace_abs, "input")
        excel_info = read_workspace_excel_schema_and_sample(input_dir)
        try:
            return json.dumps(excel_info, ensure_ascii=False, default=str)
        except Exception:
            return excel_info.get("summary", "No files uploaded")

    async def run_flow_with_workspace(
        self, session_id: str, input_requirement: str
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        带工作区上下文的规划流程：先读取工作区 input/ 下 Excel 的 Schema 与样本，再执行规划。
        供流式任务接口绑定 Session 时使用。
        """
        file_info = self._get_workspace_file_info(session_id)
        async for item in self.run_flow(input_requirement, file_info):
            yield item

    async def run_flow(
        self, input_requirement: str, file_info: str = "No files uploaded"
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        运行完整的多智能体规划流程（整合所有步骤）

        参数：
            input_requirement: 用户原始需求文本
            file_info: 上传文件信息（默认：无文件上传）

        返回：
            AsyncGenerator: 流式返回完整流程的每个阶段结果
        """
        # 初始化流程结果存储
        result = {
            "输入需求": input_requirement,
            "结构化需求": None,
            "经验搜索结果": None,
            "知识搜索结果": None,
            "任务分配结果": None,
            "执行成功": False,
            "错误信息": None,
        }

        try:
            # 输出初始化信息
            yield {
                "type": "status",
                "stage": "初始化",
                "message": f"规划器会话ID：{self.plan_session_id}",
                "session_id": self.plan_session_id,
            }

            # 1. 需求结构化处理
            yield {
                "type": "step_start",
                "step": 1,
                "name": "需求结构化分析",
                "description": "开始对原始需求进行结构化处理",
            }

            # 调用需求结构化方法并处理流式结果
            organize_gen = self.organize_requirement(input_requirement, file_info)
            structured_result = None
            async for data in self._process_stream_generator(
                organize_gen,
                step_name="需求结构化",
                return_key="structured_requirement",
            ):
                yield {**data, "step": 1}
                if data.get("type") == "step_result":
                    structured_result = data["result"]

            result["结构化需求"] = structured_result
            yield {
                "type": "step_complete",
                "step": 1,
                "name": "需求结构化分析",
                "result": structured_result,
            }

            # 2. 历史经验搜索
            yield {
                "type": "step_start",
                "step": 2,
                "name": "过往经验搜索",
                "description": "搜索历史经验库获取相关案例",
            }

            experience_gen = self.search_experience(structured_result)
            experience_result = None
            async for data in self._process_stream_generator(
                experience_gen, step_name="经验搜索", return_key="results"
            ):
                yield {**data, "step": 2}
                if data.get("type") == "step_result":
                    experience_result = data["result"]

            result["经验搜索结果"] = experience_result
            yield {
                "type": "step_complete",
                "step": 2,
                "name": "过往经验搜索",
                "result": experience_result,
            }

            # 3. 知识库检索
            yield {
                "type": "step_start",
                "step": 3,
                "name": "知识库检索",
                "description": "从知识库中检索相关知识",
            }

            knowledge_gen = self.search_knowledge(structured_result)
            knowledge_result = None
            async for data in self._process_stream_generator(
                knowledge_gen, step_name="知识搜索", return_key="knowledge"
            ):
                yield {**data, "step": 3}
                if data.get("type") == "step_result":
                    knowledge_result = data["result"]

            result["知识搜索结果"] = knowledge_result
            yield {
                "type": "step_complete",
                "step": 3,
                "name": "知识库检索",
                "result": knowledge_result,
            }

            # 4. 任务分解与分配
            yield {
                "type": "step_start",
                "step": 4,
                "name": "任务分解与分配",
                "description": "将结构化需求分解为具体任务并分配",
            }

            assign_gen = self.assign_tasks(
                requirement=structured_result,
                knowledge=knowledge_result,
                experience=experience_result,
            )
            task_result = None
            async for data in self._process_stream_generator(
                assign_gen, step_name="任务分配", return_key="tasks"
            ):
                yield {**data, "step": 4}
                if data.get("type") == "step_result":
                    task_result = data["result"]

            # 明确执行模式与各代码文件相对路径（供 Coder/Worker 使用）
            tasks_list = task_result or []
            execution_mode = "simple" if len(tasks_list) <= 1 else "complex"
            code_file_paths = (
                ["code/main.py"]
                if execution_mode == "simple"
                else [f"code/task_{t.get('task_id', i)}.py" for i, t in enumerate(tasks_list, 1)]
            )
            for i, t in enumerate(tasks_list):
                t["relative_path"] = code_file_paths[i] if i < len(code_file_paths) else f"code/task_{t.get('task_id', i)}.py"

            task_assign_result = {
                "tasks": tasks_list,
                "total_tasks": len(tasks_list),
                "success": True,
                "execution_mode": execution_mode,
                "code_file_paths": code_file_paths,
            }
            result["任务分配结果"] = task_assign_result
            result["execution_mode"] = execution_mode
            result["code_file_paths"] = code_file_paths
            yield {
                "type": "step_complete",
                "step": 4,
                "name": "任务分解与分配",
                "result": task_assign_result,
            }

            # 流程执行成功
            result["执行成功"] = True

            # 返回最终流程结果
            yield {
                "type": "stage_result",
                "success": True,
                "data": result,
                "message": "规划器流程执行完成",
            }

        except Exception as e:
            # 流程整体异常处理
            error_msg = str(e)
            result["错误信息"] = error_msg
            result["执行成功"] = False

            yield {
                "type": "error",
                "message": f"Planner流程异常: {error_msg}",
                "error": error_msg,
                "result": result,
            }
            print(f"Planner流程异常: {error_msg}")

    async def _process_stream_generator(
        self, gen, step_name: str, return_key: str = "structured_requirement"
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        内部工具方法：统一处理流式生成器，标准化输出格式

        参数：
            gen: 待处理的异步生成器
            step_name: 当前步骤名称（用于日志/状态标识）
            return_key: 最终结果的提取键名

        返回：
            AsyncGenerator: 标准化的流式输出
        """
        final_result = None  # 存储最终结果

        # 迭代处理生成器的每个输出
        async for data in gen:
            if data["type"] == "status":
                # 标准化状态输出
                yield {
                    "type": "status",
                    "stage": step_name,
                    "sub_step": data.get("step", "unknown"),
                    "message": data["message"],
                }

            elif data["type"] == "llm_chunk":
                # 标准化LLM流式片段输出
                yield {
                    "type": "content",
                    "stage": step_name,
                    "content_type": "llm_chunk",
                    "content": data["content"],
                }

            elif data["type"] == "llm_complete":
                # 标准化LLM完整响应输出
                yield {
                    "type": "content",
                    "stage": step_name,
                    "content_type": "llm_complete",
                    "thinking": data.get("thinking", "无"),
                    "content": data.get("content", "无"),
                }

            elif data["type"] == "result":
                # 提取最终结果
                final_result = data.get(return_key, data)

            elif data["type"] == "error":
                # 标准化错误输出并抛出异常
                error_msg = f"{step_name}执行失败：{data['message']}（{data.get('raw_input', '')}）"
                yield {
                    "type": "error",
                    "stage": step_name,
                    "message": error_msg,
                    "raw_error": data,
                }
                raise ValueError(error_msg)

            elif data["type"] == "item_result":
                # 标准化单个项的处理结果输出
                yield {
                    "type": "item_status",
                    "stage": step_name,
                    "item_index": data.get("item_index", "unknown"),
                    "status": data["status"],
                    "reason": data.get("reason", ""),
                }

        # 检查是否获取到有效结果
        if not final_result:
            error_msg = f"{step_name}未返回有效结果"
            yield {"type": "error", "stage": step_name, "message": error_msg}
            raise ValueError(error_msg)

        # 返回标准化的最终结果
        yield {"type": "step_result", "stage": step_name, "result": final_result}


# -------------------------- 测试代码 --------------------------
if __name__ == "__main__":
    import asyncio
    import aiohttp

    # 异步测试主函数
    async def test_agent_planner_flow():
        """测试AgentPlanner的完整运行流程"""
        print("=== 开始测试多智能体规划器 ===")

        # 创建异步HTTP会话
        async with aiohttp.ClientSession() as http_session:
            # 实例化规划器（使用异步上下文管理器自动管理资源）
            async with AgentPlanner(http_session=http_session) as planner:
                # 测试用的原始需求
                test_input = "我要根据这个excel的表格的各列的值，对各行数据进行分类，然后生成一个新的表格，包含分类结果。"
                # 模拟文件信息（可选）
                test_file_info = "No files uploaded"

                # 运行完整规划流程并遍历流式结果
                async for flow_data in planner.run_flow(
                    input_requirement=test_input, file_info=test_file_info
                ):
                    if flow_data["type"] == "status":
                        print(f"🔄 [{flow_data['stage']}] {flow_data['message']}")
                    elif flow_data["type"] == "content":
                        if flow_data["content_type"] == "llm_chunk":
                            print(
                                flow_data["content"], end="", flush=True
                            )  # 实时输出LLM片段
                        elif flow_data["content_type"] == "llm_complete":
                            print(
                                f"\n\n💡 [LLM完整响应] 思考内容：{flow_data.get('thinking', '无')}\n内容：{flow_data.get('content', '无')}\n"
                            )
                    elif flow_data["type"] == "error":
                        print(f"\n❌ 错误：{flow_data['message']}\n")

                    else:
                        print(f"\n{flow_data}\n")  # 原始数据输出（调试用）

    # 运行异步测试函数
    try:
        asyncio.run(test_agent_planner_flow())
    except Exception as e:
        print(f"\n❌ 测试执行失败：{str(e)}")
    finally:
        print("\n=== 测试结束 ===")
