"""功能集成：统计 / 管线 / ML / 特征 / DL 异步任务真正跑完。"""

from __future__ import annotations

import pytest

from psych_functional_helpers import (
    RISK_CSV,
    assert_success,
    extract_task_id,
    wait_task,
    wait_task_success,
    write_mini_csv,
)

pytestmark = pytest.mark.integration


def _ingest_ready(client, headers, tmp_path, name: str = "async-ds"):
    csv_path = RISK_CSV if RISK_CSV.is_file() else write_mini_csv(tmp_path / "m.csv")
    content = csv_path.read_bytes()
    ds = assert_success(
        client.post(
            "/psych/datasets",
            headers=headers,
            json={"name": name, "source_type": "table"},
        ),
        status_code=201,
    )
    r = client.post(
        f"/psych/datasets/{ds['id']}/ingest",
        headers=headers,
        files={"file": (csv_path.name, content, "text/csv")},
        data={"record_type": "row", "patient_key_col": "patient_id"},
    )
    assert r.status_code == 201, r.text
    task_id = extract_task_id(r.json())
    assert task_id, r.text
    wait_task_success(client, headers, task_id)
    return ds["id"], str(csv_path)


def test_stats_run_poll_results(psych_client, psych_users, tmp_path):
    user_a, _ = psych_users
    headers = user_a["headers"]
    ds_id, file_path = _ingest_ready(psych_client, headers, tmp_path, "stats-ds")

    submitted = assert_success(
        psych_client.post(
            "/psych/stats/run",
            headers=headers,
            json={
                "method_ids": ["describe_full", "pearson_correlation"],
                "dataset_id": ds_id,
            },
        ),
        status_code=201,
    )
    task_id = submitted["task_id"]
    wait_task_success(psych_client, headers, task_id)

    results = assert_success(
        psych_client.get(f"/psych/stats/results/{task_id}", headers=headers)
    )
    assert results.get("results") is not None
    assert results.get("task", {}).get("status") == "success" or results.get("results")


def test_stats_via_file_path(psych_client, psych_users, tmp_path):
    user_a, _ = psych_users
    headers = user_a["headers"]
    csv_path = write_mini_csv(tmp_path / "fp.csv")
    submitted = assert_success(
        psych_client.post(
            "/psych/stats/run",
            headers=headers,
            json={
                "method_ids": ["describe_full"],
                "file_path": str(csv_path),
            },
        ),
        status_code=201,
    )
    wait_task_success(psych_client, headers, submitted["task_id"])


def test_pipeline_create_and_run(psych_client, psych_users, tmp_path):
    user_a, _ = psych_users
    headers = user_a["headers"]
    ds_id, _ = _ingest_ready(psych_client, headers, tmp_path, "pipe-ds")

    methods = assert_success(
        psych_client.get("/psych/pipelines/methods", headers=headers)
    )
    assert methods

    pipe = assert_success(
        psych_client.post(
            "/psych/pipelines",
            headers=headers,
            json={
                "name": "fn-pipeline",
                "steps": [{"method_id": "describe_full"}],
            },
        ),
        status_code=201,
    )
    run = assert_success(
        psych_client.post(
            f"/psych/pipelines/{pipe['id']}/run",
            headers=headers,
            json={"dataset_id": ds_id},
        ),
        status_code=201,
    )
    wait_task_success(psych_client, headers, run["task_id"])


def test_ml_train_list_predict(psych_client, psych_users, tmp_path):
    user_a, _ = psych_users
    headers = user_a["headers"]
    ds_id, _ = _ingest_ready(psych_client, headers, tmp_path, "ml-ds")

    algos = assert_success(psych_client.get("/psych/ml/algorithms", headers=headers))
    assert len(algos.get("algorithms") or []) >= 1

    train = assert_success(
        psych_client.post(
            "/psych/ml/train",
            headers=headers,
            json={
                "algo_id": "logistic_regression",
                "dataset_id": ds_id,
                "model_name": "fn-lr",
                "mapping": {
                    "id_col": "patient_id",
                    "feature_columns": [
                        "age",
                        "HAMD_total",
                        "HAMA_total",
                        "PHQ9_total",
                    ],
                    "target_col": "relapse",
                },
                "sync_resource": False,
            },
        ),
        status_code=201,
    )
    wait_task_success(psych_client, headers, train["task_id"], timeout_s=180)

    models = assert_success(psych_client.get("/psych/ml/models", headers=headers))
    assert len(models.get("models") or []) >= 1
    model_id = models["models"][0]["id"]

    detail = assert_success(
        psych_client.get(f"/psych/ml/models/{model_id}", headers=headers)
    )
    assert detail.get("id") == model_id or detail.get("algo_id")

    pred = assert_success(
        psych_client.post(
            "/psych/ml/predict",
            headers=headers,
            json={
                "model_id": model_id,
                "file_path": str(RISK_CSV if RISK_CSV.is_file() else write_mini_csv(tmp_path / "pred.csv")),
            },
        )
    )
    assert pred is not None


