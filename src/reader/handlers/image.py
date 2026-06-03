import base64
import os
from typing import Any, Dict, Optional

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from PIL import Image

from configs.config import (
    API_KEY,
    DEFAULT_VISION_MODEL,
    OPENAI_COMPATIBLE_API_BASE,
)
from configs.prompts import SYSTEM_PROMPT_READER_VISION


def _vision_llm() -> Optional[ChatOpenAI]:
    if not (DEFAULT_VISION_MODEL or "").strip():
        return None
    return ChatOpenAI(
        model=DEFAULT_VISION_MODEL.strip(),
        temperature=0.2,
        api_key=API_KEY,
        base_url=OPENAI_COMPATIBLE_API_BASE,
    )


def _describe_image_with_vision(abs_path: str, lang: str) -> str:
    llm = _vision_llm()
    if llm is None:
        return ""
    ext = os.path.splitext(abs_path)[1].lower().lstrip(".")
    mime = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
        "bmp": "image/bmp",
    }.get(ext, "image/png")
    with open(abs_path, "rb") as f:
        b64 = base64.standard_b64encode(f.read()).decode("ascii")
    system = SYSTEM_PROMPT_READER_VISION.get(lang) or SYSTEM_PROMPT_READER_VISION.get("zh") or ""
    msg = HumanMessage(
        content=[
            {"type": "text", "text": system},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            },
        ]
    )
    try:
        resp = llm.invoke([msg])
        return (resp.content or "").strip()
    except Exception as e:
        return f"（Vision 分析失败: {e}）"


def digest_image_file(workspace_root: str, relative_path: str, lang: str = "zh") -> Dict[str, Any]:
    fp = os.path.join(workspace_root, relative_path.replace("/", os.sep))
    ext = os.path.splitext(relative_path)[1].lower().lstrip(".")
    entry: Dict[str, Any] = {
        "file_type": "image",
        "format": ext or "unknown",
        "relative_path": relative_path,
    }
    try:
        size = os.path.getsize(fp)
        entry["file_size_bytes"] = size
        with Image.open(fp) as im:
            entry["width"] = im.width
            entry["height"] = im.height
            entry["mode"] = im.mode
    except Exception as e:
        entry["error"] = str(e)
        return entry

    desc = _describe_image_with_vision(fp, lang)
    if desc:
        entry["vision_description"] = desc
    else:
        entry["vision_description"] = (
            "（未配置 DEFAULT_VISION_MODEL，仅记录图片元数据）"
            if lang == "zh"
            else "(DEFAULT_VISION_MODEL not set; metadata only)"
        )
    return entry
