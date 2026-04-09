import os

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from backend.api_models import StreamingTaskRequest
from backend.route_services import (
    build_health_response,
    handle_session_upload_excel,
    build_session_snapshot_response,
    build_run_analysis_response,
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
# 设置目录权限（确保服务器可读写）
os.chmod(UPLOAD_DIR, 0o755)



# -------------------------- API路由 --------------------------
@app.get("/health")  # 保持原有健康检查
async def health_check():
    return build_health_response()

# -------------------------- 会话工作区与状态接口 --------------------------

@app.post("/session/upload-excel")
async def session_upload_excel(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    user_id: int = Form(0),
):
    return await handle_session_upload_excel(file, session_id, user_id)


@app.get("/session/snapshot")
async def session_snapshot(session_id: str):
    return build_session_snapshot_response(session_id)


@app.post("/run-analysis")
async def run_analysis(body: StreamingTaskRequest):
    return build_run_analysis_response(body)


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
