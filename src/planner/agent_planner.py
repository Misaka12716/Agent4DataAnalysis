# 使用 LangGraph 定义规划器流程，便于阅读与维护
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from configs.prompts import get_system_prompt, get_user_prompt
from utils.config import OPENAI_COMPATIBLE_API_BASE, API_KEY, DEFAULT_MODEL
from utils.workspace_manager import resolve_workspace_root
from utils.dataframe_reader import read_workspace_excel_schema_and_sample
from db.session_store import SessionStore

from typing import Dict, Any, AsyncGenerator, Optional, List
from langchain_core.messages import BaseMessage
import asyncio
import json
import os
import json_repair


def _parse_thinking_and_content(raw: str) -> tuple[str, str]:
    """从模型原始输出中分离 <think>...</think> 与正文。"""
    if "</think>" in raw and raw.strip().startswith("<think>"):
        idx = raw.find("</think>")
        thinking = raw[7:idx].strip()
        content = raw[idx + 8:].strip()
        return thinking, content
    return "", raw.strip()


def _create_llm(streaming: bool = True) -> ChatOpenAI:
    """创建 LangChain ChatOpenAI 实例。"""
    return ChatOpenAI(
        model=DEFAULT_MODEL,
        temperature=0.3,
        api_key=API_KEY,
        base_url=OPENAI_COMPATIBLE_API_BASE,
        streaming=streaming,
    )


# -------------------------- 节点内：需求结构化 --------------------------
async def _node_req_analysis(state: Dict[str, Any]) -> Dict[str, Any]:
    """节点：解析用户需求，产出结构化需求 JSON。"""
    cb = state.get("stream_callback")
    messages: List[BaseMessage] = state.get("messages") or []
    lang = state.get("lang", "zh")
    input_data = state["input_requirement"]
    file_info = state.get("file_info") or "No files uploaded"

    if cb:
        await cb("state", {"node": "req_analysis"})

    user_prompt = get_user_prompt(
        "planner", "req_analysis", lang=lang,
        input_data=input_data,
        file_info=file_info,
    )
    full_content = ""

    llm = _create_llm(streaming=True)
    chat_messages = messages + [HumanMessage(content=user_prompt.strip())]

    async for chunk in llm.astream(chat_messages):
        token = chunk.content if hasattr(chunk, "content") else chunk.get("content", "") or ""
        if not token:
            continue
        full_content += token
        if cb:
            await cb("llm_chunk", {"content": token})

    thinking, content = _parse_thinking_and_content(full_content)
    if cb:
        await cb("llm_complete", {"content": content, "thinking": thinking})

    new_messages = messages + [HumanMessage(content=user_prompt.strip()), AIMessage(content=full_content)]

    try:
        repaired = json_repair.repair_json(content)
        structured_req = json.loads(repaired)
        return {"structured_req": structured_req, "messages": new_messages, "error": None}
    except Exception as e:
        return {"structured_req": None, "messages": new_messages, "error": f"需求解析失败：{str(e)}"}


# -------------------------- 节点内：任务分解与分配 --------------------------
async def _node_assign_tasks(state: Dict[str, Any]) -> Dict[str, Any]:
    """节点：根据结构化需求分解为子任务列表。"""
    cb = state.get("stream_callback")
    messages: List[BaseMessage] = state.get("messages") or []
    lang = state.get("lang", "zh")
    requirement = state.get("structured_req")
    if not requirement:
        return {"tasks": None, "error": "缺少结构化需求"}

    if cb:
        await cb("state", {"node": "assign_tasks"})

    user_prompt = get_user_prompt(
        "planner", "assign_tasks", lang=lang,
        structured_req=requirement,
    )
    full_content = ""

    llm = _create_llm(streaming=True)
    chat_messages = messages + [HumanMessage(content=user_prompt)]

    async for chunk in llm.astream(chat_messages):
        token = chunk.content if hasattr(chunk, "content") else chunk.get("content", "") or ""
        if not token:
            continue
        full_content += token
        if cb:
            await cb("llm_chunk", {"content": token})

    thinking, content = _parse_thinking_and_content(full_content)
    if cb:
        await cb("llm_complete", {"content": content, "thinking": thinking})

    new_messages = messages + [HumanMessage(content=user_prompt), AIMessage(content=full_content)]

    try:
        cleaned = content.replace("```json", "").replace("```", "").strip()
        repaired = json_repair.repair_json(cleaned)
        tasks = json.loads(repaired)
        if not isinstance(tasks, list):
            raise ValueError("输出必须为JSON数组")
        if len(tasks) > 10:
            raise ValueError(f"子任务数量超过限制：{len(tasks)} > 10")

        required = [
            "task_id", "task_name", "description",
            "dependencies", "worker_type", "input", "output",
        ]
        task_ids = []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            if "task_name" not in task or not str(task.get("task_name", "")).strip():
                task["task_name"] = (
                    task.get("name")
                    or task.get("任务名称")
                    or (str(task.get("description", ""))[:20] if task.get("description") else None)
                    or f"任务{task.get('task_id', '?')}"
                )
            if "input" not in task:
                task["input"] = task.get("input", [])
            if "output" not in task:
                task["output"] = task.get("output", [])
            if not isinstance(task.get("input"), list):
                task["input"] = [task["input"]] if task.get("input") else []
            if not isinstance(task.get("output"), list):
                task["output"] = [task["output"]] if task.get("output") else []

            missing = [f for f in required if f not in task]
            if missing:
                raise ValueError(f"子任务缺少字段：{missing}")
            tid = task["task_id"]
            if tid in task_ids:
                raise ValueError(f"重复task_id：{tid}")
            task_ids.append(tid)

        return {"tasks": tasks, "messages": new_messages, "error": None}
    except Exception as e:
        return {"tasks": None, "messages": new_messages, "error": f"任务分解失败：{str(e)}"}


