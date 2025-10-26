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

# -------------------------- Configuration Parameters --------------------------
SESSIONS_FILE_PATH = f"{PATH}/utils/chat_sessions.json"
SESSIONS_LOCK_PATH = f"{SESSIONS_FILE_PATH}.lock"
_chat_sessions = {}


# -------------------------- Utility Functions (File I/O) --------------------------
def _load_sessions() -> dict:
    lock = FileLock(SESSIONS_LOCK_PATH)
    with lock:
        try:
            if not os.path.exists(SESSIONS_FILE_PATH):
                print(
                    f"Session file does not exist, initializing empty sessions: {SESSIONS_FILE_PATH}"
                )
                return {}

            with open(SESSIONS_FILE_PATH, "r", encoding="utf-8") as f:
                sessions = json.load(f)
            print(f"Successfully loaded {len(sessions)} historical sessions")
            return sessions

        except json.JSONDecodeError:
            print(
                f"Session file format error, resetting to empty sessions: {SESSIONS_FILE_PATH}"
            )
            if os.path.exists(SESSIONS_FILE_PATH):
                backup_path = f"{SESSIONS_FILE_PATH}.backup.{datetime.now().strftime('%Y%m%d%H%M%S')}"
                os.rename(SESSIONS_FILE_PATH, backup_path)
                print(f"Corrupted file backed up to: {backup_path}")
            return {}

        except IOError as e:
            print(f"Failed to load session file: {str(e)}, using empty sessions")
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
            print(f"Failed to save session file: {str(e)}")
            return False


# -------------------------- Module Initialization: Load Historical Sessions --------------------------
_chat_sessions = _load_sessions()


# -------------------------- Basic Session Operations --------------------------
def _generate_session_id() -> str:
    return str(uuid.uuid4())


def parse_model_output(raw_content: str) -> tuple[str, str]:
    parts = raw_content.split("</think>", 1)
    if len(parts) == 2 and parts[0].strip().startswith("</think>"):
        thinking = parts[0].strip()[7:].strip()
        content = parts[1].strip()
        return thinking, content
    return "", raw_content.strip()


# -------------------------- API Call Utility Functions --------------------------
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
            raise ValueError(f"API request failed [{response.status}]: {error_text}")

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
                    print(f"Ignoring undecodable bytes: {line_bytes[:50]}...")
                    continue

                if line == "[DONE]":
                    return
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    print(f"Ignoring invalid JSON data: {line[:50]}...")
                    continue

            buffer = lines[-1]

        if buffer.strip():
            try:
                line = buffer.decode("utf-8").lstrip("data: ")
                if line != "[DONE]":
                    yield json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                print(f"Ignoring invalid data in buffer: {buffer[:50]}...")


async def _call_openai_compatible_api(
    session: ClientSession, request_data: dict
) -> dict:
    api_url = f"{OPENAI_COMPATIBLE_API_BASE}/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"}

    async with session.post(
        url=api_url, headers=headers, data=json.dumps(request_data)
    ) as response:
        if not response.ok:
            error_text = await response.text()
            raise ValueError(f"API request failed [{response.status}]: {error_text}")

        return await response.json()


# -------------------------- Single-turn Conversation Interface --------------------------
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
        else:
            response = await _call_openai_compatible_api(session, request_data)
            if "choices" in response and response["choices"]:
                full_content = (
                    response["choices"][0].get("message", {}).get("content", "")
                )
                thinking, content = parse_model_output(full_content)
                return {
                    "thinking": thinking,
                    "content": content,
                    "model": selected_model,
                    "success": True,
                }
            else:
                return {
                    "thinking": "",
                    "content": "No valid response received",
                    "model": selected_model,
                    "error": "Abnormal API return format",
                    "success": False,
                }

    except Exception as e:
        error_msg = f"Single-turn conversation error: {str(e)}"
        print(f"{datetime.now()} {error_msg}")
        return {
            "thinking": "",
            "content": f"Processing failed: {str(e)}",
            "model": selected_model,
            "error": str(e),
            "success": False,
        }


# -------------------------- Multi-turn Conversation Interface --------------------------
def new_chat_session() -> str:
    session_id = _generate_session_id()
    _chat_sessions[session_id] = []
    if _save_sessions(_chat_sessions):
        print(
            f"New chat session created successfully, ID: {session_id} (saved to file)"
        )
    else:
        print(
            f"New chat session created successfully (ID: {session_id}), but failed to save to file!"
        )
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
        return {"error": f"Session does not exist: {session_id}", "success": False}

    selected_model = model_name if model_name in SUPPORTED_MODELS else DEFAULT_MODEL
    try:
        # Construct user message
        user_content = prompt if need_thinking else f"{prompt}/no_think"
        user_message = {"role": "user", "content": user_content}
        if images and len(images) > 0:
            user_message["images"] = images

        # Load conversation history
        chat_history = _chat_sessions[session_id].copy()
        chat_history.append(user_message)

        # Construct API request
        request_data = {
            "model": selected_model,
            "messages": chat_history,
            "stream": stream,
        }

        if stream:

            async def stream_generator():
                full_content = ""
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

                # Stream ends: update conversation history
                thinking, content = parse_model_output(full_content)
                assistant_message = {"role": "assistant", "content": content}
                _chat_sessions[session_id].append(user_message)
                _chat_sessions[session_id].append(assistant_message)
                _save_sessions(_chat_sessions)

                yield {
                    "type": "complete",
                    "session_id": session_id,
                    "thinking": thinking,
                    "content": content,
                    "model": selected_model,
                    "history_length": len(_chat_sessions[session_id]),
                    "success": True,
                }

            return stream_generator()
        else:
            response = await _call_openai_compatible_api(http_session, request_data)
            if "choices" in response and response["choices"]:
                full_content = (
                    response["choices"][0].get("message", {}).get("content", "")
                )
                thinking, content = parse_model_output(full_content)

                # Update conversation history
                assistant_message = {"role": "assistant", "content": content}
                _chat_sessions[session_id].append(user_message)
                _chat_sessions[session_id].append(assistant_message)
                _save_sessions(_chat_sessions)

                return {
                    "session_id": session_id,
                    "thinking": thinking,
                    "content": content,
                    "model": selected_model,
                    "history_length": len(_chat_sessions[session_id]),
                    "success": True,
                }
            else:
                return {
                    "session_id": session_id,
                    "thinking": "",
                    "content": "No valid response received",
                    "model": selected_model,
                    "error": "Abnormal API return format",
                    "success": False,
                }

    except Exception as e:
        error_msg = f"Multi-turn conversation error [{session_id}]: {str(e)}"
        print(f"{datetime.now()} {error_msg}")
        return {
            "session_id": session_id,
            "thinking": "",
            "content": f"Conversation processing failed: {str(e)}",
            "error": str(e),
            "success": False,
        }
