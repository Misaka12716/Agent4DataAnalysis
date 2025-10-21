import json
import uuid
import os
from datetime import datetime
from aiohttp import ClientSession
from filelock import FileLock
from utils.config import (
    PATH,
    SUPPORTED_MODELS,
    DEFAULT_MODEL,
    OPENAI_COMPATIBLE_API_BASE,
    API_KEY,
)

# -------------------------- 配置参数 --------------------------
SESSIONS_FILE_PATH = f"{PATH}/utils/chat_sessions.json"
SESSIONS_LOCK_PATH = f"{SESSIONS_FILE_PATH}.lock"
_chat_sessions = {}


# -------------------------- 工具函数（文件读写） --------------------------
def _load_sessions() -> dict:
    lock = FileLock(SESSIONS_LOCK_PATH)
    with lock:
        try:
            if not os.path.exists(SESSIONS_FILE_PATH):
                print(f"会话文件不存在，初始化空会话：{SESSIONS_FILE_PATH}")
                return {}

            with open(SESSIONS_FILE_PATH, "r", encoding="utf-8") as f:
                sessions = json.load(f)
            print(f"成功加载 {len(sessions)} 个历史会话")
            return sessions

        except json.JSONDecodeError:
            print(f"会话文件格式错误，重置为空会话：{SESSIONS_FILE_PATH}")
            if os.path.exists(SESSIONS_FILE_PATH):
                backup_path = f"{SESSIONS_FILE_PATH}.backup.{datetime.now().strftime('%Y%m%d%H%M%S')}"
                os.rename(SESSIONS_FILE_PATH, backup_path)
                print(f"错误文件已备份至：{backup_path}")
            return {}

        except IOError as e:
            print(f"加载会话文件失败：{str(e)}，使用空会话")
            return {}


def _save_sessions(sessions: dict) -> bool:
    lock = FileLock(SESSIONS_LOCK_PATH)
    with lock:
        try:
            parent_dir = os.path.dirname(SESSIONS_FILE_PATH)
            if parent_dir and not os.path.exists(parent_dir):
                os.makedirs(parent_dir, exist_ok=True)

            with open(SESSIONS_FILE_PATH, "w", encoding="utf-8") as f:
                json.dump(sessions, f, ensure_ascii=False, indent=2)
            return True

        except IOError as e:
            print(f"保存会话文件失败：{str(e)}")
            return False


# -------------------------- 模块初始化：加载历史会话 --------------------------
_chat_sessions = _load_sessions()


# -------------------------- 会话基础操作 --------------------------
def _generate_session_id() -> str:
    return str(uuid.uuid4())


def parse_model_output(raw_content: str) -> tuple[str, str]:
    parts = raw_content.split("</think>", 1)
    if len(parts) == 2 and parts[0].strip().startswith("</think>"):
        thinking = parts[0].strip()[7:].strip()
        content = parts[1].strip()
        return thinking, content
    return "", raw_content.strip()


# -------------------------- API调用工具函数（仅保留流式） --------------------------
async def _call_openai_compatible_api_stream(
    session: ClientSession, request_data: dict
):
    api_url = f"{OPENAI_COMPATIBLE_API_BASE}/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"}

    async with session.post(
        url=api_url, headers=headers, data=json.dumps(request_data)
    ) as response:
        if not response.ok:
            error_text = await response.text()
            raise ValueError(f"API请求失败 [{response.status}]: {error_text}")

        buffer = b""
        async for chunk_bytes, _ in response.content.iter_chunks():
            if not chunk_bytes:
                continue

            buffer += chunk_bytes
            lines = buffer.split(b"\n")

            for line_bytes in lines[:-1]:
                line_bytes = line_bytes.strip()
                if not line_bytes:
                    continue

                try:
                    line = line_bytes.decode("utf-8").lstrip("data: ")
                except UnicodeDecodeError:
                    print(f"忽略无法解码的字节：{line_bytes[:50]}...")
                    continue

                if line == "[DONE]":
                    return
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    print(f"忽略无效JSON数据：{line[:50]}...")
                    continue

            buffer = lines[-1]

        if buffer.strip():
            try:
                line = buffer.decode("utf-8").lstrip("data: ")
                if line != "[DONE]":
                    yield json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                print(f"忽略缓冲区无效数据：{buffer[:50]}...")