def _route_after_req_analysis(state: Dict[str, Any]) -> str:
    """req_analysis 之后：有错误则结束，否则进入任务分解。"""
    if state.get("error"):
        return "end"
    return "assign_tasks"


def _build_planner_graph():
    """构建 LangGraph：req_analysis -> [assign_tasks | END] -> END。"""
    graph_builder = StateGraph(dict)
    graph_builder.add_node("req_analysis", _node_req_analysis)
    graph_builder.add_node("assign_tasks", _node_assign_tasks)
    graph_builder.set_entry_point("req_analysis")
    graph_builder.add_conditional_edges(
        "req_analysis",
        _route_after_req_analysis,
        path_map={"assign_tasks": "assign_tasks", "end": END},
    )
    graph_builder.add_edge("assign_tasks", END)
    return graph_builder.compile()


# -------------------------- AgentPlanner 类 --------------------------
class AgentPlanner:
    """
    多智能体规划器（LangGraph 版）
    流程：需求结构化 -> 任务分解分配；仅 yield 关键 LLM 内容与少量状态信息。
    """

    def __init__(self):
        self.lang = "zh"
        self.system_prompt = get_system_prompt("planner", self.lang)
        self._closed = False
        self._graph = _build_planner_graph()

    async def close(self):
        if self._closed:
            return
        self._closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
        return False

    def _get_workspace_file_info(self, session_id: str) -> str:
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
        file_info = self._get_workspace_file_info(session_id)
        async for item in self.run_flow(input_requirement, file_info):
            yield item

    async def run_flow(
        self, input_requirement: str, file_info: str = "No files uploaded"
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        运行规划流程，仅 yield：
        - state：每个节点开始时一条
        - llm_chunk / llm_complete：关键 LLM 输出
        - stage_result：最终结果（与原有 data 结构兼容）
        - error：异常时
        """
        queue: asyncio.Queue = asyncio.Queue()
        final_state: Optional[Dict[str, Any]] = None
        run_error: Optional[str] = None

        async def stream_callback(event_type: str, data: Dict[str, Any]):
            await queue.put((event_type, data))

        async def run_graph():
            nonlocal final_state, run_error
            try:
                initial = {
                    "input_requirement": input_requirement,
                    "file_info": file_info,
                    "messages": [SystemMessage(content=self.system_prompt)],
                    "lang": self.lang,
                    "stream_callback": stream_callback,
                }
                async for state in self._graph.astream(initial):
                    final_state = state
                await queue.put(("done", None))
            except Exception as e:
                run_error = str(e)
                await queue.put(("error", {"message": run_error}))

        task = asyncio.create_task(run_graph())

        try:
            while True:
                event_type, data = await queue.get()
                if event_type == "done":
                    break
                if event_type == "error":
                    yield {"type": "error", "message": data.get("message", "未知错误")}
                    return
                if event_type == "state":
                    yield {"type": "state", "node": data.get("node", "")}
                elif event_type == "llm_chunk":
                    yield {"type": "llm_chunk", "content": data.get("content", "")}
                elif event_type == "llm_complete":
                    yield {
                        "type": "llm_complete",
                        "content": data.get("content", ""),
                        "thinking": data.get("thinking", ""),
                    }
            await task
        except asyncio.CancelledError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            raise

        if run_error:
            yield {"type": "error", "message": run_error}
            return

        if not final_state:
            yield {"type": "error", "message": "规划流程未返回状态"}
            return

        err = final_state.get("error")
        if err:
            yield {"type": "error", "message": err}
            return

        structured_req = final_state.get("structured_req")
        tasks_list = final_state.get("tasks") or []
        execution_mode = "simple" if len(tasks_list) <= 1 else "complex"
        code_file_paths = (
            ["code/main.py"]
            if execution_mode == "simple"
            else [f"code/task_{t.get('task_id', i)}.py" for i, t in enumerate(tasks_list, 1)]
        )
        for i, t in enumerate(tasks_list):
            t["relative_path"] = (
                code_file_paths[i] if i < len(code_file_paths) else f"code/task_{t.get('task_id', i)}.py"
            )

        task_assign_result = {
            "tasks": tasks_list,
            "total_tasks": len(tasks_list),
            "success": True,
            "execution_mode": execution_mode,
            "code_file_paths": code_file_paths,
        }
        result = {
            "输入需求": input_requirement,
            "结构化需求": structured_req,
            "任务分配结果": task_assign_result,
            "执行成功": True,
            "错误信息": None,
        }
        result["execution_mode"] = execution_mode
        result["code_file_paths"] = code_file_paths

        yield {
            "type": "stage_result",
            "success": True,
            "data": result,
            "message": "规划器流程执行完成",
        }


# -------------------------- 测试 --------------------------
if __name__ == "__main__":
    async def test_planner():
        async with AgentPlanner() as planner:
            test_input = "根据 excel 各列对各行分类，生成包含分类结果的新表格。"
            async for ev in planner.run_flow(test_input, "No files uploaded"):
                    if ev.get("type") == "state":
                        print(f"[状态] {ev.get('node')}")
                    elif ev.get("type") == "llm_chunk":
                        print(ev.get("content", ""), end="", flush=True)
                    elif ev.get("type") == "llm_complete":
                        print("\n[LLM 完成]")
                    elif ev.get("type") == "stage_result":
                        print("结果:", ev.get("data", {}).keys())
                    elif ev.get("type") == "error":
                        print("错误:", ev.get("message"))

    asyncio.run(test_planner())
