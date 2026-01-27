from fastapi import FastAPI, Request, HTTPException, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from typing import AsyncGenerator, Optional, List
import json
import asyncio
import os
from datetime import datetime
from backend.console import ConsoleAgentWorkflow  # 保持原有导入

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


# -------------------------- 工具函数 --------------------------
def get_file_details(file_path: str) -> dict:
    """获取文件详细信息（用于上传后返回）"""
    try:
        file_stat = os.stat(file_path)
        file_name = os.path.basename(file_path)
        # 获取文件MIME类型（简化版，如需精确可使用 python-magic 库）
        file_ext = os.path.splitext(file_name)[1].lower()
        mime_map = {
            ".txt": "text/plain",
            ".pdf": "application/pdf",
            ".csv": "text/csv",
            ".json": "application/json",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
        }
        file_type = mime_map.get(file_ext, "application/octet-stream")

        return {
            "file_path": file_path,  # 文件存储绝对路径
            "file_name": file_name,  # 文件名（含后缀）
            "file_size": file_stat.st_size,  # 大小（字节）
            "file_size_human": (
                f"{file_stat.st_size / 1024:.2f}KB"
                if file_stat.st_size < 1024 * 1024
                else f"{file_stat.st_size / (1024*1024):.2f}MB"
            ),  # 人性化大小
            "file_type": file_type,  # MIME类型
            "upload_time": datetime.fromtimestamp(file_stat.st_ctime).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),  # 上传时间
            "is_exists": True,
        }
    except Exception as e:
        return {
            "file_path": file_path,
            "file_name": os.path.basename(file_path),
            "file_size": 0,
            "file_size_human": "0B",
            "file_type": "unknown",
            "upload_time": "",
            "is_exists": False,
            "error": str(e),
        }


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


@app.post("/upload-file")  # 文件上传接口
async def upload_file(request: Request, file: UploadFile = File(...)):
    """
    上传文件到服务器，返回存储详情
    - 支持单个文件上传
    - 自动重命名防覆盖（时间戳+原文件名）
    - 最大支持2048M文件（与Nginx配置一致）
    """
    try:
        # 1. 校验文件大小
        file_size = 0
        for chunk in file.file:
            file_size += len(chunk)
            if file_size > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail=f"文件过大（最大支持{MAX_FILE_SIZE/(1024*1024)}MB）",
                )

        # 2. 重置文件指针（读取大小后指针到末尾，需重置才能重新保存）
        file.file.seek(0)

        # 3. 生成唯一文件名（时间戳+原文件名，避免覆盖）
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_filename = (
            f"{timestamp}_{file.filename.replace(' ', '_')}"  # 替换空格防异常
        )
        file_path = os.path.abspath(os.path.join(UPLOAD_DIR, safe_filename))

        # 4. 保存文件到服务器
        with open(file_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):  # 分块读取（1MB/块）
                f.write(chunk)

        # 5. 获取文件详情并返回
        file_details = get_file_details(file_path)
        return JSONResponse(
            content={
                "status": "success",
                "message": "文件上传成功",
                "file_details": file_details,
            },
            status_code=200,
        )
    except HTTPException:
        raise  # 抛出已定义的HTTP异常
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文件上传失败：{str(e)}")


@app.post("/run-workflow")
async def run_workflow(request: Request, body: WorkflowRequest):
    try:
        return StreamingResponse(
            workflow_stream_generator(body.input_data, body.file_info),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"启动工作流失败: {str(e)}")


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
