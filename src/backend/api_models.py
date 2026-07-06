from pydantic import BaseModel
from typing import Optional


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
    template_id: Optional[int] = None


class ReconnectStreamRequest(BaseModel):
    session_id: str


class SaveSessionTitleRequest(BaseModel):
    session_id: str
    title: str


class SendSmsCodeRequest(BaseModel):
    phone: str


class LoginWithSmsRequest(BaseModel):
    phone: str
    code: str


class UpdateUsernameRequest(BaseModel):
    username: str
