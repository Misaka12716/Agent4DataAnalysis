from fastapi import FastAPI, Request, HTTPException, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from typing import AsyncGenerator, Optional, List
import json
import asyncio
import os
from datetime import datetime
from backend.console import ConsoleAgentWorkflow  # 保持原有导入
from utils.workspace_manager import init_workspace
from db.session_store import SessionStore
from planner.agent_planner import AgentPlanner
from coder.workspace_coder import generate_and_write_code
from worker.workspace_worker import run_workspace_tasks
from reporter.report_agent import stream_report

# -------------------------- 配置与初始化 --------------------------
app = FastAPI(title="Agent Workflow Server", version="1.1")  # 版本更新为1.1

# 配置CORS（保持原有配置）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 配置文件上传参数
UPLOAD_DIR = "./backend/uploads"  # 文件存储目录（相对于项目根目录）
MAX_FILE_SIZE = 2048 * 1024 * 1024  # 最大文件大小（2048M，与Nginx配置一致）

# 创建上传目录（不存在则自动创建）
os.makedirs(UPLOAD_DIR, exist_ok=True)
# 设置目录权限（确保服务器可读写）
os.chmod(UPLOAD_DIR, 0o755)


# -------------------------- 请求模型 --------------------------
class WorkflowRequest(BaseModel):
    input_data: str
    file_info: Optional[str] = "No files uploaded"
    """
    可选参数：上传文件后的存储路径（从 /upload-file 接口返回的 file_path 字段获取）
    示例："./backend/uploads/20251203_1015_test.pdf"
    """


class StreamingTaskRequest(BaseModel):
    """流式分析任务请求：绑定会话，执行 Planner-Coder-Worker-Reporter"""
    session_id: str
    input_data: str



