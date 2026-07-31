# backend/clinical_routes.py — 2.1.6 临床支持 API（独立注册）

from __future__ import annotations

import math
import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from backend.jwt_auth import CurrentUser, get_current_user

try:
    import numpy as np
except ImportError:
    np = None


def _json_safe(value: Any) -> Any:
    if np is not None and isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return value


def _ok(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(content={"status": "success", "data": _json_safe(data)}, status_code=status)


def _normalize_risk_train_body(body: dict) -> dict:
    """兼容前端 {task,X,y,features,target} 与 service {task_type,training_data,label}。"""
    if body.get("training_data"):
        return body
    features = body.get("features") or []
    label = body.get("label") or body.get("target") or "relapse"
    rows_x = body.get("X")
    rows_y = body.get("y")
    if rows_x is not None and rows_y is not None:
        training_data = []
        for i, xrow in enumerate(rows_x):
            row = {features[j]: xrow[j] for j in range(min(len(features), len(xrow)))}
            row[label] = rows_y[i] if i < len(rows_y) else 0
            training_data.append(row)
        return {
            "task_type": body.get("task_type") or body.get("task") or "relapse",
            "model_type": body.get("model_type") or "RandomForest",
            "features": features,
            "label": label,
            "training_data": training_data,
            "model_params": body.get("model_params"),
        }
    return body


def _resolve_cohort(body: dict, owner_user_id: int, limit: int = 500) -> List[str]:
    from backend.clinical_data_service import resolve_cohort_ids

    raw = body.get("cohort_patient_ids") or body.get("cohort_ids") or body.get("patient_ids")
    return resolve_cohort_ids(raw, limit=limit, owner_user_id=owner_user_id)


def _resolve_risk_label(row: dict, task_type: str, label: str) -> int:
    if row.get(label) is not None:
        try:
            return int(row.get(label))
        except (TypeError, ValueError):
            pass
    if task_type == "relapse":
        return int(row.get("relapse") or 0)
    if task_type == "self_harm":
        return 1 if float(row.get("PHQ9_total") or 0) >= 20 else 0
    if task_type == "adverse_reaction":
        return 1 if float(row.get("HAMA_total") or 0) >= 29 else 0
    return 0


def _build_risk_train_body(body: dict, owner_user_id: int) -> dict:
    norm = _normalize_risk_train_body(body)
    if norm.get("training_data"):
        return norm
    cohort = _resolve_cohort(body, owner_user_id, limit=500)
    if not cohort:
        return norm
    from backend.clinical_data_service import fetch_patient_rows

    task_type = str(body.get("task_type") or body.get("task") or "relapse")
    features = body.get("features") or ["HAMD_total", "HAMA_total", "PHQ9_total", "age"]
    label = body.get("label") or body.get("target") or task_type if task_type in ("relapse", "self_harm", "adverse_reaction") else "relapse"
    rows = fetch_patient_rows(cohort, owner_user_id, limit=500)
    training_data = []
    for row in rows:
        item = {f: row.get(f) for f in features}
        item[label] = _resolve_risk_label(row, task_type, label)
        training_data.append(item)
    return {
        **norm,
        "task_type": task_type,
        "model_type": norm.get("model_type") or body.get("model_type") or "RandomForest",
        "features": features,
        "label": label,
        "training_data": training_data,
    }


def _resolve_patient_indicators(body: dict, owner_user_id: int) -> Tuple[Dict[str, float], Optional[str], Optional[int], Optional[str]]:
    indicators = body.get("indicators")
    gender = body.get("gender")
    age = body.get("age")
    diagnosis = body.get("diagnosis")
    if isinstance(indicators, dict) and indicators:
        return indicators, gender, age, diagnosis
    if not isinstance(indicators, list):
        return {}, gender, age, diagnosis
    pid = str(body.get("patient_id") or "").strip()
    if not pid:
        raise HTTPException(status_code=400, detail="patient_id 必填")
    from backend.clinical_data_service import get_patient_row

    row = get_patient_row(pid, owner_user_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"患者不存在: {pid}")
    out: Dict[str, float] = {}
    for ind in indicators:
        val = row.get(str(ind).strip())
        if val is not None:
            try:
                out[str(ind).strip()] = float(val)
            except (TypeError, ValueError):
                pass
    if not out:
        raise HTTPException(status_code=400, detail="该患者缺少所请求指标的有效数值")
    return out, gender or row.get("gender"), age if age is not None else row.get("age"), diagnosis or row.get("diagnosis")


def _resolve_time_range(body: dict) -> Optional[tuple]:
    from backend.followup_service import resolve_time_window

    preset = (body.get("time_window") or body.get("visit_type") or "").strip()
    custom = body.get("time_range")
    if isinstance(custom, (list, tuple)) and len(custom) == 2:
        return resolve_time_window(preset, custom[0], custom[1])
    return resolve_time_window(
        preset,
        body.get("start_date"),
        body.get("end_date"),
        anchor_date=body.get("anchor_date"),
    )


def _prepare_import_rows(body: dict) -> tuple[list, list[str]]:
    """Apply optional column_mapping to raw upload rows before DB import."""
    from backend.clinical_mapping_service import apply_import_mapping

    rows = body.get("rows") or body.get("patients") or body.get("records") or body.get("ranges") or []
    column_mapping = body.get("column_mapping") or body.get("mapping")
    if column_mapping:
        mapped, map_errors = apply_import_mapping(rows, column_mapping)
        return mapped, map_errors
    return list(rows), []


def register_clinical_routes(app) -> None:
    """在现有 FastAPI app 上追加 2.1.6 临床支持路由。"""
    try:
        from backend.clinical_owner import migrate_all_clinical_owner_columns

        migrate_all_clinical_owner_columns()
    except Exception:
        pass

    @app.get("/clinical/evidence")
    async def clinical_evidence(module: str = "", method: str = "", current_user: CurrentUser = Depends(get_current_user)):
        from backend.clinical_evidence import get_evidence

        return _ok(get_evidence(module=module.strip() or None, method=method.strip() or None))

    @app.get("/clinical/data/status")
    async def clinical_data_status(current_user: CurrentUser = Depends(get_current_user)):
        from backend.clinical_data_service import get_clinical_data_status

        resp, err = get_clinical_data_status(current_user.user_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    @app.post("/clinical/import/suggest-mapping")
    async def clinical_import_suggest_mapping(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.clinical_mapping_service import suggest_import_mapping

        rows = body.get("rows") or body.get("sample_rows") or []
        resp, err = suggest_import_mapping(
            str(body.get("dataset_type") or body.get("import_type") or ""),
            rows,
            user_override=body.get("column_mapping") or body.get("user_override"),
            use_llm=bool(body.get("use_llm", True)),
        )
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    # ── N2_1 患者检索与纳排 ─────────────────────────────────────
    @app.post("/patient/query")
    async def patient_query(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.patient_query_service import query_patients

        tree = body.get("condition_tree")
        if not tree:
            raise HTTPException(status_code=400, detail="condition_tree 必填")
        resp, err = query_patients(tree, int(body.get("page") or 1), int(body.get("page_size") or 20), current_user.user_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    @app.post("/patient/query/save")
    async def patient_query_save(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.patient_query_service import save_query

        qid, err = save_query(
            current_user.user_id,
            str(body.get("query_name") or "未命名查询"),
            body.get("condition_tree") or {},
        )
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok({"query_id": qid}, status=201)

    @app.get("/patient/query/list")
    async def patient_query_list(current_user: CurrentUser = Depends(get_current_user)):
        from backend.patient_query_service import list_saved_queries

        rows, err = list_saved_queries(current_user.user_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(rows)

    @app.get("/patient/query/{query_id}")
    async def patient_query_get(query_id: int, current_user: CurrentUser = Depends(get_current_user)):
        from backend.patient_query_service import get_saved_query

        row, err = get_saved_query(query_id, current_user.user_id)
        if err:
            raise HTTPException(status_code=404 if "不存在" in err else 400, detail=err)
        return _ok(row)

    @app.post("/patient/import")
    async def patient_import(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.patient_query_service import import_patients

        rows, map_errors = _prepare_import_rows(body)
        mode = str(body.get("mode") or "upsert")
        resp, err = import_patients(rows, mode=mode, owner_user_id=current_user.user_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        if map_errors:
            resp = {**(resp or {}), "mapping_errors": map_errors}
        return _ok(resp)

    @app.post("/patient/query/export")
    async def patient_query_export(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.patient_query_service import export_results

        fmt = (body.get("format") or "csv").lower()
        path, err = export_results(body.get("condition_tree") or {}, fmt, current_user.user_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        media = "text/csv" if fmt == "csv" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = "patients_export.csv" if fmt == "csv" else "patients_export.xlsx"
        return FileResponse(path, media_type=media, filename=filename)

    # ── N2_2 参考区间与异常评估 ─────────────────────────────────
    @app.get("/reference/list")
    async def reference_list(current_user: CurrentUser = Depends(get_current_user)):
        from backend.reference_range_service import manage_reference_range

        resp, err = manage_reference_range("list", {"owner_user_id": current_user.user_id})
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    @app.post("/reference/create")
    async def reference_create(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.reference_range_service import manage_reference_range

        body = {**body, "owner_user_id": current_user.user_id}
        resp, err = manage_reference_range("create", body)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp, status=201)

    @app.post("/reference/import")
    async def reference_import(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.reference_range_service import import_reference_ranges

        rows, map_errors = _prepare_import_rows(body)
        mode = str(body.get("mode") or "upsert")
        resp, err = import_reference_ranges(rows, mode=mode, owner_user_id=current_user.user_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        if map_errors:
            resp = {**(resp or {}), "mapping_errors": map_errors}
        return _ok(resp)

    @app.get("/reference/{ref_id}")
    async def reference_get(ref_id: int, current_user: CurrentUser = Depends(get_current_user)):
        from backend.reference_range_service import manage_reference_range

        resp, err = manage_reference_range("get", {"id": ref_id, "owner_user_id": current_user.user_id})
        if err:
            raise HTTPException(status_code=404 if "不存在" in err else 400, detail=err)
        return _ok(resp)

    @app.put("/reference/{ref_id}")
    async def reference_update(ref_id: int, body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.reference_range_service import manage_reference_range

        body = {**body, "id": ref_id, "owner_user_id": current_user.user_id}
        resp, err = manage_reference_range("update", body)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    @app.delete("/reference/{ref_id}")
    async def reference_delete(ref_id: int, current_user: CurrentUser = Depends(get_current_user)):
        from backend.reference_range_service import manage_reference_range

        resp, err = manage_reference_range("delete", {"id": ref_id, "owner_user_id": current_user.user_id})
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    @app.post("/reference/evaluate")
    async def reference_evaluate(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.reference_range_service import evaluate_abnormality

        indicators = body.get("indicators") or {}
        gender = body.get("gender")
        age = body.get("age")
        diagnosis = body.get("diagnosis")
        if not isinstance(indicators, dict):
            indicators, gender, age, diagnosis = _resolve_patient_indicators(body, current_user.user_id)
        resp, err = evaluate_abnormality(
            indicators,
            gender=gender,
            age=age,
            diagnosis=diagnosis,
            owner_user_id=current_user.user_id,
        )
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    @app.post("/reference/batch_evaluate")
    async def reference_batch_evaluate(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.reference_range_service import batch_evaluate

        resp, err = batch_evaluate(
            body.get("patient_ids") or [],
            body.get("indicators") or ["HAMD_total", "HAMA_total", "PHQ9_total"],
            owner_user_id=current_user.user_id,
        )
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    @app.post("/reference/compare")
    async def reference_compare(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.reference_range_service import compare_to_reference_population

        indicators = body.get("indicators")
        if not indicators:
            indicator = body.get("indicator") or "HAMD_total"
            indicators = [indicator] if isinstance(indicator, str) else indicator
        cohort = body.get("cohort_patient_ids") or _resolve_cohort(body, current_user.user_id)
        resp, err = compare_to_reference_population(
            cohort,
            body.get("reference_cohort_ids") or [],
            indicators,
            owner_user_id=current_user.user_id,
        )
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    # ── N2_3 随访管理 ───────────────────────────────────────────
    @app.post("/followup/add")
    async def followup_add(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.followup_service import add_followup_record

        pid = str(body.get("patient_id") or "").strip()
        data = {k: v for k, v in body.items() if k != "patient_id"}
        rid, err = add_followup_record(pid, data, owner_user_id=current_user.user_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok({"record_id": rid}, status=201)

    @app.post("/followup/import")
    async def followup_import(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.followup_service import import_followups

        rows, map_errors = _prepare_import_rows(body)
        mode = str(body.get("mode") or "upsert")
        resp, err = import_followups(rows, mode=mode, owner_user_id=current_user.user_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        if map_errors:
            resp = {**(resp or {}), "mapping_errors": map_errors}
        return _ok(resp)

    @app.post("/followup/query")
    async def followup_query(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.followup_service import query_followups

        tr = _resolve_time_range(body)
        pids = body.get("patient_ids")
        if not pids and body.get("patient_id"):
            pids = [str(body.get("patient_id")).strip()]
        resp, err = query_followups(pids, body.get("indicators"), tr, owner_user_id=current_user.user_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    @app.post("/followup/trend")
    async def followup_trend(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.followup_service import generate_trend_data

        pids = body.get("patient_ids") or []
        if not pids and body.get("patient_id"):
            pids = [str(body.get("patient_id")).strip()]
        indicators = body.get("indicators") or ["HAMD_total", "HAMA_total", "PHQ9_total"]
        tr = _resolve_time_range(body)
        per_patient = bool(body.get("per_patient", True))
        resp, err = generate_trend_data(
            pids or _resolve_cohort(body, current_user.user_id, limit=10)[:10],
            indicators,
            tr,
            per_patient=per_patient,
            owner_user_id=current_user.user_id,
        )
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    @app.post("/followup/compare")
    async def followup_compare(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.followup_service import compare_groups

        tr = _resolve_time_range(body)
        resp, err = compare_groups(
            body.get("group_a") or [],
            body.get("group_b") or [],
            body.get("indicators") or ["HAMD_total"],
            tr,
            owner_user_id=current_user.user_id,
        )
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    # ── N2_4 风险预测 ───────────────────────────────────────────
    @app.post("/risk/train")
    async def risk_train(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.risk_prediction_service import train_model

        norm = _build_risk_train_body(body, current_user.user_id)
        resp, err = train_model(
            norm.get("task_type") or "relapse",
            norm.get("training_data") or [],
            norm.get("features") or [],
            norm.get("label") or "relapse",
            model_type=norm.get("model_type") or "RandomForest",
            model_params=norm.get("model_params"),
            owner_user_id=current_user.user_id,
        )
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp, status=201)

    @app.post("/risk/predict")
    async def risk_predict(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.risk_prediction_service import predict_risk

        model_id = int(body.get("model_id") or 0)
        pdata = body.get("patient_data") or body.get("input_data") or {}
        pid = str(body.get("patient_id") or "").strip()
        if pid and not pdata:
            from backend.clinical_data_service import get_patient_row

            row = get_patient_row(pid, current_user.user_id)
            if not row:
                raise HTTPException(status_code=404, detail=f"患者不存在: {pid}")
            pdata = dict(row)
        resp, err = predict_risk(model_id, pdata, owner_user_id=current_user.user_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    @app.post("/risk/batch_predict")
    async def risk_batch_predict(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.risk_prediction_service import batch_predict

        cohort = _resolve_cohort(body, current_user.user_id)
        resp, err = batch_predict(int(body.get("model_id") or 0), cohort, owner_user_id=current_user.user_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    @app.get("/risk/models")
    async def risk_models(task_type: str = "", current_user: CurrentUser = Depends(get_current_user)):
        from backend.risk_prediction_service import list_risk_models

        tt = task_type.strip() or None
        resp, err = list_risk_models(tt, owner_user_id=current_user.user_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    @app.get("/risk/model/{model_id}/evaluate")
    async def risk_model_evaluate(model_id: int, current_user: CurrentUser = Depends(get_current_user)):
        from backend.risk_prediction_service import model_evaluation

        resp, err = model_evaluation(model_id, owner_user_id=current_user.user_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    @app.get("/risk/predictions")
    async def risk_predictions(
        model_id: int = 0,
        limit: int = 100,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.risk_prediction_service import list_predictions

        mid = int(model_id) if model_id else None
        resp, err = list_predictions(
            owner_user_id=current_user.user_id,
            model_id=mid,
            limit=min(int(limit or 100), 500),
        )
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    # ── N2_5 共病分析 ───────────────────────────────────────────
    @app.post("/comorbidity/matrix")
    async def comorbidity_matrix(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.comorbidity_service import compute_comorbidity_matrix

        cohort = _resolve_cohort(body, current_user.user_id)
        resp, err = compute_comorbidity_matrix(cohort, owner_user_id=current_user.user_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    @app.post("/comorbidity/spectrum")
    async def comorbidity_spectrum(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.comorbidity_service import analyze_spectrum_relationship, infer_primary_diagnosis

        cohort_ids = _resolve_cohort(body, current_user.user_id)
        primary = (body.get("primary_diagnosis") or "").strip()
        if not primary:
            primary = infer_primary_diagnosis(cohort_ids, current_user.user_id) or "depression"
        resp, err = analyze_spectrum_relationship(primary, cohort_ids, owner_user_id=current_user.user_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    @app.post("/comorbidity/cluster")
    async def comorbidity_cluster(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.comorbidity_service import cluster_comorbidity_patterns

        cohort = _resolve_cohort(body, current_user.user_id)
        resp, err = cluster_comorbidity_patterns(cohort, int(body.get("n_clusters") or 3), owner_user_id=current_user.user_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    @app.post("/comorbidity/heatmap")
    async def comorbidity_heatmap(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.comorbidity_service import compute_comorbidity_matrix, generate_heatmap_data

        cohort = _resolve_cohort(body, current_user.user_id)
        matrix = body.get("matrix")
        labels = body.get("labels")
        if not matrix:
            matrix_result, err = compute_comorbidity_matrix(cohort, owner_user_id=current_user.user_id)
            if err:
                raise HTTPException(status_code=400, detail=err)
            matrix = matrix_result.get("frequency_matrix") or {}
            labels = matrix_result.get("diagnoses") or []
        resp, err = generate_heatmap_data(matrix, labels or [])
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    @app.post("/comorbidity/network")
    async def comorbidity_network(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.comorbidity_service import build_network_from_matrix, compute_comorbidity_matrix, generate_network_data

        cohort = _resolve_cohort(body, current_user.user_id)
        edges = body.get("edges")
        if edges:
            resp, err = generate_network_data(edges, body.get("nodes"))
        else:
            matrix_result, err = compute_comorbidity_matrix(cohort, owner_user_id=current_user.user_id)
            if err:
                raise HTTPException(status_code=400, detail=err)
            resp, err = build_network_from_matrix(matrix_result)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    @app.post("/comorbidity/analyze")
    async def comorbidity_analyze(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.comorbidity_service import analyze_cohort_convenience

        cohort = _resolve_cohort(body, current_user.user_id)
        resp, err = analyze_cohort_convenience(
            body.get("analysis_type") or "共病矩阵",
            cohort,
            primary_diagnosis=body.get("primary_diagnosis"),
            owner_user_id=current_user.user_id,
        )
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    # ── N2_6 图文报告 ───────────────────────────────────────────
    @app.post("/report/build")
    async def report_build(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.report_export_service import build_report, build_clinical_report_sections

        session_id = str(body.get("session_id") or "").strip()
        raw_tid = body.get("template_id")
        template_id = int(raw_tid) if raw_tid not in (None, "") else None
        cohort_ids = body.get("cohort_patient_ids") or body.get("cohort_ids") or []
        sections = body.get("analysis_results")
        if body.get("auto_aggregate"):
            sections = build_clinical_report_sections(
                session_id,
                sections,
                template_id=template_id,
                cohort_patient_ids=cohort_ids,
                owner_user_id=current_user.user_id,
            )
        use_llm = body.get("use_llm", True)
        if sections and use_llm:
            from backend.clinical_report_llm import enrich_report_sections

            sections = enrich_report_sections(sections, use_llm=True)
        resp, err = build_report(current_user.user_id, session_id, template_id, sections)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp, status=201)

    @app.get("/report/list")
    async def report_list(current_user: CurrentUser = Depends(get_current_user)):
        from backend.report_export_service import list_reports

        resp, err = list_reports(current_user.user_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    @app.get("/report/{report_id}/export")
    async def report_export(report_id: int, format: str = "html", current_user: CurrentUser = Depends(get_current_user)):
        from backend.report_export_service import export_report

        path, err = export_report(report_id, format=format, user_id=current_user.user_id)
        if err:
            raise HTTPException(status_code=400, detail=err)
        if not path or not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="导出文件不存在")
        if path.endswith(".pdf"):
            return FileResponse(path, media_type="application/pdf", filename=os.path.basename(path))
        return FileResponse(path, media_type="text/html", filename=os.path.basename(path))

    # ── N2_7 相关性分析 ─────────────────────────────────────────
    @app.post("/correlation/compute")
    async def correlation_compute(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.correlation_service import compute_correlation

        resp, err = compute_correlation(
            body.get("data") or [],
            body.get("columns") or [],
            method=(body.get("method") or "pearson"),
        )
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    @app.post("/correlation/partial")
    async def correlation_partial(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.correlation_service import partial_correlation

        columns = body.get("columns")
        if not columns:
            columns = [v for v in [body.get("x"), body.get("y")] if v]
        resp, err = partial_correlation(
            body.get("data") or [],
            columns,
            body.get("control_vars") or body.get("controls") or [],
        )
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    @app.post("/correlation/heatmap")
    async def correlation_heatmap(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.correlation_service import compute_correlation, correlation_heatmap_data

        matrix = body.get("matrix")
        labels = body.get("labels")
        p_values = body.get("p_values")
        if not matrix:
            corr, err = compute_correlation(
                body.get("data") or [],
                body.get("columns") or [],
                method=(body.get("method") or "pearson"),
            )
            if err:
                raise HTTPException(status_code=400, detail=err)
            matrix = corr.get("matrix")
            labels = corr.get("labels")
            p_values = corr.get("p_values")
        resp, err = correlation_heatmap_data(
            matrix,
            labels,
            p_values,
        )
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)

    @app.post("/correlation/significant_pairs")
    async def correlation_significant(body: dict, current_user: CurrentUser = Depends(get_current_user)):
        from backend.correlation_service import compute_correlation, find_significant_pairs

        matrix = body.get("matrix")
        labels = body.get("labels")
        p_values = body.get("p_values")
        if not matrix:
            corr, err = compute_correlation(
                body.get("data") or [],
                body.get("columns") or [],
                method=(body.get("method") or "pearson"),
            )
            if err:
                raise HTTPException(status_code=400, detail=err)
            matrix = corr.get("matrix")
            labels = corr.get("labels")
            p_values = corr.get("p_values")
        resp, err = find_significant_pairs(
            matrix,
            labels,
            p_values,
            min_abs_r=float(body.get("min_abs_r") or 0.3),
            threshold=float(body.get("p_threshold") or body.get("threshold") or 0.05),
            correction=str(body.get("correction") or "fdr_bh"),
        )
        if err:
            raise HTTPException(status_code=400, detail=err)
        return _ok(resp)
