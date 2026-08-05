"""精神专科 /psych 模块单元测试（stub MySQL）。"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

if "utils.mysql_utils" not in sys.modules:
    _mysql_mod = MagicMock()
    _mysql_mod.mysql_handler = MagicMock()
    sys.modules["utils.mysql_utils"] = _mysql_mod


def test_stats_catalog_has_at_least_10():
    from psych.stats.catalog import list_stats_methods

    methods = list_stats_methods()
    assert len(methods) >= 10
    ids = {m["method_id"] for m in methods}
    assert "describe_full" in ids
    assert "pearson_correlation" in ids
    assert "welch_t_test" in ids


def test_ml_registry_has_at_least_10_and_extensible():
    from psych.ml.registry import list_algorithms, register_algo, get_algo

    algos = list_algorithms()
    assert len(algos) >= 10
    register_algo("demo_algo", solver_id="linear_regression", name_zh="演示", task_type="regression")
    assert get_algo("demo_algo")["solver_id"] == "linear_regression"


def test_scale_score_phq9():
    from psych.scales.forms import score_items

    items = {f"PHQ9_{i}": 1 for i in range(1, 10)}
    total, subs, cleaned = score_items("PHQ9", items)
    assert total == 9.0
    assert len(cleaned) == 9


def test_scale_parse_and_score_service():
    from backend.psych_scale_service import parse_raw, score

    with patch("backend.psych_scale_service.store") as st:
        st.get_scale_form.return_value = (
            {
                "scale_code": "PHQ9",
                "version": "1.0",
                "display_name": "PHQ-9",
                "items_json": [{"code": f"PHQ9_{i}"} for i in range(1, 10)],
                "scoring_json": {"type": "sum"},
            },
            None,
        )
        st.upsert_scale_form.return_value = (1, None)
        parsed, err = parse_raw(1, "PHQ9", [1] * 9, patient_key="P1")
        assert err is None
        assert "item_scores" in parsed

        st.insert_scale_score.return_value = (99, None)
        scored, err2 = score(1, "PHQ9", parsed["item_scores"], "P1")
        assert err2 is None
        assert scored["total"] == 9.0
        assert scored["id"] == 99


def test_dl_fallback_train_infer(tmp_path):
    from psych.dl import models as dl_models

    texts = ["焦虑 失眠", "情绪 低落", "正常 生活", "幻觉 妄想"] * 3
    labels = [0, 1, 0, 1] * 3
    with patch.object(dl_models, "_torch_available", return_value=False):
        out, err = dl_models.train_text_model(
            "text_cnn", texts, labels, str(tmp_path / "dl"), epochs=1
        )
        assert err is None
        assert out.get("meta_path")
        pred, perr = dl_models.infer_text_model(out["meta_path"], ["焦虑 失眠"])
        assert perr is None
        assert "predictions" in pred


def test_capability_bootstrap_list():
    from psych.capability.bootstrap import default_capabilities

    caps = default_capabilities()
    kinds = {c["kind"] for c in caps}
    assert "stats" in kinds
    assert "ml" in kinds
    assert "llm" in kinds
    assert "dl" in kinds


def test_psych_routes_register():
    """全量路径注册断言：与 PsychAPI.md / psych_routes 清单对齐。"""
    from pathlib import Path
    import sys

    _dir = Path(__file__).resolve().parent
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))

    from psych_test_helpers import EXPECTED_PSYCH_PATHS, make_psych_app, registered_paths

    app = make_psych_app()
    paths = registered_paths(app)
    missing = EXPECTED_PSYCH_PATHS - paths
    assert not missing, f"未注册的 psych 路径: {sorted(missing)}"
    assert "/psych/health" in paths
    assert "/psych/stats/run" in paths
    assert "/psych/ml/train" in paths
    assert "/psych/datasets" in paths
    assert "/psych/dl/train" in paths
    assert "/psych/capabilities" in paths
    assert "/psych/scales/score" in paths
    assert "/psych-app" in paths
