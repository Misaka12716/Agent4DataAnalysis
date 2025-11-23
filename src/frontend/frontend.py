# 前端代码中替换utils.llm_response的调用，新增后端接口调用函数
import aiohttp
import json
from typing import List, Dict, AsyncGenerator

# 后端地址
BACKEND_URL = "http://localhost:8000/api"


async def new_chat_session(system_prompt: str = "") -> str:
    """新建会话（调用后端接口）"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{BACKEND_URL}/sessions", json={"system_prompt": system_prompt}
        ) as resp:
            if resp.status != 200:
                raise Exception(f"新建会话失败：{resp.status}")
            data = await resp.json()
            return data["session_id"]


async def get_chat_history(session_id: str) -> List[Dict]:
    """获取聊天历史（调用后端接口）"""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{BACKEND_URL}/sessions/{session_id}/history") as resp:
            if resp.status != 200:
                raise Exception(f"获取历史失败：{resp.status}")
            data = await resp.json()
            return data["history"]


async def ai_chat(
    session_id: str,
    prompt: str,
    http_session: aiohttp.ClientSession,
    model_name: str = None,
    images: List[str] = None,
    stream: bool = True,
    need_thinking: bool = False,
) -> AsyncGenerator[Dict, None]:
    """聊天接口（调用后端流式接口）"""
    payload = {
        "prompt": prompt,
        "file_paths": None,  # 由前端传入保存的文件路径列表
        "images": images,
        "stream": stream,
        "need_thinking": need_thinking,
    }

    async with http_session.post(
        f"{BACKEND_URL}/sessions/{session_id}/chat", json=payload
    ) as resp:
        if resp.status != 200:
            raise Exception(f"聊天接口调用失败：{resp.status}")

        # 处理流式响应
        async for line in resp.content:
            if line:
                line_str = line.decode("utf-8").strip()
                if line_str:
                    chunk = json.loads(line_str)
                    yield chunk


async def close_chat_session(session_id: str):
    """关闭会话（调用后端接口）"""
    async with aiohttp.ClientSession() as session:
        async with session.delete(f"{BACKEND_URL}/sessions/{session_id}") as resp:
            if resp.status != 200:
                raise Exception(f"关闭会话失败：{resp.status}")


# 文件解析接口调用（前端上传文件后可选调用）
async def parse_uploaded_file(file_path: str) -> Dict:
    """解析文件（调用后端接口）"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{BACKEND_URL}/files/parse", json={"file_path": file_path}
        ) as resp:
            if resp.status != 200:
                raise Exception(f"解析文件失败：{resp.status}")
            return await resp.json()
