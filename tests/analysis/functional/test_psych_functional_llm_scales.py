"""功能集成：变量 / 量表 / 真 LLM / 导出。"""

from __future__ import annotations

import pytest

from psych_functional_helpers import assert_success, wait_task_success, write_mini_csv

pytestmark = pytest.mark.integration


def test_variables_and_categories_persist(psych_client, psych_users):
    user_a, _ = psych_users
    headers = user_a["headers"]

    cat = assert_success(
        psych_client.post(
            "/psych/var-categories",
            headers=headers,
            json={"name": "量表分", "sort_order": 1},
        ),
        status_code=201,
    )
    var = assert_success(
        psych_client.post(
            "/psych/variables",
            headers=headers,
            json={
                "var_name": "HAMD_total",
                "display_name": "HAMD总分",
                "dtype": "float",
                "category": "量表分",
            },
        ),
        status_code=201,
    )
    listed = assert_success(psych_client.get("/psych/variables", headers=headers))
    assert any(v.get("var_name") == "HAMD_total" for v in (listed.get("variables") or []))

    assert_success(
        psych_client.put(
            f"/psych/variables/{var['id']}",
            headers=headers,
            json={"display_name": "HAMD"},
        )
    )
    cats = assert_success(psych_client.get("/psych/var-categories", headers=headers))
    assert any(c.get("id") == cat["id"] for c in (cats.get("categories") or []))

    assert_success(
        psych_client.put(
            "/psych/analysis-params",
            headers=headers,
            json={"scope": "stats", "items": {"alpha": 0.05}},
        )
    )
    params = assert_success(
        psych_client.get("/psych/analysis-params?scope=stats", headers=headers)
    )
    assert params.get("params")


def test_scales_parse_score_trend_compare(psych_client, psych_users):
    user_a, _ = psych_users
    headers = user_a["headers"]

    forms = assert_success(psych_client.get("/psych/scales/forms", headers=headers))
    assert isinstance(forms.get("forms"), list)

    raw_items = list(range(1, 10))  # PHQ9 9 项
    parsed = assert_success(
        psych_client.post(
            "/psych/scales/parse",
            headers=headers,
            json={"scale_code": "PHQ9", "raw": raw_items, "patient_key": "P_FN_1"},
        )
    )
    item_scores = parsed.get("item_scores") or {
        f"PHQ9_{i}": 1 for i in range(1, 10)
    }

    scored = assert_success(
        psych_client.post(
            "/psych/scales/score",
            headers=headers,
            json={
                "scale_code": "PHQ9",
                "item_scores": item_scores,
                "patient_key": "P_FN_1",
            },
        ),
        status_code=201,
    )
    assert scored.get("total") is not None

    # 第二名患者便于 compare
    assert_success(
        psych_client.post(
            "/psych/scales/score",
            headers=headers,
            json={
                "scale_code": "PHQ9",
                "item_scores": {f"PHQ9_{i}": 2 for i in range(1, 10)},
                "patient_key": "P_FN_2",
            },
        ),
        status_code=201,
    )
    assert_success(
        psych_client.post(
            "/psych/scales/score",
            headers=headers,
            json={
                "scale_code": "PHQ9",
                "item_scores": {f"PHQ9_{i}": 0 for i in range(1, 10)},
                "patient_key": "P_FN_3",
            },
        ),
        status_code=201,
    )

    scores = assert_success(
        psych_client.get(
            "/psych/scales/scores?scale_code=PHQ9&patient_key=P_FN_1",
            headers=headers,
        )
    )
    assert len(scores.get("scores") or []) >= 1

    trend = assert_success(
        psych_client.get(
            "/psych/scales/trend?patient_key=P_FN_1&scale_code=PHQ9",
            headers=headers,
        )
    )
    assert trend is not None

    cmp = assert_success(
        psych_client.post(
            "/psych/scales/compare",
            headers=headers,
            json={
                "scale_code": "PHQ9",
                "group_a": ["P_FN_1", "P_FN_2"],
                "group_b": ["P_FN_3"],
            },
        )
    )
    assert cmp is not None

    exported = assert_success(
        psych_client.get("/psych/scales/export?scale_code=PHQ9", headers=headers)
    )
    assert exported is not None


def test_llm_extract_relate_query_qa(psych_client, psych_users, tmp_path):
    user_a, _ = psych_users
    headers = user_a["headers"]
    clinical = (
        "患者，女，32岁，主诉情绪低落、失眠两周，兴趣减退，偶有消极意念。"
        "既往诊断抑郁障碍，目前口服舍曲林 50mg qd。HAMD=22，PHQ-9=15。"
    )

    extracted = assert_success(
        psych_client.post(
            "/psych/llm/extract",
            headers=headers,
            json={"text": clinical, "extract_type": "clinical_entities"},
        ),
        status_code=201,
    )
    # 结构化结果：实体对象或 raw；至少应有可解析内容
    assert extracted
    entities = extracted.get("entities") or extracted.get("parsed") or extracted
    assert isinstance(entities, (dict, list, str))

    related = assert_success(
        psych_client.post(
            "/psych/llm/relate",
            headers=headers,
            json={
                "entities": entities if isinstance(entities, dict) else {"text": clinical},
                "question": "症状与诊断可能有何关联？",
            },
        )
    )
    assert related

    # query 可无 dataset
    queried = assert_success(
        psych_client.post(
            "/psych/llm/query",
            headers=headers,
            json={"query": "筛选抑郁障碍且 HAMD 大于 20 的患者"},
        )
    )
    assert queried

    qa = assert_success(
        psych_client.post(
            "/psych/llm/qa",
            headers=headers,
            json={
                "question": "该患者下一步随访重点是什么？",
                "context": clinical,
            },
        )
    )
    assert qa.get("answer") or qa.get("raw") or qa


def test_exports_and_download(psych_client, psych_users, tmp_path):
    user_a, _ = psych_users
    headers = user_a["headers"]
    csv_path = write_mini_csv(tmp_path / "exp.csv")

    # 先跑一个 stats 任务作为可导出对象
    run = assert_success(
        psych_client.post(
            "/psych/stats/run",
            headers=headers,
            json={"method_ids": ["describe_full"], "file_path": str(csv_path)},
        ),
        status_code=201,
    )
    wait_task_success(psych_client, headers, run["task_id"])

    created = assert_success(
        psych_client.post(
            "/psych/exports",
            headers=headers,
            json={
                "kind": "stats",
                "format": "json",
                "task_id": run["task_id"],
                "note": "fn-export",
                "data": {"ok": True, "rows": [{"a": 1}]},
            },
        ),
        status_code=201,
    )
    export_id = created.get("export_id") or created.get("id")
    assert export_id

    dl = psych_client.get(
        f"/psych/exports/{export_id}/download",
        headers=headers,
    )
    assert dl.status_code == 200, dl.text
    assert len(dl.content) > 0
