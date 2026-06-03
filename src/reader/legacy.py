from typing import Any, Dict

from reader.graph import get_reader_graph


def excel_schema_from_digest(digest: Dict[str, Any]) -> Dict[str, Any]:
    """从 workspace_digest 提取与旧 excel_schema 兼容的结构。"""
    files = digest.get("files") or {}
    excel_files: Dict[str, Any] = {}
    for rel, info in files.items():
        if info.get("file_type") != "table":
            continue
        entry: Dict[str, Any] = {
            "relative_path": rel,
            "columns": info.get("columns") or [],
        }
        shape = info.get("shape")
        if shape is not None:
            entry["shape"] = tuple(shape) if isinstance(shape, list) else shape
        if info.get("pandas_info") is not None:
            entry["pandas_info"] = info["pandas_info"]
        if info.get("read_hint"):
            entry["read_excel_hint"] = info["read_hint"]
        if info.get("error"):
            entry["error"] = info["error"]
        if info.get("sample_rows") is not None:
            entry["sample_rows"] = info["sample_rows"]
        excel_files[rel] = entry

    n = len(excel_files)
    summary = digest.get("summary") or (f"共 {n} 个表格文件" if n else "未发现表格文件")
    if n and "表格" not in summary and "table" not in summary.lower():
        summary = f"{summary}（其中 {n} 个为表格）"
    return {"files": excel_files, "summary": summary}


def read_workspace_excel_schema_and_sample(input_dir_abs_path: str) -> Dict[str, Any]:
    """兼容 shim：委托 Reader 智能体并返回 excel_schema 形状。"""
    final = get_reader_graph().invoke(
        {
            "workspace_root": input_dir_abs_path,
            "session_id": "",
            "lang": "zh",
            "file_inventory": [],
            "file_digests": {},
            "errors": [],
        }
    )
    digest = final.get("workspace_digest") or {"files": {}, "summary": ""}
    return excel_schema_from_digest(digest)
