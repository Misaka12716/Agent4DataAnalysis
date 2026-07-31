# backend/report_export_service.py
# 精神专科图文报告输出 — 构建 + HTML 渲染 + PDF 导出

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from utils.mysql_utils import mysql_handler
from db.report_schema import TABLE_REPORTS

REPORT_EXPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "workspace", "reports")

DIAG_ZH = {
    "depression": "抑郁",
    "anxiety": "焦虑",
    "schizophrenia": "精神分裂症",
    "sleep_disorder": "睡眠障碍",
    "child_adolescent": "儿童青少年精神障碍",
}

DEFAULT_REPORT_STRUCTURE = [
    "研究对象基本信息",
    "数据质控结果",
    "HAMD量表分析",
    "治疗反应分析",
    "抑郁-焦虑共病分析",
    "用药与结局关联",
    "结论与建议",
]

DEFAULT_BUILTIN_TEMPLATE = {
    "id": 0,
    "template_name": "临床标准报告（内置）",
    "disease_type": "depression",
    "version": "1.0.0",
    "report_structure": DEFAULT_REPORT_STRUCTURE,
}


def resolve_report_template(
    template_id: Optional[int] = None,
) -> Tuple[dict, int, Optional[str]]:
    """解析报告模板：指定 id → 库内首个 → 内置默认结构。"""
    from backend.template_service import TemplateService

    if template_id is not None and int(template_id) > 0:
        template, terr = TemplateService.get_template(int(template_id))
        if template:
            return template, int(template_id), None

    templates, lerr = TemplateService.list_templates()
    if not lerr and templates:
        first = templates[0]
        tid = int(first.get("id") or 0)
        return first, tid, None

    builtin = dict(DEFAULT_BUILTIN_TEMPLATE)
    builtin["report_structure"] = list(DEFAULT_REPORT_STRUCTURE)
    return builtin, 0, None

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  :root {{
    --text: #1a1a1a;
    --muted: #666;
    --border: #e0e0e0;
    --accent: #2c5282;
    --abstract-bg: #f7fafc;
    --limit-bg: #fffaf0;
  }}
  body {{
    font-family: "Microsoft YaHei", "SimHei", "Noto Sans SC", "Source Han Sans SC", sans-serif;
    max-width: 820px; margin: 0 auto; padding: 28px 24px; color: var(--text);
    line-height: 1.65; font-size: 14px;
  }}
  h1 {{ text-align: center; font-size: 22px; font-weight: 700; margin: 0 0 6px; letter-spacing: 0.02em; }}
  .meta {{ text-align: center; color: var(--muted); font-size: 12px; margin-bottom: 28px; }}
  .abstract {{
    background: var(--abstract-bg); border-left: 4px solid var(--accent);
    padding: 14px 16px; margin: 0 0 28px; border-radius: 0 4px 4px 0;
  }}
  .abstract h2 {{ margin: 0 0 8px; font-size: 14px; color: var(--accent); border: none; padding: 0; }}
  .abstract p {{ margin: 0 0 8px; }}
  .abstract p:last-child {{ margin-bottom: 0; }}
  section.report-section {{ margin-top: 28px; }}
  section.report-section h2 {{
    font-size: 15px; font-weight: 700; color: var(--text);
    border-bottom: 1px solid var(--border); padding-bottom: 6px; margin: 0 0 12px;
  }}
  section.report-section h2 .sec-num {{ color: var(--accent); margin-right: 6px; }}
  section.report-section p {{ margin: 0 0 10px; text-align: justify; }}
  section.report-section ul {{ margin: 8px 0 12px 20px; padding: 0; }}
  section.report-section li {{ margin-bottom: 6px; }}
  .contributions {{ background: #f0fff4; border: 1px solid #c6f6d5; padding: 12px 14px; border-radius: 4px; }}
  .limitations {{
    background: var(--limit-bg); border-left: 4px solid #dd6b20;
    padding: 14px 16px; margin-top: 28px; border-radius: 0 4px 4px 0;
  }}
  .limitations h2 {{ margin: 0 0 8px; font-size: 14px; color: #c05621; border: none; padding: 0; }}
  table {{ border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 13px; }}
  th, td {{ border: 1px solid var(--border); padding: 6px 10px; text-align: left; }}
  th {{ background: #f5f5f5; }}
  .abnormal {{ color: #c00; font-weight: bold; }}
  .footer {{
    margin-top: 40px; font-size: 11px; color: #999; text-align: center;
    border-top: 1px solid #eee; padding-top: 12px;
  }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="meta">生成时间: {generated_at} | 模板: {template_name} | 版本: {version}</div>
{abstract_block}
{body}
{limitations_block}
<div class="footer">本报告由 Agent Platform 精神科数据分析平台自动生成，仅供临床科研决策辅助，须经负责人复核后使用。</div>
</body>
</html>"""


def _ensure_table() -> Tuple[bool, Optional[str]]:
    try:
        if not mysql_handler._check_table_exists(TABLE_REPORTS):
            from db.report_schema import REPORT_TABLE_DDL
            affected, err = mysql_handler.execute(REPORT_TABLE_DDL)
            if err:
                return False, f"创建报告表失败: {err}"
        else:
            # 旧表可能缺列 — 补齐
            required = {"report_json", "html_content", "pdf_path", "sections", "session_id", "template_id"}
            existing = mysql_handler.get_table_columns(TABLE_REPORTS)
            if existing:
                alters = {
                    "report_json": "TEXT",
                    "html_content": "TEXT",
                    "pdf_path": "TEXT",
                    "sections": "TEXT",
                    "session_id": "TEXT",
                    "template_id": "INTEGER",
                }
                for col, ctype in alters.items():
                    if col in required and col not in existing:
                        _, err = mysql_handler.execute(
                            f"ALTER TABLE {TABLE_REPORTS} ADD COLUMN {col} {ctype}"
                        )
                        if err and "duplicate" not in str(err).lower():
                            return False, f"迁移报告表失败: {err}"
        return True, None
    except Exception as e:
        return False, str(e)


def _format_rate_text(rates: dict) -> str:
    parts = []
    for k, v in (rates or {}).items():
        try:
            fv = float(v)
            pct = fv if fv > 1 else fv * 100
            parts.append(f"{k} 异常率 {pct:.1f}%")
        except (TypeError, ValueError):
            parts.append(f"{k} {v}")
    return "、".join(parts)


def _build_abstract(sections: List[dict], report_json: dict) -> str:
    """按 ml-paper-writing 五句式摘要：成果 / 难点 / 方法 / 证据 / 关键数字。"""
    titles = [s.get("title", "") for s in sections]
    cohort_hint = ""
    for s in sections:
        if any(k in s.get("title", "") for k in ("研究对象", "基本信息", "纳排")):
            cohort_hint = str(s.get("content", ""))[:200]
            break
    disease = DIAG_ZH.get(report_json.get("disease_type", ""), report_json.get("disease_type", "精神科"))
    lines = [
        f"本报告对{disease}队列开展标准化量表分析与临床决策辅助汇总。",
        "精神科真实世界数据存在异质性高、参考区间分层复杂、共病结构交织等难点，需结合多模块证据综合判读。",
        "分析流程覆盖数据质控、量表统计、随访趋势、共病矩阵、风险预测与参考区间比对。",
        f"报告共 {len(sections)} 个章节：{'、'.join(titles[:4])}{'…' if len(titles) > 4 else ''}。",
    ]
    if cohort_hint:
        lines.append(cohort_hint)
    else:
        lines.append("具体样本量与关键指标见各章节实证结果。")
    return "\n".join(lines)


def _section_to_html(section: dict, section_num: int = 0) -> str:
    """将单个章节 JSON 转为 HTML（学术论文式层级）。"""
    title = section.get("title", "")
    content = section.get("content", "")
    data_type = section.get("type", "text")
    num_tag = f'<span class="sec-num">{section_num}.</span>' if section_num else ""

    is_conclusion = any(k in title for k in ("结论", "建议", "讨论"))
    is_limitations = any(k in title for k in ("限制", "局限", "方法学依据"))

    if is_limitations:
        html = f'<div class="limitations"><h2>{title}</h2>\n'
    else:
        html = f'<section class="report-section"><h2>{num_tag}{title}</h2>\n'

    if data_type == "table":
        html += f'<div class="table-wrapper">{content}</div>\n'
    elif data_type == "chart":
        html += f'<div class="chart"><img src="{content}" alt="{title}" style="max-width:100%"/></div>\n'
    elif data_type == "statistics":
        html += f'<div class="stats">{content}</div>\n'
    else:
        paragraphs = [p.strip() for p in str(content).split("\n\n") if p.strip()]
        if len(paragraphs) <= 1 and "\n" in str(content):
            paragraphs = [p.strip() for p in str(content).split("\n") if p.strip()]
        if not paragraphs:
            paragraphs = [str(content)]
        if is_conclusion and len(paragraphs) >= 2:
            html += '<div class="contributions"><ul>'
            html += "".join(f"<li>{p.replace(chr(10), ' ')}</li>" for p in paragraphs[:4])
            html += "</ul></div>"
            for p in paragraphs[4:]:
                html += f"<p>{p.replace(chr(10), '<br/>')}</p>"
        else:
            html += "".join(f"<p>{p.replace(chr(10), '<br/>')}</p>" for p in paragraphs)

    html += "</div>\n" if is_limitations else "</section>\n"
    return html


def build_report(
    user_id: int,
    session_id: str,
    template_id: int,
    analysis_results: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[dict], Optional[str]]:
    """
    综合各模块分析结果构建结构化报告 JSON。
    analysis_results: {N1_2: {...}, N2_1: {...}, N2_2: {...}, ...}
    """
    ok, err = _ensure_table()
    if not ok:
        return None, err

    template, effective_template_id, _ = resolve_report_template(template_id)

    report_structure = template.get("report_structure", [])
    if isinstance(report_structure, str):
        try:
            report_structure = json.loads(report_structure)
        except Exception:
            report_structure = []

    from backend.clinical_evidence import methodology

    report_methodology = methodology(
        "report",
        caveat="报告整合平台分析结果和参考文献依据；自动生成内容须由临床/研究负责人复核后使用。",
    )

    # 构建章节
    sections = []
    for i, section_title in enumerate(report_structure):
        section = {
            "id": i + 1,
            "title": section_title,
            "type": "text",
            "content": analysis_results.get(section_title, f"（{section_title} 数据待补充）") if analysis_results else f"（{section_title} 分析结果）",
        }
        sections.append(section)

    if not any(s.get("title") == "方法学依据与限制" for s in sections):
        evidence_lines = []
        for e in report_methodology.get("evidence", []):
            ident = e.get("doi") or e.get("pmid") or e.get("url") or ""
            evidence_lines.append(
                f"<p><strong>{e.get('id')}</strong>: {e.get('authors')} ({e.get('year')}). "
                f"{e.get('title')}. {e.get('venue')}. {ident}<br/>"
                f"支持: {e.get('supports')}<br/>限制: {e.get('caveat')}</p>"
            )
        sections.append({
            "id": len(sections) + 1,
            "title": "方法学依据与限制",
            "type": "text",
            "content": "\n".join(evidence_lines),
        })

    report_json = {
        "template_name": template.get("template_name", ""),
        "template_version": template.get("version", "1.0.0"),
        "disease_type": template.get("disease_type", ""),
        "sections": sections,
        "session_id": session_id,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "methodology": report_methodology,
    }

    report_name = f"{template.get('template_name', '报告')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # 保存到数据库
    insert_data = {
        "user_id": user_id,
        "session_id": session_id,
        "template_id": effective_template_id or None,
        "report_name": report_name,
        "report_json": json.dumps(report_json, ensure_ascii=False),
        "sections": json.dumps(sections, ensure_ascii=False),
    }
    _, report_id, err = mysql_handler.insert(TABLE_REPORTS, insert_data)
    if err:
        return None, f"保存报告失败: {err}"

    report_json["report_id"] = report_id
    report_json["report_name"] = report_name
    html, herr = render_html(report_json)
    if html:
        report_json["html_content"] = html
    return report_json, None


def render_html(report_json: dict) -> Tuple[Optional[str], Optional[str]]:
    """将结构化报告 JSON 渲染为 HTML 字符串。"""
    if not report_json.get("sections"):
        return None, "报告无章节内容"

    sections = report_json["sections"]
    body_parts = []
    section_num = 0
    limitations_html = ""
    for section in sections:
        title = section.get("title", "")
        if any(k in title for k in ("限制", "局限", "方法学依据")):
            limitations_html = _section_to_html(section, 0)
            continue
        section_num += 1
        body_parts.append(_section_to_html(section, section_num))

    abstract_text = report_json.get("executive_summary") or _build_abstract(sections, report_json)
    abstract_block = (
        f'<div class="abstract"><h2>执行摘要</h2>'
        + "".join(f"<p>{p}</p>" for p in abstract_text.split("\n") if p.strip())
        + "</div>"
    )

    html = HTML_TEMPLATE.format(
        title=report_json.get("template_name", "精神科分析报告"),
        generated_at=report_json.get("generated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        template_name=report_json.get("template_name", ""),
        version=report_json.get("template_version", "1.0.0"),
        abstract_block=abstract_block,
        body="\n".join(body_parts),
        limitations_block=limitations_html,
    )
    return html, None


def export_pdf(report_json: dict) -> Tuple[Optional[str], Optional[str]]:
    """
    导出 PDF 文件。
    尝试使用 weasyprint，若不可用则保存 HTML 文件并返回路径。
    """
    html, err = render_html(report_json)
    if err:
        return None, err

    os.makedirs(REPORT_EXPORT_DIR, exist_ok=True)
    report_name = report_json.get("report_name", "report")
    safe_name = "".join(c for c in report_name if c.isalnum() or c in "._- ")[:100]
    html_path = os.path.join(REPORT_EXPORT_DIR, f"{safe_name}.html")
    pdf_path = os.path.join(REPORT_EXPORT_DIR, f"{safe_name}.pdf")

    # 保存 HTML
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    # 尝试 PDF
    try:
        from weasyprint import HTML
        HTML(string=html).write_pdf(pdf_path)
        return pdf_path, None
    except ImportError:
        pass

    try:
        import pdfkit
        pdfkit.from_string(html, pdf_path)
        return pdf_path, None
    except (ImportError, OSError):
        pass

    # 回退：返回 HTML 路径
    return html_path, None


def export_report(report_id: int, format: str = "html", user_id: Optional[int] = None) -> Tuple[Optional[str], Optional[str]]:
    """导出指定格式的报告文件。"""
    if user_id:
        rows, err = mysql_handler.query(
            f"SELECT * FROM {TABLE_REPORTS} WHERE id = %s AND user_id = %s",
            (report_id, int(user_id)),
        )
    else:
        rows, err = mysql_handler.query(f"SELECT * FROM {TABLE_REPORTS} WHERE id = %s", (report_id,))
    if err:
        return None, f"查询报告失败: {err}"
    if not rows:
        return None, "报告不存在"

    report = rows[0]
    report_json = json.loads(report["report_json"]) if isinstance(report["report_json"], str) else report.get("report_json", {})

    if format == "html":
        html, herr = render_html(report_json)
        if herr:
            return None, herr
        os.makedirs(REPORT_EXPORT_DIR, exist_ok=True)
        safe_name = "".join(c for c in report.get("report_name", "report") if c.isalnum() or c in "._- ")[:100]
        html_path = os.path.join(REPORT_EXPORT_DIR, f"{safe_name}_{report_id}.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        return html_path, None

    elif format == "pdf":
        pdf_path, perr = export_pdf(report_json)
        return pdf_path, perr

    return None, f"不支持的格式: {format}"


def _section_keyword_fill(title: str, ctx: Dict[str, str]) -> Optional[str]:
    """按模板章节标题关键词匹配自动填充内容。"""
    t = title or ""
    rules = [
        (("研究对象", "基本信息", "纳排", "队列"), "patient_summary"),
        (("质控", "数据质量", "异常"), "qc_summary"),
        (("HAMA", "HAMD", "量表", "症状"), "scale_summary"),
        (("治疗反应", "随访", "趋势"), "treatment_summary"),
        (("共病", "抑郁-焦虑", "相关"), "comorbidity_summary"),
        (("用药", "结局", "关联"), "medication_summary"),
        (("风险", "预测"), "risk_summary"),
        (("结论", "建议"), "conclusion_summary"),
    ]
    for keywords, key in rules:
        if any(kw in t for kw in keywords):
            val = ctx.get(key)
            if val:
                return val
    return None


def build_clinical_report_sections(
    session_id: str,
    existing: Optional[Dict[str, Any]] = None,
    template_id: Optional[int] = None,
    cohort_patient_ids: Optional[List[str]] = None,
    owner_user_id: Optional[int] = None,
) -> Dict[str, str]:
    """从临床模块 DB + 前端缓存自动汇总报告章节（匹配模板 report_structure 标题）。"""
    from backend.clinical_data_service import resolve_cohort_ids

    cohort = resolve_cohort_ids(
        cohort_patient_ids, limit=30, owner_user_id=owner_user_id,
    )
    sections: Dict[str, str] = {}
    if existing:
        for k, v in existing.items():
            if v and str(v).strip() and "待补充" not in str(v):
                sections[str(k)] = str(v)[:4000]

    ctx: Dict[str, str] = {}

    try:
        from backend.patient_query_service import query_patients

        q, _ = query_patients(
            {
                "operator": "AND",
                "conditions": [{"field": "patient_id", "op": "LIKE", "value": "%"}],
            },
            page=1,
            page_size=500,
            owner_user_id=owner_user_id,
        )
        patients: List[dict] = []
        total = 0
        if cohort and q:
            q_ids = set(cohort)
            patients = [p for p in (q.get("patients") or []) if p.get("patient_id") in q_ids]
            total = len(patients)
        elif q:
            patients = q.get("patients") or []
            total = int(q.get("total") or len(patients))
        if patients:
            diags: Dict[str, int] = {}
            meds: Dict[str, int] = {}
            for p in patients:
                d = p.get("diagnosis") or "unknown"
                diags[d] = diags.get(d, 0) + 1
                m = p.get("medication") or "unknown"
                meds[m] = meds.get(m, 0) + 1
            ages = [int(p.get("age") or 0) for p in patients] or [0]
            ctx["patient_summary"] = (
                f"本研究纳入患者 {total} 例。"
                f"主要诊断分布为："
                + "、".join(f"{DIAG_ZH.get(k, k)} {v} 例" for k, v in sorted(diags.items(), key=lambda x: -x[1]))
                + "。"
                f"年龄范围 {min(ages)}–{max(ages)} 岁。"
            )
            top_meds = sorted(meds.items(), key=lambda x: -x[1])[:3]
            ctx["medication_summary"] = (
                "用药分布（前三）："
                + "；".join(f"{m} {n} 例" for m, n in top_meds)
            )
    except Exception:
        ctx["patient_summary"] = f"会话 {session_id}"

    try:
        from backend.reference_range_service import batch_evaluate

        batch, _ = batch_evaluate(
            cohort if cohort else [],
            ["HAMD_total", "HAMA_total", "PHQ9_total"],
            owner_user_id=owner_user_id,
        )
        if batch:
            rates = batch.get("abnormal_rates") or {}
            rate_txt = _format_rate_text(rates)
            note = batch.get("interpretation_note", "")
            ctx["qc_summary"] = f"参考区间质控：{rate_txt}。{note}"[:800]
            ctx["scale_summary"] = ctx["qc_summary"]
    except Exception:
        pass

    try:
        from backend.followup_service import generate_trend_data

        trend, _ = generate_trend_data(
            cohort[: min(10, len(cohort))] if cohort else [],
            ["HAMD_total", "HAMA_total"],
            per_patient=False,
            owner_user_id=owner_user_id,
        )
        if trend:
            tbl = trend.get("trend_table") or []
            lines = []
            for row in tbl[:4]:
                tp = row.get("time_point", "")
                hamd = row.get("HAMD_total") or row.get("HAMD")
                if hamd is not None:
                    lines.append(f"{tp} HAMD 均值约 {hamd}")
            n_fu = len(cohort[: min(10, len(cohort))]) if cohort else 0
            ctx["treatment_summary"] = (
                f"随访趋势（队列 {n_fu} 例）：" + ("；".join(lines) if lines else "已上传随访数据")
            )[:800]
            if not ctx.get("scale_summary"):
                ctx["scale_summary"] = ctx["treatment_summary"]
    except Exception:
        pass

    try:
        from backend.comorbidity_service import compute_comorbidity_matrix

        matrix, _ = compute_comorbidity_matrix(cohort if cohort else [], owner_user_id=owner_user_id)
        if matrix:
            diags = matrix.get("diagnoses") or []
            ctx["comorbidity_summary"] = (
                f"共病分析（n={matrix.get('total_patients', len(cohort))}）："
                f"检出 {len(diags)} 类诊断/症状信号（{', '.join(DIAG_ZH.get(d, d) for d in diags[:5])}等）。"
                f"结果基于量表阈值推断的探索性共现结构。"
            )[:800]
    except Exception:
        pass

    try:
        from backend.risk_prediction_service import list_risk_models

        models, _ = list_risk_models()
        if models:
            m0 = models[0]
            ctx["risk_summary"] = (
                f"风险模型 {m0.get('model_name')} ({m0.get('task_type')})；"
                f"指标: {str(m0.get('metrics', ''))[:300]}"
            )
            ctx["conclusion_summary"] = ctx["risk_summary"]
    except Exception:
        ctx["risk_summary"] = "待运行风险预测模块后更新"

    ctx["conclusion_summary"] = ctx.get("conclusion_summary") or ctx.get("risk_summary") or "请结合上述量表、共病与随访结果综合判读。"

    tpl, _, _ = resolve_report_template(template_id)
    rs = tpl.get("report_structure") or []
    if isinstance(rs, str):
        try:
            rs = json.loads(rs)
        except Exception:
            rs = []
    report_structure: List[str] = list(rs) if rs else []

    if not report_structure:
        report_structure = list(sections.keys()) or list(DEFAULT_REPORT_STRUCTURE)

    filled: Dict[str, str] = {}
    for title in report_structure:
        if title in sections:
            filled[title] = sections[title]
        else:
            auto = _section_keyword_fill(title, ctx)
            filled[title] = auto or sections.get(title) or f"（{title} 数据待补充）"

    for title, content in sections.items():
        if title not in filled and content:
            filled[title] = content

    return filled


def list_reports(user_id: int) -> Tuple[Optional[list], Optional[str]]:
    """列出历史报告。"""
    ok, err = _ensure_table()
    if not ok:
        return None, err
    rows, qerr = mysql_handler.query(
        f"SELECT id, user_id, session_id, template_id, report_name, created_at FROM {TABLE_REPORTS} WHERE user_id = %s ORDER BY id DESC",
        (user_id,)
    )
    if qerr:
        return None, f"查询报告列表失败: {qerr}"
    return list(rows) if rows else [], None
