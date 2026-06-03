import json
from typing import Any, Dict

from langchain_openai import ChatOpenAI

from configs.config import API_KEY, DEFAULT_READER_MODEL, OPENAI_COMPATIBLE_API_BASE
from configs.prompts import get_system_prompt, get_user_prompt
from reader.formatters import workspace_digest_to_markdown


def synthesize_markdown_node(state: Dict[str, Any]) -> Dict[str, Any]:
    digest = state.get("workspace_digest") or {}
    lang = state.get("lang") or "zh"
    files = digest.get("files") or {}

    if not files:
        summary = digest.get("summary") or (
            "工作区无文件" if lang == "zh" else "No files in workspace"
        )
        return {"markdown_summary": summary}

    structured = workspace_digest_to_markdown(digest)
    if len(structured) <= 12000:
        return {"markdown_summary": structured.strip()}

    try:
        llm = ChatOpenAI(
            model=DEFAULT_READER_MODEL,
            temperature=0.2,
            api_key=API_KEY,
            base_url=OPENAI_COMPATIBLE_API_BASE,
        )
        compact = json.dumps(digest, ensure_ascii=False, default=str)[:24000]
        system = get_system_prompt("reader", lang)
        user = get_user_prompt("reader", "synthesize", lang=lang, digest_json=compact)
        if not user:
            user = (
                f"请将以下工作区文件摘要压缩为规划助手可读的 Markdown（保留路径、列名、样本行要点）：\n\n{compact}"
                if lang == "zh"
                else f"Compress this workspace digest into planner-friendly Markdown:\n\n{compact}"
            )
        resp = llm.invoke(
            [
                {"role": "system", "content": system or "你是工作区文件摘要助手。"},
                {"role": "user", "content": user},
            ]
        )
        text = (resp.content or "").strip()
        if text:
            return {"markdown_summary": text}
    except Exception:
        pass

    return {"markdown_summary": structured.strip()}
