# backend/workbench_kg_client.py — MedicalKG 检索 + LLM 综合评价
"""
知识图谱/知识库评价：

1. GET {base}/api/discovery/status 探活
2. POST {base}/api/subgraph/recall 取相关子图证据
3. 用 LLM 基于证据对分析结果做新颖性/合理性等综合评价

环境变量：
  MEDICALKG_API_BASE   默认 http://127.0.0.1:9335
  WORKBENCH_KG_EVAL=1  开启评价（否则仅返回 disabled）
  MEDICALKG_GRAPH_PATH 母图 overview.viewer.json 路径
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

_LOG = logging.getLogger(__name__)

_DEFAULT_GRAPH = (
    "/outputs/graphrag_viewer/"
    "精神疾病新版母图_低成本社区追加_qwen35_l0_l1.overview.viewer.json"
)


def kg_api_base() -> str:
    return os.getenv("MEDICALKG_API_BASE", "http://127.0.0.1:9335").rstrip("/")


def kg_eval_enabled() -> bool:
    return os.getenv("WORKBENCH_KG_EVAL", "").strip() in ("1", "true", "TRUE", "yes")


def kg_graph_path() -> str:
    return (
        os.getenv("MEDICALKG_GRAPH_PATH", "").strip()
        or _DEFAULT_GRAPH
    )


def probe_kg_status(timeout: float = 3.0) -> Dict[str, Any]:
    """轻量探活 discovery/status；失败不抛异常。"""
    base = kg_api_base()
    out: Dict[str, Any] = {
        "base": base,
        "available": False,
        "endpoint": f"{base}/api/discovery/status",
        "detail": None,
    }
    try:
        import httpx

        r = httpx.get(out["endpoint"], timeout=timeout)
        if r.status_code == 200:
            body = r.json()
            out["available"] = bool(
                body.get("ok") or body.get("graph") or body.get("status") == "ok" or True
            )
            out["detail"] = {
                k: body.get(k)
                for k in ("ok", "graph", "loaded", "resident_expected_graph_ready", "msg", "message")
                if k in body
            } or body
        else:
            out["detail"] = {"http_status": r.status_code, "text": r.text[:300]}
    except Exception as exc:
        out["detail"] = {"error": str(exc)}
        _LOG.debug("kg probe failed: %s", exc)
    return out


def _query_from_task(task: str, summary: str, query: Optional[str]) -> str:
    if query and query.strip():
        return query.strip()[:240]
    text = f"{task or ''} {summary or ''}"
    # Prefer English biomedical tokens + Chinese disease/analysis keywords
    en = re.findall(r"[A-Za-z][A-Za-z\-]{2,}", text)
    zh = re.findall(r"[\u4e00-\u9fff]{2,8}", text)
    # Drop ultra-generic tokens
    stop = {
        "the", "and", "for", "with", "from", "that", "this", "eda", "csv",
        "analysis", "summary", "complete", "please",
    }
    en = [t for t in en if t.lower() not in stop][:8]
    zh = [t for t in zh if t not in {"分析", "数据", "结果", "完整", "生成", "图表", "摘要"}][:6]
    q = " ".join((zh + en)[:10]).strip()
    return q or (task or "psychiatric clinical analysis")[:120]


def recall_subgraph_evidence(
    query: str,
    *,
    node_limit: int = 24,
    edge_limit: int = 16,
    timeout: float = 90.0,
) -> Dict[str, Any]:
    """Call MedicalKG subgraph recall; return compact evidence."""
    base = kg_api_base()
    endpoint = f"{base}/api/subgraph/recall"
    payload = {
        "data_path": kg_graph_path(),
        "query": query,
        "mode": "hybrid",
        "node_limit": node_limit,
        "edge_limit": edge_limit,
        "hops": 1,
    }
    out: Dict[str, Any] = {
        "ok": False,
        "endpoint": endpoint,
        "query": query,
        "data_path": payload["data_path"],
        "nodes": [],
        "edges": [],
        "snippets": [],
        "error": None,
    }
    try:
        import httpx

        r = httpx.post(endpoint, json=payload, timeout=timeout)
        if r.status_code != 200:
            out["error"] = f"HTTP {r.status_code}: {r.text[:300]}"
            return out
        body = r.json()
        if not body.get("ok"):
            out["error"] = str(body.get("error") or body.get("message") or body)[:400]
            return out
        viewer = body.get("viewer") or {}
        nodes = viewer.get("nodes") or body.get("nodes") or []
        edges = viewer.get("edges") or body.get("edges") or []
        out["ok"] = True
        out["elapsed_seconds"] = body.get("elapsed_seconds")
        snippets: List[str] = []
        for n in nodes[:node_limit]:
            title = (
                n.get("title_zh")
                or n.get("title_en")
                or n.get("label")
                or n.get("id")
                or ""
            )
            typ = n.get("type_zh") or n.get("type_en") or n.get("type") or ""
            if title:
                line = f"实体: {title}" + (f" ({typ})" if typ else "")
                snippets.append(line)
                out["nodes"].append({"title": title, "type": typ})
        for e in edges[:edge_limit]:
            s = e.get("source_title_zh") or e.get("source_title_en") or e.get("source") or ""
            t = e.get("target_title_zh") or e.get("target_title_en") or e.get("target") or ""
            rel = e.get("relation_label_zh") or e.get("relation_label_en") or e.get("relation") or "related"
            if s and t:
                line = f"关系: {s} —[{rel}]→ {t}"
                snippets.append(line)
                out["edges"].append({"source": s, "relation": rel, "target": t})
        out["snippets"] = snippets[:40]
    except Exception as exc:
        out["error"] = str(exc)
        _LOG.debug("subgraph recall failed: %s", exc)
    return out


def _ensure_llm_env_aliases() -> None:
    """Map persona-style LLM_PROFILE_* vars to llm_client expected names."""
    if not (os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")):
        key = os.getenv("LLM_PROFILE_API_KEY") or os.getenv("LLM_API_KEY") or ""
        if key:
            os.environ.setdefault("OPENAI_API_KEY", key)
            os.environ.setdefault("ANTHROPIC_API_KEY", key)
    if not (
        os.getenv("OPENAI_API_BASE")
        or os.getenv("OPENAI_BASE_URL")
        or os.getenv("ANTHROPIC_API_BASE_URL")
    ):
        base = os.getenv("LLM_PROFILE_API_BASE") or os.getenv("LLM_BASE_URL") or ""
        if base:
            os.environ.setdefault("OPENAI_API_BASE", base)
            os.environ.setdefault("ANTHROPIC_API_BASE_URL", base)
    if not (os.getenv("LLM_MODEL") or os.getenv("ANTHROPIC_MODEL")):
        model = os.getenv("LLM_PROFILE_MODEL") or ""
        if model:
            os.environ.setdefault("LLM_MODEL", model)
            os.environ.setdefault("ANTHROPIC_MODEL", model)


def _llm_judge(task: str, summary: str, evidence_text: str) -> Dict[str, Any]:
    """Ask LLM to judge novelty/reasonableness from KG evidence."""
    _ensure_llm_env_aliases()
    try:
        from operator_pipeline import llm_client
    except Exception as exc:
        return {"ok": False, "error": f"llm_client unavailable: {exc}"}

    system = (
        "你是科研数据分析评审助手。根据用户分析任务、结果摘要，以及知识图谱检索到的实体/关系证据，"
        "从新颖性、合理性、与已知知识一致性、可改进建议四个维度给出简短中文评价。"
        "必须返回 JSON："
        '{"novelty":0-10,"reasonableness":0-10,"consistency":0-10,'
        '"novelty_note":"...","reasonableness_note":"...","suggestions":["..."],'
        '"overall":"..."}'
    )
    user = (
        f"## 分析任务\n{task[:800]}\n\n"
        f"## 结果摘要\n{(summary or '')[:1200]}\n\n"
        f"## 知识图谱证据\n{evidence_text[:3500] or '(无检索证据)'}\n"
    )
    try:
        data = llm_client.chat_json(system=system, user=user)
        if isinstance(data, dict):
            return {"ok": True, "judgment": data}
        return {"ok": False, "error": f"unexpected LLM payload type: {type(data)}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:400]}


def enrich_evaluation_with_kg(
    task: str,
    summary: str,
    evaluation: Dict[str, Any],
    query: Optional[str] = None,
) -> Dict[str, Any]:
    """MedicalKG recall + LLM 综合评价；失败时降级，不阻塞主分析。"""
    ev = dict(evaluation or {})
    hook: Dict[str, Any] = {
        "provider": "medicalkg_discovery+llm",
        "status": "stub",
        "api_base": kg_api_base(),
        "suggested_endpoints": [
            "GET /api/discovery/status",
            "POST /api/subgraph/recall",
        ],
    }

    if not kg_eval_enabled():
        hook["status"] = "disabled"
        hook["note"] = "设置 WORKBENCH_KG_EVAL=1 后启用 MedicalKG 检索 + LLM 评价"
        ev["knowledge_graph"] = hook
        return ev

    probe = probe_kg_status()
    hook["probe"] = probe
    if not probe.get("available"):
        hook["status"] = "unreachable"
        hook["note"] = "MedicalKG 探活失败；评价仍使用本地启发式结果"
        ev["knowledge_graph"] = hook
        if not ev.get("novelty_note"):
            ev["novelty_note"] = "知识图谱不可达，未做新颖性检索评价"
        return ev

    q = _query_from_task(task, summary, query)
    recall = recall_subgraph_evidence(q)
    hook["recall"] = {
        "ok": recall.get("ok"),
        "query": recall.get("query"),
        "data_path": recall.get("data_path"),
        "node_count": len(recall.get("nodes") or []),
        "edge_count": len(recall.get("edges") or []),
        "elapsed_seconds": recall.get("elapsed_seconds"),
        "error": recall.get("error"),
        "snippets": (recall.get("snippets") or [])[:12],
    }
    hook["novelty_evidence"] = recall.get("snippets") or []

    evidence_text = "\n".join(recall.get("snippets") or [])
    llm = _llm_judge(task, summary, evidence_text)
    hook["llm"] = {
        "ok": llm.get("ok"),
        "error": llm.get("error"),
    }
    if llm.get("ok") and isinstance(llm.get("judgment"), dict):
        j = llm["judgment"]
        hook["status"] = "enriched"
        hook["note"] = "已用 MedicalKG 子图证据 + LLM 完成综合评价"
        hook["judgment"] = j
        # Merge into top-level evaluation fields used by UI
        if j.get("novelty_note"):
            ev["novelty_note"] = str(j.get("novelty_note"))[:500]
        if j.get("reasonableness_note"):
            ev["reasonableness"] = str(j.get("reasonableness_note"))[:500]
        elif j.get("overall"):
            ev["reasonableness"] = str(j.get("overall"))[:500]
        scores = {
            "novelty": j.get("novelty"),
            "reasonableness": j.get("reasonableness"),
            "consistency": j.get("consistency"),
        }
        hook["scores"] = scores
        if isinstance(j.get("suggestions"), list):
            flags = list(ev.get("flags") or [])
            for s in j["suggestions"][:5]:
                flags.append(f"KG建议: {s}")
            ev["flags"] = flags
    elif recall.get("ok"):
        hook["status"] = "evidence_only"
        hook["note"] = (
            "MedicalKG 证据已召回；LLM 评价失败，仅返回图谱证据。"
            f" llm_error={llm.get('error')}"
        )
        if not ev.get("novelty_note"):
            ev["novelty_note"] = (
                "已检索到图谱证据，但 LLM 评价未完成："
                + "; ".join((recall.get("snippets") or [])[:3])
            )[:500]
    else:
        hook["status"] = "recall_failed"
        hook["note"] = f"子图召回失败: {recall.get('error')}"
        if not ev.get("novelty_note"):
            ev["novelty_note"] = "知识图谱召回失败，未生成新颖性证据"

    ev["knowledge_graph"] = hook
    return ev
