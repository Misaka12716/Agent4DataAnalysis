"""2.1.4 /psych 接口测试共享工厂、样例数据与断言。"""

from __future__ import annotations

from typing import Any, Dict, Optional, Set

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.jwt_auth import create_access_token
from backend.psych_routes import register_psych_routes

USER_ID = 10
USERNAME = "tester"
PHONE = "13800138000"

# 与 docs/PsychAPI.md + psych_routes.py 对齐的业务路径（含 Demo）
EXPECTED_PSYCH_PATHS: Set[str] = {
    "/psych/health",
    "/psych/tasks",
    "/psych/tasks/{task_id}",
    "/psych/tasks/{task_id}/cancel",
    "/psych/stats/methods",
    "/psych/stats/run",
    "/psych/stats/results/{task_id}",
    "/psych/ml/algorithms",
    "/psych/ml/train",
    "/psych/ml/predict",
    "/psych/ml/models",
    "/psych/ml/models/{model_id}",
    "/psych/datasets",
    "/psych/datasets/{dataset_id}",
    "/psych/datasets/{dataset_id}/ingest",
    "/psych/datasets/{dataset_id}/preview",
    "/psych/datasets/{dataset_id}/query",
    "/psych/pipelines/methods",
    "/psych/pipelines",
    "/psych/pipelines/{pipe_id}/run",
    "/psych/param-templates",
    "/psych/variables",
    "/psych/variables/{var_id}",
    "/psych/variables/batch",
    "/psych/variables/mapping",
    "/psych/variables/dictionary/export",
    "/psych/var-categories",
    "/psych/var-categories/{cat_id}",
    "/psych/analysis-params",
    "/psych/exports",
    "/psych/exports/{export_id}/download",
    "/psych/features/extract",
    "/psych/features",
    "/psych/features/{feat_id}",
    "/psych/scales/forms",
    "/psych/scales/parse",
    "/psych/scales/score",
    "/psych/scales/scores",
    "/psych/scales/trend",
    "/psych/scales/compare",
    "/psych/scales/export",
    "/psych/llm/extract",
    "/psych/llm/relate",
    "/psych/llm/query",
    "/psych/llm/qa",
    "/psych/capabilities",
    "/psych/capabilities/{capability_id}",
    "/psych/capabilities/compose",
    "/psych/dl/models",
    "/psych/dl/train",
    "/psych/dl/infer",
    "/psych-app",
}

SAMPLE_DATASET: Dict[str, Any] = {
    "id": 1,
    "name": "抑郁队列基线",
    "source_type": "mixed",
    "project_id": None,
    "description": "demo",
    "user_id": USER_ID,
}

SAMPLE_TASK: Dict[str, Any] = {
    "task_id": "task_demo_001",
    "status": "success",
    "module": "stats",
    "method_id": "describe_full",
    "result_json": {"ok_count": 1},
    "error_message": None,
    "user_id": USER_ID,
}

SAMPLE_PENDING_TASK: Dict[str, Any] = {
    "task_id": "task_pending_001",
    "status": "running",
    "module": "ml",
    "user_id": USER_ID,
}


def make_psych_app() -> FastAPI:
    app = FastAPI()
    register_psych_routes(app)
    return app


def make_auth_headers(
    user_id: int = USER_ID,
    username: str = USERNAME,
    phone: str = PHONE,
) -> Dict[str, str]:
    token, _ = create_access_token(user_id, username, phone)
    return {"Authorization": f"Bearer {token}"}


def make_client() -> TestClient:
    return TestClient(make_psych_app())


def rbac_user_row(
    user_id: int = USER_ID,
    username: str = USERNAME,
    phone: str = PHONE,
) -> Dict[str, Any]:
    return {
        "id": user_id,
        "username": username,
        "phone": phone,
        "platform_role": "user",
        "status": "active",
    }


def assert_success(resp, *, status_code: int = 200) -> Any:
    assert resp.status_code == status_code, resp.text
    body = resp.json()
    assert body.get("status") == "success"
    assert "data" in body
    return body["data"]


def assert_unauthorized(resp) -> None:
    assert resp.status_code == 401, resp.text


def assert_validation_error(resp) -> None:
    assert resp.status_code == 422, resp.text


def registered_paths(app: FastAPI) -> Set[Optional[str]]:
    return {getattr(r, "path", None) for r in app.routes}
