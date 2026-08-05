"""2.1.4 模块5：融合大语言模型（service mock，不调真实 LLM）。"""

from __future__ import annotations

from unittest.mock import patch

from psych_test_helpers import assert_success, assert_unauthorized, assert_validation_error


def test_llm_extract_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_llm_service.extract",
        return_value=(
            {"entities": {"symptoms": ["失眠"], "diagnosis": ["抑郁障碍"]}, "id": 1},
            None,
        ),
    ):
        data = assert_success(
            psych_client.post(
                "/psych/llm/extract",
                headers=auth_headers,
                json={
                    "text": "患者主诉失眠两周，情绪低落。",
                    "extract_type": "clinical_entities",
                },
            ),
            status_code=201,
        )
    assert "symptoms" in data["entities"]


def test_llm_extract_empty_text_still_validates(psych_client, auth_headers):
    """空字符串通过 Pydantic（必填 str），业务层可报错。"""
    with patch(
        "backend.psych_llm_service.extract",
        return_value=(None, "文本为空"),
    ):
        r = psych_client.post(
            "/psych/llm/extract",
            headers=auth_headers,
            json={"text": ""},
        )
    assert r.status_code == 500
    assert "文本为空" in r.json()["detail"]


def test_llm_extract_missing_text(psych_client, auth_headers):
    r = psych_client.post(
        "/psych/llm/extract",
        headers=auth_headers,
        json={"extract_type": "clinical_entities"},
    )
    assert_validation_error(r)


def test_llm_extract_requires_auth(psych_client):
    assert_unauthorized(
        psych_client.post("/psych/llm/extract", json={"text": "hello"})
    )


def test_llm_relate_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_llm_service.relate",
        return_value=({"relations": [{"from": "失眠", "to": "抑郁"}], "answer": "相关"}, None),
    ):
        data = assert_success(
            psych_client.post(
                "/psych/llm/relate",
                headers=auth_headers,
                json={
                    "entities": {"symptoms": ["失眠"]},
                    "question": "症状与诊断关系？",
                },
            )
        )
    assert len(data["relations"]) == 1


def test_llm_relate_missing_entities(psych_client, auth_headers):
    r = psych_client.post(
        "/psych/llm/relate",
        headers=auth_headers,
        json={"question": "x"},
    )
    assert_validation_error(r)


def test_llm_relate_error(psych_client, auth_headers):
    with patch(
        "backend.psych_llm_service.relate",
        return_value=(None, "LLM 不可用"),
    ):
        r = psych_client.post(
            "/psych/llm/relate",
            headers=auth_headers,
            json={"entities": {"a": 1}},
        )
    assert r.status_code == 500


def test_llm_relate_requires_auth(psych_client):
    assert_unauthorized(
        psych_client.post("/psych/llm/relate", json={"entities": {}})
    )


def test_llm_query_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_llm_service.nl_query",
        return_value=({"sql_hint": "SELECT *", "rows": [], "answer": "无匹配"}, None),
    ):
        data = assert_success(
            psych_client.post(
                "/psych/llm/query",
                headers=auth_headers,
                json={"query": "查出所有抑郁患者", "dataset_id": 1},
            )
        )
    assert "answer" in data


def test_llm_query_missing_query(psych_client, auth_headers):
    r = psych_client.post(
        "/psych/llm/query",
        headers=auth_headers,
        json={"dataset_id": 1},
    )
    assert_validation_error(r)


def test_llm_query_requires_auth(psych_client):
    assert_unauthorized(psych_client.post("/psych/llm/query", json={"query": "x"}))


def test_llm_qa_ok(psych_client, auth_headers):
    with patch(
        "backend.psych_llm_service.qa",
        return_value=({"answer": "建议继续随访", "citations": []}, None),
    ):
        data = assert_success(
            psych_client.post(
                "/psych/llm/qa",
                headers=auth_headers,
                json={
                    "question": "下一步治疗建议？",
                    "context": "HAMD=18",
                    "dataset_id": 1,
                },
            )
        )
    assert "随访" in data["answer"]


def test_llm_qa_missing_question(psych_client, auth_headers):
    r = psych_client.post(
        "/psych/llm/qa",
        headers=auth_headers,
        json={"context": "x"},
    )
    assert_validation_error(r)


def test_llm_qa_error(psych_client, auth_headers):
    with patch(
        "backend.psych_llm_service.qa",
        return_value=(None, "LLM timeout"),
    ):
        r = psych_client.post(
            "/psych/llm/qa",
            headers=auth_headers,
            json={"question": "hello"},
        )
    assert r.status_code == 500


def test_llm_qa_requires_auth(psych_client):
    assert_unauthorized(psych_client.post("/psych/llm/qa", json={"question": "x"}))