# -------------------------- 单轮对话接口（已隐含流式，此处保留不影响测试） --------------------------
async def ai_response(
    prompt: str,
    session: ClientSession,
    model_name: str | None = None,
    images: list | None = None,
    stream: bool = False,
    need_thinking: bool = False,
):
    selected_model = model_name if model_name in SUPPORTED_MODELS else DEFAULT_MODEL
    try:
        user_content = prompt if need_thinking else f"{prompt}/no_think"
        request_data = {
            "model": selected_model,
            "messages": [{"role": "user", "content": user_content}],
            "stream": stream,
        }

        if images and len(images) > 0:
            request_data["messages"][0]["images"] = images

        if stream:

            async def stream_generator():
                full_content = ""
                async for chunk in _call_openai_compatible_api_stream(
                    session, request_data
                ):
                    if "choices" in chunk and chunk["choices"]:
                        delta = chunk["choices"][0].get("delta", {})
                        if "content" in delta and delta["content"]:
                            full_content += delta["content"]
                            yield {
                                "type": "chunk",
                                "content": delta["content"],
                                "model": selected_model,
                                "success": True,
                            }
                thinking, content = parse_model_output(full_content)
                yield {
                    "type": "complete",
                    "thinking": thinking,
                    "content": content,
                    "model": selected_model,
                    "success": True,
                }

            return stream_generator()

        # 非流式逻辑已保留但测试中不会调用，若需彻底删除可直接移除该分支
        error_msg = "当前测试仅支持流式调用，请勿使用非流式"
        print(error_msg)
        return {
            "thinking": "",
            "content": error_msg,
            "model": selected_model,
            "error": error_msg,
            "success": False,
        }

    except Exception as e:
        error_msg = f"单轮对话错误: {str(e)}"
        print(f"{datetime.now()} {error_msg}")
        return {
            "thinking": "",
            "content": f"处理失败: {str(e)}",
            "model": selected_model,
            "error": str(e),
            "success": False,
        }


# -------------------------- 多轮对话接口（核心流式逻辑保留） --------------------------
def new_chat_session() -> str:
    session_id = _generate_session_id()
    _chat_sessions[session_id] = []
    if _save_sessions(_chat_sessions):
        print(f"新会话创建成功，ID: {session_id}（已保存到文件）")
    else:
        print(f"新会话创建成功（ID: {session_id}），但保存文件失败！")
    return session_id


def get_chat_history(session_id: str) -> list:
    return _chat_sessions.get(session_id, [])


async def ai_chat(
    session_id: str,
    prompt: str,
    http_session: ClientSession,
    model_name: str | None = None,
    images: list | None = None,
    stream: bool = False,
    need_thinking: bool = False,
):
    if session_id not in _chat_sessions:
        return {"error": f"会话不存在: {session_id}", "success": False}

    selected_model = model_name if model_name in SUPPORTED_MODELS else DEFAULT_MODEL
    try:
        # 1. 构造用户消息
        user_content = prompt if need_thinking else f"{prompt}/no_think"
        user_message = {"role": "user", "content": user_content}
        if images and len(images) > 0:
            user_message["images"] = images

        # 2. 加载历史对话（核心：基于历史生成新响应）
        chat_history = _chat_sessions[session_id].copy()
        chat_history.append(user_message)

        # 3. 构造API请求（强制stream=True，测试中固定传参）
        request_data = {
            "model": selected_model,
            "messages": chat_history,
            "stream": stream,
        }

        # 4. 仅保留流式响应处理
        if stream:

            async def stream_generator():
                full_content = ""
                # 调用流式API
                async for chunk in _call_openai_compatible_api_stream(
                    http_session, request_data
                ):
                    if "choices" in chunk and chunk["choices"]:
                        delta = chunk["choices"][0].get("delta", {})
                        if "content" in delta and delta["content"]:
                            full_content += delta["content"]
                            yield {
                                "type": "chunk",
                                "session_id": session_id,
                                "content": delta["content"],
                                "model": selected_model,
                                "success": True,
                            }

                # 流式结束：更新历史记录（核心：验证历史是否持久化）
                thinking, content = parse_model_output(full_content)
                assistant_message = {"role": "assistant", "content": content}
                _chat_sessions[session_id].append(user_message)
                _chat_sessions[session_id].append(assistant_message)
                _save_sessions(_chat_sessions)  # 保存到文件

                # 返回完整结果（包含历史长度，用于验证）
                yield {
                    "type": "complete",
                    "session_id": session_id,
                    "thinking": thinking,
                    "content": content,
                    "model": selected_model,
                    "history_length": len(_chat_sessions[session_id]),  # 历史消息数
                    "success": True,
                }

            return stream_generator()

        # 非流式逻辑已保留但测试中不会调用
        error_msg = "当前测试仅支持流式多轮对话，请勿关闭stream"
        print(error_msg)
        return {
            "session_id": session_id,
            "thinking": "",
            "content": error_msg,
            "error": error_msg,
            "success": False,
        }

    except Exception as e:
        error_msg = f"多轮对话错误 [{session_id}]: {str(e)}"
        print(f"{datetime.now()} {error_msg}")
        return {
            "session_id": session_id,
            "thinking": "",
            "content": f"对话处理失败: {str(e)}",
            "error": str(e),
            "success": False,
        }


