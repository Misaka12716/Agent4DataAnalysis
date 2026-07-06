"""Data-governance atomic solvers (F01 / F13).

Three solvers in one module, all tiny and pure-pandas:

  - missing_summary       缺失率 / dtype / 唯一值数汇总（不改数据）
  - data_imputation       NaN + sentinel 的多策略填补 / 删除
  - outlier_iqr_flag      Tukey 围栏 + sentinel 标记（不改数据）

Each ships with a deterministic ``selftest()`` that uses a hand-built
fixture whose answers are obvious by inspection.

Row alignment invariant (V8 Pattern B fix)
==========================================
Row-shaped output CSVs carry a leftmost ``__row_id__`` column matching
the ORIGINAL DataFrame's positional row index.  Downstream consumers
(coder code, other operators) can ``df.merge(out, on='__row_id__')`` to
align rows safely, even after the coder filters / reorders rows.

EXCEPTION: ``data_imputation`` with ``method='drop_row'`` literally drops
the offending rows; the resulting csv still has ``__row_id__`` so the
caller can see which positional rows of the raw csv survived.

Aggregate outputs (``missing_summary``, one row per column not per row of
input) are exempt.

中文说明
========
1. ``missing_summary``：逐列统计缺失数 / 缺失率 / 唯一值数 / dtype。
   - 输入：任意 DataFrame，无角色映射要求
   - 输出 ``summary_csv`` = ``missing_summary.csv``
     [column, dtype, n_missing, missing_rate, n_unique]

2. ``data_imputation`` (V8 Phase 3+)：多策略缺失值处理。把 NaN + 自动
   识别的 sentinel 占位符（-9999 / -999 / ...）按指定策略处理。
   - 输入：可选 ``numeric_columns``，不传就自动选所有数值列
   - 静态参数 ``method``∈{median (默认), mean, mode, constant, ffill,
     bfill, drop_row, none}
     · ``median`` / ``mean`` / ``mode``：用统计量填
     · ``constant`` + ``constant_value=0``：填固定值
     · ``ffill`` / ``bfill``：前 / 后填（时序常用）
     · ``drop_row``：把含 NaN / sentinel 的行直接删掉
     · ``none``：不改值，只输出每列的 ``<col>_is_missing`` 标记
   - 静态参数 ``sentinel_values``：见 ``_parse_sentinel_param``，None=
     自动检测；"off"=只处理 NaN；[-9999,-1]=显式列表
   - 输出 ``imputed_csv`` = ``imputed.csv``
   - 选择策略的经验：求描述统计（median/mean/quantile）→ ``drop_row``；
     给后续建模用 → ``median`` / ``mean``；时序 → ``ffill``

3. ``outlier_iqr_flag``：Tukey 围栏 [Q1-k·IQR, Q3+k·IQR] 标记异常 +
   sentinel 强制标记（sentinel 不参与 Q1/Q3 估计）。
   - 输入：可选 ``numeric_columns`` + 可选 ``id_col``
   - 静态参数：``k`` 默认 1.5；``sentinel_values`` 同上
   - 输出 ``flags_csv`` = ``iqr_outlier_flags.csv``
     [__row_id__, id?, {col}_outlier×N, {col}_sentinel×N,
      any_outlier, any_sentinel]
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract
from operator_pipeline.error_codes import OperatorInputError


# ---------------------------------------------------------------------------
# Sentinel value detection (V8 Phase 3 §IQR-strengthen)
# ---------------------------------------------------------------------------
# Known integer placeholder values widely used as "missing data" sentinels
# in real-world clinical / survey / surveillance data (RADAR's -9999, CDC
# influenza, NHANES -1 codes, etc.).  We intentionally EXCLUDE {-1, 0, 1}
# because those are valid values in many domains; auto-detection is
# additionally gated on the candidate lying outside a 3·IQR fence
# computed on the remaining (non-candidate) values, which makes false
# positives essentially impossible on well-behaved continuous columns.
DEFAULT_SENTINEL_CANDIDATES: Tuple[float, ...] = (
    -99999.0, -9999.0, -999.0, -99.0, -9.0,
    99999.0,  9999.0,  999.0,  99.0,
)


def _detect_sentinels(
    s: pd.Series,
    user_provided: Optional[Iterable] = None,
    *,
    candidates: Iterable = DEFAULT_SENTINEL_CANDIDATES,
    outlier_fence_k: float = 3.0,
    min_occurrences: int = 1,
    min_non_sentinel_n: int = 4,
) -> List[float]:
    """Return the sentinel values present in ``s`` that should be treated
    as missing for downstream stats (IQR / median / mean / etc.).

    Resolution order:
      1. If ``user_provided`` is not ``None`` (including ``[]`` for
         explicit opt-out), use it verbatim — no auto-detection.
      2. Otherwise scan ``s`` for any value in ``candidates`` that
         (a) occurs at least ``min_occurrences`` times, AND
         (b) lies outside the ``[Q1 - k·IQR, Q3 + k·IQR]`` fence
             computed on the REMAINING non-candidate data.
         Both conditions are necessary so that, e.g. ``-9999`` is only
         flagged when the rest of the column has a sensible distribution
         that ``-9999`` clearly does not belong to — preventing false
         positives on cash-flow / temperature / financial columns where
         ``-9999`` might be a legitimate observation.

    中文：sentinel（占位符）自动识别。规则：值必须 (a) 在已知占位候选集
    {-9999, -999, -99, -9, 9999, 999, 99, ...} 内；并且 (b) 在剔除自身后
    的数据 3·IQR 围栏外。这样既能识别 RADAR / NHANES / CDC 常见的占位
    符，又不会把"恰巧在范围内"的合法值误标。
    """
    if user_provided is not None:
        return sorted({float(v) for v in user_provided})
    series = pd.to_numeric(s, errors="coerce").dropna()
    if series.empty:
        return []
    cand_set = {float(c) for c in candidates}
    detected: List[float] = []
    for cand in cand_set:
        n_cand = int((series == cand).sum())
        if n_cand < min_occurrences:
            continue
        other = series[series != cand]
        if len(other) < min_non_sentinel_n:
            continue
        q1 = float(np.nanpercentile(other, 25))
        q3 = float(np.nanpercentile(other, 75))
        iqr = q3 - q1
        if iqr <= 0:
            # near-degenerate distribution: require cand to be far from
            # the bulk vs. the column's empirical span.
            rng = max(float(other.max() - other.min()), 1.0)
            if abs(cand - float(other.median())) > 5 * rng:
                detected.append(cand)
            continue
        low = q1 - outlier_fence_k * iqr
        high = q3 + outlier_fence_k * iqr
        if cand < low or cand > high:
            detected.append(cand)
    return sorted(detected)


def _parse_sentinel_param(raw: Any) -> Optional[List[float]]:
    """Normalize a ``sentinel_values`` static param into either ``None``
    (auto-detect ON) or a list of floats (explicit list, possibly empty
    to mean "auto-detect OFF").

    Accepted shapes:
      - None / missing      → auto-detect ON  (returns None)
      - "auto" (any case)   → auto-detect ON  (returns None)
      - "off" / "none"      → auto-detect OFF (returns [])
      - []                  → auto-detect OFF (returns [])
      - [-9999, -1]         → use those values (returns [-9999.0, -1.0])
      - "-9999, -1"         → parses as [-9999.0, -1.0]
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in ("", "auto", "default"):
            return None
        if s in ("off", "none", "disable", "disabled", "false"):
            return []
        parts = [p.strip() for p in raw.replace(";", ",").split(",")
                  if p.strip()]
        out: List[float] = []
        for p in parts:
            try:
                out.append(float(p))
            except ValueError:
                continue
        return out
    if isinstance(raw, (list, tuple, set)):
        out2: List[float] = []
        for v in raw:
            try:
                out2.append(float(v))
            except (TypeError, ValueError):
                continue
        return out2
    # scalar number
    try:
        return [float(raw)]
    except (TypeError, ValueError):
        return None


