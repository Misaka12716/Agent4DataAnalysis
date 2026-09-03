import os

# 解决本地开发时，请求外部接口时，出现代理问题
os.environ["NO_PROXY"] = "*"
os.environ["HTTPX_NO_PROXY"] = "1"

try:
    from pathlib import Path
    from dotenv import load_dotenv

    _root_env = Path(__file__).resolve().parents[2] / ".env"
    if _root_env.is_file():
        load_dotenv(_root_env, override=False)
except ImportError:
    pass

from fastapi import Depends, FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from backend.api_models import (
    StreamingTaskRequest,
    ReconnectStreamRequest,
    SaveSessionTitleRequest,
    CreateSessionRequest,
    CopyProjectRawRequest,
    DeleteSessionFileRequest,
)
from backend.current_user import CurrentUser, get_default_user
from backend.route_services import (
    build_health_response,
    handle_session_upload_excel,
    handle_session_delete_file,
    build_session_snapshot_response,
    build_run_analysis_response,
    build_reconnect_analysis_response,
    build_create_session_response,
    build_user_sessions_response,
    build_save_session_title_response,
    build_session_workspace_tree_response,
    build_session_meta_response,
    build_copy_project_raw_to_session_response,
)

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

# 创建上传目录（不存在则自动创建）
os.makedirs(UPLOAD_DIR, exist_ok=True)




# -------------------------- API路由 --------------------------
@app.get("/health")  # 保持原有健康检查
async def health_check():
    return build_health_response()

# -------------------------- 会话工作区与状态接口 --------------------------

@app.post("/session/create")
async def create_session(
    body: CreateSessionRequest,
    current_user: CurrentUser = Depends(get_default_user),
):
    return build_create_session_response(current_user.user_id, body.project_id)


@app.get("/session/list")
async def list_user_sessions(
    project_id: int | None = None,
    current_user: CurrentUser = Depends(get_default_user),
):
    return build_user_sessions_response(current_user.user_id, project_id=project_id)


@app.get("/session/meta")
async def session_meta(
    session_id: str,
    current_user: CurrentUser = Depends(get_default_user),
):
    return build_session_meta_response(session_id, current_user.user_id)


@app.post("/session/copy-from-project-raw")
async def session_copy_from_project_raw(
    body: CopyProjectRawRequest,
    current_user: CurrentUser = Depends(get_default_user),
):
    return build_copy_project_raw_to_session_response(
        body.session_id,
        body.relative_paths,
        current_user.user_id,
    )


@app.post("/session/save-title")
async def save_session_title(
    body: SaveSessionTitleRequest,
    current_user: CurrentUser = Depends(get_default_user),
):
    return build_save_session_title_response(
        body.session_id,
        body.title,
        current_user.user_id,
    )


@app.post("/session/upload-excel")
async def session_upload_excel(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    current_user: CurrentUser = Depends(get_default_user),
):
    return await handle_session_upload_excel(file, session_id, current_user.user_id)


@app.delete("/session/workspace-file")
async def session_delete_workspace_file(
    body: DeleteSessionFileRequest,
    current_user: CurrentUser = Depends(get_default_user),
):
    return handle_session_delete_file(
        body.session_id,
        body.relative_path,
        current_user.user_id,
    )


@app.get("/session/snapshot")
async def session_snapshot(
    session_id: str,
    current_user: CurrentUser = Depends(get_default_user),
):
    return build_session_snapshot_response(session_id, current_user.user_id)


@app.get("/session/workspace-tree")
async def session_workspace_tree(
    session_id: str,
    current_user: CurrentUser = Depends(get_default_user),
):
    return build_session_workspace_tree_response(session_id, current_user.user_id)


@app.post("/run-analysis")
async def run_analysis(
    body: StreamingTaskRequest,
    current_user: CurrentUser = Depends(get_default_user),
):
    return build_run_analysis_response(body, current_user.user_id)


@app.post("/run-analysis/reconnect")
async def reconnect_analysis_stream(
    body: ReconnectStreamRequest,
    current_user: CurrentUser = Depends(get_default_user),
):
    return build_reconnect_analysis_response(body.session_id, current_user.user_id)


from backend.route_registry import register_modular_routes
from backend.frontend_static import register_frontend_static

register_modular_routes(app)
register_frontend_static(app)

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
