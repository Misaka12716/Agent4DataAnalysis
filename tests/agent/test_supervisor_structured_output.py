"""Supervisor structured_output 方法选择：json_schema 白名单。"""

from types import SimpleNamespace

import pytest

from orchestrator.analysis_pipeline_graph import _should_try_json_schema_method


@pytest.mark.parametrize(
    "model_name, expected",
    [
        ("glm-4.7-flash:q4_K_M", False),
        ("qwen3-coder:30b", False),
        ("llama3.1:8b", False),
        ("gpt-4o", True),
        ("o3-mini", True),
        ("o1-preview", True),
        ("chatgpt-4o-latest", True),
        ("", False),
    ],
)
def test_should_try_json_schema_method(model_name: str, expected: bool):
    llm = SimpleNamespace(model_name=model_name, model=model_name)
    assert _should_try_json_schema_method(llm) is expected


def test_should_try_json_schema_method_empty_falls_back_to_model_attr():
    llm = SimpleNamespace(model_name="", model="gpt-4o-mini")
    assert _should_try_json_schema_method(llm) is True
