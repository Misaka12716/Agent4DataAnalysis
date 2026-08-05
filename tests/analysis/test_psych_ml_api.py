"""2.1.4 模块7：机器学习 algorithms / train / predict / models。"""

from __future__ import annotations

from unittest.mock import patch

from psych_test_helpers import assert_success, assert_unauthorized, assert_validation_error

SAMPLE_MODEL = {
    "id": 7,
    "algo_id": "logistic_regression",
    "model_name": "风险模型",
    "status": "ready",
}


def test_ml_algorithms_ok(psych_client, auth_headers):
    algos = [
        {"algo_id": "logistic_regression", "name_zh": "逻辑回归"},
        {"algo_id": "random_forest", "name_zh": "随机森林"},
    ]
    with patch(
        "backend.psych_ml_service.get_algorithms",
        return_value=algos,
    ):
        data = assert_success(
            psych_client.get("/psych/ml/algorithms", headers=auth_headers)
        )
    assert len(data["algorithms"]) >= 2


def test_ml_algorithms_requires_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/ml/algorithms"))


def test_ml_train_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_ml_service.train_model",
        return_value=({"task_id": "task_ml_1", "status": "pending"}, None),
    ) as mock_train:
        data = assert_success(
            psych_client.post(
                "/psych/ml/train",
                headers=auth_headers,
                json={
                    "algo_id": "logistic_regression",
                    "dataset_id": 1,
                    "model_name": "风险模型",
                },
            ),
            status_code=201,
        )
    assert data["task_id"] == "task_ml_1"
    assert mock_train.call_args[0][1] == "logistic_regression"


def test_ml_train_missing_algo(psych_client, auth_headers):
    r = psych_client.post(
        "/psych/ml/train",
        headers=auth_headers,
        json={"dataset_id": 1},
    )
    assert_validation_error(r)


def test_ml_train_error(psych_client, auth_headers):
    with patch(
        "backend.psych_ml_service.train_model",
        return_value=(None, "算法不存在"),
    ):
        r = psych_client.post(
            "/psych/ml/train",
            headers=auth_headers,
            json={"algo_id": "nope", "dataset_id": 1},
        )
    assert r.status_code == 400


def test_ml_train_requires_auth(psych_client):
    assert_unauthorized(
        psych_client.post("/psych/ml/train", json={"algo_id": "logistic_regression"})
    )


def test_ml_predict_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_ml_service.predict",
        return_value=({"predictions": [0, 1], "model_id": 7}, None),
    ):
        data = assert_success(
            psych_client.post(
                "/psych/ml/predict",
                headers=auth_headers,
                json={
                    "model_id": 7,
                    "rows": [{"age": 30}, {"age": 45}],
                },
            )
        )
    assert data["predictions"] == [0, 1]


def test_ml_predict_missing_model_id(psych_client, auth_headers):
    r = psych_client.post(
        "/psych/ml/predict",
        headers=auth_headers,
        json={"rows": []},
    )
    assert_validation_error(r)


def test_ml_predict_error(psych_client, auth_headers):
    with patch(
        "backend.psych_ml_service.predict",
        return_value=(None, "模型不存在"),
    ):
        r = psych_client.post(
            "/psych/ml/predict",
            headers=auth_headers,
            json={"model_id": 99, "rows": [{"x": 1}]},
        )
    assert r.status_code == 400


def test_ml_predict_requires_auth(psych_client):
    assert_unauthorized(psych_client.post("/psych/ml/predict", json={"model_id": 1}))


def test_ml_list_models_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_ml_service.list_models",
        return_value=([SAMPLE_MODEL], None),
    ):
        data = assert_success(psych_client.get("/psych/ml/models", headers=auth_headers))
    assert data["models"][0]["id"] == 7


def test_ml_list_models_db_error(psych_client, auth_headers):
    with patch(
        "backend.psych_ml_service.list_models",
        return_value=(None, "db"),
    ):
        r = psych_client.get("/psych/ml/models", headers=auth_headers)
    assert r.status_code == 500


def test_ml_get_model_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_ml_service.get_model",
        return_value=(SAMPLE_MODEL, None),
    ):
        data = assert_success(
            psych_client.get("/psych/ml/models/7", headers=auth_headers)
        )
    assert data["algo_id"] == "logistic_regression"


def test_ml_get_model_not_found(psych_client, auth_headers):
    with patch(
        "backend.psych_ml_service.get_model",
        return_value=(None, "模型不存在"),
    ):
        r = psych_client.get("/psych/ml/models/99", headers=auth_headers)
    assert r.status_code == 404


def test_ml_models_require_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/ml/models"))
    assert_unauthorized(psych_client.get("/psych/ml/models/1"))
