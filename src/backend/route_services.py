import json
import logging
import os
import threading
import uuid
from datetime import datetime
from typing import Optional, List

from fastapi import HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from reader.file_types import classify_file, is_upload_allowed, upload_allowed_extensions

from backend.analysis_stream import (
    streaming_task_generator,
    reconnect_streaming_task_generator,
)
from backend.api_models import StreamingTaskRequest
from backend.project_auth import (
    assert_project_access,
    assert_project_not_archived,
    assert_session_access,
    assert_session_project_not_archived,
)
from db.rbac_schema import PERM_ANALYSIS_CREATE, PERM_DATA_DELETE, PERM_DATA_UPLOAD
from db.rbac_store import RbacStore
from backend.project_asset_registry import register_upload, relative_path_for_project_asset
from db.session_store import SessionStore
from utils.upload_naming import allocate_unique_name_in_dir, original_basename
from utils.workspace_manager import (
    init_workspace,
    build_workspace_tree,
    build_workspace_files_payload,
    is_safe_relative_path,
    resolve_project_root,
    resolve_workspace_root,
)
from utils.workspace_file_ops import delete_file, write_bytes_file
from utils.session_memory import SESSION_MEMORY_FILENAME, persist_workspace_snapshot
from runtime.factory import ensure_runtime
from configs.config import LANGUAGE

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 2048 * 1024 * 1024  # 最大文件大小（2048M，与Nginx配置一致）


def _schedule_workspace_snapshot(
    session_id: str,
    *,
    lang: str = "zh",
    note: str = "",
    input_hint: str = "",
) -> None:
    """后台刷新会话工作区快照（含 Reader/OCR），不阻塞上传等 HTTP 响应。"""

    def _worker() -> None:
        try:
            persist_workspace_snapshot(
                session_id,
                lang=lang,
                note=note,
                input_hint=input_hint,
            )
        except Exception:
            logger.exception(
                "background persist_workspace_snapshot failed: session_id=%s",
                session_id,
            )

    threading.Thread(
        target=_worker,
        name=f"workspace-snapshot-{session_id[:8]}",
        daemon=True,
    ).start()


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
    current_user_id: int,
) -> JSONResponse:
    """
    上传文件到会话工作区（表格 / 图片 / 文本 / 文档 / 影像，与 FormatRegistry 一致）。
    要求 session_id 已存在于 session_user 映射表。
    """
    if not session_id.strip():
        raise HTTPException(status_code=400, detail="session_id 不能为空")
    session_user = assert_session_access(session_id, current_user_id, PERM_DATA_UPLOAD)
    assert_session_project_not_archived(session_user)

    original_filename = file.filename or ""
    ext = os.path.splitext(original_filename)[1].lower()
    if not is_upload_allowed(original_filename):
        allowed = ", ".join(f".{e}" for e in upload_allowed_extensions())
        raise HTTPException(
            status_code=415,
            detail=f"不支持的文件类型: {ext or '（无扩展名）'}；允许: {allowed}",
        )

    file_size = 0
    for chunk in file.file:
        file_size += len(chunk)
        if file_size > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"文件过大（最大 {MAX_FILE_SIZE // (1024 * 1024)}MB）",
            )
    await file.seek(0)

    ensure_runtime(session_id)

    workspace_abs = str(session_user.get("workspace_abs_path") or "").strip()
    if not workspace_abs:
        workspace_abs = init_workspace(int(session_user.get("user_id") or current_user_id), session_id)
    os.makedirs(workspace_abs, exist_ok=True)
    # 无冲突保留原名；冲突时原名 (N).ext
    allocated = allocate_unique_name_in_dir(workspace_abs, original_filename)
    safe_name = allocated.stored_name
    client_original = original_basename(original_filename)
    try:
        chunks: list[bytes] = []
        while chunk := await file.read(1024 * 1024):
            chunks.append(chunk)
        file_data = b"".join(chunks)
        if not write_bytes_file(session_id, safe_name, file_data):
            raise HTTPException(status_code=500, detail="保存文件到工作区失败")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存文件失败: {e}")

    _schedule_workspace_snapshot(
        session_id,
        lang=LANGUAGE,
        note=f"已上传文件到工作区：`{safe_name}`（类型: {classify_file(safe_name)}）。",
        input_hint="（上传后尚未发起新一轮分析时可参考下列文件列表）",
    )

    project_id = session_user.get("project_id")
    if project_id:
        try:
            proj_root = resolve_project_root(int(project_id))
            if proj_root:
                rel = relative_path_for_project_asset(
                    proj_root,
                    workspace_abs,
                    session_id,
                    os.path.join(workspace_abs, safe_name),
                )
                register_upload(
                    project_id=int(project_id),
                    session_id=session_id,
                    relative_path=rel,
                    original_filename=client_original,
                    file_category=classify_file(safe_name),
                )
        except Exception:
            pass

    from backend.chunked_upload_finalize import attach_deprecated_fields

    return JSONResponse(
        content=attach_deprecated_fields(
            {
                "status": "success",
                "message": "文件已写入会话工作区根目录",
                "session_id": session_id,
                "relative_path": safe_name,
                "original_filename": client_original,
                "renamed": allocated.renamed,
                "file_category": classify_file(safe_name),
                "workspace_abs_path": workspace_abs,
            }
        ),
        status_code=200,
    )


