from fastapi import FastAPI, Request, HTTPException, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from typing import AsyncGenerator, Optional, List
import json
import asyncio
import os
from datetime import datetime
from utils.workspace_manager import (
    init_workspace,
    resolve_workspace_root,
    list_workspace_files,
    generate_data_filename,
)
from utils.dataframe_reader import read_workspace_excel_schema_and_sample
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

                if not plan_data:
                    yield _push(session_id, {
                        "type": "error",
                        "message": "规划阶段未产出有效结果",
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    })
                    return

                requirement_analysis = (plan_data.get("需求解析") or "").strip()
                steps_outline = (plan_data.get("步骤分解") or "").strip()
                if not requirement_analysis or not steps_outline:
                    yield _push(session_id, {
                        "type": "error",
                        "message": "规划结果缺少需求解析或步骤分解",
                        "data": plan_data,
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    })
                    return

                execution_mode = "simple"
                code_file_paths = ["main.py"]
                planner_summary = (plan_data.get("规划全文") or "").strip()
                if not planner_summary:
                    planner_summary = json.dumps(
                        {
                            "需求解析": requirement_analysis,
                            "步骤分解": steps_outline,
                        },
                        ensure_ascii=False,
                    )

                # 2. Coder：按任务生成代码并写入工作区
                code_specs = [{
                    "task_desc": planner_summary or input_data,
                    "requirement_analysis": requirement_analysis,
                    "steps_outline": steps_outline,
                    "relative_path": code_file_paths[0],
                }]
                # 获取工作区文件列表与 Excel 结构，供 Coder 使用真实路径与格式
                workspace_context = {}
                workspace_root = resolve_workspace_root(session_id)
                if workspace_root:
                    workspace_context["file_list"] = list_workspace_files(session_id)
                    workspace_context["excel_schema"] = read_workspace_excel_schema_and_sample(workspace_root)
                coder_results = generate_and_write_code(session_id, code_specs, workspace_context=workspace_context)
                yield _push(session_id, {"type": "coder", "data": coder_results})

                # 3. Worker：在工作区内执行代码
                worker_results = run_workspace_tasks(
                    session_id, execution_mode, code_file_paths
                )
                yield _push(session_id, {"type": "worker", "data": worker_results})

                # 4. Reporter：流式报告
                async for chunk in stream_report(
                    planner_summary,
                    worker_results,
                    session_id=session_id,
                ):
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
    上传 Excel 到会话工作区。若该会话尚无工作区则先创建，文件保存到工作区根目录。
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
    # 统一数据文件命名：data.xxx / data_1.xxx / data_2.xxx ...
    safe_name = generate_data_filename(workspace_abs, file.filename or "")
    save_path = os.path.join(workspace_abs, safe_name)
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
            "message": "文件已写入会话工作区根目录",
            "session_id": session_id,
            "relative_path": safe_name,
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