def _resolve_numeric_columns(
    df: pd.DataFrame,
    requested: Any,
    solver_name: str,
    *,
    allow_empty: bool = False,
) -> Tuple[List[str], List[str]]:
    """Resolve a ``numeric_columns`` mapping value into (kept, skipped).

    V8 Phase 2 §3.6: requested-but-non-numeric / missing columns are
    silently skipped (with the list surfaced in the result), so a
    planner that handed us a mixed list does not crash the whole step.

    If ``requested`` is empty / None, autodiscovery picks every numeric
    column in ``df`` (legacy behaviour).

    Raises ``NO_TARGET_COLUMNS`` if the planner explicitly asked for
    columns and none of them are usable.  Raises ``NO_NUMERIC_COLUMNS``
    if autodiscovery returned nothing.
    """
    if requested:
        kept: List[str] = []
        skipped: List[str] = []
        for c in requested:
            if c in df.columns and pd.api.types.is_numeric_dtype(df[c]):
                kept.append(c)
            else:
                skipped.append(c)
        if not kept and not allow_empty:
            raise OperatorInputError(
                "NO_TARGET_COLUMNS",
                solver=solver_name,
                requested=list(requested),
            )
        return kept, skipped
    # __row_id__ is a runner-injected positional index, never real data;
    # always exclude from autodiscovery so solvers don't treat it as a
    # column to impute / scale / stat over.
    autocols = [c for c in df.columns
                if pd.api.types.is_numeric_dtype(df[c])
                and c != "__row_id__"]
    if not autocols and not allow_empty:
        raise OperatorInputError(
            "NO_NUMERIC_COLUMNS",
            solver=solver_name,
        )
    return autocols, []


# ---------------------------------------------------------------------------
# Solver 1: missing_summary  (Q05 / Q06 / Q33)
# ---------------------------------------------------------------------------
# Contract 说明：
#   - roles 为空 → mapper 不需要做任何列名解析，solver 直接吃整个 DataFrame
#   - 没有 static_params，行为完全确定
#   - 输出仅一个 csv：缺失统计表
MISSING_SUMMARY_CONTRACT = SolverContract(
    name="missing_summary",
    capability="F01_data_governance_cleaning",
    description=(
        "Per-column missing counts and rates.  Output: csv "
        "[column, dtype, n_missing, missing_rate, n_unique]."
    ),
    roles={},  # operates on the entire DataFrame
    output_files={"summary_csv": "missing_summary.csv"},
    output_kind={"summary_csv": "s"},
)