def handle_session_delete_file(
    session_id: str,
    relative_path: str,
    current_user_id: int,
) -> JSONResponse:
    """删除会话工作区内的单个文件，并清理对应 project_assets 登记。"""
    if not session_id.strip():
        raise HTTPException(status_code=400, detail="session_id 不能为空")
    session_user = assert_session_access(session_id, current_user_id, PERM_DATA_DELETE)
    assert_session_project_not_archived(session_user)

    rel = (relative_path or "").strip().replace("\\", "/").lstrip("/")
    if not rel or not is_safe_relative_path(rel) or ".." in rel.split("/"):
        raise HTTPException(status_code=400, detail="relative_path 无效")
    if os.path.basename(rel) == SESSION_MEMORY_FILENAME or rel == SESSION_MEMORY_FILENAME:
        raise HTTPException(status_code=400, detail="禁止删除系统文件 SESSION_MEMORY.md")

    workspace_abs = str(session_user.get("workspace_abs_path") or "").strip()
    if not workspace_abs:
        workspace_abs = resolve_workspace_root(session_id) or ""
    if not workspace_abs:
        raise HTTPException(status_code=404, detail="会话工作区不存在")

    abs_path = os.path.normpath(os.path.join(workspace_abs, rel.replace("/", os.sep)))
    workspace_norm = os.path.normpath(workspace_abs)
    if not abs_path.startswith(workspace_norm + os.sep) and abs_path != workspace_norm:
        raise HTTPException(status_code=400, detail="路径越界")
    if os.path.isdir(abs_path):
        raise HTTPException(status_code=400, detail="仅支持删除文件，不支持删除目录")
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="文件不存在")

    ensure_runtime(session_id)
    if not delete_file(session_id, rel):
        raise HTTPException(status_code=500, detail="删除文件失败")

    project_id = session_user.get("project_id")
    if project_id:
        try:
            proj_root = resolve_project_root(int(project_id))
            if proj_root:
                asset_rel = relative_path_for_project_asset(
                    proj_root,
                    workspace_abs,
                    session_id,
                    abs_path,
                )
                RbacStore.delete_asset(int(project_id), asset_rel)
        except Exception:
            logger.debug(
                "cleanup project_assets failed: session=%s path=%s",
                session_id,
                rel,
                exc_info=True,
            )

    _schedule_workspace_snapshot(
        session_id,
        lang=LANGUAGE,
        note=f"已从工作区删除文件：`{rel}`。",
        input_hint="（删除后尚未发起新一轮分析时可参考下列文件列表）",
    )

    return JSONResponse(
        content={
            "status": "success",
            "msg": "file deleted",
            "session_id": session_id,
            "relative_path": rel,
        },
        status_code=200,
    )


