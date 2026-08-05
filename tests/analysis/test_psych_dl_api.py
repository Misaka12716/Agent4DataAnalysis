"""2.1.4 模块9：深度学习。"""

from __future__ import annotations

from unittest.mock import patch

from psych_test_helpers import assert_success, assert_unauthorized, assert_validation_error


def test_dl_models_ok(psych_client, auth_headers):
    models = [
        {"model_id": "text_cnn", "name_zh": "文本CNN"},
        {"model_id": "text_lstm", "name_zh": "文本LSTM"},
    ]
    with patch(
        "backend.psych_dl_service.get_models",
        return_value=models,
    ):
        data = assert_success(psych_client.get("/psych/dl/models", headers=auth_headers))
    assert len(data["models"]) >= 2


def test_dl_models_requires_auth(psych_client):
    assert_unauthorized(psych_client.get("/psych/dl/models"))


def test_dl_train_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_dl_service.train",
        return_value=({"task_id": "task_dl_1", "status": "pending"}, None),
    ) as mock_train:
        data = assert_success(
            psych_client.post(
                "/psych/dl/train",
                headers=auth_headers,
                json={
                    "model_id": "text_cnn",
                    "texts": ["焦虑 失眠", "情绪 低落", "正常"],
                    "labels": [1, 1, 0],
                    "epochs": 1,
                },
            ),
            status_code=201,
        )
    assert data["task_id"] == "task_dl_1"
    assert mock_train.call_args[0][1] == "text_cnn"


def test_dl_train_mismatched_labels_still_accepted_by_schema(psych_client, auth_headers):
    """Pydantic 不校验 texts/labels 等长；业务层报错。"""
    with patch(
        "backend.psych_dl_service.train",
        return_value=(None, "texts 与 labels 长度不一致"),
    ):
        r = psych_client.post(
            "/psych/dl/train",
            headers=auth_headers,
            json={
                "model_id": "text_cnn",
                "texts": ["a", "b"],
                "labels": [0],
            },
        )
    assert r.status_code == 400


def test_dl_train_missing_fields(psych_client, auth_headers):
    r = psych_client.post(
        "/psych/dl/train",
        headers=auth_headers,
        json={"model_id": "text_cnn"},
    )
    assert_validation_error(r)


def test_dl_train_requires_auth(psych_client):
    assert_unauthorized(
        psych_client.post(
            "/psych/dl/train",
            json={"model_id": "text_cnn", "texts": ["a"], "labels": [0]},
        )
    )


def test_dl_infer_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_dl_service.infer",
        return_value=({"predictions": [1, 0], "probs": [[0.1, 0.9], [0.8, 0.2]]}, None),
    ):
        data = assert_success(
            psych_client.post(
                "/psych/dl/infer",
                headers=auth_headers,
                json={
                    "meta_path": "/tmp/dl/meta.json",
                    "texts": ["焦虑 失眠", "正常 生活"],
                },
            )
        )
    assert data["predictions"] == [1, 0]


def test_dl_infer_missing_meta(psych_client, auth_headers):
    r = psych_client.post(
        "/psych/dl/infer",
        headers=auth_headers,
        json={"texts": ["x"]},
    )
    assert_validation_error(r)


def test_dl_infer_error(psych_client, auth_headers):
    with patch(
        "backend.psych_dl_service.infer",
        return_value=(None, "模型文件不存在"),
    ):
        r = psych_client.post(
            "/psych/dl/infer",
            headers=auth_headers,
            json={"meta_path": "/missing.json", "texts": ["x"]},
        )
    assert r.status_code == 400


def test_dl_infer_requires_auth(psych_client):
    assert_unauthorized(
        psych_client.post(
            "/psych/dl/infer",
            json={"meta_path": "/x", "texts": ["a"]},
        )
    )
