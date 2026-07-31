# psych/ml package
from psych.ml.registry import (
    get_algo,
    get_custom_trainer,
    list_algorithms,
    register_algo,
    resolve_solver_id,
)

__all__ = [
    "list_algorithms",
    "register_algo",
    "get_algo",
    "get_custom_trainer",
    "resolve_solver_id",
]
