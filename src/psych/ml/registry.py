# psych/ml/registry.py — 机器学习算法目录与扩展入口

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

# algo_id → registry solver_id + 元数据
_ALGO_REGISTRY: Dict[str, Dict[str, Any]] = {
    "logistic_regression": {
        "solver_id": "logistic_regression",
        "name_zh": "逻辑回归",
        "task_type": "classification",
        "params_schema": {
            "feature_columns": {"type": "array", "role": True},
            "target_col": {"type": "string", "role": True},
            "id_col": {"type": "string", "role": True},
        },
    },
    "random_forest": {
        "solver_id": "random_forest",
        "name_zh": "随机森林",
        "task_type": "classification",
        "params_schema": {},
    },
    "xgboost": {
        "solver_id": "xgboost",
        "name_zh": "XGBoost",
        "task_type": "classification",
        "params_schema": {},
    },
    "lightgbm": {
        "solver_id": "lightgbm",
        "name_zh": "LightGBM",
        "task_type": "classification",
        "params_schema": {},
    },
    "svm_rbf": {
        "solver_id": "svm_rbf",
        "name_zh": "支持向量机(RBF)",
        "task_type": "classification",
        "params_schema": {},
    },
    "knn_k_selection": {
        "solver_id": "knn_k_selection",
        "name_zh": "K近邻",
        "task_type": "classification",
        "params_schema": {},
    },
    "cox_regression": {
        "solver_id": "cox_regression",
        "name_zh": "Cox回归",
        "task_type": "survival",
        "params_schema": {},
    },
    "hist_gradient_boosting": {
        "solver_id": "hist_gradient_boosting",
        "name_zh": "直方图梯度提升",
        "task_type": "classification",
        "params_schema": {},
    },
    "linear_regression": {
        "solver_id": "linear_regression",
        "name_zh": "线性回归",
        "task_type": "regression",
        "params_schema": {},
    },
    "lasso_cv_select": {
        "solver_id": "lasso_cv_select",
        "name_zh": "Lasso CV特征选择",
        "task_type": "feature_selection",
        "params_schema": {},
    },
}

# 扩展钩子：自定义训练函数 (algo_id -> callable)
_CUSTOM_TRAINERS: Dict[str, Callable[..., Any]] = {}


def register_algo(
    algo_id: str,
    *,
    solver_id: Optional[str] = None,
    name_zh: str = "",
    task_type: str = "classification",
    params_schema: Optional[Dict[str, Any]] = None,
    trainer: Optional[Callable[..., Any]] = None,
) -> None:
    """扩展入口：按需新增/覆盖算法。"""
    _ALGO_REGISTRY[algo_id] = {
        "solver_id": solver_id or algo_id,
        "name_zh": name_zh or algo_id,
        "task_type": task_type,
        "params_schema": params_schema or {},
    }
    if trainer is not None:
        _CUSTOM_TRAINERS[algo_id] = trainer


def list_algorithms() -> List[Dict[str, Any]]:
    return [{"algo_id": aid, **meta} for aid, meta in _ALGO_REGISTRY.items()]


def get_algo(algo_id: str) -> Dict[str, Any]:
    if algo_id not in _ALGO_REGISTRY:
        raise KeyError(f"未知算法: {algo_id}")
    return dict(_ALGO_REGISTRY[algo_id])


def get_custom_trainer(algo_id: str) -> Optional[Callable[..., Any]]:
    return _CUSTOM_TRAINERS.get(algo_id)


def resolve_solver_id(algo_id: str) -> str:
    return str(get_algo(algo_id)["solver_id"])
