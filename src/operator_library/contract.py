"""Role-based contract for Software 1 solvers.

A solver declares the *kinds* of columns it needs, not the names.  A
mapper (rule-based or LLM) is responsible for resolving roles to actual
column names in the user's DataFrame.

中文说明
========
所有 Software 1 solver 的"输入契约"声明在这里。

设计要点：
- solver **不写死用户的列名**，只声明"我需要一个 ID 列 / 一个数值
  目标列 / 一组 PANSS 条目"这种 *角色 (Role)*。
- 由独立的 mapper（规则匹配或 LLM）把角色解析成实际 DataFrame
  里的列名，结果封装在 ``ColumnMapping`` 里再交给 solver。
- 这样同一份 solver 代码就能跑在列名风格完全不同的数据上
  （``patient_id`` / ``PatientID`` / ``受试者编号`` 都行）。

三个核心 dataclass：
- ``Role``           : 列的"种类"枚举（ID / NUMERIC / BINARY_TARGET …）
- ``RoleSpec``       : 单个参数槽位的描述（角色 + 是否必填 + 提示）
- ``SolverContract`` : 一个 solver 完整的 IO 描述（roles + 静态参数 +
                       输出文件清单），暴露给 UI / LLM 使用
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class Role(str, Enum):
    """Column roles a solver can request."""

    ID = "id"
    """Patient / subject / record identifier; one row = one entity."""

    NUMERIC = "numeric"
    """A single numeric column (continuous or discrete)."""

    NUMERIC_LIST = "numeric_list"
    """Multiple numeric columns (e.g. covariates / biomarkers)."""

    BINARY_TARGET = "binary_target"
    """A 0/1 (or True/False) column used as classification target."""

    NUMERIC_TARGET = "numeric_target"
    """A continuous regression target."""

    CATEGORICAL = "categorical"
    """A categorical column (object / few unique values)."""

    DATETIME = "datetime"
    """A datetime column (parseable by pandas)."""

    TIME_TO_EVENT = "time_to_event"
    """Survival time column (numeric, non-negative)."""

    EVENT_INDICATOR = "event_indicator"
    """0/1 censoring/event flag for survival."""

    ORDINAL = "ordinal"
    """Ordered integer column (e.g. Likert 1..7)."""

    ITEM_GROUP = "item_group"
    """A *list* of column names belonging to one logical group
    (e.g. PANSS Positive items P1..P7).  Resolved as List[str]."""

    P_VALUE = "p_value"
    """A column containing p-values (0..1)."""

    TEXT = "text"
    """Free-text column."""

    PARAMS = "params"
    """An arbitrary Python config object (dict / list / value) that is
    *not* a column reference — must always be supplied via mapping
    override.  Mappers ignore this role."""


@dataclass(frozen=True)
class RoleSpec:
    """Specification for one parameter the solver needs.

    中文：solver contract 里 ``roles`` 字典每个 value 就是一个 RoleSpec。
    - role:        这个槽位想要哪种类型的列（见 Role 枚举）
    - description: 喂给 LLM mapper / UI 看的人类可读说明
    - optional:    True 表示用户可以不传；solver 内部要自己 fallback
    - group_hint:  仅对 ITEM_GROUP / NUMERIC_LIST 类型有用，给 mapper
                   分组时的额外提示（例如 "Positive: typically prefixed
                   P1..P7"）
    """
    role: Role
    description: str
    optional: bool = False
    # For ITEM_GROUP / NUMERIC_LIST: list of human-readable hints, e.g.
    # ['Positive symptom items', 'Negative symptom items'].  Mapper may
    # use these as guidance when partitioning columns.
    group_hint: Optional[str] = None


@dataclass(frozen=True)
class SolverContract:
    """Declarative description of a solver's IO.

    中文：每个 solver 模块都会在文件顶部声明一个 SolverContract 实例
    （命名为 CONTRACT 或 XXX_CONTRACT），它告诉外面：
      - name:          solver 在 registry 里的唯一名字
      - capability:    对应 Software 1 哪条能力（F01..F14 编号）
      - description:   一句话功能描述（**会拼进 LLM prompt**，
                       所以保持英文）
      - roles:         {role_key -> RoleSpec}，此 solver 需要哪些列
      - static_params: 不依赖列、只是数值/开关的超参（alpha、k、
                       cv_folds 之类），可以被 UI 调
      - output_files:  {output_key -> filename}，约定写出哪些文件
    """
    name: str
    capability: str       # e.g. "F14_panss_factor_score"
    description: str
    roles: Dict[str, RoleSpec]
    static_params: Dict[str, Any] = field(default_factory=dict)
    output_files: Dict[str, str] = field(default_factory=dict)
    output_kind: Dict[str, str] = field(default_factory=dict)
    # ^ output_key->t|s  t=data-table(rows=entities)  s=stats/summary(not a data table)
    # ^ "output_key": "filename"  e.g. {"scored_csv": "panss_scored.csv"}


@dataclass
class ColumnMapping:
    """Result of running a mapper.  Resolves each role to either a single
    column name (str) or a list of column names (List[str]).

    中文：mapper（规则 or LLM）把 contract 里每个 role_key 解析成
    用户 DataFrame 里的真实列名（或列名列表），结果就装在这里。
    solver.run(df, mapping, output_dir) 拿到的就是这个对象。
    """
    mapping: Dict[str, Any]                      # role_key -> col(s)
    rationale: str = ""                          # mapper explanation
    source: str = "rule_based"                   # rule_based | llm | manual

    def __getitem__(self, key: str) -> Any:
        return self.mapping[key]

    def get(self, key: str, default=None):
        return self.mapping.get(key, default)