class MissingSummarySolver:
    contract = MISSING_SUMMARY_CONTRACT

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        n = len(df)
        rows = []
        # 逐列扫描：n_missing 用 isna().sum()，n_unique 用 nunique(dropna=True)
        # 这样"全 NaN 列"的 n_unique=0，便于下游识别"无信息列"
        for c in df.columns:
            s = df[c]
            nm = int(s.isna().sum())
            rows.append({
                "column":       str(c),
                "dtype":        str(s.dtype),
                "n_missing":    nm,
                "missing_rate": round(nm / n, 6) if n else 0.0,
                "n_unique":     int(s.nunique(dropna=True)),
            })
        out = pd.DataFrame(rows)
        path = Path(output_dir) / MISSING_SUMMARY_CONTRACT.output_files["summary_csv"]
        out.to_csv(path, index=False)
        return {"summary_csv": str(path),
                "summary_df": out,
                "total_missing_cells": int(out["n_missing"].sum()),
                "n_rows": n}


# ---------------------------------------------------------------------------
# Solver 2: fillna_median  (Q33)
# ---------------------------------------------------------------------------
# Contract 说明：
#   - numeric_columns 是 NUMERIC_LIST 类型且 optional：
#       * 不传 → solver 自己用 is_numeric_dtype 选所有数值列
#       * 传了 → 严格只填这些列，其它列即使是数值也不动
#   - 没有 static_params：行为唯一（always median, ddof 不参与）
_IMPUTE_METHODS = (
    "median", "mean", "mode", "constant",
    "ffill", "bfill", "drop_row", "none",
)


DATA_IMPUTATION_CONTRACT = SolverContract(
    name="data_imputation",
    capability="F01_data_governance_cleaning",
    description=(
        "Impute missing values (NaN + auto-detected sentinel placeholders "
        "like -9999/-999) in numeric columns by a chosen strategy.  "
        "method ∈ {median (default), mean, mode, constant, ffill, bfill, "
        "drop_row, none}.  `drop_row` removes the offending rows entirely "
        "(prefer when downstream needs a clean column for descriptive "
        "stats like median/mean/quantile).  `none` only adds a "
        "<col>_is_missing flag without changing values.  "
        "numeric_columns omitted → all numeric columns.  "
        "Output: csv [__row_id__, ...imputed columns (possibly fewer "
        "rows if method=drop_row)]."
    ),
    roles={
        "numeric_columns": RoleSpec(
            Role.NUMERIC_LIST,
            "the numeric columns to impute (others pass through)",
            optional=True,
        ),
    },
    static_params={
        # Imputation strategy; see _IMPUTE_METHODS.
        "method": "median",
        # Used when method=='constant'.  None + method=constant raises.
        "constant_value": None,
        # See _parse_sentinel_param for accepted shapes.
        #   None  → auto-detect sentinels on per-column basis
        #   "off" → only NaN is treated as missing
        #   [-9999, -1] → those values + NaN are treated as missing
        "sentinel_values": None,
    },
    output_files={"imputed_csv": "imputed.csv"},
    output_kind={"imputed_csv": "t"},
)


