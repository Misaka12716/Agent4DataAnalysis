from pydantic import BaseModel


class WorkflowRequest(BaseModel):
    input_data: str
    file_info: str | None = "No files uploaded"
    """
    可选参数：上传文件后的存储路径（从 /upload-file 接口返回的 file_path 字段获取）
    示例："./backend/uploads/20251203_1015_test.pdf"
    """


class StreamingTaskRequest(BaseModel):
    """流式分析任务请求：绑定会话，执行 Planner-Coder-Worker-Reporter"""

    session_id: str
    input_data: str


class SendSmsCodeRequest(BaseModel):
    phone: str


class LoginWithSmsRequest(BaseModel):
    phone: str