def test_features_extract_list_detail(psych_client, psych_users, tmp_path):
    user_a, _ = psych_users
    headers = user_a["headers"]
    ds_id, _ = _ingest_ready(psych_client, headers, tmp_path, "feat-ds")

    submitted = assert_success(
        psych_client.post(
            "/psych/features/extract",
            headers=headers,
            json={
                "feature_type": "stat",
                "dataset_id": ds_id,
                "feature_set_name": "fn-stat",
            },
        ),
        status_code=201,
    )
    wait_task_success(psych_client, headers, submitted["task_id"])

    listed = assert_success(
        psych_client.get(f"/psych/features?dataset_id={ds_id}", headers=headers)
    )
    feats = listed.get("features") or []
    assert len(feats) >= 1
    feat_id = feats[0]["id"]
    detail = assert_success(
        psych_client.get(f"/psych/features/{feat_id}", headers=headers)
    )
    assert detail.get("id") == feat_id or detail.get("feature_set_name")

    dl = psych_client.get(f"/psych/features/{feat_id}/download", headers=headers)
    assert dl.status_code == 200
    assert b"feature" in dl.content or b"," in dl.content


def test_dl_train_infer(psych_client, psych_users, monkeypatch):
    """本环境 torch 导入链可能因 site-packages 权限失败，强制走 sklearn 回退仍验证真训练。"""
    monkeypatch.setattr("psych.dl.models._torch_available", lambda: False)
    user_a, _ = psych_users
    headers = user_a["headers"]

    models = assert_success(psych_client.get("/psych/dl/models", headers=headers))
    assert models.get("models")

    texts = [
        "焦虑 失眠 心悸",
        "情绪 低落 兴趣减退",
        "幻觉 妄想 思维紊乱",
        "睡眠 正常 食欲好",
        "紧张 担心 坐立不安",
        "愉快 精力充沛",
    ] * 2
    labels = [1, 1, 1, 0, 1, 0] * 2

    train = assert_success(
        psych_client.post(
            "/psych/dl/train",
            headers=headers,
            json={
                "model_id": "text_cnn",
                "texts": texts,
                "labels": labels,
                "epochs": 1,
            },
        ),
        status_code=201,
    )
    row = wait_task_success(psych_client, headers, train["task_id"], timeout_s=180)
    result = row.get("result_json") or {}
    meta_path = result.get("meta_path")
    assert meta_path, row

    inferred = assert_success(
        psych_client.post(
            "/psych/dl/infer",
            headers=headers,
            json={"meta_path": meta_path, "texts": ["焦虑 失眠", "正常 生活"]},
        )
    )
    assert "predictions" in inferred


def test_task_cancel_flow(psych_client, psych_users, monkeypatch):
    """提交 DL 任务后尝试取消；允许已跑完导致无法取消。"""
    monkeypatch.setattr("psych.dl.models._torch_available", lambda: False)
    user_a, _ = psych_users
    headers = user_a["headers"]
    texts = [f"样本文本 {i} 症状描述" for i in range(20)]
    labels = [i % 2 for i in range(20)]
    train = assert_success(
        psych_client.post(
            "/psych/dl/train",
            headers=headers,
            json={
                "model_id": "text_cnn",
                "texts": texts,
                "labels": labels,
                "epochs": 3,
            },
        ),
        status_code=201,
    )
    task_id = train["task_id"]
    cancel = psych_client.post(f"/psych/tasks/{task_id}/cancel", headers=headers)
    # 400 = 已完成无法取消；200 = 已请求取消
    assert cancel.status_code in (200, 400), cancel.text
    final = wait_task(
        psych_client,
        headers,
        task_id,
        accept=("success", "failed", "cancelled"),
        timeout_s=180,
    )
    assert final["status"] in ("success", "failed", "cancelled")
