"""DICOM 影像 Handler（依赖 pydicom，缺失时降级）。"""

from __future__ import annotations

import os
from typing import Any, Dict


def digest_dicom_file(workspace_root: str, relative_path: str, **_kwargs: Any) -> Dict[str, Any]:
    fp = os.path.join(workspace_root, relative_path.replace("/", os.sep))
    entry: Dict[str, Any] = {
        "file_type": "imaging",
        "format": "dicom",
        "relative_path": relative_path,
        "handler_id": "imaging_dicom",
    }
    try:
        entry["file_size_bytes"] = os.path.getsize(fp)
    except OSError as e:
        entry["error"] = str(e)
        return entry

    try:
        import pydicom  # type: ignore
    except ImportError:
        entry["note"] = "未安装 pydicom，仅登记元数据；可 pip install pydicom 启用 DICOM 解析"
        return entry

    try:
        ds = pydicom.dcmread(fp, stop_before_pixels=True, force=True)

        def _get(name: str):
            val = getattr(ds, name, None)
            if val is None:
                return None
            return str(val)

        entry.update(
            {
                "patient_id": _get("PatientID"),
                "patient_name": _get("PatientName"),
                "study_date": _get("StudyDate"),
                "modality": _get("Modality"),
                "study_description": _get("StudyDescription"),
                "series_description": _get("SeriesDescription"),
                "rows": getattr(ds, "Rows", None),
                "columns": getattr(ds, "Columns", None),
            }
        )
    except Exception as e:
        entry["error"] = str(e)
    return entry


class DicomImagingHandler:
    handler_id = "imaging_dicom"

    def digest(self, workspace_root: str, relative_path: str, **kwargs: Any) -> Dict[str, Any]:
        return digest_dicom_file(workspace_root, relative_path, **kwargs)