class DataImputationSolver:
    contract = DATA_IMPUTATION_CONTRACT

    def __init__(self, method: str = "median",
                 constant_value: Any = None,
                 sentinel_values: Any = None):
        """中文：多策略缺失值填补 / 删除算子。

        :param method:          median/mean/mode/constant/ffill/bfill/
                                drop_row/none，见 _IMPUTE_METHODS
        :param constant_value:  method=='constant' 时填的固定值
        :param sentinel_values: 占位符值。None=自动；"off"=只处理 NaN；
                                显式列表（如 [-9999, -1]）。
        """
        m = (method or "median").strip().lower()
        if m not in _IMPUTE_METHODS:
            raise OperatorInputError(
                "INVALID_STAT",
                solver="data_imputation",
                stat=m,
                whitelist=list(_IMPUTE_METHODS),
            )
        self.method = m
        self.constant_value = constant_value
        self.sentinel_param = sentinel_values

    def _impute_column(self, col: pd.Series,
                        sentinels: List[float]) -> Tuple[pd.Series,
                                                            float, int]:
        """Return (imputed_col, fill_value, n_sentinel_cells).

        ``fill_value`` is float('nan') for ffill/bfill/none and is the
        actual scalar used to substitute missing values for the other
        methods.  For ``drop_row``, this returns col + NaN-where-mask;
        the caller is responsible for dropping the rows.
        """
        if sentinels:
            mask = col.isin(sentinels)
            n_sentinel = int(mask.sum())
            col_clean = col.where(~mask, other=np.nan)
        else:
            n_sentinel = 0
            col_clean = col
        if self.method == "median":
            v = float(col_clean.median())
            return col_clean.fillna(v), v, n_sentinel
        if self.method == "mean":
            v = float(col_clean.mean())
            return col_clean.fillna(v), v, n_sentinel
        if self.method == "mode":
            modes = col_clean.mode(dropna=True)
            if modes.empty:
                v = float("nan")
                return col_clean, v, n_sentinel
            v = float(modes.iloc[0])
            return col_clean.fillna(v), v, n_sentinel
        if self.method == "constant":
            if self.constant_value is None:
                raise OperatorInputError(
                    "MISSING_STAT_PARAM",
                    solver="data_imputation",
                    stat="constant",
                    param="constant_value",
                )
            try:
                v = float(self.constant_value)
            except (TypeError, ValueError):
                raise OperatorInputError(
                    "INVALID_STAT",
                    solver="data_imputation",
                    stat=f"constant({self.constant_value!r})",
                    whitelist=["a numeric value"],
                )
            return col_clean.fillna(v), v, n_sentinel
        if self.method == "ffill":
            return col_clean.ffill(), float("nan"), n_sentinel
        if self.method == "bfill":
            return col_clean.bfill(), float("nan"), n_sentinel
        # 'drop_row' / 'none' both keep NaN positions; caller decides
        return col_clean, float("nan"), n_sentinel

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        # V8 Phase 2 §3.6: silently skip requested-but-unusable columns
        # so a mixed list from the planner doesn't blow up the whole step.
        cols, skipped = _resolve_numeric_columns(
            df, mapping.get("numeric_columns"),
            solver_name="data_imputation",
        )
        user_sentinels = _parse_sentinel_param(self.sentinel_param)
        out = df.copy()
        fill_values: Dict[str, float] = {}
        sentinels_used: Dict[str, List[float]] = {}
        n_sentinel_cells = 0
        n_nan_filled = int(df[cols].isna().sum().sum())
        # ``method=='none'`` adds per-column boolean flag columns and
        # leaves values untouched.  For other methods, we transform the
        # value columns in place.
        if self.method == "none":
            for c in cols:
                col = out[c]
                sn = _detect_sentinels(col, user_provided=user_sentinels)
                if sn:
                    sentinels_used[c] = sn
                miss = col.isna() | (col.isin(sn) if sn
                                       else pd.Series(False,
                                                       index=col.index))
                n_sentinel_cells += int((col.isin(sn)).sum() if sn
                                         else 0)
                out[f"{c}_is_missing"] = miss.astype(int).values
        else:
            for c in cols:
                col = out[c]
                sn = _detect_sentinels(col, user_provided=user_sentinels)
                if sn:
                    sentinels_used[c] = sn
                imputed, v, n_sn = self._impute_column(col, sn)
                n_sentinel_cells += n_sn
                fill_values[c] = v
                out[c] = imputed
        # drop_row pruning happens AFTER all columns processed so a row
        # missing in ANY target column gets dropped exactly once
        n_rows_dropped = 0
        n_rows_in = len(df)
        if self.method == "drop_row":
            mask_drop = out[cols].isna().any(axis=1)
            n_rows_dropped = int(mask_drop.sum())
            out = out.loc[~mask_drop].copy()
        # V8 Pattern B fix: prepend __row_id__ (positional index of the
        # ORIGINAL df) so the coder can merge the imputed output back
        # against the raw table without ambiguity.  drop_row keeps the
        # original __row_id__ values so the surviving rows still map.
        # Note: runner now always injects __row_id__ on read_csv, so the
        # caller's df may already carry it; if so, preserve the upstream
        # values (esp. important for drop_row) rather than re-inserting.
        if "__row_id__" in out.columns:
            if self.method == "drop_row":
                # Surviving rows already carry the correct upstream
                # __row_id__ values; nothing to do.
                pass
            else:
                # Non-drop methods: overwrite with positional indices of
                # the ORIGINAL df so the column is a clean 0..N-1 range.
                out["__row_id__"] = range(len(df))
                # Move it to the first position.
                cols_order = (["__row_id__"]
                              + [c for c in out.columns if c != "__row_id__"])
                out = out[cols_order]
        else:
            out.insert(0, "__row_id__", out.index.values
                        if self.method == "drop_row" else range(len(df)))
        path = (Path(output_dir)
                 / DATA_IMPUTATION_CONTRACT.output_files["imputed_csv"])
        out.to_csv(path, index=False)
        result: Dict[str, Any] = {
            "imputed_csv": str(path),
            "method": self.method,
            "fill_values": fill_values,
            "n_filled_cells": n_nan_filled + n_sentinel_cells,
            "n_nan_filled": n_nan_filled,
            "n_sentinel_filled": n_sentinel_cells,
            "n_rows_in": n_rows_in,
            "n_rows_out": len(out),
            "n_rows_dropped": n_rows_dropped,
        }
        if sentinels_used:
            result["sentinels_detected"] = sentinels_used
        if skipped:
            result["skipped_columns"] = skipped
        return result


