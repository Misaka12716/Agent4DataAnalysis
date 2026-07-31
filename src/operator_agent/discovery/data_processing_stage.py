# -*- coding: utf-8 -*-
"""Data-processing agent — V8 §5 / plan ``data-processing-stage``.

Produces a data profile + cleaning suggestions for the public blackboard.
When requested by the caller, it also applies a narrow set of deterministic,
safe cleaning transforms and persists ``cleaned_input.csv`` for downstream
stages.

Reuses :func:`operator_library.profiler.profile_df` /
:func:`operator_library.profiler.profile_to_text` for the profile; cleaning
suggestions are derived from the profile + a read-only inspection of the
dataframe (quartiles for IQR outliers, coercion probes for dtype issues,
duplicate-row count).

Exception contract: if the dataframe is unreadable / the profiler throws, the
stage emits an ``Error`` signal on the bus and then **re-raises**
:class:`DataProcessingError` so the supervisor can handle it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from operator_library.profiler import profile_df, profile_to_text

from .blackboard import PublicBlackboard
from .data_shape_inference import format_shape_banner, infer_shape
from .paths import ensure_run_dir
from .signals import SignalBus
from .types import CleanSuggestion, ProfileSummary

__all__ = ["run", "DataProcessingError", "PRODUCER"]

PRODUCER = "data_processing"

# --- documented thresholds (constants) ------------------------------------
# Profile rendering budget (lines).
PROFILE_MAX_LINES = 200

# Missing-fraction severity bands.  Any missing > 0 yields a suggestion.
MISSING_HIGH = 0.50      # >=50% missing  → severity "high" (consider dropping)
MISSING_MEDIUM = 0.20    # >=20% missing  → severity "medium"
# (0 < frac < MISSING_MEDIUM) → severity "low"

# IQR outlier detection (Tukey fences) on numeric columns.
OUTLIER_IQR_K = 1.5              # fence = Q1 - k*IQR / Q3 + k*IQR
OUTLIER_FLAG_FRACTION = 0.01     # suggest only if >1% of rows are outliers
OUTLIER_HIGH = 0.10              # >=10% outliers → "high"
OUTLIER_MEDIUM = 0.05            # >=5%  outliers → "medium"

# Object-column "mostly numeric" → suggest type coercion.
TYPE_NUMERIC_FRACTION = 0.80     # >=80% of non-null values parse as numbers

# Duplicate-row severity bands (fraction of all rows duplicated).
DUPLICATE_HIGH = 0.10
DUPLICATE_MEDIUM = 0.01


class DataProcessingError(RuntimeError):
    """Raised when profiling fails (after an ``Error`` signal is emitted)."""


def _missing_severity(frac: float) -> str:
    if frac >= MISSING_HIGH:
        return "high"
    if frac >= MISSING_MEDIUM:
        return "medium"
    return "low"


def _outlier_severity(frac: float) -> str:
    if frac >= OUTLIER_HIGH:
        return "high"
    if frac >= OUTLIER_MEDIUM:
        return "medium"
    return "low"


def _duplicate_severity(frac: float) -> str:
    if frac >= DUPLICATE_HIGH:
        return "high"
    if frac >= DUPLICATE_MEDIUM:
        return "medium"
    return "low"


def _missing_suggestions(df: pd.DataFrame) -> List[CleanSuggestion]:
    out: List[CleanSuggestion] = []
    n = len(df)
    if n == 0:
        return out
    for col in df.columns:
        frac = float(df[col].isna().mean())
        if frac <= 0.0:
            continue
        sev = _missing_severity(frac)
        if sev == "high":
            advice = (f"{frac:.0%} missing — consider dropping the column or "
                      f"the affected rows, or impute with a model")
        elif pd.api.types.is_numeric_dtype(df[col]):
            advice = f"{frac:.0%} missing — impute with median (fillna)"
        else:
            advice = f"{frac:.0%} missing — impute with mode/'unknown' (fillna)"
        out.append(CleanSuggestion(column=str(col), issue="missing",
                                   suggestion=advice, severity=sev))
    return out


def _outlier_suggestions(df: pd.DataFrame) -> List[CleanSuggestion]:
    out: List[CleanSuggestion] = []
    for col in df.columns:
        if not pd.api.types.is_numeric_dtype(df[col]):
            continue
        try:
            s = df[col].dropna()
            if len(s) < 4:
                continue
            q1 = s.quantile(0.25)
            q3 = s.quantile(0.75)
            iqr = q3 - q1
            if iqr <= 0:
                continue
            lower = q1 - OUTLIER_IQR_K * iqr
            upper = q3 + OUTLIER_IQR_K * iqr
            n_out = int(((s < lower) | (s > upper)).sum())
            frac = n_out / len(s)
        except Exception:
            continue
        if frac < OUTLIER_FLAG_FRACTION:
            continue
        out.append(CleanSuggestion(
            column=str(col), issue="outlier",
            suggestion=(f"{n_out} IQR outliers ({frac:.0%}) outside "
                        f"[{lower:.4g}, {upper:.4g}] — winsorize/clip or flag "
                        f"(do not silently drop)"),
            severity=_outlier_severity(frac)))
    return out


def _type_suggestions(df: pd.DataFrame) -> List[CleanSuggestion]:
    out: List[CleanSuggestion] = []
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            continue
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            continue
        try:
            s = df[col].dropna()
            if len(s) == 0:
                continue
            coerced = pd.to_numeric(s, errors="coerce")
            frac_numeric = float(coerced.notna().mean())
        except Exception:
            continue
        if frac_numeric >= TYPE_NUMERIC_FRACTION and frac_numeric < 1.0001:
            # All-or-mostly numeric strings stored as object — coerce.
            out.append(CleanSuggestion(
                column=str(col), issue="type",
                suggestion=(f"object column is {frac_numeric:.0%} numeric — "
                            f"coerce dtype with pd.to_numeric"),
                severity="medium"))
    return out


def _duplicate_suggestions(df: pd.DataFrame) -> List[CleanSuggestion]:
    n = len(df)
    if n == 0:
        return []
    try:
        n_dup = int(df.duplicated().sum())
    except Exception:
        return []
    if n_dup <= 0:
        return []
    frac = n_dup / n
    return [CleanSuggestion(
        column="(all rows)", issue="duplicate",
        suggestion=(f"{n_dup} duplicate rows ({frac:.0%}) — review and "
                    f"drop_duplicates if unintended"),
        severity=_duplicate_severity(frac))]


def _build_suggestions(df: pd.DataFrame) -> List[CleanSuggestion]:
    suggestions: List[CleanSuggestion] = []
    suggestions.extend(_missing_suggestions(df))
    suggestions.extend(_outlier_suggestions(df))
    suggestions.extend(_type_suggestions(df))
    suggestions.extend(_duplicate_suggestions(df))
    return suggestions


def _safe_literal(value: Any) -> str:
    return repr(value)


def _apply_safe_cleaning(
    df: pd.DataFrame,
    suggestions: List[CleanSuggestion],
) -> Tuple[pd.DataFrame, List[str], List[Dict[str, Any]]]:
    """Apply deterministic low-risk cleaning transforms.

    We intentionally skip outlier clipping and high-missing columns: those
    require scientific judgement.  The snippets returned here are executable
    pandas statements for reproducibility, not arbitrary LLM-generated code.
    """
    cleaned = df.copy()
    snippets: List[str] = []
    actions: List[Dict[str, Any]] = []

    for s in suggestions:
        col = s.column
        if s.issue == "type" and col in cleaned.columns:
            before = str(cleaned[col].dtype)
            cleaned[col] = pd.to_numeric(cleaned[col], errors="coerce")
            snippets.append(
                f"df[{_safe_literal(col)}] = pd.to_numeric("
                f"df[{_safe_literal(col)}], errors='coerce')")
            actions.append({
                "column": col,
                "issue": "type",
                "action": "pd.to_numeric(errors='coerce')",
                "dtype_before": before,
                "dtype_after": str(cleaned[col].dtype),
            })

    for s in suggestions:
        col = s.column
        if s.issue != "missing" or col not in cleaned.columns:
            continue
        if s.severity == "high":
            continue
        n_missing = int(cleaned[col].isna().sum())
        if n_missing <= 0:
            continue
        if pd.api.types.is_numeric_dtype(cleaned[col]):
            fill_value = cleaned[col].median()
            if pd.isna(fill_value):
                continue
            cleaned[col] = cleaned[col].fillna(fill_value)
            snippets.append(
                f"df[{_safe_literal(col)}] = "
                f"df[{_safe_literal(col)}].fillna("
                f"df[{_safe_literal(col)}].median())")
            fill_repr = float(fill_value)
        else:
            mode = cleaned[col].mode(dropna=True)
            fill_value = mode.iloc[0] if not mode.empty else "unknown"
            cleaned[col] = cleaned[col].fillna(fill_value)
            snippets.append(
                f"df[{_safe_literal(col)}] = "
                f"df[{_safe_literal(col)}].fillna("
                f"{_safe_literal(fill_value)})")
            fill_repr = str(fill_value)
        actions.append({
            "column": col,
            "issue": "missing",
            "action": "fillna",
            "n_filled": n_missing,
            "fill_value": fill_repr,
        })

    had_dups = any(s.issue == "duplicate" for s in suggestions)
    if had_dups:
        before_n = int(len(cleaned))
        cleaned = cleaned.drop_duplicates().reset_index(drop=True)
        dropped = before_n - int(len(cleaned))
        if dropped > 0:
            snippets.append("df = df.drop_duplicates().reset_index(drop=True)")
            actions.append({
                "column": "(all rows)",
                "issue": "duplicate",
                "action": "drop_duplicates",
                "n_dropped": dropped,
            })

    return cleaned, snippets, actions


def run(
    df: pd.DataFrame,
    task: str,
    public_bb: PublicBlackboard,
    *,
    run_dir: Optional[Path] = None,
    bus: Optional[SignalBus] = None,
    seed: Optional[int] = None,
    apply_cleaning: bool = False,
) -> Tuple[ProfileSummary, List[CleanSuggestion]]:
    """Profile ``df`` and emit (non-executed) cleaning suggestions.

    Writes ``"profile"`` (a :class:`ProfileSummary` dict) and
    ``"clean_suggestions"`` (a list of :class:`CleanSuggestion` dicts) to the
    public blackboard, both produced by ``"data_processing"``.

    Returns ``(profile_summary, suggestions)``.

    Raises :class:`DataProcessingError` (after emitting an ``Error`` signal)
    when the dataframe cannot be profiled.
    """
    source_df = df
    suggestions: List[CleanSuggestion] = []
    cleaning_applied: List[str] = []
    cleaning_actions: List[Dict[str, Any]] = []
    cleaned_path: Optional[Path] = None

    try:
        # Preserve the original failure contract: if the dataframe cannot be
        # profiled at all, emit a profile error before doing any cleaning work.
        profile_df(df)
    except Exception as exc:
        if bus is not None:
            bus.emit_error(PRODUCER, error=repr(exc),
                           reason="profile_failed")
        raise DataProcessingError(
            f"failed to profile dataframe: {exc!r}") from exc

    suggestions = _build_suggestions(source_df)
    if apply_cleaning:
        source_df, cleaning_applied, cleaning_actions = _apply_safe_cleaning(
            source_df, suggestions)

    if run_dir is not None:
        rd = Path(run_dir)
        try:
            cleaned_path = rd / "cleaned_input.csv"
            cleaned_path.parent.mkdir(parents=True, exist_ok=True)
            source_df.to_csv(cleaned_path, index=False)
        except Exception:
            cleaned_path = None

    profile = profile_df(source_df)
    profile_text = profile_to_text(profile, max_lines=PROFILE_MAX_LINES)

    # --- schema-aware shape inference (best-effort) ----------------------
    # The profiler is dataset-agnostic: it never says "this is a DEG result"
    # or "this is a parallel-arm trial".  Infer that here and prepend a
    # short banner so the downstream hypothesis agent sees the hint at the
    # top of its prompt context (without it, the LLM tends to map raw
    # column names like 'logFC'/'adj_p_value' onto hypothesis variables).
    inferred = infer_shape(source_df)
    if inferred is not None:
        profile_text = format_shape_banner(inferred) + "\n\n" + profile_text

    shape = profile.get("shape") or [0, 0]
    cols_info = profile.get("columns") or []
    columns = [str(c.get("name")) for c in cols_info]
    dtypes = {str(c.get("name")): str(c.get("dtype")) for c in cols_info}

    profile_summary = ProfileSummary(
        n_rows=int(shape[0]) if len(shape) > 0 else 0,
        n_cols=int(shape[1]) if len(shape) > 1 else 0,
        columns=columns,
        dtypes=dtypes,
        profile_text=profile_text,
        inferred_shape=(inferred.to_dict() if inferred is not None else None),
    )

    # Optionally persist the full profile text as an artifact (path + summary
    # on the blackboard, never the payload itself — V8 §11).
    if run_dir is not None:
        rd = Path(run_dir)
        run_paths_obj = ensure_run_dir(rd.name, runs_root=rd.parent)
        profile_path = run_paths_obj.artifacts_dir / "profile.txt"
        try:
            profile_path.write_text(profile_text, encoding="utf-8")
            profile_summary.profile_path = str(profile_path)
            public_bb.register_artifact(
                profile_path,
                f"{profile_summary.n_rows}x{profile_summary.n_cols} profile")
        except Exception:  # artifact persistence is best-effort
            pass

    public_bb.put("profile", profile_summary.to_dict(),
                  producer=PRODUCER, seed=seed)
    public_bb.put("clean_suggestions", [s.to_dict() for s in suggestions],
                  producer=PRODUCER, seed=seed)
    public_bb.put("cleaning_applied", list(cleaning_applied),
                  producer=PRODUCER, seed=seed)
    public_bb.put("cleaning_actions", cleaning_actions,
                  producer=PRODUCER, seed=seed)
    public_bb.put("cleaned_input_path",
                  str(cleaned_path) if cleaned_path is not None else None,
                  producer=PRODUCER, seed=seed)

    if bus is not None:
        bus.emit_done(PRODUCER, n_suggestions=len(suggestions),
                      n_cleaning_applied=len(cleaning_applied),
                      cleaned_input_path=(str(cleaned_path)
                                          if cleaned_path is not None
                                          else None))

    return profile_summary, suggestions