def build_session_snapshot_response(session_id: str, current_user_id: int) -> JSONResponse:
    """
    会话快照：前端首次加载或断线重连时调用，返回该会话的「完整累计内容」和当前「版本号」。
    """
    assert_session_access(session_id, current_user_id)
    try:
        content, version = SessionStore.get_latest_content(session_id)
    except Exception:
        content, version = "", 0
    return JSONResponse(
        content={"content": content or "", "version": version},
        status_code=200,
    )


def build_run_analysis_response(body: StreamingTaskRequest, current_user_id: int) -> StreamingResponse:
    """
    流式分析任务：绑定 session_id，加载工作区上下文，执行完整分析链路；
    后端每产生新片段先更新「完整内容」+ 版本号，再 SSE 推送给前端。
    """
    assert_session_access(body.session_id, current_user_id, PERM_ANALYSIS_CREATE)
    session_user, _ = SessionStore.get_session_user(body.session_id)
    if session_user:
        assert_session_project_not_archived(session_user)
    # 在流水线事件之前持久化用户原始输入，便于快照回放时还原完整对话。
    ok, _, err = SessionStore.append_content(
        body.session_id,
        json.dumps(
            {
                "type": "user_input",
                "content": body.input_data,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
            ensure_ascii=False,
        )
        + "\n",
    )
    if not ok:
        raise HTTPException(status_code=500, detail=f"保存用户输入失败: {err}")
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


def build_reconnect_analysis_response(session_id: str, current_user_id: int) -> StreamingResponse:
    """
    断线恢复流：
    先把 session 的当前锁存快照推给前端，再在分析未结束时继续推送后续事件。
    """
    sid = session_id.strip()
    assert_session_access(sid, current_user_id, PERM_ANALYSIS_CREATE)
    try:
        return StreamingResponse(
            reconnect_streaming_task_generator(sid),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"启动重连流失败: {str(e)}")


def build_create_session_response(user_id: int, project_id: Optional[int] = None) -> JSONResponse:
    """
    创建会话：后端生成 session_id，在项目下初始化工作区，并写入 session_user 映射表。
    project_id 省略时自动使用「个人默认」项目。
    """
    from backend.project_service import ProjectService

    if user_id <= 0:
        raise HTTPException(status_code=400, detail="user_id 必须为正整数")
    exists, err = SessionStore.user_exists(user_id)
    if err:
        raise HTTPException(status_code=500, detail=f"查询用户失败: {err}")
    if not exists:
        raise HTTPException(status_code=404, detail="user_id 不存在，请先登录或注册")

    effective_project_id, err = ProjectService.resolve_project_id(user_id, project_id)
    if err or effective_project_id <= 0:
        raise HTTPException(status_code=500, detail=err or "无法解析项目")

    project = assert_project_access(effective_project_id, user_id)
    assert_project_not_archived(project)

    session_id = str(uuid.uuid4())
    workspace_abs = init_workspace(user_id, session_id, project_id=effective_project_id)
    ok, err = SessionStore.create_session(
        session_id, user_id, workspace_abs, project_id=effective_project_id
    )
    if not ok:
        raise HTTPException(status_code=500, detail=f"创建会话失败: {err or 'unknown error'}")

    ensure_runtime(session_id)

    _schedule_workspace_snapshot(
        session_id,
        lang=LANGUAGE,
        note="会话已创建，工作区已初始化。",
        input_hint="（尚未发起分析）",
    )

    return JSONResponse(
        content={
            "status": "success",
            "msg": "session created",
            "data": {
                "session_id": session_id,
                "user_id": user_id,
                "project_id": effective_project_id,
                "workspace_abs_path": workspace_abs,
            },
        },
        status_code=200,
    )


def build_session_meta_response(session_id: str, current_user_id: int) -> JSONResponse:
    """返回会话元数据（project_id、工作区路径等），供前端同步权限上下文。"""
    sid = session_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="session_id 不能为空")
    session_user = assert_session_access(sid, current_user_id)
    project_id = session_user.get("project_id")
    return JSONResponse(
        content={
            "status": "success",
            "msg": "session meta",
            "data": {
                "session_id": sid,
                "user_id": int(session_user.get("user_id") or 0),
                "project_id": int(project_id) if project_id else None,
                "title": session_user.get("title"),
                "workspace_abs_path": str(session_user.get("workspace_abs_path") or ""),
            },
        },
        status_code=200,
    )