# ---------------------------------------------------------------------------
# Solver 3: outlier_iqr_flag  (Q04 / Q25)
# ---------------------------------------------------------------------------
# Contract 说明：
#   - id_col              optional：纯数值表也能跑，但有 id 时会带在输出里
#   - numeric_columns     必填：要标的列（其它列即使数值也不动）
#   - static_params.k     默认 1.5（经典 Tukey）；2.0 = 更宽松，3.0 = 极端值
OUTLIER_IQR_CONTRACT = SolverContract(
    name="outlier_iqr_flag",
    capability="F13_outlier_reference_range_detection",
    description=(
        "Flag values outside [Q1 - k*IQR, Q3 + k*IQR] Tukey fences per "
        "numeric column.  Sentinel placeholders (-9999 / -999 / ... when "
        "they lie far outside the bulk distribution) are also flagged "
        "and EXCLUDED from Q1/Q3 estimation so they don't drag the fence "
        "wider.  numeric_columns omitted → auto-select all numeric "
        "columns.  Output: csv [__row_id__, id?, <col>_outlier, "
        "<col>_sentinel, any_outlier, any_sentinel]."
    ),
    roles={
        "id_col":           RoleSpec(Role.ID, "subject identifier",
                                      optional=True),
        "numeric_columns":  RoleSpec(Role.NUMERIC_LIST,
                                      "numeric columns to flag (omit → "
                                      "all numeric columns)",
                                      optional=True),
    },
    static_params={
        "k": 1.5,
        # See _parse_sentinel_param for accepted shapes.
        "sentinel_values": None,
    },
    output_files={"flags_csv": "iqr_outlier_flags.csv"},
    output_kind={"flags_csv": "t"},  # rows still = original observations
)


class OutlierIqrFlagSolver:
    contract = OUTLIER_IQR_CONTRACT

    def __init__(self, k: float = 1.5, sentinel_values: Any = None):
        """中文：

        :param k: Tukey 围栏的乘数。1.5 是教科书默认（覆盖正态约 99.3%），
                  2.0 倾向"只标极端值"，3.0 几乎只剩明显错误。
        :param sentinel_values: 占位符值。None=自动检测；"off"=只跑 Tukey；
                                列表（如 [-9999, -1]）=指定占位符值。
        """
        self.k = k
        self.sentinel_param = sentinel_values

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        id_col = mapping.get("id_col")
        # V8 Phase 2 §3.6: skip non-numeric / missing columns instead of
        # crashing inside ``astype(float)``.
        cols, skipped = _resolve_numeric_columns(
            df, mapping.get("numeric_columns"),
            solver_name="outlier_iqr_flag",
        )
        user_sentinels = _parse_sentinel_param(self.sentinel_param)

        out = pd.DataFrame()
        # V8 Pattern B fix: __row_id__ is ALWAYS the leftmost column so
        # downstream code can ``df.merge(flags, on='__row_id__')``
        # without losing alignment when the coder filters / reorders.
        out["__row_id__"] = range(len(df))
        if id_col and id_col in df.columns:
            out[id_col] = df[id_col].values
        any_out = pd.Series(0, index=df.index, dtype=int)
        any_sentinel = pd.Series(0, index=df.index, dtype=int)
        bounds: Dict[str, Dict[str, float]] = {}
        sentinels_used: Dict[str, List[float]] = {}
        for c in cols:
            v = pd.to_numeric(df[c], errors="coerce")
            # V8 Phase 3 §IQR-strengthen: detect sentinels, mask them
            # OUT of the Q1/Q3/IQR computation so a column with
            # 5% "-9999" placeholders gets the SAME fence as the same
            # column without them.  Sentinels then get force-flagged as
            # outliers (and tracked in a separate <col>_sentinel column).
            sn = _detect_sentinels(v, user_provided=user_sentinels)
            sentinel_mask = (v.isin(sn) if sn
                              else pd.Series(False, index=v.index))
            v_clean = v.where(~sentinel_mask, other=np.nan)
            q1 = float(np.nanpercentile(v_clean, 25))
            q3 = float(np.nanpercentile(v_clean, 75))
            iqr = q3 - q1
            # Tukey 围栏：[Q1 - k·IQR, Q3 + k·IQR]，k 默认 1.5
            low, high = q1 - self.k * iqr, q3 + self.k * iqr
            bounds[c] = {"q1": q1, "q3": q3, "iqr": iqr,
                         "low": low, "high": high,
                         "sentinels": list(sn)}
            if sn:
                sentinels_used[c] = list(sn)
            # NaN → 不算 outlier（fillna(False)），避免缺失污染统计
            # Sentinel 同时也算 outlier（这样 coder 用 any_outlier 过滤
            # 时就直接把占位符行剔除），并在专门列再标一次。
            tukey_flag = ((v_clean < low) | (v_clean > high)).fillna(False)
            sent_flag = sentinel_mask.fillna(False).astype(bool)
            combined = (tukey_flag | sent_flag).astype(int)
            out[f"{c}_outlier"] = combined.values
            out[f"{c}_sentinel"] = sent_flag.astype(int).values
            any_out |= combined.values
            any_sentinel |= sent_flag.astype(int).values
        out["any_outlier"] = any_out.values
        out["any_sentinel"] = any_sentinel.values
        path = Path(output_dir) / OUTLIER_IQR_CONTRACT.output_files["flags_csv"]
        out.to_csv(path, index=False)
        result: Dict[str, Any] = {
            "flags_csv": str(path),
            "bounds": bounds,
            "n_outlier_rows": int(any_out.sum()),
            "n_sentinel_rows": int(any_sentinel.sum()),
        }
        if sentinels_used:
            result["sentinels_detected"] = sentinels_used
        if skipped:
            result["skipped_columns"] = skipped
        return result


# ---------------------------------------------------------------------------
# Public factories
# ---------------------------------------------------------------------------
def get_missing_summary_solver(): return MissingSummarySolver()
def get_data_imputation_solver(method: str = "median",
                                constant_value: Any = None,
                                sentinel_values: Any = None):
    return DataImputationSolver(method=method,
                                  constant_value=constant_value,
                                  sentinel_values=sentinel_values)
