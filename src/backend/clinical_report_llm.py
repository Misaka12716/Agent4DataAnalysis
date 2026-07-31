# backend/clinical_report_llm.py — 临床报告大模型润色（可插拔，API_KEY 为空时走规则模板）

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

DIAG_ZH = {
    "depression": "抑郁",
    "anxiety": "焦虑",
    "schizophrenia": "精神分裂症",
    "sleep_disorder": "睡眠障碍",
    "child_adolescent": "儿童青少年精神障碍",
}


def llm_available() -> bool:
    try:
        from configs.config import API_KEY
        return bool(API_KEY)
    except Exception:
        return False


def _looks_like_json_blob(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if t.startswith("{") or t.startswith("["):
        return True
    return bool(re.search(r'"\w+"\s*:', t))


def _rule_based_narrative(section_title: str, facts: Any) -> str:
    """无 LLM 时的结构化中文摘要。"""
    title = section_title or "分析结果"
    if isinstance(facts, str):
        if not _looks_like_json_blob(facts):
            return facts[:4000]
        try:
            facts = json.loads(facts)
        except Exception:
            return facts[:4000]

    if not isinstance(facts, dict):
        return str(facts)[:4000]

    if "relationships" in facts or "pairs" in facts:
        primary = facts.get("primary_diagnosis", "")
        rels = facts.get("relationships") or facts.get("pairs") or []
        lines = [
            f"本队列以{DIAG_ZH.get(primary, primary)}为主诊断信号，共纳入 {facts.get('total_patients', 'N/A')} 例。",
            facts.get("inference_note", ""),
        ]
        for r in rels[:6]:
            other = DIAG_ZH.get(r.get("comorbid_diagnosis", ""), r.get("comorbid_diagnosis", ""))
            rr = r.get("relative_risk")
            pv = r.get("p_value")
            sig = "显著" if r.get("significant") else "未达显著"
            if rr is not None and pv is not None:
                lines.append(
                    f"与{other}共现 {r.get('co_occurring_count', 0)} 例，"
                    f"相对风险 RR={rr}，p={pv}（{sig}）。"
                )
        return "\n".join(l for l in lines if l)[:4000]

    if "matrix" in facts or "frequency_matrix" in facts:
        diags = facts.get("diagnoses") or []
        total = facts.get("total_patients", "")
        return (
            f"共病矩阵覆盖 {len(diags)} 类诊断/症状信号，队列 {total} 例。"
            f" {facts.get('inference_note', '')}"
        )[:4000]

    if "predictions" in facts or "risk_distribution" in facts:
        dist = facts.get("risk_distribution") or (facts.get("summary") or {}).get("risk_distribution") or {}
        return f"批量风险预测完成，风险分层分布：{dist}。"[:4000]

    parts = []
    for k, v in list(facts.items())[:8]:
        if k in ("methodology", "evidence", "clinical_caveats"):
            continue
        parts.append(f"{k}：{v}")
    body = "；".join(parts)
    return f"{title}：{body}"[:4000] if body else f"{title}：请结合平台分析模块结果综合判读。"


def generate_clinical_narrative(section_title: str, facts: Any, language: str = "zh") -> str:
    """调用大模型将结构化事实润色为报告段落；失败或未配置时回退规则模板。"""
    if isinstance(facts, dict):
        fact_text = json.dumps(facts, ensure_ascii=False, indent=0)[:3000]
    else:
        fact_text = str(facts)[:3000]

    if not llm_available():
        return _rule_based_narrative(section_title, facts)

    try:
        from configs.config import API_KEY, CLINICAL_REPORT_MODEL, DEFAULT_MODEL, OPENAI_COMPATIBLE_API_BASE
        from langchain_openai import ChatOpenAI
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = ChatOpenAI(
            model=CLINICAL_REPORT_MODEL or DEFAULT_MODEL,
            temperature=0.3,
            openai_api_key=API_KEY,
            openai_api_base=OPENAI_COMPATIBLE_API_BASE,
        )
        sys_prompt = (
            "你是精神科临床研究报告撰写助手，遵循学术论文叙事结构（What/Why/So What）。"
            "根据结构化分析事实撰写该章节正文（中文）。"
            "要求：1) 不要输出 JSON 或代码；2) 2-4 段自然语言，首段点明本章要回答的问题；"
            "3) 引用关键数字（样本量、率、RR、p 值等）；"
            "4) 结论章节用条目列出 2-4 条可操作建议；"
            "5) 注明探索性/决策辅助性质，避免过度诊断结论。"
        )
        user_prompt = f"章节标题：{section_title}\n\n结构化事实：\n{fact_text}"
        resp = llm.invoke([SystemMessage(content=sys_prompt), HumanMessage(content=user_prompt)])
        text = (resp.content or "").strip()
        if len(text) < 40:
            return _rule_based_narrative(section_title, facts)
        return text[:4000]
    except Exception:
        return _rule_based_narrative(section_title, facts)


def enrich_report_sections(
    sections: Dict[str, str],
    use_llm: bool = True,
) -> Dict[str, str]:
    """将章节中的 JSON 块或原始 dict 字符串润色为可读正文。"""
    enriched: Dict[str, str] = {}
    for title, content in (sections or {}).items():
        raw = str(content or "").strip()
        if not raw or "待补充" in raw:
            enriched[title] = raw or f"（{title} 数据待补充）"
            continue
        if _looks_like_json_blob(raw):
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw
            enriched[title] = (
                generate_clinical_narrative(title, parsed)
                if use_llm
                else _rule_based_narrative(title, parsed)
            )
        elif use_llm and llm_available() and len(raw) < 120 and raw.startswith("{"):
            enriched[title] = generate_clinical_narrative(title, raw)
        else:
            enriched[title] = raw
    return enriched