def build_user_sessions_response(
    user_id: int,
    project_id: Optional[int] = None,
) -> JSONResponse:
    """
    查询用户可访问会话列表：自己创建的 + 可访问项目内的共享会话。
    project_id 为正整数时仅返回该项目下的会话。
    """
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="user_id 必须为正整数")
    exists, err = SessionStore.user_exists(user_id)
    if err:
        raise HTTPException(status_code=500, detail=f"查询用户失败: {err}")
    if not exists:
        raise HTTPException(status_code=404, detail="user_id 不存在，请先登录或注册")

    sessions, err = SessionStore.get_accessible_sessions(user_id, project_id=project_id)
    if err:
        raise HTTPException(status_code=500, detail=f"查询会话列表失败: {err}")

    return JSONResponse(
        content={
            "status": "success",
            "msg": "query user sessions success",
            "data": {
                "user_id": user_id,
                "sessions": sessions,
            },
        },
        status_code=200,
    )


def build_save_session_title_response(
    session_id: str,
    title: str,
    current_user_id: int,
) -> JSONResponse:
    """
    保存会话标题：如果已有非空标题则不重复写入；仅首次写入有效。
    """
    sid = session_id.strip()
    clean_title = title.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="session_id 不能为空")
    if not clean_title:
        raise HTTPException(status_code=400, detail="title 不能为空")

    assert_session_access(sid, current_user_id)

    ok, saved, err = SessionStore.save_session_title_if_absent(sid, clean_title)
    if not ok:
        if err == "session_id not found":
            raise HTTPException(status_code=404, detail="session_id 不存在，请先创建会话")
        if err == "title is empty":
            raise HTTPException(status_code=400, detail="title 不能为空")
        raise HTTPException(status_code=500, detail=f"保存会话标题失败: {err}")

    # 已有标题时返回数据库现值，首次写入时返回本次写入值
    final_title = clean_title
    if not saved:
        latest, err = SessionStore.get_session_user(sid)
        if err:
            raise HTTPException(status_code=500, detail=f"查询会话标题失败: {err}")
        final_title = str((latest or {}).get("title") or clean_title)

    return JSONResponse(
        content={
            "status": "success",
            "msg": "session title saved" if saved else "session title already exists",
            "data": {
                "session_id": sid,
                "title": final_title,
                "saved": saved,
            },
        },
        status_code=200,
    )


def build_session_workspace_tree_response(session_id: str, current_user_id: int) -> JSONResponse:
    """
    查询会话工作区目录树：返回该 session_id 对应工作区下的完整层级结构。
    """
    sid = session_id.strip()
    session_user = assert_session_access(sid, current_user_id)

    workspace_abs = str(session_user.get("workspace_abs_path") or "").strip()
    if not workspace_abs:
        raise HTTPException(status_code=500, detail="会话工作区路径缺失")

    if not os.path.isdir(workspace_abs):
        tree = {
            "name": "",
            "type": "directory",
            "relative_path": "",
            "children": [],
        }
        return JSONResponse(
            content={
                "status": "success",
                "msg": "workspace not found, return empty tree",
                "data": {
                    "session_id": sid,
                    "workspace_abs_path": workspace_abs,
                    "tree": tree,
                    "files": [],
                },
            },
            status_code=200,
        )

    try:
        tree = build_workspace_tree(workspace_abs)
        files = build_workspace_files_payload(workspace_abs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"构建工作区目录树失败: {e}")

    return JSONResponse(
        content={
            "status": "success",
            "msg": "query workspace tree success",
            "data": {
                "session_id": sid,
                "workspace_abs_path": workspace_abs,
                "tree": tree,
                "files": files,
            },
        },
        status_code=200,
    )