def get_outlier_iqr_solver(k: float = 1.5, sentinel_values: Any = None):
    return OutlierIqrFlagSolver(k=k, sentinel_values=sentinel_values)


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------
def selftest() -> Dict[str, Any]:
    """Hand-built fixture with obvious ground truth.

    中文：3 个 sub-solver 的"出厂自检"。

    Fixture：
      - missing_summary / fillna_median：5 行 × 3 列，col_a 含 1 个 NaN，
        col_b 含 2 个 NaN，col_c 全字符串。所有期望值都能口算。
      - outlier_iqr_flag：8 行 lab=[10..16, 100]，IQR ≈ 3，1.5×IQR
        围栏会唯一标出最后一行 100。

    通过判定：
      - missing_summary：n_missing 与肉眼一致，col_b 缺失率 = 0.4
      - fillna_median：col_a NaN 填 3.0（{1,2,4,5} 的 median），
                       col_b NaN 填 30.0；非数值列 col_c 原样
      - outlier_iqr_flag：仅最后一行 (lab=100) 被标记
    """
    import tempfile

    # 5 rows; col_a has 1 NaN, col_b has 2 NaN, col_c is full
    csv = io.StringIO(
        "id,col_a,col_b,col_c\n"
        "P1,1,10,X\n"
        "P2,2,,X\n"
        "P3,,30,Y\n"
        "P4,4,,Y\n"
        "P5,5,50,Z\n"
    )
    df = pd.read_csv(csv)

    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # 1. missing_summary --------------------------------------------------
        ms = get_missing_summary_solver()
        out = ms.run(df=df, mapping=ColumnMapping({}), output_dir=tmp)
        sdf = out["summary_df"].set_index("column")
        if int(sdf.loc["col_a", "n_missing"]) != 1:
            diffs.append("missing_summary: col_a expected 1 NaN")
        if int(sdf.loc["col_b", "n_missing"]) != 2:
            diffs.append("missing_summary: col_b expected 2 NaN")
        if int(sdf.loc["col_c", "n_missing"]) != 0:
            diffs.append("missing_summary: col_c expected 0 NaN")
        if abs(float(sdf.loc["col_b", "missing_rate"]) - 0.4) > 1e-9:
            diffs.append("missing_summary: col_b missing_rate != 0.4")

        # 2. data_imputation (method=median, legacy fillna_median path)
        fm = get_data_imputation_solver(method="median")
        out2 = fm.run(df=df, mapping=ColumnMapping({}), output_dir=tmp)
        filled = pd.read_csv(out2["imputed_csv"])
        # V8 Pattern B fix: leftmost column must be __row_id__ matching
        # the positional index of the original df.
        if list(filled.columns)[0] != "__row_id__":
            diffs.append("data_imputation: leftmost column must be __row_id__")
        if filled["__row_id__"].tolist() != list(range(len(df))):
            diffs.append("data_imputation: __row_id__ should be 0..N-1 "
                         "matching original df row positions")
        # col_a values [1,2,_,4,5] median=3.0 → fill index 2
        if not np.isclose(filled.loc[2, "col_a"], 3.0):
            diffs.append(f"data_imputation(median): col_a NaN should be 3.0, "
                         f"got {filled.loc[2, 'col_a']}")
        # col_b values [10,_,30,_,50] median=30 (of {10,30,50}) → fill 1,3
        if not np.isclose(filled.loc[1, "col_b"], 30.0):
            diffs.append(f"data_imputation(median): col_b NaN should be 30.0, "
                         f"got {filled.loc[1, 'col_b']}")
        if filled["col_c"].tolist() != ["X", "X", "Y", "Y", "Z"]:
            diffs.append("data_imputation: col_c should be unchanged")
        if out2.get("method") != "median":
            diffs.append(f"data_imputation: method field wrong: "
                         f"{out2.get('method')!r}")

        # 2b. data_imputation method=mean
        fm_mean = get_data_imputation_solver(method="mean")
        out_mean = fm_mean.run(df=df, mapping=ColumnMapping({}),
                                 output_dir=tmp)
        f_mean = pd.read_csv(out_mean["imputed_csv"])
        # col_a [1,2,_,4,5] mean=3.0
        if not np.isclose(f_mean.loc[2, "col_a"], 3.0):
            diffs.append(f"data_imputation(mean): col_a NaN should be 3.0, "
                         f"got {f_mean.loc[2, 'col_a']}")
        # col_b [10,_,30,_,50] mean=30.0
        if not np.isclose(f_mean.loc[1, "col_b"], 30.0):
            diffs.append(f"data_imputation(mean): col_b NaN should be 30.0, "
                         f"got {f_mean.loc[1, 'col_b']}")

        # 2c. data_imputation method=constant
        fm_c = get_data_imputation_solver(method="constant",
                                            constant_value=0)
        out_c = fm_c.run(df=df, mapping=ColumnMapping({}), output_dir=tmp)
        f_c = pd.read_csv(out_c["imputed_csv"])
        if not np.isclose(f_c.loc[2, "col_a"], 0.0):
            diffs.append(f"data_imputation(constant=0): col_a should be 0, "
                         f"got {f_c.loc[2, 'col_a']}")
        if not np.isclose(f_c.loc[1, "col_b"], 0.0):
            diffs.append(f"data_imputation(constant=0): col_b should be 0, "
                         f"got {f_c.loc[1, 'col_b']}")

        # 2d. data_imputation method=constant missing value → fail-fast
        try:
            get_data_imputation_solver(method="constant").run(
                df=df, mapping=ColumnMapping({}), output_dir=tmp)
            diffs.append("data_imputation(constant w/o value): should raise")
        except OperatorInputError as e:
            if e.code != "MISSING_STAT_PARAM":
                diffs.append(f"data_imputation(constant w/o value): "
                             f"got code {e.code}")

        # 2e. data_imputation method=drop_row
        fm_d = get_data_imputation_solver(method="drop_row")
        out_d = fm_d.run(df=df, mapping=ColumnMapping({}),
                          output_dir=tmp)
        f_d = pd.read_csv(out_d["imputed_csv"])
        # 5 rows in; rows {1,2,3} have NaN in some target column →
        # 2 rows survive (rows 0 and 4)
        if out_d["n_rows_in"] != 5 or out_d["n_rows_out"] != 2:
            diffs.append(f"data_imputation(drop_row): expected 5→2, "
                         f"got {out_d['n_rows_in']}→{out_d['n_rows_out']}")
        if out_d["n_rows_dropped"] != 3:
            diffs.append(f"data_imputation(drop_row): expected 3 dropped, "
                         f"got {out_d['n_rows_dropped']}")
        if f_d["__row_id__"].tolist() != [0, 4]:
            diffs.append(f"data_imputation(drop_row): surviving __row_id__ "
                         f"should be [0, 4], got "
                         f"{f_d['__row_id__'].tolist()}")

        # 2f. data_imputation method=none → adds <col>_is_missing
        fm_n = get_data_imputation_solver(method="none")
        out_n = fm_n.run(df=df, mapping=ColumnMapping({}),
                          output_dir=tmp)
        f_n = pd.read_csv(out_n["imputed_csv"])
        if "col_a_is_missing" not in f_n.columns:
            diffs.append("data_imputation(none): missing col_a_is_missing")
        elif f_n["col_a_is_missing"].sum() != 1:
            diffs.append(f"data_imputation(none): col_a_is_missing sum "
                         f"should be 1, got "
                         f"{f_n['col_a_is_missing'].sum()}")
        if "col_b_is_missing" not in f_n.columns:
            diffs.append("data_imputation(none): missing col_b_is_missing")
        elif f_n["col_b_is_missing"].sum() != 2:
            diffs.append(f"data_imputation(none): col_b_is_missing sum "
                         f"should be 2, got "
                         f"{f_n['col_b_is_missing'].sum()}")

        # 3. outlier_iqr_flag -------------------------------------------------
        # build a column where 100 is an obvious outlier
        df_out = pd.DataFrame({
            "id":   [f"P{i}" for i in range(8)],
            "lab":  [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 100.0],
        })
        oi = get_outlier_iqr_solver(k=1.5)
        out3 = oi.run(df=df_out,
                       mapping=ColumnMapping({"id_col": "id",
                                              "numeric_columns": ["lab"]}),
                       output_dir=tmp)
        flags = pd.read_csv(out3["flags_csv"])
        # V8 Pattern B fix: leftmost column must be __row_id__ matching
        # the positional row index of the original df.
        if list(flags.columns)[0] != "__row_id__":
            diffs.append("outlier_iqr: leftmost column must be __row_id__")
        if flags["__row_id__"].tolist() != list(range(len(df_out))):
            diffs.append("outlier_iqr: __row_id__ should be 0..N-1 "
                         "matching original df row positions")
        # row 7 (lab=100) should be the only outlier
        if flags["lab_outlier"].tolist() != [0, 0, 0, 0, 0, 0, 0, 1]:
            diffs.append(f"outlier_iqr: expected only row 7 flagged, "
                         f"got {flags['lab_outlier'].tolist()}")
        if int(flags["any_outlier"].sum()) != 1:
            diffs.append("outlier_iqr: any_outlier sum should be 1")

        # 4. SENTINEL: simulate the RADAR ILI placeholder case --------
        # column has 95% reasonable values + 5% "-9999" placeholders.
        # Without sentinel detection, Q1/Q3 are pulled wide and the
        # placeholders never get flagged.  With detection, they should
        # all be flagged AND the fence should match the no-placeholder
        # column's fence almost exactly.
        ili = (list(range(1500, 1800, 5))  # 60 reasonable values
               + [-9999.0, -9999.0, -9999.0])  # 3 placeholders
        df_sn = pd.DataFrame({
            "id":  [f"P{i}" for i in range(len(ili))],
            "ili": ili,
        })
        oi_sn = get_outlier_iqr_solver(k=1.5)
        out_sn = oi_sn.run(
            df=df_sn,
            mapping=ColumnMapping({"id_col": "id",
                                    "numeric_columns": ["ili"]}),
            output_dir=tmp,
        )
        flags_sn = pd.read_csv(out_sn["flags_csv"])
        sentinel_count = int(flags_sn["ili_sentinel"].sum())
        if sentinel_count != 3:
            diffs.append(f"outlier_iqr: expected 3 sentinel flags, "
                         f"got {sentinel_count}")
        # All 3 placeholder rows must also be outlier rows
        out_count = int(flags_sn["ili_outlier"].sum())
        if out_count < 3:
            diffs.append(f"outlier_iqr: expected ≥3 outlier flags "
                         f"(sentinels), got {out_count}")
        if "ili" not in (out_sn.get("sentinels_detected") or {}):
            diffs.append("outlier_iqr: sentinels_detected[ili] missing")
        elif -9999.0 not in out_sn["sentinels_detected"]["ili"]:
            diffs.append(f"outlier_iqr: -9999 should be detected, "
                         f"got {out_sn['sentinels_detected']['ili']}")
        bounds = out_sn["bounds"]["ili"]
        # Without sentinel masking, q1 would be far below 1500.  With
        # masking, q1 should be near the 25th percentile of [1500..1800).
        if not (1500.0 <= bounds["q1"] <= 1800.0):
            diffs.append(f"outlier_iqr: q1 should ignore -9999, "
                         f"got q1={bounds['q1']:.1f}")

        # 5. SENTINEL OFF: explicit "off" should restore legacy fence
        oi_off = get_outlier_iqr_solver(k=1.5, sentinel_values="off")
        out_off = oi_off.run(
            df=df_sn,
            mapping=ColumnMapping({"id_col": "id",
                                    "numeric_columns": ["ili"]}),
            output_dir=tmp,
        )
        flags_off = pd.read_csv(out_off["flags_csv"])
        if int(flags_off["ili_sentinel"].sum()) != 0:
            diffs.append("outlier_iqr: sentinel='off' must not detect any")
        if "sentinels_detected" in out_off:
            diffs.append("outlier_iqr: 'off' must not populate "
                         "sentinels_detected")

        # 6. SENTINEL on data_imputation(median): -9999 must NOT pollute median
        df_fn = pd.DataFrame({
            "id":  [f"P{i}" for i in range(len(ili))],
            "ili": ili,
        })
        fm_sn = get_data_imputation_solver(method="median")
        out_fn = fm_sn.run(
            df=df_fn,
            mapping=ColumnMapping({"numeric_columns": ["ili"]}),
            output_dir=tmp,
        )
        filled_sn = pd.read_csv(out_fn["imputed_csv"])
        # Median of [1500..1800 by 5) is 1647.5 — must be in that range,
        # NOT dragged to a negative number by -9999.
        med = float(out_fn["fill_values"]["ili"])
        if not (1500.0 <= med <= 1800.0):
            diffs.append(f"data_imputation(median)@sentinel: median "
                         f"polluted by -9999, got median={med}")
        if int(out_fn.get("n_sentinel_filled", 0)) != 3:
            diffs.append(f"data_imputation@sentinel: expected 3 sentinel "
                         f"cells filled, got "
                         f"{out_fn.get('n_sentinel_filled')}")
        # The -9999 cells should now hold the median value
        replaced = filled_sn["ili"].iloc[-3:].tolist()
        if not all(abs(v - med) < 1e-6 for v in replaced):
            diffs.append(f"data_imputation(median)@sentinel: -9999 cells "
                         f"not replaced with median, got {replaced}")

        # 6b. SENTINEL on data_imputation(drop_row): sentinel rows should
        # be dropped AND surviving rows should keep their original
        # __row_id__ values (so coder can map back to raw csv).
        fm_dr = get_data_imputation_solver(method="drop_row")
        out_dr = fm_dr.run(
            df=df_fn,
            mapping=ColumnMapping({"numeric_columns": ["ili"]}),
            output_dir=tmp,
        )
        f_dr = pd.read_csv(out_dr["imputed_csv"])
        if out_dr["n_rows_dropped"] != 3:
            diffs.append(f"data_imputation(drop_row)@sentinel: expected "
                         f"3 dropped, got {out_dr['n_rows_dropped']}")
        if any(v == -9999.0 for v in f_dr["ili"].tolist()):
            diffs.append("data_imputation(drop_row)@sentinel: -9999 "
                         "rows leaked into output")
        # __row_id__ should NOT include the last 3 positions
        n = len(df_fn)
        if any(rid in (n - 3, n - 2, n - 1)
                for rid in f_dr["__row_id__"].tolist()):
            diffs.append("data_imputation(drop_row)@sentinel: last 3 "
                         "__row_id__ should be missing from output")

    return {
        "ok":      len(diffs) == 0,
        "summary": ("all sub-solvers + sentinel checks pass" if not diffs
                    else f"{len(diffs)} mismatch(es)"),
        "details": {"diffs": diffs,
                    "tested": [
                        "missing_summary",
                        "data_imputation@median",
                        "data_imputation@mean",
                        "data_imputation@constant",
                        "data_imputation@constant_missing_value",
                        "data_imputation@drop_row",
                        "data_imputation@none",
                        "data_imputation@median+sentinel",
                        "data_imputation@drop_row+sentinel",
                        "outlier_iqr_flag",
                        "outlier_iqr_flag@sentinel",
                        "outlier_iqr_flag@sentinel_off",
                    ]},
    }
