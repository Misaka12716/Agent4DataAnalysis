"""OpenAI-compatible LLM client for the pipeline demo.

Reads credentials from ``.env`` at the repo root.  Supported env keys
(first non-empty wins for each):

  - API key:    ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY``
  - Base URL:   ``ANTHROPIC_API_BASE_URL`` / ``OPENAI_API_BASE`` /
                ``OPENAI_BASE_URL``
  - Model:      ``ANTHROPIC_MODEL`` / ``LLM_MODEL`` / default
                ``claude-sonnet-4-6``

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
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


REPO_ROOT = Path(__file__).resolve().parents[2]
_DOTENV_LOADED = False


def _is_private_model_gateway(base_url: str) -> bool:
    """Only local/private OpenAI-compatible gateways may omit an API key."""
    try:
        host = (urlparse(base_url).hostname or "").strip().lower()
    except Exception:
        return False
    if host in {"localhost", "host.docker.internal"}:
        return True
    try:
        addr = ip_address(host)
        return addr.is_private or addr.is_loopback
    except ValueError:
        return False


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
        try:
            _retries_env = int(os.environ.get("LLM_HTTP_RETRIES", "5"))
        except ValueError:
            _retries_env = 5
        key = (os.environ.get("ANTHROPIC_API_KEY")
               or os.environ.get("OPENAI_API_KEY"))
        base = (os.environ.get("ANTHROPIC_API_BASE_URL")
                or os.environ.get("OPENAI_API_BASE")
                or os.environ.get("OPENAI_BASE_URL"))
        env_model = (os.environ.get("ANTHROPIC_MODEL")
                       or os.environ.get("LLM_MODEL"))
        # Always defer to AgentPlatform configs.config.DEFAULT_MODEL when
        # the user has not explicitly set LLM_MODEL.  This keeps benchmarks
        # in sync with config.py whenever we change the canonical model.
        try:
            from configs.config import (OPENAI_COMPATIBLE_API_BASE, API_KEY,
                                          DEFAULT_MODEL)
            if not key and API_KEY:
                key = API_KEY
            if not base and OPENAI_COMPATIBLE_API_BASE:
                base = OPENAI_COMPATIBLE_API_BASE
            config_default_model = DEFAULT_MODEL
        except Exception:
            config_default_model = None
        model = env_model or config_default_model or "deepseek-v3.2"
        if not base:
            return None
        # Ollama's local OpenAI-compatible endpoint needs no credential, while
        # the client still requires a non-empty placeholder for its headers.
        if not key and _is_private_model_gateway(base):
            key = "ollama"
        if not key:
            return None
        return cls(api_key=key, base_url=base.rstrip("/"), model=model,
                   max_retries=_retries_env)


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


# V8 改法-3：把 LLM 输出对齐到 bit-for-bit 可复现。
#   1. seed=BENCH_SEED 让 backend 在 nondeterministic batching 路径里走同一条
#      （DeepSeek / OpenAI v3.2 兼容口都支持；不支持的 backend 会静默忽略）
#   2. response_format=json_object 让 backend 强制合法 JSON，
#      避免 markdown 包裹 / 多余前缀文本导致 _extract_json 失败或回落到下一段
BENCH_SEED = 42


def _record_tokens(stage: str, model: Optional[str],
                    data: Dict[str, Any]) -> None:
    """Best-effort: push this call's usage into the token ledger.  Never
    raises (accounting must not break the pipeline)."""
    try:
        from utils import token_ledger
        token_ledger.record_usage_dict(stage, model, (data or {}).get("usage"))
    except Exception:
        pass


def chat_json(system: str, user: str,
              max_tokens: int = 1200,
              temperature: float = 0.0,
              seed: int = BENCH_SEED,
              json_mode: bool = True,
              stage: str = "llm") -> Dict[str, Any]:
    """Call the chat endpoint and parse a JSON object out of the reply.

    Raises :class:`LLMError` on transport failure or JSON parse failure
    after exhausting retries.

    ``stage`` labels the call for token accounting (e.g. ``"planner"``,
    ``"mapping"``); usage is recorded into ``utils.token_ledger``.

    中文：固定 seed + 开启 backend JSON mode。所有调用方的 prompt 已经
    在文本里显式提到 "JSON"，符合 OpenAI/DeepSeek 对 json_object 模式
    的 prompt 要求。``stage`` 用于按阶段累计 token 花费。
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
        "seed": seed,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
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
                # ADR-0002 (Tier 1) — distinguish transient vs deterministic
                # failures.  429 / 5xx / explicit quota / rate strings are
                # transient and worth backing off + retrying.  Other 4xx
                # (400 bad request, 401 unauthorised, 403 forbidden, 404
                # missing, 422 invalid payload) are deterministic
                # client-side bugs: retrying just burns wall time and quota.
                # Bail immediately on those so the caller (a stage) can
                # decide whether to escalate.
                body_l = (r.text or "").lower()
                transient = (r.status_code in (429, 500, 502, 503, 504)
                             or "insufficient_quota" in body_l
                             or "rate" in body_l)
                deterministic_4xx = (400 <= r.status_code < 500
                                     and not transient)
                if deterministic_4xx:
                    raise last_err
                if transient:
                    import random as _r
                    time.sleep(min(45.0, 2.0 * (2 ** attempt))
                               + _r.uniform(0, 1.5))
                else:
                    time.sleep(0.6 * (attempt + 1))
                continue
            data = r.json()
            # Record token usage regardless of parse outcome — the
            # backend already charged us for this call.
            _record_tokens(stage, cfg.model, data)
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
