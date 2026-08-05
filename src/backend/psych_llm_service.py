# backend/psych_llm_service.py — 融合大语言模型

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from db import psych_store as store

logger = logging.getLogger(__name__)


def _chat(prompt: str, system: str = "你是精神专科临床数据分析助手。") -> Tuple[str, Optional[str]]:
    try:
        from operator_pipeline.llm_client import chat_json, is_available

        if is_available():
            obj = chat_json(system=system, user=prompt)
            if obj is not None:
                return json.dumps(obj, ensure_ascii=False), None
    except Exception as exc:
        logger.warning("llm_client.chat_json failed: %s", exc)

    try:
        from planner.planner_utils import create_llm

        llm = create_llm(streaming=False)
        resp = llm.invoke(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ]
        )
        content = getattr(resp, "content", None) or str(resp)
        return str(content), None
    except Exception as exc:
        logger.exception("LLM invoke failed")
        return "", f"大模型调用失败: {exc}"


def _extract_json(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return {"raw": text}
    return {"raw": text}


def extract(
    user_id: int,
    text: str,
    extract_type: str = "clinical_entities",
    dataset_id: Optional[int] = None,
    record_id: Optional[int] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    if not text or not str(text).strip():
        return None, "text 不能为空"
    prompt = (
        f"请从以下精神专科文本中抽取关键诊疗信息，类型={extract_type}。"
        "输出严格 JSON，包含 diagnoses/symptoms/medications/scales/risks/followup 字段（数组或对象）。\n\n"
        f"文本：\n{text[:8000]}"
    )
    raw, err = _chat(prompt)
    if err:
        return None, err
    parsed = _extract_json(raw)
    model_name = None
    try:
        from configs.config import DEFAULT_MODEL

        model_name = DEFAULT_MODEL
    except Exception:
        pass
    eid, ierr = store.insert_llm_extraction(
        {
            "user_id": user_id,
            "dataset_id": dataset_id,
            "record_id": record_id,
            "extract_type": extract_type,
            "result_json": parsed,
            "model_name": model_name,
        }
    )
    if ierr:
        return None, ierr
    return {"id": eid, "extract_type": extract_type, "result": parsed, "model_name": model_name}, None


def relate(
    user_id: int, entities: Dict[str, Any], question: Optional[str] = None
) -> Tuple[Optional[dict], Optional[str]]:
    prompt = (
        "基于下列已抽取的精神专科诊疗实体，进行关联分析（共病可能、用药-症状关系、风险提示）。"
        "输出 JSON：{relations:[], risks:[], summary:''}。\n\n"
        f"实体：{json.dumps(entities, ensure_ascii=False)[:6000]}\n"
        f"补充问题：{question or '无'}"
    )
    raw, err = _chat(prompt)
    if err:
        return None, err
    return {"analysis": _extract_json(raw), "raw": raw}, None


def nl_query(
    user_id: int,
    query: str,
    dataset_id: Optional[int] = None,
    schema_hint: Optional[Any] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    if not query:
        return None, "query 不能为空"
    schema = schema_hint
    if schema is None and dataset_id is not None:
        ds, err = store.get_dataset(int(dataset_id), user_id)
        if err:
            return None, err
        schema = (ds or {}).get("schema_json")
    prompt = (
        "将自然语言检索意图转为结构化过滤条件 JSON："
        "{filters:[{field,op,value}], select:[], limit:number, explanation:''}。\n"
        f"数据集schema：{json.dumps(schema, ensure_ascii=False)[:3000]}\n"
        f"用户问题：{query}"
    )
    raw, err = _chat(prompt)
    if err:
        return None, err
    return {"query": query, "parsed": _extract_json(raw), "dataset_id": dataset_id}, None


def qa(
    user_id: int,
    question: str,
    context: Optional[str] = None,
    dataset_id: Optional[int] = None,
    task_id: Optional[str] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    if not question:
        return None, "question 不能为空"
    pieces = [context or ""]
    if dataset_id is not None:
        ds, _ = store.get_dataset(int(dataset_id), user_id)
        if ds:
            pieces.append(f"dataset={json.dumps(ds, ensure_ascii=False, default=str)[:2000]}")
    if task_id:
        task, _ = store.get_task_by_task_id(task_id)
        if task and int(task.get("user_id") or 0) == int(user_id):
            pieces.append(f"task_result={json.dumps(task.get('result_json'), ensure_ascii=False, default=str)[:3000]}")
    prompt = (
        "你是精神专科数据分析助手。基于上下文回答用户分析问题，给出结论、依据与注意事项。\n"
        f"上下文：\n{chr(10).join(pieces)[:6000]}\n\n问题：{question}"
    )
    raw, err = _chat(prompt, system="你是精神专科临床数据分析助手，回答需谨慎、可追溯。")
    if err:
        return None, err
    return {"question": question, "answer": raw}, None
