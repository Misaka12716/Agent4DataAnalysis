import os
import uuid

from fastapi import HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from backend.analysis_stream import streaming_task_generator
from backend.api_models import StreamingTaskRequest
from db.session_store import SessionStore
from utils.workspace_manager import init_workspace, generate_data_filename

MAX_FILE_SIZE = 2048 * 1024 * 1024  # 最大文件大小（2048M，与Nginx配置一致）


def build_health_response() -> JSONResponse:
    return JSONResponse(
        content={
            "status": "healthy",
            "service": "agent-workflow-server",
            "version": "1.1",
        },
        status_code=200,
    )


async def handle_session_upload_excel(
    file: UploadFile,
    session_id: str,
) -> JSONResponse:
    """
    上传 Excel 到会话工作区。要求 session_id 已存在于 session_user 映射表。
    """
    if not session_id.strip():
        raise HTTPException(status_code=400, detail="session_id 不能为空")
    session_user, err = SessionStore.get_session_user(session_id)
    if err:
        raise HTTPException(status_code=500, detail=f"查询会话失败: {err}")
    if not session_user:
        raise HTTPException(status_code=404, detail="session_id 不存在，请先创建会话")

    file_size = 0
    for chunk in file.file:
        file_size += len(chunk)
        if file_size > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"文件过大（最大 {MAX_FILE_SIZE // (1024 * 1024)}MB）",
            )
    await file.seek(0)

    workspace_abs = str(session_user.get("workspace_abs_path") or "").strip() or init_workspace(session_id)
    os.makedirs(workspace_abs, exist_ok=True)
    # 统一数据文件命名：data.xxx / data_1.xxx / data_2.xxx ...
    safe_name = generate_data_filename(workspace_abs, file.filename or "")
    save_path = os.path.join(workspace_abs, safe_name)
    try:
        with open(save_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存文件失败: {e}")

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


def build_session_snapshot_response(session_id: str) -> JSONResponse:
    """
    会话快照：前端首次加载或断线重连时调用，返回该会话的「完整累计内容」和当前「版本号」。
    """
    session_user, err = SessionStore.get_session_user(session_id)
    if err:
        raise HTTPException(status_code=500, detail=f"查询会话失败: {err}")
    if not session_user:
        raise HTTPException(status_code=404, detail="session_id 不存在，请先创建会话")
    try:
        content, version = SessionStore.get_latest_content(session_id)
    except Exception:
        content, version = "", 0
    return JSONResponse(
        content={"content": content or "", "version": version},
        status_code=200,
    )


def build_run_analysis_response(body: StreamingTaskRequest) -> StreamingResponse:
    """
    流式分析任务：绑定 session_id，加载工作区上下文，执行完整分析链路；
    后端每产生新片段先更新「完整内容」+ 版本号，再 SSE 推送给前端。
    """
    session_user, err = SessionStore.get_session_user(body.session_id)
    if err:
        raise HTTPException(status_code=500, detail=f"查询会话失败: {err}")
    if not session_user:
        raise HTTPException(status_code=404, detail="session_id 不存在，请先创建会话")
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


def build_create_session_response(user_id: int) -> JSONResponse:
    """
    创建会话：后端生成 session_id，初始化工作区，并写入 session_user 映射表。
    """
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="user_id 必须为正整数")
    exists, err = SessionStore.user_exists(user_id)
    if err:
        raise HTTPException(status_code=500, detail=f"查询用户失败: {err}")
    if not exists:
        raise HTTPException(status_code=404, detail="user_id 不存在，请先登录或注册")

    session_id = str(uuid.uuid4())
    workspace_abs = init_workspace(session_id)
    ok, err = SessionStore.create_session(session_id, user_id, workspace_abs)
    if not ok:
        raise HTTPException(status_code=500, detail=f"创建会话失败: {err or 'unknown error'}")

    return JSONResponse(
        content={
            "status": "success",
            "msg": "session created",
            "data": {
                "session_id": session_id,
                "user_id": user_id,
                "workspace_abs_path": workspace_abs,
            },
        },
        status_code=200,
    )


def build_user_sessions_response(user_id: int) -> JSONResponse:
    """
    查询用户会话列表：返回该 user_id 下全部 session_id。
    """
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="user_id 必须为正整数")
    exists, err = SessionStore.user_exists(user_id)
    if err:
        raise HTTPException(status_code=500, detail=f"查询用户失败: {err}")
    if not exists:
        raise HTTPException(status_code=404, detail="user_id 不存在，请先登录或注册")

    session_ids, err = SessionStore.get_session_ids_by_user_id(user_id)
    if err:
        raise HTTPException(status_code=500, detail=f"查询会话列表失败: {err}")

    return JSONResponse(
        content={
            "status": "success",
            "msg": "query user sessions success",
            "data": {
                "user_id": user_id,
                "session_ids": session_ids,
            },
        },
        status_code=200,
    )
