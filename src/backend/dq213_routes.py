# backend/dq213_routes.py — 2.1.3 数据质量控制可视化 API

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, List, Optional

from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    try:
        import numpy as np

        if isinstance(value, np.generic):
            return _jsonable(value.item())
    except Exception:
        pass
    return value


def _ok(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(content={"status": "success", "data": _jsonable(data)}, status_code=status)


def _default_auth_dependency():
    try:
        from backend.jwt_auth import get_current_user
    except Exception as exc:  # pragma: no cover - 宿主集成路径
        raise RuntimeError("未提供鉴权依赖；独立测试请使用 standalone.app") from exc
    return get_current_user


def _current_user_id(current_user: Any) -> int:
    value = current_user.get("user_id") if isinstance(current_user, dict) else getattr(current_user, "user_id", None)
    try:
        user_id = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="用户身份无效") from exc
    if user_id <= 0:
        raise HTTPException(status_code=401, detail="用户身份无效")
    return user_id


def _split_values(value: Any) -> Optional[List[str]]:
    if value is None:
        return None
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ValueError("筛选项必须是数组或逗号分隔字符串")


def _translate_error(exc: Exception) -> None:
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, FileNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, RuntimeError):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail="2.1.3 模块处理失败") from exc


def register_dq213_routes(
    app,
    *,
    current_user_dependency: Optional[Callable[..., Any]] = None,
    db_handler=None,
    static_dir: Optional[str | Path] = None,
    demo_seed_handler: Optional[Callable[[int, str], Any]] = None,
) -> None:
    """注册 2.1.3 路由；数据库、鉴权与演示 seed 均可由隔离宿主注入。"""

    get_current_user = current_user_dependency or _default_auth_dependency()

    if static_dir is not None:
        root = Path(static_dir).resolve()
        if not root.is_dir() or not (root / "index.html").is_file():
            raise RuntimeError(f"2.1.3 静态目录无效: {root}")
        app.mount("/static/dq213", StaticFiles(directory=str(root)), name="dq213_static")

        @app.get("/dq213-app", include_in_schema=False)
        async def dq213_app_page():
            return FileResponse(root / "index.html", media_type="text/html")

    @app.get("/dq213/health")
    async def dq213_health(current_user: Any = Depends(get_current_user)):
        _current_user_id(current_user)
        return _ok(
            {
                "ok": True,
                "module": "2.1.3",
                "version": "safe-1",
                "capabilities": ["quality", "anonymize", "timeline", "multimodal"],
                "demo_seed_enabled": demo_seed_handler is not None,
            }
        )

    @app.post("/dq213/qc/assess")
    async def dq213_qc_assess(body: Optional[dict] = None, current_user: Any = Depends(get_current_user)):
        try:
            from backend.data_qc_service import run_quality_assessment

            return _ok(
                run_quality_assessment(
                    body or {},
                    owner_user_id=_current_user_id(current_user),
                    db_handler=db_handler,
                )
            )
        except Exception as exc:
            _translate_error(exc)

    @app.get("/dq213/qc/dimensions")
    async def dq213_qc_dimensions(current_user: Any = Depends(get_current_user)):
        _current_user_id(current_user)
        return _ok(
            {
                "dimensions": [
                    {"id": "completeness", "name": "完整性", "metrics": ["missing_rate"]},
                    {"id": "consistency", "name": "一致性", "metrics": ["duplicate_id", "date_order", "category"]},
                    {"id": "accuracy", "name": "准确性", "metrics": ["invalid_numeric", "out_of_range"]},
                    {"id": "outlier", "name": "异常值检测", "metrics": ["IQR"]},
                    {"id": "unstructured", "name": "非结构化文本质量", "metrics": ["empty", "short", "garbled", "duplicate"]},
                    {"id": "multimodal", "name": "多模态质量", "metrics": ["metadata", "mime", "duplicate", "coverage"]},
                    {"id": "multitype_coverage", "name": "临床多类型覆盖", "metrics": ["coverage_ratio"]},
                ]
            }
        )

    @app.get("/dq213/qc/reports/{report_id}")
    async def dq213_qc_report_download(report_id: str, current_user: Any = Depends(get_current_user)):
        try:
            from backend.data_qc_service import get_quality_report_path

            path = get_quality_report_path(report_id, _current_user_id(current_user))
            return FileResponse(path, media_type="application/json", filename=f"dq213-qc-{report_id}.json")
        except Exception as exc:
            _translate_error(exc)

    @app.post("/dq213/phi/detect")
    async def dq213_phi_detect(body: dict, current_user: Any = Depends(get_current_user)):
        _current_user_id(current_user)
        try:
            from backend.phi_anonymize_service import detect_phi_in_text

            text = str(body.get("text") or "")
            if not text.strip():
                raise ValueError("text 必填")
            return _ok(detect_phi_in_text(text, include_values=bool(body.get("include_values", False))))
        except Exception as exc:
            _translate_error(exc)

    @app.post("/dq213/phi/anonymize")
    async def dq213_phi_anonymize(body: dict, current_user: Any = Depends(get_current_user)):
        _current_user_id(current_user)
        try:
            from backend.phi_anonymize_service import anonymize_dataset, anonymize_text

            if "salt" in body or "secret" in body:
                raise ValueError("客户端不得传入脱敏密钥")
            if "rows" in body:
                return _ok(anonymize_dataset(body.get("rows") or []))
            text = str(body.get("text") or "")
            if not text.strip():
                raise ValueError("text 或 rows 必填")
            return _ok(anonymize_text(text, mode=str(body.get("mode") or "replace")))
        except Exception as exc:
            _translate_error(exc)

    @app.post("/dq213/phi/demo")
    async def dq213_phi_demo(current_user: Any = Depends(get_current_user)):
        _current_user_id(current_user)
        try:
            from backend.phi_anonymize_service import demo_anonymize

            return _ok(demo_anonymize())
        except Exception as exc:
            _translate_error(exc)

    @app.get("/dq213/timeline/patients")
    async def dq213_timeline_patients(limit: int = 50, current_user: Any = Depends(get_current_user)):
        try:
            from backend.patient_timeline_service import list_timeline_patients

            return _ok(
                list_timeline_patients(
                    limit=limit,
                    owner_user_id=_current_user_id(current_user),
                    db_handler=db_handler,
                )
            )
        except Exception as exc:
            _translate_error(exc)

    @app.get("/dq213/timeline/{patient_id}")
    async def dq213_timeline_get(
        patient_id: str,
        types: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        modalities: Optional[str] = None,
        keyword: Optional[str] = None,
        limit: int = 500,
        current_user: Any = Depends(get_current_user),
    ):
        try:
            from backend.patient_timeline_service import build_patient_timeline

            return _ok(
                build_patient_timeline(
                    patient_id,
                    event_types=_split_values(types),
                    start_date=start_date,
                    end_date=end_date,
                    modalities=_split_values(modalities),
                    keyword=keyword,
                    limit=limit,
                    owner_user_id=_current_user_id(current_user),
                    db_handler=db_handler,
                )
            )
        except Exception as exc:
            _translate_error(exc)

    @app.post("/dq213/timeline/query")
    async def dq213_timeline_query(body: dict, current_user: Any = Depends(get_current_user)):
        try:
            from backend.patient_timeline_service import build_patient_timeline

            patient_id = str(body.get("patient_id") or "").strip()
            if not patient_id:
                raise ValueError("patient_id 必填")
            return _ok(
                build_patient_timeline(
                    patient_id,
                    event_types=_split_values(body.get("event_types")),
                    start_date=body.get("start_date"),
                    end_date=body.get("end_date"),
                    modalities=_split_values(body.get("modalities")),
                    keyword=body.get("keyword"),
                    limit=body.get("limit", 500),
                    owner_user_id=_current_user_id(current_user),
                    db_handler=db_handler,
                )
            )
        except Exception as exc:
            _translate_error(exc)

    if demo_seed_handler is not None:

        @app.post("/dq213/demo/seed")
        async def dq213_demo_seed(body: Optional[dict] = None, current_user: Any = Depends(get_current_user)):
            try:
                patient_id = str((body or {}).get("patient_id") or "DQ213-DEMO-001").strip()
                if not patient_id or len(patient_id) > 64:
                    raise ValueError("patient_id 无效")
                return _ok(demo_seed_handler(_current_user_id(current_user), patient_id))
            except Exception as exc:
                _translate_error(exc)