def build_copy_project_raw_to_session_response(
    session_id: str,
    relative_paths: Optional[List[str]],
    current_user_id: int,
) -> JSONResponse:
    """
    将项目 raw/ 下的文件复制到会话工作区，供分析链路使用。
    relative_paths 为空时复制 raw/ 下全部文件。
    """
    import shutil

    sid = session_id.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="session_id 不能为空")

    session_user = assert_session_access(sid, current_user_id, PERM_DATA_UPLOAD)
    assert_session_project_not_archived(session_user)

    project_id = session_user.get("project_id")
    if not project_id:
        raise HTTPException(status_code=400, detail="会话未关联项目，无法从项目 raw/ 复制")

    project_id = int(project_id)
    project = assert_project_access(project_id, current_user_id, PERM_DATA_UPLOAD)
    assert_project_not_archived(project)

    project_root = resolve_project_root(project_id) or str(project.get("workspace_abs_path") or "")
    raw_dir = os.path.join(project_root, "raw")
    if not os.path.isdir(raw_dir):
        raise HTTPException(status_code=404, detail="项目 raw/ 目录不存在")

    session_root = str(session_user.get("workspace_abs_path") or "").strip()
    if not session_root or not os.path.isdir(session_root):
        from utils.workspace_manager import resolve_workspace_root

        session_root = resolve_workspace_root(sid) or ""
    if not session_root or not os.path.isdir(session_root):
        raise HTTPException(status_code=404, detail="会话工作区不存在")

    if relative_paths:
        candidates = []
        for rel in relative_paths:
            clean = (rel or "").strip().lstrip("/").replace("\\", "/")
            if not clean or not clean.startswith("raw/"):
                raise HTTPException(status_code=400, detail=f"路径须位于 raw/ 下: {rel}")
            abs_path = os.path.normpath(os.path.join(project_root, clean))
            if not abs_path.startswith(os.path.abspath(project_root) + os.sep):
                raise HTTPException(status_code=400, detail=f"非法路径: {rel}")
            if not os.path.isfile(abs_path):
                raise HTTPException(status_code=404, detail=f"文件不存在: {rel}")
            candidates.append((clean, abs_path))
    else:
        candidates = []
        for name in sorted(os.listdir(raw_dir)):
            abs_path = os.path.join(raw_dir, name)
            if os.path.isfile(abs_path):
                candidates.append((f"raw/{name}", abs_path))

    if not candidates:
        raise HTTPException(status_code=404, detail="raw/ 下没有可复制的文件")

    copied: List[dict] = []
    from db.project_store import ProjectStore

    for rel, src_path in candidates:
        file_name = os.path.basename(src_path)
        true_original = file_name
        try:
            asset, _ = ProjectStore.get_asset_by_path(project_id, rel)
            if asset and asset.get("original_filename"):
                true_original = str(asset["original_filename"])
        except Exception:
            pass

        allocated = allocate_unique_name_in_dir(session_root, true_original)
        dest_name = allocated.stored_name
        dest_path = os.path.join(session_root, dest_name)
        try:
            shutil.copy2(src_path, dest_path)
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"复制失败 {rel}: {e}")
        session_rel = dest_name
        asset_rel = relative_path_for_project_asset(
            project_root, session_root, sid, dest_path
        )
        register_upload(
            project_id=project_id,
            session_id=sid,
            relative_path=asset_rel,
            original_filename=true_original,
            file_category=classify_file(dest_name),
        )
        copied.append(
            {
                "source": rel,
                "relative_path": session_rel,
                "original_filename": true_original,
                "renamed": allocated.renamed,
            }
        )

    return JSONResponse(
        content={
            "status": "success",
            "msg": "copied from project raw to session workspace",
            "data": {
                "session_id": sid,
                "project_id": project_id,
                "copied": copied,
            },
        },
        status_code=200,
    )
