# backend/psych_routes.py — 精神专科多维度分析 API

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from backend.jwt_auth import CurrentUser, get_current_user

logger = logging.getLogger(__name__)


def _ok(data, status: int = 200) -> JSONResponse:
    return JSONResponse(content={"status": "success", "data": data}, status_code=status)


def _err(err: Optional[str], code: int = 400) -> None:
    raise HTTPException(status_code=code, detail=err or "请求失败")


# ---------- request models ----------

class StatsRunBody(BaseModel):
    method_ids: List[str] = Field(..., min_length=1)
    dataset_id: Optional[int] = None
    file_path: Optional[str] = None
    mappings: Optional[Dict[str, Dict[str, Any]]] = None
    params_by_method: Optional[Dict[str, Dict[str, Any]]] = None


class MlTrainBody(BaseModel):
    algo_id: str
    dataset_id: Optional[int] = None
    file_path: Optional[str] = None
    mapping: Optional[Dict[str, Any]] = None
    params: Optional[Dict[str, Any]] = None
    model_name: Optional[str] = None
    sync_resource: bool = True


class MlPredictBody(BaseModel):
    model_id: int
    dataset_id: Optional[int] = None
    file_path: Optional[str] = None
    rows: Optional[List[Dict[str, Any]]] = None


class DatasetCreateBody(BaseModel):
    name: str
    source_type: str = "mixed"
    project_id: Optional[int] = None
    description: Optional[str] = None


class PipelineCreateBody(BaseModel):
    name: str
    steps: List[Dict[str, Any]]


class PipelineRunBody(BaseModel):
    dataset_id: Optional[int] = None
    file_path: Optional[str] = None


class ParamTemplateBody(BaseModel):
    module: str
    method_id: str
    name: str
    params: Dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False


class VariableBody(BaseModel):
    var_name: str
    display_name: Optional[str] = None
    dataset_id: Optional[int] = None
    category: Optional[str] = None
    dtype: Optional[str] = None
    dict_code: Optional[str] = None
    mapping: Optional[Dict[str, Any]] = None
    relations: Optional[Any] = None
    description: Optional[str] = None


class VariableBatchBody(BaseModel):
    items: List[Dict[str, Any]]


class CategoryBody(BaseModel):
    name: str
    parent_id: Optional[int] = None
    sort_order: int = 0


class AnalysisParamsBody(BaseModel):
    scope: str
    items: Dict[str, Any]


class ExportBody(BaseModel):
    kind: str
    format: str = "csv"
    task_id: Optional[str] = None
    dataset_id: Optional[int] = None
    data: Optional[Any] = None
    note: Optional[str] = None


class FeatureExtractBody(BaseModel):
    feature_type: str
    dataset_id: Optional[int] = None
    file_path: Optional[str] = None
    feature_set_name: Optional[str] = None
    mapping: Optional[Dict[str, Any]] = None
    params: Optional[Dict[str, Any]] = None


class ScaleParseBody(BaseModel):
    scale_code: str
    raw: Any
    patient_key: Optional[str] = None
    dataset_id: Optional[int] = None


class ScaleScoreBody(BaseModel):
    scale_code: str
    item_scores: Dict[str, Any]
    patient_key: str
    dataset_id: Optional[int] = None


class ScaleCompareBody(BaseModel):
    scale_code: str
    group_a: List[str]
    group_b: List[str]


class LlmExtractBody(BaseModel):
    text: str
    extract_type: str = "clinical_entities"
    dataset_id: Optional[int] = None
    record_id: Optional[int] = None


class LlmRelateBody(BaseModel):
    entities: Dict[str, Any]
    question: Optional[str] = None


class LlmQueryBody(BaseModel):
    query: str
    dataset_id: Optional[int] = None
    schema_hint: Optional[Any] = None


class LlmQaBody(BaseModel):
    question: str
    context: Optional[str] = None
    dataset_id: Optional[int] = None
    task_id: Optional[str] = None