# -------------------------- 测试代码（核心修改：终端动态输入+流式历史） --------------------------
if __name__ == "__main__":
    import asyncio
    from aiohttp import ClientSession

    async def test_interactive_stream_with_history():
        async with ClientSession() as http_session:
            # 1. 初始化：创建唯一会话（整个交互过程复用此会话，保证历史连贯）
            print("=" * 50)
            print("        交互式流式对话测试（带历史记录）")
            print("        说明：输入内容进行对话，输入'exit'退出")
            print("=" * 50)
            session_id = new_chat_session()
            print(f"当前会话ID：{session_id}\n")
            print("请输入你的问题：")

            # 2. 循环接收用户输入，直到输入'exit'
            while True:
                # 读取用户终端输入（处理空输入）
                user_input = input("> ").strip()
                if not user_input:
                    print("输入不能为空，请重新输入！")
                    continue
                # 退出逻辑
                if user_input.lower() == "exit":
                    print("\n正在退出...")
                    break

                # 3. 调用流式多轮对话（复用session_id，基于历史上下文）
                print("\nAI 流式回复：", end="", flush=True)
                stream_gen = await ai_chat(
                    session_id=session_id,
                    prompt=user_input,
                    http_session=http_session,
                    stream=True,  # 强制流式输出
                    need_thinking=False,  # 可根据需求改为True显示思考过程
                )

                # 4. 处理流式输出
                complete_info = None
                async for chunk in stream_gen:
                    if chunk["type"] == "chunk":
                        # 流式逐字输出
                        print(chunk["content"], end="", flush=True)
                    elif chunk["type"] == "complete":
                        # 记录完整信息（用于显示历史长度）
                        complete_info = chunk

                # 5. 输出对话结束信息（历史长度、思考过程等）
                print("\n")
                if complete_info and complete_info["success"]:
                    # 显示当前历史消息数（用户n条 + 助手n条）
                    history_count = complete_info["history_length"]
                    print(
                        f"当前会话历史消息数：{history_count}（含 {history_count//2} 轮对话）"
                    )
                    # 若开启need_thinking，显示思考过程
                    if complete_info["thinking"]:
                        print(f"AI 思考过程：{complete_info['thinking']}")
                else:
                    print("AI 回复处理失败，请重试！")
                print("\n" + "-" * 30 + "\n")

            # 6. 退出前验证历史持久化（可选：让用户确认历史已保存）
            print("=" * 50)
            print("退出前验证历史记录：")
            if os.path.exists(SESSIONS_FILE_PATH):
                with open(SESSIONS_FILE_PATH, "r", encoding="utf-8") as f:
                    file_sessions = json.load(f)
                target_history = file_sessions.get(session_id, [])
                print(f"本地文件中会话总数：{len(file_sessions)}")
                print(f"当前会话历史消息数：{len(target_history)}")
                print("历史记录已持久化到本地文件！")
            else:
                print("警告：会话文件不存在，历史记录未持久化！")
            print("=" * 50)

    # 运行交互式测试
    try:
        asyncio.run(test_interactive_stream_with_history())
    except KeyboardInterrupt:
        print("\n\n测试被手动中断")
    except Exception as e:
        print(f"\n测试全局错误：{str(e)}")
