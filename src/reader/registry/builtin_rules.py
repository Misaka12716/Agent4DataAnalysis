"""内置格式规则（2.1.2）。"""

from __future__ import annotations

from typing import List

from reader.registry.models import FormatRule


def builtin_format_rules() -> List[FormatRule]:
    return [
        FormatRule(
            format_id="builtin.table.xlsx",
            extensions=[".xlsx", ".xls"],
            category="table",
            handler_id="table",
            mime_types=[
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/vnd.ms-excel",
            ],
            priority=100,
            builtin=True,
            description="Excel 表格",
        ),
        FormatRule(
            format_id="builtin.table.csv",
            extensions=[".csv", ".tsv"],
            category="table",
            handler_id="table",
            mime_types=["text/csv", "text/tab-separated-values"],
            priority=100,
            builtin=True,
            description="CSV/TSV 表格",
        ),
        FormatRule(
            format_id="builtin.image.raster",
            extensions=[".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"],
            category="image",
            handler_id="image",
            mime_types=["image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"],
            priority=100,
            builtin=True,
            description="常见光栅影像",
        ),
        FormatRule(
            format_id="builtin.text.plain",
            extensions=[".txt", ".md", ".log"],
            category="text",
            handler_id="text",
            mime_types=["text/plain"],
            priority=100,
            builtin=True,
            description="纯文本 / Markdown",
        ),
        FormatRule(
            format_id="builtin.text.structured",
            extensions=[".json", ".yaml", ".yml", ".xml", ".html", ".htm"],
            category="text",
            handler_id="text",
            mime_types=[
                "application/json",
                "application/x-yaml",
                "application/xml",
                "text/html",
            ],
            priority=100,
            builtin=True,
            description="结构化文本",
        ),
        FormatRule(
            format_id="builtin.document.pdf",
            extensions=[".pdf"],
            category="document",
            handler_id="document_pdf",
            mime_types=["application/pdf"],
            priority=100,
            builtin=True,
            magic_prefixes=["25504446"],  # %PDF
            description="PDF 文档",
        ),
        FormatRule(
            format_id="builtin.document.docx",
            extensions=[".docx"],
            category="document",
            handler_id="document_docx",
            mime_types=[
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ],
            priority=100,
            builtin=True,
            description="Word 文档 (DOCX)",
        ),
        FormatRule(
            format_id="builtin.imaging.dicom",
            extensions=[".dcm", ".dicom"],
            category="imaging",
            handler_id="imaging_dicom",
            mime_types=["application/dicom"],
            priority=100,
            builtin=True,
            description="DICOM 医学影像",
        ),
    ]