class CapUpdateBody(BaseModel):
    enabled: Optional[bool] = None
    meta_json: Optional[Any] = None
    version: Optional[str] = None


class CapComposeBody(BaseModel):
    capability_ids: List[str]
    name: Optional[str] = None


class CapUpgradeBody(BaseModel):
    capability_id: str
    to_ver: str
    note: Optional[str] = None


class DlTrainBody(BaseModel):
    model_id: str
    texts: List[str]
    labels: List[int]
    epochs: int = 3


class DlInferBody(BaseModel):
    meta_path: str
    texts: List[str]


def register_psych_routes(app) -> None:
    # ---- health / tasks ----
    @app.get("/psych/health")
    async def psych_health(current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_capability_service import health_summary

        return _ok(health_summary())

    @app.get("/psych/tasks")
    async def psych_list_tasks(
        module: Optional[str] = None,
        limit: int = 50,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.psych_task_service import list_user_tasks

        rows, err = list_user_tasks(current_user.user_id, module=module, limit=limit)
        if err:
            _err(err, 500)
        return _ok({"tasks": rows})

    @app.get("/psych/tasks/{task_id}")
    async def psych_get_task(task_id: str, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_task_service import get_task

        row, err = get_task(task_id, current_user.user_id)
        if err:
            _err(err, 404 if "不存在" in err else 400)
        return _ok(row)

    @app.post("/psych/tasks/{task_id}/cancel")
    async def psych_cancel_task(task_id: str, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_task_service import cancel_task

        row, err = cancel_task(task_id, current_user.user_id)
        if err:
            _err(err, 400)
        return _ok(row)

    # ---- module 3 stats ----
    @app.get("/psych/stats/methods")
    async def psych_stats_methods(current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_stats_service import get_methods

        return _ok({"methods": get_methods()})

    @app.post("/psych/stats/run")
    async def psych_stats_run(body: StatsRunBody, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_stats_service import run_stats

        data, err = run_stats(
            current_user.user_id,
            body.method_ids,
            dataset_id=body.dataset_id,
            file_path=body.file_path,
            mappings=body.mappings,
            params_by_method=body.params_by_method,
        )
        if err:
            _err(err)
        return _ok(data, 201)

    @app.get("/psych/stats/results/{task_id}")
    async def psych_stats_results(task_id: str, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_stats_service import get_stats_results

        data, err = get_stats_results(task_id, current_user.user_id)
        if err:
            _err(err, 404 if "不存在" in err else 400)
        return _ok(data)

    # ---- module 7 ml ----
    @app.get("/psych/ml/algorithms")
    async def psych_ml_algorithms(current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_ml_service import get_algorithms

        return _ok({"algorithms": get_algorithms()})

    @app.post("/psych/ml/train")
    async def psych_ml_train(body: MlTrainBody, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_ml_service import train_model

        data, err = train_model(
            current_user.user_id,
            body.algo_id,
            dataset_id=body.dataset_id,
            file_path=body.file_path,
            mapping=body.mapping,
            params=body.params,
            model_name=body.model_name,
            sync_resource=body.sync_resource,
        )
        if err:
            _err(err)
        return _ok(data, 201)

    @app.post("/psych/ml/predict")
    async def psych_ml_predict(body: MlPredictBody, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_ml_service import predict

        data, err = predict(
            current_user.user_id,
            body.model_id,
            dataset_id=body.dataset_id,
            file_path=body.file_path,
            rows=body.rows,
        )
        if err:
            _err(err)
        return _ok(data)

    @app.get("/psych/ml/models")
    async def psych_ml_models(current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_ml_service import list_models

        rows, err = list_models(current_user.user_id)
        if err:
            _err(err, 500)
        return _ok({"models": rows})

    @app.get("/psych/ml/models/{model_id}")
    async def psych_ml_model_detail(model_id: int, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_ml_service import get_model

        row, err = get_model(model_id, current_user.user_id)
        if err:
            _err(err, 404 if "不存在" in err else 400)
        return _ok(row)

    # ---- module 1 data ----
    @app.post("/psych/datasets")
    async def psych_create_dataset(body: DatasetCreateBody, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_data_service import create_dataset

        data, err = create_dataset(
            current_user.user_id,
            body.name,
            source_type=body.source_type,
            project_id=body.project_id,
            description=body.description,
        )
        if err:
            _err(err)
        return _ok(data, 201)

    @app.get("/psych/datasets")
    async def psych_list_datasets(limit: int = 50, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_data_service import list_datasets

        rows, err = list_datasets(current_user.user_id, limit=limit)
        if err:
            _err(err, 500)
        return _ok({"datasets": rows})

    @app.get("/psych/datasets/{dataset_id}")
    async def psych_get_dataset(dataset_id: int, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_data_service import get_dataset

        row, err = get_dataset(dataset_id, current_user.user_id)
        if err:
            _err(err, 404 if "不存在" in err else 400)
        return _ok(row)

    @app.post("/psych/datasets/{dataset_id}/ingest")
    async def psych_ingest(
        dataset_id: int,
        file: UploadFile = File(...),
        record_type: str = Form("row"),
        patient_key_col: Optional[str] = Form(None),
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.psych_data_service import ingest_file

        content = await file.read()
        if len(content) > 200 * 1024 * 1024:
            _err("上传文件不能超过 200 MB", 413)
        data, err = ingest_file(
            current_user.user_id,
            dataset_id,
            file.filename or "data.csv",
            content,
            record_type=record_type,
            patient_key_col=patient_key_col,
        )
        if err:
            _err(err)
        return _ok(data, 201)

    @app.get("/psych/datasets/{dataset_id}/preview")
    async def psych_preview(dataset_id: int, n_rows: int = 20, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_data_service import preview_dataset

        data, err = preview_dataset(current_user.user_id, dataset_id, n_rows=n_rows)
        if err:
            _err(err)
        return _ok(data)

    @app.get("/psych/datasets/{dataset_id}/query")
    async def psych_query_records(
        dataset_id: int,
        patient_key: Optional[str] = None,
        record_type: Optional[str] = None,
        limit: int = 100,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.psych_data_service import query_records

        data, err = query_records(
            current_user.user_id, dataset_id, patient_key=patient_key, record_type=record_type, limit=limit
        )
        if err:
            _err(err)
        return _ok(data)

    # ---- module 2 pipeline ----
    @app.get("/psych/pipelines/methods")
    async def psych_pipeline_methods(current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_pipeline_service import list_pipeline_methods

        return _ok(list_pipeline_methods())

    @app.post("/psych/pipelines")
    async def psych_create_pipeline(body: PipelineCreateBody, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_pipeline_service import create_pipeline

        data, err = create_pipeline(current_user.user_id, body.name, body.steps)
        if err:
            _err(err)
        return _ok(data, 201)

    @app.get("/psych/pipelines")
    async def psych_list_pipelines(current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_pipeline_service import list_pipelines

        rows, err = list_pipelines(current_user.user_id)
        if err:
            _err(err, 500)
        return _ok({"pipelines": rows})

    @app.post("/psych/pipelines/{pipe_id}/run")
    async def psych_run_pipeline(
        pipe_id: int, body: PipelineRunBody, current_user: CurrentUser = Depends(get_current_user)
    ):
        from backend.psych_pipeline_service import run_pipeline

        data, err = run_pipeline(
            current_user.user_id, pipe_id, dataset_id=body.dataset_id, file_path=body.file_path
        )
        if err:
            _err(err)
        return _ok(data, 201)

    @app.post("/psych/param-templates")
    async def psych_save_template(body: ParamTemplateBody, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_pipeline_service import save_param_template

        data, err = save_param_template(
            current_user.user_id, body.module, body.method_id, body.name, body.params, body.is_default
        )
        if err:
            _err(err)
        return _ok(data, 201)

    @app.get("/psych/param-templates")
    async def psych_list_templates(module: Optional[str] = None, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_pipeline_service import list_param_templates

        rows, err = list_param_templates(current_user.user_id, module=module)
        if err:
            _err(err, 500)
        return _ok({"templates": rows})

    # ---- module 4 variables ----
    @app.post("/psych/variables")
    async def psych_create_var(body: VariableBody, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_variable_service import create_variable

        data, err = create_variable(current_user.user_id, body.model_dump())
        if err:
            _err(err)
        return _ok(data, 201)

    @app.get("/psych/variables")
    async def psych_list_vars(dataset_id: Optional[int] = None, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_variable_service import list_variables

        rows, err = list_variables(current_user.user_id, dataset_id=dataset_id)
        if err:
            _err(err, 500)
        return _ok({"variables": rows})

    @app.put("/psych/variables/{var_id}")
    async def psych_update_var(
        var_id: int, body: Dict[str, Any], current_user: CurrentUser = Depends(get_current_user)
    ):
        from backend.psych_variable_service import update_variable

        data, err = update_variable(var_id, current_user.user_id, body)
        if err:
            _err(err)
        return _ok(data)

    @app.delete("/psych/variables/{var_id}")
    async def psych_delete_var(var_id: int, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_variable_service import delete_variable

        data, err = delete_variable(var_id, current_user.user_id)
        if err:
            _err(err)
        return _ok(data)

    @app.post("/psych/variables/batch")
    async def psych_batch_vars(body: VariableBatchBody, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_variable_service import batch_edit

        data, err = batch_edit(current_user.user_id, body.items)
        if err:
            _err(err)
        return _ok(data)

    @app.post("/psych/variables/mapping")
    async def psych_var_mapping(body: Dict[str, Any], current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_variable_service import set_mapping

        var_id = body.get("var_id") or body.get("id")
        mapping = body.get("mapping") or body.get("mapping_json")
        if not var_id or mapping is None:
            _err("需提供 var_id 与 mapping")
        data, err = set_mapping(current_user.user_id, int(var_id), mapping)
        if err:
            _err(err)
        return _ok(data)

    @app.get("/psych/variables/dictionary/export")
    async def psych_dict_export(
        dataset_id: Optional[int] = None,
        format: str = "json",
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.psych_variable_service import export_dictionary

        data, err = export_dictionary(current_user.user_id, dataset_id=dataset_id, fmt=format)
        if err:
            _err(err)
        return _ok(data)

    @app.post("/psych/var-categories")
    async def psych_create_cat(body: CategoryBody, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_variable_service import create_category

        data, err = create_category(current_user.user_id, body.name, body.parent_id, body.sort_order)
        if err:
            _err(err)
        return _ok(data, 201)

    @app.get("/psych/var-categories")
    async def psych_list_cats(current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_variable_service import list_categories

        rows, err = list_categories(current_user.user_id)
        if err:
            _err(err, 500)
        return _ok({"categories": rows})

    @app.put("/psych/var-categories/{cat_id}")
    async def psych_update_cat(
        cat_id: int, body: Dict[str, Any], current_user: CurrentUser = Depends(get_current_user)
    ):
        from backend.psych_variable_service import update_category

        data, err = update_category(cat_id, current_user.user_id, body)
        if err:
            _err(err)
        return _ok(data)

    @app.delete("/psych/var-categories/{cat_id}")
    async def psych_delete_cat(cat_id: int, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_variable_service import delete_category

        data, err = delete_category(cat_id, current_user.user_id)
        if err:
            _err(err)
        return _ok(data)

    # ---- module 6 params / export ----
    @app.get("/psych/analysis-params")
    async def psych_get_params(scope: Optional[str] = None, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_config_export_service import list_params

        rows, err = list_params(current_user.user_id, scope=scope)
        if err:
            _err(err, 500)
        return _ok({"params": rows})

    @app.put("/psych/analysis-params")
    async def psych_put_params(body: AnalysisParamsBody, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_config_export_service import upsert_params

        data, err = upsert_params(current_user.user_id, body.scope, body.items)
        if err:
            _err(err)
        return _ok(data)

    @app.post("/psych/exports")
    async def psych_create_export(body: ExportBody, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_config_export_service import create_export

        data, err = create_export(
            current_user.user_id,
            body.kind,
            fmt=body.format,
            task_id=body.task_id,
            dataset_id=body.dataset_id,
            data=body.data,
            note=body.note,
        )
        if err:
            _err(err)
        return _ok(data, 201)

    @app.get("/psych/exports/{export_id}/download")
    async def psych_download_export(export_id: str, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_config_export_service import get_export_file

        row, err = get_export_file(export_id, current_user.user_id)
        if err:
            _err(err, 404 if "不存在" in err else 400)
        assert row is not None
        return FileResponse(row["file_path"], filename=f"{export_id}.{row.get('format') or 'dat'}")

    # ---- module 8 features ----
    @app.post("/psych/features/extract")
    async def psych_feat_extract(body: FeatureExtractBody, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_feature_service import extract_features

        data, err = extract_features(
            current_user.user_id,
            body.feature_type,
            dataset_id=body.dataset_id,
            file_path=body.file_path,
            feature_set_name=body.feature_set_name,
            mapping=body.mapping,
            params=body.params,
        )
        if err:
            _err(err)
        return _ok(data, 201)

    @app.get("/psych/features")
    async def psych_list_feats(dataset_id: Optional[int] = None, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_feature_service import list_features

        rows, err = list_features(current_user.user_id, dataset_id=dataset_id)
        if err:
            _err(err, 500)
        return _ok({"features": rows})

    @app.get("/psych/features/{feat_id}")
    async def psych_get_feat(feat_id: int, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_feature_service import get_feature

        row, err = get_feature(feat_id, current_user.user_id)
        if err:
            _err(err, 404 if "不存在" in err else 400)
        return _ok(row)

    # ---- module 11 scales ----
    @app.get("/psych/scales/forms")
    async def psych_scale_forms(current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_scale_service import list_forms

        rows, err = list_forms()
        if err:
            _err(err, 500)
        return _ok({"forms": rows})

    @app.post("/psych/scales/parse")
    async def psych_scale_parse(body: ScaleParseBody, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_scale_service import parse_raw

        data, err = parse_raw(
            current_user.user_id, body.scale_code, body.raw, body.patient_key, body.dataset_id
        )
        if err:
            _err(err)
        return _ok(data)

    @app.post("/psych/scales/score")
    async def psych_scale_score(body: ScaleScoreBody, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_scale_service import score

        data, err = score(
            current_user.user_id, body.scale_code, body.item_scores, body.patient_key, body.dataset_id
        )
        if err:
            _err(err)
        return _ok(data, 201)

    @app.get("/psych/scales/scores")
    async def psych_scale_scores(
        scale_code: Optional[str] = None,
        patient_key: Optional[str] = None,
        dataset_id: Optional[int] = None,
        limit: int = 200,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.psych_scale_service import list_scores

        rows, err = list_scores(
            current_user.user_id, scale_code=scale_code, patient_key=patient_key, dataset_id=dataset_id, limit=limit
        )
        if err:
            _err(err, 500)
        return _ok({"scores": rows})

    @app.get("/psych/scales/trend")
    async def psych_scale_trend(
        patient_key: str, scale_code: str, current_user: CurrentUser = Depends(get_current_user)
    ):
        from backend.psych_scale_service import trend

        data, err = trend(current_user.user_id, patient_key, scale_code)
        if err:
            _err(err)
        return _ok(data)

    @app.post("/psych/scales/compare")
    async def psych_scale_compare(body: ScaleCompareBody, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_scale_service import compare

        data, err = compare(current_user.user_id, body.scale_code, body.group_a, body.group_b)
        if err:
            _err(err)
        return _ok(data)

    @app.get("/psych/scales/export")
    async def psych_scale_export(
        scale_code: Optional[str] = None,
        dataset_id: Optional[int] = None,
        current_user: CurrentUser = Depends(get_current_user),
    ):
        from backend.psych_scale_service import export_scores

        data, err = export_scores(current_user.user_id, scale_code=scale_code, dataset_id=dataset_id)
        if err:
            _err(err)
        return _ok(data)

    # ---- module 5 llm ----
    @app.post("/psych/llm/extract")
    async def psych_llm_extract(body: LlmExtractBody, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_llm_service import extract

        data, err = extract(
            current_user.user_id, body.text, body.extract_type, body.dataset_id, body.record_id
        )
        if err:
            _err(err, 500)
        return _ok(data, 201)

    @app.post("/psych/llm/relate")
    async def psych_llm_relate(body: LlmRelateBody, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_llm_service import relate

        data, err = relate(current_user.user_id, body.entities, body.question)
        if err:
            _err(err, 500)
        return _ok(data)

    @app.post("/psych/llm/query")
    async def psych_llm_query(body: LlmQueryBody, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_llm_service import nl_query

        data, err = nl_query(current_user.user_id, body.query, body.dataset_id, body.schema_hint)
        if err:
            _err(err, 500)
        return _ok(data)

    @app.post("/psych/llm/qa")
    async def psych_llm_qa(body: LlmQaBody, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_llm_service import qa

        data, err = qa(
            current_user.user_id, body.question, body.context, body.dataset_id, body.task_id
        )
        if err:
            _err(err, 500)
        return _ok(data)

    # ---- module 10+12 capabilities ----
    @app.get("/psych/capabilities")
    async def psych_list_caps(kind: Optional[str] = None, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_capability_service import list_caps

        rows, err = list_caps(kind=kind)
        if err:
            _err(err, 500)
        return _ok({"capabilities": rows})

    @app.put("/psych/capabilities/{capability_id}")
    async def psych_update_cap(
        capability_id: str, body: CapUpdateBody, current_user: CurrentUser = Depends(get_current_user)
    ):
        from backend.psych_capability_service import update_cap

        data, err = update_cap(capability_id, body.enabled, body.meta_json, body.version)
        if err:
            _err(err)
        return _ok(data)

    @app.post("/psych/capabilities/compose")
    async def psych_compose(body: CapComposeBody, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_capability_service import compose

        data, err = compose(current_user.user_id, body.capability_ids, body.name)
        if err:
            _err(err)
        return _ok(data, 201)

    @app.post("/psych/capabilities/upgrade")
    async def psych_upgrade(body: CapUpgradeBody, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_capability_service import upgrade

        data, err = upgrade(body.capability_id, body.to_ver, body.note)
        if err:
            _err(err)
        return _ok(data)

    @app.get("/psych/capabilities/changelog")
    async def psych_changelog(
        capability_id: Optional[str] = None, current_user: CurrentUser = Depends(get_current_user)
    ):
        from backend.psych_capability_service import list_changelog

        rows, err = list_changelog(capability_id)
        if err:
            _err(err, 500)
        return _ok({"changelog": rows})

    # ---- module 9 dl ----
    @app.get("/psych/dl/models")
    async def psych_dl_models(current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_dl_service import get_models

        return _ok({"models": get_models()})

    @app.post("/psych/dl/train")
    async def psych_dl_train(body: DlTrainBody, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_dl_service import train

        data, err = train(current_user.user_id, body.model_id, body.texts, body.labels, body.epochs)
        if err:
            _err(err)
        return _ok(data, 201)

    @app.post("/psych/dl/infer")
    async def psych_dl_infer(body: DlInferBody, current_user: CurrentUser = Depends(get_current_user)):
        from backend.psych_dl_service import infer

        data, err = infer(current_user.user_id, body.meta_path, body.texts)
        if err:
            _err(err)
        return _ok(data)

    logger.info("psych routes registered")
