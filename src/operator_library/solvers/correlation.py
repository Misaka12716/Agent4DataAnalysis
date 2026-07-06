"""Correlation solvers (F03 / Q08 / Q30).

Pearson (linear, parametric) and Spearman (rank, non-parametric)
correlation matrices over a list of numeric columns.  Backed by
``scipy.stats``.

中文说明
========
相关性分析三件套，全部基于 ``scipy.stats``。

| solver   | 算法                | 假设           | 适用                       |
|----------|---------------------|----------------|----------------------------|
| pearson  | scipy.pearsonr      | 线性 + 正态     | 连续变量、近似正态、关系线性 |
| spearman | scipy.spearmanr     | 单调（不必线性） | 序数变量、有 outlier、形状未知 |
| kendall  | scipy.kendalltau    | 单调，秩一致性    | 小样本 + tie 多、最稳健 |

输出统一是两份文件：
- ``matrix_csv``：对称矩阵，**对角线 = 1.0**（自相关）
- ``pairs_csv``：长表，列 [col_a, col_b, stat, p_value, n]，**只存
  上三角对（i<j）**，避免重复和对称冗余
- ``stat`` 列名按算法换：r / rho / tau

输入约定
========
- ``numeric_columns``：必填，要做两两相关的数值列名列表（≥2 列）
- 自动 dropna 后 **取两列共有的非空索引**（pairwise complete obs）
- 共有样本 < 3 → stat=NaN, p=NaN, n 仍记录便于审核

自由度
======
Pearson r 的 t 统计量自由度 = n - 2（共有样本数减 2）；
scipy.pearsonr 已经按这个 N 算出 p 值，我们的 ``n`` 列报的就是
共有样本数，可与 r 的统计推断口径对应。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
from scipy import stats

from ..contract import ColumnMapping, Role, RoleSpec, SolverContract
from operator_pipeline.error_codes import OperatorInputError
from ._numeric_utils import coerce_to_numeric


def _filter_numeric_columns(
    df: pd.DataFrame,
    requested: Any,
    solver_name: str,
) -> Tuple[pd.DataFrame, List[str], List[str]]:
    """Resolve a column list to "kept" (numeric, ≥1 non-NaN) and
    "skipped".  When ``requested`` is None / empty, auto-discover all
    numeric columns in ``df`` (after attempting auto-coercion on
    object columns).  Raises ``NO_NUMERIC_COLUMNS`` if fewer than 2
    usable columns remain.

    Returns a (possibly modified) dataframe where any object columns
    that successfully auto-coerced (commas/percent/currency stripping)
    have been replaced with their numeric versions.
    """
    if requested:
        candidates = list(requested)
        auto = False
    else:
        # Auto-discover: include object columns that pass coercion at
        # ≥70% so "37,410" / "15.17%" columns don't get silently dropped.
        candidates = list(df.columns)
        auto = True

    df_work = df
    coerced_any = False
    kept: List[str] = []
    skipped: List[str] = []
    for c in candidates:
        if c not in df_work.columns:
            skipped.append(c)
            continue
        if pd.api.types.is_numeric_dtype(df_work[c]):
            if df_work[c].dropna().shape[0] > 0:
                kept.append(c)
            else:
                skipped.append(c)
            continue
        # Try numeric coercion (strip ,%$£ ¥€ etc.)
        coerced, ok, _ = coerce_to_numeric(df_work[c])
        if ok and coerced.dropna().shape[0] > 0:
            if not coerced_any:
                df_work = df_work.copy()
                coerced_any = True
            df_work[c] = coerced
            kept.append(c)
        else:
            # In auto-discover mode, silently drop non-numeric columns
            # (this is expected — most CSVs have name/id text columns).
            # In explicit mode, surface them in skipped so the caller
            # can see what was requested-but-unusable.
            if not auto:
                skipped.append(c)
    if len(kept) < 2:
        raise OperatorInputError(
            "NO_NUMERIC_COLUMNS",
            solver=solver_name,
        )
    return df_work, kept, skipped


# ---------------------------------------------------------------------------
# Pearson
# ---------------------------------------------------------------------------
# Contract 说明（三个 solver 一致）：
#   - numeric_columns 必填：列名列表，长度 ≥ 2
#   - 没有 static_params：算法本身没有可调超参
#   - 暴露给 LLM 的 role 含义：solver 只需要"一组数值列"，不区分
#     谁是因变量谁是自变量（相关性是对称的）
PEARSON_CONTRACT = SolverContract(
    name="pearson_correlation",
    capability="F03_correlation_analysis",
    description=(
        "Pairwise Pearson r + p-value (linear, parametric).  "
        "numeric_columns omitted → all numeric columns.  Output: "
        "long-format csv [col_a, col_b, r, p_value, n].  "
        "Correlation STRENGTH is measured by |r| (absolute value), "
        "NOT signed r: a perfectly negative r=-1.0 is STRONGER than "
        "any positive r<1.0.  When picking the 'strongest correlate' "
        "always use max(|r|), never max(r)."),
    roles={
        "numeric_columns": RoleSpec(
            Role.NUMERIC_LIST,
            "numeric columns to correlate (omit → all numeric)",
            optional=True),
    },
    output_files={"matrix_csv": "pearson_matrix.csv",
                  "pairs_csv":  "pearson_pairs.csv"},
    output_kind={"matrix_csv": "s", "pairs_csv": "s"},
)


SPEARMAN_CONTRACT = SolverContract(
    name="spearman_correlation",
    capability="F03_correlation_analysis",
    description=(
        "Pairwise Spearman rho + p-value (rank, non-parametric).  "
        "numeric_columns omitted → all numeric columns.  Output: "
        "long-format csv [col_a, col_b, rho, p_value, n].  "
        "Strength is |rho|; max(|rho|) picks the strongest monotonic "
        "association regardless of sign."),
    roles={
        "numeric_columns": RoleSpec(Role.NUMERIC_LIST,
                                     "numeric columns (omit → all numeric)",
                                     optional=True),
    },
    output_files={"matrix_csv": "spearman_matrix.csv",
                  "pairs_csv":  "spearman_pairs.csv"},
    output_kind={"matrix_csv": "s", "pairs_csv": "s"},
)


KENDALL_CONTRACT = SolverContract(
    name="kendall_correlation",
    capability="F03_correlation_analysis",
    description=(
        "Pairwise Kendall tau (rank concordance) + p-value.  "
        "numeric_columns omitted → all numeric columns.  Strength is "
        "|tau|; max(|tau|) picks the strongest rank concordance."),
    roles={
        "numeric_columns": RoleSpec(Role.NUMERIC_LIST,
                                     "numeric columns (omit → all numeric)",
                                     optional=True),
    },
    output_files={"matrix_csv": "kendall_matrix.csv",
                  "pairs_csv":  "kendall_pairs.csv"},
    output_kind={"matrix_csv": "s", "pairs_csv": "s"},
)


_MAX_CORR_PAIRS = 5000   # ~100 cols → all pairs; wider tables get capped
_MAX_CORR_COLS = 200     # hard ceiling: refuse to spend O(N^2) on >200 cols


def _pairs(df, cols, fn) -> pd.DataFrame:
    # 只算上三角（i < j），避免对称冗余；fn 是 scipy.pearsonr /
    # spearmanr / kendalltau 中的任一个
    #
    # Defensive cap: pairwise correlation is O(N²); a 14306-column table
    # produces ~100M pairs which spins for hours.  If we exceed
    # ``_MAX_CORR_COLS`` we keep only the first N (the planner usually
    # cares about the top columns) and emit a warning sentinel row.
    truncated = False
    if len(cols) > _MAX_CORR_COLS:
        truncated = True
        cols = cols[:_MAX_CORR_COLS]

    rows = []
    n_pairs = 0
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            if n_pairs >= _MAX_CORR_PAIRS:
                # Stop early; the partial matrix is still useful and
                # downstream agents can request a more focused subset.
                truncated = True
                break
            n_pairs += 1
            a, b = cols[i], cols[j]
            # pairwise complete obs：每对单独 dropna，再取两列共有的索引
            # 这样不会因为某第 3 列全空就把 a/b 的可用样本一起砍掉
            x = df[a].astype(float).dropna()
            y = df[b].astype(float).dropna()
            common = x.index.intersection(y.index)
            if len(common) < 3:
                # 共有样本太少，p 值无意义；仍保留 n 便于审核
                rows.append({"col_a": a, "col_b": b,
                             "stat": np.nan, "p_value": np.nan,
                             "n": len(common)})
                continue
            stat, p = fn(x.loc[common].values, y.loc[common].values)
            rows.append({"col_a": a, "col_b": b,
                         "stat": float(stat), "p_value": float(p),
                         "n": int(len(common))})
        if n_pairs >= _MAX_CORR_PAIRS:
            break
    out = pd.DataFrame(rows)
    out.attrs["truncated"] = bool(truncated)
    return out


def _run_correlation(
    df: pd.DataFrame,
    mapping: ColumnMapping,
    output_dir: Path,
    *,
    solver_name: str,
    contract: SolverContract,
    fn,
    stat_col: str,
) -> Dict[str, Any]:
    """Shared run() implementation for the 3 correlation solvers.

    V8 Phase 2 §3.5: non-numeric / all-NaN columns are SKIPPED (with the
    list returned as ``skipped_columns``) instead of raising; if fewer
    than 2 usable columns remain we fail-fast with ``NO_NUMERIC_COLUMNS``.
    """
    df, cols, skipped = _filter_numeric_columns(
        df, mapping.get("numeric_columns"), solver_name,
    )
    long = _pairs(df, cols, fn).rename(columns={"stat": stat_col})
    pairs_path = Path(output_dir) / contract.output_files["pairs_csv"]
    long.to_csv(pairs_path, index=False)
    # Matrix: diag = 1.0; upper triangle mirrored to lower triangle.
    m = pd.DataFrame(np.eye(len(cols)), index=cols, columns=cols)
    for _, row in long.iterrows():
        m.loc[row["col_a"], row["col_b"]] = row[stat_col]
        m.loc[row["col_b"], row["col_a"]] = row[stat_col]
    m_path = Path(output_dir) / contract.output_files["matrix_csv"]
    m.to_csv(m_path)
    result: Dict[str, Any] = {
        "matrix_csv": str(m_path),
        "pairs_csv":  str(pairs_path),
        "pairs_df":   long,
    }
    if skipped:
        result["skipped_columns"] = skipped
    return result


class PearsonCorrelationSolver:
    contract = PEARSON_CONTRACT

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        return _run_correlation(
            df, mapping, output_dir,
            solver_name="pearson_correlation",
            contract=PEARSON_CONTRACT,
            fn=stats.pearsonr,
            stat_col="r",
        )


class SpearmanCorrelationSolver:
    contract = SPEARMAN_CONTRACT

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        return _run_correlation(
            df, mapping, output_dir,
            solver_name="spearman_correlation",
            contract=SPEARMAN_CONTRACT,
            fn=stats.spearmanr,
            stat_col="rho",
        )


class KendallCorrelationSolver:
    contract = KENDALL_CONTRACT

    def run(self, df: pd.DataFrame, mapping: ColumnMapping,
            output_dir: Path) -> Dict[str, Any]:
        return _run_correlation(
            df, mapping, output_dir,
            solver_name="kendall_correlation",
            contract=KENDALL_CONTRACT,
            fn=stats.kendalltau,
            stat_col="tau",
        )


def get_pearson_solver():  return PearsonCorrelationSolver()
def get_spearman_solver(): return SpearmanCorrelationSolver()
def get_kendall_solver():  return KendallCorrelationSolver()


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------
def selftest() -> Dict[str, Any]:
    """Verify both solvers against an analytic-perfect-correlation
    fixture and against numpy.corrcoef as an independent reference.

    中文：3 个 sub-solver 的"出厂自检"。

    Fixture：60 行（seed=42）
      - x ~ N(0, 1)
      - y = 0.7·x + N(0, 0.5²)   → 期望 |corr(x,y)| ≈ 0.8
      - z = -x                    → 期望 corr(x, z) = -1.0 严格

    通过判定（误差 < 1e-9）：
      - Pearson (x, z) = -1.0；Pearson (x, y) 与 numpy.corrcoef 一致
      - Spearman (x, z) = -1.0（rank 对单调下降也是 -1）
      - Kendall  (x, z) = -1.0（完美反 concordant）
                  (x, y) 与独立调用 scipy.kendalltau 一致
    """
    import tempfile

    rng = np.random.default_rng(42)
    n = 60
    x = rng.normal(size=n)
    y = 0.7 * x + rng.normal(scale=0.5, size=n)   # ~0.8 correlation
    z = -x                                          # exact -1.0

    df = pd.DataFrame({"x": x, "y": y, "z": z})

    diffs = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # --- Pearson ----
        out = get_pearson_solver().run(
            df=df,
            mapping=ColumnMapping({"numeric_columns": ["x", "y", "z"]}),
            output_dir=tmp,
        )
        pairs = out["pairs_df"].set_index(["col_a", "col_b"])
        # Reference: numpy.corrcoef
        ref = np.corrcoef(df[["x", "y", "z"]].T.values)
        if abs(pairs.loc[("x", "z"), "r"] - (-1.0)) > 1e-9:
            diffs.append(f"pearson(x,z) expected -1.0, got "
                         f"{pairs.loc[('x','z'), 'r']}")
        if abs(pairs.loc[("x", "y"), "r"] - ref[0, 1]) > 1e-9:
            diffs.append(f"pearson(x,y) {pairs.loc[('x','y'), 'r']} "
                         f"diverges from numpy.corrcoef {ref[0, 1]}")

        # --- Spearman ----
        out2 = get_spearman_solver().run(
            df=df,
            mapping=ColumnMapping({"numeric_columns": ["x", "y", "z"]}),
            output_dir=tmp,
        )
        s_pairs = out2["pairs_df"].set_index(["col_a", "col_b"])
        # Reference: scipy.stats.spearmanr called twice (we already use it,
        # so just verify the perfect-rank case where spearman=−1 exactly).
        if abs(s_pairs.loc[("x", "z"), "rho"] - (-1.0)) > 1e-9:
            diffs.append(f"spearman(x,z) expected -1.0, got "
                         f"{s_pairs.loc[('x','z'), 'rho']}")

        # --- Kendall ----
        out3 = get_kendall_solver().run(
            df=df,
            mapping=ColumnMapping({"numeric_columns": ["x", "y", "z"]}),
            output_dir=tmp,
        )
        k_pairs = out3["pairs_df"].set_index(["col_a", "col_b"])
        # x and -x are perfectly anti-concordant → kendall tau = -1
        if abs(k_pairs.loc[("x", "z"), "tau"] - (-1.0)) > 1e-9:
            diffs.append(f"kendall(x,z) expected -1.0, got "
                         f"{k_pairs.loc[('x','z'), 'tau']}")
        # cross-check kendall(x,y) vs scipy.kendalltau directly
        ref_tau, _ = stats.kendalltau(df["x"], df["y"])
        if abs(k_pairs.loc[("x", "y"), "tau"] - float(ref_tau)) > 1e-9:
            diffs.append(f"kendall(x,y) {k_pairs.loc[('x','y'), 'tau']} "
                         f"diverges from scipy {ref_tau}")

    return {
        "ok": len(diffs) == 0,
        "summary": ("pearson + spearman + kendall match analytic / "
                    "independent reference" if not diffs
                    else f"{len(diffs)} mismatch(es)"),
        "details": {"diffs": diffs,
                    "tested": ["pearson_correlation",
                                "spearman_correlation",
                                "kendall_correlation"]},
    }
