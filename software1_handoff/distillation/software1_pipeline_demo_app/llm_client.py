"""OpenAI-compatible LLM client for the pipeline demo.

Reads credentials from ``.env`` at the repo root.  Supported env keys
(first non-empty wins for each):

  - API key:    ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY``
  - Base URL:   ``ANTHROPIC_API_BASE_URL`` / ``OPENAI_API_BASE`` /
                ``OPENAI_BASE_URL``
  - Model:      ``ANTHROPIC_MODEL`` / ``LLM_MODEL`` / default
                ``claude-opus-4-7``

The provider at ``api.agicto.cn/v1`` (the default in ``.env.example``)
exposes an OpenAI-style ``/chat/completions`` endpoint while serving
Anthropic Claude models, so we use ``requests`` directly to stay
SDK-agnostic.

中文说明
========
规划（planner）与列映射（mapping_engine）共用此客户端：读根目录 ``.env``，
走 OpenAI 兼容 ``/chat/completions``。Qwen3 等「思考」模型在 DashScope 上
非流式调用须 ``enable_thinking=false``，否则 400（见 ``chat_json``）。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


REPO_ROOT = Path(__file__).resolve().parents[2]
_DOTENV_LOADED = False


def _ensure_dotenv():
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    try:
        from dotenv import load_dotenv
        for p in (REPO_ROOT / ".env", REPO_ROOT.parent / ".env"):
            if p.is_file():
                load_dotenv(p, override=False)
        _DOTENV_LOADED = True
    except Exception:
        _DOTENV_LOADED = True


@dataclass
class LLMConfig:
    api_key: str
    base_url: str
    model: str
    timeout: float = 60.0
    max_retries: int = 2

    @classmethod
    def from_env(cls) -> Optional["LLMConfig"]:
        _ensure_dotenv()
        key = (os.environ.get("ANTHROPIC_API_KEY")
               or os.environ.get("OPENAI_API_KEY"))
        base = (os.environ.get("ANTHROPIC_API_BASE_URL")
                or os.environ.get("OPENAI_API_BASE")
                or os.environ.get("OPENAI_BASE_URL"))
        model = (os.environ.get("ANTHROPIC_MODEL")
                 or os.environ.get("LLM_MODEL")
                 or "claude-opus-4-7")
        if not key or not base:
            return None
        return cls(api_key=key, base_url=base.rstrip("/"), model=model)


_CONFIG_CACHE: Optional[LLMConfig] = None
_CONFIG_LOCK = threading.Lock()


def get_config() -> Optional[LLMConfig]:
    global _CONFIG_CACHE
    with _CONFIG_LOCK:
        if _CONFIG_CACHE is None:
            _CONFIG_CACHE = LLMConfig.from_env()
        return _CONFIG_CACHE


def is_available() -> bool:
    return get_config() is not None


class LLMError(RuntimeError):
    pass


def chat_json(system: str, user: str,
              max_tokens: int = 1200,
              temperature: float = 0.0) -> Dict[str, Any]:
    """Call the chat endpoint and parse a JSON object out of the reply.

    Raises :class:`LLMError` on transport failure or JSON parse failure
    after exhausting retries.
    """
    cfg = get_config()
    if cfg is None:
        raise LLMError("LLM not configured: missing api_key or base_url in .env")

    url = cfg.base_url + "/chat/completions"
    headers = {"Authorization": f"Bearer {cfg.api_key}",
               "Content-Type": "application/json"}
    payload = {
        "model": cfg.model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    # 阿里云 DashScope OpenAI 兼容口：Qwen3 系非流式必须关闭 thinking，否则 HTTP 400。
    # 其它厂商多忽略该字段，可安全附带。
    model_lower = (cfg.model or "").lower()
    if "qwen3" in model_lower or "qwen-3" in model_lower:
        payload["enable_thinking"] = False
        payload.setdefault("extra_body", {})
        if isinstance(payload["extra_body"], dict):
            payload["extra_body"]["enable_thinking"] = False

    last_err: Optional[Exception] = None
    for attempt in range(cfg.max_retries + 1):
        try:
            r = requests.post(url, headers=headers, json=payload,
                              timeout=cfg.timeout)
            if r.status_code != 200:
                last_err = LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
                time.sleep(0.6 * (attempt + 1))
                continue
            data = r.json()
            text = ""
            if "choices" in data and data["choices"]:
                msg = data["choices"][0].get("message") or {}
                text = msg.get("content") or ""
            if not text:
                last_err = LLMError(f"empty response: {json.dumps(data)[:300]}")
                continue
            parsed = _extract_json(text)
            if parsed is None:
                last_err = LLMError(f"failed to extract JSON from: {text[:300]}")
                continue
            return parsed
        except requests.RequestException as e:
            last_err = LLMError(f"request failed: {e!r}")
            time.sleep(0.6 * (attempt + 1))
            continue
    raise last_err if last_err else LLMError("unknown LLM failure")


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    s = _FENCE_RE.sub("", text).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # find first {...} block
    start = s.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(s)):
            ch = s[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = s[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break
        start = s.find("{", start + 1)
    return None