# -------------------------- 流式分析任务（会话绑定 + 快照增量） --------------------------
async def streaming_task_generator(
    session_id: str, input_data: str
) -> AsyncGenerator[str, None]:
    """
    绑定 Session_ID，加载工作区上下文，执行 Planner → Coder → Worker → Reporter。
    每产生一个片段先更新会话「完整内容」+ 版本号，再推送 SSE 给前端。
    """
    def _push(session_id: str, payload: dict) -> str:
        """更新会话内容并返回 SSE 行。"""
        try:
            fragment = json.dumps(payload, ensure_ascii=False)
            SessionStore.append_content(session_id, fragment + "\n")
        except Exception:
            pass
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    try:
        async with AgentPlanner() as planner:
                # 1. Planner（带工作区 Excel 增强）
                plan_data = None
                async for event in planner.run_flow_with_workspace(session_id, input_data):
                    yield _push(session_id, {"type": "planner", "data": event})
                    if event.get("type") == "stage_result" and event.get("data"):
                        plan_data = event["data"]

                if not plan_data or not plan_data.get("任务分配结果"):
                    yield _push(session_id, {
                        "type": "error",
                        "message": "规划未产出任务分配结果",
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    })
                    return

                task_result = plan_data["任务分配结果"]
                tasks = task_result.get("tasks") or []
                execution_mode = task_result.get("execution_mode", "simple")
                code_file_paths = task_result.get("code_file_paths") or ["code/main.py"]
                planner_summary = json.dumps(
                    {"execution_mode": execution_mode, "tasks": tasks},
                    ensure_ascii=False,
                )

                # 2. Coder：按任务生成代码并写入工作区
                code_specs = []
                for t in tasks:
                    inp = t.get("input")
                    out = t.get("output")
                    code_specs.append({
                        "task_desc": t.get("description", ""),
                        "input_var_name": "input_data",
                        "input_var_desc": (inp[0] if isinstance(inp, list) and inp else "输入数据"),
                        "output_var_name": "output_result",
                        "output_var_desc": (out[0] if isinstance(out, list) and out else "输出结果"),
                        "relative_path": t.get("relative_path", "code/main.py"),
                    })
                if not code_specs and code_file_paths:
                    code_specs = [{
                        "task_desc": input_data,
                        "input_var_name": "input_data",
                        "input_var_desc": "输入",
                        "output_var_name": "output_result",
                        "output_var_desc": "输出",
                        "relative_path": code_file_paths[0],
                    }]
                coder_results = generate_and_write_code(session_id, code_specs)
                yield _push(session_id, {"type": "coder", "data": coder_results})

                # 3. Worker：在工作区内执行代码
                worker_results = run_workspace_tasks(
                    session_id, execution_mode, code_file_paths
                )
                yield _push(session_id, {"type": "worker", "data": worker_results})

                # 4. Reporter：流式报告
                async for chunk in stream_report(planner_summary, worker_results):
                    yield _push(session_id, {"type": "report_chunk", "content": chunk})

                yield _push(session_id, {
                    "type": "streaming_ended",
                    "message": "分析任务流式输出结束",
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
    except Exception as e:
        yield _push(session_id, {
            "type": "streaming_error",
            "error": str(e),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })


# -------------------------- 流式响应处理 --------------------------
async def workflow_stream_generator(
    input_data: str, file_info: str
) -> AsyncGenerator[str, None]:
    """工作流生成器（新增文件存在性校验警告）"""
    # 新增：校验file_info中的文件是否存在（如果传入了文件路径）
    if file_info != "No files uploaded" and not os.path.exists(file_info):
        yield f"data: {json.dumps({
            'type': 'workflow_warning',
            'message': f'指定的文件不存在：{file_info}',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }, ensure_ascii=False)}\n\n"

    # 原有工作流逻辑
    workflow = ConsoleAgentWorkflow()
    try:
        async for result in workflow.run_workflow(input_data, file_info):
            yield f"data: {json.dumps(result, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({
            'type': 'workflow_ended', 
            'message': '工作流执行完成',
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        })}\n\n"
    except Exception as e:
        error_data = {
            "type": "workflow_error",
            "error": str(e),
            "message": "工作流执行过程中发生错误",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        print(f"[Error] 工作流执行失败：{str(e)}")
        yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"


# -------------------------- API路由 --------------------------
@app.get("/health")  # 保持原有健康检查
async def health_check():
    return JSONResponse(
        content={
            "status": "healthy",
            "service": "agent-workflow-server",
            "version": "1.1",
        },
        status_code=200,
    )

# -------------------------- 会话工作区与状态接口 --------------------------

@app.post("/session/upload-excel")
async def session_upload_excel(
    request: Request,
    file: UploadFile = File(...),
    session_id: str = Form(...),
    user_id: int = Form(0),
):
    """
    上传 Excel 到会话工作区。若该会话尚无工作区则先创建，文件保存到工作区的 input/ 子目录。
    并更新数据库中的会话工作区路径。
    """
    if not session_id.strip():
        raise HTTPException(status_code=400, detail="session_id 不能为空")
    file_size = 0
    for chunk in file.file:
        file_size += len(chunk)
        if file_size > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail=f"文件过大（最大 {MAX_FILE_SIZE // (1024*1024)}MB）")
    await file.seek(0)

    workspace_abs = init_workspace(session_id)
    input_dir = os.path.join(workspace_abs, "input")
    os.makedirs(input_dir, exist_ok=True)
    safe_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{file.filename.replace(' ', '_')}"
    save_path = os.path.join(input_dir, safe_name)
    try:
        with open(save_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存文件失败: {e}")

    ok, err = SessionStore.set_workspace_path(session_id, user_id, workspace_abs)
    if not ok and err:
        pass  # 仅记录，不阻断上传成功

    return JSONResponse(
        content={
            "status": "success",
            "message": "文件已写入会话工作区 input/",
            "session_id": session_id,
            "relative_path": f"input/{safe_name}",
            "workspace_abs_path": workspace_abs,
        },
        status_code=200,
    )


@app.get("/session/snapshot")
async def session_snapshot(session_id: str):
    """
    会话快照：前端首次加载或断线重连时调用，返回该会话的「完整累计内容」和当前「版本号」。
    """
    try:
        content, version = SessionStore.get_latest_content(session_id)
    except Exception:
        content, version = "", 0
    return JSONResponse(
        content={"content": content or "", "version": version},
        status_code=200,
    )


@app.post("/run-analysis")
async def run_analysis(request: Request, body: StreamingTaskRequest):
    """
    流式分析任务：绑定 session_id，加载工作区上下文，执行完整分析链路；
    后端每产生新片段先更新「完整内容」+ 版本号，再 SSE 推送给前端。
    """
    try:
        return StreamingResponse(
            streaming_task_generator(body.session_id, body.input_data),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"启动分析任务失败: {str(e)}")


# -------------------------- 启动服务器 --------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.server:app",
        host="0.0.0.0",
        port=52716,
        reload=True,
        workers=1,
    )
