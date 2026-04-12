"""编排模块与 Coder 修正入口的轻量冒烟测试（需已安装项目依赖）。"""

import pytest


def test_correct_and_write_code_import():
    from coder.workspace_coder import correct_and_write_code  # noqa: F401


def test_pipeline_graph_compiles():
    try:
        from orchestrator.analysis_pipeline_graph import get_pipeline_graph
    except ModuleNotFoundError as e:
        pytest.skip(f"缺少依赖: {e}")
    g = get_pipeline_graph()
    assert g is not None
