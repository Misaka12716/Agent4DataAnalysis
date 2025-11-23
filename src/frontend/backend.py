import os
import asyncio
from typing import Dict, List, AsyncGenerator, Optional
from dataclasses import dataclass, field
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# -------------------------- 配置 --------------------------
UPLOAD_FOLDER = "/data/agent_platform/src/uploads"  # 与前端一致的文件路径
app = FastAPI(title="数据分析Agent后端")

# 跨域配置（允许前端访问）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境请替换为前端实际域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------- 会话管理 --------------------------
@dataclass
class ChatSession:
    session_id: str
    history: List[Dict] = field(default_factory=list)
    created_at: float = field(default_factory=asyncio.get_event_loop().time)

class SessionManager:
    def __init__(self):
        self.sessions: Dict[str, ChatSession] = {}
        self.session_counter = 0

    async def create_session(self, system_prompt: str = "") -> str:
        self.session_counter += 1
        session_id = f"session_{self.session_counter}_{int(asyncio.get_event_loop().time() * 1000)}"
        self.sessions[session_id] = ChatSession(
            session_id=session_id,
            history=[{"role": "system", "content": system_prompt}] if system_prompt else []
        )
        return session_id

    async def get_session(self, session_id: str) -> ChatSession:
        if session_id not in self.sessions:
            raise HTTPException(status_code=404, detail="会话不存在")
        return self.sessions[session_id]

    async def get_history(self, session_id: str) -> List[Dict]:
        session = await self.get_session(session_id)
        # 过滤掉system消息，只返回用户和助手的对话
        return [msg for msg in session.history if msg["role"] in ["user", "assistant"]]

    async def add_message(self, session_id: str, role: str, content: str):
        session = await self.get_session(session_id)
        session.history.append({"role": role, "content": content})

    async def delete_session(self, session_id: str):
        if session_id in self.sessions:
            del self.sessions[session_id]

# 初始化会话管理器
session_manager = SessionManager()

# -------------------------- 请求模型 --------------------------
class SystemPromptRequest(BaseModel):
    system_prompt: str = ""

class ChatRequest(BaseModel):
    prompt: str
    file_paths: Optional[List[str]] = None
    images: Optional[List[str]] = None  # Base64图片列表
    stream: bool = True
    need_thinking: bool = False

class ParseFileRequest(BaseModel):
    file_path: str

# -------------------------- 核心业务逻辑 --------------------------
async def process_chat(
    session_id: str,
    prompt: str,
    file_paths: List[str] = None,
    images: List[str] = None,
    stream: bool = True
) -> AsyncGenerator[Dict, None]:
    """模拟LLM对话逻辑（流式/非流式），实际可替换为真实LLM调用"""
    session = await session_manager.get_session(session_id)
    
    # 拼接用户输入（包含文件信息）
    user_content = prompt
    if file_paths:
        file_info = "\n\n上传文件路径：\n" + "\n".join([f"- {path}" for path in file_paths])
        user_content += file_info
    
    # 记录用户消息
    await session_manager.add_message(session_id, "user", user_content)
    
    # 模拟AI思考和回复（实际需替换为LLM调用）
    thinking_content = "正在分析用户问题和文件..." if images else "正在处理您的请求..."
    if images:
        thinking_content += "\n检测到图片，正在识别图片内容..."
    
    # 流式响应生成
    if stream:
        if thinking_content and not images:
            yield {"type": "chunk", "content": thinking_content + "\n\n"}
        
        # 模拟分块回复（实际从LLM流式输出中获取）
        response_chunks = [
            "我已经收到您的请求，",
            "并分析了以下文件：\n",
            *[f"- {path}\n" for path in file_paths] if file_paths else [],
            "\n接下来我将为您提供详细的分析结果：\n",
            "1. 文件格式验证：所有文件路径有效\n",
            "2. 数据概览：待解析文件内容后生成\n",
            "3. 建议分析方向：数据统计、可视化、异常检测"
        ]
        
        for chunk in response_chunks:
            yield {"type": "chunk", "content": chunk}
            await asyncio.sleep(0.1)  # 模拟网络延迟
        
        # 完整回复
        full_response = "".join(response_chunks)
        if thinking_content:
            full_response = thinking_content + "\n\n" + full_response
        
        yield {"type": "complete", "content": full_response}
    else:
        # 非流式响应
        full_response = "非流式回复：" + user_content + "\n\nAI处理结果..."
        yield {"type": "complete", "content": full_response}
    
    # 记录助手消息
    await session_manager.add_message(session_id, "assistant", full_response)

async def parse_file(file_path: str) -> Dict:
    """解析文件逻辑（留空，待用户扩展）"""
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="文件不存在")
    
    # TODO: 用户自定义文件解析逻辑（如读取CSV/XLSX/PDF等）
    file_info = {
        "file_path": file_path,
        "file_size": os.path.getsize(file_path),
        "file_type": os.path.splitext(file_path)[-1],
        "parse_result": "待实现解析逻辑"  # 替换为实际解析结果
    }
    return file_info

# -------------------------- API接口 --------------------------
@app.post("/api/sessions", summary="新建会话")
async def create_session(request: SystemPromptRequest = Body(...)):
    session_id = await session_manager.create_session(request.system_prompt)
    return {"session_id": session_id}

@app.get("/api/sessions/{session_id}/history", summary="获取聊天历史")
async def get_chat_history(session_id: str):
    history = await session_manager.get_history(session_id)
    return {"history": history}

@app.post("/api/sessions/{session_id}/chat", summary="聊天接口（支持流式）")
async def chat(session_id: str, request: ChatRequest = Body(...)):
    if request.stream:
        # 流式响应
        async def stream_generator():
            async for chunk in process_chat(
                session_id=session_id,
                prompt=request.prompt,
                file_paths=request.file_paths,
                images=request.images,
                stream=request.stream
            ):
                yield f"{chunk}\n"  # 每行一个JSON块（SSE格式）
        
        return StreamingResponse(stream_generator(), media_type="text/event-stream")
    else:
        # 非流式响应
        async for chunk in process_chat(
            session_id=session_id,
            prompt=request.prompt,
            file_paths=request.file_paths,
            images=request.images,
            stream=request.stream
        ):
            if chunk["type"] == "complete":
                return chunk

@app.delete("/api/sessions/{session_id}", summary="删除会话")
async def delete_session(session_id: str):
    await session_manager.delete_session(session_id)
    return {"detail": "会话已删除"}

@app.post("/api/files/parse", summary="解析文件")
async def parse_file_api(request: ParseFileRequest = Body(...)):
    result = await parse_file(request.file_path)
    return result

# -------------------------- 启动服务 --------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        workers=1
    )